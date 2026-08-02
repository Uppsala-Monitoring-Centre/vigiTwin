"""Tests for inter-reviewer agreement statistics (percent agreement + kappa)."""

from __future__ import annotations

import pytest

from llm_dup_dev.study_eval.scle.agreement import _cohens_kappa, compute_agreement
from llm_dup_dev.study_eval.scle.form import (
    CaseDifficulty,
    ReferenceLabelIssue,
    ReviewerSubmission,
    SCLECaseAssessment,
    YesNo,
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


def _misclassified(pair_id, reviewer, *, stages, domains=()):
    return SCLECaseAssessment(
        pair_id=pair_id,
        reviewer_id=reviewer,
        free_text_assessment="t",
        case_difficulty=CaseDifficulty.NON_TRIVIAL,
        reference_label_issue=ReferenceLabelIssue.NO_APPARENT_ISSUE,
        never_event=YesNo.NO,
        unexpected_method_behaviour=YesNo.NO,
        incorrect_or_imprecise_input_data=YesNo.NO,
        error_source_description="root cause",
        error_stages=list(stages),
        implicated_feature_domains=list(domains),
    )


def _stage_submission(reviewer, per_pair_stages, *, stage_vocab, domain_vocab=()):
    return ReviewerSubmission(
        workbook_id="w",
        reviewer_id=reviewer,
        evaluation_component="precision",
        sample_checksum="x",
        error_stages=list(stage_vocab),
        feature_domains=list(domain_vocab),
        assessments=[
            _misclassified(f"A{i}__B{i}", reviewer, stages=s)
            for i, s in enumerate(per_pair_stages)
        ],
    )


_STAGES = ("evidence_extraction", "independence_assessment")


def test_each_configured_stage_gets_its_own_statistic():
    sub_a = _stage_submission(
        "alice", [("evidence_extraction",), ()], stage_vocab=_STAGES
    )
    sub_b = _stage_submission(
        "bob", [("evidence_extraction",), ("independence_assessment",)],
        stage_vocab=_STAGES,
    )
    report = compute_agreement(sub_a, sub_b)
    by_field = {d.field for d in report.dimensions}
    assert "error_stage::evidence_extraction" in by_field
    assert "error_stage::independence_assessment" in by_field

    diff = {d.field: d for d in report.dimensions}
    # Both ticked evidence extraction on pair 0 and neither on pair 1.
    assert diff["error_stage::evidence_extraction"].n_agree == 2
    # They differ on independence assessment for pair 1 only.
    assert diff["error_stage::independence_assessment"].n_compared == 2
    assert diff["error_stage::independence_assessment"].n_agree == 1


def test_both_ticking_nothing_counts_as_agreement_not_a_skip():
    """A tick-only dimension both reviewers left blank is a real agreement."""
    sub_a = _stage_submission("alice", [()], stage_vocab=_STAGES)
    sub_b = _stage_submission("bob", [()], stage_vocab=_STAGES)
    diff = {d.field: d for d in compute_agreement(sub_a, sub_b).dimensions}
    assert diff["implicated_feature_domains"].n_compared == 1
    assert diff["implicated_feature_domains"].n_agree == 1
    assert diff["error_stage::evidence_extraction"].n_compared == 1


def test_agreement_rejects_mismatched_vocabularies():
    sub_a = _stage_submission("alice", [()], stage_vocab=_STAGES)
    sub_b = _stage_submission("bob", [()], stage_vocab=("evidence_extraction",))
    with pytest.raises(ValueError, match="error-stage vocabularies"):
        compute_agreement(sub_a, sub_b)


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
