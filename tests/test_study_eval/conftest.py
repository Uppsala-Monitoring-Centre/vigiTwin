"""Synthetic builders and fixtures for the study_eval test suite.

Everything here is synthetic; no real reports or database access. The builders
let each test dial in exactly the label / retrieval distribution it needs, over
the generic :class:`ReferenceSet` / :class:`PredictionSet` inputs.
"""

from __future__ import annotations

from llm_dup_dev.study_eval.aggregate import run_precision_analysis
from llm_dup_dev.study_eval.sampling import sample_scle_precision
from llm_dup_dev.study_eval.schemas import (
    PairPrediction,
    PredictionSet,
    ReferencePair,
    ReferenceSet,
)


def make_precision(
    spec: list[tuple[str, str]],
    *,
    set_id: str = "p_set",
    screening: dict[str, bool] | None = None,
) -> tuple[ReferenceSet, PredictionSet]:
    """Build a precision set + predictions from ``(reference_label, adj_label)``.

    Pairs are named ``A{i}__B{i}``. ``screening`` defaults to all-True (the
    precision set is the screening method's candidate set).
    """
    pairs = []
    predictions = {}
    for i, (ref, adj) in enumerate(spec):
        pid = f"A{i}__B{i}"
        pairs.append(
            ReferencePair(
                pair_id=pid,
                report_id1=f"A{i}",
                report_id2=f"B{i}",
                cluster_id=f"c{i}",
                reference_label=ref,
            )
        )
        predictions[pid] = PairPrediction(
            pair_id=pid,
            adjudicator_label=adj,
            screening_retrieved=(screening or {}).get(pid, True),
        )
    reference_set = ReferenceSet(set_id=set_id, role="precision", pairs=pairs)
    return reference_set, PredictionSet(predictions=predictions)


def make_recall(
    specs: dict[str, list[tuple[bool, str]]],
) -> tuple[list[ReferenceSet], PredictionSet]:
    """Build recall sets + predictions from ``{set_id: [(screening, adj_label)]}``.

    Pairs are named ``{set_id}_{i}__x``, one per cluster.
    """
    reference_sets = []
    predictions = {}
    for set_id, rows in specs.items():
        pairs = []
        for i, (screened, adj) in enumerate(rows):
            pid = f"{set_id}_{i}__x"
            pairs.append(
                ReferencePair(
                    pair_id=pid,
                    report_id1=f"{set_id}_{i}",
                    report_id2="x",
                    cluster_id=f"{set_id}_c{i}",
                )
            )
            predictions[pid] = PairPrediction(
                pair_id=pid, adjudicator_label=adj, screening_retrieved=screened
            )
        reference_sets.append(
            ReferenceSet(set_id=set_id, role="recall", label=set_id, pairs=pairs)
        )
    return reference_sets, PredictionSet(predictions=predictions)


def build_scle_precision_sample(
    spec: list[tuple[str, str]], *, seed: str = "s", per_cell: int = 10
):
    """Return ``(reference_set, predictions, sample)`` for a precision SCLE draw."""
    reference_set, predictions = make_precision(spec)
    result = run_precision_analysis(reference_set, predictions)
    sample = sample_scle_precision(
        reference_set, result, predictions, per_cell=per_cell, seed=seed
    )
    return reference_set, predictions, sample


def fill_reviewer_workbook(
    path, reviewer: str, *, never_event: str = "no", ref_issue_first: str | None = None
) -> None:
    """Fill an on-disk reviewer workbook with valid answers (for tests)."""
    import openpyxl

    from llm_dup_dev.study_eval.scle.workbook import NOT_APPLICABLE

    wb = openpyxl.load_workbook(path)
    ws = wb["cases"]
    hdr = [c.value for c in ws[1]]
    col = lambda n: hdr.index(n) + 1  # noqa: E731
    for r in range(2, ws.max_row + 1):
        pid = ws.cell(row=r, column=col("Pair ID")).value
        ws.cell(row=r, column=col("Free-text assessment")).value = f"{reviewer}:{pid}"
        ws.cell(row=r, column=col("Case difficulty")).value = "non_trivial"
        rli = ref_issue_first if (ref_issue_first and r == 2) else "no_apparent_issue"
        ws.cell(row=r, column=col("Reference-label issue")).value = rli
        if ws.cell(row=r, column=col("Never event")).value != NOT_APPLICABLE:
            ws.cell(row=r, column=col("Never event")).value = never_event
            ws.cell(row=r, column=col("Unexpected method behaviour")).value = "no"
            ws.cell(row=r, column=col("Material input-data limitation")).value = "no"
            ws.cell(row=r, column=col("Primary source of error")).value = (
                "relationship_assessment"
            )
        md_cell = ws.cell(row=r, column=col("Method-difference explanation"))
        if md_cell.value != NOT_APPLICABLE:
            md_cell.value = "difference explanation"
    wb.save(path)


def fill_arbitration_workbook(path, discrepancy) -> None:
    """Fill an arbitration workbook, resolving each disagreement to reviewer A."""
    import openpyxl

    from llm_dup_dev.study_eval.scle.arbitration import _DIM_HEADER

    wb = openpyxl.load_workbook(path)
    ws = wb["cases"]
    hdr = [c.value for c in ws[1]]
    col = lambda n: hdr.index(n) + 1  # noqa: E731
    disc = {
        p.pair_id: {d.field for d in p.dimension_disagreements}
        for p in discrepancy.pair_discrepancies
    }
    for r in range(2, ws.max_row + 1):
        pid = ws.cell(row=r, column=col("Pair ID")).value
        ws.cell(row=r, column=col("Free-text materially different")).value = "no"
        ws.cell(row=r, column=col("Consensus free-text")).value = "agreed text"
        ws.cell(row=r, column=col("Arbiter rationale")).value = "rationale"
        for field in disc.get(pid, ()):
            a_val = ws.cell(row=r, column=col(f"{_DIM_HEADER[field]} (A)")).value
            ws.cell(row=r, column=col(f"{_DIM_HEADER[field]} consensus")).value = a_val
    wb.save(path)
