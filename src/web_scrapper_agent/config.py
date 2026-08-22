from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _positive_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


SCRAPER_TIMEOUT_SECONDS = _positive_int("SCRAPER_TIMEOUT_SECONDS", 20, 5)
SCRAPER_MAX_BYTES = _positive_int("SCRAPER_MAX_BYTES", 2_000_000, 65_536)
SCRAPER_MAX_CHARS = _positive_int("SCRAPER_MAX_CHARS", 12_000, 1_000)
SCRAPER_MAX_URLS = _positive_int("SCRAPER_MAX_URLS", 10, 1)
SCRAPER_MAX_CONCURRENCY = _positive_int("SCRAPER_MAX_CONCURRENCY", 5, 1)
SCRAPER_MAX_REDIRECTS = _positive_int("SCRAPER_MAX_REDIRECTS", 5, 0)
SCRAPER_MAX_LINKS = _positive_int("SCRAPER_MAX_LINKS", 50, 1)
SCRAPER_USER_AGENT = os.getenv(
    "SCRAPER_USER_AGENT",
    "FreelanceAutomationBot/1.0 (+https://localhost/; async research agent)",
).strip()

LEAD_OUTPUT_DIR = Path(
    os.getenv("LEAD_OUTPUT_DIR", str(PROJECT_ROOT / "outputs" / "leads"))
)
LEAD_MAX_CANDIDATE_MULTIPLIER = _positive_int(
    "LEAD_MAX_CANDIDATE_MULTIPLIER", 3, 1
)
LEAD_AI_COMMENT_MAX_CHARS = _positive_int("LEAD_AI_COMMENT_MAX_CHARS", 600, 100)
LEAD_AI_COMMENT_CONCURRENCY = _positive_int("LEAD_AI_COMMENT_CONCURRENCY", 3, 1)
LEAD_SEARCH_URL = os.getenv(
    "LEAD_SEARCH_URL", "https://html.duckduckgo.com/html/"
).strip()
LEAD_SEARCH_TIMEOUT_SECONDS = _positive_int("LEAD_SEARCH_TIMEOUT_SECONDS", 20, 5)
LEAD_SEARCH_MAX_QUERIES = _positive_int("LEAD_SEARCH_MAX_QUERIES", 4, 1)
LEAD_SEARCH_RESULTS_PER_QUERY = _positive_int(
    "LEAD_SEARCH_RESULTS_PER_QUERY", 8, 1
)
LEAD_SEARCH_CONCURRENCY = _positive_int("LEAD_SEARCH_CONCURRENCY", 3, 1)

MCP_WEB_SCRAPER_HOST = os.getenv("MCP_WEB_SCRAPER_HOST", "127.0.0.1").strip()
MCP_WEB_SCRAPER_PORT = _positive_int("MCP_WEB_SCRAPER_PORT", 8000, 1)
MCP_WEB_SCRAPER_URL = os.getenv(
    "MCP_WEB_SCRAPER_URL",
    f"http://{MCP_WEB_SCRAPER_HOST}:{MCP_WEB_SCRAPER_PORT}/mcp",
).strip()
MCP_WEB_SCRAPER_CLIENT_TIMEOUT_SECONDS = _positive_int(
    "MCP_WEB_SCRAPER_CLIENT_TIMEOUT_SECONDS", 30, 5
)
MCP_WEB_SCRAPER_CLIENT_READ_TIMEOUT_SECONDS = _positive_int(
    "MCP_WEB_SCRAPER_CLIENT_READ_TIMEOUT_SECONDS", 300, 30
)
