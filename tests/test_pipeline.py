"""Tests for the feature generation pipeline (end-to-end orchestration)."""

import pytest

from llm_dup_dev.classifier.config import PipelineConfig
from llm_dup_dev.feature_generation.pipeline import generate_pair_features
from llm_dup_dev.feature_generation.probability import FrequencyStats
from llm_dup_dev.models.enums import (
    InconsistencySeverity,
    ReportingContextLevel,
)
from llm_dup_dev.models.narrative_source_case import (
    SourceCaseAssessment,
    SourceCaseVerdict,
    SourceIdentifierType,
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
            config=default_config,
            pair_id="CUSTOM_PAIR_001",
        )
        assert features.pair_id == "CUSTOM_PAIR_001"

    def test_empty_report_pair(
        self, report_empty, report_empty_2, default_config
    ):
        """Two empty reports should produce features without errors.

        Two *distinct* empty reports: a report paired with itself is rejected
        when the pair id is derived.
        """
        stats = FrequencyStats(
            N_total=100_000, N_drugs=0, N_AEs=0, N_drugs_AEs=0
        )
        features = generate_pair_features(
            r1=report_empty,
            r2=report_empty_2,
            stats=stats,
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
            config=default_config,
        )

        total_from_list = len(features.inconsistencies)
        total_from_counts = (
            features.inconsistency_counts.minor
            + features.inconsistency_counts.moderate
            + features.inconsistency_counts.strong
            + features.inconsistency_counts.decisive
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
            config=default_config,
        )
        from llm_dup_dev.models.pair_features import PairFeatures

        json_str = features.model_dump_json()
        restored = PairFeatures.model_validate_json(json_str)
        assert restored.pair_id == features.pair_id
        assert restored.L_match == features.L_match
        assert restored.matching_drugs == features.matching_drugs


class TestSourceCaseAssessmentIntegration:

    def test_none_assessment_leaves_field_null_and_adds_no_findings(
        self, report_a, report_b, stats_similar, default_config
    ):
        features = generate_pair_features(
            r1=report_a,
            r2=report_b,
            stats=stats_similar,
            config=default_config,
            source_case_assessment=None,
        )
        assert features.source_case_assessment is None
        assert all(
            i.source != "narrative_source_case"
            for i in features.inconsistencies
        )

    def test_same_source_different_case_adds_decisive(
        self, report_a, report_b, stats_similar, default_config
    ):
        assessment = SourceCaseAssessment(
            verdict=SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE,
            shared_source_identifier="10.1234/abc",
            shared_source_identifier_type=SourceIdentifierType.DOI,
        )
        features = generate_pair_features(
            r1=report_a,
            r2=report_b,
            stats=stats_similar,
            config=default_config,
            source_case_assessment=assessment,
        )
        # Assessment preserved verbatim
        assert features.source_case_assessment == assessment
        # One decisive source-case inconsistency synthesized
        src_rows = [
            i for i in features.inconsistencies
            if i.source == "narrative_source_case"
        ]
        assert len(src_rows) == 1
        assert src_rows[0].severity == InconsistencySeverity.DECISIVE
        # Counts reflect it
        assert features.inconsistency_counts.decisive >= 1

    def test_same_source_uncomparable_adds_no_findings(
        self, report_a, report_b, stats_similar, default_config
    ):
        # SAME_SOURCE_UNCOMPARABLE_CASE represents absence of comparable
        # evidence rather than a discrepancy — no Inconsistency row is
        # synthesized and the assessment alone drives the override.
        assessment = SourceCaseAssessment(
            verdict=SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
            shared_source_identifier="10.xyz/abc",
            shared_source_identifier_type=SourceIdentifierType.DOI,
        )
        features = generate_pair_features(
            r1=report_a,
            r2=report_b,
            stats=stats_similar,
            config=default_config,
            source_case_assessment=assessment,
        )
        src_rows = [
            i for i in features.inconsistencies
            if i.source == "narrative_source_case"
        ]
        assert src_rows == []
        # Counts should not have been bumped by this verdict.
        assert features.inconsistency_counts.moderate == 0
        # Raw assessment still preserved for the decision logic to branch on.
        assert features.source_case_assessment == assessment

    def test_positive_verdict_adds_no_findings(
        self, report_a, report_b, stats_similar, default_config
    ):
        assessment = SourceCaseAssessment(
            verdict=SourceCaseVerdict.SAME_SOURCE_SAME_CASE,
            shared_source_identifier="10.1234/abc",
            shared_source_identifier_type=SourceIdentifierType.DOI,
        )
        features = generate_pair_features(
            r1=report_a,
            r2=report_b,
            stats=stats_similar,
            config=default_config,
            source_case_assessment=assessment,
        )
        src_rows = [
            i for i in features.inconsistencies
            if i.source == "narrative_source_case"
        ]
        assert src_rows == []
        # Raw assessment still preserved for the decision logic to branch on.
        assert features.source_case_assessment == assessment


