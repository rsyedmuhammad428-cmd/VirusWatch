"""Connector-independent ingestion orchestration.

Status rules (spec: "Do not mark a job successful if parsing or persistence fails"):

  succeeded          download + parse + persist all worked and no source row was rejected
  partial            persisted, but some source rows were rejected (error_summary says which)
  failed             anything else: fetch/validation/schema error, no usable rows, too many
                     rejections, or a database error. Nothing is persisted in these cases.
  skipped_unchanged  same bytes and same parser/query as the last data-bearing run (unless forced)

Dry-run executes everything, including the real upsert statements, inside a transaction that is
rolled back, so the reported insert/update counts are exact but the database is unchanged.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, timedelta
from pathlib import Path
from uuid import UUID

from app.ingestion.connectors.base import SourceConnector
from app.ingestion.errors import (
    AlreadyRunningError,
    IngestionError,
    TooManyRejectionsError,
)
from app.ingestion.http import HttpFetcher
from app.ingestion.models import (
    IngestRequest,
    ParseResult,
    PersistOutcome,
    RunStatus,
    RunSummary,
    Snapshot,
)
from app.ingestion.ports import IngestionRepository, RunResult, SourceRecord

log = logging.getLogger(__name__)

MAX_ERROR_SUMMARY = 500
SAMPLE_REJECTIONS = 20


@dataclass(frozen=True)
class RunnerConfig:
    max_reject_fraction: float = 0.2  # above this share of rejected in-scope rows, abort
    stale_after: timedelta = timedelta(hours=6)
    raw_dir: Path | None = None  # if set, the downloaded file is archived here

    def __post_init__(self) -> None:
        if not 0 <= self.max_reject_fraction <= 1:
            raise ValueError("max_reject_fraction must be within [0, 1]")


@dataclass
class _State:
    """Progress so far, so a failure can still record what was learned (e.g. the checksum)."""

    snapshot: Snapshot | None = None
    parsed: ParseResult | None = None
    outcome: PersistOutcome | None = None
    unchanged_snapshot: bool = False
    extra_metadata: dict[str, object] = field(default_factory=dict)


def safe_error_summary(exc: BaseException) -> str:
    """Domain errors carry safe messages. Anything else may embed SQL, parameters or paths, so
    only the exception type is stored (the full traceback goes to the application log)."""
    text = str(exc) if isinstance(exc, IngestionError) else f"Unexpected {type(exc).__name__}"
    return text[:MAX_ERROR_SUMMARY]


async def run_ingestion(
    connector: SourceConnector,
    repo: IngestionRepository,
    fetcher: HttpFetcher,
    request: IngestRequest,
    config: RunnerConfig = RunnerConfig(),  # noqa: B008 (frozen dataclass)
) -> RunSummary:
    source = await repo.get_source(connector.slug)
    if request.triggered_by == "scheduled" and not source.enabled:
        log.info("source_disabled_skip", extra={"source": source.slug})
        return RunSummary(RunStatus.SKIPPED_DISABLED, None, source.slug, request.dry_run)

    await _recover_stale_runs(repo, source, config)

    signature = connector.signature(request)
    start_metadata: dict[str, object] = {
        "signature": signature,
        "parser_version": connector.parser_version,
        "countries": sorted(request.countries),
        "since_year": request.since_year,
        "counters_note": (
            "records_downloaded/records_rejected count source rows; "
            "records_inserted/records_updated count observations."
        ),
    }
    try:
        run_id = await repo.start_run(
            source.id,
            triggered_by=request.triggered_by,
            dry_run=request.dry_run,
            metadata=start_metadata,
        )
    except AlreadyRunningError:
        log.warning("run_already_active", extra={"source": source.slug})
        return RunSummary(RunStatus.SKIPPED_ALREADY_RUNNING, None, source.slug, request.dry_run)

    state = _State()
    error: str | None = None
    try:
        await _execute(connector, repo, fetcher, request, config, source, run_id, signature, state)
    except asyncio.CancelledError:
        raise  # the stale-run reaper will close the row; never swallow cancellation
    except Exception as exc:
        log.exception("ingestion_failed", extra={"source": source.slug, "run_id": str(run_id)})
        error = safe_error_summary(exc)

    result = _build_result(state, error)
    await repo.finish_run(run_id, result)
    summary = _build_summary(source.slug, run_id, request, state, result)
    log.info(
        "ingestion_finished",
        extra={
            "source": source.slug,
            "run_id": str(run_id),
            "status": summary.status.value,
            "dry_run": request.dry_run,
            "rows_read": summary.rows_read,
            "rows_rejected": summary.rows_rejected,
            "inserted": summary.observations_inserted,
            "updated": summary.observations_updated,
        },
    )
    return summary


async def _recover_stale_runs(
    repo: IngestionRepository, source: SourceRecord, config: RunnerConfig
) -> None:
    reaped = await repo.fail_stale_runs(source.id, config.stale_after)
    if reaped:
        log.warning("stale_runs_failed", extra={"source": source.slug, "count": reaped})


async def _execute(
    connector: SourceConnector,
    repo: IngestionRepository,
    fetcher: HttpFetcher,
    request: IngestRequest,
    config: RunnerConfig,
    source: SourceRecord,
    run_id: UUID,
    signature: str,
    state: _State,
) -> None:
    state.snapshot = await connector.fetch(request, fetcher)
    snapshot = state.snapshot

    if config.raw_dir is not None:
        state.extra_metadata["raw_archive"] = await asyncio.to_thread(
            _archive_raw, config.raw_dir, connector.slug, snapshot
        )

    if not request.force and not request.dry_run:
        previous = await repo.last_data_run(source.id)
        if previous and previous.checksum == snapshot.checksum and previous.signature == signature:
            state.unchanged_snapshot = True
            return

    # Parsing can take seconds for a full backfill; keep the event loop responsive.
    state.parsed = await asyncio.to_thread(connector.parse, snapshot, request)
    parsed = state.parsed
    _enforce_quality_gates(parsed, config)

    state.outcome = await repo.persist_observations(
        source.id,
        run_id,
        checksum=snapshot.checksum,
        retrieved_at=snapshot.retrieved_at,
        observations=parsed.observations,
        dry_run=request.dry_run,
    )


def _enforce_quality_gates(parsed: ParseResult, config: RunnerConfig) -> None:
    if parsed.rows_in_scope == 0 or not parsed.observations:
        raise IngestionError(
            "The source returned no usable rows for the requested countries/years "
            f"({parsed.rows_read} rows read, {parsed.rows_rejected} rejected). "
            "Refusing to report success."
        )
    fraction = parsed.rows_rejected / parsed.rows_in_scope
    if fraction > config.max_reject_fraction:
        raise TooManyRejectionsError(
            f"{parsed.rows_rejected} of {parsed.rows_in_scope} in-scope rows were rejected "
            f"({fraction:.0%} > allowed {config.max_reject_fraction:.0%}); reasons: "
            f"{parsed.reject_reason_counts()}. Nothing was persisted."
        )


def _build_result(state: _State, error: str | None) -> RunResult:
    snapshot, parsed, outcome = state.snapshot, state.parsed, state.outcome
    metadata: dict[str, object] = dict(state.extra_metadata)

    if parsed is not None:
        metadata.update(
            columns_seen=len(parsed.columns),
            rows_out_of_scope=parsed.rows_out_of_scope,
            duplicate_rows_identical=parsed.rows_duplicate_identical,
            observations_generated=len(parsed.observations),
            reject_reasons=parsed.reject_reason_counts(),
            sample_rejections=[
                {"row": r.row_number, "reason": r.reason, "detail": r.detail}
                for r in parsed.rejections[:SAMPLE_REJECTIONS]
            ],
            warnings=parsed.warnings[:SAMPLE_REJECTIONS],
            latest_period_end=parsed.latest_period_end.isoformat()
            if parsed.latest_period_end
            else None,
        )
    if outcome is not None:
        metadata["observations_unchanged"] = outcome.unchanged

    if error is not None:
        status, summary = RunStatus.FAILED, error
    elif state.unchanged_snapshot:
        status, summary = RunStatus.SKIPPED_UNCHANGED, None
    elif parsed is not None and parsed.rows_rejected:
        status = RunStatus.PARTIAL
        summary = (
            f"{parsed.rows_rejected} of {parsed.rows_in_scope} source rows rejected: "
            f"{parsed.reject_reason_counts()}"
        )[:MAX_ERROR_SUMMARY]
    else:
        status, summary = RunStatus.SUCCEEDED, None

    return RunResult(
        status=status,
        records_downloaded=parsed.rows_read if parsed else 0,
        records_inserted=outcome.inserted if outcome else 0,
        records_updated=outcome.updated if outcome else 0,
        records_rejected=parsed.rows_rejected if parsed else 0,
        checksum=snapshot.checksum if snapshot else None,
        source_published_at=snapshot.source_published_at if snapshot else None,
        error_summary=summary,
        metadata=metadata,
    )


def _build_summary(
    slug: str, run_id: UUID, request: IngestRequest, state: _State, result: RunResult
) -> RunSummary:
    parsed, outcome = state.parsed, state.outcome
    return RunSummary(
        status=result.status,
        run_id=run_id,
        source_slug=slug,
        dry_run=request.dry_run,
        rows_read=result.records_downloaded,
        rows_rejected=result.records_rejected,
        rows_out_of_scope=parsed.rows_out_of_scope if parsed else 0,
        observations_inserted=outcome.inserted if outcome else 0,
        observations_updated=outcome.updated if outcome else 0,
        observations_unchanged=outcome.unchanged if outcome else 0,
        checksum=result.checksum,
        latest_period_end=parsed.latest_period_end if parsed else None,
        error=result.error_summary if result.status is RunStatus.FAILED else None,
        reject_reasons=parsed.reject_reason_counts() if parsed else {},
    )


def _archive_raw(raw_dir: Path, slug: str, snapshot: Snapshot) -> str:
    """Keep the exact downloaded bytes plus a provenance sidecar. Returns the relative path."""
    digest = snapshot.checksum.removeprefix("sha256:")
    stamp = snapshot.retrieved_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    directory = raw_dir / slug
    directory.mkdir(parents=True, exist_ok=True)
    data_path = directory / f"{stamp}_{digest[:12]}.csv.gz"
    data_path.write_bytes(gzip.compress(snapshot.content))
    sidecar = {
        "url": snapshot.url,
        "checksum": snapshot.checksum,
        "retrieved_at": snapshot.retrieved_at.isoformat(),
        "content_type": snapshot.content_type,
        "bytes": len(snapshot.content),
    }
    data_path.with_suffix(".json").write_text(json.dumps(sidecar, indent=2))
    return f"{slug}/{data_path.name}"
