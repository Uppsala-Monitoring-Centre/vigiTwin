"""Run the four-step `study_eval` workflow end to end on synthetic data.

This is the worked example `docs/study_eval.md` describes, as a script you can
actually execute. It needs no database, no network and no real reports — every
input is built here — so it is the quickest way to see what the package does
and to confirm an installation works:

    python scripts/study_eval_demo.py

In a real study, steps 2 and 4 are separated by human work: two reviewers fill
their workbooks, then an arbiter settles the disagreements. This script fills
those workbooks programmatically so the whole chain can run unattended. The
"reviewers" here deliberately disagree on one pair, so step 3 has something to
arbitrate and step 4 has a consensus to resolve.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import openpyxl

from llm_dup_dev.study_eval import workflow
from llm_dup_dev.study_eval.schemas import (
    PairPrediction,
    PredictionSet,
    ReferencePair,
    ReferenceSet,
)
from llm_dup_dev.study_eval.scle.arbitration import _dim_header
from llm_dup_dev.study_eval.scle.workbook import NOT_APPLICABLE

#: (reference label, what the method decided). Note the two vocabularies:
#: reference labels are 'duplicate' / 'non_duplicate' / 'abstain', while
#: adjudicator labels are the user-facing ADJUDICATOR_LABELS. A deliberate mix, so the
#: confusion matrix has entries off the diagonal and the SCLE has both
#: agreeing and erring pairs to examine.
SPEC: list[tuple[str, str]] = [
    ("duplicate", "Duplicates"),
    ("duplicate", "Duplicates"),
    ("duplicate", "Unrelated"),
    ("non_duplicate", "Unrelated"),
    ("non_duplicate", "Duplicates"),
    ("non_duplicate", "Otherwise_related"),
    ("duplicate", "Abstain"),
    ("non_duplicate", "Unrelated"),
]


def build_inputs() -> tuple[list[ReferenceSet], PredictionSet]:
    """A one-set precision bundle and the matching predictions."""
    pairs, predictions = [], {}
    for i, (reference_label, adjudicator_label) in enumerate(SPEC):
        pair_id = f"A{i}__B{i}"
        pairs.append(
            ReferencePair(
                pair_id=pair_id,
                report_id1=f"A{i}",
                report_id2=f"B{i}",
                cluster_id=f"c{i}",
                reference_label=reference_label,
            )
        )
        predictions[pair_id] = PairPrediction(
            pair_id=pair_id,
            adjudicator_label=adjudicator_label,
            screening_retrieved=True,
        )
    reference_set = ReferenceSet(set_id="demo_precision", role="precision", pairs=pairs)
    return [reference_set], PredictionSet(predictions=predictions)


def fill_reviewer_workbook(path: Path, reviewer: str, *, dissent: bool) -> None:
    """Stand in for a human reviewer filling their workbook.

    ``dissent`` makes this reviewer rate the first pair's difficulty
    differently, which is what gives step 3 a disagreement to arbitrate.
    """
    wb = openpyxl.load_workbook(path)
    ws = wb["pairs"]
    header = [c.value for c in ws[1]]

    def col(name: str) -> int:
        return header.index(name) + 1

    for row in range(2, ws.max_row + 1):
        pair_id = ws.cell(row=row, column=col("Pair ID")).value
        ws.cell(row=row, column=col("Free-text assessment")).value = (
            f"{reviewer} assessed {pair_id}"
        )
        ws.cell(row=row, column=col("Difficulty")).value = (
            "trivial" if (dissent and row == 2) else "non_trivial"
        )
        ws.cell(row=row, column=col("Reference-label issue")).value = (
            "no_apparent_issue"
        )
        if ws.cell(row=row, column=col("Never event")).value != NOT_APPLICABLE:
            ws.cell(row=row, column=col("Never event")).value = "no"
            ws.cell(row=row, column=col("Unexpected method behaviour")).value = "no"
            ws.cell(
                row=row, column=col("Incorrect or imprecise input data")
            ).value = "no"
            ws.cell(row=row, column=col("Source of error")).value = (
                "shared drug over-weighted"
            )
        explanation = ws.cell(row=row, column=col("Method-difference explanation"))
        if explanation.value != NOT_APPLICABLE:
            explanation.value = "difference explanation"
    wb.save(path)


def fill_arbitration_workbook(path: Path, discrepancy) -> None:
    """Stand in for the arbiter, settling every disagreement in A's favour."""
    wb = openpyxl.load_workbook(path)
    ws = wb["pairs"]
    header = [c.value for c in ws[1]]

    def col(name: str) -> int:
        return header.index(name) + 1

    disagreed = {
        p.pair_id: {d.field for d in p.dimension_disagreements}
        for p in discrepancy.pair_discrepancies
    }
    for row in range(2, ws.max_row + 1):
        pair_id = ws.cell(row=row, column=col("Pair ID")).value
        ws.cell(row=row, column=col("Free-text materially different")).value = "no"
        ws.cell(row=row, column=col("Consensus free-text")).value = "agreed text"
        ws.cell(row=row, column=col("Arbiter rationale")).value = "sided with A"
        for field in disagreed.get(pair_id, ()):
            dim = _dim_header(field)
            ws.cell(row=row, column=col(f"{dim} consensus")).value = ws.cell(
                row=row, column=col(f"{dim} (A)")
            ).value
    wb.save(path)


def run(output_dir: Path) -> Path:
    """Run all four steps under ``output_dir``; return the final-results dir."""
    reference_sets, predictions = build_inputs()

    print("Step 1  run_analysis")
    analysis = workflow.run_analysis(
        reference_sets,
        predictions,
        seed="demo-2026",
        scle_precision_per_cell=2,
        output_dir=output_dir / "analysis",
    )
    sample_key = next(iter(analysis.scle_samples))
    sample = analysis.scle_samples[sample_key]
    print(f"        sampled {len(sample.cases)} cases for SCLE ({sample_key})")

    print("Step 2  generate_reviewer_workbooks")
    workbooks = workflow.generate_reviewer_workbooks(
        sample, ["reviewer_1", "reviewer_2"], output_dir / "review"
    )
    for book, dissent in zip(workbooks, (False, True), strict=True):
        fill_reviewer_workbook(book, book.stem, dissent=dissent)
        print(f"        {book.name} filled")

    print("Step 3  build_arbitration")
    build = workflow.build_arbitration(
        sample, workbooks, output_dir / "arbitration", arbiter_id="demo_arbiter"
    )
    rows = build.discrepancy.pair_discrepancies
    dimension_level = sum(1 for p in rows if p.dimension_disagreements)
    print(
        f"        {len(rows)} pair(s) go to the arbiter "
        f"({dimension_level} with a structured disagreement, the rest because "
        f"the two free-text assessments differ and only the arbiter can say "
        f"whether that matters)"
    )
    fill_arbitration_workbook(build.arbitration_workbook, build.discrepancy)

    print("Step 4  finalize")
    final_dir = output_dir / "final"
    workflow.finalize(sample, workbooks, build.arbitration_workbook, final_dir)
    print(f"        consensus and final tables written to {final_dir}")
    return final_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Where to write artifacts. Defaults to a temporary directory.",
    )
    args = parser.parse_args()

    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        final_dir = run(args.output_dir)
        print(f"\nDone. Artifacts under {args.output_dir}")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            final_dir = run(Path(tmp))
            produced = sorted(p.name for p in final_dir.iterdir())
            print(f"\nDone. Final step produced: {', '.join(produced)}")
            print("Re-run with --output-dir to keep the artifacts.")


if __name__ == "__main__":
    main()
