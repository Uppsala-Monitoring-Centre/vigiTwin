"""Tests for the feature generation pipeline (end-to-end orchestration)."""

import pytest

from llm_dup_dev.classifier.config import PipelineConfig
from llm_dup_dev.feature_generation.pipeline import generate_pair_features
from llm_dup_dev.feature_generation.probability import FrequencyStats
from llm_dup_dev.models.enums import (
    InconsistencySeverity,
    NarrativeConsistency,
    ReportingContextLevel,
)


@pytest.fixture
def default_config():
    return PipelineConfig()


@pytest.fixture
def stats_similar():
    """Stats for a moderately rare shared clinical context."""
    return FrequencyStats(
        N_total=100_000, N_drugs=48, N_AEs=200, N_drugs_AEs=12
    )


class TestGeneratePairFeatures:
    def test_near_duplicate_pair(
        self, report_a, report_b, stats_similar, default_config
    ):
        """Two near-duplicate reports should produce meaningful features."""
        features = generate_pair_features(
            r1=report_a,
            r2=report_b,
            stats=stats_similar,
            narrative_consistency=NarrativeConsistency.CONSISTENT,
            config=default_config,
        )

        # Pair ID
        assert features.pair_id == "R001__R002"

        # Shared clinical features
        assert "pregabalin" in features.matching_drugs
        assert "sertraline" in features.matching_drugs
        assert "somnolence" in features.matching_AEs

        # Reporting context
        assert features.reporting_context.level == ReportingContextLevel.COUNTRY_YEAR
        assert features.reporting_context.country == "US"
        assert features.reporting_context.year == 2020

        # Probability
        assert features.L_match > 0
        assert features.probability.p_joint > 0

        # Narrative
        assert features.narrative_consistency == NarrativeConsistency.CONSISTENT

        # Version
        assert features.feature_version == "1.0"

    def test_different_pair(
        self, report_a, report_c, default_config
    ):
        """Two clearly different reports should have no clinical overlap."""
        stats = FrequencyStats(
            N_total=100_000, N_drugs=5000, N_AEs=8000, N_drugs_AEs=2000
        )
        features = generate_pair_features(
            r1=report_a,
            r2=report_c,
            stats=stats,
            narrative_consistency=NarrativeConsistency.INCONSISTENT,
            config=default_config,
        )

        assert features.matching_drugs == []
        assert features.matching_AEs == []

        # Should have inconsistencies (sex mismatch, large age diff)
        assert features.inconsistency_counts.strong >= 1  # sex
        assert features.reporting_context.level == ReportingContextLevel.GLOBAL

    def test_custom_pair_id(
        self, report_a, report_b, stats_similar, default_config
    ):
        features = generate_pair_features(
            r1=report_a,
            r2=report_b,
            stats=stats_similar,
            narrative_consistency=NarrativeConsistency.NEUTRAL,
            config=default_config,
            pair_id="CUSTOM_PAIR_001",
        )
        assert features.pair_id == "CUSTOM_PAIR_001"

    def test_empty_report_pair(self, report_empty, default_config):
        """Two empty reports should produce features without errors."""
        stats = FrequencyStats(
            N_total=100_000, N_drugs=0, N_AEs=0, N_drugs_AEs=0
        )
        features = generate_pair_features(
            r1=report_empty,
            r2=report_empty,
            stats=stats,
            narrative_consistency=NarrativeConsistency.NEUTRAL,
            config=default_config,
        )

        assert features.matching_drugs == []
        assert features.matching_AEs == []
        assert features.inconsistency_counts.moderate == 0
        assert features.inconsistency_counts.strong == 0

    def test_inconsistency_counts_aggregated(
        self, report_a, report_c, default_config
    ):
        """Inconsistency counts should correctly aggregate by severity."""
        stats = FrequencyStats(
            N_total=100_000, N_drugs=5000, N_AEs=8000, N_drugs_AEs=2000
        )
        features = generate_pair_features(
            r1=report_a,
            r2=report_c,
            stats=stats,
            narrative_consistency=NarrativeConsistency.INCONSISTENT,
            config=default_config,
        )

        total_from_list = len(features.inconsistencies)
        total_from_counts = (
            features.inconsistency_counts.moderate
            + features.inconsistency_counts.strong
        )
        assert total_from_list == total_from_counts

    def test_clinical_date_inconsistency_counts_present(
        self, report_a, report_b, stats_similar, default_config
    ):
        """clinical_date_inconsistency_counts must be present with integer fields."""
        features = generate_pair_features(
            r1=report_a,
            r2=report_b,
            stats=stats_similar,
            narrative_consistency=NarrativeConsistency.NEUTRAL,
            config=default_config,
        )
        cdc = features.clinical_date_inconsistency_counts
        assert cdc is not None
        assert isinstance(cdc.n_minor, int)
        assert isinstance(cdc.n_moderate, int)
        assert isinstance(cdc.n_strong, int)
        assert isinstance(cdc.n_decisive, int)
        assert cdc.n_minor >= 0
        assert cdc.n_moderate >= 0
        assert cdc.n_strong >= 0
        assert cdc.n_decisive >= 0

    def test_evidence_summary_present(
        self, report_a, report_b, stats_similar, default_config
    ):
        features = generate_pair_features(
            r1=report_a,
            r2=report_b,
            stats=stats_similar,
            narrative_consistency=NarrativeConsistency.CONSISTENT,
            config=default_config,
        )
        assert features.evidence_summary is not None
        assert "drugs" in features.evidence_summary.shared
        assert "adverse_events" in features.evidence_summary.shared

    def test_serialization_roundtrip(
        self, report_a, report_b, stats_similar, default_config
    ):
        """PairFeatures should survive JSON serialization."""
        features = generate_pair_features(
            r1=report_a,
            r2=report_b,
            stats=stats_similar,
            narrative_consistency=NarrativeConsistency.CONSISTENT,
            config=default_config,
        )
        from llm_dup_dev.models.pair_features import PairFeatures

        json_str = features.model_dump_json()
        restored = PairFeatures.model_validate_json(json_str)
        assert restored.pair_id == features.pair_id
        assert restored.L_match == features.L_match
        assert restored.matching_drugs == features.matching_drugs
