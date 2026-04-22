"""Data models for the duplicate adjudication pipeline.

Three conceptual layers (see docs/data_contracts.md):
- Report: structured representation of a single report
- ReportPair: candidate pair identifiers
- PairFeatures: derived features for report-pair duplicate adjudication

Plus classification results and enumeration types.
"""

from .enums import (
    AnchorType,
    DrugRole,
    DuplicateRelationship,
    IndependenceAssessment,
    InconsistencySeverity,
    NarrativeConsistency,
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
from .narrative_source_case import (
    CaseIdentifierFamily,
    EvidenceSpan,
    NarrativeSourceCase,
    SourceCaseAssessment,
    SourceCaseVerdict,
    SourceIdentifierType,
    SourceType,
)
from .report_pair import ReportPair
from .pair_features import (
    AnchorPopulation,
    ClinicalDateFindingSummary,
    ClinicalDateInconsistencyCounts,
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
    "DrugRole",
    "DuplicateRelationship",
    "IndependenceAssessment",
    "InconsistencySeverity",
    "NarrativeConsistency",
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
    # Derived narrative signals
    "CaseIdentifierFamily",
    "EvidenceSpan",
    "NarrativeSourceCase",
    "SourceCaseAssessment",
    "SourceCaseVerdict",
    "SourceIdentifierType",
    "SourceType",
    # ReportPair layer
    "ReportPair",
    # PairFeatures layer
    "AnchorPopulation",
    "ClinicalDateFindingSummary",
    "ClinicalDateInconsistencyCounts",
    "EvidenceSummary",
    "Inconsistency",
    "InconsistencyCounts",
    "PairFeatures",
    "ProbabilityEstimate",
    "ReportingContext",
    # Classification
    "ClassificationResult",
]
