"""Feature matching between two reports.

Computes shared clinical features (drug sets, AE sets) and
demographic attribute matches using set intersection semantics.

Matching rule for multi-valued attributes: v1 ∩ v2 ≠ ∅
(at least one candidate value must match between the two reports).

See: docs/data_contracts.md § Set semantics and Matching rules
See: docs/same_individual_assessment_spec.md § Matching Clinical Feature Sets
"""

from __future__ import annotations

from dataclasses import dataclass, field

from llm_dup_dev.models.report import (
    AgeValue,
    DateValue,
    MeasurementValue,
    PartialDate,
    Report,
)


# ---------------------------------------------------------------------------
# Resolution-aware date comparison helpers
# ---------------------------------------------------------------------------

_RESOLUTION_RANK = {"year": 0, "month": 1, "day": 2}


def _coarser_resolution(r1: str, r2: str) -> str:
    """Return the coarser of two date resolutions."""
    if _RESOLUTION_RANK.get(r1, 0) <= _RESOLUTION_RANK.get(r2, 0):
        return r1
    return r2


def _truncate_to_resolution(date_str: str, resolution: str) -> str:
    """Truncate a date string to the given resolution.

    "2020-06-12" at month → "2020-06"
    "2020-06-12" at year  → "2020"
    """
    if resolution == "year":
        return date_str[:4]
    elif resolution == "month":
        return date_str[:7]
    return date_str  # day — full string


# ---------------------------------------------------------------------------
# Match result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DemographicMatch:
    """Result of comparing a single demographic attribute between two reports."""

    feature: str
    matched: bool
    values_r1: list = field(default_factory=list)
    values_r2: list = field(default_factory=list)
    matched_value: object = None
    date_resolution: str | None = None  # for date features: "day"/"month"/"year"


@dataclass
class ClinicalDateMatch:
    """A unique date that matched across two reports via explicit date-field
    coincidence for a shared drug or adverse event.

    This initial implementation uses explicit date coincidence only — NOT
    interval overlap.  A drug exposure like start=1994-03-30, stop=1994-05-12
    does NOT imply that every date in between is available for matching.

    Only same-field-type comparisons count (start↔start, end↔end, onset↔onset)
    for the same matched entity.
    """

    matched_value: str  # date string truncated to matched resolution
    resolution: str  # "day", "month", or "year"


@dataclass
class MatchResult:
    """Complete matching result between two reports."""

    matching_drugs: list[str] = field(default_factory=list)  # sorted
    matching_AEs: list[str] = field(default_factory=list)  # sorted
    drugs_only_r1: list[str] = field(default_factory=list)
    drugs_only_r2: list[str] = field(default_factory=list)
    aes_only_r1: list[str] = field(default_factory=list)
    aes_only_r2: list[str] = field(default_factory=list)
    demographic_matches: list[DemographicMatch] = field(default_factory=list)
    matched_clinical_dates: list[ClinicalDateMatch] = field(default_factory=list)

    @property
    def has_drug_overlap(self) -> bool:
        return len(self.matching_drugs) > 0

    @property
    def has_ae_overlap(self) -> bool:
        return len(self.matching_AEs) > 0


# ---------------------------------------------------------------------------
# Drug and AE set matching
# ---------------------------------------------------------------------------


def compute_matching_drugs(
    r1: Report, r2: Report
) -> tuple[list[str], list[str], list[str]]:
    """Compute drug set intersection and differences.

    Drug substances are compared case-insensitively. The original casing
    from report 1 is preserved in the output.

    Returns
    -------
    (matching, only_r1, only_r2)
        All lists in canonical sorted order.
    """
    substances_1 = {d.substance.lower(): d.substance for d in r1.drugs}
    substances_2 = {d.substance.lower(): d.substance for d in r2.drugs}

    matching_keys = set(substances_1) & set(substances_2)
    only_r1_keys = set(substances_1) - set(substances_2)
    only_r2_keys = set(substances_2) - set(substances_1)

    matching = sorted(substances_1[k] for k in matching_keys)
    only_r1 = sorted(substances_1[k] for k in only_r1_keys)
    only_r2 = sorted(substances_2[k] for k in only_r2_keys)

    return matching, only_r1, only_r2


