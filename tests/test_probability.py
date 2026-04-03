"""Tests for probability estimation and L_match computation."""

import math

import pytest

from llm_dup_dev.classifier.config import DomainSizeConfig
from llm_dup_dev.feature_generation.matching import DemographicMatch, MatchResult
from llm_dup_dev.feature_generation.probability import (
    SMOOTHING_PSEUDOCOUNT,
    FrequencyStats,
    compute_probability,
    leave_pair_out_probability,
)
from llm_dup_dev.models.enums import AnchorType
from llm_dup_dev.models.pair_features import AnchorPopulation


@pytest.fixture
def basic_match():
    """A match result with overlapping drugs, AEs, and some demographics."""
    return MatchResult(
        matching_drugs=["pregabalin", "sertraline"],
        matching_AEs=["somnolence"],
        drugs_only_r1=[],
        drugs_only_r2=["omeprazole"],
        aes_only_r1=["dizziness"],
        aes_only_r2=["nausea"],
        demographic_matches=[
            DemographicMatch(
                feature="patient_sex",
                matched=True,
                values_r1=["female"],
                values_r2=["female"],
            ),
            DemographicMatch(
                feature="patient_age",
                matched=True,
                values_r1=[54],
                values_r2=[54],
            ),
            DemographicMatch(
                feature="patient_height",
                matched=False,
                values_r1=[165],
                values_r2=[170],
            ),
            DemographicMatch(
                feature="patient_weight",
                matched=False,
                values_r1=[68],
                values_r2=[75],
            ),
            DemographicMatch(
                feature="report_date",
                matched=True,
                values_r1=["2020-06-12"],
                values_r2=["2020-06-12"],
                date_resolution="day",
            ),
        ],
    )


@pytest.fixture
def anchor_drugs():
    return AnchorPopulation(
        N_drugs=48,
        N_AEs=200,
        N_drugs_AEs=12,
        type=AnchorType.DRUGS,
        N_anchor=48,
    )


@pytest.fixture
def stats():
    return FrequencyStats(
        N_total=100_000, N_drugs=48, N_AEs=200, N_drugs_AEs=12
    )


@pytest.fixture
def domain_config():
    return DomainSizeConfig()


