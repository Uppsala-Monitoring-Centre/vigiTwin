"""The arbitration workbook and its validated read-back.

The arbiter (the co-author who produced the original reference annotation) works
one workbook covering the structurally discrepant SCLE pairs, on a ``pairs``
sheet laid out like the reviewers'. Both free-text assessments come first, so the
arbiter reads what each reviewer said before judging any single answer; then each
structured dimension appears as the two reviewers' values side by side plus a
consensus cell. The arbiter fills a consensus value for the dimensions that
disagreed, judges whether the free-text interpretations differ materially, and
writes a consensus free-text, a consensus method-difference, and a rationale
covering both free-text blocks.

Consensus cells carry the same fill vocabulary as the reviewer workbook: a
dimension the two reviewers already agreed on is **pre-filled** with the settled
value and shaded as do-not-edit context, one that never applied to the pair reads
``N/A``, and only a cell that genuinely needs a decision is left blank and
shaded. Editing a pre-filled cell has no effect on the outcome — agreed
dimensions are resolved from the reviews — so ``merge_consensus`` raises rather
than dropping such an edit silently.

As with the reviewer workbook, the read-back is the source of truth and is
validated against the originating sample.

Which structured dimensions exist depends on the study's error-stage list, so the
dimension set is taken from the :class:`DiscrepancyReport` when the workbook is
written and embedded in a sheet of its own, letting the read-back recover it
without being handed the configuration again.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from llm_dup_dev.study_eval.checksums import sha256_canonical_json
from llm_dup_dev.study_eval.sampling import SCLESample
from llm_dup_dev.study_eval.scle.discrepancy import DiscrepancyReport
from llm_dup_dev.study_eval.scle.form import (
    CaseDifficulty,
    ReferenceLabelIssue,
    ReviewerSubmission,
    SCLEAutoContext,
    YesNo,
    applicable_dimensions,
    error_stage_name,
)
from llm_dup_dev.study_eval.scle.vocabularies import (
    read_vocabulary_sheet,
    write_vocabulary_sheet,
)
from llm_dup_dev.study_eval.scle.workbook import (
    CONTEXT_FILL_RGB,
    METADATA_SHEET,
    NOT_APPLICABLE,
    NOT_APPLICABLE_FILL_RGB,
    PAIRS_SHEET,
    REQUIRED_FILL_RGB,
    STAGE_HEADER_PREFIX,
    _DEFAULT_WIDTH,
    _PAIR_ID_HEADER,
    _PAIR_ID_WIDTH,
    _PROSE_WIDTH,
    _alignments,
    _cell_str,
    _fill,
    _set_packet_hyperlink,
    _style_sheet,
)

#: Sheet recording the structured dimensions this arbitration covers.
DIMENSIONS_SHEET = "Dimensions"

# Enum option lists per fixed structured dimension (feature domains are free
# text; every per-stage dimension is yes/no).
_FIXED_FIELD_ENUM: dict[str, type[Enum]] = {
    "case_difficulty": CaseDifficulty,
    "reference_label_issue": ReferenceLabelIssue,
    "never_event": YesNo,
    "unexpected_method_behaviour": YesNo,
    "incorrect_or_imprecise_input_data": YesNo,
}

_FIXED_DIM_HEADER = {
    "case_difficulty": "Difficulty",
    "reference_label_issue": "Reference-label issue",
    "never_event": "Never event",
    "unexpected_method_behaviour": "Unexpected method behaviour",
    "incorrect_or_imprecise_input_data": "Incorrect or imprecise input data",
    "implicated_feature_domains": "Implicated feature domains",
}


def _field_enum(dimension: str) -> type[Enum] | None:
    """The dropdown vocabulary for a dimension, or ``None`` where it is free text."""
    if error_stage_name(dimension) is not None:
        return YesNo
    return _FIXED_FIELD_ENUM.get(dimension)


def _dim_header(dimension: str) -> str:
    """The reviewer-facing name of a dimension, mirroring the reviewer workbook."""
    stage = error_stage_name(dimension)
    if stage is not None:
        return f"{STAGE_HEADER_PREFIX}{stage}"
    try:
        return _FIXED_DIM_HEADER[dimension]
    except KeyError:  # pragma: no cover - guards a mis-wired dimension list
        raise ValueError(
            f"no arbitration header defined for structured dimension "
            f"{dimension!r}."
        ) from None


_MATERIALLY_DIFFERENT_HEADER = "Free-text materially different"
_CONSENSUS_FREE_TEXT_HEADER = "Consensus free-text"
_ARBITER_RATIONALE_HEADER = "Arbiter rationale"
_METHOD_DIFF_A_HEADER = "Method-difference (A)"
_METHOD_DIFF_B_HEADER = "Method-difference (B)"
_CONSENSUS_METHOD_DIFF_HEADER = "Consensus method-difference"

#: Columns holding sentences rather than labels, mirroring the reviewer
#: workbook's prose columns: left-aligned, with room to wrap.
_PROSE_HEADERS: frozenset[str] = frozenset(
    {
        "Free-text (A)",
        "Free-text (B)",
        _METHOD_DIFF_A_HEADER,
        _METHOD_DIFF_B_HEADER,
        _CONSENSUS_METHOD_DIFF_HEADER,
        _CONSENSUS_FREE_TEXT_HEADER,
        _ARBITER_RATIONALE_HEADER,
    }
)


def _dimension_applies(dimension: str, ctx: SCLEAutoContext) -> bool:
    """Whether a structured dimension was asked of this pair at all.

    Taken from the auto-context rather than from either reviewer's answer, which
    cannot tell "the question did not apply" apart from "nothing was ticked".
    The per-stage dimensions all follow the one ``error_stages`` field.
    """
    field = "error_stages" if error_stage_name(dimension) is not None else dimension
    return field in applicable_dimensions(ctx)


def _arbitration_width(header: str) -> int:
    if header in _PROSE_HEADERS:
        return _PROSE_WIDTH
    if header == _PAIR_ID_HEADER:
        return _PAIR_ID_WIDTH
    return _DEFAULT_WIDTH


class ArbitrationDecision(BaseModel):
    """The arbiter's resolution for one pair.

    ``consensus_dimensions`` carries a value only for the structured dimensions
    the arbiter chose to set (required for those that disagreed; ignored for
    those that already agreed, where the workbook pre-fills the agreed value as
    read-only context). ``free_text_materially_different`` records the arbiter's
    judgement on the two free-text assessments.

    ``consensus_method_difference`` is the arbiter's resolution of the two
    method-difference explanations. It has no materially-different flag and no
    rationale of its own: ``arbiter_rationale`` covers both free-text blocks.
    """

    pair_id: str
    consensus_dimensions: dict[str, str]
    free_text_materially_different: bool
    consensus_free_text: str
    arbiter_rationale: str
    consensus_method_difference: str = ""


class ArbitrationSubmission(BaseModel):
    """The arbiter's complete set of decisions for one component.

    ``structured_dimensions`` is the dimension set the workbook was written for,
    recovered from the workbook itself on read-back.
    """

    arbitration_id: str
    arbiter_id: str
    evaluation_component: str
    sample_checksum: str
    structured_dimensions: list[str] = []
    decisions: list[ArbitrationDecision]


def _dim_value(assessment, field: str) -> str:
    """Render one reviewer's answer on a dimension for the side-by-side columns."""
    stage = error_stage_name(field)
    if stage is not None:
        if not assessment.misclassification_answered:
            return ""
        return YesNo.YES.value if stage in assessment.error_stages else YesNo.NO.value
    if field == "implicated_feature_domains":
        return ", ".join(sorted(assessment.implicated_feature_domains))
    value = getattr(assessment, field)
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
    packet_targets: dict[str, str] | None = None,
) -> tuple[Path, str]:
    """Write the arbitration workbook for one component.

    Only the structurally-disagreeing pairs appear. Each row's pair id links to
    the pair's HTML review file when a target is supplied in ``packet_targets``.
    The dimension set comes from ``report`` and is embedded in the
    :data:`DIMENSIONS_SHEET` sheet. Returns the written path and the arbitration
    id.
    """
    import openpyxl  # type: ignore[import-untyped]
    from openpyxl.styles import Font, PatternFill  # type: ignore[import-untyped]
    from openpyxl.worksheet.datavalidation import DataValidation  # type: ignore[import-untyped]

    arbitration_id = arbitration_id or uuid.uuid4().hex[:12]
    sample_checksum = sha256_canonical_json(sample)
    dimensions: Sequence[str] = report.structured_dimensions

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

    write_vocabulary_sheet(wb, DIMENSIONS_SHEET, dimensions, Font)

    ws = wb.create_sheet(PAIRS_SHEET)

    # Focus the workbook on the structurally-disagreeing pairs only; agreement
    # pairs are auto-resolved from the reviews at finalize time.
    ordered = sorted(disagree_pairs)

    # Build the header layout. The pair id cell carries the packet link, so
    # there is no packet column; a stratum column none of the arbitrated pairs
    # populates is left out, as in the reviewer workbook.
    headers: list[str] = [_PAIR_ID_HEADER, "Reference label", "Adjudicator output"]
    for field, header in (
        ("confusion_cell", "Confusion cell"),
        ("joint_stratum", "Joint stratum"),
    ):
        if any(getattr(ctx_by_pair[pair_id], field) for pair_id in ordered):
            headers.append(header)
    # Both free-text assessments sit before the first dimension, so the arbiter
    # reads what each reviewer actually said before judging any single answer.
    headers.extend(["Free-text (A)", "Free-text (B)"])
    consensus_col_of: dict[str, int] = {}
    for field in dimensions:
        h = _dim_header(field)
        headers.append(f"{h} (A)")
        headers.append(f"{h} (B)")
        headers.append(f"{h} consensus")
        consensus_col_of[field] = len(headers)  # 1-based col index
    headers.extend(
        [
            _METHOD_DIFF_A_HEADER,
            _METHOD_DIFF_B_HEADER,
            _CONSENSUS_METHOD_DIFF_HEADER,
            _MATERIALLY_DIFFERENT_HEADER,
            _CONSENSUS_FREE_TEXT_HEADER,
            _ARBITER_RATIONALE_HEADER,
        ]
    )
    materially_col = headers.index(_MATERIALLY_DIFFERENT_HEADER) + 1

    grey = _fill(PatternFill, CONTEXT_FILL_RGB)
    na_fill = _fill(PatternFill, NOT_APPLICABLE_FILL_RGB)
    required_fill = _fill(PatternFill, REQUIRED_FILL_RGB)
    context_headers = {
        _PAIR_ID_HEADER,
        "Reference label",
        "Adjudicator output",
        "Confusion cell",
        "Joint stratum",
        "Free-text (A)",
        "Free-text (B)",
    }
    field_by_consensus_col = {col: field for field, col in consensus_col_of.items()}
    disagreed_by_pair = {
        p.pair_id: {d.field for d in p.dimension_disagreements}
        for p in report.pair_discrepancies
    }
    header_align, centre, left = _alignments()
    for c_idx, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=c_idx, value=h)
        cell.font = Font(bold=True)
        cell.alignment = header_align
        if h in context_headers or h.endswith("(A)") or h.endswith("(B)"):
            cell.fill = grey

    # Consensus dropdowns per enum dimension + the materially-different flag.
    dv_by_col: dict[int, "DataValidation"] = {}
    for field, col_idx in consensus_col_of.items():
        enum_cls = _field_enum(field)
        if enum_cls is None:
            continue
        opts = ",".join(str(m.value) for m in enum_cls)
        dv = DataValidation(type="list", formula1=f'"{opts}"', allow_blank=True)
        ws.add_data_validation(dv)
        dv_by_col[col_idx] = dv
    dv_material = DataValidation(type="list", formula1='"yes,no"', allow_blank=True)
    ws.add_data_validation(dv_material)

    for r_idx, pair_id in enumerate(ordered, start=2):
        ctx = ctx_by_pair[pair_id]
        a = a_by_pair.get(pair_id)
        b = b_by_pair.get(pair_id)
        target = (packet_targets or {}).get(pair_id)
        disagreed = disagreed_by_pair.get(pair_id, set())
        row = {
            _PAIR_ID_HEADER: pair_id,
            "Reference label": ctx.reference_label,
            "Adjudicator output": ctx.adjudicator_final_label,
            "Confusion cell": ctx.confusion_cell or "",
            "Joint stratum": ctx.joint_stratum or "",
            "Free-text (A)": a.free_text_assessment if a else "",
            "Free-text (B)": b.free_text_assessment if b else "",
        }
        for field in dimensions:
            h = _dim_header(field)
            if not _dimension_applies(field, ctx):
                # Never asked of this pair, so neither reviewer answered it and
                # there is nothing to resolve. Say so, rather than leaving three
                # blanks that read as unanswered questions.
                row[f"{h} (A)"] = NOT_APPLICABLE
                row[f"{h} (B)"] = NOT_APPLICABLE
                row[f"{h} consensus"] = NOT_APPLICABLE
                continue
            row[f"{h} (A)"] = _dim_value(a, field) if a else ""
            row[f"{h} (B)"] = _dim_value(b, field) if b else ""
            if field not in disagreed:
                # Already settled between the reviewers: show the agreed value as
                # read-only context so only the cells needing a decision are
                # blank. Editing it here has no effect and `merge_consensus`
                # raises rather than dropping the change silently.
                row[f"{h} consensus"] = _dim_value(a, field) if a else ""

        if _dimension_applies("method_difference_explanation", ctx):
            row[_METHOD_DIFF_A_HEADER] = (a.method_difference_explanation or "") if a else ""
            row[_METHOD_DIFF_B_HEADER] = (b.method_difference_explanation or "") if b else ""
        else:
            row[_METHOD_DIFF_A_HEADER] = NOT_APPLICABLE
            row[_METHOD_DIFF_B_HEADER] = NOT_APPLICABLE
            row[_CONSENSUS_METHOD_DIFF_HEADER] = NOT_APPLICABLE

        for c_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=r_idx, column=c_idx)
            cell.alignment = left if h in _PROSE_HEADERS else centre
            if h in row:
                if h in context_headers or h.endswith("(A)") or h.endswith("(B)"):
                    cell.fill = grey
                if h == _PAIR_ID_HEADER and target:
                    _set_packet_hyperlink(cell, target, text=pair_id)
                else:
                    cell.value = row[h]
            if row.get(h) == NOT_APPLICABLE:
                cell.fill = na_fill
                continue
            consensus_field = field_by_consensus_col.get(c_idx)
            if consensus_field is None:
                continue
            if consensus_field in disagreed:
                cell.fill = required_fill
                if c_idx in dv_by_col:
                    dv_by_col[c_idx].add(cell)
            else:
                cell.fill = grey
        dv_material.add(ws.cell(row=r_idx, column=materially_col))

    _style_sheet(ws, [_arbitration_width(h) for h in headers])

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out, arbitration_id


