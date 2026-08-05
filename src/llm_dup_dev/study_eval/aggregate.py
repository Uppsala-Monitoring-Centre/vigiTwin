"""Protocol-defined aggregate precision and recall calculations.

Inputs are a generic :class:`ReferenceSet` plus a :class:`PredictionSet`; the
functions here derive the per-pair truth/prediction join and compute the
protocol quantities. The positive class is expressed as an explicit rule over
the four-value evaluated-method output (:class:`PositiveClass`) rather than
reusing :class:`llm_dup_dev.eval.labels.AbstainMode`, because ``AbstainMode``
cannot express the "otherwise-related counts as positive" secondary analysis and
its ``DECIDED_ONLY`` mode contradicts the protocol's primary rule (an abstention
is a negative classification).

This module owns the **output vocabulary** (:data:`ADJUDICATOR_LABELS`, which is
the adjudicator's own contract) and the **mechanism** (a positive class is a
subset of that vocabulary). It does not own *which* collapse rules a study runs,
their names, or which one is the baseline: those are study configuration, and the
caller supplies them — the same division :mod:`.scle.vocabularies` applies to the
SCLE vocabularies. The three constants below are the current study's rules,
provided for convenience, not the package's opinion about what to run.

Reference-abstain pairs (a human ``abstain``) are excluded from every precision
estimate and counted. Recall-reference pairs are positive controls, so recall
estimates have no reference-abstain exclusion.

A recall set may declare ``screening_mode: not_evaluated`` (see
:data:`~llm_dup_dev.study_eval.schemas.ScreeningMode`), meaning no
screening-algorithm results exist for it and screening performance is outside
its estimand. Every screening and end-to-end quantity is then left unset rather
than given a value: the screening estimands are ``None``, not an estimate of
zero, one or anything else, and no synthetic all-positive screening verdicts are
constructed anywhere. What remains is the adjudicator's own performance over all
supplied reference pairs, which is exactly the estimand such a set supports.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, field_validator

from llm_dup_dev.study_eval.preflight import preflight_reference_set
from llm_dup_dev.study_eval.schemas import PredictionSet, ReferenceSet
from llm_dup_dev.study_eval.stats import EstimateWithCI, estimate


#: The evaluated method's own output vocabulary. Owned here because it is the
#: adjudicator's contract, not a study choice: a positive class may only ever be
#: built out of these four labels.
ADJUDICATOR_LABELS: tuple[str, ...] = (
    "Duplicates",
    "Otherwise_related",
    "Unrelated",
    "Abstain",
)


class PositiveClass(BaseModel):
    """A study-supplied rule collapsing the adjudicator output to a positive class.

    ``name`` identifies the rule in every output table (the ``variant`` column),
    in the run manifest, and as the baseline key for the secondary-comparison
    and summary artifacts. ``positive_labels`` is the subset of
    :data:`ADJUDICATOR_LABELS` counted as positive; every other label is
    negative.

    Validated as configuration, in the same spirit as
    :func:`~llm_dup_dev.study_eval.scle.vocabularies.normalise_vocabulary`: the
    name must be non-blank and the labels a non-empty subset of the output
    vocabulary. Deliberately *not* enforced is that ``Duplicates`` be included or
    that the subset be proper — a study may define whatever collapse it can
    justify, and the package checks only what it owns.

    Frozen, so an instance can key a dict and cannot drift after a run has
    recorded it.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    positive_labels: frozenset[str]

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if stripped == "":
            raise ValueError("PositiveClass name is blank after trimming.")
        return stripped

    @field_validator("positive_labels")
    @classmethod
    def _labels_in_vocabulary(cls, value: frozenset[str]) -> frozenset[str]:
        if len(value) == 0:
            raise ValueError(
                f"PositiveClass positive_labels must be non-empty; permitted "
                f"labels are {list(ADJUDICATOR_LABELS)}."
            )
        unknown = sorted(value - set(ADJUDICATOR_LABELS))
        if unknown:
            raise ValueError(
                f"PositiveClass positive_labels contains {unknown}, which is not "
                f"in the adjudicator output vocabulary "
                f"{list(ADJUDICATOR_LABELS)}."
            )
        return value


# The current study's rules, provided for convenience. The package does not
# decide which of these (or which others) a study runs: pass whichever
# ``PositiveClass`` values the protocol prespecifies, and name the baseline
# explicitly when writing the comparison and summary artifacts.
PRIMARY = PositiveClass(name="primary", positive_labels=frozenset({"Duplicates"}))
ABSTAIN_POSITIVE = PositiveClass(
    name="abstain_positive",
    positive_labels=frozenset({"Duplicates", "Abstain"}),
)
OTHERWISE_RELATED_POSITIVE = PositiveClass(
    name="otherwise_related_positive",
    positive_labels=frozenset({"Duplicates", "Otherwise_related"}),
)


