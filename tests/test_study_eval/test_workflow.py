"""End-to-end tests for the four-step workflow."""

from __future__ import annotations

import openpyxl
import pytest

from llm_dup_dev.study_eval import workflow
from llm_dup_dev.study_eval.aggregate import AnalysisVariant
from llm_dup_dev.study_eval.scle.feature_domains import (
    FEATURE_DOMAINS_SHEET,
    workbook_feature_domains,
)

from tests.test_study_eval.conftest import (
    fill_arbitration_workbook,
    fill_reviewer_workbook,
    make_precision,
    make_recall,
)


def _packet_hyperlinks(path):
    """Return {pair_id: hyperlink_target} for the Packet column of a workbook."""
    wb = openpyxl.load_workbook(path)
    ws = wb["cases"]
    hdr = [c.value for c in ws[1]]
    pcol = hdr.index("Packet") + 1
    pid_col = hdr.index("Pair ID") + 1
    out = {}
    for r in range(2, ws.max_row + 1):
        pid = ws.cell(row=r, column=pid_col).value
        if pid is None:
            continue
        cell = ws.cell(row=r, column=pcol)
        out[pid] = cell.hyperlink.target if cell.hyperlink else None
    return out

_PRECISION_SPEC = (
    [("duplicate", "Duplicates")] * 4
    + [("non_duplicate", "Duplicates")] * 3  # FP
    + [("duplicate", "Otherwise_related")] * 3  # FN
    + [("non_duplicate", "Unrelated")] * 4  # TN
)


def _combined_inputs():
    pset, ppreds = make_precision(_PRECISION_SPEC, set_id="prec")
    rsets, rpreds = make_recall(
        {"fda_gold": [(True, "Duplicates"), (False, "Unrelated"), (True, "Unrelated")]}
    )
    from llm_dup_dev.study_eval.schemas import PredictionSet

    predictions = PredictionSet(
        predictions={**ppreds.predictions, **rpreds.predictions}
    )
    return [pset, *rsets], predictions


def test_run_analysis_precision_and_recall(tmp_path):
    reference_sets, predictions = _combined_inputs()
    result = workflow.run_analysis(
        reference_sets, predictions, seed="s", scle_precision_per_cell=2,
        scle_recall_per_stratum=2, output_dir=tmp_path,
    )
    assert len(result.precision_results) == 1
    assert result.recall_result is not None
    assert "precision::prec" in result.scle_samples
    assert "recall" in result.scle_samples

    names = {p.name for p in tmp_path.iterdir()}
    assert "predictions_table.csv" in names
    assert "precision_metrics.csv" in names
    assert "recall_metrics.csv" in names
    assert "evaluation_run_manifest.json" in names
    assert "scle_sample_precision_prec.json" in names


def test_secondary_analysis_is_step1_only_no_scle():
    reference_sets, predictions = _combined_inputs()
    result = workflow.run_analysis(
        reference_sets, predictions, seed="s", sample_scle=False,
        variant=AnalysisVariant.ABSTAIN_POSITIVE,
    )
    assert result.scle_samples == {}
    assert result.variant == "abstain_positive"


def test_scle_with_nonprimary_variant_warns_but_samples():
    import pytest

    reference_sets, predictions = _combined_inputs()
    with pytest.warns(UserWarning, match="non-primary variant"):
        result = workflow.run_analysis(
            reference_sets, predictions, seed="s", sample_scle=True,
            variant=AnalysisVariant.ABSTAIN_POSITIVE,
        )
    # Sampling still happens; strata reflect the chosen variant.
    assert result.scle_samples != {}
    assert result.variant == "abstain_positive"


def _fill_arbitration(path, discrepancy):
    fill_arbitration_workbook(path, discrepancy)


