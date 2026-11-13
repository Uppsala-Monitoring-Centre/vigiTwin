"""Comparison logic: Report pair -> PairViewModel.

Applies field-specific comparison rules to two Reports and produces
the intermediate view model consumed by the Jinja2 templates.

Comparison strategy by field type
---------------------------------
- **Exact match**: sex, country (case-insensitive set overlap)
- **Tolerant numeric**: age, height, weight (match / partial / mismatch)
- **Partial-date-aware**: report date, drug dates
  (truncated to coarser resolution before comparison)
- **Set-based union**: drugs, adverse events
  (one column per entity in the union of both reports)
- **Nested detail**: drug sub-fields compared individually

Adaptive inclusion
------------------
Each ``_compare_*`` helper returns ``None`` when both sides are empty,
causing the field to be omitted from the demographics table.
"""

from __future__ import annotations

import math
from collections import OrderedDict

from llm_dup_dev.data.offline import FrozenPairBundle, FrozenScopeStats
from llm_dup_dev.feature_generation.matching import (
    _coarser_resolution,
    _truncate_to_resolution,
)
from llm_dup_dev.feature_generation.narrative_demographics import (
    merge_narrative_demographics,
)
from llm_dup_dev.models.classification import (
    ClassificationResult,
    InconsistencyPenalty,
)
from llm_dup_dev.models.explicit_link import (
    ExplicitLinkAssessment,
    ExplicitLinkDirection,
    ExplicitLinkVerdict,
)
from llm_dup_dev.models.narrative_demographics import NarrativeDemographics
from llm_dup_dev.models.narrative_source_case import SourceCaseVerdict
from llm_dup_dev.models.pair_features import PairFeatures
from llm_dup_dev.models.report import (
    AdverseEvent,
    AgeValue,
    CaseIdentifierRef,
    DateValue,
    Drug,
    MeasurementValue,
    Report,
    SourcedValue,
)

from .models import (
    AEClinicalPictureCountsVM,
    AEClinicalPictureRuleDetailsVM,
    AEDetail,
    AERow,
    AnnotationVM,
    ClassificationSummaryVM,
    ClinicalDateCountsVM,
    ClinicalDateFindingVM,
    CaseIdentifierListVM,
    ComparisonStatus,
    DecisionSummaryVM,
    DrugDetail,
    DrugRow,
    ExplicitLinkMatchVM,
    ExplicitLinkVM,
    FeatureContributionVM,
    FeaturesSummaryVM,
    FieldComparison,
    FreqScopeVM,
    FreqStatsVM,
    InconsistencyCountsVM,
    InconsistencyVM,
    NarrativeComparison,
    PairViewModel,
    ScreeningFeatureVM,
    ScreeningVM,
    SourceCaseAssessmentVM,
    SubfieldStatus,
)
from .normalize import (
    format_age_values,
    format_date_values,
    format_measurement_values,
    format_partial_date,
    format_sourced_values,
    normalize_ae_key,
    normalize_drug_key,
)

# ---------------------------------------------------------------------------
# Display-tolerance constants — *fallback only*.
#
# When PairFeatures are available the demographics rows are coloured by the
# analysis's own inconsistency severity (see ``_apply_demographic_severity``),
# so the table always matches the pipeline. These tolerances are used only for
# the numeric match/partial/mismatch status when no PairFeatures are supplied
# (e.g. a standalone preview render). They are intentionally *not* the pipeline
# thresholds.
# ---------------------------------------------------------------------------

AGE_TOLERANCE_YEARS: float = 3.0
AGE_PARTIAL_TOLERANCE_YEARS: float = 6.0
HEIGHT_TOLERANCE_CM: float = 5.0
HEIGHT_PARTIAL_TOLERANCE_CM: float = 10.0
WEIGHT_TOLERANCE_KG: float = 5.0
WEIGHT_PARTIAL_TOLERANCE_KG: float = 15.0

# ---------------------------------------------------------------------------
# Preferred display order for demographic fields
# ---------------------------------------------------------------------------

_DEMOGRAPHIC_FIELD_ORDER = [
    "sex",
    "age",
    "country",
    "report_date",
    "height",
    "weight",
]


# ===================================================================
# Scalar field comparisons
# ===================================================================


def _compare_sourced_values(
    label: str,
    left: list[SourcedValue],
    right: list[SourcedValue],
) -> FieldComparison | None:
    """Exact case-insensitive set-overlap comparison."""
    if not left and not right:
        return None

    left_display = format_sourced_values(left)
    right_display = format_sourced_values(right)

    if not left:
        status = ComparisonStatus.MISSING_LEFT
    elif not right:
        status = ComparisonStatus.MISSING_RIGHT
    else:
        left_set = {v.value.lower() for v in left}
        right_set = {v.value.lower() for v in right}
        if left_set & right_set:
            status = ComparisonStatus.MATCH
        else:
            status = ComparisonStatus.MISMATCH

    return FieldComparison(
        label=label,
        left_display=left_display,
        right_display=right_display,
        status=status,
    )


def _min_pairwise_diff(
    left: list[float], right: list[float]
) -> float:
    """Minimum absolute difference across all (left, right) value pairs."""
    return min(abs(lv - rv) for lv in left for rv in right)


def _compare_age_values(
    left: list[AgeValue],
    right: list[AgeValue],
) -> FieldComparison | None:
    """Tolerant numeric comparison for age."""
    if not left and not right:
        return None

    left_display = format_age_values(left)
    right_display = format_age_values(right)

    if not left:
        status = ComparisonStatus.MISSING_LEFT
    elif not right:
        status = ComparisonStatus.MISSING_RIGHT
    else:
        # Convert all to years for comparison
        left_years = [_age_in_years(v) for v in left]
        right_years = [_age_in_years(v) for v in right]
        diff = _min_pairwise_diff(left_years, right_years)
        if diff <= AGE_TOLERANCE_YEARS:
            status = ComparisonStatus.MATCH
        elif diff <= AGE_PARTIAL_TOLERANCE_YEARS:
            status = ComparisonStatus.PARTIAL_MATCH
        else:
            status = ComparisonStatus.MISMATCH

    tooltip = ""
    if (
        left
        and right
        and status != ComparisonStatus.MATCH
    ):
        left_years = [_age_in_years(v) for v in left]
        right_years = [_age_in_years(v) for v in right]
        tooltip = f"min difference: {_min_pairwise_diff(left_years, right_years):.1f} years"

    return FieldComparison(
        label="Age",
        left_display=left_display,
        right_display=right_display,
        status=status,
        tooltip=tooltip,
    )