def is_positive(adjudicator_label: str, positive_class: PositiveClass) -> bool:
    """Return whether ``adjudicator_label`` counts as positive under the rule."""
    return adjudicator_label in positive_class.positive_labels


# --- Precision ------------------------------------------------------------


class PrecisionResult(BaseModel):
    """Precision-evaluation results for one reference set under one variant.

    ``variant`` holds the :class:`PositiveClass` name the estimates were computed
    under; it is the ``variant`` column of every output table.
    """

    variant: str
    set_id: str
    #: Always ``"observed"`` — a precision set is the screening candidate set.
    screening_mode: str = "observed"
    n_pairs: int
    n_reference_abstain: int
    n_evaluable: int
    n_reference_duplicate: int
    n_reference_non_duplicate: int
    n_adjudicator_positive: int
    n_adjudicator_abstain: int

    screening_baseline_precision: EstimateWithCI
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


_REF_LABELS = ("duplicate", "non_duplicate", "abstain")


def run_precision_analysis(
    reference_set: ReferenceSet,
    predictions: PredictionSet,
    positive_class: PositiveClass = PRIMARY,
) -> PrecisionResult:
    """Compute the protocol precision-evaluation quantities for one set.

    Note the deliberate asymmetry around abstention, which ``positive_class``
    does **not** govern: ``Abstain`` as an *adjudicator* label participates in the
    collapse rule like any other output label, whereas ``abstain`` as a
    *reference* label is a protocol-level exclusion from the precision
    denominator and is never configurable here.
    """
    if reference_set.role != "precision":
        raise ValueError(
            f"run_precision_analysis needs a precision set; {reference_set.set_id!r} "
            f"has role {reference_set.role!r}."
        )
    preflight_reference_set(reference_set, predictions)

    cross = {a: {r: 0 for r in _REF_LABELS} for a in ADJUDICATOR_LABELS}
    cells: dict[str, str] = {}

    n_ref_dup = n_ref_non = n_ref_abstain = 0
    n_adj_positive = n_adj_abstain = 0
    tp = fp = fn = tn = 0

    for pair in reference_set.pairs:
        adj = predictions.predictions[pair.pair_id].adjudicator_label
        ref = pair.reference_label
        assert ref is not None  # precision sets are validated to carry a label
        cross[adj][ref] += 1
        if adj == "Abstain":
            n_adj_abstain += 1

        if ref == "abstain":
            n_ref_abstain += 1
            cells[pair.pair_id] = "excluded"
            continue

        positive = is_positive(adj, positive_class)
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
    n_negative_all = sum(
        1
        for pair in reference_set.pairs
        if not is_positive(
            predictions.predictions[pair.pair_id].adjudicator_label, positive_class
        )
    )

    return PrecisionResult(
        variant=positive_class.name,
        set_id=reference_set.set_id,
        screening_mode=reference_set.screening_mode,
        n_pairs=len(reference_set.pairs),
        n_reference_abstain=n_ref_abstain,
        n_evaluable=n_evaluable,
        n_reference_duplicate=n_ref_dup,
        n_reference_non_duplicate=n_ref_non,
        n_adjudicator_positive=n_adj_positive,
        n_adjudicator_abstain=n_adj_abstain,
        screening_baseline_precision=estimate(
            "screening_baseline_precision", n_ref_dup, n_evaluable
        ),
        adjudicator_precision=estimate("adjudicator_precision", tp, tp + fp),
        conditional_recall=estimate("conditional_recall", tp, n_ref_dup),
        fp_rejection=estimate("fp_rejection", tn, n_ref_non),
        proportion_removed_evaluable=estimate(
            "proportion_removed_evaluable", n_removed_evaluable, n_evaluable
        ),
        proportion_removed_all=estimate(
            "proportion_removed_all", n_negative_all, len(reference_set.pairs)
        ),
        abstention_proportion=estimate(
            "abstention_proportion", n_adj_abstain, len(reference_set.pairs)
        ),
        cross_classification=cross,
        pair_cells=cells,
    )


def run_precision_variants(
    reference_set: ReferenceSet,
    predictions: PredictionSet,
    variants: Sequence[PositiveClass],
) -> dict[str, PrecisionResult]:
    """Run each supplied collapse rule over one precision set, keyed by name.

    ``variants`` is required: which rules a study runs is its own prespecified
    choice, not a package default. Duplicate names are rejected, since the result
    dict — and the ``variant`` column of every table built from it — is keyed by
    name.
    """
    _check_distinct_names(variants)
    return {
        pc.name: run_precision_analysis(reference_set, predictions, pc)
        for pc in variants
    }


