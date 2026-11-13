"""Tests for data access protocols and synthetic implementations."""

import pytest

from llm_dup_dev.data.protocols import ReportLoader, StatsProvider
from llm_dup_dev.data.synthetic import SyntheticReportLoader, SyntheticStatsProvider
from llm_dup_dev.feature_generation.probability import FrequencyStats
from llm_dup_dev.models.report_pair import ReportPair


# ===================================================================
# Protocol conformance
# ===================================================================

class TestProtocolConformance:
    """Verify that synthetic implementations satisfy the protocols."""

    def test_report_loader_is_protocol(self, report_a, report_b):
        loader = SyntheticReportLoader({
            report_a.report_id: report_a,
            report_b.report_id: report_b,
        })
        assert isinstance(loader, ReportLoader)

    def test_stats_provider_is_protocol(self):
        provider = SyntheticStatsProvider()
        assert isinstance(provider, StatsProvider)


# ===================================================================
# SyntheticReportLoader
# ===================================================================

class TestSyntheticReportLoader:

    def test_load_single_report(self, report_a):
        loader = SyntheticReportLoader({report_a.report_id: report_a})
        result = loader.load_report(report_a.report_id)
        assert result.report_id == report_a.report_id

    def test_load_missing_report_raises(self, report_a):
        loader = SyntheticReportLoader({report_a.report_id: report_a})
        with pytest.raises(KeyError):
            loader.load_report("NONEXISTENT")

    def test_load_pair(self, report_a, report_b):
        loader = SyntheticReportLoader({
            report_a.report_id: report_a,
            report_b.report_id: report_b,
        })
        pair = ReportPair.from_reports(report_a, report_b)
        r1, r2 = loader.load_pair(pair)
        assert r1.report_id == report_a.report_id
        assert r2.report_id == report_b.report_id

    def test_load_pairs_iterator(self, report_a, report_b, report_c):
        loader = SyntheticReportLoader({
            report_a.report_id: report_a,
            report_b.report_id: report_b,
            report_c.report_id: report_c,
        })
        pairs = [
            ReportPair.from_report_ids("R001", "R002"),
            ReportPair.from_report_ids("R001", "R003"),
        ]
        results = list(loader.load_pairs(pairs))
        assert len(results) == 2
        assert results[0][0].pair_id == "R001__R002"
        assert results[1][0].pair_id == "R001__R003"

    def test_report_ids(self, report_a, report_b):
        loader = SyntheticReportLoader({
            report_a.report_id: report_a,
            report_b.report_id: report_b,
        })
        assert sorted(loader.report_ids) == ["R001", "R002"]


# ===================================================================
# SyntheticStatsProvider
# ===================================================================

class TestSyntheticStatsProvider:

    def test_default_stats(self):
        provider = SyntheticStatsProvider(
            default_N_total=10000,
            default_N_drugs=500,
            default_N_AEs=400,
            default_N_drugs_AEs=80,
        )
        stats = provider.compute_stats(["aspirin"], ["headache"], "US", 2020)
        assert stats.N_total == 10000
        assert stats.N_drugs == 500
        assert stats.N_AEs == 400
        assert stats.N_drugs_AEs == 80

    def test_override(self):
        provider = SyntheticStatsProvider()
        override = FrequencyStats(
            N_total=100, N_drugs=10, N_AEs=15, N_drugs_AEs=3,
        )
        key = provider._context_key(["aspirin"], ["headache"], "US", 2020)
        provider.register_override(key, override)

        stats = provider.compute_stats(["aspirin"], ["headache"], "US", 2020)
        assert stats.N_total == 100
        assert stats.N_drugs == 10

    def test_override_does_not_affect_other_contexts(self):
        provider = SyntheticStatsProvider(default_N_total=5000)
        override = FrequencyStats(
            N_total=100, N_drugs=10, N_AEs=15, N_drugs_AEs=3,
        )
        key = provider._context_key(["aspirin"], ["headache"], "US", 2020)
        provider.register_override(key, override)

        # Different context → default
        stats = provider.compute_stats(["ibuprofen"], ["headache"], "US", 2020)
        assert stats.N_total == 5000

    def test_context_key_deterministic(self):
        """Same inputs always produce same key, regardless of order."""
        provider = SyntheticStatsProvider()
        key1 = provider._context_key(["b", "a"], ["y", "x"], "US", 2020)
        key2 = provider._context_key(["a", "b"], ["x", "y"], "US", 2020)
        assert key1 == key2

    def test_context_key_none_values(self):
        provider = SyntheticStatsProvider()
        key = provider._context_key([], [], None, None)
        assert "None" in key
