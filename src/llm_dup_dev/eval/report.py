"""
Format EvalSummary objects into human-readable text or markdown.
"""

from __future__ import annotations

from .metrics import BinaryMetrics, ConfusionMatrix, EvalSummary


# All numeric metrics are quoted to two significant figures. Using Python's
# ``g`` format lets values like 0.046 retain their leading zeros while still
# collapsing 0.97222... to 0.97 and 1.0 to 1.
def _fmt(value: float) -> str:
    return f"{value:.2g}"


def summary_to_text(summary: EvalSummary) -> str:
    """
    Plain-text summary suitable for terminal output or log files.
    """
    lines = [
        f"=== Experiment: {summary.experiment_id} ===",
        f"Pairs evaluated: {summary.n_pairs}  (abstained: {summary.n_abstained})",
        "",
        "--- Reference: 3x3 Confusion ---",
        _format_confusion_text(summary.final_confusion),
        "",
        "--- Primary: detecting Non-duplicates ---",
        "  Positive class: Non_duplicates "
        "(Otherwise_related + Unrelated collapsed)",
        *_format_binary_text(summary.nonduplicates_vs_duplicates),
        "",
        "--- Complement: detecting Duplicates ---",
        "  Positive class: Duplicates (same 2x2 partition, polarity flipped)",
        *_format_binary_text(summary.duplicates_vs_nonduplicates),
    ]
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
        "### Reference: 3x3 Confusion",
        "",
        _format_confusion_markdown(summary.final_confusion),
        "",
        "### Primary: detecting Non-duplicates",
        "",
        "*Positive class: Non_duplicates "
        "(Otherwise_related + Unrelated collapsed).*",
        "",
        *_format_binary_markdown(summary.nonduplicates_vs_duplicates),
        "",
        "### Complement: detecting Duplicates",
        "",
        "*Positive class: Duplicates. Same 2x2 partition, polarity flipped.*",
        "",
        *_format_binary_markdown(summary.duplicates_vs_nonduplicates),
    ]
    return "\n".join(lines)


# ======================== binary view helpers ========================


def _format_binary_text(bm: BinaryMetrics) -> list[str]:
    """Render a BinaryMetrics as plain-text lines."""
    return [
        f"  Pairs in this view: {bm.n_pairs}",
        f"  Precision:       {_fmt(bm.precision)}",
        f"  Recall:          {_fmt(bm.recall)}",
        f"  F1:              {_fmt(bm.f1)}",
        f"  Specificity:     {_fmt(bm.specificity)}",
        f"  False positives: {bm.false_positives}",
        f"  False negatives: {bm.false_negatives}",
        "",
        _format_confusion_text(bm.confusion),
    ]


def _format_binary_markdown(bm: BinaryMetrics) -> list[str]:
    """Render a BinaryMetrics as markdown lines."""
    return [
        f"**Pairs in this view:** {bm.n_pairs}",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Precision | {_fmt(bm.precision)} |",
        f"| Recall | {_fmt(bm.recall)} |",
        f"| F1 | {_fmt(bm.f1)} |",
        f"| Specificity | {_fmt(bm.specificity)} |",
        f"| False positives | {bm.false_positives} |",
        f"| False negatives | {bm.false_negatives} |",
        "",
        _format_confusion_markdown(bm.confusion),
    ]


# ======================== confusion matrix helpers ========================


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
