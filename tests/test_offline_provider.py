"""Tests for offline (frozen-validation) mode.

Covers:
  - FrozenStatsProvider for global-only, country, and country_year scopes
  - Error cases: missing pair_id, missing scope, match mismatch
  - Backward compatibility: existing live SyntheticStatsProvider path
  - End-to-end: offline provider gives same result as live provider for
    equivalent counts
"""

from __future__ import annotations

import pytest

from llm_dup_dev.data.offline import (
    FrozenPairBundle,
    FrozenScopeStats,
    FrozenStatsProvider,
)
from llm_dup_dev.data.protocols import PairContextSink, StatsProvider
from llm_dup_dev.data.synthetic import SyntheticReportLoader, SyntheticStatsProvider
from llm_dup_dev.feature_generation.probability import FrequencyStats
from llm_dup_dev.models.report import (
    AdverseEvent,
    AgeValue,
    DateValue,
    Drug,
    Report,
    SourcedValue,
)
from llm_dup_dev.models.report_pair import ReportPair
from llm_dup_dev.pipeline.runner import PipelineRunner


# ---------------------------------------------------------------------------
# Shared stats values used across tests
# ---------------------------------------------------------------------------

GLOBAL_STATS = FrozenScopeStats(N_total=5000, N_drugs=200, N_AEs=300, N_drugs_AEs=40)
COUNTRY_STATS = FrozenScopeStats(N_total=1200, N_drugs=50, N_AEs=80, N_drugs_AEs=12)
COUNTRY_YEAR_STATS = FrozenScopeStats(N_total=300, N_drugs=15, N_AEs=22, N_drugs_AEs=4)


# ---------------------------------------------------------------------------
# Report fixtures for the three scope scenarios
# ---------------------------------------------------------------------------


@pytest.fixture
def report_global_1() -> Report:
    """Report with country US, year 2020 — no country overlap with report_global_2."""
    return Report(
        report_id="G1",
        country_of_origin_values=[SourcedValue(value="US", sources=["structured"])],
        report_date_values=[
            DateValue(value="2020-01-01", resolution="day", sources=["structured"])
        ],
        drugs=[Drug(substance="aspirin", sources=["structured"])],
        adverse_events=[AdverseEvent(pt_name="headache", sources=["structured"])],
        patient_age_values=[AgeValue(value=40, unit="years", sources=["structured"])],
        patient_sex_values=[SourcedValue(value="male", sources=["structured"])],
    )


@pytest.fixture
def report_global_2() -> Report:
    """Report with country DE — no country overlap → global context with G1."""
    return Report(
        report_id="G2",
        country_of_origin_values=[SourcedValue(value="DE", sources=["structured"])],
        report_date_values=[
            DateValue(value="2020-01-01", resolution="day", sources=["structured"])
        ],
        drugs=[Drug(substance="aspirin", sources=["structured"])],
        adverse_events=[AdverseEvent(pt_name="headache", sources=["structured"])],
        patient_age_values=[AgeValue(value=40, unit="years", sources=["structured"])],
        patient_sex_values=[SourcedValue(value="male", sources=["structured"])],
    )


@pytest.fixture
def report_country_1() -> Report:
    """Report with country US, year 2020 — shares country but not year with C2."""
    return Report(
        report_id="C1",
        country_of_origin_values=[SourcedValue(value="US", sources=["structured"])],
        report_date_values=[
            DateValue(value="2020-03-01", resolution="day", sources=["structured"])
        ],
        drugs=[Drug(substance="metformin", sources=["structured"])],
        adverse_events=[AdverseEvent(pt_name="nausea", sources=["structured"])],
        patient_age_values=[AgeValue(value=55, unit="years", sources=["structured"])],
        patient_sex_values=[SourcedValue(value="female", sources=["structured"])],
    )


@pytest.fixture
def report_country_2() -> Report:
    """Report with country US, year 2022 — shares country but not year → country context."""
    return Report(
        report_id="C2",
        country_of_origin_values=[SourcedValue(value="US", sources=["structured"])],
        report_date_values=[
            DateValue(value="2022-03-01", resolution="day", sources=["structured"])
        ],
        drugs=[Drug(substance="metformin", sources=["structured"])],
        adverse_events=[AdverseEvent(pt_name="nausea", sources=["structured"])],
        patient_age_values=[AgeValue(value=55, unit="years", sources=["structured"])],
        patient_sex_values=[SourcedValue(value="female", sources=["structured"])],
    )


