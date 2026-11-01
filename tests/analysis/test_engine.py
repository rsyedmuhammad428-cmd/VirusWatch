from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.analysis.engine import UnsupportedSeriesError, analyze_series
from app.analysis.models import (
    METHODOLOGY_VERSION,
    AnalysisConfig,
    IndicatorRule,
    Point,
    SeriesKey,
)

KEY = SeriesKey("geo-1", "PK", "influenza", None, "influenza_positivity", "sentinel", "percent")
START = date(2024, 1, 1)  # a Monday
SMALL = "small_denominator"


def series(values, *, start=START, flags=None):
    """Consecutive weekly points; None = a blank (missing) week; flags = {index: (flag, ...)}."""
    flags = flags or {}
    pts = []
    for i, v in enumerate(values):
        s = start + timedelta(weeks=i)
        pts.append(
            Point(
                observation_id=f"o{i}",
                period_start=s,
                period_end=s + timedelta(days=6),
                value=None if v is None else Decimal(str(v)),
                data_status="missing" if v is None else "reported",
                flags=tuple(flags.get(i, ())),
            )
        )
    return pts


def as_of_for(points, days=7):
    return points[-1].period_end + timedelta(days=days)


def run(values, *, cfg=None, as_of=None, key=KEY, flags=None):
    pts = series(values, flags=flags)
    return {
        r.analysis_type: r
        for r in analyze_series(key, pts, cfg or AnalysisConfig(), as_of or as_of_for(pts))
    }


FLAT_THEN_JUMP = [10] * 8 + [30]


# ---------------------------------------------------------------------------- applicability
class TestApplicability:
    def test_six_analyses_in_a_fixed_order(self):
        pts = series(FLAT_THEN_JUMP)
        results = analyze_series(KEY, pts, AnalysisConfig(), as_of_for(pts))
        assert [r.analysis_type for r in results] == [
            "freshness",
            "rolling_average",
            "trend",
            "baseline_comparison",
            "seasonal_comparison",
            "anomaly_detection",
        ]
        assert {r.methodology_version for r in results} == {METHODOLOGY_VERSION}

    def test_indicator_without_a_rule_is_not_analysed(self):
        other = replace(KEY, indicator="influenza_positive_count")
        assert analyze_series(other, series(FLAT_THEN_JUMP), AnalysisConfig(), START) == []

    def test_empty_series_gives_nothing(self):
        assert analyze_series(KEY, [], AnalysisConfig(), START) == []

    def test_non_weekly_periods_are_refused(self):
        bad = [Point("x", date(2024, 1, 1), date(2024, 1, 31), Decimal(5), "reported")]
        with pytest.raises(UnsupportedSeriesError, match="weekly"):
            analyze_series(KEY, bad, AnalysisConfig(), START)

    def test_duplicate_periods_are_refused(self):
        pts = series([1, 2])
        with pytest.raises(UnsupportedSeriesError, match="duplicate"):
            analyze_series(
                KEY, [*pts, replace(pts[0], observation_id="dup")], AnalysisConfig(), START
            )

    def test_input_order_does_not_matter(self):
        pts = series(FLAT_THEN_JUMP)
        a = analyze_series(KEY, pts, AnalysisConfig(), as_of_for(pts))
        b = analyze_series(KEY, list(reversed(pts)), AnalysisConfig(), as_of_for(pts))
        assert a == b


# ---------------------------------------------------------------------------- freshness
class TestFreshness:
    @pytest.mark.parametrize(
        ("days_after_week_end", "status"),
        [(7, "normal"), (28, "normal"), (29, "stale_data"), (400, "stale_data")],
    )
    def test_boundary_is_inclusive(self, days_after_week_end, status):
        pts = series(FLAT_THEN_JUMP)
        r = run(FLAT_THEN_JUMP, as_of=pts[-1].period_end + timedelta(days=days_after_week_end))
        assert r["freshness"].status == status
        assert r["freshness"].observed_value == days_after_week_end

    def test_age_is_measured_from_the_latest_reported_week_not_a_blank_one(self):
        values = [5, 6, 7, None]  # newest week blank
        pts = series(values)
        r = run(values, as_of=pts[2].period_end + timedelta(days=10))
        assert r["freshness"].observed_value == 10
        assert r["freshness"].input_observation_ids == ("o2",)

    def test_nothing_reported_is_insufficient_data(self):
        assert run([None, None], as_of=date(2024, 2, 1))["freshness"].status == "insufficient_data"


