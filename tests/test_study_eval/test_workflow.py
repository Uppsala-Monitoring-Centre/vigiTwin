"""End-to-end tests for the four-step workflow."""

from __future__ import annotations

import openpyxl
import pytest

from llm_dup_dev.study_eval import workflow
from llm_dup_dev.study_eval.aggregate import AnalysisVariant
from llm_dup_dev.study_eval.scle.vocabularies import (
    ERROR_STAGES_SHEET,
    FEATURE_DOMAINS_SHEET,
)
from llm_dup_dev.study_eval.scle.workbook import (
    DOMAIN_HEADER_PREFIX,
    STAGE_HEADER_PREFIX,
    workbook_vocabularies,
)

from tests.test_study_eval.conftest import (
    fill_arbitration_workbook,
    fill_reviewer_workbook,
    make_precision,
    make_recall,
)


def _packet_hyperlinks(path):
    """Return {pair_id: hyperlink_target} for the Pair ID column of a workbook."""
    wb = openpyxl.load_workbook(path)
    ws = wb["pairs"]
    hdr = [c.value for c in ws[1]]
    pid_col = hdr.index("Pair ID") + 1
    out = {}
    for r in range(2, ws.max_row + 1):
        cell = ws.cell(row=r, column=pid_col)
        if cell.value is None:
            continue
        out[cell.value] = cell.hyperlink.target if cell.hyperlink else None
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


def _mixed_mode_inputs():
    """The combined inputs plus a recall set with no screening results."""
    reference_sets, predictions = _combined_inputs()
    manual, mpreds = make_recall(
        {"manual": [(None, "Duplicates")] * 3 + [(None, "Unrelated")] * 3},
        modes={"manual": "not_evaluated"},
    )
    from llm_dup_dev.study_eval.schemas import PredictionSet

    return [*reference_sets, *manual], PredictionSet(
        predictions={**predictions.predictions, **mpreds.predictions}
    )


def test_run_analysis_handles_a_mixture_of_screening_modes(tmp_path):
    reference_sets, predictions = _mixed_mode_inputs()
    result = workflow.run_analysis(
        reference_sets, predictions, seed="s", scle_precision_per_cell=2,
        scle_recall_per_stratum=2, output_dir=tmp_path,
    )

    recall = result.recall_result
    assert recall.screening_evaluated_set_ids == ["fda_gold"]
    assert recall.screening_not_evaluated_set_ids == ["manual"]

    manual = next(s for s in recall.sets if s.reference_set_id == "manual")
    assert manual.recall_screening is None
    assert manual.recall_adjudicator_ceiling.numerator == 3

    summary = (tmp_path / "summary.md").read_text()
    assert "screening not evaluated" in summary


