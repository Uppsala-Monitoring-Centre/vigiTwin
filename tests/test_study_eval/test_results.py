"""What the written artifacts say — and, as much, what they must not say.

Two themes: how absent screening results are represented, and how the
secondary-comparison table attributes a figure to the set it came from.
"""

from __future__ import annotations

import csv
import json

import pytest

from llm_dup_dev.study_eval.aggregate import (
    ABSTAIN_POSITIVE,
    OTHERWISE_RELATED_POSITIVE,
    PRIMARY,
    PositiveClass,
    run_precision_analysis,
    run_precision_variants,
    run_recall_analysis,
)
from llm_dup_dev.study_eval.results import (
    summary_to_markdown,
    write_secondary_comparison_csv,
    write_study_artifacts,
)
from llm_dup_dev.study_eval.schemas import PredictionSet

from tests.test_study_eval.conftest import make_precision, make_recall

#: The three rules this study prespecifies, in protocol order.
_ALL_RULES = [PRIMARY, ABSTAIN_POSITIVE, OTHERWISE_RELATED_POSITIVE]

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
    write_study_artifacts(tmp_path, recall_results=[_mixed_recall_result()], baseline="primary")
    rows = _rows(tmp_path / "recall_metrics.csv")

    manual = [r for r in rows if r["reference_set"] == "manual"]
    assert {r["estimand"] for r in manual} == {"recall_adjudicator_ceiling"}
    assert all(r["screening_mode"] == "not_evaluated" for r in manual)

    # Omitted, not written blank: a blank cell already means "undefined at n=0".
    observed = [r for r in rows if r["reference_set"] == "fda_gold"]
    assert _SCREENING_ESTIMANDS <= {r["estimand"] for r in observed}
    assert all(r["screening_mode"] == "observed" for r in observed)


def test_joint_classification_csv_covers_only_observed_sets(tmp_path):
    write_study_artifacts(tmp_path, recall_results=[_mixed_recall_result()], baseline="primary")
    rows = _rows(tmp_path / "recall_joint_classification.csv")

    assert {r["reference_set"] for r in rows} == {"fda_gold"}


def test_study_results_json_nulls_the_screening_estimands(tmp_path):
    write_study_artifacts(tmp_path, recall_results=[_mixed_recall_result()], baseline="primary")
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
    summary = summary_to_markdown([], [_mixed_recall_result()], baseline="primary")
    line = _set_line(summary, "manual")

    assert "screening not evaluated" in line
    # No screening or serial figure of any kind — not a number, and not the
    # `n/a` that elsewhere means "undefined at n=0".
    assert "screening n/a" not in line
    assert "serial" not in line


def test_summary_still_prints_a_perfect_adjudicator_score():
    """100% is a legitimate adjudicator result and must remain printable."""
    summary = summary_to_markdown([], [_mixed_recall_result()], baseline="primary")
    line = _set_line(summary, "manual")

    assert "adjudicator recall on the supplied reference set" in line
    assert "1.000" in line


def test_summary_states_the_scope_of_the_screening_figures():
    summary = summary_to_markdown([], [_mixed_recall_result()], baseline="primary")

    assert "fda_gold" in summary
    assert "not estimated for manual" in summary


def test_observed_only_summary_is_unchanged():
    sets, predictions = make_recall({"fda_gold": [(True, "Duplicates")]})
    summary = summary_to_markdown([], [run_recall_analysis(sets, predictions)], baseline="primary")
    line = _set_line(summary, "fda_gold")

    assert "screening 1.000" in line
    assert "serial 1.000" in line
    assert "ceiling 1.000" in line
    assert "not evaluated" not in summary


# --- secondary comparison: a figure belongs to the set it came from ---------


def _two_precision_sets_across_variants():
    """Three variants over each of two precision sets with different metrics.

    The two sets share pair ids (a pair may legitimately belong to several
    sets); what makes the bundle discriminating is that their compositions
    differ, so every metric has a different primary baseline per set.
    """
    spec = (
        [("duplicate", "Duplicates")] * 4
        + [("non_duplicate", "Duplicates")] * 3
        + [("duplicate", "Otherwise_related")] * 3
        + [("non_duplicate", "Unrelated")] * 4
    )
    set_a, preds_a = make_precision(spec, set_id="prec_a")
    set_b, preds_b = make_precision(spec[:6], set_id="prec_b")
    predictions = PredictionSet(
        predictions={**preds_a.predictions, **preds_b.predictions}
    )
    return (
        list(run_precision_variants(set_a, predictions, _ALL_RULES).values())
        + list(run_precision_variants(set_b, predictions, _ALL_RULES).values()),
        predictions,
    )


