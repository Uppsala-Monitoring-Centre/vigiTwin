"""The evidence review format: what vigiTwin concluded, and on what basis.

A second review format beside the existing detailed one. Where the detailed
review colours a value by comparing it with its counterpart, this one colours
a value only when the stored assessment named it — green for matching
evidence or the anchor pattern, red for a retained inconsistency, neutral for
everything else, including agreements that were never scored.

Entry points::

    from llm_dup_dev.review_html.evidence import build_evidence_case
    from llm_dup_dev.review_html.evidence import build_assessment_summary

Publication mode lives in :mod:`.publication`: the same cases, with their
displayed values transformed and a fail-closed export. The internal review
here is unchanged by it.

CLI::

    python -m llm_dup_dev.review_html --format evidence ...      # a saved run
    python -m llm_dup_dev.review_html.evidence --manifest ...    # a collection
    python -m llm_dup_dev.review_html.evidence --mode publication ...
"""

from __future__ import annotations

from .build import (
    ExamplePresentation,
    ReferenceInfo,
    SCLEAssessment,
    SCLEState,
    build_evidence_case,
)
from .collection import (
    build_collection,
    export_summary_drafts,
    write_summary_drafts,
)
from .manifest import (
    CuratedExample,
    CuratedExampleManifest,
    ExampleDataset,
    LoadedExampleManifest,
    ReviewStatus,
    SCLEAssessmentEntry,
    SummaryMode,
    load_curated_manifest,
    write_curated_manifest,
)
from . import publication
from .models import (
    Attribution,
    DecisionPathway,
    DecisionStage,
    DecisionTileVM,
    AttributionKind,
    CaseSummaryVM,
    EvidenceCaseVM,
    EvidenceCell,
    EvidenceGap,
    EvidenceGapCode,
    EvidencePageVM,
    EvidenceRole,
    EvidenceSource,
    PresentationMode,
    ProvenanceVM,
    SourceContextVM,
    TileState,
    ValueKind,
)
from .screening import (
    SCREENING_VERDICT_KEYS,
    ScreeningVerdict,
    screening_verdict,
)
from .settings import (
    EvidenceRunSettings,
    load_evidence_run_settings,
    page_provenance,
)
from .summary import AssessmentSummary, SummarySlot, build_assessment_summary

__all__ = [
    "SCREENING_VERDICT_KEYS",
    "AssessmentSummary",
    "Attribution",
    "AttributionKind",
    "CaseSummaryVM",
    "CuratedExample",
    "CuratedExampleManifest",
    "DecisionPathway",
    "DecisionStage",
    "DecisionTileVM",
    "EvidenceCaseVM",
    "EvidenceCell",
    "EvidenceGap",
    "EvidenceGapCode",
    "EvidencePageVM",
    "EvidenceRole",
    "EvidenceRunSettings",
    "EvidenceSource",
    "ExampleDataset",
    "ExamplePresentation",
    "LoadedExampleManifest",
    "PresentationMode",
    "ProvenanceVM",
    "ReferenceInfo",
    "ReviewStatus",
    "SCLEAssessment",
    "SCLEAssessmentEntry",
    "SCLEState",
    "ScreeningVerdict",
    "SourceContextVM",
    "SummaryMode",
    "SummarySlot",
    "TileState",
    "ValueKind",
    "build_assessment_summary",
    "build_collection",
    "build_evidence_case",
    "export_summary_drafts",
    "load_curated_manifest",
    "load_evidence_run_settings",
    "page_provenance",
    "publication",
    "screening_verdict",
    "write_curated_manifest",
    "write_summary_drafts",
]
