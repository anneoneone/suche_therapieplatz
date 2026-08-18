"""Therapist parsing endpoints."""

from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse

from fastapi import APIRouter, File, HTTPException, UploadFile
import httpx

from ...config import Settings
from ...models import TherapistData
from ...parsers.pdf_parser import PDFParser
from ...parsers.text_parser import TextParser
from ...sources import specialties
from ..schemas import (
    CrawlRequest,
    ParseResponse,
    ParseUrlRequest,
    SpecialtiesResponse,
    SpecialtyOption,
    TherapistResponse,
)

router = APIRouter(prefix="/therapists", tags=["therapists"])

_ALLOWED_PARSE_URL_HOSTS = {"psych-info.de", "www.psych-info.de"}


def _therapist_to_response(t: TherapistData) -> TherapistResponse:
    key = t.specialty or specialties.infer_key(t)
    label = (
        specialties.SPECIALTIES[key].label if key in specialties.SPECIALTIES else None
    )
    return TherapistResponse(
        name=t.name,
        address=t.address,
        phone=t.telefon,
        email=t.email,
        salutation=t.salutation,
        specialty=key,
        specialty_label=label,
        distance_km=t.distance_km,
        sources=list(t.sources),
    )


@router.get("/specialties", response_model=SpecialtiesResponse)
async def list_specialties() -> SpecialtiesResponse:
    """Return the specialties offered in the search dropdown."""
    return SpecialtiesResponse(
        specialties=[
            SpecialtyOption(key=s.key, label=s.label)
            for s in specialties.all_specialties()
        ],
        default=specialties.DEFAULT_KEY,
    )


@router.post("/crawl", response_model=ParseResponse)
def crawl_directory(request: CrawlRequest) -> ParseResponse:
    """Live-crawl a therapist directory around a geocoded address.

    Supports ``psychotherapeutensuche`` (nationwide, distance-sorted
    upstream, no emails) and ``ptk_bayern`` (Bavaria, single JSON request,
    ~30% with emails, coordinates included). Results are ranked by distance
    via :func:`merge_and_rank` where coordinates are available; without
    them the upstream order is preserved. Declared ``def`` (not ``async``)
    on purpose: the sources do blocking HTTP, so FastAPI runs the handler
    in the threadpool.
    """
    import re

    from ...sources.base import SearchParams
    from ...sources.geocode import Geocoder, GeocodingError
    from ...sources.merger import merge_and_rank
    from ...sources.psychotherapeutensuche import PsychotherapeutensucheSource
    from ...sources.ptk_bayern import PTKBayernSource

    if request.source not in ("psychotherapeutensuche", "ptk_bayern"):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown crawl source: {request.source!r}",
        )
    if request.require_email and request.source == "psychotherapeutensuche":
        raise HTTPException(
            status_code=400,
            detail=(
                "psychotherapeutensuche.de lists no email addresses; "
                "use source 'ptk_bayern' or drop require_email"
            ),
        )

    settings = Settings()
    geocoder = Geocoder(
        endpoint=settings.geocoder_endpoint,
        user_agent=settings.scraper_user_agent,
        cache_dir=settings.http_cache_dir,
    )
    try:
        origin = geocoder.geocode(request.address, require_berlin=False)
    except GeocodingError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Could not geocode address: {e}",
        ) from e
    finally:
        geocoder.close()

    plz_match = re.search(r"\b(\d{5})\b", f"{request.address} {origin.display_name}")
    # ptk_bayern returns the full result set in one request, so over-fetch
    # when filtering on email and cap after the filter.
    limit_per_source = 500 if request.require_email else request.max_results
    params = SearchParams(
        lat=origin.lat,
        lon=origin.lon,
        radius_km=request.radius_km,
        limit_per_source=limit_per_source,
        postal_code=plz_match.group(1) if plz_match else None,
        city=None if plz_match else request.address,
    )

    if request.source == "ptk_bayern":
        source: PTKBayernSource | PsychotherapeutensucheSource = PTKBayernSource(
            user_agent=settings.scraper_user_agent
        )
    else:
        # 0.6s pacing keeps a max_results=30 crawl (~33 requests) around
        # 20-25s — polite, yet fast enough for a synchronous request.
        source = PsychotherapeutensucheSource(
            user_agent=settings.scraper_user_agent,
            min_delay_seconds=0.6,
        )
    try:
        found = source.search(params)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"Crawl failed: {e}",
        ) from e
    finally:
        source.close()

    if request.require_email:
        found = [t for t in found if t.email]
    therapists = merge_and_rank(
        {source.name: found},
        origin.lat,
        origin.lon,
        request.max_results,
    )

    therapist_responses = [_therapist_to_response(t) for t in therapists]
    with_email = sum(1 for t in therapists if t.email)
    return ParseResponse(
        therapists=therapist_responses,
        total=len(therapists),
        with_email=with_email,
    )


