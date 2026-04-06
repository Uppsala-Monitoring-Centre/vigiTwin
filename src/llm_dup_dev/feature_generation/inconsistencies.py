"""Detect inconsistencies between two reports.

Evaluates structured demographic attributes and identifies
differences that may indicate different individuals.

Inconsistencies do not automatically imply different individuals;
they are interpreted together with matching evidence in the
decision logic.

See: docs/same_individual_assessment_spec.md § Inconsistency Detection
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date

from llm_dup_dev.classifier.config import InconsistencyThresholds
from llm_dup_dev.models.enums import DrugRole, InconsistencySeverity
from llm_dup_dev.models.pair_features import Inconsistency
from llm_dup_dev.models.report import PartialDate, Report


def detect_inconsistencies(
    r1: Report,
    r2: Report,
    thresholds: InconsistencyThresholds,
) -> list[Inconsistency]:
    """Detect all structured-data inconsistencies between two reports.

    Checks: age, height, weight, sex, drug set (role-based), drug dates (timing).

    For multi-valued attributes, the *minimum* difference across all
    value pairs is used (conservative: if any pair of values is close,
    no inconsistency is raised).

    Returns inconsistencies sorted by severity (strong first).
    """
    inconsistencies: list[Inconsistency] = []

    inconsistencies.extend(_check_age(r1, r2, thresholds))
    inconsistencies.extend(_check_height(r1, r2, thresholds))
    inconsistencies.extend(_check_weight(r1, r2, thresholds))
    inconsistencies.extend(_check_sex(r1, r2))
    inconsistencies.extend(_check_drug_set(r1, r2))
    inconsistencies.extend(_check_drug_dates(r1, r2, thresholds))

    severity_order = {
        InconsistencySeverity.STRONG: 0,
        InconsistencySeverity.MODERATE: 1,
        InconsistencySeverity.MINOR: 2,
    }
    inconsistencies.sort(key=lambda x: severity_order.get(x.severity, 3))

    return inconsistencies


# ---------------------------------------------------------------------------
# Drug-date inconsistency
# ---------------------------------------------------------------------------

DateInterval = tuple[date, date]


def _partial_date_to_interval(pd: PartialDate) -> DateInterval:
    """Convert a PartialDate to a [start, end] date interval.

    day   → [date, date]
    month → [YYYY-MM-01, last day of month]
    year  → [YYYY-01-01, YYYY-12-31]
    """
    if pd.resolution == "day":
        d = date.fromisoformat(pd.value)
        return (d, d)
    if pd.resolution == "month":
        year, month = int(pd.value[:4]), int(pd.value[5:7])
        last_day = calendar.monthrange(year, month)[1]
        return (date(year, month, 1), date(year, month, last_day))
    # year
    year = int(pd.value[:4])
    return (date(year, 1, 1), date(year, 12, 31))


def _interval_distance_days(iv1: DateInterval, iv2: DateInterval) -> int:
    """Minimum distance in days between two date intervals.

    Returns 0 if the intervals overlap, otherwise the gap in days.
    Uses .days on the timedelta result to return an int for threshold
    comparisons.
    """
    start1, end1 = iv1
    start2, end2 = iv2
    if start1 <= end2 and start2 <= end1:
        return 0
    return min(
        abs((start1 - end2).days),
        abs((start2 - end1).days),
    )


def _date_weight(diff_days: int, thresholds: InconsistencyThresholds) -> float:
    """Map a date difference in days to an inconsistency weight.

    diff = 0             → 0.0  (exact or overlapping → no contribution)
    1 ≤ diff ≤ small     → small_weight  (0.5)
    small < diff ≤ mod   → moderate_weight  (1.0)
    diff > moderate      → large_weight  (3.0)
    """
    if diff_days == 0:
        return 0.0
    if diff_days <= thresholds.drug_date_small_days:
        return thresholds.drug_date_small_weight
    if diff_days <= thresholds.drug_date_moderate_days:
        return thresholds.drug_date_moderate_weight
    return thresholds.drug_date_large_weight


def _check_drug_dates(
    r1: Report,
    r2: Report,
    thresholds: InconsistencyThresholds,
) -> list[Inconsistency]:
    """Check for drug date (timing) inconsistencies in shared drugs.

    Only drugs present in both reports are evaluated. For each shared drug:
    - Compare start dates (if both reports have at least one)
    - Compare stop dates (if both reports have at least one)
    - Use the minimum pairwise interval distance when multiple dates exist
    - Down-weight if the drug is concomitant in both reports
    - Cap per-drug contribution at drug_date_cap_per_drug

    Returns 0 or 1 Inconsistency (moderate or strong).
    """
    groups1: dict[str, list] = defaultdict(list)
    groups2: dict[str, list] = defaultdict(list)
    for d in r1.drugs:
        groups1[d.substance.lower()].append(d)
    for d in r2.drugs:
        groups2[d.substance.lower()].append(d)

    shared = set(groups1) & set(groups2)
    if not shared:
        return []

    total_score = 0.0

    for substance in shared:
        drugs1 = groups1[substance]
        drugs2 = groups2[substance]

        # Collect non-None date intervals — None guard is here, not in the helper
        starts1 = [_partial_date_to_interval(d.start_date) for d in drugs1 if d.start_date is not None]
        starts2 = [_partial_date_to_interval(d.start_date) for d in drugs2 if d.start_date is not None]
        ends1 = [_partial_date_to_interval(d.end_date) for d in drugs1 if d.end_date is not None]
        ends2 = [_partial_date_to_interval(d.end_date) for d in drugs2 if d.end_date is not None]

        drug_score = 0.0

        if starts1 and starts2:
            min_diff = min(
                _interval_distance_days(iv1, iv2)
                for iv1 in starts1
                for iv2 in starts2
            )
            drug_score += _date_weight(min_diff, thresholds)

        if ends1 and ends2:
            min_diff = min(
                _interval_distance_days(iv1, iv2)
                for iv1 in ends1
                for iv2 in ends2
            )
            drug_score += _date_weight(min_diff, thresholds)

        # Down-weight when the substance is concomitant on both sides
        all_concomitant = all(d.role == DrugRole.CONCOMITANT for d in drugs1) and all(
            d.role == DrugRole.CONCOMITANT for d in drugs2
        )
        if all_concomitant:
            drug_score *= thresholds.drug_date_concomitant_factor

        total_score += min(thresholds.drug_date_cap_per_drug, drug_score)

    if total_score >= thresholds.drug_date_score_strong:
        return [
            Inconsistency(
                feature="drug_dates",
                severity=InconsistencySeverity.STRONG,
                difference=total_score,
                description=f"Drug date inconsistency score: {total_score:.2f}",
            )
        ]
    if total_score >= thresholds.drug_date_score_moderate:
        return [
            Inconsistency(
                feature="drug_dates",
                severity=InconsistencySeverity.MODERATE,
                difference=total_score,
                description=f"Drug date inconsistency score: {total_score:.2f}",
            )
        ]
    return []

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _classify_numeric_difference(
    feature: str,
    min_diff: float,
    units: str,
    moderate_threshold: float,
    strong_threshold: float,
) -> list[Inconsistency]:
    """Classify a numeric difference into severity levels.

    Any non-zero difference below the moderate threshold is counted as
    a minor inconsistency.
    """
    if min_diff >= strong_threshold:
        return [
            Inconsistency(
                feature=feature,
                severity=InconsistencySeverity.STRONG,
                difference=min_diff,
                units=units,
            )
        ]
    if min_diff >= moderate_threshold:
        return [
            Inconsistency(
                feature=feature,
                severity=InconsistencySeverity.MODERATE,
                difference=min_diff,
                units=units,
            )
        ]
    if min_diff > 0:
        return [
            Inconsistency(
                feature=feature,
                severity=InconsistencySeverity.MINOR,
                difference=min_diff,
                units=units,
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Per-feature checks
# ---------------------------------------------------------------------------


def _ages_in_years(report: Report) -> list[float]:
    """Extract all age values converted to years."""
    result: list[float] = []
    for av in report.patient_age_values:
        if av.unit == "years":
            result.append(av.value)
        elif av.unit == "months":
            result.append(av.value / 12.0)
        elif av.unit == "days":
            result.append(av.value / 365.25)
        else:
            result.append(av.value)  # assume years
    return result


def _check_age(
    r1: Report,
    r2: Report,
    thresholds: InconsistencyThresholds,
) -> list[Inconsistency]:
    """Check for age inconsistencies (in years)."""
    ages_1 = _ages_in_years(r1)
    ages_2 = _ages_in_years(r2)

    if not ages_1 or not ages_2:
        return []

    min_diff = min(abs(a1 - a2) for a1 in ages_1 for a2 in ages_2)

    return _classify_numeric_difference(
        feature="patient_age",
        min_diff=min_diff,
        units="years",
        moderate_threshold=thresholds.age_moderate,
        strong_threshold=thresholds.age_strong,
    )


def _check_height(
    r1: Report,
    r2: Report,
    thresholds: InconsistencyThresholds,
) -> list[Inconsistency]:
    """Check for height inconsistencies (in cm)."""
    if not r1.patient_height_values or not r2.patient_height_values:
        return []

    vals_1 = [v.value for v in r1.patient_height_values]
    vals_2 = [v.value for v in r2.patient_height_values]
    min_diff = min(abs(v1 - v2) for v1 in vals_1 for v2 in vals_2)

    return _classify_numeric_difference(
        feature="patient_height",
        min_diff=min_diff,
        units="cm",
        moderate_threshold=thresholds.height_moderate,
        strong_threshold=thresholds.height_strong,
    )


def _check_weight(
    r1: Report,
    r2: Report,
    thresholds: InconsistencyThresholds,
) -> list[Inconsistency]:
    """Check for weight inconsistencies (in kg)."""
    if not r1.patient_weight_values or not r2.patient_weight_values:
        return []

    vals_1 = [v.value for v in r1.patient_weight_values]
    vals_2 = [v.value for v in r2.patient_weight_values]
    min_diff = min(abs(v1 - v2) for v1 in vals_1 for v2 in vals_2)

    return _classify_numeric_difference(
        feature="patient_weight",
        min_diff=min_diff,
        units="kg",
        moderate_threshold=thresholds.weight_moderate,
        strong_threshold=thresholds.weight_strong,
    )


def _check_sex(r1: Report, r2: Report) -> list[Inconsistency]:
    """Check for sex inconsistencies.

    Any mismatch in recorded patient sex is a strong inconsistency.
    Missing values (empty lists) do not produce inconsistencies.
    """
    if not r1.patient_sex_values or not r2.patient_sex_values:
        return []

    sexes_1 = {v.value.lower() for v in r1.patient_sex_values}
    sexes_2 = {v.value.lower() for v in r2.patient_sex_values}

    # If any overlap exists, no inconsistency
    if sexes_1 & sexes_2:
        return []

    return [
        Inconsistency(
            feature="patient_sex",
            severity=InconsistencySeverity.STRONG,
            description=f"Sex mismatch: {sorted(sexes_1)} vs {sorted(sexes_2)}",
        )
    ]


# ---------------------------------------------------------------------------
# Drug-set inconsistency
# ---------------------------------------------------------------------------

_KEY_ROLES = {DrugRole.SUSPECTED, DrugRole.INTERACTING}


def _key_substance_names(report: Report) -> set[str]:
    """Extract lowercased substance names for drugs with key roles.

    Key roles are 'suspected' and 'interacting'. Drugs with
    role=None or role='concomitant' are excluded.
    """
    return {
        d.substance.lower()
        for d in report.drugs
        if d.role in _KEY_ROLES
    }


def _all_substance_names(report: Report) -> set[str]:
    """Extract lowercased substance names for all drugs regardless of role."""
    return {d.substance.lower() for d in report.drugs}


def _check_drug_set(r1: Report, r2: Report) -> list[Inconsistency]:
    """Check for directional drug-set inconsistencies.

    For each direction (r1→r2, r2→r1): if any key drug from the source
    report is NOT present in all drugs of the target report, emit one
    moderate inconsistency for that direction. Multiple missing key drugs
    in the same direction still produce only one inconsistency.

    Key drugs are those with role 'suspected' or 'interacting'.
    Concomitant-only drugs never trigger inconsistencies.
    A drug present in both reports with different roles is NOT inconsistent.
    Reports with no key drugs (including role=None) produce no inconsistencies.

    Returns 0, 1, or 2 moderate inconsistencies.
    """
    key_1 = _key_substance_names(r1)
    key_2 = _key_substance_names(r2)
    all_1 = _all_substance_names(r1)
    all_2 = _all_substance_names(r2)

    inconsistencies: list[Inconsistency] = []

    # Direction 1: r1 key drugs missing from r2 all drugs
    if key_1:
        missing_in_r2 = key_1 - all_2
        if missing_in_r2:
            inconsistencies.append(
                Inconsistency(
                    feature="drug_set",
                    severity=InconsistencySeverity.MODERATE,
                    description=(
                        f"Key drug(s) in report 1 not found in report 2: "
                        f"{sorted(missing_in_r2)}"
                    ),
                )
            )

    # Direction 2: r2 key drugs missing from r1 all drugs
    if key_2:
        missing_in_r1 = key_2 - all_1
        if missing_in_r1:
            inconsistencies.append(
                Inconsistency(
                    feature="drug_set",
                    severity=InconsistencySeverity.MODERATE,
                    description=(
                        f"Key drug(s) in report 2 not found in report 1: "
                        f"{sorted(missing_in_r1)}"
                    ),
                )
            )

    return inconsistencies
