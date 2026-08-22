from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from src.web_scrapper_agent.config import (
    LEAD_SEARCH_CONCURRENCY,
    LEAD_SEARCH_MAX_QUERIES,
    LEAD_SEARCH_RESULTS_PER_QUERY,
    LEAD_SEARCH_TIMEOUT_SECONDS,
    LEAD_SEARCH_URL,
    SCRAPER_USER_AGENT,
)
from src.web_scrapper_agent.lead_models import LeadProfile


class DiscoveryError(RuntimeError):
    """A safe, user-facing business discovery failure."""


class AsyncBusinessURLDiscovery:
    """Find public business URLs from service and location search queries."""

    _SEARCH_HOSTS = {
        "duckduckgo.com",
        "html.duckduckgo.com",
        "www.duckduckgo.com",
    }
    _EXCLUDED_HOSTS = (
        "facebook.com",
        "instagram.com",
        "linkedin.com",
        "tiktok.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "wikipedia.org",
    )

    def __init__(
        self,
        *,
        search_url: str = LEAD_SEARCH_URL,
        timeout_seconds: int = LEAD_SEARCH_TIMEOUT_SECONDS,
        max_queries: int = LEAD_SEARCH_MAX_QUERIES,
        results_per_query: int = LEAD_SEARCH_RESULTS_PER_QUERY,
        max_concurrency: int = LEAD_SEARCH_CONCURRENCY,
        user_agent: str = SCRAPER_USER_AGENT,
    ) -> None:
        self.search_url = search_url
        self.timeout = httpx.Timeout(timeout_seconds)
        self.max_queries = max(1, max_queries)
        self.results_per_query = max(1, results_per_query)
        self.max_concurrency = max(1, max_concurrency)
        self.user_agent = user_agent
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    timeout=self.timeout,
                    follow_redirects=True,
                    headers={
                        "Accept": "text/html,application/xhtml+xml",
                        "User-Agent": self.user_agent,
                    },
                )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def discover(
        self,
        profile: LeadProfile,
        *,
        max_results: int,
    ) -> dict[str, Any]:
        profile.validate()
        max_results = max(1, min(int(max_results), 100))
        queries = self._build_queries(profile)
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def search_one(query: str) -> dict[str, Any]:
            async with semaphore:
                try:
                    return await self._search_query(query)
                except DiscoveryError as error:
                    return {"query": query, "urls": [], "error": str(error)}
                except Exception:
                    return {
                        "query": query,
                        "urls": [],
                        "error": "Unexpected business discovery failure.",
                    }

        results = await asyncio.gather(*(search_one(query) for query in queries))
        urls: list[str] = []
        seen: set[str] = set()
        errors: list[str] = []
        for result in results:
            if result.get("error"):
                errors.append(f"{result['query']}: {result['error']}")
            for url in result.get("urls", []):
                if url not in seen and len(urls) < max_results:
                    seen.add(url)
                    urls.append(url)

        if not urls and errors:
            raise DiscoveryError("No business URLs were discovered. " + " | ".join(errors))

        return {
            "queries": queries,
            "source_urls": urls,
            "result_count": len(urls),
            "errors": errors,
        }

    async def _search_query(self, query: str) -> dict[str, Any]:
        client = await self._get_client()
        try:
            response = await client.get(
                self.search_url,
                params={"q": query},
            )
        except httpx.TimeoutException as error:
            raise DiscoveryError("The search request timed out.") from error
        except httpx.RequestError as error:
            raise DiscoveryError("Could not connect to the search provider.") from error

        if response.status_code >= 400:
            raise DiscoveryError(f"The search provider returned HTTP {response.status_code}.")
        content_type = response.headers.get("content-type", "").lower()
        if content_type and "html" not in content_type:
            raise DiscoveryError("The search provider returned a non-HTML response.")

        soup = BeautifulSoup(response.text, "html.parser")
        urls: list[str] = []
        seen: set[str] = set()
        anchors = soup.select("a.result__a") or soup.select("a[href]")
        for anchor in anchors:
            url = self._result_url(str(anchor.get("href", "")))
            if url and url not in seen and self._is_business_url(url):
                seen.add(url)
                urls.append(url)
                if len(urls) >= self.results_per_query:
                    break
        return {"query": query, "urls": urls}

    def _build_queries(self, profile: LeadProfile) -> list[str]:
        city, state = profile.city_state()
        location = f'"{city}" "{state}"'
        queries: list[str] = []
        for service in profile.services:
            queries.append(f'"{service}" businesses in {location}')
        if profile.target_sectors:
            for sector in profile.target_sectors:
                queries.append(f'"{sector}" businesses needing "{profile.services[0]}" in {location}')
        queries.append(f'"{profile.profession}" companies in {location}')
        return list(dict.fromkeys(queries))[: self.max_queries]

    def _result_url(self, href: str) -> str:
        if not href:
            return ""
        href = urljoin(self.search_url, href)
        parsed = urlparse(href)
        if parsed.hostname in self._SEARCH_HOSTS:
            wrapped = parse_qs(parsed.query).get("uddg", [""])[0]
            href = unquote(wrapped)
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        return href.split("#", 1)[0].rstrip("/")

    @classmethod
    def _is_business_url(cls, url: str) -> bool:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if not hostname or hostname in cls._SEARCH_HOSTS:
            return False
        if any(hostname == item or hostname.endswith(f".{item}") for item in cls._EXCLUDED_HOSTS):
            return False
        lowered = url.lower()
        if any(marker in lowered for marker in ("/search", "/login", "/privacy", "/terms")):
            return False
        return not lowered.endswith((".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip"))
