"""Tests for healthcare-provider data sources and merger."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from therapist_finder.models import TherapistData
from therapist_finder.sources.base import SearchParams
from therapist_finder.sources.geocode import (
    Geocoder,
    GeocodingError,
    haversine_km,
)
from therapist_finder.sources.merger import merge_and_rank
from therapist_finder.sources.overpass import OverpassSource
from therapist_finder.sources.psych_info import PsychInfoSource
from therapist_finder.sources.psychotherapeutensuche import (
    PsychotherapeutensucheSource,
)
from therapist_finder.sources.ptk_bayern import PTKBayernSource
from therapist_finder.sources.therapie_de import TherapieDeSource

FIXTURES = Path(__file__).parent / "fixtures" / "sources"
UA = "therapist-finder-tests/0.0"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture()
def search_params() -> SearchParams:
    """Search params anchored at Kastanienallee 12, 10435 Berlin."""
    return SearchParams(
        specialty="Psychotherapeut",
        lat=52.5396,
        lon=13.4127,
        radius_km=5.0,
        limit_per_source=20,
    )


class TestOverpassSource:
    """Tests for the OSM Overpass source."""

    def test_parses_elements(
        self, httpx_mock: HTTPXMock, search_params: SearchParams
    ) -> None:
        """It maps OSM tags into ``TherapistData`` and drops nameless elements."""
        payload = json.loads(_read("overpass.json"))
        httpx_mock.add_response(
            url="https://overpass.example/api/interpreter",
            method="POST",
            json=payload,
        )
        src = OverpassSource(
            endpoint="https://overpass.example/api/interpreter", user_agent=UA
        )
        try:
            results = src.search(search_params)
        finally:
            src.close()

        # Two valid entries (third element has empty name)
        assert len(results) == 2
        first = results[0]
        assert first.name == "Praxis Dr. Anna Beispiel"
        assert first.address == "Kastanienallee 12, 10435 Berlin"
        assert first.telefon == "+49 30 1234567"
        assert first.email == "praxis@beispiel.de"
        assert first.website == "https://praxis-beispiel.de"
        assert first.languages == ["de", "en"]
        assert first.lat == pytest.approx(52.5396)
        assert first.lon == pytest.approx(13.4127)
        assert first.sources == ["osm"]


class TestPsychInfoSource:
    """Tests for the psych-info.de HTML scraper (residential only)."""

    def test_parses_list_and_detail(
        self, httpx_mock: HTTPXMock, search_params: SearchParams
    ) -> None:
        """List + detail pages combine into a populated ``TherapistData``."""
        httpx_mock.add_response(
            url="https://psych.example/robots.txt", method="GET", text=""
        )
        httpx_mock.add_response(
            url="https://psych.example/suche?ort=Berlin&verfahren=PP&seite=1",
            method="GET",
            text=_read("psych_info_list.html"),
        )
        httpx_mock.add_response(
            url="https://psych.example/therapeut/anna-beispiel",
            method="GET",
            text=_read("psych_info_detail.html"),
        )
        httpx_mock.add_response(
            url="https://psych.example/therapeut/clara-kollege",
            method="GET",
            text=_read("psych_info_detail.html"),
        )
        search_params.limit_per_source = 2

        src = PsychInfoSource(
            user_agent=UA,
            base_url="https://psych.example",
            min_delay_seconds=0.0,
        )
        try:
            results = src.search(search_params)
        finally:
            src.close()

        assert len(results) == 2
        first = results[0]
        assert first.name == "Dr. Anna Beispiel"
        assert first.email == "praxis@beispiel.de"
        assert first.website == "https://praxis-beispiel.de"
        assert "Verhaltenstherapie" in first.therapieform
        assert first.languages == ["Deutsch", "Englisch"]
        assert first.insurance_type == "privat"
        assert first.sources == ["psych_info"]

    def test_respects_robots_txt(
        self, httpx_mock: HTTPXMock, search_params: SearchParams
    ) -> None:
        """A Disallow: / robots.txt blocks all fetching."""
        httpx_mock.add_response(
            url="https://psych.example/robots.txt",
            method="GET",
            text="User-agent: *\nDisallow: /\n",
        )
        src = PsychInfoSource(
            user_agent=UA,
            base_url="https://psych.example",
            min_delay_seconds=0.0,
        )
        try:
            assert src.search(search_params) == []
        finally:
            src.close()


class TestTherapieDeSource:
    """Tests for the therapie.de HTML scraper (residential only)."""

    def test_parses_and_flags_heilpraktiker(
        self, httpx_mock: HTTPXMock, search_params: SearchParams
    ) -> None:
        """Detail text with 'Heilpraktikerin' → insurance_type 'heilpraktiker'."""
        httpx_mock.add_response(
            url="https://therapiede.example/robots.txt", method="GET", text=""
        )
        httpx_mock.add_response(
            url="https://therapiede.example/psychotherapie/-ort-/berlin/",
            method="GET",
            text=_read("therapie_de_list.html"),
        )
        httpx_mock.add_response(
            url="https://therapiede.example/therapeut/mia-mueller",
            method="GET",
            text=_read("therapie_de_detail.html"),
        )
        httpx_mock.add_response(
            url="https://therapiede.example/therapeut/lea-lotus",
            method="GET",
            text=_read("therapie_de_detail.html"),
        )
        search_params.limit_per_source = 2

        src = TherapieDeSource(
            user_agent=UA,
            base_url="https://therapiede.example",
            listing_path="/psychotherapie/-ort-/berlin",
            min_delay_seconds=0.0,
        )
        try:
            results = src.search(search_params)
        finally:
            src.close()

        assert len(results) == 2
        heilpraktikerin = next(r for r in results if "Lotus" in r.name)
        assert heilpraktikerin.insurance_type == "heilpraktiker"
        assert heilpraktikerin.email == "kontakt@lotus-praxis.de"
        assert heilpraktikerin.website == "https://lotus-praxis.de"
        assert heilpraktikerin.sources == ["therapie_de"]


class TestPsychotherapeutensucheSource:
    """Tests for the psychotherapeutensuche.de HTML scraper."""

    _QS = (
        "search%5BsearchRequest%5D%5Bfilter%5D%5Bdistance%5D%5Blocation%5D"
        "%5Blat%5D=52.53960"
        "&search%5BsearchRequest%5D%5Bfilter%5D%5Bdistance%5D%5Blocation%5D"
        "%5Blon%5D=13.41270"
        "&search%5BsearchRequest%5D%5Bfilter%5D%5Bdistance%5D%5Bdistance%5D=5km"
    )

    def test_parses_list_and_detail(
        self, httpx_mock: HTTPXMock, search_params: SearchParams
    ) -> None:
        """Microdata detail pages become fully populated ``TherapistData``."""
        httpx_mock.add_response(
            url="https://pts.example/robots.txt", method="GET", text=""
        )
        httpx_mock.add_response(
            url=f"https://pts.example/therapeuten/?{self._QS}",
            method="GET",
            text=_read("psychotherapeutensuche_list.html"),
        )
        for slug in ("anja-zilker-996845", "harvey-becker-204187"):
            httpx_mock.add_response(
                url=f"https://pts.example/{slug}/",
                method="GET",
                text=_read("psychotherapeutensuche_detail.html"),
            )
        search_params.limit_per_source = 2

        src = PsychotherapeutensucheSource(
            user_agent=UA,
            base_url="https://pts.example",
            min_delay_seconds=0.0,
        )
        try:
            results = src.search(search_params)
        finally:
            src.close()

        assert len(results) == 2
        first = results[0]
        assert first.name == "Dipl.-Psych. Anja Zilker"
        assert first.salutation == "Sehr geehrte Frau Dipl.-Psych. Zilker"
        assert first.address == "Knaackstr. 78, 10435 Berlin"
        assert first.telefon == "030 1234567"
        assert first.website == "https://praxis-zilker.example"
        assert first.therapieform == ["Psychologische Psychotherapeutin"]
        assert first.insurance_type == "both"
        assert first.email is None
        assert first.sources == ["psychotherapeutensuche"]

    def test_list_fallback_when_detail_fails(
        self, httpx_mock: HTTPXMock, search_params: SearchParams
    ) -> None:
        """Detail-page failures degrade to list-card data (name + address)."""
        httpx_mock.add_response(
            url="https://pts.example/robots.txt", method="GET", text=""
        )
        httpx_mock.add_response(
            url=f"https://pts.example/therapeuten/?{self._QS}",
            method="GET",
            text=_read("psychotherapeutensuche_list.html"),
        )
        for slug in ("anja-zilker-996845", "harvey-becker-204187"):
            httpx_mock.add_response(
                url=f"https://pts.example/{slug}/", method="GET", status_code=500
            )
        search_params.limit_per_source = 2

        src = PsychotherapeutensucheSource(
            user_agent=UA,
            base_url="https://pts.example",
            min_delay_seconds=0.0,
        )
        try:
            results = src.search(search_params)
        finally:
            src.close()

        assert len(results) == 2
        assert results[0].name == "Dipl.-Psych. Anja Zilker"
        assert results[0].address == "Knaackstr. 78, 10435 Berlin Prenzlauer Berg"
        assert results[1].name == "Dipl.-Psych./Dipl.-Soz.päd. Harvey Becker"

    def test_url_building_snaps_radius_and_paginates(self) -> None:
        """Radius snaps up to the site's fixed options; page 2+ uses /seite/n/."""
        src = PsychotherapeutensucheSource(
            user_agent=UA, base_url="https://pts.example", min_delay_seconds=0.0
        )
        try:
            params = SearchParams(
                lat=52.5, lon=13.4, radius_km=7.0, limit_per_source=25
            )
            urls = src._iter_list_urls(params)
        finally:
            src.close()

        assert len(urls) == 3
        assert urls[0].startswith("https://pts.example/therapeuten/?")
        assert "distance%5D%5Bdistance%5D=10km" in urls[0]
        assert urls[1].startswith("https://pts.example/therapeuten/seite/2/?")
        assert urls[2].startswith("https://pts.example/therapeuten/seite/3/?")


