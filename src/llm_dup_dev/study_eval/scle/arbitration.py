"""The arbitration workbook and its validated read-back.

The arbiter (the co-author who produced the original reference annotation) works
one workbook covering **all** SCLE cases, with the structurally discrepant ones
flagged and sorted to the top. Each row shows both reviewers' values for every
structured dimension side by side and both free-text assessments; the arbiter
fills a consensus value for the dimensions that disagreed, judges whether the
free-text interpretations differ materially, and writes a consensus free-text
plus a rationale.

As with the reviewer workbook, the read-back is the source of truth and is
validated against the originating sample.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from llm_dup_dev.study_eval.checksums import sha256_canonical_json
from llm_dup_dev.study_eval.sampling import SCLESample
from llm_dup_dev.study_eval.scle.discrepancy import (
    STRUCTURED_DIMENSIONS,
    DiscrepancyReport,
)
from llm_dup_dev.study_eval.scle.form import (
    CaseDifficulty,
    InputDataLimitationCategory,
    PrimaryErrorSource,
    ReferenceLabelIssue,
    ReviewerSubmission,
    YesNoUnclear,
)
from llm_dup_dev.study_eval.scle.workbook import (
    CASES_SHEET,
    METADATA_SHEET,
    _cell_str,
)

# Enum option lists per structured dimension (feature domains are free text).
_FIELD_ENUM: dict[str, type[Enum]] = {
    "case_difficulty": CaseDifficulty,
    "reference_label_issue": ReferenceLabelIssue,
    "never_event": YesNoUnclear,
    "unexpected_method_behaviour": YesNoUnclear,
    "material_input_data_limitation": YesNoUnclear,
    "input_data_limitation_category": InputDataLimitationCategory,
    "primary_error_source": PrimaryErrorSource,
}

_DIM_HEADER = {
    "case_difficulty": "Case difficulty",
    "reference_label_issue": "Reference-label issue",
    "never_event": "Never event",
    "unexpected_method_behaviour": "Unexpected method behaviour",
    "material_input_data_limitation": "Material input-data limitation",
    "input_data_limitation_category": "Input-data limitation category",
    "primary_error_source": "Primary source of error",
    "implicated_feature_domains": "Implicated feature domains",
}

_MATERIALLY_DIFFERENT_HEADER = "Free-text materially different"
_CONSENSUS_FREE_TEXT_HEADER = "Consensus free-text"
_ARBITER_RATIONALE_HEADER = "Arbiter rationale"


class ArbitrationDecision(BaseModel):
    """The arbiter's resolution for one pair.

    ``consensus_dimensions`` carries a value only for the structured dimensions
    the arbiter chose to set (required for those that disagreed; ignored for
    those that already agreed). ``free_text_materially_different`` records the
    arbiter's judgement on the two free-text assessments.
    """

    pair_id: str
    consensus_dimensions: dict[str, str]
    free_text_materially_different: bool
    consensus_free_text: str
    arbiter_rationale: str


class ArbitrationSubmission(BaseModel):
    """The arbiter's complete set of decisions for one component."""

    arbitration_id: str
    arbiter_id: str
    evaluation_component: str
    sample_checksum: str
    decisions: list[ArbitrationDecision]


def _dim_value(assessment, field: str) -> str:
    value = getattr(assessment, field)
    if field == "implicated_feature_domains":
        return ", ".join(sorted(value)) if value else ""
    if value is None:
        return ""
    return getattr(value, "value", str(value))


