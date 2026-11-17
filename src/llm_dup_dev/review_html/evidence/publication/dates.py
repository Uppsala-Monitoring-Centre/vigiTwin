"""Precision-aware date shifting, and the candidate offsets to try.

A single whole-day offset applies to a pair. For complete dates that is the
whole story: the shift is exact, so equal dates stay equal, order is kept and
every interval is preserved to the day.

Partial dates are the hard case, and the reason this module exists. A
year-only date cannot literally be moved thirty days and still be written as
a calendar year, so the shift is defined on the *timeline* and the result is
written back at the date's **original precision**:

.. code-block:: text

    shift(d, delta) = truncate(earliest_plausible_date(d) + delta, resolution(d))

* ``day``   — ``2023-11-13`` + 30 → ``2023-12-13``; an exact shift.
* ``month`` — the first of the month, shifted, truncated: ``2023-11`` → ``2023-12``.
* ``year``  — the first of January, shifted, truncated: ``2023`` + 30 →
  ``2023-01-31`` → ``2023``, which is why a year-only drug start can sit
  unchanged beside an onset that moved by a month.

The rule is deterministic and monotone, and it never changes a date's
precision. What it does **not** guarantee is that the evidential meaning
survives — shifting a year-only date's anchor moves its plausible interval,
which can change a separation the page states something about. That is
checked, offset by offset, in :mod:`.invariants`, where a separation has to
keep its severity band and stay within a tolerance rather than keep its exact
arithmetic, and an offset that cannot is rejected rather than published. This
module makes no claim beyond representability.

Nothing here requires a date to move. An offset of zero is a candidate like
any other, and so is a whole number of years: see
:func:`offset_candidates`.

The interval primitives are reused from
:mod:`llm_dup_dev.feature_generation.clinical_date_alignment` rather than
reimplemented, so the publication layer measures dates exactly the way the
pipeline measured them.
"""

from __future__ import annotations

import random
import re
from datetime import date, timedelta
from typing import Literal

from llm_dup_dev.feature_generation.clinical_date_alignment import (
    earliest_plausible_date,
    latest_plausible_date,
)
from llm_dup_dev.models.report import PartialDate

from .policy import PublicationPolicy

#: The three precisions :class:`PartialDate` allows, as its own annotation
#: spells them — so a resolution this module derives can be handed straight
#: back to the model without a cast at every call site.
DateResolution = Literal["day", "month", "year"]

#: An ISO 8601 date at one of the three precisions the model allows.
_ISO_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")

class DateShiftError(ValueError):
    """A date could not be shifted at all — not a rejected offset, a broken one."""


def resolution_of(value: str) -> DateResolution | None:
    """The precision *value* is written at, or ``None`` if it is not a date.

    ``ClinicalDateFindingSummary.source_date`` and ``.matched_date`` are bare
    strings with no resolution field, unlike :class:`PartialDate`, so the
    precision has to come from the writing. Returning ``None`` rather than
    guessing is what lets the caller fail closed on anything unexpected.
    """
    match = _ISO_DATE_RE.match(value.strip())
    if match is None:
        return None
    year, month, day = match.groups()
    try:
        if day is not None:
            date(int(year), int(month), int(day))
            return "day"
        if month is not None:
            date(int(year), int(month), 1)
            return "month"
        date(int(year), 1, 1)
    except ValueError:
        return None
    return "year"


def parse_partial_date(value: str) -> PartialDate | None:
    """*value* as a :class:`PartialDate`, or ``None`` when it is not a date."""
    resolution = resolution_of(value)
    if resolution is None:
        return None
    return PartialDate(value=value.strip(), resolution=resolution)


def truncate_to_resolution(value: date, resolution: DateResolution) -> str:
    """Write a calendar date back at the given precision."""
    if resolution == "year":
        return f"{value.year:04d}"
    if resolution == "month":
        return f"{value.year:04d}-{value.month:02d}"
    return value.isoformat()


def shift_partial_date(pd: PartialDate, offset_days: int) -> PartialDate:
    """Shift *pd* along the timeline by *offset_days*, keeping its precision.

    The anchor is the date's earliest plausible day, so the result is a
    function of the interval's lower bound alone — deterministic, and
    identical for two dates written identically.
    """
    try:
        shifted = earliest_plausible_date(pd) + timedelta(days=offset_days)
    except (OverflowError, ValueError) as exc:  # pragma: no cover - guard
        raise DateShiftError(
            f"cannot shift {pd.value!r} by {offset_days} days"
        ) from exc
    return PartialDate(
        value=truncate_to_resolution(shifted, pd.resolution),
        resolution=pd.resolution,
    )


def within_plausible_window(pd: PartialDate, policy: PublicationPolicy) -> bool:
    """Whether a shifted date still sits inside the policy's era window.

    A shift that puts an adverse event in 1931 or 2093 is representable and
    useless: a reader notices, and the page stops being a faithful picture of
    a real assessment.
    """
    lo = earliest_plausible_date(pd)
    hi = latest_plausible_date(pd)
    return (
        lo.year >= policy.date_year_floor and hi.year <= policy.date_year_ceiling
    )


def offset_candidates(policy: PublicationPolicy, seed: str) -> list[int]:
    """Candidate offsets for one pair, in the order they should be tried.

    Built in the house determinism style of
    :mod:`llm_dup_dev.study_eval.sampling`: the pool is assembled in sorted
    order before any draw, so set or dict iteration order can never reach the
    shuffle, and the seed is a string so one pair's draw cannot perturb
    another's.

    **Zero is in the pool, and so is every whole number of years.** Nothing
    is excluded for looking as though it had done nothing, because "it looks
    unshifted" and "it is unshifted" are states an observer must not be able
    to tell apart. An offset the policy forbids is an offset an observer can
    subtract from the possibilities, and a pool that guaranteed movement
    would let them rule out the displayed date as the true one — which is
    exactly the inference the transform exists to prevent.
    """
    allows_zero = policy.allow_unchanged_values and policy.date_offset_min_days == 0
    pool = [0] if allows_zero else []
    pool += [
        offset
        for magnitude in range(
            max(policy.date_offset_min_days, 1), policy.date_offset_max_days + 1
        )
        for offset in (-magnitude, magnitude)
    ]
    rng = random.Random(seed)
    rng.shuffle(pool)
    return pool[: policy.date_offset_candidates]


__all__ = [
    "DateShiftError",
    "offset_candidates",
    "parse_partial_date",
    "resolution_of",
    "shift_partial_date",
    "truncate_to_resolution",
    "within_plausible_window",
]
