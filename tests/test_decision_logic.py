"""Tests for classifier.decision_logic.

Uses the weighted inconsistency score
``w_tot = w_minor * minor + w_moderate * moderate + w_strong * strong``
(defaults: w_minor=0.4, w_moderate=1.0, w_strong=3.0) with per-zone
thresholds:

    strong zone (L > 3.0): w_tot < 4 → Non-independent / Duplicates
                            else      → Non-independent / Otherwise_related
    medium zone (L > 2.0): w_tot < 3 → Non-independent / Duplicates
                            else      → Non-independent / Otherwise_related
    weak zone   (L > 1.0): w_tot < 1 → Non-independent / Duplicates
                            else      → Independent
    below weak:            always Independent
"""

from llm_dup_dev.classifier.config import DecisionThresholds
from llm_dup_dev.classifier.decision_logic import classify
from llm_dup_dev.models.enums import (
    AnchorType,
    DuplicateRelationship,
    IndependenceAssessment,
    NarrativeConsistency,
    ReportingContextLevel,
)
from llm_dup_dev.models.pair_features import (
    AnchorPopulation,
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
) -> PairFeatures:
    """Build a minimal PairFeatures for decision logic testing."""
    return PairFeatures(
        pair_id=pair_id,
        reporting_context=ReportingContext(level=ReportingContextLevel.GLOBAL),
        anchor=AnchorPopulation(
            N_drugs=100, N_AEs=100, N_drugs_AEs=50,
            type=AnchorType.DRUGS_AES, N_anchor=50,
        ),
        probability=ProbabilityEstimate(p_joint=1e-5, expected_collisions=0.01),
        L_match=L_match,
        inconsistency_counts=InconsistencyCounts(
            moderate=moderate, strong=strong, decisive=decisive,
        ),
        narrative_consistency=NarrativeConsistency.NEUTRAL,
    )


def _assert_duplicates(result):
    assert result.independence_assessment == IndependenceAssessment.NON_INDEPENDENT
    assert result.duplicate_relationship == DuplicateRelationship.DUPLICATES
    assert result.final_label == "Duplicates"


def _assert_otherwise_related(result):
    assert result.independence_assessment == IndependenceAssessment.NON_INDEPENDENT
    assert result.duplicate_relationship == DuplicateRelationship.OTHERWISE_RELATED
    assert result.final_label == "Otherwise_related"


def _assert_independent(result):
    assert result.independence_assessment == IndependenceAssessment.INDEPENDENT
    assert result.duplicate_relationship is None
    assert result.final_label == "Unrelated"


# Default thresholds: strong=3.0, med=2.0, weak=1.0, w_m=1, w_s=3
THRESHOLDS = DecisionThresholds()


# ===================================================================
# Strong zone: L_match > 3.0  —  w_tot < 4 → Duplicates
# w_tot = 1*moderate + 3*strong
# ===================================================================

class TestStrongZone:
    """L_match > lmatch_strong (default 3.0)."""

    def test_no_inconsistencies_duplicates(self):
        """w_tot=0 < 4 → Duplicates."""
        f = _make_features(L_match=4.0)
        result = classify(f, THRESHOLDS)
        _assert_duplicates(result)

    def test_one_strong_zero_moderate_duplicates(self):
        """w_tot = 3*1 = 3 < 4 → Duplicates."""
        f = _make_features(L_match=4.0, strong=1)
        result = classify(f, THRESHOLDS)
        _assert_duplicates(result)

    def test_three_moderate_duplicates(self):
        """w_tot = 1*3 = 3 < 4 → Duplicates."""
        f = _make_features(L_match=4.0, moderate=3)
        result = classify(f, THRESHOLDS)
        _assert_duplicates(result)

    def test_one_strong_one_moderate_boundary_otherwise_related(self):
        """w_tot = 3 + 1 = 4, NOT < 4 → Otherwise_related."""
        f = _make_features(L_match=4.0, strong=1, moderate=1)
        result = classify(f, THRESHOLDS)
        _assert_otherwise_related(result)

    def test_two_strong_otherwise_related(self):
        """w_tot = 3*2 = 6 ≥ 4 → Otherwise_related."""
        f = _make_features(L_match=4.0, strong=2)
        result = classify(f, THRESHOLDS)
        _assert_otherwise_related(result)

    def test_four_moderate_boundary_otherwise_related(self):
        """w_tot = 1*4 = 4, NOT < 4 → Otherwise_related."""
        f = _make_features(L_match=4.0, moderate=4)
        result = classify(f, THRESHOLDS)
        _assert_otherwise_related(result)

    def test_boundary_lmatch_exactly_strong(self):
        """L_match == lmatch_strong (3.0) → NOT in strong zone, falls to medium."""
        f = _make_features(L_match=3.0)
        result = classify(f, THRESHOLDS)
        # 3.0 is NOT > 3.0, so falls to medium zone
        _assert_duplicates(result)

    def test_just_above_strong(self):
        """L_match barely above strong threshold."""
        f = _make_features(L_match=3.01)
        result = classify(f, THRESHOLDS)
        _assert_duplicates(result)


