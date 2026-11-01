"""Response models of the surveillance and sources endpoints."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, Field, PlainSerializer

# Pydantic would serialise Decimal as a JSON string ("12.5"). Clients expect numbers; values are
# stored with at most 6 decimals, which a float represents exactly enough for display.
Number = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]


class SourceRef(BaseModel):
    slug: str
    name: str
    publisher: str
    license_name: str | None
    terms_verified: bool = Field(description="False until someone has reviewed the source's terms.")


class SeriesRef(BaseModel):
    """Identity of one time series. Series are never merged across any of these fields."""

    geography_id: str
    country_code: str
    country_name: str
    geographic_resolution: str
    resolution_note: str
    disease: str
    pathogen: str | None
    indicator: str
    indicator_type: str | None = None
    surveillance_stream: str | None
    unit: str | None = None


class ObservationOut(BaseModel):
    observation_id: str
    period_start: date
    period_end: date
    value: Number | None = Field(description="null = missing / not reported. Never zero.")
    unit: str | None
    numerator: Number | None
    denominator: Number | None
    data_status: str
    quality_flags: list[str]
    published_at: datetime | None
    retrieved_at: datetime
    source_url: str
    source: SourceRef


class ObservationItem(ObservationOut):
    series: SeriesRef


class AnalysisOut(BaseModel):
    analysis_type: str
    status: str
    period_start: date
    period_end: date
    baseline_start: date | None
    baseline_end: date | None
    observed_value: Number | None
    baseline_value: Number | None
    effect_size: Number | None
    score: Number | None
    threshold: Number | None
    uncertainty: dict[str, Any]
    explanation: str | None
    limitations: str | None
    missing_data_handling: str | None
    methodology_version: str
    input_observation_count: int
    computed_at: datetime


class Freshness(BaseModel):
    latest_period_end: date
    data_age_days: int = Field(description="Days between the end of the latest period and today.")
    stale_after_days: int
    is_stale: bool


class Meta(BaseModel):
    generated_at: datetime
    notices: list[str]


class Page(BaseModel):
    total: int
    limit: int
    offset: int


class GeographyOut(BaseModel):
    geography_id: str
    country_code: str
    country_name: str
    geographic_resolution: str
    resolution_note: str
    region_name: str | None
    code_system: str | None
    observation_count: int
    first_period_start: date | None
    latest_period_end: date | None


class GeographiesResponse(BaseModel):
    data: list[GeographyOut]
    meta: Meta


class ObservationsResponse(BaseModel):
    data: list[ObservationItem]
    page: Page
    meta: Meta


class LatestSeries(BaseModel):
    series: SeriesRef
    latest: ObservationOut
    freshness: Freshness
    signal: str = Field(
        description="Status of the latest anomaly screening, or 'not_analysed'. "
        "A signal is a statistical flag, never a confirmed outbreak."
    )
    analyses: dict[str, AnalysisOut]


class LatestResponse(BaseModel):
    data: list[LatestSeries]
    meta: Meta


class TrendPoint(BaseModel):
    period_start: date
    period_end: date
    value: Number | None
    data_status: str
    quality_flags: list[str]


class TrendSeries(BaseModel):
    series: SeriesRef
    points: list[TrendPoint]
    trend: AnalysisOut | None
    rolling_average: AnalysisOut | None
    freshness: Freshness | None


class TrendsResponse(BaseModel):
    data: list[TrendSeries]
    meta: Meta


class AlertOut(BaseModel):
    series: SeriesRef
    status: str
    analysis: AnalysisOut
    freshness: Freshness | None
    disclaimer: str


class AlertsResponse(BaseModel):
    data: list[AlertOut]
    meta: Meta


class ComparisonCell(BaseModel):
    value: Number | None
    numerator: Number | None
    denominator: Number | None
    data_status: str
    quality_flags: list[str]


class ComparisonCountry(BaseModel):
    country_code: str
    country_name: str
    geographic_resolution: str
    resolution_note: str
    source: SourceRef | None
    weeks_with_data: int
    latest_period_end: date | None


class ComparisonWeek(BaseModel):
    period_start: date
    period_end: date
    values: dict[str, ComparisonCell | None] = Field(
        description="Keyed by country code; null where that country reported nothing that week."
    )


class ComparisonResponse(BaseModel):
    indicator: str
    unit: str | None
    surveillance_stream: str | None
    countries: list[ComparisonCountry]
    countries_without_data: list[str]
    weeks: list[ComparisonWeek]
    caveats: list[str]
    meta: Meta


class SourceOut(BaseModel):
    source_id: str
    slug: str
    name: str
    publisher: str
    source_url: str
    source_type: str
    update_cadence: str | None
    enabled: bool
    license_name: str | None
    license_url: str | None
    terms_verified: bool


class SourcesResponse(BaseModel):
    data: list[SourceOut]
    meta: Meta


class SourceStatusOut(BaseModel):
    source_id: str
    slug: str
    name: str
    enabled: bool
    update_cadence: str | None
    last_successful_run_at: datetime | None
    last_run_status: str | None
    latest_reporting_period_end: date | None
    latest_published_at: datetime | None
    latest_retrieved_at: datetime | None


class SourceStatusResponse(BaseModel):
    data: list[SourceStatusOut]
    meta: Meta


class RunOut(BaseModel):
    run_id: str
    started_at: datetime
    completed_at: datetime | None
    status: str
    triggered_by: str
    dry_run: bool
    records_downloaded: int
    records_inserted: int
    records_updated: int
    records_rejected: int
    checksum: str | None
    source_published_at: datetime | None
    error_summary: str | None


class RunsResponse(BaseModel):
    data: list[RunOut]
    meta: Meta