# ---------------------------------------------------------------------------- rolling average
class TestRolling:
    def test_mean_of_the_window(self):
        r = run([1, 2, 3, 4, 10, 20, 30, 40])["rolling_average"]
        assert r.observed_value == 25  # last 4 weeks
        assert r.status == "normal"

    def test_blank_weeks_are_skipped_not_counted_as_zero(self):
        r = run([5, 5, 5, 10, None, 20, 10])["rolling_average"]
        # window = last 4 weeks = 10, None, 20, 10 -> mean of the 3 reported, NOT (40/4)
        assert r.observed_value == pytest.approx(13.333333)

    def test_too_few_reported_weeks_gives_no_average(self):
        r = run([5, 5, None, None, 20, None])["rolling_average"]
        assert r.status == "insufficient_data" and r.observed_value is None

    def test_small_sample_weeks_are_excluded(self):
        r = run([10, 10, 10, 90], flags={3: (SMALL,)})["rolling_average"]
        assert r.observed_value == 10
        assert "o3" not in r.input_observation_ids


# ---------------------------------------------------------------------------- trend
class TestTrend:
    def test_clear_increase(self):
        r = run([1, 2, 3, 4, 5, 6, 7, 8])["trend"]
        assert r.confidence_or_uncertainty["direction"] == "increasing"
        assert r.effect_size == pytest.approx(1.0)
        assert r.score == pytest.approx(3.3404, abs=1e-3)  # Mann-Kendall z, hand-checked
        assert r.status == "normal"

    def test_clear_decrease(self):
        r = run([8, 7, 6, 5, 4, 3, 2, 1])["trend"]
        assert r.confidence_or_uncertainty["direction"] == "decreasing"
        assert r.effect_size == pytest.approx(-1.0)

    def test_noise_is_not_called_a_trend(self):
        r = run([5, 9, 4, 9, 5, 8, 4, 9])["trend"]
        assert r.confidence_or_uncertainty["direction"] == "no_clear_trend"

    def test_flat_series(self):
        r = run([7] * 8)["trend"]
        assert r.confidence_or_uncertainty["direction"] == "no_clear_trend" and r.effect_size == 0

    def test_missing_weeks_leave_a_gap_in_time(self):
        # y = 2 * week_index, but weeks 2 and 4 are blank
        r = run([0, 2, None, 6, None, 10, 12, 14])["trend"]
        assert r.effect_size == pytest.approx(2.0)

    def test_too_few_points(self):
        assert run([1, 2, 3, None, None, 6, None, None])["trend"].status == "insufficient_data"


# ---------------------------------------------------------------------------- anomaly detection
class TestAnomaly:
    def test_large_jump_over_a_flat_baseline_is_a_possible_anomaly(self):
        r = run(FLAT_THEN_JUMP)
        a = r["anomaly_detection"]
        assert a.status == "possible_anomaly"
        assert (a.observed_value, a.baseline_value, a.effect_size) == (30, 10, 20)
        assert a.score == 20  # (30-10) / floor scale 1.0
        assert a.confidence_or_uncertainty["baseline_kind"] == "trailing"
        # the comparison rows never go beyond 'elevated': only anomaly_detection can say more
        assert r["baseline_comparison"].status == "elevated"

    @pytest.mark.parametrize(
        ("current", "expected"),
        [
            (11.99, "normal"),  # below both thresholds
            (12.0, "elevated"),  # z = 2.0 and rise = 2.0 points: both inclusive
            (13.49, "elevated"),
            (13.5, "possible_anomaly"),  # z = 3.5
            (5, "normal"),  # a fall is never flagged
        ],
    )
    def test_threshold_boundaries(self, current, expected):
        assert run([10] * 8 + [current])["anomaly_detection"].status == expected

    def test_a_fall_has_a_negative_score(self):
        assert run([10] * 8 + [5])["anomaly_detection"].score < 0

    def test_practical_significance_is_required_as_well_as_statistical(self):
        tight = replace(
            AnalysisConfig(),
            rules={KEY.indicator: IndicatorRule(min_abs_increase=2.0, min_scale=0.1)},
        )
        a = run([10] * 8 + [11], cfg=tight)["anomaly_detection"]
        assert a.score == pytest.approx(10.0)  # statistically huge ...
        assert a.status == "normal"  # ... but a 1-point rise is below the 2-point minimum

    def test_a_noisy_baseline_demands_a_bigger_jump(self):
        a = run([5, 15, 5, 15, 5, 15, 5, 15, 20])["anomaly_detection"]
        assert a.status == "normal" and a.score < 2

    def test_latest_week_blank_is_insufficient_not_zero(self):
        a = run([10] * 8 + [None])["anomaly_detection"]
        assert a.status == "insufficient_data" and a.observed_value is None
        assert "blank" in a.explanation

    def test_a_tiny_sample_can_never_raise_an_alert(self):
        a = run([10] * 8 + [90], flags={8: (SMALL,)})["anomaly_detection"]
        assert a.status == "insufficient_data" and "small sample" in a.explanation

    def test_small_sample_weeks_do_not_enter_the_baseline(self):
        values = [10, 10, 10, 10, 10, 10, 90, 90, 30]
        a = run(values, flags={6: (SMALL,), 7: (SMALL,)})["anomaly_detection"]
        assert a.baseline_value == 10 and a.status == "possible_anomaly"
        assert not {"o6", "o7"} & set(a.input_observation_ids)

    def test_stale_data_is_reported_as_stale_not_assessed(self):
        pts = series(FLAT_THEN_JUMP)
        a = run(FLAT_THEN_JUMP, as_of=pts[-1].period_end + timedelta(days=60))["anomaly_detection"]
        assert a.status == "stale_data"

    def test_not_enough_history_is_insufficient(self):
        a = run([10] * 5 + [30])["anomaly_detection"]
        assert a.status == "insufficient_data" and "baseline" in a.explanation

    def test_gaps_in_the_baseline_reduce_the_point_count_but_are_not_zeros(self):
        values = [10, None, 10, None, 10, 10, 10, 10, 30]
        a = run(values)["anomaly_detection"]
        assert a.baseline_value == 10  # zeros would have dragged the median down
        assert a.confidence_or_uncertainty["n_baseline_points"] == 6

    def test_too_many_gaps_leave_no_baseline(self):
        assert run([10, None, 10, None, 10, None, 10, None, 30])["anomaly_detection"].status == (
            "insufficient_data"
        )