class TestProbabilityComputation:
    def test_basic_produces_positive_lmatch(
        self, basic_match, anchor_drugs, stats, domain_config
    ):
        result = compute_probability(
            basic_match, anchor_drugs, stats, domain_config
        )
        assert result.L_match > 0
        assert result.p_joint > 0
        assert result.p_joint < 1
        assert result.expected_collisions >= 0

    def test_estimate_carries_feature_contributions(
        self, basic_match, anchor_drugs, stats, domain_config
    ):
        """ProbabilityResult.estimate should include per-feature contributions."""
        result = compute_probability(
            basic_match, anchor_drugs, stats, domain_config
        )
        estimate = result.estimate

        # Should have the same number of contributions as internal feature_probabilities
        assert len(estimate.feature_contributions) == len(result.feature_probabilities)

        # Each contribution should mirror the internal FeatureProbability
        for fc, fp in zip(estimate.feature_contributions, result.feature_probabilities):
            assert fc.feature == fp.feature
            assert fc.p_f == fp.p_f
            assert fc.method == fp.method
            assert fc.domain_size == fp.domain_size
            assert fc.description == fp.description

    def test_feature_contributions_product_equals_p_joint(
        self, basic_match, anchor_drugs, stats, domain_config
    ):
        """Product of all feature p_f values should equal p_joint."""
        result = compute_probability(
            basic_match, anchor_drugs, stats, domain_config
        )
        estimate = result.estimate

        product = 1.0
        for fc in estimate.feature_contributions:
            product *= fc.p_f

        assert abs(product - estimate.p_joint) < 1e-15

    def test_feature_contributions_include_expected_features(
        self, basic_match, anchor_drugs, stats, domain_config
    ):
        """With DRUGS anchor: should see ae_set, patient_sex, patient_age, report_date."""
        result = compute_probability(
            basic_match, anchor_drugs, stats, domain_config
        )
        estimate = result.estimate
        feature_names = {fc.feature for fc in estimate.feature_contributions}

        # AE set should be included (anchor is DRUGS, not AES)
        assert "ae_set" in feature_names
        # Drug set should NOT be included (anchor is DRUGS)
        assert "drug_set" not in feature_names
        # Matched demographics
        assert "patient_sex" in feature_names
        assert "patient_age" in feature_names
        assert "report_date" in feature_names
        # Unmatched demographics should not appear
        assert "patient_height" not in feature_names
        assert "patient_weight" not in feature_names

    def test_feature_contributions_method_types(
        self, basic_match, anchor_drugs, stats, domain_config
    ):
        """AE set should be leave_pair_out; demographics should be domain_size."""
        result = compute_probability(
            basic_match, anchor_drugs, stats, domain_config
        )
        estimate = result.estimate

        for fc in estimate.feature_contributions:
            if fc.feature == "ae_set":
                assert fc.method == "leave_pair_out"
                assert fc.domain_size is None
            elif fc.feature in ("patient_sex", "patient_age", "report_date"):
                assert fc.method == "domain_size"
                assert fc.domain_size is not None
                assert fc.domain_size > 0

    def test_empty_contributions_when_no_features(self, domain_config):
        """No matched features → empty feature_contributions list."""
        empty_match = MatchResult(
            matching_drugs=[],
            matching_AEs=[],
            demographic_matches=[],
        )
        anchor = AnchorPopulation(
            N_drugs=100,
            N_AEs=100,
            N_drugs_AEs=50,
            type=AnchorType.DRUGS_AES,
            N_anchor=50,
        )
        stats = FrequencyStats(
            N_total=100_000, N_drugs=100, N_AEs=100, N_drugs_AEs=50
        )
        result = compute_probability(empty_match, anchor, stats, domain_config)
        estimate = result.estimate
        assert estimate.feature_contributions == []
        assert estimate.p_joint == 1.0

    def test_drugs_excluded_when_anchor(
        self, basic_match, anchor_drugs, stats, domain_config
    ):
        """When anchor is DRUGS, drug set should NOT contribute."""
        result = compute_probability(
            basic_match, anchor_drugs, stats, domain_config
        )
        drug_feats = [
            fp for fp in result.feature_probabilities if fp.feature == "drug_set"
        ]
        assert len(drug_feats) == 0

    def test_aes_included_when_anchor_is_drugs(
        self, basic_match, anchor_drugs, stats, domain_config
    ):
        """When anchor is DRUGS, AE set SHOULD contribute."""
        result = compute_probability(
            basic_match, anchor_drugs, stats, domain_config
        )
        ae_feats = [
            fp for fp in result.feature_probabilities if fp.feature == "ae_set"
        ]
        assert len(ae_feats) == 1

    def test_both_excluded_when_anchor_drugs_aes(
        self, basic_match, stats, domain_config
    ):
        """When anchor is DRUGS_AES, neither drug nor AE set contributes."""
        anchor = AnchorPopulation(
            N_drugs=200,
            N_AEs=300,
            N_drugs_AEs=50,
            type=AnchorType.DRUGS_AES,
            N_anchor=50,
        )
        result = compute_probability(
            basic_match, anchor, stats, domain_config
        )
        clinical_feats = [
            fp
            for fp in result.feature_probabilities
            if fp.feature in ("drug_set", "ae_set")
        ]
        assert len(clinical_feats) == 0

    def test_no_matched_features_lmatch_zero(self, domain_config):
        """With no matched features, L_match should be 0."""
        empty_match = MatchResult(
            matching_drugs=[],
            matching_AEs=[],
            demographic_matches=[],
        )
        anchor = AnchorPopulation(
            N_drugs=100,
            N_AEs=100,
            N_drugs_AEs=50,
            type=AnchorType.DRUGS_AES,
            N_anchor=50,
        )
        stats = FrequencyStats(
            N_total=100_000, N_drugs=100, N_AEs=100, N_drugs_AEs=50
        )
        result = compute_probability(empty_match, anchor, stats, domain_config)
        # p_joint=1.0, E_joint = 50*49/2 = 1225 → L_match = max(0, -log10(1225)) = 0
        assert result.L_match == 0.0

    def test_rare_combination_high_lmatch(self, domain_config):
        """Extremely rare feature combinations should produce high L_match."""
        match = MatchResult(
            matching_drugs=["rare_drug"],
            matching_AEs=["rare_ae"],
            demographic_matches=[
                DemographicMatch(
                    feature="patient_age",
                    matched=True,
                    values_r1=[54],
                    values_r2=[54],
                ),
                DemographicMatch(
                    feature="patient_sex",
                    matched=True,
                    values_r1=["f"],
                    values_r2=["f"],
                ),
                DemographicMatch(
                    feature="report_date",
                    matched=True,
                    values_r1=["2020-06-12"],
                    values_r2=["2020-06-12"],
                    date_resolution="day",
                ),
            ],
        )
        anchor = AnchorPopulation(
            N_drugs=5,
            N_AEs=3,
            N_drugs_AEs=1,
            type=AnchorType.AES,
            N_anchor=3,
        )
        stats = FrequencyStats(
            N_total=500_000, N_drugs=5, N_AEs=3, N_drugs_AEs=1
        )
        result = compute_probability(match, anchor, stats, domain_config)
        assert result.L_match > 5  # very strong evidence

    def test_single_anchor_report_gives_zero_collisions(self, domain_config):
        """N_anchor=1 → 0 possible pairs → E_joint=0 → high L_match."""
        match = MatchResult(
            matching_drugs=["drug_a"],
            matching_AEs=["ae_a"],
            demographic_matches=[
                DemographicMatch(
                    feature="patient_age",
                    matched=True,
                    values_r1=[30],
                    values_r2=[30],
                ),
            ],
        )
        anchor = AnchorPopulation(
            N_drugs=1,
            N_AEs=50,
            N_drugs_AEs=1,
            type=AnchorType.DRUGS,
            N_anchor=1,
        )
        stats = FrequencyStats(
            N_total=100_000, N_drugs=1, N_AEs=50, N_drugs_AEs=1
        )
        result = compute_probability(match, anchor, stats, domain_config)
        # N_anchor=1 → 0 pairs → expected_collisions=0
        assert result.expected_collisions == 0.0
        # L_match = -log10(0 + epsilon) should be very high
        assert result.L_match > 10

    def test_date_resolution_affects_domain_size(self, domain_config):
        """Month-resolution dates should use a smaller domain size."""
        match_day = MatchResult(
            matching_drugs=[],
            matching_AEs=[],
            demographic_matches=[
                DemographicMatch(
                    feature="report_date",
                    matched=True,
                    date_resolution="day",
                ),
            ],
        )
        match_month = MatchResult(
            matching_drugs=[],
            matching_AEs=[],
            demographic_matches=[
                DemographicMatch(
                    feature="report_date",
                    matched=True,
                    date_resolution="month",
                ),
            ],
        )
        anchor = AnchorPopulation(
            N_drugs=100,
            N_AEs=100,
            N_drugs_AEs=50,
            type=AnchorType.DRUGS_AES,
            N_anchor=50,
        )
        stats = FrequencyStats(
            N_total=100_000, N_drugs=100, N_AEs=100, N_drugs_AEs=50
        )

        result_day = compute_probability(
            match_day, anchor, stats, domain_config
        )
        result_month = compute_probability(
            match_month, anchor, stats, domain_config
        )

        # Day resolution has larger D_f → smaller p_f → higher L_match
        assert result_day.L_match > result_month.L_match


