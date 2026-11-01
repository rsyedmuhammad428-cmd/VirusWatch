"""Canonical in-memory representation of ingested data and run results.

ObservationRecord mirrors the CHECK constraints on public.observations so invalid combinations
fail in Python (with a clear message) before they ever reach the database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

_IDENTIFIER = re.compile(r"^[a-z0-9_]+$")

IndicatorType = Literal[
    "clinical_surveillance",
    "laboratory_positivity",
    "laboratory_count",
    "wastewater",
    "hospitalization",
    "mortality",
    "other",
]
DataStatus = Literal["reported", "provisional", "revised", "estimated", "missing"]
SurveillanceStream = Literal["sentinel", "nonsentinel", "undefined"]


class ObservationRecord(BaseModel):
    """One value for one indicator, in one geography, for one reporting period."""

    model_config = ConfigDict(frozen=True)

    country_code: str  # ISO 3166-1 alpha-2; resolved to a geography row at persistence time
    disease: str
    pathogen: str | None = None
    indicator: str
    indicator_type: IndicatorType
    surveillance_stream: SurveillanceStream | None = None
    reporting_period_start: date
    reporting_period_end: date
    value_numeric: Decimal | None = None
    numerator: Decimal | None = None
    denominator: Decimal | None = None
    unit: str | None = None
    data_status: DataStatus = "reported"
    quality_flags: tuple[str, ...] = ()
    source_record_id: str
    source_url: str
    metadata: dict[str, object] = {}

    @field_validator("country_code")
    @classmethod
    def _iso2(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Z]{2}", v):
            raise ValueError(f"country_code must be ISO 3166-1 alpha-2, got {v!r}")
        return v

    @field_validator("disease", "pathogen", "indicator")
    @classmethod
    def _identifier(cls, v: str | None) -> str | None:
        if v is not None and not _IDENTIFIER.fullmatch(v):
            raise ValueError(f"identifier must be lowercase snake_case, got {v!r}")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> ObservationRecord:
        if self.reporting_period_end < self.reporting_period_start:
            raise ValueError("reporting_period_end precedes reporting_period_start")
        if self.data_status == "missing":
            if self.value_numeric is not None:
                raise ValueError("a missing observation must not carry a value")
        elif self.value_numeric is None:
            raise ValueError(f"a {self.data_status!r} observation needs a value")
        if self.value_numeric is not None and self.unit is None:
            raise ValueError("a numeric value needs a unit")
        for number in (self.value_numeric, self.numerator, self.denominator):
            if number is not None and not number.is_finite():
                raise ValueError("numeric values must be finite")
        if self.denominator is not None and self.denominator < 0:
            raise ValueError("denominator must not be negative")
        return self

    @property
    def natural_key(self) -> tuple[object, ...]:
        """Identity used for idempotent upserts; mirrors observations_natural_key."""
        return (
            self.country_code,
            self.disease,
            self.pathogen,
            self.indicator,
            self.reporting_period_start,
            self.reporting_period_end,
            self.surveillance_stream,
        )


@dataclass(frozen=True)
class RowRejection:
    row_number: int  # 1-based line number in the source file (header is line 1)
    reason: str  # stable machine-readable code
    detail: str = ""


@dataclass
class ParseResult:
    observations: list[ObservationRecord] = field(default_factory=list)
    rejections: list[RowRejection] = field(default_factory=list)
    rows_read: int = 0
    rows_out_of_scope: int = 0  # valid rows for countries/years we were not asked for
    rows_duplicate_identical: int = 0
    warnings: list[str] = field(default_factory=list)
    columns: tuple[str, ...] = ()

    @property
    def rows_rejected(self) -> int:
        return len({r.row_number for r in self.rejections})

    @property
    def rows_in_scope(self) -> int:
        return self.rows_read - self.rows_out_of_scope

    @property
    def latest_period_end(self) -> date | None:
        return max((o.reporting_period_end for o in self.observations), default=None)

    def reject_reason_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.rejections:
            counts[r.reason] = counts.get(r.reason, 0) + 1
        return dict(sorted(counts.items()))


@dataclass(frozen=True)
class Snapshot:
    """A downloaded file, exactly as received."""

    url: str
    content: bytes
    checksum: str  # "sha256:<hex>"
    retrieved_at: datetime  # timezone-aware, UTC
    content_type: str | None = None
    source_published_at: datetime | None = None  # only if the server told us (Last-Modified)


class RunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED_UNCHANGED = "skipped_unchanged"
    # The two below are never written to ingestion_runs (no run is started).
    SKIPPED_DISABLED = "skipped_disabled"
    SKIPPED_ALREADY_RUNNING = "skipped_already_running"


@dataclass(frozen=True)
class IngestRequest:
    countries: frozenset[str]  # ISO 3166-1 alpha-3, validated against app.ingestion.countries
    since_year: int | None = None  # ISO year lower bound; None = everything the source has
    dry_run: bool = False
    force: bool = False  # re-ingest even if the snapshot is unchanged
    triggered_by: Literal["manual", "scheduled", "backfill", "test"] = "manual"


@dataclass(frozen=True)
class PersistOutcome:
    inserted: int
    updated: int  # existing observations whose value-bearing fields changed (a revision was logged)
    unchanged: int  # existing observations re-confirmed by this snapshot


@dataclass(frozen=True)
class RunSummary:
    status: RunStatus
    run_id: object | None  # UUID when a run row exists
    source_slug: str
    dry_run: bool
    rows_read: int = 0
    rows_rejected: int = 0
    rows_out_of_scope: int = 0
    observations_inserted: int = 0
    observations_updated: int = 0
    observations_unchanged: int = 0
    checksum: str | None = None
    latest_period_end: date | None = None
    error: str | None = None
    reject_reasons: dict[str, int] = field(default_factory=dict)
