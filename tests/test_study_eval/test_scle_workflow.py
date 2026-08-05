"""Discrepancy detection, disagreement-focused arbitration, and consensus."""

from __future__ import annotations

import openpyxl
import pytest

from llm_dup_dev.study_eval.scle.arbitration import (
    read_arbitration,
    write_arbitration_workbook,
)
from llm_dup_dev.study_eval.scle.consensus import merge_consensus
from llm_dup_dev.study_eval.scle.discrepancy import detect_discrepancies
from llm_dup_dev.study_eval.results import write_scle_consensus_csv
from llm_dup_dev.study_eval.scle.workbook import (
    NOT_APPLICABLE,
    read_reviewer_submission,
    write_reviewer_workbook,
)

from tests.test_study_eval.conftest import (
    ERROR_STAGES,
    FEATURE_DOMAINS,
    build_scle_precision_sample,
    fill_arbitration_workbook,
    fill_reviewer_workbook,
)

# TP (correct) + FN (misclassified) gives an applicable never_event to disagree on.
_SPEC = [("duplicate", "Duplicates"), ("duplicate", "Otherwise_related")]


def _two_submissions(tmp_path, *, disagree=True, concern=False, vocabularies=False):
    _, _, sample = build_scle_precision_sample(_SPEC)
    pa = tmp_path / "wa.xlsx"
    pb = tmp_path / "wb.xlsx"
    vocab = (
        {"error_stages": ERROR_STAGES, "feature_domains": FEATURE_DOMAINS}
        if vocabularies
        else {}
    )
    write_reviewer_workbook(sample, "alice", pa, **vocab)
    write_reviewer_workbook(sample, "bob", pb, **vocab)
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
        d.field for p in report.pair_discrepancies for d in p.dimension_disagreements
    }
    assert "never_event" in fields


def test_no_disagreement_when_identical(tmp_path):
    _, sub_a, sub_b = _two_submissions(tmp_path, disagree=False)
    report = detect_discrepancies(sub_a, sub_b)
    assert report.n_pairs_with_disagreement == 0


def test_arbitration_workbook_contains_only_disagreement_pairs(tmp_path):
    sample, sub_a, sub_b = _two_submissions(tmp_path, disagree=True)
    report = detect_discrepancies(sub_a, sub_b)
    arb_path = tmp_path / "arb.xlsx"
    write_arbitration_workbook(report, sub_a, sub_b, sample, arb_path)

    wb = openpyxl.load_workbook(arb_path)
    ws = wb["pairs"]
    body_rows = [r for r in ws.iter_rows(min_row=2, values_only=True) if any(r)]
    disagree_pairs = {
        p.pair_id for p in report.pair_discrepancies if p.dimension_disagreements
    }
    assert len(body_rows) == len(disagree_pairs)


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

    sources = {src for a in result.consensus for src in a.dimension_sources.values()}
    assert "reviewer_agreement" in sources
    assert "arbitration" in sources
    # Both reviewers' free-texts retained.
    for a in result.consensus:
        assert set(a.reviewer_free_texts) == {"alice", "bob"}


def test_consensus_agreement_pairs_need_no_arbitration(tmp_path):
    # No structural disagreement -> arbitration workbook is empty -> consensus
    # must still resolve every pair from reviewer agreement.
    sample, sub_a, sub_b = _two_submissions(tmp_path, disagree=False)
    report = detect_discrepancies(sub_a, sub_b)
    arb_path = tmp_path / "arb.xlsx"
    write_arbitration_workbook(report, sub_a, sub_b, sample, arb_path)
    arbitration = read_arbitration(arb_path, sample)
    assert arbitration.decisions == []

    result = merge_consensus(sub_a, sub_b, arbitration, report)
    assert len(result.consensus) == len(sub_a.assessments)
    assert all(not a.arbitrated for a in result.consensus)
    assert all(
        src == "reviewer_agreement"
        for a in result.consensus
        for src in a.dimension_sources.values()
    )


# --- Arbitration workbook layout and pre-filling ---------------------------


def _arbitration_sheet(tmp_path, **kwargs):
    """Write an arbitration workbook for a disagreeing pair and return it."""
    sample, sub_a, sub_b = _two_submissions(tmp_path, disagree=True, **kwargs)
    report = detect_discrepancies(sub_a, sub_b)
    arb_path = tmp_path / "arb.xlsx"
    write_arbitration_workbook(report, sub_a, sub_b, sample, arb_path)
    ws = openpyxl.load_workbook(arb_path)["pairs"]
    hdr = [c.value for c in ws[1]]
    return sample, sub_a, sub_b, report, arb_path, ws, hdr


