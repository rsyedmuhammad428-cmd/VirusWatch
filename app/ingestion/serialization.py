"""JSON encoding of observations for the bulk upsert (pure; no database driver)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.ingestion.models import ObservationRecord


def _num(value: Decimal | None) -> str | None:
    # Strings keep full decimal precision through JSON; Postgres parses them as numeric.
    return None if value is None else format(value, "f")


def observation_to_json(o: ObservationRecord) -> dict[str, Any]:
    """Shape consumed by sql.UPSERT_OBSERVATIONS (keys match its jsonb_to_recordset columns)."""
    return {
        "country_code": o.country_code,
        "disease": o.disease,
        "pathogen": o.pathogen,
        "indicator": o.indicator,
        "indicator_type": o.indicator_type,
        "surveillance_stream": o.surveillance_stream,
        "reporting_period_start": o.reporting_period_start.isoformat(),
        "reporting_period_end": o.reporting_period_end.isoformat(),
        "value_numeric": _num(o.value_numeric),
        "numerator": _num(o.numerator),
        "denominator": _num(o.denominator),
        "unit": o.unit,
        "data_status": o.data_status,
        "quality_flags": list(o.quality_flags),
        "source_record_id": o.source_record_id,
        "source_url": o.source_url,
        "metadata_json": o.metadata,
    }
