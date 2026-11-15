"""The evidence review format: what vigiTwin concluded, and on what basis.

A second review format beside the existing detailed one. Where the detailed
review colours a value by comparing it with its counterpart, this one colours
a value only when the stored assessment named it — green for matching
evidence or the anchor pattern, red for a retained inconsistency, neutral for
everything else, including agreements that were never scored.

Entry points::

    from llm_dup_dev.review_html.evidence import build_evidence_case
    from llm_dup_dev.review_html.evidence import build_assessment_summary

CLI::

    python -m llm_dup_dev.review_html --format evidence ...      # a saved run
    python -m llm_dup_dev.review_html.evidence --manifest ...    # a collection
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
from .models import (
    Attribution,
    AttributionKind,
    EvidenceCaseVM,
    EvidenceCell,
    EvidenceGap,
    EvidenceGapCode,
    EvidencePageVM,
    EvidenceRole,
    EvidenceSource,
    ProvenanceVM,
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
    "CuratedExample",
    "CuratedExampleManifest",
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
    "ProvenanceVM",
    "ReferenceInfo",
    "ReviewStatus",
    "SCLEAssessment",
    "SCLEAssessmentEntry",
    "SCLEState",
    "ScreeningVerdict",
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
    "screening_verdict",
    "write_curated_manifest",
    "write_summary_drafts",
]
