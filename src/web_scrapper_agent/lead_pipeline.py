from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from src.web_scrapper_agent.config import (
    LEAD_MAX_CANDIDATE_MULTIPLIER,
    LEAD_SEARCH_RESULTS_PER_QUERY,
)
from src.web_scrapper_agent.discovery import AsyncBusinessURLDiscovery
from src.web_scrapper_agent.lead_extractor import extract_business_lead
from src.web_scrapper_agent.lead_models import BusinessLead, LeadProfile
from src.web_scrapper_agent.provider import AsyncWebScraper, ScrapingError


class AsyncLeadPipeline:
    """Discover candidate pages, scrape them concurrently, and qualify leads."""

    def __init__(
        self,
        scraper: AsyncWebScraper | None = None,
        discovery: AsyncBusinessURLDiscovery | None = None,
    ) -> None:
        self.scraper = scraper or AsyncWebScraper()
        self.discovery = discovery or AsyncBusinessURLDiscovery()

    async def close(self) -> None:
        await self.discovery.close()
        await self.scraper.close()

    async def collect(
        self,
        profile: LeadProfile,
        *,
        max_leads: int = 20,
        max_concurrency: int = 5,
    ) -> dict[str, Any]:
        try:
            profile.validate()
        except ValueError as error:
            raise ScrapingError(str(error)) from error
        max_leads = max(1, min(int(max_leads), 100))

        discovery = await self.discovery.discover(
            profile,
            max_results=max(
                self.scraper.max_urls,
                max_leads * LEAD_MAX_CANDIDATE_MULTIPLIER,
                LEAD_SEARCH_RESULTS_PER_QUERY,
            ),
        )
        source_urls = discovery.get("source_urls", [])
        if not source_urls:
            return {
                "ok": True,
                "profile": profile.as_text(),
                "queries": discovery.get("queries", []),
                "source_urls": [],
                "candidate_count": 0,
                "lead_count": 0,
                "leads": [],
                "discovery_errors": discovery.get("errors", []),
            }

        seed_pages = await self._scrape_in_batches(
            source_urls,
            max_chars=6_000,
            max_concurrency=max_concurrency,
            include_links=True,
        )
        candidate_urls = self._candidate_urls(
            source_urls,
            seed_pages.get("results", []),
            max_candidates=max_leads * LEAD_MAX_CANDIDATE_MULTIPLIER,
        )
        pages = await self._scrape_in_batches(
            candidate_urls,
            max_chars=12_000,
            max_concurrency=max_concurrency,
            include_links=True,
        )

        leads: list[BusinessLead] = []
        seen_keys: set[str] = set()
        for page in pages.get("results", []):
            if not page.get("ok"):
                continue
            lead = extract_business_lead(page, profile)
            key = self._dedupe_key(lead)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            leads.append(lead)

        leads.sort(key=lambda lead: (lead.fit_score, lead.confidence), reverse=True)
        leads = leads[:max_leads]
        return {
            "ok": True,
            "profile": profile.as_text(),
            "queries": discovery.get("queries", []),
            "source_urls": source_urls,
            "candidate_count": len(candidate_urls),
            "lead_count": len(leads),
            "leads": [lead.to_dict() for lead in leads],
            "discovery_errors": discovery.get("errors", []),
        }

    async def _scrape_in_batches(
        self,
        urls: list[str],
        *,
        max_chars: int,
        max_concurrency: int,
        include_links: bool,
    ) -> dict[str, Any]:
        """Scrape more URLs than one MCP call allows without exceeding limits."""
        results: list[dict[str, Any]] = []
        for start in range(0, len(urls), self.scraper.max_urls):
            batch = urls[start : start + self.scraper.max_urls]
            payload = await self.scraper.scrape_urls(
                batch,
                max_chars=max_chars,
                max_concurrency=max_concurrency,
                include_links=include_links,
            )
            results.extend(payload.get("results", []))
        return {
            "ok": True,
            "results": results,
            "result_count": len(results),
            "success_count": sum(bool(item.get("ok")) for item in results),
        }

    def _candidate_urls(
        self,
        source_urls: list[str],
        seed_pages: list[Any],
        *,
        max_candidates: int,
    ) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()

        def add(url: str) -> None:
            normalized = url.strip()
            if (
                normalized
                and normalized not in seen
                and self._is_candidate_url(normalized)
                and len(candidates) < max_candidates
            ):
                seen.add(normalized)
                candidates.append(normalized)

        for source_url in source_urls:
            add(source_url)
        for page in seed_pages:
            if not isinstance(page, dict) or not page.get("ok"):
                continue
            links = page.get("links", [])
            if isinstance(links, list):
                for link in links:
                    if isinstance(link, dict):
                        add(str(link.get("url", "")))
        return candidates

    @staticmethod
    def _is_candidate_url(url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        lowered = url.lower()
        excluded = (
            "facebook.com",
            "instagram.com",
            "linkedin.com",
            "tiktok.com",
            "twitter.com",
            "x.com",
            "/login",
            "/privacy",
            "/terms",
            "/cookie",
        )
        if any(item in lowered for item in excluded):
            return False
        return not lowered.endswith((".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip"))

    @staticmethod
    def _dedupe_key(lead: BusinessLead) -> str:
        return "|".join(
            value.lower().strip()
            for value in (lead.website, lead.business_name, lead.phone)
            if value
        )