# --- Recall ---------------------------------------------------------------

#: The four joint screening–adjudicator retrieval strata. Used for a reference
#: set with ``screening_mode: observed``.
JOINT_STRATA: tuple[str, ...] = (
    "both",
    "neither",
    "screening_only",
    "adjudicator_only",
)

#: Strata for a reference set with ``screening_mode: not_evaluated``. Without a
#: screening verdict the joint strata are undefined, so cases are stratified on
#: the adjudicator outcome alone — the only dimension such a set observes.
ADJUDICATOR_STRATA: tuple[str, ...] = (
    "adjudicator_positive",
    "adjudicator_negative",
)


class RecallSetResult(BaseModel):
    """Recall results for one reference set under one variant.

    Under ``screening_mode: not_evaluated`` every screening-dependent field —
    ``recall_screening``, ``recall_serial``, ``recall_reduction``,
    ``joint_counts`` and ``joint_proportions`` — is ``None``, meaning *not
    estimated*, and is distinct from an estimate that came out undefined (which
    is a present :class:`EstimateWithCI` with ``proportion=None`` and
    ``denominator=0``).
    """

    variant: str
    reference_set_id: str
    reference_set_label: str
    #: The set's screening mode: ``"observed"`` or ``"not_evaluated"``.
    screening_mode: str = "observed"
    n_pairs: int
    n_adjudicator_abstain: int

    #: ``None`` when screening was not evaluated for this set.
    recall_screening: EstimateWithCI | None = None
    #: ``None`` when screening was not evaluated for this set.
    recall_serial: EstimateWithCI | None = None
    #: Under ``observed``, the ceiling recall a perfect screening step would
    #: allow. Under ``not_evaluated``, the directly estimated adjudicator recall
    #: over all supplied reference pairs. Always present.
    recall_adjudicator_ceiling: EstimateWithCI

    #: stratum name -> count. ``None`` when screening was not evaluated.
    joint_counts: dict[str, int] | None = None
    #: stratum name -> proportion of N, with CI. ``None`` when not evaluated.
    joint_proportions: dict[str, EstimateWithCI] | None = None
    #: recall_screening - recall_serial, point estimate (protocol: descriptive).
    #: ``None`` when screening was not evaluated, as both inputs are absent.
    recall_reduction: float | None

    #: pair_id -> stratum, for SCLE sampling. The vocabulary is
    #: :data:`JOINT_STRATA` under ``observed`` and :data:`ADJUDICATOR_STRATA`
    #: under ``not_evaluated``.
    pair_strata: dict[str, str]


class RecallResult(BaseModel):
    """Recall-evaluation results across all reference sets under one variant.

    The protocol reports recall separately per reference set — the sets differ in
    provenance and annotation procedure — so there is no pooled estimate to which
    a mixture of screening modes could contribute. What the two id lists provide
    instead is the *scope* of the screening estimands: which sets carry them and
    which do not. A consumer building any screening aggregate must restrict
    itself to ``screening_evaluated_set_ids`` and report that as its denominator
    scope; a set in ``screening_not_evaluated_set_ids`` contributes nothing and
    must never be counted as a zero or a one.
    """

    variant: str
    sets: list[RecallSetResult]
    #: Set ids whose screening estimands are present (``screening_mode: observed``).
    screening_evaluated_set_ids: list[str] = []
    #: Set ids with no screening estimands (``screening_mode: not_evaluated``).
    screening_not_evaluated_set_ids: list[str] = []


def _joint_stratum(screening: bool, adjudicator_positive: bool) -> str:
    if screening and adjudicator_positive:
        return "both"
    if screening and not adjudicator_positive:
        return "screening_only"
    if not screening and adjudicator_positive:
        return "adjudicator_only"
    return "neither"


def _adjudicator_stratum(adjudicator_positive: bool) -> str:
    return "adjudicator_positive" if adjudicator_positive else "adjudicator_negative"