def compute_matching_aes(
    r1: Report, r2: Report
) -> tuple[list[str], list[str], list[str]]:
    """Compute adverse event set intersection and differences.

    PT names are compared case-insensitively. The original casing
    from report 1 is preserved in the output.

    Returns
    -------
    (matching, only_r1, only_r2)
        All lists in canonical sorted order.
    """
    pts_1 = {ae.pt_name.lower(): ae.pt_name for ae in r1.adverse_events}
    pts_2 = {ae.pt_name.lower(): ae.pt_name for ae in r2.adverse_events}

    matching_keys = set(pts_1) & set(pts_2)
    only_r1_keys = set(pts_1) - set(pts_2)
    only_r2_keys = set(pts_2) - set(pts_1)

    matching = sorted(pts_1[k] for k in matching_keys)
    only_r1 = sorted(pts_1[k] for k in only_r1_keys)
    only_r2 = sorted(pts_2[k] for k in only_r2_keys)

    return matching, only_r1, only_r2


# ---------------------------------------------------------------------------
# Demographic attribute matching
# ---------------------------------------------------------------------------


def _match_sex(r1: Report, r2: Report) -> DemographicMatch:
    """Check if patient sex values overlap (case-insensitive)."""
    vals_1 = {v.value.lower() for v in r1.patient_sex_values}
    vals_2 = {v.value.lower() for v in r2.patient_sex_values}
    overlap = vals_1 & vals_2
    return DemographicMatch(
        feature="patient_sex",
        matched=bool(overlap),
        values_r1=[v.value for v in r1.patient_sex_values],
        values_r2=[v.value for v in r2.patient_sex_values],
        matched_value=next(iter(overlap), None),
    )


def _match_age(r1: Report, r2: Report) -> DemographicMatch:
    """Check if patient age values overlap (exact numeric match)."""
    vals_1 = {v.value for v in r1.patient_age_values}
    vals_2 = {v.value for v in r2.patient_age_values}
    overlap = vals_1 & vals_2
    return DemographicMatch(
        feature="patient_age",
        matched=bool(overlap),
        values_r1=[v.value for v in r1.patient_age_values],
        values_r2=[v.value for v in r2.patient_age_values],
        matched_value=next(iter(overlap), None),
    )


def _match_measurement(
    feature_name: str,
    values_r1: list[MeasurementValue],
    values_r2: list[MeasurementValue],
) -> DemographicMatch:
    """Check if measurement values overlap (exact numeric match)."""
    nums_1 = {v.value for v in values_r1}
    nums_2 = {v.value for v in values_r2}
    overlap = nums_1 & nums_2
    return DemographicMatch(
        feature=feature_name,
        matched=bool(overlap),
        values_r1=[v.value for v in values_r1],
        values_r2=[v.value for v in values_r2],
        matched_value=next(iter(overlap), None),
    )


def _match_dates(
    feature_name: str,
    dates_r1: list[DateValue],
    dates_r2: list[DateValue],
) -> DemographicMatch:
    """Check if report date values overlap.

    Comparison is performed at the coarser resolution of each pair.
    The finest matching resolution is recorded for domain-size lookup.
    """
    best_resolution: str | None = None
    best_value: str | None = None

    for d1 in dates_r1:
        for d2 in dates_r2:
            coarse = _coarser_resolution(d1.resolution, d2.resolution)
            t1 = _truncate_to_resolution(d1.value, coarse)
            t2 = _truncate_to_resolution(d2.value, coarse)
            if t1 == t2:
                # Keep the finest (most informative) resolution among matches
                if best_resolution is None or (
                    _RESOLUTION_RANK.get(coarse, 0)
                    > _RESOLUTION_RANK.get(best_resolution, 0)
                ):
                    best_resolution = coarse
                    best_value = t1

    return DemographicMatch(
        feature=feature_name,
        matched=best_resolution is not None,
        values_r1=[d.value for d in dates_r1],
        values_r2=[d.value for d in dates_r2],
        matched_value=best_value,
        date_resolution=best_resolution,
    )


# ---------------------------------------------------------------------------
# Clinical date matching (drug start/end, AE onset)
# ---------------------------------------------------------------------------


def _compare_partial_dates(
    d1: PartialDate | None,
    d2: PartialDate | None,
) -> tuple[str, str] | None:
    """Compare two partial dates using resolution-aware logic.

    Returns ``(matched_value, resolution)`` if they match, else ``None``.
    The matched value is truncated to the coarser of the two resolutions.
    """
    if d1 is None or d2 is None:
        return None
    coarse = _coarser_resolution(d1.resolution, d2.resolution)
    t1 = _truncate_to_resolution(d1.value, coarse)
    t2 = _truncate_to_resolution(d2.value, coarse)
    if t1 == t2:
        return (t1, coarse)
    return None