@pytest.fixture
def report_country_year_1() -> Report:
    """Report with country US, year 2021 — shares both with CY2."""
    return Report(
        report_id="CY1",
        country_of_origin_values=[SourcedValue(value="US", sources=["structured"])],
        report_date_values=[
            DateValue(value="2021-06-01", resolution="day", sources=["structured"])
        ],
        drugs=[Drug(substance="lisinopril", sources=["structured"])],
        adverse_events=[AdverseEvent(pt_name="cough", sources=["structured"])],
        patient_age_values=[AgeValue(value=62, unit="years", sources=["structured"])],
        patient_sex_values=[SourcedValue(value="male", sources=["structured"])],
    )


@pytest.fixture
def report_country_year_2() -> Report:
    """Report with country US, year 2021 — shares both → country_year context."""
    return Report(
        report_id="CY2",
        country_of_origin_values=[SourcedValue(value="US", sources=["structured"])],
        report_date_values=[
            DateValue(value="2021-09-15", resolution="day", sources=["structured"])
        ],
        drugs=[Drug(substance="lisinopril", sources=["structured"])],
        adverse_events=[AdverseEvent(pt_name="cough", sources=["structured"])],
        patient_age_values=[AgeValue(value=62, unit="years", sources=["structured"])],
        patient_sex_values=[SourcedValue(value="male", sources=["structured"])],
    )


# ---------------------------------------------------------------------------
# Frozen bundle fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bundle_global() -> FrozenPairBundle:
    """Bundle for a global-context pair (no shared country)."""
    return FrozenPairBundle(
        pair_id="G1__G2",
        matching_drugs=["aspirin"],
        matching_AEs=["headache"],
        global_stats=GLOBAL_STATS,
        # no country_stats / country_year_stats
    )


@pytest.fixture
def bundle_country() -> FrozenPairBundle:
    """Bundle for a country-context pair (shared country, different years)."""
    return FrozenPairBundle(
        pair_id="C1__C2",
        matching_drugs=["metformin"],
        matching_AEs=["nausea"],
        global_stats=GLOBAL_STATS,
        country_stats=COUNTRY_STATS,
    )


@pytest.fixture
def bundle_country_year() -> FrozenPairBundle:
    """Bundle for a country_year-context pair (shared country and year)."""
    return FrozenPairBundle(
        pair_id="CY1__CY2",
        matching_drugs=["lisinopril"],
        matching_AEs=["cough"],
        global_stats=GLOBAL_STATS,
        country_stats=COUNTRY_STATS,
        country_year_stats=COUNTRY_YEAR_STATS,
    )


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:

    def test_frozen_provider_satisfies_stats_provider(self, bundle_global):
        provider = FrozenStatsProvider({"G1__G2": bundle_global})
        assert isinstance(provider, StatsProvider)

    def test_frozen_provider_satisfies_pair_context_sink(self, bundle_global):
        provider = FrozenStatsProvider({"G1__G2": bundle_global})
        assert isinstance(provider, PairContextSink)


# ---------------------------------------------------------------------------
# Global-only pair
# ---------------------------------------------------------------------------


class TestGlobalScope:

    def test_returns_global_stats(self, bundle_global):
        provider = FrozenStatsProvider({"G1__G2": bundle_global})
        provider.set_current_pair("G1__G2")
        stats = provider.compute_stats(
            matching_drugs=["aspirin"],
            matching_AEs=["headache"],
            country=None,
            year=None,
        )
        assert stats == FrequencyStats(
            N_total=5000, N_drugs=200, N_AEs=300, N_drugs_AEs=40
        )

    def test_end_to_end_global(
        self, report_global_1, report_global_2, bundle_global
    ):
        """Full pipeline run with FrozenStatsProvider on a global pair."""
        loader = SyntheticReportLoader({
            "G1": report_global_1,
            "G2": report_global_2,
        })
        provider = FrozenStatsProvider({"G1__G2": bundle_global})
        runner = PipelineRunner(report_loader=loader, stats_provider=provider)
        pair = ReportPair(pair_id="G1__G2", report_1_id="G1", report_2_id="G2")

        result = runner.run_pair(pair)
        assert result.pair_id == "G1__G2"
        assert result.features.L_match >= 0


# ---------------------------------------------------------------------------
# Country pair
# ---------------------------------------------------------------------------