# ===================================================================
# Orientation invariance of the decision
# ===================================================================

#: Fields whose value legitimately depends on which report is "report 1".
#: Everything else must survive a reversal untouched.
_PRESENTATIONAL_FIELDS = ("description", "rationale")


def _reversed_pair(r1, r2, stats, config):
    """Features for (r1, r2) and for (r2, r1), in that order."""
    return (
        generate_pair_features(r1=r1, r2=r2, stats=stats, config=config),
        generate_pair_features(r1=r2, r2=r1, stats=stats, config=config),
    )


class TestOrientationInvariance:
    """Reversing r1/r2 must not change anything that scores, joins or decides.

    ``generate_pair_features`` derives a canonical ``pair_id`` but deliberately
    does *not* reorder its two reports, because feature content is
    orientation-dependent by design: ``EvidenceSummary.report1_only`` and the
    descriptions that quote both sides are about a specific report. So a
    reversed call yields a payload that differs in presentation under the same
    ``pair_id``.

    That is only tolerable while the *decision* is orientation-invariant.
    ``PairFeatures`` carries no report ids, so a consumer holding a stored
    record cannot tell which orientation produced it. If a future
    orientation-dependent rule made the decision differ, one ``pair_id`` would
    mean two different answers — precisely what canonical pair identity exists
    to prevent (see :mod:`llm_dup_dev.models.pair_identity`). Nothing else
    guards that property, so these tests do.

    Both a near-duplicate pair and a clearly unrelated one are covered, so the
    duplicate and the unrelated decision paths are each exercised.
    """

    @pytest.fixture(params=["near_duplicate", "unrelated"])
    def pair(self, request, report_a, report_b, report_c):
        return report_a, report_b if request.param == "near_duplicate" else report_c

    def test_pair_id_is_the_same_under_reversal(
        self, pair, stats_similar, default_config
    ):
        fwd, rev = _reversed_pair(*pair, stats_similar, default_config)
        assert fwd.pair_id == rev.pair_id

    def test_classification_is_identical_under_reversal(
        self, pair, stats_similar, default_config
    ):
        """The whole decision trace, not just the label.

        If this ever fails, the same pair_id now identifies two different
        answers and the orientation must be canonicalised upstream — do not
        relax the assertion.
        """
        from llm_dup_dev.classifier.config import DecisionThresholds
        from llm_dup_dev.classifier.decision_logic import classify

        fwd, rev = _reversed_pair(*pair, stats_similar, default_config)
        thresholds = DecisionThresholds()
        assert classify(fwd, thresholds).model_dump(mode="json") == classify(
            rev, thresholds
        ).model_dump(mode="json")

    def test_every_other_field_is_identical_under_reversal(
        self, pair, stats_similar, default_config
    ):
        """The whole payload bar the two documented exceptions.

        Asserted as "everything except" rather than field by field so a field
        added later is covered by default: a new orientation-dependent one has
        to be added to the exception list deliberately, which is the moment to
        ask whether it should exist at all.
        """
        fwd, rev = _reversed_pair(*pair, stats_similar, default_config)
        exceptions = {"inconsistencies", "evidence_summary"}

        def comparable(features):
            return {
                k: v
                for k, v in features.model_dump(mode="json").items()
                if k not in exceptions
            }

        assert comparable(fwd) == comparable(rev)

    def test_inconsistencies_differ_only_in_prose(
        self, pair, stats_similar, default_config
    ):
        """Same findings, same severities, same order — only the wording flips.

        A reversed call renders "X vs Y" as "Y vs X"; nothing structural about
        the finding may change.
        """
        fwd, rev = _reversed_pair(*pair, stats_similar, default_config)

        def structural(inconsistency):
            return {
                k: v
                for k, v in inconsistency.model_dump(mode="json").items()
                if k not in _PRESENTATIONAL_FIELDS
            }

        assert [structural(i) for i in fwd.inconsistencies] == [
            structural(i) for i in rev.inconsistencies
        ]

    def test_evidence_summary_is_the_documented_exception(
        self, pair, stats_similar, default_config
    ):
        """report1_only/report2_only swap — that is the point of the field.

        Pinned so the presentational asymmetry stays a deliberate, known one
        rather than something that quietly grows.
        """
        fwd, rev = _reversed_pair(*pair, stats_similar, default_config)
        assert fwd.evidence_summary.report1_only == rev.evidence_summary.report2_only
        assert fwd.evidence_summary.report2_only == rev.evidence_summary.report1_only
        assert fwd.evidence_summary.shared == rev.evidence_summary.shared
