"""Tests for probability estimation and L_match computation."""

import math

import pytest

from llm_dup_dev.classifier.config import DomainSizeConfig
from llm_dup_dev.feature_generation.matching import (
    ClinicalDateMatch,
    DemographicMatch,
    MatchResult,
)
from llm_dup_dev.feature_generation.probability import (
    MARGINAL_FLOOR,
    SMOOTHING_PSEUDOCOUNT,
    FrequencyStats,
    _clinical_date_domain_sizes,
    _marginal_with_backoff,
    _smoothed_marginal,
    compute_probability,
    leave_pair_out_probability,
)
from llm_dup_dev.models.enums import AnchorType, ReportingContextLevel
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
        """With DRUGS anchor: should see ae_set, patient_sex, patient_age (not report_date)."""
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
        # Report date is excluded from probability computation
        assert "report_date" not in feature_names
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
            elif fc.feature in ("patient_sex", "patient_age"):
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
        """Month-resolution dates should use a smaller domain size.

        Uses a non-report_date feature since report_date is excluded
        from probability computation.
        """
        match_day = MatchResult(
            matching_drugs=[],
            matching_AEs=[],
            demographic_matches=[
                DemographicMatch(
                    feature="drug_start_date",
                    matched=True,
                    date_resolution="day",
                    values_r1=["2020-06-12"],
                    values_r2=["2020-06-12"],
                ),
            ],
        )
        match_month = MatchResult(
            matching_drugs=[],
            matching_AEs=[],
            demographic_matches=[
                DemographicMatch(
                    feature="drug_start_date",
                    matched=True,
                    date_resolution="month",
                    values_r1=["2020-06"],
                    values_r2=["2020-06"],
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

    def test_report_date_excluded_from_probability(self, domain_config):
        """Report date should NOT contribute to probability computation."""
        match_with_date = MatchResult(
            matching_drugs=[],
            matching_AEs=[],
            demographic_matches=[
                DemographicMatch(
                    feature="patient_sex",
                    matched=True,
                    values_r1=["female"],
                    values_r2=["female"],
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
        match_without_date = MatchResult(
            matching_drugs=[],
            matching_AEs=[],
            demographic_matches=[
                DemographicMatch(
                    feature="patient_sex",
                    matched=True,
                    values_r1=["female"],
                    values_r2=["female"],
                ),
            ],
        )
        anchor = AnchorPopulation(
            N_drugs=100, N_AEs=100, N_drugs_AEs=50,
            type=AnchorType.DRUGS_AES, N_anchor=50,
        )
        stats = FrequencyStats(
            N_total=100_000, N_drugs=100, N_AEs=100, N_drugs_AEs=50
        )

        result_with = compute_probability(match_with_date, anchor, stats, domain_config)
        result_without = compute_probability(match_without_date, anchor, stats, domain_config)

        # report_date should have no effect on probability
        assert result_with.p_joint == result_without.p_joint
        assert result_with.L_match == result_without.L_match

        # report_date should not appear in feature contributions
        features = {fp.feature for fp in result_with.feature_probabilities}
        assert "report_date" not in features


class TestDateDomainSize:
    """Tests for context-dependent date domain size."""

    def test_country_year_default_540(self):
        """Country-year context should use 540 days (1.5 years) as default."""
        match = MatchResult(
            matching_drugs=[],
            matching_AEs=[],
            demographic_matches=[
                DemographicMatch(
                    feature="drug_start_date",
                    matched=True,
                    date_resolution="day",
                    values_r1=["2020-06-12"],
                    values_r2=["2020-06-12"],
                ),
            ],
        )
        anchor = AnchorPopulation(
            N_drugs=100, N_AEs=100, N_drugs_AEs=50,
            type=AnchorType.DRUGS_AES, N_anchor=50,
        )
        stats = FrequencyStats(
            N_total=100_000, N_drugs=100, N_AEs=100, N_drugs_AEs=50
        )
        config = DomainSizeConfig()
        result = compute_probability(
            match, anchor, stats, config,
            reporting_context_level=ReportingContextLevel.COUNTRY_YEAR,
        )
        date_feat = [fp for fp in result.feature_probabilities if fp.feature == "drug_start_date"]
        assert len(date_feat) == 1
        assert date_feat[0].domain_size == 540

    def test_global_default_3650(self):
        """Global context should use 3650 days as default date domain."""
        match = MatchResult(
            matching_drugs=[],
            matching_AEs=[],
            demographic_matches=[
                DemographicMatch(
                    feature="drug_start_date",
                    matched=True,
                    date_resolution="day",
                    values_r1=["2020-06-12"],
                    values_r2=["2020-06-12"],
                ),
            ],
        )
        anchor = AnchorPopulation(
            N_drugs=100, N_AEs=100, N_drugs_AEs=50,
            type=AnchorType.DRUGS_AES, N_anchor=50,
        )
        stats = FrequencyStats(
            N_total=100_000, N_drugs=100, N_AEs=100, N_drugs_AEs=50
        )
        config = DomainSizeConfig()
        result = compute_probability(
            match, anchor, stats, config,
            reporting_context_level=ReportingContextLevel.GLOBAL,
        )
        date_feat = [fp for fp in result.feature_probabilities if fp.feature == "drug_start_date"]
        assert len(date_feat) == 1
        assert date_feat[0].domain_size == 3650

# ---------------------------------------------------------------------------
# Clinical date probability integration
# ---------------------------------------------------------------------------


class TestClinicalDateProbability:
    """Tests that matched clinical dates contribute to collision probability."""

    @pytest.fixture
    def simple_anchor(self):
        return AnchorPopulation(
            N_drugs=100, N_AEs=100, N_drugs_AEs=50,
            type=AnchorType.DRUGS_AES, N_anchor=50,
        )

    @pytest.fixture
    def simple_stats(self):
        return FrequencyStats(
            N_total=100_000, N_drugs=100, N_AEs=100, N_drugs_AEs=50,
        )

    def test_clinical_dates_contribute_to_probability(
        self, simple_anchor, simple_stats
    ):
        """Matched clinical dates should lower p_joint and raise L_match."""
        match_no_dates = MatchResult(
            matching_drugs=["aspirin"],
            matching_AEs=[],
            demographic_matches=[],
            matched_clinical_dates=[],
        )
        match_with_dates = MatchResult(
            matching_drugs=["aspirin"],
            matching_AEs=[],
            demographic_matches=[],
            matched_clinical_dates=[
                ClinicalDateMatch(matched_value="1994-03-30", resolution="day"),
                ClinicalDateMatch(matched_value="1994-05-12", resolution="day"),
            ],
        )
        config = DomainSizeConfig()

        result_no = compute_probability(
            match_no_dates, simple_anchor, simple_stats, config
        )
        result_with = compute_probability(
            match_with_dates, simple_anchor, simple_stats, config
        )

        # Clinical dates should lower p_joint
        assert result_with.p_joint < result_no.p_joint
        # And raise L_match
        assert result_with.L_match > result_no.L_match

    def test_clinical_date_feature_name_and_method(
        self, simple_anchor, simple_stats
    ):
        """Clinical date features should use 'clinical_date' name and domain_size method."""
        match = MatchResult(
            matching_drugs=[],
            matching_AEs=[],
            demographic_matches=[],
            matched_clinical_dates=[
                ClinicalDateMatch(matched_value="2020-06-01", resolution="day"),
            ],
        )
        config = DomainSizeConfig()
        result = compute_probability(match, simple_anchor, simple_stats, config)

        clinical_feats = [
            fp for fp in result.feature_probabilities
            if fp.feature == "clinical_date"
        ]
        assert len(clinical_feats) == 1
        assert clinical_feats[0].method == "domain_size"
        assert clinical_feats[0].domain_size == 3650  # global default
        assert "2020-06-01" in clinical_feats[0].description

    def test_two_dates_contribute_independently(
        self, simple_anchor, simple_stats
    ):
        """Two unique matched dates should produce two feature contributions."""
        match = MatchResult(
            matching_drugs=[],
            matching_AEs=[],
            demographic_matches=[],
            matched_clinical_dates=[
                ClinicalDateMatch(matched_value="1994-03-30", resolution="day"),
                ClinicalDateMatch(matched_value="1994-05-12", resolution="day"),
            ],
        )
        config = DomainSizeConfig()
        result = compute_probability(match, simple_anchor, simple_stats, config)

        clinical_feats = [
            fp for fp in result.feature_probabilities
            if fp.feature == "clinical_date"
        ]
        assert len(clinical_feats) == 2
        # Under the sequential local-gap rule: the more recent date
        # (1994-05-12) uses the global default (3650), the older date
        # (1994-03-30) uses 2 × 43-day gap = 86.
        expected_p_joint = (1.0 / 3650) * (1.0 / 86)
        assert result.p_joint == pytest.approx(expected_p_joint)

    def test_clinical_date_country_year_domain(
        self, simple_anchor, simple_stats
    ):
        """Country-year context should use 540-day domain for clinical dates."""
        match = MatchResult(
            matching_drugs=[],
            matching_AEs=[],
            demographic_matches=[],
            matched_clinical_dates=[
                ClinicalDateMatch(matched_value="2020-06-01", resolution="day"),
            ],
        )
        config = DomainSizeConfig()
        result = compute_probability(
            match, simple_anchor, simple_stats, config,
            reporting_context_level=ReportingContextLevel.COUNTRY_YEAR,
        )
        clinical_feats = [
            fp for fp in result.feature_probabilities
            if fp.feature == "clinical_date"
        ]
        assert clinical_feats[0].domain_size == 540

    def test_clinical_date_month_resolution_domain(
        self, simple_anchor, simple_stats
    ):
        """Month-resolution clinical date should use month domain size."""
        match = MatchResult(
            matching_drugs=[],
            matching_AEs=[],
            demographic_matches=[],
            matched_clinical_dates=[
                ClinicalDateMatch(matched_value="2020-06", resolution="month"),
            ],
        )
        config = DomainSizeConfig()
        result = compute_probability(
            match, simple_anchor, simple_stats, config,
        )
        clinical_feats = [
            fp for fp in result.feature_probabilities
            if fp.feature == "clinical_date"
        ]
        # Global default: 3650 days → ceiling(3650/30) = 122 months
        assert clinical_feats[0].domain_size == 122

    def test_clinical_dates_product_in_p_joint(
        self, simple_anchor, simple_stats
    ):
        """Product of all feature p_f values should equal p_joint."""
        match = MatchResult(
            matching_drugs=["aspirin"],
            matching_AEs=["rash"],
            demographic_matches=[
                DemographicMatch(
                    feature="patient_sex", matched=True,
                    values_r1=["female"], values_r2=["female"],
                ),
            ],
            matched_clinical_dates=[
                ClinicalDateMatch(matched_value="2020-06-01", resolution="day"),
            ],
        )
        config = DomainSizeConfig()
        result = compute_probability(
            match, simple_anchor, simple_stats, config,
        )
        product = 1.0
        for fp in result.feature_probabilities:
            product *= fp.p_f
        assert abs(product - result.p_joint) < 1e-15

    def test_empty_clinical_dates_no_contribution(
        self, simple_anchor, simple_stats
    ):
        """Empty matched_clinical_dates should not add any feature contributions."""
        match = MatchResult(
            matching_drugs=[],
            matching_AEs=[],
            demographic_matches=[],
            matched_clinical_dates=[],
        )
        config = DomainSizeConfig()
        result = compute_probability(match, simple_anchor, simple_stats, config)
        clinical_feats = [
            fp for fp in result.feature_probabilities
            if fp.feature == "clinical_date"
        ]
        assert len(clinical_feats) == 0


# ---------------------------------------------------------------------------
# Sequential local-gap rule for clinical dates
# ---------------------------------------------------------------------------


class TestClinicalDateSequentialGap:
    """Tests for the sequential local-gap clinical-date domain-sizing rule."""

    @pytest.fixture
    def simple_anchor(self):
        return AnchorPopulation(
            N_drugs=100, N_AEs=100, N_drugs_AEs=50,
            type=AnchorType.DRUGS_AES, N_anchor=50,
        )

    @pytest.fixture
    def simple_stats(self):
        return FrequencyStats(
            N_total=100_000, N_drugs=100, N_AEs=100, N_drugs_AEs=50,
        )

    def _clinical_feats_by_value(self, result):
        out = {}
        for fp in result.feature_probabilities:
            if fp.feature == "clinical_date":
                # Extract matched_value from description: "clinical_date VAL (res): ..."
                val = fp.description.split(" ", 2)[1]
                out[val] = fp.domain_size
        return out

    def test_single_clinical_date_uses_context_default(
        self, simple_anchor, simple_stats
    ):
        match = MatchResult(
            matching_drugs=[], matching_AEs=[],
            demographic_matches=[],
            matched_clinical_dates=[
                ClinicalDateMatch(matched_value="2025-05-20", resolution="day"),
            ],
        )
        config = DomainSizeConfig()
        result = compute_probability(
            match, simple_anchor, simple_stats, config,
            reporting_context_level=ReportingContextLevel.COUNTRY_YEAR,
        )
        feats = self._clinical_feats_by_value(result)
        assert feats == {"2025-05-20": 540}

    def test_two_nearby_dates_second_gets_local_gap(
        self, simple_anchor, simple_stats
    ):
        match = MatchResult(
            matching_drugs=[], matching_AEs=[],
            demographic_matches=[],
            matched_clinical_dates=[
                ClinicalDateMatch(matched_value="2025-05-20", resolution="day"),
                ClinicalDateMatch(matched_value="2025-05-18", resolution="day"),
            ],
        )
        config = DomainSizeConfig()
        result = compute_probability(
            match, simple_anchor, simple_stats, config,
            reporting_context_level=ReportingContextLevel.COUNTRY_YEAR,
        )
        feats = self._clinical_feats_by_value(result)
        # Most recent gets default; older gets 2 × 2-day gap = 4.
        assert feats == {"2025-05-20": 540, "2025-05-18": 4}
        # p_joint should only include these two date contributions.
        assert result.p_joint == pytest.approx((1 / 540) * (1 / 4))

    def test_clustered_plus_outlier(self, simple_anchor, simple_stats):
        match = MatchResult(
            matching_drugs=[], matching_AEs=[],
            demographic_matches=[],
            matched_clinical_dates=[
                ClinicalDateMatch(matched_value="2022-05-20", resolution="day"),
                ClinicalDateMatch(matched_value="2022-05-18", resolution="day"),
                ClinicalDateMatch(matched_value="2022-05-15", resolution="day"),
                ClinicalDateMatch(matched_value="2019-01-01", resolution="day"),
            ],
        )
        config = DomainSizeConfig()
        result = compute_probability(
            match, simple_anchor, simple_stats, config,
            reporting_context_level=ReportingContextLevel.COUNTRY_YEAR,
        )
        feats = self._clinical_feats_by_value(result)
        # Gap 2022-05-15 → 2019-01-01 = 1230 days → 2 × 1230 = 2460.
        assert feats == {
            "2022-05-20": 540,
            "2022-05-18": 4,
            "2022-05-15": 6,
            "2019-01-01": 2460,
        }

    def test_ordering_independent_of_input_order(
        self, simple_anchor, simple_stats
    ):
        dates_canonical = [
            ClinicalDateMatch(matched_value="2022-05-20", resolution="day"),
            ClinicalDateMatch(matched_value="2022-05-18", resolution="day"),
            ClinicalDateMatch(matched_value="2022-05-15", resolution="day"),
            ClinicalDateMatch(matched_value="2019-01-01", resolution="day"),
        ]
        dates_shuffled = [
            ClinicalDateMatch(matched_value="2019-01-01", resolution="day"),
            ClinicalDateMatch(matched_value="2022-05-18", resolution="day"),
            ClinicalDateMatch(matched_value="2022-05-20", resolution="day"),
            ClinicalDateMatch(matched_value="2022-05-15", resolution="day"),
        ]
        config = DomainSizeConfig()
        context = ReportingContextLevel.COUNTRY_YEAR

        def run(clinical_dates):
            match = MatchResult(
                matching_drugs=[], matching_AEs=[],
                demographic_matches=[],
                matched_clinical_dates=clinical_dates,
            )
            return self._clinical_feats_by_value(
                compute_probability(
                    match, simple_anchor, simple_stats, config,
                    reporting_context_level=context,
                )
            )

        assert run(dates_canonical) == run(dates_shuffled)

    def test_month_resolution_gap(self, simple_anchor, simple_stats):
        match = MatchResult(
            matching_drugs=[], matching_AEs=[],
            demographic_matches=[],
            matched_clinical_dates=[
                ClinicalDateMatch(matched_value="2025-05", resolution="month"),
                ClinicalDateMatch(matched_value="2025-02", resolution="month"),
            ],
        )
        config = DomainSizeConfig()
        result = compute_probability(
            match, simple_anchor, simple_stats, config,
            reporting_context_level=ReportingContextLevel.GLOBAL,
        )
        feats = self._clinical_feats_by_value(result)
        # Global default for month resolution: ceil(3650 / 30) = 122.
        # Gap 2025-05-01 − 2025-02-01 = 89 days → ceil(89/30) = 3 months → 2 × 3 = 6.
        assert feats == {"2025-05": 122, "2025-02": 6}

    def test_year_resolution_gap(self, simple_anchor, simple_stats):
        match = MatchResult(
            matching_drugs=[], matching_AEs=[],
            demographic_matches=[],
            matched_clinical_dates=[
                ClinicalDateMatch(matched_value="2025", resolution="year"),
                ClinicalDateMatch(matched_value="2020", resolution="year"),
            ],
        )
        config = DomainSizeConfig()
        result = compute_probability(
            match, simple_anchor, simple_stats, config,
            reporting_context_level=ReportingContextLevel.GLOBAL,
        )
        feats = self._clinical_feats_by_value(result)
        # Global default for year resolution: ceil(3650 / 365) = 10.
        # Gap 2025-01-01 − 2020-01-01 = 1827 days → ceil(1827/365) = 6 years → 2 × 6 = 12.
        assert feats == {"2025": 10, "2020": 12}

    def test_report_date_does_not_affect_clinical_domain(
        self, simple_anchor, simple_stats
    ):
        """A far-away report date must not inflate clinical-date domains."""
        match = MatchResult(
            matching_drugs=[], matching_AEs=[],
            demographic_matches=[
                DemographicMatch(
                    feature="report_date",
                    matched=True,
                    date_resolution="day",
                    values_r1=["2019-01-01"],
                    values_r2=["2019-01-01"],
                ),
            ],
            matched_clinical_dates=[
                ClinicalDateMatch(matched_value="2025-05-20", resolution="day"),
                ClinicalDateMatch(matched_value="2025-05-18", resolution="day"),
            ],
        )
        config = DomainSizeConfig()
        result = compute_probability(
            match, simple_anchor, simple_stats, config,
            reporting_context_level=ReportingContextLevel.COUNTRY_YEAR,
        )
        feats = self._clinical_feats_by_value(result)
        assert feats == {"2025-05-20": 540, "2025-05-18": 4}

    def test_uniqueness_preserved_no_zero_gap(self):
        """Even if two identical dates slip through, the guard prevents zero domain."""
        dates = [
            ClinicalDateMatch(matched_value="2025-05-20", resolution="day"),
            ClinicalDateMatch(matched_value="2025-05-20", resolution="day"),
        ]
        sizes = _clinical_date_domain_sizes(
            dates, DomainSizeConfig(), is_country_year=True
        )
        # First (by sort): context default 540. Second: zero-day gap → guarded to 2.
        assert 540 in sizes
        assert 2 in sizes
        assert all(s is not None and s > 0 for s in sizes)


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

        # Smoothed leave-pair-out (no broader stats): primary level is
        # (198 + alpha*MARGINAL_FLOOR) / (998 + alpha). With plentiful
        # data this is within ~1% of the previous bare marginal 198/998.
        m = SMOOTHING_PSEUDOCOUNT
        expected = (198 + m * MARGINAL_FLOOR) / (998 + m)
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

        m = SMOOTHING_PSEUDOCOUNT
        expected = (198 + m * MARGINAL_FLOOR) / (998 + m)
        assert drug_feats[0].p_f == pytest.approx(expected)

    def test_description_shows_leave_pair_out_counts(self):
        """Description should show the smoothed leave-pair-out trace."""
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

        # Description carries primary counts and the backoff trace.
        assert "200/1000 primary" in ae_feat.description
        assert "smoothed leave-pair-out" in ae_feat.description
        assert "primary" in ae_feat.description


class TestMarginalBackoff:
    """Regression coverage for the hierarchical leave-pair-out smoother.

    Targets the edge case where ``N_drug_AE == N_drug == N_AE`` so the
    leave-pair-out count for a feature collapses to zero — without
    backoff the contribution would be silently dropped.
    """

    def _match(self):
        return MatchResult(
            matching_drugs=["drug_a"],
            matching_AEs=["ae_a"],
            demographic_matches=[],
        )

    def _anchor_drugs(self, n_drugs=25, n_aes=200, n_drugs_aes=3):
        return AnchorPopulation(
            N_drugs=n_drugs, N_AEs=n_aes, N_drugs_AEs=n_drugs_aes,
            type=AnchorType.DRUGS, N_anchor=n_drugs,
        )

    def _anchor_aes(self, n_drugs=200, n_aes=25, n_drugs_aes=3):
        return AnchorPopulation(
            N_drugs=n_drugs, N_AEs=n_aes, N_drugs_AEs=n_drugs_aes,
            type=AnchorType.AES, N_anchor=n_aes,
        )

    # ---- Helper-level checks -----------------------------------------

    def test_smoothed_marginal_collapses_to_prior_when_context_empty(self):
        # n_total=2 → n_total_excl=0; smoothed estimate equals the prior.
        assert _smoothed_marginal(2, 2, p_prior=0.123, alpha=10) == pytest.approx(0.123)

    def test_smoothed_marginal_recovers_bare_marginal_with_zero_alpha(self):
        # alpha=0 short-circuits to the bare leave-pair-out marginal.
        assert _smoothed_marginal(202, 1002, p_prior=0.5, alpha=0) == pytest.approx(
            (202 - 2) / (1002 - 2)
        )

    def test_marginal_with_backoff_returns_positive_with_no_broader_stats(self):
        # All broader fields None: floor + primary smoothing still yields > 0.
        p, trace = _marginal_with_backoff(
            n_feature=2, n_total=1000,
            n_feature_country=None, n_total_country=None,
            n_feature_global=None, n_total_global=None,
        )
        assert p > 0
        # Primary numerator collapses to 0 → result equals
        # alpha*floor / (n_total_excl + alpha).
        m = SMOOTHING_PSEUDOCOUNT
        assert p == pytest.approx((m * MARGINAL_FLOOR) / (998 + m))
        assert "floor" in trace
        assert "primary" in trace
        assert "country" not in trace
        assert "global" not in trace

    def test_marginal_with_backoff_uses_country_prior_when_global_missing(self):
        p, trace = _marginal_with_backoff(
            n_feature=2, n_total=1000,
            n_feature_country=8, n_total_country=5000,
            n_feature_global=None, n_total_global=None,
        )
        m = SMOOTHING_PSEUDOCOUNT
        # Floor → country (smoothed) → primary (smoothed).
        country_p = (6 + m * MARGINAL_FLOOR) / (4998 + m)
        primary_p = (0 + m * country_p) / (998 + m)
        assert p == pytest.approx(primary_p)
        assert "country" in trace
        assert "global" not in trace

    def test_marginal_with_backoff_floor_uses_global_total_when_available(self):
        p, _ = _marginal_with_backoff(
            n_feature=2, n_total=10,
            n_feature_country=None, n_total_country=None,
            n_feature_global=2, n_total_global=1_000_002,
        )
        # Floor = 1 / (1_000_002 - 2) = 1e-6
        m = SMOOTHING_PSEUDOCOUNT
        floor = 1.0 / max(1_000_002 - 2, 1)
        # Global feature count collapses too → smoothed global = alpha*floor/(N+alpha).
        global_p = (0 + m * floor) / ((1_000_002 - 2) + m)
        primary_p = (0 + m * global_p) / ((10 - 2) + m)
        assert p == pytest.approx(primary_p)

    # ---- compute_probability integration -----------------------------

    def test_normal_case_with_broader_stats_close_to_bare_marginal(self):
        """Plentiful data: smoothed value ~ matches the previous bare marginal."""
        stats = FrequencyStats(
            N_total=1000, N_drugs=25, N_AEs=200, N_drugs_AEs=3,
            N_total_country=5000, N_drugs_country=120, N_AEs_country=900,
            N_total_global=1_000_000, N_drugs_global=20_000, N_AEs_global=180_000,
        )
        result = compute_probability(
            self._match(), self._anchor_drugs(), stats, DomainSizeConfig()
        )
        ae_feat = next(
            fp for fp in result.feature_probabilities if fp.feature == "ae_set"
        )
        # Smoothed value should be within 5% of the bare marginal 198/998.
        bare = (200 - 2) / (1000 - 2)
        assert ae_feat.p_f == pytest.approx(bare, rel=0.05)
        # Trace mentions every applied level.
        assert "country" in ae_feat.description
        assert "global" in ae_feat.description

    def test_edge_case_ae_only_in_pair_uses_country_prior(self):
        """Pair-only AE set: previously dropped; now backs off to country."""
        stats = FrequencyStats(
            N_total=1000, N_drugs=25, N_AEs=2, N_drugs_AEs=2,
            N_total_country=5000, N_drugs_country=120, N_AEs_country=8,
            N_total_global=1_000_000, N_drugs_global=20_000, N_AEs_global=50,
        )
        result = compute_probability(
            self._match(), self._anchor_drugs(n_aes=2, n_drugs_aes=2),
            stats, DomainSizeConfig(),
        )
        ae_feats = [fp for fp in result.feature_probabilities if fp.feature == "ae_set"]
        # Previously this contribution was OMITTED; now it must be present.
        assert len(ae_feats) == 1
        assert ae_feats[0].p_f > 0

        # Verify the value matches the explicit ladder.
        m = SMOOTHING_PSEUDOCOUNT
        floor = 1.0 / max(1_000_000 - 2, 1)
        p_global = (max(0, 50 - 2) + m * floor) / (max(0, 1_000_000 - 2) + m)
        p_country = (max(0, 8 - 2) + m * p_global) / (max(0, 5000 - 2) + m)
        p_primary = (max(0, 2 - 2) + m * p_country) / (max(0, 1000 - 2) + m)
        assert ae_feats[0].p_f == pytest.approx(p_primary)

    def test_edge_case_drug_only_in_pair_symmetric(self):
        """Mirror of the AE-only case, with anchor=AES and drug-set rare."""
        stats = FrequencyStats(
            N_total=1000, N_drugs=2, N_AEs=25, N_drugs_AEs=2,
            N_total_country=5000, N_drugs_country=8, N_AEs_country=120,
            N_total_global=1_000_000, N_drugs_global=50, N_AEs_global=20_000,
        )
        result = compute_probability(
            self._match(), self._anchor_aes(n_drugs=2, n_drugs_aes=2),
            stats, DomainSizeConfig(),
        )
        drug_feats = [
            fp for fp in result.feature_probabilities if fp.feature == "drug_set"
        ]
        assert len(drug_feats) == 1
        assert drug_feats[0].p_f > 0

        m = SMOOTHING_PSEUDOCOUNT
        floor = 1.0 / max(1_000_000 - 2, 1)
        p_global = (max(0, 50 - 2) + m * floor) / (max(0, 1_000_000 - 2) + m)
        p_country = (max(0, 8 - 2) + m * p_global) / (max(0, 5000 - 2) + m)
        p_primary = (max(0, 2 - 2) + m * p_country) / (max(0, 1000 - 2) + m)
        assert drug_feats[0].p_f == pytest.approx(p_primary)

    def test_no_broader_stats_falls_back_to_floor(self):
        """Pair-only AE with no broader stats: floor keeps p_f non-zero."""
        stats = FrequencyStats(
            N_total=1000, N_drugs=25, N_AEs=2, N_drugs_AEs=2,
        )
        result = compute_probability(
            self._match(), self._anchor_drugs(n_aes=2, n_drugs_aes=2),
            stats, DomainSizeConfig(),
        )
        ae_feats = [fp for fp in result.feature_probabilities if fp.feature == "ae_set"]
        assert len(ae_feats) == 1
        assert ae_feats[0].p_f > 0

        # Direct check: only the floor and primary level are applied.
        m = SMOOTHING_PSEUDOCOUNT
        expected = (m * MARGINAL_FLOOR) / (998 + m)
        assert ae_feats[0].p_f == pytest.approx(expected)

    def test_global_only_uses_global_derived_floor(self):
        """No country level (e.g., reporting context = COUNTRY): floor = 1/N_global."""
        stats = FrequencyStats(
            N_total=1000, N_drugs=25, N_AEs=2, N_drugs_AEs=2,
            N_total_global=1_000_002, N_drugs_global=20_000, N_AEs_global=2,
        )
        result = compute_probability(
            self._match(), self._anchor_drugs(n_aes=2, n_drugs_aes=2),
            stats, DomainSizeConfig(),
        )
        ae_feats = [fp for fp in result.feature_probabilities if fp.feature == "ae_set"]
        assert len(ae_feats) == 1
        assert ae_feats[0].p_f > 0

        m = SMOOTHING_PSEUDOCOUNT
        floor = 1.0 / max(1_000_002 - 2, 1)
        # Global feature collapses too (N_AEs_global=2 → excl 0).
        p_global = (m * floor) / ((1_000_002 - 2) + m)
        p_primary = (m * p_global) / (998 + m)
        assert ae_feats[0].p_f == pytest.approx(p_primary)

    @pytest.mark.parametrize(
        "n_drugs, n_aes, n_drugs_aes, n_total",
        [
            (2, 2, 2, 1000),       # original edge case
            (3, 200, 3, 1000),     # tiny drug count
            (200, 3, 3, 1000),     # tiny AE count
            (1, 1, 1, 100),        # below 2 (defensive: count < pair size)
            (200, 200, 50, 5000),  # plentiful
        ],
    )
    def test_positivity_invariant(
        self, n_drugs, n_aes, n_drugs_aes, n_total
    ):
        """drug_set and ae_set p_f are always strictly positive when matched."""
        stats = FrequencyStats(
            N_total=n_total, N_drugs=n_drugs, N_AEs=n_aes, N_drugs_AEs=n_drugs_aes,
        )
        # Anchor=DRUGS_AES would suppress both contributions; pick anchors
        # that leave both drug_set and ae_set as additional features in
        # one or the other configuration.
        for anchor in (
            self._anchor_drugs(n_drugs=n_drugs, n_aes=n_aes, n_drugs_aes=n_drugs_aes),
            self._anchor_aes(n_drugs=n_drugs, n_aes=n_aes, n_drugs_aes=n_drugs_aes),
        ):
            result = compute_probability(
                self._match(), anchor, stats, DomainSizeConfig()
            )
            for fp in result.feature_probabilities:
                if fp.feature in ("drug_set", "ae_set"):
                    assert fp.p_f > 0, f"{fp.feature} got p_f={fp.p_f} (anchor={anchor.type})"