class TestCountryScope:

    def test_returns_country_stats(self, bundle_country):
        provider = FrozenStatsProvider({"C1__C2": bundle_country})
        provider.set_current_pair("C1__C2")
        stats = provider.compute_stats(
            matching_drugs=["metformin"],
            matching_AEs=["nausea"],
            country="US",
            year=None,
        )
        assert stats == FrequencyStats(
            N_total=1200, N_drugs=50, N_AEs=80, N_drugs_AEs=12
        )

    def test_end_to_end_country(
        self, report_country_1, report_country_2, bundle_country
    ):
        """Full pipeline run on a country-context pair."""
        loader = SyntheticReportLoader({
            "C1": report_country_1,
            "C2": report_country_2,
        })
        provider = FrozenStatsProvider({"C1__C2": bundle_country})
        runner = PipelineRunner(report_loader=loader, stats_provider=provider)
        pair = ReportPair(pair_id="C1__C2", report_1_id="C1", report_2_id="C2")

        result = runner.run_pair(pair)
        assert result.pair_id == "C1__C2"


# ---------------------------------------------------------------------------
# Country-year pair
# ---------------------------------------------------------------------------


class TestCountryYearScope:

    def test_returns_country_year_stats(self, bundle_country_year):
        provider = FrozenStatsProvider({"CY1__CY2": bundle_country_year})
        provider.set_current_pair("CY1__CY2")
        stats = provider.compute_stats(
            matching_drugs=["lisinopril"],
            matching_AEs=["cough"],
            country="US",
            year=2021,
        )
        assert stats == FrequencyStats(
            N_total=300, N_drugs=15, N_AEs=22, N_drugs_AEs=4
        )

    def test_end_to_end_country_year(
        self, report_country_year_1, report_country_year_2, bundle_country_year
    ):
        """Full pipeline run on a country_year-context pair."""
        loader = SyntheticReportLoader({
            "CY1": report_country_year_1,
            "CY2": report_country_year_2,
        })
        provider = FrozenStatsProvider({"CY1__CY2": bundle_country_year})
        runner = PipelineRunner(report_loader=loader, stats_provider=provider)
        pair = ReportPair(pair_id="CY1__CY2", report_1_id="CY1", report_2_id="CY2")

        result = runner.run_pair(pair)
        assert result.pair_id == "CY1__CY2"


# ---------------------------------------------------------------------------
# Error: missing pair_id
# ---------------------------------------------------------------------------


class TestMissingPairId:

    def test_missing_pair_raises_key_error(self, bundle_global):
        provider = FrozenStatsProvider({"G1__G2": bundle_global})
        provider.set_current_pair("DOES_NOT_EXIST")
        with pytest.raises(KeyError, match="DOES_NOT_EXIST"):
            provider.compute_stats(
                matching_drugs=["aspirin"],
                matching_AEs=["headache"],
                country=None,
                year=None,
            )

    def test_missing_set_current_pair_raises_runtime_error(self, bundle_global):
        """compute_stats without a prior set_current_pair call must fail loudly."""
        provider = FrozenStatsProvider({"G1__G2": bundle_global})
        with pytest.raises(RuntimeError, match="set_current_pair"):
            provider.compute_stats(
                matching_drugs=["aspirin"],
                matching_AEs=["headache"],
                country=None,
                year=None,
            )

    def test_missing_pair_raises_in_runner(
        self, report_global_1, report_global_2, bundle_global
    ):
        """PipelineRunner with FrozenStatsProvider propagates KeyError for unknown pair."""
        loader = SyntheticReportLoader({
            "G1": report_global_1,
            "G2": report_global_2,
        })
        provider = FrozenStatsProvider({"DIFFERENT__PAIR": bundle_global})
        runner = PipelineRunner(report_loader=loader, stats_provider=provider)
        pair = ReportPair(pair_id="G1__G2", report_1_id="G1", report_2_id="G2")
        with pytest.raises(KeyError, match="G1__G2"):
            runner.run_pair(pair)


# ---------------------------------------------------------------------------
# Error: missing scope in bundle
# ---------------------------------------------------------------------------


class TestMissingScope:

    def test_country_year_scope_missing_raises_key_error(self, bundle_global):
        """bundle_global has no country_year_stats — requesting it must fail."""
        provider = FrozenStatsProvider({"G1__G2": bundle_global})
        provider.set_current_pair("G1__G2")
        with pytest.raises(KeyError, match="country_year"):
            provider.compute_stats(
                matching_drugs=["aspirin"],
                matching_AEs=["headache"],
                country="US",
                year=2020,
            )

    def test_country_scope_missing_raises_key_error(self, bundle_global):
        """bundle_global has no country_stats — requesting it must fail."""
        provider = FrozenStatsProvider({"G1__G2": bundle_global})
        provider.set_current_pair("G1__G2")
        with pytest.raises(KeyError, match="country"):
            provider.compute_stats(
                matching_drugs=["aspirin"],
                matching_AEs=["headache"],
                country="US",
                year=None,
            )


