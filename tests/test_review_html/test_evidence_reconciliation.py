"""Tests for how the contributions table relates to the stored score.

The two figures a reader adds up — the contributions total ``-log10(p_joint)``
and the anchor adjustment ``-log10(M)`` — come to ``-log10(p_joint x M)``,
while the stored score is ``max(0, -log10(P_match + epsilon))`` with
``P_match = 1 - (1 - p_joint) ** M``. The sum therefore tracks the score only
while ``p_joint x M`` is small, and the score is bounded at both ends.

The page states the sum as the table's total and says nothing about the
bounds: neither moves a classification, and a paragraph of arithmetic on
every case cost more attention than it returned. What is still tested is that
the total is right, that the column adds up to it, and that a difference no
bound explains is recorded as a gap rather than passed over.
"""

from __future__ import annotations

import math

from llm_dup_dev.models.enums import AnchorType
from llm_dup_dev.models.pair_features import (
    AnchorPopulation,
    FeatureContribution,
    ProbabilityEstimate,
)
from llm_dup_dev.review_html.evidence.models import EvidencePageVM
from llm_dup_dev.review_html.evidence.settings import EvidenceRunSettings
from llm_dup_dev.review_html.render import render_evidence_page

from tests.test_review_html.test_evidence_build import _case, _features, _report


def _anchor(n: int) -> AnchorPopulation:
    return AnchorPopulation(
        N_drugs=40,
        N_AEs=50,
        N_drugs_AEs=n,
        type=AnchorType.DRUGS_AES,
        N_anchor=n,
    )


def _with(*, decades: float | None, n_anchor: int, stored: float):
    """Features whose contributions total ``decades`` for a given anchor."""
    contributions = (
        []
        if decades is None
        else [
            FeatureContribution(
                feature="drug_set", p_f=10.0**-decades, method="empirical"
            )
        ]
    )
    return _features(
        anchor=_anchor(n_anchor),
        probability=ProbabilityEstimate(
            p_joint=1e-3, p_match=1e-3, feature_contributions=contributions
        ),
        L_match=stored,
    )


def _reconciliation(features, settings: EvidenceRunSettings | None = None):
    case = _case(_report("R1"), _report("R2"), features, run_settings=settings)
    assert case.reconciliation is not None
    return case.reconciliation, case


def _codes(case) -> list[str]:
    return [g.code.value for g in case.gaps]


def _html(case) -> str:
    return " ".join(render_evidence_page(EvidencePageVM(title="t", cases=[case])).split())


_EPSILON = EvidenceRunSettings(epsilon=1e-15)


class TestTheTotal:
    def test_an_anchor_of_two_adds_up_exactly(self):
        """M = 1, so the adjustment is zero and the total is the score."""
        r, case = _reconciliation(_with(decades=9.0, n_anchor=2, stored=9.0))
        assert r.anchor_adjustment == 0
        assert r.additive_total == 9.0
        assert r.additive_total_text == "+9.0"
        assert "score_unreconciled" not in _codes(case)

    def test_the_adjustment_is_part_of_the_total(self):
        # 11 reports -> M = C(11,2) = 55 -> adjustment -log10(55).
        r, _ = _reconciliation(_with(decades=9.0, n_anchor=11, stored=7.3))
        assert r.anchor_adjustment == -math.log10(55)
        assert r.additive_total == 9.0 - math.log10(55)
        assert r.additive_total_text == "+7.3"

    def test_the_total_is_rendered_under_the_column(self):
        _, case = _reconciliation(_with(decades=9.0, n_anchor=2, stored=9.0))
        html = _html(case)
        assert "<tfoot>" in html
        assert "<td>Total</td><td>+9.0</td>" in html

    def test_the_adjustment_is_its_own_row_when_it_moves_the_total(self):
        """So the column the reader adds up reaches the figure beneath it."""
        _, case = _reconciliation(_with(decades=9.0, n_anchor=11, stored=7.3))
        html = _html(case)
        assert "<td>Anchor population adjustment</td><td>-1.7</td>" in html

    def test_no_adjustment_row_when_the_anchor_is_two(self):
        _, case = _reconciliation(_with(decades=9.0, n_anchor=2, stored=9.0))
        assert "Anchor population adjustment" not in _html(case)


class TestTheProseIsGone:
    """The bounds moved no classification, so the page stopped explaining them."""

    def test_the_ceiling_is_not_described(self):
        _, case = _reconciliation(
            _with(decades=17.2, n_anchor=2, stored=15.0), _EPSILON
        )
        html = _html(case)
        assert "capped at" not in html
        assert "numerical stabiliser" not in html
        assert "<td>Total</td><td>+17.2</td>" in html

    def test_the_floor_is_not_described(self):
        _, case = _reconciliation(
            _with(decades=None, n_anchor=11, stored=0.0), _EPSILON
        )
        html = _html(case)
        assert "lower bound" not in html
        assert "agree to the precision shown" not in html

    def test_the_stored_score_is_not_restated_here(self):
        """It is in the assessment table; the contributions table totals itself."""
        _, case = _reconciliation(
            _with(decades=17.2, n_anchor=2, stored=15.0), _EPSILON
        )
        assert "stored matching score" not in _html(case)


class TestTheBoundsStillDecideTheGap:
    """The flags survive because they separate a bound from a disagreement."""

    def test_the_ceiling_explains_the_difference(self):
        r, case = _reconciliation(
            _with(decades=17.2, n_anchor=2, stored=15.0), _EPSILON
        )
        assert r.ceiling_reached
        assert "score_unreconciled" not in _codes(case)

    def test_without_epsilon_the_cap_cannot_be_assumed(self):
        r, case = _reconciliation(_with(decades=17.2, n_anchor=2, stored=15.0))
        assert not r.ceiling_reached
        assert "score_unreconciled" in _codes(case)

    def test_the_floor_explains_the_difference(self):
        r, case = _reconciliation(_with(decades=None, n_anchor=11, stored=0.0))
        assert r.floor_reached
        assert r.additive_total_text == "-1.7"
        assert "score_unreconciled" not in _codes(case)

    def test_a_negative_total_with_a_real_score_is_not_the_floor(self):
        """Only a score *at* zero is explained by the lower bound."""
        r, case = _reconciliation(_with(decades=None, n_anchor=11, stored=5.0))
        assert not r.floor_reached
        assert "score_unreconciled" in _codes(case)
