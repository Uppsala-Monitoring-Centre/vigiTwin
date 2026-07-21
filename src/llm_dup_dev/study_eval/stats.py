"""Proportions with two-sided confidence intervals — stdlib only.

The protocol requires "counts and proportions with two-sided 95% confidence
intervals" but does not name a method. This package prespecifies the **Wilson
score interval**: it is closed-form (no scipy), well-behaved at the small
sample sizes here (n ≈ 50–100) and near the boundaries 0 and 1 where several of
the study's proportions will sit, and is the interval recommended over both the
Wald and Clopper–Pearson intervals by Brown, Cai & DasGupta (2001) for this
regime. The method name is recorded on every :class:`EstimateWithCI` so a
downstream change of method is auditable, and the computation is isolated here
so such a change touches one function.
"""

from __future__ import annotations

import math

from pydantic import BaseModel

#: z for a two-sided 95% interval (standard normal 0.975 quantile). Hard-coded
#: rather than computed so the module needs no statistics dependency.
_Z_95 = 1.959963984540054


def _z_for_level(level: float) -> float:
    """Return the two-sided z multiplier for ``level``.

    Only the 95% level is needed by the study and is returned exactly; other
    levels fall back to an inverse-normal approximation good to ~1e-4, which is
    ample for reporting. Kept internal so callers stay on the prespecified 95%.
    """
    if abs(level - 0.95) < 1e-9:
        return _Z_95
    # Acklam's rational approximation to the inverse standard normal CDF.
    p = 1.0 - (1.0 - level) / 2.0
    a = [
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    ]
    b = [
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    ]
    c = [
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    ]
    d = [
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    ]
    p_low = 0.02425
    p_high = 1 - p_low
    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p <= p_high:
        q = p - 0.5
        r = q * q
        return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
            ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1
        )
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
        (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
    )


def wilson_interval(k: int, n: int, level: float = 0.95) -> tuple[float, float]:
    """Two-sided Wilson score interval for a binomial proportion ``k / n``.

    Parameters
    ----------
    k : int
        Number of successes (``0 <= k <= n``).
    n : int
        Number of trials.
    level : float
        Confidence level (default 0.95).

    Returns
    -------
    tuple of float
        ``(low, high)``, both clamped to ``[0, 1]``. For ``n == 0`` the
        interval is undefined; ``(0.0, 1.0)`` is returned by convention.
    """
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    if not 0 <= k <= n:
        raise ValueError(f"k must satisfy 0 <= k <= n, got k={k}, n={n}")
    if n == 0:
        return (0.0, 1.0)

    z = _z_for_level(level)
    phat = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (phat + z2 / (2 * n)) / denom
    half = (z / denom) * math.sqrt(phat * (1 - phat) / n + z2 / (4 * n * n))
    low = max(0.0, centre - half)
    high = min(1.0, centre + half)
    return (low, high)


class EstimateWithCI(BaseModel):
    """A proportion reported as count, proportion, and confidence interval.

    ``proportion`` and the interval bounds are ``None`` only when ``denominator``
    is 0 (the estimate is undefined). ``ci_method`` and ``ci_level`` are carried
    on every estimate so a results table is self-describing.
    """

    name: str
    numerator: int
    denominator: int
    proportion: float | None
    ci_low: float | None
    ci_high: float | None
    ci_method: str = "wilson"
    ci_level: float = 0.95


def estimate(name: str, k: int, n: int, *, level: float = 0.95) -> EstimateWithCI:
    """Build an :class:`EstimateWithCI` for ``k`` successes out of ``n``.

    A zero denominator yields a defined record with ``None`` proportion and
    bounds, rather than raising — undefined estimates are expected (e.g.
    precision when the adjudicator classifies nothing positive) and should be
    reported as undefined, not omitted.
    """
    if n == 0:
        return EstimateWithCI(
            name=name,
            numerator=k,
            denominator=0,
            proportion=None,
            ci_low=None,
            ci_high=None,
            ci_level=level,
        )
    low, high = wilson_interval(k, n, level)
    return EstimateWithCI(
        name=name,
        numerator=k,
        denominator=n,
        proportion=k / n,
        ci_low=low,
        ci_high=high,
        ci_level=level,
    )


__all__ = ["wilson_interval", "EstimateWithCI", "estimate"]
