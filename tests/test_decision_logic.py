"""Tests for classifier.decision_logic.

Uses the weighted inconsistency score
``w_tot = w_minor * minor + w_moderate * moderate + w_strong * strong
         + w_decisive * decisive``
(defaults: w_minor=0.4, w_moderate=1.0, w_strong=3.0, w_decisive=100.0)
with per-zone thresholds:

    compelling zone (L > 3.0): w_tot < 4 → Non-independent / Duplicates
                            else      → Non-independent / Otherwise_related
    intermediate zone (L > 2.0): w_tot < 3 → Non-independent / Duplicates
                            else      → Non-independent / Otherwise_related
    limited zone   (L > 1.0): w_tot < 1 → Non-independent / Duplicates
                            else      → Non-independent / Otherwise_related
    below limited:            always Independent
"""

from llm_dup_dev.classifier.config import DecisionThresholds
from llm_dup_dev.classifier.decision_logic import classify
from llm_dup_dev.feature_generation.probability import compute_l_patient
from llm_dup_dev.models.enums import (
    AnchorType,
    RelationshipAssessment,
    IndependenceAssessment,
    IndependenceReason,
    NarrativeConsistency,
    ReportingContextLevel,
)
from llm_dup_dev.models.narrative_source_case import (
    SourceCaseAssessment,
    SourceCaseVerdict,
    SourceIdentifierType,
)
from llm_dup_dev.models.pair_features import (
    AnchorPopulation,
    FeatureContribution,
    InconsistencyCounts,
    PairFeatures,
    ProbabilityEstimate,
    ReportingContext,
)


# ---------------------------------------------------------------------------
# Helper to build a minimal PairFeatures with controllable inputs
# ---------------------------------------------------------------------------

def _make_features(
    L_match: float,
    strong: int = 0,
    moderate: int = 0,
    decisive: int = 0,
    pair_id: str = "test_pair",
    source_case_assessment: SourceCaseAssessment | None = None,
    feature_contributions: list[FeatureContribution] | None = None,
    L_patient: float | None = None,
) -> PairFeatures:
    """Build a minimal PairFeatures for decision logic testing.

    ``feature_contributions`` populates ``ProbabilityEstimate.feature_contributions``
    so tests can exercise the patient-specific ``L_patient`` decomposition
    used by the SAME_SOURCE_UNCOMPARABLE_CASE Duplicates branch. Default
    is an empty list (no decomposition available).

    ``L_patient`` defaults to what the feature-generation pipeline would
    derive from ``feature_contributions``, so tests can keep expressing
    patient evidence as contributions. Pass it explicitly to decouple the
    two (e.g. to pin a boundary value directly).
    """
    contributions = feature_contributions or []
    return PairFeatures(
        pair_id=pair_id,
        reporting_context=ReportingContext(level=ReportingContextLevel.GLOBAL),
        anchor=AnchorPopulation(
            N_drugs=100, N_AEs=100, N_drugs_AEs=50,
            type=AnchorType.DRUGS_AES, N_anchor=50,
        ),
        probability=ProbabilityEstimate(
            p_joint=1e-5,
            p_match=0.01,
            feature_contributions=contributions,
        ),
        L_match=L_match,
        L_patient=(
            compute_l_patient(contributions) if L_patient is None else L_patient
        ),
        inconsistency_counts=InconsistencyCounts(
            moderate=moderate, strong=strong, decisive=decisive,
        ),
        narrative_consistency=NarrativeConsistency.NEUTRAL,
        source_case_assessment=source_case_assessment,
    )


def _fc(feature: str, p_f: float) -> FeatureContribution:
    """Concise FeatureContribution builder for tests."""
    return FeatureContribution(
        feature=feature, p_f=p_f, method="domain_size",
    )


# Fixture contribution bundles used in the UNCOMPARABLE_CASE tests below.
# Values come from the plan's worked-example table:
#   sex p_f=0.5  → -log10 ≈ 0.30
#   age p_f=1/80 → -log10 ≈ 1.90
# Sum (sex + age) ≈ 2.204 — just clears lpatient_compelling = 2.0. The
# margin is only 0.204, so raising that default above ~2.2 flips these.
_SEX_PLUS_AGE_CONTRIBS = [
    _fc("patient_sex", 0.5),
    _fc("patient_age", 1.0 / 80.0),
]
_SEX_ONLY_CONTRIBS = [_fc("patient_sex", 0.5)]
# Drug/AE contributions with extreme p_f — full L_match would be enormous,
# but L_patient must remain 0 because these features are excluded.
_DRUG_AE_ONLY_CONTRIBS = [
    _fc("drug_set", 1e-10),
    _fc("ae_set", 1e-10),
]


def _assessment(
    verdict: SourceCaseVerdict,
    identifier: str | None = "10.1234/abc",
) -> SourceCaseAssessment:
    return SourceCaseAssessment(
        verdict=verdict,
        shared_source_identifier=identifier,
        shared_source_identifier_type=(
            SourceIdentifierType.DOI if identifier else None
        ),
    )


def _assert_same_case(result):
    assert result.independence_assessment == IndependenceAssessment.NON_INDEPENDENT
    assert result.relationship_assessment == RelationshipAssessment.SAME_CASE
    assert result.final_label == "Duplicates"


def _assert_different_case(result):
    assert result.independence_assessment == IndependenceAssessment.NON_INDEPENDENT
    assert result.relationship_assessment == RelationshipAssessment.DIFFERENT_CASE
    assert result.final_label == "Otherwise_related"


