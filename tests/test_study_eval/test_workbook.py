"""Tests for reviewer-workbook generation and validated read-back."""

from __future__ import annotations

import openpyxl
import pytest

from llm_dup_dev.study_eval.scle.form import YesNo
from llm_dup_dev.study_eval.scle.vocabularies import (
    ERROR_STAGES_SHEET,
    FEATURE_DOMAINS_SHEET,
)
from llm_dup_dev.study_eval.scle.workbook import (
    DOMAIN_HEADER_PREFIX,
    GUIDANCE_SHEET,
    NOT_APPLICABLE,
    STAGE_HEADER_PREFIX,
    read_reviewer_submission,
    workbook_vocabularies,
    write_reviewer_workbook,
)

from tests.test_study_eval.conftest import (
    ERROR_STAGES,
    FEATURE_DOMAINS,
    build_scle_precision_sample,
    fill_reviewer_workbook,
)

# A spec producing a TP (correct) and an FN (misclassified) case.
_SPEC = [("duplicate", "Duplicates"), ("duplicate", "Otherwise_related")]

#: Headers whose cells hold sentences rather than labels, and so are left-aligned.
_PROSE_HEADERS = {
    "Decision trace",
    "Free-text assessment",
    "Source of error",
    "Method-difference explanation",
}


def _write(sample, path, reviewer="alice", **kwargs):
    """Write a workbook under the suite's standard vocabularies."""
    kwargs.setdefault("error_stages", ERROR_STAGES)
    kwargs.setdefault("feature_domains", FEATURE_DOMAINS)
    return write_reviewer_workbook(sample, reviewer, path, **kwargs)


