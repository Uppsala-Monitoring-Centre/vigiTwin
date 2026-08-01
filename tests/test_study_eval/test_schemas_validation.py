"""Tests for the generic input-schema validators."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from llm_dup_dev.study_eval.schemas import (
    PairPrediction,
    PredictionSet,
    ReferencePair,
    ReferenceSet,
)


def _pair(pid, r1, r2, ref=None, cluster=None):
    return ReferencePair(
        pair_id=pid, report_id1=r1, report_id2=r2, reference_label=ref, cluster_id=cluster
    )


def test_pair_id_must_match_report_ids():
    with pytest.raises(ValidationError):
        _pair("WRONG", "R1", "R2")


def test_precision_set_requires_reference_label():
    with pytest.raises(ValidationError, match="needs a reference_label"):
        ReferenceSet(set_id="p", role="precision", pairs=[_pair("R1__R2", "R1", "R2")])


def test_recall_set_rejects_non_duplicate_label():
    with pytest.raises(ValidationError, match="positive controls"):
        ReferenceSet(
            set_id="r",
            role="recall",
            pairs=[_pair("R1__R2", "R1", "R2", ref="non_duplicate")],
        )


def test_recall_set_allows_missing_or_duplicate_label():
    rset = ReferenceSet(
        set_id="r",
        role="recall",
        pairs=[
            _pair("R1__R2", "R1", "R2"),
            _pair("R3__R4", "R3", "R4", ref="duplicate"),
        ],
    )
    assert len(rset.pairs) == 2


def test_duplicate_pair_id_rejected():
    with pytest.raises(ValidationError, match="duplicate pair_id"):
        ReferenceSet(
            set_id="p",
            role="precision",
            pairs=[
                _pair("R1__R2", "R1", "R2", ref="duplicate"),
                _pair("R1__R2", "R1", "R2", ref="duplicate"),
            ],
        )


def test_flipped_orientation_of_same_pair_rejected():
    """A flipped pair is now rejected at construction, not at set level.

    Report ids must be canonically ordered, so ``("R2", "R1")`` never becomes a
    valid ``ReferencePair`` in the first place.
    """
    with pytest.raises(ValidationError, match="not in canonical order"):
        ReferenceSet(
            set_id="p",
            role="precision",
            pairs=[
                _pair("R1__R2", "R1", "R2", ref="duplicate"),
                _pair("R2__R1", "R2", "R1", ref="duplicate"),
            ],
        )


def test_same_two_reports_twice_rejected_as_duplicate_pair_id():
    """Canonical ids mean both orientations collide on pair_id.

    This is what replaced the separate unordered-pair check: two rows naming
    the same two reports canonicalise to one id, so the duplicate-pair_id
    branch catches them.
    """
    with pytest.raises(ValidationError, match="duplicate pair_id"):
        ReferenceSet(
            set_id="p",
            role="precision",
            pairs=[
                _pair("R1__R2", "R1", "R2", ref="duplicate"),
                _pair("R1__R2", "R1", "R2", ref="non_duplicate"),
            ],
        )


def test_self_pair_rejected():
    with pytest.raises(ValidationError, match="two distinct reports"):
        _pair("R1__R1", "R1", "R1", ref="duplicate")


def test_numeric_report_ids_must_be_ordered_numerically():
    """Lexicographic ordering would have accepted "10" before "9"."""
    with pytest.raises(ValidationError, match="not in canonical order"):
        _pair("10__9", "10", "9", ref="duplicate")

    assert _pair("9__10", "9", "10", ref="duplicate").pair_id == "9__10"


def test_two_pairs_same_cluster_rejected():
    with pytest.raises(ValidationError, match="more than one pair"):
        ReferenceSet(
            set_id="p",
            role="precision",
            pairs=[
                _pair("R1__R2", "R1", "R2", ref="duplicate", cluster="C1"),
                _pair("R3__R4", "R3", "R4", ref="duplicate", cluster="C1"),
            ],
        )


def test_screening_mode_defaults_to_observed():
    """not_evaluated is never inferred; it has to be selected explicitly."""
    rset = ReferenceSet(set_id="r", role="recall", pairs=[_pair("R1__R2", "R1", "R2")])
    assert rset.screening_mode == "observed"


def test_recall_set_accepts_not_evaluated():
    rset = ReferenceSet(
        set_id="r",
        role="recall",
        screening_mode="not_evaluated",
        pairs=[_pair("R1__R2", "R1", "R2")],
    )
    assert rset.screening_mode == "not_evaluated"


def test_precision_set_rejects_not_evaluated():
    """A precision set *is* the screening candidate set — the two contradict."""
    with pytest.raises(ValidationError, match="contradictory for a precision set"):
        ReferenceSet(
            set_id="p",
            role="precision",
            screening_mode="not_evaluated",
            pairs=[_pair("R1__R2", "R1", "R2", ref="duplicate")],
        )


def test_unknown_screening_mode_rejected():
    with pytest.raises(ValidationError):
        ReferenceSet(
            set_id="r",
            role="recall",
            screening_mode="assume_perfect",
            pairs=[_pair("R1__R2", "R1", "R2")],
        )


def test_screening_retrieved_may_be_omitted():
    """A prediction bundle need carry no screening verdict at all."""
    prediction = PairPrediction(pair_id="R1__R2", adjudicator_label="Duplicates")
    assert prediction.screening_retrieved is None


def test_prediction_rejects_unknown_label():
    with pytest.raises(ValidationError):
        PairPrediction(pair_id="R1__R2", adjudicator_label="Bananas", screening_retrieved=True)


def test_prediction_set_key_must_match_pair_id():
    with pytest.raises(ValidationError, match="dict key and the record"):
        PredictionSet(
            predictions={
                "R1__R2": PairPrediction(
                    pair_id="X__Y", adjudicator_label="Duplicates", screening_retrieved=True
                )
            }
        )


def test_require_coverage_reports_missing():
    ps = PredictionSet(
        predictions={
            "R1__R2": PairPrediction(
                pair_id="R1__R2", adjudicator_label="Duplicates", screening_retrieved=True
            )
        }
    )
    with pytest.raises(ValueError, match="prediction missing"):
        ps.require_coverage(["R1__R2", "R3__R4"], context="test")
