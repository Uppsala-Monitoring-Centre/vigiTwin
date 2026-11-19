"""What a date transform must preserve, and how that is checked.

The shift rule in :mod:`.dates` guarantees only that a transformed date is
*representable* at its original precision. Whether it still means the same
thing is a separate question, and this module is the answer to it: for one
candidate offset, extract every relationship the presentation rests on,
transform, extract again, and require the two to be identical.

The relationships, over every ordered pair of dates in a case:

#. **Precision** — unchanged for every date.
#. **Display equality** — two dates written alike stay alike, and two written
   differently stay different. This is the "preserve matching relationships"
   requirement at the level a reader sees.
#. **Resolution-aware match verdict** — whether the pipeline's own
   comparison would still call them a match, and at which resolution. The
   matched *value* changes by design; the verdict must not.
#. **Exact day difference** — for two day-precision dates, preserved to the
   day. A one-day difference between two onsets is evidence.
#. **Separation** — what the page claims about the distance between two
   dates, which is not the same as the number of days itself. Preserved
   **exactly** where it is zero, where both dates are complete (a common
   offset gives that for free), and where it is short enough to be read
   directly — ``separation_exact_days``, seven by default, so a one-day
   discrepancy between two onsets can never be rounded into something else.
   Above that it may move by ``separation_relative_tolerance`` of itself,
   **provided it stays in the same severity band**: a finding saying two
   dates are "at least 796 days apart" is making a claim about a magnitude
   and a direction, and the band is what holds its meaning. The bands come
   from the pipeline's own clinical-date thresholds, so the transform cannot
   move a finding into a severity the assessment did not give it. Where one
   side is a *partial* date the rule is enforced for the pairs a finding
   states something about; elsewhere a breach is recorded as an advisory.
#. **Interval order** — the sign of the difference between lower bounds and
   between upper bounds, and whether one date lies strictly before or after
   the other. The direction of a discrepancy, in other words. Required of
   every pair: unlike a numeric bound it is always achievable, so an offset
   that pushes a complete date past a year-only interval boundary is
   rejected and another tried.

Together, 4 and 5 are why no prose needs re-deriving for *meaning*. A figure
the page quotes may still have moved inside its tolerance, so the figure
itself is recomputed from the transformed dates before the page is written —
presentation only, with the assessment's own severity, weight and counts left
exactly as the pipeline produced them.

Two further conditions are not relationships between dates but are checked
here because they decide the same question:

* **Plausibility** — every shifted date stays inside the policy's era window.

**Nothing here requires a date to move.** An offset of zero preserves every
relationship perfectly and is a legitimate outcome; a rule that forced
movement would let anyone holding the policy rule out the displayed date as
the true one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from llm_dup_dev.feature_generation.clinical_date_alignment import (
    earliest_plausible_date,
    latest_plausible_date,
    min_day_difference,
)

# Imported privately, as `review_html.compare` already does. These are the
# day-difference thresholds the pipeline graded the findings by; re-spelling
# them here would let the transform move a finding into a severity band the
# assessment never gave it, without anything noticing.
from llm_dup_dev.feature_generation.clinical_date_inconsistencies import (
    _CLINICAL_DATE_MINOR_DAYS,
    _CLINICAL_DATE_MODERATE_DAYS,
    _DECISIVE_IMPOSSIBILITY_GRACE_DAYS,
)

# Imported privately, as `review_html.compare` already does: these are the
# pipeline's own comparison rules, and re-deriving them here would let the
# publication layer and the assessment disagree about what a match is.
from llm_dup_dev.feature_generation.matching import _compare_partial_dates
from llm_dup_dev.models.report import PartialDate

from .dates import shift_partial_date, within_plausible_window
from .policy import PublicationPolicy


#: Every day-difference threshold a clinical-date verdict turns on, in order:
#: the minor and moderate boundaries from the canonical severity table, and
#: the grace window beyond which a report date preceding an onset becomes a
#: decisive impossibility. A separation that stays between the same two
#: boundaries cannot have changed the assessment's reading of it.
SEVERITY_BOUNDARY_DAYS: tuple[int, ...] = tuple(
    sorted(
        {
            0,
            _CLINICAL_DATE_MINOR_DAYS,
            _DECISIVE_IMPOSSIBILITY_GRACE_DAYS,
            _CLINICAL_DATE_MODERATE_DAYS,
        }
    )
)


def severity_band(days: int) -> int:
    """Which band a day difference falls in, as an index.

    Zero is its own band: two dates that could be the same date are a
    different statement from two that are a week apart, however small the
    week looks beside a boundary.
    """
    return sum(1 for boundary in SEVERITY_BOUNDARY_DAYS if days > boundary)


def separation_preserved(
    before: int, after: int, policy: PublicationPolicy
) -> bool:
    """Whether a separation still says what it said.

    Exact where the figure is read directly — zero, or at or below
    ``separation_exact_days`` — and otherwise within a relative tolerance and
    inside the same severity band. The band is the binding constraint for
    anything near a boundary, and the tolerance for anything far from one.
    """
    if before <= policy.separation_exact_days:
        return after == before
    if severity_band(after) != severity_band(before):
        return False
    allowed = policy.separation_relative_tolerance * before
    return abs(after - before) <= allowed


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)


@dataclass(frozen=True)
class DateRelation:
    """Every relationship between one ordered pair of dates."""

    equal: bool
    #: The resolution at which the pipeline would call these a match, or
    #: ``None`` when it would not.
    match_resolution: str | None
    #: Signed day difference, for two day-precision dates only.
    exact_diff: int | None
    min_gap: int
    lower_order: int
    upper_order: int
    strictly_before: bool
    strictly_after: bool


@dataclass(frozen=True)
class DateRelations:
    """The relational fingerprint of a whole set of dates."""

    resolutions: tuple[str, ...]
    pairs: tuple[DateRelation, ...]


@dataclass(frozen=True)
class InvariantFailure:
    """One way a candidate offset would change what the page says."""

    rule: str
    detail: str


@dataclass(frozen=True)
class InvariantReport:
    """The verdict on one candidate offset.

    ``failures`` rejects the offset. ``advisories`` do not: they are changes
    to relationships the page never asserts — the plausible separation of a
    year-only date from a complete one — which a reader cannot read off the
    page but a disclosure reviewer may still want to see. They are carried
    into the side-by-side review rather than dropped.
    """

    failures: tuple[InvariantFailure, ...] = ()
    advisories: tuple[InvariantFailure, ...] = ()

    @property
    def accepted(self) -> bool:
        return not self.failures


def _relation(a: PartialDate, b: PartialDate) -> DateRelation:
    lo_a, hi_a = earliest_plausible_date(a), latest_plausible_date(a)
    lo_b, hi_b = earliest_plausible_date(b), latest_plausible_date(b)
    match = _compare_partial_dates(a, b)
    exact = (
        (lo_a - lo_b).days
        if a.resolution == "day" and b.resolution == "day"
        else None
    )
    return DateRelation(
        equal=a.value == b.value and a.resolution == b.resolution,
        match_resolution=None if match is None else match[1],
        exact_diff=exact,
        min_gap=min_day_difference(a, b),
        lower_order=_sign((lo_a - lo_b).days),
        upper_order=_sign((hi_a - hi_b).days),
        strictly_before=hi_a < lo_b,
        strictly_after=lo_a > hi_b,
    )


def coarse_anchor_held(
    a: PartialDate,
    moved_a: PartialDate,
    b: PartialDate,
    moved_b: PartialDate,
) -> bool:
    """Whether one of this pair is a coarse date that stayed where it was.

    A year-only or month-only value does not move under a shift smaller than
    its own precision: thirty days from 1 January 2006 is still 2006. So in a
    history mixing precisions, a modest offset moves the day-level dates and
    leaves the coarse ones, which changes where a day-level date sits inside
    a coarse one's span.

    That is the condition this names. What a reader loses is a relationship
    between a date known to the day and one known only to the year; what they
    would otherwise lose is any shift at all except a whole year, because
    nothing else leaves a bare ``2006`` in place against a ``2006-12-05``.
    An unmoved year is close to no disclosure on its own — it is the precise
    dates beside it that identify — so the trade is worth making, and
    ``PublicationPolicy.allow_coarse_date_drift`` is where it is made.
    """
    for coarse, shifted_coarse, other, shifted_other in (
        (a, moved_a, b, moved_b),
        (b, moved_b, a, moved_a),
    ):
        if (
            coarse.resolution != "day"
            and coarse.value == shifted_coarse.value
            and other.value != shifted_other.value
        ):
            return True
    return False


def date_relations(dates: Sequence[PartialDate]) -> DateRelations:
    """The relational fingerprint of *dates*, in the order given.

    Positional: the caller passes the same list before and after shifting, so
    index *i* is the same date either side and the two fingerprints compare
    directly.
    """
    return DateRelations(
        resolutions=tuple(d.resolution for d in dates),
        pairs=tuple(
            _relation(dates[i], dates[j])
            for i in range(len(dates))
            for j in range(i + 1, len(dates))
        ),
    )


#: What a cross-block pair must keep. The gap between an event and the report
#: that describes it is expected to move — that is what separate offsets are
#: for — but which came first is not negotiable, and neither is an equality
#: the page shows.
_ORDERING_FACTS = ("equal", "match_resolution", "strictly_before", "strictly_after")


def check_cross_block(
    clinical: Sequence[PartialDate],
    report: Sequence[PartialDate],
    clinical_offset: int,
    report_offset: int,
    *,
    label: str = "",
) -> InvariantReport:
    """Whether two offsets keep what the page says *across* the two blocks.

    Called per report, with that report's own clinical dates and its own
    report dates, so the rule it enforces is the one that means something: an
    event stays before the report that describes it. This is what makes a
    *forward* clinical shift safe — the direction that could otherwise carry
    an event past its own report — so the direction itself needs no
    restricting.

    Every clinical/report pair of one report keeps its ordering facts: an
    event that preceded that report still precedes it, and two dates the
    pipeline would call equal are still equal. Dates belonging to the other
    report are checked against that report, not this one; their relationship
    to this one is not something the page states.

    The *gap* between them is expected to change. That is the point of
    separate offsets and the one thing the split gives up, so it is not
    reported per case: it would fire on every case that took two offsets and
    say nothing a reader cannot see from the two offsets themselves, which
    the key record holds. The transform manifest states the scheme and the
    footer says it on the page.
    """
    if not clinical or not report or clinical_offset == report_offset:
        return InvariantReport()
    shifted_clinical = [shift_partial_date(d, clinical_offset) for d in clinical]
    shifted_report = [shift_partial_date(d, report_offset) for d in report]

    failures: list[InvariantFailure] = []
    for i, left in enumerate(clinical):
        for j, right in enumerate(report):
            before = _relation(left, right)
            after = _relation(shifted_clinical[i], shifted_report[j])
            for fact in _ORDERING_FACTS:
                if getattr(before, fact) != getattr(after, fact):
                    whose = f" of {label}" if label else ""
                    failures.append(
                        InvariantFailure(
                            "clinical_before_report",
                            f"{left.value} and the report date {right.value}"
                            f"{whose} are {fact.replace('_', ' ')} as "
                            "reported, and would not be after these offsets",
                        )
                    )
    return InvariantReport(failures=tuple(failures))


def dedupe_dates(dates: Sequence[PartialDate]) -> list[PartialDate]:
    """*dates* with exact repeats dropped, first occurrence order kept.

    Two cells showing the same date are one date as far as the relationships
    go, and keeping both would make the fingerprint quadratic in the number of
    cells rather than in the number of distinct values.
    """
    seen: dict[tuple[str, str], PartialDate] = {}
    for item in dates:
        seen.setdefault((item.value, item.resolution), item)
    return list(seen.values())


def _asserted_index_pairs(
    dates: Sequence[PartialDate],
    asserted: Sequence[tuple[PartialDate, PartialDate]],
) -> set[tuple[int, int]]:
    """Index pairs of *dates* that *asserted* names, in either order."""
    position = {(d.value, d.resolution): i for i, d in enumerate(dates)}
    out: set[tuple[int, int]] = set()
    for left, right in asserted:
        i = position.get((left.value, left.resolution))
        j = position.get((right.value, right.resolution))
        if i is None or j is None or i == j:
            continue
        out.add((min(i, j), max(i, j)))
    return out


def check_date_offset(
    dates: Sequence[PartialDate],
    offset_days: int,
    policy: PublicationPolicy,
    *,
    asserted: Sequence[tuple[PartialDate, PartialDate]] = (),
) -> InvariantReport:
    """Whether *offset_days* preserves what the page says about *dates*.

    *asserted* names the pairs the presentation makes a numeric claim about —
    in practice the two values of each clinical-date finding. Their separation
    must survive exactly. Every other pair is held to the qualitative
    relationships only, and a bound that moves there becomes an advisory.

    The reporting context's year is not among *dates*: it is frozen rather
    than shifted, so it has no relationships here to preserve. What it does
    have is its own condition, in ``scramble._ContextGuard``.

    An accepted report is the only thing that licenses the offset; the caller
    tries the next candidate otherwise, and flags the case when the candidates
    run out. An offset is never accepted with a relaxed invariant.
    """
    if not dates:
        return InvariantReport()
    shifted = [shift_partial_date(d, offset_days) for d in dates]
    failures: list[InvariantFailure] = []
    advisories: list[InvariantFailure] = []

    before = date_relations(dates)
    after = date_relations(shifted)
    asserted_pairs = _asserted_index_pairs(dates, asserted)

    if before.resolutions != after.resolutions:  # pragma: no cover - rule guard
        failures.append(
            InvariantFailure(
                "precision",
                "a date changed precision, which the shift rule forbids",
            )
        )

    index = 0
    for i in range(len(dates)):
        for j in range(i + 1, len(dates)):
            was, now = before.pairs[index], after.pairs[index]
            index += 1
            if was == now:
                continue
            pair = f"{dates[i].value} / {dates[j].value}"
            became = f"{shifted[i].value} / {shifted[j].value}"
            if was.equal != now.equal:
                failures.append(
                    InvariantFailure(
                        "display_equality",
                        f"{pair} became {became}: "
                        f"{'equal' if now.equal else 'unequal'} where it was "
                        f"{'equal' if was.equal else 'unequal'}",
                    )
                )
            drifted = policy.allow_coarse_date_drift and coarse_anchor_held(
                dates[i], shifted[i], dates[j], shifted[j]
            )
            if was.match_resolution != now.match_resolution:
                entry = InvariantFailure(
                    "match_verdict",
                    f"{pair} became {became}: match verdict moved from "
                    f"{was.match_resolution!r} to {now.match_resolution!r}",
                )
                if (i, j) in asserted_pairs or not drifted:
                    failures.append(entry)
                else:
                    advisories.append(
                        InvariantFailure("coarse_date_drift", entry.detail)
                    )
            if was.exact_diff != now.exact_diff:
                failures.append(
                    InvariantFailure(
                        "exact_difference",
                        f"{pair} became {became}: exact difference moved from "
                        f"{was.exact_diff} to {now.exact_diff} days",
                    )
                )
            if was.min_gap != now.min_gap and not separation_preserved(
                was.min_gap, now.min_gap, policy
            ):
                complete = (
                    dates[i].resolution == "day" and dates[j].resolution == "day"
                )
                gap = InvariantFailure(
                    "separation",
                    f"{pair} became {became}: separation moved from "
                    f"{was.min_gap} to {now.min_gap} days, outside the "
                    f"tolerance or into another severity band",
                )
                if (i, j) in asserted_pairs or complete:
                    failures.append(gap)
                else:
                    advisories.append(
                        InvariantFailure("unasserted_separation", gap.detail)
                    )
            if (
                was.lower_order != now.lower_order
                or was.upper_order != now.upper_order
                or was.strictly_before != now.strictly_before
                or was.strictly_after != now.strictly_after
            ):
                order = InvariantFailure(
                    "interval_order",
                    f"{pair} became {became}: their ordering changed",
                )
                if (i, j) in asserted_pairs or not drifted:
                    failures.append(order)
                else:
                    advisories.append(
                        InvariantFailure("coarse_date_drift", order.detail)
                    )

    if not policy.allow_unchanged_values and all(
        a.value == b.value for a, b in zip(dates, shifted, strict=True)
    ):
        failures.append(
            InvariantFailure(
                "effectiveness",
                f"an offset of {offset_days} days moved no displayed date, "
                "which this policy does not allow",
            )
        )

    for original, moved in zip(dates, shifted, strict=True):
        if not within_plausible_window(moved, policy):
            failures.append(
                InvariantFailure(
                    "plausibility",
                    f"{original.value} became {moved.value}, outside "
                    f"{policy.date_year_floor}-{policy.date_year_ceiling}",
                )
            )

    return InvariantReport(tuple(failures), tuple(advisories))


__all__ = [
    "DateRelation",
    "DateRelations",
    "InvariantFailure",
    "InvariantReport",
    "check_cross_block",
    "coarse_anchor_held",
    "check_date_offset",
    "date_relations",
    "dedupe_dates",
]
