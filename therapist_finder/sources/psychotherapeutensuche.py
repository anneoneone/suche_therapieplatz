"""Scraper for psychotherapeutensuche.de (PsyOS GmbH).

A nationwide voluntary therapist directory (~10.6k profiles, TYPO3 +
Codappix SearchCore / Elasticsearch). Unlike psych-info / therapie.de it
serves plain server-rendered HTML with schema.org ``Person`` microdata on
profile pages, and its radius search works as a simple GET — no JS needed.

Endpoint shape (verified 2026-08):

* list:   ``/therapeuten/?search[searchRequest][filter][distance][...]``
  (page 2+: ``/therapeuten/seite/{n}/?...``), 10 results per page,
  sorted by distance.
* detail: ``/{vorname-nachname-NNNNNN}/`` with itemprop markup for name,
  jobTitle, gender, PostalAddress, telephone, plus a "Kostenträger" card.

robots.txt only disallows ``/suche/?*`` and ``/typo3/*`` and publishes a
``therapists.xml`` sitemap; the URLs used here are allowed. Profiles carry
no email addresses (contact is via an on-site form).

Legal hygiene (§87b UrhG): fetch only the radius-relevant subset, keep
only displayed fields, pace requests, never republish the corpus.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from bs4.element import Tag

from therapist_finder.models import InsuranceType, TherapistData
from therapist_finder.sources._html_scraper import (
    HTMLScraper,
    ListEntry,
    clean,
    text_of,
)
from therapist_finder.sources.base import SearchParams
from therapist_finder.utils.salutation import make_salutation

logger = logging.getLogger(__name__)

_DEFAULT_BASE = "https://www.psychotherapeutensuche.de"

#: Radius options the search UI offers; the backend snaps up to the next one.
_ALLOWED_RADII_KM = (1, 2, 5, 10, 25, 50)

_RESULTS_PER_PAGE = 10

#: Kostenträger labels → InsuranceType buckets. "Kostenerstattung" is a
#: private practice billing GKV patients via reimbursement → counts private.
_KASSEN_LABELS = ("gesetzliche krankenversicherung",)
_PRIVAT_LABELS = ("privatkassen und selbstzahler", "kostenerstattung")


class PsychotherapeutensucheSource(HTMLScraper):
    """HTML scraper for www.psychotherapeutensuche.de."""

    name = "psychotherapeutensuche"

    def __init__(
        self,
        user_agent: str,
        base_url: str = _DEFAULT_BASE,
        min_delay_seconds: float = 1.0,
        client: object | None = None,
        respect_robots_txt: bool = True,
    ) -> None:
        """Initialise. ``client`` is an ``httpx.Client`` (typed loose for mocks)."""
        super().__init__(
            user_agent=user_agent,
            base_url=base_url,
            min_delay_seconds=min_delay_seconds,
            client=client,  # type: ignore[arg-type]
            respect_robots_txt=respect_robots_txt,
        )

    def _iter_list_urls(self, params: SearchParams) -> list[str]:
        distance = next(
            (d for d in _ALLOWED_RADII_KM if d >= params.radius_km),
            _ALLOWED_RADII_KM[-1],
        )
        qs = urlencode(
            {
                "search[searchRequest][filter][distance][location][lat]": (
                    f"{params.lat:.5f}"
                ),
                "search[searchRequest][filter][distance][location][lon]": (
                    f"{params.lon:.5f}"
                ),
                "search[searchRequest][filter][distance][distance]": f"{distance}km",
            }
        )
        pages = max(
            1,
            -(-params.limit_per_source // _RESULTS_PER_PAGE),  # ceil division
        )
        urls = [f"{self.base_url}/therapeuten/?{qs}"]
        urls += [
            f"{self.base_url}/therapeuten/seite/{n}/?{qs}" for n in range(2, pages + 1)
        ]
        return urls

    def _parse_list_page(self, html: str) -> list[ListEntry]:
        soup = BeautifulSoup(html, "lxml")
        entries: list[ListEntry] = []
        for block in soup.select(".search-result--item"):
            title_el = block.select_one("h4.card-title")
            if title_el is None:
                continue
            name = clean(title_el.get_text())
            if not name:
                continue
            link_el = block.select_one(".search-result--content a[href]")
            href = link_el.get("href") if link_el else None
            entries.append(
                ListEntry(
                    name=_strip_gender_prefix(name),
                    detail_url=(
                        f"{self.base_url}{href}" if isinstance(href, str) else None
                    ),
                    address=_list_card_address(block),
                )
            )
        return entries

    def _parse_detail_page(self, html: str, *, fallback: ListEntry) -> TherapistData:
        soup = BeautifulSoup(html, "lxml")
        person = soup.select_one('[itemtype="http://schema.org/Person"]') or soup

        gender = clean(text_of(person, '[itemprop="gender"]'))
        title = clean(text_of(person, '[itemprop="jobTitle"]'))
        plain_name = clean(text_of(person, '[itemprop="name"]'))
        name = clean(f"{title} {plain_name}") or fallback.name

        street = clean(text_of(person, '[itemprop="streetAddress"]'))
        postcode = clean(text_of(person, '[itemprop="postalCode"]'))
        locality = clean(text_of(person, '[itemprop="addressLocality"]'))
        address = (
            f"{street}, {postcode} {locality}".strip(", ").strip()
            if street or locality
            else fallback.address
        )

        profession = clean(text_of(person, '[itemprop="description"]'))
        return TherapistData(
            name=name,
            address=address or None,
            telefon=clean(text_of(person, '[itemprop="telephone"]')) or None,
            website=_extract_profile_website(person),
            therapieform=[profession] if profession else [],
            salutation=make_salutation(clean(f"{gender} {title} {plain_name}")),
            insurance_type=_extract_insurance(soup),
            sources=[self.name],
        )


def _strip_gender_prefix(name: str) -> str:
    for prefix in ("Herr ", "Frau "):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def _list_card_address(block: Tag) -> str | None:
    """Pull '<street> · <plz> <city>' out of a result card's text lines."""
    card_text = block.select_one("p.card-text")
    if card_text is None:
        return None
    lines = [clean(s) for s in card_text.get_text("\n").split("\n")]
    lines = [ln for ln in lines if ln and ln != "·"]
    # Lines are: profession, street, "PLZ city [district]", badge codes.
    for i, line in enumerate(lines[:-1]):
        if line[:1].isdigit() is False and lines[i + 1][:5].isdigit():
            return f"{line}, {lines[i + 1]}"
    return None


def _extract_profile_website(person: Tag) -> str | None:
    for a in person.select("a[href^='http']"):
        href = a.get("href")
        if isinstance(href, str) and "Webseite" in a.get_text():
            return href
    return None


def _extract_insurance(soup: BeautifulSoup) -> InsuranceType | None:
    labels = [clean(li.get_text()).lower() for li in soup.select(".card-body li")]
    has_kassen = any(lbl in _KASSEN_LABELS for lbl in labels)
    has_privat = any(lbl in _PRIVAT_LABELS for lbl in labels)
    if has_kassen and has_privat:
        return "both"
    if has_kassen:
        return "kassen"
    if has_privat:
        return "privat"
    return None
