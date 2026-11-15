"""Tests for the deterministic vigiTwin assessment summary.

The two worked examples from the specification are reproduced verbatim as
acceptance tests. The rest pin the claims the generator must not make: a
threshold it cannot verify, a comparison that never happened, a probability of
duplication.
"""

from __future__ import annotations

import pytest

from llm_dup_dev.models.classification import (
    ClassificationResult,
    InconsistencyPenalty,
    PenaltyTerm,
)
from llm_dup_dev.models.enums import (
    AnchorType,
    IndependenceAssessment,
    IndependenceReason,
    InconsistencySeverity,
    RelationshipAssessment,
    ReportingContextLevel,
)
from llm_dup_dev.models.explicit_link import (
    ExplicitLinkAssessment,
    ExplicitLinkVerdict,
)
from llm_dup_dev.models.narrative_source_case import (
    SourceCaseAssessment,
    SourceCaseVerdict,
)
from llm_dup_dev.models.pair_features import (
    AnchorPopulation,
    FeatureContribution,
    Inconsistency,
    InconsistencyCounts,
    PairFeatures,
    ProbabilityEstimate,
    ReportingContext,
)
from llm_dup_dev.review_html.evidence.settings import EvidenceRunSettings
from llm_dup_dev.review_html.evidence.summary import (
    SummarySlot,
    build_assessment_summary,
)


def _contribution(
    feature: str, decades: float, value: str | None = None
) -> FeatureContribution:
    return FeatureContribution(
        feature=feature,
        p_f=10 ** (-decades),
        method="domain_size",
        matched_value=value,
        value_resolution="day" if value else None,
    )


def _features(**overrides) -> PairFeatures:
    base = {
        "pair_id": "R1__R2",
        "reporting_context": ReportingContext(
            level=ReportingContextLevel.COUNTRY, country="US"
        ),
        "matching_drugs": ["aspirin"],
        "matching_AEs": ["headache"],
        "anchor": AnchorPopulation(
            N_drugs=10,
            N_AEs=10,
            N_drugs_AEs=5,
            type=AnchorType.DRUGS_AES,
            N_anchor=5,
        ),
        "probability": ProbabilityEstimate(
            p_joint=1e-4, p_match=1e-4, feature_contributions=[]
        ),
        "L_match": 4.0,
        "L_patient": 1.0,
    }
    base.update(overrides)
    return PairFeatures(**base)


def _classification(**overrides) -> ClassificationResult:
    base = {
        "pair_id": "R1__R2",
        "independence_assessment": IndependenceAssessment.NON_INDEPENDENT,
        "relationship_assessment": RelationshipAssessment.SAME_CASE,
        "independence_reason": IndependenceReason.RARITY_COMPELLING,
        "penalty": InconsistencyPenalty(w_tot=0.0, threshold=4.0),
    }
    base.update(overrides)
    return ClassificationResult(**base)


