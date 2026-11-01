"""Turn database rows into API responses, and enforce the rules the API must never break.

Pure functions of (rows, today/now): no database, no clock, no FastAPI, so every rule is unit
tested. Rows may come straight from asyncpg (typed values) or from tests (strings); pydantic
coerces both.

Rules enforced here:
- national data is always described as national (never as a city or province),
- staleness is computed from today's date, not trusted from an old analysis row,
- a flagged result is never presented as an outbreak and is withheld once its data are stale,
- cross-country comparison is limited to rate indicators with explicit denominators, one sampling
  stream at a time, and never produces a difference, ranking or score between countries.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Any

from app.analysis.models import AnalysisConfig
from app.surveillance import schemas as s

Row = Mapping[str, Any]
SeriesId = tuple[str, str, str | None, str, str | None]

STALE_AFTER_DAYS = AnalysisConfig().stale_after_days  # single source of truth with the engine
FLAGGED = frozenset({"elevated", "possible_anomaly"})

# Rate indicators with an explicit denominator that may be shown side by side across countries.
COMPARABLE_INDICATORS = frozenset({"influenza_positivity"})
COMPARABLE_INDICATOR_TYPES = frozenset({"laboratory_positivity"})

BASE_NOTICES = (
    "Values are reported surveillance indicators, not individual diagnoses.",
    "A statistical flag (elevated, possible_anomaly) is a screening signal. It does not confirm "
    "an outbreak.",
    "A null value means the source did not report a number. It is never zero.",
)
ALERT_DISCLAIMER = (
    "Statistical screening result based on one reported indicator. It does not confirm an "
    "outbreak; check the source and the data limitations before acting on it."
)
COMPARISON_CAVEATS = (
    "Positivity depends on who is tested and why; sentinel networks differ between countries.",
    "Testing volume, laboratory methods and case definitions can differ, so a higher or lower "
    "value does not by itself mean more or less influenza.",
    "Countries report with different delays; recent weeks may be incomplete and are revised later.",
    "Values are shown side by side only. No difference, ranking or score between countries is "
    "calculated.",
)


class ComparisonError(ValueError):
    """The requested comparison would be misleading; `code` is machine-readable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- small helpers
_DATE_COLUMNS = (
    "period_start",
    "period_end",
    "baseline_start",
    "baseline_end",
    "latest_observation_end",
    "first_period_start",
    "latest_period_end",
)


def _normalise(rows: Iterable[Row]) -> list[dict[str, Any]]:
    """Copy rows with date columns as `date` objects (drivers may return ISO strings)."""
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        for column in _DATE_COLUMNS:
            value = row.get(column)
            if isinstance(value, str) and value:
                row[column] = date.fromisoformat(value)
        out.append(row)
    return out


def meta(now: datetime, extra: Sequence[str] = ()) -> s.Meta:
    return s.Meta(generated_at=now, notices=[*BASE_NOTICES, *extra])


def resolution_note(level: str, region_name: str | None = None) -> str:
    if level == "country":
        return (
            "National-level data: describes the whole country and must not be read as "
            "describing any city, province or region."
        )
    where = f" '{region_name}'" if region_name else ""
    return f"Data describe only the {level}-level area{where}, not any larger or smaller area."


def _flags(raw: Any) -> list[str]:
    if raw in (None, ""):
        return []
    values = json.loads(raw) if isinstance(raw, str) else raw
    return [str(v) for v in values or []]


def _json(raw: Any) -> dict[str, Any]:
    if raw in (None, ""):
        return {}
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


def series_id(row: Row) -> SeriesId:
    return (
        str(row["geography_id"]),
        str(row["disease"]),
        row["pathogen"] or None,
        str(row["indicator"]),
        row["surveillance_stream"] or None,
    )


def series_ref(row: Row) -> s.SeriesRef:
    return s.SeriesRef(
        geography_id=str(row["geography_id"]),
        country_code=row["country_code"],
        country_name=row["country_name"],
        geographic_resolution=row["geographic_resolution"],
        resolution_note=resolution_note(row["geographic_resolution"]),
        disease=row["disease"],
        pathogen=row["pathogen"] or None,
        indicator=row["indicator"],
        indicator_type=row.get("indicator_type"),
        surveillance_stream=row["surveillance_stream"] or None,
        unit=row.get("unit"),
    )


def source_ref(row: Row) -> s.SourceRef:
    return s.SourceRef(
        slug=row["source_slug"],
        name=row["source_name"],
        publisher=row["source_publisher"],
        license_name=row["license_name"] or None,
        terms_verified=row["terms_verified"],
    )


def observation(row: Row) -> s.ObservationOut:
    return s.ObservationOut(
        observation_id=str(row["observation_id"]),
        period_start=row["period_start"],
        period_end=row["period_end"],
        value=row["value"],
        unit=row["unit"],
        numerator=row["numerator"],
        denominator=row["denominator"],
        data_status=row["data_status"],
        quality_flags=_flags(row["quality_flags"]),
        published_at=row["published_at"],
        retrieved_at=row["retrieved_at"],
        source_url=row["source_url"],
        source=source_ref(row),
    )


