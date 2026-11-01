"""Deterministic analysis of one weekly surveillance series.

Pure function of (series, configuration, as_of date): no clock, database, network or randomness,
so every result can be reproduced from the stored inputs and parameters.

What this module will never do:
- turn a missing value into zero (gaps stay gaps),
- label anything an outbreak (the strongest status is `possible_anomaly`, a statistical flag),
- analyse an indicator that has no explicit IndicatorRule (unknown scale, unknown meaning),
- raise an alert from data that is stale, blank, or based on a tiny sample.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.analysis import stats
from app.analysis.models import (
    METHODOLOGY_VERSION,
    AnalysisConfig,
    AnalysisResult,
    AnalysisType,
    IndicatorRule,
    Point,
    SeriesKey,
    Status,
)

WEEK = timedelta(days=7)

_MISSING_HANDLING = (
    "Missing or blank weeks are never treated as zero: they are skipped, and a minimum number of "
    "reported weeks is required. Weeks flagged small_denominator are excluded from every statistic "
    "and can never raise an alert."
)
_LIMITS_COMMON = (
    "Statistical screening of one reported series; it does not confirm or exclude an outbreak. "
    "Reporting delays and later revisions can change results."
)


class UnsupportedSeriesError(ValueError):
    """The series cannot be analysed by this engine (e.g. not weekly, or duplicate periods)."""


@dataclass(frozen=True)
class _Baseline:
    kind: str  # 'trailing' | 'seasonal'
    median: float
    scale: float
    n_points: int
    n_years: int | None
    start: date
    end: date
    ids: tuple[str, ...]
    values: tuple[float, ...]


# --------------------------------------------------------------------------- helpers
def _num(value: Decimal | None) -> float:
    if value is None:
        raise ValueError("missing value has no number")
    return float(value)


def _round(x: float | None) -> float | None:
    return None if x is None else round(x, 6)


def _canonical(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def _reliable(p: Point) -> bool:
    return p.usable and not p.small_sample


def _in_excluded(p: Point, cfg: AnalysisConfig) -> bool:
    return any(p.period_start <= hi and p.period_end >= lo for lo, hi in cfg.exclude_ranges)


def _fmt(x: float) -> str:
    return f"{x:.2f}"


def _input_hash(
    analysis_type: str,
    key: SeriesKey,
    cfg: AnalysisConfig,
    points: list[Point],
    extra: dict[str, object] | None = None,
) -> str:
    """Reproducibility key: identical inputs + parameters + method version => identical hash."""
    payload = {
        "method": METHODOLOGY_VERSION,
        "type": analysis_type,
        "series": [
            key.geography_id,
            key.disease,
            key.pathogen,
            key.indicator,
            key.surveillance_stream,
        ],
        "params": cfg.parameters(),
        "inputs": [
            [
                p.observation_id,
                p.period_start.isoformat(),
                _canonical(p.value),
                p.data_status,
                sorted(p.flags),
            ]
            for p in points
        ],
        "extra": extra or {},
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode()).hexdigest()


def _validate(points: list[Point]) -> list[Point]:
    ordered = sorted(points, key=lambda p: (p.period_end, p.period_start))
    for p in ordered:
        if (p.period_end - p.period_start).days != 6:
            raise UnsupportedSeriesError(
                f"only weekly (7-day) periods are supported; got {p.period_start}..{p.period_end}"
            )
    starts = [p.period_start for p in ordered]
    if len(set(starts)) != len(starts):
        raise UnsupportedSeriesError("duplicate reporting periods in one series")
    return ordered


# --------------------------------------------------------------------------- baselines
def _trailing_baseline(
    by_start: dict[date, Point], current: Point, cfg: AnalysisConfig, rule: IndicatorRule
) -> _Baseline | str:
    weeks = [current.period_start - WEEK * k for k in range(1, cfg.baseline_window_weeks + 1)]
    pts = [by_start[w] for w in reversed(weeks) if w in by_start]
    good = [p for p in pts if _reliable(p) and not _in_excluded(p, cfg)]
    if len(good) < cfg.baseline_min_points:
        return (
            f"only {len(good)} usable weeks in the {cfg.baseline_window_weeks} weeks before the "
            f"current one (need {cfg.baseline_min_points})"
        )
    values = tuple(_num(p.value) for p in good)
    return _Baseline(
        kind="trailing",
        median=stats.median(values),
        scale=stats.robust_scale(values, floor=rule.min_scale),
        n_points=len(good),
        n_years=None,
        start=good[0].period_start,
        end=good[-1].period_end,
        ids=tuple(p.observation_id for p in good),
        values=values,
    )


def _seasonal_baseline(
    by_start: dict[date, Point], current: Point, cfg: AnalysisConfig, rule: IndicatorRule
) -> _Baseline | str:
    half = timedelta(days=7 * cfg.seasonal_half_width_weeks)
    good: list[Point] = []
    years = 0
    for y in range(1, cfg.seasonal_years + 1):
        centre = current.period_start - timedelta(days=364 * y)  # 52 weeks keeps the weekday
        found = [
            by_start[d]
            for d in (
                centre + WEEK * k
                for k in range(-cfg.seasonal_half_width_weeks, cfg.seasonal_half_width_weeks + 1)
            )
            if d in by_start and abs(d - centre) <= half
        ]
        found = [p for p in found if _reliable(p) and not _in_excluded(p, cfg)]
        if found:
            years += 1
            good.extend(found)
    if years < cfg.seasonal_min_years or len(good) < cfg.seasonal_min_points:
        return (
            f"seasonal history has {len(good)} usable weeks from {years} prior years "
            f"(need {cfg.seasonal_min_points} weeks from {cfg.seasonal_min_years} years)"
        )
    good.sort(key=lambda p: p.period_start)
    values = tuple(_num(p.value) for p in good)
    return _Baseline(
        kind="seasonal",
        median=stats.median(values),
        scale=stats.robust_scale(values, floor=rule.min_scale),
        n_points=len(good),
        n_years=years,
        start=good[0].period_start,
        end=good[-1].period_end,
        ids=tuple(p.observation_id for p in good),
        values=values,
    )


def _grade(
    value: float, base: _Baseline, rule: IndicatorRule, cfg: AnalysisConfig, *, allow_anomaly: bool
) -> tuple[Status, float, float]:
    """(status, effect, robust z). A flag needs BOTH statistical and practical significance."""
    effect = value - base.median
    z = effect / base.scale
    if effect >= rule.min_abs_increase and z >= cfg.elevated_z:
        if allow_anomaly and z >= cfg.anomaly_z:
            return "possible_anomaly", effect, z
        return "elevated", effect, z
    return "normal", effect, z


# --------------------------------------------------------------------------- the analyses
class _Context:
    """Everything the individual analyses share, computed once per series."""

    def __init__(self, key: SeriesKey, points: list[Point], cfg: AnalysisConfig, as_of: date):
        self.key, self.cfg, self.as_of = key, cfg, as_of
        self.rule = cfg.rules[key.indicator]
        self.points = _validate(points)
        self.by_start = {p.period_start: p for p in self.points}
        self.current = self.points[-1]
        usable = [p for p in self.points if p.usable]
        self.latest_usable = usable[-1] if usable else None
        self.age_days = (as_of - self.latest_usable.period_end).days if self.latest_usable else None
        self.stale = self.age_days is not None and self.age_days > cfg.stale_after_days

    def result(
        self,
        analysis_type: AnalysisType,
        status: Status,
        explanation: str,
        *,
        inputs: list[Point],
        start: date | None = None,
        end: date | None = None,
        observed: float | None = None,
        baseline: float | None = None,
        effect: float | None = None,
        score: float | None = None,
        threshold: float | None = None,
        base: _Baseline | None = None,
        uncertainty: dict[str, object] | None = None,
        limitations: str = _LIMITS_COMMON,
        extra: dict[str, object] | None = None,
    ) -> AnalysisResult:
        ids = tuple(p.observation_id for p in inputs)
        base_ids = base.ids if base else ()
        return AnalysisResult(
            key=self.key,
            analysis_type=analysis_type,
            status=status,
            period_start=start or self.current.period_start,
            period_end=end or self.current.period_end,
            latest_observation_end=self.current.period_end,
            observed_value=_round(observed),
            baseline_value=_round(baseline),
            effect_size=_round(effect),
            score=_round(score),
            threshold=_round(threshold),
            baseline_start=base.start if base else None,
            baseline_end=base.end if base else None,
            confidence_or_uncertainty=uncertainty or {},
            parameters=self.cfg.parameters(),
            missing_data_handling=_MISSING_HANDLING,
            limitations=limitations,
            input_observation_ids=tuple(dict.fromkeys((*ids, *base_ids))),
            input_hash=_input_hash(analysis_type, self.key, self.cfg, inputs, extra),
            explanation=explanation,
        )

    def inputs_with(self, base: _Baseline | None) -> list[Point]:
        if base is None:
            return [self.current]
        ids = set(base.ids)
        return [*[p for p in self.points if p.observation_id in ids], self.current]

    @property
    def label(self) -> str:
        unit = f" {self.key.unit}" if self.key.unit else ""
        return f"{self.key.indicator} for the week starting {self.current.period_start}{unit}"


def _freshness(c: _Context) -> AnalysisResult:
    if c.latest_usable is None or c.age_days is None:
        return c.result(
            "freshness",
            "insufficient_data",
            "No reported (non-missing) values exist for this series.",
            inputs=[c.current],
            extra={"as_of": c.as_of.isoformat()},
        )
    status: Status = "stale_data" if c.stale else "normal"
    text = (
        f"Latest reported period ends {c.latest_usable.period_end}, {c.age_days} days before "
        f"{c.as_of}. "
        + (
            f"This is older than the staleness limit of {c.cfg.stale_after_days} days."
            if c.stale
            else f"Within the {c.cfg.stale_after_days}-day staleness limit."
        )
    )
    return c.result(
        "freshness",
        status,
        text,
        inputs=[c.latest_usable],
        observed=float(c.age_days),
        threshold=float(c.cfg.stale_after_days),
        uncertainty={"unit": "days since end of latest reported period"},
        limitations="Age is measured from the end of the reporting period, not from publication.",
        extra={"as_of": c.as_of.isoformat()},
    )


def _window(c: _Context, weeks: int) -> list[Point]:
    start = c.current.period_start - WEEK * (weeks - 1)
    return [p for p in c.points if p.period_start >= start]


def _rolling(c: _Context) -> AnalysisResult:
    win = _window(c, c.cfg.rolling_window_weeks)
    good = [p for p in win if _reliable(p)]
    start = c.current.period_start - WEEK * (c.cfg.rolling_window_weeks - 1)
    if len(good) < c.cfg.rolling_min_points:
        return c.result(
            "rolling_average",
            "insufficient_data",
            f"{len(good)} usable weeks in the last {c.cfg.rolling_window_weeks} "
            f"(need {c.cfg.rolling_min_points}); no average computed.",
            inputs=win or [c.current],
            start=start,
        )
    avg = stats.mean([_num(p.value) for p in good])
    return c.result(
        "rolling_average",
        "normal",
        f"Mean of {len(good)} usable weeks out of the last {c.cfg.rolling_window_weeks}: "
        f"{_fmt(avg)}{(' ' + c.key.unit) if c.key.unit else ''}.",
        inputs=good,
        start=start,
        observed=avg,
        uncertainty={"n_points": len(good), "window_weeks": c.cfg.rolling_window_weeks},
    )


def _trend(c: _Context) -> AnalysisResult:
    win = _window(c, c.cfg.trend_window_weeks)
    good = [p for p in win if _reliable(p)]
    start = c.current.period_start - WEEK * (c.cfg.trend_window_weeks - 1)
    if len(good) < c.cfg.trend_min_points:
        return c.result(
            "trend",
            "insufficient_data",
            f"{len(good)} usable weeks in the last {c.cfg.trend_window_weeks} "
            f"(need {c.cfg.trend_min_points}); no trend computed.",
            inputs=win or [c.current],
            start=start,
        )
    xs = [float((p.period_start - good[0].period_start).days // 7) for p in good]
    ys = [_num(p.value) for p in good]
    slope = stats.theil_sen_slope(xs, ys)
    _, z, p_value = stats.mann_kendall(ys)
    direction = "no_clear_trend"
    if p_value < c.cfg.trend_alpha:
        direction = "increasing" if slope > 0 else "decreasing" if slope < 0 else "no_clear_trend"
    return c.result(
        "trend",
        "normal",
        f"Over {len(good)} usable weeks the Theil-Sen slope is {slope:+.3f} per week "
        f"(Mann-Kendall z = {z:.2f}, p = {p_value:.3f}): {direction.replace('_', ' ')}.",
        inputs=good,
        start=start,
        observed=ys[-1],
        effect=slope,
        score=z,
        threshold=c.cfg.trend_alpha,
        uncertainty={
            "direction": direction,
            "p_value": round(p_value, 6),
            "n_points": len(good),
            "note": "p-value is an indication only for fewer than ~8 points",
        },
        limitations=_LIMITS_COMMON + " A trend describes recent direction, not future values.",
    )


def _comparison(
    c: _Context, kind: str, base: _Baseline | str, analysis_type: AnalysisType
) -> AnalysisResult:
    cur = c.current
    if isinstance(base, str):
        return c.result(
            analysis_type, "insufficient_data", f"No {kind} baseline: {base}.", inputs=[cur]
        )
    problem = _current_problem(c)
    if problem:
        return c.result(
            analysis_type,
            "insufficient_data",
            problem,
            inputs=c.inputs_with(base),
            base=base,
            baseline=base.median,
        )
    value = _num(cur.value)
    status, effect, z = _grade(value, base, c.rule, c.cfg, allow_anomaly=False)
    return c.result(
        analysis_type,
        status,
        _comparison_text(c, kind, value, base, z, status),
        inputs=c.inputs_with(base),
        observed=value,
        baseline=base.median,
        effect=effect,
        score=z,
        threshold=c.cfg.elevated_z,
        base=base,
        uncertainty=_base_uncertainty(base),
        limitations=_LIMITS_COMMON + _baseline_caveat(base),
    )


def _current_problem(c: _Context) -> str | None:
    if not c.current.usable:
        return (
            f"The latest period ({c.current.period_start}) was reported blank, "
            "so nothing is compared."
        )
    if c.current.small_sample:
        return "The latest period rests on a very small sample, so it is not compared."
    return None


def _base_uncertainty(base: _Baseline) -> dict[str, object]:
    out: dict[str, object] = {
        "baseline_kind": base.kind,
        "n_baseline_points": base.n_points,
        "baseline_robust_sigma": round(base.scale, 6),
    }
    if base.n_years is not None:
        out["n_baseline_years"] = base.n_years
    return out


def _baseline_caveat(base: _Baseline) -> str:
    if base.kind == "seasonal":
        return (
            " Seasonal baselines assume past seasons are representative; "
            "unusual past seasons distort them."
        )
    return " A trailing baseline cannot tell a normal seasonal rise from an unusual one."


def _comparison_text(
    c: _Context, kind: str, value: float, base: _Baseline, z: float, status: Status
) -> str:
    head = (
        f"{c.label} is {_fmt(value)} against a {kind} baseline median of {_fmt(base.median)} "
        f"({base.n_points} weeks; robust z = {z:.2f}). "
    )
    if status == "normal":
        return head + "Within the expected range."
    tail = (
        "Above the expected range. " if status == "elevated" else "Well above the expected range. "
    )
    return head + tail + "This is a statistical flag only; it does not confirm an outbreak."


def _anomaly(c: _Context, seasonal: _Baseline | str, trailing: _Baseline | str) -> AnalysisResult:
    cur = c.current
    if c.stale:
        latest_end = c.latest_usable.period_end if c.latest_usable else "never"
        return c.result(
            "anomaly_detection",
            "stale_data",
            f"Latest reported period ended {latest_end} "
            f"({c.age_days} days before {c.as_of}); too old to assess.",
            inputs=[cur],
            extra={"stale": True},
        )
    problem = _current_problem(c)
    base = seasonal if not isinstance(seasonal, str) else trailing
    if problem or isinstance(base, str):
        reason = problem or f"No usable baseline (seasonal: {seasonal}; trailing: {trailing})."
        return c.result(
            "anomaly_detection", "insufficient_data", reason, inputs=[cur], extra={"stale": False}
        )
    value = _num(cur.value)
    status, effect, z = _grade(value, base, c.rule, c.cfg, allow_anomaly=True)
    return c.result(
        "anomaly_detection",
        status,
        _comparison_text(c, base.kind, value, base, z, status),
        inputs=c.inputs_with(base),
        observed=value,
        baseline=base.median,
        effect=effect,
        score=z,
        threshold=c.cfg.anomaly_z,
        base=base,
        uncertainty={
            **_base_uncertainty(base),
            "elevated_z": c.cfg.elevated_z,
            "min_abs_increase": c.rule.min_abs_increase,
        },
        limitations=_LIMITS_COMMON + _baseline_caveat(base),
        extra={"stale": False},
    )


def analyze_series(
    key: SeriesKey, points: list[Point], config: AnalysisConfig, as_of: date
) -> list[AnalysisResult]:
    """All analyses for one series. Returns [] when the indicator has no rule or no points."""
    if key.indicator not in config.rules or not points:
        return []
    c = _Context(key, points, config, as_of)
    seasonal = _seasonal_baseline(c.by_start, c.current, config, c.rule)
    trailing = _trailing_baseline(c.by_start, c.current, config, c.rule)
    return [
        _freshness(c),
        _rolling(c),
        _trend(c),
        _comparison(c, "trailing", trailing, "baseline_comparison"),
        _comparison(c, "seasonal", seasonal, "seasonal_comparison"),
        _anomaly(c, seasonal, trailing),
    ]
