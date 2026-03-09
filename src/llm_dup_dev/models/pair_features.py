"""Data models for the PairFeatures layer.

The PairFeatures layer contains all derived information used to evaluate
whether two reports refer to the same individual. These features are
computed from the Report layer and statistical summaries of the database.

See: docs/data_contracts.md § PairFeatures Layer
See: docs/same_individual_assessment_spec.md § Evidence components
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

    See: docs/same_individual_assessment_spec.md § Reporting Context
    """

    level: ReportingContextLevel
    country: str | None = None
    year: int | None = None


class AnchorPopulation(BaseModel):
    """Anchor population statistics for probability estimation.

    Contains three candidate population sizes and the selected anchor.

    See: docs/same_individual_assessment_spec.md § Anchor Selection
    """

    N_drugs: int
    N_AEs: int
    N_drugs_AEs: int
    type: AnchorType
    N_anchor: int


class ProbabilityEstimate(BaseModel):
    """Result of the collision probability computation.

    See: docs/same_individual_assessment_spec.md § Probability estimation
    """

    p_joint: float
    expected_collisions: float


class Inconsistency(BaseModel):
    """A detected inconsistency between two reports.

    See: docs/same_individual_assessment_spec.md § Inconsistency Detection
    """

    feature: str
    severity: InconsistencySeverity
    difference: float | None = None
    units: str | None = None
    description: str | None = None


class InconsistencyCounts(BaseModel):
    """Summary counts of inconsistencies by severity level.

    Decisive inconsistencies (e.g. distinct patient IDs from the
    same study identified in the narratives) override all other
    evidence and force a Different individuals classification.
    """

    moderate: int = 0
    strong: int = 0
    decisive: int = 0


class EvidenceSummary(BaseModel):
    """Diagnostic summary of shared and report-specific evidence.

    Optional field for debugging and interpretability.
    Does not affect the decision logic.
    """

    shared: dict = Field(default_factory=dict)
    report1_only: dict = Field(default_factory=dict)
    report2_only: dict = Field(default_factory=dict)


class PairFeatures(BaseModel):
    """All derived features for evaluating whether two reports
    refer to the same individual.

    Computed from the Report layer and statistical summaries.
    Provides inputs to the decision logic.

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

    # Inconsistencies
    inconsistencies: list[Inconsistency] = Field(default_factory=list)
    inconsistency_counts: InconsistencyCounts = Field(
        default_factory=InconsistencyCounts
    )

    # Narrative assessment
    narrative_consistency: NarrativeConsistency

    # Optional diagnostic information
    evidence_summary: EvidenceSummary | None = None