def test_write_then_read_roundtrip(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    written, workbook_id = _write(sample, path)
    assert written.exists()

    fill_reviewer_workbook(path, "alice")
    sub = read_reviewer_submission(path, sample)

    assert sub.reviewer_id == "alice"
    assert len(sub.assessments) == len(sample.cases)
    by_pair = {a.pair_id: a for a in sub.assessments}
    fn_pair = next(c.pair_id for c in sample.cases if c.stratum == "FN")
    tp_pair = next(c.pair_id for c in sample.cases if c.stratum == "TP")
    # Misclassified FN case carries the misclassification block.
    assert by_pair[fn_pair].never_event == YesNo.NO
    assert by_pair[fn_pair].error_source_description
    # Correctly classified TP case does not.
    assert by_pair[tp_pair].never_event is None
    # The submission carries the vocabularies it was answered under.
    assert sub.error_stages == list(ERROR_STAGES)
    assert sub.feature_domains == list(FEATURE_DOMAINS)


def test_generated_columns_follow_the_supplied_order(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    # Deliberately unsorted, to prove the study's order is preserved.
    stages = ("relationship_assessment", "evidence_extraction")
    domains = ("narrative", "dates", "drugs")
    _write(sample, path, error_stages=stages, feature_domains=domains)

    hdr = [c.value for c in openpyxl.load_workbook(path)["pairs"][1]]
    assert [h for h in hdr if h.startswith(STAGE_HEADER_PREFIX)] == [
        f"{STAGE_HEADER_PREFIX}{s}" for s in stages
    ]
    assert [h for h in hdr if h.startswith(DOMAIN_HEADER_PREFIX)] == [
        f"{DOMAIN_HEADER_PREFIX}{d}" for d in domains
    ]
    # The stage block sits before the domain block, both before the last column.
    assert hdr.index(f"{STAGE_HEADER_PREFIX}{stages[-1]}") < hdr.index(
        f"{DOMAIN_HEADER_PREFIX}{domains[0]}"
    )
    assert hdr[-1] == "Method-difference explanation"
    assert workbook_vocabularies(path) == (stages, domains)


def test_tick_only_columns_read_back_as_lists(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    _write(sample, path)
    fill_reviewer_workbook(
        path, "alice", ticked_stages=("independence_assessment",), ticked_domains=()
    )

    sub = read_reviewer_submission(path, sample)
    by_pair = {a.pair_id: a for a in sub.assessments}
    fn_pair = next(c.pair_id for c in sample.cases if c.stratum == "FN")
    tp_pair = next(c.pair_id for c in sample.cases if c.stratum == "TP")
    # Only the ticked entry is recorded; blanks mean "no", not "unanswered".
    assert by_pair[fn_pair].error_stages == ["independence_assessment"]
    assert by_pair[fn_pair].implicated_feature_domains == []
    assert by_pair[tp_pair].error_stages == []


def _blank_yes_no_cells(path, *, also_blank_source_of_error=False):
    """Clear the yes/no misclassification cells a reviewer may leave empty."""
    wb = openpyxl.load_workbook(path)
    ws = wb["pairs"]
    hdr = [c.value for c in ws[1]]
    headers = [
        "Never event",
        "Unexpected method behaviour",
        "Incorrect or imprecise input data",
    ]
    if also_blank_source_of_error:
        headers.append("Source of error")
    for r in range(2, ws.max_row + 1):
        for header in headers:
            cell = ws.cell(row=r, column=hdr.index(header) + 1)
            if cell.value != NOT_APPLICABLE:
                cell.value = None
    wb.save(path)


def test_blank_yes_no_cells_read_back_as_no(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    _write(sample, path)
    fill_reviewer_workbook(path, "alice")
    _blank_yes_no_cells(path)

    sub = read_reviewer_submission(path, sample)
    by_pair = {a.pair_id: a for a in sub.assessments}
    fn_pair = next(c.pair_id for c in sample.cases if c.stratum == "FN")
    assert by_pair[fn_pair].never_event == YesNo.NO
    assert by_pair[fn_pair].unexpected_method_behaviour == YesNo.NO
    assert by_pair[fn_pair].incorrect_or_imprecise_input_data == YesNo.NO
    # The block still counts as answered, so tick-only comparisons stay valid.
    assert by_pair[fn_pair].misclassification_answered


def test_correctly_classified_pairs_keep_the_block_unanswered(tmp_path):
    # The default must not leak onto pairs that were never asked the question.
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    _write(sample, path)
    fill_reviewer_workbook(path, "alice")
    _blank_yes_no_cells(path)

    sub = read_reviewer_submission(path, sample)
    by_pair = {a.pair_id: a for a in sub.assessments}
    tp_pair = next(c.pair_id for c in sample.cases if c.stratum == "TP")
    assert by_pair[tp_pair].never_event is None
    assert not by_pair[tp_pair].misclassification_answered


def test_blank_source_of_error_still_fails_on_a_misclassified_pair(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    _write(sample, path)
    fill_reviewer_workbook(path, "alice")
    _blank_yes_no_cells(path, also_blank_source_of_error=True)

    with pytest.raises(ValueError) as exc:
        read_reviewer_submission(path, sample)
    message = str(exc.value)
    assert "error_source_description" in message
    # Only the free text is missing — the blank yes/no cells are not gaps.
    assert "never_event" not in message


def test_required_cells_are_shaded_and_blank_tolerant_ones_are_not(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    _write(sample, path)

    ws = openpyxl.load_workbook(path)["pairs"]
    hdr = [c.value for c in ws[1]]
    fill_of = lambda r, n: ws.cell(  # noqa: E731
        row=r, column=hdr.index(n) + 1
    ).fill.start_color.rgb
    fn_row = next(
        r
        for r in range(2, ws.max_row + 1)
        if ws.cell(row=r, column=hdr.index("Source of error") + 1).value
        != NOT_APPLICABLE
    )
    shaded = fill_of(fn_row, "Source of error")
    assert shaded.endswith("FDF0D5")
    # Always-applicable questions are shaded too.
    assert fill_of(fn_row, "Free-text assessment").endswith("FDF0D5")
    assert fill_of(fn_row, "Difficulty").endswith("FDF0D5")
    # Blank-tolerant cells are left unshaded, which is the whole distinction.
    for header in ("Never event", f"{STAGE_HEADER_PREFIX}evidence_extraction"):
        assert not fill_of(fn_row, header).endswith("FDF0D5")


def test_omitted_vocabularies_drop_the_questions(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    write_reviewer_workbook(sample, "alice", path)  # no vocabularies

    wb = openpyxl.load_workbook(path)
    hdr = [c.value for c in wb["pairs"][1]]
    assert not [h for h in hdr if h.startswith(STAGE_HEADER_PREFIX)]
    assert not [h for h in hdr if h.startswith(DOMAIN_HEADER_PREFIX)]
    assert ERROR_STAGES_SHEET not in wb.sheetnames
    assert FEATURE_DOMAINS_SHEET not in wb.sheetnames

    # The workbook is still complete and reads back cleanly.
    fill_reviewer_workbook(path, "alice")
    sub = read_reviewer_submission(path, sample)
    assert sub.error_stages == []
    assert all(a.error_stages == [] for a in sub.assessments)


def test_read_rejects_a_generated_column_not_in_the_vocabulary(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    _write(sample, path)
    fill_reviewer_workbook(path, "alice")

    # Tamper: add a stage column the embedded vocabulary does not list.
    wb = openpyxl.load_workbook(path)
    ws = wb["pairs"]
    ws.cell(row=1, column=ws.max_column + 1, value=f"{STAGE_HEADER_PREFIX}invented")
    wb.save(path)

    with pytest.raises(ValueError, match="embedded vocabularies"):
        read_reviewer_submission(path, sample)


def test_read_rejects_a_non_yes_no_tick(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    _write(sample, path)
    fill_reviewer_workbook(path, "alice")

    wb = openpyxl.load_workbook(path)
    ws = wb["pairs"]
    hdr = [c.value for c in ws[1]]
    column = hdr.index(f"{STAGE_HEADER_PREFIX}{ERROR_STAGES[0]}") + 1
    for r in range(2, ws.max_row + 1):
        if ws.cell(row=r, column=column).value != NOT_APPLICABLE:
            ws.cell(row=r, column=column).value = "maybe"
            break
    wb.save(path)

    with pytest.raises(ValueError, match="expected"):
        read_reviewer_submission(path, sample)


def test_every_question_carries_guidance(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    _write(sample, path)

    wb = openpyxl.load_workbook(path)
    ws = wb["pairs"]
    for cell in ws[1]:
        assert cell.comment is not None, f"no guidance on header {cell.value!r}"
        assert cell.comment.text.strip()

    assert GUIDANCE_SHEET in wb.sheetnames
    guidance = wb[GUIDANCE_SHEET]
    listed = {
        guidance.cell(row=r, column=1).value
        for r in range(2, guidance.max_row + 1)
    }
    assert {c.value for c in ws[1]} <= listed
    # The interpretation the study agreed on reaches the reviewer verbatim.
    difficulty = next(
        guidance.cell(row=r, column=3).value
        for r in range(2, guidance.max_row + 1)
        if guidance.cell(row=r, column=1).value == "Difficulty"
    )
    assert "very rare" in difficulty
    assert "'clear'" in difficulty


def _recall_sample(specs, modes=None, *, seed="s"):
    """Draw a recall SCLE sample from a ``make_recall`` spec."""
    from llm_dup_dev.study_eval.aggregate import run_recall_analysis
    from llm_dup_dev.study_eval.sampling import sample_scle_recall
    from tests.test_study_eval.conftest import make_recall

    rsets, preds = make_recall(specs, modes=modes)
    return sample_scle_recall(
        run_recall_analysis(rsets, preds), preds, per_stratum=5, seed=seed
    )


def test_screening_columns_are_dropped_when_nothing_was_screened(tmp_path):
    """A column of nothing but "does not apply" is not worth its width."""
    sample = _recall_sample(
        {"manual": [(None, "Duplicates"), (None, "Unrelated")]},
        modes={"manual": "not_evaluated"},
    )
    path = tmp_path / "reviewer_a.xlsx"
    write_reviewer_workbook(sample, "alice", path)

    ws = openpyxl.load_workbook(path)["pairs"]
    header = [c.value for c in ws[1]]
    assert "Screening retrieved" not in header
    assert "Methods disagree" not in header
    # The joint stratum goes too — such a set has none.
    assert "Joint stratum" not in header
    assert "Confusion cell" in header

    # The response dimension that depended on them is still shown, as N/A.
    column = header.index("Method-difference explanation") + 1
    values = {ws.cell(row=r, column=column).value for r in range(2, ws.max_row + 1)}
    assert values == {NOT_APPLICABLE}


def test_screening_columns_survive_a_partly_screened_sample(tmp_path):
    """Mixed sets are the case the columns exist for: keep them, mark the gaps."""
    sample = _recall_sample(
        {
            "fda": [(True, "Duplicates"), (False, "Unrelated")],
            "manual": [(None, "Duplicates")],
        },
        modes={"manual": "not_evaluated"},
    )
    path = tmp_path / "reviewer_a.xlsx"
    write_reviewer_workbook(sample, "alice", path)

    ws = openpyxl.load_workbook(path)["pairs"]
    header = [c.value for c in ws[1]]
    column = header.index("Screening retrieved") + 1
    values = {ws.cell(row=r, column=column).value for r in range(2, ws.max_row + 1)}
    assert NOT_APPLICABLE in values and values - {NOT_APPLICABLE}


def test_constant_context_moves_to_the_metadata_sheet(tmp_path):
    """One reference set for the whole workbook is stated once, not per row."""
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    _write(sample, path)

    wb = openpyxl.load_workbook(path)
    header = [c.value for c in wb["pairs"][1]]
    assert "Reference set" not in header
    assert "Component" not in header
    # Every pair in a precision set is screening-positive, so that column would
    # read "yes" all the way down.
    assert "Screening retrieved" not in header

    meta = {
        r[0]: r[1] for r in wb["metadata"].iter_rows(min_row=2, values_only=True) if r
    }
    assert meta["reference_set_id"] == sample.cases[0].reference_set_id
    assert meta["evaluation_component"] == "precision"
    assert meta["n_pairs"] == str(len(sample.cases))
    assert meta["screening_retrieved"] == "yes"


def test_reference_set_column_stays_when_the_sample_spans_sets(tmp_path):
    sample = _recall_sample(
        {
            "fda": [(True, "Duplicates")],
            "who": [(True, "Unrelated")],
        }
    )
    path = tmp_path / "reviewer_a.xlsx"
    write_reviewer_workbook(sample, "alice", path)

    ws = openpyxl.load_workbook(path)["pairs"]
    header = [c.value for c in ws[1]]
    column = header.index("Reference set") + 1
    values = {ws.cell(row=r, column=column).value for r in range(2, ws.max_row + 1)}
    assert values == {"fda", "who"}


def test_pair_id_links_to_the_packet(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    targets = {c.pair_id: f"packets/{c.pair_id}.html" for c in sample.cases}
    write_reviewer_workbook(sample, "alice", path, packet_targets=targets)

    wb = openpyxl.load_workbook(path)
    ws = wb["pairs"]
    hdr = [c.value for c in ws[1]]
    assert "Packet" not in hdr  # the link lives on the id itself
    pid_col = hdr.index("Pair ID") + 1
    for r in range(2, ws.max_row + 1):
        cell = ws.cell(row=r, column=pid_col)
        # The id stays readable; only the link is added.
        assert cell.value in targets
        assert cell.hyperlink.target == targets[cell.value]

    # Read-back is unaffected by the hyperlinks.
    fill_reviewer_workbook(path, "alice")
    sub = read_reviewer_submission(path, sample)
    assert len(sub.assessments) == len(sample.cases)


def test_no_packet_targets_leaves_the_pair_id_plain(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    write_reviewer_workbook(sample, "alice", path)  # no packet_targets

    wb = openpyxl.load_workbook(path)
    ws = wb["pairs"]
    pid_col = [c.value for c in ws[1]].index("Pair ID") + 1
    pair_ids = {c.pair_id for c in sample.cases}
    for r in range(2, ws.max_row + 1):
        cell = ws.cell(row=r, column=pid_col)
        assert cell.hyperlink is None
        assert cell.value in pair_ids


def test_pairs_sheet_is_laid_out_for_scanning(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "reviewer_a.xlsx"
    _write(sample, path)

    ws = openpyxl.load_workbook(path)["pairs"]
    hdr = [c.value for c in ws[1]]
    assert ws.freeze_panes == "B2"
    assert ws.column_dimensions["A"].width  # widths are set, not left to Excel

    # Headers wrap and sit against the data, whatever they wrap to.
    for cell in ws[1]:
        assert cell.alignment.vertical == "bottom"
        assert cell.alignment.wrap_text

    # Everything sits at the top of its cell; only prose is left-aligned.
    for r in range(2, ws.max_row + 1):
        for c_idx in range(1, len(hdr) + 1):
            cell = ws.cell(row=r, column=c_idx)
            assert cell.alignment.vertical == "top", hdr[c_idx - 1]
            expected = "left" if hdr[c_idx - 1] in _PROSE_HEADERS else "center"
            assert cell.alignment.horizontal == expected, hdr[c_idx - 1]


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
    ws = wb["pairs"]
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
    ws = wb["pairs"]
    hdr = [c.value for c in ws[1]]
    col = lambda n: hdr.index(n) + 1  # noqa: E731
    ws.cell(row=2, column=col("Difficulty")).value = "extremely_hard"
    wb.save(path)

    with pytest.raises(ValueError):
        read_reviewer_submission(path, sample)


def test_read_rejects_missing_row(tmp_path):
    _, _, sample = build_scle_precision_sample(_SPEC)
    path = tmp_path / "wb.xlsx"
    write_reviewer_workbook(sample, "alice", path)
    fill_reviewer_workbook(path, "alice")

    wb = openpyxl.load_workbook(path)
    ws = wb["pairs"]
    ws.delete_rows(2, 1)
    wb.save(path)

    with pytest.raises(ValueError, match="missing rows"):
        read_reviewer_submission(path, sample)
