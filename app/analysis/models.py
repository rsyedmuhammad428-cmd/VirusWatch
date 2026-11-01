"""Inputs, configuration and results of the analysis engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

# Bump when any rule, default threshold or formula changes: results are keyed on it, so old
# results stay interpretable and are never silently overwritten.
METHODOLOGY_VERSION = "1.0.0"

AnalysisType = Literal[
    "freshness",
    "rolling_average",
    "trend",
    "baseline_comparison",
    "seasonal_comparison",
    "anomaly_detection",
]
Status = Literal["normal", "elevated", "possible_anomaly", "insufficient_data", "stale_data"]
# 'confirmed_by_source' is deliberately absent: the engine can never produce it. Only a human
# recording an explicit statement by a credible source may.

SMALL_DENOMINATOR_FLAG = "small_denominator"  # set by the ingestion layer (< 10 specimens)


@dataclass(frozen=True)
class SeriesKey:
    """Identity of one time series. Series are never merged across any of these fields."""

    geography_id: str
    country_code: str
    disease: str
    pathogen: str | None
    indicator: str
    surveillance_stream: str | None
    unit: str | None = None


@dataclass(frozen=True)
class Point:
    """One reporting period of one series, as stored."""

    observation_id: str
    period_start: date
    period_end: date
    value: Decimal | None  # None = missing. Never 0.
    data_status: str
    flags: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return self.value is not None

    @property
    def small_sample(self) -> bool:
        return SMALL_DENOMINATOR_FLAG in self.flags


@dataclass(frozen=True)
class IndicatorRule:
    """Indicator-specific scale. Without a rule an indicator is not analysed: guessing a scale
    for an unknown unit would make thresholds meaningless."""

    min_abs_increase: float  # smallest rise (in the indicator's unit) worth flagging
    min_scale: float  # smallest spread (same unit) treated as real variation


@dataclass(frozen=True)
class AnalysisConfig:
    # Freshness: weekly sources report with delay, so "stale" means clearly overdue, not late.
    stale_after_days: int = 28
    # Rolling average
    rolling_window_weeks: int = 4
    rolling_min_points: int = 3
    # Trend (Theil-Sen slope + Mann-Kendall)
    trend_window_weeks: int = 8
    trend_min_points: int = 6
    trend_alpha: float = 0.10
    # Trailing baseline (excludes the current week)
    baseline_window_weeks: int = 8
    baseline_min_points: int = 6
    # Seasonal baseline: same time of year, +/- half-width weeks, in each of up to N prior years
    seasonal_years: int = 5
    seasonal_half_width_weeks: int = 2
    seasonal_min_years: int = 3
    seasonal_min_points: int = 12
    # Date ranges (inclusive) excluded from baselines, e.g. periods of abnormal circulation.
    # Empty by default: choosing them is a scientific decision, not a coding one.
    exclude_ranges: tuple[tuple[date, date], ...] = ()
    # Alert thresholds on the robust z-score (modified z-score; 3.5 is the Iglewicz-Hoaglin cut-off)
    elevated_z: float = 2.0
    anomaly_z: float = 3.5
    rules: dict[str, IndicatorRule] = field(
        default_factory=lambda: {
            # percentage points of influenza positivity
            "influenza_positivity": IndicatorRule(min_abs_increase=2.0, min_scale=1.0)
        }
    )

    def __post_init__(self) -> None:
        if not 0 < self.elevated_z < self.anomaly_z:
            raise ValueError("require 0 < elevated_z < anomaly_z")
        if self.baseline_min_points > self.baseline_window_weeks:
            raise ValueError("baseline_min_points exceeds baseline_window_weeks")
        if self.trend_min_points > self.trend_window_weeks:
            raise ValueError("trend_min_points exceeds trend_window_weeks")
        if self.rolling_min_points > self.rolling_window_weeks:
            raise ValueError("rolling_min_points exceeds rolling_window_weeks")
        if self.seasonal_min_years > self.seasonal_years:
            raise ValueError("seasonal_min_years exceeds seasonal_years")
        if not 0 < self.trend_alpha < 1:
            raise ValueError("trend_alpha must be in (0, 1)")

    def parameters(self) -> dict[str, object]:
        """JSON-safe snapshot stored with every result (part of the reproducibility key)."""
        return {
            "stale_after_days": self.stale_after_days,
            "rolling_window_weeks": self.rolling_window_weeks,
            "rolling_min_points": self.rolling_min_points,
            "trend_window_weeks": self.trend_window_weeks,
            "trend_min_points": self.trend_min_points,
            "trend_alpha": self.trend_alpha,
            "baseline_window_weeks": self.baseline_window_weeks,
            "baseline_min_points": self.baseline_min_points,
            "seasonal_years": self.seasonal_years,
            "seasonal_half_width_weeks": self.seasonal_half_width_weeks,
            "seasonal_min_years": self.seasonal_min_years,
            "seasonal_min_points": self.seasonal_min_points,
            "exclude_ranges": [[a.isoformat(), b.isoformat()] for a, b in self.exclude_ranges],
            "elevated_z": self.elevated_z,
            "anomaly_z": self.anomaly_z,
            "rules": {
                k: {"min_abs_increase": v.min_abs_increase, "min_scale": v.min_scale}
                for k, v in sorted(self.rules.items())
            },
        }


@dataclass(frozen=True)
class AnalysisResult:
    """One row of public.analysis_results (geography/series identity carried by `key`)."""

    key: SeriesKey
    analysis_type: AnalysisType
    status: Status
    period_start: date
    period_end: date
    latest_observation_end: date | None
    observed_value: float | None
    baseline_value: float | None
    effect_size: float | None
    score: float | None
    threshold: float | None
    baseline_start: date | None
    baseline_end: date | None
    confidence_or_uncertainty: dict[str, object]
    parameters: dict[str, object]
    missing_data_handling: str
    limitations: str
    input_observation_ids: tuple[str, ...]
    input_hash: str
    explanation: str
    methodology_version: str = METHODOLOGY_VERSION
