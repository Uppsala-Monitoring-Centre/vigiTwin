"""
Scoring primitives for evaluating pipeline output against human annotations.

All functions operate on aligned sequences of labels (post-filtering).
Label normalization / Abstain filtering happens upstream in comparison.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


# ======================== confusion matrix ========================


@dataclass
class ConfusionMatrix:
    """
    Confusion matrix where rows = predicted, columns = actual (ground truth).

    Access raw counts via `matrix[pred_idx][actual_idx]`.
    """

    labels: tuple[str, ...]
    matrix: list[list[int]]

    def accuracy(self) -> float:
        """Overall accuracy (fraction of correct predictions)."""
        correct = sum(self.matrix[i][i] for i in range(len(self.labels)))
        total = sum(sum(row) for row in self.matrix)
        return correct / total if total > 0 else 0.0

    def precision_recall_f1(self, label: str) -> dict[str, float]:
        """Per-class precision, recall, and F1 score."""
        idx = self.labels.index(label)
        tp = self.matrix[idx][idx]
        fp = sum(self.matrix[idx]) - tp
        fn = sum(row[idx] for row in self.matrix) - tp
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )
        return {"precision": precision, "recall": recall, "f1": f1}


def confusion_matrix(
    predicted: Sequence[str],
    actual: Sequence[str],
    labels: tuple[str, ...],
) -> ConfusionMatrix:
    """Build a confusion matrix from two aligned sequences of labels."""
    idx_map = {label: i for i, label in enumerate(labels)}
    n = len(labels)
    mat = [[0] * n for _ in range(n)]
    for p, a in zip(predicted, actual):
        if p in idx_map and a in idx_map:
            mat[idx_map[p]][idx_map[a]] += 1
    return ConfusionMatrix(labels=labels, matrix=mat)


# ======================== summary dataclass ========================


@dataclass
class EvalSummary:
    """Aggregated evaluation results for one experiment run."""

    experiment_id: str
    n_pairs: int
    n_abstained: int

    # Final classification metrics
    final_accuracy: float
    final_confusion: ConfusionMatrix
    final_per_class: dict[str, dict[str, float]]

    # Metadata (frozen experiment config for reproducibility)
    config_snapshot: Optional[dict] = None
