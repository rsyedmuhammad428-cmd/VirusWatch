from __future__ import annotations

import asyncio
import gzip
import json
from datetime import UTC, date, datetime, timedelta

import pytest

from app.ingestion.connectors.flunet import FluNetConnector
from app.ingestion.errors import PersistenceError
from app.ingestion.http import RetryPolicy
from app.ingestion.models import IngestRequest, RunStatus
from app.ingestion.runner import RunnerConfig, run_ingestion, safe_error_summary
from tests.ingestion.fakes import FakeFetcher, FakeRepository, ok, status
from tests.ingestion.flunet_helpers import FIXTURE, csv_bytes, row

FIXED_NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
PAK = frozenset({"PAK"})


def connector() -> FluNetConnector:
    return FluNetConnector(
        retry_policy=RetryPolicy(max_attempts=3, base_delay=0, max_delay=0), clock=lambda: FIXED_NOW
    )


def request(**overrides) -> IngestRequest:
    return IngestRequest(**{"countries": PAK, **overrides})


def good_csv(n_weeks: int = 5, **row_overrides) -> bytes:
    rows = [
        row(
            ISO_WEEK=w,
            ISO_WEEKSTARTDATE=date.fromisocalendar(2024, w, 1).isoformat(),
            **row_overrides,
        )
        for w in range(1, n_weeks + 1)
    ]
    return csv_bytes(*rows)


def go(repo, fetcher, req=None, config=None, conn=None):
    config = config or RunnerConfig()
    return asyncio.run(run_ingestion(conn or connector(), repo, fetcher, req or request(), config))


# ------------------------------------------------------------------ happy path
class TestSuccess:
    def test_clean_file_succeeds_and_persists_everything(self):
        repo, fetcher = FakeRepository(), FakeFetcher(ok(good_csv(5)))
        summary = go(repo, fetcher)

        assert summary.status is RunStatus.SUCCEEDED and summary.error is None
        assert (summary.rows_read, summary.rows_rejected) == (5, 0)
        assert summary.observations_inserted == 15  # 3 core indicators x 5 weeks
        assert summary.latest_period_end == date(2024, 2, 4)
        run = repo.only_run()
        assert run.status == "succeeded" and run.result.error_summary is None
        assert (run.result.records_downloaded, run.result.records_inserted) == (5, 15)
        assert run.result.checksum == summary.checksum and summary.checksum.startswith("sha256:")

    def test_run_metadata_supports_reproducibility(self):
        repo = FakeRepository()
        go(repo, FakeFetcher(ok(good_csv(2))), request(since_year=2020))
        meta = repo.only_run().metadata
        assert meta["parser_version"] == "flunet-1" and meta["since_year"] == 2020
        assert meta["countries"] == ["PAK"] and len(str(meta["signature"])) == 16
        assert meta["observations_generated"] == 6 and meta["reject_reasons"] == {}

    def test_fetch_uses_the_odata_filter_for_the_request(self):
        fetcher = FakeFetcher(ok(good_csv(1)))
        go(FakeRepository(), fetcher, request(countries=frozenset({"USA", "PAK"}), since_year=2022))
        params = fetcher.calls[0]["params"]
        assert params["$format"] == "csv"
        assert (
            params["$filter"]
            == "(COUNTRY_CODE eq 'PAK' or COUNTRY_CODE eq 'USA') and ISO_YEAR ge 2022"
        )

    def test_transient_failure_then_success(self):
        repo, fetcher = FakeRepository(), FakeFetcher(status(503), ok(good_csv(1)))
        assert go(repo, fetcher).status is RunStatus.SUCCEEDED and len(fetcher.calls) == 2

    def test_stale_runs_are_reaped_before_every_run(self):
        repo = FakeRepository(stale_to_report=2)
        go(repo, FakeFetcher(ok(good_csv(1))))
        assert repo.stale_calls == [timedelta(hours=6)]


