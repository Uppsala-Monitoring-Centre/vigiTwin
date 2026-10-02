"""Tests for the Wilson confidence-interval primitives."""

from __future__ import annotations

import math

import pytest

from llm_dup_dev.study_eval.stats import estimate, wilson_interval


def test_wilson_matches_reference_values():
    # Reference values from statsmodels proportion_confint(method="wilson").
    low, high = wilson_interval(45, 50)
    assert low == pytest.approx(0.7856, abs=1e-3)
    assert high == pytest.approx(0.9569, abs=1e-3)

    low, high = wilson_interval(8, 10)
    assert low == pytest.approx(0.4901, abs=1e-3)
    assert high == pytest.approx(0.9433, abs=1e-3)


def test_wilson_boundaries():
    # All successes: upper bound is exactly 1, lower bound below 1.
    low, high = wilson_interval(50, 50)
    assert high == 1.0
    assert 0.9 < low < 1.0
    # No successes: lower bound exactly 0.
    low, high = wilson_interval(0, 50)
    assert low == 0.0
    assert 0.0 < high < 0.1


def test_wilson_interval_contains_point_estimate():
    for k, n in [(1, 3), (7, 20), (99, 100), (13, 50)]:
        low, high = wilson_interval(k, n)
        assert low <= k / n <= high
        assert 0.0 <= low <= high <= 1.0


def test_wilson_zero_trials_returns_unit_interval():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_rejects_bad_inputs():
    with pytest.raises(ValueError):
        wilson_interval(5, 3)
    with pytest.raises(ValueError):
        wilson_interval(-1, 3)


def test_estimate_zero_denominator_is_undefined_not_error():
    est = estimate("x", 0, 0)
    assert est.proportion is None
    assert est.ci_low is None and est.ci_high is None
    assert est.denominator == 0


def test_estimate_carries_method_and_level():
    est = estimate("x", 3, 10)
    assert est.ci_method == "wilson"
    assert est.ci_level == 0.95
    assert est.proportion == pytest.approx(0.3)
    assert not math.isnan(est.ci_low)


# --------------------------------------------------------------------------
# Confidence levels other than the prespecified 95%.
#
# `_z_for_level` falls back to Acklam's rational approximation to the inverse
# normal CDF for any level but 0.95. The study only ever asks for 0.95, so
# until these tests existed that approximation — forty lines of hard-coded
# coefficients — had never been executed at all. It is exercised here against
# `statistics.NormalDist` as an independent stdlib oracle.
# --------------------------------------------------------------------------

_LEVELS = [0.50, 0.68, 0.80, 0.90, 0.95, 0.975, 0.98, 0.99, 0.995, 0.999, 0.9999]


@pytest.mark.parametrize("level", _LEVELS)
def test_z_for_level_matches_stdlib_inverse_normal(level):
    """Covers the central branch and both tail branches of the approximation."""
    from statistics import NormalDist

    from llm_dup_dev.study_eval.stats import _z_for_level

    expected = NormalDist().inv_cdf(1 - (1 - level) / 2)
    # The docstring claims ~1e-4; the observed worst case across these levels
    # is ~3e-9. Asserted at 1e-6 — far tighter than the documented promise, so
    # a regression is caught, but loose enough not to pin coefficient noise.
    assert _z_for_level(level) == pytest.approx(expected, abs=1e-6)


def test_z_for_level_returns_the_exact_constant_at_95_percent():
    """The prespecified level must not go through the approximation at all."""
    from llm_dup_dev.study_eval.stats import _Z_95, _z_for_level

    assert _z_for_level(0.95) == _Z_95
    assert _z_for_level(0.95 + 1e-12) == _Z_95  # within the documented tolerance


def test_z_for_level_is_monotonic():
    zs = [_z(level) for level in _LEVELS]
    assert zs == sorted(zs), "a higher confidence level must not give a smaller z"


def _z(level):
    from llm_dup_dev.study_eval.stats import _z_for_level

    return _z_for_level(level)


@pytest.mark.parametrize("level", [0.80, 0.90, 0.99])
def test_wilson_level_is_actually_plumbed_through(level):
    """A non-default level must change the interval, not be silently ignored."""
    narrow = wilson_interval(30, 50, level=0.80)
    wide = wilson_interval(30, 50, level=0.99)
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])

    low, high = wilson_interval(30, 50, level=level)
    assert 0.0 <= low <= 30 / 50 <= high <= 1.0


@pytest.mark.parametrize("level", _LEVELS)
def test_wilson_stays_within_the_unit_interval_at_every_level(level):
    """Boundary counts are where a bad z most easily escapes [0, 1]."""
    for k, n in ((0, 1), (1, 1), (0, 10), (10, 10), (1, 1000), (999, 1000)):
        low, high = wilson_interval(k, n, level=level)
        assert 0.0 <= low <= high <= 1.0, f"k={k} n={n} level={level}"