def test_predictions_table_keeps_screening_retrieved_boolean(tmp_path):
    """The mode goes in its own column, never as a status in a boolean one."""
    import csv

    reference_sets, predictions = _mixed_mode_inputs()
    workflow.run_analysis(
        reference_sets, predictions, seed="s", sample_scle=False, output_dir=tmp_path
    )
    with open(tmp_path / "predictions_table.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    manual = [r for r in rows if r["reference_set"] == "manual"]
    assert manual
    assert all(r["screening_mode"] == "not_evaluated" for r in manual)
    assert all(r["screening_retrieved"] == "" for r in manual)
    assert all(
        r["joint_stratum"] in {"adjudicator_positive", "adjudicator_negative"}
        for r in manual
    )

    observed = [r for r in rows if r["reference_set"] == "fda_gold"]
    assert all(r["screening_mode"] == "observed" for r in observed)
    assert {r["screening_retrieved"] for r in observed} <= {"yes", "no"}


def test_manifest_records_every_screening_mode(tmp_path):
    import json

    reference_sets, predictions = _mixed_mode_inputs()
    workflow.run_analysis(
        reference_sets, predictions, seed="s", sample_scle=False, output_dir=tmp_path
    )
    manifest = json.loads((tmp_path / "evaluation_run_manifest.json").read_text())

    assert manifest["screening_modes"] == {
        "prec": "observed",
        "fda_gold": "observed",
        "manual": "not_evaluated",
    }


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



# --- study vocabulary integration ------------------------------------------

# The study's frozen lists, deliberately unsorted so column order is testable.
_FROZEN_STAGES = ("relationship_assessment", "evidence_extraction")
_FROZEN_DOMAINS = ("demographics", "drugs")


def _pair_ids(path):
    """Ordered list of Pair ID values in a workbook's cases sheet."""
    wb = openpyxl.load_workbook(path)
    ws = wb["pairs"]
    hdr = [c.value for c in ws[1]]
    pid_col = hdr.index("Pair ID") + 1
    out = []
    for r in range(2, ws.max_row + 1):
        pid = ws.cell(row=r, column=pid_col).value
        if pid is not None:
            out.append(pid)
    return out


def _precision_sample(tmp_path):
    reference_sets, predictions = _combined_inputs()
    analysis = workflow.run_analysis(
        reference_sets, predictions, seed="s", scle_precision_per_cell=2,
    )
    return analysis.scle_samples["precision::prec"]


def test_omitting_the_vocabularies_drops_the_questions(tmp_path):
    sample = _precision_sample(tmp_path)

    baseline = workflow.generate_reviewer_workbooks(
        sample, ["alice"], tmp_path / "base"
    )
    supplied = workflow.generate_reviewer_workbooks(
        sample,
        ["alice"],
        tmp_path / "full",
        error_stages=_FROZEN_STAGES,
        feature_domains=_FROZEN_DOMAINS,
    )

    # Without the lists there are no vocabulary sheets and no generated columns,
    # but the sampled pairs and packet links are identical.
    base_wb = openpyxl.load_workbook(baseline[0])
    assert FEATURE_DOMAINS_SHEET not in base_wb.sheetnames
    assert ERROR_STAGES_SHEET not in base_wb.sheetnames
    base_hdr = [c.value for c in base_wb["pairs"][1]]
    assert not [h for h in base_hdr if h.startswith(STAGE_HEADER_PREFIX)]
    assert not [h for h in base_hdr if h.startswith(DOMAIN_HEADER_PREFIX)]
    assert _pair_ids(baseline[0]) == _pair_ids(supplied[0])
    assert _packet_hyperlinks(baseline[0]) == _packet_hyperlinks(supplied[0])


def test_generate_reviewer_workbooks_embeds_lists_in_both(tmp_path):
    sample = _precision_sample(tmp_path)

    workbooks = workflow.generate_reviewer_workbooks(
        sample,
        ["alice", "bob"],
        tmp_path / "wb",
        error_stages=_FROZEN_STAGES,
        feature_domains=_FROZEN_DOMAINS,
    )
    assert len(workbooks) == 2
    for wbpath in workbooks:
        assert workbook_vocabularies(wbpath) == (_FROZEN_STAGES, _FROZEN_DOMAINS)
        hdr = [c.value for c in openpyxl.load_workbook(wbpath)["pairs"][1]]
        assert [h for h in hdr if h.startswith(STAGE_HEADER_PREFIX)] == [
            f"{STAGE_HEADER_PREFIX}{s}" for s in _FROZEN_STAGES
        ]
        # Packet links and sampled pairs are unaffected by the generated columns.
        assert all(
            target == f"packets/{pid}.html"
            for pid, target in _packet_hyperlinks(wbpath).items()
        )
    assert _pair_ids(workbooks[0]) == [c.pair_id for c in sample.cases]


def test_bad_vocabulary_fails_before_any_workbook_is_written(tmp_path):
    sample = _precision_sample(tmp_path)
    out = tmp_path / "wb"
    with pytest.raises(ValueError, match="error_stages"):
        workflow.generate_reviewer_workbooks(
            sample, ["alice", "bob"], out, error_stages=["a", " a "]
        )
    assert not list(out.glob("*.xlsx"))


def test_mismatched_vocabulary_rejected_by_arbitration_and_finalize(tmp_path):
    sample = _precision_sample(tmp_path)
    workbooks = workflow.generate_reviewer_workbooks(
        sample,
        ["alice", "bob"],
        tmp_path / "wb",
        error_stages=_FROZEN_STAGES,
        feature_domains=_FROZEN_DOMAINS,
    )
    fill_reviewer_workbook(
        workbooks[0], "alice", never_event="no",
        ticked_stages=("evidence_extraction",), ticked_domains=("drugs",),
    )
    fill_reviewer_workbook(
        workbooks[1], "bob", never_event="yes",
        ticked_stages=("evidence_extraction",), ticked_domains=("drugs",),
    )

    # The workbooks were generated under _FROZEN_STAGES; a caller claiming a
    # different frozen list is processing the wrong workbooks.
    with pytest.raises(ValueError, match="error_stages"):
        workflow.build_arbitration(
            sample, workbooks, tmp_path / "arb", error_stages=("something_else",)
        )

    build = workflow.build_arbitration(
        sample, workbooks, tmp_path / "arb",
        error_stages=_FROZEN_STAGES, feature_domains=_FROZEN_DOMAINS,
    )
    fill_arbitration_workbook(build.arbitration_workbook, build.discrepancy)
    with pytest.raises(ValueError, match="feature_domains"):
        workflow.finalize(
            sample, workbooks, build.arbitration_workbook, tmp_path / "final",
            feature_domains=("not_the_frozen_list",),
        )


def test_per_stage_dimensions_reach_agreement_and_consensus(tmp_path):
    sample = _precision_sample(tmp_path)
    workbooks = workflow.generate_reviewer_workbooks(
        sample,
        ["alice", "bob"],
        tmp_path / "wb",
        error_stages=_FROZEN_STAGES,
        feature_domains=_FROZEN_DOMAINS,
    )
    # The reviewers tick different stages, so a per-stage dimension disagrees.
    fill_reviewer_workbook(
        workbooks[0], "alice", ticked_stages=("evidence_extraction",),
        ticked_domains=("drugs",),
    )
    fill_reviewer_workbook(
        workbooks[1], "bob", ticked_stages=("relationship_assessment",),
        ticked_domains=("drugs",),
    )

    build = workflow.build_arbitration(sample, workbooks, tmp_path / "arb")
    fields = {d.field for d in build.agreement.dimensions}
    assert "error_stage::evidence_extraction" in fields
    assert "error_stage::relationship_assessment" in fields
    assert "implicated_feature_domains" in fields
    disagreed = {
        d.field
        for p in build.discrepancy.pair_discrepancies
        for d in p.dimension_disagreements
    }
    assert "error_stage::evidence_extraction" in disagreed

    fill_arbitration_workbook(build.arbitration_workbook, build.discrepancy)
    result = workflow.finalize(
        sample, workbooks, build.arbitration_workbook, tmp_path / "final"
    )
    # Consensus resolves each stage to reviewer A's answer and collapses the
    # per-stage dimensions back into one list, with per-stage provenance kept.
    misclassified = [c for c in result.consensus.consensus if c.error_stages]
    assert misclassified
    for assessment in misclassified:
        assert assessment.error_stages == ["evidence_extraction"]
        assert (
            assessment.dimension_sources["error_stage::evidence_extraction"]
            == "arbitration"
        )
