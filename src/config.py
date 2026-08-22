import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _positive_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default

LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME")
LLM_BASE_URL = os.getenv("LLM_BASE_URL")
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "none").strip() or None
LLM_MAX_COMPLETION_TOKENS = _positive_int(
    "LLM_MAX_COMPLETION_TOKENS", 1024, 64
)
LLM_TIMEOUT_SECONDS = _positive_int("LLM_TIMEOUT_SECONDS", 120, 10)
