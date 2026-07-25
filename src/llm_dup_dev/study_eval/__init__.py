"""Generic, reusable performance-evaluation package for method comparison.

Implements the deterministic and administrative parts of a case-level
performance-evaluation study, organised as four steps (see :mod:`.workflow`):

1. :func:`run_analysis` — validate reference sets + frozen predictions, compute
   precision/recall confusion matrices and estimates, emit a per-pair
   prediction/truth table, and draw the SCLE sample with ordered backups;
2. :func:`generate_reviewer_workbooks` — reviewer XLSX files;
3. :func:`build_arbitration` — inter-reviewer agreement + a disagreement-focused
   arbitration workbook;
4. :func:`finalize` — consensus (preserving the original reviews) + final tables.

Inputs are generic: a list of :class:`ReferenceSet` (each with a role and
provenance) and a :class:`PredictionSet` of frozen per-pair predictions. Nothing
here is specific to any one screening method or set of reference sets. The
package is developed against synthetic data in this repo; a thin secure-side
adapter builds the input models from a frozen run and invokes the steps. See
``docs/study_eval.md``.
"""

from __future__ import annotations

from llm_dup_dev.study_eval.aggregate import (
    AnalysisVariant,
    PrecisionResult,
    RecallResult,
    run_precision_analysis,
    run_recall_analysis,
)
from llm_dup_dev.study_eval.records import SamplingRecord
from llm_dup_dev.study_eval.sampling import (
    SCLECase,
    SCLESample,
    promote_backup,
    sample_scle_precision,
    sample_scle_recall,
)
from llm_dup_dev.study_eval.schemas import (
    PairPrediction,
    PredictionSet,
    ReferencePair,
    ReferenceSet,
)
from llm_dup_dev.study_eval.stats import EstimateWithCI, wilson_interval
from llm_dup_dev.study_eval.workflow import (
    AnalysisResult,
    ArbitrationBuild,
    FinalizeResult,
    build_arbitration,
    finalize,
    generate_reviewer_workbooks,
    run_analysis,
)

__all__ = [
    # schemas
    "ReferencePair",
    "ReferenceSet",
    "PairPrediction",
    "PredictionSet",
    # aggregate + stats
    "AnalysisVariant",
    "PrecisionResult",
    "RecallResult",
    "run_precision_analysis",
    "run_recall_analysis",
    "EstimateWithCI",
    "wilson_interval",
    # sampling
    "SamplingRecord",
    "SCLECase",
    "SCLESample",
    "sample_scle_precision",
    "sample_scle_recall",
    "promote_backup",
    # workflow (the four steps)
    "run_analysis",
    "generate_reviewer_workbooks",
    "build_arbitration",
    "finalize",
    "AnalysisResult",
    "ArbitrationBuild",
    "FinalizeResult",
]
