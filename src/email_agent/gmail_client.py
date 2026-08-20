import base64
import time
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr

from googleapiclient.discovery import build

from src.email_agent.auth import get_credentials

MAX_BODY_CHARS = 8000  # Cap read-tool output to preserve the LLM context.


def _header_map(msg: dict) -> dict:
    return {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}


def _extract_body(payload: dict) -> str:
    """Walk the MIME tree. Prefer text/plain, fall back to text/html."""
    plain, html = [], []

    def walk(part: dict) -> None:
        mime = part.get("mimeType", "")
        if mime.startswith("multipart/"):
            for sub in part.get("parts", []):
                walk(sub)
            return

        data = part.get("body", {}).get("data")
        if not data:  # Attachments carry an attachmentId instead.
            return

        text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        if mime == "text/plain":
            plain.append(text)
        elif mime == "text/html":
            html.append(text)

    walk(payload)
    return "\n".join(plain) if plain else "\n".join(html)


def _validate_recipients(recipients: str) -> list[str]:
    """Return clean email addresses and block header-injection attempts."""
    if not recipients or any(character in recipients for character in "\r\n"):
        raise ValueError("Recipient addresses must be provided on one line.")

    parsed = getaddresses([recipients])
    addresses = [address.strip() for _, address in parsed if address.strip()]
    if not addresses or any("@" not in address for address in addresses):
        raise ValueError("Provide one or more valid email addresses.")
    return addresses


class GmailClient:
    """Small Gmail wrapper: read operations plus one explicit send operation."""

    def __init__(self) -> None:
        self._service = build("gmail", "v1", credentials=get_credentials())

    def search(self, query: str, max_results: int = 10) -> list[dict]:
        """Return lightweight summaries of messages matching a Gmail query."""
        response = (
            self._service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        summaries = []
        for item in response.get("messages", []):
            msg = self._get(item["id"], format_="metadata")
            headers = _header_map(msg)
            summaries.append(
                {
                    "id": item["id"],
                    "from": headers.get("from", ""),
                    "subject": headers.get("subject", "(no subject)"),
                    "date": headers.get("date", ""),
                    "snippet": msg.get("snippet", ""),
                }
            )
        return summaries

    def read(self, message_id: str) -> dict:
        """Return the full parsed content of one message."""
        msg = self._get(message_id, format_="full")
        headers = _header_map(msg)
        return {
            "id": message_id,
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "subject": headers.get("subject", "(no subject)"),
            "date": headers.get("date", ""),
            "body": _extract_body(msg["payload"])[:MAX_BODY_CHARS],
        }

    def recent_inbox(
        self,
        minutes: int,
        category: str = "all",
        max_results: int = 10,
    ) -> list[dict]:
        """Return Inbox emails newer than an exact local timestamp.

        Gmail's ``newer_than`` query only has day/month/year units, so this
        method deliberately filters Gmail's ``internalDate`` in Python for
        requests such as "last 15 minutes" or "last hour".
        """
        category = category.casefold().strip()
        category_queries = {
            "all": "",
            "primary": " category:primary",
            "social": " category:social",
            "promotions": " category:promotions",
            "updates": " category:updates",
            "forums": " category:forums",
        }
        if category not in category_queries:
            raise ValueError(
                "category must be all, primary, social, promotions, updates, or forums"
            )

        cutoff_ms = int((time.time() - minutes * 60) * 1000)
        response = (
            self._service.users()
            .messages()
            .list(
                userId="me",
                q=f"in:inbox{category_queries[category]}",
                maxResults=max_results,
            )
            .execute()
        )

        messages = []
        for item in response.get("messages", []):
            message = self._get(item["id"], format_="metadata")
            internal_date_ms = int(message.get("internalDate", 0))
            if internal_date_ms < cutoff_ms:
                continue
            headers = _header_map(message)
            messages.append(
                {
                    "id": item["id"],
                    "from": headers.get("from", ""),
                    "subject": headers.get("subject", "(no subject)"),
                    "received_at": datetime.fromtimestamp(
                        internal_date_ms / 1000, tz=UTC
                    ).astimezone().isoformat(timespec="seconds"),
                    "snippet": message.get("snippet", ""),
                }
            )
        return messages

    def send(self, to: str, subject: str, body: str) -> dict:
        """Send a plain-text email and return only safe delivery metadata."""
        if any(character in subject for character in "\r\n"):
            raise ValueError("The subject must be a single line.")
        if not body.strip():
            raise ValueError("The email body cannot be empty.")

        recipients = _validate_recipients(to)
        message = EmailMessage()
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject.strip()
        message.set_content(body)

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        result = (
            self._service.users()
            .messages()
            .send(userId="me", body={"raw": raw})
            .execute()
        )
        return {
            "id": result.get("id"),
            "thread_id": result.get("threadId"),
            "to": recipients,
            "subject": subject.strip(),
        }

    def inbox_candidates(self, max_results: int = 50) -> list[dict]:
        """Return recent inbound Inbox messages with metadata for local policy checks.

        This is intentionally not an LLM tool. It is used only by the
        auto-reply worker, which applies strict sender and header filtering.
        """
        response = (
            self._service.users()
            .messages()
            .list(
                userId="me",
                q="in:inbox -from:me newer_than:2d",
                maxResults=max_results,
            )
            .execute()
        )
        candidates = []
        for item in response.get("messages", []):
            message = self._get(item["id"], format_="metadata")
            candidates.append(
                {
                    "id": item["id"],
                    "thread_id": message.get("threadId", ""),
                    "label_ids": message.get("labelIds", []),
                    "headers": _header_map(message),
                }
            )
        return candidates

    def own_email_address(self) -> str:
        """Return the authenticated Gmail address."""
        profile = self._service.users().getProfile(userId="me").execute()
        return profile["emailAddress"].casefold()

    def send_auto_reply(self, original: dict, body: str) -> dict:
        """Reply once in the original Gmail thread with auto-response headers."""
        headers = original["headers"]
        _, recipient = parseaddr(headers.get("reply-to") or headers.get("from", ""))
        if not recipient:
            raise ValueError("The incoming message has no valid reply address.")

        original_subject = headers.get("subject", "").strip() or "your email"
        subject = (
            original_subject
            if original_subject.casefold().startswith("re:")
            else f"Re: {original_subject}"
        )
        message = EmailMessage()
        message["To"] = recipient
        message["Subject"] = subject
        message["Auto-Submitted"] = "auto-replied"
        message["X-Auto-Response-Suppress"] = "All"

        original_message_id = headers.get("message-id")
        if original_message_id:
            message["In-Reply-To"] = original_message_id
            message["References"] = original_message_id
        message.set_content(body)

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        result = (
            self._service.users()
            .messages()
            .send(
                userId="me",
                body={"raw": raw, "threadId": original["thread_id"]},
            )
            .execute()
        )
        return {"id": result.get("id"), "thread_id": result.get("threadId")}

    def _get(self, message_id: str, format_: str) -> dict:
        return (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format=format_)
            .execute()
        )
