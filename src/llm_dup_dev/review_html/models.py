"""View models for the HTML review renderer.

These intermediate models sit between raw Report/PairFeatures domain objects
and the Jinja2 templates. The comparison layer produces these; the rendering
layer consumes them. Neither layer touches the other's data structures.

Design goals:
    - Templates only access typed, pre-formatted fields (no business logic).
    - ComparisonStatus maps directly to CSS class names.
    - Adaptive inclusion: fields with no data on either side are simply
      omitted from the view model lists.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Comparison status
# ---------------------------------------------------------------------------


class ComparisonStatus(str, Enum):
    """Semantic comparison outcome, mapped to CSS classes by the renderer."""

    MATCH = "match"
    PARTIAL_MATCH = "partial_match"
    MISMATCH = "mismatch"
    MISSING_LEFT = "missing_left"
    MISSING_RIGHT = "missing_right"
    INFORMATIONAL = "informational"
    NEUTRAL = "neutral"  # shown with status badge but no row background colour


# ---------------------------------------------------------------------------
# Scalar field comparison (demographics / report metadata)
# ---------------------------------------------------------------------------


class FieldComparison(BaseModel):
    """One row in the demographics / metadata comparison table."""

    label: str
    left_display: str
    right_display: str
    status: ComparisonStatus
    tooltip: str = ""


# ---------------------------------------------------------------------------
# Drug comparison
# ---------------------------------------------------------------------------


class SubfieldStatus(BaseModel):
    """Per-sub-field match status within a drug cell.

    Allows the template to color individual drug detail rows (strength,
    form, role, dates) independently rather than only at the row level.
    """

    role: ComparisonStatus | None = None
    form: ComparisonStatus | None = None
    strength: ComparisonStatus | None = None
    start_date: ComparisonStatus | None = None
    end_date: ComparisonStatus | None = None


class DrugDetail(BaseModel):
    """Per-report rendering detail for a single drug instance."""

    substance: str
    role: str | None = None
    form: str | None = None
    strength: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    subfield_status: SubfieldStatus | None = None


class DrugRow(BaseModel):
    """One row in the drug comparison table (keyed by normalized substance)."""

    substance: str
    left_details: list[DrugDetail] = Field(default_factory=list)
    right_details: list[DrugDetail] = Field(default_factory=list)
    status: ComparisonStatus


# ---------------------------------------------------------------------------
# Adverse-event comparison
# ---------------------------------------------------------------------------


class AEDetail(BaseModel):
    """Per-report rendering detail for a single adverse event instance."""

    pt_name: str
    start_date: str | None = None


class AERow(BaseModel):
    """One row in the AE comparison table (keyed by normalized PT name)."""

    pt_name: str
    left_details: list[AEDetail] = Field(default_factory=list)
    right_details: list[AEDetail] = Field(default_factory=list)
    status: ComparisonStatus


# ---------------------------------------------------------------------------
# Narrative comparison
# ---------------------------------------------------------------------------


class NarrativeComparison(BaseModel):
    """Side-by-side narrative display."""

    left_text: str | None = None
    right_text: str | None = None


# ---------------------------------------------------------------------------
# Optional analytical sections (future-extensible)
# ---------------------------------------------------------------------------


class InconsistencyVM(BaseModel):
    """View model for a single detected inconsistency."""

    feature: str
    severity: str
    description: str


class FeatureContributionVM(BaseModel):
    """View model for a single feature's probability contribution."""

    feature: str
    p_f: float
    method: str
    domain_size: float | None = None
    description: str = ""


class InconsistencyCountsVM(BaseModel):
    """View model for inconsistency count summary."""

    moderate: int = 0
    strong: int = 0
    decisive: int = 0

    @property
    def total(self) -> int:
        return self.moderate + self.strong + self.decisive


class FeaturesSummaryVM(BaseModel):
    """View model for the optional PairFeatures evidence section."""

    l_match: float
    expected_collisions: float
    anchor_type: str
    anchor_n: int
    reporting_context: str
    narrative_consistency: str
    matching_drugs: list[str] = Field(default_factory=list)
    matching_aes: list[str] = Field(default_factory=list)
    inconsistencies: list[InconsistencyVM] = Field(default_factory=list)

    # Extended fields for prediction/evaluation mode
    p_joint: float | None = None
    anchor_n_drugs: int | None = None
    anchor_n_aes: int | None = None
    anchor_n_drugs_aes: int | None = None
    feature_contributions: list[FeatureContributionVM] = Field(
        default_factory=list
    )
    inconsistency_counts: InconsistencyCountsVM | None = None


class ClassificationSummaryVM(BaseModel):
    """View model for the optional classification result section."""

    primary: str
    relationship: str | None = None
    final_label: str
    explanation: str


# ---------------------------------------------------------------------------
# Frequency statistics section
# ---------------------------------------------------------------------------


class FreqScopeVM(BaseModel):
    """View model for one reporting-scope's frequency counts."""

    label: str  # "Global", "Country", or "Country-year"
    N_total: int
    N_drugs: int
    N_AEs: int
    N_drugs_AEs: int


class FreqStatsVM(BaseModel):
    """View model for the frequency statistics section.

    Contains one entry per available reporting scope (global is always
    present when this object exists; country and country_year are optional).

    ``matching_drugs`` and ``matching_aes`` carry the canonical drug /
    adverse-event lists used to compute the counts; the template uses them
    to render descriptive column headers.
    """

    scopes: list[FreqScopeVM] = Field(default_factory=list)
    matching_drugs: list[str] = Field(default_factory=list)
    matching_aes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Decision summary (compact panel for prediction/evaluation mode)
# ---------------------------------------------------------------------------


class DecisionSummaryVM(BaseModel):
    """Compact decision summary for prediction/evaluation pages.

    Combines the final classification with the key quantities that
    drove the decision, for at-a-glance review.
    """

    final_label: str
    explanation: str
    l_match: float | None = None
    inconsistency_counts: InconsistencyCountsVM | None = None
    narrative_consistency: str | None = None
    relationship: str | None = None


# ---------------------------------------------------------------------------
# Top-level page view model
# ---------------------------------------------------------------------------


class PairViewModel(BaseModel):
    """Complete view model for one pair's HTML page.

    Built by ``compare.build_pair_view_model`` and consumed by
    ``render.render_pair``.
    """

    pair_id: str
    report_1_id: str
    report_2_id: str

    # Optional external link shown in the header (e.g. a Vigilyze URL)
    external_url: str | None = None
    external_url_label: str = "External link"

    demographics: list[FieldComparison] = Field(default_factory=list)
    drugs: list[DrugRow] = Field(default_factory=list)
    adverse_events: list[AERow] = Field(default_factory=list)
    narratives: NarrativeComparison = Field(
        default_factory=NarrativeComparison
    )

    features_summary: FeaturesSummaryVM | None = None
    classification_summary: ClassificationSummaryVM | None = None
    freq_stats: FreqStatsVM | None = None
    decision_summary: DecisionSummaryVM | None = None
    debug_json: str | None = None


# ---------------------------------------------------------------------------
# Batch index
# ---------------------------------------------------------------------------


class BatchIndexItem(BaseModel):
    """One entry in the batch index page."""

    pair_id: str
    report_1_id: str
    report_2_id: str
    filename: str
    final_label: str | None = None
    l_match: float | None = None
    relationship: str | None = None
    inconsistency_summary: str | None = None
    narrative_consistency: str | None = None
