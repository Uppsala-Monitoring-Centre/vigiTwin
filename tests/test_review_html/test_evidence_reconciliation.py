"""Tests for how the page reconciles its contributions with the stored score.

The two figures a reader adds up — the contributions total ``-log10(p_joint)``
and the anchor adjustment ``-log10(M)`` — come to ``-log10(p_joint x M)``, while
the stored score is ``max(0, -log10(P_match + epsilon))`` with
``P_match = 1 - (1 - p_joint) ** M``. The sum therefore tracks the score only
while ``p_joint x M`` is small, and the score is bounded at both ends. These
tests pin that the page names whichever bound applies instead of claiming
agreement.
"""

from __future__ import annotations

import math

from llm_dup_dev.models.enums import AnchorType
from llm_dup_dev.models.pair_features import (
    AnchorPopulation,
    FeatureContribution,
    ProbabilityEstimate,
)
from llm_dup_dev.review_html.evidence.settings import EvidenceRunSettings

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
    case = _case(
        _report("R1"), _report("R2"), features, run_settings=settings
    )
    assert case.reconciliation is not None
    return case.reconciliation, case


_EPSILON = EvidenceRunSettings(epsilon=1e-15)


class TestTheNumbersAgree:
    def test_an_anchor_of_two_adds_up_exactly(self):
        """M = 1, so the adjustment is zero and the sum is the score."""
        r, case = _reconciliation(_with(decades=9.0, n_anchor=2, stored=9.0))
        assert r.anchor_adjustment == 0
        assert r.additive_total == 9.0
        assert not r.ceiling_reached
        assert not r.floor_reached
        assert "agree to the precision shown" in r.note
        assert "score_unreconciled" not in [g.code.value for g in case.gaps]


class TestTheUpperBound:
    def test_the_ceiling_is_named_when_epsilon_is_known(self):
        r, _ = _reconciliation(
            _with(decades=17.2, n_anchor=2, stored=15.0), _EPSILON
        )
        assert r.ceiling_reached
        assert not r.floor_reached
        assert "capped at 15.0" in r.note

    def test_without_epsilon_the_cap_is_not_asserted(self):
        r, case = _reconciliation(_with(decades=17.2, n_anchor=2, stored=15.0))
        assert not r.ceiling_reached
        assert "score_unreconciled" in [g.code.value for g in case.gaps]


class TestTheLowerBound:
    """The case the page used to claim agreement on: contributions 0, score 0."""

    @staticmethod
    def _floor_features():
        # 11 reports -> M = C(11,2) = 55, so the adjustment is -log10(55).
        # With no contributions p_joint is 1, P_match saturates at 1 and the
        # score is max(0, -log10(1 + eps)) = 0.
        return _with(decades=None, n_anchor=11, stored=0.0)

    def test_the_floor_is_reported_not_agreement(self):
        r, _ = _reconciliation(self._floor_features(), _EPSILON)
        assert r.floor_reached
        assert not r.ceiling_reached
        assert "agree to the precision shown" not in r.note
        assert "lower bound of 0.0" in r.note

    def test_the_additive_figure_is_stated(self):
        """A reader who adds 0.0 and -1.7 must see that figure named."""
        r, _ = _reconciliation(self._floor_features(), _EPSILON)
        assert r.additive_total == -math.log10(55)
        assert r.additive_total_text == "-1.7"
        assert "come to -1.7" in r.note

    def test_the_floor_needs_no_run_settings(self):
        """Unlike the ceiling, zero is not a function of epsilon."""
        r, case = _reconciliation(self._floor_features())
        assert r.floor_reached
        assert "lower bound of 0.0" in r.note
        assert "score_unreconciled" not in [g.code.value for g in case.gaps]

    def test_the_note_says_what_a_zero_score_means(self):
        r, _ = _reconciliation(self._floor_features(), _EPSILON)
        assert "matching evidence is absent" in r.note

    def test_a_negative_sum_with_a_real_score_is_not_the_floor(self):
        """Only a score *at* zero is explained by the floor."""
        r, case = _reconciliation(_with(decades=None, n_anchor=11, stored=5.0))
        assert not r.floor_reached
        assert "lower bound" not in r.note
        assert "score_unreconciled" in [g.code.value for g in case.gaps]


class TestTheScaleIsShown:
    """Both bounds, so the reader can place the score on a known range."""

    def test_the_range_is_rendered_when_epsilon_is_known(self):
        from llm_dup_dev.review_html.evidence.models import EvidencePageVM
        from llm_dup_dev.review_html.render import render_evidence_page

        _, case = _reconciliation(
            _with(decades=9.0, n_anchor=2, stored=9.0), _EPSILON
        )
        html = render_evidence_page(EvidencePageVM(title="t", cases=[case]))
        assert "scale 0.0–15.0" in " ".join(html.split())
