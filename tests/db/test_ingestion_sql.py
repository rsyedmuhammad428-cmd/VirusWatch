"""The ingestion SQL (app/ingestion/sql.py), executed verbatim against a real PostgreSQL."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.ingestion import sql
from app.ingestion.connectors.flunet_parser import parse_flunet_csv
from app.ingestion.serialization import observation_to_json
from tests.db.helpers import (
    RETRIEVED,
    URL,
    finish,
    obs_rows,
    one,
    parse,
    revised_count,
    source_id,
    start_run,
    upsert,
)
from tests.db.psql_harness import PsqlError, render
from tests.ingestion.flunet_helpers import FIXTURE, row


# ------------------------------------------------------------------ run bookkeeping
class TestRuns:
    def test_only_one_live_run_per_source(self, pg):
        start_run(pg)
        with pytest.raises(PsqlError, match="ingestion_runs_one_running_per_source"):
            start_run(pg)

    def test_dry_runs_may_overlap_a_live_run(self, pg):
        start_run(pg)
        start_run(pg, dry_run=True)
        start_run(pg, dry_run=True)
        assert pg.scalar("select count(*) from public.ingestion_runs") == "3"

    def test_a_finished_run_frees_the_slot(self, pg):
        finish(pg, start_run(pg))
        start_run(pg)

    def test_stale_runs_are_failed_with_a_reason_and_free_the_slot(self, pg):
        stale = start_run(pg)
        pg.execute(sql.FAIL_STALE_RUNS, {"source_id": source_id(pg), "max_age_seconds": 3600.0})
        assert pg.scalar("select status from public.ingestion_runs") == "running"  # not old enough
        pg.execute(sql.FAIL_STALE_RUNS, {"source_id": source_id(pg), "max_age_seconds": 0.0})
        row_ = pg.execute(
            "select status, error_summary, completed_at is not null as done "
            "from public.ingestion_runs"
        )[0]
        assert (
            row_["status"] == "failed"
            and "Abandoned" in row_["error_summary"]
            and row_["done"] == "t"
        )
        start_run(pg)
        assert stale

    def test_stale_reaper_ignores_finished_runs(self, pg):
        finish(pg, start_run(pg))
        pg.execute(sql.FAIL_STALE_RUNS, {"source_id": source_id(pg), "max_age_seconds": 0.0})
        assert pg.scalar("select status from public.ingestion_runs") == "succeeded"

    def test_finish_records_counters_and_merges_metadata(self, pg):
        run_id = start_run(pg, metadata={"signature": "abc123"})
        finish(pg, run_id, source_published_at=datetime(2026, 10, 28, tzinfo=UTC))
        r = pg.execute(
            "select status, records_downloaded, records_inserted, records_updated, checksum, "
            "completed_at is not null as done, "
            "(source_published_at at time zone 'utc')::date::text as published, "
            "metadata_json->>'signature' as sig, metadata_json->>'extra' as extra "
            "from public.ingestion_runs"
        )[0]
        assert r == {
            "status": "succeeded",
            "records_downloaded": "10",
            "records_inserted": "7",
            "records_updated": "1",
            "checksum": "sha256:abc",
            "done": "t",
            "published": "2026-10-28",
            "sig": "abc123",
            "extra": "true",
        }

    def test_a_finished_run_cannot_be_finished_again(self, pg):
        run_id = start_run(pg)
        finish(pg, run_id)
        finish(pg, run_id, status="failed", error_summary="late")
        assert pg.scalar("select status from public.ingestion_runs") == "succeeded"

    def test_failed_and_partial_runs_must_carry_a_reason(self, pg):
        with pytest.raises(PsqlError, match="ingestion_runs_failure_has_reason"):
            finish(pg, start_run(pg), status="failed")
        finish(pg, start_run(pg, dry_run=True), status="partial", error_summary="1 of 10 rejected")

    def test_last_data_run_ignores_dry_failed_and_skipped_runs(self, pg):
        assert pg.execute(sql.LAST_DATA_RUN, {"source_id": source_id(pg)}) == []
        finish(pg, start_run(pg, metadata={"signature": "old"}), checksum="sha256:old")
        finish(
            pg,
            start_run(pg, metadata={"signature": "new"}),
            status="partial",
            error_summary="x",
            checksum="sha256:new",
        )
        finish(
            pg, start_run(pg, dry_run=True, metadata={"signature": "dry"}), checksum="sha256:dry"
        )
        finish(
            pg,
            start_run(pg, metadata={"signature": "bad"}),
            status="failed",
            error_summary="boom",
            checksum="sha256:bad",
        )
        finish(
            pg,
            start_run(pg, metadata={"signature": "skip"}),
            status="skipped_unchanged",
            checksum="sha256:skip",
        )
        last = pg.execute(sql.LAST_DATA_RUN, {"source_id": source_id(pg)})[0]
        assert (last["status"], last["checksum"], last["signature"]) == (
            "partial",
            "sha256:new",
            "new",
        )


# ------------------------------------------------------------------ geographies
class TestGeographies:
    def test_ensure_is_idempotent_and_never_renames(self, pg):
        before = pg.scalar("select count(*) from public.geographies")
        pg.execute(
            sql.ENSURE_COUNTRY_GEOGRAPHY, {"country_code": "PK", "country_name": "Renamed Pakistan"}
        )
        assert pg.scalar("select count(*) from public.geographies") == before
        assert (
            pg.scalar("select country_name from public.geographies where country_code = 'PK'")
            == "Pakistan"
        )

    def test_ensure_creates_a_missing_country_once(self, pg):
        for _ in range(2):
            pg.execute(
                sql.ENSURE_COUNTRY_GEOGRAPHY, {"country_code": "IN", "country_name": "India"}
            )
        rows = pg.execute(
            "select admin_level, code_system, source_geography_code, population "
            "from public.geographies where country_code = 'IN'"
        )
        assert rows == [
            {
                "admin_level": "country",
                "code_system": "ISO3166-1-alpha2",
                "source_geography_code": "IN",
                "population": "",
            }
        ]


# ------------------------------------------------------------------ upsert
class TestUpsert:
    def test_first_load_inserts_with_full_provenance(self, pg):
        run_id = start_run(pg)
        observations = parse(
            row(SPEC_PROCESSED_NB=100, INF_ALL=25, INF_NEGATIVE=70, INF_A=20, INF_B=5)
        )
        assert upsert(pg, observations, run_id) == (len(observations), 0)

        pos = one(pg, "influenza_positivity")
        assert (pos["value"], pos["numerator"], pos["denominator"], pos["unit"]) == (
            "26.3158",
            "25",
            "95",
            "percent",
        )
        assert pos["data_status"] == "reported" and pos["start"] == "2024-01-01"
        assert (
            "derived_by_viruswatch" in pos["flags"]
            and "denominator_differs_from_specimens_processed" in pos["flags"]
        )

        full = pg.execute(
            "select ingestion_run_id::text as run, source_snapshot_checksum as checksum, "
            "source_url, "
            "source_record_id, retrieved_at at time zone 'utc' as retrieved, "
            "o.metadata_json->'source_columns' as cols, g.country_code from public.observations o "
            "join public.geographies g on g.id = o.geography_id "
            "where indicator = 'influenza_positivity'"
        )[0]
        assert (
            full["run"] == run_id and full["checksum"] == "sha256:aaa" and full["source_url"] == URL
        )
        assert full["source_record_id"] == "PAK|2024-W01|SENTINEL" and full["country_code"] == "PK"
        assert json.loads(full["cols"]) == ["INF_ALL", "INF_NEGATIVE"]
        assert full["retrieved"].startswith("2026-10-30 12:00:00")

    def test_numeric_precision_survives_json_and_sql(self, pg):
        upsert(pg, parse(row(INF_ALL=1, INF_NEGATIVE=63)), start_run(pg))
        assert one(pg, "influenza_positivity")["value"] == "1.5625"

    def test_reloading_the_same_data_changes_nothing_and_logs_no_revision(self, pg):
        observations = parse(row(SPEC_PROCESSED_NB=50, INF_ALL=5, INF_NEGATIVE=45))
        run1, run2 = start_run(pg), None
        upsert(pg, observations, run1)
        finish(pg, run1)
        run2 = start_run(pg)
        assert upsert(pg, observations, run2, checksum="sha256:bbb") == (0, len(observations))
        assert pg.scalar("select count(*) from public.observations") == str(len(observations))
        assert revised_count(pg, run2) == 0
        assert pg.scalar("select count(*) from public.observation_revisions") == "0"
        # ...but the rows now say they were re-confirmed by the newer snapshot
        assert pg.scalar(
            "select count(*) from public.observations where source_snapshot_checksum = 'sha256:bbb'"
        ) == str(len(observations))
        assert pg.scalar(
            f"select count(*) from public.observations where ingestion_run_id = '{run2}'"
        ) == str(len(observations))

    def test_a_correction_updates_in_place_and_preserves_the_old_value(self, pg):
        run1 = start_run(pg)
        upsert(pg, parse(row(INF_ALL=5, INF_NEGATIVE=45)), run1)
        finish(pg, run1)
        run2 = start_run(pg)
        inserted, existing = upsert(
            pg, parse(row(INF_ALL=6, INF_NEGATIVE=45)), run2, checksum="sha256:bbb"
        )
        assert (inserted, existing) == (0, 3)  # specimens, positive count, positivity
        assert revised_count(pg, run2) == 2  # only the two that changed
        assert revised_count(pg, run2) == 2

        assert one(pg, "influenza_positive_count")["value"] == "6"
        assert one(pg, "influenza_positive_count")["data_status"] == "revised"
        pos = one(pg, "influenza_positivity")
        assert (pos["value"], pos["numerator"], pos["denominator"], pos["data_status"]) == (
            "11.7647",
            "6",
            "51",
            "revised",
        )

        rev = pg.execute(
            "select r.previous_value_numeric::text as prev, "
            "r.previous_snapshot_checksum as prev_checksum, "
            "r.previous_data_status as prev_status, "
            "r.superseded_by_ingestion_run_id::text as by_run "
            "from public.observation_revisions r "
            "join public.observations o on o.id = r.observation_id "
            "where o.indicator = 'influenza_positive_count'"
        )
        assert rev == [
            {"prev": "5", "prev_checksum": "sha256:aaa", "prev_status": "reported", "by_run": run2}
        ]

    def test_a_source_that_blanks_a_value_makes_it_missing_not_zero(self, pg):
        run1 = start_run(pg)
        upsert(pg, parse(row(INF_ALL=5, INF_NEGATIVE=45)), run1)
        finish(pg, run1)
        run2 = start_run(pg)
        upsert(pg, parse(row(INF_ALL=None, INF_NEGATIVE=45)), run2, checksum="sha256:bbb")
        for indicator in ("influenza_positive_count", "influenza_positivity"):
            r = one(pg, indicator)
            assert (r["data_status"], r["value"]) == ("missing", ""), indicator
        assert revised_count(pg, run2) == 2  # the earlier values are kept in revisions

    def test_a_late_report_filling_a_missing_value_is_a_revision(self, pg):
        run1 = start_run(pg)
        upsert(pg, parse(row(INF_ALL=None, INF_NEGATIVE=None)), run1)
        finish(pg, run1)
        assert one(pg, "influenza_positive_count")["data_status"] == "missing"
        run2 = start_run(pg)
        upsert(pg, parse(row(INF_ALL=4, INF_NEGATIVE=36)), run2, checksum="sha256:bbb")
        assert (
            one(pg, "influenza_positive_count")["data_status"],
            one(pg, "influenza_positive_count")["value"],
        ) == ("revised", "4")
        assert (
            pg.scalar("select previous_data_status from public.observation_revisions limit 1")
            == "missing"
        )

    def test_a_missing_value_staying_missing_is_not_a_revision(self, pg):
        run1 = start_run(pg)
        upsert(pg, parse(row(INF_ALL=None, INF_NEGATIVE=None)), run1)
        finish(pg, run1)
        run2 = start_run(pg)
        upsert(pg, parse(row(INF_ALL=None, INF_NEGATIVE=None)), run2, checksum="sha256:bbb")
        assert (
            revised_count(pg, run2) == 0
            and one(pg, "influenza_positivity")["data_status"] == "missing"
        )

    def test_streams_are_stored_as_separate_series(self, pg):
        upsert(
            pg,
            parse(
                row(ORIGIN_SOURCE="SENTINEL", INF_ALL=1),
                row(ORIGIN_SOURCE="NONSENTINEL", INF_ALL=2),
            ),
            start_run(pg),
        )
        assert one(pg, "influenza_positive_count", stream="sentinel")["value"] == "1"
        assert one(pg, "influenza_positive_count", stream="nonsentinel")["value"] == "2"

    def test_rows_without_a_geography_are_dropped_and_the_count_exposes_it(self, pg):
        """The repository raises if inserted + existing != batch size; this shows it can tell."""
        observations = parse(row(INF_ALL=1, INF_NEGATIVE=9))
        stray = observations[0].model_copy(update={"country_code": "ZZ"})
        inserted, existing = upsert(pg, [observations[0], stray], start_run(pg))
        assert inserted + existing == 1 < 2
        assert pg.scalar("select count(*) from public.observations") == "1"

    def test_special_characters_in_text_fields_are_preserved(self, pg):
        o = parse(row())[0].model_copy(update={"source_record_id": 'O\'Brien "quoted" \\ ünï'})
        upsert(pg, [o], start_run(pg))
        assert (
            pg.scalar("select source_record_id from public.observations")
            == 'O\'Brien "quoted" \\ ünï'
        )

    def test_a_dry_run_inside_a_rolled_back_transaction_reports_counts_but_persists_nothing(
        self, pg
    ):
        run_id = start_run(pg, dry_run=True)
        observations = parse(row(INF_ALL=5, INF_NEGATIVE=45))
        statement = render(
            sql.UPSERT_OBSERVATIONS,
            {
                "rows": json.dumps([observation_to_json(o) for o in observations]),
                "source_id": source_id(pg),
                "run_id": run_id,
                "retrieved_at": RETRIEVED,
                "checksum": "sha256:dry",
            },
        )
        out = pg.script(f"begin;\n{statement};\nrollback;")
        assert out.splitlines()[1].split(",")[:2] == [str(len(observations)), "0"]
        assert pg.scalar("select count(*) from public.observations") == "0"

    def test_a_failure_part_way_rolls_back_the_whole_batch(self, pg):
        good = parse(row(INF_ALL=5, INF_NEGATIVE=45))
        payload = [observation_to_json(o) for o in good]
        payload[-1]["unit"] = None  # violates observations_numeric_has_unit
        statement = render(
            sql.UPSERT_OBSERVATIONS,
            {
                "rows": json.dumps(payload),
                "source_id": source_id(pg),
                "run_id": start_run(pg),
                "retrieved_at": RETRIEVED,
                "checksum": "sha256:x",
            },
        )
        with pytest.raises(PsqlError, match="observations_numeric_has_unit"):
            pg.script(f"begin;\n{statement};\ncommit;")
        assert pg.scalar("select count(*) from public.observations") == "0"


# ------------------------------------------------------------------ the whole pipeline
class TestEndToEndFixture:
    def load(self, pg):
        result = parse_flunet_csv(
            FIXTURE.read_bytes(),
            countries=frozenset({"PAK", "USA"}),
            since_year=None,
            source_url=URL,
        )
        run_id = start_run(pg)
        counts = upsert(pg, result.observations, run_id)
        finish(pg, run_id)
        return result, counts

    def test_fixture_loads_completely(self, pg):
        result, counts = self.load(pg)
        assert counts == (43, 0) and len(result.observations) == 43
        assert pg.scalar("select count(*) from public.observations") == "43"

    def test_values_in_the_database_match_the_source_rows(self, pg):
        self.load(pg)
        week = "2024-01-01"
        assert one(pg, "influenza_positive_count", week=week, country="PK")["value"] == "25"
        assert (
            one(pg, "influenza_positive_count", "influenza_b_victoria", week=week, country="PK")[
                "value"
            ]
            == "5"
        )
        assert (
            one(pg, "influenza_positive_count", "influenza_b", week=week, country="PK")["value"]
            == "5"
        )
        assert (
            one(pg, "influenza_positive_count", stream="nonsentinel", week=week, country="PK")[
                "value"
            ]
            == "2"
        )
        assert (
            one(pg, "influenza_positivity", stream="nonsentinel", week=week, country="PK")["value"]
            == "5.0000"
        )

    def test_blank_and_zero_test_weeks_are_missing_never_zero(self, pg):
        self.load(pg)
        blank_week = obs_rows(pg, "reporting_period_start = '2024-01-08'")
        assert len(blank_week) == 3 and all(
            r["data_status"] == "missing" and r["value"] == "" for r in blank_week
        )
        zero_tests = [
            r
            for r in obs_rows(pg, "reporting_period_start = '2024-01-15'")
            if r["indicator"] == "influenza_positivity"
        ][0]
        assert (zero_tests["data_status"], zero_tests["value"], zero_tests["denominator"]) == (
            "missing",
            "",
            "0",
        )
        assert (
            pg.scalar("select count(*) from public.observations where data_status = 'missing'")
            == "4"
        )

    def test_week_53_and_the_not_defined_stream(self, pg):
        self.load(pg)
        r = pg.execute(
            "select reporting_period_start::text as s, reporting_period_end::text as e "
            "from public.observations where surveillance_stream = 'undefined' limit 1"
        )[0]
        assert (r["s"], r["e"]) == ("2020-12-28", "2021-01-03")

    def test_loading_twice_is_idempotent(self, pg):
        result, _ = self.load(pg)
        assert upsert(pg, result.observations, start_run(pg), checksum="sha256:again") == (0, 43)
        assert pg.scalar("select count(*) from public.observations") == "43"

    def test_both_countries_resolve_to_their_geographies(self, pg):
        self.load(pg)
        rows = pg.execute(
            "select g.country_code, count(*) as n from public.observations o "
            "join public.geographies g on g.id = o.geography_id group by 1 order by 1"
        )
        assert rows == [{"country_code": "PK", "n": "40"}, {"country_code": "US", "n": "3"}]

    def test_nothing_is_marked_provisional_or_estimated_without_a_source_saying_so(self, pg):
        self.load(pg)
        assert (
            pg.scalar(
                "select count(*) from public.observations "
                "where data_status in ('provisional', 'estimated')"
            )
            == "0"
        )
        assert (
            Decimal(
                pg.scalar("select count(*) from public.observations where published_at is not null")
            )
            == 0
        )
