"""Data models for the PairFeatures layer.

The PairFeatures layer contains all derived information used by the
duplicate adjudication pipeline to classify a report pair as duplicates,
otherwise related, or unrelated. These features are computed from the
Report layer and statistical summaries of the database.

See: docs/data_contracts.md § PairFeatures Layer
See: docs/duplicate_adjudication_spec.md § Rarity-based matching evidence
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from .enums import (
    AEClinicalPictureStatus,
    AnchorType,
    InconsistencySeverity,
    ReportingContextLevel,
)
from .narrative_source_case import SourceCaseAssessment


class ReportingContext(BaseModel):
    """The population context for frequency estimation.

    Defines the subset of the database within which probabilities
    and frequencies are evaluated.

    See: docs/duplicate_adjudication_spec.md § Reporting context
    """

    level: ReportingContextLevel
    country: str | None = None
    year: int | None = None


class AnchorPopulation(BaseModel):
    """Anchor population statistics for probability estimation.

    Contains three candidate population sizes and the selected anchor.

    See: docs/duplicate_adjudication_spec.md § Anchor selection
    """

    N_drugs: int
    N_AEs: int
    N_drugs_AEs: int
    type: AnchorType
    N_anchor: int


class FeatureContribution(BaseModel):
    """Per-feature probability contribution to the composite p_joint.

    Exposes the individual feature probabilities that are multiplied
    together to form p_joint. Useful for debugging and method development:
    understanding which features contributed how much evidence.

    See: docs/duplicate_adjudication_spec.md § Rarity-based matching evidence
    """

    feature: str
    p_f: float
    method: str  # "empirical" or "domain_size"
    domain_size: float | None = None
    description: str = ""


class ProbabilityEstimate(BaseModel):
    """Result of the collision probability computation.

    ``p_joint`` is the chance-match probability for a single pairwise
    opportunity; ``p_match`` adjusts it for the multiplicity of the anchor
    population. Both are probabilities in [0, 1] — neither is a p-value.

    See: docs/duplicate_adjudication_spec.md § Rarity-based matching evidence
    """

    p_joint: float = Field(
        description=(
            "Chance-match probability for one equivalent pairwise "
            "opportunity: the product of the per-feature p_f values."
        ),
    )
    p_match: float = Field(
        description=(
            "P_match = 1 - (1 - p_joint)**M: the probability that at least "
            "one chance collision occurs among the M = C(N_anchor, 2) "
            "unordered report pairs in the anchor population. Drives "
            "L_match = max(0, -log10(P_match + epsilon)). A multiplicity-"
            "adjusted chance-match probability under an independence/"
            "equivalent-opportunity assumption, not a p-value."
        ),
    )
    feature_contributions: list[FeatureContribution] = Field(default_factory=list)


class Inconsistency(BaseModel):
    """A detected inconsistency between two reports.

    Carries entries from all sources: rule-based demographic/drug-set
    checks and LLM-assisted AE clinical-picture findings. The optional
    ``source``, ``kind``, ``rationale``, ``terms``, ``cluster_label``,
    ``suppressed`` and ``suppressed_by`` fields support the richer
    LLM-derived entries while remaining backwards-compatible defaults
    for existing rule-based call sites.

    Suppressed entries (``suppressed=True``) are kept in the master
    list for transparency in human review but are excluded from the
    severity counts that feed downstream decision logic.

    See: docs/duplicate_adjudication_spec.md § Inconsistency signals
    """

    feature: str
    severity: InconsistencySeverity
    difference: float | None = None
    units: str | None = None
    description: str | None = None

    # Extended provenance / metadata (defaulted so existing call sites
    # need no change). Populated by the AE clinical-picture component.
    source: str = "rule"
    kind: str | None = None
    rationale: str = ""
    terms: list[str] = Field(default_factory=list)
    cluster_label: str = ""
    suppressed: bool = False
    suppressed_by: str = ""


class InconsistencyCounts(BaseModel):
    """Summary counts of inconsistencies by severity level.

    These counts are merged across all inconsistency sources:
    demographic (age, weight, height, sex), drug-set, clinical-date,
    and narrative-derived inconsistencies.  They feed directly into
    the weighted inconsistency score w_tot in the decision logic.

    Decisive inconsistencies (e.g. distinct patient IDs from the
    same study, or clinical-date impossibilities) contribute to
    w_tot via ``w_decisive``, which is set high enough that any
    decisive count always exceeds the zone tolerance.
    """

    minor: int = 0
    moderate: int = 0
    strong: int = 0
    decisive: int = 0


class ClinicalDateInconsistencyCounts(BaseModel):
    """Severity-bucketed counts from the clinical-date inconsistency module.

    See: feature_generation/clinical_date_inconsistencies.py
    """

    n_minor: int = 0
    n_moderate: int = 0
    n_strong: int = 0
    n_decisive: int = 0


class ClinicalDateFindingSummary(BaseModel):
    """Serializable summary of a single selected clinical-date finding.

    Captures the key information needed for human review without
    exposing internal implementation details.
    """

    entity_label: str  # e.g. "drug_start:aspirin", "ae_onset:headache"
    severity: InconsistencySeverity
    source_date: str  # date value on the source report
    matched_date: str  # date value on the other report
    diff_days: int | None = None  # minimum day difference (None for decisive)
    downgrades: list[str] = Field(default_factory=list)


class AEClinicalPictureCounts(BaseModel):
    """Severity-bucketed counts from the AE clinical-picture LLM component.

    Counts reflect *kept* findings only (i.e. after the dedup step
    suppresses subordinate findings under a decisive global mismatch).
    They are derivable from the master ``Inconsistency`` list by
    filtering ``source == "llm_ae_clinical_picture"`` and
    ``suppressed is False``, but persisted explicitly for ergonomics
    and downstream symmetry with ``ClinicalDateInconsistencyCounts``.

    See: llm/tasks/ae_clinical_picture.py
    """

    n_minor: int = 0
    n_moderate: int = 0
    n_strong: int = 0
    n_decisive: int = 0


class GlobalOverlapLevel(str, Enum):
    """Most granular shared MedDRA-hierarchy level between two AE pictures.

    Used by the deterministic AE clinical-picture assessor (Step 1).
    """

    PT = "pt"
    HLT = "hlt"
    HLGT = "hlgt"
    SOC = "soc"
    NONE = "none"


class RuleBasedAEClinicalPictureDetails(BaseModel):
    """Audit trail of the deterministic Step-1 / Step-2 AE clinical-picture
    assessment. Surfaced on the result and on ``PairFeatures`` so the
    review HTML and downstream tooling can display the underlying U/M
    counts and overlap level alongside the bucketed severity counts.

    See: feature_generation/ae_clinical_picture_rules.py
    """

    step1_overlap_level: GlobalOverlapLevel
    step1_severity: InconsistencySeverity | None = None  # None = no inconsistency
    step2_r1_to_r2_u: int = 0
    step2_r1_to_r2_m: int = 0
    step2_r1_to_r2_severity: InconsistencySeverity | None = None
    step2_r2_to_r1_u: int = 0
    step2_r2_to_r1_m: int = 0
    step2_r2_to_r1_severity: InconsistencySeverity | None = None
    investigations_excluded_step2: bool = True
    missing_hierarchy_warnings: list[str] = Field(default_factory=list)


class AEClinicalPictureResult(BaseModel):
    """Full result of the AE clinical-picture inconsistency assessment.

    Returned by either
    :func:`llm_dup_dev.llm.tasks.ae_clinical_picture.assess_ae_clinical_picture`
    (LLM-based) or
    :func:`llm_dup_dev.feature_generation.ae_clinical_picture_rules.assess_ae_clinical_picture_rule_based`
    (deterministic) and threaded through the pipeline.  ``findings`` are
    already in the unified :class:`Inconsistency` shape, ready to be
    appended to ``PairFeatures.inconsistencies`` (suppressed entries
    included).

    ``method`` distinguishes the two assessors.  ``rule_based_details``
    is populated only when ``method == "rule_based"`` and carries the
    Step-1 overlap level and Step-2 directional U/M counts for audit.
    """

    status: AEClinicalPictureStatus = AEClinicalPictureStatus.NOT_ASSESSED
    # "" (assessed) | "no_llm_client" | "trivially_matched" | "llm_error"
    # | "rule_based" | "llm_fallback_to_rule" | "missing_hierarchy"
    reason: str = ""
    method: str = ""  # "" | "llm" | "rule_based"
    counts: AEClinicalPictureCounts = Field(default_factory=AEClinicalPictureCounts)
    findings: list[Inconsistency] = Field(default_factory=list)
    reasoning: str = ""  # free-text rationale for human review (LLM only)
    rule_based_details: RuleBasedAEClinicalPictureDetails | None = None


class EvidenceSummary(BaseModel):
    """Diagnostic summary of shared and report-specific evidence.

    Optional field for debugging and interpretability.
    Does not affect the decision logic.
    """

    shared: dict = Field(default_factory=dict)
    report1_only: dict = Field(default_factory=dict)
    report2_only: dict = Field(default_factory=dict)


class PairFeatures(BaseModel):
    """All derived features for adjudicating a candidate duplicate report pair.

    Combines shared clinical context, anchor population statistics,
    rarity-based matching evidence, detected inconsistencies and
    (optionally) narrative consistency. Computed from the Report layer
    and statistical summaries; provides the inputs to the duplicate
    adjudication decision logic.

    See: docs/data_contracts.md § PairFeatures Layer
    """

    pair_id: str
    feature_version: str = "1.0"

    reporting_context: ReportingContext

    # Shared clinical feature sets (canonical sorted order)
    matching_drugs: list[str] = Field(default_factory=list)
    matching_AEs: list[str] = Field(default_factory=list)

    # Anchor population and probability
    anchor: AnchorPopulation
    probability: ProbabilityEstimate
    L_match: float
    # Patient-specific evidence: the same log10 scale as L_match, restricted
    # to the features that identify a patient (drug_set / ae_set excluded) and
    # carrying no multiplicity adjustment. Computed for every pair, but
    # consumed only by the shared-source / uncomparable-case branch of the
    # decision logic. See feature_generation.probability.compute_l_patient.
    L_patient: float

    # Inconsistencies (structured: age, weight, height, sex, drug-set)
    inconsistencies: list[Inconsistency] = Field(default_factory=list)
    # Merged counts across all sources (incl. clinical-date) — used by decision logic
    inconsistency_counts: InconsistencyCounts = Field(
        default_factory=InconsistencyCounts
    )

    # Clinical-date inconsistencies (rule-based module) — separate for auditability
    clinical_date_inconsistency_counts: ClinicalDateInconsistencyCounts = Field(
        default_factory=ClinicalDateInconsistencyCounts
    )
    clinical_date_findings: list[ClinicalDateFindingSummary] = Field(
        default_factory=list
    )

    # AE clinical-picture (LLM-assisted) — status + bucketed counts.
    # The actual finding entries live in ``inconsistencies`` above
    # (filter ``source == "llm_ae_clinical_picture"`` to retrieve them).
    ae_clinical_picture_status: AEClinicalPictureStatus = (
        AEClinicalPictureStatus.NOT_ASSESSED
    )
    ae_clinical_picture_reason: str = ""
    ae_clinical_picture_method: str = ""  # "" | "llm" | "rule_based"
    ae_clinical_picture_counts: AEClinicalPictureCounts = Field(
        default_factory=AEClinicalPictureCounts
    )
    ae_clinical_picture_reasoning: str = ""
    # Populated only when method == "rule_based" — Step-1/Step-2 audit trail.
    ae_clinical_picture_rule_based_details: (
        RuleBasedAEClinicalPictureDetails | None
    ) = None

    # Derived narrative source/case signals (pair-level deterministic
    # verdict from two per-report LLM extractions). ``None`` when the
    # extraction/comparison was not performed. Raw verdict is kept
    # alongside the synthesized Inconsistency rows so the decision
    # logic can branch on the verdict directly.
    source_case_assessment: SourceCaseAssessment | None = None

    # Optional diagnostic information
    evidence_summary: EvidenceSummary | None = None