def write_arbitration_workbook(
    report: DiscrepancyReport,
    sub_a: ReviewerSubmission,
    sub_b: ReviewerSubmission,
    sample: SCLESample,
    path: str | Path,
    *,
    arbiter_id: str = "",
    arbitration_id: str | None = None,
) -> tuple[Path, str]:
    """Write the arbitration workbook for one component.

    All pairs appear; those with a structured disagreement are sorted first and
    flagged. Returns the written path and the arbitration id.
    """
    import openpyxl  # type: ignore[import-untyped]
    from openpyxl.styles import Font, PatternFill  # type: ignore[import-untyped]
    from openpyxl.worksheet.datavalidation import DataValidation  # type: ignore[import-untyped]

    arbitration_id = arbitration_id or uuid.uuid4().hex[:12]
    sample_checksum = sha256_canonical_json(sample)

    ctx_by_pair = {c.pair_id: c.auto_context for c in sample.cases}
    a_by_pair = {a.pair_id: a for a in sub_a.assessments}
    b_by_pair = {b.pair_id: b for b in sub_b.assessments}
    disagree_pairs = {
        p.pair_id for p in report.pair_discrepancies if p.dimension_disagreements
    }

    wb = openpyxl.Workbook()
    meta = wb.active
    meta.title = METADATA_SHEET
    meta_rows = {
        "arbitration_id": arbitration_id,
        "arbiter_id": arbiter_id,
        "evaluation_component": sample.evaluation_component,
        "sample_checksum": sample_checksum,
        "reviewer_a": sub_a.reviewer_id,
        "reviewer_b": sub_b.reviewer_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    meta["A1"], meta["B1"] = "key", "value"
    meta["A1"].font = meta["B1"].font = Font(bold=True)
    for i, (k, v) in enumerate(meta_rows.items(), start=2):
        meta[f"A{i}"], meta[f"B{i}"] = k, v

    ws = wb.create_sheet(CASES_SHEET)

    # Build the header layout.
    headers: list[str] = [
        "Pair ID",
        "Reference label",
        "Adjudicator output",
        "Confusion cell",
        "Joint stratum",
    ]
    consensus_col_of: dict[str, int] = {}
    for field in STRUCTURED_DIMENSIONS:
        h = _DIM_HEADER[field]
        headers.append(f"{h} (A)")
        headers.append(f"{h} (B)")
        headers.append(f"{h} consensus")
        consensus_col_of[field] = len(headers)  # 1-based col index
    headers.extend(
        [
            "Free-text (A)",
            "Free-text (B)",
            _MATERIALLY_DIFFERENT_HEADER,
            _CONSENSUS_FREE_TEXT_HEADER,
            _ARBITER_RATIONALE_HEADER,
        ]
    )
    materially_col = headers.index(_MATERIALLY_DIFFERENT_HEADER) + 1

    grey = PatternFill(start_color="DDDDDD", end_color="DDDDDD", fill_type="solid")
    context_headers = {
        "Pair ID",
        "Reference label",
        "Adjudicator output",
        "Confusion cell",
        "Joint stratum",
        "Free-text (A)",
        "Free-text (B)",
    }
    for c_idx, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=c_idx, value=h)
        cell.font = Font(bold=True)
        if h in context_headers or h.endswith("(A)") or h.endswith("(B)"):
            cell.fill = grey

    # Consensus dropdowns per enum dimension + the materially-different flag.
    dv_by_col: dict[int, "DataValidation"] = {}
    for field, col_idx in consensus_col_of.items():
        enum_cls = _FIELD_ENUM.get(field)
        if enum_cls is None:
            continue
        opts = ",".join(str(m.value) for m in enum_cls)
        dv = DataValidation(type="list", formula1=f'"{opts}"', allow_blank=True)
        ws.add_data_validation(dv)
        dv_by_col[col_idx] = dv
    dv_material = DataValidation(type="list", formula1='"yes,no"', allow_blank=True)
    ws.add_data_validation(dv_material)

    # Focus the workbook on the structurally-disagreeing pairs only; agreement
    # pairs are auto-resolved from the reviews at finalize time.
    ordered = sorted(disagree_pairs)

    for r_idx, pair_id in enumerate(ordered, start=2):
        ctx = ctx_by_pair[pair_id]
        a = a_by_pair.get(pair_id)
        b = b_by_pair.get(pair_id)
        row = {
            "Pair ID": pair_id,
            "Reference label": ctx.reference_label,
            "Adjudicator output": ctx.adjudicator_final_label,
            "Confusion cell": ctx.confusion_cell or "",
            "Joint stratum": ctx.joint_stratum or "",
            "Free-text (A)": a.free_text_assessment if a else "",
            "Free-text (B)": b.free_text_assessment if b else "",
        }
        for field in STRUCTURED_DIMENSIONS:
            h = _DIM_HEADER[field]
            row[f"{h} (A)"] = _dim_value(a, field) if a else ""
            row[f"{h} (B)"] = _dim_value(b, field) if b else ""
        for c_idx, h in enumerate(headers, start=1):
            if h in row:
                cell = ws.cell(row=r_idx, column=c_idx, value=row[h])
                if h in context_headers or h.endswith("(A)") or h.endswith("(B)"):
                    cell.fill = grey
        # Attach dropdowns to consensus + materially-different cells.
        for col_idx, dv in dv_by_col.items():
            dv.add(ws.cell(row=r_idx, column=col_idx))
        dv_material.add(ws.cell(row=r_idx, column=materially_col))

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out, arbitration_id


