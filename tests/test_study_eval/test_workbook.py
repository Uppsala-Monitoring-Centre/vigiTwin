"""Tests for reviewer-workbook generation and validated read-back."""

from __future__ import annotations

import openpyxl
import pytest

from llm_dup_dev.study_eval.scle.form import YesNoUnclear
from llm_dup_dev.study_eval.scle.workbook import (
    NOT_APPLICABLE,
    read_reviewer_submission,
    write_reviewer_workbook,
)

from tests.test_study_eval.conftest import build_scle_precision_sample, fill_reviewer_workbook

# A spec producing a TP (correct) and an FN (misclassified) case.
_SPEC = [("duplicate", "Duplicates"), ("duplicate", "Otherwise_related")]


def test_write_then_read_roundtrip(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    written, workbook_id = write_reviewer_workbook(sample, "alice", path)
    assert written.exists()

    fill_reviewer_workbook(path, "alice")
    sub = read_reviewer_submission(path, sample)

    assert sub.reviewer_id == "alice"
    assert len(sub.assessments) == len(sample.cases)
    by_pair = {a.pair_id: a for a in sub.assessments}
    fn_pair = next(c.pair_id for c in sample.cases if c.stratum == "FN")
    tp_pair = next(c.pair_id for c in sample.cases if c.stratum == "TP")
    # Misclassified FN case carries the misclassification block.
    assert by_pair[fn_pair].never_event == YesNoUnclear.NO
    # Correctly classified TP case does not.
    assert by_pair[tp_pair].never_event is None


def test_not_evaluated_case_shows_screening_as_not_applicable(tmp_path):
    """A reviewer sees "does not apply", not an ambiguous blank."""
    from llm_dup_dev.study_eval.aggregate import run_recall_analysis
    from llm_dup_dev.study_eval.sampling import sample_scle_recall
    from tests.test_study_eval.conftest import make_recall

    rsets, preds = make_recall(
        {"manual": [(None, "Duplicates"), (None, "Unrelated")]},
        modes={"manual": "not_evaluated"},
    )
    sample = sample_scle_recall(
        run_recall_analysis(rsets, preds), preds, per_stratum=5, seed="s"
    )
    path = tmp_path / "reviewer_a.xlsx"
    write_reviewer_workbook(sample, "alice", path)

    ws = openpyxl.load_workbook(path)["cases"]
    header = [c.value for c in ws[1]]
    for name in ("Screening retrieved", "Methods disagree"):
        column = header.index(name) + 1
        values = {ws.cell(row=r, column=column).value for r in range(2, ws.max_row + 1)}
        assert values == {NOT_APPLICABLE}

    # The disagreement dimension is switched off for such a case.
    column = header.index("Method-difference explanation") + 1
    values = {ws.cell(row=r, column=column).value for r in range(2, ws.max_row + 1)}
    assert values == {NOT_APPLICABLE}


def test_packet_hyperlinks_written(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    targets = {c.pair_id: f"packets/{c.pair_id}.html" for c in sample.cases}
    write_reviewer_workbook(sample, "alice", path, packet_targets=targets)

    wb = openpyxl.load_workbook(path)
    ws = wb["cases"]
    hdr = [c.value for c in ws[1]]
    pcol = hdr.index("Packet") + 1
    pid_col = hdr.index("Pair ID") + 1
    for r in range(2, ws.max_row + 1):
        pid = ws.cell(row=r, column=pid_col).value
        cell = ws.cell(row=r, column=pcol)
        assert cell.hyperlink is not None
        assert cell.hyperlink.target == targets[pid]
        assert cell.value == f"{pid}.html"

    # Read-back is unaffected by the hyperlinks.
    fill_reviewer_workbook(path, "alice")
    sub = read_reviewer_submission(path, sample)
    assert len(sub.assessments) == len(sample.cases)


def test_no_packet_targets_leaves_packet_blank(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    write_reviewer_workbook(sample, "alice", path)  # no packet_targets

    wb = openpyxl.load_workbook(path)
    ws = wb["cases"]
    hdr = [c.value for c in ws[1]]
    pcol = hdr.index("Packet") + 1
    for r in range(2, ws.max_row + 1):
        cell = ws.cell(row=r, column=pcol)
        assert cell.hyperlink is None
        assert cell.value in (None, "")


def test_read_rejects_checksum_mismatch(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    _, _, other = build_scle_precision_sample(
        [("duplicate", "Duplicates"), ("non_duplicate", "Unrelated")], seed="other"
    )
    path = tmp_path / "wb.xlsx"
    write_reviewer_workbook(sample, "alice", path)
    fill_reviewer_workbook(path, "alice")
    with pytest.raises(ValueError, match="does not match"):
        read_reviewer_submission(path, other)


def test_read_rejects_edited_not_applicable_cell(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "wb.xlsx"
    write_reviewer_workbook(sample, "alice", path)
    fill_reviewer_workbook(path, "alice")

    # Tamper: fill a never_event cell on the correctly classified TP case.
    wb = openpyxl.load_workbook(path)
    ws = wb["cases"]
    hdr = [c.value for c in ws[1]]
    col = lambda n: hdr.index(n) + 1  # noqa: E731
    for r in range(2, ws.max_row + 1):
        if ws.cell(row=r, column=col("Never event")).value == NOT_APPLICABLE:
            ws.cell(row=r, column=col("Never event")).value = "yes"
            break
    wb.save(path)

    with pytest.raises(ValueError):
        read_reviewer_submission(path, sample)


def test_read_rejects_out_of_vocabulary_value(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "wb.xlsx"
    write_reviewer_workbook(sample, "alice", path)
    fill_reviewer_workbook(path, "alice")

    wb = openpyxl.load_workbook(path)
    ws = wb["cases"]
    hdr = [c.value for c in ws[1]]
    col = lambda n: hdr.index(n) + 1  # noqa: E731
    ws.cell(row=2, column=col("Case difficulty")).value = "extremely_hard"
    wb.save(path)

    with pytest.raises(ValueError):
        read_reviewer_submission(path, sample)


def test_read_rejects_missing_row(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "wb.xlsx"
    write_reviewer_workbook(sample, "alice", path)
    fill_reviewer_workbook(path, "alice")

    wb = openpyxl.load_workbook(path)
    ws = wb["cases"]
    ws.delete_rows(2, 1)
    wb.save(path)

    with pytest.raises(ValueError, match="missing rows"):
        read_reviewer_submission(path, sample)