class TestPTKBayernSource:
    """Tests for the PTK Bayern JSON search agent (Bavaria only)."""

    def _params(self) -> SearchParams:
        """Params anchored near Marienplatz, München."""
        return SearchParams(
            lat=48.1374,
            lon=11.5755,
            radius_km=5.0,
            limit_per_source=20,
            postal_code="80331",
        )

    def test_parses_items_and_ranks_by_distance(self, httpx_mock: HTTPXMock) -> None:
        """JSON items map to ``TherapistData``, sorted by haversine distance."""
        httpx_mock.add_response(
            method="GET",
            json=json.loads(_read("ptk_bayern.json")),
        )
        src = PTKBayernSource(user_agent=UA, base_url="https://ptk.example")
        try:
            results = src.search(self._params())
        finally:
            src.close()

        request = httpx_mock.get_requests()[0]
        assert request.url.path == "/ptk/adressen.nsf/ptk_search_psychotherapeuten"
        assert request.url.params["plz"] == "80331"
        assert request.url.params["umkreis"] == "5"
        assert request.url.params["versicherung"] == "pkvgkv"
        assert request.url.params["abrechnung"] == "pkvgkv"

        # Nameless third item is dropped; nearest (Sendlingerstr.) first.
        assert len(results) == 2
        first = results[0]
        assert first.name == "Dr. phil. Anna Beispiel"
        assert first.salutation == "Sehr geehrte Frau Beispiel"
        assert first.address == "Sendlingerstr. 25, 80331 München"
        assert first.telefon == "089 1234567"
        assert first.email == "praxis@beispiel-muenchen.de"
        assert first.website == "https://www.praxis-beispiel.de"
        assert first.insurance_type == "both"
        assert first.specialty == "psychotherapie"
        assert first.lat == pytest.approx(48.1351218)
        assert first.sources == ["ptk_bayern"]

        second = results[1]
        assert second.name == "Klara Kinderlieb"
        # HTML entities in the agent JSON ("M&#252;nchen") are decoded.
        assert second.address == "Marienplatz 1, 80331 München"
        assert second.email is None
        assert second.insurance_type == "privat"
        assert second.specialty == "kinder_jugend_psychotherapie"

    def test_requires_postal_code_or_city(self) -> None:
        """Without PLZ/Ort the upstream search is meaningless — skip it."""
        src = PTKBayernSource(user_agent=UA, base_url="https://ptk.example")
        try:
            results = src.search(SearchParams(lat=48.1, lon=11.6))
        finally:
            src.close()
        assert results == []

    def test_radius_snaps_to_allowed_values(self, httpx_mock: HTTPXMock) -> None:
        """radius_km 12 → next allowed umkreis 15."""
        httpx_mock.add_response(method="GET", json={"key": "", "data": []})
        src = PTKBayernSource(user_agent=UA, base_url="https://ptk.example")
        try:
            params = self._params()
            params.radius_km = 12.0
            src.search(params)
        finally:
            src.close()
        assert httpx_mock.get_requests()[0].url.params["umkreis"] == "15"