class TestWorkedExamples:
    """The two examples from the specification, verbatim."""

    def test_strongly_matching_pair(self):
        features = _features(
            reporting_context=ReportingContext(
                level=ReportingContextLevel.COUNTRY, country="US"
            ),
            matching_drugs=[f"drug{i}" for i in range(17)],
            matching_AEs=[f"ae{i}" for i in range(8)],
            anchor=AnchorPopulation(
                N_drugs=5,
                N_AEs=2,
                N_drugs_AEs=2,
                type=AnchorType.AES,
                N_anchor=2,
            ),
            probability=ProbabilityEstimate(
                p_joint=1e-17,
                p_match=1e-17,
                feature_contributions=[
                    _contribution("drug_set", 6.7),
                    _contribution("patient_sex", 0.3),
                    _contribution("clinical_date", 2.2, "2002-01-04"),
                    _contribution("clinical_date", 1.9, "2002-03-22"),
                    _contribution("clinical_date", 2.5, "2002-05-02"),
                    _contribution("clinical_date", 3.6, "2002-10-10"),
                ],
            ),
            L_match=15.0,
            inconsistencies=[
                Inconsistency(
                    feature="drug_set",
                    severity=InconsistencySeverity.MODERATE,
                    terms=["vitamins nos"],
                    kind="missing_in_r2",
                ),
                Inconsistency(
                    feature="drug_set",
                    severity=InconsistencySeverity.MODERATE,
                    terms=["multivitamin"],
                    kind="missing_in_r1",
                ),
                Inconsistency(
                    feature="ae_clinical_picture:unmatched_burden:r2_to_r1",
                    severity=InconsistencySeverity.MINOR,
                    terms=["Decreased appetite"],
                    source="rule_ae_clinical_picture",
                ),
            ],
            inconsistency_counts=InconsistencyCounts(minor=1, moderate=2),
        )
        classification = _classification(
            penalty=InconsistencyPenalty(
                w_tot=2.4,
                threshold=4.0,
                terms=[
                    PenaltyTerm(
                        severity=InconsistencySeverity.MINOR,
                        count=1,
                        weight=0.4,
                    ),
                    PenaltyTerm(
                        severity=InconsistencySeverity.MODERATE,
                        count=2,
                        weight=1.0,
                    ),
                ],
            ),
        )
        assert build_assessment_summary(features, classification).as_text() == (
            "The reports share 17 drug terms and eight adverse event terms. "
            "The shared adverse event pattern defines an anchor population of "
            "two reports in the selected country context. Additional matching "
            "evidence includes the shared drug pattern, sex and four clinical "
            "dates. vigiTwin identified two moderate drug inconsistencies and "
            "one minor adverse event inconsistency. The matching score of 15.0 "
            "supports non-independence, and the total inconsistency penalty of "
            "2.4 is below the same-case threshold of 4.0. The reports were "
            "classified as duplicates."
        )

    def test_independence_not_rejected(self):
        features = _features(
            reporting_context=ReportingContext(
                level=ReportingContextLevel.COUNTRY_YEAR, country="US", year=2010
            ),
            matching_drugs=["meclozine"],
            matching_AEs=["exposure during pregnancy"],
            anchor=AnchorPopulation(
                N_drugs=472,
                N_AEs=4536,
                N_drugs_AEs=11,
                type=AnchorType.DRUGS_AES,
                N_anchor=11,
            ),
            probability=ProbabilityEstimate(
                p_joint=1.0, p_match=1.0, feature_contributions=[]
            ),
            L_match=0.0,
            inconsistencies=[
                Inconsistency(
                    feature="ae_clinical_picture:unmatched_burden:r1_to_r2",
                    severity=InconsistencySeverity.MINOR,
                    terms=["Ventricular septal defect"],
                    source="rule_ae_clinical_picture",
                ),
                Inconsistency(
                    feature="ae_clinical_picture:unmatched_burden:r2_to_r1",
                    severity=InconsistencySeverity.MINOR,
                    terms=["Cleft lip"],
                    source="rule_ae_clinical_picture",
                ),
            ],
            inconsistency_counts=InconsistencyCounts(minor=2),
        )
        classification = _classification(
            independence_assessment=IndependenceAssessment.INDEPENDENT,
            relationship_assessment=None,
            independence_reason=IndependenceReason.RARITY_INSUFFICIENT,
            penalty=InconsistencyPenalty(w_tot=0.8),
        )
        assert build_assessment_summary(features, classification).as_text() == (
            "The reports share one drug term and one adverse event term. This "
            "combination occurs in 11 reports in the selected country-year "
            "context and defines the anchor population. No additional matching "
            "evidence was identified. The matching score of 0 did not support "
            "rejection of independence, and the reports were classified as "
            "unrelated. Two minor adverse event inconsistencies were "
            "identified, but not weighed against the same-case tolerance: "
            "that comparison is only reached once independence is rejected."
        )