def _age_in_years(v: AgeValue) -> float:
    """Convert an AgeValue to years."""
    if v.unit == "months":
        return v.value / 12.0
    if v.unit == "days":
        return v.value / 365.25
    return v.value


def _compare_measurement_values(
    label: str,
    left: list[MeasurementValue],
    right: list[MeasurementValue],
    tolerance: float,
    partial_tolerance: float,
) -> FieldComparison | None:
    """Tolerant numeric comparison for height/weight."""
    if not left and not right:
        return None

    left_display = format_measurement_values(left)
    right_display = format_measurement_values(right)

    if not left:
        status = ComparisonStatus.MISSING_LEFT
    elif not right:
        status = ComparisonStatus.MISSING_RIGHT
    else:
        left_nums = [v.value for v in left]
        right_nums = [v.value for v in right]
        diff = _min_pairwise_diff(left_nums, right_nums)
        if diff <= tolerance:
            status = ComparisonStatus.MATCH
        elif diff <= partial_tolerance:
            status = ComparisonStatus.PARTIAL_MATCH
        else:
            status = ComparisonStatus.MISMATCH

    tooltip = ""
    if (
        left
        and right
        and status != ComparisonStatus.MATCH
    ):
        unit = left[0].unit if left else ""
        left_nums = [v.value for v in left]
        right_nums = [v.value for v in right]
        tooltip = f"min difference: {_min_pairwise_diff(left_nums, right_nums):.1f} {unit}"

    return FieldComparison(
        label=label,
        left_display=left_display,
        right_display=right_display,
        status=status,
        tooltip=tooltip,
    )


def _compare_date_values(
    label: str,
    left: list[DateValue],
    right: list[DateValue],
    *,
    neutral: bool = False,
) -> FieldComparison | None:
    """Partial-date-aware comparison (truncate to coarser resolution).

    Parameters
    ----------
    neutral : bool
        When True the row uses ``NEUTRAL`` status regardless of the
        match outcome, so no background colour is applied in the table.
        Use this for fields (e.g. report date) where date differences are
        informational rather than diagnostic.
    """
    if not left and not right:
        return None

    left_display = format_date_values(left)
    right_display = format_date_values(right)

    if not left:
        underlying = ComparisonStatus.MISSING_LEFT
    elif not right:
        underlying = ComparisonStatus.MISSING_RIGHT
    else:
        matched = False
        for d1 in left:
            for d2 in right:
                coarse = _coarser_resolution(d1.resolution, d2.resolution)
                t1 = _truncate_to_resolution(d1.value, coarse)
                t2 = _truncate_to_resolution(d2.value, coarse)
                if t1 == t2:
                    matched = True
                    break
            if matched:
                break
        underlying = ComparisonStatus.MATCH if matched else ComparisonStatus.MISMATCH

    status = ComparisonStatus.NEUTRAL if neutral else underlying

    return FieldComparison(
        label=label,
        left_display=left_display,
        right_display=right_display,
        status=status,
    )


# ===================================================================
# Drug comparison (union-based)
# ===================================================================


def _compare_optional_str(
    left: str | None, right: str | None
) -> ComparisonStatus | None:
    """Compare two optional string sub-fields.

    Returns None when both are absent (field not applicable to display).
    """
    if left is None and right is None:
        return None
    if left is None:
        return ComparisonStatus.MISSING_LEFT
    if right is None:
        return ComparisonStatus.MISSING_RIGHT
    return (
        ComparisonStatus.MATCH
        if left.lower() == right.lower()
        else ComparisonStatus.MISMATCH
    )


def _compare_partial_date_str(
    left_drug: Drug, right_drug: Drug, field: str
) -> ComparisonStatus | None:
    """Compare a start_date or end_date sub-field between two Drug objects."""
    l_date = getattr(left_drug, field)
    r_date = getattr(right_drug, field)
    if l_date is None and r_date is None:
        return None
    if l_date is None:
        return ComparisonStatus.MISSING_LEFT
    if r_date is None:
        return ComparisonStatus.MISSING_RIGHT
    coarse = _coarser_resolution(l_date.resolution, r_date.resolution)
    t1 = _truncate_to_resolution(l_date.value, coarse)
    t2 = _truncate_to_resolution(r_date.value, coarse)
    return ComparisonStatus.MATCH if t1 == t2 else ComparisonStatus.MISMATCH


def _build_subfield_status(lhs: Drug, rhs: Drug) -> SubfieldStatus:
    """Build per-sub-field comparison statuses for a drug present in both reports."""
    role_l = lhs.role.value if lhs.role else None
    role_r = rhs.role.value if rhs.role else None
    return SubfieldStatus(
        role=_compare_optional_str(role_l, role_r),
        form=_compare_optional_str(lhs.form, rhs.form),
        strength=_compare_optional_str(lhs.strength, rhs.strength),
        start_date=_compare_partial_date_str(lhs, rhs, "start_date"),
        end_date=_compare_partial_date_str(lhs, rhs, "end_date"),
    )


def _drug_to_detail(drug: Drug, counterpart: Drug | None = None) -> DrugDetail:
    """Convert a domain Drug to a display DrugDetail.

    If ``counterpart`` is provided, populates ``subfield_status`` with
    per-field comparison outcomes for inline coloring.
    """
    return DrugDetail(
        substance=drug.substance,
        role=drug.role.value if drug.role else None,
        form=drug.form,
        strength=drug.strength,
        start_date=format_partial_date(drug.start_date),
        end_date=format_partial_date(drug.end_date),
        subfield_status=(
            _build_subfield_status(drug, counterpart)
            if counterpart is not None
            else None
        ),
    )


def _build_drug_rows(r1: Report, r2: Report) -> list[DrugRow]:
    """Build union-based drug comparison rows.

    Groups drugs by normalised substance name. Each row shows all
    instances from both reports for that substance.
    """
    # Group drugs by normalised key, preserving insertion order
    left_groups: OrderedDict[str, list[Drug]] = OrderedDict()
    for d in r1.drugs:
        left_groups.setdefault(normalize_drug_key(d.substance), []).append(d)

    right_groups: OrderedDict[str, list[Drug]] = OrderedDict()
    for d in r2.drugs:
        right_groups.setdefault(normalize_drug_key(d.substance), []).append(d)

    # Union of keys in a stable order (left first, then right-only)
    all_keys: list[str] = list(left_groups.keys())
    for k in right_groups:
        if k not in left_groups:
            all_keys.append(k)

    rows: list[DrugRow] = []
    for key in sorted(all_keys):
        left_drugs = left_groups.get(key, [])
        right_drugs = right_groups.get(key, [])

        # Pick the best display name (prefer left, then right)
        display_name = (
            left_drugs[0].substance if left_drugs else right_drugs[0].substance
        )

        if not left_drugs:
            status = ComparisonStatus.MISSING_LEFT
            left_details = []
            right_details = [_drug_to_detail(d) for d in right_drugs]
        elif not right_drugs:
            status = ComparisonStatus.MISSING_RIGHT
            left_details = [_drug_to_detail(d) for d in left_drugs]
            right_details = []
        else:
            status, left_details, right_details = _compare_drug_details(
                left_drugs, right_drugs
            )

        rows.append(
            DrugRow(
                substance=display_name,
                left_details=left_details,
                right_details=right_details,
                status=status,
            )
        )
    return rows