# ------------------------------------------------------------------ never report false success
class TestFailures:
    def test_permanent_http_error_fails_and_persists_nothing(self):
        repo = FakeRepository()
        summary = go(repo, FakeFetcher(status(404)))
        assert summary.status is RunStatus.FAILED and "HTTP 404" in summary.error
        assert repo.persist_calls == [] and repo.store == {}
        run = repo.only_run()
        assert (
            run.status == "failed"
            and run.result.checksum is None
            and run.result.records_downloaded == 0
        )

    def test_html_error_page_with_200_is_not_ingested(self):
        body = b"<html>Service unavailable</html>"
        summary = go(FakeRepository(), FakeFetcher(ok(body, **{"content-type": "text/html"})))
        assert summary.status is RunStatus.FAILED and "content type" in summary.error

    def test_schema_mismatch_fails_but_still_records_the_checksum(self):
        repo = FakeRepository()
        body = b"WRONG,HEADER\n1,2\n"
        summary = go(repo, FakeFetcher(ok(body)))
        assert summary.status is RunStatus.FAILED and "Required column" in summary.error
        assert repo.persist_calls == []
        assert repo.only_run().result.checksum == summary.checksum is not None

    def test_no_rows_for_requested_scope_is_a_failure_not_a_success(self):
        body = csv_bytes(row(COUNTRY_CODE="IND"))
        summary = go(FakeRepository(), FakeFetcher(ok(body)))
        assert summary.status is RunStatus.FAILED and "no usable rows" in summary.error

    def test_header_only_file_is_a_failure(self):
        summary = go(FakeRepository(), FakeFetcher(ok(csv_bytes())))
        assert summary.status is RunStatus.FAILED

    def test_too_many_rejections_abort_without_persisting(self):
        repo = FakeRepository()
        summary = go(
            repo,
            FakeFetcher(ok(FIXTURE.read_bytes())),
            request(countries=frozenset({"PAK", "USA"})),
        )
        assert summary.status is RunStatus.FAILED
        assert (
            "7 of 17 in-scope rows were rejected" in summary.error
            and "Nothing was persisted" in summary.error
        )
        assert repo.persist_calls == [] and repo.store == {}

    def test_persistence_failure_marks_the_run_failed(self):
        repo = FakeRepository(
            persist_error=PersistenceError("Database write failed (OperationalError).")
        )
        summary = go(repo, FakeFetcher(ok(good_csv(2))))
        assert summary.status is RunStatus.FAILED and "Database write failed" in summary.error
        assert repo.only_run().status == "failed"

    def test_unexpected_exceptions_do_not_leak_details(self):
        secret = "password=hunter2 host=db.internal"
        repo = FakeRepository(persist_error=RuntimeError(secret))
        summary = go(repo, FakeFetcher(ok(good_csv(2))))
        assert summary.error == "Unexpected RuntimeError"
        assert secret not in repr(repo.only_run().result) and secret not in repr(summary)

    def test_safe_error_summary_truncates(self):
        from app.ingestion.errors import IngestionError

        assert len(safe_error_summary(IngestionError("x" * 5000))) == 500

    def test_failed_run_never_leaves_partial_data(self):
        repo = FakeRepository(persist_error=PersistenceError("boom"))
        go(repo, FakeFetcher(ok(good_csv(3))))
        assert repo.store == {}

    def test_cancellation_is_not_swallowed(self):
        repo = FakeRepository()
        with pytest.raises(asyncio.CancelledError):
            go(repo, FakeFetcher(asyncio.CancelledError()))
        assert repo.only_run().status == "running"  # left for the stale-run reaper


