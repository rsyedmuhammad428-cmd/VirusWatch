"""Dataset classes, licence states and the permission rules derived from them.

The three questions the platform must be able to answer for every dataset are separate on purpose:
  * may we show it to the public (with attribution)?
  * may we redistribute the raw file?
  * may we use it commercially?
Unverified or unstated terms answer "no" to all three; the data may still be used internally for
validation. Research papers are a different class and never feed case counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DataClass(StrEnum):
    SURVEILLANCE = "surveillance"  # counts/rates of disease events or lab results, per period
    HEALTH_CONTEXT = "health_context"  # surveys, coverage, burden estimates, services
    FACILITY_REFERENCE = "facility_reference"  # where facilities are; no disease data
    RESEARCH = "research"  # papers; evidence for explanations, never current counts
    COMPILATION = "compilation"  # a re-bundle of other documents; not an original source


class LicenceState(StrEnum):
    OPEN = "open"  # verified permissive licence (attribution at most)
    NONCOMMERCIAL = "noncommercial"  # verified licence that forbids commercial use
    NONE_STATED = "none_stated"  # source page shows no licence
    UNVERIFIED = "unverified"  # terms exist but were not read


@dataclass(frozen=True)
class UsePermission:
    internal_validation: bool
    public_display: bool
    redistribute_raw: bool
    commercial: bool
    reason: str


def permissions(data_class: DataClass, state: LicenceState) -> UsePermission:
    """The only place that turns a licence state into permissions."""
    if data_class is DataClass.RESEARCH:
        # Papers are cited, never redistributed; their licences are per article.
        return UsePermission(True, False, False, False, "per-article licence; cite, do not host")
    if data_class is DataClass.COMPILATION:
        return UsePermission(True, False, False, False, "derivative bundle; use the originals")
    if state is LicenceState.OPEN:
        return UsePermission(True, True, True, True, "verified open licence; attribute")
    if state is LicenceState.NONCOMMERCIAL:
        return UsePermission(
            True, True, False, False, "non-commercial only; commercial use needs permission"
        )
    why = "no licence stated" if state is LicenceState.NONE_STATED else "terms not verified"
    return UsePermission(True, False, False, False, f"{why}: internal validation only")


@dataclass(frozen=True)
class DatasetRecord:
    dataset_id: str
    title: str
    data_class: DataClass
    publisher: str | None  # who produced it, only if the source page or file says so
    filename: str  # name as supplied
    sha256: str
    size_bytes: int
    download_url: str | None  # only set when read from the source's own page
    licence_name: str | None
    licence_state: LicenceState
    licence_evidence_url: str | None
    coverage: str  # human-readable period, as observed in the file
    geography: str  # coarsest-to-finest levels actually present
    indicators: str
    notes: str = ""

    @property
    def use(self) -> UsePermission:
        return permissions(self.data_class, self.licence_state)