def _compare_drug_details(
    left: list[Drug], right: list[Drug]
) -> tuple[ComparisonStatus, list[DrugDetail], list[DrugDetail]]:
    """Compare drug sub-fields and build detail view models with subfield statuses.

    Uses first instance from each side for comparison heuristic.
    Returns the row-level status and detail lists with subfield_status populated.
    """
    # Row background colour is driven by date sub-fields only.
    # Dose / form / role mismatches are shown via inline sub-field coloring
    # but do not escalate the row to yellow.
    sub = _build_subfield_status(left[0], right[0])
    date_mismatch = (
        sub.start_date == ComparisonStatus.MISMATCH
        or sub.end_date == ComparisonStatus.MISMATCH
    )
    row_status = (
        ComparisonStatus.PARTIAL_MATCH if date_mismatch else ComparisonStatus.MATCH
    )

    # Build details with subfield_status for first instance; bare for extras
    left_details = [_drug_to_detail(left[0], right[0])] + [
        _drug_to_detail(d) for d in left[1:]
    ]
    right_details = [_drug_to_detail(right[0], left[0])] + [
        _drug_to_detail(d) for d in right[1:]
    ]

    return row_status, left_details, right_details


# ===================================================================
# Adverse-event comparison (union-based)
# ===================================================================


def _ae_dates_match(left_ae: AdverseEvent, right_ae: AdverseEvent) -> bool:
    """Return True if the two AEs have compatible start dates (or either is absent)."""
    left_date, right_date = left_ae.start_date, right_ae.start_date
    if left_date is None or right_date is None:
        return True  # no date on at least one side → not a conflict
    coarse = _coarser_resolution(left_date.resolution, right_date.resolution)
    return (
        _truncate_to_resolution(left_date.value, coarse)
        == _truncate_to_resolution(right_date.value, coarse)
    )


def _build_ae_rows(r1: Report, r2: Report) -> list[AERow]:
    """Build union-based adverse event comparison rows.

    Row background is yellow (PARTIAL_MATCH) when the AE is present in both
    reports but their start dates conflict; green (MATCH) otherwise.
    """
    # Group raw AdverseEvent objects by normalised PT key
    left_raw: OrderedDict[str, list[AdverseEvent]] = OrderedDict()
    for ae in r1.adverse_events:
        left_raw.setdefault(normalize_ae_key(ae.pt_name), []).append(ae)

    right_raw: OrderedDict[str, list[AdverseEvent]] = OrderedDict()
    for ae in r2.adverse_events:
        right_raw.setdefault(normalize_ae_key(ae.pt_name), []).append(ae)

    all_keys: list[str] = list(left_raw.keys())
    for k in right_raw:
        if k not in left_raw:
            all_keys.append(k)

    rows: list[AERow] = []
    for key in sorted(all_keys):
        left_aes = left_raw.get(key, [])
        right_aes = right_raw.get(key, [])

        display_name = (
            left_aes[0].pt_name if left_aes else right_aes[0].pt_name
        )

        left_details = [
            AEDetail(pt_name=ae.pt_name, start_date=format_partial_date(ae.start_date))
            for ae in left_aes
        ]
        right_details = [
            AEDetail(pt_name=ae.pt_name, start_date=format_partial_date(ae.start_date))
            for ae in right_aes
        ]

        if not left_aes:
            status = ComparisonStatus.MISSING_LEFT
        elif not right_aes:
            status = ComparisonStatus.MISSING_RIGHT
        else:
            # Yellow only when start dates are in conflict
            dates_ok = _ae_dates_match(left_aes[0], right_aes[0])
            status = ComparisonStatus.MATCH if dates_ok else ComparisonStatus.PARTIAL_MATCH

        rows.append(
            AERow(
                pt_name=display_name,
                left_details=left_details,
                right_details=right_details,
                status=status,
            )
        )
    return rows


# ===================================================================
# Optional PairFeatures / Classification view-model builders
# ===================================================================


def _annotate_demographic_contributions(
    demographics: list[FieldComparison],
    feature_contributions: list[FeatureContributionVM],
) -> None:
    """Mutate demographics rows in place with per-feature contribution chips.

    For each ``FeatureContribution`` whose feature name maps to a demographic
    label (via ``_FEATURE_TO_DEMO_LABEL``), populate ``match_contribution``
    and ``match_contribution_class`` on the corresponding row. Non-matching
    rows or features without a label mapping (drug_set/ae_set/clinical_date)
    are skipped — those carry their chips elsewhere.
    """
    by_label: dict[str, FieldComparison] = {f.label: f for f in demographics}
    for fc in feature_contributions:
        label = _FEATURE_TO_DEMO_LABEL.get(fc.feature)
        if label is None:
            continue
        row = by_label.get(label)
        if row is None or fc.p_f <= 0:
            continue
        contribution = -math.log10(fc.p_f)
        row.match_contribution = contribution
        row.match_contribution_class = _contrib_pos_class(contribution)


# Ordering for picking the most severe inconsistency when several map to
# the same demographic row.
_SEVERITY_ORDER: dict[str, int] = {
    "minor": 0,
    "moderate": 1,
    "strong": 2,
    "decisive": 3,
}


