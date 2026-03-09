"""Tests for the eval bridge between new pipeline output and legacy eval framework."""

import pytest

from llm_dup_dev.eval.bridge import evaluate_batch, evaluate_results, to_eval_dict
from llm_dup_dev.models.classification import ClassificationResult
from llm_dup_dev.models.enums import (
    AnchorType,
    NarrativeConsistency,
    PrimaryClassification,
    RelationshipClassification,
    ReportingContextLevel,
)
from llm_dup_dev.models.pair_features import (
    AnchorPopulation,
    InconsistencyCounts,
    PairFeatures,
    ProbabilityEstimate,
    ReportingContext,
)
from llm_dup_dev.pipeline.runner import BatchResult, PairResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pair_result(
    pair_id: str,
    primary: PrimaryClassification,
    relationship: RelationshipClassification | None = None,
) -> PairResult:
    """Build a minimal PairResult for eval bridge testing."""
    features = PairFeatures(
        pair_id=pair_id,
        reporting_context=ReportingContext(level=ReportingContextLevel.GLOBAL),
        anchor=AnchorPopulation(
            N_drugs=100, N_AEs=100, N_drugs_AEs=50,
            type=AnchorType.DRUGS_AES, N_anchor=50,
        ),
        probability=ProbabilityEstimate(p_joint=1e-5, expected_collisions=0.01),
        L_match=4.0,
        inconsistency_counts=InconsistencyCounts(),
        narrative_consistency=NarrativeConsistency.NEUTRAL,
    )
    classification = ClassificationResult(
        pair_id=pair_id,
        primary=primary,
        relationship=relationship,
        explanation="test",
    )
    return PairResult(
        pair_id=pair_id,
        features=features,
        classification=classification,
    )


def _make_annotation(
    pair_id: str,
    assessment: str,
    same_individual: str = "same",
    certainty: float = 80.0,
) -> dict:
    return {
        "pair_id": pair_id,
        "same_individual": same_individual,
        "ae_consistency": "consistent",
        "cluster_assessment": "no_evidence_of_cluster",
        "assessment": assessment,
        "certainty": certainty,
    }


# ===================================================================
# to_eval_dict
# ===================================================================

class TestToEvalDict:

    def test_same_individual(self):
        r = _make_pair_result("p1", PrimaryClassification.SAME_INDIVIDUAL)
        d = to_eval_dict(r)
        assert d["pair_id"] == "p1"
        assert d["final_label"] == "Duplicates"
        assert d["same_patient_json"]["same_individual_assessment"] == "same"

    def test_different_related(self):
        r = _make_pair_result(
            "p2",
            PrimaryClassification.DIFFERENT_INDIVIDUALS,
            RelationshipClassification.RELATED,
        )
        d = to_eval_dict(r)
        assert d["final_label"] == "Otherwise_related"
        assert d["same_patient_json"]["same_individual_assessment"] == "different"

    def test_different_unrelated(self):
        r = _make_pair_result(
            "p3",
            PrimaryClassification.DIFFERENT_INDIVIDUALS,
            RelationshipClassification.UNRELATED,
        )
        d = to_eval_dict(r)
        assert d["final_label"] == "Unrelated"
        assert d["same_patient_json"]["same_individual_assessment"] == "different"

    def test_abstain(self):
        r = _make_pair_result("p4", PrimaryClassification.ABSTAIN)
        d = to_eval_dict(r)
        assert d["final_label"] == "Abstain"
        assert d["same_patient_json"]["same_individual_assessment"] == "unclear"

    def test_ae_and_cluster_not_assessed(self):
        """New pipeline doesn't produce these — should be sentinel values."""
        r = _make_pair_result("p1", PrimaryClassification.SAME_INDIVIDUAL)
        d = to_eval_dict(r)
        assert d["ae_json"]["ae_consistency"] == "_not_assessed"
        assert d["clustering_json"]["cluster_assessment"] == "_not_assessed"


# ===================================================================
# evaluate_results
# ===================================================================

