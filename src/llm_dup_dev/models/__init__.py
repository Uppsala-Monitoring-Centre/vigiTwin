"""Data models for the same-individual assessment pipeline.

Three conceptual layers (see docs/data_contracts.md):
- Report: structured representation of a single report
- ReportPair: candidate pair identifiers
- PairFeatures: derived features for duplicate assessment

Plus classification results and enumeration types.
"""

from .enums import (
    AnchorType,
    InconsistencySeverity,
    NarrativeConsistency,
    PrimaryClassification,
    RelationshipClassification,
    ReportingContextLevel,
)
from .report import (
    AdverseEvent,
    AgeValue,
    DateValue,
    Drug,
    MeasurementValue,
    PartialDate,
    Report,
    SourcedValue,
)
from .report_pair import ReportPair
from .pair_features import (
    AnchorPopulation,
    EvidenceSummary,
    Inconsistency,
    InconsistencyCounts,
    PairFeatures,
    ProbabilityEstimate,
    ReportingContext,
)
from .classification import ClassificationResult

__all__ = [
    # Enums
    "AnchorType",
    "InconsistencySeverity",
    "NarrativeConsistency",
    "PrimaryClassification",
    "RelationshipClassification",
    "ReportingContextLevel",
    # Report layer
    "AdverseEvent",
    "AgeValue",
    "DateValue",
    "Drug",
    "MeasurementValue",
    "PartialDate",
    "Report",
    "SourcedValue",
    # ReportPair layer
    "ReportPair",
    # PairFeatures layer
    "AnchorPopulation",
    "EvidenceSummary",
    "Inconsistency",
    "InconsistencyCounts",
    "PairFeatures",
    "ProbabilityEstimate",
    "ReportingContext",
    # Classification
    "ClassificationResult",
]