# ---------------------------------------------------------------------------
# Error: match mismatch between bundle and pipeline
# ---------------------------------------------------------------------------


class TestMatchMismatch:

    def test_drug_mismatch_raises_value_error(self, bundle_global):
        provider = FrozenStatsProvider({"G1__G2": bundle_global})
        provider.set_current_pair("G1__G2")
        with pytest.raises(ValueError, match="matching_drugs mismatch"):
            provider.compute_stats(
                matching_drugs=["paracetamol"],  # bundle stores ["aspirin"]
                matching_AEs=["headache"],
                country=None,
                year=None,
            )

    def test_ae_mismatch_raises_value_error(self, bundle_global):
        provider = FrozenStatsProvider({"G1__G2": bundle_global})
        provider.set_current_pair("G1__G2")
        with pytest.raises(ValueError, match="matching_AEs mismatch"):
            provider.compute_stats(
                matching_drugs=["aspirin"],
                matching_AEs=["nausea"],  # bundle stores ["headache"]
                country=None,
                year=None,
            )

    def test_mismatch_raised_in_runner(
        self, report_global_1, report_global_2
    ):
        """A bundle with wrong matching_drugs causes ValueError in the runner."""
        wrong_bundle = FrozenPairBundle(
            pair_id="G1__G2",
            matching_drugs=["wrong_drug"],  # pipeline will compute ["aspirin"]
            matching_AEs=["headache"],
            global_stats=GLOBAL_STATS,
        )
        loader = SyntheticReportLoader({
            "G1": report_global_1,
            "G2": report_global_2,
        })
        provider = FrozenStatsProvider({"G1__G2": wrong_bundle})
        runner = PipelineRunner(report_loader=loader, stats_provider=provider)
        pair = ReportPair(pair_id="G1__G2", report_1_id="G1", report_2_id="G2")
        with pytest.raises(ValueError, match="matching_drugs mismatch"):
            runner.run_pair(pair)


# ---------------------------------------------------------------------------
# Offline and live paths give the same result for equivalent counts
# ---------------------------------------------------------------------------


class TestOfflineEquivalenceToLive:
    """When the frozen bundle contains the same counts as a live provider
    would return, both paths must produce identical PairResult outputs."""

    def test_same_result_as_synthetic_provider(
        self, report_global_1, report_global_2, bundle_global
    ):
        loader = SyntheticReportLoader({
            "G1": report_global_1,
            "G2": report_global_2,
        })
        pair = ReportPair(pair_id="G1__G2", report_1_id="G1", report_2_id="G2")

        # Live provider — use the same counts as GLOBAL_STATS
        live_provider = SyntheticStatsProvider(
            default_N_total=GLOBAL_STATS.N_total,
            default_N_drugs=GLOBAL_STATS.N_drugs,
            default_N_AEs=GLOBAL_STATS.N_AEs,
            default_N_drugs_AEs=GLOBAL_STATS.N_drugs_AEs,
        )
        live_result = PipelineRunner(
            report_loader=loader, stats_provider=live_provider
        ).run_pair(pair)

        # Offline provider — same counts via frozen bundle
        offline_provider = FrozenStatsProvider({"G1__G2": bundle_global})
        offline_result = PipelineRunner(
            report_loader=loader, stats_provider=offline_provider
        ).run_pair(pair)

        assert offline_result.final_label == live_result.final_label
        assert offline_result.features.L_match == pytest.approx(
            live_result.features.L_match
        )
        assert offline_result.classification.primary == live_result.classification.primary


# ---------------------------------------------------------------------------
# Backward compatibility: existing live StatsProvider path still works
# ---------------------------------------------------------------------------


class TestLivePathUnchanged:
    """SyntheticStatsProvider (no PairContextSink) must continue to work
    without any code changes."""

    def test_synthetic_provider_not_pair_context_sink(self):
        provider = SyntheticStatsProvider()
        # SyntheticStatsProvider does NOT implement PairContextSink
        assert not isinstance(provider, PairContextSink)

    def test_runner_with_synthetic_provider(self, report_global_1, report_global_2):
        loader = SyntheticReportLoader({
            "G1": report_global_1,
            "G2": report_global_2,
        })
        provider = SyntheticStatsProvider()
        runner = PipelineRunner(report_loader=loader, stats_provider=provider)
        pair = ReportPair(pair_id="G1__G2", report_1_id="G1", report_2_id="G2")

        result = runner.run_pair(pair)
        assert result.pair_id == "G1__G2"
        assert result.features.L_match >= 0
        assert result.final_label in (
            "Duplicates", "Otherwise_related", "Unrelated", "Abstain"
        )
