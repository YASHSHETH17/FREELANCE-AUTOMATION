from __future__ import annotations

from datetime import timedelta

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from src.web_scrapper_agent.config import (
    MCP_WEB_SCRAPER_CLIENT_READ_TIMEOUT_SECONDS,
    MCP_WEB_SCRAPER_CLIENT_TIMEOUT_SECONDS,
    MCP_WEB_SCRAPER_URL,
)


def build_scraper_mcp_client() -> MultiServerMCPClient:
    """Create an async client for the shared URL-scraping MCP server."""
    return MultiServerMCPClient(
        {
            "scraper": {
                "transport": "streamable_http",
                "url": MCP_WEB_SCRAPER_URL,
                "timeout": timedelta(
                    seconds=MCP_WEB_SCRAPER_CLIENT_TIMEOUT_SECONDS
                ),
                "sse_read_timeout": timedelta(
                    seconds=MCP_WEB_SCRAPER_CLIENT_READ_TIMEOUT_SECONDS
                ),
            }
        }
    )


async def load_scraping_tools(
    client: MultiServerMCPClient | None = None,
) -> tuple[MultiServerMCPClient, list[BaseTool]]:
    """Load scraping tools asynchronously for any LangChain agent."""
    client = client or build_scraper_mcp_client()
    tools = await client.get_tools(server_name="scraper")
    if not tools:
        raise RuntimeError("The web-scraping MCP server exposed no tools.")
    return client, tools
