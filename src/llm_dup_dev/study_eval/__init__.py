"""Fit-for-purpose evaluation package for the duplicate-adjudication study.

This package implements the deterministic and administrative parts of the
performance-evaluation study described in
``docs/performance_evaluation_description.md``:

1. validation of canonical pair-level evaluation inputs (:mod:`.schemas`);
2. protocol-defined aggregate precision and recall (:mod:`.aggregate`,
   :mod:`.stats`);
3. deterministic SCLE sampling (:mod:`.sampling`);
4. reviewer workbooks and static reviewer packets (:mod:`.scle`);
5. validation and comparison of reviewer submissions (:mod:`.scle.discrepancy`);
6. arbitration workbook creation (:mod:`.scle.arbitration`);
7. consensus merge preserving originals (:mod:`.scle.consensus`);
8. final result tables and summary artifacts (:mod:`.results`);
9. provenance and reproducibility metadata (:mod:`.manifest`, :mod:`.checksums`).

It is developed entirely against synthetic data in this repo; a thin secure-side
adapter maps real reference sets and frozen method outputs into the generic
schemas here and invokes it. See ``docs/study_eval.md`` for the workflow and the
secure-side integration contract.
"""

from __future__ import annotations

from llm_dup_dev.study_eval.aggregate import (
    AnalysisVariant,
    PrecisionResult,
    RecallResult,
    run_precision_analysis,
    run_precision_variants,
    run_recall_analysis,
    run_recall_variants,
)
from llm_dup_dev.study_eval.records import SamplingRecord
from llm_dup_dev.study_eval.sampling import (
    SCLECase,
    SCLESample,
    sample_one_per_cluster,
    sample_scle_precision,
    sample_scle_recall,
)
from llm_dup_dev.study_eval.schemas import (
    AdjudicatorOutput,
    MethodOutputs,
    PrecisionAnalysisInput,
    PrecisionEvaluationSet,
    PrecisionPairRecord,
    RecallAnalysisInput,
    RecallEvaluationSet,
    RecallPairRecord,
)
from llm_dup_dev.study_eval.stats import EstimateWithCI, wilson_interval

__all__ = [
    # schemas
    "PrecisionPairRecord",
    "PrecisionEvaluationSet",
    "RecallPairRecord",
    "RecallEvaluationSet",
    "AdjudicatorOutput",
    "MethodOutputs",
    "PrecisionAnalysisInput",
    "RecallAnalysisInput",
    # aggregate + stats
    "AnalysisVariant",
    "PrecisionResult",
    "RecallResult",
    "run_precision_analysis",
    "run_precision_variants",
    "run_recall_analysis",
    "run_recall_variants",
    "EstimateWithCI",
    "wilson_interval",
    # sampling
    "SamplingRecord",
    "SCLECase",
    "SCLESample",
    "sample_one_per_cluster",
    "sample_scle_precision",
    "sample_scle_recall",
]
