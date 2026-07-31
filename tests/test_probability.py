"""Tests for probability estimation and L_match computation."""

import math

import pytest
from pydantic import ValidationError

from llm_dup_dev.classifier.config import DomainSizeConfig
from llm_dup_dev.classifier.decision_logic import classify
from llm_dup_dev.feature_generation.anchor import select_anchor
from llm_dup_dev.feature_generation.matching import (
    ClinicalDateMatch,
    DemographicMatch,
    MatchResult,
)
from llm_dup_dev.feature_generation.probability import (
    MARGINAL_FLOOR,
    FrequencyStats,
    _clinical_date_domain_sizes,
    _get_domain_size,
    _marginal_with_backoff,
    compute_l_patient,
    compute_probability,
    n_anchor_pairs,
    probability_any_collision,
)
from llm_dup_dev.models.enums import (
    AnchorType,
    IndependenceAssessment,
    IndependenceReason,
    ReportingContextLevel,
)
from llm_dup_dev.models.pair_features import (
    AnchorPopulation,
    FeatureContribution,
    InconsistencyCounts,
    PairFeatures,
    ProbabilityEstimate,
    ReportingContext,
)


def _fc(feature: str, p_f: float) -> FeatureContribution:
    """Concise FeatureContribution builder for the L_patient tests."""
    return FeatureContribution(feature=feature, p_f=p_f, method="domain_size")


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
        assert result.p_match >= 0

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
        # p_joint=1.0 → a collision is certain on every one of the M=1225
        # pairs → P_match = 1 - (1-1)^1225 = 1 → L_match = max(0, -log10(1)) = 0
        assert result.p_match == 1.0
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

    def test_single_anchor_report_gives_zero_match_probability(self, domain_config):
        """N_anchor=1 → 0 possible pairs → P_match=0 → high L_match.

        Pins current behaviour only; it does not endorse it. The input is
        unreachable in production: the anchor counts include the focal pair
        (``_marginal_with_backoff`` subtracts 2 for exactly that reason), so
        a pair with a matching drug forces N_drugs >= 2. N_anchor=1 would be
        a data-layer contradiction.

        The reachable M == 0 case is instead N_anchor=0 — the spec's
        "degenerate case", where the pair shares no drugs and no AEs. There,
        L_match=15.0 lands in the compelling zone off a vacuous P_match=0,
        which is backwards. Tracked separately; predates the P_match change,
        which left this path's value unchanged (E_joint=0 gave 15.0 too).
        """
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
        # N_anchor=1 → M=0 → no opportunity for a chance collision → P_match=0
        assert result.p_match == 0.0
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

        # Bare leave-pair-out at the primary scope: 198/998. No
        # smoothing — the primary scope has extra-pair evidence so the
        # cascade returns the raw LPO ratio.
        expected = 198 / 998
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

        expected = 198 / 998
        assert drug_feats[0].p_f == pytest.approx(expected)

    def test_description_shows_leave_pair_out_counts(self):
        """Description should show the LPO cascade trace."""
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

        # Description carries primary counts and the cascade trace.
        assert "200/1000 primary" in ae_feat.description
        assert "leave-pair-out" in ae_feat.description
        assert "lpo@primary" in ae_feat.description