def read_arbitration(path: str | Path, sample: SCLESample) -> ArbitrationSubmission:
    """Read and validate an arbitration workbook against its ``sample``."""
    import openpyxl  # type: ignore[import-untyped]

    wb = openpyxl.load_workbook(path, data_only=True)
    if METADATA_SHEET not in wb.sheetnames or CASES_SHEET not in wb.sheetnames:
        raise ValueError(
            f"{path}: workbook must contain '{METADATA_SHEET}' and "
            f"'{CASES_SHEET}' sheets."
        )
    meta = wb[METADATA_SHEET]
    meta_map: dict[str, str] = {}
    for r in meta.iter_rows(min_row=2, values_only=True):
        if r and r[0] is not None:
            meta_map[str(r[0])] = _cell_str(r[1])

    expected = sha256_canonical_json(sample)
    if meta_map.get("sample_checksum") != expected:
        raise ValueError(
            f"{path}: sample_checksum does not match the provided sample."
        )
    if meta_map.get("evaluation_component") != sample.evaluation_component:
        raise ValueError(f"{path}: component mismatch with sample.")

    ws = wb[CASES_SHEET]
    rows = list(ws.iter_rows(values_only=True))
    header = [_cell_str(h) for h in rows[0]]
    idx = {h: i for i, h in enumerate(header)}

    sample_pairs = {c.pair_id for c in sample.cases}
    decisions: list[ArbitrationDecision] = []
    problems: list[str] = []

    for row in rows[1:]:
        if row is None or all(v is None for v in row):
            continue
        pair_id = _cell_str(row[idx["Pair ID"]]) if "Pair ID" in idx else ""
        if not pair_id:
            continue
        if pair_id not in sample_pairs:
            problems.append(f"{path}: pair {pair_id!r} is not in the sample.")
            continue

        consensus: dict[str, str] = {}
        for field in STRUCTURED_DIMENSIONS:
            col = f"{_DIM_HEADER[field]} consensus"
            if col in idx:
                val = _cell_str(row[idx[col]])
                if val:
                    consensus[field] = val

        materially = _cell_str(row[idx.get(_MATERIALLY_DIFFERENT_HEADER, -1)]).lower() \
            if _MATERIALLY_DIFFERENT_HEADER in idx else ""
        consensus_free_text = (
            _cell_str(row[idx[_CONSENSUS_FREE_TEXT_HEADER]])
            if _CONSENSUS_FREE_TEXT_HEADER in idx
            else ""
        )
        rationale = (
            _cell_str(row[idx[_ARBITER_RATIONALE_HEADER]])
            if _ARBITER_RATIONALE_HEADER in idx
            else ""
        )
        decisions.append(
            ArbitrationDecision(
                pair_id=pair_id,
                consensus_dimensions=consensus,
                free_text_materially_different=(materially == "yes"),
                consensus_free_text=consensus_free_text,
                arbiter_rationale=rationale,
            )
        )

    # The arbitration workbook is disagreement-focused, so it need not cover
    # every sample pair — only rows for pairs outside the sample are an error
    # (checked in the loop above).
    if problems:
        raise ValueError(
            f"{path}: arbitration workbook failed validation:\n"
            + "\n".join(f"  - {p}" for p in problems)
        )

    return ArbitrationSubmission(
        arbitration_id=meta_map.get("arbitration_id", ""),
        arbiter_id=meta_map.get("arbiter_id", ""),
        evaluation_component=sample.evaluation_component,
        sample_checksum=expected,
        decisions=decisions,
    )


__all__ = [
    "ArbitrationDecision",
    "ArbitrationSubmission",
    "write_arbitration_workbook",
    "read_arbitration",
]
