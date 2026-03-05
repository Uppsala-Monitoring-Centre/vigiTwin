"""
Format EvalSummary objects into human-readable text or markdown.
"""

from __future__ import annotations

from .labels import FINAL_LABELS
from .metrics import ConfusionMatrix, EvalSummary


def summary_to_text(summary: EvalSummary) -> str:
    """
    Plain-text summary suitable for terminal output or log files.

    Example output::

        === Experiment: baseline_v1 ===
        Pairs evaluated: 138  (abstained: 12)

        --- Final Classification ---
          Accuracy:                    0.823
          Certainty-weighted accuracy: 0.841
          Duplicates            P=0.880  R=0.850  F1=0.865
          Otherwise_related     P=0.720  R=0.690  F1=0.705
          Unrelated             P=0.810  R=0.840  F1=0.825

        --- Per-Field Accuracy ---
          Same patient:     0.890
          AE consistency:   0.820
          Cluster:          0.910
    """
    lines = [
        f"=== Experiment: {summary.experiment_id} ===",
        f"Pairs evaluated: {summary.n_pairs}  (abstained: {summary.n_abstained})",
        "",
        "--- Final Classification ---",
        f"  Accuracy:                    {summary.final_accuracy:.3f}",
        f"  Certainty-weighted accuracy: {summary.final_certainty_weighted_accuracy:.3f}",
    ]

    for label in FINAL_LABELS:
        m = summary.final_per_class.get(label, {})
        lines.append(
            f"  {label:22s}  P={m.get('precision', 0):.3f}"
            f"  R={m.get('recall', 0):.3f}"
            f"  F1={m.get('f1', 0):.3f}"
        )

    lines += [
        "",
        "--- Per-Field Accuracy ---",
        f"  Same patient:     {summary.same_patient_accuracy:.3f}",
        f"  AE consistency:   {summary.ae_consistency_accuracy:.3f}",
        f"  Cluster:          {summary.cluster_accuracy:.3f}",
    ]

    lines += ["", _format_confusion_text(summary.final_confusion)]
    return "\n".join(lines)


def summary_to_markdown(summary: EvalSummary) -> str:
    """
    Markdown-formatted summary suitable for notebooks or documentation.
    """
    lines = [
        f"## Experiment: {summary.experiment_id}",
        "",
        f"**Pairs evaluated:** {summary.n_pairs} (abstained: {summary.n_abstained})",
        "",
        "### Final Classification",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Accuracy | {summary.final_accuracy:.3f} |",
        f"| Certainty-weighted accuracy | {summary.final_certainty_weighted_accuracy:.3f} |",
        "",
        "| Label | Precision | Recall | F1 |",
        "|-------|-----------|--------|----|",
    ]

    for label in FINAL_LABELS:
        m = summary.final_per_class.get(label, {})
        lines.append(
            f"| {label} "
            f"| {m.get('precision', 0):.3f} "
            f"| {m.get('recall', 0):.3f} "
            f"| {m.get('f1', 0):.3f} |"
        )

    lines += [
        "",
        "### Per-Field Accuracy",
        "",
        "| Field | Accuracy |",
        "|-------|----------|",
        f"| Same patient | {summary.same_patient_accuracy:.3f} |",
        f"| AE consistency | {summary.ae_consistency_accuracy:.3f} |",
        f"| Cluster | {summary.cluster_accuracy:.3f} |",
        "",
        "### Confusion Matrix",
        "",
        _format_confusion_markdown(summary.final_confusion),
    ]
    return "\n".join(lines)


# ======================== helpers ========================


def _format_confusion_text(cm: ConfusionMatrix) -> str:
    """Format confusion matrix as an aligned text table."""
    if not cm.labels:
        return "Confusion matrix: (empty)"

    max_len = max(len(label) for label in cm.labels)
    col_width = max(max_len, 5)

    header = "Confusion (rows=predicted, cols=actual):"
    col_header = " " * (col_width + 2) + "  ".join(
        f"{label:>{col_width}}" for label in cm.labels
    )
    rows = []
    for i, label in enumerate(cm.labels):
        vals = "  ".join(f"{v:>{col_width}}" for v in cm.matrix[i])
        rows.append(f"  {label:>{col_width}}  {vals}")
    return "\n".join([header, col_header] + rows)


def _format_confusion_markdown(cm: ConfusionMatrix) -> str:
    """Format confusion matrix as a markdown table."""
    if not cm.labels:
        return "*Empty confusion matrix*"

    header = "| Predicted \\ Actual | " + " | ".join(cm.labels) + " |"
    separator = "|" + "|".join(["---"] * (len(cm.labels) + 1)) + "|"
    rows = []
    for i, label in enumerate(cm.labels):
        vals = " | ".join(str(v) for v in cm.matrix[i])
        rows.append(f"| **{label}** | {vals} |")
    return "\n".join([header, separator] + rows)
