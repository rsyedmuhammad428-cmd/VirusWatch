from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.analysis.loading import group_series
from app.analysis.models import AnalysisConfig, AnalysisResult, Point, SeriesKey
from app.analysis.ports import StoreOutcome
from app.analysis.service import run_analysis
from tests.analysis.test_engine import FLAT_THEN_JUMP, KEY, START, series


def db_row(i, *, geo="g1", stream="sentinel", indicator="influenza_positivity", value="10", **kw):
    s = START + timedelta(weeks=i)
    return {
        "observation_id": f"{geo}-{stream}-{i}",
        "geography_id": geo,
        "country_code": "PK",
        "disease": "influenza",
        "pathogen": None,
        "indicator": indicator,
        "surveillance_stream": stream,
        "unit": "percent",
        "period_start": s,
        "period_end": s + timedelta(days=6),
        "value": value,
        "data_status": "reported",
        "quality_flags": "[]",
        **kw,
    }


class TestGroupSeries:
    def test_streams_geographies_and_indicators_are_never_merged(self):
        rows = [
            db_row(0),
            db_row(0, stream="nonsentinel"),
            db_row(0, geo="g2"),
            db_row(0, indicator="influenza_positive_count"),
        ]
        assert len(group_series(rows)) == 4

    def test_rows_are_ordered_by_week_within_a_series(self):
        groups = group_series([db_row(2), db_row(0), db_row(1)])
        assert [p.period_start for p in groups[0][1]] == [
            START + timedelta(weeks=i) for i in range(3)
        ]

    def test_blank_values_stay_missing_never_zero(self):
        ((_, pts),) = group_series(
            [db_row(0, value=None, data_status="missing"), db_row(1, value="")]
        )
        assert [p.value for p in pts] == [None, None]

    def test_values_keep_decimal_precision(self):
        ((_, pts),) = group_series([db_row(0, value="26.3158")])
        assert pts[0].value == Decimal("26.3158")

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("[]", ()),
            (None, ()),
            ("", ()),
            ('["small_denominator", "x"]', ("small_denominator", "x")),
            (["a"], ("a",)),
        ],
    )
    def test_quality_flags_parse_from_json_text_or_lists(self, raw, expected):
        ((_, pts),) = group_series([db_row(0, quality_flags=raw)])
        assert pts[0].flags == expected

    def test_iso_date_strings_are_accepted(self):
        ((_, pts),) = group_series([db_row(0, period_start="2024-01-01", period_end="2024-01-07")])
        assert pts[0].period_start == date(2024, 1, 1)

    def test_output_order_is_deterministic(self):
        rows = [db_row(0, geo="g2"), db_row(0, geo="g1"), db_row(0, geo="g1", stream="nonsentinel")]
        assert group_series(rows) == group_series(list(reversed(rows)))


class FakeRepo:
    def __init__(self, series_list, *, already=0):
        self.series_list, self.already = series_list, already
        self.requested: list[str] | None = None
        self.stored: list[AnalysisResult] = []

    async def load_series(self, indicators):
        self.requested = list(indicators)
        return self.series_list

    async def store_results(self, results):
        self.stored = list(results)
        return StoreOutcome(submitted=len(results), inserted=len(results) - self.already)


def go(repo, *, dry_run=False, cfg=None, as_of=None):
    pts = series(FLAT_THEN_JUMP)
    return asyncio.run(
        run_analysis(
            repo,
            cfg or AnalysisConfig(),
            as_of or pts[-1].period_end + timedelta(days=7),
            dry_run=dry_run,
        )
    )


class TestService:
    def test_analyses_every_series_and_stores_all_results(self):
        repo = FakeRepo(
            [
                (KEY, series(FLAT_THEN_JUMP)),
                (replace(KEY, surveillance_stream="nonsentinel"), series([10] * 9)),
            ]
        )
        s = go(repo)
        assert (s.series_found, s.series_analysed, s.results_submitted, s.results_inserted) == (
            2,
            2,
            12,
            12,
        )
        assert s.status_counts == {"normal": 1, "possible_anomaly": 1}
        assert s.flagged == [
            "possible_anomaly: PK influenza influenza_positivity/sentinel week 2024-02-26"
        ]
        assert len(repo.stored) == 12

    def test_only_indicators_with_a_rule_are_requested(self):
        repo = FakeRepo([])
        go(repo)
        assert repo.requested == ["influenza_positivity"]

    def test_rerunning_reports_what_was_already_stored(self):
        s = go(FakeRepo([(KEY, series(FLAT_THEN_JUMP))], already=6))
        assert (s.results_submitted, s.results_inserted, s.results_already_stored) == (6, 0, 6)

    def test_dry_run_computes_but_stores_nothing(self):
        repo = FakeRepo([(KEY, series(FLAT_THEN_JUMP))])
        s = go(repo, dry_run=True)
        assert s.dry_run and s.results_submitted == 6 and s.results_inserted == 0
        assert repo.stored == [] and s.flagged

    def test_an_unsupported_series_is_reported_and_does_not_stop_the_others(self):
        monthly = [Point("m", date(2024, 1, 1), date(2024, 1, 31), Decimal(5), "reported")]
        repo = FakeRepo(
            [
                (replace(KEY, geography_id="bad", country_code="XX"), monthly),
                (KEY, series(FLAT_THEN_JUMP)),
            ]
        )
        s = go(repo)
        assert s.series_analysed == 1 and len(s.series_skipped) == 1
        assert s.series_skipped[0].startswith("XX influenza influenza_positivity: only weekly")
        assert s.results_submitted == 6

    def test_stale_series_is_counted_as_stale_not_flagged(self):
        pts = series(FLAT_THEN_JUMP)
        s = go(FakeRepo([(KEY, pts)]), as_of=pts[-1].period_end + timedelta(days=90))
        assert s.status_counts == {"stale_data": 1} and s.flagged == []

    def test_nothing_to_analyse_stores_nothing(self):
        repo = FakeRepo([])
        s = go(repo)
        assert s.series_found == 0 and s.results_submitted == 0 and repo.stored == []

    def test_a_series_without_a_rule_is_not_counted_as_analysed(self):
        other = SeriesKey(
            "g", "PK", "influenza", None, "influenza_positive_count", "sentinel", "count"
        )
        s = go(FakeRepo([(other, series([1] * 9))]))
        assert s.series_found == 1 and s.series_analysed == 0