class TestMarginalBackoff:
    """Regression coverage for the first-level-with-evidence LPO cascade.

    Targets the edge case where ``N_feature == 2`` at the primary scope
    (the feature is observed only in the focal pair locally) — the
    cascade must back off to country, then global, then a per-report
    floor, and must never return zero.
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

    def test_returns_floor_when_no_scope_has_extra_pair_evidence(self):
        """All broader stats None and primary pair-only → MARGINAL_FLOOR."""
        p, trace = _marginal_with_backoff(
            n_feature=2, n_total=1000,
            n_feature_country=None, n_total_country=None,
            n_feature_global=None, n_total_global=None,
        )
        assert p == pytest.approx(MARGINAL_FLOOR)
        assert "floor" in trace

    def test_uses_country_lpo_when_primary_pair_only_and_country_has_evidence(self):
        p, trace = _marginal_with_backoff(
            n_feature=2, n_total=1000,
            n_feature_country=8, n_total_country=5000,
            n_feature_global=None, n_total_global=None,
        )
        assert p == pytest.approx((8 - 2) / (5000 - 2))
        assert "lpo@country" in trace

    def test_falls_through_to_global_lpo_when_only_global_has_evidence(self):
        p, trace = _marginal_with_backoff(
            n_feature=2, n_total=1000,
            n_feature_country=2, n_total_country=5000,
            n_feature_global=50, n_total_global=1_000_000,
        )
        assert p == pytest.approx((50 - 2) / (1_000_000 - 2))
        assert "lpo@global" in trace

    def test_floor_uses_global_total_when_no_scope_has_evidence(self):
        p, trace = _marginal_with_backoff(
            n_feature=2, n_total=10,
            n_feature_country=None, n_total_country=None,
            n_feature_global=2, n_total_global=1_000_002,
        )
        assert p == pytest.approx(1.0 / (1_000_002 - 2))
        assert "N_total_global" in trace

    def test_primary_lpo_used_when_primary_has_extra_pair_evidence(self):
        """Even if broader scopes have more occurrences, primary wins when it has any."""
        p, trace = _marginal_with_backoff(
            n_feature=3, n_total=1000,
            n_feature_country=120, n_total_country=5000,
            n_feature_global=20_000, n_total_global=1_000_000,
        )
        assert p == pytest.approx((3 - 2) / (1000 - 2))
        assert "lpo@primary" in trace

    def test_skips_scope_with_n_total_too_small(self):
        """A scope with n_total <= 2 is skipped (no LPO room)."""
        p, trace = _marginal_with_backoff(
            n_feature=2, n_total=2,             # primary too small
            n_feature_country=8, n_total_country=5000,
            n_feature_global=50, n_total_global=1_000_000,
        )
        assert p == pytest.approx((8 - 2) / (5000 - 2))
        assert "lpo@country" in trace

    # ---- compute_probability integration -----------------------------

    def test_normal_case_returns_bare_primary_lpo(self):
        """Plentiful primary evidence: returns exactly 198/998."""
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
        assert ae_feat.p_f == pytest.approx((200 - 2) / (1000 - 2))
        assert "lpo@primary" in ae_feat.description

    def test_edge_case_ae_only_in_pair_uses_country_lpo(self):
        """Pair-only AE in primary: cascade picks country bare LPO."""
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
        assert len(ae_feats) == 1
        assert ae_feats[0].p_f == pytest.approx((8 - 2) / (5000 - 2))
        assert "lpo@country" in ae_feats[0].description

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
        assert drug_feats[0].p_f == pytest.approx((8 - 2) / (5000 - 2))
        assert "lpo@country" in drug_feats[0].description

    def test_unique_to_pair_everywhere_lands_at_global_floor(self):
        """Feature unique to focal pair at every scope → floor = 1/(N_global-2)."""
        stats = FrequencyStats(
            N_total=1000, N_drugs=25, N_AEs=2, N_drugs_AEs=2,
            N_total_country=5000, N_drugs_country=120, N_AEs_country=2,
            N_total_global=1_000_000, N_drugs_global=20_000, N_AEs_global=2,
        )
        result = compute_probability(
            self._match(), self._anchor_drugs(n_aes=2, n_drugs_aes=2),
            stats, DomainSizeConfig(),
        )
        ae_feats = [fp for fp in result.feature_probabilities if fp.feature == "ae_set"]
        assert len(ae_feats) == 1
        assert ae_feats[0].p_f == pytest.approx(1.0 / (1_000_000 - 2))
        # Critical regression: must be ~1e-6, not the old cascade's ~1e-16.
        assert ae_feats[0].p_f > 1e-7

    def test_no_broader_stats_falls_back_to_constant_floor(self):
        """Pair-only AE with no broader stats: MARGINAL_FLOOR keeps p_f non-zero."""
        stats = FrequencyStats(
            N_total=1000, N_drugs=25, N_AEs=2, N_drugs_AEs=2,
        )
        result = compute_probability(
            self._match(), self._anchor_drugs(n_aes=2, n_drugs_aes=2),
            stats, DomainSizeConfig(),
        )
        ae_feats = [fp for fp in result.feature_probabilities if fp.feature == "ae_set"]
        assert len(ae_feats) == 1
        assert ae_feats[0].p_f == pytest.approx(MARGINAL_FLOOR)

    def test_global_only_uses_global_derived_floor(self):
        """No country level: feature pair-only globally → floor = 1/(N_global-2)."""
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
        assert ae_feats[0].p_f == pytest.approx(1.0 / (1_000_002 - 2))

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


# ===================================================================
# Multiplicity adjustment: P_match = 1 - (1 - p_joint)**M
# ===================================================================

class TestMultiplicityAdjustment:
    """Tests for n_anchor_pairs and probability_any_collision.

    The method measures the probability that *at least one* chance
    collision occurs among the M = C(N_anchor, 2) unordered report pairs
    in the anchor population, not the expected number of collisions.
    """

    # -- M = C(N_anchor, 2) ------------------------------------------

    @pytest.mark.parametrize(
        "n_anchor,expected",
        [
            (0, 0), (1, 0),      # too small to furnish any pair
            (2, 1),              # 2*1/2
            (3, 3), (45, 990), (50, 1225),
        ],
    )
    def test_n_anchor_pairs(self, n_anchor, expected):
        """M = C(N_anchor, 2), and 0 when fewer than two reports."""
        assert n_anchor_pairs(n_anchor) == expected

    # -- Boundary cases ----------------------------------------------

    def test_zero_multiplicity_gives_zero_probability(self):
        """M == 0 → P_match == 0 (no opportunity for a collision)."""
        assert probability_any_collision(0.5, 0) == 0.0
        assert probability_any_collision(0.5, n_anchor_pairs(1)) == 0.0

    def test_zero_per_pair_probability_gives_zero_probability(self):
        """p_joint == 0 → P_match == 0."""
        assert probability_any_collision(0.0, 1225) == 0.0

    def test_certain_collision(self):
        """p_joint == 1 and M > 0 → P_match == 1, and L_match == 0."""
        p_match = probability_any_collision(1.0, 1225)
        assert p_match == 1.0
        # max(0, -log10(1 + 1e-15)) → the clamp absorbs the epsilon
        assert max(0.0, -math.log10(p_match + 1e-15)) == 0.0

    def test_single_comparison_returns_p_joint(self):
        """M == 1 → P_match == p_joint exactly."""
        for p in (1e-12, 1e-6, 1e-3, 0.5):
            assert probability_any_collision(p, 1) == pytest.approx(p, rel=1e-12)

    # -- Regimes -----------------------------------------------------

    def test_small_probability_regime_approximates_expected_count(self):
        """P_match ≈ M * p_joint when the expected count is very small."""
        M, p = 990, 2.3e-8
        e_joint = M * p  # 2.277e-05
        p_match = probability_any_collision(p, M)
        assert p_match == pytest.approx(e_joint, rel=1e-4)

    def test_non_negligible_regime_is_below_expected_count(self):
        """The exact probability is < the expected count and stays ≤ 1."""
        M, p = 1225, 2e-4
        e_joint = M * p  # 0.245 — no longer negligible
        p_match = probability_any_collision(p, M)
        assert p_match == pytest.approx(0.21731, rel=1e-4)
        assert p_match < e_joint
        assert 0.0 <= p_match <= 1.0

    def test_saturates_at_one_when_expected_count_exceeds_one(self):
        """E_joint may exceed 1; a probability may not."""
        M, p = 1225, 0.5
        assert M * p > 1.0  # the old expected count was unbounded
        p_match = probability_any_collision(p, M)
        assert p_match == 1.0

    # -- Numerical stability -----------------------------------------

    def test_tiny_p_joint_does_not_collapse_to_zero(self):
        """Very small p_joint must survive; the naive form does not.

        This is the regression that motivates log1p/expm1: the naive
        1-(1-p)**M underflows to exactly 0.0 here, which -log10 would
        then report as *maximal* rather than negligible evidence.
        """
        M, p = 1225, 1e-17
        assert 1 - (1 - p) ** M == 0.0  # naive form loses the result entirely
        p_match = probability_any_collision(p, M)
        assert p_match == pytest.approx(1.225e-14, rel=1e-6)
        assert p_match > 0.0

    @pytest.mark.parametrize(
        "M,p",
        [
            (1225, 1e-15),
            (4_999_950_000, 1e-300),     # N_anchor = 100_000
            (4_999_950_000, 5e-324),     # smallest subnormal
            (10**12, 1e-18),
            (1, 1e-308),
        ],
    )
    def test_extreme_inputs_stay_finite_and_in_range(self, M, p):
        """No NaN, no overflow, and P_match stays within [0, 1]."""
        p_match = probability_any_collision(p, M)
        assert not math.isnan(p_match)
        assert math.isfinite(p_match)
        assert 0.0 <= p_match <= 1.0

    def test_monotonic_in_multiplicity(self):
        """More pairwise opportunities → higher collision probability."""
        p = 1e-6
        values = [probability_any_collision(p, M) for M in (1, 10, 1000, 100_000)]
        assert values == sorted(values)
        assert all(v <= 1.0 for v in values)

    # -- Exact vs Poisson approximation ------------------------------

    def test_agrees_with_poisson_in_rare_event_regime(self):
        """1 - exp(-E_joint) agrees with the exact form when E_joint is small.

        The Poisson expression is a diagnostic approximation only; the
        exact binomial complement remains authoritative.
        """
        M, p = 990, 2.3e-8
        poisson = 1 - math.exp(-(M * p))
        assert probability_any_collision(p, M) == pytest.approx(poisson, rel=1e-6)

    def test_exact_form_departs_from_poisson_when_not_rare(self):
        """Away from the rare-event limit the two diverge; exact wins."""
        M, p = 1225, 2e-4
        poisson = 1 - math.exp(-(M * p))          # ≈ 0.21728
        exact = probability_any_collision(p, M)   # ≈ 0.21731
        assert exact != poisson
        assert exact == pytest.approx(poisson, rel=1e-3)  # close, but not equal
        assert exact == pytest.approx(1 - (1 - p) ** M, rel=1e-9)


class TestProbabilityResultWiring:
    """compute_probability must expose P_match and derive L_match from it."""

    def test_l_match_is_derived_from_p_match(self, basic_match, anchor_drugs,
                                             stats, domain_config):
        """L_match == max(0, -log10(p_match + epsilon)) on the real result."""
        result = compute_probability(
            basic_match, anchor_drugs, stats, domain_config
        )
        expected = max(0.0, -math.log10(result.p_match + 1e-15))
        assert result.L_match == pytest.approx(expected)

    def test_p_match_matches_the_exact_formula(self, basic_match, anchor_drugs,
                                               stats, domain_config):
        """p_match == 1 - (1 - p_joint)**M for the selected anchor."""
        result = compute_probability(
            basic_match, anchor_drugs, stats, domain_config
        )
        M = n_anchor_pairs(anchor_drugs.N_anchor)
        assert result.p_match == pytest.approx(
            probability_any_collision(result.p_joint, M), rel=1e-12
        )
        assert 0.0 <= result.p_match <= 1.0


class TestEvidenceZoneRegression:
    """The revised L_match must still drive the compelling/intermediate/limited zones.

    Zone dispatch uses strict ``>``, so each case is placed clear of its
    threshold. P_match ≤ E_joint (union bound), so the switch from the
    expected count can only raise L_match — by at most ~0.02 at the
    limited threshold, ~0.002 at intermediate and ~0.0002 at compelling.
    No zone assignment in these cases changes as a result.
    """

    @pytest.mark.parametrize(
        "p_joint,expected_reason",
        [
            # M = 1225 for N_anchor=50. L_match = -log10(1 - (1-p)^1225).
            (1e-8, IndependenceReason.RARITY_COMPELLING),      # L ≈ 4.91  > 3.0
            (2e-6, IndependenceReason.RARITY_INTERMEDIATE),    # L ≈ 2.61  ∈ (2, 3]
            (2e-5, IndependenceReason.RARITY_LIMITED),         # L ≈ 1.61  ∈ (1, 2]
            (2e-3, IndependenceReason.RARITY_INSUFFICIENT),    # L ≈ 0.42  ≤ 1.0
        ],
    )
    def test_zone_assignment_from_revised_l_match(self, p_joint, expected_reason):
        """Each regime lands in its intended evidence zone."""
        M = n_anchor_pairs(50)
        l_match = max(0.0, -math.log10(probability_any_collision(p_joint, M) + 1e-15))

        features = PairFeatures(
            pair_id="zone_test",
            reporting_context=ReportingContext(level=ReportingContextLevel.GLOBAL),
            anchor=AnchorPopulation(
                N_drugs=100, N_AEs=100, N_drugs_AEs=50,
                type=AnchorType.DRUGS_AES, N_anchor=50,
            ),
            probability=ProbabilityEstimate(
                p_joint=p_joint,
                p_match=probability_any_collision(p_joint, M),
            ),
            L_match=l_match,
            L_patient=0.0,  # not exercised: this asserts on L_match zones
            inconsistency_counts=InconsistencyCounts(),
        )
        result = classify(features)
        assert result.independence_reason == expected_reason

    def test_zone_terminology_is_retained(self):
        """The matching-evidence zones keep the compelling/intermediate/limited names."""
        values = {r.value for r in IndependenceReason}
        assert {"rarity_compelling", "rarity_intermediate", "rarity_limited"} <= values
        # The older strong/moderate/weak terminology must not reappear.
        assert not {"rarity_strong", "rarity_moderate", "rarity_weak"} & values


class TestProbabilityEstimateSerialization:
    """p_match and expected counts must not be conflated in the output contract."""

    def test_p_match_is_serialized(self):
        """p_match round-trips through JSON with its own field."""
        est = ProbabilityEstimate(p_joint=2.3e-8, p_match=2.277e-5)
        restored = ProbabilityEstimate.model_validate_json(est.model_dump_json())
        assert restored.p_match == pytest.approx(2.277e-5)
        assert restored.p_joint == pytest.approx(2.3e-8)

    def test_expected_collisions_field_is_gone(self):
        """The expected-count field must not survive under its old name.

        A probability must never be served through a field that downstream
        consumers read as an expected count.
        """
        assert "expected_collisions" not in ProbabilityEstimate.model_fields
        assert "p_match" in ProbabilityEstimate.model_fields

    def test_p_match_is_required(self):
        """p_match has no default — it must be supplied explicitly."""
        with pytest.raises(ValidationError):
            ProbabilityEstimate(p_joint=1e-5)

    def test_schema_documents_p_match_as_a_probability(self):
        """The published schema describes p_match distinctly and not as a p-value."""
        schema = ProbabilityEstimate.model_json_schema()
        assert "expected_collisions" not in schema["properties"]
        desc = schema["properties"]["p_match"]["description"]
        assert "at least one" in desc
        assert "not a p-value" in desc
        # p_joint is the per-pair quantity; the two must be described separately.
        assert "one equivalent pairwise" in schema["properties"]["p_joint"]["description"]


class TestDegenerateAnchorEvidence:
    """A pair sharing no drugs and no AEs must not yield maximal evidence.

    Regression for the degenerate-anchor bug: select_anchor used to report
    N_anchor=0 when both matching sets were empty, which made M vanish, so
    P_match=0 held vacuously and L_match saturated at -log10(epsilon)=15 —
    the compelling zone. Two reports with nothing in common classified as
    Duplicates. The anchor is now the whole reporting context, because an
    empty matching set leaves the population unnarrowed rather than empty.
    """

    def _classify(self, match, stats):
        anchor = select_anchor(
            N_drugs=stats.N_drugs, N_AEs=stats.N_AEs,
            N_drugs_AEs=stats.N_drugs_AEs, N_total=stats.N_total,
        )
        result = compute_probability(match, anchor, stats, DomainSizeConfig())
        features = PairFeatures(
            pair_id="degenerate",
            reporting_context=ReportingContext(level=ReportingContextLevel.GLOBAL),
            anchor=anchor,
            probability=result.estimate,
            L_match=result.L_match,
            L_patient=result.L_patient,
            inconsistency_counts=InconsistencyCounts(),
        )
        return anchor, result, classify(features)

    @staticmethod
    def _no_overlap_stats():
        """What a correct provider returns for a pair sharing no drugs/AEs."""
        return FrequencyStats(
            N_total=100_000, N_drugs=0, N_AEs=0, N_drugs_AEs=0
        )

    def test_nothing_in_common_is_not_compelling_evidence(self):
        """No drugs, no AEs, only sex+age → not a duplicate."""
        match = MatchResult(
            matching_drugs=[], matching_AEs=[],
            drugs_only_r1=["aspirin"], drugs_only_r2=["warfarin"],
            aes_only_r1=["rash"], aes_only_r2=["nausea"],
            demographic_matches=[
                DemographicMatch(feature="patient_sex", matched=True,
                                 values_r1=["female"], values_r2=["female"]),
                DemographicMatch(feature="patient_age", matched=True,
                                 values_r1=[54], values_r2=[54]),
            ],
        )
        anchor, result, decision = self._classify(match, self._no_overlap_stats())

        assert anchor.N_anchor == 100_000     # unnarrowed, not zero
        assert result.p_match == 1.0          # a chance match is near-certain
        assert result.L_match == 0.0
        assert decision.independence_reason == IndependenceReason.RARITY_INSUFFICIENT
        assert decision.independence_assessment == IndependenceAssessment.INDEPENDENT

    def test_no_matched_features_agrees_regardless_of_anchor(self):
        """Nothing matched → L_match=0 whether or not the anchor is narrowed.

        The bug made this identical input give L_match=0 for a populated
        anchor but 15.0 for a degenerate one.
        """
        match = MatchResult(
            matching_drugs=[], matching_AEs=[], demographic_matches=[]
        )
        _, degenerate, _ = self._classify(match, self._no_overlap_stats())
        _, populated, _ = self._classify(
            match,
            FrequencyStats(N_total=100_000, N_drugs=50, N_AEs=50, N_drugs_AEs=50),
        )
        # p_joint = 1.0 (empty product): a chance match is certain. L_match
        # must not simultaneously claim it is impossible.
        assert degenerate.p_joint == 1.0
        assert degenerate.L_match == 0.0 == populated.L_match

    def test_rare_demographics_still_carry_evidence(self):
        """The unnarrowed anchor judges, rather than discards, the evidence.

        A pair sharing no drugs/AEs but an implausibly rare demographic
        profile is still weighed — against the multiplicity of the whole
        context — instead of being dismissed outright.
        """
        match = MatchResult(
            matching_drugs=[], matching_AEs=[],
            demographic_matches=[
                DemographicMatch(feature="patient_sex", matched=True,
                                 values_r1=["female"], values_r2=["female"]),
                DemographicMatch(feature="patient_age", matched=True,
                                 values_r1=[54], values_r2=[54]),
                DemographicMatch(feature="patient_birth_date", matched=True,
                                 values_r1=["1970-03-14"], values_r2=["1970-03-14"],
                                 date_resolution="day"),
            ],
        )
        # A small context, so the multiplicity cannot swamp the evidence.
        stats = FrequencyStats(N_total=200, N_drugs=0, N_AEs=0, N_drugs_AEs=0)
        anchor, result, _ = self._classify(match, stats)
        assert anchor.N_anchor == 200
        assert result.L_match > 0.0, "rare demographic evidence was discarded"

    def test_degenerate_anchor_never_reaches_the_l_match_ceiling(self):
        """No zero-overlap input may produce the saturated L_match."""
        ceiling = -math.log10(1e-15)  # 15.0
        for n_total in (2, 10, 200, 100_000):
            match = MatchResult(
                matching_drugs=[], matching_AEs=[],
                demographic_matches=[
                    DemographicMatch(feature="patient_sex", matched=True,
                                     values_r1=["female"], values_r2=["female"]),
                ],
            )
            stats = FrequencyStats(
                N_total=n_total, N_drugs=0, N_AEs=0, N_drugs_AEs=0
            )
            _, result, _ = self._classify(match, stats)
            assert result.L_match < ceiling, f"saturated at N_total={n_total}"


# ===================================================================
# L_patient — patient-specific evidence
# ===================================================================

class TestMeasurementDomainScaling:
    """Height/weight/age domains scale with the matched decimal resolution."""

    def _dm(self, feature, value, resolution):
        return DemographicMatch(
            feature=feature, matched=True, matched_value=value,
            value_resolution=resolution,
        )

    def test_adult_height_integer_resolution(self, domain_config):
        dm = self._dm("patient_height", 170.0, 1.0)
        assert _get_domain_size(dm, domain_config, matched_age_years=40.0) == 40.0

    def test_adult_height_tenth_resolution_scales(self, domain_config):
        """0.1 cm resolution → domain ×10 (40 → 400)."""
        dm = self._dm("patient_height", 170.2, 0.1)
        assert _get_domain_size(dm, domain_config, matched_age_years=40.0) == 400.0

    def test_adult_weight_tenth_resolution_scales(self, domain_config):
        dm = self._dm("patient_weight", 75.2, 0.1)
        assert _get_domain_size(dm, domain_config, matched_age_years=40.0) == 400.0

    def test_child_height_scales_from_child_base(self, domain_config):
        """Child base 20 → 200 at 0.1 cm resolution."""
        dm = self._dm("patient_height", 90.5, 0.1)
        assert _get_domain_size(dm, domain_config, matched_age_years=5.0) == 200.0

    def test_age_years_tenth_resolution_scales(self, domain_config):
        dm = DemographicMatch(
            feature="patient_age", matched=True, matched_value=40.5,
            value_resolution=0.1, age_unit="years",
        )
        assert _get_domain_size(dm, domain_config) == 800.0


class TestComputeLPatient:
    """compute_l_patient must:

    1. Sum -log10(p_f) over patient-specific features only.
    2. Skip drug_set / ae_set entries.
    3. Skip zero/negative p_f defensively.
    4. Return 0 on empty input.
    5. Apply no multiplicity adjustment of any kind.

    Moved here from tests/test_decision_logic.py when the computation moved
    out of the classifier: L_patient is an evidence measure derived from the
    per-feature contributions, not a decision.
    """

    def test_returns_zero_on_empty_contributions(self):
        """No contributions → 0.0, the conservative default."""
        assert compute_l_patient([]) == 0.0

    def test_excludes_drug_and_ae_set_features(self):
        """drug_set / ae_set carry no patient-identity evidence."""
        contributions = [_fc("drug_set", 1e-10), _fc("ae_set", 1e-10)]
        # These p_f would contribute +20 to L_match; L_patient must ignore them.
        assert compute_l_patient(contributions) == 0.0

    def test_excludes_features_not_on_allowlist(self):
        """Only allow-listed patient features contribute; anything else (a
        future or unexpected feature name) is dropped, not silently summed."""
        contributions = [
            _fc("patient_age", 1.0 / 80.0),   # counted
            _fc("report_date", 0.001),        # not on the allowlist → dropped
            _fc("some_new_feature", 1e-6),    # not on the allowlist → dropped
        ]
        assert compute_l_patient(contributions) == pytest.approx(1.90309, abs=1e-4)

    def test_sums_patient_specific_contributions(self):
        """sex + age → -log10(0.5) + -log10(1/80) ≈ 0.30103 + 1.90309."""
        contributions = [_fc("patient_sex", 0.5), _fc("patient_age", 1.0 / 80.0)]
        assert compute_l_patient(contributions) == pytest.approx(2.20412, abs=1e-4)

    def test_mixed_contributions_only_patient_specific_count(self):
        """Drug/AE entries are ignored even when interleaved."""
        contributions = [
            _fc("drug_set", 1e-10),           # excluded
            _fc("patient_sex", 0.5),          # 0.30103
            _fc("ae_set", 1e-10),             # excluded
            _fc("patient_age", 1.0 / 80.0),   # 1.90309
            _fc("clinical_date", 1.0 / 540.0),  # 2.73239
        ]
        expected = 0.30103 + 1.90309 + 2.73239
        assert compute_l_patient(contributions) == pytest.approx(expected, abs=1e-3)

    def test_skips_nonpositive_p_f(self):
        """A non-positive p_f is skipped rather than raising on log10(0)."""
        contributions = [
            _fc("patient_sex", 0.0),      # skipped (defensive)
            _fc("patient_age", -1.0),     # skipped (defensive)
            _fc("patient_weight", 0.5),   # contributes 0.30103
        ]
        assert compute_l_patient(contributions) == pytest.approx(0.30103, abs=1e-4)

    def test_applies_no_multiplicity_adjustment(self):
        """L_patient is the bare sum — no anchor term, however large M is.

        Its comparison universe is the unknown K patients in a shared
        publication, not the anchor population, so the C(N_anchor, 2)
        multiplicity that L_match carries must not appear here.
        """
        contributions = [_fc("patient_age", 0.01)]  # -log10 = exactly 2.0
        # Had we subtracted log10(N_pairs) for N_anchor=50, we would get
        # 2.0 - log10(1225) ≈ -1.09. The correct result is 2.0.
        assert compute_l_patient(contributions) == pytest.approx(2.0, abs=1e-6)

    def test_accepts_both_contribution_shapes(self):
        """FeatureProbability and FeatureContribution are interchangeable here.

        compute_probability passes the internal form; callers reading a
        serialized PairFeatures pass the model form. They must agree.
        """
        from llm_dup_dev.feature_generation.probability import FeatureProbability

        internal = [
            FeatureProbability(feature="patient_sex", p_f=0.5, method="domain_size"),
            FeatureProbability(feature="drug_set", p_f=1e-10, method="empirical"),
        ]
        serialized = [_fc("patient_sex", 0.5), _fc("drug_set", 1e-10)]
        assert compute_l_patient(internal) == compute_l_patient(serialized)


class TestLPatientOnProbabilityResult:
    """compute_probability must carry L_patient alongside L_match."""

    def test_l_patient_is_populated(self, basic_match, anchor_drugs, stats,
                                    domain_config):
        """The result exposes L_patient derived from its own contributions."""
        result = compute_probability(
            basic_match, anchor_drugs, stats, domain_config
        )
        assert result.L_patient == pytest.approx(
            compute_l_patient(result.estimate.feature_contributions)
        )
        assert result.L_patient > 0

    def test_l_patient_excludes_the_ae_set_contribution(
        self, basic_match, anchor_drugs, stats, domain_config
    ):
        """With a DRUGS anchor the AE set contributes to L_match, not L_patient."""
        result = compute_probability(
            basic_match, anchor_drugs, stats, domain_config
        )
        features = {fc.feature for fc in result.estimate.feature_contributions}
        assert "ae_set" in features  # it is present, and must still be excluded
        by_hand = sum(
            -math.log10(fc.p_f)
            for fc in result.estimate.feature_contributions
            if fc.feature not in ("drug_set", "ae_set") and fc.p_f > 0
        )
        assert result.L_patient == pytest.approx(by_hand)

    def test_l_patient_is_independent_of_anchor_size(self, basic_match, stats,
                                                     domain_config):
        """Changing the anchor moves L_match but must not move L_patient."""
        small = AnchorPopulation(N_drugs=3, N_AEs=200, N_drugs_AEs=2,
                                 type=AnchorType.DRUGS, N_anchor=3)
        large = AnchorPopulation(N_drugs=48, N_AEs=200, N_drugs_AEs=12,
                                 type=AnchorType.DRUGS, N_anchor=48)
        r_small = compute_probability(basic_match, small, stats, domain_config)
        r_large = compute_probability(basic_match, large, stats, domain_config)

        assert r_small.L_match != r_large.L_match          # anchor moves L_match
        assert r_small.L_patient == pytest.approx(r_large.L_patient)


class TestLPatientSerialization:
    """L_patient must reach consumers as a field, not only as prose."""

    def test_pair_features_carries_l_patient(self):
        """The field round-trips through JSON."""
        f = PairFeatures(
            pair_id="p",
            reporting_context=ReportingContext(level=ReportingContextLevel.GLOBAL),
            anchor=AnchorPopulation(N_drugs=100, N_AEs=100, N_drugs_AEs=50,
                                    type=AnchorType.DRUGS_AES, N_anchor=50),
            probability=ProbabilityEstimate(p_joint=1e-5, p_match=0.01),
            L_match=3.0,
            L_patient=2.31,
            inconsistency_counts=InconsistencyCounts(),
        )
        restored = PairFeatures.model_validate_json(f.model_dump_json())
        assert restored.L_patient == pytest.approx(2.31)

    def test_l_patient_is_required(self):
        """No default: every constructor must decide.

        A silent 0.0 would read as "no patient evidence" and could flip a
        Duplicates pair to Abstain.
        """
        assert "L_patient" in PairFeatures.model_fields
        assert PairFeatures.model_fields["L_patient"].is_required()

    def test_schema_exposes_l_patient(self):
        """The published schema carries the field, beside L_match."""
        schema = PairFeatures.model_json_schema()
        assert "L_patient" in schema["properties"]
        assert "L_patient" in schema["required"]