class TestEvaluateResults:

    def test_basic_evaluation(self):
        results = [
            _make_pair_result("p1", PrimaryClassification.SAME_INDIVIDUAL),
            _make_pair_result(
                "p2",
                PrimaryClassification.DIFFERENT_INDIVIDUALS,
                RelationshipClassification.UNRELATED,
            ),
        ]
        annotations = [
            _make_annotation("p1", "Duplicates", "same"),
            _make_annotation("p2", "Unrelated", "different"),
        ]
        summary = evaluate_results(results, annotations, experiment_id="test")
        assert summary.experiment_id == "test"
        assert summary.n_pairs == 2
        assert summary.final_accuracy == 1.0  # both correct

    def test_incorrect_prediction(self):
        results = [
            _make_pair_result("p1", PrimaryClassification.SAME_INDIVIDUAL),
        ]
        annotations = [
            _make_annotation("p1", "Unrelated", "different"),
        ]
        summary = evaluate_results(results, annotations)
        assert summary.final_accuracy == 0.0

    def test_abstain_annotations_excluded(self):
        results = [
            _make_pair_result("p1", PrimaryClassification.SAME_INDIVIDUAL),
            _make_pair_result("p2", PrimaryClassification.SAME_INDIVIDUAL),
        ]
        annotations = [
            _make_annotation("p1", "Duplicates", "same"),
            _make_annotation("p2", "Abstain", "unclear"),
        ]
        summary = evaluate_results(results, annotations)
        assert summary.n_pairs == 1
        assert summary.n_abstained == 1

    def test_same_patient_accuracy(self):
        """same_individual field should match when pipeline agrees."""
        results = [
            _make_pair_result("p1", PrimaryClassification.SAME_INDIVIDUAL),
        ]
        annotations = [
            _make_annotation("p1", "Duplicates", "same"),
        ]
        summary = evaluate_results(results, annotations)
        assert summary.same_patient_accuracy == 1.0

    def test_ae_and_cluster_accuracy_zero(self):
        """New pipeline returns sentinels → per-field accuracy should be 0."""
        results = [
            _make_pair_result("p1", PrimaryClassification.SAME_INDIVIDUAL),
        ]
        annotations = [
            _make_annotation("p1", "Duplicates", "same"),
        ]
        summary = evaluate_results(results, annotations)
        assert summary.ae_consistency_accuracy == 0.0
        assert summary.cluster_accuracy == 0.0

    def test_config_snapshot_preserved(self):
        results = [_make_pair_result("p1", PrimaryClassification.SAME_INDIVIDUAL)]
        annotations = [_make_annotation("p1", "Duplicates")]
        config = {"model": "v2", "thresholds": {"strong": 3.0}}
        summary = evaluate_results(
            results, annotations, config_snapshot=config,
        )
        assert summary.config_snapshot == config


# ===================================================================
# evaluate_batch
# ===================================================================

class TestEvaluateBatch:

    def test_batch_evaluation(self):
        batch = BatchResult(
            results=[
                _make_pair_result("p1", PrimaryClassification.SAME_INDIVIDUAL),
                _make_pair_result(
                    "p2",
                    PrimaryClassification.DIFFERENT_INDIVIDUALS,
                    RelationshipClassification.UNRELATED,
                ),
            ]
        )
        annotations = [
            _make_annotation("p1", "Duplicates"),
            _make_annotation("p2", "Unrelated"),
        ]
        summary = evaluate_batch(batch, annotations, experiment_id="batch_test")
        assert summary.n_pairs == 2
        assert summary.final_accuracy == 1.0

    def test_batch_with_errors_only_evaluates_successes(self):
        batch = BatchResult(
            results=[
                _make_pair_result("p1", PrimaryClassification.SAME_INDIVIDUAL),
            ],
            errors=[("p_bad", KeyError("missing"))],
        )
        annotations = [
            _make_annotation("p1", "Duplicates"),
            _make_annotation("p_bad", "Unrelated"),
        ]
        summary = evaluate_batch(batch, annotations)
        assert summary.n_pairs == 1  # only p1 evaluated

    def test_empty_batch(self):
        batch = BatchResult()
        summary = evaluate_batch(batch, [])
        assert summary.n_pairs == 0
