"""Tests for the deterministic explicit inter-report link comparator."""

import pytest

from llm_dup_dev.feature_generation.explicit_link_comparison import (
    MIN_IDENTIFIER_LENGTH,
    compare_explicit_links,
    is_matchable_identifier,
    normalize_case_identifier,
)
from llm_dup_dev.models.explicit_link import (
    ExplicitLinkDirection,
    ExplicitLinkVerdict,
)
from llm_dup_dev.models.report import CaseIdentifierKind, CaseIdentifierRef, Report


# A realistic worldwide-unique-style identifier: long enough to clear the
# matchability guard, with the internal punctuation that must survive
# normalization.
ID_A = "US-EXAMPLECO-2020-000123"
ID_B = "GB-MHRA-ADR 98765432"


def _report(report_id, own=(), pointers=()):
    """Build a Report carrying only case-identifier data.

    ``own`` entries are ``(kind, value)`` tuples — a report's own case
    numbers are typed by the field they came from. ``pointers`` are bare
    strings: the C.1.9 field carries no such qualifier.
    """
    return Report(
        report_id=report_id,
        case_identifiers=[
            CaseIdentifierRef(kind=CaseIdentifierKind(kind), value=value)
            for kind, value in own
        ],
        other_case_identifiers=list(pointers),
    )


class TestNormalization:

    def test_casefolds_and_trims(self):
        assert normalize_case_identifier("  US-Abc-123456  ") == "us-abc-123456"

    def test_preserves_internal_punctuation(self):
        # Hyphens and slashes are structural in these identifiers, not noise.
        assert normalize_case_identifier("US-CO/2020-0001") == "us-co/2020-0001"

    def test_collapses_internal_whitespace(self):
        assert normalize_case_identifier("GB MHRA   ADR 987") == "gb mhra adr 987"

    def test_strips_wrapping_punctuation(self):
        assert normalize_case_identifier('"US-CO-123456".') == "us-co-123456"

    def test_nfkc_folds_full_width_characters(self):
        assert normalize_case_identifier("ＵＳ－ＣＯ－１２３４５６") == "us-co-123456"

    def test_is_idempotent(self):
        once = normalize_case_identifier("  US-Abc-123456 ")
        assert normalize_case_identifier(once) == once


class TestMatchabilityGuard:

    def test_short_values_are_not_matchable(self):
        assert not is_matchable_identifier("a" * (MIN_IDENTIFIER_LENGTH - 1))

    def test_values_at_the_bar_are_matchable(self):
        assert is_matchable_identifier("a" * MIN_IDENTIFIER_LENGTH)

    @pytest.mark.parametrize(
        "sentinel", ["unknown", "not known", "not available", "missing", "none"]
    )
    def test_absence_sentinels_are_not_matchable(self, sentinel):
        assert not is_matchable_identifier(sentinel)


class TestNotAssessed:

    def test_no_identifier_data_on_either_side(self):
        result = compare_explicit_links(_report("A"), _report("B"))
        assert result.verdict == ExplicitLinkVerdict.NOT_ASSESSED
        assert result.matches == []

    def test_data_on_one_side_only_is_assessed(self):
        # The loader supplied identifiers for one report, so the comparator
        # did look — "no link" is a finding here, not an absence of data.
        result = compare_explicit_links(
            _report("A", own=[("safety_report_id", ID_A)]),
            _report("B"),
        )
        assert result.verdict == ExplicitLinkVerdict.NO_LINK


class TestDirectLinks:

    def test_r1_points_at_r2(self):
        result = compare_explicit_links(
            _report("A", pointers=[ID_B]),
            _report("B", own=[("safety_report_id", ID_B)]),
        )
        assert result.verdict == ExplicitLinkVerdict.EXPLICIT_LINK
        assert len(result.matches) == 1
        match = result.matches[0]
        assert match.direction == ExplicitLinkDirection.R1_TO_R2
        assert match.pointer_value == ID_B
        assert match.target_value == ID_B

    def test_r2_points_at_r1(self):
        result = compare_explicit_links(
            _report("A", own=[("safety_report_id", ID_A)]),
            _report("B", pointers=[ID_A]),
        )
        assert result.verdict == ExplicitLinkVerdict.EXPLICIT_LINK
        assert result.matches[0].direction == ExplicitLinkDirection.R2_TO_R1

    def test_both_directions_are_recorded(self):
        result = compare_explicit_links(
            _report(
                "A",
                own=[("safety_report_id", ID_A)],
                pointers=[ID_B],
            ),
            _report(
                "B",
                own=[("safety_report_id", ID_B)],
                pointers=[ID_A],
            ),
        )
        assert result.verdict == ExplicitLinkVerdict.EXPLICIT_LINK
        assert {m.direction for m in result.matches} == {
            ExplicitLinkDirection.R1_TO_R2,
            ExplicitLinkDirection.R2_TO_R1,
        }

    def test_link_survives_formatting_differences(self):
        result = compare_explicit_links(
            _report("A", pointers=[f"  {ID_B.lower()} "]),
            _report("B", own=[("safety_report_id", ID_B)]),
        )
        assert result.verdict == ExplicitLinkVerdict.EXPLICIT_LINK

    def test_repeated_pointer_yields_one_match(self):
        result = compare_explicit_links(
            _report(
                "A",
                pointers=[
                    ID_B,
                    ID_B.lower(),
                ],
            ),
            _report("B", own=[("safety_report_id", ID_B)]),
        )
        assert len(result.matches) == 1