# ---------------------------------------------------------------------------- seasonality
def seasonal_values(years: int, *, peak=25, base=5, peak_weeks=range(8, 13)):
    """`years` full 52-week years of a winter peak, then 10 weeks of the next year."""
    return [peak if (i % 52) in peak_weeks else base for i in range(52 * years + 10)]


class TestSeasonal:
    def test_a_normal_winter_peak_is_not_flagged_when_history_shows_winter_peaks(self):
        values = seasonal_values(4)  # current week = index 52*4+9: inside the usual peak
        r = run(values)
        a = r["anomaly_detection"]
        assert a.confidence_or_uncertainty["baseline_kind"] == "seasonal"
        assert a.confidence_or_uncertainty["n_baseline_years"] == 4
        assert a.status == "normal"
        # ...while a trailing-only view would have called the same week elevated
        assert r["baseline_comparison"].status == "elevated"
        assert r["seasonal_comparison"].status == "normal"

    def test_an_off_season_spike_is_flagged_against_the_seasonal_baseline(self):
        values = seasonal_values(4)
        values.extend([5] * 20)
        values[-1] = 25  # week 29 of the year: summer, where every prior year shows ~5
        a = run(values)["anomaly_detection"]
        assert a.confidence_or_uncertainty["baseline_kind"] == "seasonal"
        assert a.status == "possible_anomaly"

    def test_short_history_falls_back_to_the_trailing_baseline(self):
        a = run(seasonal_values(2))["anomaly_detection"]
        assert a.confidence_or_uncertainty["baseline_kind"] == "trailing"

    def test_seasonal_comparison_explains_when_history_is_too_short(self):
        s = run(seasonal_values(2))["seasonal_comparison"]
        assert s.status == "insufficient_data" and "prior years" in s.explanation

    def test_excluded_date_ranges_are_left_out_of_the_baseline(self):
        values = seasonal_values(4)
        first_year_end = START + timedelta(weeks=52)
        cfg = replace(AnalysisConfig(), exclude_ranges=((START, first_year_end),))
        a = run(values, cfg=cfg)["anomaly_detection"]
        # 4 prior years minus the excluded first year leaves 3, still enough
        assert a.confidence_or_uncertainty["n_baseline_years"] == 3

    def test_excluding_too_much_history_disables_the_seasonal_baseline(self):
        values = seasonal_values(4)
        cfg = replace(AnalysisConfig(), exclude_ranges=((START, START + timedelta(weeks=110)),))
        a = run(values, cfg=cfg)["anomaly_detection"]
        assert a.confidence_or_uncertainty["baseline_kind"] == "trailing"


