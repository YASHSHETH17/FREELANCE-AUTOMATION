from langchain_core.tools import tool

from gmail_client import GmailClient

# First import triggers the browser OAuth flow if token.json is missing or lacks
# the newly required gmail.send permission.
_gmail = GmailClient()


@tool
def search_emails(query: str, max_results: int = 10):
    """Search Gmail using Gmail query syntax.

    Args:
        query: Gmail query, for example ``from:hr@acme.com is:unread``.
        max_results: Number of messages to return, from 1 to 25.
    """
    max_results = max(1, min(int(max_results), 25))
    try:
        matches = _gmail.search(query, max_results=max_results)
    except Exception as error:
        return f"Search failed: {error}"
    return matches if matches else f"No emails matched: {query}"


@tool
def read_email(message_id: str):
    """Fetch the full content of one email returned by ``search_emails``."""
    try:
        return _gmail.read(message_id)
    except Exception as error:
        return f"Could not read message {message_id}: {error}"


@tool
def recent_inbox_emails(
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
    minutes = max(1, min(int(minutes), 1440))
    max_results = max(1, min(int(max_results), 100))
    try:
        messages = _gmail.recent_inbox(
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
def send_email(to: str, subject: str, body: str):
    """Send a plain-text email.

    This tool is ALWAYS paused by the application's human-in-the-loop
    middleware before it executes. ``to`` may contain comma-separated email
    addresses. Use only after the user explicitly asks to send an email.
    """
    try:
        sent = _gmail.send(to=to, subject=subject, body=body)
    except Exception as error:
        return f"Email was not sent: {error}"
    return f"Email sent successfully: {sent}"
