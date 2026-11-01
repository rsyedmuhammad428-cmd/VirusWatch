"""The analysis SQL (app/analysis/sql.py) against a real PostgreSQL, plus the full pipeline."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.analysis import sql
from app.analysis.engine import analyze_series
from app.analysis.loading import group_series
from app.analysis.models import AnalysisConfig
from app.analysis.serialization import result_to_json
from app.ingestion.connectors.flunet_parser import parse_flunet_csv
from tests.db.helpers import URL, finish, parse, start_run, upsert
from tests.db.psql_harness import PsqlError
from tests.ingestion.flunet_helpers import FIXTURE, row

INDICATORS = json.dumps(["influenza_positivity"])


def weekly_rows(*weeks, stream="SENTINEL"):
    """One FluNet-style row per week; each week is (index, positives, negatives)."""
    base = date(2024, 1, 1)
    out = []
    for i, pos, neg in weeks:
        monday = base + timedelta(weeks=i)
        iso = monday.isocalendar()
        out.append(
            row(
                ISO_YEAR=iso.year,
                ISO_WEEK=iso.week,
                ISO_WEEKSTARTDATE=monday.isoformat(),
                ORIGIN_SOURCE=stream,
                INF_ALL=pos,
                INF_NEGATIVE=neg,
            )
        )
    return out


def load_into_db(pg, lines, **kw):
    run_id = start_run(pg)
    n = upsert(pg, parse(*lines, **kw), run_id)
    finish(pg, run_id)
    return n


def load_points(pg):
    return group_series(pg.execute(sql.LOAD_POINTS, {"indicators": INDICATORS}))


def store(pg, results):
    r = pg.execute(sql.INSERT_RESULTS, {"rows": json.dumps([result_to_json(x) for x in results])})[
        0
    ]
    return int(r["submitted"]), int(r["inserted"])


def analyse(pg, as_of):
    out = []
    for key, points in load_points(pg):
        out.extend(analyze_series(key, points, AnalysisConfig(), as_of))
    return out


# 9 weeks of 10% positivity, then a jump to 30%
JUMP = [(i, 10, 90) for i in range(8)] + [(8, 30, 70)]


class TestLoadPoints:
    def test_returns_the_stored_series_with_flags_and_decimals(self, pg):
        load_into_db(pg, weekly_rows((0, 25, 70)))
        ((key, pts),) = load_points(pg)
        assert (key.country_code, key.disease, key.indicator, key.surveillance_stream) == (
            "PK",
            "influenza",
            "influenza_positivity",
            "sentinel",
        )
        assert key.unit == "percent"
        assert str(pts[0].value) == "26.3158"
        assert "derived_by_viruswatch" in pts[0].flags
        assert pts[0].period_end - pts[0].period_start == timedelta(days=6)

    def test_only_requested_indicators_are_returned(self, pg):
        load_into_db(pg, weekly_rows((0, 25, 70)))
        rows = pg.execute(sql.LOAD_POINTS, {"indicators": json.dumps(["influenza_positive_count"])})
        assert {r["indicator"] for r in rows} == {"influenza_positive_count"}
        assert pg.execute(sql.LOAD_POINTS, {"indicators": json.dumps(["nope"])}) == []

    def test_streams_arrive_as_separate_series(self, pg):
        load_into_db(pg, weekly_rows((0, 5, 45)) + weekly_rows((0, 7, 43), stream="NONSENTINEL"))
        assert sorted(k.surveillance_stream for k, _ in load_points(pg)) == [
            "nonsentinel",
            "sentinel",
        ]

    def test_missing_weeks_load_as_missing_not_zero(self, pg):
        lines = weekly_rows((0, 5, 45), (2, 5, 45))
        lines.insert(
            1, row(ISO_WEEK=2, ISO_WEEKSTARTDATE="2024-01-08", INF_ALL=None, INF_NEGATIVE=None)
        )
        load_into_db(pg, lines)
        ((_, pts),) = load_points(pg)
        assert [p.value is None for p in pts] == [False, True, False]
        assert pts[1].data_status == "missing"


class TestInsertResults:
    def results(self, pg):
        load_into_db(pg, weekly_rows(*JUMP))
        return analyse(pg, as_of=date(2024, 3, 4))

    def test_stores_every_result_with_provenance(self, pg):
        results = self.results(pg)
        assert store(pg, results) == (6, 6)
        a = pg.execute(
            "select status, observed_value::float8::text as observed, "
            "baseline_value::float8::text as baseline, "
            "methodology_version, review_status, surveillance_stream, "
            "cardinality(input_observation_ids) as n_inputs, "
            "parameters->>'anomaly_z' as z, confidence_or_uncertainty->>'baseline_kind' as kind "
            "from public.analysis_results where analysis_type = 'anomaly_detection'"
        )[0]
        assert a == {
            "status": "possible_anomaly",
            "observed": "30",
            "baseline": "10",
            "methodology_version": "1.0.0",
            "review_status": "auto",
            "surveillance_stream": "sentinel",
            "n_inputs": "9",
            "z": "3.5",
            "kind": "trailing",
        }

    def test_input_observation_ids_point_at_real_observations(self, pg):
        store(pg, self.results(pg))
        orphans = pg.scalar(
            "select count(*) from public.analysis_results a, unnest(a.input_observation_ids) as i "
            "where not exists (select 1 from public.observations o where o.id = i)"
        )
        assert orphans == "0"

    def test_rerunning_on_unchanged_data_inserts_nothing(self, pg):
        results = self.results(pg)
        store(pg, results)
        assert store(pg, results) == (6, 0)
        assert pg.scalar("select count(*) from public.analysis_results") == "6"

    def test_a_revised_observation_produces_new_results_and_keeps_the_old_ones(self, pg):
        store(pg, self.results(pg))
        load_into_db(pg, weekly_rows((8, 40, 60)))  # the latest week is corrected
        newer = analyse(pg, as_of=date(2024, 3, 4))
        submitted, inserted = store(pg, newer)
        assert inserted > 0
        assert pg.scalar("select count(*) from public.analysis_results") == str(6 + inserted)
        values = pg.execute(
            "select distinct observed_value::float8::text as v from public.analysis_results "
            "where analysis_type = 'anomaly_detection' order by 1"
        )
        assert [r["v"] for r in values] == ["30", "40"]

    def test_sentinel_and_nonsentinel_results_do_not_collide(self, pg):
        load_into_db(pg, weekly_rows(*JUMP) + weekly_rows(*JUMP, stream="NONSENTINEL"))
        assert store(pg, analyse(pg, date(2024, 3, 4))) == (12, 12)
        assert (
            pg.scalar("select count(distinct surveillance_stream) from public.analysis_results")
            == "2"
        )

    def test_a_methodology_change_creates_new_rows_instead_of_overwriting(self, pg):
        results = self.results(pg)
        store(pg, results)
        bumped = [replace(r, methodology_version="9.9.9") for r in results]
        assert store(pg, bumped) == (6, 6)

    def test_confirmed_by_source_needs_a_url(self, pg):
        r = result_to_json(self.results(pg)[0]) | {"status": "confirmed_by_source"}
        with pytest.raises(PsqlError, match="analysis_confirmed_needs_source"):
            pg.execute(sql.INSERT_RESULTS, {"rows": json.dumps([r])})

    def test_a_failed_batch_stores_nothing(self, pg):
        good, bad = (result_to_json(r) for r in self.results(pg)[:2])
        bad["status"] = "outbreak"  # not an allowed status
        with pytest.raises(PsqlError, match="analysis_results_status_check"):
            pg.execute(sql.INSERT_RESULTS, {"rows": json.dumps([good, bad])})
        assert pg.scalar("select count(*) from public.analysis_results") == "0"

    def test_results_for_an_unknown_geography_are_rejected(self, pg):
        r = result_to_json(self.results(pg)[0]) | {
            "geography_id": "00000000-0000-0000-0000-000000000000"
        }
        with pytest.raises(PsqlError, match="foreign key"):
            pg.execute(sql.INSERT_RESULTS, {"rows": json.dumps([r])})


class TestPipelineOnTheFixture:
    def test_fixture_through_ingestion_analysis_and_storage(self, pg):
        parsed = parse_flunet_csv(
            FIXTURE.read_bytes(),
            countries=frozenset({"PAK", "USA"}),
            since_year=None,
            source_url=URL,
        )
        run_id = start_run(pg)
        upsert(pg, parsed.observations, run_id)
        finish(pg, run_id)

        results = analyse(pg, as_of=date(2024, 3, 11))
        assert results, "expected analysable series in the fixture"
        submitted, inserted = store(pg, results)
        assert submitted == inserted == len(results)
        # every analysed series is one of the fixture's positivity series and nothing else
        assert pg.scalar("select count(distinct indicator) from public.analysis_results") == "1"
        statuses = {
            r["status"] for r in pg.execute("select distinct status from public.analysis_results")
        }
        assert "confirmed_by_source" not in statuses
        # the fixture is far too short for a seasonal baseline: never claimed
        assert (
            pg.scalar(
                "select count(*) from public.analysis_results "
                "where confidence_or_uncertainty->>'baseline_kind' = 'seasonal'"
            )
            == "0"
        )
