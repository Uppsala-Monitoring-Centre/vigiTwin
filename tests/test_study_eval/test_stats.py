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
