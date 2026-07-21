"""Canonical pair-level input schemas for the performance-evaluation study.

These are the *generic* models the secure-side adapter fills in. The adapter's
whole job is to map real reference sets and frozen method outputs into these
shapes; nothing here knows about VigiBase, MedDRA, or any concrete database.
See docs/study_eval.md § Secure-side integration.

Two label vocabularies are kept deliberately distinct:

* the **human reference** label (:data:`ReferenceLabel`): ``duplicate`` /
  ``non_duplicate`` / ``abstain``. This is what the precision annotator
  assigned; every recall-reference pair is a ``duplicate`` by construction.
* the **adjudicator** output label — the four-value vocabulary from
  :meth:`ClassificationResult.final_label` (``Duplicates`` /
  ``Otherwise_related`` / ``Unrelated`` / ``Abstain``), validated here against
  :mod:`llm_dup_dev.eval.labels`.

Conflating the two is the classic way to score against the wrong ground truth,
so they never share a field or a validator.

Pair identity follows the repo-wide convention
(:func:`llm_dup_dev.eval.annotations.derive_pair_id`): ``pair_id`` is
``{report_id1}__{report_id2}`` and is **orientation-sensitive**. The models
below validate that every ``pair_id`` is consistent with its two report ids and
that no unordered report-id pair appears twice (which would mean ``A__B`` and
``B__A`` both slipped in for the same two reports).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, model_validator

from llm_dup_dev.eval.annotations import derive_pair_id
from llm_dup_dev.models.classification import ClassificationResult
from llm_dup_dev.study_eval.records import SamplingRecord

# --- Reference (human) label vocabulary ------------------------------------

ReferenceLabel = Literal["duplicate", "non_duplicate", "abstain"]

#: Adjudicator output labels this package recognises. The three decided labels
#: come from ``eval.labels.FINAL_LABELS``; ``Abstain`` is the fourth
#: adjudicator outcome (see ``ClassificationResult.final_label``).
ADJUDICATOR_LABELS: tuple[str, ...] = (
    "Duplicates",
    "Otherwise_related",
    "Unrelated",
    "Abstain",
)

#: The five regulator-derived recall reference sets named in the protocol.
#: An unknown ``reference_set_id`` is *warned about*, not rejected, so the
#: package stays generic (a sixth set can be added without a code change).
KNOWN_REFERENCE_SETS: tuple[str, ...] = (
    "fda_gold",
    "fda_silver",
    "aemps",
    "mhra",
    "lareb",
)


def _check_pair_id(pair_id: str, report_id1: str, report_id2: str) -> None:
    """Raise ``ValueError`` if ``pair_id`` is not ``{report_id1}__{report_id2}``."""
    expected = derive_pair_id(report_id1, report_id2)
    if pair_id != expected:
        raise ValueError(
            f"pair_id {pair_id!r} is inconsistent with its report ids "
            f"(expected {expected!r} from report_id1={report_id1!r}, "
            f"report_id2={report_id2!r}). pair_id is orientation-sensitive."
        )


def _unordered_key(report_id1: str, report_id2: str) -> frozenset[str]:
    """Orientation-independent identity of a pair, for duplicate detection."""
    return frozenset((report_id1, report_id2))


# --- Precision evaluation set ----------------------------------------------


class PrecisionPairRecord(BaseModel):
    """One report pair in the precision evaluation set.

    ``reference_label`` is the human annotator's verdict. ``cluster_id`` is the
    vigiMatch2025 cluster the pair was sampled from, when known — recorded for
    provenance; the precision set already holds at most one pair per cluster by
    construction.
    """

    pair_id: str
    report_id1: str
    report_id2: str
    cluster_id: str | None = None
    reference_label: ReferenceLabel
    annotator_id: str | None = None
    annotated_at: date | None = None

    @model_validator(mode="after")
    def _check_identity(self) -> "PrecisionPairRecord":
        _check_pair_id(self.pair_id, self.report_id1, self.report_id2)
        return self


class PrecisionEvaluationSet(BaseModel):
    """The precision evaluation set: vigiMatch2025 candidates with human labels.

    Every pair here was flagged as a suspected duplicate by the screening
    method, so the proportion the annotator labelled ``duplicate`` is the
    screening method's precision within this sample.
    """

    set_id: str
    screening_method: str = "vigiMatch2025"
    snapshot_description: str | None = None
    sampling_record: "SamplingRecord | None" = None
    pairs: list[PrecisionPairRecord]

    @model_validator(mode="after")
    def _check_uniqueness(self) -> "PrecisionEvaluationSet":
        _validate_pair_uniqueness(self.pairs)
        _validate_one_pair_per_cluster(self.pairs, required=False)
        return self


# --- Recall evaluation sets ------------------------------------------------


class RecallPairRecord(BaseModel):
    """One reference-duplicate pair sampled from a recall reference set.

    Every recall pair is a positive control (a known duplicate), so there is no
    per-pair reference label. ``cluster_id`` is required because recall
    sampling draws one pair per duplicate cluster. ``provenance`` distinguishes
    manually classified pairs from E2B-field-linked pairs (the Spanish and
    Dutch sets contain both, and the protocol prioritises manual pairs).
    ``eligible`` records whether the pair belongs to the held-out test portion.
    """

    pair_id: str
    report_id1: str
    report_id2: str
    cluster_id: str
    provenance: Literal["manual", "e2b_derived", "unspecified"] = "unspecified"
    eligible: bool = True

    @model_validator(mode="after")
    def _check_identity(self) -> "RecallPairRecord":
        _check_pair_id(self.pair_id, self.report_id1, self.report_id2)
        return self


class RecallEvaluationSet(BaseModel):
    """One regulator-derived recall reference set (e.g. FDA Gold).

    ``reference_set_id`` is validated against :data:`KNOWN_REFERENCE_SETS` with
    a warning (not an error) for unknown ids, keeping the package generic.
    """

    reference_set_id: str
    reference_set_label: str
    sampling_record: "SamplingRecord | None" = None
    pairs: list[RecallPairRecord]

    @model_validator(mode="after")
    def _check_set(self) -> "RecallEvaluationSet":
        if self.reference_set_id not in KNOWN_REFERENCE_SETS:
            import warnings

            warnings.warn(
                f"reference_set_id {self.reference_set_id!r} is not one of the "
                f"known sets {KNOWN_REFERENCE_SETS}. Proceeding; results will "
                f"still be computed for it.",
                stacklevel=2,
            )
        _validate_pair_uniqueness(self.pairs)
        return self


# --- Frozen method outputs -------------------------------------------------


class AdjudicatorOutput(BaseModel):
    """The frozen adjudicator verdict for one pair.

    ``final_label`` is the decided outcome. ``classification`` is the optional
    full decision trace (the same ``ClassificationResult`` the pipeline
    produces); when present it is checked for consistency with ``final_label``
    and ``pair_id``, so a mismatched trace cannot slip through.
    """

    pair_id: str
    final_label: str
    classification: ClassificationResult | None = None

    @model_validator(mode="after")
    def _check(self) -> "AdjudicatorOutput":
        if self.final_label not in ADJUDICATOR_LABELS:
            raise ValueError(
                f"final_label {self.final_label!r} is not one of "
                f"{ADJUDICATOR_LABELS}."
            )
        if self.classification is not None:
            if self.classification.pair_id != self.pair_id:
                raise ValueError(
                    f"classification.pair_id {self.classification.pair_id!r} "
                    f"does not match AdjudicatorOutput.pair_id {self.pair_id!r}."
                )
            if self.classification.final_label != self.final_label:
                raise ValueError(
                    f"classification trace implies final_label "
                    f"{self.classification.final_label!r} but AdjudicatorOutput "
                    f"says {self.final_label!r} for pair {self.pair_id!r}."
                )
        return self


class MethodOutputs(BaseModel):
    """A frozen bundle of method outputs for a set of pairs.

    One bundle corresponds to one frozen run of the method. The two abstain /
    otherwise-related *re-interpretation* secondary analyses do not need a new
    bundle — they re-read this one under a different positivity rule (see
    :mod:`llm_dup_dev.study_eval.aggregate`). The LLM-AE-clinical-picture
    secondary analysis *does* produce genuinely different classifications and
    therefore arrives as a second bundle with a distinct ``variant_id``.

    ``adjudicator`` and ``vigimatch_retrieved`` are keyed by ``pair_id``.
    ``vigimatch_retrieved[pair_id]`` is whether the screening method flagged
    that pair as a suspected duplicate.
    """

    variant_id: str = "primary"
    adjudicator: dict[str, AdjudicatorOutput]
    vigimatch_retrieved: dict[str, bool]

    @model_validator(mode="after")
    def _check_keys(self) -> "MethodOutputs":
        for key, output in self.adjudicator.items():
            if output.pair_id != key:
                raise ValueError(
                    f"adjudicator[{key!r}] has pair_id {output.pair_id!r}; the "
                    f"dict key and the record's pair_id must match."
                )
        return self


# --- Analysis inputs (what the secure adapter hands to the entry points) ---


class PrecisionAnalysisInput(BaseModel):
    """Everything needed to run the precision analysis for one output bundle."""

    evaluation_set: PrecisionEvaluationSet
    outputs: MethodOutputs

    @model_validator(mode="after")
    def _check_coverage(self) -> "PrecisionAnalysisInput":
        pair_ids = [p.pair_id for p in self.evaluation_set.pairs]
        missing = [pid for pid in pair_ids if pid not in self.outputs.adjudicator]
        if missing:
            raise ValueError(
                f"adjudicator output missing for {len(missing)} precision "
                f"pair(s): {missing[:5]}{'...' if len(missing) > 5 else ''}"
            )
        missing_flag = [
            pid for pid in pair_ids if pid not in self.outputs.vigimatch_retrieved
        ]
        if missing_flag:
            raise ValueError(
                f"vigimatch_retrieved flag missing for {len(missing_flag)} "
                f"precision pair(s): {missing_flag[:5]}"
                f"{'...' if len(missing_flag) > 5 else ''}"
            )
        not_screened = [
            pid
            for pid in pair_ids
            if not self.outputs.vigimatch_retrieved.get(pid, False)
        ]
        if not_screened:
            raise ValueError(
                f"{len(not_screened)} precision pair(s) are not flagged as "
                f"vigiMatch-retrieved, but the precision set is defined as the "
                f"screening method's candidate set: {not_screened[:5]}"
                f"{'...' if len(not_screened) > 5 else ''}"
            )
        return self


class RecallAnalysisInput(BaseModel):
    """Everything needed to run the recall analysis across the reference sets.

    The adjudicator is assumed to have been run under **complete candidate
    coverage** — i.e. on every sampled reference pair, regardless of whether
    the screening method retrieved it — because estimand 2 (the recall ceiling)
    needs an adjudicator verdict for every pair.
    """

    evaluation_sets: list[RecallEvaluationSet]
    outputs: MethodOutputs

    @model_validator(mode="after")
    def _check_coverage(self) -> "RecallAnalysisInput":
        ids = [s.reference_set_id for s in self.evaluation_sets]
        if len(ids) != len(set(ids)):
            dupes = sorted({x for x in ids if ids.count(x) > 1})
            raise ValueError(f"duplicate reference_set_id(s): {dupes}")
        for eval_set in self.evaluation_sets:
            for pair in eval_set.pairs:
                if pair.pair_id not in self.outputs.adjudicator:
                    raise ValueError(
                        f"adjudicator output missing for recall pair "
                        f"{pair.pair_id!r} in set {eval_set.reference_set_id!r}. "
                        f"The adjudicator must be run on every sampled pair "
                        f"(complete candidate coverage)."
                    )
                if pair.pair_id not in self.outputs.vigimatch_retrieved:
                    raise ValueError(
                        f"vigimatch_retrieved flag missing for recall pair "
                        f"{pair.pair_id!r} in set {eval_set.reference_set_id!r}."
                    )
        return self


# --- Shared uniqueness validators ------------------------------------------


def _validate_pair_uniqueness(
    pairs: list[PrecisionPairRecord] | list[RecallPairRecord],
) -> None:
    """Reject a repeated ``pair_id`` or a repeated unordered report-id pair."""
    seen_ids: set[str] = set()
    seen_unordered: dict[frozenset[str], str] = {}
    for pair in pairs:
        if pair.pair_id in seen_ids:
            raise ValueError(f"duplicate pair_id {pair.pair_id!r}")
        seen_ids.add(pair.pair_id)
        key = _unordered_key(pair.report_id1, pair.report_id2)
        if key in seen_unordered:
            raise ValueError(
                f"the same two reports appear twice: {pair.pair_id!r} and "
                f"{seen_unordered[key]!r} are the same unordered pair "
                f"(orientation flipped)."
            )
        seen_unordered[key] = pair.pair_id


def _validate_one_pair_per_cluster(
    pairs: list[PrecisionPairRecord] | list[RecallPairRecord],
    *,
    required: bool,
) -> None:
    """Reject more than one pair sharing a (non-null) ``cluster_id``.

    When ``required`` is True a null ``cluster_id`` is itself an error.
    """
    seen: dict[str, str] = {}
    for pair in pairs:
        cluster_id = pair.cluster_id
        if cluster_id is None:
            if required:
                raise ValueError(
                    f"pair {pair.pair_id!r} has no cluster_id, but this set "
                    f"requires one per pair."
                )
            continue
        if cluster_id in seen:
            raise ValueError(
                f"cluster {cluster_id!r} contributes more than one pair: "
                f"{seen[cluster_id]!r} and {pair.pair_id!r}. Sampling draws at "
                f"most one pair per cluster."
            )
        seen[cluster_id] = pair.pair_id


# ``SamplingRecord`` is imported at the top from ``.records``; the set models
# reference it via a string annotation, so resolve those forward refs now.
PrecisionEvaluationSet.model_rebuild()
RecallEvaluationSet.model_rebuild()


__all__ = [
    "ReferenceLabel",
    "ADJUDICATOR_LABELS",
    "KNOWN_REFERENCE_SETS",
    "PrecisionPairRecord",
    "PrecisionEvaluationSet",
    "RecallPairRecord",
    "RecallEvaluationSet",
    "AdjudicatorOutput",
    "MethodOutputs",
    "PrecisionAnalysisInput",
    "RecallAnalysisInput",
    "SamplingRecord",
]