def _assert_independent(result):
    assert result.independence_assessment == IndependenceAssessment.INDEPENDENT
    assert result.relationship_assessment is None
    assert result.final_label == "Unrelated"


def _assert_abstain(result):
    assert result.independence_assessment == IndependenceAssessment.NON_INDEPENDENT
    assert result.relationship_assessment == RelationshipAssessment.ABSTAIN
    assert result.final_label == "Abstain"


# Default thresholds: lmatch compelling=3.0, intermediate=2.0, limited=1.0;
# lpatient_compelling=2.0; w_minor=0.4, w_moderate=1.0, w_strong=3.0.
THRESHOLDS = DecisionThresholds()


# ===================================================================
# Compelling zone: L_match > 3.0  —  w_tot < 4 → Duplicates
# w_tot = 1*moderate + 3*strong
# ===================================================================

class TestStrongZone:
    """L_match > lmatch_compelling (default 3.0)."""

    def test_no_inconsistencies_duplicates(self):
        """w_tot=0 < 4 → Duplicates."""
        f = _make_features(L_match=4.0)
        result = classify(f, THRESHOLDS)
        _assert_same_case(result)

    def test_one_strong_zero_moderate_duplicates(self):
        """w_tot = 3*1 = 3 < 4 → Duplicates."""
        f = _make_features(L_match=4.0, strong=1)
        result = classify(f, THRESHOLDS)
        _assert_same_case(result)

    def test_three_moderate_duplicates(self):
        """w_tot = 1*3 = 3 < 4 → Duplicates."""
        f = _make_features(L_match=4.0, moderate=3)
        result = classify(f, THRESHOLDS)
        _assert_same_case(result)

    def test_one_strong_one_moderate_boundary_otherwise_related(self):
        """w_tot = 3 + 1 = 4, NOT < 4 → Otherwise_related."""
        f = _make_features(L_match=4.0, strong=1, moderate=1)
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)

    def test_two_strong_otherwise_related(self):
        """w_tot = 3*2 = 6 ≥ 4 → Otherwise_related."""
        f = _make_features(L_match=4.0, strong=2)
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)

    def test_four_moderate_boundary_otherwise_related(self):
        """w_tot = 1*4 = 4, NOT < 4 → Otherwise_related."""
        f = _make_features(L_match=4.0, moderate=4)
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)

    def test_boundary_lmatch_exactly_strong(self):
        """L_match == lmatch_compelling (3.0) → NOT in compelling zone, falls to medium."""
        f = _make_features(L_match=3.0)
        result = classify(f, THRESHOLDS)
        # 3.0 is NOT > 3.0, so falls to intermediate zone
        _assert_same_case(result)

    def test_just_above_strong(self):
        """L_match barely above strong threshold."""
        f = _make_features(L_match=3.01)
        result = classify(f, THRESHOLDS)
        _assert_same_case(result)


# ===================================================================
# Intermediate zone: lmatch_intermediate < L_match ≤ lmatch_compelling — w_tot < 3 → Duplicates
# ===================================================================

class TestMediumZone:
    """lmatch_intermediate (2.0) < L_match ≤ lmatch_compelling (3.0)."""

    def test_no_inconsistencies_duplicates(self):
        """w_tot=0 < 3 → Duplicates."""
        f = _make_features(L_match=2.5)
        result = classify(f, THRESHOLDS)
        _assert_same_case(result)

    def test_two_moderate_duplicates(self):
        """w_tot = 1*2 = 2 < 3 → Duplicates."""
        f = _make_features(L_match=2.5, moderate=2)
        result = classify(f, THRESHOLDS)
        _assert_same_case(result)

    def test_one_strong_boundary_otherwise_related(self):
        """w_tot = 3*1 = 3, NOT < 3 → Otherwise_related."""
        f = _make_features(L_match=2.5, strong=1)
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)

    def test_three_moderate_boundary_otherwise_related(self):
        """w_tot = 1*3 = 3, NOT < 3 → Otherwise_related."""
        f = _make_features(L_match=2.5, moderate=3)
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)

    def test_boundary_lmatch_exactly_med(self):
        """L_match == lmatch_intermediate (2.0) → NOT in intermediate zone, falls to weak."""
        f = _make_features(L_match=2.0)
        result = classify(f, THRESHOLDS)
        # 2.0 is NOT > 2.0, falls to limited zone
        # No inconsistencies → Duplicates
        _assert_same_case(result)


# ===================================================================
# Limited zone: lmatch_limited < L_match ≤ lmatch_intermediate — w_tot < 1 → Duplicates,
# else Non-independent / Otherwise_related (matching evidence still
# rejects independence; inconsistencies only downgrade the relationship).
# ===================================================================

class TestWeakZone:
    """lmatch_limited (1.0) < L_match ≤ lmatch_intermediate (2.0)."""

    def test_no_inconsistencies_duplicates(self):
        """w_tot=0 < 1 → Duplicates."""
        f = _make_features(L_match=1.5)
        result = classify(f, THRESHOLDS)
        _assert_same_case(result)

    def test_one_moderate_boundary_otherwise_related(self):
        """w_tot = 1, NOT < 1 → Otherwise_related."""
        f = _make_features(L_match=1.5, moderate=1)
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)

    def test_one_strong_otherwise_related(self):
        """w_tot = 3 ≥ 1 → Otherwise_related."""
        f = _make_features(L_match=1.5, strong=1)
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)

    def test_boundary_lmatch_exactly_weak(self):
        """L_match == lmatch_limited (1.0) → NOT in limited zone, falls to below."""
        f = _make_features(L_match=1.0)
        result = classify(f, THRESHOLDS)
        _assert_independent(result)