def observation_item(row: Row) -> s.ObservationItem:
    return s.ObservationItem(**observation(row).model_dump(), series=series_ref(row))


def analysis(row: Row) -> s.AnalysisOut:
    return s.AnalysisOut(
        analysis_type=row["analysis_type"],
        status=row["status"],
        period_start=row["period_start"],
        period_end=row["period_end"],
        baseline_start=row["baseline_start"],
        baseline_end=row["baseline_end"],
        observed_value=row["observed_value"],
        baseline_value=row["baseline_value"],
        effect_size=row["effect_size"],
        score=row["score"],
        threshold=row["threshold"],
        uncertainty=_json(row["uncertainty"]),
        explanation=row["explanation"],
        limitations=row["limitations"],
        missing_data_handling=row["missing_data_handling"],
        methodology_version=row["methodology_version"],
        input_observation_count=row["input_count"],
        computed_at=row["computed_at"],
    )


def freshness(latest_period_end: date, today: date) -> s.Freshness:
    age = (today - latest_period_end).days
    return s.Freshness(
        latest_period_end=latest_period_end,
        data_age_days=age,
        stale_after_days=STALE_AFTER_DAYS,
        is_stale=age > STALE_AFTER_DAYS,
    )


def _analyses_by_series(rows: Iterable[Row]) -> dict[SeriesId, dict[str, Row]]:
    grouped: dict[SeriesId, dict[str, Row]] = defaultdict(dict)
    for r in rows:
        grouped[series_id(r)][r["analysis_type"]] = r
    return grouped


# --------------------------------------------------------------------------- endpoints
def build_geographies(rows: Iterable[Row], now: datetime) -> s.GeographiesResponse:
    rows = _normalise(rows)
    return s.GeographiesResponse(
        data=[
            s.GeographyOut(
                geography_id=str(r["geography_id"]),
                country_code=r["country_code"],
                country_name=r["country_name"],
                geographic_resolution=r["geographic_resolution"],
                resolution_note=resolution_note(r["geographic_resolution"], r["region_name"]),
                region_name=r["region_name"] or None,
                code_system=r["code_system"] or None,
                observation_count=r["observation_count"],
                first_period_start=r["first_period_start"],
                latest_period_end=r["latest_period_end"],
            )
            for r in rows
        ],
        meta=meta(now),
    )


def build_observations(
    rows: Sequence[Row], *, limit: int, offset: int, now: datetime
) -> s.ObservationsResponse:
    rows = _normalise(rows)
    total = int(rows[0]["total_count"]) if rows else 0
    return s.ObservationsResponse(
        data=[observation_item(r) for r in rows],
        page=s.Page(total=total, limit=limit, offset=offset),
        meta=meta(now),
    )


def build_latest(
    observation_rows: Iterable[Row], analysis_rows: Iterable[Row], *, today: date, now: datetime
) -> s.LatestResponse:
    analyses = _analyses_by_series(_normalise(analysis_rows))
    out: list[s.LatestSeries] = []
    for r in _normalise(observation_rows):
        by_type = analyses.get(series_id(r), {})
        screened = by_type.get("anomaly_detection")
        if screened is None:
            signal = "not_analysed"
        elif screened["period_end"] != r["period_end"]:
            signal = "analysis_outdated"  # the screened week is not the newest observed week
        else:
            signal = screened["status"]
        out.append(
            s.LatestSeries(
                series=series_ref(r),
                latest=observation(r),
                freshness=freshness(r["period_end"], today),
                signal=signal,
                analyses={t: analysis(a) for t, a in sorted(by_type.items())},
            )
        )
    return s.LatestResponse(data=out, meta=meta(now))


def build_trends(
    point_rows: Iterable[Row], analysis_rows: Iterable[Row], *, today: date, now: datetime
) -> s.TrendsResponse:
    analyses = _analyses_by_series(_normalise(analysis_rows))
    grouped: dict[SeriesId, list[Row]] = defaultdict(list)
    for r in _normalise(point_rows):
        grouped[series_id(r)].append(r)
    out: list[s.TrendSeries] = []
    for rows in grouped.values():
        rows.sort(key=lambda r: r["period_start"])
        by_type = analyses.get(series_id(rows[0]), {})
        out.append(
            s.TrendSeries(
                series=series_ref(rows[0]),
                points=[
                    s.TrendPoint(
                        period_start=r["period_start"],
                        period_end=r["period_end"],
                        value=r["value"],
                        data_status=r["data_status"],
                        quality_flags=_flags(r["quality_flags"]),
                    )
                    for r in rows
                ],
                trend=analysis(by_type["trend"]) if "trend" in by_type else None,
                rolling_average=(
                    analysis(by_type["rolling_average"]) if "rolling_average" in by_type else None
                ),
                freshness=freshness(rows[-1]["period_end"], today),
            )
        )
    return s.TrendsResponse(data=out, meta=meta(now))


