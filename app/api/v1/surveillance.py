"""Public, read-only surveillance endpoints (run as database role `anon`: RLS applies)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import public_conn
from app.api.v1.common import fetch
from app.core.errors import AppError
from app.surveillance import queries, service
from app.surveillance import schemas as s

router = APIRouter(prefix="/surveillance", tags=["surveillance"])

Conn = Annotated[AsyncConnection, Depends(public_conn)]
Country = Annotated[
    str | None,
    Query(pattern=r"^[A-Za-z]{2}$", description="ISO 3166-1 alpha-2 country code, e.g. PK"),
]
Identifier = Annotated[str | None, Query(pattern=r"^[a-z0-9_]{1,64}$")]
Stream = Annotated[
    Literal["sentinel", "nonsentinel", "undefined"] | None,
    Query(description="Sampling stream. Streams are separate series and are never merged."),
]
DateFrom = Annotated[date | None, Query(description="Include periods ending on/after this date")]
DateTo = Annotated[date | None, Query(description="Include periods starting on/before this date")]


def _series_params(
    country: str | None, disease: str | None, indicator: str | None, stream: str | None
) -> dict[str, Any]:
    return {
        "country": country.upper() if country else None,
        "disease": disease,
        "indicator": indicator,
        "stream": stream,
    }


def _now() -> datetime:
    return datetime.now(UTC)


@router.get("/geographies", response_model=s.GeographiesResponse, summary="Geographies with data")
async def geographies(conn: Conn) -> s.GeographiesResponse:
    """Every geography that has observations, with the resolution of the data it carries."""
    rows = await fetch(conn, queries.GEOGRAPHIES, {})
    return service.build_geographies(rows, _now())


@router.get("/observations", response_model=s.ObservationsResponse, summary="Stored observations")
async def observations(
    conn: Conn,
    country: Country = None,
    disease: Identifier = None,
    indicator: Identifier = None,
    stream: Stream = None,
    date_from: DateFrom = None,
    date_to: DateTo = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> s.ObservationsResponse:
    """Observations newest first, with source, retrieval time, data status and quality flags."""
    rows = await fetch(
        conn,
        queries.OBSERVATIONS,
        {
            **_series_params(country, disease, indicator, stream),
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
            "offset": offset,
        },
    )
    return service.build_observations(rows, limit=limit, offset=offset, now=_now())


@router.get(
    "/latest", response_model=s.LatestResponse, summary="Latest value and signal per series"
)
async def latest(
    conn: Conn,
    country: Country = None,
    disease: Identifier = None,
    indicator: Identifier = None,
    stream: Stream = None,
) -> s.LatestResponse:
    """The newest observation of each series with its freshness and latest statistical screening.

    `signal` is a statistical flag or a data-quality state. It never means a confirmed outbreak.
    """
    params = _series_params(country, disease, indicator, stream)
    observation_rows = await fetch(conn, queries.LATEST_OBSERVATIONS, params)
    analysis_rows = await fetch(conn, queries.LATEST_ANALYSES, params)
    now = _now()
    return service.build_latest(observation_rows, analysis_rows, today=now.date(), now=now)


@router.get(
    "/trends", response_model=s.TrendsResponse, summary="Series with trend and rolling mean"
)
async def trends(
    conn: Conn,
    country: Country = None,
    disease: Identifier = None,
    indicator: Identifier = None,
    stream: Stream = None,
    date_from: DateFrom = None,
    date_to: DateTo = None,
) -> s.TrendsResponse:
    """Chronological points per series plus the latest stored trend and rolling-average results.

    Blank weeks stay null; they are never drawn as zero.
    """
    params = _series_params(country, disease, indicator, stream)
    point_rows = await fetch(
        conn,
        queries.SERIES_POINTS,
        {**params, "date_from": date_from, "date_to": date_to, "limit": 5000},
    )
    analysis_rows = await fetch(conn, queries.LATEST_ANALYSES, params)
    now = _now()
    return service.build_trends(point_rows, analysis_rows, today=now.date(), now=now)


@router.get("/alerts", response_model=s.AlertsResponse, summary="Statistical flags (not outbreaks)")
async def alerts(
    conn: Conn,
    country: Country = None,
    disease: Identifier = None,
    indicator: Identifier = None,
    stream: Stream = None,
    status: Annotated[Literal["elevated", "possible_anomaly"] | None, Query()] = None,
) -> s.AlertsResponse:
    """Series whose latest screening is `elevated` or `possible_anomaly` and whose data are fresh.

    These are screening results from one indicator. They do not confirm an outbreak.
    """
    rows = await fetch(
        conn, queries.LATEST_ANALYSES, _series_params(country, disease, indicator, stream)
    )
    if status:
        rows = [r for r in rows if r["status"] == status]
    now = _now()
    return service.build_alerts(rows, today=now.date(), now=now)


@router.get("/comparisons", response_model=s.ComparisonResponse, summary="Side-by-side positivity")
async def comparisons(
    conn: Conn,
    countries: Annotated[
        str, Query(pattern=r"^[A-Za-z]{2}(,[A-Za-z]{2}){1,9}$", description="e.g. PK,US")
    ],
    indicator: Annotated[str, Query(pattern=r"^[a-z0-9_]{1,64}$")] = "influenza_positivity",
    stream: Annotated[Literal["sentinel", "nonsentinel", "undefined"], Query()] = "sentinel",
    date_from: DateFrom = None,
    date_to: DateTo = None,
) -> s.ComparisonResponse:
    """Rate indicators with explicit denominators, shown side by side for one sampling stream.

    No difference, ranking or score between countries is computed. Counts are refused.
    """
    codes = sorted({c.upper() for c in countries.split(",")})
    try:
        service.validate_comparison_request(indicator, codes)  # before touching the database
    except service.ComparisonError as exc:
        raise AppError(422, exc.code, str(exc)) from exc
    rows = await fetch(
        conn,
        queries.COMPARISON_POINTS,
        {
            "indicator": indicator,
            "stream": stream,
            "countries": json.dumps(codes),
            "date_from": date_from,
            "date_to": date_to,
            "limit": 5000,
        },
    )
    try:
        return service.build_comparison(
            rows, indicator=indicator, stream=stream, countries=codes, now=_now()
        )
    except service.ComparisonError as exc:
        raise AppError(422, exc.code, str(exc)) from exc
