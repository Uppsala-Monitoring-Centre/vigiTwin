"""Feature matching between two reports.

Computes shared clinical features (drug sets, AE sets) and
demographic attribute matches using set intersection semantics.

Matching rule for multi-valued attributes: v1 ∩ v2 ≠ ∅
(at least one candidate value must match between the two reports).

See: docs/data_contracts.md § Set semantics and Matching rules
See: docs/duplicate_adjudication_spec.md § Matching clinical feature sets
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from llm_dup_dev.feature_generation.clinical_date_inconsistencies import (
    effective_overall_ae_onset_date,
)
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
    age_unit: str | None = None  # for patient_age: "years" or "months"
    value_resolution: float | None = None  # decimal resolution of a matched age/height/weight


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


def _compute_set_overlap(
    items_r1: list,
    items_r2: list,
    key_fn: Callable,
) -> tuple[list[str], list[str], list[str]]:
    """Compute case-insensitive set intersection and differences.

    *key_fn* extracts the display name from each item (e.g. ``d.substance``
    or ``ae.pt_name``).  Original casing from report 1 is preserved.

    Returns ``(matching, only_r1, only_r2)`` — all in canonical sorted order.
    """
    map_1 = {key_fn(item).lower(): key_fn(item) for item in items_r1}
    map_2 = {key_fn(item).lower(): key_fn(item) for item in items_r2}

    matching_keys = set(map_1) & set(map_2)
    only_r1_keys = set(map_1) - set(map_2)
    only_r2_keys = set(map_2) - set(map_1)

    return (
        sorted(map_1[k] for k in matching_keys),
        sorted(map_1[k] for k in only_r1_keys),
        sorted(map_2[k] for k in only_r2_keys),
    )


def compute_matching_drugs(
    r1: Report, r2: Report
) -> tuple[list[str], list[str], list[str]]:
    """Compute drug set intersection and differences (case-insensitive)."""
    return _compute_set_overlap(r1.drugs, r2.drugs, lambda d: d.substance)


def compute_matching_aes(
    r1: Report, r2: Report
) -> tuple[list[str], list[str], list[str]]:
    """Compute adverse event set intersection and differences (case-insensitive)."""
    return _compute_set_overlap(
        r1.adverse_events, r2.adverse_events, lambda ae: ae.pt_name
    )


# ---------------------------------------------------------------------------
# Demographic attribute matching
# ---------------------------------------------------------------------------


def _decimal_resolution(value: float) -> float:
    """Resolution implied by a numeric value's decimal places (54.2 → 0.1, 54 → 1.0)."""
    s = str(value)
    if "." in s:
        decimal_places = len(s.split(".")[1].rstrip("0"))
        if decimal_places > 0:
            return 10.0 ** (-decimal_places)
    return 1.0


def _quantize(value: float, resolution: float) -> int:
    """Round *value* to the nearest grid step of *resolution* (half-up).

    Returns the integer grid index, so the grid value is
    ``index * resolution``. Half-up (rather than Python's banker's ``round``)
    makes the bin ``[v − r/2, v + r/2)`` symmetric across all grid points; the
    tiny epsilon absorbs binary-float noise at the half-step boundary.
    """
    return int(value / resolution + 0.5 + 1e-9)


def _quantize_floor(value: float, resolution: float) -> int:
    """Truncate *value* down to the grid step of *resolution* (floor).

    Returns the integer grid index for the bin ``[index·r, (index+1)·r)``.
    Used for age: people report their *completed* age (74 until their 75th
    birthday), so 74.6 years floors into the 74-year bin rather than rounding
    up to 75. Ages are non-negative, so ``int`` truncation is a floor; the
    epsilon absorbs binary-float noise just below a grid point (a stored 75.0
    that evaluates to 74.9999999 still floors to 75).
    """
    return int(value / resolution + 1e-9)


