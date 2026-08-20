import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

api = os.environ.get("LLM_API_KEY")
model = os.environ.get("LLM_MODEL_NAME")
url = os.environ.get("LLM_BASE_URL")

# Gmail OAuth: read messages and send messages. Sending is still protected by
# HumanInTheLoopMiddleware; this scope only allows the Gmail API operation.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
CREDENTIALS_PATH = BASE_DIR / os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
TOKEN_PATH = BASE_DIR / os.getenv("GMAIL_TOKEN_PATH", "token.json")


def _positive_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


# Automatic replies are deliberately opt-in. They bypass HITL only after the
# direct-human checks in auto_reply.py pass.
AUTO_REPLY_ENABLED = os.getenv("AUTO_REPLY_ENABLED", "false").lower() == "true"
# Start in observation mode. Set this to false only after a dry-run test.
AUTO_REPLY_DRY_RUN = os.getenv("AUTO_REPLY_DRY_RUN", "true").lower() == "true"
AUTO_REPLY_POLL_SECONDS = _positive_int("AUTO_REPLY_POLL_SECONDS", 60, 30)
AUTO_REPLY_MAX_PER_HOUR = _positive_int("AUTO_REPLY_MAX_PER_HOUR", 10, 1)
AUTO_REPLY_BODY = os.getenv(
    "AUTO_REPLY_BODY",
    "Thank you for your email. I have received your message and will connect with you shortly.",
).strip()
AUTO_REPLY_STATE_PATH = BASE_DIR / os.getenv(
    "AUTO_REPLY_STATE_PATH", ".auto_reply_state.json"
)
AUTO_REPLY_LOG_PATH = BASE_DIR / os.getenv(
    "AUTO_REPLY_LOG_PATH", "auto_reply_log.csv"
)
sender_name = os.getenv("SENDER_NAME", "Yash")