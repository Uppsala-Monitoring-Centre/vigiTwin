"""
Compare pipeline outputs against eval set annotations.

This module is the main entry point called from the secure repo.
It accepts pipeline results and human annotations (as lists of dicts),
filters out Abstain cases, and produces an EvalSummary with aggregate metrics.

Annotations use a single ``duplicates_assessment`` field with values:
    duplicates, otherwise_related, unrelated, abstain

Pipeline results provide a ``final_label`` with values:
    Duplicates, Otherwise_related, Unrelated, Abstain

Label normalisation happens internally so that both sides are compared
in lower-case form.
"""

from __future__ import annotations

from typing import Any

from .labels import FINAL_LABELS, is_abstain, normalise_label
from .metrics import (
    ConfusionMatrix,
    EvalSummary,
    confusion_matrix,
)


def evaluate(
    pipeline_results: list[dict[str, Any]],
    eval_annotations: list[dict[str, Any]],
    experiment_id: str = "unnamed",
    config_snapshot: dict | None = None,
) -> EvalSummary:
    """
    Compare pipeline results against human annotations and compute metrics.

    Parameters
    ----------
    pipeline_results : list of dict
        Each dict must contain:
        - "pair_id"     : identifier matching the eval set
        - "final_label" : "Duplicates" | "Otherwise_related" | "Unrelated"

    eval_annotations : list of dict
        Each dict must contain:
        - "pair_id"               : identifier
        - "duplicates_assessment" : "duplicates" | "otherwise_related"
                                    | "unrelated" | "abstain"

    experiment_id : str
        Label for this experiment run (used in reporting).

    config_snapshot : dict, optional
        Frozen experiment configuration for reproducibility.

    Returns
    -------
    EvalSummary
        Aggregated metrics (no individual case data).
    """
    # Index eval annotations by pair_id
    eval_by_id = {a["pair_id"]: a for a in eval_annotations}

    # Aligned lists for scoring
    pred_final: list[str] = []
    actual_final: list[str] = []

    n_abstained = 0

    for result in pipeline_results:
        pid = result["pair_id"]
        ann = eval_by_id.get(pid)
        if ann is None:
            continue

        assessment = ann["duplicates_assessment"]

        # Skip Abstain annotations
        if is_abstain(assessment):
            n_abstained += 1
            continue

        # Normalise both labels to the canonical FINAL_LABELS vocabulary
        pred_final.append(normalise_label(result["final_label"]))
        actual_final.append(normalise_label(assessment))

    cm = confusion_matrix(pred_final, actual_final, FINAL_LABELS)

    return EvalSummary(
        experiment_id=experiment_id,
        n_pairs=len(pred_final),
        n_abstained=n_abstained,
        final_accuracy=cm.accuracy(),
        final_confusion=cm,
        final_per_class={
            label: cm.precision_recall_f1(label) for label in FINAL_LABELS
        },
        config_snapshot=config_snapshot,
    )
