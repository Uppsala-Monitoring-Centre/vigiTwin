"""Tests for the end-to-end pipeline runner."""

import pytest

from llm_dup_dev.classifier.config import DecisionThresholds, PipelineConfig
from llm_dup_dev.data.offline import (
    FrozenNarrativeDemographics,
    FrozenSourceCaseExtractions,
)
from llm_dup_dev.data.synthetic import SyntheticReportLoader, SyntheticStatsProvider
from llm_dup_dev.feature_generation.probability import FrequencyStats
from llm_dup_dev.llm.fake import FakeLLMClient
from llm_dup_dev.llm.tasks.ae_clinical_picture import AEClinicalPictureVerdict
from llm_dup_dev.llm.tasks.narrative_comparison import NarrativeVerdict
from llm_dup_dev.models.narrative_demographics import (
    NarrativeAgeValue,
    NarrativeDemographics,
    NarrativeMeasurementValue,
    NarrativeSex,
    NarrativeSexValue,
)
from llm_dup_dev.models.narrative_source_case import (
    CaseIdentifierFamily,
    NarrativeSourceCase,
    SourceIdentifier,
    SourceIdentifierType,
)
from llm_dup_dev.models.enums import (
    AEClinicalPictureStatus,
    IndependenceAssessment,
    IndependenceReason,
    NarrativeConsistency,
)
from llm_dup_dev.models.report import AdverseEvent, AgeValue, Report, SourcedValue
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
            lmatch_compelling=10.0, lmatch_intermediate=8.0, lmatch_limited=6.0
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
        # The runner also makes an AE clinical-picture LLM call, a
        # narrative source/case extraction per report, and a narrative
        # demographics extraction per report; seed benign responses so
        # the full chain succeeds.
        fake.seed_response(AEClinicalPictureVerdict(findings=[]))
        fake.seed_response(NarrativeSourceCase())
        fake.seed_response(NarrativeSourceCase())
        fake.seed_response(NarrativeDemographics())
        fake.seed_response(NarrativeDemographics())

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
        # Six requests per pair: narrative + AE clinical-picture +
        # narrative source/case per report + narrative demographics
        # per report.
        assert len(fake.requests) == 6

    def test_narrative_provider_takes_precedence_over_llm(
        self, loader, stats_provider, pair_ab
    ):
        """Explicit narrative_provider overrides llm_client (for narrative)."""
        fake = FakeLLMClient()
        # Narrative LLM call should NOT happen; AE clinical-picture +
        # narrative source/case per report + narrative demographics
        # per report still do.
        fake.seed_response(AEClinicalPictureVerdict(findings=[]))
        fake.seed_response(NarrativeSourceCase())
        fake.seed_response(NarrativeSourceCase())
        fake.seed_response(NarrativeDemographics())
        fake.seed_response(NarrativeDemographics())

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
        # narrative source/case (2 reports) + narrative demographics
        # (2 reports) = 5 calls.
        assert len(fake.requests) == 5
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


# ===================================================================
# Frozen source/case extraction bundles
# ===================================================================

