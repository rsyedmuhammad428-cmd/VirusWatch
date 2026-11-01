"""Helpers shared by the database tests: run bookkeeping, FluNet fixtures, observation lookups."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.ingestion import sql
from app.ingestion.connectors.flunet_parser import parse_flunet_csv
from app.ingestion.models import ObservationRecord
from app.ingestion.serialization import observation_to_json
from tests.ingestion.flunet_helpers import csv_bytes

RETRIEVED = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
URL = "https://example.test/flunet?$format=csv"


def source_id(pg) -> str:
    return pg.scalar("select id from public.data_sources where slug = 'who_flunet'")


def start_run(pg, *, dry_run=False, metadata=None) -> str:
    rows = pg.execute(
        sql.START_RUN,
        {
            "source_id": source_id(pg),
            "triggered_by": "test",
            "dry_run": dry_run,
            "metadata": metadata or {},
        },
    )
    return rows[0]["id"]


def finish(pg, run_id, status="succeeded", **overrides):
    params = {
        "run_id": run_id,
        "status": status,
        "records_downloaded": 10,
        "records_inserted": 7,
        "records_updated": 1,
        "records_rejected": 0,
        "checksum": "sha256:abc",
        "source_published_at": None,
        "error_summary": None,
        "metadata": {"extra": True},
    }
    pg.execute(sql.FINISH_RUN, {**params, **overrides})


def parse(*lines, countries=frozenset({"PAK"}), **kw):
    result = parse_flunet_csv(
        csv_bytes(*lines), countries=countries, since_year=None, source_url=URL
    )
    assert not result.rejections, result.rejections
    return result.observations


def upsert(pg, observations: list[ObservationRecord], run_id: str, checksum="sha256:aaa"):
    params = {
        "rows": json.dumps([observation_to_json(o) for o in observations]),
        "source_id": source_id(pg),
        "run_id": run_id,
        "retrieved_at": RETRIEVED,
        "checksum": checksum,
    }
    r = pg.execute(sql.UPSERT_OBSERVATIONS, params)[0]
    return int(r["inserted"]), int(r["existing"])


def revised_count(pg, run_id) -> int:
    return int(pg.scalar(sql.COUNT_REVISIONS_FOR_RUN, {"run_id": run_id}))


def obs_rows(pg, where="true"):
    return pg.execute(
        "select indicator, pathogen, surveillance_stream, reporting_period_start::text as start, "
        "value_numeric::text as value, numerator::text as numerator, "
        "denominator::text as denominator, "
        f"unit, data_status, quality_flags::text as flags from public.observations where {where} "
        "order by reporting_period_start, surveillance_stream, indicator, pathogen"
    )


def one(pg, indicator, pathogen=None, stream="sentinel", week=None, country=None):
    p = "pathogen is null" if pathogen is None else f"pathogen = '{pathogen}'"
    w = "" if week is None else f" and reporting_period_start = '{week}'"
    if country is not None:
        w += (
            " and geography_id = (select id from public.geographies "
            f"where country_code = '{country}')"
        )
    rows = obs_rows(
        pg, f"indicator = '{indicator}' and {p} and surveillance_stream = '{stream}'{w}"
    )
    assert len(rows) == 1, rows
    return rows[0]