def _numeric_resolution_match(
    values_1: list[float],
    values_2: list[float],
    *,
    quantize: Callable[[float, float], int] = _quantize,
) -> tuple[bool, float | None, float | None]:
    """Match numeric demographics at the coarser of two decimal resolutions.

    Two values match when they map (via *quantize*) to the same grid point at
    the coarser of their two decimal resolutions. With the default half-up
    ``_quantize``, 75.2 and 75 both fall in the 1-unit bin centred on 75 and
    match, while 75.6 does not. Age passes ``_quantize_floor`` instead, so the
    bins are ``[index·r, (index+1)·r)`` (completed-age semantics). Among all
    matching cross-report pairs the finest-resolution match is returned, since
    an exact fine-grained match (75.2 ↔ 75.2) is stronger evidence than a
    coarse one (75.2 ↔ 75) and should earn the correspondingly larger domain.

    Returns ``(matched, matched_value, resolution)``; the value and resolution
    are ``None`` when nothing matches.
    """
    best_res: float | None = None
    best_val: float | None = None
    for v1 in values_1:
        for v2 in values_2:
            res = max(_decimal_resolution(v1), _decimal_resolution(v2))
            index = quantize(v1, res)
            if index == quantize(v2, res):
                if best_res is None or res < best_res:
                    best_res = res
                    ndec = max(0, -round(math.log10(res)))
                    best_val = round(index * res, ndec)
    if best_res is None:
        return False, None, None
    return True, best_val, best_res


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
    """Match patient age at the coarser of the two values' decimal resolutions.

    Values are compared **within the same unit** (years with years, months
    with months). Unlike height and weight, age uses **floor** (completed-age)
    bins rather than nearest rounding: 74.6 y and 74 y describe the same age
    (one is 74 until the 75th birthday), so they match, whereas nearest
    rounding would place 74.6 in the 75 bin. The finest-resolution match is
    recorded, together with its unit and decimal resolution, for domain-size
    computation.
    """
    by_unit_1: dict[str, list[float]] = {}
    by_unit_2: dict[str, list[float]] = {}
    for v in r1.patient_age_values:
        by_unit_1.setdefault(v.unit, []).append(v.value)
    for v in r2.patient_age_values:
        by_unit_2.setdefault(v.unit, []).append(v.value)

    best: tuple[float, float, str] | None = None  # (resolution, value, unit)
    for unit in set(by_unit_1) & set(by_unit_2):
        matched, val, res = _numeric_resolution_match(
            by_unit_1[unit], by_unit_2[unit], quantize=_quantize_floor
        )
        if matched and val is not None and res is not None:
            if best is None or res < best[0]:
                best = (res, val, unit)

    return DemographicMatch(
        feature="patient_age",
        matched=best is not None,
        values_r1=[v.value for v in r1.patient_age_values],
        values_r2=[v.value for v in r2.patient_age_values],
        matched_value=best[1] if best is not None else None,
        age_unit=best[2] if best is not None else None,
        value_resolution=best[0] if best is not None else None,
    )


