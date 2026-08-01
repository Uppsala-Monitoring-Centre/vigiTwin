"""Canonical, generic input schemas for the performance-evaluation workflow.

These are the models the secure-side adapter fills in. The adapter maps real
reference sets and frozen method outputs into these shapes; nothing here knows
about VigiBase, MedDRA, or any concrete database, screening method, or set of
reference sets.

Two label vocabularies are kept deliberately distinct:

* the **human reference** label (:data:`ReferenceLabel`): ``duplicate`` /
  ``non_duplicate`` / ``abstain``. In a precision reference set each pair carries
  one; in a recall reference set every pair is a positive control (``duplicate``
  by construction), so the per-pair label is optional there.
* the **evaluated method** (the "adjudicator") output label — the four-value
  vocabulary from :meth:`ClassificationResult.final_label` (``Duplicates`` /
  ``Otherwise_related`` / ``Unrelated`` / ``Abstain``).

Conflating the two is the classic way to score against the wrong ground truth,
so they never share a field or a validator.

Division of labour with the secure side
---------------------------------------
The secure side **constructs** the reference sets — it does all set-construction
sampling (one pair per cluster to reach a target size, held-out eligibility
filtering, any manual-vs-derived prioritisation) and sends one homogeneous set
per provenance. The dev side receives already-constructed sets and computes
estimates directly over the pairs it is given; it never samples *within* a
reference set. The only sampling the dev side performs is SCLE case selection
(see :mod:`llm_dup_dev.study_eval.sampling`).

Pair identity follows the repo-wide convention
(:mod:`llm_dup_dev.models.pair_identity`): the two report ids are stored in
canonical ascending order — numerically for numeric ids — and ``pair_id`` is
``{report_id1}__{report_id2}`` derived from that order. A pair therefore has one
identity whichever way round the secure side supplies it, and reference sets and
prediction sets join reliably.

A reference set that is *not* canonical is rejected rather than reordered. The
adapter should build its pairs with
:func:`llm_dup_dev.models.pair_identity.canonical_pair_id`; silently rewriting a
``pair_id`` here would break its join against the predictions and the human
annotations still keyed on the old id.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, model_validator

from llm_dup_dev.models.classification import ClassificationResult
from llm_dup_dev.models.pair_identity import validate_pair_identity
from llm_dup_dev.study_eval.records import SamplingRecord

# --- Vocabularies ----------------------------------------------------------

#: The role a reference set plays in the evaluation.
AnalysisRole = Literal["precision", "recall"]

#: Human reference label vocabulary.
ReferenceLabel = Literal["duplicate", "non_duplicate", "abstain"]

#: Whether screening-algorithm results are part of a reference set's estimand.
#:
#: ``observed``
#:     Screening results exist and are evaluated. Every pair needs a
#:     ``screening_retrieved`` verdict, screening and end-to-end estimands are
#:     computed. This is the default and the pre-existing behaviour.
#: ``not_evaluated``
#:     No screening-algorithm results are available for this set; every supplied
#:     pair went directly to the adjudicator. Screening performance is outside
#:     the estimand, so screening and screening-dependent quantities are not
#:     estimated at all — this is **not** an assumption of perfect screening.
#:     The mode describes the *estimand*, not whether screening data happen to
#:     exist: a ``screening_retrieved`` value supplied for such a pair is
#:     accepted and never read (see :mod:`llm_dup_dev.study_eval.preflight`).
ScreeningMode = Literal["observed", "not_evaluated"]

#: Decided labels the evaluated method can emit. The three decided labels come
#: from ``eval.labels.FINAL_LABELS``; ``Abstain`` is the fourth outcome (see
#: ``ClassificationResult.final_label``).
ADJUDICATOR_LABELS: tuple[str, ...] = (
    "Duplicates",
    "Otherwise_related",
    "Unrelated",
    "Abstain",
)


# --- Reference sets --------------------------------------------------------


class ReferencePair(BaseModel):
    """One report pair in a reference set.

    ``reference_label`` is the human annotator's verdict; it is required for a
    precision set and absent (or ``"duplicate"``) for a recall set. ``cluster_id``
    is optional provenance metadata — the secure side has already reduced each
    cluster to at most one pair, so the dev side only sanity-checks it.

    Pair identity must be canonical: ``report_id1`` and ``report_id2`` in
    ascending order (numerically for numeric ids) and ``pair_id`` derived from
    that order. A noncanonical pair is rejected, not reordered — see the module
    docstring.
    """

    pair_id: str
    report_id1: str
    report_id2: str
    cluster_id: str | None = None
    reference_label: ReferenceLabel | None = None
    annotator_id: str | None = None
    annotated_at: date | None = None

    @model_validator(mode="after")
    def _check_identity(self) -> "ReferencePair":
        validate_pair_identity(self.pair_id, self.report_id1, self.report_id2)
        return self


class ReferenceSet(BaseModel):
    """A generic reference set with a declared role and provenance.

    ``role`` selects the analysis: ``"precision"`` sets are the screening
    method's candidate set with per-pair human labels; ``"recall"`` sets are
    held-out positive controls (known duplicates). ``provenance`` describes the
    set as a whole — each set is homogeneous in provenance (the secure side
    sends one set per provenance), so there is no per-pair provenance here.
    ``sampling_record`` optionally carries the secure side's construction record.

    ``screening_mode`` declares whether screening performance is part of this
    set's estimand (see :data:`ScreeningMode`). It is per set, never global, and
    is never inferred — ``not_evaluated`` must be selected explicitly. Only a
    recall set may use it: a precision set *is* the screening method's candidate
    set, so "screening was not evaluated" cannot describe one.
    """

    set_id: str
    role: AnalysisRole
    label: str | None = None
    provenance: str | None = None
    screening_mode: ScreeningMode = "observed"
    sampling_record: SamplingRecord | None = None
    pairs: list[ReferencePair]

    @model_validator(mode="after")
    def _check_set(self) -> "ReferenceSet":
        _validate_pair_uniqueness(self.pairs)
        # cluster_id is optional metadata, but if present it must be unique
        # (the secure side already reduced each cluster to one pair).
        _validate_one_pair_per_cluster(self.pairs)

        if self.role == "precision" and self.screening_mode == "not_evaluated":
            raise ValueError(
                f"precision set {self.set_id!r}: screening_mode "
                f"'not_evaluated' is contradictory for a precision set, which "
                f"is by definition the screening method's candidate set. Use "
                f"screening_mode 'observed', or declare the set as a recall set."
            )

        if self.role == "precision":
            missing = [p.pair_id for p in self.pairs if p.reference_label is None]
            if missing:
                raise ValueError(
                    f"precision set {self.set_id!r}: every pair needs a "
                    f"reference_label; missing for {missing[:5]}"
                    f"{'...' if len(missing) > 5 else ''}"
                )
        else:  # recall
            labelled = [
                p.pair_id
                for p in self.pairs
                if p.reference_label not in (None, "duplicate")
            ]
            if labelled:
                raise ValueError(
                    f"recall set {self.set_id!r}: pairs are positive controls, "
                    f"so reference_label must be absent or 'duplicate'; got "
                    f"other labels for {labelled[:5]}"
                    f"{'...' if len(labelled) > 5 else ''}"
                )
        return self


# --- Frozen method predictions ---------------------------------------------


class PairPrediction(BaseModel):
    """The frozen prediction for one pair.

    ``adjudicator_label`` is the evaluated method's decided outcome.
    ``screening_retrieved`` is whether the upstream screening method flagged the
    pair as a candidate; ``None`` means no screening result was supplied. The
    field is read only for sets with ``screening_mode: observed``, where
    :mod:`llm_dup_dev.study_eval.preflight` requires it — a
    ``not_evaluated`` set neither needs it nor reads it, so the same prediction
    record can serve a pair belonging to sets of both modes.
    ``classification`` is the optional full decision trace (the same
    ``ClassificationResult`` the pipeline produces); when present it is checked
    for consistency with ``adjudicator_label`` and ``pair_id``.
    """

    pair_id: str
    adjudicator_label: str
    screening_retrieved: bool | None = None
    classification: ClassificationResult | None = None

    @model_validator(mode="after")
    def _check(self) -> "PairPrediction":
        if self.adjudicator_label not in ADJUDICATOR_LABELS:
            raise ValueError(
                f"adjudicator_label {self.adjudicator_label!r} is not one of "
                f"{ADJUDICATOR_LABELS}."
            )
        if self.classification is not None:
            if self.classification.pair_id != self.pair_id:
                raise ValueError(
                    f"classification.pair_id {self.classification.pair_id!r} "
                    f"does not match PairPrediction.pair_id {self.pair_id!r}."
                )
            if self.classification.final_label != self.adjudicator_label:
                raise ValueError(
                    f"classification trace implies final_label "
                    f"{self.classification.final_label!r} but PairPrediction "
                    f"says {self.adjudicator_label!r} for pair {self.pair_id!r}."
                )
        return self


class PredictionSet(BaseModel):
    """A frozen bundle of per-pair predictions from one method configuration.

    One bundle corresponds to one frozen run/configuration. The abstain- and
    otherwise-related-positive *re-interpretation* secondary analyses do not need
    a new bundle — they re-read this one under a different positivity rule (see
    :mod:`llm_dup_dev.study_eval.aggregate`). A configuration that produces
    genuinely different classifications (e.g. an LLM-based component swap) arrives
    as a second bundle with a distinct ``configuration_id``.

    ``predictions`` is keyed by ``pair_id``.
    """

    configuration_id: str = "primary"
    predictions: dict[str, PairPrediction]

    @model_validator(mode="after")
    def _check_keys(self) -> "PredictionSet":
        for key, prediction in self.predictions.items():
            if prediction.pair_id != key:
                raise ValueError(
                    f"predictions[{key!r}] has pair_id {prediction.pair_id!r}; "
                    f"the dict key and the record's pair_id must match."
                )
        return self

    def require_coverage(self, pair_ids: list[str], *, context: str) -> None:
        """Raise if any of ``pair_ids`` lacks a prediction."""
        missing = [pid for pid in pair_ids if pid not in self.predictions]
        if missing:
            raise ValueError(
                f"{context}: prediction missing for {len(missing)} pair(s): "
                f"{missing[:5]}{'...' if len(missing) > 5 else ''}"
            )


# --- Shared uniqueness validators ------------------------------------------


def _validate_pair_uniqueness(pairs: list[ReferencePair]) -> None:
    """Reject a repeated ``pair_id``.

    Because ``pair_id`` is canonical, this is also the check that catches the
    same two reports appearing twice with their orientation flipped: both rows
    canonicalise to the same id. There is no separate unordered-pair check.
    """
    seen_ids: set[str] = set()
    for pair in pairs:
        if pair.pair_id in seen_ids:
            raise ValueError(
                f"duplicate pair_id {pair.pair_id!r}: the same two reports "
                f"appear twice. pair_id is canonical, so two rows naming the "
                f"same reports in either orientation collide here."
            )
        seen_ids.add(pair.pair_id)


def _validate_one_pair_per_cluster(pairs: list[ReferencePair]) -> None:
    """Reject more than one pair sharing a (non-null) ``cluster_id``."""
    seen: dict[str, str] = {}
    for pair in pairs:
        cluster_id = pair.cluster_id
        if cluster_id is None:
            continue
        if cluster_id in seen:
            raise ValueError(
                f"cluster {cluster_id!r} contributes more than one pair: "
                f"{seen[cluster_id]!r} and {pair.pair_id!r}. The secure side "
                f"should reduce each cluster to at most one pair."
            )
        seen[cluster_id] = pair.pair_id


# ``SamplingRecord`` is imported at the top from ``.records``; the set model
# references it via a string annotation, so resolve the forward ref now.
ReferenceSet.model_rebuild()


__all__ = [
    "AnalysisRole",
    "ReferenceLabel",
    "ScreeningMode",
    "ADJUDICATOR_LABELS",
    "ReferencePair",
    "ReferenceSet",
    "PairPrediction",
    "PredictionSet",
    "SamplingRecord",
]