class TestTargetFieldAgnostic:
    """A pointer resolves against any of the target's own fields.

    The C.1.9 reference carries no qualifier, and any of the three
    fields can hold a worldwide-unique identifier, so the field the
    target happened to file the value under must not affect matching —
    it is only recorded on the match.
    """

    @pytest.mark.parametrize(
        "target_kind",
        [
            CaseIdentifierKind.SAFETY_REPORT_ID,
            CaseIdentifierKind.COMPANY_NUMBER,
            CaseIdentifierKind.AUTHORITY_NUMBER,
        ],
    )
    def test_matches_whichever_field_the_target_used(self, target_kind):
        result = compare_explicit_links(
            _report("A", pointers=[ID_B]),
            _report("B", own=[(target_kind.value, ID_B)]),
        )
        assert result.verdict == ExplicitLinkVerdict.EXPLICIT_LINK
        assert result.matches[0].target_kind == target_kind

    def test_reasoning_names_the_target_field(self):
        result = compare_explicit_links(
            _report("A", pointers=[ID_B]),
            _report("B", own=[("company_number", ID_B)]),
        )
        assert f"company_number={ID_B}" in result.reasoning


class TestNonLinks:

    def test_shared_pointer_at_a_third_case_does_not_link(self):
        # Both reports naming a common master/study record says nothing
        # about whether they describe the same patient.
        third = "ZZ-COMMON-20200099"
        result = compare_explicit_links(
            _report("A", own=[("safety_report_id", ID_A)], pointers=[third]),
            _report("B", own=[("safety_report_id", ID_B)], pointers=[third]),
        )
        assert result.verdict == ExplicitLinkVerdict.NO_LINK

    def test_self_pointer_is_dropped(self):
        # A follow-up naming its own earlier transmission must not link to
        # a second report that happens to carry the same identifier.
        result = compare_explicit_links(
            _report(
                "A",
                own=[("safety_report_id", ID_A)],
                pointers=[ID_A],
            ),
            _report("B", own=[("safety_report_id", ID_A)]),
        )
        assert result.verdict == ExplicitLinkVerdict.NO_LINK

    def test_short_shared_value_does_not_link(self):
        short = "1" * (MIN_IDENTIFIER_LENGTH - 1)
        result = compare_explicit_links(
            _report("A", pointers=[short]),
            _report("B", own=[("company_number", short)]),
        )
        assert result.verdict == ExplicitLinkVerdict.NO_LINK

    def test_shared_sentinel_does_not_link(self):
        result = compare_explicit_links(
            _report("A", pointers=["UNKNOWN"]),
            _report("B", own=[("company_number", "unknown")]),
        )
        assert result.verdict == ExplicitLinkVerdict.NO_LINK

    def test_pointer_against_pointer_does_not_link(self):
        # Pointers only ever resolve against the *other* report's own
        # identifiers, never against its pointer list.
        result = compare_explicit_links(
            _report("A", pointers=[ID_A]),
            _report("B", pointers=[ID_A]),
        )
        assert result.verdict == ExplicitLinkVerdict.NO_LINK

    def test_different_identifiers_do_not_link(self):
        result = compare_explicit_links(
            _report("A", own=[("safety_report_id", ID_A)], pointers=[ID_A]),
            _report("B", own=[("safety_report_id", ID_B)]),
        )
        assert result.verdict == ExplicitLinkVerdict.NO_LINK


class TestDeterminism:

    def test_match_order_is_independent_of_input_order(self):
        forward = compare_explicit_links(
            _report(
                "A",
                own=[("safety_report_id", ID_A)],
                pointers=[ID_B, "XX-OTHER-4242"],
            ),
            _report(
                "B",
                own=[("safety_report_id", ID_B), ("company_number", "XX-OTHER-4242")],
                pointers=[ID_A],
            ),
        )
        reversed_inputs = compare_explicit_links(
            _report(
                "A",
                own=[("safety_report_id", ID_A)],
                pointers=["XX-OTHER-4242", ID_B],
            ),
            _report(
                "B",
                own=[("company_number", "XX-OTHER-4242"), ("safety_report_id", ID_B)],
                pointers=[ID_A],
            ),
        )
        assert [m.normalized_value for m in forward.matches] == [
            m.normalized_value for m in reversed_inputs.matches
        ]