# ===================================================================
# Below weak: L_match ≤ lmatch_limited
# ===================================================================

class TestBelowWeak:
    """L_match ≤ lmatch_limited (1.0)."""

    def test_zero_lmatch_independent(self):
        f = _make_features(L_match=0.0)
        result = classify(f, THRESHOLDS)
        _assert_independent(result)

    def test_low_lmatch_independent(self):
        f = _make_features(L_match=0.5)
        result = classify(f, THRESHOLDS)
        _assert_independent(result)

    def test_no_inconsistencies_still_independent(self):
        """Even with zero inconsistencies, low L_match → Independent."""
        f = _make_features(L_match=0.8)
        result = classify(f, THRESHOLDS)
        _assert_independent(result)


# ===================================================================
# Decisive inconsistencies
# ===================================================================

class TestDecisiveInconsistency:
    """Decisive inconsistencies participate via ``w_decisive`` (default 100)
    which exceeds every zone threshold, so any decisive count forces the
    failing-zone branch."""

    def test_decisive_with_high_lmatch_otherwise_related(self):
        """Decisive + L_match > lmatch_intermediate → Non-independent / Otherwise_related."""
        f = _make_features(L_match=5.0, decisive=1)
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)

    def test_decisive_with_medium_lmatch_otherwise_related(self):
        """Decisive + L_match just above lmatch_intermediate → Otherwise_related."""
        f = _make_features(L_match=2.5, decisive=1)
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)

    def test_decisive_with_weak_lmatch_otherwise_related(self):
        """Decisive + lmatch_limited < L_match ≤ lmatch_intermediate → Otherwise_related."""
        f = _make_features(L_match=1.5, decisive=1)
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)

    def test_decisive_boundary_at_med(self):
        """Decisive + L_match == lmatch_intermediate (2.0) → NOT > med, falls to weak
        zone → Otherwise_related."""
        f = _make_features(L_match=2.0, decisive=1)
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)

    def test_decisive_at_weak_boundary_independent(self):
        """Decisive + L_match == lmatch_limited (1.0) → falls to below-limited →
        Independent (only zone where decisive still yields Unrelated)."""
        f = _make_features(L_match=1.0, decisive=1)
        result = classify(f, THRESHOLDS)
        _assert_independent(result)

    def test_decisive_forces_otherwise_related_in_clean_strong_zone(self):
        """Even with no other inconsistencies and very high L_match,
        decisive forces Otherwise_related (not Duplicates)."""
        f = _make_features(L_match=10.0, decisive=1, strong=0, moderate=0)
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)

    def test_multiple_decisive(self):
        """Multiple decisive inconsistencies still handled correctly."""
        f = _make_features(L_match=4.0, decisive=3)
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)
        assert "decisive=3" in result.explanation


# ===================================================================
# Custom thresholds
# ===================================================================

class TestCustomThresholds:
    """Verify that non-default thresholds work correctly."""

    def test_relaxed_lmatch(self):
        """With lower L_match thresholds, same L_match enters a higher zone."""
        relaxed = DecisionThresholds(
            lmatch_compelling=2.0, lmatch_intermediate=1.0, lmatch_limited=0.5,
        )
        # L_match=2.5 is now in the compelling zone (>2.0)
        # strong=1 → w_tot=3 < 4 → Duplicates
        f = _make_features(L_match=2.5, strong=1)
        result = classify(f, relaxed)
        _assert_same_case(result)

    def test_strict_lmatch(self):
        """With higher L_match thresholds, same L_match falls to lower zone."""
        strict = DecisionThresholds(
            lmatch_compelling=5.0, lmatch_intermediate=4.0, lmatch_limited=3.0,
        )
        # L_match=3.5 is now in the limited zone (>3.0 but ≤4.0)
        # moderate=1 → w_tot=1 ≥ 1 → Otherwise_related
        f = _make_features(L_match=3.5, moderate=1)
        result = classify(f, strict)
        _assert_different_case(result)

    def test_custom_weights(self):
        """Custom weights change what w_tot value is produced."""
        # With w_moderate=2, w_strong=5, w_tot_compelling_zone=6
        # strong=1, moderate=0 → w_tot = 5 < 6 → Duplicates
        custom = DecisionThresholds(
            w_moderate=2.0, w_strong=5.0, w_tot_compelling_zone=6.0,
        )
        f = _make_features(L_match=4.0, strong=1)
        result = classify(f, custom)
        _assert_same_case(result)

    def test_custom_weights_boundary(self):
        """Custom weights at boundary produce Otherwise_related."""
        # With w_moderate=2, w_strong=5, w_tot_compelling_zone=6
        # strong=1, moderate=1 → w_tot = 5+2 = 7 ≥ 6 → Otherwise_related
        custom = DecisionThresholds(
            w_moderate=2.0, w_strong=5.0, w_tot_compelling_zone=6.0,
        )
        f = _make_features(L_match=4.0, strong=1, moderate=1)
        result = classify(f, custom)
        _assert_different_case(result)


# ===================================================================
# Explanation strings
# ===================================================================