def _apply_demographic_severity(
    demographics: list[FieldComparison],
    features: PairFeatures,
) -> None:
    """Colour demographic rows by the analysis's own inconsistency severity.

    Reads the authoritative ``PairFeatures.inconsistencies`` list — the same
    entries the decision logic counts — and stamps each corresponding
    demographic row with its severity, so the table's colours match the
    analysis rather than an independent display tolerance. (Previously a
    numeric field could show green while the analysis recorded a moderate
    inconsistency, because the display used its own laxer tolerances.)

    Rows with no recorded inconsistency (an exact/close match) keep their
    match status and green colour. Missing-side and neutral rows are never
    overridden. When several inconsistencies map to one row, the most
    severe wins.
    """
    by_label: dict[str, FieldComparison] = {f.label: f for f in demographics}
    worst: dict[str, str] = {}
    for inc in features.inconsistencies:
        if inc.suppressed:
            continue
        label = _FEATURE_TO_DEMO_LABEL.get(inc.feature)
        if label is None:
            continue
        sev = inc.severity.value
        if _SEVERITY_ORDER.get(sev, -1) > _SEVERITY_ORDER.get(
            worst.get(label, ""), -1
        ):
            worst[label] = sev
    for label, sev in worst.items():
        row = by_label.get(label)
        if row is None:
            continue
        if row.status in (
            ComparisonStatus.MISSING_LEFT,
            ComparisonStatus.MISSING_RIGHT,
            ComparisonStatus.NEUTRAL,
        ):
            continue
        row.severity = sev


# Extraction list attribute on ``NarrativeDemographics`` -> demographics row
# label. Country and Report date have no narrative counterpart, so they never
# carry evidence.
_NARRATIVE_EVIDENCE_LABELS: dict[str, str] = {
    "ages": "Age",
    "sexes": "Sex",
    "weights": "Weight",
    "heights": "Height",
}


def _attach_narrative_evidence(
    demographics: list[FieldComparison],
    narrative_demographics_1: NarrativeDemographics | None,
    narrative_demographics_2: NarrativeDemographics | None,
) -> None:
    """Mutate demographics rows in place with narrative evidence snippets.

    ``merge_narrative_demographics`` drops the verbatim ``evidence`` snippet
    each candidate carries — the Report's value models have nowhere to put it
    — so the snippets are read straight off the extraction objects here. The
    reviewer otherwise sees an italicised narrative-derived value with no way
    to check what the LLM based it on.

    Attached per row and per side rather than per displayed value: the display
    strings dedupe across structured and narrative contributors, so a rendered
    segment cannot be mapped back to one candidate. Every snippet the
    extractor produced for the field is shown instead.
    """
    by_label: dict[str, FieldComparison] = {f.label: f for f in demographics}
    for attr, label in _NARRATIVE_EVIDENCE_LABELS.items():
        row = by_label.get(label)
        if row is None:
            continue
        if narrative_demographics_1 is not None:
            row.left_evidence = [
                c.evidence
                for c in getattr(narrative_demographics_1, attr)
                if c.evidence
            ]
        if narrative_demographics_2 is not None:
            row.right_evidence = [
                c.evidence
                for c in getattr(narrative_demographics_2, attr)
                if c.evidence
            ]


def _build_features_summary(
    features: PairFeatures,
) -> FeaturesSummaryVM:
    """Map PairFeatures to the view model."""
    ctx = features.reporting_context
    ctx_str = ctx.level.value
    if ctx.country:
        ctx_str += f" ({ctx.country}"
        if ctx.year:
            ctx_str += f", {ctx.year}"
        ctx_str += ")"

    # Pre-compute drug-set / AE-set log-rarity contributions and the
    # anchor penalty so templates can render inline chips.
    drugs_c: float | None = None
    aes_c: float | None = None
    for fc in features.probability.feature_contributions:
        if fc.feature == "drug_set" and fc.p_f > 0:
            drugs_c = -math.log10(fc.p_f)
        elif fc.feature == "ae_set" and fc.p_f > 0:
            aes_c = -math.log10(fc.p_f)
    anchor_pen = _anchor_penalty(features.anchor.N_anchor)

    return FeaturesSummaryVM(
        l_match=features.L_match,
        l_patient=features.L_patient,
        p_match=features.probability.p_match,
        anchor_type=features.anchor.type.value,
        anchor_n=features.anchor.N_anchor,
        reporting_context=ctx_str,
        matching_drugs=features.matching_drugs,
        matching_aes=features.matching_AEs,
        inconsistencies=[
            InconsistencyVM(
                feature=inc.feature,
                severity=inc.severity.value,
                description=inc.description or "",
                source=inc.source,
                kind=inc.kind or "",
                rationale=inc.rationale,
                terms=list(inc.terms),
                cluster_label=inc.cluster_label,
                suppressed=inc.suppressed,
                suppressed_by=inc.suppressed_by,
            )
            for inc in features.inconsistencies
        ],
        # Extended prediction/evaluation fields
        p_joint=features.probability.p_joint,
        anchor_n_drugs=features.anchor.N_drugs,
        anchor_n_aes=features.anchor.N_AEs,
        anchor_n_drugs_aes=features.anchor.N_drugs_AEs,
        feature_contributions=[
            FeatureContributionVM(
                feature=fc.feature,
                p_f=fc.p_f,
                method=fc.method,
                domain_size=fc.domain_size,
                description=fc.description,
            )
            for fc in features.probability.feature_contributions
        ],
        matching_drugs_contribution=drugs_c,
        matching_drugs_contribution_class=(
            _contrib_pos_class(drugs_c) if drugs_c is not None else None
        ),
        matching_aes_contribution=aes_c,
        matching_aes_contribution_class=(
            _contrib_pos_class(aes_c) if aes_c is not None else None
        ),
        anchor_penalty=anchor_pen,
        anchor_penalty_class=(
            _contrib_neg_class(anchor_pen) if anchor_pen is not None else None
        ),
        inconsistency_counts=InconsistencyCountsVM(
            minor=features.inconsistency_counts.minor,
            moderate=features.inconsistency_counts.moderate,
            strong=features.inconsistency_counts.strong,
            decisive=features.inconsistency_counts.decisive,
        ),
        clinical_date_counts=ClinicalDateCountsVM(
            n_minor=features.clinical_date_inconsistency_counts.n_minor,
            n_moderate=features.clinical_date_inconsistency_counts.n_moderate,
            n_strong=features.clinical_date_inconsistency_counts.n_strong,
            n_decisive=features.clinical_date_inconsistency_counts.n_decisive,
        ),
        clinical_date_findings=[
            ClinicalDateFindingVM(
                entity_label=f.entity_label,
                severity=f.severity.value,
                source_date=f.source_date,
                matched_date=f.matched_date,
                diff_days=f.diff_days,
                downgrades=f.downgrades,
            )
            for f in features.clinical_date_findings
        ],
        ae_clinical_picture_status=features.ae_clinical_picture_status.value,
        ae_clinical_picture_reason=features.ae_clinical_picture_reason,
        ae_clinical_picture_method=features.ae_clinical_picture_method or None,
        ae_clinical_picture_counts=AEClinicalPictureCountsVM(
            n_minor=features.ae_clinical_picture_counts.n_minor,
            n_moderate=features.ae_clinical_picture_counts.n_moderate,
            n_strong=features.ae_clinical_picture_counts.n_strong,
            n_decisive=features.ae_clinical_picture_counts.n_decisive,
        ),
        ae_clinical_picture_reasoning=features.ae_clinical_picture_reasoning,
        ae_clinical_picture_rule_details=_build_rule_details_vm(
            features.ae_clinical_picture_rule_based_details
        ),
    )


