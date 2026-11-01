"""Small, dependency-free statistical primitives.

Everything here is a pure function of its arguments, so the same input always gives the same
output. Implementations are deliberately simple and documented so they can be checked by hand.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from statistics import fmean
from statistics import median as _median

# Consistency constant: makes the MAD estimate the standard deviation for normal data.
MAD_TO_SIGMA = 1.4826


def median(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("median of an empty sequence")
    return float(_median(values))


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("mean of an empty sequence")
    return float(fmean(values))


def mad(values: Sequence[float]) -> float:
    """Median absolute deviation about the median (unscaled)."""
    centre = median(values)
    return median([abs(v - centre) for v in values])


def robust_scale(values: Sequence[float], *, floor: float) -> float:
    """sigma-equivalent spread, never below `floor`.

    A run of near-identical weeks has MAD ~ 0, which would turn any change into an enormous
    z-score. The floor is the smallest spread considered meaningful for the indicator.
    """
    if floor <= 0:
        raise ValueError("floor must be positive")
    return max(MAD_TO_SIGMA * mad(values), floor)


def theil_sen_slope(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Median of all pairwise slopes (y_j - y_i) / (x_j - x_i). Robust to outliers.

    x are explicit positions (week numbers), so a missing week leaves a gap instead of silently
    pulling later points closer together.
    """
    if len(xs) != len(ys):
        raise ValueError("xs and ys differ in length")
    n = len(xs)
    if n < 2:
        raise ValueError("need at least two points")
    slopes = [
        (ys[j] - ys[i]) / (xs[j] - xs[i])
        for i in range(n)
        for j in range(i + 1, n)
        if xs[j] != xs[i]
    ]
    if not slopes:
        raise ValueError("all x positions are identical")
    return median(slopes)


def mann_kendall(ys: Sequence[float]) -> tuple[int, float, float]:
    """Mann-Kendall monotonic-trend test: returns (S, z, two-sided p).

    Normal approximation with tie correction and continuity correction; reliable for n >= 8,
    indicative below that. Callers state the minimum n they require.
    """
    n = len(ys)
    if n < 3:
        raise ValueError("need at least three points")
    s = sum((ys[j] > ys[i]) - (ys[j] < ys[i]) for i in range(n) for j in range(i + 1, n))
    ties = Counter(ys).values()
    variance = (n * (n - 1) * (2 * n + 5) - sum(t * (t - 1) * (2 * t + 5) for t in ties)) / 18
    if variance <= 0:  # every value identical
        return s, 0.0, 1.0
    sd = math.sqrt(variance)
    z = 0.0 if s == 0 else (s - 1) / sd if s > 0 else (s + 1) / sd
    return s, z, math.erfc(abs(z) / math.sqrt(2))