# ---------------------------------------------------------------------------- reproducibility
class TestReproducibility:
    def hashes(self, values, **kw):
        return {k: v.input_hash for k, v in run(values, **kw).items()}

    def test_same_inputs_give_identical_results_and_hashes(self):
        assert run(FLAT_THEN_JUMP) == run(FLAT_THEN_JUMP)
        assert self.hashes(FLAT_THEN_JUMP) == self.hashes(FLAT_THEN_JUMP)

    def test_a_revised_value_changes_the_hash_of_what_it_feeds(self):
        before = self.hashes(FLAT_THEN_JUMP)
        after = self.hashes([10] * 8 + [31])
        assert before["anomaly_detection"] != after["anomaly_detection"]
        assert before["rolling_average"] != after["rolling_average"]

    def test_a_revised_value_outside_a_window_does_not_change_that_windows_hash(self):
        a = self.hashes([1] + [10] * 8 + [30])
        b = self.hashes([2] + [10] * 8 + [30])  # only the oldest week differs
        assert a["rolling_average"] == b["rolling_average"]
        assert a["trend"] == b["trend"]

    def test_the_date_of_the_run_only_matters_for_freshness(self):
        pts = series(FLAT_THEN_JUMP)
        h1 = self.hashes(FLAT_THEN_JUMP, as_of=pts[-1].period_end + timedelta(days=3))
        h2 = self.hashes(FLAT_THEN_JUMP, as_of=pts[-1].period_end + timedelta(days=9))
        assert h1["freshness"] != h2["freshness"]
        assert {k: v for k, v in h1.items() if k != "freshness"} == {
            k: v for k, v in h2.items() if k != "freshness"
        }

    def test_a_threshold_change_changes_every_hash(self):
        a = self.hashes(FLAT_THEN_JUMP)
        b = self.hashes(FLAT_THEN_JUMP, cfg=replace(AnalysisConfig(), anomaly_z=4.0))
        assert all(a[k] != b[k] for k in a)

    def test_different_series_never_share_a_hash(self):
        other = replace(KEY, surveillance_stream="nonsentinel")
        assert (
            self.hashes(FLAT_THEN_JUMP)["anomaly_detection"]
            != self.hashes(FLAT_THEN_JUMP, key=other)["anomaly_detection"]
        )

    def test_parameters_are_stored_with_each_result(self):
        r = run(FLAT_THEN_JUMP)["anomaly_detection"]
        assert r.parameters == AnalysisConfig().parameters()
        assert r.parameters["rules"]["influenza_positivity"]["min_abs_increase"] == 2.0


# ---------------------------------------------------------------------------- safety properties
class TestSafety:
    SCENARIOS = {
        "jump": FLAT_THEN_JUMP,
        "flat": [10] * 9,
        "blank_latest": [10] * 8 + [None],
        "short": [1, 2, 3],
        "noisy": [5, 15, 5, 15, 5, 15, 5, 15, 40],
    }

    @pytest.mark.parametrize("name", SCENARIOS)
    def test_no_result_claims_confirmation_or_an_outbreak(self, name):
        for r in run(self.SCENARIOS[name]).values():
            assert r.status != "confirmed_by_source"
            text = r.explanation.lower()
            assert not re.search(r"outbreak (is|has|detected|declared|confirmed)", text)
            assert "confirmed" not in text.replace("does not confirm", "")
            if r.status in {"elevated", "possible_anomaly"}:
                assert "does not confirm an outbreak" in text

    @pytest.mark.parametrize("name", SCENARIOS)
    def test_every_result_documents_missing_data_handling_and_limits(self, name):
        for r in run(self.SCENARIOS[name]).values():
            assert "never treated as zero" in r.missing_data_handling
            assert r.limitations and r.input_hash and r.explanation

    def test_inputs_are_traceable_to_the_series_and_include_the_current_week(self):
        a = run(FLAT_THEN_JUMP)["anomaly_detection"]
        ids = {f"o{i}" for i in range(9)}
        assert set(a.input_observation_ids) <= ids and "o8" in a.input_observation_ids
        assert len(a.input_observation_ids) == len(set(a.input_observation_ids))

    def test_results_describe_the_latest_week(self):
        pts = series(FLAT_THEN_JUMP)
        a = run(FLAT_THEN_JUMP)["anomaly_detection"]
        assert (a.period_start, a.period_end) == (pts[-1].period_start, pts[-1].period_end)
        assert a.latest_observation_end == pts[-1].period_end
        assert a.baseline_start == pts[0].period_start and a.baseline_end == pts[-2].period_end

    def test_numbers_are_rounded_for_stable_storage(self):
        r = run([1, 2, 4, 7, 11, 16, 22, 29, 37])["rolling_average"]
        assert r.observed_value == round(r.observed_value, 6)


class TestConfigValidation:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"elevated_z": 4.0, "anomaly_z": 3.5},
            {"elevated_z": 0},
            {"baseline_min_points": 9, "baseline_window_weeks": 8},
            {"trend_min_points": 9},
            {"rolling_min_points": 5},
            {"seasonal_min_years": 6},
            {"trend_alpha": 1.0},
        ],
    )
    def test_inconsistent_settings_are_rejected(self, kwargs):
        with pytest.raises(ValueError):
            AnalysisConfig(**kwargs)