def build_alerts(analysis_rows: Iterable[Row], *, today: date, now: datetime) -> s.AlertsResponse:
    """Flagged anomaly screenings whose underlying data are still fresh."""
    alerts: list[s.AlertOut] = []
    withheld = 0
    for r in _normalise(analysis_rows):
        if r["analysis_type"] != "anomaly_detection" or r["status"] not in FLAGGED:
            continue
        fresh = freshness(r["latest_observation_end"] or r["period_end"], today)
        if fresh.is_stale:
            withheld += 1
            continue
        alerts.append(
            s.AlertOut(
                series=series_ref(r),
                status=r["status"],
                analysis=analysis(r),
                freshness=fresh,
                disclaimer=ALERT_DISCLAIMER,
            )
        )
    alerts.sort(key=lambda a: (a.status != "possible_anomaly", -float(a.analysis.score or 0)))
    extra = (
        [
            f"{withheld} flagged series withheld because their data are older than "
            f"{STALE_AFTER_DAYS} days."
        ]
        if withheld
        else []
    )
    return s.AlertsResponse(data=alerts, meta=meta(now, extra))


def validate_comparison_request(indicator: str, countries: Sequence[str]) -> None:
    """Refuse comparisons that would mislead, before any data is read."""
    if indicator not in COMPARABLE_INDICATORS:
        raise ComparisonError(
            "indicator_not_comparable",
            f"'{indicator}' cannot be compared across countries: counts depend on population, "
            "testing volume, sampling and reporting practices. Compare a rate with an explicit "
            f"denominator instead ({', '.join(sorted(COMPARABLE_INDICATORS))}).",
        )
    if len(set(countries)) < 2:
        raise ComparisonError("too_few_countries", "Provide at least two different countries.")


def build_comparison(
    rows: Iterable[Row],
    *,
    indicator: str,
    stream: str | None,
    countries: Sequence[str],
    now: datetime,
) -> s.ComparisonResponse:
    validate_comparison_request(indicator, countries)
    rows = _normalise(rows)
    bad_types = {r["indicator_type"] for r in rows} - COMPARABLE_INDICATOR_TYPES
    if bad_types:
        raise ComparisonError(
            "indicator_type_not_comparable",
            f"Indicator type(s) not comparable: {sorted(bad_types)}.",
        )
    units = {r["unit"] for r in rows}
    if len(units) > 1:
        raise ComparisonError(
            "unit_mismatch", f"Series use different units: {sorted(map(str, units))}."
        )

    by_country: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        by_country[r["country_code"]].append(r)

    cells: dict[tuple[date, date], dict[str, s.ComparisonCell | None]] = {}
    for code, crows in by_country.items():
        for r in crows:
            week = (r["period_start"], r["period_end"])
            cells.setdefault(week, {})[code] = s.ComparisonCell(
                value=r["value"],
                numerator=r["numerator"],
                denominator=r["denominator"],
                data_status=r["data_status"],
                quality_flags=_flags(r["quality_flags"]),
            )

    ordered = sorted(set(countries))
    caveats = list(COMPARISON_CAVEATS)
    cs = [
        s.ComparisonCountry(
            country_code=c,
            country_name=by_country[c][0]["country_name"],
            geographic_resolution=by_country[c][0]["geographic_resolution"],
            resolution_note=resolution_note(by_country[c][0]["geographic_resolution"]),
            source=source_ref(by_country[c][0]),
            weeks_with_data=sum(1 for r in by_country[c] if r["value"] is not None),
            latest_period_end=max(r["period_end"] for r in by_country[c]),
        )
        for c in ordered
        if c in by_country
    ]
    if len({c.geographic_resolution for c in cs}) > 1:
        caveats.append("The countries are reported at different geographic resolutions.")
    if len({c.source.slug for c in cs if c.source}) > 1:
        caveats.append("The values come from different sources with different methods.")
    if any(
        "small_denominator" in cell.quality_flags
        for w in cells.values()
        for cell in w.values()
        if cell
    ):
        caveats.append("Some weeks rest on very few tested specimens (flag: small_denominator).")

    weeks = [
        s.ComparisonWeek(
            period_start=start,
            period_end=end,
            values={c: cells[(start, end)].get(c) for c in ordered},
        )
        for (start, end) in sorted(cells)
    ]
    return s.ComparisonResponse(
        indicator=indicator,
        unit=next(iter(units), None),
        surveillance_stream=stream,
        countries=cs,
        countries_without_data=[c for c in ordered if c not in by_country],
        weeks=weeks,
        caveats=caveats,
        meta=meta(now),
    )


def build_sources(rows: Iterable[Row], now: datetime) -> s.SourcesResponse:
    return s.SourcesResponse(data=[s.SourceOut(**dict(r)) for r in rows], meta=meta(now))


def build_source_status(rows: Iterable[Row], now: datetime) -> s.SourceStatusResponse:
    return s.SourceStatusResponse(data=[s.SourceStatusOut(**dict(r)) for r in rows], meta=meta(now))


def build_runs(rows: Iterable[Row], now: datetime) -> s.RunsResponse:
    return s.RunsResponse(data=[s.RunOut(**dict(r)) for r in rows], meta=meta(now))
