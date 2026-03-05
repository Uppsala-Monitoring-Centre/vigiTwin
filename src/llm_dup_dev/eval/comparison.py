"""
Compare pipeline outputs against eval set annotations.

This module is the main entry point called from the secure repo.
It accepts pipeline results and human annotations (as lists of dicts),
filters out Abstain cases, and produces an EvalSummary with aggregate metrics.
"""

from __future__ import annotations

from typing import Any, Optional

from .labels import FINAL_LABELS, is_abstain
from .metrics import (
    ConfusionMatrix,
    EvalSummary,
    certainty_weighted_accuracy,
    confusion_matrix,
    field_accuracy,
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
        - "pair_id"           : identifier matching the eval set
        - "same_patient_json" : dict with "same_individual_assessment"
        - "ae_json"           : dict with "ae_consistency"
        - "clustering_json"   : dict with "cluster_assessment"
        - "final_label"       : "Duplicates" | "Otherwise_related" | "Unrelated"

    eval_annotations : list of dict
        Each dict must contain:
        - "pair_id"            : identifier
        - "same_individual"    : "same" | "different" | "unclear"
        - "ae_consistency"     : "consistent" | "partially_consistent" | "inconsistent"
        - "cluster_assessment" : "possible_cluster" | "no_evidence_of_cluster"
        - "assessment"         : "Duplicates" | "Otherwise_related" | "Unrelated" | "Abstain"
        - "certainty"          : float (0–100)

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
    certainties: list[float] = []

    pred_sp: list[str] = []
    actual_sp: list[str] = []

    pred_ae: list[str] = []
    actual_ae: list[str] = []

    pred_cl: list[str] = []
    actual_cl: list[str] = []

    n_abstained = 0

    for result in pipeline_results:
        pid = result["pair_id"]
        ann = eval_by_id.get(pid)
        if ann is None:
            continue

        # Skip Abstain annotations
        if is_abstain(ann["assessment"]):
            n_abstained += 1
            continue

        # Final classification
        pred_final.append(result["final_label"])
        actual_final.append(ann["assessment"])
        certainties.append(ann.get("certainty", 50.0))

        # Per-field: same patient
        pred_sp.append(
            _safe_get(result, "same_patient_json", "same_individual_assessment", "unclear")
        )
        actual_sp.append(ann.get("same_individual", "unclear"))

        # Per-field: AE consistency
        pred_ae.append(
            _safe_get(result, "ae_json", "ae_consistency", "inconsistent")
        )
        actual_ae.append(ann.get("ae_consistency", "inconsistent"))

        # Per-field: cluster assessment
        pred_cl.append(
            _safe_get(result, "clustering_json", "cluster_assessment", "no_evidence_of_cluster")
        )
        actual_cl.append(ann.get("cluster_assessment", "no_evidence_of_cluster"))

    cm = confusion_matrix(pred_final, actual_final, FINAL_LABELS)

    return EvalSummary(
        experiment_id=experiment_id,
        n_pairs=len(pred_final),
        n_abstained=n_abstained,
        final_accuracy=cm.accuracy(),
        final_certainty_weighted_accuracy=certainty_weighted_accuracy(
            pred_final, actual_final, certainties
        ),
        final_confusion=cm,
        final_per_class={
            label: cm.precision_recall_f1(label) for label in FINAL_LABELS
        },
        same_patient_accuracy=field_accuracy(pred_sp, actual_sp),
        ae_consistency_accuracy=field_accuracy(pred_ae, actual_ae),
        cluster_accuracy=field_accuracy(pred_cl, actual_cl),
        config_snapshot=config_snapshot,
    )


def _safe_get(result: dict, json_key: str, field_key: str, default: str) -> str:
    """Safely extract a nested field from a pipeline result dict."""
    json_obj = result.get(json_key)
    if isinstance(json_obj, dict):
        val = json_obj.get(field_key)
        if val is not None:
            return str(val).strip()
    return default