class TestThresholdsAreOnlyQuotedWhenKnown:
    """A threshold the record does not carry is never invented."""

    def test_no_threshold_number_without_run_settings(self):
        text = build_assessment_summary(_features(), _classification()).as_text()
        assert "threshold of 3" not in text
        assert "exceeding" not in text

    def test_threshold_quoted_from_run_settings(self):
        settings = EvidenceRunSettings(
            lmatch_compelling=3.0, epsilon=1e-15, source="run_manifest.yaml"
        )
        text = build_assessment_summary(
            _features(), _classification(), run_settings=settings
        ).as_text()
        assert "exceeding the compelling threshold of 3.0" in text

    def test_custom_threshold_is_used_not_the_default(self):
        """A run with its own thresholds is described with its own numbers."""
        settings = EvidenceRunSettings(lmatch_compelling=2.5, epsilon=1e-12)
        text = build_assessment_summary(
            _features(), _classification(), run_settings=settings
        ).as_text()
        assert "threshold of 2.5" in text
        assert "3.0" not in text

    def test_missing_settings_are_recorded_as_a_gap(self):
        summary = build_assessment_summary(_features(), _classification())
        assert [g.code.value for g in summary.gaps] == ["run_settings_missing"]


class TestNoComparisonWasMade:
    """``penalty.threshold is None`` means no comparison, and must read so."""

    def test_independent_pair_says_why_no_comparison_was_made(self):
        classification = _classification(
            independence_assessment=IndependenceAssessment.INDEPENDENT,
            relationship_assessment=None,
            independence_reason=IndependenceReason.RARITY_INSUFFICIENT,
            penalty=InconsistencyPenalty(w_tot=1.2),
        )
        text = build_assessment_summary(
            _features(L_match=0.5), classification
        ).as_text()
        assert "because independence was not rejected" in text
        assert "below the same-case threshold" not in text
        # The old wording read as though the comparison ran and stalled.
        assert "no same-case decision was reached" not in text

    def test_pinned_different_case_reports_no_penalty_comparison(self):
        """``SAME_SOURCE_DIFFERENT_CASE`` is pinned regardless of the penalty."""
        features = _features(
            source_case_assessment=SourceCaseAssessment(
                verdict=SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE,
                shared_source_identifier="10.1000/abc",
            ),
            inconsistency_counts=InconsistencyCounts(decisive=1),
        )
        classification = _classification(
            relationship_assessment=RelationshipAssessment.DIFFERENT_CASE,
            independence_reason=IndependenceReason.SHARED_SOURCE,
            penalty=InconsistencyPenalty(w_tot=100.0),
        )
        text = build_assessment_summary(features, classification).as_text()
        assert "no inconsistency-penalty comparison" in text
        assert "below" not in text


class TestDecisionPathways:
    """Every pathway is described by the reason actually recorded."""

    def test_shared_source_does_not_credit_the_score(self):
        features = _features(
            L_match=0.2,
            source_case_assessment=SourceCaseAssessment(
                verdict=SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT,
                shared_source_identifier="10.1000/abc",
            ),
        )
        classification = _classification(
            independence_reason=IndependenceReason.SHARED_SOURCE
        )
        text = build_assessment_summary(features, classification).as_text()
        assert "direct evidence of a shared source" in text
        assert "not on the matching score" in text
        assert "matching score of 0.2" not in text

    def test_explicit_link_is_named_as_such(self):
        features = _features(
            L_match=0.1,
            explicit_link=ExplicitLinkAssessment(
                verdict=ExplicitLinkVerdict.EXPLICIT_LINK
            ),
        )
        classification = _classification(
            independence_reason=IndependenceReason.EXPLICIT_LINK
        )
        text = build_assessment_summary(features, classification).as_text()
        assert "explicitly references the other" in text

    def test_abstain_is_not_reported_as_a_classification(self):
        classification = _classification(
            relationship_assessment=RelationshipAssessment.ABSTAIN,
            penalty=InconsistencyPenalty(w_tot=5.0, threshold=4.0),
        )
        text = build_assessment_summary(_features(), classification).as_text()
        assert "leaving the question unresolved" in text
        assert "reached no classification" in text

    def test_no_classification_still_describes_the_evidence(self):
        summary = build_assessment_summary(_features(), None)
        assert "The reports share" in summary.as_text()
        assert "no_classification" in [g.code.value for g in summary.gaps]