def _fill_rgb(ws, hdr, row, header):
    cell = ws.cell(row=row, column=hdr.index(header) + 1)
    return cell.fill.start_color.rgb if cell.fill and cell.fill.start_color else ""


def test_free_text_columns_precede_the_first_dimension(tmp_path):
    *_, hdr = _arbitration_sheet(tmp_path)
    first_dimension = next(i for i, h in enumerate(hdr) if h.endswith(" (A)")
                           and not h.startswith("Free-text"))
    assert hdr.index("Free-text (A)") < first_dimension
    assert hdr.index("Free-text (B)") < first_dimension
    # Moved, not duplicated: read-back indexes columns by header.
    assert hdr.count("Free-text (A)") == 1
    assert hdr.count("Free-text (B)") == 1


def test_agreed_dimensions_are_prefilled_and_disagreeing_ones_are_shaded(tmp_path):
    _, sub_a, _, report, _, ws, hdr = _arbitration_sheet(tmp_path)
    pair_id = ws.cell(row=2, column=hdr.index("Pair ID") + 1).value
    disagreed = {
        d.field
        for p in report.pair_discrepancies
        if p.pair_id == pair_id
        for d in p.dimension_disagreements
    }
    assert "never_event" in disagreed

    # The dimension under dispute is left blank and marked as needing a decision.
    assert ws.cell(row=2, column=hdr.index("Never event consensus") + 1).value is None
    assert _fill_rgb(ws, hdr, 2, "Never event consensus").endswith("FDF0D5")

    # A dimension they agreed on carries the settled value, styled do-not-edit.
    agreed_cell = ws.cell(row=2, column=hdr.index("Difficulty consensus") + 1)
    a_value = ws.cell(row=2, column=hdr.index("Difficulty (A)") + 1).value
    assert agreed_cell.value == a_value
    assert _fill_rgb(ws, hdr, 2, "Difficulty consensus").endswith("DDDDDD")


def test_non_applicable_dimensions_read_na_across_the_triple(tmp_path):
    # Disagreeing only on the reference-label issue pulls the *correctly
    # classified* pair into arbitration, so its whole misclassification block —
    # and its method-difference note — never applied.
    sample, sub_a, sub_b = _two_submissions(tmp_path, disagree=False, concern=True)
    report = detect_discrepancies(sub_a, sub_b)
    arb_path = tmp_path / "arb.xlsx"
    write_arbitration_workbook(report, sub_a, sub_b, sample, arb_path)
    ws = openpyxl.load_workbook(arb_path)["pairs"]
    hdr = [c.value for c in ws[1]]

    for header in (
        "Never event (A)",
        "Never event (B)",
        "Never event consensus",
        "Method-difference (A)",
        "Method-difference (B)",
        "Consensus method-difference",
    ):
        assert ws.cell(row=2, column=hdr.index(header) + 1).value == NOT_APPLICABLE
        assert _fill_rgb(ws, hdr, 2, header).endswith("F0F0F0")

    # The sentinel is not mistaken for an arbiter answer on read-back.
    arbitration = read_arbitration(arb_path, sample)
    assert arbitration.decisions[0].consensus_method_difference == ""
    assert NOT_APPLICABLE not in arbitration.decisions[0].consensus_dimensions.values()
    assert "never_event" not in arbitration.decisions[0].consensus_dimensions


def test_merge_consensus_rejects_an_edited_agreed_dimension(tmp_path):
    sample, sub_a, sub_b = _two_submissions(tmp_path, disagree=True)
    report = detect_discrepancies(sub_a, sub_b)
    arb_path = tmp_path / "arb.xlsx"
    write_arbitration_workbook(report, sub_a, sub_b, sample, arb_path)
    fill_arbitration_workbook(arb_path, report)

    # The arbiter overwrites a dimension both reviewers already agreed on.
    wb = openpyxl.load_workbook(arb_path)
    ws = wb["pairs"]
    hdr = [c.value for c in ws[1]]
    ws.cell(row=2, column=hdr.index("Difficulty consensus") + 1).value = "unclear"
    wb.save(arb_path)

    arbitration = read_arbitration(arb_path, sample)
    with pytest.raises(ValueError) as exc:
        merge_consensus(sub_a, sub_b, arbitration, report)
    assert "case_difficulty" in str(exc.value)
    assert "unclear" in str(exc.value)


