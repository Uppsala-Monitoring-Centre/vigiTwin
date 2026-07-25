"""End-to-end tests for the four-step workflow."""

from __future__ import annotations

from llm_dup_dev.study_eval import workflow
from llm_dup_dev.study_eval.aggregate import AnalysisVariant

from tests.test_study_eval.conftest import (
    fill_arbitration_workbook,
    fill_reviewer_workbook,
    make_precision,
    make_recall,
)

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


def test_scle_with_nonprimary_variant_is_rejected():
    import pytest

    reference_sets, predictions = _combined_inputs()
    with pytest.raises(ValueError, match="only defined for the primary"):
        workflow.run_analysis(
            reference_sets, predictions, seed="s", sample_scle=True,
            variant=AnalysisVariant.ABSTAIN_POSITIVE,
        )


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
