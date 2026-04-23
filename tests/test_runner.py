"""Tests for the end-to-end pipeline runner."""

import pytest

from llm_dup_dev.classifier.config import DecisionThresholds, PipelineConfig
from llm_dup_dev.data.synthetic import SyntheticReportLoader, SyntheticStatsProvider
from llm_dup_dev.feature_generation.probability import FrequencyStats
from llm_dup_dev.llm.fake import FakeLLMClient
from llm_dup_dev.llm.tasks.ae_clinical_picture import AEClinicalPictureVerdict
from llm_dup_dev.llm.tasks.narrative_comparison import NarrativeVerdict
from llm_dup_dev.models.narrative_source_case import NarrativeSourceCase
from llm_dup_dev.models.enums import (
    IndependenceAssessment,
    NarrativeConsistency,
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
        assert result.classification.independence_assessment in IndependenceAssessment
        assert result.final_label in (
            "Duplicates", "Otherwise_related", "Unrelated", "Abstain"
        )

    def test_near_duplicate_classified_non_independent(self, runner, pair_ab):
        """Reports A and B share drugs, demographics, dates — expect strong evidence."""
        result = runner.run_pair(pair_ab)
        # With default stats (N_drugs=200), pregabalin+sertraline is a rare
        # combination. Independence should be rejected.
        assert (
            result.classification.independence_assessment
            == IndependenceAssessment.NON_INDEPENDENT
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

    def test_default_narrative_is_not_available(self, runner, pair_ab):
        result = runner.run_pair(pair_ab)
        assert (
            result.features.narrative_consistency
            == NarrativeConsistency.NOT_AVAILABLE
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
        # With very high thresholds, evidence is too weak to reject independence
        assert (
            result.classification.independence_assessment
            == IndependenceAssessment.INDEPENDENT
        )

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

    def test_always_returns_not_available(self):
        result = neutral_narrative_provider("pair1", "text1", "text2")
        assert result == NarrativeConsistency.NOT_AVAILABLE

    def test_handles_none_narratives(self):
        result = neutral_narrative_provider("pair1", None, None)
        assert result == NarrativeConsistency.NOT_AVAILABLE


# ===================================================================
# LLM-based narrative integration
# ===================================================================

class TestLLMNarrativeIntegration:

    def test_batch_uses_llm_client_for_narratives(
        self, loader, stats_provider, pair_ab
    ):
        """When llm_client is provided, batch uses it for narrative assessment."""
        fake = FakeLLMClient()
        verdict = NarrativeVerdict(
            consistency=NarrativeConsistency.STRONGLY_CONSISTENT,
            evidence=["Both describe same patient"],
        )
        fake.seed_response(verdict)
        # The runner also makes an AE clinical-picture LLM call and a
        # narrative source/case extraction per report (two reports per
        # pair); seed benign responses so the full chain succeeds.
        fake.seed_response(AEClinicalPictureVerdict(findings=[]))
        fake.seed_response(NarrativeSourceCase())
        fake.seed_response(NarrativeSourceCase())

        runner = PipelineRunner(
            report_loader=loader,
            stats_provider=stats_provider,
            llm_client=fake,
        )
        result = runner.run_batch([pair_ab])

        assert result.n_processed == 1
        assert (
            result.results[0].features.narrative_consistency
            == NarrativeConsistency.STRONGLY_CONSISTENT
        )
        assert result.results[0].narrative_verdict == verdict
        # Four requests per pair: narrative + AE clinical-picture +
        # narrative source/case per report.
        assert len(fake.requests) == 4

    def test_narrative_provider_takes_precedence_over_llm(
        self, loader, stats_provider, pair_ab
    ):
        """Explicit narrative_provider overrides llm_client (for narrative)."""
        fake = FakeLLMClient()
        # Narrative LLM call should NOT happen; AE clinical-picture +
        # narrative source/case per report still do.
        fake.seed_response(AEClinicalPictureVerdict(findings=[]))
        fake.seed_response(NarrativeSourceCase())
        fake.seed_response(NarrativeSourceCase())

        runner = PipelineRunner(
            report_loader=loader,
            stats_provider=stats_provider,
            llm_client=fake,
        )

        def always_inconsistent(pair_id, n1, n2):
            return NarrativeConsistency.INCONSISTENT

        result = runner.run_batch([pair_ab], narrative_provider=always_inconsistent)

        assert (
            result.results[0].features.narrative_consistency
            == NarrativeConsistency.INCONSISTENT
        )
        # Narrative LLM call skipped; AE clinical-picture (1) +
        # narrative source/case (2 reports) = 3 calls.
        assert len(fake.requests) == 3
        # No narrative verdict when using callback
        assert result.results[0].narrative_verdict is None

    def test_llm_failure_falls_back_to_not_available(
        self, loader, stats_provider, pair_ab
    ):
        """When LLM call fails, narrative is NOT_AVAILABLE and pair still processes."""
        fake = FakeLLMClient()
        fake.seed_error(ConnectionError("LLM unavailable"))

        runner = PipelineRunner(
            report_loader=loader,
            stats_provider=stats_provider,
            llm_client=fake,
        )
        result = runner.run_batch([pair_ab])

        assert result.n_processed == 1
        assert result.n_errors == 0  # LLM failure is not a pair error
        assert (
            result.results[0].features.narrative_consistency
            == NarrativeConsistency.NOT_AVAILABLE
        )
        assert result.results[0].narrative_verdict is None

    def test_no_llm_client_defaults_to_not_available(
        self, loader, stats_provider, pair_ab
    ):
        """Without llm_client or narrative_provider, defaults to NOT_AVAILABLE."""
        runner = PipelineRunner(
            report_loader=loader,
            stats_provider=stats_provider,
        )
        result = runner.run_batch([pair_ab])

        assert (
            result.results[0].features.narrative_consistency
            == NarrativeConsistency.NOT_AVAILABLE
        )

    def test_run_pair_with_verdict(self, runner, pair_ab):
        """run_pair accepts a pre-computed NarrativeVerdict."""
        verdict = NarrativeVerdict(
            consistency=NarrativeConsistency.CONSISTENT,
            evidence=["Age matches"],
        )
        result = runner.run_pair(pair_ab, narrative_verdict=verdict)

        assert (
            result.features.narrative_consistency
            == NarrativeConsistency.CONSISTENT
        )
        assert result.narrative_verdict == verdict

    def test_run_pair_verdict_overrides_consistency(self, runner, pair_ab):
        """When both narrative_consistency and verdict are given, verdict wins."""
        verdict = NarrativeVerdict(
            consistency=NarrativeConsistency.INCONSISTENT,
        )
        result = runner.run_pair(
            pair_ab,
            narrative_consistency=NarrativeConsistency.STRONGLY_CONSISTENT,
            narrative_verdict=verdict,
        )
        assert (
            result.features.narrative_consistency
            == NarrativeConsistency.INCONSISTENT
        )

    def test_pair_result_has_no_verdict_by_default(self, runner, pair_ab):
        """Without LLM, PairResult.narrative_verdict is None."""
        result = runner.run_pair(pair_ab)
        assert result.narrative_verdict is None


# ===================================================================
# Narrative source/case integration
# ===================================================================

class TestSourceCaseIntegration:

    def test_no_llm_client_assessment_is_none(self, runner, pair_ab):
        """Without LLM, no source/case assessment is produced."""
        batch = runner.run_batch([pair_ab])
        result = batch.results[0]
        assert result.source_case_assessment is None
        assert result.features.source_case_assessment is None

    def test_batch_memoizes_extraction_per_report(
        self, loader, stats_provider, pair_ab, pair_ac
    ):
        """Same report shared across two pairs → extracted once per report."""
        fake = FakeLLMClient()
        # Two pairs, each needs: narrative comparison (1) +
        # AE clinical-picture (1). Source/case extraction is per report,
        # and R001 is shared across both pairs — we expect 3 source/case
        # extraction calls total (R001 once, R002, R003).
        for _ in range(2):
            fake.seed_response(NarrativeVerdict(
                consistency=NarrativeConsistency.CONSISTENT,
            ))
            fake.seed_response(AEClinicalPictureVerdict(findings=[]))
        # Source/case extractions: R001, R002, R001-again-memoized, R003
        # → only R001/R002/R003 actually fire.
        fake.seed_response(NarrativeSourceCase())
        fake.seed_response(NarrativeSourceCase())
        fake.seed_response(NarrativeSourceCase())

        runner_ = PipelineRunner(
            report_loader=loader,
            stats_provider=stats_provider,
            llm_client=fake,
        )
        batch = runner_.run_batch([pair_ab, pair_ac])

        assert batch.n_processed == 2
        assert batch.n_errors == 0
        # Count source/case extraction requests by response_model.
        src_requests = [
            r for r in fake.requests
            if r.response_model is NarrativeSourceCase
        ]
        assert len(src_requests) == 3

    def test_extraction_failure_does_not_abort_pair(
        self, loader, stats_provider, pair_ab
    ):
        """Extraction errors → source_case_assessment=None, pair still processes."""
        fake = FakeLLMClient()
        # Successful narrative + AE, then two failing source/case calls.
        fake.seed_response(NarrativeVerdict(
            consistency=NarrativeConsistency.CONSISTENT,
        ))
        fake.seed_response(AEClinicalPictureVerdict(findings=[]))
        fake.seed_error(RuntimeError("extraction failed"))
        fake.seed_error(RuntimeError("extraction failed"))

        runner_ = PipelineRunner(
            report_loader=loader,
            stats_provider=stats_provider,
            llm_client=fake,
        )
        batch = runner_.run_batch([pair_ab])

        assert batch.n_processed == 1
        assert batch.n_errors == 0
        assert batch.results[0].source_case_assessment is None
        assert batch.results[0].features.source_case_assessment is None