# ===================================================================
# Medium zone: lmatch_med < L_match ≤ lmatch_strong — w_tot < 3 → Duplicates
# ===================================================================

class TestMediumZone:
    """lmatch_med (2.0) < L_match ≤ lmatch_strong (3.0)."""

    def test_no_inconsistencies_duplicates(self):
        """w_tot=0 < 3 → Duplicates."""
        f = _make_features(L_match=2.5)
        result = classify(f, THRESHOLDS)
        _assert_duplicates(result)

    def test_two_moderate_duplicates(self):
        """w_tot = 1*2 = 2 < 3 → Duplicates."""
        f = _make_features(L_match=2.5, moderate=2)
        result = classify(f, THRESHOLDS)
        _assert_duplicates(result)

    def test_one_strong_boundary_otherwise_related(self):
        """w_tot = 3*1 = 3, NOT < 3 → Otherwise_related."""
        f = _make_features(L_match=2.5, strong=1)
        result = classify(f, THRESHOLDS)
        _assert_otherwise_related(result)

    def test_three_moderate_boundary_otherwise_related(self):
        """w_tot = 1*3 = 3, NOT < 3 → Otherwise_related."""
        f = _make_features(L_match=2.5, moderate=3)
        result = classify(f, THRESHOLDS)
        _assert_otherwise_related(result)

    def test_boundary_lmatch_exactly_med(self):
        """L_match == lmatch_med (2.0) → NOT in medium zone, falls to weak."""
        f = _make_features(L_match=2.0)
        result = classify(f, THRESHOLDS)
        # 2.0 is NOT > 2.0, falls to weak zone
        # No inconsistencies → Duplicates
        _assert_duplicates(result)


# ===================================================================
# Weak zone: lmatch_weak < L_match ≤ lmatch_med — w_tot < 1 → Duplicates,
# else Independent (rejection of independence is too marginal to support
# any inconsistency).
# ===================================================================

class TestWeakZone:
    """lmatch_weak (1.0) < L_match ≤ lmatch_med (2.0)."""

    def test_no_inconsistencies_duplicates(self):
        """w_tot=0 < 1 → Duplicates."""
        f = _make_features(L_match=1.5)
        result = classify(f, THRESHOLDS)
        _assert_duplicates(result)

    def test_one_moderate_boundary_independent(self):
        """w_tot = 1, NOT < 1 → Independent (Unrelated)."""
        f = _make_features(L_match=1.5, moderate=1)
        result = classify(f, THRESHOLDS)
        _assert_independent(result)

    def test_one_strong_independent(self):
        """w_tot = 3 ≥ 1 → Independent (Unrelated)."""
        f = _make_features(L_match=1.5, strong=1)
        result = classify(f, THRESHOLDS)
        _assert_independent(result)

    def test_boundary_lmatch_exactly_weak(self):
        """L_match == lmatch_weak (1.0) → NOT in weak zone, falls to below."""
        f = _make_features(L_match=1.0)
        result = classify(f, THRESHOLDS)
        _assert_independent(result)


# ===================================================================
# Below weak: L_match ≤ lmatch_weak
# ===================================================================