def test_merge_consensus_accepts_untouched_prefilled_values(tmp_path):
    # The pre-filled values must survive a round trip unchanged, including the
    # set-valued dimension the workbook renders with ", ".
    sample, sub_a, sub_b = _two_submissions(
        tmp_path, disagree=True, vocabularies=True
    )
    report = detect_discrepancies(sub_a, sub_b)
    arb_path = tmp_path / "arb.xlsx"
    write_arbitration_workbook(report, sub_a, sub_b, sample, arb_path)
    fill_arbitration_workbook(arb_path, report)
    arbitration = read_arbitration(arb_path, sample)

    prefilled = arbitration.decisions[0].consensus_dimensions
    assert "implicated_feature_domains" in prefilled

    result = merge_consensus(sub_a, sub_b, arbitration, report)
    assert result.consensus  # no raise, and the agreed values stand
    for a in result.consensus:
        assert a.dimension_sources["implicated_feature_domains"] == (
            "reviewer_agreement"
        )


def test_read_arbitration_tolerates_a_workbook_without_the_new_column(tmp_path):
    # Read-back matches columns by header, so an arbitration workbook written
    # before the method-difference block still reads: the column is simply
    # absent and the consensus comes back empty.
    sample, sub_a, sub_b = _two_submissions(tmp_path, disagree=True)
    report = detect_discrepancies(sub_a, sub_b)
    arb_path = tmp_path / "arb.xlsx"
    write_arbitration_workbook(report, sub_a, sub_b, sample, arb_path)
    fill_arbitration_workbook(arb_path, report)

    wb = openpyxl.load_workbook(arb_path)
    ws = wb["pairs"]
    hdr = [c.value for c in ws[1]]
    ws.delete_cols(hdr.index("Consensus method-difference") + 1)
    wb.save(arb_path)

    arbitration = read_arbitration(arb_path, sample)
    assert arbitration.decisions[0].consensus_method_difference == ""
    # The dimensions the arbiter did resolve still come through.
    assert arbitration.decisions[0].consensus_dimensions


def test_method_difference_consensus_reaches_the_record_and_csv(tmp_path):
    # A recall sample can carry pairs where the methods disagree, which is where
    # the method-difference question applies.
    sample, sub_a, sub_b = _two_submissions(tmp_path, disagree=True)
    report = detect_discrepancies(sub_a, sub_b)
    arb_path = tmp_path / "arb.xlsx"
    write_arbitration_workbook(report, sub_a, sub_b, sample, arb_path)
    fill_arbitration_workbook(arb_path, report)

    wb = openpyxl.load_workbook(arb_path)
    ws = wb["pairs"]
    hdr = [c.value for c in ws[1]]
    ws.cell(row=2, column=hdr.index("Consensus method-difference") + 1).value = (
        "screening keyed on the shared drug"
    )
    wb.save(arb_path)

    arbitration = read_arbitration(arb_path, sample)
    result = merge_consensus(sub_a, sub_b, arbitration, report)
    arbitrated = [a for a in result.consensus if a.arbitrated]
    assert arbitrated
    assert arbitrated[0].consensus_method_difference == (
        "screening keyed on the shared drug"
    )

    csv_path = write_scle_consensus_csv(result, tmp_path / "consensus.csv")
    text = csv_path.read_text()
    assert "consensus_method_difference" in text.splitlines()[0]
    assert "screening keyed on the shared drug" in text


def test_reference_label_concerns_collected(tmp_path):
    sample, sub_a, sub_b = _two_submissions(tmp_path, disagree=False, concern=True)
    report = detect_discrepancies(sub_a, sub_b)
    arb_path = tmp_path / "arb.xlsx"
    write_arbitration_workbook(report, sub_a, sub_b, sample, arb_path)
    fill_arbitration_workbook(arb_path, report)
    arbitration = read_arbitration(arb_path, sample)
    result = merge_consensus(sub_a, sub_b, arbitration, report)
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
    wb = openpyxl.load_workbook(arb_path)
    ws = wb["pairs"]
    hdr = [c.value for c in ws[1]]
    col = lambda n: hdr.index(n) + 1  # noqa: E731
    for r in range(2, ws.max_row + 1):
        ws.cell(row=r, column=col("Consensus free-text")).value = "x"
        ws.cell(row=r, column=col("Free-text materially different")).value = "no"
    wb.save(arb_path)
    arbitration = read_arbitration(arb_path, sample)
    with pytest.raises(ValueError, match="no.*consensus value"):
        merge_consensus(sub_a, sub_b, arbitration, report)
