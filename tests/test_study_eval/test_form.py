"""Tests for the SCLE form: applicability and assessment validators."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from llm_dup_dev.study_eval.scle.form import (
    CaseDifficulty,
    ReferenceLabelIssue,
    SCLEAutoContext,
    SCLECaseAssessment,
    YesNo,
    applicable_dimensions,
    check_assessment_against_context,
    error_stage_dimension,
    error_stage_name,
    structured_dimensions,
)


def _ctx(pair_id="A__B", *, misclassified, disagree):
    return SCLEAutoContext(
        pair_id=pair_id,
        evaluation_component="precision",
        reference_label="duplicate",
        adjudicator_final_label="Duplicates",
        methods_disagree=disagree,
        is_misclassified=misclassified,
    )


def test_applicable_dimensions_by_flags():
    correct = _ctx(misclassified=False, disagree=False)
    assert applicable_dimensions(correct) == {
        "free_text_assessment",
        "case_difficulty",
        "reference_label_issue",
    }
    mis = _ctx(misclassified=True, disagree=True)
    dims = applicable_dimensions(mis)
    assert "never_event" in dims
    assert "error_source_description" in dims
    assert "error_stages" in dims
    assert "method_difference_explanation" in dims


def _correct_assessment(pair_id="A__B"):
    return SCLECaseAssessment(
        pair_id=pair_id,
        reviewer_id="r",
        free_text_assessment="decisive evidence",
        case_difficulty=CaseDifficulty.TRIVIAL,
        reference_label_issue=ReferenceLabelIssue.NO_APPARENT_ISSUE,
    )


def _misclassified_assessment(pair_id="A__B", **overrides):
    payload = {
        "pair_id": pair_id,
        "reviewer_id": "r",
        "free_text_assessment": "x",
        "case_difficulty": CaseDifficulty.NON_TRIVIAL,
        "reference_label_issue": ReferenceLabelIssue.NO_APPARENT_ISSUE,
        "never_event": YesNo.NO,
        "unexpected_method_behaviour": YesNo.NO,
        "incorrect_or_imprecise_input_data": YesNo.NO,
        "error_source_description": "interaction of extraction and relationship",
    }
    payload.update(overrides)
    return SCLECaseAssessment(**payload)


def test_correct_case_passes_context_check():
    a = _correct_assessment()
    ctx = _ctx(misclassified=False, disagree=False)
    assert check_assessment_against_context(a, ctx) == []


def test_clear_is_a_valid_case_difficulty():
    a = _correct_assessment()
    assert a.case_difficulty is CaseDifficulty.TRIVIAL
    clear = SCLECaseAssessment.model_validate(
        {**a.model_dump(), "case_difficulty": "clear"}
    )
    assert clear.case_difficulty is CaseDifficulty.CLEAR


def test_case_difficulty_offers_four_ordered_categories():
    # Member order is the workbook's dropdown order, so it is part of the form.
    assert [m.value for m in CaseDifficulty] == [
        "trivial",
        "clear",
        "non_trivial",
        "unclear",
    ]


def test_yes_no_dimensions_reject_unclear():
    with pytest.raises(ValidationError):
        _misclassified_assessment(never_event="unclear")


def test_missing_free_text_source_of_error_flagged():
    a = _correct_assessment()  # no misclassification block
    ctx = _ctx(misclassified=True, disagree=False)
    problems = check_assessment_against_context(a, ctx)
    assert any("error_source_description" in p for p in problems)


def test_absent_yes_no_dimensions_are_not_flagged():
    # A blank yes/no cell means "no", so an absent one is not a gap. Read-back
    # resolves them to an explicit "no"; the context check must not object
    # either way.
    a = _correct_assessment()
    ctx = _ctx(misclassified=True, disagree=False)
    problems = check_assessment_against_context(a, ctx)
    assert not any("never_event" in p for p in problems)
    assert not any("unexpected_method_behaviour" in p for p in problems)
    assert not any("incorrect_or_imprecise_input_data" in p for p in problems)


def test_inapplicable_dimension_filled_flagged():
    a = _misclassified_assessment()
    ctx = _ctx(misclassified=False, disagree=False)
    problems = check_assessment_against_context(a, ctx)
    assert any("not applicable" in p for p in problems)


def test_tick_only_lists_may_be_empty_on_a_misclassified_case():
    a = _misclassified_assessment()
    ctx = _ctx(misclassified=True, disagree=False)
    assert a.error_stages == []
    assert a.implicated_feature_domains == []
    assert check_assessment_against_context(a, ctx) == []


def test_tick_only_lists_must_be_empty_on_a_correct_case():
    a = _correct_assessment()
    filled = SCLECaseAssessment.model_validate(
        {**a.model_dump(), "error_stages": ["evidence_extraction"]}
    )
    ctx = _ctx(misclassified=False, disagree=False)
    problems = check_assessment_against_context(filled, ctx)
    assert any("error_stages" in p for p in problems)


def test_misclassification_block_must_be_all_or_nothing():
    with pytest.raises(ValidationError):
        SCLECaseAssessment(
            pair_id="A__B",
            reviewer_id="r",
            free_text_assessment="x",
            case_difficulty=CaseDifficulty.TRIVIAL,
            reference_label_issue=ReferenceLabelIssue.NO_APPARENT_ISSUE,
            never_event=YesNo.NO,  # only one of the four set
        )


def test_free_text_source_of_error_is_required_where_it_applies():
    # No longer a model-level group member: the three yes/no dimensions may be
    # blank alongside it, so the requirement is enforced against the context.
    a = _misclassified_assessment(error_source_description=None)
    ctx = _ctx(misclassified=True, disagree=False)
    problems = check_assessment_against_context(a, ctx)
    assert any("error_source_description" in p for p in problems)


def test_yes_no_dimensions_are_filled_as_a_group():
    with pytest.raises(ValidationError):
        _misclassified_assessment(unexpected_method_behaviour=None)


def test_repeated_tick_only_entry_rejected():
    with pytest.raises(ValidationError):
        _misclassified_assessment(
            implicated_feature_domains=["narrative", "narrative"]
        )


def test_misclassification_answered_distinguishes_unasked_from_unticked():
    assert _misclassified_assessment().misclassification_answered
    assert not _correct_assessment().misclassification_answered


def test_structured_dimensions_expand_the_configured_stages():
    dims = structured_dimensions(["evidence_extraction", "independence_assessment"])
    assert dims == (
        "case_difficulty",
        "reference_label_issue",
        "never_event",
        "unexpected_method_behaviour",
        "incorrect_or_imprecise_input_data",
        "error_stage::evidence_extraction",
        "error_stage::independence_assessment",
        "implicated_feature_domains",
    )
    # With no configured stages the per-stage dimensions simply do not exist.
    assert not any("error_stage" in d for d in structured_dimensions([]))


def test_error_stage_dimension_roundtrip():
    dim = error_stage_dimension("relationship_assessment")
    assert error_stage_name(dim) == "relationship_assessment"
    assert error_stage_name("case_difficulty") is None
