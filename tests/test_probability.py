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
    SMOOTHING_PSEUDOCOUNT,
    FrequencyStats,
    _compute_date_span_days,
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
    """Tests for context-dependent date domain size and span extension."""

    def test_country_year_default_365(self):
        """Country-year context should use 365 days as default date domain."""
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
        assert date_feat[0].domain_size == 365

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

    def test_span_extends_domain_beyond_default(self):
        """Matching dates spanning >365 days should extend the country-year domain."""
        match = MatchResult(
            matching_drugs=[],
            matching_AEs=[],
            demographic_matches=[
                DemographicMatch(
                    feature="drug_start_date",
                    matched=True,
                    date_resolution="day",
                    values_r1=["2001-02-01"],
                    values_r2=["2001-02-01"],
                ),
                DemographicMatch(
                    feature="ae_onset_date",
                    matched=True,
                    date_resolution="day",
                    values_r1=["2007-03-03"],
                    values_r2=["2007-03-03"],
                ),
                DemographicMatch(
                    feature="drug_stop_date",
                    matched=True,
                    date_resolution="day",
                    values_r1=["2007-03-08"],
                    values_r2=["2007-03-08"],
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
        # Span from 2001-02-01 to 2007-03-08 = 2226 days
        # This exceeds 365 default, so domain should be 2226
        date_feat = [fp for fp in result.feature_probabilities if fp.feature == "drug_start_date"]
        assert len(date_feat) == 1
        assert date_feat[0].domain_size == 2226

    def test_span_does_not_shrink_below_default(self):
        """If span is smaller than the default, use the default."""
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
                DemographicMatch(
                    feature="drug_stop_date",
                    matched=True,
                    date_resolution="day",
                    values_r1=["2020-07-12"],
                    values_r2=["2020-07-12"],
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
        # Span is 30 days, but global default is 3650 — use 3650
        date_feat = [fp for fp in result.feature_probabilities if fp.feature == "drug_start_date"]
        assert len(date_feat) == 1
        assert date_feat[0].domain_size == 3650


class TestComputeDateSpanDays:
    """Tests for _compute_date_span_days helper."""

    def test_single_date_returns_zero(self):
        matches = [
            DemographicMatch(
                feature="drug_start_date", matched=True, date_resolution="day",
                values_r1=["2020-06-12"], values_r2=["2020-06-12"],
            ),
        ]
        assert _compute_date_span_days(matches) == 0

    def test_multiple_dates_computes_span(self):
        matches = [
            DemographicMatch(
                feature="drug_start_date", matched=True, date_resolution="day",
                values_r1=["2001-02-01"], values_r2=["2001-02-01"],
            ),
            DemographicMatch(
                feature="ae_onset_date", matched=True, date_resolution="day",
                values_r1=["2007-03-03"], values_r2=["2007-03-03"],
            ),
            DemographicMatch(
                feature="drug_stop_date", matched=True, date_resolution="day",
                values_r1=["2007-03-08"], values_r2=["2007-03-08"],
            ),
        ]
        # 2001-02-01 to 2007-03-08 = 2226 days
        assert _compute_date_span_days(matches) == 2226

    def test_unmatched_dates_excluded(self):
        matches = [
            DemographicMatch(
                feature="drug_start_date", matched=False, date_resolution="day",
                values_r1=["2001-02-01"], values_r2=["2005-06-12"],
            ),
            DemographicMatch(
                feature="ae_onset_date", matched=True, date_resolution="day",
                values_r1=["2020-06-12"], values_r2=["2020-06-12"],
            ),
        ]
        # Only one matched feature with identical dates → span = 0
        assert _compute_date_span_days(matches) == 0

    def test_report_date_included_in_span_computation(self):
        """Report date values ARE used in span computation even though
        report_date is excluded from probability features."""
        matches = [
            DemographicMatch(
                feature="report_date", matched=True, date_resolution="day",
                values_r1=["2001-01-01"], values_r2=["2001-01-01"],
            ),
            DemographicMatch(
                feature="drug_start_date", matched=True, date_resolution="day",
                values_r1=["2007-06-12"], values_r2=["2007-06-12"],
            ),
        ]
        # 2001-01-01 to 2007-06-12 = 2353 days
        assert _compute_date_span_days(matches) == 2353


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
        # p_joint should be (1/D_f)^2
        expected_p_joint = (1.0 / 3650) ** 2
        assert result.p_joint == pytest.approx(expected_p_joint)

    def test_clinical_date_country_year_domain(
        self, simple_anchor, simple_stats
    ):
        """Country-year context should use 365-day domain for clinical dates."""
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
        assert clinical_feats[0].domain_size == 365

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

    def test_clinical_dates_in_span_computation(self):
        """Clinical dates should contribute to the date span calculation."""
        clinical_dates = [
            ClinicalDateMatch(matched_value="2001-02-01", resolution="day"),
            ClinicalDateMatch(matched_value="2007-03-08", resolution="day"),
        ]
        span = _compute_date_span_days([], clinical_dates)
        assert span == 2226

    def test_clinical_dates_extend_domain_via_span(
        self, simple_anchor, simple_stats
    ):
        """Clinical dates spanning > default should extend the domain size."""
        match = MatchResult(
            matching_drugs=[],
            matching_AEs=[],
            demographic_matches=[],
            matched_clinical_dates=[
                ClinicalDateMatch(matched_value="2001-02-01", resolution="day"),
                ClinicalDateMatch(matched_value="2007-03-08", resolution="day"),
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
        # Span is 2226 days, exceeds 365 default → domain = 2226
        assert all(fp.domain_size == 2226 for fp in clinical_feats)

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

        # Verify the actual value: marginal leave-pair-out = (200-2)/(1000-2) = 198/998
        expected = (200 - 2) / (1000 - 2)
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

        # Verify the actual value: marginal leave-pair-out = (200-2)/(1000-2) = 198/998
        expected = (200 - 2) / (1000 - 2)
        assert drug_feats[0].p_f == pytest.approx(expected)

    def test_description_shows_leave_pair_out_counts(self):
        """Description should show marginal leave-pair-out formula."""
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

        assert "(200-2)/(1000-2)" in ae_feat.description
