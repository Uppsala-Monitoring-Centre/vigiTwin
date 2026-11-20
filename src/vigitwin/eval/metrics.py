"""
Scoring primitives for evaluating pipeline output against human annotations.

All functions operate on aligned sequences of labels (post-filtering).
Label normalization / Abstain filtering happens upstream in comparison.py.

Reporting philosophy
--------------------
Evaluation centres on detecting non-duplicates as a binary problem.
Overall accuracy over the 3-class label set is not reported: it is
dominated by the majority class and hides the precision / recall
trade-off we actually care about.

The summary therefore exposes:

1. ``final_confusion`` — the raw 3x3 confusion matrix (Duplicates /
   Otherwise_related / Unrelated) as a reference.
2. ``nonduplicates_vs_duplicates`` — primary binary view with
   Otherwise_related and Unrelated collapsed into a single
   ``Non_duplicates`` class. Positive class is ``Non_duplicates``;
   precision / recall / F1 / specificity / FP / FN are anchored on
   non-duplicate detection, so recall is the proportion of actual
   non-duplicates the pipeline correctly classifies as such.
3. ``duplicates_vs_nonduplicates`` — complementary binary view over
   the same 2x2 partition (no pairs filtered) with the polarity
   flipped: positive class is ``Duplicates``. Recall is the
   proportion of actual duplicates caught. Useful for monitoring
   the duplicate-side precision/recall trade-off that the primary
   view's specificity only reports indirectly.
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
        """Overall accuracy (fraction of correct predictions).

        Retained as a primitive for callers that want it; not surfaced
        in :class:`EvalSummary` because it is not informative for an
        imbalanced 3-class duplicate-detection task.
        """
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
    """Build a confusion matrix from two aligned sequences of labels.

    Pairs whose predicted or actual label is not in ``labels`` are
    silently ignored (filtering is the caller's responsibility).
    """
    idx_map = {label: i for i, label in enumerate(labels)}
    n = len(labels)
    mat = [[0] * n for _ in range(n)]
    for p, a in zip(predicted, actual, strict=True):
        if p in idx_map and a in idx_map:
            mat[idx_map[p]][idx_map[a]] += 1
    return ConfusionMatrix(labels=labels, matrix=mat)


# ======================== binary metrics ========================


@dataclass
class BinaryMetrics:
    """Precision / recall / F1 / specificity / raw cell counts for a 2-class view.

    Attributes
    ----------
    positive_label, negative_label
        The class names used for the positive and negative classes in
        the underlying 2x2 confusion matrix. Convention: rows =
        predicted, columns = actual, with position 0 = positive.
    confusion
        The 2x2 :class:`ConfusionMatrix` whose ``labels`` are
        ``(positive_label, negative_label)``.
    n_pairs
        Number of pairs contributing to this binary view (sum of the
        confusion matrix). May differ from the overall pair count when
        the caller filters out pairs outside the 2-class vocabulary.
    true_positives, false_positives, false_negatives, true_negatives
        Raw cell counts, exposed explicitly so callers don't have to
        index into ``confusion.matrix``.
    precision, recall, f1, specificity
        Standard definitions. ``specificity = TN / (TN + FP)``.
    """

    positive_label: str
    negative_label: str
    confusion: ConfusionMatrix
    n_pairs: int
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    precision: float
    recall: float
    f1: float
    specificity: float


def binary_metrics(
    predicted: Sequence[str],
    actual: Sequence[str],
    positive_label: str,
    negative_label: str,
) -> BinaryMetrics:
    """Compute binary metrics from aligned label sequences.

    Pairs whose predicted or actual label is neither the positive nor
    the negative class are silently ignored. Precision / recall / F1 /
    specificity are computed from the resulting 2x2 confusion matrix.
    """
    cm = confusion_matrix(predicted, actual, (positive_label, negative_label))
    tp = cm.matrix[0][0]
    fp = cm.matrix[0][1]
    fn = cm.matrix[1][0]
    tn = cm.matrix[1][1]
    n = tp + fp + fn + tn

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    specificity = tn / (tn + fp) if (tn + fp) > 0 else 0.0

    return BinaryMetrics(
        positive_label=positive_label,
        negative_label=negative_label,
        confusion=cm,
        n_pairs=n,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        true_negatives=tn,
        precision=precision,
        recall=recall,
        f1=f1,
        specificity=specificity,
    )


# ======================== summary dataclass ========================


@dataclass
class EvalSummary:
    """Aggregated evaluation results for one experiment run.

    See the module docstring for the rationale behind the binary views.

    Abstain accounting
    ------------------
    Two abstain counters are reported:

    * ``n_abstained`` — pairs excluded because the *human annotation* is
      Abstain (always excluded from scoring regardless of mode).
    * ``n_pipeline_abstained`` — pairs where the *pipeline* output is
      Abstain. How these are handled depends on the ``AbstainMode``
      passed to ``evaluate()``:

      - ``DECIDED_ONLY``: excluded from scoring (not counted in
        ``n_pairs``).
      - ``ABSTAIN_AS_DUPLICATE``: remapped to ``Duplicates`` and scored.
      - ``ABSTAIN_AS_NONDUPLICATE``: remapped to ``Otherwise_related``
        and scored.

    The abstention rate is ``n_pipeline_abstained / (n_pairs +
    n_pipeline_abstained)`` in DECIDED_ONLY mode, or simply
    ``n_pipeline_abstained / n_pairs`` in the remapping modes.
    Stratification by same-source status requires the full
    ``PairFeatures`` objects (available to callers via the pipeline
    runner) and is outside the scope of this summary.
    """

    experiment_id: str
    n_pairs: int
    n_abstained: int

    # Reference: raw 3x3 confusion over FINAL_LABELS.
    final_confusion: ConfusionMatrix

    # Primary view: same 2x2 partition (Non_duplicates = Otherwise_related +
    # Unrelated collapsed, vs Duplicates) with Non_duplicates as the positive
    # class — recall = fraction of actual non-duplicates caught.
    nonduplicates_vs_duplicates: BinaryMetrics

    # Complement view: same partition with the polarity flipped —
    # Duplicates as the positive class. Recall = fraction of actual
    # duplicates caught.
    duplicates_vs_nonduplicates: BinaryMetrics

    # Pipeline abstains: pairs where the pipeline output was Abstain.
    # Handling depends on the AbstainMode passed to evaluate().
    n_pipeline_abstained: int = 0

    # Metadata (frozen experiment config for reproducibility)
    config_snapshot: Optional[dict] = None
