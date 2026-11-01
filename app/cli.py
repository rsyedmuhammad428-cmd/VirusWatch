"""Command line entry point:  python -m app.cli <command>

    python -m app.cli sources
    python -m app.cli ingest --source who_flunet --dry-run
    python -m app.cli ingest --source who_flunet --countries PAK,USA --since-year 2015
    python -m app.cli ingest --source who_flunet --backfill
    python -m app.cli analyze [--as-of 2026-10-30] [--dry-run] [--json]

Exit codes: 0 ok / skipped, 1 failed, 2 partial (some rows rejected), 3 another run is active.
`analyze` exits 0 on success even when it flags series: a statistical flag is not a failure.
Heavy imports are deferred so `--help` and argument handling work without a configured database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import date
from pathlib import Path

from app.analysis.errors import AnalysisPersistenceError
from app.ingestion.errors import IngestionError
from app.ingestion.models import IngestRequest, RunStatus, RunSummary

EXIT_OK, EXIT_FAILED, EXIT_PARTIAL, EXIT_ACTIVE = 0, 1, 2, 3
EXIT_USAGE = 64
DEFAULT_LOOKBACK_YEARS = 1  # incremental runs: current ISO year and the one before


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="VirusWatch operations")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sources", help="List registered sources and whether a connector exists")

    ingest = sub.add_parser("ingest", help="Download, validate and store one source")
    ingest.add_argument("--source", required=True, help="data_sources.slug, e.g. who_flunet")
    ingest.add_argument("--countries", help="ISO3 codes, comma separated (default: from settings)")
    ingest.add_argument("--since-year", type=int, help="Only ISO years >= this value")
    ingest.add_argument("--backfill", action="store_true", help="All history (unless --since-year)")
    ingest.add_argument("--dry-run", action="store_true", help="Run everything, then roll back")
    ingest.add_argument("--force", action="store_true", help="Re-ingest an unchanged snapshot")
    ingest.add_argument(
        "--scheduled", action="store_true", help="Mark as scheduled (honours enabled)"
    )
    ingest.add_argument("--raw-dir", type=Path, help="Archive the downloaded file here")
    ingest.add_argument("--json", action="store_true", help="Machine-readable summary")

    sub.add_parser("notify", help="Run one email cycle: queue due digests/alerts, send, record")

    analyze = sub.add_parser("analyze", help="Run the statistical analysis over stored series")
    analyze.add_argument(
        "--as-of", type=date.fromisoformat, help="Date used for freshness (default: today, UTC)"
    )
    analyze.add_argument("--dry-run", action="store_true", help="Compute and report, store nothing")
    analyze.add_argument("--json", action="store_true", help="Machine-readable summary")
    return parser


def build_request(
    args: argparse.Namespace, *, default_countries: str, today: date | None = None
) -> IngestRequest:
    from app.ingestion.countries import resolve_countries

    today = today or date.today()
    countries = resolve_countries((args.countries or default_countries).split(","))
    since_year = args.since_year
    if since_year is None and not args.backfill:
        since_year = today.isocalendar().year - DEFAULT_LOOKBACK_YEARS
    if args.backfill and args.scheduled:
        raise IngestionError("--backfill and --scheduled cannot be combined.")
    return IngestRequest(
        countries=countries,
        since_year=since_year,
        dry_run=args.dry_run,
        force=args.force,
        triggered_by="backfill" if args.backfill else "scheduled" if args.scheduled else "manual",
    )


def _require_db_url(settings) -> str:
    db_url = settings.supabase_db_url.get_secret_value()
    if not db_url:
        raise IngestionError("SUPABASE_DB_URL is not set.")
    return db_url


def exit_code(status: RunStatus) -> int:
    return {
        RunStatus.SUCCEEDED: EXIT_OK,
        RunStatus.SKIPPED_UNCHANGED: EXIT_OK,
        RunStatus.SKIPPED_DISABLED: EXIT_OK,
        RunStatus.PARTIAL: EXIT_PARTIAL,
        RunStatus.FAILED: EXIT_FAILED,
        RunStatus.SKIPPED_ALREADY_RUNNING: EXIT_ACTIVE,
    }[status]


def format_summary(s: RunSummary) -> str:
    lines = [
        f"source        {s.source_slug}{'  (DRY RUN: nothing was saved)' if s.dry_run else ''}",
        f"status        {s.status.value}",
    ]
    if s.run_id:
        lines.append(f"run id        {s.run_id}")
    if s.checksum:
        lines.append(f"snapshot      {s.checksum}")
    if s.rows_read:
        lines += [
            f"source rows   {s.rows_read} read, {s.rows_out_of_scope} out of scope, "
            f"{s.rows_rejected} rejected",
            f"observations  {s.observations_inserted} new, {s.observations_updated} revised, "
            f"{s.observations_unchanged} unchanged",
        ]
    if s.latest_period_end:
        lines.append(f"latest week   ends {s.latest_period_end.isoformat()}")
    if s.reject_reasons:
        lines.append(f"rejections    {s.reject_reasons}")
    if s.error:
        lines.append(f"error         {s.error}")
    return "\n".join(lines)


async def _ingest(args: argparse.Namespace) -> int:
    from app.config import get_settings
    from app.core.logging_config import configure_logging
    from app.db import Database
    from app.ingestion.connectors import get_connector
    from app.ingestion.http import RetryPolicy
    from app.ingestion.httpx_fetcher import HttpxFetcher
    from app.ingestion.runner import RunnerConfig, run_ingestion
    from app.ingestion.sql_repository import SqlIngestionRepository

    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.environment != "development")
    db_url = _require_db_url(settings)

    request = build_request(args, default_countries=settings.ingestion_default_countries)
    connector = get_connector(
        args.source,
        retry_policy=RetryPolicy(max_attempts=settings.ingestion_max_attempts),
        max_bytes=settings.ingestion_max_download_mb * 1024 * 1024,
        timeout_seconds=settings.ingestion_timeout_seconds,
    )
    db = Database(db_url, pool_size=2)
    try:
        async with HttpxFetcher(user_agent=settings.ingestion_user_agent) as fetcher:
            summary = await run_ingestion(
                connector,
                SqlIngestionRepository(db),
                fetcher,
                request,
                RunnerConfig(
                    max_reject_fraction=settings.ingestion_max_reject_fraction, raw_dir=args.raw_dir
                ),
            )
    finally:
        await db.dispose()

    if args.json:
        print(json.dumps(asdict(summary), default=str, indent=2))
    else:
        print(format_summary(summary))
    return exit_code(summary.status)


def format_analysis_summary(s) -> str:
    lines = [
        f"as of         {s.as_of.isoformat()}{'   (dry run: nothing stored)' if s.dry_run else ''}",
        f"series        {s.series_found} found, {s.series_analysed} analysed, "
        f"{len(s.series_skipped)} skipped",
        f"results       {s.results_submitted} computed, {s.results_inserted} new, "
        f"{s.results_already_stored} already stored",
        f"status        {s.status_counts or 'none'}",
    ]
    lines += [f"skipped       {reason}" for reason in s.series_skipped]
    lines += [
        f"FLAG          {item}   (statistical flag; not a confirmed outbreak)" for item in s.flagged
    ]
    return "\n".join(lines)


async def _analyze(args: argparse.Namespace) -> int:
    from datetime import UTC, datetime

    from app.analysis.models import AnalysisConfig
    from app.analysis.service import run_analysis
    from app.analysis.sql_repository import SqlAnalysisRepository
    from app.config import get_settings
    from app.core.logging_config import configure_logging
    from app.db import Database

    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.environment != "development")
    db = Database(_require_db_url(settings), pool_size=2)
    try:
        summary = await run_analysis(
            SqlAnalysisRepository(db),
            AnalysisConfig(),
            args.as_of or datetime.now(UTC).date(),
            dry_run=args.dry_run,
        )
    finally:
        await db.dispose()

    print(
        json.dumps(asdict(summary), default=str, indent=2)
        if args.json
        else format_analysis_summary(summary)
    )
    return EXIT_OK


async def _notify() -> int:
    from datetime import UTC, datetime

    from app.config import get_settings
    from app.core.logging_config import configure_logging
    from app.db import Database
    from app.notifications.dispatcher import DispatchConfig, run_cycle
    from app.notifications.planner import DEFAULT_ACCEPTED_REVIEWS
    from app.notifications.sender import HttpxPoster, ResendSender
    from app.notifications.sql_repository import SqlNotificationRepository

    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.environment != "development")
    api_key = settings.resend_api_key.get_secret_value()
    if not api_key or not settings.email_from:
        print("RESEND_API_KEY and EMAIL_FROM must be set.", file=sys.stderr)
        return EXIT_USAGE
    reviews = (
        DEFAULT_ACCEPTED_REVIEWS | {"auto"}
        if settings.alert_accept_unreviewed
        else DEFAULT_ACCEPTED_REVIEWS
    )
    db = Database(_require_db_url(settings), pool_size=2)
    try:
        summary = await run_cycle(
            SqlNotificationRepository(db),
            ResendSender(api_key, settings.email_from, HttpxPoster()),
            now=datetime.now(UTC),
            config=DispatchConfig(
                batch_size=settings.notification_batch_size,
                accepted_reviews=frozenset(reviews),
                frontend_base_url=settings.frontend_base_url,
            ),
        )
    finally:
        await db.dispose()
    print(json.dumps(asdict(summary), indent=2))
    return EXIT_OK


async def _sources() -> int:
    from sqlalchemy import text

    from app.config import get_settings
    from app.db import Database
    from app.ingestion.connectors import available_sources

    db = Database(_require_db_url(get_settings()), pool_size=1)
    try:
        async with db.session(read_only=True) as conn:
            rows = (
                await conn.execute(
                    text(
                        "select slug, enabled, update_cadence "
                        "from public.data_sources order by slug"
                    )
                )
            ).all()
    finally:
        await db.dispose()
    implemented = set(available_sources())
    print(f"{'slug':26} {'enabled':8} {'connector':10} cadence")
    for slug, enabled, cadence in rows:
        connector = "yes" if slug in implemented else "no"
        print(f"{slug:26} {enabled!s:8} {connector:10} {cadence or '-'}")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "ingest":
            return asyncio.run(_ingest(args))
        if args.command == "analyze":
            return asyncio.run(_analyze(args))
        if args.command == "notify":
            return asyncio.run(_notify())
        return asyncio.run(_sources())
    except (IngestionError, AnalysisPersistenceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
