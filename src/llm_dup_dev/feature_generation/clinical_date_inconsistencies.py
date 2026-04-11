"""Rule-based clinical-date inconsistency detection for report pairs.

Compares drug start dates, AE onset dates, report dates, and a derived
overall AE onset date between two reports.  Produces severity-bucketed
counts (minor / moderate / strong / decisive) with a full audit trail.

Drug end dates are **never** primary inconsistency sources; they may
only serve as downgrade evidence when they exactly match a source date
on the opposite report.

Incomplete-date strategy (option 2 from the spec):
    Use the *minimum possible* day difference between two date intervals.
    Only assign a severity when the category is guaranteed — i.e. even
    the most generous interpretation of incomplete dates still exceeds
    the threshold.  This avoids false positives at the cost of
    occasionally missing a discrepancy when resolution is too coarse.

Multiple entries per entity:
    The same normalized drug substance may appear on a report with
    multiple distinct start dates (e.g. re-challenge regimens), and
    the same MedDRA PT may appear with multiple onset dates.  For
    each matched entity (drug substance or PT) across the two
    reports, we compute the **minimum-cost one-to-one alignment**
    between the two reports' date sets (using the same interval-aware
    minimum possible day difference), deduplicate exact duplicate
    date values within a report first, and emit candidate findings
    only from the aligned pairs — leftover unmatched dates do not by
    themselves create extra inconsistencies.  Drug role is **ignored**
    for counterpart selection; it is retained only for downstream
    rules (source-date priority, concomitant-drug downgrade).

See: docs/same_individual_assessment_spec.md § Clinical Date Inconsistencies
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from enum import Enum, IntEnum
from itertools import permutations
from typing import Callable

from llm_dup_dev.models.enums import DrugRole, InconsistencySeverity
from llm_dup_dev.models.report import DateValue, Drug, PartialDate, Report


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


def _min_day_difference(pd1: PartialDate, pd2: PartialDate) -> int:
    """Minimum possible absolute day difference between two partial dates.

    Returns 0 when the intervals overlap.
    """
    lo1, hi1 = _date_interval(pd1)
    lo2, hi2 = _date_interval(pd2)
    if lo1 <= hi2 and lo2 <= hi1:
        return 0
    return min(abs((lo1 - hi2).days), abs((lo2 - hi1).days))


def _datevalue_to_partial(dv: DateValue) -> PartialDate:
    return PartialDate(value=dv.value, resolution=dv.resolution)


# ---------------------------------------------------------------------------
# Entity types and priority
# ---------------------------------------------------------------------------


class EntityType(IntEnum):
    """Priority order for source-date bucketing (lower = higher priority)."""

    OVERALL_AE_ONSET = 0
    SUSPECTED_DRUG_START = 1
    AE_ONSET = 2
    CONCOMITANT_DRUG_START = 3


class Severity(str, Enum):
    """Internal severity labels including 'ignored'."""

    MINOR = "minor"
    MODERATE = "moderate"
    STRONG = "strong"
    DECISIVE = "decisive"
    IGNORED = "ignored"
    NONE = "none"

    def to_inconsistency_severity(self) -> InconsistencySeverity | None:
        _MAP = {
            Severity.MINOR: InconsistencySeverity.MINOR,
            Severity.MODERATE: InconsistencySeverity.MODERATE,
            Severity.STRONG: InconsistencySeverity.STRONG,
            Severity.DECISIVE: InconsistencySeverity.DECISIVE,
        }
        return _MAP.get(self)


_SEVERITY_RANK: dict[Severity, int] = {
    Severity.DECISIVE: 4,
    Severity.STRONG: 3,
    Severity.MODERATE: 2,
    Severity.MINOR: 1,
    Severity.NONE: 0,
    Severity.IGNORED: -1,
}


def _severity_gt(a: Severity, b: Severity) -> bool:
    return _SEVERITY_RANK[a] > _SEVERITY_RANK[b]


# ---------------------------------------------------------------------------
# Threshold configuration
# ---------------------------------------------------------------------------

# Overall AE onset date thresholds (tighter)
_AE_ONSET_MINOR_DAYS = 7
_AE_ONSET_MODERATE_DAYS = 30

# Drug start / AE onset thresholds (more lenient).
# Per-entity drug start and AE onset discrepancies cap at MODERATE: a single
# entity-level mismatch is never strong evidence on its own (the overall AE
# onset date covers the case-level signal). Differences <= 7 days are treated
# as no inconsistency to absorb minor data-entry / regimen-rounding noise.
_ENTITY_NONE_DAYS = 7
_ENTITY_MODERATE_DAYS = 70


def _classify_overall_ae_onset(diff_days: int) -> Severity:
    if diff_days == 0:
        return Severity.NONE
    if diff_days <= _AE_ONSET_MINOR_DAYS:
        return Severity.MINOR
    if diff_days <= _AE_ONSET_MODERATE_DAYS:
        return Severity.MODERATE
    return Severity.STRONG


def _classify_entity(diff_days: int) -> Severity:
    """Classify a per-entity drug start / AE onset date difference.

    Caps at MODERATE: single-entity mismatches are never STRONG.
    Differences within :data:`_ENTITY_NONE_DAYS` are treated as NONE.
    """
    if diff_days <= _ENTITY_NONE_DAYS:
        return Severity.NONE
    if diff_days < _ENTITY_MODERATE_DAYS:
        return Severity.MINOR
    return Severity.MODERATE


# ---------------------------------------------------------------------------
# Finding / result data structures
# ---------------------------------------------------------------------------


@dataclass
class ClinicalDateFinding:
    """One candidate inconsistency before bucketing."""

    source_report: str  # report_id of the source report
    entity_type: EntityType
    entity_label: str  # e.g. "overall_ae_onset", "drug_start:aspirin", "ae_onset:headache"
    source_date: PartialDate
    matched_date: PartialDate | None  # date on other report; None for decisive
    raw_diff_days: int | None  # minimum possible day difference; None for decisive
    raw_severity: Severity
    downgrades: list[str] = field(default_factory=list)  # descriptions
    final_severity: Severity = Severity.NONE
    selected: bool = False  # True if this finding was selected by bucketing


@dataclass
class ClinicalDateResult:
    """Structured result of clinical-date inconsistency analysis."""

    n_minor: int = 0
    n_moderate: int = 0
    n_strong: int = 0
    n_decisive: int = 0
    findings: list[ClinicalDateFinding] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Overall AE onset date derivation
# ---------------------------------------------------------------------------


def derive_overall_ae_onset_date(report: Report) -> PartialDate | None:
    """Derive the overall AE onset date for a single report from drug/AE dates.

    This is the dev-side fallback computation. It is used when the
    report does not carry an explicit ``reported_overall_ae_onset_date``
    extracted upstream — see :func:`effective_overall_ae_onset_date`
    for the canonical accessor that prefers the explicit value.

    Primary rule:
        Pick the **earliest** AE onset date. Only refrain from picking
        an AE onset when there is *clear evidence* it is incompatible
        with a suspected drug — i.e. the AE onset interval is
        unambiguously before every regimen of that drug.

    Fallback:
        If no suspected-drug start dates exist, pick the earliest AE
        onset date unconditionally.

    Compatibility is defined to err on the side of accepting early AE
    onsets. Specifically, we group suspected drug start dates by
    normalized substance name (so re-challenges of the same drug with
    multiple regimens are treated jointly). An AE onset ``ae`` is
    compatible with a substance when **any** of its regimens
    ``r`` satisfies::

        earliest_plausible(r) <= latest_plausible(ae)

    i.e. it is plausible that the drug had already been started by the
    time the AE occurred. An AE is retained only when it is compatible
    with *every* suspected substance.

    Consequences:

    * A month-resolution suspected drug start (e.g. ``1968-11``) does
      **not** disqualify a same-month day-resolution AE onset (e.g.
      ``1968-11-12``) — the drug could plausibly have started on
      ``1968-11-01``.
    * For a re-challenge with two regimens of the same drug — one
      before and one after the AE onset — the earlier regimen alone
      is enough to keep the AE in the candidate set.

    If no AE onset is compatible with every suspected substance, the
    derivation returns ``None`` (the pharmacologically unlikely case
    where every recorded onset is strictly before every suspected
    drug's earliest plausible start).
    """
    suspected_with_start: list[Drug] = [
        d
        for d in report.drugs
        if d.role == DrugRole.SUSPECTED and d.start_date is not None
    ]
    ae_onsets: list[PartialDate] = [
        ae.start_date
        for ae in report.adverse_events
        if ae.start_date is not None
    ]

    if not ae_onsets:
        return None

    if not suspected_with_start:
        # No suspected-drug start dates constrain the derivation.
        return min(ae_onsets, key=earliest_plausible_date)

    # Group suspected drug start dates by normalized substance name so
    # that multi-regimen re-challenges of the same drug are treated
    # jointly: a single compatible regimen suffices per substance.
    regimens_by_substance: dict[str, list[PartialDate]] = defaultdict(list)
    for drug in suspected_with_start:
        assert drug.start_date is not None  # filtered above
        regimens_by_substance[_normalize_entity_key(drug.substance)].append(
            drug.start_date
        )

    def _ae_compatible(ae: PartialDate) -> bool:
        ae_latest = latest_plausible_date(ae)
        for regimens in regimens_by_substance.values():
            if not any(
                earliest_plausible_date(r) <= ae_latest for r in regimens
            ):
                return False
        return True

    # Iterate earliest-first and pick the earliest compatible AE onset.
    for ae in sorted(ae_onsets, key=earliest_plausible_date):
        if _ae_compatible(ae):
            return ae

    return None


def effective_overall_ae_onset_date(report: Report) -> PartialDate | None:
    """Return the canonical overall AE onset date used by the pipeline.

    Resolution order:

    1. ``report.reported_overall_ae_onset_date`` — an explicitly
       extracted, report-level AE onset date supplied by the loader
       (e.g. from a "CASE EVENT DATE: YYYYMMDD" pattern on US reports).
       Trusted as-is; the supplied :class:`PartialDate` is consumed
       directly without re-parsing or re-bucketing of resolution.
    2. :func:`derive_overall_ae_onset_date` fallback — the dev-side
       rule that combines suspected-drug start dates and AE onset
       dates.

    All downstream clinical-date logic should call this helper rather
    than ``derive_overall_ae_onset_date`` directly so that explicit
    extractions are honoured consistently.
    """
    explicit = report.reported_overall_ae_onset_date
    if explicit is not None:
        return explicit
    return derive_overall_ae_onset_date(report)


# ---------------------------------------------------------------------------
# Collect downgrade-eligible dates from a report (includes drug end dates)
# ---------------------------------------------------------------------------


def _collect_all_dates_for_downgrade(
    report: Report,
    overall_ae_onset: PartialDate | None,
) -> set[str]:
    """Collect all date *values* (strings) on a report for downgrade checks.

    Includes drug start dates, AE onset dates, overall AE onset date,
    and drug end dates.  Uses the raw ``value`` field for exact matching.
    """
    dates: set[str] = set()

    if overall_ae_onset is not None:
        dates.add(overall_ae_onset.value)

    for drug in report.drugs:
        if drug.start_date is not None:
            dates.add(drug.start_date.value)
        if drug.end_date is not None:
            dates.add(drug.end_date.value)

    for ae in report.adverse_events:
        if ae.start_date is not None:
            dates.add(ae.start_date.value)

    return dates


# ---------------------------------------------------------------------------
# Per-entity date collectors (for multi-regimen alignment)
# ---------------------------------------------------------------------------


@dataclass
class _DrugDateEntry:
    """A single dated drug entry preserving role metadata.

    Role is retained so downstream concomitant-drug downgrade logic can
    still apply on the source side after role-agnostic counterpart
    selection.
    """

    partial_date: PartialDate
    role: DrugRole | None


def _normalize_entity_key(name: str) -> str:
    """Case-fold a substance or PT name for use as a matching key.

    Mirrors the behaviour of ``review_html.normalize.normalize_drug_key``
    / ``normalize_ae_key`` without introducing a cross-layer import.
    """
    return name.strip().lower()


def _collect_drug_date_groups(
    report: Report,
) -> dict[str, list[_DrugDateEntry]]:
    """Group drug start dates by normalized substance name.

    Drugs without a start date are omitted (nothing to compare).
    """
    groups: dict[str, list[_DrugDateEntry]] = defaultdict(list)
    for drug in report.drugs:
        if drug.start_date is None:
            continue
        groups[_normalize_entity_key(drug.substance)].append(
            _DrugDateEntry(partial_date=drug.start_date, role=drug.role)
        )
    return groups


def _collect_ae_date_groups(report: Report) -> dict[str, list[PartialDate]]:
    """Group AE onset dates by normalized PT name."""
    groups: dict[str, list[PartialDate]] = defaultdict(list)
    for ae in report.adverse_events:
        if ae.start_date is None:
            continue
        groups[_normalize_entity_key(ae.pt_name)].append(ae.start_date)
    return groups


def _dedup_drug_date_entries(
    entries: list[_DrugDateEntry],
) -> list[_DrugDateEntry]:
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
    seen: dict[str, _DrugDateEntry] = {}
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


def _dedup_partial_dates(dates: list[PartialDate]) -> list[PartialDate]:
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


def _align_drug_date_entries(
    entries_1: list[_DrugDateEntry],
    entries_2: list[_DrugDateEntry],
) -> list[tuple[_DrugDateEntry, _DrugDateEntry]]:
    """Role-agnostic 1:1 alignment between two drug-date entry lists.

    The alignment cost is the interval-aware minimum possible day
    difference used elsewhere for severity assignment. Leftovers on
    the larger side are discarded — they do not generate findings.
    """
    if not entries_1 or not entries_2:
        return []
    cost_matrix = [
        [_min_day_difference(a.partial_date, b.partial_date) for b in entries_2]
        for a in entries_1
    ]
    return [(entries_1[i], entries_2[j]) for i, j in _min_cost_matching(cost_matrix)]


def _align_partial_date_lists(
    dates_1: list[PartialDate],
    dates_2: list[PartialDate],
) -> list[tuple[PartialDate, PartialDate]]:
    """1:1 alignment between two lists of partial dates (e.g. AE onsets)."""
    if not dates_1 or not dates_2:
        return []
    cost_matrix = [
        [_min_day_difference(a, b) for b in dates_2] for a in dates_1
    ]
    return [(dates_1[i], dates_2[j]) for i, j in _min_cost_matching(cost_matrix)]


# ---------------------------------------------------------------------------
# Downgrade logic
# ---------------------------------------------------------------------------


def _apply_cross_entity_downgrade(
    severity: Severity,
    source_date: PartialDate,
    other_report_dates: set[str],
) -> tuple[Severity, str | None]:
    """Downgrade by one step if source date exactly matches any date on other report."""
    if source_date.value in other_report_dates:
        reason = f"exact match with date {source_date.value} on other report"
        if severity == Severity.STRONG:
            return Severity.MODERATE, reason
        if severity == Severity.MODERATE:
            return Severity.MINOR, reason
        # MINOR stays MINOR
        return severity, reason
    return severity, None


def _apply_concomitant_downgrade(
    severity: Severity,
) -> tuple[Severity, str]:
    """Downgrade concomitant drug start dates by one step.

    Per-entity drug start severity is capped at MODERATE, so a two-step
    downgrade would always wipe concomitant findings out entirely. With a
    single-step downgrade, a moderate concomitant mismatch still
    contributes as a minor inconsistency.
    """
    reason = "concomitant drug downgrade (-1 step)"
    if severity == Severity.STRONG:
        return Severity.MODERATE, reason
    if severity == Severity.MODERATE:
        return Severity.MINOR, reason
    if severity == Severity.MINOR:
        return Severity.IGNORED, reason
    return severity, reason


# ---------------------------------------------------------------------------
# Source-date bucketing
# ---------------------------------------------------------------------------


def _bucket_and_select(
    findings: list[ClinicalDateFinding],
) -> list[ClinicalDateFinding]:
    """Apply one-inconsistency-per-source-date rule per report.

    Groups findings by (source_report, source_date.value).
    Within each bucket, selects at most one finding.

    Priority: entity_type (lower = higher), then final_severity (higher first).
    If the highest-priority entity has no inconsistency, a lower-priority
    entity may contribute.
    """
    buckets: dict[tuple[str, str], list[ClinicalDateFinding]] = defaultdict(list)
    for f in findings:
        key = (f.source_report, f.source_date.value)
        buckets[key].append(f)

    for bucket_findings in buckets.values():
        # Sort: entity_type ascending (higher priority first), then
        # severity descending within same priority
        bucket_findings.sort(
            key=lambda f: (
                f.entity_type,
                -_SEVERITY_RANK.get(f.final_severity, -1),
            )
        )

        selected = False
        for f in bucket_findings:
            if selected:
                break
            if f.final_severity in (
                Severity.NONE,
                Severity.IGNORED,
            ):
                # This entity has no inconsistency; allow lower-priority
                # entities in the bucket to contribute.
                continue
            f.selected = True
            selected = True

    return findings


# ---------------------------------------------------------------------------
# Cross-direction deduplication
# ---------------------------------------------------------------------------


def _dedup_across_directions(
    findings: list[ClinicalDateFinding],
) -> None:
    """Deduplicate selected findings produced by the bidirectional analysis.

    The bidirectional loop generates findings from both R1→R2 and R2→R1.
    For a single entity mismatch (e.g. the same drug on both reports with
    different dates) this produces two selected findings per aligned
    pair.  We keep only the one with the *lower* final severity
    (conservative: if one direction downgrades the finding, we honour
    that).

    The grouping key is direction-independent at the **aligned-pair**
    level: ``(entity_label, frozenset({source_date, matched_date}))``.
    This ensures that when the same entity yields multiple distinct
    aligned pairs (e.g. two different regimen start-date pairs for the
    same drug), each pair is deduplicated independently rather than
    collapsing them all into one.

    Mutates findings in-place by clearing ``selected`` on the duplicate.
    """
    groups: dict[
        tuple[str, frozenset[str]], list[ClinicalDateFinding]
    ] = defaultdict(list)
    for f in findings:
        if not f.selected:
            continue
        groups[_finding_pair_key(f)].append(f)

    for group in groups.values():
        if len(group) <= 1:
            continue
        # Keep the finding with the lowest severity; deselect the rest
        group.sort(key=lambda f: _SEVERITY_RANK.get(f.final_severity, -1))
        for f in group[1:]:
            f.selected = False


# ---------------------------------------------------------------------------
# Decisive impossibility check
# ---------------------------------------------------------------------------


def _check_decisive_impossibility(
    report_date: PartialDate,
    report_date_report_id: str,
    other_ae_onset: PartialDate,
    other_report_id: str,
) -> ClinicalDateFinding | None:
    """Check if report_date is strictly before earliest plausible AE onset.

    For fully specified dates, require at least 1 day of positive separation
    (report_date < earliest_onset - 0 days, i.e. report_date < earliest_onset).
    """
    report_latest = latest_plausible_date(report_date)
    onset_earliest = earliest_plausible_date(other_ae_onset)

    if report_latest < onset_earliest:
        return ClinicalDateFinding(
            source_report=report_date_report_id,
            entity_type=EntityType.OVERALL_AE_ONSET,
            entity_label=(
                f"report_date({report_date_report_id}) < "
                f"overall_ae_onset({other_report_id})"
            ),
            source_date=report_date,
            matched_date=other_ae_onset,
            raw_diff_days=(onset_earliest - report_latest).days,
            raw_severity=Severity.DECISIVE,
            final_severity=Severity.DECISIVE,
            selected=True,  # decisive findings are always selected
        )
    return None


# ---------------------------------------------------------------------------
# Entity labels
# ---------------------------------------------------------------------------

OVERALL_AE_ONSET_LABEL = "overall_ae_onset"


def _drug_start_label(substance_key: str) -> str:
    return f"drug_start:{substance_key}"


def _ae_onset_label(pt_key: str) -> str:
    return f"ae_onset:{pt_key}"


def _drug_entity_type(role: DrugRole | None) -> EntityType:
    if role == DrugRole.CONCOMITANT:
        return EntityType.CONCOMITANT_DRUG_START
    return EntityType.SUSPECTED_DRUG_START


# ---------------------------------------------------------------------------
# Per-pair finding builder
# ---------------------------------------------------------------------------


def _build_finding(
    source_date: PartialDate,
    matched_date: PartialDate,
    source_report_id: str,
    entity_type: EntityType,
    entity_label: str,
    classify: Callable[[int], Severity],
    other_downgrade_dates: set[str],
) -> ClinicalDateFinding:
    """Build a single non-decisive finding for one (source, matched) pair.

    Centralises the raw-severity classification, concomitant-drug
    downgrade (keyed off ``entity_type``), and cross-entity downgrade
    flow so the three entity families (overall AE onset, drug start,
    AE onset) share one implementation.
    """
    diff = _min_day_difference(source_date, matched_date)
    raw_sev = classify(diff)
    current_sev = raw_sev
    downgrades: list[str] = []

    if raw_sev != Severity.NONE:
        if entity_type == EntityType.CONCOMITANT_DRUG_START:
            current_sev, reason = _apply_concomitant_downgrade(current_sev)
            downgrades.append(reason)

        if current_sev not in (Severity.NONE, Severity.IGNORED):
            current_sev, reason = _apply_cross_entity_downgrade(
                current_sev, source_date, other_downgrade_dates,
            )
            if reason:
                downgrades.append(reason)

    return ClinicalDateFinding(
        source_report=source_report_id,
        entity_type=entity_type,
        entity_label=entity_label,
        source_date=source_date,
        matched_date=matched_date,
        raw_diff_days=diff,
        raw_severity=raw_sev,
        downgrades=downgrades,
        final_severity=current_sev,
    )


# ---------------------------------------------------------------------------
# Cross-direction dedup key (shared with tests for single source of truth)
# ---------------------------------------------------------------------------


def _finding_pair_key(
    finding: ClinicalDateFinding,
) -> tuple[str, frozenset[str]]:
    """Direction-independent key identifying an aligned-pair finding."""
    if finding.matched_date is not None:
        return (
            finding.entity_label,
            frozenset({finding.source_date.value, finding.matched_date.value}),
        )
    return (finding.entity_label, frozenset({finding.source_date.value}))


# ---------------------------------------------------------------------------
# Main comparison function
# ---------------------------------------------------------------------------


def compute_clinical_date_inconsistencies(
    r1: Report,
    r2: Report,
) -> ClinicalDateResult:
    """Compute clinical-date inconsistencies between two reports.

    Returns a :class:`ClinicalDateResult` with severity counts and a
    full audit trail of findings.
    """
    # Resolve the canonical overall AE onset date for each report.
    # Prefers an explicit ``reported_overall_ae_onset_date`` (extracted
    # upstream by the loader) and falls back to the dev-side derivation
    # rule otherwise.
    onset1 = effective_overall_ae_onset_date(r1)
    onset2 = effective_overall_ae_onset_date(r2)

    # Collect all dates for downgrade checks
    downgrade_dates1 = _collect_all_dates_for_downgrade(r1, onset1)
    downgrade_dates2 = _collect_all_dates_for_downgrade(r2, onset2)

    findings: list[ClinicalDateFinding] = []

    # Overall AE onset: singleton pair (both directions).
    if onset1 is not None and onset2 is not None:
        findings.append(_build_finding(
            onset1, onset2, r1.report_id,
            EntityType.OVERALL_AE_ONSET, OVERALL_AE_ONSET_LABEL,
            _classify_overall_ae_onset, downgrade_dates2,
        ))
        findings.append(_build_finding(
            onset2, onset1, r2.report_id,
            EntityType.OVERALL_AE_ONSET, OVERALL_AE_ONSET_LABEL,
            _classify_overall_ae_onset, downgrade_dates1,
        ))

    # Drugs: per matched normalized substance, align and emit pairs.
    # Source-side role picks entity_type (concomitant → downgrade applies).
    drug_groups_1 = _collect_drug_date_groups(r1)
    drug_groups_2 = _collect_drug_date_groups(r2)
    for substance in sorted(set(drug_groups_1) & set(drug_groups_2)):
        entries_1 = _dedup_drug_date_entries(drug_groups_1[substance])
        entries_2 = _dedup_drug_date_entries(drug_groups_2[substance])
        label = _drug_start_label(substance)
        for e1, e2 in _align_drug_date_entries(entries_1, entries_2):
            findings.append(_build_finding(
                e1.partial_date, e2.partial_date, r1.report_id,
                _drug_entity_type(e1.role), label,
                _classify_entity, downgrade_dates2,
            ))
            findings.append(_build_finding(
                e2.partial_date, e1.partial_date, r2.report_id,
                _drug_entity_type(e2.role), label,
                _classify_entity, downgrade_dates1,
            ))

    # AEs: per matched normalized PT, align and emit pairs.
    ae_groups_1 = _collect_ae_date_groups(r1)
    ae_groups_2 = _collect_ae_date_groups(r2)
    for pt in sorted(set(ae_groups_1) & set(ae_groups_2)):
        dates_1 = _dedup_partial_dates(ae_groups_1[pt])
        dates_2 = _dedup_partial_dates(ae_groups_2[pt])
        label = _ae_onset_label(pt)
        for d1, d2 in _align_partial_date_lists(dates_1, dates_2):
            findings.append(_build_finding(
                d1, d2, r1.report_id,
                EntityType.AE_ONSET, label,
                _classify_entity, downgrade_dates2,
            ))
            findings.append(_build_finding(
                d2, d1, r2.report_id,
                EntityType.AE_ONSET, label,
                _classify_entity, downgrade_dates1,
            ))

    # --- Decisive impossibility findings ---
    decisive_findings: list[ClinicalDateFinding] = []
    seen_decisive: set[tuple[str, str]] = set()

    for report, other_report, other_onset in [
        (r1, r2, onset2),
        (r2, r1, onset1),
    ]:
        if other_onset is None:
            continue
        for dv in report.report_date_values:
            pd = _datevalue_to_partial(dv)
            finding = _check_decisive_impossibility(
                pd, report.report_id, other_onset, other_report.report_id,
            )
            if finding is not None:
                # Dedup: avoid counting identical directional impossibility
                key = (report.report_id, other_report.report_id)
                if key not in seen_decisive:
                    seen_decisive.add(key)
                    decisive_findings.append(finding)

    # --- Source-date bucketing for non-decisive findings ---
    _bucket_and_select(findings)

    # --- Cross-direction dedup (keep lower severity per entity) ---
    _dedup_across_directions(findings)

    # Combine
    all_findings = findings + decisive_findings

    # Tally
    result = ClinicalDateResult(findings=all_findings)
    for f in all_findings:
        if not f.selected:
            continue
        if f.final_severity == Severity.MINOR:
            result.n_minor += 1
        elif f.final_severity == Severity.MODERATE:
            result.n_moderate += 1
        elif f.final_severity == Severity.STRONG:
            result.n_strong += 1
        elif f.final_severity == Severity.DECISIVE:
            result.n_decisive += 1

    return result
