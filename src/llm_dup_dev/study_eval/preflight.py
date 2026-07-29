"""Reusable input validation run before any estimate is computed.

The structural contract (pair-ID orientation, uniqueness, label vocabularies,
role/label rules) is enforced by the models themselves in
:mod:`llm_dup_dev.study_eval.schemas`. What is left is everything that needs a
reference set *and* a prediction set together: prediction coverage, set-ID
uniqueness across the bundle, and the screening requirements.

Screening requirements are conditional on each set's
:data:`~llm_dup_dev.study_eval.schemas.ScreeningMode`:

``observed``
    Every pair needs a ``screening_retrieved`` verdict, and a precision set —
    which *is* the screening method's candidate set — needs all of them true.
    Missing or unusable screening results are a hard error, as before.
``not_evaluated``
    No screening result is required and none is ever read. Everything else —
    coverage, reference labels, adjudicator predictions — is validated exactly
    as for an ``observed`` set.

Supplied-but-unused screening data: **accepted and explicitly ignored**, not
rejected. ``screening_mode`` states what a set's estimand is, not whether
screening data physically exist somewhere, so a pair is free to belong to an
``observed`` set where its screening verdict is analysed *and* to a separate
``not_evaluated`` set where screening is deliberately out of scope. Ignoring is
never silent: a warning names the set and how many values went unused.

Problems are accumulated and raised as a single ``ValueError`` (the style of
:func:`llm_dup_dev.study_eval.scle.feature_domains.validate_submission_feature_domains`)
so one preflight run reports every blocking issue rather than the first.
"""

from __future__ import annotations

import warnings

from llm_dup_dev.study_eval.schemas import PredictionSet, ReferenceSet


def _truncate(items: list[str], limit: int = 5) -> str:
    return f"{items[:limit]}{'...' if len(items) > limit else ''}"


def _check_coverage(reference_set: ReferenceSet, predictions: PredictionSet) -> list[str]:
    """Missing-prediction problems for one set, in ``require_coverage``'s wording."""
    try:
        predictions.require_coverage(
            [p.pair_id for p in reference_set.pairs],
            context=f"{reference_set.role} set {reference_set.set_id!r}",
        )
    except ValueError as exc:
        return [str(exc)]
    return []


def _check_screening(
    reference_set: ReferenceSet, predictions: PredictionSet
) -> list[str]:
    """Screening problems for one set, conditional on its ``screening_mode``.

    Only pairs that actually have a prediction are inspected; a missing
    prediction is already reported by :func:`_check_coverage`, and reporting it
    twice would bury the real cause.
    """
    covered = [
        p.pair_id for p in reference_set.pairs if p.pair_id in predictions.predictions
    ]

    if reference_set.screening_mode == "not_evaluated":
        ignored = [
            pid
            for pid in covered
            if predictions.predictions[pid].screening_retrieved is not None
        ]
        if ignored:
            warnings.warn(
                f"{reference_set.role} set {reference_set.set_id!r} has "
                f"screening_mode 'not_evaluated', so screening performance is "
                f"outside its estimand: the screening_retrieved value supplied "
                f"for {len(ignored)} of its {len(covered)} pair(s) is ignored "
                f"and no screening or end-to-end metric is computed for this "
                f"set. Set screening_mode to 'observed' if those values are "
                f"meant to be evaluated.",
                stacklevel=2,
            )
        return []

    problems: list[str] = []
    missing = [
        pid for pid in covered if predictions.predictions[pid].screening_retrieved is None
    ]
    if missing:
        problems.append(
            f"{reference_set.role} set {reference_set.set_id!r}: screening_mode "
            f"is 'observed', so every pair needs a screening_retrieved verdict; "
            f"it is missing for {len(missing)} pair(s): {_truncate(missing)}"
        )

    if reference_set.role == "precision":
        not_screened = [
            pid
            for pid in covered
            if predictions.predictions[pid].screening_retrieved is False
        ]
        if not_screened:
            problems.append(
                f"precision set {reference_set.set_id!r}: {len(not_screened)} pair(s) "
                f"are not flagged as screening-retrieved, but the set is defined as "
                f"the screening method's candidate set: {_truncate(not_screened)}"
            )
    return problems


def preflight_reference_set(
    reference_set: ReferenceSet, predictions: PredictionSet
) -> None:
    """Validate one reference set against ``predictions``.

    Raises a single ``ValueError`` listing every problem found. Called by the
    analysis functions so a direct caller is protected even when it bypasses
    :func:`preflight_reference_sets`.
    """
    problems = _check_coverage(reference_set, predictions)
    problems += _check_screening(reference_set, predictions)
    _raise(problems)


def preflight_reference_sets(
    reference_sets: list[ReferenceSet], predictions: PredictionSet
) -> None:
    """Validate a whole bundle of reference sets against ``predictions``.

    Checks set-ID uniqueness across the bundle, then every per-set requirement.
    All problems across all sets are reported together.
    """
    problems: list[str] = []

    ids = [s.set_id for s in reference_sets]
    duplicates = sorted({x for x in ids if ids.count(x) > 1})
    if duplicates:
        problems.append(f"duplicate reference set id(s): {duplicates}")

    for reference_set in reference_sets:
        problems += _check_coverage(reference_set, predictions)
        problems += _check_screening(reference_set, predictions)

    _raise(problems)


def _raise(problems: list[str]) -> None:
    if problems:
        raise ValueError(
            "reference-set preflight failed:\n"
            + "\n".join(f"  - {p}" for p in problems)
        )


__all__ = [
    "preflight_reference_set",
    "preflight_reference_sets",
]