@router.post("/parse", response_model=ParseResponse)
async def parse_file(
    file: UploadFile = File(..., description="PDF or text file to parse"),
) -> ParseResponse:
    """Parse therapist data from uploaded PDF or text file.

    Args:
        file: Uploaded file (PDF or plain text).

    Returns:
        Parsed therapist data with statistics.

    Raises:
        HTTPException: If file type is unsupported or parsing fails.
    """
    # Validate file type
    filename = file.filename or ""
    is_pdf = filename.lower().endswith(".pdf") or file.content_type == "application/pdf"
    is_text = (
        filename.lower().endswith(".txt")
        or file.content_type == "text/plain"
        or file.content_type == "application/octet-stream"
    )

    if not (is_pdf or is_text):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.content_type}. Use PDF or TXT files.",
        )

    # Save to temp file for processing
    suffix = ".pdf" if is_pdf else ".txt"
    try:
        with NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = Path(tmp.name)

        # Parse file
        settings = Settings()
        parser = PDFParser(settings) if is_pdf else TextParser(settings)
        therapists = parser.parse_file(tmp_path)

        # Convert to response format
        therapist_responses = [_therapist_to_response(t) for t in therapists]

        with_email = sum(1 for t in therapists if t.email)

        return ParseResponse(
            therapists=therapist_responses,
            total=len(therapists),
            with_email=with_email,
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to parse file: {str(e)}",
        ) from e

    finally:
        # Clean up temp file
        if tmp_path.exists():
            tmp_path.unlink()


@router.post("/parse-url", response_model=ParseResponse)
async def parse_url(request: ParseUrlRequest) -> ParseResponse:
    """Download a PDF from a psych-info.de URL and parse it.

    The host allowlist is narrow on purpose — the parser only understands
    Psych-Info Resultate PDFs for now. Loosen the allowlist once we support
    more remote layouts.
    """
    parsed = urlparse(str(request.url))
    if parsed.scheme != "https":
        raise HTTPException(
            status_code=400,
            detail="URL must use https://",
        )
    if (parsed.hostname or "").lower() not in _ALLOWED_PARSE_URL_HOSTS:
        raise HTTPException(
            status_code=400,
            detail="Only psych-info.de URLs are accepted",
        )

    tmp_path: Path | None = None
    try:
        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                response = client.get(str(request.url))
                response.raise_for_status()
        except httpx.HTTPError as e:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to download PDF: {e}",
            ) from e

        content_type = response.headers.get("content-type", "").lower()
        url_path = parsed.path.lower()
        looks_like_pdf = content_type.startswith(
            "application/pdf"
        ) or url_path.endswith(".pdf")
        if not looks_like_pdf:
            raise HTTPException(
                status_code=502,
                detail=f"Upstream did not return a PDF (content-type={content_type!r})",
            )

        with NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(response.content)
            tmp_path = Path(tmp.name)

        try:
            therapists = PDFParser(Settings()).parse_file(tmp_path)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(
                status_code=502,
                detail=f"Failed to parse PDF: {e}",
            ) from e

        therapist_responses = [_therapist_to_response(t) for t in therapists]
        with_email = sum(1 for t in therapists if t.email)
        return ParseResponse(
            therapists=therapist_responses,
            total=len(therapists),
            with_email=with_email,
        )
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()