# ---------------------------------------------------------------------------
# Leave-pair-out probability helper tests
# ---------------------------------------------------------------------------


class TestLeavePairOutProbability:
    """Tests for the leave_pair_out_probability helper function."""

    def test_standard_leave_pair_out(self):
        """A. n_anchor=25, k_anchor=3, pair contributes 2 → k_other=1/n_other=23."""
        m = SMOOTHING_PSEUDOCOUNT  # 10
        p0 = 50 / 1000  # 0.05

        result = leave_pair_out_probability(
            n_anchor=25,
            k_anchor_and_feature=3,
            n_feature_in_context=50,
            n_total_in_context=1000,
        )

        # Expected: (1 + 10*0.05) / (23 + 10) = (1 + 0.5) / 33 = 1.5/33
        expected = (1 + m * p0) / (23 + m)
        assert result == pytest.approx(expected)
        assert result == pytest.approx(1.5 / 33)

    def test_extreme_sparse_zero_k_other(self):
        """B. k_anchor=2, pair are the only two → k_other=0, smoothed toward p0."""
        m = SMOOTHING_PSEUDOCOUNT
        p0 = 50 / 1000  # 0.05

        result = leave_pair_out_probability(
            n_anchor=25,
            k_anchor_and_feature=2,
            n_feature_in_context=50,
            n_total_in_context=1000,
        )

        # Expected: (0 + 10*0.05) / (23 + 10) = 0.5/33
        expected = (0 + m * p0) / (23 + m)
        assert result == pytest.approx(expected)
        assert result > 0  # smoothing prevents zero

    def test_symmetry_ae_anchor_drug_feature(self):
        """C. AEs are anchor, drugs are additional feature."""
        # Anchor = AEs, N_anchor = N_AEs = 30
        # Feature = drugs, k = N_drugs_AEs = 5
        # p0 = N_drugs / N_total = 100 / 5000
        m = SMOOTHING_PSEUDOCOUNT
        result = leave_pair_out_probability(
            n_anchor=30,
            k_anchor_and_feature=5,
            n_feature_in_context=100,
            n_total_in_context=5000,
        )
        p0 = 100 / 5000
        expected = (3 + m * p0) / (28 + m)
        assert result == pytest.approx(expected)

    def test_symmetry_drug_anchor_ae_feature(self):
        """C. Drugs are anchor, AEs are additional feature."""
        # Anchor = drugs, N_anchor = N_drugs = 30
        # Feature = AEs, k = N_drugs_AEs = 5
        # p0 = N_AEs / N_total = 200 / 5000
        m = SMOOTHING_PSEUDOCOUNT
        result = leave_pair_out_probability(
            n_anchor=30,
            k_anchor_and_feature=5,
            n_feature_in_context=200,
            n_total_in_context=5000,
        )
        p0 = 200 / 5000
        expected = (3 + m * p0) / (28 + m)
        assert result == pytest.approx(expected)

    def test_tiny_anchor_size_2(self):
        """D. n_anchor=2, the minimum real pair → n_other=0."""
        m = SMOOTHING_PSEUDOCOUNT
        p0 = 100 / 5000

        result = leave_pair_out_probability(
            n_anchor=2,
            k_anchor_and_feature=2,
            n_feature_in_context=100,
            n_total_in_context=5000,
        )

        # n_other=0, k_other=0 → purely prior-driven
        expected = (0 + m * p0) / (0 + m)
        assert result == pytest.approx(expected)
        assert result == pytest.approx(p0)  # reduces to p0 when n_other=0

    def test_tiny_anchor_size_1(self):
        """D. n_anchor=1, degenerate → n_other=0 (clamped)."""
        m = SMOOTHING_PSEUDOCOUNT
        p0 = 100 / 5000

        result = leave_pair_out_probability(
            n_anchor=1,
            k_anchor_and_feature=1,
            n_feature_in_context=100,
            n_total_in_context=5000,
        )

        # n_other=0, k_other=0 → purely prior-driven
        expected = (0 + m * p0) / (0 + m)
        assert result == pytest.approx(expected)

    def test_clamping_k_at_zero(self):
        """D. k_anchor_and_feature=0 → k_other stays 0 (no negative)."""
        result = leave_pair_out_probability(
            n_anchor=25,
            k_anchor_and_feature=0,
            n_feature_in_context=50,
            n_total_in_context=1000,
        )
        # k_other = max(0, 0-2) = 0
        m = SMOOTHING_PSEUDOCOUNT
        p0 = 50 / 1000
        expected = (0 + m * p0) / (23 + m)
        assert result == pytest.approx(expected)

    def test_clamping_n_at_zero(self):
        """D. n_anchor=0 → n_other stays 0 (clamped)."""
        result = leave_pair_out_probability(
            n_anchor=0,
            k_anchor_and_feature=0,
            n_feature_in_context=50,
            n_total_in_context=1000,
        )
        m = SMOOTHING_PSEUDOCOUNT
        p0 = 50 / 1000
        expected = (0 + m * p0) / (0 + m)
        assert result == pytest.approx(expected)

    def test_p0_zero(self):
        """D. p0=0 (feature never seen in context) → smoothed result is k_other/(n_other+m)."""
        result = leave_pair_out_probability(
            n_anchor=25,
            k_anchor_and_feature=3,
            n_feature_in_context=0,
            n_total_in_context=1000,
        )
        m = SMOOTHING_PSEUDOCOUNT
        # p0 = 0, k_other=1, n_other=23
        expected = 1 / (23 + m)
        assert result == pytest.approx(expected)
        assert result > 0  # no division by zero

    def test_n_total_zero_returns_none(self):
        """D. n_total=0 → cannot compute p0 → returns None."""
        result = leave_pair_out_probability(
            n_anchor=25,
            k_anchor_and_feature=3,
            n_feature_in_context=50,
            n_total_in_context=0,
        )
        assert result is None

    def test_result_always_positive_with_valid_inputs(self):
        """Smoothing should ensure positive result when p0 > 0."""
        result = leave_pair_out_probability(
            n_anchor=100,
            k_anchor_and_feature=2,
            n_feature_in_context=1,
            n_total_in_context=100_000,
        )
        assert result is not None
        assert result > 0

    def test_no_log_domain_errors(self):
        """Result should be safe for log computation (positive and finite)."""
        result = leave_pair_out_probability(
            n_anchor=2,
            k_anchor_and_feature=2,
            n_feature_in_context=1,
            n_total_in_context=100_000,
        )
        assert result is not None
        assert result > 0
        assert math.isfinite(math.log10(result))


