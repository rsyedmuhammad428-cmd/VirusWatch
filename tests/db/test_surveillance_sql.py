"""The surveillance/sources SQL against real PostgreSQL, as the roles the API uses."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime

import pytest

from app.analysis import sql as analysis_sql
from app.analysis.engine import analyze_series
from app.analysis.loading import group_series
from app.analysis.models import AnalysisConfig
from app.analysis.serialization import result_to_json
from app.ingestion.connectors.flunet_parser import parse_flunet_csv
from app.surveillance import queries as q
from app.surveillance import service as svc
from tests.db.helpers import URL, finish, start_run, upsert
from tests.db.psql_harness import PsqlError
from tests.ingestion.flunet_helpers import FIXTURE

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
NO_FILTERS = {"country": None, "disease": None, "indicator": None, "stream": None}
RANGE = {"date_from": None, "date_to": None}


_TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(\.\d+)?[+-]\d\d(:?\d\d)?$")


def typed(rows):
    """psql returns text; the real driver returns NULL as None and timestamps as datetimes.
    Mirror both so the response builders see what they will see in production."""

    def convert(value):
        if value == "":
            return None
        if _TIMESTAMP.match(value):
            return datetime.fromisoformat(value)
        return value

    return [{k: convert(v) for k, v in r.items()} for r in rows]


@pytest.fixture
def loaded(pg):
    """Fixture data ingested, analysed (as of 2024-03-11) and stored."""
    parsed = parse_flunet_csv(
        FIXTURE.read_bytes(), countries=frozenset({"PAK", "USA"}), since_year=None, source_url=URL
    )
    run_id = start_run(pg)
    upsert(pg, parsed.observations, run_id)
    finish(pg, run_id)
    rows = pg.execute(
        analysis_sql.LOAD_POINTS, {"indicators": json.dumps(["influenza_positivity"])}
    )
    results = []
    for key, points in group_series(rows):
        results.extend(analyze_series(key, points, AnalysisConfig(), date(2024, 3, 11)))
    payload = json.dumps([result_to_json(r) for r in results])
    pg.execute(analysis_sql.INSERT_RESULTS, {"rows": payload})
    return pg


def anon(pg, statement, params=None):
    return pg.execute(statement, params or {}, role="anon")


class TestGeographies:
    def test_lists_only_geographies_that_have_observations(self, loaded):
        rows = anon(loaded, q.GEOGRAPHIES)
        assert [(r["country_code"], r["observation_count"]) for r in rows] == [
            ("PK", "40"),
            ("US", "3"),
        ]
        assert {r["geographic_resolution"] for r in rows} == {"country"}

    def test_empty_database_has_none(self, pg):
        assert anon(pg, q.GEOGRAPHIES) == []


class TestObservations:
    def page(self, pg, **kw):
        return anon(pg, q.OBSERVATIONS, {**NO_FILTERS, **RANGE, "limit": 100, "offset": 0, **kw})

    def test_unfiltered_returns_everything_newest_first(self, loaded):
        rows = self.page(loaded)
        assert len(rows) == 43 and {r["total_count"] for r in rows} == {"43"}
        ends = [r["period_end"] for r in rows]
        assert ends == sorted(ends, reverse=True)

    def test_each_row_carries_source_attribution_and_terms_status(self, loaded):
        r = self.page(loaded)[0]
        assert (r["source_slug"], r["terms_verified"], r["license_name"]) == ("who_flunet", "f", "")
        assert (
            r["source_url"] == URL and r["retrieved_at"] and r["geographic_resolution"] == "country"
        )

    def test_filters_combine(self, loaded):
        rows = self.page(loaded, country="PK", indicator="influenza_positivity", stream="sentinel")
        assert rows and {
            (r["country_code"], r["indicator"], r["surveillance_stream"]) for r in rows
        } == {("PK", "influenza_positivity", "sentinel")}
        assert self.page(loaded, country="IN") == []
        assert self.page(loaded, disease="dengue") == []

    def test_date_range_is_inclusive_on_overlap(self, loaded):
        rows = self.page(loaded, date_from="2024-01-08", date_to="2024-01-14")
        assert {r["period_start"] for r in rows} == {"2024-01-08"}

    def test_pagination_pages_are_disjoint_and_total_is_stable(self, loaded):
        a = self.page(loaded, limit=10, offset=0)
        b = self.page(loaded, limit=10, offset=10)
        assert len(a) == len(b) == 10 and {r["total_count"] for r in a + b} == {"43"}
        assert not {r["observation_id"] for r in a} & {r["observation_id"] for r in b}
        assert len(self.page(loaded, limit=10, offset=40)) == 3
        assert self.page(loaded, limit=10, offset=100) == []

    def test_missing_values_are_null_not_zero(self, loaded):
        rows = self.page(loaded, country="PK", date_from="2024-01-08", date_to="2024-01-14")
        assert rows and all(r["value"] == "" and r["data_status"] == "missing" for r in rows)


class TestLatestAndPoints:
    def test_one_latest_row_per_series(self, loaded):
        rows = anon(loaded, q.LATEST_OBSERVATIONS, NO_FILTERS)
        keys = {
            (
                r["geography_id"],
                r["disease"],
                r["pathogen"],
                r["indicator"],
                r["surveillance_stream"],
            )
            for r in rows
        }
        assert len(rows) == len(keys) > 3
        pk = next(
            r
            for r in rows
            if r["country_code"] == "PK"
            and r["indicator"] == "influenza_positivity"
            and r["surveillance_stream"] == "sentinel"
        )
        assert pk["period_end"] == "2024-03-03"  # the fixture's latest sentinel week for PK

    def test_series_points_are_chronological_and_limited(self, loaded):
        params = {
            **NO_FILTERS,
            **RANGE,
            "country": "PK",
            "indicator": "influenza_positivity",
            "stream": "sentinel",
            "limit": 5,
        }
        rows = anon(loaded, q.SERIES_POINTS, params)
        assert len(rows) == 5
        assert [r["period_start"] for r in rows] == sorted(r["period_start"] for r in rows)


class TestComparisonPoints:
    def params(self, **kw):
        return {
            "indicator": "influenza_positivity",
            "stream": "sentinel",
            "countries": json.dumps(["PK", "US"]),
            **RANGE,
            "limit": 1000,
            **kw,
        }

    def test_returns_only_the_requested_indicator_stream_and_countries(self, loaded):
        rows = anon(loaded, q.COMPARISON_POINTS, self.params())
        assert {r["indicator"] for r in rows} == {"influenza_positivity"}
        assert {r["surveillance_stream"] for r in rows} == {"sentinel"}
        assert {r["country_code"] for r in rows} <= {"PK", "US"}

    def test_country_list_restricts(self, loaded):
        rows = anon(loaded, q.COMPARISON_POINTS, self.params(countries=json.dumps(["US"])))
        assert {r["country_code"] for r in rows} == {"US"}

    def test_a_null_stream_matches_only_series_without_a_stream(self, loaded):
        assert anon(loaded, q.COMPARISON_POINTS, self.params(stream=None)) == []


class TestAnalyses:
    def test_latest_result_per_series_and_type(self, loaded):
        rows = anon(loaded, q.LATEST_ANALYSES, NO_FILTERS)
        keys = [(r["geography_id"], r["surveillance_stream"], r["analysis_type"]) for r in rows]
        assert len(keys) == len(set(keys)) and rows
        assert {r["analysis_type"] for r in rows} <= {
            "freshness",
            "rolling_average",
            "trend",
            "baseline_comparison",
            "seasonal_comparison",
            "anomaly_detection",
        }

    def test_a_newer_result_replaces_an_older_one(self, loaded):
        before = anon(
            loaded, q.LATEST_ANALYSES, {**NO_FILTERS, "country": "PK", "stream": "sentinel"}
        )
        old = next(r for r in before if r["analysis_type"] == "anomaly_detection")
        loaded.script(
            "insert into public.analysis_results (geography_id, disease, indicator, "
            "surveillance_stream, analysis_type, analysis_period_start, analysis_period_end, "
            "status, methodology_version, input_hash) "
            f"values ('{old['geography_id']}', 'influenza', 'influenza_positivity', 'sentinel', "
            "'anomaly_detection', '2030-01-07', '2030-01-13', 'normal', '1.0.0', 'newer');"
        )
        after = anon(
            loaded, q.LATEST_ANALYSES, {**NO_FILTERS, "country": "PK", "stream": "sentinel"}
        )
        new = next(r for r in after if r["analysis_type"] == "anomaly_detection")
        assert new["period_end"] == "2030-01-13" and old["period_end"] != new["period_end"]

    def test_results_under_review_or_rejected_are_invisible_to_the_public(self, loaded):
        loaded.script("update public.analysis_results set review_status = 'pending_review';")
        assert anon(loaded, q.LATEST_ANALYSES, NO_FILTERS) == []
        loaded.script("update public.analysis_results set review_status = 'approved';")
        assert anon(loaded, q.LATEST_ANALYSES, NO_FILTERS)
        loaded.script("update public.analysis_results set review_status = 'rejected';")
        assert anon(loaded, q.LATEST_ANALYSES, NO_FILTERS) == []


class TestSources:
    def test_public_can_list_sources_with_terms_status(self, pg):
        rows = anon(pg, q.SOURCES)
        flunet = next(r for r in rows if r["slug"] == "who_flunet")
        assert (flunet["enabled"], flunet["terms_verified"]) == ("f", "f")

    def test_the_public_cannot_read_run_status_or_runs(self, loaded):
        with pytest.raises(PsqlError, match="permission denied"):
            anon(loaded, q.SOURCE_STATUS)
        with pytest.raises(PsqlError, match="permission denied"):
            anon(
                loaded,
                q.SOURCE_RUNS,
                {"source_id": "00000000-0000-0000-0000-000000000000", "limit": 5},
            )

    def test_service_role_sees_freshness_and_runs(self, loaded):
        status = loaded.execute(q.SOURCE_STATUS, role="service_role")
        flunet = next(r for r in status if r["slug"] == "who_flunet")
        assert flunet["last_run_status"] == "succeeded"
        assert flunet["latest_reporting_period_end"] == "2024-03-03"
        sid = loaded.scalar("select id from public.data_sources where slug = 'who_flunet'")
        runs = loaded.execute(q.SOURCE_RUNS, {"source_id": sid, "limit": 5}, role="service_role")
        assert len(runs) == 1 and runs[0]["status"] == "succeeded"


class TestRealRowsThroughTheShapers:
    """Rows exactly as the database returns them, through the response builders."""

    TODAY = date(2024, 3, 14)

    def test_latest(self, loaded):
        obs = typed(anon(loaded, q.LATEST_OBSERVATIONS, {**NO_FILTERS, "country": "PK"}))
        ana = typed(anon(loaded, q.LATEST_ANALYSES, {**NO_FILTERS, "country": "PK"}))
        out = svc.build_latest(obs, ana, today=self.TODAY, now=NOW)
        pos = next(
            d
            for d in out.data
            if d.series.indicator == "influenza_positivity"
            and d.series.surveillance_stream == "sentinel"
        )
        assert pos.latest.value is not None and pos.freshness.data_age_days == 11
        assert pos.signal in {"normal", "insufficient_data", "elevated", "possible_anomaly"}
        assert "National-level" in pos.series.resolution_note

    def test_trends_alerts_and_observations(self, loaded):
        params = {**NO_FILTERS, **RANGE, "indicator": "influenza_positivity", "limit": 520}
        pts = typed(anon(loaded, q.SERIES_POINTS, params))
        ana = typed(anon(loaded, q.LATEST_ANALYSES, NO_FILTERS))
        trends = svc.build_trends(pts, ana, today=self.TODAY, now=NOW)
        assert trends.data and all(t.points for t in trends.data)
        svc.build_alerts(ana, today=self.TODAY, now=NOW)  # must not raise on real rows
        obs = typed(anon(loaded, q.OBSERVATIONS, {**NO_FILTERS, **RANGE, "limit": 5, "offset": 0}))
        assert svc.build_observations(obs, limit=5, offset=0, now=NOW).page.total == 43

    def test_comparison_on_real_rows(self, loaded):
        rows = typed(
            anon(
                loaded,
                q.COMPARISON_POINTS,
                {
                    "indicator": "influenza_positivity",
                    "stream": "sentinel",
                    "countries": json.dumps(["PK", "US"]),
                    **RANGE,
                    "limit": 1000,
                },
            )
        )
        out = svc.build_comparison(
            rows,
            indicator="influenza_positivity",
            stream="sentinel",
            countries=["PK", "US"],
            now=NOW,
        )
        assert {c.country_code for c in out.countries} <= {"PK", "US"} and out.weeks
