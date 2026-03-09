"""Bridge between the new pipeline output and the eval framework.

The original evaluate() in comparison.py expects the legacy pipeline
interface (dicts with same_patient_json, ae_json, clustering_json).
This module provides:

1. to_eval_dict()       — converts a PairResult to the legacy dict format
2. evaluate_batch()     — accepts BatchResult + annotations directly
3. evaluate_results()   — accepts a list of PairResult + annotations

The new pipeline does not produce same_patient_json, ae_json, or
clustering_json separately — it produces a single ClassificationResult.
For eval purposes:
- final_label is taken from ClassificationResult.final_label
- same_patient is derived from PrimaryClassification
- ae_consistency and cluster_assessment are not produced by the new
  pipeline and default to sentinel values that won't match annotations
  (this is expected — those per-field metrics will show 0% accuracy
  until the narrative analysis module is implemented)

See: docs/same_individual_assessment_spec.md § Decision logic
"""

from __future__ import annotations

from typing import Any, Sequence

from llm_dup_dev.eval.comparison import evaluate
from llm_dup_dev.eval.metrics import EvalSummary
from llm_dup_dev.models.enums import PrimaryClassification
from llm_dup_dev.pipeline.runner import BatchResult, PairResult


def to_eval_dict(result: PairResult) -> dict[str, Any]:
    """Convert a PairResult to the legacy eval dict format.

    The legacy format expects:
        - pair_id
        - same_patient_json: {"same_individual_assessment": "same"|"different"|"unclear"}
        - ae_json: {"ae_consistency": ...}
        - clustering_json: {"cluster_assessment": ...}
        - final_label: "Duplicates"|"Otherwise_related"|"Unrelated"

    Mapping from new pipeline:
        Same individual → "same"
        Different individuals → "different"
        Abstain → "unclear"
    """
    primary = result.classification.primary

    if primary == PrimaryClassification.SAME_INDIVIDUAL:
        same_patient = "same"
    elif primary == PrimaryClassification.DIFFERENT_INDIVIDUALS:
        same_patient = "different"
    else:
        same_patient = "unclear"

    return {
        "pair_id": result.pair_id,
        "same_patient_json": {
            "same_individual_assessment": same_patient,
        },
        # Not produced by the new pipeline — sentinel values
        "ae_json": {
            "ae_consistency": "_not_assessed",
        },
        "clustering_json": {
            "cluster_assessment": "_not_assessed",
        },
        "final_label": result.final_label,
    }


def evaluate_results(
    results: Sequence[PairResult],
    eval_annotations: list[dict[str, Any]],
    experiment_id: str = "unnamed",
    config_snapshot: dict | None = None,
) -> EvalSummary:
    """Evaluate a list of PairResult objects against human annotations.

    Convenience wrapper that converts PairResult objects to the legacy
    format and delegates to the existing evaluate() function.

    Parameters
    ----------
    results : Sequence[PairResult]
        Pipeline output from run_pair() or run_batch().
    eval_annotations : list of dict
        Human annotations in the standard eval format.
    experiment_id : str
        Label for this experiment run.
    config_snapshot : dict, optional
        Frozen experiment configuration.

    Returns
    -------
    EvalSummary
    """
    pipeline_dicts = [to_eval_dict(r) for r in results]
    return evaluate(
        pipeline_results=pipeline_dicts,
        eval_annotations=eval_annotations,
        experiment_id=experiment_id,
        config_snapshot=config_snapshot,
    )


def evaluate_batch(
    batch: BatchResult,
    eval_annotations: list[dict[str, Any]],
    experiment_id: str = "unnamed",
    config_snapshot: dict | None = None,
) -> EvalSummary:
    """Evaluate a BatchResult against human annotations.

    Parameters
    ----------
    batch : BatchResult
        Output from PipelineRunner.run_batch().
    eval_annotations : list of dict
        Human annotations in the standard eval format.
    experiment_id : str
        Label for this experiment run.
    config_snapshot : dict, optional
        Frozen experiment configuration.

    Returns
    -------
    EvalSummary
    """
    return evaluate_results(
        results=batch.results,
        eval_annotations=eval_annotations,
        experiment_id=experiment_id,
        config_snapshot=config_snapshot,
    )
