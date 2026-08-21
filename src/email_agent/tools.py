from __future__ import annotations

import asyncio

from langchain_core.tools import tool

from src.email_agent.gmail_client import GmailClient

_gmail: GmailClient | None = None
_gmail_lock: asyncio.Lock | None = None


async def _get_gmail() -> GmailClient:
    """Lazily create Gmail off the event loop so imports never trigger OAuth."""
    global _gmail, _gmail_lock
    if _gmail is not None:
        return _gmail
    if _gmail_lock is None:
        _gmail_lock = asyncio.Lock()
    async with _gmail_lock:
        if _gmail is None:
            _gmail = await asyncio.to_thread(GmailClient)
    return _gmail


@tool
async def search_emails(query: str, max_results: int = 10):
    """Search Gmail using Gmail query syntax.

    Args:
        query: Gmail query, for example ``from:hr@acme.com is:unread``.
        max_results: Number of messages to return, from 1 to 25.
    """
    try:
        max_results = max(1, min(int(max_results), 25))
        gmail = await _get_gmail()
        matches = await gmail.asearch(query, max_results=max_results)
    except Exception as error:
        return f"Search failed: {error}"
    return matches if matches else f"No emails matched: {query}"


@tool
async def read_email(message_id: str):
    """Fetch the full content of one email returned by ``search_emails``."""
    try:
        gmail = await _get_gmail()
        return await gmail.aread(message_id)
    except Exception as error:
        return f"Could not read message {message_id}: {error}"


@tool
async def recent_inbox_emails(
    minutes: int = 60,
    category: str = "all",
    max_results: int = 10,
):
    """Find emails received in the last exact number of minutes.

    Use this tool, not ``search_emails``, whenever the user says recent,
    latest, "last hour", or "last N minutes". It uses Gmail's true received
    timestamp, so it correctly supports hour and minute windows.

    Args:
        minutes: Lookback window in minutes (1-1440).
        category: One of all, primary, social, promotions, updates, or forums.
        max_results: Number of messages to inspect and return (1-100).
    """
    try:
        minutes = max(1, min(int(minutes), 1440))
        max_results = max(1, min(int(max_results), 100))
        gmail = await _get_gmail()
        messages = await gmail.arecent_inbox(
            minutes=minutes,
            category=category,
            max_results=max_results,
        )
    except Exception as error:
        return f"Recent-email lookup failed: {error}"

    window = f"the last {minutes} minute(s)"
    category_label = "Inbox" if category == "all" else f"{category.title()} inbox"
    return messages if messages else f"No messages in {category_label} during {window}."


@tool
async def send_email(to: str, subject: str, body: str):
    """Send a plain-text email.

    This tool is ALWAYS paused by the application's human-in-the-loop
    middleware before it executes. ``to`` may contain comma-separated email
    addresses. Use only after the user explicitly asks to send an email.
    """
    try:
        gmail = await _get_gmail()
        sent = await gmail.asend(to=to, subject=subject, body=body)
    except Exception as error:
        return f"Email was not sent: {error}"
    return f"Email sent successfully: {sent}"