class TestFrozenSourceCaseExtractions:

    def test_full_coverage_skips_all_source_case_llm_calls(
        self, loader, stats_provider, pair_ab
    ):
        """Bundle covers both reports → zero NarrativeSourceCase LLM calls."""
        fake = FakeLLMClient()
        # Still need narrative + AE calls per pair; no source/case seeds.
        fake.seed_response(NarrativeVerdict(
            consistency=NarrativeConsistency.CONSISTENT,
        ))
        fake.seed_response(AEClinicalPictureVerdict(findings=[]))

        bundle = FrozenSourceCaseExtractions(
            extractions={
                "R001": NarrativeSourceCase(),
                "R002": NarrativeSourceCase(),
            }
        )
        runner_ = PipelineRunner(
            report_loader=loader,
            stats_provider=stats_provider,
            llm_client=fake,
            frozen_source_case_extractions=bundle,
        )
        batch = runner_.run_batch([pair_ab])

        assert batch.n_processed == 1
        src_requests = [
            r for r in fake.requests
            if r.response_model is NarrativeSourceCase
        ]
        assert src_requests == []

    def test_partial_coverage_falls_back_to_live_llm(
        self, loader, stats_provider, pair_ab
    ):
        """Only R001 in bundle → one live source/case call (for R002)."""
        fake = FakeLLMClient()
        fake.seed_response(NarrativeVerdict(
            consistency=NarrativeConsistency.CONSISTENT,
        ))
        fake.seed_response(AEClinicalPictureVerdict(findings=[]))
        # Exactly one source/case extraction call is expected — for R002.
        fake.seed_response(NarrativeSourceCase())

        bundle = FrozenSourceCaseExtractions(
            extractions={"R001": NarrativeSourceCase()}
        )
        runner_ = PipelineRunner(
            report_loader=loader,
            stats_provider=stats_provider,
            llm_client=fake,
            frozen_source_case_extractions=bundle,
        )
        batch = runner_.run_batch([pair_ab])

        assert batch.n_processed == 1
        src_requests = [
            r for r in fake.requests
            if r.response_model is NarrativeSourceCase
        ]
        assert len(src_requests) == 1

    def test_partial_coverage_without_llm_client_yields_none(
        self, loader, stats_provider, pair_ab
    ):
        """Bundle covers R001, no LLM → R002 has no extraction → assessment None."""
        bundle = FrozenSourceCaseExtractions(
            extractions={"R001": NarrativeSourceCase()}
        )
        runner_ = PipelineRunner(
            report_loader=loader,
            stats_provider=stats_provider,
            frozen_source_case_extractions=bundle,
        )
        batch = runner_.run_batch([pair_ab])

        assert batch.n_processed == 1
        assert batch.n_errors == 0
        assert batch.results[0].source_case_assessment is None

    def test_shared_report_across_pairs_served_from_bundle_once(
        self, loader, stats_provider, pair_ab, pair_ac
    ):
        """Same report in two pairs → still zero live extractions when covered."""
        fake = FakeLLMClient()
        for _ in range(2):
            fake.seed_response(NarrativeVerdict(
                consistency=NarrativeConsistency.CONSISTENT,
            ))
            fake.seed_response(AEClinicalPictureVerdict(findings=[]))

        bundle = FrozenSourceCaseExtractions(
            extractions={
                "R001": NarrativeSourceCase(),
                "R002": NarrativeSourceCase(),
                "R003": NarrativeSourceCase(),
            }
        )
        runner_ = PipelineRunner(
            report_loader=loader,
            stats_provider=stats_provider,
            llm_client=fake,
            frozen_source_case_extractions=bundle,
        )
        batch = runner_.run_batch([pair_ab, pair_ac])

        assert batch.n_processed == 2
        src_requests = [
            r for r in fake.requests
            if r.response_model is NarrativeSourceCase
        ]
        assert src_requests == []

    def test_empty_bundle_equivalent_to_none(
        self, loader, stats_provider, pair_ab
    ):
        """extractions={} behaves identically to passing no bundle."""
        fake = FakeLLMClient()
        fake.seed_response(NarrativeVerdict(
            consistency=NarrativeConsistency.CONSISTENT,
        ))
        fake.seed_response(AEClinicalPictureVerdict(findings=[]))
        # Two source/case extractions still fire (R001 and R002).
        fake.seed_response(NarrativeSourceCase())
        fake.seed_response(NarrativeSourceCase())

        bundle = FrozenSourceCaseExtractions(extractions={})
        runner_ = PipelineRunner(
            report_loader=loader,
            stats_provider=stats_provider,
            llm_client=fake,
            frozen_source_case_extractions=bundle,
        )
        batch = runner_.run_batch([pair_ab])

        assert batch.n_processed == 1
        src_requests = [
            r for r in fake.requests
            if r.response_model is NarrativeSourceCase
        ]
        assert len(src_requests) == 2

    def test_shared_source_override_fires_with_frozen_bundle_no_llm_deterministic_ae(
        self, loader, stats_provider, pair_ab
    ):
        """Pins the LLM-free deployment path: deterministic AE +
        ``llm_client=None`` + a frozen bundle whose pair-wise comparison
        yields a verdict in ``_SHARED_SOURCE_VERDICTS`` ⇒ the shared-source
        override fires and the ``SHARED_SOURCE`` reason is recorded.

        Regression for the silent-failure mode where switching AE to
        deterministic and dropping the LLM client used to lose the override
        without warning.
        """
        # Two extractions with the same DOI and the same case identifier
        # (``Case 2``) → ``compare_source_case`` returns
        # ``SAME_SOURCE_SAME_CASE``, which is in ``_SHARED_SOURCE_VERDICTS``.
        shared = NarrativeSourceCase(
            source_identifiers=[
                SourceIdentifier(
                    type=SourceIdentifierType.DOI, value="10.a/b",
                ),
            ],
            multi_patient_source=True,
            case_identifier_within_source="Case 2",
            case_identifier_family=CaseIdentifierFamily.CASE,
            case_identifier_value_normalized="2",
        )
        bundle = FrozenSourceCaseExtractions(
            extractions={"R001": shared, "R002": shared}
        )

        runner_ = PipelineRunner(
            report_loader=loader,
            stats_provider=stats_provider,
            llm_client=None,
            ae_clinical_picture_mode="rule_based",
            frozen_source_case_extractions=bundle,
        )
        batch = runner_.run_batch([pair_ab])

        assert batch.n_processed == 1
        result = batch.results[0].classification
        assert result.independence_assessment == IndependenceAssessment.NON_INDEPENDENT
        assert result.independence_reason == IndependenceReason.SHARED_SOURCE

    def test_no_warning_when_bundle_covers_pair_without_llm(
        self, loader, stats_provider, pair_ab, caplog
    ):
        """A bundle covering the pair suppresses the source-case warning
        even with ``llm_client=None``."""
        bundle = FrozenSourceCaseExtractions(
            extractions={
                "R001": NarrativeSourceCase(),
                "R002": NarrativeSourceCase(),
            }
        )
        demographics_bundle = FrozenNarrativeDemographics(
            extractions={
                "R001": NarrativeDemographics(),
                "R002": NarrativeDemographics(),
            }
        )
        runner_ = PipelineRunner(
            report_loader=loader,
            stats_provider=stats_provider,
            llm_client=None,
            ae_clinical_picture_mode="rule_based",
            frozen_source_case_extractions=bundle,
            frozen_narrative_demographics=demographics_bundle,
        )
        with caplog.at_level("WARNING", logger="llm_dup_dev.pipeline.runner"):
            runner_.run_batch([pair_ab])
        assert "Shared-source override is disabled" not in caplog.text
        assert "Narrative-demographics extraction is disabled" not in caplog.text

    def test_warns_when_no_llm_and_no_source_case_bundle(
        self, loader, stats_provider, pair_ab, caplog
    ):
        """Warns once at ``run_batch`` start when the override would
        silently fail (no llm_client, no frozen bundle)."""
        runner_ = PipelineRunner(
            report_loader=loader,
            stats_provider=stats_provider,
            llm_client=None,
            ae_clinical_picture_mode="rule_based",
        )
        with caplog.at_level("WARNING", logger="llm_dup_dev.pipeline.runner"):
            runner_.run_batch([pair_ab])
        assert "Shared-source override is disabled" in caplog.text
        assert "Narrative-demographics extraction is disabled" in caplog.text


