"""Turn flat database rows into SeriesKey -> [Point] groups (pure; no database access)."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from app.analysis.models import Point, SeriesKey


def _flags(raw: Any) -> tuple[str, ...]:
    if raw in (None, ""):
        return ()
    values = json.loads(raw) if isinstance(raw, str) else raw
    return tuple(str(v) for v in values or ())


def _date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _decimal(value: Any) -> Decimal | None:
    return None if value in (None, "") else Decimal(str(value))


def group_series(rows: Iterable[Mapping[str, Any]]) -> list[tuple[SeriesKey, list[Point]]]:
    """Group rows (any order) into series. Series are never merged across geography, disease,
    pathogen, indicator or surveillance stream; output order is deterministic."""
    groups: dict[SeriesKey, list[Point]] = {}
    for r in rows:
        key = SeriesKey(
            geography_id=str(r["geography_id"]),
            country_code=str(r["country_code"]),
            disease=str(r["disease"]),
            pathogen=r["pathogen"] or None,
            indicator=str(r["indicator"]),
            surveillance_stream=r["surveillance_stream"] or None,
            unit=r["unit"] or None,
        )
        groups.setdefault(key, []).append(
            Point(
                observation_id=str(r["observation_id"]),
                period_start=_date(r["period_start"]),
                period_end=_date(r["period_end"]),
                value=_decimal(r["value"]),
                data_status=str(r["data_status"]),
                flags=_flags(r["quality_flags"]),
            )
        )
    return sorted(
        ((k, sorted(v, key=lambda p: p.period_start)) for k, v in groups.items()),
        key=lambda kv: (
            kv[0].geography_id,
            kv[0].disease,
            kv[0].pathogen or "",
            kv[0].indicator,
            kv[0].surveillance_stream or "",
        ),
    )
