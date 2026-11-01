"""Countries the ingestion layer is allowed to create/resolve geographies for.

WHO identifies countries by ISO 3166-1 alpha-3; our `geographies.country_code` is alpha-2.
Only countries listed here can be requested. Adding one is a deliberate, reviewed change:
append a row and double-check both codes against ISO 3166-1.

No population data lives here (see section 7: never invent population values).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ingestion.errors import IngestionError


@dataclass(frozen=True)
class Country:
    iso3: str
    iso2: str
    name: str


SUPPORTED_COUNTRIES: dict[str, Country] = {
    c.iso3: c
    for c in (
        Country("PAK", "PK", "Pakistan"),
        Country("USA", "US", "United States"),
        Country("IND", "IN", "India"),
        Country("BGD", "BD", "Bangladesh"),
        Country("AFG", "AF", "Afghanistan"),
        Country("IRN", "IR", "Iran"),
        Country("GBR", "GB", "United Kingdom"),
        Country("CAN", "CA", "Canada"),
    )
}


def resolve_countries(iso3_codes: list[str] | tuple[str, ...] | frozenset[str]) -> frozenset[str]:
    """Validate requested ISO3 codes; raise with a helpful message on the first unsupported one."""
    cleaned = {c.strip().upper() for c in iso3_codes if c and c.strip()}
    if not cleaned:
        raise IngestionError("At least one country is required.")
    unsupported = sorted(cleaned - SUPPORTED_COUNTRIES.keys())
    if unsupported:
        raise IngestionError(
            f"Unsupported country code(s): {', '.join(unsupported)}. "
            f"Supported: {', '.join(sorted(SUPPORTED_COUNTRIES))}. "
            "Add new countries in app/ingestion/countries.py."
        )
    return frozenset(cleaned)
