"""Tests for llm_dup_dev.models.pair_identity.

The invariant under test: a pair has one identity regardless of the order its
two report ids arrive in. Everything downstream joins on ``pair_id`` alone —
``PairFeatures`` and ``ClassificationResult`` carry no report ids — so an
orientation-sensitive id silently scores zero pairs while still reporting a
number.
"""

import pytest

from llm_dup_dev.models.pair_identity import (
    PAIR_ID_SEPARATOR,
    canonical_pair_id,
    canonical_report_ids,
    report_id_sort_key,
    split_pair_id,
    validate_pair_identity,
    validate_report_id,
)


# ===================================================================
# Orientation independence — the point of the module
# ===================================================================

class TestOrientationIndependence:
    def test_reversed_inputs_give_the_same_pair_id(self):
        assert canonical_pair_id("R001", "R002") == canonical_pair_id("R002", "R001")

    def test_reversed_inputs_give_the_same_ordered_ids(self):
        assert canonical_report_ids("R002", "R001") == ("R001", "R002")
        assert canonical_report_ids("R001", "R002") == ("R001", "R002")

    def test_pair_id_is_lower_then_higher(self):
        assert canonical_pair_id("R002", "R001") == "R001__R002"

    def test_canonicalisation_is_idempotent(self):
        first = canonical_report_ids("R002", "R001")
        assert canonical_report_ids(*first) == first
        assert canonical_pair_id(*first) == canonical_pair_id("R002", "R001")


# ===================================================================
# Ordering convention
# ===================================================================

class TestOrdering:
    def test_numeric_ids_order_numerically_not_lexicographically(self):
        """The case plain string sorting gets wrong: "10" < "9" as strings."""
        assert canonical_pair_id("9", "10") == "9__10"
        assert canonical_pair_id("10", "9") == "9__10"

    @pytest.mark.parametrize(
        "a,b,expected",
        [
            ("2", "10", ("2", "10")),
            ("10", "2", ("2", "10")),
            ("100", "99", ("99", "100")),
            ("8047905", "7215592", ("7215592", "8047905")),
        ],
    )
    def test_numeric_ordering_cases(self, a, b, expected):
        assert canonical_report_ids(a, b) == expected

    def test_non_numeric_ids_order_lexicographically(self):
        assert canonical_report_ids("R002", "R001") == ("R001", "R002")
        assert canonical_report_ids("B", "A") == ("A", "B")

    def test_numeric_sorts_before_non_numeric(self):
        """Mixed pairs are arbitrary-but-total, not semantic."""
        assert canonical_report_ids("R10", "9") == ("9", "R10")
        assert canonical_pair_id("R10", "9") == "9__R10"

    def test_leading_zero_variants_stay_a_total_order(self):
        """"007" and "7" are the same integer but different reports."""
        assert canonical_report_ids("7", "007") == ("007", "7")
        assert canonical_report_ids("007", "7") == ("007", "7")
        # A total order means the answer does not depend on input order.
        assert canonical_pair_id("7", "007") == canonical_pair_id("007", "7")

    def test_sort_key_is_stable_for_equal_ids(self):
        assert report_id_sort_key("42") == report_id_sort_key("42")

    def test_unicode_digits_are_not_treated_as_numeric(self):
        """int() would accept these; a report id never should."""
        key = report_id_sort_key("٩")
        assert key[0] == 1


# ===================================================================
# Rejections
# ===================================================================

class TestSelfPairs:
    def test_self_pair_rejected(self):
        with pytest.raises(ValueError, match="two distinct reports"):
            canonical_report_ids("R001", "R001")

    def test_self_pair_rejected_by_pair_id(self):
        with pytest.raises(ValueError, match="two distinct reports"):
            canonical_pair_id("R001", "R001")

    def test_self_pair_rejected_on_the_read_path(self):
        with pytest.raises(ValueError, match="two distinct reports"):
            validate_pair_identity("R001__R001", "R001", "R001")


class TestReportIdValidation:
    @pytest.mark.parametrize("bad", ["", "   ", "\t"])
    def test_blank_ids_rejected(self, bad):
        with pytest.raises(ValueError, match="non-blank string"):
            validate_report_id(bad)

    def test_separator_inside_a_report_id_rejected(self):
        """It would make the pair_id impossible to split unambiguously."""
        with pytest.raises(ValueError, match="pair_id separator"):
            validate_report_id("R__1")

    def test_separator_rejected_when_building_a_pair(self):
        with pytest.raises(ValueError, match="pair_id separator"):
            canonical_pair_id("R__1", "R002")

    def test_blank_rejected_when_building_a_pair(self):
        with pytest.raises(ValueError, match="non-blank string"):
            canonical_pair_id("", "R002")

    def test_error_names_the_offending_field(self):
        with pytest.raises(ValueError, match="report_id_b"):
            canonical_report_ids("R001", "")


# ===================================================================
# split_pair_id — the inverse
# ===================================================================

class TestSplitPairId:
    def test_round_trips_canonical_pair_id(self):
        assert split_pair_id(canonical_pair_id("R002", "R001")) == ("R001", "R002")
        assert split_pair_id(canonical_pair_id("10", "9")) == ("9", "10")

    def test_rejects_an_id_without_a_separator(self):
        with pytest.raises(ValueError, match="does not split"):
            split_pair_id("R001")

    def test_rejects_an_id_with_too_many_separators(self):
        with pytest.raises(ValueError, match="does not split"):
            split_pair_id("A__B__C")

    def test_rejects_a_blank_half(self):
        with pytest.raises(ValueError, match="does not split"):
            split_pair_id("A__")


# ===================================================================
# validate_pair_identity — one case per failure mode
# ===================================================================

class TestValidatePairIdentity:
    def test_accepts_a_canonical_triple(self):
        validate_pair_identity("R001__R002", "R001", "R002")

    def test_accepts_a_canonical_numeric_triple(self):
        validate_pair_identity("9__10", "9", "10")

    def test_rejects_noncanonical_report_order(self):
        with pytest.raises(ValueError, match="not in canonical order"):
            validate_pair_identity("R002__R001", "R002", "R001")

    def test_rejects_noncanonical_numeric_order(self):
        """Lexicographic ordering would have accepted "10" before "9"."""
        with pytest.raises(ValueError, match="not in canonical order"):
            validate_pair_identity("10__9", "10", "9")

    def test_rejects_a_pair_id_inconsistent_with_its_report_ids(self):
        with pytest.raises(ValueError, match="inconsistent with its report ids"):
            validate_pair_identity("something_else", "R001", "R002")

    def test_rejects_a_blank_report_id(self):
        with pytest.raises(ValueError, match="non-blank string"):
            validate_pair_identity("R001__", "R001", "")

    def test_error_says_to_regenerate_not_repair_in_place(self):
        """A silent rewrite here would break the join it is meant to protect."""
        with pytest.raises(ValueError, match="Regenerate the artifact"):
            validate_pair_identity("R002__R001", "R002", "R001")

    def test_context_is_included_in_the_message(self):
        with pytest.raises(ValueError, match="pairs.json"):
            validate_pair_identity(
                "R002__R001", "R002", "R001", context="pairs.json"
            )


def test_separator_constant_is_the_documented_one():
    assert PAIR_ID_SEPARATOR == "__"
