"""API request and response schemas."""

from pydantic import BaseModel, Field, HttpUrl


class UserInfoRequest(BaseModel):
    """User information for email generation."""

    first_name: str = Field(..., description="User's first name")
    last_name: str = Field(..., description="User's last name")
    birth_date: str | None = Field(None, description="Birth date (DD.MM.YYYY)")
    insurance: str | None = Field(None, description="Health insurance name")
    email: str | None = Field(None, description="User's email address")
    phone: str | None = Field(None, description="User's phone number")
    vermittlungscode: str | None = Field(None, description="Referral code")

    @property
    def full_name(self) -> str:
        """Get full name."""
        return f"{self.first_name} {self.last_name}"


class TherapistResponse(BaseModel):
    """Therapist data in API response."""

    name: str
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    salutation: str | None = None
    specialty: str | None = None
    specialty_label: str | None = None
    distance_km: float | None = None
    sources: list[str] = []


class ParseResponse(BaseModel):
    """Response from parse endpoint."""

    therapists: list[TherapistResponse]
    total: int
    with_email: int


class ParseUrlRequest(BaseModel):
    """Request to fetch and parse a PDF from a remote URL."""

    url: HttpUrl = Field(
        ...,
        description="HTTPS URL pointing to a PDF hosted on psych-info.de",
    )


class CrawlRequest(BaseModel):
    """Request to live-crawl a therapist directory around an address."""

    source: str = Field(
        default="psychotherapeutensuche",
        description=(
            "Directory to crawl: 'psychotherapeutensuche' (nationwide, no "
            "emails) or 'ptk_bayern' (Bavaria, ~30% with emails)"
        ),
    )
    address: str = Field(
        ...,
        min_length=3,
        description="Free-text address, PLZ, or city to search around",
    )
    radius_km: float = Field(
        default=10.0, ge=1.0, le=50.0, description="Search radius in kilometres"
    )
    max_results: int = Field(
        default=30, ge=1, le=100, description="Maximum providers to return"
    )
    require_email: bool = Field(
        default=False,
        description="Only return therapists that list an email address",
    )


class SpecialtyOption(BaseModel):
    """A selectable specialty for the search UI dropdown."""

    key: str
    label: str


class SpecialtiesResponse(BaseModel):
    """List of selectable specialties, in display order."""

    specialties: list[SpecialtyOption]
    default: str


class EmailDraftResponse(BaseModel):
    """Email draft in API response."""

    to: str
    subject: str
    body: str
    therapist_name: str


class GenerateRequest(BaseModel):
    """Request for email generation."""

    therapists: list[TherapistResponse]
    user_info: UserInfoRequest
    template_body: str | None = Field(
        None,
        description=(
            "Optional template body override. If unset, the server-side "
            "default template is loaded from disk."
        ),
    )


class GenerateResponse(BaseModel):
    """Response from generate endpoint."""

    drafts: list[EmailDraftResponse]
    table_csv: str


class TemplateResponse(BaseModel):
    """Response from GET /emails/template."""

    body: str


class AiGenerateRequest(BaseModel):
    """Request for POST /emails/ai-generate."""

    target_lang: str = Field(..., description="Language code for the body (e.g. 'de')")
    insurance: str | None = Field(None, description="Insurance label, optional")
    therapist_emails: list[str] = Field(
        default_factory=list,
        description=(
            "Therapist emails in the current send batch; used to look up "
            "prior mail bodies so the AI can avoid repeating phrasing."
        ),
    )
    browser_id: str = Field(..., description="Anonymous per-browser identifier")


class AiGenerateResponse(BaseModel):
    """Response from POST /emails/ai-generate."""

    body: str


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    version: str = "1.0.0"


class ErrorResponse(BaseModel):
    """Error response."""

    detail: str
    code: str | None = None
