from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from csv import DictReader, DictWriter
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from src.email_agent.config import (
    AUTO_REPLY_BODY,
    AUTO_REPLY_DRY_RUN,
    AUTO_REPLY_ENABLED,
    AUTO_REPLY_LOG_PATH,
    AUTO_REPLY_MAX_PER_HOUR,
    AUTO_REPLY_POLL_SECONDS,
    AUTO_REPLY_REWRITE_ENABLED,
    AUTO_REPLY_STATE_PATH,
)
from src.email_agent.gmail_client import GmailClient

logger = logging.getLogger("auto_reply")

_AUTOMATED_LOCAL_PARTS = (
    "noreply",
    "no-reply",
    "do-not-reply",
    "donotreply",
    "mailer-daemon",
    "postmaster",
)
_AUTOMATED_LABELS = {
    "CATEGORY_PROMOTIONS",
    "CATEGORY_SOCIAL",
    "CATEGORY_UPDATES",
    "CATEGORY_FORUMS",
}


@dataclass(frozen=True)
class InboxNotification:
    """Completed background work waiting for terminal display."""

    message_id: str
    sender: str
    subject: str
    received_at: str
    auto_reply_status: str

    def display_line(self) -> str:
        sender = _terminal_text(self.sender or "Unknown sender")
        subject = _terminal_text(self.subject or "(no subject)")
        received_at = _terminal_text(self.received_at or "unknown time")
        status = _terminal_text(self.auto_reply_status)
        return f"- {received_at} | {sender} | {subject} | {status}"


@dataclass(frozen=True)
class _QueuedInboxTask:
    message: dict[str, Any]
    attempt: int = 0


@dataclass(frozen=True)
class _TaskResult:
    notification: InboxNotification
    retryable: bool = False


def _terminal_text(value: str) -> str:
    """Keep untrusted email headers from disrupting terminal output."""
    cleaned = "".join(
        character
        for character in str(value).replace("\r", " ").replace("\n", " ")
        if character.isprintable()
    )
    return " ".join(cleaned.split())


class AutoReplyState:
    """Local ledger for discovery, queued work, replies, and rate limiting."""

    def __init__(self, path: Path) -> None:
        self.path = path
        # State mutations happen on the event loop; this lock also protects
        # save() when its file write runs via asyncio.to_thread().
        self._lock = threading.RLock()
        self.data = {
            "initialized": False,
            "seen_message_ids": [],
            "pending_message_ids": [],
            "replied_thread_ids": [],
            "reply_timestamps": [],
        }
        self._load()
        self._normalise()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except (OSError, json.JSONDecodeError):
            logger.warning("Could not read %s; starting a fresh safe baseline.", self.path)

    def _normalise(self) -> None:
        with self._lock:
            for key in (
                "seen_message_ids",
                "pending_message_ids",
                "replied_thread_ids",
                "reply_timestamps",
            ):
                if not isinstance(self.data.get(key), list):
                    self.data[key] = []
            self.data["initialized"] = bool(self.data.get("initialized", False))

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            temporary.replace(self.path)

    @property
    def initialized(self) -> bool:
        with self._lock:
            return bool(self.data["initialized"])

    def has_seen(self, message_id: str) -> bool:
        with self._lock:
            return message_id in self.data["seen_message_ids"]

    def has_pending(self, message_id: str) -> bool:
        with self._lock:
            return message_id in self.data["pending_message_ids"]

    def queue_message(self, message_id: str) -> None:
        """Persist discovery state before handing work to an in-memory queue."""
        with self._lock:
            if message_id not in self.data["seen_message_ids"]:
                self.data["seen_message_ids"].append(message_id)
            if message_id not in self.data["pending_message_ids"]:
                self.data["pending_message_ids"].append(message_id)
            self.data["seen_message_ids"] = self.data["seen_message_ids"][-2000:]
            self.data["pending_message_ids"] = self.data["pending_message_ids"][-2000:]

    def complete_message(self, message_id: str) -> None:
        with self._lock:
            self.data["pending_message_ids"] = [
                item
                for item in self.data["pending_message_ids"]
                if item != message_id
            ]

    def has_replied_to_thread(self, thread_id: str) -> bool:
        with self._lock:
            return thread_id in self.data["replied_thread_ids"]

    def record_handled(
        self,
        message_id: str,
        thread_id: str,
        *,
        count_toward_limit: bool,
    ) -> None:
        with self._lock:
            self.queue_message(message_id)
            threads = self.data["replied_thread_ids"]
            if thread_id and thread_id not in threads:
                threads.append(thread_id)
            self.data["replied_thread_ids"] = threads[-2000:]
            if count_toward_limit:
                self._prune_timestamps()
                self.data["reply_timestamps"].append(time.time())

    def can_reply_now(self) -> bool:
        with self._lock:
            self._prune_timestamps()
            return len(self.data["reply_timestamps"]) < AUTO_REPLY_MAX_PER_HOUR

    def establish_baseline(self, messages: list[dict[str, Any]]) -> None:
        with self._lock:
            for message in messages:
                message_id = message.get("id")
                if message_id:
                    self.data["seen_message_ids"].append(message_id)
            self.data["seen_message_ids"] = list(
                dict.fromkeys(self.data["seen_message_ids"])
            )[-2000:]
            self.data["initialized"] = True

    def _prune_timestamps(self) -> None:
        now = time.time()
        self.data["reply_timestamps"] = [
            stamp
            for stamp in self.data["reply_timestamps"]
            if isinstance(stamp, (int, float)) and stamp >= now - 3600
        ]


