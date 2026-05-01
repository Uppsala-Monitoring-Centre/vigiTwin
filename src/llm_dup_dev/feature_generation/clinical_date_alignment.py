"""Interval-aware date primitives and 1:1 alignment for clinical dates.

Building blocks shared by the clinical-date inconsistency orchestrator:

* Plausible-date interval helpers (``earliest_plausible_date``,
  ``latest_plausible_date``) and the interval-aware
  ``min_day_difference`` cost metric.
* Per-entity collectors and exact-value dedupers for drug-start and
  AE-onset dates.
* A minimum-cost one-to-one matching engine and the two alignment
  wrappers used to pair dates across two reports.

This module is deliberately free of severity / threshold logic. It
operates on :class:`PartialDate` and :class:`DrugDateEntry` only;
finding construction, downgrade rules, and bucketing live in
``clinical_date_inconsistencies``.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from itertools import permutations

from llm_dup_dev.models.enums import DrugRole
from llm_dup_dev.models.report import PartialDate, Report


# ---------------------------------------------------------------------------
# Incomplete-date helpers
# ---------------------------------------------------------------------------


def earliest_plausible_date(pd: PartialDate) -> date:
    """Return the earliest calendar date consistent with *pd*."""
    if pd.resolution == "day":
        return date.fromisoformat(pd.value)
    if pd.resolution == "month":
        y, m = int(pd.value[:4]), int(pd.value[5:7])
        return date(y, m, 1)
    # year
    return date(int(pd.value[:4]), 1, 1)


def latest_plausible_date(pd: PartialDate) -> date:
    """Return the latest calendar date consistent with *pd*."""
    if pd.resolution == "day":
        return date.fromisoformat(pd.value)
    if pd.resolution == "month":
        y, m = int(pd.value[:4]), int(pd.value[5:7])
        last = calendar.monthrange(y, m)[1]
        return date(y, m, last)
    y = int(pd.value[:4])
    return date(y, 12, 31)


def _date_interval(pd: PartialDate) -> tuple[date, date]:
    return (earliest_plausible_date(pd), latest_plausible_date(pd))


def min_day_difference(pd1: PartialDate, pd2: PartialDate) -> int:
    """Minimum possible absolute day difference between two partial dates.

    Returns 0 when the intervals overlap.
    """
    lo1, hi1 = _date_interval(pd1)
    lo2, hi2 = _date_interval(pd2)
    if lo1 <= hi2 and lo2 <= hi1:
        return 0
    return min(abs((lo1 - hi2).days), abs((lo2 - hi1).days))


# ---------------------------------------------------------------------------
# Per-entity date collectors (for multi-regimen alignment)
# ---------------------------------------------------------------------------


@dataclass
class DrugDateEntry:
    """A single dated drug entry preserving role metadata.

    Role is retained so downstream concomitant-drug downgrade logic can
    still apply on the source side after role-agnostic counterpart
    selection.
    """

    partial_date: PartialDate
    role: DrugRole | None


def normalize_entity_key(name: str) -> str:
    """Case-fold a substance or PT name for use as a matching key.

    Mirrors the behaviour of ``review_html.normalize.normalize_drug_key``
    / ``normalize_ae_key`` without introducing a cross-layer import.
    """
    return name.strip().lower()


def collect_drug_date_groups(
    report: Report,
) -> dict[str, list[DrugDateEntry]]:
    """Group drug start dates by normalized substance name.

    Drugs without a start date are omitted (nothing to compare).
    """
    groups: dict[str, list[DrugDateEntry]] = defaultdict(list)
    for drug in report.drugs:
        if drug.start_date is None:
            continue
        groups[normalize_entity_key(drug.substance)].append(
            DrugDateEntry(partial_date=drug.start_date, role=drug.role)
        )
    return groups


def collect_ae_date_groups(report: Report) -> dict[str, list[PartialDate]]:
    """Group AE onset dates by normalized PT name."""
    groups: dict[str, list[PartialDate]] = defaultdict(list)
    for ae in report.adverse_events:
        if ae.start_date is None:
            continue
        groups[normalize_entity_key(ae.pt_name)].append(ae.start_date)
    return groups


def dedup_drug_date_entries(
    entries: list[DrugDateEntry],
) -> list[DrugDateEntry]:
    """Deduplicate drug date entries by exact date value within a report.

    When two entries share the same date ``value`` but differ in role,
    a non-concomitant role dominates over a concomitant one for that
    slot. The rationale is that if the same substance appears twice at
    the same date — once characterised as suspected/interacting and
    once as concomitant — it should not be treated as purely
    background medication and therefore should not be subject to the
    concomitant-drug downgrade for this slot.

    Deterministic order: the first occurrence of each date value keeps
    its position; later duplicates that win the non-concomitant
    tiebreak overwrite that slot's role but not its position.
    """
    order: list[str] = []
    seen: dict[str, DrugDateEntry] = {}
    for entry in entries:
        key = entry.partial_date.value
        if key not in seen:
            seen[key] = entry
            order.append(key)
        else:
            existing = seen[key]
            if (
                existing.role == DrugRole.CONCOMITANT
                and entry.role != DrugRole.CONCOMITANT
            ):
                seen[key] = entry
    return [seen[k] for k in order]


def dedup_partial_dates(dates: list[PartialDate]) -> list[PartialDate]:
    """Deduplicate partial dates by their raw ``value``, preserving order."""
    order: list[str] = []
    seen: dict[str, PartialDate] = {}
    for d in dates:
        if d.value not in seen:
            seen[d.value] = d
            order.append(d.value)
    return [seen[k] for k in order]


# ---------------------------------------------------------------------------
# Interval-aware minimum-cost 1:1 alignment
# ---------------------------------------------------------------------------

# Brute-force assignment enumeration. Reports in this domain almost
# never carry more than a handful of regimen rows per normalized drug
# (k ≤ 5 in practice); k! ≤ 40320 at k=8 stays well under a millisecond.
# The limit applies to max(n, m) because we enumerate permutations of
# the larger side, taken k = min(n, m) at a time.
_ALIGNMENT_BRUTE_FORCE_LIMIT = 8


def _min_cost_matching(
    cost_matrix: list[list[int]],
) -> list[tuple[int, int]]:
    """Return a minimum-cost one-to-one bipartite assignment.

    Given a rectangular cost matrix ``cost[i][j]``, finds an assignment
    of distinct row indices to distinct column indices of size
    ``min(n_rows, n_cols)`` that minimises the total cost.

    Ties are broken deterministically by preferring the
    lexicographically smallest sorted ``[(row, col), ...]`` assignment,
    so results are reproducible regardless of input iteration order.

    Raises :class:`RuntimeError` when ``max(n, m)`` exceeds
    :data:`_ALIGNMENT_BRUTE_FORCE_LIMIT`. If this is ever hit on real
    data, replace this implementation with
    ``scipy.optimize.linear_sum_assignment`` — the brute-force bound
    exists to fail loudly rather than silently approximating.
    """
    n = len(cost_matrix)
    if n == 0:
        return []
    m = len(cost_matrix[0])
    if m == 0:
        return []

    if max(n, m) > _ALIGNMENT_BRUTE_FORCE_LIMIT:
        raise RuntimeError(
            f"_min_cost_matching: input {n}x{m} exceeds brute-force "
            f"limit of {_ALIGNMENT_BRUTE_FORCE_LIMIT}. Replace with "
            f"scipy.optimize.linear_sum_assignment for larger inputs."
        )

    best_cost: int | None = None
    best_assignment: list[tuple[int, int]] = []

    if n <= m:
        # Assign every row to a distinct column: enumerate length-n
        # permutations of the m columns.
        for col_perm in permutations(range(m), n):
            total = sum(cost_matrix[i][col_perm[i]] for i in range(n))
            candidate = sorted(zip(range(n), col_perm))
            if best_cost is None or total < best_cost or (
                total == best_cost and candidate < best_assignment
            ):
                best_cost = total
                best_assignment = candidate
    else:
        # Assign every column to a distinct row: enumerate length-m
        # permutations of the n rows.
        for row_perm in permutations(range(n), m):
            total = sum(cost_matrix[row_perm[j]][j] for j in range(m))
            candidate = sorted(zip(row_perm, range(m)))
            if best_cost is None or total < best_cost or (
                total == best_cost and candidate < best_assignment
            ):
                best_cost = total
                best_assignment = candidate

    return best_assignment


def align_drug_date_entries(
    entries_1: list[DrugDateEntry],
    entries_2: list[DrugDateEntry],
) -> list[tuple[DrugDateEntry, DrugDateEntry]]:
    """Role-agnostic 1:1 alignment between two drug-date entry lists.

    The alignment cost is the interval-aware minimum possible day
    difference used elsewhere for severity assignment. Leftovers on
    the larger side are discarded — they do not generate findings.
    """
    if not entries_1 or not entries_2:
        return []
    cost_matrix = [
        [min_day_difference(a.partial_date, b.partial_date) for b in entries_2]
        for a in entries_1
    ]
    return [(entries_1[i], entries_2[j]) for i, j in _min_cost_matching(cost_matrix)]


def align_partial_date_lists(
    dates_1: list[PartialDate],
    dates_2: list[PartialDate],
) -> list[tuple[PartialDate, PartialDate]]:
    """1:1 alignment between two lists of partial dates (e.g. AE onsets)."""
    if not dates_1 or not dates_2:
        return []
    cost_matrix = [
        [min_day_difference(a, b) for b in dates_2] for a in dates_1
    ]
    return [(dates_1[i], dates_2[j]) for i, j in _min_cost_matching(cost_matrix)]