def run_recall_analysis(
    reference_sets: list[ReferenceSet],
    predictions: PredictionSet,
    positive_class: PositiveClass = PRIMARY,
) -> RecallResult:
    """Compute the protocol recall estimands for every recall reference set.

    The evaluated method is assumed to have been run under **complete candidate
    coverage** — a prediction for every pair, regardless of screening retrieval —
    because the recall-ceiling estimand needs a verdict for every pair.

    A set with ``screening_mode: not_evaluated`` contributes only its adjudicator
    estimand; its screening and end-to-end fields are left ``None`` and its pairs
    are stratified on the adjudicator outcome alone.
    """
    ids = [s.set_id for s in reference_sets]
    if len(ids) != len(set(ids)):
        dupes = sorted({x for x in ids if ids.count(x) > 1})
        raise ValueError(f"duplicate reference set id(s): {dupes}")

    sets: list[RecallSetResult] = []
    for eval_set in reference_sets:
        if eval_set.role != "recall":
            raise ValueError(
                f"run_recall_analysis needs recall sets; {eval_set.set_id!r} has "
                f"role {eval_set.role!r}."
            )
        preflight_reference_set(eval_set, predictions)
        screening_observed = eval_set.screening_mode == "observed"

        n = len(eval_set.pairs)
        n_screen = n_serial = n_ceiling = n_abstain = 0
        counts = {s: 0 for s in JOINT_STRATA} if screening_observed else None
        strata: dict[str, str] = {}

        for pair in eval_set.pairs:
            pred = predictions.predictions[pair.pair_id]
            adj = pred.adjudicator_label
            positive = is_positive(adj, positive_class)
            if adj == "Abstain":
                n_abstain += 1
            n_ceiling += positive
            if counts is None:
                # No screening verdict is read for this set, and none is
                # invented: only the adjudicator dimension is observed.
                strata[pair.pair_id] = _adjudicator_stratum(positive)
                continue
            screened = pred.screening_retrieved
            assert screened is not None  # preflight requires it under 'observed'
            n_screen += screened
            n_serial += screened and positive
            stratum = _joint_stratum(screened, positive)
            counts[stratum] += 1
            strata[pair.pair_id] = stratum

        recall_screen = (
            estimate("recall_screening", n_screen, n) if screening_observed else None
        )
        recall_serial = (
            estimate("recall_serial", n_serial, n) if screening_observed else None
        )
        reduction: float | None = None
        if (
            recall_screen is not None
            and recall_serial is not None
            and recall_screen.proportion is not None
            and recall_serial.proportion is not None
        ):
            reduction = recall_screen.proportion - recall_serial.proportion

        sets.append(
            RecallSetResult(
                variant=positive_class.name,
                reference_set_id=eval_set.set_id,
                reference_set_label=eval_set.label or eval_set.set_id,
                screening_mode=eval_set.screening_mode,
                n_pairs=n,
                n_adjudicator_abstain=n_abstain,
                recall_screening=recall_screen,
                recall_serial=recall_serial,
                recall_adjudicator_ceiling=estimate(
                    "recall_adjudicator_ceiling", n_ceiling, n
                ),
                joint_counts=counts,
                joint_proportions=(
                    {s: estimate(f"joint_{s}", counts[s], n) for s in JOINT_STRATA}
                    if counts is not None
                    else None
                ),
                recall_reduction=reduction,
                pair_strata=strata,
            )
        )
    return RecallResult(
        variant=positive_class.name,
        sets=sets,
        screening_evaluated_set_ids=[
            s.reference_set_id for s in sets if s.screening_mode == "observed"
        ],
        screening_not_evaluated_set_ids=[
            s.reference_set_id for s in sets if s.screening_mode != "observed"
        ],
    )


def run_recall_variants(
    reference_sets: list[ReferenceSet],
    predictions: PredictionSet,
    variants: Sequence[PositiveClass],
) -> dict[str, RecallResult]:
    """Run each supplied collapse rule over the recall sets, keyed by name.

    As for :func:`run_precision_variants`, ``variants`` is required and names
    must be distinct.
    """
    _check_distinct_names(variants)
    return {
        pc.name: run_recall_analysis(reference_sets, predictions, pc)
        for pc in variants
    }


def _check_distinct_names(variants: Sequence[PositiveClass]) -> None:
    """Reject an empty rule list or repeated rule names."""
    if len(variants) == 0:
        raise ValueError("variants must contain at least one PositiveClass.")
    names = [pc.name for pc in variants]
    if len(names) != len(set(names)):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise ValueError(
            f"duplicate PositiveClass name(s): {dupes}; results are keyed by name."
        )


__all__ = [
    "ADJUDICATOR_LABELS",
    "PositiveClass",
    "PRIMARY",
    "ABSTAIN_POSITIVE",
    "OTHERWISE_RELATED_POSITIVE",
    "JOINT_STRATA",
    "ADJUDICATOR_STRATA",
    "is_positive",
    "PrecisionResult",
    "RecallSetResult",
    "RecallResult",
    "run_precision_analysis",
    "run_precision_variants",
    "run_recall_analysis",
    "run_recall_variants",
]