class TestExplanations:
    """Verify that explanations contain useful information."""

    def test_duplicates_has_lmatch_value(self):
        f = _make_features(L_match=4.0)
        result = classify(f, THRESHOLDS)
        assert "4.00" in result.explanation

    def test_duplicates_has_w_tot(self):
        """Duplicates result explanation includes w_tot value."""
        f = _make_features(L_match=4.0, moderate=1)
        result = classify(f, THRESHOLDS)
        assert "w_tot=1.0" in result.explanation

    def test_otherwise_related_has_w_tot_and_counts(self):
        f = _make_features(L_match=4.0, strong=2, moderate=1)
        result = classify(f, THRESHOLDS)
        assert "w_tot=7.0" in result.explanation
        assert "strong=2" in result.explanation
        assert "moderate=1" in result.explanation

    def test_below_limited_mentions_threshold(self):
        f = _make_features(L_match=0.3)
        result = classify(f, THRESHOLDS)
        assert "1.0" in result.explanation  # mentions the weak threshold


# ===================================================================
# Default thresholds (no thresholds argument)
# ===================================================================

class TestDefaultThresholds:
    """Verify classify works when thresholds argument is omitted."""

    def test_uses_defaults_duplicates(self):
        f = _make_features(L_match=4.0)
        result = classify(f)
        _assert_same_case(result)

    def test_uses_defaults_independent(self):
        f = _make_features(L_match=0.5)
        result = classify(f)
        _assert_independent(result)


# ===================================================================
# independence_reason propagation (rarity-zone paths)
# ===================================================================

class TestIndependenceReasonFromRarity:
    """Each existing L_match zone branch should set the matching reason."""

    def test_compelling_zone_sets_rarity_compelling(self):
        result = classify(_make_features(L_match=4.0), THRESHOLDS)
        assert result.independence_reason == IndependenceReason.RARITY_COMPELLING

    def test_intermediate_zone_sets_rarity_intermediate(self):
        result = classify(_make_features(L_match=2.5), THRESHOLDS)
        assert result.independence_reason == IndependenceReason.RARITY_INTERMEDIATE

    def test_limited_zone_sets_rarity_limited(self):
        result = classify(_make_features(L_match=1.5), THRESHOLDS)
        assert result.independence_reason == IndependenceReason.RARITY_LIMITED

    def test_below_limited_sets_rarity_insufficient(self):
        result = classify(_make_features(L_match=0.5), THRESHOLDS)
        assert result.independence_reason == IndependenceReason.RARITY_INSUFFICIENT


# ===================================================================
# Shared-source verdicts (direct evidence of shared provenance)
# ===================================================================

