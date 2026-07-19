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

See: docs/duplicate_adjudication_spec.md § Inconsistency signals
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

    Checks: age, height, weight, sex, country of origin, drug set
    (role-based).

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
    inconsistencies.extend(_check_country(r1, r2))
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
    minor_threshold: float = 0.0,
    decisive_threshold: float | None = None,
) -> list[Inconsistency]:
    """Classify a numeric difference into severity levels.

    A non-zero difference at or above ``minor_threshold`` and below the
    moderate threshold is counted as a minor inconsistency. The default
    ``minor_threshold=0.0`` preserves the original "any non-zero" rule
    for attributes (age) where recording precision is not a concern;
    weight and height pass a positive value to suppress sub-1
    rounding-precision differences.

    When ``decisive_threshold`` is provided and the difference meets or
    exceeds it, a decisive inconsistency is emitted instead of a strong
    one. Used for age, where gaps of 11+ years cannot plausibly be
    explained by typos or extraction errors.
    """
    if decisive_threshold is not None and min_diff >= decisive_threshold:
        return [
            Inconsistency(
                feature=feature,
                severity=InconsistencySeverity.DECISIVE,
                difference=min_diff,
                units=units,
            )
        ]
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
    if min_diff > 0 and min_diff >= minor_threshold:
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


def _classify_numeric_inconsistency(
    values_1: list[float],
    values_2: list[float],
    *,
    feature: str,
    units: str,
    moderate_threshold: float,
    strong_threshold: float,
    minor_threshold: float = 0.0,
    decisive_threshold: float | None = None,
) -> list[Inconsistency]:
    """Shared shape for numeric-attribute inconsistency checks.

    Returns no inconsistencies when either side is empty.  Otherwise
    computes the minimum absolute pairwise difference and delegates to
    :func:`_classify_numeric_difference` for severity bucketing.
    """
    if not values_1 or not values_2:
        return []

    min_diff = min(abs(v1 - v2) for v1 in values_1 for v2 in values_2)
    return _classify_numeric_difference(
        feature=feature,
        min_diff=min_diff,
        units=units,
        moderate_threshold=moderate_threshold,
        strong_threshold=strong_threshold,
        minor_threshold=minor_threshold,
        decisive_threshold=decisive_threshold,
    )


def _check_age(
    r1: Report,
    r2: Report,
    thresholds: InconsistencyThresholds,
) -> list[Inconsistency]:
    """Check for age inconsistencies (in years)."""
    return _classify_numeric_inconsistency(
        _ages_in_years(r1),
        _ages_in_years(r2),
        feature="patient_age",
        units="years",
        moderate_threshold=thresholds.age_moderate,
        strong_threshold=thresholds.age_strong,
        decisive_threshold=thresholds.age_decisive,
    )


def _check_height(
    r1: Report,
    r2: Report,
    thresholds: InconsistencyThresholds,
) -> list[Inconsistency]:
    """Check for height inconsistencies (in cm)."""
    return _classify_numeric_inconsistency(
        [v.value for v in r1.patient_height_values],
        [v.value for v in r2.patient_height_values],
        feature="patient_height",
        units="cm",
        minor_threshold=thresholds.height_minor,
        moderate_threshold=thresholds.height_moderate,
        strong_threshold=thresholds.height_strong,
    )


def _check_weight(
    r1: Report,
    r2: Report,
    thresholds: InconsistencyThresholds,
) -> list[Inconsistency]:
    """Check for weight inconsistencies (in kg)."""
    return _classify_numeric_inconsistency(
        [v.value for v in r1.patient_weight_values],
        [v.value for v in r2.patient_weight_values],
        feature="patient_weight",
        units="kg",
        minor_threshold=thresholds.weight_minor,
        moderate_threshold=thresholds.weight_moderate,
        strong_threshold=thresholds.weight_strong,
    )


def _classify_set_overlap_inconsistency(
    values_1: list,
    values_2: list,
    *,
    feature: str,
    severity: InconsistencySeverity,
    label: str,
) -> list[Inconsistency]:
    """Shared shape for multi-valued string-attribute checks.

    Lower-cases the values on both sides; returns no inconsistencies
    when either side is empty or the lower-cased sets overlap.
    Otherwise emits a single inconsistency at ``severity`` describing
    the disjoint sets.
    """
    if not values_1 or not values_2:
        return []

    set_1 = {v.value.lower() for v in values_1}
    set_2 = {v.value.lower() for v in values_2}

    if set_1 & set_2:
        return []

    return [
        Inconsistency(
            feature=feature,
            severity=severity,
            description=f"{label} mismatch: {sorted(set_1)} vs {sorted(set_2)}",
        )
    ]


def _check_sex(r1: Report, r2: Report) -> list[Inconsistency]:
    """Check for sex inconsistencies.

    Any mismatch in recorded patient sex is a strong inconsistency.
    Missing values (empty lists) do not produce inconsistencies.
    """
    return _classify_set_overlap_inconsistency(
        r1.patient_sex_values,
        r2.patient_sex_values,
        feature="patient_sex",
        severity=InconsistencySeverity.STRONG,
        label="Sex",
    )


def _check_country(r1: Report, r2: Report) -> list[Inconsistency]:
    """Check for country-of-origin inconsistencies.

    A mismatch in recorded country of origin is a minor inconsistency:
    cross-country duplication must remain detectable, so the effect on
    the weighted inconsistency score is limited, but the mismatch is
    still surfaced for reviewer awareness. Missing values on either
    side do not produce an inconsistency.
    """
    return _classify_set_overlap_inconsistency(
        r1.country_of_origin_values,
        r2.country_of_origin_values,
        feature="patient_country",
        severity=InconsistencySeverity.MINOR,
        label="Country",
    )


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


def _substance_components(name: str) -> set[str]:
    """Split a substance name into lowercased ingredient components.

    Combination products are coded with ';' between ingredients in
    WHODrug (e.g. "valsartan;amlodipine"); single-ingredient names
    yield themselves as the only component. Splitting is exact-match
    on components — never substring — so distinct substances that
    contain each other's names (e.g. "prednisolone" vs
    "methylprednisolone") remain distinct.
    """
    return {part.strip().lower() for part in name.split(";") if part.strip()}


def _all_substance_components(report: Report) -> set[str]:
    """Union of ingredient components across all drugs regardless of role."""
    components: set[str] = set()
    for d in report.drugs:
        components |= _substance_components(d.substance)
    return components


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

    Combination-product override: presence is checked at the level of
    semicolon-split ingredient components, so a key drug counts as found
    when all of its components appear among the components of the target
    report's drugs. A combination coded as separate ingredients on the
    other report (or an ingredient covered by a combination there) is
    therefore not inconsistent, while a genuinely unreported ingredient
    still is.

    Returns 0, 1, or 2 moderate inconsistencies.
    """
    key_1 = _key_substance_names(r1)
    key_2 = _key_substance_names(r2)
    components_1 = _all_substance_components(r1)
    components_2 = _all_substance_components(r2)

    inconsistencies: list[Inconsistency] = []

    # Direction 1: r1 key drugs missing from r2 all drugs
    if key_1:
        missing_in_r2 = {
            name
            for name in key_1
            if not _substance_components(name) <= components_2
        }
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
        missing_in_r1 = {
            name
            for name in key_2
            if not _substance_components(name) <= components_1
        }
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
