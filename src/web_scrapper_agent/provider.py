from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from src.web_scrapper_agent.config import (
    SCRAPER_MAX_BYTES,
    SCRAPER_MAX_CHARS,
    SCRAPER_MAX_CONCURRENCY,
    SCRAPER_MAX_LINKS,
    SCRAPER_MAX_REDIRECTS,
    SCRAPER_MAX_URLS,
    SCRAPER_TIMEOUT_SECONDS,
    SCRAPER_USER_AGENT,
)


class ScrapingError(RuntimeError):
    """A safe, user-facing scraping failure."""


class AsyncWebScraper:
    """Bounded asynchronous HTML scraper with SSRF protections."""

    _ALLOWED_SCHEMES = {"http", "https"}
    _REMOVED_TAGS = {
        "aside",
        "canvas",
        "footer",
        "form",
        "header",
        "iframe",
        "nav",
        "noscript",
        "script",
        "style",
        "svg",
        "template",
    }

    def __init__(
        self,
        *,
        timeout_seconds: int = SCRAPER_TIMEOUT_SECONDS,
        max_bytes: int = SCRAPER_MAX_BYTES,
        max_chars: int = SCRAPER_MAX_CHARS,
        max_urls: int = SCRAPER_MAX_URLS,
        max_concurrency: int = SCRAPER_MAX_CONCURRENCY,
        max_redirects: int = SCRAPER_MAX_REDIRECTS,
        max_links: int = SCRAPER_MAX_LINKS,
        user_agent: str = SCRAPER_USER_AGENT,
    ) -> None:
        self.timeout = httpx.Timeout(timeout_seconds)
        self.max_bytes = max_bytes
        self.max_chars = max_chars
        self.max_urls = max_urls
        self.max_concurrency = max_concurrency
        self.max_redirects = max_redirects
        self.max_links = max_links
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
                    follow_redirects=False,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8",
                        "User-Agent": self.user_agent,
                    },
                )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def scrape_url(
        self,
        url: str,
        *,
        max_chars: int = SCRAPER_MAX_CHARS,
        include_links: bool = False,
    ) -> dict[str, Any]:
        original_url = self._validate_url(url)
        final_url, status_code, content_type, html = await self._fetch_html(
            original_url
        )
        structured_data = self._structured_data(
            BeautifulSoup(html, "html.parser")
        )
        soup = self._build_soup(html)
        text = self._extract_text(soup)
        bounded_chars = self._bounded_chars(max_chars)
        truncated = len(text) > bounded_chars
        text = text[:bounded_chars].rstrip()

        links = self._extract_links(soup, final_url, self.max_links) if include_links else []
        return {
            "ok": True,
            "url": original_url,
            "final_url": final_url,
            "status_code": status_code,
            "content_type": content_type,
            "fetched_at": datetime.now(UTC).isoformat(),
            "title": self._text_of(soup.title),
            "description": self._meta_content(soup, "description"),
            "canonical_url": self._canonical_url(soup, final_url),
            "structured_data": structured_data,
            "text": text,
            "text_length": len(text),
            "truncated": truncated,
            "links": links,
        }

    async def scrape_urls(
        self,
        urls: list[str],
        *,
        max_chars: int = SCRAPER_MAX_CHARS,
        max_concurrency: int = SCRAPER_MAX_CONCURRENCY,
        include_links: bool = False,
    ) -> dict[str, Any]:
        if not urls:
            raise ScrapingError("Provide at least one URL.")
        if len(urls) > self.max_urls:
            raise ScrapingError(f"A maximum of {self.max_urls} URLs can be scraped at once.")

        semaphore = asyncio.Semaphore(
            max(1, min(int(max_concurrency), self.max_concurrency))
        )

        async def scrape_one(url: str) -> dict[str, Any]:
            async with semaphore:
                try:
                    return await self.scrape_url(
                        url,
                        max_chars=max_chars,
                        include_links=include_links,
                    )
                except ScrapingError as error:
                    return {"ok": False, "url": url, "error": str(error)}
                except Exception:
                    return {"ok": False, "url": url, "error": "Unexpected scraping failure."}

        results = await asyncio.gather(*(scrape_one(url) for url in urls))
        return {
            "ok": True,
            "results": results,
            "result_count": len(results),
            "success_count": sum(bool(item.get("ok")) for item in results),
        }

    async def extract_links(
        self,
        url: str,
        *,
        max_links: int = SCRAPER_MAX_LINKS,
    ) -> dict[str, Any]:
        original_url = self._validate_url(url)
        final_url, status_code, _content_type, html = await self._fetch_html(
            original_url
        )
        soup = self._build_soup(html)
        links = self._extract_links(soup, final_url, max(1, min(int(max_links), self.max_links)))
        return {
            "ok": True,
            "url": original_url,
            "final_url": final_url,
            "status_code": status_code,
            "links": links,
            "link_count": len(links),
        }

    async def _fetch_html(self, url: str) -> tuple[str, int, str, str]:
        current_url = url
        client = await self._get_client()

        for redirect_number in range(self.max_redirects + 1):
            await self._validate_public_host(current_url)
            try:
                async with client.stream("GET", current_url) as response:
                    if 300 <= response.status_code < 400:
                        location = response.headers.get("location")
                        if not location:
                            raise ScrapingError("The page returned a redirect without a location.")
                        if redirect_number >= self.max_redirects:
                            raise ScrapingError("The page exceeded the redirect limit.")
                        current_url = self._validate_url(urljoin(current_url, location))
                        continue

                    if response.status_code >= 400:
                        raise ScrapingError(
                            f"The page returned HTTP {response.status_code}."
                        )

                    content_type = response.headers.get("content-type", "").lower()
                    if content_type and not any(
                        supported in content_type
                        for supported in ("text/html", "application/xhtml+xml", "text/plain")
                    ):
                        raise ScrapingError(
                            f"Unsupported content type: {content_type.split(';', 1)[0]}"
                        )

                    content_length = response.headers.get("content-length")
                    if content_length and int(content_length) > self.max_bytes:
                        raise ScrapingError("The page is larger than the configured size limit.")

                    chunks: list[bytes] = []
                    total_bytes = 0
                    async for chunk in response.aiter_bytes():
                        total_bytes += len(chunk)
                        if total_bytes > self.max_bytes:
                            raise ScrapingError("The page exceeded the configured size limit.")
                        chunks.append(chunk)

                    raw_body = b"".join(chunks)
                    encoding = response.encoding or "utf-8"
                    return (
                        current_url,
                        response.status_code,
                        content_type.split(";", 1)[0],
                        raw_body.decode(encoding, errors="replace"),
                    )
            except httpx.TimeoutException as error:
                raise ScrapingError("The page request timed out.") from error
            except httpx.RequestError as error:
                raise ScrapingError("Could not connect to the page.") from error

        raise ScrapingError("The page exceeded the redirect limit.")

    async def _validate_public_host(self, url: str) -> None:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            raise ScrapingError("The URL has no hostname.")

        lowered_hostname = hostname.lower().rstrip(".")
        if lowered_hostname in {
            "localhost",
            "localhost.localdomain",
            "ip6-localhost",
            "ip6-loopback",
        } or lowered_hostname.endswith(".localhost"):
            raise ScrapingError("Local, private, or reserved addresses are not allowed.")

        try:
            literal_ip = ipaddress.ip_address(lowered_hostname)
        except ValueError:
            literal_ip = None
        if literal_ip is not None:
            if not literal_ip.is_global:
                raise ScrapingError("Local, private, or reserved addresses are not allowed.")
            return

        try:
            addresses = await asyncio.to_thread(
                socket.getaddrinfo,
                hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except (OSError, ValueError) as error:
            raise ScrapingError("Could not resolve the URL hostname.") from error

        resolved_ips = {entry[4][0] for entry in addresses}
        if not resolved_ips:
            raise ScrapingError("The URL hostname did not resolve.")

        for address in resolved_ips:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise ScrapingError("Local, private, or reserved addresses are not allowed.")

    @classmethod
    def _validate_url(cls, url: str) -> str:
        if not isinstance(url, str) or len(url) > 2048:
            raise ScrapingError("The URL must be a string no longer than 2048 characters.")
        normalized = url.strip()
        parsed = urlparse(normalized)
        if parsed.scheme.lower() not in cls._ALLOWED_SCHEMES or not parsed.hostname:
            raise ScrapingError("Only public HTTP and HTTPS URLs are supported.")
        if parsed.username or parsed.password:
            raise ScrapingError("URLs containing embedded credentials are not allowed.")
        try:
            parsed.port
        except ValueError as error:
            raise ScrapingError("The URL contains an invalid port.") from error
        return normalized

    def _build_soup(self, html: str) -> BeautifulSoup:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(self._REMOVED_TAGS):
            tag.decompose()
        return soup

    @staticmethod
    def _extract_text(soup: BeautifulSoup) -> str:
        root = soup.find("article") or soup.find("main") or soup.body or soup
        lines: list[str] = []
        for raw_line in root.get_text("\n").splitlines():
            line = " ".join(raw_line.split())
            if line and (not lines or lines[-1] != line):
                lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _text_of(element: Any) -> str:
        return " ".join(element.get_text(" ").split()) if element else ""

    @staticmethod
    def _meta_content(soup: BeautifulSoup, name: str) -> str:
        tag = soup.find("meta", attrs={"name": name})
        return str(tag.get("content", "")).strip() if tag else ""

    @staticmethod
    def _canonical_url(soup: BeautifulSoup, fallback: str) -> str:
        tag = soup.find("link", rel=lambda value: value and "canonical" in value)
        return urljoin(fallback, str(tag.get("href", "")).strip()) if tag else ""

    @staticmethod
    def _structured_data(soup: BeautifulSoup) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = script.string or script.get_text()
            try:
                parsed = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            candidates = parsed if isinstance(parsed, list) else [parsed]
            for candidate in candidates:
                if isinstance(candidate, dict):
                    graph = candidate.get("@graph")
                    if isinstance(graph, list):
                        records.extend(item for item in graph if isinstance(item, dict))
                    else:
                        records.append(candidate)
                if len(records) >= 10:
                    return records[:10]
        return records[:10]

    @staticmethod
    def _extract_links(
        soup: BeautifulSoup,
        base_url: str,
        max_links: int,
    ) -> list[dict[str, str]]:
        links: list[dict[str, str]] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href", "")).strip()
            target = urljoin(base_url, href)
            parsed = urlparse(target)
            if parsed.scheme not in {"http", "https", "mailto", "tel"} or target in seen:
                continue
            seen.add(target)
            links.append(
                {
                    "url": target,
                    "text": " ".join(anchor.get_text(" ").split())[:300],
                }
            )
            if len(links) >= max_links:
                break
        return links

    def _bounded_chars(self, requested: int) -> int:
        try:
            return max(1_000, min(int(requested), self.max_chars))
        except (TypeError, ValueError) as error:
            raise ScrapingError("max_chars must be a positive integer.") from error
