"""Tests for the eval bridge between new pipeline output and eval framework."""

from llm_dup_dev.eval.bridge import evaluate_batch, evaluate_results, to_eval_dict
from llm_dup_dev.eval.labels import AbstainMode
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
        # Primary binary view reports perfect separation on these two pairs.
        assert summary.nonduplicates_vs_duplicates.precision == 1.0
        assert summary.nonduplicates_vs_duplicates.recall == 1.0

    def test_incorrect_prediction(self):
        # Predicted Duplicates, actual Unrelated.
        # In the primary view (positive = Non_duplicates) this is a
        # false negative — the actual positive was missed. Precision
        # is undefined (no predicted positives) so falls back to 0.0;
        # recall is also 0.0 since the one actual positive was missed.
        results = [_make_duplicates("p1")]
        annotations = [_make_annotation("p1", "unrelated")]
        summary = evaluate_results(results, annotations)
        assert summary.nonduplicates_vs_duplicates.precision == 0.0
        assert summary.nonduplicates_vs_duplicates.recall == 0.0

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
        # 3x3 diagonal cell for Otherwise_related should be 1.
        cm = summary.final_confusion
        or_idx = cm.labels.index("Otherwise_related")
        assert cm.matrix[or_idx][or_idx] == 1
        # Primary view (positive = Non_duplicates): this is a true positive.
        primary = summary.nonduplicates_vs_duplicates
        assert primary.n_pairs == 1
        assert primary.confusion.matrix[0][0] == 1  # TP cell
        assert primary.precision == 1.0
        assert primary.recall == 1.0

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
        assert summary.nonduplicates_vs_duplicates.precision == 1.0
        assert summary.nonduplicates_vs_duplicates.recall == 1.0

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


# ===================================================================
# Pipeline Abstain handling — AbstainMode
# ===================================================================

class TestPipelineAbstainHandling:
    """Pipeline-produced Abstain results are handled per AbstainMode."""

    def _make_abstain_pipeline_result(self) -> PairResult:
        return _make_pair_result(
            "p_abstain",
            IndependenceAssessment.NON_INDEPENDENT,
            DuplicateRelationship.ABSTAIN,
        )

    def test_decided_only_excludes_pipeline_abstain(self):
        """DECIDED_ONLY: pipeline abstain excluded from n_pairs,
        counted in n_pipeline_abstained."""
        results = [
            _make_duplicates("p1"),
            self._make_abstain_pipeline_result(),
        ]
        annotations = [
            _make_annotation("p1", "duplicates"),
            _make_annotation("p_abstain", "duplicates"),
        ]
        summary = evaluate_results(
            results, annotations, abstain_mode=AbstainMode.DECIDED_ONLY
        )
        assert summary.n_pairs == 1
        assert summary.n_pipeline_abstained == 1

    def test_decided_only_is_default(self):
        """DECIDED_ONLY is the default mode."""
        results = [self._make_abstain_pipeline_result()]
        annotations = [_make_annotation("p_abstain", "duplicates")]
        summary = evaluate_results(results, annotations)
        assert summary.n_pipeline_abstained == 1
        assert summary.n_pairs == 0

    def test_abstain_as_duplicate_counts_in_n_pairs(self):
        """ABSTAIN_AS_DUPLICATE: pipeline abstain remapped to Duplicates,
        counted in n_pairs."""
        results = [self._make_abstain_pipeline_result()]
        annotations = [_make_annotation("p_abstain", "duplicates")]
        summary = evaluate_results(
            results, annotations, abstain_mode=AbstainMode.ABSTAIN_AS_DUPLICATE
        )
        assert summary.n_pairs == 1
        assert summary.n_pipeline_abstained == 1
        # Remapped to Duplicates vs actual Duplicates → TP in complement view
        assert summary.duplicates_vs_nonduplicates.true_positives == 1

    def test_abstain_as_nonduplicate_counts_in_n_pairs(self):
        """ABSTAIN_AS_NONDUPLICATE: pipeline abstain remapped to
        Otherwise_related, counted in n_pairs."""
        results = [self._make_abstain_pipeline_result()]
        annotations = [_make_annotation("p_abstain", "unrelated")]
        summary = evaluate_results(
            results, annotations, abstain_mode=AbstainMode.ABSTAIN_AS_NONDUPLICATE
        )
        assert summary.n_pairs == 1
        assert summary.n_pipeline_abstained == 1
        # Remapped to Otherwise_related vs actual Unrelated → TP in primary view
        # (both collapse to Non_duplicates)
        assert summary.nonduplicates_vs_duplicates.true_positives == 1

    def test_no_pipeline_abstains_yields_zero(self):
        """n_pipeline_abstained is 0 when no pipeline abstains present."""
        results = [_make_duplicates("p1")]
        annotations = [_make_annotation("p1", "duplicates")]
        summary = evaluate_results(results, annotations)
        assert summary.n_pipeline_abstained == 0

    def test_annotation_abstain_does_not_increment_pipeline_count(self):
        """n_pipeline_abstained is only incremented for pipeline abstains,
        not annotation abstains."""
        results = [_make_duplicates("p1")]
        annotations = [_make_annotation("p1", "abstain")]
        summary = evaluate_results(results, annotations)
        assert summary.n_abstained == 1
        assert summary.n_pipeline_abstained == 0

    def test_both_abstain_types_independent(self):
        """Both annotation and pipeline abstains can occur simultaneously."""
        results = [
            _make_duplicates("p1"),
            self._make_abstain_pipeline_result(),
        ]
        annotations = [
            _make_annotation("p1", "abstain"),  # annotation abstain
            _make_annotation("p_abstain", "duplicates"),  # pipeline abstain
        ]
        summary = evaluate_results(results, annotations)
        assert summary.n_abstained == 1
        assert summary.n_pipeline_abstained == 1
        assert summary.n_pairs == 0  # both excluded in DECIDED_ONLY mode

    def test_abstain_mode_batch_passthrough(self):
        """evaluate_batch passes abstain_mode through correctly."""
        batch = BatchResult(
            results=[
                _make_duplicates("p1"),
                self._make_abstain_pipeline_result(),
            ]
        )
        annotations = [
            _make_annotation("p1", "duplicates"),
            _make_annotation("p_abstain", "duplicates"),
        ]
        summary = evaluate_batch(
            batch, annotations, abstain_mode=AbstainMode.ABSTAIN_AS_DUPLICATE
        )
        assert summary.n_pipeline_abstained == 1
        assert summary.n_pairs == 2
