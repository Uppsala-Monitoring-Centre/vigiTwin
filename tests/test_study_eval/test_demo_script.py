"""The documented end-to-end demo must keep working.

`README.md` tells a reader they can execute `scripts/study_eval_demo.py` as
written, and `docs/study_eval.md` presents the same four steps. That promise is
only worth making if something checks it, so this runs the real script against
the real workflow — no mocks — and asserts the artifacts it claims to produce.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parents[2] / "scripts" / "study_eval_demo.py"


@pytest.fixture(scope="module")
def demo():
    """Import the script by path; `scripts/` is not an installed package."""
    spec = importlib.util.spec_from_file_location("study_eval_demo", DEMO)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["study_eval_demo"] = module
    spec.loader.exec_module(module)
    yield module
    del sys.modules["study_eval_demo"]


def test_demo_script_exists():
    assert DEMO.is_file(), f"README points readers at {DEMO.name}; it must exist"


def test_demo_runs_all_four_steps_and_writes_final_artifacts(demo, tmp_path):
    final_dir = demo.run(tmp_path)

    assert (tmp_path / "analysis").is_dir(), "step 1 wrote no analysis directory"
    assert sorted((tmp_path / "review").glob("*.xlsx")), "step 2 wrote no workbooks"
    assert (tmp_path / "arbitration").is_dir(), "step 3 wrote no arbitration output"

    produced = {p.name for p in final_dir.iterdir()}
    for expected in (
        "study_results.json",
        "evaluation_run_manifest.json",
        "consensus_precision.json",
    ):
        assert expected in produced, f"step 4 did not write {expected}"


def test_demo_inputs_are_valid_and_use_both_label_vocabularies(demo):
    """The spec mixes the two vocabularies; a swap would be an easy mistake."""
    from llm_dup_dev.study_eval import ADJUDICATOR_LABELS

    reference_sets, predictions = demo.build_inputs()
    (reference_set,) = reference_sets

    assert {p.reference_label for p in reference_set.pairs} <= {
        "duplicate",
        "non_duplicate",
        "abstain",
    }
    assert {p.adjudicator_label for p in predictions.predictions.values()} <= set(
        ADJUDICATOR_LABELS
    )
    assert {p.pair_id for p in reference_set.pairs} == set(predictions.predictions)


def test_demo_produces_exactly_one_structured_disagreement(demo, tmp_path):
    """The dissent is deliberate: without it step 3 has nothing to arbitrate."""
    from llm_dup_dev.study_eval import workflow

    reference_sets, predictions = demo.build_inputs()
    analysis = workflow.run_analysis(
        reference_sets,
        predictions,
        seed="demo-2026",
        scle_precision_per_cell=2,
        output_dir=tmp_path / "analysis",
    )
    sample = analysis.scle_samples[next(iter(analysis.scle_samples))]
    workbooks = workflow.generate_reviewer_workbooks(
        sample, ["reviewer_1", "reviewer_2"], tmp_path / "review"
    )
    for book, dissent in zip(workbooks, (False, True), strict=True):
        demo.fill_reviewer_workbook(book, book.stem, dissent=dissent)

    build = workflow.build_arbitration(
        sample, workbooks, tmp_path / "arbitration", arbiter_id="demo_arbiter"
    )
    disagreed = [
        p for p in build.discrepancy.pair_discrepancies if p.dimension_disagreements
    ]
    assert len(disagreed) == 1
    assert [d.field for d in disagreed[0].dimension_disagreements] == ["case_difficulty"]
