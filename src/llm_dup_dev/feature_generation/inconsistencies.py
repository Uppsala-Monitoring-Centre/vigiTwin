"""Detect inconsistencies between two reports.

Evaluates structured demographic attributes and identifies
differences that may indicate different individuals.

Inconsistencies do not automatically imply different individuals;
they are interpreted together with matching evidence in the
decision logic.

Clinical-date inconsistencies (drug start dates, AE onset dates,
report dates) are handled by the dedicated rule-based module in
``clinical_date_inconsistencies.py`` and exposed separately on
``PairFeatures.clinical_date_inconsistency_counts``.

See: docs/same_individual_assessment_spec.md § Inconsistency Detection
"""

from __future__ import annotations

from llm_dup_dev.classifier.config import InconsistencyThresholds
from llm_dup_dev.models.enums import DrugRole, InconsistencySeverity
from llm_dup_dev.models.pair_features import Inconsistency
from llm_dup_dev.models.report import Report


def detect_inconsistencies(
    r1: Report,
    r2: Report,
    thresholds: InconsistencyThresholds,
) -> list[Inconsistency]:
    """Detect all structured-data inconsistencies between two reports.

    Checks: age, height, weight, sex, drug set (role-based).

    Clinical-date inconsistencies are handled separately by the
    rule-based module ``clinical_date_inconsistencies.py``.

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

    severity_order = {
        InconsistencySeverity.STRONG: 0,
        InconsistencySeverity.MODERATE: 1,
        InconsistencySeverity.MINOR: 2,
    }
    inconsistencies.sort(key=lambda x: severity_order.get(x.severity, 3))

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
