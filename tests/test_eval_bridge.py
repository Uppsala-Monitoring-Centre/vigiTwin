"""Tests for the eval bridge between new pipeline output and eval framework."""

from llm_dup_dev.eval.bridge import evaluate_batch, evaluate_results, to_eval_dict
from llm_dup_dev.models.classification import ClassificationResult
from llm_dup_dev.models.enums import (
    AnchorType,
    DuplicateRelationship,
    IndependenceAssessment,
    NarrativeConsistency,
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
    independence_assessment: IndependenceAssessment,
    duplicate_relationship: DuplicateRelationship | None = None,
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
        independence_assessment=independence_assessment,
        duplicate_relationship=duplicate_relationship,
        explanation="test",
    )
    return PairResult(
        pair_id=pair_id,
        features=features,
        classification=classification,
    )


def _make_duplicates(pair_id: str) -> PairResult:
    return _make_pair_result(
        pair_id,
        IndependenceAssessment.NON_INDEPENDENT,
        DuplicateRelationship.DUPLICATES,
    )


def _make_otherwise_related(pair_id: str) -> PairResult:
    return _make_pair_result(
        pair_id,
        IndependenceAssessment.NON_INDEPENDENT,
        DuplicateRelationship.OTHERWISE_RELATED,
    )


def _make_independent(pair_id: str) -> PairResult:
    return _make_pair_result(pair_id, IndependenceAssessment.INDEPENDENT)


def _make_abstain(pair_id: str) -> PairResult:
    return _make_pair_result(pair_id, IndependenceAssessment.ABSTAIN)


def _make_annotation(
    pair_id: str,
    duplicates_assessment: str,
) -> dict:
    return {
        "pair_id": pair_id,
        "duplicates_assessment": duplicates_assessment,
    }


# ===================================================================
# to_eval_dict
# ===================================================================

class TestToEvalDict:

    def test_duplicates(self):
        r = _make_duplicates("p1")
        d = to_eval_dict(r)
        assert d["pair_id"] == "p1"
        assert d["final_label"] == "Duplicates"

    def test_otherwise_related(self):
        r = _make_otherwise_related("p2")
        d = to_eval_dict(r)
        assert d["final_label"] == "Otherwise_related"

    def test_independent(self):
        r = _make_independent("p3")
        d = to_eval_dict(r)
        assert d["final_label"] == "Unrelated"

    def test_abstain(self):
        r = _make_abstain("p4")
        d = to_eval_dict(r)
        assert d["final_label"] == "Abstain"

    def test_dict_is_minimal(self):
        """to_eval_dict should only contain pair_id and final_label."""
        r = _make_duplicates("p1")
        d = to_eval_dict(r)
        assert set(d.keys()) == {"pair_id", "final_label"}


# ===================================================================
# evaluate_results
# ===================================================================

class TestEvaluateResults:

    def test_basic_evaluation(self):
        results = [
            _make_duplicates("p1"),
            _make_independent("p2"),
        ]
        annotations = [
            _make_annotation("p1", "duplicates"),
            _make_annotation("p2", "unrelated"),
        ]
        summary = evaluate_results(results, annotations, experiment_id="test")
        assert summary.experiment_id == "test"
        assert summary.n_pairs == 2
        assert summary.final_accuracy == 1.0  # both correct

    def test_incorrect_prediction(self):
        results = [_make_duplicates("p1")]
        annotations = [_make_annotation("p1", "unrelated")]
        summary = evaluate_results(results, annotations)
        assert summary.final_accuracy == 0.0

    def test_abstain_annotations_excluded(self):
        results = [
            _make_duplicates("p1"),
            _make_duplicates("p2"),
        ]
        annotations = [
            _make_annotation("p1", "duplicates"),
            _make_annotation("p2", "abstain"),
        ]
        summary = evaluate_results(results, annotations)
        assert summary.n_pairs == 1
        assert summary.n_abstained == 1

    def test_otherwise_related_label_normalisation(self):
        """Pipeline uses 'Otherwise_related', annotation uses 'otherwise_related'."""
        results = [_make_otherwise_related("p1")]
        annotations = [_make_annotation("p1", "otherwise_related")]
        summary = evaluate_results(results, annotations)
        assert summary.final_accuracy == 1.0

    def test_config_snapshot_preserved(self):
        results = [_make_duplicates("p1")]
        annotations = [_make_annotation("p1", "duplicates")]
        config = {"model": "v2", "thresholds": {"strong": 3.0}}
        summary = evaluate_results(
            results, annotations, config_snapshot=config,
        )
        assert summary.config_snapshot == config

    def test_no_per_field_accuracies(self):
        """EvalSummary should not have per-field accuracy attributes."""
        results = [_make_duplicates("p1")]
        annotations = [_make_annotation("p1", "duplicates")]
        summary = evaluate_results(results, annotations)
        assert not hasattr(summary, "same_patient_accuracy")
        assert not hasattr(summary, "ae_consistency_accuracy")
        assert not hasattr(summary, "cluster_accuracy")
        assert not hasattr(summary, "final_certainty_weighted_accuracy")


# ===================================================================
# evaluate_batch
# ===================================================================

class TestEvaluateBatch:

    def test_batch_evaluation(self):
        batch = BatchResult(
            results=[
                _make_duplicates("p1"),
                _make_independent("p2"),
            ]
        )
        annotations = [
            _make_annotation("p1", "duplicates"),
            _make_annotation("p2", "unrelated"),
        ]
        summary = evaluate_batch(batch, annotations, experiment_id="batch_test")
        assert summary.n_pairs == 2
        assert summary.final_accuracy == 1.0

    def test_batch_with_errors_only_evaluates_successes(self):
        batch = BatchResult(
            results=[_make_duplicates("p1")],
            errors=[("p_bad", KeyError("missing"))],
        )
        annotations = [
            _make_annotation("p1", "duplicates"),
            _make_annotation("p_bad", "unrelated"),
        ]
        summary = evaluate_batch(batch, annotations)
        assert summary.n_pairs == 1  # only p1 evaluated

    def test_empty_batch(self):
        batch = BatchResult()
        summary = evaluate_batch(batch, [])
        assert summary.n_pairs == 0
