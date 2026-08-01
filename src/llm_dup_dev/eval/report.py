"""
Format EvalSummary and SweepResult objects into human-readable text or markdown.
"""

from __future__ import annotations

from .metrics import BinaryMetrics, ConfusionMatrix, EvalSummary
from .sweep import Coverage, SweepResult


# All numeric metrics are quoted to two significant figures. Using Python's
# ``g`` format lets values like 0.046 retain their leading zeros while still
# collapsing 0.97222... to 0.97 and 1.0 to 1.
def _fmt(value: float) -> str:
    return f"{value:.2g}"


def summary_to_text(summary: EvalSummary) -> str:
    """
    Plain-text summary suitable for terminal output or log files.
    """
    abstain_detail = f"annotation abstains: {summary.n_abstained}"
    if summary.n_pipeline_abstained:
        abstain_detail += f", pipeline abstains: {summary.n_pipeline_abstained}"
    lines = [
        f"=== Experiment: {summary.experiment_id} ===",
        f"Pairs evaluated: {summary.n_pairs}  ({abstain_detail})",
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
    abstain_detail = f"annotation abstains: {summary.n_abstained}"
    if summary.n_pipeline_abstained:
        abstain_detail += f", pipeline abstains: {summary.n_pipeline_abstained}"
    lines = [
        f"## Experiment: {summary.experiment_id}",
        "",
        f"**Pairs evaluated:** {summary.n_pairs} ({abstain_detail})",
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


# ===================================================================
# Threshold sweeps
# ===================================================================

# Sweep points are compared against each other, not read in isolation, so the
# two-significant-figure ``_fmt`` above is too coarse — it would collapse
# adjacent points onto the same displayed value and hide the very differences
# the sweep exists to show.
def _fmt_sweep(value: float) -> str:
    return f"{value:.4f}"


def _coverage_lines(coverage: Coverage) -> list[str]:
    """Render coverage, leading with anything that undermines the numbers."""
    lines = [
        f"Coverage: {coverage.n_matched} of {coverage.n_annotations} "
        f"annotations matched {coverage.n_features} stored features",
    ]
    if coverage.unmatched_annotations:
        lines.append(
            f"  WARNING: {len(coverage.unmatched_annotations)} annotation(s) "
            f"have no stored features and are NOT scored. "
            f"e.g. {coverage.unmatched_annotations[0]}"
        )
    if coverage.unannotated_features:
        lines.append(
            f"  Note: {len(coverage.unannotated_features)} stored pair(s) have "
            f"no annotation (unscored, not wrong)."
        )
    return lines


def sweep_to_text(result: SweepResult) -> str:
    """Plain-text table of a threshold sweep.

    Reports ``n_pairs`` beside ``abstained`` on every row deliberately: under
    ``DECIDED_ONLY`` an abstained pair leaves the denominator, so a row can post
    a better F1 purely by refusing the pairs it would have failed. Reading the
    two columns together is what stops that being mistaken for an improvement.
    No row is marked "best" — see :func:`llm_dup_dev.eval.sweep.sweep`.
    """
    lines = [
        "=== Threshold sweep ===",
        f"Abstain mode: {result.abstain_mode.value}",
    ]
    if result.coverage is not None:
        lines += _coverage_lines(result.coverage)
    lines.append("")

    if not result.points:
        lines.append("No valid threshold settings were scored.")
    else:
        header = f"{'setting':<44} {'n':>5} {'abst':>5} {'prec':>7} {'rec':>7} {'F1':>7}"
        lines += [header, "-" * len(header)]
        for p in result.points:
            m = p.summary.duplicates_vs_nonduplicates
            lines.append(
                f"{p.summary.experiment_id:<44} {p.n_pairs:>5} {p.n_abstained:>5} "
                f"{_fmt_sweep(m.precision):>7} {_fmt_sweep(m.recall):>7} "
                f"{_fmt_sweep(m.f1):>7}"
            )
        lines.append("")
        lines.append("Positive class: Duplicates. No row is selected as best:")
        lines.append("F1 is not comparable across rows whose 'n' differs.")

    if result.skipped:
        lines.append("")
        lines.append(f"Skipped {len(result.skipped)} invalid setting(s):")
        for raw, reason in result.skipped:
            lines.append(f"  {reason}")
    return "\n".join(lines)


def sweep_to_markdown(result: SweepResult) -> str:
    """Markdown table of a threshold sweep. See :func:`sweep_to_text`."""
    lines = [
        "## Threshold sweep",
        "",
        f"**Abstain mode:** `{result.abstain_mode.value}`",
        "",
    ]
    if result.coverage is not None:
        lines += [f"- {line.strip()}" for line in _coverage_lines(result.coverage)]
        lines.append("")

    if not result.points:
        lines.append("_No valid threshold settings were scored._")
    else:
        lines += [
            "| Setting | n | Abstained | Precision | Recall | F1 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for p in result.points:
            m = p.summary.duplicates_vs_nonduplicates
            lines.append(
                f"| `{p.summary.experiment_id}` | {p.n_pairs} | {p.n_abstained} "
                f"| {_fmt_sweep(m.precision)} | {_fmt_sweep(m.recall)} "
                f"| {_fmt_sweep(m.f1)} |"
            )
        lines += [
            "",
            "Positive class: `Duplicates`. No row is marked best — F1 is not "
            "comparable across rows whose `n` differs, because abstained pairs "
            "leave the denominator.",
        ]

    if result.skipped:
        lines += ["", f"**Skipped {len(result.skipped)} invalid setting(s):**", ""]
        lines += [f"- {reason}" for _, reason in result.skipped]
    return "\n".join(lines)