class TestGeocoder:
    """Tests for the Photon + Nominatim geocoder wrapper."""

    def test_nominatim_success(self, httpx_mock: HTTPXMock, tmp_path: Path) -> None:
        """Nominatim endpoint parses the flat-list response shape."""
        httpx_mock.add_response(
            url=httpx.URL(
                "https://nominatim.example/search",
                params={
                    "q": "Kastanienallee 12, 10435 Berlin",
                    "format": "jsonv2",
                    "limit": "1",
                    "addressdetails": "1",
                    "countrycodes": "de",
                },
            ),
            method="GET",
            json=[
                {
                    "lat": "52.5396",
                    "lon": "13.4127",
                    "display_name": "Kastanienallee 12, 10435 Berlin, Germany",
                }
            ],
        )
        geocoder = Geocoder(
            endpoint="https://nominatim.example/search",
            user_agent=UA,
            cache_dir=tmp_path,
        )
        try:
            loc = geocoder.geocode("Kastanienallee 12, 10435 Berlin")
        finally:
            geocoder.close()
        assert loc.lat == pytest.approx(52.5396)
        assert loc.lon == pytest.approx(13.4127)

    def test_photon_success(self, httpx_mock: HTTPXMock, tmp_path: Path) -> None:
        """Photon endpoint parses the GeoJSON FeatureCollection response."""
        httpx_mock.add_response(
            url=httpx.URL(
                "https://photon.example/api/",
                params={
                    "q": "Kastanienallee 12, 10435 Berlin",
                    "limit": "1",
                    "lang": "de",
                },
            ),
            method="GET",
            json={
                "type": "FeatureCollection",
                "features": [
                    {
                        "geometry": {
                            "type": "Point",
                            "coordinates": [13.4127, 52.5396],
                        },
                        "properties": {
                            "street": "Kastanienallee",
                            "housenumber": "12",
                            "postcode": "10435",
                            "city": "Berlin",
                        },
                    }
                ],
            },
        )
        geocoder = Geocoder(
            endpoint="https://photon.example/api/",
            user_agent=UA,
            cache_dir=tmp_path,
        )
        try:
            loc = geocoder.geocode("Kastanienallee 12, 10435 Berlin")
        finally:
            geocoder.close()
        assert loc.lat == pytest.approx(52.5396)
        assert loc.lon == pytest.approx(13.4127)
        assert "Kastanienallee" in loc.display_name

    def test_geocode_outside_berlin_raises(
        self, httpx_mock: HTTPXMock, tmp_path: Path
    ) -> None:
        """Resolved coords outside Berlin → ``GeocodingError``."""
        httpx_mock.add_response(
            method="GET",
            json=[{"lat": "48.1374", "lon": "11.5755", "display_name": "Munich"}],
        )
        geocoder = Geocoder(
            endpoint="https://nominatim.example/search",
            user_agent=UA,
            cache_dir=tmp_path,
        )
        try:
            with pytest.raises(GeocodingError):
                geocoder.geocode("Marienplatz, München")
        finally:
            geocoder.close()

    def test_haversine_symmetry(self) -> None:
        """Haversine is symmetric and zero for identical points."""
        assert haversine_km(52.52, 13.40, 52.52, 13.40) == pytest.approx(0.0)
        d1 = haversine_km(52.52, 13.40, 52.54, 13.42)
        d2 = haversine_km(52.54, 13.42, 52.52, 13.40)
        assert d1 == pytest.approx(d2)