def compute_matching_clinical_dates(
    r1: Report,
    r2: Report,
    matching_drugs: list[str],
    matching_AEs: list[str],
) -> list[ClinicalDateMatch]:
    """Compute unique matching dates from drug and AE date fields.

    Examines explicitly recorded date values only — NOT treatment intervals.
    A drug exposure period (start → stop) does NOT imply that intermediate
    dates are available for matching.

    Rules:
        1. Only same-field-type comparisons count:
           drug start ↔ drug start, drug end ↔ drug end, AE onset ↔ AE onset.
        2. Only dates for the same matched entity are compared.
        3. The result is collapsed to unique matched dates.  If the same
           calendar date matches for multiple drugs/AEs/field types, it
           contributes only once (at the finest matched resolution).
        4. Standard partial-date matching rules apply (month-only and
           year-only values match at their coarser resolution).

    Parameters
    ----------
    r1, r2 : Report
        The two reports being compared.
    matching_drugs : list[str]
        Drug substance names shared by both reports (from ``compute_matching_drugs``).
    matching_AEs : list[str]
        AE PT names shared by both reports (from ``compute_matching_aes``).

    Returns
    -------
    list[ClinicalDateMatch]
        Unique matching dates, sorted by matched value.
    """
    # Build lookup maps: substance/pt_name (lowercase) → entity
    drugs_r1 = {d.substance.lower(): d for d in r1.drugs}
    drugs_r2 = {d.substance.lower(): d for d in r2.drugs}
    aes_r1 = {ae.pt_name.lower(): ae for ae in r1.adverse_events}
    aes_r2 = {ae.pt_name.lower(): ae for ae in r2.adverse_events}

    # Collect unique matches: matched_value → finest resolution
    unique: dict[str, str] = {}

    for drug_name in matching_drugs:
        key = drug_name.lower()
        d1 = drugs_r1.get(key)
        d2 = drugs_r2.get(key)
        if d1 is None or d2 is None:
            continue

        # Compare start dates (start ↔ start only)
        result = _compare_partial_dates(d1.start_date, d2.start_date)
        if result is not None:
            _update_unique_dates(unique, result[0], result[1])

        # Compare end dates (end ↔ end only)
        result = _compare_partial_dates(d1.end_date, d2.end_date)
        if result is not None:
            _update_unique_dates(unique, result[0], result[1])

    for ae_name in matching_AEs:
        key = ae_name.lower()
        ae1 = aes_r1.get(key)
        ae2 = aes_r2.get(key)
        if ae1 is None or ae2 is None:
            continue

        # Compare onset dates (onset ↔ onset only)
        result = _compare_partial_dates(ae1.start_date, ae2.start_date)
        if result is not None:
            _update_unique_dates(unique, result[0], result[1])

    return [
        ClinicalDateMatch(matched_value=val, resolution=res)
        for val, res in sorted(unique.items())
    ]


def _update_unique_dates(
    unique: dict[str, str], value: str, resolution: str
) -> None:
    """Add a matched date, keeping the finest resolution for each value."""
    existing = unique.get(value)
    if existing is None or _RESOLUTION_RANK.get(
        resolution, 0
    ) > _RESOLUTION_RANK.get(existing, 0):
        unique[value] = resolution


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_matches(r1: Report, r2: Report) -> MatchResult:
    """Compute all feature matches between two reports.

    Returns matching drug/AE sets (canonical sorted order) and
    demographic attribute matches with provenance information.
    """
    matching_drugs, drugs_r1, drugs_r2 = compute_matching_drugs(r1, r2)
    matching_aes, aes_r1, aes_r2 = compute_matching_aes(r1, r2)

    demographic_matches = [
        _match_sex(r1, r2),
        _match_age(r1, r2),
        _match_measurement(
            "patient_height",
            r1.patient_height_values,
            r2.patient_height_values,
        ),
        _match_measurement(
            "patient_weight",
            r1.patient_weight_values,
            r2.patient_weight_values,
        ),
        _match_dates(
            "report_date",
            r1.report_date_values,
            r2.report_date_values,
        ),
    ]

    # Clinical date matching: unique dates from drug start/end and AE onset
    matched_clinical_dates = compute_matching_clinical_dates(
        r1, r2, matching_drugs, matching_aes
    )

    return MatchResult(
        matching_drugs=matching_drugs,
        matching_AEs=matching_aes,
        drugs_only_r1=drugs_r1,
        drugs_only_r2=drugs_r2,
        aes_only_r1=aes_r1,
        aes_only_r2=aes_r2,
        demographic_matches=demographic_matches,
        matched_clinical_dates=matched_clinical_dates,
    )