# ---------------------------------------------------------------------------
# Integration: leave-pair-out in compute_probability
# ---------------------------------------------------------------------------


class TestLeavePairOutIntegration:
    """Tests that compute_probability uses leave-pair-out for drug/AE features."""

    def test_ae_feature_uses_leave_pair_out_when_anchor_drugs(self):
        """When anchor is DRUGS, AE set should use leave_pair_out method."""
        match = MatchResult(
            matching_drugs=["drug_a"],
            matching_AEs=["ae_a"],
            demographic_matches=[],
        )
        anchor = AnchorPopulation(
            N_drugs=25, N_AEs=200, N_drugs_AEs=3,
            type=AnchorType.DRUGS, N_anchor=25,
        )
        stats = FrequencyStats(N_total=1000, N_drugs=25, N_AEs=200, N_drugs_AEs=3)
        config = DomainSizeConfig()

        result = compute_probability(match, anchor, stats, config)

        ae_feats = [fp for fp in result.feature_probabilities if fp.feature == "ae_set"]
        assert len(ae_feats) == 1
        assert ae_feats[0].method == "leave_pair_out"

        # Verify the actual value: (1 + 10*0.2) / (23 + 10) = 3.0/33
        m = SMOOTHING_PSEUDOCOUNT
        p0 = 200 / 1000
        expected = (1 + m * p0) / (23 + m)
        assert ae_feats[0].p_f == pytest.approx(expected)

    def test_drug_feature_uses_leave_pair_out_when_anchor_aes(self):
        """When anchor is AES, drug set should use leave_pair_out method."""
        match = MatchResult(
            matching_drugs=["drug_a"],
            matching_AEs=["ae_a"],
            demographic_matches=[],
        )
        anchor = AnchorPopulation(
            N_drugs=200, N_AEs=25, N_drugs_AEs=3,
            type=AnchorType.AES, N_anchor=25,
        )
        stats = FrequencyStats(N_total=1000, N_drugs=200, N_AEs=25, N_drugs_AEs=3)
        config = DomainSizeConfig()

        result = compute_probability(match, anchor, stats, config)

        drug_feats = [fp for fp in result.feature_probabilities if fp.feature == "drug_set"]
        assert len(drug_feats) == 1
        assert drug_feats[0].method == "leave_pair_out"

        # Verify the actual value: (1 + 10*(200/1000)) / (23 + 10) = 3.0/33
        m = SMOOTHING_PSEUDOCOUNT
        p0 = 200 / 1000
        expected = (1 + m * p0) / (23 + m)
        assert drug_feats[0].p_f == pytest.approx(expected)

    def test_description_shows_leave_pair_out_counts(self):
        """Description should show k_other/n_other and p0 numerator/denominator."""
        match = MatchResult(
            matching_drugs=["drug_a"],
            matching_AEs=["ae_a"],
            demographic_matches=[],
        )
        anchor = AnchorPopulation(
            N_drugs=25, N_AEs=200, N_drugs_AEs=3,
            type=AnchorType.DRUGS, N_anchor=25,
        )
        stats = FrequencyStats(N_total=1000, N_drugs=25, N_AEs=200, N_drugs_AEs=3)
        config = DomainSizeConfig()

        result = compute_probability(match, anchor, stats, config)
        ae_feat = [fp for fp in result.feature_probabilities if fp.feature == "ae_set"][0]

        assert "k_other=1/23" in ae_feat.description
        assert "p0=200/1000" in ae_feat.description