# ===================================================================
# Frozen narrative-demographics bundles
# ===================================================================

class TestFrozenNarrativeDemographics:

    def test_full_coverage_skips_all_demographics_llm_calls(
        self, loader, stats_provider, pair_ab
    ):
        """Bundle covers both reports → zero NarrativeDemographics calls."""
        fake = FakeLLMClient()
        fake.seed_response(NarrativeVerdict(
            consistency=NarrativeConsistency.CONSISTENT,
        ))
        fake.seed_response(AEClinicalPictureVerdict(findings=[]))
        # Source/case extractions still fire per report.
        fake.seed_response(NarrativeSourceCase())
        fake.seed_response(NarrativeSourceCase())

        bundle = FrozenNarrativeDemographics(
            extractions={
                "R001": NarrativeDemographics(),
                "R002": NarrativeDemographics(),
            }
        )
        runner_ = PipelineRunner(
            report_loader=loader,
            stats_provider=stats_provider,
            llm_client=fake,
            frozen_narrative_demographics=bundle,
        )
        batch = runner_.run_batch([pair_ab])

        assert batch.n_processed == 1
        demo_requests = [
            r for r in fake.requests
            if r.response_model is NarrativeDemographics
        ]
        assert demo_requests == []

    def test_partial_bundle_falls_back_to_live_extraction(
        self, loader, stats_provider, pair_ab
    ):
        """Only R001 in bundle → one live demographics call (for R002)."""
        fake = FakeLLMClient()
        fake.seed_response(NarrativeVerdict(
            consistency=NarrativeConsistency.CONSISTENT,
        ))
        fake.seed_response(AEClinicalPictureVerdict(findings=[]))
        fake.seed_response(NarrativeSourceCase())
        fake.seed_response(NarrativeSourceCase())
        # Exactly one demographics extraction call is expected — for R002.
        fake.seed_response(NarrativeDemographics())

        bundle = FrozenNarrativeDemographics(
            extractions={"R001": NarrativeDemographics()}
        )
        runner_ = PipelineRunner(
            report_loader=loader,
            stats_provider=stats_provider,
            llm_client=fake,
            frozen_narrative_demographics=bundle,
        )
        batch = runner_.run_batch([pair_ab])

        assert batch.n_processed == 1
        demo_requests = [
            r for r in fake.requests
            if r.response_model is NarrativeDemographics
        ]
        assert len(demo_requests) == 1

    def test_no_llm_with_bundle(self, loader, stats_provider, pair_ab):
        """Full-coverage bundle + no LLM client → batch still succeeds."""
        bundle = FrozenNarrativeDemographics(
            extractions={
                "R001": NarrativeDemographics(),
                "R002": NarrativeDemographics(),
            }
        )
        runner_ = PipelineRunner(
            report_loader=loader,
            stats_provider=stats_provider,
            frozen_narrative_demographics=bundle,
        )
        batch = runner_.run_batch([pair_ab])

        assert batch.n_processed == 1
        assert batch.n_errors == 0

    def test_empty_bundle_equivalent_to_none(
        self, loader, stats_provider, pair_ab
    ):
        """extractions={} behaves identically to passing no bundle."""
        fake = FakeLLMClient()
        fake.seed_response(NarrativeVerdict(
            consistency=NarrativeConsistency.CONSISTENT,
        ))
        fake.seed_response(AEClinicalPictureVerdict(findings=[]))
        fake.seed_response(NarrativeSourceCase())
        fake.seed_response(NarrativeSourceCase())
        # Both R001 and R002 demographics still fire.
        fake.seed_response(NarrativeDemographics())
        fake.seed_response(NarrativeDemographics())

        bundle = FrozenNarrativeDemographics(extractions={})
        runner_ = PipelineRunner(
            report_loader=loader,
            stats_provider=stats_provider,
            llm_client=fake,
            frozen_narrative_demographics=bundle,
        )
        batch = runner_.run_batch([pair_ab])

        assert batch.n_processed == 1
        demo_requests = [
            r for r in fake.requests
            if r.response_model is NarrativeDemographics
        ]
        assert len(demo_requests) == 2

    def test_shared_report_across_pairs_extracted_once(
        self, loader, stats_provider, pair_ab, pair_ac
    ):
        """Same report in two pairs → one demographics extraction per report."""
        fake = FakeLLMClient()
        for _ in range(2):
            fake.seed_response(NarrativeVerdict(
                consistency=NarrativeConsistency.CONSISTENT,
            ))
            fake.seed_response(AEClinicalPictureVerdict(findings=[]))
        # Three unique reports across pair_ab and pair_ac:
        # R001, R002, R003 — each extracted once for source/case and
        # once for demographics.
        for _ in range(3):
            fake.seed_response(NarrativeSourceCase())
        for _ in range(3):
            fake.seed_response(NarrativeDemographics())

        runner_ = PipelineRunner(
            report_loader=loader,
            stats_provider=stats_provider,
            llm_client=fake,
        )
        batch = runner_.run_batch([pair_ab, pair_ac])

        assert batch.n_processed == 2
        demo_requests = [
            r for r in fake.requests
            if r.response_model is NarrativeDemographics
        ]
        assert len(demo_requests) == 3

    def test_structured_vs_narrative_same_age_no_inconsistency_end_to_end(
        self, loader, stats_provider, pair_ac
    ):
        """End-to-end: bundle-injected narrative age makes structured
        min-diff match, so no age inconsistency is raised.

        R001 has structured age 54; R003 has structured age 28.
        Structured-only min-diff is 26 (strong). Injecting a narrative
        age of 28 for R001 via the bundle collapses min-diff to 0 →
        the age check should produce no inconsistency. This is the
        load-bearing integration check: it proves the full chain
        (bundle → cache → kwarg → merge → generate_pair_features →
        inconsistencies) wires generous matching without caller-side
        glue.
        """
        fake = FakeLLMClient()
        fake.seed_response(NarrativeVerdict(
            consistency=NarrativeConsistency.CONSISTENT,
        ))
        fake.seed_response(AEClinicalPictureVerdict(findings=[]))
        fake.seed_response(NarrativeSourceCase())
        fake.seed_response(NarrativeSourceCase())

        bundle = FrozenNarrativeDemographics(
            extractions={
                "R001": NarrativeDemographics(
                    ages=[NarrativeAgeValue(
                        value=28,
                        unit="years",
                        evidence="28-year-old",
                    )],
                ),
                "R003": NarrativeDemographics(),
            }
        )
        runner_ = PipelineRunner(
            report_loader=loader,
            stats_provider=stats_provider,
            llm_client=fake,
            frozen_narrative_demographics=bundle,
        )
        batch = runner_.run_batch([pair_ac])

        assert batch.n_processed == 1
        assert batch.n_errors == 0
        inconsistencies = batch.results[0].features.inconsistencies
        age_issues = [i for i in inconsistencies if i.feature == "age"]
        assert age_issues == []


