"""PostgreSQL implementation of IngestionRepository (SQLAlchemy Core + asyncpg)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.db import Database
from app.ingestion import sql
from app.ingestion.countries import SUPPORTED_COUNTRIES
from app.ingestion.errors import AlreadyRunningError, IngestionError, PersistenceError
from app.ingestion.models import ObservationRecord, PersistOutcome
from app.ingestion.ports import PreviousRun, RunResult, SourceRecord
from app.ingestion.serialization import observation_to_json

log = logging.getLogger(__name__)

BATCH_SIZE = 2000  # rows per statement: bounds parameter size and statement time
_ONE_RUNNING_INDEX = "ingestion_runs_one_running_per_source"
_ISO2_TO_COUNTRY = {c.iso2: c for c in SUPPORTED_COUNTRIES.values()}


class SqlIngestionRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_source(self, slug: str) -> SourceRecord:
        async with self._db.session(read_only=True) as conn:
            row = (await conn.execute(text(sql.GET_SOURCE), {"slug": slug})).mappings().first()
        if row is None:
            raise IngestionError(
                f"Source {slug!r} is not registered in data_sources (are migrations applied?)."
            )
        return SourceRecord(row["id"], row["slug"], row["name"], row["enabled"])

    async def fail_stale_runs(self, source_id: UUID, older_than: timedelta) -> int:
        async with self._db.session() as conn:
            result = await conn.execute(
                text(sql.FAIL_STALE_RUNS),
                {"source_id": str(source_id), "max_age_seconds": older_than.total_seconds()},
            )
            return result.rowcount

    async def start_run(
        self, source_id: UUID, *, triggered_by: str, dry_run: bool, metadata: dict[str, object]
    ) -> UUID:
        try:
            async with self._db.session() as conn:
                run_id = (
                    await conn.execute(
                        text(sql.START_RUN),
                        {
                            "source_id": str(source_id),
                            "triggered_by": triggered_by,
                            "dry_run": dry_run,
                            "metadata": json.dumps(metadata, default=str),
                        },
                    )
                ).scalar_one()
        except IntegrityError as exc:
            if _ONE_RUNNING_INDEX in str(exc.orig):
                raise AlreadyRunningError(
                    f"A run for source {source_id} is already active."
                ) from exc
            raise
        return run_id

    async def last_data_run(self, source_id: UUID) -> PreviousRun | None:
        async with self._db.session(read_only=True) as conn:
            row = (
                (await conn.execute(text(sql.LAST_DATA_RUN), {"source_id": str(source_id)}))
                .mappings()
                .first()
            )
        return PreviousRun(row["status"], row["checksum"], row["signature"]) if row else None

    async def persist_observations(
        self,
        source_id: UUID,
        run_id: UUID,
        *,
        checksum: str,
        retrieved_at: datetime,
        observations: list[ObservationRecord],
        dry_run: bool,
    ) -> PersistOutcome:
        inserted = existing = 0
        try:
            # One transaction for everything: all rows are written or none are. In dry-run mode
            # the transaction is rolled back at the end, so counts are real but nothing persists.
            async with self._db.session(rollback=dry_run) as conn:
                await self._ensure_geographies(conn, observations)
                for start in range(0, len(observations), BATCH_SIZE):
                    batch = observations[start : start + BATCH_SIZE]
                    row = (
                        await conn.execute(
                            text(sql.UPSERT_OBSERVATIONS),
                            {
                                "rows": json.dumps([observation_to_json(o) for o in batch]),
                                "source_id": str(source_id),
                                "run_id": str(run_id),
                                "retrieved_at": retrieved_at,
                                "checksum": checksum,
                            },
                        )
                    ).one()
                    if row.inserted + row.existing != len(batch):
                        raise PersistenceError(
                            f"Batch wrote {row.inserted + row.existing} of {len(batch)} rows "
                            "(a country geography could not be resolved)."
                        )
                    inserted += row.inserted
                    existing += row.existing
                revised = (
                    await conn.execute(text(sql.COUNT_REVISIONS_FOR_RUN), {"run_id": str(run_id)})
                ).scalar_one()
        except PersistenceError:
            raise
        except SQLAlchemyError as exc:
            log.exception("persist_failed")
            raise PersistenceError(f"Database write failed ({type(exc).__name__}).") from exc

        if revised > existing:
            raise PersistenceError("Revision count exceeds updated rows; refusing to continue.")
        return PersistOutcome(inserted=inserted, updated=revised, unchanged=existing - revised)

    async def _ensure_geographies(self, conn: Any, observations: list[ObservationRecord]) -> None:
        for code in sorted({o.country_code for o in observations}):
            country = _ISO2_TO_COUNTRY.get(code)
            if country is None:
                raise PersistenceError(f"No supported country for code {code!r}.")
            await conn.execute(
                text(sql.ENSURE_COUNTRY_GEOGRAPHY),
                {"country_code": country.iso2, "country_name": country.name},
            )

    async def finish_run(self, run_id: UUID, result: RunResult) -> None:
        async with self._db.session() as conn:
            outcome = await conn.execute(
                text(sql.FINISH_RUN),
                {
                    "run_id": str(run_id),
                    "status": result.status.value,
                    "records_downloaded": result.records_downloaded,
                    "records_inserted": result.records_inserted,
                    "records_updated": result.records_updated,
                    "records_rejected": result.records_rejected,
                    "checksum": result.checksum,
                    "source_published_at": result.source_published_at,
                    "error_summary": result.error_summary,
                    "metadata": json.dumps(result.metadata, default=str),
                },
            )
            if outcome.rowcount != 1:
                raise PersistenceError(f"Run {run_id} was not in 'running' state when finishing.")
