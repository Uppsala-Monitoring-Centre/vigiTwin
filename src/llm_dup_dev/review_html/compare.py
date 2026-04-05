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

from collections import OrderedDict

from llm_dup_dev.data.offline import FrozenPairBundle
from llm_dup_dev.feature_generation.matching import (
    _coarser_resolution,
    _truncate_to_resolution,
)
from llm_dup_dev.models.classification import ClassificationResult
from llm_dup_dev.models.pair_features import PairFeatures
from llm_dup_dev.models.report import (
    AdverseEvent,
    AgeValue,
    DateValue,
    Drug,
    MeasurementValue,
    Report,
    SourcedValue,
)

from .models import (
    AEDetail,
    AERow,
    AnnotationVM,
    BatchIndexItem,
    ClassificationSummaryVM,
    ComparisonStatus,
    DecisionSummaryVM,
    DrugDetail,
    DrugRow,
    FeatureContributionVM,
    FeaturesSummaryVM,
    FieldComparison,
    FreqScopeVM,
    FreqStatsVM,
    InconsistencyCountsVM,
    InconsistencyVM,
    NarrativeComparison,
    PairViewModel,
    SubfieldStatus,
)
from .normalize import (
    MISSING,
    format_age_values,
    format_date_values,
    format_measurement_values,
    format_partial_date,
    format_sourced_values,
    normalize_ae_key,
    normalize_drug_key,
)

# ---------------------------------------------------------------------------
# Display-tolerance constants (for colour-coding only, not pipeline thresholds)
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
    """Minimum absolute difference across all (l, r) pairs."""
    return min(abs(l - r) for l in left for r in right)


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


def _build_subfield_status(l: Drug, r: Drug) -> SubfieldStatus:
    """Build per-sub-field comparison statuses for a drug present in both reports."""
    role_l = l.role.value if l.role else None
    role_r = r.role.value if r.role else None
    return SubfieldStatus(
        role=_compare_optional_str(role_l, role_r),
        form=_compare_optional_str(l.form, r.form),
        strength=_compare_optional_str(l.strength, r.strength),
        start_date=_compare_partial_date_str(l, r, "start_date"),
        end_date=_compare_partial_date_str(l, r, "end_date"),
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
    l, r = left[0], right[0]

    # Row background colour is driven by date sub-fields only.
    # Dose / form / role mismatches are shown via inline sub-field coloring
    # but do not escalate the row to yellow.
    sub = _build_subfield_status(l, r)
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
    l, r = left_ae.start_date, right_ae.start_date
    if l is None or r is None:
        return True  # no date on at least one side → not a conflict
    coarse = _coarser_resolution(l.resolution, r.resolution)
    return (
        _truncate_to_resolution(l.value, coarse)
        == _truncate_to_resolution(r.value, coarse)
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

    return FeaturesSummaryVM(
        l_match=features.L_match,
        expected_collisions=features.probability.expected_collisions,
        anchor_type=features.anchor.type.value,
        anchor_n=features.anchor.N_anchor,
        reporting_context=ctx_str,
        narrative_consistency=features.narrative_consistency.value,
        matching_drugs=features.matching_drugs,
        matching_aes=features.matching_AEs,
        inconsistencies=[
            InconsistencyVM(
                feature=inc.feature,
                severity=inc.severity.value,
                description=inc.description or "",
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
        inconsistency_counts=InconsistencyCountsVM(
            moderate=features.inconsistency_counts.moderate,
            strong=features.inconsistency_counts.strong,
            decisive=features.inconsistency_counts.decisive,
        ),
    )


def _build_classification_summary(
    classification: ClassificationResult,
) -> ClassificationSummaryVM:
    return ClassificationSummaryVM(
        primary=classification.primary.value,
        relationship=(
            classification.relationship.value
            if classification.relationship
            else None
        ),
        final_label=classification.final_label,
        explanation=classification.explanation,
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
                moderate=features.inconsistency_counts.moderate,
                strong=features.inconsistency_counts.strong,
                decisive=features.inconsistency_counts.decisive,
            )
            if features
            else None
        ),
        narrative_consistency=(
            features.narrative_consistency.value if features else None
        ),
        relationship=(
            classification.relationship.value
            if classification.relationship
            else None
        ),
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

    def _scope(label: str, s: object) -> FreqScopeVM:
        return FreqScopeVM(
            label=label,
            N_total=s.N_total,  # type: ignore[union-attr]
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

    agrees: bool | None = None
    if pipeline_label is not None and normalised != "Abstain":
        agrees = normalised == pipeline_label

    return AnnotationVM(
        raw_label=raw,
        normalised_label=normalised,
        agrees_with_pipeline=agrees,
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

    Returns
    -------
    PairViewModel
        Ready for rendering by ``render.render_pair``.
    """
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
        demographic_builders[key]
        for key in _DEMOGRAPHIC_FIELD_ORDER
        if demographic_builders.get(key) is not None
    ]

    # --- drugs & AEs ---
    drugs = _build_drug_rows(report_1, report_2)
    adverse_events = _build_ae_rows(report_1, report_2)

    # --- narratives ---
    narratives = NarrativeComparison(
        left_text=report_1.narrative,
        right_text=report_2.narrative,
    )

    # --- optional sections ---
    features_summary = (
        _build_features_summary(features) if features else None
    )
    classification_summary = (
        _build_classification_summary(classification)
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

    decision_summary = _build_decision_summary(features, classification)

    annotation_vm = _build_annotation_vm(
        annotation,
        pipeline_label=(
            classification.final_label if classification else None
        ),
    )

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
        features_summary=features_summary,
        classification_summary=classification_summary,
        freq_stats=freq_stats,
        decision_summary=decision_summary,
        annotation=annotation_vm,
        debug_json=debug_json,
    )


def build_batch_index_item(
    vm: PairViewModel,
    filename: str,
) -> BatchIndexItem:
    """Build an index entry from a rendered pair view model."""
    # Inconsistency summary: "2M / 1S / 0D" style
    inc_summary: str | None = None
    if (
        vm.features_summary
        and vm.features_summary.inconsistency_counts
    ):
        ic = vm.features_summary.inconsistency_counts
        inc_summary = f"{ic.moderate}M / {ic.strong}S / {ic.decisive}D"

    return BatchIndexItem(
        pair_id=vm.pair_id,
        report_1_id=vm.report_1_id,
        report_2_id=vm.report_2_id,
        filename=filename,
        final_label=(
            vm.classification_summary.final_label
            if vm.classification_summary
            else None
        ),
        l_match=(
            vm.features_summary.l_match
            if vm.features_summary
            else None
        ),
        relationship=(
            vm.classification_summary.relationship
            if vm.classification_summary
            else None
        ),
        inconsistency_summary=inc_summary,
        narrative_consistency=(
            vm.features_summary.narrative_consistency
            if vm.features_summary
            else None
        ),
        annotation_label=(
            vm.annotation.normalised_label if vm.annotation else None
        ),
        annotation_agrees=(
            vm.annotation.agrees_with_pipeline if vm.annotation else None
        ),
    )
