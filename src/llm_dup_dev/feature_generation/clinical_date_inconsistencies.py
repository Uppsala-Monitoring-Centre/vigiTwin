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

See: docs/same_individual_assessment_spec.md § Clinical Date Inconsistencies
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from enum import Enum, IntEnum
from typing import Literal

from llm_dup_dev.models.enums import DrugRole, InconsistencySeverity
from llm_dup_dev.models.report import DateValue, PartialDate, Report


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

# Drug start / AE onset thresholds (more lenient)
_ENTITY_MINOR_DAYS = 14
_ENTITY_MODERATE_DAYS = 60


def _classify_overall_ae_onset(diff_days: int) -> Severity:
    if diff_days == 0:
        return Severity.NONE
    if diff_days <= _AE_ONSET_MINOR_DAYS:
        return Severity.MINOR
    if diff_days <= _AE_ONSET_MODERATE_DAYS:
        return Severity.MODERATE
    return Severity.STRONG


def _classify_entity(diff_days: int) -> Severity:
    if diff_days == 0:
        return Severity.NONE
    if diff_days <= _ENTITY_MINOR_DAYS:
        return Severity.MINOR
    if diff_days <= _ENTITY_MODERATE_DAYS:
        return Severity.MODERATE
    return Severity.STRONG


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
    """Derive the overall AE onset date for a single report.

    Primary rule:
        If at least one suspected-drug start date exists, pick the
        earliest AE onset date >= max(suspected_start_dates).

    Fallback:
        If no suspected-drug start dates, pick the earliest AE onset
        date.

    Comparisons use the *latest plausible* value for suspected-drug
    starts (conservative: we want AE onsets definitely after) and the
    *earliest plausible* value for AE onset dates.
    """
    suspected_starts: list[PartialDate] = [
        d.start_date
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

    if suspected_starts:
        # max suspected start: use latest plausible date for comparison
        max_suspected = max(suspected_starts, key=latest_plausible_date)
        max_suspected_latest = latest_plausible_date(max_suspected)

        # Filter AE onsets whose earliest plausible date >= max suspected latest
        qualifying = [
            ao for ao in ae_onsets
            if earliest_plausible_date(ao) >= max_suspected_latest
        ]
        if not qualifying:
            return None
        return min(qualifying, key=earliest_plausible_date)

    # Fallback: earliest AE onset
    return min(ae_onsets, key=earliest_plausible_date)


# ---------------------------------------------------------------------------
# Collect all dated entities from a report
# ---------------------------------------------------------------------------


@dataclass
class _DatedEntity:
    entity_type: EntityType
    label: str
    partial_date: PartialDate
    drug_name: str | None = None  # for drug-based entities


def _collect_entities(
    report: Report,
    overall_ae_onset: PartialDate | None,
) -> list[_DatedEntity]:
    """Collect all primary dated entities from a report."""
    entities: list[_DatedEntity] = []

    if overall_ae_onset is not None:
        entities.append(_DatedEntity(
            entity_type=EntityType.OVERALL_AE_ONSET,
            label="overall_ae_onset",
            partial_date=overall_ae_onset,
        ))

    for drug in report.drugs:
        if drug.start_date is None:
            continue
        is_concomitant = drug.role == DrugRole.CONCOMITANT
        etype = (
            EntityType.CONCOMITANT_DRUG_START
            if is_concomitant
            else EntityType.SUSPECTED_DRUG_START
        )
        entities.append(_DatedEntity(
            entity_type=etype,
            label=f"drug_start:{drug.substance.lower()}",
            partial_date=drug.start_date,
            drug_name=drug.substance.lower(),
        ))

    for ae in report.adverse_events:
        if ae.start_date is None:
            continue
        entities.append(_DatedEntity(
            entity_type=EntityType.AE_ONSET,
            label=f"ae_onset:{ae.pt_name.lower()}",
            partial_date=ae.start_date,
        ))

    return entities


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
# Match entities across reports
# ---------------------------------------------------------------------------


def _find_match_on_other(
    entity: _DatedEntity,
    other_entities: list[_DatedEntity],
) -> PartialDate | None:
    """Find the best matching entity on the other report.

    Drug starts match by normalized drug name (closest date if multiple).
    AE onsets match by normalized pt_name (closest date if multiple).
    Overall AE onset matches if the other report also has one.

    Returns the matched PartialDate or None.
    """
    if entity.entity_type == EntityType.OVERALL_AE_ONSET:
        candidates = [
            e for e in other_entities
            if e.entity_type == EntityType.OVERALL_AE_ONSET
        ]
    elif entity.entity_type in (
        EntityType.SUSPECTED_DRUG_START,
        EntityType.CONCOMITANT_DRUG_START,
    ):
        candidates = [
            e for e in other_entities
            if e.entity_type in (
                EntityType.SUSPECTED_DRUG_START,
                EntityType.CONCOMITANT_DRUG_START,
            )
            and e.drug_name == entity.drug_name
        ]
    else:  # AE_ONSET
        candidates = [
            e for e in other_entities
            if e.entity_type == EntityType.AE_ONSET
            and e.label == entity.label
        ]

    if not candidates:
        return None

    # Pick candidate with minimum day difference (generous: closest match)
    return min(
        (c.partial_date for c in candidates),
        key=lambda pd: _min_day_difference(entity.partial_date, pd),
    )


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
    """Downgrade concomitant drug start dates by two steps."""
    reason = "concomitant drug downgrade (-2 steps)"
    if severity == Severity.STRONG:
        return Severity.MINOR, reason
    if severity == Severity.MODERATE:
        return Severity.IGNORED, reason
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
    # Derive overall AE onset dates
    onset1 = derive_overall_ae_onset_date(r1)
    onset2 = derive_overall_ae_onset_date(r2)

    # Collect entities
    entities1 = _collect_entities(r1, onset1)
    entities2 = _collect_entities(r2, onset2)

    # Collect all dates for downgrade checks
    downgrade_dates1 = _collect_all_dates_for_downgrade(r1, onset1)
    downgrade_dates2 = _collect_all_dates_for_downgrade(r2, onset2)

    findings: list[ClinicalDateFinding] = []

    # --- Standard discrepancy findings (both directions) ---
    for source_entities, other_entities, source_report, other_downgrade_dates in [
        (entities1, entities2, r1.report_id, downgrade_dates2),
        (entities2, entities1, r2.report_id, downgrade_dates1),
    ]:
        for entity in source_entities:
            matched_date = _find_match_on_other(entity, other_entities)
            if matched_date is None:
                # No match → no inconsistency (missing data rule)
                continue

            diff = _min_day_difference(entity.partial_date, matched_date)

            # Classify based on entity type
            if entity.entity_type == EntityType.OVERALL_AE_ONSET:
                raw_sev = _classify_overall_ae_onset(diff)
            else:
                raw_sev = _classify_entity(diff)

            if raw_sev == Severity.NONE:
                findings.append(ClinicalDateFinding(
                    source_report=source_report,
                    entity_type=entity.entity_type,
                    entity_label=entity.label,
                    source_date=entity.partial_date,
                    matched_date=matched_date,
                    raw_diff_days=diff,
                    raw_severity=Severity.NONE,
                    final_severity=Severity.NONE,
                ))
                continue

            current_sev = raw_sev
            downgrades: list[str] = []

            # Concomitant drug downgrade (apply first — strongest downgrade)
            if entity.entity_type == EntityType.CONCOMITANT_DRUG_START:
                current_sev, reason = _apply_concomitant_downgrade(current_sev)
                downgrades.append(reason)

            # Cross-entity downgrade (only if still countable)
            if current_sev not in (Severity.NONE, Severity.IGNORED):
                current_sev, reason = _apply_cross_entity_downgrade(
                    current_sev,
                    entity.partial_date,
                    other_downgrade_dates,
                )
                if reason:
                    downgrades.append(reason)

            findings.append(ClinicalDateFinding(
                source_report=source_report,
                entity_type=entity.entity_type,
                entity_label=entity.label,
                source_date=entity.partial_date,
                matched_date=matched_date,
                raw_diff_days=diff,
                raw_severity=raw_sev,
                downgrades=downgrades,
                final_severity=current_sev,
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