def test_secondary_comparison_uses_each_sets_own_primary_baseline(tmp_path):
    """A pooled bundle must not difference one set against another's baseline."""
    results, _ = _two_precision_sets_across_variants()
    baselines = {
        r.set_id: r.adjudicator_precision.proportion
        for r in results
        if r.variant == "primary"
    }
    # Guard the guard: identical baselines would make this test pass blind.
    assert baselines["prec_a"] != baselines["prec_b"]

    write_study_artifacts(tmp_path, precision_results=results, baseline="primary")
    rows = [
        r
        for r in _rows(tmp_path / "secondary_comparison.csv")
        if r["component"] == "precision"
    ]

    # Both sets are named; neither set's rows are filed under the other.
    assert {r["set"] for r in rows} == {"prec_a", "prec_b"}
    for row in rows:
        if row["metric"] != "adjudicator_precision":
            continue
        assert row["baseline_proportion"] == f"{baselines[row['set']]:.4f}"


def test_secondary_comparison_rejects_a_variant_with_no_baseline(tmp_path):
    """An incomparable bundle fails loudly rather than dropping the row."""
    results, _ = _two_precision_sets_across_variants()
    orphaned = [
        r for r in results if not (r.set_id == "prec_b" and r.variant == "primary")
    ]

    with pytest.raises(KeyError):
        write_study_artifacts(tmp_path, precision_results=orphaned, baseline="primary")


# --- The baseline is named, never guessed -----------------------------------


def test_summary_rejects_a_baseline_absent_from_the_results():
    """A missing baseline must not degrade into a title-only summary.

    The baseline used to be found by matching the literal name "primary", so a
    study naming its rules differently silently got a summary containing nothing
    but its heading.
    """
    result = _mixed_recall_result()  # computed under "primary"

    with pytest.raises(ValueError, match="not among the supplied results"):
        summary_to_markdown([], [result], baseline="house_rule")


def test_secondary_comparison_rejects_a_baseline_absent_from_the_results(tmp_path):
    """Likewise for the comparison table, which used to come out header-only."""
    results, _ = _two_precision_sets_across_variants()

    with pytest.raises(ValueError, match="not among the supplied results"):
        write_secondary_comparison_csv(
            results, [], tmp_path / "cmp.csv", baseline="house_rule"
        )


def test_a_study_named_baseline_works_end_to_end(tmp_path):
    """Nothing privileges the name "primary" — any designated rule is a baseline."""
    house = PositiveClass(
        name="house_rule", positive_labels=frozenset({"Duplicates", "Abstain"})
    )
    rset, preds = make_precision([("duplicate", "Duplicates")])
    results = list(run_precision_variants(rset, preds, [house, PRIMARY]).values())

    write_study_artifacts(tmp_path, precision_results=results, baseline="house_rule")

    summary = (tmp_path / "summary.md").read_text()
    assert "## Precision (house_rule)" in summary
    rows = _rows(tmp_path / "secondary_comparison.csv")
    # Only the non-baseline rule is differenced against the baseline.
    assert {r["variant"] for r in rows} == {"primary"}


def test_single_rule_run_writes_no_comparison_table(tmp_path):
    """One rule means nothing to compare, so no empty artifact is produced."""
    rset, preds = make_precision([("duplicate", "Duplicates")])
    results = [run_precision_analysis(rset, preds, PRIMARY)]

    written = write_study_artifacts(
        tmp_path, precision_results=results, baseline="primary"
    )

    assert not (tmp_path / "secondary_comparison.csv").exists()
    assert all("secondary_comparison" not in str(p) for p in written)
    # The summary is still written, and still populated.
    assert "## Precision (primary)" in (tmp_path / "summary.md").read_text()


def test_artifacts_require_a_baseline_when_results_are_supplied(tmp_path):
    rset, preds = make_precision([("duplicate", "Duplicates")])
    results = [run_precision_analysis(rset, preds, PRIMARY)]

    with pytest.raises(ValueError, match="needs `baseline`"):
        write_study_artifacts(tmp_path, precision_results=results)
