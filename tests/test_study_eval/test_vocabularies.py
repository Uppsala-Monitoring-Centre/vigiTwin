"""Tests for the study's frozen SCLE vocabularies: config and workbook sheets."""

from __future__ import annotations

import openpyxl
import pytest

from llm_dup_dev.study_eval.scle.vocabularies import (
    ERROR_STAGES_SHEET,
    FEATURE_DOMAINS_SHEET,
    normalise_vocabulary,
    read_vocabulary_sheet,
)
from llm_dup_dev.study_eval.scle.workbook import (
    workbook_vocabularies,
    write_reviewer_workbook,
)

from tests.test_study_eval.conftest import build_scle_precision_sample

# Deliberately unsorted to prove order is preserved, not normalised.
_FROZEN_DOMAINS = ("drugs", "demographics", "narrative", "dates")
_FROZEN_STAGES = ("relationship_assessment", "evidence_extraction")

_SPEC = [("duplicate", "Duplicates"), ("duplicate", "Otherwise_related")]


# --- Configuration validation ----------------------------------------------


def test_normalise_preserves_order_and_trims():
    assert normalise_vocabulary(["  drugs ", "demographics"]) == (
        "drugs",
        "demographics",
    )


def test_normalise_is_idempotent():
    once = normalise_vocabulary(_FROZEN_DOMAINS)
    assert normalise_vocabulary(once) == once


def test_normalise_rejects_empty_list():
    with pytest.raises(ValueError, match="non-empty"):
        normalise_vocabulary([])


def test_normalise_rejects_duplicates_after_trimming():
    with pytest.raises(ValueError, match="duplicates"):
        normalise_vocabulary(["drugs", " drugs "])


def test_normalise_rejects_blank_entry():
    with pytest.raises(ValueError, match="blank"):
        normalise_vocabulary(["drugs", "   "])


def test_normalise_rejects_comma_in_entry():
    # Feature domains are compared as one comma-joined set-valued dimension.
    with pytest.raises(ValueError, match="comma"):
        normalise_vocabulary(["drugs", "a,b"])


def test_normalise_rejects_non_string_entry():
    with pytest.raises(ValueError, match="not a string"):
        normalise_vocabulary(["drugs", 3])  # type: ignore[list-item]


def test_normalise_label_names_the_list_in_errors():
    with pytest.raises(ValueError, match="error_stages"):
        normalise_vocabulary([], label="error_stages")


# --- Workbook embedding -----------------------------------------------------


def test_workbook_embeds_both_lists_in_order(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    write_reviewer_workbook(
        sample,
        "alice",
        path,
        error_stages=_FROZEN_STAGES,
        feature_domains=_FROZEN_DOMAINS,
    )

    wb = openpyxl.load_workbook(path)
    assert read_vocabulary_sheet(wb[ERROR_STAGES_SHEET]) == _FROZEN_STAGES
    assert read_vocabulary_sheet(wb[FEATURE_DOMAINS_SHEET]) == _FROZEN_DOMAINS


def test_workbook_vocabularies_roundtrip(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    write_reviewer_workbook(
        sample,
        "alice",
        path,
        error_stages=_FROZEN_STAGES,
        feature_domains=_FROZEN_DOMAINS,
    )
    assert workbook_vocabularies(path) == (_FROZEN_STAGES, _FROZEN_DOMAINS)


def test_workbook_vocabularies_empty_when_not_configured(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    write_reviewer_workbook(sample, "alice", path)
    assert workbook_vocabularies(path) == ((), ())


def test_instructions_point_at_the_embedded_lists(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    write_reviewer_workbook(
        sample,
        "alice",
        path,
        error_stages=_FROZEN_STAGES,
        feature_domains=_FROZEN_DOMAINS,
    )
    instr = openpyxl.load_workbook(path)["instructions"]
    text = "\n".join(
        str(instr.cell(row=r, column=1).value or "")
        for r in range(1, instr.max_row + 1)
    )
    assert "tick-only" in text
    assert ERROR_STAGES_SHEET in text
    assert FEATURE_DOMAINS_SHEET in text


def test_bad_vocabulary_fails_before_the_file_is_written(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    with pytest.raises(ValueError, match="duplicates"):
        write_reviewer_workbook(
            sample, "alice", path, error_stages=["a", "a"]
        )
    assert not path.exists()
