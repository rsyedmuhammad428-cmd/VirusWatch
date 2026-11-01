"""PostgreSQL implementation of AnalysisRepository (SQLAlchemy Core + asyncpg)."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.analysis import sql
from app.analysis.errors import AnalysisPersistenceError
from app.analysis.loading import group_series
from app.analysis.models import AnalysisResult, Point, SeriesKey
from app.analysis.ports import StoreOutcome
from app.analysis.serialization import result_to_json
from app.db import Database

log = logging.getLogger(__name__)

BATCH_SIZE = 500  # results per statement


class SqlAnalysisRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def load_series(self, indicators: Sequence[str]) -> list[tuple[SeriesKey, list[Point]]]:
        try:
            async with self._db.session(read_only=True) as conn:
                rows = (
                    (
                        await conn.execute(
                            text(sql.LOAD_POINTS), {"indicators": json.dumps(sorted(indicators))}
                        )
                    )
                    .mappings()
                    .all()
                )
        except SQLAlchemyError as exc:
            log.exception("analysis_load_failed")
            raise AnalysisPersistenceError(
                f"Could not read observations ({type(exc).__name__})."
            ) from exc
        return group_series(rows)

    async def store_results(self, results: Sequence[AnalysisResult]) -> StoreOutcome:
        submitted = inserted = 0
        try:
            async with self._db.session() as conn:  # one transaction: all results or none
                for start in range(0, len(results), BATCH_SIZE):
                    batch = results[start : start + BATCH_SIZE]
                    row = (
                        await conn.execute(
                            text(sql.INSERT_RESULTS),
                            {"rows": json.dumps([result_to_json(r) for r in batch])},
                        )
                    ).one()
                    if row.submitted != len(batch):
                        raise AnalysisPersistenceError(
                            f"Statement saw {row.submitted} of {len(batch)} results."
                        )
                    submitted += row.submitted
                    inserted += row.inserted
        except AnalysisPersistenceError:
            raise
        except SQLAlchemyError as exc:
            log.exception("analysis_store_failed")
            raise AnalysisPersistenceError(
                f"Could not store analysis results ({type(exc).__name__})."
            ) from exc
        return StoreOutcome(submitted=submitted, inserted=inserted)
