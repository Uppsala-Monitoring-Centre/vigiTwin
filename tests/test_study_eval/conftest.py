"""Synthetic builders and fixtures for the study_eval test suite.

Everything here is synthetic; no real reports or database access. The builders
let each test dial in exactly the label / retrieval distribution it needs.
"""

from __future__ import annotations

import pytest

from llm_dup_dev.study_eval.aggregate import run_precision_analysis
from llm_dup_dev.study_eval.sampling import sample_scle_precision
from llm_dup_dev.study_eval.schemas import (
    AdjudicatorOutput,
    MethodOutputs,
    PrecisionAnalysisInput,
    PrecisionEvaluationSet,
    PrecisionPairRecord,
    RecallAnalysisInput,
    RecallEvaluationSet,
    RecallPairRecord,
)


def make_precision_input(
    spec: list[tuple[str, str]],
    *,
    set_id: str = "p_set",
    vigimatch: dict[str, bool] | None = None,
) -> PrecisionAnalysisInput:
    """Build a precision analysis input from ``(reference_label, final_label)``.

    Pairs are named ``A{i}__B{i}``. ``vigimatch`` defaults to all-True (the
    precision set is the screening method's candidate set).
    """
    pairs = []
    adjudicator = {}
    for i, (ref, final) in enumerate(spec):
        pid = f"A{i}__B{i}"
        pairs.append(
            PrecisionPairRecord(
                pair_id=pid,
                report_id1=f"A{i}",
                report_id2=f"B{i}",
                cluster_id=f"c{i}",
                reference_label=ref,
            )
        )
        adjudicator[pid] = AdjudicatorOutput(pair_id=pid, final_label=final)
    vig = vigimatch or {pid: True for pid in adjudicator}
    return PrecisionAnalysisInput(
        evaluation_set=PrecisionEvaluationSet(set_id=set_id, pairs=pairs),
        outputs=MethodOutputs(adjudicator=adjudicator, vigimatch_retrieved=vig),
    )


def make_recall_input(
    specs: dict[str, list[tuple[bool, str]]],
) -> RecallAnalysisInput:
    """Build a recall analysis input from ``{set_id: [(vigimatch, final_label)]}``.

    Pairs are named ``{set_id}_{i}__x``, one per cluster.
    """
    eval_sets = []
    adjudicator = {}
    vig = {}
    for set_id, rows in specs.items():
        pairs = []
        for i, (retrieved, final) in enumerate(rows):
            pid = f"{set_id}_{i}__x"
            pairs.append(
                RecallPairRecord(
                    pair_id=pid,
                    report_id1=f"{set_id}_{i}",
                    report_id2="x",
                    cluster_id=f"{set_id}_c{i}",
                )
            )
            adjudicator[pid] = AdjudicatorOutput(pair_id=pid, final_label=final)
            vig[pid] = retrieved
        eval_sets.append(
            RecallEvaluationSet(
                reference_set_id=set_id if set_id in {
                    "fda_gold", "fda_silver", "aemps", "mhra", "lareb"
                } else "fda_gold",
                reference_set_label=set_id,
                pairs=pairs,
            )
        )
    return RecallAnalysisInput(
        evaluation_sets=eval_sets,
        outputs=MethodOutputs(adjudicator=adjudicator, vigimatch_retrieved=vig),
    )


def build_scle_precision_sample(
    spec: list[tuple[str, str]], *, seed: str = "s", per_cell: int = 10
):
    """Return ``(input, result, sample)`` for a precision SCLE draw."""
    inp = make_precision_input(spec)
    result = run_precision_analysis(inp)
    sample = sample_scle_precision(inp, result, per_cell=per_cell, seed=seed)
    return inp, result, sample


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


def fill_arbitration_workbook(path, report) -> None:
    """Fill an arbitration workbook, resolving each disagreement to reviewer A."""
    import openpyxl

    from llm_dup_dev.study_eval.scle.arbitration import _DIM_HEADER

    wb = openpyxl.load_workbook(path)
    ws = wb["cases"]
    hdr = [c.value for c in ws[1]]
    col = lambda n: hdr.index(n) + 1  # noqa: E731
    disc = {
        p.pair_id: {d.field for d in p.dimension_disagreements}
        for p in report.pair_discrepancies
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


@pytest.fixture
def precision_input() -> PrecisionAnalysisInput:
    # 2 TP, 1 FP, 1 FN, 1 TN, 1 reference-abstain.
    return make_precision_input(
        [
            ("duplicate", "Duplicates"),  # TP
            ("duplicate", "Duplicates"),  # TP
            ("non_duplicate", "Duplicates"),  # FP
            ("duplicate", "Otherwise_related"),  # FN
            ("non_duplicate", "Unrelated"),  # TN
            ("abstain", "Duplicates"),  # excluded
        ]
    )
