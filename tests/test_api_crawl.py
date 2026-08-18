"""Tests for the /api/therapists/crawl endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from therapist_finder.api.main import app
from therapist_finder.models import TherapistData
from therapist_finder.sources.geocode import GeocodingError, Location


def _fake_geocoder(location: Location | None = None) -> MagicMock:
    geocoder = MagicMock()
    if location is None:
        geocoder.geocode.side_effect = GeocodingError("no match")
    else:
        geocoder.geocode.return_value = location
    return geocoder


def _fake_source(results: list[TherapistData], name: str) -> MagicMock:
    source = MagicMock()
    source.name = name  # assign post-construction: MagicMock(name=...) is special
    source.search.return_value = results
    return source


def test_crawl_happy_path() -> None:
    """Geocodes the address, runs the source, and maps to the response schema."""
    therapists = [
        TherapistData(
            name="Dipl.-Psych. Anja Zilker",
            address="Knaackstr. 78, 10435 Berlin",
            telefon="030 1234567",
            salutation="Sehr geehrte Frau Dipl.-Psych. Zilker",
            insurance_type="both",
            sources=["psychotherapeutensuche"],
        )
    ]
    geocoder = _fake_geocoder(
        Location(lat=52.5396, lon=13.4103, display_name="10435 Berlin")
    )
    source = _fake_source(therapists, "psychotherapeutensuche")

    client = TestClient(app)
    with (
        patch("therapist_finder.sources.geocode.Geocoder", return_value=geocoder),
        patch(
            "therapist_finder.sources.psychotherapeutensuche."
            "PsychotherapeutensucheSource",
            return_value=source,
        ),
    ):
        resp = client.post(
            "/api/therapists/crawl",
            json={"address": "Kastanienallee 12, 10435 Berlin", "radius_km": 10},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["with_email"] == 0
    first = body["therapists"][0]
    assert first["name"] == "Dipl.-Psych. Anja Zilker"
    assert first["phone"] == "030 1234567"
    assert first["sources"] == ["psychotherapeutensuche"]

    geocoder.geocode.assert_called_once_with(
        "Kastanienallee 12, 10435 Berlin", require_berlin=False
    )
    params = source.search.call_args.args[0]
    assert params.lat == 52.5396
    assert params.radius_km == 10
    source.close.assert_called_once()


def test_crawl_ptk_bayern_ranks_and_filters_email() -> None:
    """ptk_bayern crawl ranks by distance and honours require_email."""
    therapists = [
        TherapistData(
            name="Dr. phil. Anna Beispiel",
            address="Sendlingerstr. 25, 80331 München",
            email="praxis@beispiel-muenchen.de",
            lat=48.1351,
            lon=11.5704,
            sources=["ptk_bayern"],
        ),
        TherapistData(
            name="Klara Kinderlieb",
            address="Marienplatz 1, 80331 München",
            lat=48.1373,
            lon=11.5755,
            sources=["ptk_bayern"],
        ),
    ]
    geocoder = _fake_geocoder(
        Location(lat=48.1374, lon=11.5755, display_name="München")
    )
    source = _fake_source(therapists, "ptk_bayern")

    client = TestClient(app)
    with (
        patch("therapist_finder.sources.geocode.Geocoder", return_value=geocoder),
        patch(
            "therapist_finder.sources.ptk_bayern.PTKBayernSource",
            return_value=source,
        ),
    ):
        resp = client.post(
            "/api/therapists/crawl",
            json={
                "source": "ptk_bayern",
                "address": "Marienplatz 1, 80331 München",
                "require_email": True,
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Klara has no email → filtered out despite being closer.
    assert body["total"] == 1
    assert body["with_email"] == 1
    assert body["therapists"][0]["name"] == "Dr. phil. Anna Beispiel"
    assert body["therapists"][0]["distance_km"] is not None

    params = source.search.call_args.args[0]
    assert params.postal_code == "80331"
    # Over-fetched so the email filter doesn't eat into max_results.
    assert params.limit_per_source == 500


def test_crawl_require_email_rejected_for_psychotherapeutensuche() -> None:
    client = TestClient(app)
    resp = client.post(
        "/api/therapists/crawl",
        json={
            "source": "psychotherapeutensuche",
            "address": "10435 Berlin",
            "require_email": True,
        },
    )
    assert resp.status_code == 400
    assert "no email addresses" in resp.json()["detail"]


def test_crawl_rejects_unknown_source() -> None:
    client = TestClient(app)
    resp = client.post(
        "/api/therapists/crawl",
        json={"source": "doctolib", "address": "10435 Berlin"},
    )
    assert resp.status_code == 400
    assert "Unknown crawl source" in resp.json()["detail"]


def test_crawl_geocoding_failure_is_400() -> None:
    geocoder = _fake_geocoder(location=None)
    client = TestClient(app)
    with patch("therapist_finder.sources.geocode.Geocoder", return_value=geocoder):
        resp = client.post(
            "/api/therapists/crawl",
            json={"address": "Nowhereville 999"},
        )
    assert resp.status_code == 400
    assert "geocode" in resp.json()["detail"].lower()
