"""Tests for inter-reviewer agreement statistics (percent agreement + kappa)."""

from __future__ import annotations

import pytest

from llm_dup_dev.study_eval.scle.agreement import _cohens_kappa, compute_agreement
from llm_dup_dev.study_eval.scle.form import (
    CaseDifficulty,
    ReferenceLabelIssue,
    ReviewerSubmission,
    SCLECaseAssessment,
)


def _assessment(pair_id, reviewer, difficulty):
    return SCLECaseAssessment(
        pair_id=pair_id,
        reviewer_id=reviewer,
        free_text_assessment="t",
        case_difficulty=difficulty,
        reference_label_issue=ReferenceLabelIssue.NO_APPARENT_ISSUE,
    )


def _submission(reviewer, difficulties):
    return ReviewerSubmission(
        workbook_id="w",
        reviewer_id=reviewer,
        evaluation_component="precision",
        sample_checksum="x",
        assessments=[
            _assessment(f"A{i}__B{i}", reviewer, d) for i, d in enumerate(difficulties)
        ],
    )


def test_cohens_kappa_perfect_but_single_category_is_none():
    # Both raters use only one category -> kappa undefined.
    assert _cohens_kappa([("a", "a"), ("a", "a")]) is None


def test_cohens_kappa_two_categories():
    paired = [("x", "x"), ("x", "x"), ("y", "y"), ("y", "x")]
    k = _cohens_kappa(paired)
    assert k is not None
    assert -1.0 <= k <= 1.0


def test_cohens_kappa_empty_is_none():
    assert _cohens_kappa([]) is None


def test_agreement_percent_and_counts():
    T, N = CaseDifficulty.TRIVIAL, CaseDifficulty.NON_TRIVIAL
    sub_a = _submission("alice", [T, T, N, N])
    sub_b = _submission("bob", [T, N, N, N])  # differs on pair 1
    report = compute_agreement(sub_a, sub_b)

    diff = {d.field: d for d in report.dimensions}
    # case_difficulty: 3/4 agree.
    assert diff["case_difficulty"].n_compared == 4
    assert diff["case_difficulty"].n_agree == 3
    assert diff["case_difficulty"].percent_agreement == pytest.approx(0.75)
    # reference_label_issue: all identical -> 4/4, kappa undefined (single cat).
    assert diff["reference_label_issue"].n_agree == 4
    assert diff["reference_label_issue"].cohens_kappa is None


def test_overall_agreement_across_applicable_dimensions():
    T, N = CaseDifficulty.TRIVIAL, CaseDifficulty.NON_TRIVIAL
    sub_a = _submission("alice", [T, T])
    sub_b = _submission("bob", [T, N])
    report = compute_agreement(sub_a, sub_b)
    # 2 dimensions x 2 pairs = 4 comparisons; 3 agree (one case_difficulty diff).
    assert report.overall_percent_agreement == pytest.approx(3 / 4)


def test_agreement_rejects_mismatched_pairs():
    sub_a = _submission("alice", [CaseDifficulty.TRIVIAL])
    sub_b = ReviewerSubmission(
        workbook_id="w",
        reviewer_id="bob",
        evaluation_component="precision",
        sample_checksum="x",
        assessments=[_assessment("Z0__Y0", "bob", CaseDifficulty.TRIVIAL)],
    )
    with pytest.raises(ValueError, match="different pairs"):
        compute_agreement(sub_a, sub_b)
