"""Source for the PTK Bayern Psychotherapeut*innen-Suchdienst.

The Bavarian chamber of psychotherapists (Psychotherapeutenkammer Bayern, a
Körperschaft des öffentlichen Rechts) runs its therapist search on Lotus
Domino. Behind the form at ``/ptk/web.nsf/formular?openForm&formular=
depsychotherapeutensuche`` sits a JSON agent that returns the complete
result set in ONE request — no pagination, no detail pages:

    GET /ptk/adressen.nsf/ptk_search_psychotherapeuten?OpenAgent
        &plz=<PLZ>&ort=<Ort>&umkreis=<km>&versicherung=pkvgkv&...

Each item carries name, academic title (``anrede``), street/PLZ/Ort, phone,
fax, **email** (~30% of entries, verified 2026-08 for Munich), homepage,
``geoCoordinates`` (lat,lon), ``patientengruppe`` (kin/erw) and
``verrechnungsbasis`` (GKV/PKV). This makes it the strongest email source
so far — but coverage is **Bavaria only**.

The upstream search is PLZ/Ort-based, so ``SearchParams.postal_code`` (or
``city``) must be set; lat/lon are only used to rank results locally.
robots.txt (verified 2026-08) contains no Disallow rules.
"""

from __future__ import annotations

import html
import logging
from urllib.parse import urlencode

import httpx

from therapist_finder.models import InsuranceType, TherapistData
from therapist_finder.sources.base import SearchParams, TherapistSource
from therapist_finder.sources.geocode import haversine_km
from therapist_finder.utils.salutation import make_salutation

logger = logging.getLogger(__name__)

_DEFAULT_BASE = "https://www.ptk-bayern.de"
_AGENT_PATH = "/ptk/adressen.nsf/ptk_search_psychotherapeuten"

#: Umkreis (radius) values the upstream select offers; snapped up to.
_ALLOWED_RADII_KM = (0, 1, 3, 5, 8, 10, 15, 20, 25, 30, 35, 40, 50)


class PTKBayernSource(TherapistSource):
    """JSON-agent client for the PTK Bayern therapist search (Bavaria only)."""

    name = "ptk_bayern"

    def __init__(
        self,
        user_agent: str,
        base_url: str = _DEFAULT_BASE,
        client: httpx.Client | None = None,
    ) -> None:
        """Initialise with HTTP defaults; one search = one GET request."""
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=60.0,
            follow_redirects=True,
        )
        self._owns_client = client is None

    def search(self, params: SearchParams) -> list[TherapistData]:
        """Query the agent once and map the JSON items to ``TherapistData``.

        Results are ranked by haversine distance to ``params.lat/lon`` (the
        agent returns them unsorted) and truncated to
        ``params.limit_per_source``.
        """
        if not params.postal_code and not params.city:
            logger.warning(
                "%s: needs SearchParams.postal_code or city — skipping", self.name
            )
            return []

        url = f"{self.base_url}{_AGENT_PATH}?OpenAgent&{self._query(params)}"
        try:
            resp = self._client.get(url)
            resp.raise_for_status()
            items = resp.json().get("data", [])
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("%s: search failed: %s", self.name, e)
            return []

        results = [t for t in (_to_therapist(item) for item in items) if t]
        results.sort(key=lambda t: _distance_or_inf(t, params.lat, params.lon))
        logger.info("%s: returning %d providers", self.name, len(results))
        return results[: params.limit_per_source]

    def close(self) -> None:
        """Close the underlying HTTP client if we own it."""
        if self._owns_client:
            self._client.close()

    @staticmethod
    def _query(params: SearchParams) -> str:
        umkreis = next(
            (r for r in _ALLOWED_RADII_KM if r >= params.radius_km),
            _ALLOWED_RADII_KM[-1],
        )
        # Empty values and the pkvgkv defaults mirror the upstream form —
        # the agent returns an empty result set if they are omitted.
        return urlencode(
            {
                "plz": params.postal_code or "",
                "ort": params.city or "",
                "umkreis": str(umkreis),
                "name": "",
                "pgruppe": "",
                "versicherung": "pkvgkv",
                "wleistungen": "",
                "abrechnung": "pkvgkv",
                "verfahren": "",
                "weitereverfahren": "",
                "weitereverfahren_wissen": "",
                "geschlecht": "",
                "oeffentlich": "",
                "barrierefrei": "",
                "corona": "",
                "sprache": "",
                "weiteresprachen": "",
                "beszg": "",
            }
        )


def _to_therapist(item: dict[str, str]) -> TherapistData | None:
    def field(key: str) -> str:
        # The agent HTML-escapes its JSON strings (e.g. "G&#246;tz").
        return html.unescape(item.get(key) or "").strip()

    first = field("svorname")
    last = field("sname")
    title = field("anrede")  # academic title, e.g. Dr. phil.
    plain_name = " ".join(p for p in (first, last) if p)
    if not plain_name:
        return None

    street = field("strasse")
    plz = field("plz")
    ort = field("ort")
    address = ", ".join(p for p in (street, f"{plz} {ort}".strip()) if p)

    lat, lon = _parse_coordinates(item.get("geoCoordinates") or "")
    homepage = field("homepage")
    return TherapistData(
        name=" ".join(p for p in (title, plain_name) if p),
        address=address or None,
        telefon=field("telefon") or None,
        email=field("email") or None,
        website=f"https://{homepage}" if homepage else None,
        salutation=make_salutation(plain_name),
        insurance_type=_insurance(item.get("verrechnungsbasis") or ""),
        specialty=_specialty(item.get("patientengruppe") or ""),
        lat=lat,
        lon=lon,
        sources=[PTKBayernSource.name],
    )


def _parse_coordinates(raw: str) -> tuple[float | None, float | None]:
    parts = raw.split(",")
    if len(parts) != 2:
        return None, None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None, None


def _insurance(verrechnungsbasis: str) -> InsuranceType | None:
    kinds = {p.strip().upper() for p in verrechnungsbasis.split(";") if p.strip()}
    has_gkv = "GKV" in kinds
    has_pkv = "PKV" in kinds
    if has_gkv and has_pkv:
        return "both"
    if has_gkv:
        return "kassen"
    if has_pkv:
        return "privat"
    return None


def _specialty(patientengruppe: str) -> str | None:
    groups = {p.strip() for p in patientengruppe.split(";") if p.strip()}
    if groups == {"kin"}:
        return "kinder_jugend_psychotherapie"
    if groups:
        return "psychotherapie"
    return None


def _distance_or_inf(t: TherapistData, lat: float, lon: float) -> float:
    if t.lat is None or t.lon is None:
        return float("inf")
    return haversine_km(lat, lon, t.lat, t.lon)
