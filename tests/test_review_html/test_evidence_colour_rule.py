"""Red means "counted against the pair", not "is an inconsistency".

Widening that definition was the price of showing the anchor population
adjustment as what it is: a term that pushed the matching score down. These
tests hold the widened rule to the two things that make it a rule rather than
an exception — it applies to the same figure wherever the figure appears, and
it does not reach anything that told neither way.
"""

from __future__ import annotations

import math

from llm_dup_dev.models.enums import (
    AnchorType,
    InconsistencySeverity,
)
from llm_dup_dev.models.pair_features import (
    AnchorPopulation,
    FeatureContribution,
    Inconsistency,
    ProbabilityEstimate,
)
from llm_dup_dev.review_html.evidence.models import (
    EvidenceRole,
    sign_css_class,
)

from tests.test_review_html.test_evidence_build import _features
from tests.test_review_html.test_evidence_render import _case, _html


def _anchor(n: int) -> AnchorPopulation:
    return AnchorPopulation(
        N_drugs=10,
        N_AEs=10,
        N_drugs_AEs=5,
        type=AnchorType.DRUGS_AES,
        N_anchor=n,
    )


def _scored(n_anchor: int, decades: float, l_match: float):
    """Features whose contributions and anchor give a known adjustment."""
    return _features(
        matching_drugs=["topiramate"],
        matching_AEs=["cleft lip"],
        anchor=_anchor(n_anchor),
        probability=ProbabilityEstimate(
            p_joint=10**-decades,
            p_match=10**-decades,
            feature_contributions=[
                FeatureContribution(
                    feature="clinical_date",
                    p_f=10**-decades,
                    method="domain_size",
                    matched_value="2002-01-04",
                    value_resolution="day",
                )
            ],
        ),
        L_match=l_match,
    )


class TestTheSignRule:
    def test_a_positive_term_is_green(self):
        assert sign_css_class(0.3) == "value-match"

    def test_a_negative_term_is_red(self):
        assert sign_css_class(-9.6) == "value-mismatch"

    def test_a_term_that_moved_nothing_is_neutral(self):
        assert sign_css_class(0.0) == "value-neutral"


class TestTheAnchorAdjustment:
    """The figure that motivated widening red."""

    def test_the_panel_cell_is_red(self):
        case = _case(features=_scored(11, 9.0, 7.3))
        panel = case.summary_panel
        assert panel is not None
        assert panel.anchor_adjustment is not None
        assert panel.anchor_adjustment.role is EvidenceRole.INCONSISTENT
        assert panel.anchor_adjustment.css_class == "value-mismatch"

    def test_the_same_figure_is_red_in_section_two(self):
        """One rule, or it is not a rule: the panel and the table agree."""
        case = _case(features=_scored(11, 9.0, 7.3))
        assert case.reconciliation is not None
        assert case.reconciliation.anchor_adjustment_css_class == "value-mismatch"
        assert case.reconciliation.anchor_adjustment == -math.log10(55)

        html = " ".join(_html(features=_scored(11, 9.0, 7.3)).split())
        # Once in the panel, carrying its tooltip, and once in the section-2
        # column. Both red; neither is the other's copy.
        assert html.count("-1.7") == 2
        assert (
            '<span class="value-mismatch" title="the number of report pairs'
        ) in html
        assert (
            "<td>Anchor population adjustment</td>"
            '<td class="value-mismatch">-1.7</td>'
        ) in html

    def test_it_carries_an_attribution_naming_the_multiplicity(self):
        """Red without a recorded reason would be a colour, not a claim."""
        panel = _case(features=_scored(11, 9.0, 7.3)).summary_panel
        assert panel is not None
        assert panel.anchor_adjustment is not None
        (attribution,) = panel.anchor_adjustment.attributions
        assert attribution.kind.value == "anchor_multiplicity"
        assert attribution.contribution == -math.log10(55)

    def test_a_two_report_anchor_has_no_adjustment_at_all(self):
        """C(2,2) = 1, so the term is exactly zero and the row is not shown."""
        case = _case(features=_scored(2, 9.0, 9.0))
        panel = case.summary_panel
        assert panel is not None
        assert panel.anchor_adjustment is None
        assert "Anchor population adjustment" not in _html(
            features=_scored(2, 9.0, 9.0)
        )


class TestTheContributionsColumn:
    def test_a_contribution_is_green(self):
        case = _case(features=_scored(2, 9.0, 9.0))
        (row,) = case.matching_features
        assert row.contribution_css_class == "value-match"

    def test_a_positive_total_is_green(self):
        case = _case(features=_scored(2, 9.0, 9.0))
        assert case.reconciliation is not None
        assert case.reconciliation.additive_total_css_class == "value-match"

    def test_a_negative_total_is_red(self):
        """The floor case: the evidence no longer makes a chance match unlikely."""
        case = _case(features=_scored(1000, 0.5, 0.0))
        assert case.reconciliation is not None
        assert case.reconciliation.additive_total < 0
        assert case.reconciliation.additive_total_css_class == "value-mismatch"


class TestTheWideningReachesNothingElse:
    def test_a_suppressed_finding_stays_neutral(self):
        """It contributed exactly zero, so it told neither way."""
        features = _features(
            matching_drugs=["topiramate"],
            inconsistencies=[
                Inconsistency(
                    feature="ae_clinical_picture:unmatched_burden:r1_to_r2",
                    severity=InconsistencySeverity.MINOR,
                    terms=["cleft lip"],
                    source="llm_ae_clinical_picture",
                    suppressed=True,
                    suppressed_by="clinical picture",
                )
            ],
        )
        case = _case(features=features)
        (row,) = [r for r in case.adverse_events if r.term.text == "cleft lip"]
        assert row.term.role is EvidenceRole.NEUTRAL

    def test_matching_values_are_still_green(self):
        assert "value-match" in _html()


class TestTheLegendStatesTheWiderRule:
    def test_it_says_for_and_against(self):
        html = _html()
        assert "Green: counted for the pair" in html
        assert "Red: counted against it" in html

    def test_it_still_names_the_inconsistency_case(self):
        """The commonest instance, which a reader should not have to infer."""
        text = " ".join(_html().split())
        assert "a retained inconsistency" in text

    def test_it_names_the_other_instance(self):
        text = " ".join(_html().split())
        assert "a term that lowered the score" in text
