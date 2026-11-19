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
from enum import Enum
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


def _bounds(
    policy: PublicationPolicy, role: DateRole
) -> tuple[int, int, tuple[int, ...]]:
    """The floor, the ceiling and the directions one block may move in."""
    if role is DateRole.CLINICAL and policy.decouple_clinical_dates:
        return (
            policy.clinical_offset_min_days,
            policy.clinical_offset_max_days,
            (-1,) if policy.clinical_offset_backward_only else (-1, 1),
        )
    return policy.date_offset_min_days, policy.date_offset_max_days, (-1, 1)


def offset_floor(policy: PublicationPolicy, role: DateRole) -> int:
    """The smallest magnitude the policy asks for, for one block."""
    return _bounds(policy, role)[0]


class DateRole(str, Enum):
    """Which block of a case's dates an offset applies to.

    The two move independently because they are independent in the source: a
    report date says when the report reached the centre, a clinical date says
    when something happened to the patient, and reports routinely arrive
    years after the event. Tying them to one offset made the tighter
    constraint govern both — see :func:`offset_candidates`.
    """

    #: Report dates. Bounded by the reporting context's year, which is frozen.
    REPORT = "report"
    #: Drug start and end dates and adverse-event onsets. Free of the context
    #: year, so these can move by whole years where the relations require it.
    CLINICAL = "clinical"


def offset_candidates(
    policy: PublicationPolicy,
    seed: str,
    *,
    role: DateRole = DateRole.REPORT,
) -> list[int]:
    """Candidate offsets for one block of one pair, in trial order.

    :func:`offset_tiers` flattened. The search itself walks the tiers, so
    that a stronger offset carrying an advisory is preferred to a weaker one
    carrying none; this is the pool as one list, for callers that only need
    to know what is in it.

    Built in the house determinism style of
    :mod:`llm_dup_dev.study_eval.sampling`: the pool is assembled in sorted
    order before any draw, so set or dict iteration order can never reach the
    shuffle, and the seed is a string so one pair's draw cannot perturb
    another's.

    **Every whole number of years is in the pool**, and for a case mixing
    year-, month- and day-precision dates those are usually the only offsets
    that survive the relation invariants at all: anything else moves a bare
    ``2006`` relative to a ``2006-05-30``. The clinical block's range is wide
    enough to hold several of them, which is what makes a floor satisfiable
    for a case with a long history.

    **Zero is still in the pool where the policy allows it, but it is tried
    last.** Keeping it is what stops "it looks unshifted" and "it is
    unshifted" being distinguishable states; trying it last is what stops it
    *winning*. Zero is the one offset guaranteed to raise no advisory — with
    nothing moved, no relationship can move — so a first pass that preferred
    advisory-free offsets handed it every date-rich case, which is exactly
    the population whose real timeline is most identifying. Ordering it last
    costs an observer nothing: the pool is the same.
    """
    return [
        offset
        for tier in offset_tiers(policy, seed, role=role)
        for offset in tier
    ]


def offset_tiers(
    policy: PublicationPolicy,
    seed: str,
    *,
    role: DateRole = DateRole.REPORT,
) -> list[list[int]]:
    """The candidate pool in bands, strongest obfuscation first.

    Three bands, and the order is the whole point:

    1. magnitudes at or above the policy's floor — what the policy asks for;
    2. magnitudes below it, where they exist — not what the policy asks for,
       but a case whose relationships only survive a four-day shift is
       better published four days out than refused, and the shift is
       reported as :attr:`~.findings.DisclosureCode.DATE_OFFSET_BELOW_FLOOR`
       so a reviewer knows the obfuscation is weak;
    3. zero, last, and only where the policy allows it at all.

    A floor is therefore a preference rather than a refusal, while zero stays
    a last resort. The alternative was a floor that turned every hard case
    into a rejection — and the case that prompted this carried sixteen dates,
    which is exactly the case a reviewer least wants to handle by hand.
    """
    floor, ceiling, signs = _bounds(policy, role)

    def band(lowest: int, highest: int) -> list[int]:
        pool = [
            sign * magnitude
            for magnitude in range(lowest, highest + 1)
            for sign in signs
        ]
        random.Random(f"{seed}:{lowest}-{highest}").shuffle(pool)
        return pool[: policy.date_offset_candidates]

    tiers = [band(max(floor, 1), ceiling)]
    if floor > 1:
        tiers.append(band(1, floor - 1))
    if policy.allow_unchanged_values:
        tiers.append([0])
    return [tier for tier in tiers if tier]


__all__ = [
    "DateRole",
    "DateShiftError",
    "offset_floor",
    "offset_tiers",
    "offset_candidates",
    "parse_partial_date",
    "resolution_of",
    "shift_partial_date",
    "truncate_to_resolution",
    "within_plausible_window",
]
