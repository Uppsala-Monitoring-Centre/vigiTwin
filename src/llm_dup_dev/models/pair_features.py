"""Data models for the PairFeatures layer.

The PairFeatures layer contains all derived information used by the
duplicate adjudication pipeline to classify a report pair as duplicates,
otherwise related, or unrelated. These features are computed from the
Report layer and statistical summaries of the database.

See: docs/data_contracts.md § PairFeatures Layer
See: docs/duplicate_adjudication_spec.md § Evidence components
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .enums import (
    AnchorType,
    InconsistencySeverity,
    NarrativeConsistency,
    ReportingContextLevel,
)


class ReportingContext(BaseModel):
    """The population context for frequency estimation.

    Defines the subset of the database within which probabilities
    and frequencies are evaluated.

    See: docs/duplicate_adjudication_spec.md § Reporting Context
    """

    level: ReportingContextLevel
    country: str | None = None
    year: int | None = None


class AnchorPopulation(BaseModel):
    """Anchor population statistics for probability estimation.

    Contains three candidate population sizes and the selected anchor.

    See: docs/duplicate_adjudication_spec.md § Anchor Selection
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

    See: docs/duplicate_adjudication_spec.md § Probability estimation
    """

    feature: str
    p_f: float
    method: str  # "empirical" or "domain_size"
    domain_size: float | None = None
    description: str = ""


class ProbabilityEstimate(BaseModel):
    """Result of the collision probability computation.

    See: docs/duplicate_adjudication_spec.md § Probability estimation
    """

    p_joint: float
    expected_collisions: float
    feature_contributions: list[FeatureContribution] = Field(default_factory=list)


class Inconsistency(BaseModel):
    """A detected inconsistency between two reports.

    See: docs/duplicate_adjudication_spec.md § Inconsistency Detection
    """

    feature: str
    severity: InconsistencySeverity
    difference: float | None = None
    units: str | None = None
    description: str | None = None


class InconsistencyCounts(BaseModel):
    """Summary counts of inconsistencies by severity level.

    These counts are merged across all inconsistency sources:
    demographic (age, weight, height, sex), drug-set, clinical-date,
    and narrative-derived inconsistencies.  They feed directly into
    the weighted inconsistency score w_tot in the decision logic.

    Decisive inconsistencies (e.g. distinct patient IDs from the
    same study, or clinical-date impossibilities) override all other
    evidence and force a Different individuals classification.
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

    # Narrative assessment
    narrative_consistency: NarrativeConsistency

    # Optional diagnostic information
    evidence_summary: EvidenceSummary | None = None