class TestMerger:
    """Tests for the cross-source merger."""

    def test_merges_and_sorts_by_distance(self) -> None:
        """Duplicate across sources collapses, results sort by distance asc."""
        origin_lat, origin_lon = 52.5396, 13.4127
        near = TherapistData(
            name="Dr. Anna Beispiel",
            address="Kastanienallee 12, 10435 Berlin",
            email="praxis@beispiel.de",
            lat=52.5396,
            lon=13.4127,
            sources=["osm"],
        )
        near_dup = TherapistData(
            name="Anna Beispiel",
            address="Kastanienallee 12, 10435 Berlin",
            telefon="030 1234567",
            sources=["ptk"],
        )
        far = TherapistData(
            name="Dr. Weit Entfernt",
            address="Wilhelmstraße 5, 12247 Berlin",
            lat=52.45,
            lon=13.36,
            sources=["116117"],
        )

        merged = merge_and_rank(
            {"osm": [near], "ptk": [near_dup], "116117": [far]},
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            limit=10,
        )

        assert len(merged) == 2
        first = merged[0]
        assert first.name == "Dr. Anna Beispiel"
        assert first.telefon == "030 1234567"
        assert first.email == "praxis@beispiel.de"
        assert sorted(first.sources) == ["osm", "ptk"]
        assert first.distance_km == pytest.approx(0.0, abs=0.05)
        assert merged[1].name == "Dr. Weit Entfernt"
        assert merged[1].distance_km is not None
        assert merged[1].distance_km > first.distance_km  # type: ignore[operator]

    def test_limit_truncates_sorted_results(self) -> None:
        """``limit`` applies after sorting, not per-source."""
        providers = [
            TherapistData(
                name=f"Doc {i}",
                address=f"Test {i}, 10{i:03d} Berlin",
                lat=52.52 + i * 0.01,
                lon=13.40,
                sources=["osm"],
            )
            for i in range(5)
        ]
        merged = merge_and_rank(
            {"osm": providers},
            origin_lat=52.52,
            origin_lon=13.40,
            limit=3,
        )
        assert [p.name for p in merged] == ["Doc 0", "Doc 1", "Doc 2"]
