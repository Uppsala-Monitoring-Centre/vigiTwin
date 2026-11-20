"""
Compare pipeline outputs against eval set annotations.

This module is the main entry point called from the secure repo.
It accepts pipeline results and human annotations (as lists of dicts),
handles abstain cases, and produces an EvalSummary with aggregate metrics.

Annotations use a single ``duplicates_assessment`` field with values:
    duplicates, otherwise_related, unrelated, abstain

Pipeline results provide a ``final_label`` with values:
    Duplicates, Otherwise_related, Unrelated, Abstain

Label normalisation happens internally so that both sides are compared
in lower-case form.

Abstain handling
----------------
Two kinds of abstain must be distinguished:

* *Annotation abstains* — human annotators marked a pair as Abstain
  because the true label is ambiguous or unknown. These are excluded from
  scoring in all modes (they provide no ground truth to compare against).

* *Pipeline abstains* — the pipeline returned Abstain for an
  epistemically underdetermined same-source scenario. How these are
  treated is controlled by the ``abstain_mode`` argument to
  ``evaluate()``; see :class:`~.labels.AbstainMode` for the three modes.
"""

from __future__ import annotations

from typing import Any

from .labels import FINAL_LABELS, AbstainMode, is_abstain, normalise_label
from .metrics import (
    BinaryMetrics,
    EvalSummary,
    binary_metrics,
    confusion_matrix,
)

# Label used for the collapsed negative class in the primary binary view.
_NON_DUPLICATES_LABEL = "Non_duplicates"


def _collapse_to_duplicates_vs_nonduplicates(label: str) -> str:
    """Map a 3-class label to the 2-class primary view vocabulary."""
    return "Duplicates" if label == "Duplicates" else _NON_DUPLICATES_LABEL


def _nonduplicates_vs_duplicates(
    predicted: list[str], actual: list[str]
) -> BinaryMetrics:
    """Primary binary view: Non_duplicates as positive class.

    Non_duplicates (the union of Otherwise_related and Unrelated) is
    the positive class — detecting non-duplicates is the task of
    interest, so precision / recall / specificity are anchored on
    that class. Duplicates is the negative class.
    """
    pred_binary = [_collapse_to_duplicates_vs_nonduplicates(p) for p in predicted]
    actual_binary = [_collapse_to_duplicates_vs_nonduplicates(a) for a in actual]
    return binary_metrics(
        pred_binary,
        actual_binary,
        positive_label=_NON_DUPLICATES_LABEL,
        negative_label="Duplicates",
    )


def _duplicates_vs_nonduplicates(
    predicted: list[str], actual: list[str]
) -> BinaryMetrics:
    """Complement binary view: Duplicates as positive class.

    Same 2x2 partition as the primary view — Otherwise_related and
    Unrelated both collapse to Non_duplicates — but with the polarity
    flipped. Recall here is the fraction of actual duplicates the
    pipeline correctly flags.
    """
    pred_binary = [_collapse_to_duplicates_vs_nonduplicates(p) for p in predicted]
    actual_binary = [_collapse_to_duplicates_vs_nonduplicates(a) for a in actual]
    return binary_metrics(
        pred_binary,
        actual_binary,
        positive_label="Duplicates",
        negative_label=_NON_DUPLICATES_LABEL,
    )




def evaluate(
    pipeline_results: list[dict[str, Any]],
    eval_annotations: list[dict[str, Any]],
    experiment_id: str = "unnamed",
    config_snapshot: dict | None = None,
    abstain_mode: AbstainMode = AbstainMode.DECIDED_ONLY,
) -> EvalSummary:
    """
    Compare pipeline results against human annotations and compute metrics.

    Parameters
    ----------
    pipeline_results : list of dict
        Each dict must contain:
        - "pair_id"     : identifier matching the eval set
        - "final_label" : "Duplicates" | "Otherwise_related" | "Unrelated"
                          | "Abstain"

    eval_annotations : list of dict
        Each dict must contain:
        - "pair_id"               : identifier
        - "duplicates_assessment" : "duplicates" | "otherwise_related"
                                    | "unrelated" | "abstain"

    experiment_id : str
        Label for this experiment run (used in reporting).

    config_snapshot : dict, optional
        Frozen experiment configuration for reproducibility.

    abstain_mode : AbstainMode
        Controls how pipeline-produced Abstain results are handled.
        See :class:`~.labels.AbstainMode` for details.

        - ``DECIDED_ONLY`` (default): pairs where the pipeline abstained
          are excluded from scoring. ``n_pipeline_abstained`` is reported
          but not counted in ``n_pairs``.
        - ``ABSTAIN_AS_DUPLICATE``: pipeline abstains are remapped to
          ``Duplicates`` before scoring.
        - ``ABSTAIN_AS_NONDUPLICATE``: pipeline abstains are remapped to
          ``Otherwise_related`` before scoring.

    Returns
    -------
    EvalSummary
        Aggregated metrics: a 3x3 reference confusion matrix plus two
        binary views (duplicates-vs-nonduplicates and
        duplicates-vs-unrelated). See :mod:`.metrics` for details.
    """
    # Index eval annotations by pair_id
    eval_by_id = {a["pair_id"]: a for a in eval_annotations}

    # Aligned lists for scoring
    pred_final: list[str] = []
    actual_final: list[str] = []

    n_abstained = 0          # annotation abstains (always excluded)
    n_pipeline_abstained = 0  # pipeline abstains (mode-dependent handling)

    for result in pipeline_results:
        pid = result["pair_id"]
        ann = eval_by_id.get(pid)
        if ann is None:
            continue

        assessment = ann["duplicates_assessment"]

        # Annotation abstains are always excluded — they provide no ground
        # truth to compare against.
        if is_abstain(assessment):
            n_abstained += 1
            continue

        pipeline_label = result["final_label"]

        if is_abstain(pipeline_label):
            n_pipeline_abstained += 1
            if abstain_mode == AbstainMode.DECIDED_ONLY:
                continue  # exclude from scoring
            elif abstain_mode == AbstainMode.ABSTAIN_AS_DUPLICATE:
                pipeline_label = "Duplicates"
            else:  # ABSTAIN_AS_NONDUPLICATE
                pipeline_label = "Otherwise_related"

        # Normalise both labels to the canonical FINAL_LABELS vocabulary
        pred_final.append(normalise_label(pipeline_label))
        actual_final.append(normalise_label(assessment))

    cm = confusion_matrix(pred_final, actual_final, FINAL_LABELS)
    bin_primary = _nonduplicates_vs_duplicates(pred_final, actual_final)
    bin_complement = _duplicates_vs_nonduplicates(pred_final, actual_final)

    return EvalSummary(
        experiment_id=experiment_id,
        n_pairs=len(pred_final),
        n_abstained=n_abstained,
        n_pipeline_abstained=n_pipeline_abstained,
        final_confusion=cm,
        nonduplicates_vs_duplicates=bin_primary,
        duplicates_vs_nonduplicates=bin_complement,
        config_snapshot=config_snapshot,
    )
