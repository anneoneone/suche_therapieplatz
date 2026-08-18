"""Base abstraction for healthcare-provider data sources."""

from abc import ABC, abstractmethod
from types import TracebackType

from pydantic import BaseModel, Field

from therapist_finder.models import TherapistData


class SearchParams(BaseModel):
    """Parameters a source needs to search around a geocoded address."""

    specialty: str = Field(
        default="Psychotherapeut",
        description="Specialty to filter by (source-specific keyword)",
    )
    lat: float = Field(..., description="Latitude of the search origin")
    lon: float = Field(..., description="Longitude of the search origin")
    radius_km: float = Field(
        default=10.0, ge=0.1, le=50.0, description="Search radius in kilometres"
    )
    limit_per_source: int = Field(
        default=100,
        ge=1,
        le=500,
        description="Max results requested from each source (over-fetch)",
    )
    languages: list[str] = Field(
        default_factory=list,
        description="Required spoken languages (source-specific)",
    )
    postal_code: str | None = Field(
        default=None,
        description=(
            "Postal code of the search origin, for sources whose upstream "
            "search is PLZ-based rather than coordinate-based (e.g. PTK "
            "Bayern)"
        ),
    )
    city: str | None = Field(
        default=None,
        description="City of the search origin (fallback for PLZ-based sources)",
    )


class TherapistSource(ABC):
    """A directory of healthcare providers that can be queried by location."""

    name: str

    @abstractmethod
    def search(self, params: SearchParams) -> list[TherapistData]:
        """Return providers matching ``params``.

        Implementations should tag each returned :class:`TherapistData` with
        ``sources=[self.name]`` and populate ``lat``/``lon`` when the upstream
        directory exposes them.
        """

    def close(self) -> None:  # noqa: B027 - default is no-op; subclasses may override
        """Release any held resources (HTTP clients, caches, ...)."""

    def __enter__(self) -> "TherapistSource":
        """Context manager entry."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Context manager exit releases resources."""
        self.close()
