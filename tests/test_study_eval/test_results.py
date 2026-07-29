"""How absent screening results are represented in the written artifacts."""

from __future__ import annotations

import csv
import json

from llm_dup_dev.study_eval.aggregate import run_recall_analysis
from llm_dup_dev.study_eval.results import summary_to_markdown, write_study_artifacts

from tests.test_study_eval.conftest import make_recall

_SCREENING_ESTIMANDS = {"recall_screening", "recall_serial", "recall_reduction"}


def _mixed_recall_result():
    """One observed set, one not_evaluated set with a perfect adjudicator."""
    sets, predictions = make_recall(
        {
            "fda_gold": [(True, "Duplicates"), (False, "Unrelated")],
            "manual": [(None, "Duplicates"), (None, "Duplicates")],
        },
        modes={"manual": "not_evaluated"},
    )
    return run_recall_analysis(sets, predictions)


def _rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _set_line(summary: str, label: str) -> str:
    return next(line for line in summary.splitlines() if f"**{label}**" in line)


def test_recall_csv_omits_screening_rows_for_not_evaluated(tmp_path):
    write_study_artifacts(tmp_path, recall_results=[_mixed_recall_result()])
    rows = _rows(tmp_path / "recall_metrics.csv")

    manual = [r for r in rows if r["reference_set"] == "manual"]
    assert {r["estimand"] for r in manual} == {"recall_adjudicator_ceiling"}
    assert all(r["screening_mode"] == "not_evaluated" for r in manual)

    # Omitted, not written blank: a blank cell already means "undefined at n=0".
    observed = [r for r in rows if r["reference_set"] == "fda_gold"]
    assert _SCREENING_ESTIMANDS <= {r["estimand"] for r in observed}
    assert all(r["screening_mode"] == "observed" for r in observed)


def test_joint_classification_csv_covers_only_observed_sets(tmp_path):
    write_study_artifacts(tmp_path, recall_results=[_mixed_recall_result()])
    rows = _rows(tmp_path / "recall_joint_classification.csv")

    assert {r["reference_set"] for r in rows} == {"fda_gold"}


def test_study_results_json_nulls_the_screening_estimands(tmp_path):
    write_study_artifacts(tmp_path, recall_results=[_mixed_recall_result()])
    data = json.loads((tmp_path / "study_results.json").read_text())
    manual = next(
        s
        for s in data["recall_results"][0]["sets"]
        if s["reference_set_id"] == "manual"
    )

    assert manual["recall_screening"] is None
    assert manual["recall_serial"] is None
    assert manual["recall_reduction"] is None
    assert manual["joint_counts"] is None
    assert manual["joint_proportions"] is None
    assert manual["screening_mode"] == "not_evaluated"
    # The adjudicator estimand is still a real estimate.
    assert manual["recall_adjudicator_ceiling"]["proportion"] == 1.0


def test_summary_prints_no_screening_figure_for_not_evaluated():
    summary = summary_to_markdown([], [_mixed_recall_result()])
    line = _set_line(summary, "manual")

    assert "screening not evaluated" in line
    # No screening or serial figure of any kind — not a number, and not the
    # `n/a` that elsewhere means "undefined at n=0".
    assert "screening n/a" not in line
    assert "serial" not in line


def test_summary_still_prints_a_perfect_adjudicator_score():
    """100% is a legitimate adjudicator result and must remain printable."""
    summary = summary_to_markdown([], [_mixed_recall_result()])
    line = _set_line(summary, "manual")

    assert "adjudicator recall on the supplied reference set" in line
    assert "1.000" in line


def test_summary_states_the_scope_of_the_screening_figures():
    summary = summary_to_markdown([], [_mixed_recall_result()])

    assert "fda_gold" in summary
    assert "not estimated for manual" in summary


def test_observed_only_summary_is_unchanged():
    sets, predictions = make_recall({"fda_gold": [(True, "Duplicates")]})
    summary = summary_to_markdown([], [run_recall_analysis(sets, predictions)])
    line = _set_line(summary, "fda_gold")

    assert "screening 1.000" in line
    assert "serial 1.000" in line
    assert "ceiling 1.000" in line
    assert "not evaluated" not in summary