# ------------------------------------------------------------------ partial success
class TestPartial:
    def mixed(self, n_good: int, n_bad: int) -> bytes:
        good = [
            row(ISO_WEEK=w, ISO_WEEKSTARTDATE=date.fromisocalendar(2024, w, 1).isoformat())
            for w in range(1, n_good + 1)
        ]
        bad = [
            row(
                ISO_WEEK=w,
                ISO_WEEKSTARTDATE=date.fromisocalendar(2024, w, 1).isoformat(),
                INF_ALL="x",
            )
            for w in range(n_good + 1, n_good + n_bad + 1)
        ]
        return csv_bytes(*good, *bad)

    def test_some_rejections_yield_partial_with_reasons_and_persist_valid_rows(self):
        repo = FakeRepository()
        summary = go(repo, FakeFetcher(ok(self.mixed(9, 1))))  # 10% rejected < 20% limit
        assert summary.status is RunStatus.PARTIAL
        assert summary.observations_inserted == 27 and summary.rows_rejected == 1
        result = repo.only_run().result
        assert result.status is RunStatus.PARTIAL
        assert (
            "1 of 10 source rows rejected" in result.error_summary
            and "invalid_count" in result.error_summary
        )
        assert result.metadata["sample_rejections"][0]["reason"] == "invalid_count"

    def test_exactly_at_the_limit_is_still_partial(self):
        assert (
            go(FakeRepository(), FakeFetcher(ok(self.mixed(4, 1)))).status is RunStatus.PARTIAL
        )  # 20%

    def test_just_over_the_limit_fails(self):
        assert (
            go(FakeRepository(), FakeFetcher(ok(self.mixed(3, 1)))).status is RunStatus.FAILED
        )  # 25%

    def test_limit_is_configurable(self):
        lenient = RunnerConfig(max_reject_fraction=0.5)
        repo = FakeRepository()
        summary = go(
            repo,
            FakeFetcher(ok(FIXTURE.read_bytes())),
            request(countries=frozenset({"PAK", "USA"})),
            lenient,
        )
        assert summary.status is RunStatus.PARTIAL and summary.observations_inserted == 43

    def test_invalid_config_rejected(self):
        with pytest.raises(ValueError):
            RunnerConfig(max_reject_fraction=1.5)


# ---------------------------------------------------------- idempotency and change detection
class TestChangeDetection:
    def test_identical_snapshot_is_skipped(self):
        repo, body = FakeRepository(), good_csv(2)
        assert go(repo, FakeFetcher(ok(body))).status is RunStatus.SUCCEEDED
        second = go(repo, FakeFetcher(ok(body)))
        assert second.status is RunStatus.SKIPPED_UNCHANGED
        assert len(repo.persist_calls) == 1 and repo.runs[1].status == "skipped_unchanged"

    def test_force_reingests_an_unchanged_snapshot_idempotently(self):
        repo, body = FakeRepository(), good_csv(2)
        go(repo, FakeFetcher(ok(body)))
        forced = go(repo, FakeFetcher(ok(body)), request(force=True))
        assert forced.status is RunStatus.SUCCEEDED
        assert (
            forced.observations_inserted,
            forced.observations_updated,
            forced.observations_unchanged,
        ) == (0, 0, 6)
        assert len(repo.store) == 6

    def test_a_source_correction_is_applied_and_counted_as_updated(self):
        repo = FakeRepository()
        go(repo, FakeFetcher(ok(csv_bytes(row(INF_ALL=5, INF_NEGATIVE=45)))))
        second = go(repo, FakeFetcher(ok(csv_bytes(row(INF_ALL=6, INF_NEGATIVE=45)))))
        assert second.status is RunStatus.SUCCEEDED
        assert (
            second.observations_inserted,
            second.observations_updated,
            second.observations_unchanged,
        ) == (0, 2, 1)
        key = next(k for k, o in repo.store.items() if o.indicator == "influenza_positive_count")
        assert repo.store[key].value_numeric == 6

    def test_same_bytes_with_different_query_are_not_skipped(self):
        repo, body = FakeRepository(), good_csv(2)
        go(repo, FakeFetcher(ok(body)), request(since_year=2020))
        assert (
            go(repo, FakeFetcher(ok(body)), request(since_year=2021)).status is RunStatus.SUCCEEDED
        )

    def test_a_parser_version_bump_reingests_identical_bytes(self):
        repo, body = FakeRepository(), good_csv(2)
        go(repo, FakeFetcher(ok(body)))
        newer = connector()
        newer.parser_version = "flunet-2"
        assert go(repo, FakeFetcher(ok(body)), conn=newer).status is RunStatus.SUCCEEDED

    def test_failed_runs_do_not_block_the_next_attempt(self):
        repo, body = FakeRepository(), good_csv(2)
        go(repo, FakeFetcher(ok(body)), config=RunnerConfig())  # succeeds
        repo.persist_error = PersistenceError("db down")
        assert go(repo, FakeFetcher(ok(good_csv(3)))).status is RunStatus.FAILED
        repo.persist_error = None
        assert go(repo, FakeFetcher(ok(good_csv(3)))).status is RunStatus.SUCCEEDED


