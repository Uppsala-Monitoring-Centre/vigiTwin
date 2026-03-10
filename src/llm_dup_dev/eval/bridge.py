"""Bridge between the new pipeline output and the eval framework.

The evaluate() function in comparison.py works with dicts containing
``pair_id`` and ``final_label``. This module provides:

1. to_eval_dict()       — converts a PairResult to the eval dict format
2. evaluate_batch()     — accepts BatchResult + annotations directly
3. evaluate_results()   — accepts a list of PairResult + annotations

The pipeline's final_label maps directly to the annotation vocabulary:
    Duplicates         ↔ duplicates
    Otherwise_related  ↔ otherwise_related
    Unrelated          ↔ unrelated

Label normalisation is handled by the comparison module.
"""

from __future__ import annotations

from typing import Any, Sequence

from llm_dup_dev.eval.comparison import evaluate
from llm_dup_dev.eval.metrics import EvalSummary
from llm_dup_dev.pipeline.runner import BatchResult, PairResult


def to_eval_dict(result: PairResult) -> dict[str, Any]:
    """Convert a PairResult to the eval dict format.

    Returns a dict with:
        - pair_id:     str
        - final_label: "Duplicates" | "Otherwise_related" | "Unrelated" | "Abstain"
    """
    return {
        "pair_id": result.pair_id,
        "final_label": result.final_label,
    }


def evaluate_results(
    results: Sequence[PairResult],
    eval_annotations: list[dict[str, Any]],
    experiment_id: str = "unnamed",
    config_snapshot: dict | None = None,
) -> EvalSummary:
    """Evaluate a list of PairResult objects against human annotations.

    Convenience wrapper that converts PairResult objects to the eval
    format and delegates to the existing evaluate() function.

    Parameters
    ----------
    results : Sequence[PairResult]
        Pipeline output from run_pair() or run_batch().
    eval_annotations : list of dict
        Human annotations with ``pair_id`` and ``duplicates_assessment``.
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
        Human annotations with ``pair_id`` and ``duplicates_assessment``.
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