def _build_rule_details_vm(
    details,
) -> AEClinicalPictureRuleDetailsVM | None:
    """Translate the deterministic AE clinical-picture audit block to its VM."""
    if details is None:
        return None
    return AEClinicalPictureRuleDetailsVM(
        step1_overlap_level=details.step1_overlap_level.value,
        step1_severity=(
            details.step1_severity.value if details.step1_severity else None
        ),
        step2_r1_to_r2_u=details.step2_r1_to_r2_u,
        step2_r1_to_r2_m=details.step2_r1_to_r2_m,
        step2_r1_to_r2_severity=(
            details.step2_r1_to_r2_severity.value
            if details.step2_r1_to_r2_severity
            else None
        ),
        step2_r2_to_r1_u=details.step2_r2_to_r1_u,
        step2_r2_to_r1_m=details.step2_r2_to_r1_m,
        step2_r2_to_r1_severity=(
            details.step2_r2_to_r1_severity.value
            if details.step2_r2_to_r1_severity
            else None
        ),
        investigations_excluded_step2=details.investigations_excluded_step2,
        missing_hierarchy_warnings=list(details.missing_hierarchy_warnings),
    )


def _build_classification_summary(
    classification: ClassificationResult,
    features: PairFeatures | None,
) -> ClassificationSummaryVM:
    return ClassificationSummaryVM(
        independence_assessment=classification.independence_assessment.value,
        relationship_assessment=(
            classification.relationship_assessment.value
            if classification.relationship_assessment
            else None
        ),
        final_label=classification.final_label,
        explanation=classification.explanation,
        penalty_summary=_format_penalty_summary(classification.penalty),
    )


def _build_source_case_vm(
    features: PairFeatures | None,
) -> SourceCaseAssessmentVM | None:
    """Project the pair's source/case assessment for the Decision summary.

    Returns ``None`` when no assessment is attached or the verdict is
    ``NOT_ASSESSED`` (i.e. the comparator never ran), so the panel row
    is suppressed entirely. ``NO_DECISIVE_SIGNAL`` is still surfaced
    because it represents a real comparator pass that found nothing
    actionable — useful to display.
    """
    if features is None or features.source_case_assessment is None:
        return None
    asm = features.source_case_assessment
    if asm.verdict == SourceCaseVerdict.NOT_ASSESSED:
        return None
    return SourceCaseAssessmentVM(
        verdict=asm.verdict.value,
        shared_source_identifier=asm.shared_source_identifier,
        shared_source_identifier_type=(
            asm.shared_source_identifier_type.value
            if asm.shared_source_identifier_type is not None
            else None
        ),
        reasoning=asm.reasoning or None,
    )


_DIRECTION_LABEL: dict[ExplicitLinkDirection, str] = {
    ExplicitLinkDirection.R1_TO_R2: "Report 1 → Report 2",
    ExplicitLinkDirection.R2_TO_R1: "Report 2 → Report 1",
}


def _format_own_identifiers(refs: list[CaseIdentifierRef]) -> list[str]:
    """Render ``kind: value`` strings for a report's own case numbers."""
    return [f"{ref.kind.value}: {ref.value}" for ref in refs]


def _build_explicit_link_vm(
    features: PairFeatures | None,
    report_1: Report,
    report_2: Report,
) -> ExplicitLinkVM | None:
    """Project the pair's explicit inter-report link for the pair page.

    Returns ``None`` when no assessment is attached or the verdict is
    ``NOT_ASSESSED`` (neither report carried case-identifier data), so
    the section is suppressed rather than asserting an absence the
    pipeline never checked. ``NO_LINK`` *is* surfaced: the identifier
    lists are exactly what a reviewer needs in order to judge why
    nothing resolved.
    """
    if features is None or features.explicit_link is None:
        return None
    link: ExplicitLinkAssessment = features.explicit_link
    if link.verdict == ExplicitLinkVerdict.NOT_ASSESSED:
        return None

    matches = [
        ExplicitLinkMatchVM(
            direction_label=_DIRECTION_LABEL[match.direction],
            target_kind_label=match.target_kind.value,
            pointer_value=match.pointer_value,
            target_value=match.target_value,
        )
        for match in link.matches
    ]
    identifiers = [
        CaseIdentifierListVM(
            report_label=label,
            own=_format_own_identifiers(report.case_identifiers),
            # Pointers are bare values — the C.1.9 field carries no kind.
            pointers=list(report.other_case_identifiers),
        )
        for label, report in (("Report 1", report_1), ("Report 2", report_2))
    ]
    return ExplicitLinkVM(
        verdict=link.verdict.value,
        linked=link.verdict == ExplicitLinkVerdict.EXPLICIT_LINK,
        matches=matches,
        identifiers=identifiers,
        reasoning=link.reasoning or None,
    )


def _build_decision_summary(
    features: PairFeatures | None,
    classification: ClassificationResult | None,
) -> DecisionSummaryVM | None:
    """Build a compact decision summary from features and classification."""
    if classification is None:
        return None
    return DecisionSummaryVM(
        final_label=classification.final_label,
        explanation=classification.explanation,
        l_match=features.L_match if features else None,
        inconsistency_counts=(
            InconsistencyCountsVM(
                minor=features.inconsistency_counts.minor,
                moderate=features.inconsistency_counts.moderate,
                strong=features.inconsistency_counts.strong,
                decisive=features.inconsistency_counts.decisive,
            )
            if features
            else None
        ),
        clinical_date_counts=(
            ClinicalDateCountsVM(
                n_minor=features.clinical_date_inconsistency_counts.n_minor,
                n_moderate=features.clinical_date_inconsistency_counts.n_moderate,
                n_strong=features.clinical_date_inconsistency_counts.n_strong,
                n_decisive=features.clinical_date_inconsistency_counts.n_decisive,
            )
            if features
            else None
        ),
        ae_clinical_picture_counts=(
            AEClinicalPictureCountsVM(
                n_minor=features.ae_clinical_picture_counts.n_minor,
                n_moderate=features.ae_clinical_picture_counts.n_moderate,
                n_strong=features.ae_clinical_picture_counts.n_strong,
                n_decisive=features.ae_clinical_picture_counts.n_decisive,
            )
            if features
            else None
        ),
        ae_clinical_picture_status=(
            features.ae_clinical_picture_status.value if features else None
        ),
        source_case=_build_source_case_vm(features),
        relationship_assessment=(
            classification.relationship_assessment.value
            if classification.relationship_assessment
            else None
        ),
        penalty_summary=_format_penalty_summary(classification.penalty),
    )