# ------------------------------------------------------------------ dry run
class TestDryRun:
    def test_dry_run_reports_real_counts_but_changes_nothing(self):
        repo = FakeRepository()
        summary = go(repo, FakeFetcher(ok(good_csv(3))), request(dry_run=True))
        assert summary.status is RunStatus.SUCCEEDED and summary.dry_run
        assert summary.observations_inserted == 9
        assert repo.store == {} and repo.persist_calls[0]["dry_run"] is True
        assert repo.only_run().dry_run is True

    def test_dry_run_is_not_mistaken_for_a_previous_real_run(self):
        repo, body = FakeRepository(), good_csv(2)
        go(repo, FakeFetcher(ok(body)), request(dry_run=True))
        real = go(repo, FakeFetcher(ok(body)))
        assert real.status is RunStatus.SUCCEEDED and len(repo.store) == 6

    def test_dry_run_ignores_the_unchanged_shortcut(self):
        repo, body = FakeRepository(), good_csv(2)
        go(repo, FakeFetcher(ok(body)))
        dry = go(repo, FakeFetcher(ok(body)), request(dry_run=True))
        assert dry.status is RunStatus.SUCCEEDED and dry.observations_unchanged == 6

    def test_dry_run_may_overlap_a_live_run(self):
        repo = FakeRepository()
        asyncio.run(
            repo.start_run(repo.source.id, triggered_by="manual", dry_run=False, metadata={})
        )
        assert (
            go(repo, FakeFetcher(ok(good_csv(1))), request(dry_run=True)).status
            is RunStatus.SUCCEEDED
        )


# ------------------------------------------------------------------ gating and concurrency
class TestGating:
    def test_second_live_run_is_refused_without_touching_the_source(self):
        repo, fetcher = FakeRepository(), FakeFetcher(ok(good_csv(1)))
        asyncio.run(
            repo.start_run(repo.source.id, triggered_by="scheduled", dry_run=False, metadata={})
        )
        summary = go(repo, fetcher)
        assert summary.status is RunStatus.SKIPPED_ALREADY_RUNNING and summary.run_id is None
        assert fetcher.calls == [] and len(repo.runs) == 1

    def test_scheduled_run_of_a_disabled_source_is_skipped(self):
        repo, fetcher = FakeRepository(enabled=False), FakeFetcher(ok(good_csv(1)))
        summary = go(repo, fetcher, request(triggered_by="scheduled"))
        assert summary.status is RunStatus.SKIPPED_DISABLED
        assert repo.runs == [] and fetcher.calls == []

    def test_manual_run_of_a_disabled_source_is_allowed(self):
        """Needed for the very first review run, before the source is enabled."""
        repo = FakeRepository(enabled=False)
        assert go(repo, FakeFetcher(ok(good_csv(1)))).status is RunStatus.SUCCEEDED

    def test_unknown_source_raises(self):
        from app.ingestion.errors import IngestionError

        repo = FakeRepository()
        repo.source = type(repo.source)(repo.source.id, "other_source", "x", True)
        with pytest.raises(IngestionError, match="not registered"):
            go(repo, FakeFetcher(ok(b"")))


# ------------------------------------------------------------------ raw snapshot archive
class TestRawArchive:
    def test_downloaded_bytes_are_archived_with_provenance(self, tmp_path):
        body = good_csv(2)
        repo = FakeRepository()
        go(repo, FakeFetcher(ok(body)), config=RunnerConfig(raw_dir=tmp_path))
        archived = repo.only_run().metadata["raw_archive"]
        data_file = tmp_path / archived
        assert data_file.parent.name == "who_flunet" and data_file.name.startswith(
            "20261030T120000Z_"
        )
        assert gzip.decompress(data_file.read_bytes()) == body
        sidecar = json.loads(data_file.with_suffix(".json").read_text())
        assert sidecar["checksum"] == repo.only_run().result.checksum
        assert sidecar["url"].endswith("?$format=csv") and sidecar["bytes"] == len(body)

    def test_archive_is_written_even_when_the_run_later_fails(self, tmp_path):
        repo = FakeRepository()
        go(repo, FakeFetcher(ok(b"WRONG,HEADER\n1,2\n")), config=RunnerConfig(raw_dir=tmp_path))
        assert list((tmp_path / "who_flunet").glob("*.csv.gz"))  # evidence for debugging
