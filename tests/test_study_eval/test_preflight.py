"""Preflight: screening requirements are conditional on each set's mode."""

from __future__ import annotations

import pytest

from llm_dup_dev.study_eval.preflight import (
    preflight_reference_set,
    preflight_reference_sets,
)
from llm_dup_dev.study_eval.schemas import PairPrediction, PredictionSet
from tests.test_study_eval.conftest import make_precision, make_recall


# --- observed: the strict checks are retained --------------------------------


def test_observed_requires_a_screening_verdict_for_every_pair():
    sets, predictions = make_recall({"r": [(True, "Duplicates"), (None, "Unrelated")]})
    with pytest.raises(ValueError, match="every pair needs a screening_retrieved"):
        preflight_reference_sets(sets, predictions)


def test_observed_precision_rejects_a_non_retrieved_pair():
    reference_set, predictions = make_precision(
        [("duplicate", "Duplicates"), ("non_duplicate", "Unrelated")],
        screening={"A1__B1": False},
    )
    with pytest.raises(ValueError, match="not flagged as screening-retrieved"):
        preflight_reference_set(reference_set, predictions)


def test_observed_set_with_complete_screening_passes():
    sets, predictions = make_recall({"r": [(True, "Duplicates"), (False, "Unrelated")]})
    preflight_reference_sets(sets, predictions)


# --- not_evaluated: screening is not required --------------------------------


def test_not_evaluated_needs_no_screening_verdict():
    sets, predictions = make_recall(
        {"r": [(None, "Duplicates"), (None, "Unrelated")]},
        modes={"r": "not_evaluated"},
    )
    preflight_reference_sets(sets, predictions)


def test_not_evaluated_accepts_supplied_screening_but_warns():
    """Policy: supplied screening data is ignored, never silently."""
    sets, predictions = make_recall(
        {"r": [(True, "Duplicates"), (None, "Unrelated")]},
        modes={"r": "not_evaluated"},
    )
    with pytest.warns(UserWarning, match="is ignored"):
        preflight_reference_sets(sets, predictions)


def test_not_evaluated_still_validates_unrelated_inputs():
    """Coverage is a hard error regardless of the screening mode."""
    sets, predictions = make_recall(
        {"r": [(None, "Duplicates")]}, modes={"r": "not_evaluated"}
    )
    predictions = PredictionSet(predictions={})
    with pytest.raises(ValueError, match="prediction missing for 1 pair"):
        preflight_reference_sets(sets, predictions)


# --- mixed bundles -----------------------------------------------------------


def test_a_pair_may_belong_to_sets_of_both_modes():
    """The mode states the estimand, not whether screening data exist."""
    observed, predictions = make_recall({"obs": [(True, "Duplicates")]})
    shared_id = observed[0].pairs[0].pair_id
    not_evaluated, _ = make_recall(
        {"obs": [(True, "Duplicates")]}, modes={"obs": "not_evaluated"}
    )
    not_evaluated[0].set_id = "ne"

    with pytest.warns(UserWarning, match="is ignored"):
        preflight_reference_sets([observed[0], not_evaluated[0]], predictions)
    assert predictions.predictions[shared_id].screening_retrieved is True


def test_a_single_role_bundle_is_accepted():
    """Precision-only and recall-only are supported configurations."""
    precision_set, ppredictions = make_precision([("duplicate", "Duplicates")])
    preflight_reference_sets([precision_set], ppredictions)

    recall_sets, rpredictions = make_recall({"r": [(True, "Duplicates")]})
    preflight_reference_sets(recall_sets, rpredictions)


def test_an_empty_bundle_is_rejected():
    """Nothing to measure is an input error, not a successful empty run."""
    with pytest.raises(ValueError, match="no reference sets supplied"):
        preflight_reference_sets([], PredictionSet(predictions={}))


def test_duplicate_set_ids_are_rejected():
    sets, predictions = make_recall({"r": [(True, "Duplicates")]})
    with pytest.raises(ValueError, match="duplicate reference set id"):
        preflight_reference_sets([sets[0], sets[0]], predictions)


def test_every_problem_is_reported_together():
    """One run surfaces all blocking issues, not just the first."""
    sets, predictions = make_recall(
        {"a": [(None, "Duplicates")], "b": [(None, "Unrelated")]}
    )
    with pytest.raises(ValueError) as excinfo:
        preflight_reference_sets(sets, predictions)
    message = str(excinfo.value)
    assert "recall set 'a'" in message
    assert "recall set 'b'" in message


def test_missing_prediction_is_not_also_reported_as_missing_screening():
    """A pair with no prediction has one cause, and it is reported once."""
    sets, _ = make_recall({"r": [(True, "Duplicates"), (True, "Unrelated")]})
    kept = sets[0].pairs[0].pair_id
    predictions = PredictionSet(
        predictions={
            kept: PairPrediction(
                pair_id=kept, adjudicator_label="Duplicates", screening_retrieved=True
            )
        }
    )
    with pytest.raises(ValueError) as excinfo:
        preflight_reference_sets(sets, predictions)
    message = str(excinfo.value)
    assert "prediction missing" in message
    assert "screening_retrieved verdict" not in message
