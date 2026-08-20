from __future__ import annotations

import json
import logging
import threading
import time
from csv import DictReader, DictWriter
from datetime import UTC, datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from config import (
    AUTO_REPLY_BODY,
    AUTO_REPLY_DRY_RUN,
    AUTO_REPLY_MAX_PER_HOUR,
    AUTO_REPLY_POLL_SECONDS,
    AUTO_REPLY_LOG_PATH,
    AUTO_REPLY_STATE_PATH,
)
from gmail_client import GmailClient

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


class AutoReplyState:
    """Small local ledger that prevents duplicate replies after a restart."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = {
            "initialized": False,
            "seen_message_ids": [],
            "replied_thread_ids": [],
            "reply_timestamps": [],
        }
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except (OSError, json.JSONDecodeError):
            logger.warning("Could not read %s; starting a fresh safe baseline.", self.path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    @property
    def initialized(self) -> bool:
        return bool(self.data["initialized"])

    def has_seen(self, message_id: str) -> bool:
        return message_id in self.data["seen_message_ids"]

    def has_replied_to_thread(self, thread_id: str) -> bool:
        return thread_id in self.data["replied_thread_ids"]

    def mark_seen(self, message_id: str) -> None:
        ids = self.data["seen_message_ids"]
        if message_id not in ids:
            ids.append(message_id)
        self.data["seen_message_ids"] = ids[-2000:]

    def record_reply(self, message_id: str, thread_id: str) -> None:
        self.mark_seen(message_id)
        threads = self.data["replied_thread_ids"]
        if thread_id not in threads:
            threads.append(thread_id)
        self.data["replied_thread_ids"] = threads[-2000:]
        now = time.time()
        timestamps = [
            stamp for stamp in self.data["reply_timestamps"] if stamp >= now - 3600
        ]
        timestamps.append(now)
        self.data["reply_timestamps"] = timestamps

    def can_reply_now(self) -> bool:
        now = time.time()
        self.data["reply_timestamps"] = [
            stamp for stamp in self.data["reply_timestamps"] if stamp >= now - 3600
        ]
        return len(self.data["reply_timestamps"]) < AUTO_REPLY_MAX_PER_HOUR

    def establish_baseline(self, messages: list[dict[str, Any]]) -> None:
        for message in messages:
            self.mark_seen(message["id"])
        self.data["initialized"] = True
        self.save()


def is_direct_human_email(message: dict[str, Any], own_address: str) -> bool:
    """Allow only a conservative subset of personal, inbound email."""
    headers = {key.casefold(): value for key, value in message["headers"].items()}
    _, sender = parseaddr(headers.get("reply-to") or headers.get("from", ""))
    sender = sender.casefold()
    if not sender or sender == own_address:
        return False

    local_part = sender.partition("@")[0]
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
    """Poll Gmail for new direct human emails and send one reply per thread."""

    def __init__(self) -> None:
        self.gmail = GmailClient()
        self.state = AutoReplyState(AUTO_REPLY_STATE_PATH)
        self.own_address = self.gmail.own_email_address()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            name="gmail-auto-reply",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=5)

    def _run(self) -> None:
        try:
            current_messages = self.gmail.inbox_candidates()
            if not self.state.initialized:
                # Never auto-reply to an old Inbox when this feature is enabled.
                self.state.establish_baseline(current_messages)
                logger.info("Auto-reply baseline created; waiting for new messages.")

            while not self.stop_event.wait(AUTO_REPLY_POLL_SECONDS):
                self._check_for_new_messages()
        except Exception:
            logger.exception("Auto-reply monitor stopped because of an unexpected error.")

    def _check_for_new_messages(self) -> None:
        for message in self.gmail.inbox_candidates():
            if self.state.has_seen(message["id"]):
                continue

            if not is_direct_human_email(message, self.own_address):
                self.state.mark_seen(message["id"])
                self.state.save()
                continue
            if self.state.has_replied_to_thread(message["thread_id"]):
                self.state.mark_seen(message["id"])
                self.state.save()
                continue
            if not self.state.can_reply_now():
                logger.warning("Auto-reply hourly limit reached; retrying later.")
                return

            if AUTO_REPLY_DRY_RUN:
                # Treat the email as handled so switching to live mode never
                # replies later to a message that was only meant for testing.
                self.state.mark_seen(message["id"])
                self.state.save()
                self._write_audit_record(message, status="dry_run")
                sender = message["headers"].get("from", "unknown sender")
                subject = message["headers"].get("subject", "(no subject)")
                logger.info(
                    "AUTO-REPLY DRY RUN | would reply to=%s | subject=%r | body=%r",
                    sender,
                    subject,
                    AUTO_REPLY_BODY,
                )
                continue

            try:
                self.gmail.send_auto_reply(message, AUTO_REPLY_BODY)
            except Exception:
                # Keep the message unmarked, so a temporary Gmail error retries.
                logger.exception("Could not send automatic reply for %s", message["id"])
                continue

            self.state.record_reply(message["id"], message["thread_id"])
            self.state.save()
            self._write_audit_record(message, status="sent")
            sender = message["headers"].get("from", "unknown sender")
            subject = message["headers"].get("subject", "(no subject)")
            logger.info(
                "AUTO-REPLY SENT | to=%s | subject=%r | thread=%s",
                sender,
                subject,
                message["thread_id"],
            )

    def _write_audit_record(self, message: dict[str, Any], status: str) -> None:
        """Append safe delivery metadata; do not store the email body."""
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
                        "thread_id": message["thread_id"],
                        "message_id": message["id"],
                    }
                )
        except OSError:
            logger.exception("Automatic reply was sent, but its audit log could not be written.")


def recent_auto_replies(limit: int = 10) -> list[dict[str, str]]:
    """Return the most recent local automatic-reply audit records."""
    if not AUTO_REPLY_LOG_PATH.exists():
        return []
    try:
        with AUTO_REPLY_LOG_PATH.open(newline="", encoding="utf-8") as file:
            return list(DictReader(file))[-limit:]
    except OSError:
        logger.exception("Could not read the automatic-reply audit log.")
        return []