def _row_cell(row: tuple, idx: dict[str, int], header: str) -> str | None:
    """The raw entry in one column of one row; ``None`` where the column is absent."""
    if header not in idx:
        return None
    return _cell_str(row[idx[header]])


def _row_answer(row: tuple, idx: dict[str, int], header: str) -> str:
    """The arbiter's entry in one column, ignoring the N/A sentinel."""
    val = _row_cell(row, idx, header)
    return "" if val is None or val == NOT_APPLICABLE else val


def read_arbitration(path: str | Path, sample: SCLESample) -> ArbitrationSubmission:
    """Read and validate an arbitration workbook against its ``sample``."""
    import openpyxl  # type: ignore[import-untyped]

    wb = openpyxl.load_workbook(path, data_only=True)
    if METADATA_SHEET not in wb.sheetnames or PAIRS_SHEET not in wb.sheetnames:
        raise ValueError(
            f"{path}: workbook must contain '{METADATA_SHEET}' and "
            f"'{PAIRS_SHEET}' sheets."
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

    dimensions = (
        read_vocabulary_sheet(wb[DIMENSIONS_SHEET])
        if DIMENSIONS_SHEET in wb.sheetnames
        else ()
    )

    ws = wb[PAIRS_SHEET]
    rows = list(ws.iter_rows(values_only=True))
    header = [_cell_str(h) for h in rows[0]]
    idx = {h: i for i, h in enumerate(header)}

    sample_pairs = {c.pair_id for c in sample.cases}
    decisions: list[ArbitrationDecision] = []
    problems: list[str] = []

    for row in rows[1:]:
        if row is None or all(v is None for v in row):
            continue
        pair_id = (
            _cell_str(row[idx[_PAIR_ID_HEADER]]) if _PAIR_ID_HEADER in idx else ""
        )
        if not pair_id:
            continue
        if pair_id not in sample_pairs:
            problems.append(f"{path}: pair {pair_id!r} is not in the sample.")
            continue

        consensus: dict[str, str] = {}
        for field in dimensions:
            val = _row_cell(row, idx, f"{_dim_header(field)} consensus")
            if val is None or val == NOT_APPLICABLE:
                # No such column, or the dimension never applied to this pair:
                # there is no decision to carry either way.
                continue
            if val:
                consensus[field] = val
            elif field == "implicated_feature_domains":
                # The one set-valued dimension: "no domains implicated" is a real
                # answer, and the workbook can only render it as a blank cell.
                # Keeping the key makes that decision explicit, so consensus
                # construction resolves it to an empty list rather than reporting
                # the arbitration as missing. For every other dimension a blank
                # still means undecided and must fail validation downstream.
                consensus[field] = val

        materially = _row_answer(row, idx, _MATERIALLY_DIFFERENT_HEADER).lower()
        decisions.append(
            ArbitrationDecision(
                pair_id=pair_id,
                consensus_dimensions=consensus,
                free_text_materially_different=(materially == "yes"),
                consensus_free_text=_row_answer(
                    row, idx, _CONSENSUS_FREE_TEXT_HEADER
                ),
                arbiter_rationale=_row_answer(row, idx, _ARBITER_RATIONALE_HEADER),
                consensus_method_difference=_row_answer(
                    row, idx, _CONSENSUS_METHOD_DIFF_HEADER
                ),
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
        structured_dimensions=list(dimensions),
        decisions=decisions,
    )


__all__ = [
    "DIMENSIONS_SHEET",
    "ArbitrationDecision",
    "ArbitrationSubmission",
    "write_arbitration_workbook",
    "read_arbitration",
]
