"""Tests for anchor population selection."""

from llm_dup_dev.models.enums import AnchorType
from llm_dup_dev.feature_generation.anchor import select_anchor


class TestAnchorSelection:
    def test_drugs_below_threshold(self):
        result = select_anchor(
            N_drugs=30, N_AEs=200, N_drugs_AEs=10, N_total=100_000, threshold=50
        )
        assert result.type == AnchorType.DRUGS
        assert result.N_anchor == 30

    def test_aes_below_threshold(self):
        result = select_anchor(
            N_drugs=200, N_AEs=30, N_drugs_AEs=10, N_total=100_000, threshold=50
        )
        assert result.type == AnchorType.AES
        assert result.N_anchor == 30

    def test_both_below_threshold_selects_smaller(self):
        result = select_anchor(
            N_drugs=20, N_AEs=40, N_drugs_AEs=5, N_total=100_000, threshold=50
        )
        assert result.type == AnchorType.DRUGS
        assert result.N_anchor == 20

    def test_both_above_threshold_uses_intersection(self):
        result = select_anchor(
            N_drugs=200, N_AEs=300, N_drugs_AEs=50, N_total=100_000, threshold=50
        )
        assert result.type == AnchorType.DRUGS_AES
        assert result.N_anchor == 50

    def test_equal_to_threshold(self):
        """At exactly the threshold, should still use the smaller."""
        result = select_anchor(
            N_drugs=50, N_AEs=100, N_drugs_AEs=20, N_total=100_000, threshold=50
        )
        assert result.type == AnchorType.DRUGS
        assert result.N_anchor == 50

    def test_equal_values_below_threshold(self):
        """When N_drugs == N_AEs and both ≤ T, pick drugs (≤ comparison)."""
        result = select_anchor(
            N_drugs=30, N_AEs=30, N_drugs_AEs=10, N_total=100_000, threshold=50
        )
        assert result.type == AnchorType.DRUGS
        assert result.N_anchor == 30

    def test_preserves_all_counts(self):
        result = select_anchor(
            N_drugs=120, N_AEs=48, N_drugs_AEs=12, N_total=100_000, threshold=50
        )
        assert result.N_drugs == 120
        assert result.N_AEs == 48
        assert result.N_drugs_AEs == 12

    def test_very_small_anchor(self):
        """Extremely small populations should work correctly."""
        result = select_anchor(
            N_drugs=1, N_AEs=500, N_drugs_AEs=1, N_total=100_000, threshold=50
        )
        assert result.type == AnchorType.DRUGS
        assert result.N_anchor == 1

    def test_zero_aes_uses_drugs(self):
        """When N_AEs=0 (no AE overlap), anchor should be drugs, not zero."""
        result = select_anchor(
            N_drugs=25, N_AEs=0, N_drugs_AEs=0, N_total=100_000, threshold=50
        )
        assert result.type == AnchorType.DRUGS
        assert result.N_anchor == 25

    def test_zero_drugs_uses_aes(self):
        """When N_drugs=0 (no drug overlap), anchor should be AEs."""
        result = select_anchor(
            N_drugs=0, N_AEs=40, N_drugs_AEs=0, N_total=100_000, threshold=50
        )
        assert result.type == AnchorType.AES
        assert result.N_anchor == 40

    def test_both_zero_uses_whole_context(self):
        """No overlap at all → neither set narrows → anchor is N_total.

        Zero would make M = C(N_anchor, 2) vanish, so P_match = 0
        vacuously and L_match saturates — reporting "nothing in common"
        as maximal duplicate evidence. See
        TestDegenerateAnchorEvidence in tests/test_probability.py.
        """
        result = select_anchor(
            N_drugs=0, N_AEs=0, N_drugs_AEs=0, N_total=100_000, threshold=50
        )
        assert result.N_anchor == 100_000

    def test_both_zero_anchor_is_never_below_two(self):
        """The unnarrowed anchor always leaves pairs to compare."""
        result = select_anchor(
            N_drugs=0, N_AEs=0, N_drugs_AEs=0, N_total=2, threshold=50
        )
        assert result.N_anchor == 2

    def test_zero_counts_do_not_shrink_the_anchor(self):
        """An empty matching set must never make the anchor smaller.

        A count of zero means "the matching set is empty", never "the
        population is empty" — the counts include the focal pair, so a
        non-empty matching set forces its count to at least 2.
        """
        for n_drugs, n_aes in ((0, 0), (0, 40), (25, 0)):
            result = select_anchor(
                N_drugs=n_drugs, N_AEs=n_aes, N_drugs_AEs=0,
                N_total=100_000, threshold=50,
            )
            assert result.N_anchor >= 2, (n_drugs, n_aes)
