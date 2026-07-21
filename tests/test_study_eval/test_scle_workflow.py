"""Discrepancy detection, arbitration, and consensus (incl. immutability)."""

from __future__ import annotations

import pytest

from llm_dup_dev.study_eval.scle.arbitration import (
    read_arbitration,
    write_arbitration_workbook,
)
from llm_dup_dev.study_eval.scle.consensus import merge_consensus
from llm_dup_dev.study_eval.scle.discrepancy import detect_discrepancies
from llm_dup_dev.study_eval.scle.workbook import (
    read_reviewer_submission,
    write_reviewer_workbook,
)

from tests.test_study_eval.conftest import (
    build_scle_precision_sample,
    fill_arbitration_workbook,
    fill_reviewer_workbook,
)

# TP (correct) + FN (misclassified) gives an applicable never_event to disagree on.
_SPEC = [("duplicate", "Duplicates"), ("duplicate", "Otherwise_related")]


def _two_submissions(tmp_path, *, disagree=True, concern=False):
    _, _, sample = build_scle_precision_sample(_SPEC)
    pa = tmp_path / "wa.xlsx"
    pb = tmp_path / "wb.xlsx"
    write_reviewer_workbook(sample, "alice", pa)
    write_reviewer_workbook(sample, "bob", pb)
    fill_reviewer_workbook(
        pa, "alice", ref_issue_first="possibly_incorrect" if concern else None
    )
    fill_reviewer_workbook(pb, "bob", never_event="yes" if disagree else "no")
    sub_a = read_reviewer_submission(pa, sample)
    sub_b = read_reviewer_submission(pb, sample)
    return sample, sub_a, sub_b


def test_detect_discrepancies_finds_structured_disagreement(tmp_path):
    _, sub_a, sub_b = _two_submissions(tmp_path, disagree=True)
    report = detect_discrepancies(sub_a, sub_b)
    assert report.n_pairs_with_disagreement == 1
    fields = {
        d.field
        for p in report.pair_discrepancies
        for d in p.dimension_disagreements
    }
    assert "never_event" in fields


def test_no_disagreement_when_identical(tmp_path):
    _, sub_a, sub_b = _two_submissions(tmp_path, disagree=False)
    report = detect_discrepancies(sub_a, sub_b)
    assert report.n_pairs_with_disagreement == 0


def test_detect_rejects_mismatched_pairs():
    from llm_dup_dev.study_eval.scle.form import (
        CaseDifficulty,
        ReferenceLabelIssue,
        ReviewerSubmission,
        SCLECaseAssessment,
    )

    def sub(reviewer, pair_ids):
        return ReviewerSubmission(
            workbook_id="w",
            reviewer_id=reviewer,
            evaluation_component="precision",
            sample_checksum="x",
            assessments=[
                SCLECaseAssessment(
                    pair_id=pid,
                    reviewer_id=reviewer,
                    free_text_assessment="t",
                    case_difficulty=CaseDifficulty.TRIVIAL,
                    reference_label_issue=ReferenceLabelIssue.NO_APPARENT_ISSUE,
                )
                for pid in pair_ids
            ],
        )

    with pytest.raises(ValueError, match="different pairs"):
        detect_discrepancies(sub("a", ["A0__B0"]), sub("b", ["C0__D0"]))


def test_consensus_preserves_originals_and_tracks_sources(tmp_path):
    sample, sub_a, sub_b = _two_submissions(tmp_path, disagree=True)
    report = detect_discrepancies(sub_a, sub_b)

    before_a = sub_a.model_dump_json()
    before_b = sub_b.model_dump_json()

    arb_path = tmp_path / "arb.xlsx"
    write_arbitration_workbook(report, sub_a, sub_b, sample, arb_path, arbiter_id="carol")
    fill_arbitration_workbook(arb_path, report)
    arbitration = read_arbitration(arb_path, sample)

    result = merge_consensus(sub_a, sub_b, arbitration, report)

    # Originals untouched.
    assert sub_a.model_dump_json() == before_a
    assert sub_b.model_dump_json() == before_b
    assert result.reviewer_submissions[0].model_dump_json() == before_a

    # Every consensus dimension has a source; both kinds appear.
    sources = {
        src
        for a in result.consensus
        for src in a.dimension_sources.values()
    }
    assert "reviewer_agreement" in sources
    assert "arbitration" in sources


def test_reference_label_concerns_collected(tmp_path):
    sample, sub_a, sub_b = _two_submissions(tmp_path, disagree=False, concern=True)
    report = detect_discrepancies(sub_a, sub_b)
    arb_path = tmp_path / "arb.xlsx"
    write_arbitration_workbook(report, sub_a, sub_b, sample, arb_path)
    fill_arbitration_workbook(arb_path, report)
    arbitration = read_arbitration(arb_path, sample)
    result = merge_consensus(sub_a, sub_b, arbitration, report)
    # Alice flagged the first case; both agree (bob also no_apparent_issue -> disagreement).
    # The concern surfaces only if consensus reference_label_issue != no_apparent_issue.
    concern_pairs = set(result.reference_label_concerns)
    for a in result.consensus:
        if a.reference_label_issue not in (None, "no_apparent_issue"):
            assert a.pair_id in concern_pairs


def test_consensus_requires_arbitration_for_disagreement(tmp_path):
    sample, sub_a, sub_b = _two_submissions(tmp_path, disagree=True)
    report = detect_discrepancies(sub_a, sub_b)
    arb_path = tmp_path / "arb.xlsx"
    write_arbitration_workbook(report, sub_a, sub_b, sample, arb_path)
    # Deliberately do NOT fill consensus values.
    import openpyxl

    wb = openpyxl.load_workbook(arb_path)
    ws = wb["cases"]
    hdr = [c.value for c in ws[1]]
    col = lambda n: hdr.index(n) + 1  # noqa: E731
    for r in range(2, ws.max_row + 1):
        ws.cell(row=r, column=col("Consensus free-text")).value = "x"
        ws.cell(row=r, column=col("Free-text materially different")).value = "no"
    wb.save(arb_path)
    arbitration = read_arbitration(arb_path, sample)
    with pytest.raises(ValueError, match="no.*consensus value"):
        merge_consensus(sub_a, sub_b, arbitration, report)