class TestAnchorIsNotCreditedTwice:
    """An anchor-defining set did not also contribute to the score."""

    def test_anchor_set_absent_from_additional_evidence(self):
        features = _features(
            anchor=AnchorPopulation(
                N_drugs=4,
                N_AEs=90,
                N_drugs_AEs=4,
                type=AnchorType.DRUGS,
                N_anchor=4,
            ),
            probability=ProbabilityEstimate(
                p_joint=1e-4,
                p_match=1e-4,
                feature_contributions=[_contribution("ae_set", 2.0)],
            ),
        )
        text = build_assessment_summary(features, _classification()).as_text()
        assert (
            "The shared drug pattern occurs in 4 reports in the selected "
            "country context and defines the anchor population." in text
        )
        assert (
            "Additional matching evidence includes the shared adverse event "
            "pattern." in text
        )
        assert "includes the shared drug pattern" not in text

    def test_single_set_anchor_is_not_called_a_combination(self):
        """"This combination" would imply the other set narrowed it too."""
        features = _features(
            anchor=AnchorPopulation(
                N_drugs=40,
                N_AEs=900,
                N_drugs_AEs=40,
                type=AnchorType.DRUGS,
                N_anchor=40,
            ),
        )
        text = build_assessment_summary(features, _classification()).as_text()
        assert "This combination" not in text
        assert "The shared drug pattern occurs in 40 reports" in text


class TestForbiddenVocabulary:
    """The summary never offers a probability of duplication."""

    @pytest.mark.parametrize(
        "forbidden",
        ["p_match", "p_joint", "L_match", "probability", "p-value", "pipeline"],
    )
    def test_terms_absent(self, forbidden):
        texts = [
            build_assessment_summary(_features(), _classification()).as_text(),
            build_assessment_summary(
                _features(L_match=0.0),
                _classification(
                    independence_assessment=IndependenceAssessment.INDEPENDENT,
                    relationship_assessment=None,
                    independence_reason=IndependenceReason.RARITY_INSUFFICIENT,
                    penalty=InconsistencyPenalty(w_tot=0.0),
                ),
            ).as_text(),
        ]
        for text in texts:
            assert forbidden.lower() not in text.lower()


class TestDeterminism:
    """Same inputs, same text — including under reordered contributions."""

    def test_repeated_calls_agree(self):
        features, classification = _features(), _classification()
        first = build_assessment_summary(features, classification).as_text()
        second = build_assessment_summary(features, classification).as_text()
        assert first == second

    def test_contribution_order_does_not_change_the_text(self):
        contributions = [
            _contribution("patient_sex", 0.3),
            _contribution("drug_set", 6.7),
            _contribution("clinical_date", 2.2, "2002-01-04"),
        ]
        forwards = _features(
            probability=ProbabilityEstimate(
                p_joint=1e-9,
                p_match=1e-9,
                feature_contributions=contributions,
            )
        )
        backwards = _features(
            probability=ProbabilityEstimate(
                p_joint=1e-9,
                p_match=1e-9,
                feature_contributions=list(reversed(contributions)),
            )
        )
        assert build_assessment_summary(
            forwards, _classification()
        ).as_text() == build_assessment_summary(
            backwards, _classification()
        ).as_text()


class TestNearThresholdRounding:
    """Rounding must not make a decision read as contradicting itself."""

    def test_precision_grows_until_the_values_differ(self):
        classification = _classification(
            penalty=InconsistencyPenalty(w_tot=3.999, threshold=4.0)
        )
        text = build_assessment_summary(_features(), classification).as_text()
        assert "penalty of 3.999 is below the same-case threshold of 4.000" in text

    def test_equal_values_read_as_at_or_above(self):
        classification = _classification(
            relationship_assessment=RelationshipAssessment.DIFFERENT_CASE,
            penalty=InconsistencyPenalty(w_tot=4.0, threshold=4.0),
        )
        text = build_assessment_summary(_features(), classification).as_text()
        assert "4.0 is at or above the same-case threshold of 4.0" in text


class TestParagraphs:
    """Splitting is by slot, so the break lands in the same place every time."""

    def test_split_after_additional_evidence(self):
        paragraphs = build_assessment_summary(
            _features(), _classification()
        ).paragraphs(split_after=SummarySlot.ADDITIONAL)
        assert len(paragraphs) == 2
        assert paragraphs[0].startswith("The reports share")
        assert paragraphs[1].startswith("vigiTwin identified")