def _match_measurement(
    feature_name: str,
    values_r1: list[MeasurementValue],
    values_r2: list[MeasurementValue],
) -> DemographicMatch:
    """Match height/weight at the coarser of the two values' decimal resolutions."""
    matched, val, res = _numeric_resolution_match(
        [v.value for v in values_r1], [v.value for v in values_r2]
    )
    return DemographicMatch(
        feature=feature_name,
        matched=matched,
        values_r1=[v.value for v in values_r1],
        values_r2=[v.value for v in values_r2],
        matched_value=val,
        value_resolution=res,
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
    """Compute unique matching dates from drug, AE, and overall AE onset fields.

    Examines explicitly recorded date values only — NOT treatment intervals.
    A drug exposure period (start → stop) does NOT imply that intermediate
    dates are available for matching.

    Rules:
        1. Only same-field-type comparisons count:
           drug start ↔ drug start, drug end ↔ drug end, AE onset ↔ AE onset.
        2. Only dates for the same matched entity are compared.
        3. The overall AE onset date is compared at the report level
           (does not require shared AE terms).
        4. The result is collapsed to unique matched dates.  If the same
           calendar date matches for multiple drugs/AEs/field types, it
           contributes only once (at the finest matched resolution).  A
           coarser match whose value is the truncation of a finer match
           (e.g. year-only ``2000`` alongside day ``2000-04-08``) is
           subsumed and dropped — it is the coarser view of the same
           calendar coincidence and must not be rewarded independently.
        5. Standard partial-date matching rules apply (month-only and
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
    # Build lookup maps: substance/pt_name (lowercase) → list of entities.
    # A substance (or AE PT name) may legitimately appear more than once per
    # report — e.g. the same drug recorded with different roles (suspected vs
    # concomitant) or split across structured/narrative entries where only a
    # subset carry date information. All entries must participate in
    # comparison, otherwise a dateful entry can be silently shadowed by a
    # dateless duplicate and a genuine date coincidence is missed.
    drugs_r1: dict[str, list] = {}
    for d in r1.drugs:
        drugs_r1.setdefault(d.substance.lower(), []).append(d)
    drugs_r2: dict[str, list] = {}
    for d in r2.drugs:
        drugs_r2.setdefault(d.substance.lower(), []).append(d)
    aes_r1: dict[str, list] = {}
    for ae in r1.adverse_events:
        aes_r1.setdefault(ae.pt_name.lower(), []).append(ae)
    aes_r2: dict[str, list] = {}
    for ae in r2.adverse_events:
        aes_r2.setdefault(ae.pt_name.lower(), []).append(ae)

    # Collect unique matches: matched_value → finest resolution
    unique: dict[str, str] = {}

    for drug_name in matching_drugs:
        key = drug_name.lower()
        entries_1 = drugs_r1.get(key, [])
        entries_2 = drugs_r2.get(key, [])
        if not entries_1 or not entries_2:
            continue

        # Compare every pair of same-substance entries across reports.
        # Role does not affect matching here — if any pairing of the same
        # substance yields a date coincidence, it counts.
        for d1 in entries_1:
            for d2 in entries_2:
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
        ae_entries_1 = aes_r1.get(key, [])
        ae_entries_2 = aes_r2.get(key, [])
        if not ae_entries_1 or not ae_entries_2:
            continue

        # Compare onset dates (onset ↔ onset only) across every pair of
        # same-PT AE entries — same rationale as for drug duplicates.
        for ae1 in ae_entries_1:
            for ae2 in ae_entries_2:
                result = _compare_partial_dates(ae1.start_date, ae2.start_date)
                if result is not None:
                    _update_unique_dates(unique, result[0], result[1])

    # Overall AE onset date: report-level comparison that does not require
    # shared AE terms.  Feeds into the same unique-date dict so an already-
    # matched calendar date is not double-counted.
    onset1 = effective_overall_ae_onset_date(r1)
    onset2 = effective_overall_ae_onset_date(r2)
    if onset1 is not None and onset2 is not None:
        result = _compare_partial_dates(onset1, onset2)
        if result is not None:
            _update_unique_dates(unique, result[0], result[1])

    unique = _drop_subsumed_dates(unique)
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


def _drop_subsumed_dates(unique: dict[str, str]) -> dict[str, str]:
    """Drop entries that are prefix-truncations of a finer entry in the same dict.

    A coarse match (e.g. year-only "2021") that falls inside a finer match
    (e.g. "2021-11-08") is the coarser view of the same calendar coincidence
    and must not contribute an independent reward downstream.
    """
    items = list(unique.items())
    kept: dict[str, str] = {}
    for val, res in items:
        rank = _RESOLUTION_RANK[res]
        subsumed = any(
            _RESOLUTION_RANK[other_res] > rank
            and _truncate_to_resolution(other_val, res) == val
            for other_val, other_res in items
            if other_val != val
        )
        if not subsumed:
            kept[val] = res
    return kept


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

    # Clinical date matching: unique dates from drug start/end, AE onset,
    # and overall AE onset
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