def test_full_four_step_workflow(tmp_path):
    reference_sets, predictions = _combined_inputs()
    analysis = workflow.run_analysis(
        reference_sets, predictions, seed="s", scle_precision_per_cell=2,
        output_dir=tmp_path / "analysis",
    )
    sample = analysis.scle_samples["precision::prec"]

    # Step 2
    workbooks = workflow.generate_reviewer_workbooks(
        sample, ["alice", "bob"], tmp_path / "wb"
    )
    assert len(workbooks) == 2
    fill_reviewer_workbook(workbooks[0], "alice", never_event="no")
    fill_reviewer_workbook(workbooks[1], "bob", never_event="yes")

    # Step 3
    build = workflow.build_arbitration(sample, workbooks, tmp_path / "arb")
    assert build.agreement.overall_percent_agreement is not None
    arb_names = {p.name for p in (tmp_path / "arb").iterdir()}
    assert "scle_agreement_precision.csv" in arb_names

    # Fill the arbitration workbook (resolve disagreements to reviewer A).
    _fill_arbitration(build.arbitration_workbook, build.discrepancy)

    # Step 4
    before = [open(w, "rb").read() for w in workbooks]
    final = workflow.finalize(
        sample, workbooks, build.arbitration_workbook, tmp_path / "final"
    )
    # Reviewer workbooks are not mutated by finalize.
    assert [open(w, "rb").read() for w in workbooks] == before
    assert len(final.consensus.consensus) == len(sample.cases)
    final_names = {p.name for p in (tmp_path / "final").iterdir()}
    assert "consensus_precision.json" in final_names
    assert "scle_consensus_precision.csv" in final_names


def test_reviewer_workbooks_link_to_packets(tmp_path):
    reference_sets, predictions = _combined_inputs()
    analysis = workflow.run_analysis(
        reference_sets, predictions, seed="s", scle_precision_per_cell=2,
    )
    sample = analysis.scle_samples["precision::prec"]
    workbooks = workflow.generate_reviewer_workbooks(sample, ["alice"], tmp_path / "wb")

    links = _packet_hyperlinks(workbooks[0])
    assert links  # every row has a link
    for pid, target in links.items():
        assert target == f"packets/{pid}.html"


def test_packet_dir_none_omits_links(tmp_path):
    reference_sets, predictions = _combined_inputs()
    analysis = workflow.run_analysis(
        reference_sets, predictions, seed="s", scle_precision_per_cell=2,
    )
    sample = analysis.scle_samples["precision::prec"]
    workbooks = workflow.generate_reviewer_workbooks(
        sample, ["alice"], tmp_path / "wb", packet_dir=None
    )
    assert all(target is None for target in _packet_hyperlinks(workbooks[0]).values())


def test_arbitration_workbook_links_to_packets(tmp_path):
    reference_sets, predictions = _combined_inputs()
    analysis = workflow.run_analysis(
        reference_sets, predictions, seed="s", scle_precision_per_cell=2,
    )
    sample = analysis.scle_samples["precision::prec"]
    workbooks = workflow.generate_reviewer_workbooks(sample, ["alice", "bob"], tmp_path / "wb")
    # Make the reviewers disagree so the arbitration workbook has rows.
    fill_reviewer_workbook(workbooks[0], "alice", never_event="no")
    fill_reviewer_workbook(workbooks[1], "bob", never_event="yes")

    build = workflow.build_arbitration(sample, workbooks, tmp_path / "arb")
    assert build.discrepancy.n_pairs_with_disagreement > 0

    links = _packet_hyperlinks(build.arbitration_workbook)
    assert links
    for pid, target in links.items():
        assert target == f"packets/{pid}.html"


# --- feature-domain integration -------------------------------------------

# A frozen list that is a strict subset of the package FEATURE_DOMAINS, so a
# reviewer can enter a package-valid domain that is not in the frozen list.
_FROZEN_DOMAINS = ("demographics", "drugs")


def _pair_ids(path):
    """Ordered list of Pair ID values in a workbook's cases sheet."""
    wb = openpyxl.load_workbook(path)
    ws = wb["cases"]
    hdr = [c.value for c in ws[1]]
    pid_col = hdr.index("Pair ID") + 1
    out = []
    for r in range(2, ws.max_row + 1):
        pid = ws.cell(row=r, column=pid_col).value
        if pid is not None:
            out.append(pid)
    return out


