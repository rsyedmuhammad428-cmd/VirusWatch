from __future__ import annotations

import math

import pytest

from app.analysis import stats


class TestMedianMeanMad:
    def test_median_odd_even(self):
        assert stats.median([3, 1, 2]) == 2
        assert stats.median([4, 1, 3, 2]) == 2.5

    def test_mean(self):
        assert stats.mean([1, 2, 6]) == 3

    def test_empty_inputs_are_errors_not_zero(self):
        for fn in (stats.median, stats.mean):
            with pytest.raises(ValueError):
                fn([])

    def test_mad_of_known_data(self):
        # median 2; deviations 1,0,0,1,4 -> sorted 0,0,1,1,4 -> median 1
        assert stats.mad([1, 2, 2, 3, 6]) == 1

    def test_mad_ignores_an_outlier_that_would_wreck_the_standard_deviation(self):
        assert stats.mad([10, 10, 11, 10, 1000]) == 0


class TestRobustScale:
    def test_scales_mad_to_sigma(self):
        assert stats.robust_scale([1, 2, 2, 3, 6], floor=0.1) == pytest.approx(1.4826)

    def test_floor_applies_to_flat_data(self):
        assert stats.robust_scale([5, 5, 5, 5], floor=0.7) == 0.7

    def test_floor_must_be_positive(self):
        with pytest.raises(ValueError):
            stats.robust_scale([1, 2, 3], floor=0)


class TestTheilSen:
    def test_exact_line(self):
        assert stats.theil_sen_slope([0, 1, 2, 3], [1, 3, 5, 7]) == 2

    def test_robust_to_one_outlier(self):
        xs = list(range(8))
        ys = [1, 2, 3, 4, 100, 6, 7, 8]
        assert stats.theil_sen_slope(xs, ys) == pytest.approx(1.0)

    def test_gaps_in_x_are_respected(self):
        # y = 2x sampled at x = 0, 1, 4 (weeks 2 and 3 missing): slope must still be 2
        assert stats.theil_sen_slope([0, 1, 4], [0, 2, 8]) == 2

    @pytest.mark.parametrize(
        ("xs", "ys"),
        [([1], [1]), ([1, 2], [1]), ([3, 3], [1, 2])],
        ids=["one", "mismatch", "same-x"],
    )
    def test_invalid_input(self, xs, ys):
        with pytest.raises(ValueError):
            stats.theil_sen_slope(xs, ys)


class TestMannKendall:
    def test_strictly_increasing_matches_hand_calculation(self):
        s, z, p = stats.mann_kendall(list(range(8)))
        assert s == 28
        # var = 8*7*21/18 = 65.333; sd = 8.0829; z = (28-1)/sd
        assert z == pytest.approx(27 / math.sqrt(8 * 7 * 21 / 18))
        assert z == pytest.approx(3.3404, abs=1e-3)
        assert p == pytest.approx(0.00084, abs=1e-4)

    def test_decreasing_has_negative_z(self):
        s, z, _ = stats.mann_kendall(list(range(8, 0, -1)))
        assert s == -28 and z < 0

    def test_constant_series_has_no_trend(self):
        assert stats.mann_kendall([4, 4, 4, 4]) == (0, 0.0, 1.0)

    def test_tie_correction_matches_hand_calculation(self):
        # 28 pairs, 4 tied -> S = 24; var = (8*7*21 - 4 * 2*1*9) / 18 = 61.333; z = 23 / sqrt(var)
        s, z, _ = stats.mann_kendall([1, 1, 2, 2, 3, 3, 4, 4])
        assert s == 24
        assert z == pytest.approx(23 / math.sqrt((8 * 7 * 21 - 4 * 18) / 18))
        assert z == pytest.approx(2.937, abs=1e-3)

    def test_alternating_series_is_not_significant(self):
        assert stats.mann_kendall([1, 5, 1, 5, 1, 5, 1, 5])[2] > 0.1

    def test_needs_three_points(self):
        with pytest.raises(ValueError):
            stats.mann_kendall([1, 2])
