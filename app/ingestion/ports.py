"""The persistence interface the runner depends on (implemented by sql_repository)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from app.ingestion.models import ObservationRecord, PersistOutcome, RunStatus


@dataclass(frozen=True)
class SourceRecord:
    id: UUID
    slug: str
    name: str
    enabled: bool


@dataclass(frozen=True)
class PreviousRun:
    status: str
    checksum: str | None
    signature: str | None


@dataclass(frozen=True)
class RunResult:
    """What gets written to the ingestion_runs row when a run ends."""

    status: RunStatus
    records_downloaded: int
    records_inserted: int
    records_updated: int
    records_rejected: int
    checksum: str | None
    source_published_at: datetime | None
    error_summary: str | None
    metadata: dict[str, object]


class IngestionRepository(Protocol):
    async def get_source(self, slug: str) -> SourceRecord:
        """Raise IngestionError if the source is not registered in data_sources."""
        ...

    async def fail_stale_runs(self, source_id: UUID, older_than: timedelta) -> int:
        """Mark 'running' rows older than the limit as failed (crashed process). Returns count."""
        ...

    async def start_run(
        self, source_id: UUID, *, triggered_by: str, dry_run: bool, metadata: dict[str, object]
    ) -> UUID:
        """Insert a 'running' row. Raise AlreadyRunningError if a live run already exists."""
        ...

    async def last_data_run(self, source_id: UUID) -> PreviousRun | None:
        """Most recent non-dry-run run that succeeded or partially succeeded."""
        ...

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
        """Atomically upsert all observations (one transaction; rolled back when dry_run).

        Must raise PersistenceError on any failure, leaving nothing written."""
        ...

    async def finish_run(self, run_id: UUID, result: RunResult) -> None: ...