def is_direct_human_email(message: dict[str, Any], own_address: str) -> bool:
    """Allow only a conservative subset of personal, inbound email."""
    headers = {key.casefold(): value for key, value in message["headers"].items()}
    _, from_address = parseaddr(headers.get("from", ""))
    _, reply_address = parseaddr(headers.get("reply-to", ""))
    from_address = from_address.casefold()
    reply_address = reply_address.casefold()

    if not from_address or "@" not in from_address or from_address == own_address:
        return False
    if reply_address and "@" not in reply_address:
        return False

    local_part = from_address.partition("@")[0]
    if any(marker in local_part for marker in _AUTOMATED_LOCAL_PARTS):
        return False

    labels = set(message.get("label_ids", []))
    if labels.intersection(_AUTOMATED_LABELS):
        return False

    precedence = headers.get("precedence", "").casefold()
    if precedence in {"bulk", "list", "junk"}:
        return False
    if headers.get("list-unsubscribe"):
        return False
    if headers.get("auto-submitted", "").casefold() not in {"", "no"}:
        return False
    if headers.get("x-auto-response-suppress"):
        return False
    return True


class AutoReplyMonitor:
    """Async inbox discovery, task processing, and terminal notifications."""

    def __init__(self) -> None:
        self.gmail: GmailClient | None = None
        self.own_address = ""
        self.state = AutoReplyState(AUTO_REPLY_STATE_PATH)
        self.stop_event = asyncio.Event()
        self.task_queue: asyncio.Queue[_QueuedInboxTask] = asyncio.Queue()
        self.notification_queue: asyncio.Queue[InboxNotification] = asyncio.Queue()
        self._queued_ids: set[str] = set()
        self._gmail_lock = asyncio.Lock()
        self._reply_model: Any | None = None
        self.discovery_task: asyncio.Task[None] | None = None
        self.worker_task: asyncio.Task[None] | None = None
        self._worker_processing = False

    async def start(self) -> None:
        self.discovery_task = asyncio.create_task(
            self._discover_loop(),
            name="gmail-inbox-discovery",
        )
        self.worker_task = asyncio.create_task(
            self._worker_loop(),
            name="gmail-auto-reply-worker",
        )

    async def stop(self) -> None:
        self.stop_event.set()
        if self.discovery_task and not self.discovery_task.done():
            self.discovery_task.cancel()
        if (
            self.worker_task
            and not self.worker_task.done()
            and not self._worker_processing
        ):
            self.worker_task.cancel()
        tasks = [
            task
            for task in (self.discovery_task, self.worker_task)
            if task is not None
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _get_gmail(self) -> GmailClient:
        async with self._gmail_lock:
            if self.gmail is None:
                self.gmail = await asyncio.to_thread(GmailClient)
                self.own_address = await self.gmail.aown_email_address()
            return self.gmail

    async def _save_state(self) -> None:
        await asyncio.to_thread(self.state.save)

    async def drain_notifications(self) -> list[InboxNotification]:
        """Return completed background work for the main terminal to display."""
        notifications: list[InboxNotification] = []
        while True:
            try:
                notifications.append(self.notification_queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        if not notifications:
            return []

        for notification in notifications:
            self.state.complete_message(notification.message_id)
            self._queued_ids.discard(notification.message_id)
        await self._save_state()
        return notifications

    async def _wait_or_stop(self, seconds: float) -> bool:
        await asyncio.sleep(seconds)
        return self.stop_event.is_set()

    async def _discover_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                gmail = await self._get_gmail()
                messages = await gmail.ainbox_candidates()
                if not self.state.initialized:
                    # Existing mail is not considered new when monitoring starts.
                    self.state.establish_baseline(messages)
                    await self._save_state()
                    logger.info("Inbox baseline created; waiting for new messages.")
                else:
                    await self._enqueue_discovered_messages(messages)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Inbox discovery failed; retrying on the next poll.")

            await self._wait_or_stop(AUTO_REPLY_POLL_SECONDS)

    async def _enqueue_discovered_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> None:
        state_changed = False
        for message in messages:
            message_id = message.get("id")
            if not message_id:
                continue
            if not self.state.has_seen(message_id):
                self.state.queue_message(message_id)
                state_changed = True
                self._enqueue_task(message)
            elif self.state.has_pending(message_id):
                # Rehydrate durable work after a restart.
                self._enqueue_task(message)

        if state_changed:
            await self._save_state()

    def _enqueue_task(self, message: dict[str, Any], attempt: int = 0) -> None:
        message_id = message["id"]
        if message_id in self._queued_ids:
            return
        self._queued_ids.add(message_id)
        self.task_queue.put_nowait(_QueuedInboxTask(message=message, attempt=attempt))

    async def _worker_loop(self) -> None:
        # Do not start new queued sends while the terminal application is
        # shutting down. Pending work remains durable and is rehydrated later.
        while not self.stop_event.is_set():
            try:
                task = await asyncio.wait_for(self.task_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            try:
                try:
                    self._worker_processing = True
                    result = await self._process_task(task.message)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "Background task failed for %s",
                        task.message.get("id"),
                    )
                    result = _TaskResult(
                        self._notification(task.message, "background task failed"),
                        retryable=True,
                    )
                finally:
                    self._worker_processing = False

                if result.retryable and task.attempt < 2:
                    delay = min(60, 2**task.attempt * 10)
                    logger.warning(
                        "Background task for %s failed; retrying in %s seconds.",
                        task.message["id"],
                        delay,
                    )
                    if not await self._wait_or_stop(delay):
                        self.task_queue.put_nowait(
                            _QueuedInboxTask(
                                message=task.message,
                                attempt=task.attempt + 1,
                            )
                        )
                    continue
                self.notification_queue.put_nowait(result.notification)
            finally:
                self.task_queue.task_done()

    async def _process_task(self, message: dict[str, Any]) -> _TaskResult:
        if not AUTO_REPLY_ENABLED:
            return _TaskResult(
                self._notification(message, "monitor-only: auto-reply disabled")
            )

        if not is_direct_human_email(message, self.own_address):
            return _TaskResult(
                self._notification(message, "not eligible for auto-reply")
            )

        thread_id = message.get("thread_id", "")
        if self.state.has_replied_to_thread(thread_id):
            return _TaskResult(self._notification(message, "thread already handled"))

        if not self.state.can_reply_now():
            return _TaskResult(
                self._notification(message, "hourly reply limit reached; not sent"),
                retryable=True,
            )

        body = await self._reply_body()
        if AUTO_REPLY_DRY_RUN:
            self.state.record_handled(
                message["id"],
                thread_id,
                count_toward_limit=False,
            )
            await self._save_state()
            await asyncio.to_thread(self._write_audit_record, message, "dry_run")
            return _TaskResult(self._notification(message, "dry-run: reply not sent"))

        try:
            gmail = await self._get_gmail()
            await gmail.asend_auto_reply(message, body)
        except Exception:
            logger.exception("Could not send automatic reply for %s", message["id"])
            return _TaskResult(
                self._notification(message, "auto-reply failed"),
                retryable=True,
            )

        self.state.record_handled(
            message["id"],
            thread_id,
            count_toward_limit=True,
        )
        await self._save_state()
        await asyncio.to_thread(self._write_audit_record, message, "sent")
        return _TaskResult(self._notification(message, "auto-reply sent"))

    async def _reply_body(self) -> str:
        if not AUTO_REPLY_REWRITE_ENABLED or not AUTO_REPLY_BODY:
            return AUTO_REPLY_BODY

        try:
            if self._reply_model is None:
                from src.models import build_chat_model

                self._reply_model = await asyncio.to_thread(build_chat_model)
            prompt = [
                {
                    "role": "system",
                    "content": (
                        "Rewrite the approved email template in a warm, natural, "
                        "concise tone. Preserve its meaning. Do not add facts, "
                        "dates, promises, links, questions, or commitments. "
                        "Return only the email body."
                    ),
                },
                {"role": "user", "content": AUTO_REPLY_BODY},
            ]
            if hasattr(self._reply_model, "ainvoke"):
                response = await self._reply_model.ainvoke(prompt)
            else:
                response = await asyncio.to_thread(self._reply_model.invoke, prompt)
            content = response.content
            if isinstance(content, str):
                generated = content.strip()
            elif isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, str):
                        parts.append(block)
                    elif isinstance(block, dict):
                        parts.append(str(block.get("text", "")))
                generated = "".join(parts).strip()
            else:
                generated = ""
            if generated and len(generated) <= 2000 and "\nTo:" not in generated:
                return generated
            logger.warning("LLM auto-reply rewrite failed validation; using template.")
        except Exception:
            logger.exception("LLM auto-reply rewrite failed; using template.")
        return AUTO_REPLY_BODY

    @staticmethod
    def _notification(message: dict[str, Any], status: str) -> InboxNotification:
        headers = message.get("headers", {})
        return InboxNotification(
            message_id=message["id"],
            sender=headers.get("from", ""),
            subject=headers.get("subject", "(no subject)"),
            received_at=message.get("received_at", ""),
            auto_reply_status=status,
        )

    @staticmethod
    def _write_audit_record(message: dict[str, Any], status: str) -> None:
        """Append safe delivery metadata; do not store email bodies."""
        try:
            AUTO_REPLY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            write_header = not AUTO_REPLY_LOG_PATH.exists()
            with AUTO_REPLY_LOG_PATH.open("a", newline="", encoding="utf-8") as file:
                writer = DictWriter(
                    file,
                    fieldnames=[
                        "sent_at",
                        "status",
                        "recipient",
                        "subject",
                        "thread_id",
                        "message_id",
                    ],
                )
                if write_header:
                    writer.writeheader()
                writer.writerow(
                    {
                        "sent_at": datetime.now(UTC).isoformat(),
                        "status": status,
                        "recipient": message["headers"].get("from", ""),
                        "subject": message["headers"].get("subject", ""),
                        "thread_id": message.get("thread_id", ""),
                        "message_id": message["id"],
                    }
                )
        except OSError:
            logger.exception(
                "Automatic reply completed, but its audit log could not be written."
            )


def _read_recent_auto_replies(limit: int) -> list[dict[str, str]]:
    if not AUTO_REPLY_LOG_PATH.exists():
        return []
    try:
        with AUTO_REPLY_LOG_PATH.open(newline="", encoding="utf-8") as file:
            return list(DictReader(file))[-limit:]
    except OSError:
        logger.exception("Could not read the automatic-reply audit log.")
        return []


async def recent_auto_replies(limit: int = 10) -> list[dict[str, str]]:
    """Return recent local audit records without blocking the event loop."""
    return await asyncio.to_thread(_read_recent_auto_replies, limit)