# ===================================================================
# Frequency statistics view-model builder
# ===================================================================


def _join_and(items: list[str]) -> str:
    """Join a list of strings in natural-language 'A, B, and C' style.

    Returns an empty string when the list is empty.
    """
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _build_freq_stats_vm(
    bundle: FrozenPairBundle | None,
    *,
    country: str | None = None,
    year: int | None = None,
) -> FreqStatsVM | None:
    """Map a FrozenPairBundle to the frequency statistics view model.

    Returns None when no bundle is provided.  Omits country / country_year
    scopes when they are absent in the bundle (they are optional).

    Parameters
    ----------
    bundle : FrozenPairBundle, optional
        The frozen frequency artifact for this pair.
    country : str, optional
        The shared country of the pair's reports (e.g. ``"SE"``).
        Used as the row label for the country scope instead of the
        generic ``"Country"`` fallback.
    year : int, optional
        The shared report year (e.g. ``2022``).  Combined with
        ``country`` to form the country-year row label.
    """
    if bundle is None:
        return None

    scopes: list[FreqScopeVM] = []

    def _scope(label: str, s: FrozenScopeStats) -> FreqScopeVM:
        return FreqScopeVM(
            label=label,
            N_total=s.N_total,
            N_drugs=s.N_drugs,
            N_AEs=s.N_AEs,
            N_drugs_AEs=s.N_drugs_AEs,
        )

    scopes.append(_scope("Global", bundle.global_stats))

    if bundle.country_stats is not None:
        country_label = country if country else "Country"
        scopes.append(_scope(country_label, bundle.country_stats))

    if bundle.country_year_stats is not None:
        if country and year:
            cy_label = f"{country}, {year}"
        elif country:
            cy_label = country
        else:
            cy_label = "Country-year"
        scopes.append(_scope(cy_label, bundle.country_year_stats))

    return FreqStatsVM(
        scopes=scopes,
        matching_drugs=list(bundle.matching_drugs),
        matching_aes=list(bundle.matching_AEs),
    )


# ===================================================================
# Annotation view-model builder
# ===================================================================


def _build_annotation_vm(
    annotation: dict | None,
    pipeline_label: str | None,
) -> AnnotationVM | None:
    """Build an AnnotationVM from a raw eval-set annotation dict.

    Parameters
    ----------
    annotation : dict, optional
        A human annotation dict with at least a ``"duplicates_assessment"``
        key (e.g. ``{"pair_id": "...", "duplicates_assessment": "duplicates"}``).
    pipeline_label : str, optional
        The pipeline's normalised final label (e.g. ``"Duplicates"``).
        Used to compute the agreement flag when present.

    Returns
    -------
    AnnotationVM or None
    """
    if annotation is None:
        return None

    raw = annotation.get("duplicates_assessment", "")
    if not raw:
        return None

    from llm_dup_dev.eval.labels import is_abstain, normalise_label

    if is_abstain(raw):
        normalised = "Abstain"
    else:
        try:
            normalised = normalise_label(raw)
        except ValueError:
            normalised = raw  # preserve as-is for unknown values

    # Agreement is undefined when either side is Abstain: the pipeline's
    # Abstain is a refusal to decide (not a label that can match or
    # mismatch a human verdict), and an Abstain annotation provides no
    # ground truth. Either case → no ✓/✗ chip.
    agrees: bool | None = None
    if (
        pipeline_label is not None
        and pipeline_label != "Abstain"
        and normalised != "Abstain"
    ):
        agrees = normalised == pipeline_label

    return AnnotationVM(
        raw_label=raw,
        normalised_label=normalised,
        agrees_with_pipeline=agrees,
    )


# ===================================================================
# Screening algorithm view-model builder
# ===================================================================


# Shown for a null value or an empty list — an em dash reads as "nothing
# here" without implying a zero.
_SCREENING_ABSENT = "—"

# Upper bound on the decimals used to reach a significant digit. Past this
# the number is noise for a reviewer eyeballing a score.
_SCREENING_MAX_DECIMALS = 12


def _format_screening_float(value: float) -> str:
    """Render a screening score at two decimals, extending for tiny values.

    Screening scores are read comparatively (is this contribution big or
    small?), so two decimals is the right density for a table. A value
    smaller than 0.005 would round to ``0.00`` and read as absent, so the
    precision is extended until the first significant digit appears —
    ``0.0034`` renders as ``0.003``, ``1.2e-05`` as ``0.00001``.
    """
    if math.isnan(value) or math.isinf(value):
        return str(value)

    text = f"{value:.2f}"
    if value == 0.0 or float(text) != 0.0:
        return text
    for places in range(3, _SCREENING_MAX_DECIMALS + 1):
        text = f"{value:.{places}f}"
        if float(text) != 0.0:
            return text
    return text


