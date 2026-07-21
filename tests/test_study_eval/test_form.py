"""Tests for the SCLE form: applicability and assessment validators."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from llm_dup_dev.study_eval.scle.form import (
    CaseDifficulty,
    InputDataLimitationCategory,
    PrimaryErrorSource,
    ReferenceLabelIssue,
    SCLEAutoContext,
    SCLECaseAssessment,
    YesNoUnclear,
    applicable_dimensions,
    check_assessment_against_context,
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
    assert "method_difference_explanation" in dims


def _correct_assessment(pair_id="A__B"):
    return SCLECaseAssessment(
        pair_id=pair_id,
        reviewer_id="r",
        free_text_assessment="decisive evidence",
        case_difficulty=CaseDifficulty.TRIVIAL,
        reference_label_issue=ReferenceLabelIssue.NO_APPARENT_ISSUE,
    )


def test_correct_case_passes_context_check():
    a = _correct_assessment()
    ctx = _ctx(misclassified=False, disagree=False)
    assert check_assessment_against_context(a, ctx) == []


def test_missing_misclassification_dimension_flagged():
    a = _correct_assessment()  # no misclassification block
    ctx = _ctx(misclassified=True, disagree=False)
    problems = check_assessment_against_context(a, ctx)
    assert any("never_event" in p for p in problems)


def test_inapplicable_dimension_filled_flagged():
    a = SCLECaseAssessment(
        pair_id="A__B",
        reviewer_id="r",
        free_text_assessment="x",
        case_difficulty=CaseDifficulty.TRIVIAL,
        reference_label_issue=ReferenceLabelIssue.NO_APPARENT_ISSUE,
        never_event=YesNoUnclear.NO,
        unexpected_method_behaviour=YesNoUnclear.NO,
        material_input_data_limitation=YesNoUnclear.NO,
        primary_error_source=PrimaryErrorSource.UNCLEAR,
    )
    ctx = _ctx(misclassified=False, disagree=False)
    problems = check_assessment_against_context(a, ctx)
    assert any("not applicable" in p for p in problems)


def test_misclassification_block_must_be_all_or_nothing():
    with pytest.raises(ValidationError):
        SCLECaseAssessment(
            pair_id="A__B",
            reviewer_id="r",
            free_text_assessment="x",
            case_difficulty=CaseDifficulty.TRIVIAL,
            reference_label_issue=ReferenceLabelIssue.NO_APPARENT_ISSUE,
            never_event=YesNoUnclear.NO,  # only one of the four set
        )


def test_limitation_category_requires_material_yes():
    with pytest.raises(ValidationError):
        SCLECaseAssessment(
            pair_id="A__B",
            reviewer_id="r",
            free_text_assessment="x",
            case_difficulty=CaseDifficulty.TRIVIAL,
            reference_label_issue=ReferenceLabelIssue.NO_APPARENT_ISSUE,
            never_event=YesNoUnclear.NO,
            unexpected_method_behaviour=YesNoUnclear.NO,
            material_input_data_limitation=YesNoUnclear.NO,  # not "yes"
            input_data_limitation_category=InputDataLimitationCategory.OTHER,
            primary_error_source=PrimaryErrorSource.UNCLEAR,
        )


def test_unknown_feature_domain_rejected():
    with pytest.raises(ValidationError):
        SCLECaseAssessment(
            pair_id="A__B",
            reviewer_id="r",
            free_text_assessment="x",
            case_difficulty=CaseDifficulty.TRIVIAL,
            reference_label_issue=ReferenceLabelIssue.NO_APPARENT_ISSUE,
            implicated_feature_domains=["not_a_domain"],
        )
