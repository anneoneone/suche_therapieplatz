"""Shared plumbing for residential-only HTML scrapers.

``PsychInfoSource`` and ``TherapieDeSource`` follow the same shape: paginated
server-rendered list pages, each linking to a detail page. Only the
URL-building + CSS selector strategy differs. This module factors out the
robots.txt check, rate-limiting, fetching, and pagination loop so the
concrete sources just declare how to build URLs and parse HTML.

All concrete subclasses are **residential-only**: German healthcare
directories block cloud/datacenter IPs via WAF. Running these from a CI
runner will return empty results; run from a laptop on a residential IP.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
import logging
import re
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
from bs4.element import Tag
import httpx

from therapist_finder.models import TherapistData
from therapist_finder.sources.base import SearchParams, TherapistSource
from therapist_finder.sources.rate_limit import RateLimiter

logger = logging.getLogger(__name__)


@dataclass
class ListEntry:
    """Partial data extracted from a single result on a list page."""

    name: str
    detail_url: str | None = None
    address: str | None = None
    telefon: str | None = None

    def to_therapist(self, source: str) -> TherapistData:
        """Convert list-only data to a :class:`TherapistData`."""
        return TherapistData(
            name=self.name,
            address=self.address,
            telefon=self.telefon,
            sources=[source],
        )


class HTMLScraper(TherapistSource):
    """Shared base for paginated HTML-scraping sources (residential only)."""

    #: Human-readable label logged on every run.
    base_url: str

    def __init__(
        self,
        user_agent: str,
        base_url: str,
        min_delay_seconds: float = 2.0,
        client: httpx.Client | None = None,
        respect_robots_txt: bool = True,
    ) -> None:
        """Initialise the scraper with HTTP defaults and rate limiting."""
        self.base_url = base_url.rstrip("/")
        self._user_agent = user_agent
        self._client = client or httpx.Client(
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
            },
            timeout=30.0,
            follow_redirects=True,
        )
        self._owns_client = client is None
        self._rate_limiter = RateLimiter(min_delay_seconds=min_delay_seconds)
        self._respect_robots = respect_robots_txt
        self._robots: RobotFileParser | None = None

    def search(self, params: SearchParams) -> list[TherapistData]:
        """Paginate the list, follow each detail page, accumulate results."""
        results: list[TherapistData] = []
        for page_url in self._iter_list_urls(params):
            list_html = self._fetch(page_url)
            if list_html is None:
                continue
            entries = self._parse_list_page(list_html)
            if not entries:
                logger.info(
                    "%s: no entries on %s — stopping pagination", self.name, page_url
                )
                break
            for entry in entries:
                if entry.detail_url:
                    detail_html = self._fetch(entry.detail_url)
                    if detail_html is not None:
                        results.append(
                            self._parse_detail_page(detail_html, fallback=entry)
                        )
                        continue
                results.append(entry.to_therapist(self.name))
        logger.info("%s: returning %d providers", self.name, len(results))
        return results

    def close(self) -> None:
        """Close the underlying HTTP client if we own it."""
        if self._owns_client:
            self._client.close()

    @abstractmethod
    def _iter_list_urls(self, params: SearchParams) -> list[str]:
        """Yield the list-page URLs to fetch for ``params``."""

    @abstractmethod
    def _parse_list_page(self, html: str) -> list[ListEntry]:
        """Return the list entries found on one list-page HTML."""

    @abstractmethod
    def _parse_detail_page(self, html: str, *, fallback: ListEntry) -> TherapistData:
        """Merge a provider's detail page with the list fallback data."""

    def _fetch(self, url: str) -> str | None:
        if not self._allowed(url):
            return None
        self._rate_limiter.wait(_host(url))
        try:
            resp = self._client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("%s fetch failed for %s: %s", self.name, url, e)
            return None
        return resp.text

    def _allowed(self, url: str) -> bool:
        if not self._respect_robots:
            return True
        if self._robots is None:
            self._robots = RobotFileParser()
            robots_url = urljoin(self.base_url + "/", "robots.txt")
            self._rate_limiter.wait(_host(robots_url))
            try:
                resp = self._client.get(robots_url)
                if resp.status_code == 200:
                    self._robots.parse(resp.text.splitlines())
                else:
                    self._robots.parse([])
            except httpx.HTTPError:
                self._robots.parse([])
        return self._robots.can_fetch(self._user_agent, url)


# -- shared HTML helpers -----------------------------------------------------

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def text_of(soup: Tag, selectors: str) -> str:
    """Return whitespace-cleaned text of the first element matching ``selectors``."""
    el = soup.select_one(selectors)
    return el.get_text(" ", strip=True) if el else ""


def clean(text: str) -> str:
    """Collapse whitespace and strip."""
    return re.sub(r"\s+", " ", text or "").strip()


def extract_list(soup: BeautifulSoup, selectors: str) -> list[str]:
    """Parse a comma/semicolon/slash-separated field into a list."""
    text = text_of(soup, selectors)
    return [p.strip() for p in re.split(r"[,;/]", text) if p.strip()]


def extract_email_from_html(html: str) -> str | None:
    """Pull the first plausible email address out of raw HTML."""
    m = _EMAIL_RE.search(html)
    return m.group(0) if m else None


def extract_link(block: object, base_url: str) -> str | None:
    """Return the absolute URL of the first ``<a href>`` inside ``block``."""
    find = getattr(block, "find", None)
    if find is None:
        return None
    link_el = find("a", href=True)
    if link_el is None:
        return None
    href = link_el.get("href")
    if not isinstance(href, str):
        return None
    return urljoin(base_url + "/", href)


def extract_external_website(soup: BeautifulSoup, exclude_host: str) -> str | None:
    """Return the first external http(s) link on the page, skipping ``exclude_host``."""
    for a in soup.select("a[href^='http']"):
        href = a.get("href")
        if not isinstance(href, str) or "mailto:" in href:
            continue
        if exclude_host in href:
            continue
        return href
    return None


def _host(url: str) -> str:
    return urlparse(url).netloc
