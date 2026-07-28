"""Tests for the frozen feature-domain list: config, workbook, submissions."""

from __future__ import annotations

import openpyxl
import pytest

from llm_dup_dev.study_eval.scle.feature_domains import (
    FEATURE_DOMAINS_SHEET,
    augment_reviewer_workbook,
    normalise_feature_domains,
    validate_submission_feature_domains,
    workbook_feature_domains,
)
from llm_dup_dev.study_eval.scle.form import (
    CaseDifficulty,
    ReferenceLabelIssue,
    ReviewerSubmission,
    SCLECaseAssessment,
)
from llm_dup_dev.study_eval.scle.workbook import write_reviewer_workbook

from tests.test_study_eval.conftest import build_scle_precision_sample

# Deliberately unsorted to prove order is preserved, not normalised.
_FROZEN = ("drugs", "demographics", "narrative", "dates")

_SPEC = [("duplicate", "Duplicates"), ("duplicate", "Otherwise_related")]


# --- Configuration validation ----------------------------------------------


def test_normalise_preserves_order_and_trims():
    assert normalise_feature_domains(["  drugs ", "demographics"]) == (
        "drugs",
        "demographics",
    )


def test_normalise_rejects_empty():
    with pytest.raises(ValueError, match="non-empty"):
        normalise_feature_domains([])


def test_normalise_rejects_duplicates():
    with pytest.raises(ValueError, match="duplicate"):
        normalise_feature_domains(["drugs", " drugs "])


def test_normalise_rejects_blank():
    with pytest.raises(ValueError, match="blank"):
        normalise_feature_domains(["drugs", "   "])


def test_normalise_rejects_comma():
    with pytest.raises(ValueError, match="comma"):
        normalise_feature_domains(["drugs", "a,b"])


def test_normalise_rejects_non_string():
    with pytest.raises(ValueError, match="not a string"):
        normalise_feature_domains(["drugs", 3])  # type: ignore[list-item]


# --- Workbook contents ------------------------------------------------------


def test_augment_embeds_list_and_updates_instructions(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    write_reviewer_workbook(sample, "alice", path)
    augment_reviewer_workbook(path, _FROZEN)

    wb = openpyxl.load_workbook(path)

    # Dedicated sheet exists with the exact values in the supplied order.
    assert FEATURE_DOMAINS_SHEET in wb.sheetnames
    ws = wb[FEATURE_DOMAINS_SHEET]
    values = [ws.cell(row=r, column=1).value for r in range(2, 2 + len(_FROZEN))]
    assert tuple(values) == _FROZEN
    assert ws.freeze_panes == "A2"

    # Instruction 6 is updated and the list is visible on the instructions sheet.
    instr = wb["instructions"]
    col_a = [instr.cell(row=r, column=1).value for r in range(1, instr.max_row + 1)]
    six = next(v for v in col_a if isinstance(v, str) and v.startswith("6."))
    assert "prespecified list below" in six
    assert "Prespecified feature domains:" in col_a
    for domain in _FROZEN:
        assert domain in col_a


def test_workbook_feature_domains_roundtrip(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    write_reviewer_workbook(sample, "alice", path)
    augment_reviewer_workbook(path, _FROZEN)

    assert workbook_feature_domains(path) == _FROZEN


def test_workbook_feature_domains_requires_sheet(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    write_reviewer_workbook(sample, "alice", path)  # not augmented
    with pytest.raises(ValueError, match="no 'Feature domains' sheet"):
        workbook_feature_domains(path)


# --- Submission validation --------------------------------------------------


def _submission(reviewer: str, pair_id: str, domains: list[str]) -> ReviewerSubmission:
    return ReviewerSubmission(
        workbook_id="w",
        reviewer_id=reviewer,
        evaluation_component="precision",
        sample_checksum="x",
        assessments=[
            SCLECaseAssessment(
                pair_id=pair_id,
                reviewer_id=reviewer,
                free_text_assessment="t",
                case_difficulty=CaseDifficulty.NON_TRIVIAL,
                reference_label_issue=ReferenceLabelIssue.NO_APPARENT_ISSUE,
                never_event="no",
                unexpected_method_behaviour="no",
                material_input_data_limitation="no",
                primary_error_source="relationship_assessment",
                implicated_feature_domains=domains,
            )
        ],
    )


def test_validate_accepts_known_domains():
    sub = _submission("alice", "A0__B0", ["drugs", "demographics"])
    validate_submission_feature_domains(sub, _FROZEN)  # no raise


def test_validate_rejects_unknown_domain():
    # ``adverse_events`` is a valid package domain but not in the frozen list.
    sub = _submission("alice", "A0__B0", ["adverse_events"])
    with pytest.raises(ValueError) as exc:
        validate_submission_feature_domains(sub, _FROZEN)
    msg = str(exc.value)
    assert "alice" in msg and "A0__B0" in msg
    assert "adverse_events" in msg


def test_validate_rejects_duplicate_within_assessment():
    sub = _submission("bob", "C0__D0", ["drugs", "drugs"])
    with pytest.raises(ValueError) as exc:
        validate_submission_feature_domains(sub, _FROZEN)
    msg = str(exc.value)
    assert "bob" in msg and "C0__D0" in msg
    assert "duplicate" in msg
