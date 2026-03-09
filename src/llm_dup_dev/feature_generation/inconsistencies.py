"""Detect inconsistencies between two reports.

Evaluates structured demographic attributes and identifies
differences that may indicate different individuals.

Inconsistencies do not automatically imply different individuals;
they are interpreted together with matching evidence in the
decision logic.

See: docs/same_individual_assessment_spec.md § Inconsistency Detection
"""

from __future__ import annotations

from llm_dup_dev.classifier.config import InconsistencyThresholds
from llm_dup_dev.models.enums import InconsistencySeverity
from llm_dup_dev.models.pair_features import Inconsistency
from llm_dup_dev.models.report import Report


def detect_inconsistencies(
    r1: Report,
    r2: Report,
    thresholds: InconsistencyThresholds,
) -> list[Inconsistency]:
    """Detect all structured-data inconsistencies between two reports.

    Checks: age, height, weight, sex.

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

    severity_order = {
        InconsistencySeverity.STRONG: 0,
        InconsistencySeverity.MODERATE: 1,
    }
    inconsistencies.sort(key=lambda x: severity_order.get(x.severity, 2))

    return inconsistencies


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
    """Classify a numeric difference into severity levels."""
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
