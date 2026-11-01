"""In-memory test doubles for the HTTP fetcher and the repository port."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID, uuid4

from app.ingestion.errors import AlreadyRunningError, IngestionError
from app.ingestion.http import HttpResult
from app.ingestion.models import ObservationRecord, PersistOutcome
from app.ingestion.ports import PreviousRun, RunResult, SourceRecord


class FakeFetcher:
    """Plays back a script of HttpResult / exceptions, one per call."""

    def __init__(self, *script: HttpResult | BaseException) -> None:
        self._script = list(script)
        self.calls: list[dict[str, object]] = []

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None,
        max_bytes: int,
        timeout_seconds: float,
    ) -> HttpResult:
        self.calls.append({"url": url, "params": dict(params or {}), "max_bytes": max_bytes})
        item = self._script.pop(0) if len(self._script) > 1 else self._script[0]
        if isinstance(item, BaseException):
            raise item
        return item


def ok(body: bytes, **headers: str) -> HttpResult:
    return HttpResult(200, {"content-type": "text/csv", **headers}, body, "https://example.test/x")


def status(code: int, **headers: str) -> HttpResult:
    return HttpResult(code, headers, b"", "https://example.test/x")


@dataclass
class _Run:
    id: UUID
    dry_run: bool
    metadata: dict[str, object]
    status: str = "running"
    result: RunResult | None = None


@dataclass
class FakeRepository:
    """Mimics the SQL repository's observable behaviour, including its uniqueness rules."""

    enabled: bool = True
    persist_error: BaseException | None = None
    stale_to_report: int = 0
    source: SourceRecord = field(
        default_factory=lambda: SourceRecord(uuid4(), "who_flunet", "WHO FluNet", True)
    )
    runs: list[_Run] = field(default_factory=list)
    store: dict[tuple[object, ...], ObservationRecord] = field(default_factory=dict)
    stale_calls: list[timedelta] = field(default_factory=list)
    persist_calls: list[dict[str, object]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.source = SourceRecord(self.source.id, self.source.slug, self.source.name, self.enabled)

    async def get_source(self, slug: str) -> SourceRecord:
        if slug != self.source.slug:
            raise IngestionError(f"Source {slug!r} is not registered in data_sources.")
        return self.source

    async def fail_stale_runs(self, source_id: UUID, older_than: timedelta) -> int:
        self.stale_calls.append(older_than)
        return self.stale_to_report

    async def start_run(
        self, source_id: UUID, *, triggered_by: str, dry_run: bool, metadata: dict[str, object]
    ) -> UUID:
        # Same rule as the partial unique index: one live NON-dry-run run per source.
        if not dry_run and any(r.status == "running" and not r.dry_run for r in self.runs):
            raise AlreadyRunningError("already running")
        run = _Run(uuid4(), dry_run, dict(metadata))
        self.runs.append(run)
        return run.id

    async def last_data_run(self, source_id: UUID) -> PreviousRun | None:
        for run in reversed(self.runs):
            if run.status in ("succeeded", "partial") and not run.dry_run and run.result:
                return PreviousRun(
                    run.status, run.result.checksum, str(run.metadata.get("signature"))
                )
        return None

    async def persist_observations(
        self,
        source_id: UUID,
        run_id: UUID,
        *,
        checksum: str,
        retrieved_at: object,
        observations: list[ObservationRecord],
        dry_run: bool,
    ) -> PersistOutcome:
        self.persist_calls.append(
            {"run_id": run_id, "n": len(observations), "dry_run": dry_run, "checksum": checksum}
        )
        if self.persist_error is not None:
            raise self.persist_error
        inserted = updated = unchanged = 0
        staged: dict[tuple[object, ...], ObservationRecord] = {}
        for o in observations:
            previous = self.store.get(o.natural_key)
            if previous is None:
                inserted += 1
            elif _values(previous) != _values(o):
                updated += 1
            else:
                unchanged += 1
            staged[o.natural_key] = o
        if not dry_run:  # a dry run is rolled back: counts are real, state is untouched
            self.store.update(staged)
        return PersistOutcome(inserted, updated, unchanged)

    async def finish_run(self, run_id: UUID, result: RunResult) -> None:
        run = next(r for r in self.runs if r.id == run_id)
        assert run.status == "running", "finish_run on a run that is not running"
        run.status, run.result = result.status.value, result
        run.metadata.update(result.metadata)

    # convenience for assertions
    def only_run(self) -> _Run:
        assert len(self.runs) == 1
        return self.runs[0]


def _values(o: ObservationRecord) -> tuple[object, ...]:
    return (o.value_numeric, o.numerator, o.denominator, o.unit)
