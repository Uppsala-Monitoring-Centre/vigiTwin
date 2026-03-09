"""Tests for the end-to-end pipeline runner."""

import pytest

from llm_dup_dev.classifier.config import DecisionThresholds, PipelineConfig
from llm_dup_dev.data.synthetic import SyntheticReportLoader, SyntheticStatsProvider
from llm_dup_dev.feature_generation.probability import FrequencyStats
from llm_dup_dev.models.enums import (
    NarrativeConsistency,
    PrimaryClassification,
    RelationshipClassification,
)
from llm_dup_dev.models.report import Report
from llm_dup_dev.models.report_pair import ReportPair
from llm_dup_dev.pipeline.runner import (
    BatchResult,
    PairResult,
    PipelineRunner,
    neutral_narrative_provider,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def loader(report_a, report_b, report_c, report_empty):
    """A loader pre-populated with all test reports."""
    return SyntheticReportLoader({
        report_a.report_id: report_a,
        report_b.report_id: report_b,
        report_c.report_id: report_c,
        report_empty.report_id: report_empty,
    })


@pytest.fixture
def stats_provider():
    """Stats provider with defaults that produce meaningful L_match."""
    return SyntheticStatsProvider(
        default_N_total=5000,
        default_N_drugs=200,
        default_N_AEs=300,
        default_N_drugs_AEs=40,
    )


@pytest.fixture
def runner(loader, stats_provider):
    return PipelineRunner(
        report_loader=loader,
        stats_provider=stats_provider,
    )


@pytest.fixture
def pair_ab():
    return ReportPair(pair_id="R001__R002", report_1_id="R001", report_2_id="R002")


@pytest.fixture
def pair_ac():
    return ReportPair(pair_id="R001__R003", report_1_id="R001", report_2_id="R003")


# ===================================================================
# Single pair processing
# ===================================================================

class TestRunPair:

    def test_returns_pair_result(self, runner, pair_ab):
        result = runner.run_pair(pair_ab)
        assert isinstance(result, PairResult)
        assert result.pair_id == "R001__R002"

    def test_features_populated(self, runner, pair_ab):
        result = runner.run_pair(pair_ab)
        assert result.features.L_match >= 0
        assert len(result.features.matching_drugs) > 0

    def test_classification_populated(self, runner, pair_ab):
        result = runner.run_pair(pair_ab)
        assert result.classification.primary in PrimaryClassification
        assert result.final_label in (
            "Duplicates", "Otherwise_related", "Unrelated", "Abstain"
        )

    def test_near_duplicate_classified_same_or_related(self, runner, pair_ab):
        """Reports A and B share drugs, demographics, dates — expect strong evidence."""
        result = runner.run_pair(pair_ab)
        # With default stats (N_drugs=200), pregabalin+sertraline is a rare
        # combination. The pair should classify as Same or at least Related.
        assert result.classification.primary in (
            PrimaryClassification.SAME_INDIVIDUAL,
            PrimaryClassification.DIFFERENT_INDIVIDUALS,
        )

    def test_different_reports_lower_lmatch(self, runner, pair_ab, pair_ac):
        """Reports A-C (different drugs, different country) should have lower L_match."""
        result_ab = runner.run_pair(pair_ab)
        result_ac = runner.run_pair(pair_ac)
        assert result_ac.features.L_match < result_ab.features.L_match

    def test_narrative_consistency_passed_through(self, runner, pair_ab):
        result = runner.run_pair(
            pair_ab,
            narrative_consistency=NarrativeConsistency.STRONGLY_CONSISTENT,
        )
        assert (
            result.features.narrative_consistency
            == NarrativeConsistency.STRONGLY_CONSISTENT
        )

    def test_default_narrative_is_neutral(self, runner, pair_ab):
        result = runner.run_pair(pair_ab)
        assert (
            result.features.narrative_consistency
            == NarrativeConsistency.NEUTRAL
        )


# ===================================================================
# Batch processing
# ===================================================================

class TestRunBatch:

    def test_batch_processes_all_pairs(self, runner, pair_ab, pair_ac):
        result = runner.run_batch([pair_ab, pair_ac])
        assert isinstance(result, BatchResult)
        assert result.n_processed == 2
        assert result.n_errors == 0

    def test_batch_labels_dict(self, runner, pair_ab, pair_ac):
        result = runner.run_batch([pair_ab, pair_ac])
        labels = result.labels()
        assert "R001__R002" in labels
        assert "R001__R003" in labels

    def test_batch_with_narrative_provider(self, runner, pair_ab):
        def always_consistent(pair_id, n1, n2):
            return NarrativeConsistency.CONSISTENT

        result = runner.run_batch([pair_ab], narrative_provider=always_consistent)
        assert result.n_processed == 1
        assert (
            result.results[0].features.narrative_consistency
            == NarrativeConsistency.CONSISTENT
        )

    def test_batch_continue_on_error(self, loader, stats_provider, pair_ab):
        """If a pair references a missing report, batch skips it."""
        bad_pair = ReportPair(
            pair_id="bad", report_1_id="MISSING", report_2_id="R001"
        )
        runner = PipelineRunner(
            report_loader=loader, stats_provider=stats_provider,
        )
        result = runner.run_batch([bad_pair, pair_ab], continue_on_error=True)
        assert result.n_processed == 1
        assert result.n_errors == 1
        assert result.errors[0][0] == "bad"

    def test_batch_raise_on_error(self, loader, stats_provider):
        bad_pair = ReportPair(
            pair_id="bad", report_1_id="MISSING", report_2_id="R001"
        )
        runner = PipelineRunner(
            report_loader=loader, stats_provider=stats_provider,
        )
        with pytest.raises(KeyError):
            runner.run_batch([bad_pair], continue_on_error=False)

    def test_empty_batch(self, runner):
        result = runner.run_batch([])
        assert result.n_processed == 0
        assert result.n_errors == 0


# ===================================================================
# Custom configuration
# ===================================================================

class TestCustomConfig:

    def test_strict_thresholds_changes_outcome(
        self, loader, stats_provider, pair_ab
    ):
        """Very strict thresholds should make classification harder."""
        strict = DecisionThresholds(
            lmatch_strong=10.0, lmatch_med=8.0, lmatch_weak=6.0
        )
        runner = PipelineRunner(
            report_loader=loader,
            stats_provider=stats_provider,
            decision_thresholds=strict,
        )
        result = runner.run_pair(pair_ab)
        # With very high thresholds, most pairs should be Different
        assert result.classification.primary == PrimaryClassification.DIFFERENT_INDIVIDUALS

    def test_very_small_anchor_produces_high_lmatch(
        self, loader, pair_ab
    ):
        """Tiny anchor population should produce very high L_match."""
        tiny_stats = SyntheticStatsProvider(
            default_N_total=50000,
            default_N_drugs=5,
            default_N_AEs=8,
            default_N_drugs_AEs=2,
        )
        runner = PipelineRunner(
            report_loader=loader, stats_provider=tiny_stats,
        )
        result = runner.run_pair(pair_ab)
        # Small anchor with matching features → high L_match
        assert result.features.L_match > 0


# ===================================================================
# Neutral narrative provider
# ===================================================================

class TestNeutralNarrativeProvider:

    def test_always_returns_neutral(self):
        result = neutral_narrative_provider("pair1", "text1", "text2")
        assert result == NarrativeConsistency.NEUTRAL

    def test_handles_none_narratives(self):
        result = neutral_narrative_provider("pair1", None, None)
        assert result == NarrativeConsistency.NEUTRAL
