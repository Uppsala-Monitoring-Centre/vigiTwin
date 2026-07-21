"""Protocol-defined aggregate precision and recall calculations.

The positive class is expressed as an explicit rule over the four-value
adjudicator output rather than reusing :class:`llm_dup_dev.eval.labels.AbstainMode`.
Two reasons: ``AbstainMode`` cannot express the "otherwise-related counts as
positive" secondary analysis, and its ``DECIDED_ONLY`` mode *excludes*
adjudicator abstains, which contradicts the protocol's primary rule that an
abstention is a negative classification. Keeping the rule local also leaves the
existing ``eval`` package untouched.

Reference-label handling follows the protocol: pairs the human annotator marked
``abstain`` are excluded from every precision estimate (and counted). All
recall-reference pairs are positive controls, so recall estimates have no
reference-abstain exclusion.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from llm_dup_dev.study_eval.schemas import (
    PrecisionAnalysisInput,
    RecallAnalysisInput,
)
from llm_dup_dev.study_eval.stats import EstimateWithCI, estimate


class AnalysisVariant(str, Enum):
    """How the four-value adjudicator output collapses to a positive class.

    ``PRIMARY``
        Positive iff ``Duplicates``. ``Otherwise_related``, ``Unrelated`` and
        ``Abstain`` are negative — the protocol's primary rule.
    ``ABSTAIN_POSITIVE``
        Positive iff ``Duplicates`` or ``Abstain`` (secondary analysis 1).
    ``OTHERWISE_RELATED_POSITIVE``
        Positive iff ``Duplicates`` or ``Otherwise_related`` (secondary
        analysis 2).
    """

    PRIMARY = "primary"
    ABSTAIN_POSITIVE = "abstain_positive"
    OTHERWISE_RELATED_POSITIVE = "otherwise_related_positive"


#: The re-interpretation variants that run over a single frozen output bundle.
REINTERPRETATION_VARIANTS: tuple[AnalysisVariant, ...] = (
    AnalysisVariant.PRIMARY,
    AnalysisVariant.ABSTAIN_POSITIVE,
    AnalysisVariant.OTHERWISE_RELATED_POSITIVE,
)

_POSITIVE_LABELS: dict[AnalysisVariant, frozenset[str]] = {
    AnalysisVariant.PRIMARY: frozenset({"Duplicates"}),
    AnalysisVariant.ABSTAIN_POSITIVE: frozenset({"Duplicates", "Abstain"}),
    AnalysisVariant.OTHERWISE_RELATED_POSITIVE: frozenset(
        {"Duplicates", "Otherwise_related"}
    ),
}


def is_positive(final_label: str, variant: AnalysisVariant) -> bool:
    """Return whether ``final_label`` counts as a positive under ``variant``."""
    return final_label in _POSITIVE_LABELS[variant]


# --- Precision ------------------------------------------------------------


class PrecisionResult(BaseModel):
    """Precision-evaluation results for one output bundle under one variant."""

    variant: str
    set_id: str
    n_pairs: int
    n_reference_abstain: int
    n_evaluable: int
    n_reference_duplicate: int
    n_reference_non_duplicate: int
    n_adjudicator_positive: int
    n_adjudicator_abstain: int

    vigimatch_baseline_precision: EstimateWithCI
    adjudicator_precision: EstimateWithCI
    conditional_recall: EstimateWithCI
    fp_rejection: EstimateWithCI
    proportion_removed_evaluable: EstimateWithCI
    proportion_removed_all: EstimateWithCI
    abstention_proportion: EstimateWithCI

    #: adjudicator label -> reference label -> count (4 x 3, incl. abstain col).
    cross_classification: dict[str, dict[str, int]]
    #: pair_id -> confusion cell: TP / FP / FN / TN / excluded (ref abstain).
    pair_cells: dict[str, str]


_ADJ_LABELS = ("Duplicates", "Otherwise_related", "Unrelated", "Abstain")
_REF_LABELS = ("duplicate", "non_duplicate", "abstain")


def run_precision_analysis(
    input: PrecisionAnalysisInput,
    variant: AnalysisVariant = AnalysisVariant.PRIMARY,
) -> PrecisionResult:
    """Compute the protocol precision-evaluation quantities."""
    eval_set = input.evaluation_set
    outputs = input.outputs

    cross = {a: {r: 0 for r in _REF_LABELS} for a in _ADJ_LABELS}
    cells: dict[str, str] = {}

    n_ref_dup = n_ref_non = n_ref_abstain = 0
    n_adj_positive = n_adj_abstain = 0
    # counters over evaluable pairs (reference abstain excluded)
    tp = fp = fn = tn = 0

    for pair in eval_set.pairs:
        adj = outputs.adjudicator[pair.pair_id].final_label
        ref = pair.reference_label
        cross[adj][ref] += 1
        if adj == "Abstain":
            n_adj_abstain += 1

        if ref == "abstain":
            n_ref_abstain += 1
            cells[pair.pair_id] = "excluded"
            continue

        positive = is_positive(adj, variant)
        if positive:
            n_adj_positive += 1
        if ref == "duplicate":
            n_ref_dup += 1
            cells[pair.pair_id] = "TP" if positive else "FN"
            tp += positive
            fn += not positive
        else:  # non_duplicate
            n_ref_non += 1
            cells[pair.pair_id] = "FP" if positive else "TN"
            fp += positive
            tn += not positive

    n_evaluable = n_ref_dup + n_ref_non
    n_removed_evaluable = fn + tn  # adjudicator-negative among evaluable
    # "removed" over all candidates: negatives incl. those on reference-abstain.
    n_negative_all = sum(
        1
        for pair in eval_set.pairs
        if not is_positive(outputs.adjudicator[pair.pair_id].final_label, variant)
    )

    return PrecisionResult(
        variant=variant.value,
        set_id=eval_set.set_id,
        n_pairs=len(eval_set.pairs),
        n_reference_abstain=n_ref_abstain,
        n_evaluable=n_evaluable,
        n_reference_duplicate=n_ref_dup,
        n_reference_non_duplicate=n_ref_non,
        n_adjudicator_positive=n_adj_positive,
        n_adjudicator_abstain=n_adj_abstain,
        vigimatch_baseline_precision=estimate(
            "vigimatch_baseline_precision", n_ref_dup, n_evaluable
        ),
        adjudicator_precision=estimate("adjudicator_precision", tp, tp + fp),
        conditional_recall=estimate("conditional_recall", tp, n_ref_dup),
        fp_rejection=estimate("fp_rejection", tn, n_ref_non),
        proportion_removed_evaluable=estimate(
            "proportion_removed_evaluable", n_removed_evaluable, n_evaluable
        ),
        proportion_removed_all=estimate(
            "proportion_removed_all", n_negative_all, len(eval_set.pairs)
        ),
        abstention_proportion=estimate(
            "abstention_proportion", n_adj_abstain, len(eval_set.pairs)
        ),
        cross_classification=cross,
        pair_cells=cells,
    )


def run_precision_variants(
    input: PrecisionAnalysisInput,
) -> dict[AnalysisVariant, PrecisionResult]:
    """Run the three re-interpretation variants over one precision bundle."""
    return {v: run_precision_analysis(input, v) for v in REINTERPRETATION_VARIANTS}


# --- Recall ---------------------------------------------------------------

#: The four joint vigiMatch–adjudicator retrieval strata.
JOINT_STRATA: tuple[str, ...] = (
    "both",
    "neither",
    "vigimatch_only",
    "adjudicator_only",
)


class RecallSetResult(BaseModel):
    """Recall results for one reference set under one variant."""

    variant: str
    reference_set_id: str
    reference_set_label: str
    n_pairs: int
    n_adjudicator_abstain: int

    recall_vigimatch: EstimateWithCI
    recall_serial: EstimateWithCI
    recall_adjudicator_ceiling: EstimateWithCI

    #: stratum name -> count (both / neither / vigimatch_only / adjudicator_only).
    joint_counts: dict[str, int]
    #: stratum name -> proportion of N, with CI.
    joint_proportions: dict[str, EstimateWithCI]
    #: recall_vigimatch - recall_serial, point estimate (protocol: descriptive).
    recall_reduction: float | None

    #: pair_id -> joint stratum, for SCLE sampling.
    pair_strata: dict[str, str]


class RecallResult(BaseModel):
    """Recall-evaluation results across all reference sets under one variant."""

    variant: str
    sets: list[RecallSetResult]


def _joint_stratum(vigimatch: bool, adjudicator_positive: bool) -> str:
    if vigimatch and adjudicator_positive:
        return "both"
    if vigimatch and not adjudicator_positive:
        return "vigimatch_only"
    if not vigimatch and adjudicator_positive:
        return "adjudicator_only"
    return "neither"


def run_recall_analysis(
    input: RecallAnalysisInput,
    variant: AnalysisVariant = AnalysisVariant.PRIMARY,
) -> RecallResult:
    """Compute the protocol recall estimands for every reference set."""
    sets: list[RecallSetResult] = []
    for eval_set in input.evaluation_sets:
        n = len(eval_set.pairs)
        n_vig = n_serial = n_ceiling = n_abstain = 0
        counts = {s: 0 for s in JOINT_STRATA}
        strata: dict[str, str] = {}

        for pair in eval_set.pairs:
            adj = input.outputs.adjudicator[pair.pair_id].final_label
            vig = input.outputs.vigimatch_retrieved[pair.pair_id]
            positive = is_positive(adj, variant)
            if adj == "Abstain":
                n_abstain += 1
            n_vig += vig
            n_ceiling += positive
            n_serial += vig and positive
            stratum = _joint_stratum(vig, positive)
            counts[stratum] += 1
            strata[pair.pair_id] = stratum

        recall_vig = estimate("recall_vigimatch", n_vig, n)
        recall_serial = estimate("recall_serial", n_serial, n)
        reduction: float | None = None
        if recall_vig.proportion is not None and recall_serial.proportion is not None:
            reduction = recall_vig.proportion - recall_serial.proportion

        sets.append(
            RecallSetResult(
                variant=variant.value,
                reference_set_id=eval_set.reference_set_id,
                reference_set_label=eval_set.reference_set_label,
                n_pairs=n,
                n_adjudicator_abstain=n_abstain,
                recall_vigimatch=recall_vig,
                recall_serial=recall_serial,
                recall_adjudicator_ceiling=estimate(
                    "recall_adjudicator_ceiling", n_ceiling, n
                ),
                joint_counts=counts,
                joint_proportions={
                    s: estimate(f"joint_{s}", counts[s], n) for s in JOINT_STRATA
                },
                recall_reduction=reduction,
                pair_strata=strata,
            )
        )
    return RecallResult(variant=variant.value, sets=sets)


def run_recall_variants(
    input: RecallAnalysisInput,
) -> dict[AnalysisVariant, RecallResult]:
    """Run the three re-interpretation variants over one recall bundle."""
    return {v: run_recall_analysis(input, v) for v in REINTERPRETATION_VARIANTS}


__all__ = [
    "AnalysisVariant",
    "REINTERPRETATION_VARIANTS",
    "JOINT_STRATA",
    "is_positive",
    "PrecisionResult",
    "RecallSetResult",
    "RecallResult",
    "run_precision_analysis",
    "run_precision_variants",
    "run_recall_analysis",
    "run_recall_variants",
]
