"""Tests for anchor population selection."""

from llm_dup_dev.models.enums import AnchorType
from llm_dup_dev.feature_generation.anchor import select_anchor


class TestAnchorSelection:
    def test_drugs_below_threshold(self):
        result = select_anchor(
            N_drugs=30, N_AEs=200, N_drugs_AEs=10, threshold=50
        )
        assert result.type == AnchorType.DRUGS
        assert result.N_anchor == 30

    def test_aes_below_threshold(self):
        result = select_anchor(
            N_drugs=200, N_AEs=30, N_drugs_AEs=10, threshold=50
        )
        assert result.type == AnchorType.AES
        assert result.N_anchor == 30

    def test_both_below_threshold_selects_smaller(self):
        result = select_anchor(
            N_drugs=20, N_AEs=40, N_drugs_AEs=5, threshold=50
        )
        assert result.type == AnchorType.DRUGS
        assert result.N_anchor == 20

    def test_both_above_threshold_uses_intersection(self):
        result = select_anchor(
            N_drugs=200, N_AEs=300, N_drugs_AEs=50, threshold=50
        )
        assert result.type == AnchorType.DRUGS_AES
        assert result.N_anchor == 50

    def test_equal_to_threshold(self):
        """At exactly the threshold, should still use the smaller."""
        result = select_anchor(
            N_drugs=50, N_AEs=100, N_drugs_AEs=20, threshold=50
        )
        assert result.type == AnchorType.DRUGS
        assert result.N_anchor == 50

    def test_equal_values_below_threshold(self):
        """When N_drugs == N_AEs and both ≤ T, pick drugs (≤ comparison)."""
        result = select_anchor(
            N_drugs=30, N_AEs=30, N_drugs_AEs=10, threshold=50
        )
        assert result.type == AnchorType.DRUGS
        assert result.N_anchor == 30

    def test_preserves_all_counts(self):
        result = select_anchor(
            N_drugs=120, N_AEs=48, N_drugs_AEs=12, threshold=50
        )
        assert result.N_drugs == 120
        assert result.N_AEs == 48
        assert result.N_drugs_AEs == 12

    def test_very_small_anchor(self):
        """Extremely small populations should work correctly."""
        result = select_anchor(
            N_drugs=1, N_AEs=500, N_drugs_AEs=1, threshold=50
        )
        assert result.type == AnchorType.DRUGS
        assert result.N_anchor == 1