def _format_screening_value(value: object) -> str:
    """Render one screening feature value for display.

    Booleans are tested before integers: ``bool`` is an ``int`` subclass,
    so the order matters. Lists are joined; anything else falls through to
    ``str`` rather than failing a render — the artifact model already
    rejects the shapes that have no sensible rendering.
    """
    if value is None:
        return _SCREENING_ABSENT
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _format_screening_float(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        if not value:
            return _SCREENING_ABSENT
        return ", ".join(_format_screening_value(item) for item in value)
    return str(value)


def _build_screening_vm(
    screening: dict | None,
    algorithm: str | None = None,
) -> ScreeningVM | None:
    """Build a ScreeningVM from one pair's screening-details entry.

    Parameters
    ----------
    screening : dict, optional
        The ``feature -> value`` payload for this pair, i.e. one value of
        :attr:`llm_dup_dev.data.offline.ScreeningDetails.entries`.
    algorithm : str, optional
        Name of the screening algorithm, for the section heading.

    Returns
    -------
    ScreeningVM or None
        ``None`` when no entry was supplied or it is empty, so the section
        is suppressed rather than rendering an empty table.
    """
    if not screening:
        return None

    return ScreeningVM(
        algorithm=algorithm,
        features=[
            ScreeningFeatureVM(key=key, value=_format_screening_value(value))
            for key, value in screening.items()
        ],
    )


# ===================================================================
# Public API
# ===================================================================


def build_pair_view_model(
    report_1: Report,
    report_2: Report,
    pair_id: str,
    features: PairFeatures | None = None,
    classification: ClassificationResult | None = None,
    external_url: str | None = None,
    external_url_label: str = "External link",
    frozen_bundle: FrozenPairBundle | None = None,
    include_debug: bool = False,
    annotation: dict | None = None,
    narrative_demographics_1: NarrativeDemographics | None = None,
    narrative_demographics_2: NarrativeDemographics | None = None,
    screening: dict | None = None,
    screening_algorithm: str | None = None,
) -> PairViewModel:
    """Build a complete comparison view model from two Reports.

    Parameters
    ----------
    report_1, report_2 : Report
        The two reports to compare.
    pair_id : str
        Identifier for this pair.
    features : PairFeatures, optional
        Pre-computed pipeline features for the evidence summary section.
    classification : ClassificationResult, optional
        Pipeline classification for the classification section.
    external_url : str, optional
        URL to render as a header link (e.g. a Vigilyze link).
    external_url_label : str
        Human-readable label for the external URL link.
    frozen_bundle : FrozenPairBundle, optional
        Frozen per-pair frequency statistics artifact.  When provided, the
        rendered report includes a Frequency Statistics section.
    include_debug : bool
        When True, serialises PairFeatures and ClassificationResult as
        JSON into the view model for collapsible debug sections.
    annotation : dict, optional
        A human annotation dict from the validation set with at least a
        ``"duplicates_assessment"`` key.  When provided, a human
        annotation panel is added to the page showing the label and
        whether it agrees with the pipeline classification.
    narrative_demographics_1, narrative_demographics_2 : NarrativeDemographics, optional
        Per-report narrative demographics extractions. When provided,
        each is merged into the corresponding report via
        ``merge_narrative_demographics`` before the demographics view
        model is built, so narrative-derived candidates appear in the
        demographics table alongside structured fields. Mirrors the
        same merge ``PipelineRunner.run_pair`` performs internally for
        feature generation; rendering needs an independent application
        because the runner discards the enriched reports.

        The extractions are also read directly for their ``evidence``
        snippets and ``notes``, which the merge drops — see
        ``_attach_narrative_evidence``.
    screening : dict, optional
        This pair's entry from a
        :class:`~llm_dup_dev.data.offline.ScreeningDetails` artifact — the
        ``feature -> value`` basis for the *screening* algorithm's
        classification. When provided, a Screening Algorithm section is
        added showing the values verbatim, so reviewers can compare the
        adjudicator's verdict against the screening method's.
    screening_algorithm : str, optional
        Name of the screening algorithm (e.g. ``"vigiMatch"``), shown in
        that section's heading.

    Returns
    -------
    PairViewModel
        Ready for rendering by ``render.render_pair``.
    """
    # Apply narrative demographics merge so narrative-extracted
    # candidates participate in the demographics comparison. Returns
    # copies; original Report objects are not mutated.
    if narrative_demographics_1 is not None:
        report_1 = merge_narrative_demographics(report_1, narrative_demographics_1)
    if narrative_demographics_2 is not None:
        report_2 = merge_narrative_demographics(report_2, narrative_demographics_2)

    # --- demographics (adaptive: skip if both empty) ---
    demographic_builders: dict[str, FieldComparison | None] = {
        "sex": _compare_sourced_values(
            "Sex",
            report_1.patient_sex_values,
            report_2.patient_sex_values,
        ),
        "age": _compare_age_values(
            report_1.patient_age_values,
            report_2.patient_age_values,
        ),
        "country": _compare_sourced_values(
            "Country",
            report_1.country_of_origin_values,
            report_2.country_of_origin_values,
        ),
        "report_date": _compare_date_values(
            "Report date",
            report_1.report_date_values,
            report_2.report_date_values,
            neutral=True,
        ),
        "height": _compare_measurement_values(
            "Height",
            report_1.patient_height_values,
            report_2.patient_height_values,
            tolerance=HEIGHT_TOLERANCE_CM,
            partial_tolerance=HEIGHT_PARTIAL_TOLERANCE_CM,
        ),
        "weight": _compare_measurement_values(
            "Weight",
            report_1.patient_weight_values,
            report_2.patient_weight_values,
            tolerance=WEIGHT_TOLERANCE_KG,
            partial_tolerance=WEIGHT_PARTIAL_TOLERANCE_KG,
        ),
    }

    demographics = [
        comparison
        for key in _DEMOGRAPHIC_FIELD_ORDER
        if (comparison := demographic_builders.get(key)) is not None
    ]

    # Evidence comes off the extractions rather than the merged reports, and
    # does not depend on PairFeatures — so this runs outside the features
    # block below.
    if narrative_demographics_1 is not None or narrative_demographics_2 is not None:
        _attach_narrative_evidence(
            demographics,
            narrative_demographics_1,
            narrative_demographics_2,
        )

    # --- drugs & AEs ---
    drugs = _build_drug_rows(report_1, report_2)
    adverse_events = _build_ae_rows(report_1, report_2)

    # --- narratives ---
    narratives = NarrativeComparison(
        left_text=report_1.narrative,
        right_text=report_2.narrative,
        left_literature_reference=report_1.literature_reference,
        right_literature_reference=report_2.literature_reference,
    )

    # --- optional sections ---
    features_summary = (
        _build_features_summary(features) if features else None
    )
    if features is not None and features_summary is not None:
        _annotate_demographic_contributions(
            demographics, features_summary.feature_contributions
        )
        _apply_demographic_severity(demographics, features)
    classification_summary = (
        _build_classification_summary(classification, features)
        if classification
        else None
    )
    # Derive country and year from the reports for descriptive scope labels.
    # country_stats is present only when both reports share a country, so
    # either report is a reliable source; we prefer report_1.
    _freq_country: str | None = None
    _freq_year: int | None = None
    if frozen_bundle is not None:
        if report_1.country_of_origin_values:
            _freq_country = report_1.country_of_origin_values[0].value
        if report_1.report_date_values:
            # DateValue.value is an ISO-8601 string; the first four characters
            # are always the year regardless of resolution.
            _freq_year = int(report_1.report_date_values[0].value[:4])

    freq_stats = _build_freq_stats_vm(
        frozen_bundle, country=_freq_country, year=_freq_year
    )

    explicit_link = _build_explicit_link_vm(features, report_1, report_2)

    decision_summary = _build_decision_summary(features, classification)

    annotation_vm = _build_annotation_vm(
        annotation,
        pipeline_label=(
            classification.final_label if classification else None
        ),
    )

    screening_vm = _build_screening_vm(screening, screening_algorithm)

    # Optional debug JSON for collapsible raw-data sections
    debug_json: str | None = None
    if include_debug and (features or classification):
        import json

        debug_parts: dict = {}
        if features:
            debug_parts["PairFeatures"] = features.model_dump(mode="json")
        if classification:
            debug_parts["ClassificationResult"] = classification.model_dump(
                mode="json"
            )
        debug_json = json.dumps(debug_parts, indent=2, default=str)

    return PairViewModel(
        pair_id=pair_id,
        report_1_id=report_1.report_id,
        report_2_id=report_2.report_id,
        external_url=external_url,
        external_url_label=external_url_label,
        demographics=demographics,
        drugs=drugs,
        adverse_events=adverse_events,
        narratives=narratives,
        narrative_notes_1=(
            narrative_demographics_1.notes
            if narrative_demographics_1 is not None
            else ""
        ),
        narrative_notes_2=(
            narrative_demographics_2.notes
            if narrative_demographics_2 is not None
            else ""
        ),
        features_summary=features_summary,
        classification_summary=classification_summary,
        explicit_link=explicit_link,
        freq_stats=freq_stats,
        screening=screening_vm,
        decision_summary=decision_summary,
        annotation=annotation_vm,
        debug_json=debug_json,
    )


# ===================================================================
# Shared scoring/display utilities
# ===================================================================


def _format_penalty_summary(penalty: InconsistencyPenalty | None) -> str | None:
    """Render the aggregated discrepancy penalty the classifier recorded.

    Produces a string like ``"3 × 0.4 + 1 × 1.0 = 2.2 < 4.0"`` showing how
    each severity contributed to ``w_tot``, alongside the comparison the
    decision actually made.

    Every number comes from :class:`InconsistencyPenalty` — the weights the
    run applied, the total the classifier computed, and the threshold it
    tested against. Nothing is recomputed and nothing is parsed out of the
    explanation prose, so the row cannot disagree with the decision it
    describes.

    Where the decision made no ``w_tot`` comparison — the below-limited zone
    (``L_match`` decides) and ``SAME_SOURCE_DIFFERENT_CASE`` (pinned
    regardless) — the score is shown without a comparison rather than
    inventing one.

    Returns ``None`` only when no penalty was recorded.
    """
    if penalty is None:
        return None

    terms = " + ".join(
        f"{term.count} × {term.weight:.1f}" for term in penalty.terms
    )
    total = f"{terms} = {penalty.w_tot:.1f}" if terms else f"{penalty.w_tot:.1f}"

    if penalty.threshold is None:
        return f"{total} (not compared)"
    op = "<" if penalty.satisfied else ">="
    return f"{total} {op} {penalty.threshold:.1f}"


# ---------------------------------------------------------------------------
# Per-feature L_match contribution chips
# ---------------------------------------------------------------------------
# L_match is defined on the exact multiplicity-adjusted probability
#
#     L_match = max(0, -log10(P_match + ε)),  P_match = 1 - (1 - p_joint)**N_pairs
#
# which does not decompose additively. These chips instead display its
# rare-event approximation, which does:
#
#     L_match ≈ max(0,  -log10(N_pairs)  +  Σ -log10(p_f) )
#
# where N_pairs = N_anchor * (N_anchor − 1) / 2. Each per-feature chip
# carries the positive evidence contribution -log10(p_f) (green); the
# anchor chip carries the negative starting point -log10(N_pairs) (red).
# The approximation is exact in the limit N_pairs * p_joint → 0 and drifts
# below 0.03 only once P_match approaches 1, where L_match is clamped to 0
# regardless — so the chips remain a faithful reading of the displayed score.

# Magnitude bins shared between green (positive evidence) and red
# (anchor penalty) chips so reviewers learn one scale.
_CONTRIB_BINS: tuple[tuple[float, str, str], ...] = (
    (0.3, "vw", "vw"),
    (1.0, "w", "w"),
    (2.0, "m", "m"),
    (3.5, "s", "s"),
    (math.inf, "vs", "vs"),
)


def _contrib_pos_class(c: float) -> str:
    """Return the CSS class for a positive evidence contribution (>= 0)."""
    mag = abs(c)
    for upper, suffix, _ in _CONTRIB_BINS:
        if mag < upper:
            return f"contrib-pos-{suffix}"
    return "contrib-pos-vs"


def _contrib_neg_class(c: float) -> str:
    """Return the CSS class for an anchor-penalty contribution (<= 0).

    The class is selected from ``|c|`` so the same bins as the positive
    scale apply.
    """
    mag = abs(c)
    for upper, _, suffix in _CONTRIB_BINS:
        if mag < upper:
            return f"contrib-neg-{suffix}"
    return "contrib-neg-vs"


def _anchor_penalty(n_anchor: int | None) -> float | None:
    """Return -log10(N*(N-1)/2) for N >= 2, else None.

    The anchor penalty is the additive starting point of the rare-event
    approximation to L_match (see the block comment above): it captures
    how many random pairs the per-feature evidence had to beat. L_match
    itself is -log10(P_match + eps) and does not decompose additively.
    """
    if n_anchor is None or n_anchor < 2:
        return None
    n_pairs = n_anchor * (n_anchor - 1) / 2
    return -math.log10(n_pairs)


# Map probability/inconsistency feature names to demographic FieldComparison
# labels.  Both long (``"patient_weight"``) and short (``"weight"``) forms
# are accepted for robustness. Used by ``_annotate_demographic_contributions``
# in this module and by ``review_html.index`` formatters.
_FEATURE_TO_DEMO_LABEL: dict[str, str] = {
    "patient_sex": "Sex",
    "patient_age": "Age",
    "patient_country": "Country",
    "patient_height": "Height",
    "patient_weight": "Weight",
    # Short forms (used in some inconsistency feature names)
    "sex": "Sex",
    "age": "Age",
    "height": "Height",
    "weight": "Weight",
}


# Re-exports for backwards compatibility — render.py and the test
# suite import these names from this module. The implementations live
# in review_html.index.
from .index import (  # noqa: E402  (intentional bottom-of-file import to avoid cycle)
    _format_matching_features,  # noqa: F401  (re-export; imported from here by tests)
    build_batch_index_item,
)

__all__ = [
    "build_batch_index_item",
    "build_pair_view_model",
]