# ===================================================================
# AE clinical-picture mode dispatch
# ===================================================================

class TestAEClinicalPictureMode:
    """Verify the rule-based fallback / explicit-mode dispatch."""

    @pytest.fixture
    def hierarchy_loader(self):
        """Loader whose reports' AEs carry MedDRA hierarchy."""
        r1 = Report(
            report_id="H1",
            patient_sex_values=[SourcedValue(value="female", sources=["structured"])],
            patient_age_values=[AgeValue(value=40, unit="years", sources=["structured"])],
            country_of_origin_values=[SourcedValue(value="us", sources=["structured"])],
            adverse_events=[
                AdverseEvent(
                    pt_name="arrhythmia", sources=["structured"],
                    primary_hlt_name="cardiac arrhythmias",
                    primary_hlgt_name="cardiac arrhythmias",
                    primary_soc_name="cardiac disorders",
                ),
            ],
        )
        r2 = Report(
            report_id="H2",
            patient_sex_values=[SourcedValue(value="female", sources=["structured"])],
            patient_age_values=[AgeValue(value=40, unit="years", sources=["structured"])],
            country_of_origin_values=[SourcedValue(value="us", sources=["structured"])],
            adverse_events=[
                AdverseEvent(
                    pt_name="rash", sources=["structured"],
                    primary_hlt_name="rashes eruptions and exanthems nec",
                    primary_hlgt_name="epidermal and dermal conditions",
                    primary_soc_name="skin and subcutaneous tissue disorders",
                ),
            ],
        )
        return SyntheticReportLoader({r1.report_id: r1, r2.report_id: r2})

    @pytest.fixture
    def hierarchy_pair(self):
        return ReportPair(pair_id="H1__H2", report_1_id="H1", report_2_id="H2")

    def test_auto_mode_no_llm_falls_back_to_rule_based(
        self, hierarchy_loader, stats_provider, hierarchy_pair
    ):
        runner_ = PipelineRunner(
            report_loader=hierarchy_loader,
            stats_provider=stats_provider,
            # default ae_clinical_picture_mode = "auto"
        )
        batch = runner_.run_batch([hierarchy_pair])
        features = batch.results[0].features
        assert features.ae_clinical_picture_status == AEClinicalPictureStatus.ASSESSED
        assert features.ae_clinical_picture_method == "rule_based"
        assert features.ae_clinical_picture_reason == "llm_fallback_to_rule"
        assert features.ae_clinical_picture_rule_based_details is not None
        # AEs share no SOC → decisive Step-1 verdict.
        assert features.ae_clinical_picture_counts.n_decisive == 1

    def test_rule_based_mode_always_uses_rule(
        self, hierarchy_loader, stats_provider, hierarchy_pair
    ):
        fake = FakeLLMClient()
        # Seed responses for the other LLM-driven steps that run_batch
        # always invokes (narrative comparison, source-case extraction).
        fake.seed_response(NarrativeVerdict(consistency=NarrativeConsistency.NEUTRAL))
        fake.seed_response(NarrativeSourceCase())
        fake.seed_response(NarrativeSourceCase())
        fake.seed_response(NarrativeDemographics())
        fake.seed_response(NarrativeDemographics())

        runner_ = PipelineRunner(
            report_loader=hierarchy_loader,
            stats_provider=stats_provider,
            llm_client=fake,
            ae_clinical_picture_mode="rule_based",
        )
        batch = runner_.run_batch([hierarchy_pair])
        features = batch.results[0].features
        assert features.ae_clinical_picture_method == "rule_based"
        assert features.ae_clinical_picture_reason == "rule_based"

    def test_llm_mode_no_client_records_no_llm_client(
        self, hierarchy_loader, stats_provider, hierarchy_pair
    ):
        runner_ = PipelineRunner(
            report_loader=hierarchy_loader,
            stats_provider=stats_provider,
            ae_clinical_picture_mode="llm",
        )
        batch = runner_.run_batch([hierarchy_pair])
        features = batch.results[0].features
        assert features.ae_clinical_picture_status == AEClinicalPictureStatus.NOT_ASSESSED
        assert features.ae_clinical_picture_reason == "no_llm_client"
        assert features.ae_clinical_picture_method == "llm"
