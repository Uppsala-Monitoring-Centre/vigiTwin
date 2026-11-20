"""Tests for the evidence view models' colour semantics.

These pin the three rules the whole format rests on: red beats green on one
element, rows group green -> neutral -> red, and a missing value is never
evidence.
"""

from __future__ import annotations

from vigitwin.review_html.evidence.models import (
    Attribution,
    AttributionKind,
    EvidenceCell,
    EvidenceRole,
    EvidenceSource,
    ValueKind,
    missing_cell,
    stronger_role,
)


class TestRolePrecedence:
    """Red beats green; green beats neutral; ties keep what is already there."""

    def test_inconsistent_beats_matching(self):
        assert (
            stronger_role(EvidenceRole.MATCHING, EvidenceRole.INCONSISTENT)
            is EvidenceRole.INCONSISTENT
        )

    def test_matching_does_not_displace_inconsistent(self):
        assert (
            stronger_role(EvidenceRole.INCONSISTENT, EvidenceRole.MATCHING)
            is EvidenceRole.INCONSISTENT
        )

    def test_anchor_and_matching_do_not_displace_each_other(self):
        """Both are green, so whichever was attached first stays."""
        assert (
            stronger_role(EvidenceRole.ANCHOR, EvidenceRole.MATCHING)
            is EvidenceRole.ANCHOR
        )
        assert (
            stronger_role(EvidenceRole.MATCHING, EvidenceRole.ANCHOR)
            is EvidenceRole.MATCHING
        )

    def test_anything_beats_neutral(self):
        for role in (
            EvidenceRole.ANCHOR,
            EvidenceRole.MATCHING,
            EvidenceRole.INCONSISTENT,
        ):
            assert stronger_role(EvidenceRole.NEUTRAL, role) is role


class TestCssClasses:
    """Both green roles share one class; the distinction is semantic only."""

    def test_green_roles_share_a_class(self):
        assert EvidenceRole.ANCHOR.css_class == "value-match"
        assert EvidenceRole.MATCHING.css_class == "value-match"

    def test_red_and_neutral(self):
        assert EvidenceRole.INCONSISTENT.css_class == "value-mismatch"
        assert EvidenceRole.NEUTRAL.css_class == "value-neutral"

    def test_is_positive(self):
        assert EvidenceRole.ANCHOR.is_positive
        assert EvidenceRole.MATCHING.is_positive
        assert not EvidenceRole.INCONSISTENT.is_positive
        assert not EvidenceRole.NEUTRAL.is_positive


class TestMissingAndWithheld:
    """A value nobody reported is not evidence, whatever is attached to it."""

    def test_missing_forces_neutral_css(self):
        cell = missing_cell(ValueKind.AGE)
        cell.role = EvidenceRole.MATCHING
        assert cell.css_class == "value-neutral"

    def test_withheld_is_distinct_from_missing(self):
        """A reader must tell 'not reported' from 'not shown here'."""
        cell = EvidenceCell(text="Withheld", kind=ValueKind.COUNTRY, withheld=True)
        assert cell.withheld
        assert not cell.missing

    def test_withheld_keeps_its_role(self):
        """The publication phase hides the value, not the assessment."""
        cell = EvidenceCell(
            text="Withheld",
            kind=ValueKind.DRUG_START_DATE,
            role=EvidenceRole.MATCHING,
            withheld=True,
        )
        assert cell.css_class == "value-match"


class TestAttributionCarriesNoValue:
    """Attribution records why, never what — that is the publication contract."""

    def test_fields_are_assessment_level(self):
        attribution = Attribution(
            kind=AttributionKind.MATCHING_FEATURE,
            source=EvidenceSource.FEATURE_CONTRIBUTION,
            feature="clinical_date",
            contribution=2.2,
            note="matched clinical date",
        )
        dumped = attribution.model_dump()
        assert set(dumped) == {
            "kind",
            "source",
            "feature",
            "severity",
            "contribution",
            "note",
        }

    def test_tooltip_joins_notes_in_order(self):
        cell = EvidenceCell(
            text="2002-01-04",
            kind=ValueKind.DRUG_START_DATE,
            attributions=[
                Attribution(
                    kind=AttributionKind.MATCHING_FEATURE,
                    source=EvidenceSource.FEATURE_CONTRIBUTION,
                    feature="clinical_date",
                    note="first",
                ),
                Attribution(
                    kind=AttributionKind.RETAINED_INCONSISTENCY,
                    source=EvidenceSource.CLINICAL_DATE_FINDING,
                    feature="clinical_date",
                    note="second",
                ),
            ],
        )
        assert cell.title == "first; second"