class TestSharedSourceVerdicts:
    """A reliable shared source identifier rejects independence
    regardless of L_match."""

    def test_same_source_single_patient_forces_duplicates(self):
        """SAME_SOURCE_SINGLE_PATIENT with L=0 and no other
        inconsistencies → Same_case via the Identity zone."""
        f = _make_features(
            L_match=0.0,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_same_case(result)
        assert result.independence_reason == IndependenceReason.SHARED_SOURCE

    def test_same_source_same_case_forces_duplicates(self):
        """SAME_SOURCE_SAME_CASE with L=0 and no other
        inconsistencies → Same_case via the Identity zone."""
        f = _make_features(
            L_match=0.0,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_SAME_CASE,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_same_case(result)
        assert result.independence_reason == IndependenceReason.SHARED_SOURCE

    def test_same_source_same_case_downgrades_when_other_decisive(self):
        """Positive verdict + decisive non-source-case inconsistency
        → Abstain (w_tot crosses compelling-zone threshold; single-
        patient-source inconsistencies abstain rather than
        Otherwise_related)."""
        f = _make_features(
            L_match=0.0,
            decisive=1,  # w_tot = 100, well past compelling-zone threshold 4.0
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_SAME_CASE,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_abstain(result)
        assert result.independence_reason == IndependenceReason.SHARED_SOURCE

    def test_same_source_tolerates_one_moderate(self):
        """A single moderate inconsistency (w_tot=1.0) is well below the
        compelling-zone threshold (4.0) and must NOT demote the pair."""
        f = _make_features(
            L_match=0.0,
            moderate=1,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_same_case(result)
        assert result.independence_reason == IndependenceReason.SHARED_SOURCE

    def test_same_source_tolerates_two_moderates(self):
        """Two moderate inconsistencies (w_tot=2.0) remain below the
        compelling-zone threshold (4.0)."""
        f = _make_features(
            L_match=0.0,
            moderate=2,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_same_case(result)
        assert result.independence_reason == IndependenceReason.SHARED_SOURCE

    def test_same_source_tolerates_one_strong(self):
        """A single strong inconsistency (w_tot=3.0) is still below the
        compelling-zone threshold (4.0) and remains in the Duplicates
        branch for a confirmed same-case verdict. The Identity zone shares
        the compelling-zone inconsistency budget: a single strong is
        tolerated."""
        f = _make_features(
            L_match=0.0,
            strong=1,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_SAME_CASE,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_same_case(result)
        assert result.independence_reason == IndependenceReason.SHARED_SOURCE

    def test_same_source_demotes_on_moderate_plus_strong(self):
        """Moderate + strong (w_tot=4.0) reaches the compelling-zone
        threshold: 4.0 is NOT < 4.0, so the pair is abstained (not
        Otherwise_related) for a single-patient verdict. Inconsistencies
        on a confirmed single-patient / same-case source are more
        plausibly artefacts than evidence of distinct patients."""
        f = _make_features(
            L_match=0.0,
            moderate=1,
            strong=1,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_abstain(result)
        assert result.independence_reason == IndependenceReason.SHARED_SOURCE

    def test_same_source_different_case_forces_otherwise_related(self):
        """SAME_SOURCE_DIFFERENT_CASE → Otherwise_related pinned by the
        shared-source evidence, even with L=0."""
        f = _make_features(
            L_match=0.0,
            # Decisive finding is synthesized by generate_pair_features;
            # the decision logic pins Otherwise_related regardless of
            # whether it's present here.
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)
        assert result.independence_reason == IndependenceReason.SHARED_SOURCE

    # SAME_SOURCE_UNCOMPARABLE_CASE has a two-way rule. The dedicated
    # test class TestUncomparableCaseThreeWayRule below covers branches
    # A (Duplicates), B (Otherwise_related), and C (Otherwise_related
    # default) in depth. The two tests below act as smoke tests for the
    # source-context zone entry.

    def test_same_source_uncomparable_case_no_evidence_otherwise_related(self):
        """UNCOMPARABLE_CASE smoke test: no feature contributions, no
        inconsistencies → falls through branch (A), past branch (B), to
        the branch (C) Otherwise_related default."""
        f = _make_features(
            L_match=0.0,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)
        assert result.independence_reason == IndependenceReason.SHARED_SOURCE

    def test_same_source_uncomparable_case_otherwise_related_on_decisive(self):
        """UNCOMPARABLE_CASE smoke test: decisive inconsistency
        (w_tot=100) lands in branch (B) Otherwise_related, regardless
        of any patient-specific evidence that might exist."""
        f = _make_features(
            L_match=0.0,
            decisive=1,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)
        assert result.independence_reason == IndependenceReason.SHARED_SOURCE

    def test_multi_patient_no_link_falls_through(self):
        """Advisory verdict selects no source-context zone — existing
        L_match logic applies."""
        f = _make_features(
            L_match=0.5,  # below limited threshold
            source_case_assessment=_assessment(
                SourceCaseVerdict.MULTI_PATIENT_CONTEXT_NO_LINK,
                identifier=None,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_independent(result)
        assert result.independence_reason == IndependenceReason.RARITY_INSUFFICIENT

    def test_no_decisive_signal_falls_through(self):
        f = _make_features(
            L_match=4.0,
            source_case_assessment=_assessment(
                SourceCaseVerdict.NO_DECISIVE_SIGNAL,
                identifier=None,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_same_case(result)
        assert result.independence_reason == IndependenceReason.RARITY_COMPELLING

    def test_not_assessed_falls_through(self):
        f = _make_features(
            L_match=0.5,
            source_case_assessment=_assessment(
                SourceCaseVerdict.NOT_ASSESSED,
                identifier=None,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_independent(result)

    def test_none_assessment_preserves_existing_behavior(self):
        f = _make_features(L_match=0.5, source_case_assessment=None)
        result = classify(f, THRESHOLDS)
        _assert_independent(result)
        assert result.independence_reason == IndependenceReason.RARITY_INSUFFICIENT

    def test_explanation_mentions_verdict_and_identifier(self):
        f = _make_features(
            L_match=0.0,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT,
                identifier="10.1234/abc",
            ),
        )
        result = classify(f, THRESHOLDS)
        assert "same_source_single_patient" in result.explanation
        assert "10.1234/abc" in result.explanation


# ===================================================================
# Abstain outcome — Identity zone
# ===================================================================

class TestAbstainOutcome:
    """Abstain is produced by the Identity zone for epistemically
    underdetermined same-source scenarios."""

    def test_single_patient_strong_inconsistency_abstains(self):
        """SAME_SOURCE_SINGLE_PATIENT + w_tot=4.0 (≥ 4.0) → Abstain,
        not Otherwise_related. Inconsistencies on a single-patient source
        are more plausibly extraction/reporting artefacts. One strong +
        one moderate reaches the compelling-zone threshold."""
        f = _make_features(
            L_match=0.0,
            moderate=1,
            strong=1,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_abstain(result)
        assert result.independence_reason == IndependenceReason.SHARED_SOURCE

    def test_same_case_strong_inconsistency_abstains(self):
        """SAME_SOURCE_SAME_CASE + w_tot=4.0 (≥ 4.0) → Abstain."""
        f = _make_features(
            L_match=0.0,
            moderate=1,
            strong=1,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_SAME_CASE,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_abstain(result)

    def test_uncomparable_case_no_inconsistency_otherwise_related(self):
        """SAME_SOURCE_UNCOMPARABLE_CASE + w_tot=0 → Otherwise_related.
        Absence of inconsistency is insufficient patient-specific evidence
        to call Duplicates in a multi-patient publication context, but the
        pair no longer abstains — it defaults to Otherwise_related."""
        f = _make_features(
            L_match=4.0,  # high L_match does not force Duplicates here
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)

    def test_abstain_explanation_contains_w_tot(self):
        """Abstain explanation includes the w_tot value for auditability."""
        f = _make_features(
            L_match=0.0,
            moderate=1,
            strong=1,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT,
                identifier="10.5678/xyz",
            ),
        )
        result = classify(f, THRESHOLDS)
        assert "w_tot=4.0" in result.explanation
        assert "4.0" in result.explanation  # threshold
        assert result.final_label == "Abstain"

    def test_uncomparable_default_explanation_mentions_multi_patient(self):
        """UNCOMPARABLE_CASE branch-C default explanation mentions the
        multi-patient context and resolves to Otherwise_related."""
        f = _make_features(
            L_match=0.0,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
                identifier="10.1234/abc",
            ),
        )
        result = classify(f, THRESHOLDS)
        assert "multi-patient" in result.explanation
        assert result.final_label == "Otherwise_related"

    def test_abstain_is_non_independent(self):
        """Abstain always has independence_assessment = NON_INDEPENDENT
        (same-source non-independence established)."""
        f = _make_features(
            L_match=0.0,
            moderate=1,
            strong=1,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT,
            ),
        )
        result = classify(f, THRESHOLDS)
        assert result.independence_assessment == IndependenceAssessment.NON_INDEPENDENT
        assert result.relationship_assessment == RelationshipAssessment.ABSTAIN


# ===================================================================
# UNCOMPARABLE_CASE two-way rule (Duplicates / Otherwise_related)
# ===================================================================

class TestUncomparableCaseThreeWayRule:
    """SAME_SOURCE_UNCOMPARABLE_CASE applies a two-way rule based on the
    patient-specific log-evidence ``L_patient`` (excluding drug/AE-set
    contributions) and ``w_tot``:

        if w_tot >= w_tot_limited_zone:        → Otherwise_related
        elif L_patient > lpatient_compelling:  → Duplicates
                                                 (w_tot < w_tot_limited_zone implied)
        else:                                  → Otherwise_related (default)

    Defaults: lpatient_compelling=2.0, w_tot_limited_zone=1.0.
    """

    # ---------- Branch A: Duplicates ----------

    def test_branch_a_sex_plus_age_clears_bar(self):
        """sex (p_f=0.5) + age (p_f=1/80) → L_patient ≈ 2.20 > 2.0,
        w_tot=0 < 1.0 → Duplicates."""
        f = _make_features(
            L_match=0.0,
            feature_contributions=_SEX_PLUS_AGE_CONTRIBS,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_same_case(result)
        assert result.independence_reason == IndependenceReason.SHARED_SOURCE

    def test_branch_a_explanation_surfaces_l_patient(self):
        """The (A) explanation must surface L_patient for auditability."""
        f = _make_features(
            L_match=0.0,
            feature_contributions=_SEX_PLUS_AGE_CONTRIBS,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
                identifier="10.1/abc",
            ),
        )
        result = classify(f, THRESHOLDS)
        assert "L_patient=" in result.explanation
        assert "compelling patient-specific" in result.explanation
        assert "10.1/abc" in result.explanation

    # ---------- Branch A negatives ----------

    def test_branch_a_drops_on_one_moderate_inconsistency(self):
        """Same L_patient ≈ 2.20, but moderate=1 → w_tot=1.0 ≥ 1.0
        → branch (B) Otherwise_related. (Under the literature/summary-
        case rationale, a moderate inconsistency is meaningful evidence
        of a different patient, even if patient-specific positives also
        line up.)"""
        f = _make_features(
            L_match=0.0,
            moderate=1,
            feature_contributions=_SEX_PLUS_AGE_CONTRIBS,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)

    def test_branch_a_tolerates_one_minor_inconsistency(self):
        """One minor inconsistency (w_tot=0.4 < 1.0) does not trigger
        branch (B); L_patient still ≈ 2.20 > 2.0 → Duplicates.
        Represents e.g. a 1-year off-by-one age stamp surviving as a
        Duplicates classification when the rest of the evidence is
        strong."""
        f = _make_features(
            L_match=0.0,
            feature_contributions=_SEX_PLUS_AGE_CONTRIBS,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
            ),
        )
        # Inject one minor inconsistency directly.
        from llm_dup_dev.models.pair_features import InconsistencyCounts
        f.inconsistency_counts = InconsistencyCounts(minor=1)
        result = classify(f, THRESHOLDS)
        _assert_same_case(result)

    def test_branch_a_drops_on_sex_only(self):
        """sex only → L_patient ≈ 0.30, NOT > 2.0 → Otherwise_related
        (branch-C default, not Duplicates)."""
        f = _make_features(
            L_match=0.0,
            feature_contributions=_SEX_ONLY_CONTRIBS,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)

    def test_branch_a_drug_ae_contributions_do_not_count(self):
        """drug_set p_f=1e-10 + ae_set p_f=1e-10 (full L_match would be huge)
        → L_patient = 0 (those features are excluded) → Otherwise_related
        (branch-C default, not Duplicates). Proves the multi-patient
        noise-floor exclusion is effective."""
        f = _make_features(
            L_match=10.0,  # huge L_match — must not influence the decision
            feature_contributions=_DRUG_AE_ONLY_CONTRIBS,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)

    # ---------- Branch B: Otherwise_related ----------

    def test_branch_b_strong_inconsistency_overrides_patient_evidence(self):
        """w_tot=3.0 ≥ 1.0 → Otherwise_related, even if L_patient is
        very high. (B) is checked before (A)."""
        f = _make_features(
            L_match=0.0,
            strong=1,
            feature_contributions=_SEX_PLUS_AGE_CONTRIBS,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)

    def test_branch_b_decisive_inconsistency(self):
        """Decisive inconsistency (w_tot=100) → Otherwise_related."""
        f = _make_features(
            L_match=0.0,
            decisive=1,
            feature_contributions=_SEX_PLUS_AGE_CONTRIBS,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)

    def test_branch_b_three_year_age_diff_triggers_otherwise_related(self):
        """3-year age difference → moderate inconsistency (w_tot=1.0) →
        Otherwise_related. In a literature / summary-case context, a
        3-year reported age difference is meaningful evidence of a
        different patient and should not Abstain."""
        f = _make_features(
            L_match=0.0,
            moderate=1,  # 3-year diff produces one moderate (age_moderate=2.0)
            feature_contributions=_SEX_PLUS_AGE_CONTRIBS,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)

    def test_branch_b_three_minors_accumulate_to_otherwise_related(self):
        """Three minor inconsistencies (w_tot=1.2) ≥ 1.0 → Otherwise_related.
        Even small individual discrepancies, when they accumulate, indicate
        a different patient in a curated-publication context."""
        f = _make_features(
            L_match=0.0,
            feature_contributions=_SEX_PLUS_AGE_CONTRIBS,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
            ),
        )
        from llm_dup_dev.models.pair_features import InconsistencyCounts
        f.inconsistency_counts = InconsistencyCounts(minor=3)
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)

    # ---------- Branch C: Otherwise_related (default) ----------

    def test_branch_c_no_evidence_no_inconsistency(self):
        """Empty feature_contributions and no inconsistencies → branch (C)
        Otherwise_related default."""
        f = _make_features(
            L_match=0.0,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)

    def test_branch_c_explanation_surfaces_both_quantities(self):
        """The (C) explanation must surface L_patient and w_tot."""
        f = _make_features(
            L_match=0.0,
            feature_contributions=_SEX_ONLY_CONTRIBS,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
                identifier="10.1/abc",
            ),
        )
        result = classify(f, THRESHOLDS)
        assert "L_patient=" in result.explanation
        assert "w_tot=" in result.explanation
        assert "insufficient" in result.explanation

    # ---------- Branch A boundary ----------

    def test_branch_a_boundary_exactly_at_threshold(self):
        """L_patient exactly == lpatient_compelling → NOT Duplicates
        (falls through to the branch-C Otherwise_related default).

        The gate is a strict ``>``, matching every L_match zone boundary.
        patient_age p_f=0.01 → -log10 = exactly 2.0 == the default bar.
        """
        f = _make_features(
            L_match=0.0,
            feature_contributions=[_fc("patient_age", 0.01)],
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
            ),
        )
        assert THRESHOLDS.lpatient_compelling == 2.0  # guards the fixture
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)


class TestLPatientThresholdIsIndependent:
    """``L_patient`` is judged against ``lpatient_compelling``, not an L_match zone.

    The two are different quantities on different scales — L_patient covers
    only patient-specific features and carries no multiplicity adjustment —
    so retuning an L_match zone must not shift the patient-identity bar.
    They share a default of 2.0 by calibration coincidence, not by
    relationship, which is exactly why this needs a test: at the defaults
    the coupling is invisible.
    """

    @staticmethod
    def _uncomparable_sex_plus_age():
        """L_patient ≈ 2.204, w_tot = 0 — just clears the default bar."""
        return _make_features(
            L_match=0.0,
            feature_contributions=_SEX_PLUS_AGE_CONTRIBS,
            source_case_assessment=_assessment(
                SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
            ),
        )

    def test_lmatch_intermediate_does_not_move_the_l_patient_bar(self):
        """Moving the L_match intermediate zone must not change the outcome.

        Before the split this flipped away from Duplicates, because the
        branch borrowed lmatch_intermediate.
        """
        f = self._uncomparable_sex_plus_age()
        for lmatch_intermediate in (0.5, 2.0, 8.0):
            result = classify(
                f, DecisionThresholds(lmatch_intermediate=lmatch_intermediate)
            )
            # Duplicates at every setting: the bar is lpatient_compelling.
            _assert_same_case(result)

    def test_lpatient_compelling_does_move_the_bar(self):
        """The dedicated threshold is the one that governs the branch."""
        f = self._uncomparable_sex_plus_age()
        # 2.204 > 2.0 → Duplicates; 2.204 < 8.0 → Otherwise_related (branch C).
        _assert_same_case(classify(f, DecisionThresholds(lpatient_compelling=2.0)))
        _assert_different_case(
            classify(f, DecisionThresholds(lpatient_compelling=8.0))
        )

    def test_explanation_reports_the_l_patient_threshold(self):
        """The explanation must quote the bar actually applied."""
        f = self._uncomparable_sex_plus_age()
        result = classify(
            f, DecisionThresholds(lpatient_compelling=1.0, lmatch_intermediate=9.0)
        )
        # L_patient=2.20 > 1.0 → Duplicates, quoting 1.0 and never 9.0.
        _assert_same_case(result)
        assert "L_patient=2.20 > 1.0" in result.explanation
        assert "9.0" not in result.explanation

    def test_no_link_branch_uses_the_l_patient_bar_too(self):
        """MULTI_PATIENT_CONTEXT_NO_LINK routes through the same gate.

        Its own L_match shim still uses lmatch_limited; only branch A's
        patient-evidence bar is decoupled.
        """
        f = _make_features(
            L_match=2.5,  # above lmatch_limited → shim fires
            feature_contributions=_SEX_PLUS_AGE_CONTRIBS,
            source_case_assessment=_assessment(
                SourceCaseVerdict.MULTI_PATIENT_CONTEXT_NO_LINK,
            ),
        )
        _assert_same_case(classify(f, DecisionThresholds(lpatient_compelling=2.0)))
        _assert_different_case(
            classify(f, DecisionThresholds(lpatient_compelling=8.0))
        )


# ===================================================================
# MULTI_PATIENT_CONTEXT_NO_LINK — L_match-gated two-way dispatch
# ===================================================================

class TestMultiPatientContextNoLink:
    """``MULTI_PATIENT_CONTEXT_NO_LINK`` reaches the Unresolved zone —
    the same two-way rule as ``SAME_SOURCE_UNCOMPARABLE_CASE`` — *only*
    when independence is rejected, which for this verdict requires
    ``L_match > lmatch_limited`` (it carries no shared-source evidence,
    so the independence reason is the rarity zone, not shared_source).
    Below that threshold the verdict is advisory and the pair stays
    Independent. The two verdicts share the drug-set / AE-set inflation
    concern but only UNCOMPARABLE_CASE carries positive shared-source
    evidence."""

    # ---------- Below / at the weak threshold: fall through ----------

    def test_at_weak_boundary_falls_through(self):
        """L_match exactly equal to lmatch_limited → standard zone logic
        (Independent at the boundary). Confirms the strict ``>`` in the
        shim, matching the rest of the zone boundaries."""
        f = _make_features(
            L_match=THRESHOLDS.lmatch_limited,
            source_case_assessment=_assessment(
                SourceCaseVerdict.MULTI_PATIENT_CONTEXT_NO_LINK,
                identifier=None,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_independent(result)
        assert result.independence_reason == IndependenceReason.RARITY_INSUFFICIENT

    # ---------- Above the weak threshold: two-way rule ----------

    def test_branch_a_above_weak_duplicates(self):
        """L_match=2.5 (above weak), w_tot=0, L_patient ≈ 2.20 > 2.0 →
        Duplicates via Branch A. Standard zone logic would also yield
        Duplicates here, so this confirms the Unresolved zone produces
        the same outcome when patient-specific evidence is strong."""
        f = _make_features(
            L_match=2.5,
            feature_contributions=_SEX_PLUS_AGE_CONTRIBS,
            source_case_assessment=_assessment(
                SourceCaseVerdict.MULTI_PATIENT_CONTEXT_NO_LINK,
                identifier=None,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_same_case(result)
        assert result.independence_reason == IndependenceReason.RARITY_INTERMEDIATE

    def test_branch_b_one_moderate_otherwise_related(self):
        """The motivating case: L_match=3.3 (compelling zone) with one
        moderate inconsistency. Standard zone logic would have absorbed
        the moderate and returned Duplicates (w_tot=1.0 < 4.0). The
        multi-patient two-way rule tightens to w_tot_limited_zone, so
        w_tot=1.0 ≥ 1.0 → Otherwise_related."""
        f = _make_features(
            L_match=3.3,
            moderate=1,
            feature_contributions=_SEX_PLUS_AGE_CONTRIBS,
            source_case_assessment=_assessment(
                SourceCaseVerdict.MULTI_PATIENT_CONTEXT_NO_LINK,
                identifier=None,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)
        assert result.independence_reason == IndependenceReason.RARITY_COMPELLING

    def test_branch_c_above_weak_insufficient_l_patient_otherwise_related(self):
        """L_match=2.5 with only sex contribution (L_patient ≈ 0.30 ≤
        2.0) and no inconsistencies → Branch C Otherwise_related default.
        Standard zone logic would have returned Duplicates; the Unresolved zone
        withholds the Duplicates call but no longer abstains."""
        f = _make_features(
            L_match=2.5,
            feature_contributions=_SEX_ONLY_CONTRIBS,
            source_case_assessment=_assessment(
                SourceCaseVerdict.MULTI_PATIENT_CONTEXT_NO_LINK,
                identifier=None,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)
        assert result.independence_reason == IndependenceReason.RARITY_INTERMEDIATE

    def test_branch_c_drug_ae_contributions_do_not_count(self):
        """Huge drug/AE concordance must not move a no-shared-source
        multi-patient pair into Duplicates. L_match=10.0 with only
        drug_set / ae_set contributions → L_patient = 0 → Otherwise_related
        (branch-C default, same exclusion as UNCOMPARABLE_CASE)."""
        f = _make_features(
            L_match=10.0,
            feature_contributions=_DRUG_AE_ONLY_CONTRIBS,
            source_case_assessment=_assessment(
                SourceCaseVerdict.MULTI_PATIENT_CONTEXT_NO_LINK,
                identifier=None,
            ),
        )
        result = classify(f, THRESHOLDS)
        _assert_different_case(result)

    # ---------- Explanation surface ----------

    def test_explanation_contains_verdict_and_no_identifier_suffix(self):
        """Explanation must surface the verdict string and must not
        include a shared-identifier suffix (this verdict carries
        none)."""
        f = _make_features(
            L_match=3.3,
            moderate=1,
            source_case_assessment=_assessment(
                SourceCaseVerdict.MULTI_PATIENT_CONTEXT_NO_LINK,
                identifier=None,
            ),
        )
        result = classify(f, THRESHOLDS)
        assert "multi_patient_context_no_link" in result.explanation
        # No identifier suffix should appear (the formatter returns ""
        # when no identifier is present).
        assert ", doi=" not in result.explanation