def _set_domain_cell(path, pair_id, value):
    """Set the implicated-feature-domains cell for one pair's row."""
    wb = openpyxl.load_workbook(path)
    ws = wb["cases"]
    hdr = [c.value for c in ws[1]]
    dom_col = hdr.index("Implicated feature domains (comma-separated)") + 1
    pid_col = hdr.index("Pair ID") + 1
    for r in range(2, ws.max_row + 1):
        if ws.cell(row=r, column=pid_col).value == pair_id:
            ws.cell(row=r, column=dom_col).value = value
            break
    wb.save(path)


def test_generate_reviewer_workbooks_none_matches_default(tmp_path):
    reference_sets, predictions = _combined_inputs()
    analysis = workflow.run_analysis(
        reference_sets, predictions, seed="s", scle_precision_per_cell=2,
    )
    sample = analysis.scle_samples["precision::prec"]

    baseline = workflow.generate_reviewer_workbooks(
        sample, ["alice"], tmp_path / "base"
    )
    supplied = workflow.generate_reviewer_workbooks(
        sample, ["alice"], tmp_path / "feat", feature_domains=_FROZEN_DOMAINS
    )

    # feature_domains=None leaves the workbook exactly as before: no extra sheet,
    # same pair ordering, same packet links.
    base_wb = openpyxl.load_workbook(baseline[0])
    assert FEATURE_DOMAINS_SHEET not in base_wb.sheetnames
    assert _pair_ids(baseline[0]) == _pair_ids(supplied[0])
    assert _packet_hyperlinks(baseline[0]) == _packet_hyperlinks(supplied[0])


def test_generate_reviewer_workbooks_embeds_list_in_both(tmp_path):
    reference_sets, predictions = _combined_inputs()
    analysis = workflow.run_analysis(
        reference_sets, predictions, seed="s", scle_precision_per_cell=2,
    )
    sample = analysis.scle_samples["precision::prec"]

    workbooks = workflow.generate_reviewer_workbooks(
        sample, ["alice", "bob"], tmp_path / "wb", feature_domains=_FROZEN_DOMAINS
    )
    assert len(workbooks) == 2
    for wbpath in workbooks:
        assert workbook_feature_domains(wbpath) == _FROZEN_DOMAINS
        # Packet links and sampled pairs are unchanged by augmentation.
        assert all(
            target == f"packets/{pid}.html"
            for pid, target in _packet_hyperlinks(wbpath).items()
        )
    assert _pair_ids(workbooks[0]) == [c.pair_id for c in sample.cases]


def test_invalid_reviewer_domain_rejected_before_arbitration_and_finalize(tmp_path):
    reference_sets, predictions = _combined_inputs()
    analysis = workflow.run_analysis(
        reference_sets, predictions, seed="s", scle_precision_per_cell=2,
        output_dir=tmp_path / "analysis",
    )
    sample = analysis.scle_samples["precision::prec"]
    fn_pair = next(c.pair_id for c in sample.cases if c.stratum == "FN")

    workbooks = workflow.generate_reviewer_workbooks(
        sample, ["alice", "bob"], tmp_path / "wb", feature_domains=_FROZEN_DOMAINS
    )
    fill_reviewer_workbook(workbooks[0], "alice", never_event="no")
    fill_reviewer_workbook(workbooks[1], "bob", never_event="yes")
    # 'adverse_events' is a valid package domain but not in the frozen list, so
    # the model read-back passes but the workflow gate must reject it.
    _set_domain_cell(workbooks[0], fn_pair, "adverse_events")

    # Read-back itself succeeds (the model accepts package-valid domains).
    from llm_dup_dev.study_eval.scle.workbook import read_reviewer_submission

    read_reviewer_submission(workbooks[0], sample)

    with pytest.raises(ValueError, match="adverse_events"):
        workflow.build_arbitration(
            sample, workbooks, tmp_path / "arb", feature_domains=_FROZEN_DOMAINS
        )

    # Build an arbitration workbook without the gate so finalize can be exercised.
    build = workflow.build_arbitration(sample, workbooks, tmp_path / "arb")
    fill_arbitration_workbook(build.arbitration_workbook, build.discrepancy)
    with pytest.raises(ValueError, match="adverse_events"):
        workflow.finalize(
            sample, workbooks, build.arbitration_workbook, tmp_path / "final",
            feature_domains=_FROZEN_DOMAINS,
        )