class TestBelowWeak:
    """L_match ≤ lmatch_weak (1.0)."""

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
    """Decisive inconsistencies override the zone-based logic."""

    def test_decisive_with_high_lmatch_otherwise_related(self):
        """Decisive + L_match > lmatch_med → Non-independent / Otherwise_related."""
        f = _make_features(L_match=5.0, decisive=1)
        result = classify(f, THRESHOLDS)
        _assert_otherwise_related(result)

    def test_decisive_with_medium_lmatch_otherwise_related(self):
        """Decisive + L_match just above lmatch_med → Otherwise_related."""
        f = _make_features(L_match=2.5, decisive=1)
        result = classify(f, THRESHOLDS)
        _assert_otherwise_related(result)

    def test_decisive_with_low_lmatch_independent(self):
        """Decisive + L_match ≤ lmatch_med → Independent."""
        f = _make_features(L_match=1.5, decisive=1)
        result = classify(f, THRESHOLDS)
        _assert_independent(result)

    def test_decisive_boundary_at_med(self):
        """Decisive + L_match == lmatch_med (2.0) → NOT > med → Independent."""
        f = _make_features(L_match=2.0, decisive=1)
        result = classify(f, THRESHOLDS)
        _assert_independent(result)

    def test_decisive_overrides_clean_strong_zone(self):
        """Even with no other inconsistencies and very high L_match,
        decisive forces Otherwise_related (not Duplicates)."""
        f = _make_features(L_match=10.0, decisive=1, strong=0, moderate=0)
        result = classify(f, THRESHOLDS)
        _assert_otherwise_related(result)

    def test_multiple_decisive(self):
        """Multiple decisive inconsistencies still handled correctly."""
        f = _make_features(L_match=4.0, decisive=3)
        result = classify(f, THRESHOLDS)
        _assert_otherwise_related(result)
        assert "n=3" in result.explanation


# ===================================================================
# Custom thresholds
# ===================================================================

class TestCustomThresholds:
    """Verify that non-default thresholds work correctly."""

    def test_relaxed_lmatch(self):
        """With lower L_match thresholds, same L_match enters a higher zone."""
        relaxed = DecisionThresholds(
            lmatch_strong=2.0, lmatch_med=1.0, lmatch_weak=0.5,
        )
        # L_match=2.5 is now in the strong zone (>2.0)
        # strong=1 → w_tot=3 < 4 → Duplicates
        f = _make_features(L_match=2.5, strong=1)
        result = classify(f, relaxed)
        _assert_duplicates(result)

    def test_strict_lmatch(self):
        """With higher L_match thresholds, same L_match falls to lower zone."""
        strict = DecisionThresholds(
            lmatch_strong=5.0, lmatch_med=4.0, lmatch_weak=3.0,
        )
        # L_match=3.5 is now in the weak zone (>3.0 but ≤4.0)
        # moderate=1 → w_tot=1 ≥ 1 → Independent
        f = _make_features(L_match=3.5, moderate=1)
        result = classify(f, strict)
        _assert_independent(result)

    def test_custom_weights(self):
        """Custom weights change what w_tot value is produced."""
        # With w_moderate=2, w_strong=5, w_tot_strong_zone=6
        # strong=1, moderate=0 → w_tot = 5 < 6 → Duplicates
        custom = DecisionThresholds(
            w_moderate=2.0, w_strong=5.0, w_tot_strong_zone=6.0,
        )
        f = _make_features(L_match=4.0, strong=1)
        result = classify(f, custom)
        _assert_duplicates(result)

    def test_custom_weights_boundary(self):
        """Custom weights at boundary produce Otherwise_related."""
        # With w_moderate=2, w_strong=5, w_tot_strong_zone=6
        # strong=1, moderate=1 → w_tot = 5+2 = 7 ≥ 6 → Otherwise_related
        custom = DecisionThresholds(
            w_moderate=2.0, w_strong=5.0, w_tot_strong_zone=6.0,
        )
        f = _make_features(L_match=4.0, strong=1, moderate=1)
        result = classify(f, custom)
        _assert_otherwise_related(result)


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

    def test_below_weak_mentions_threshold(self):
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
        _assert_duplicates(result)

    def test_uses_defaults_independent(self):
        f = _make_features(L_match=0.5)
        result = classify(f)
        _assert_independent(result)
