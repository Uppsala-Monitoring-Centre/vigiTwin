"""Reviewer workbooks: XLSX generation and validated read-back.

Each reviewer receives an identical workbook: a ``cases`` sheet with one row per
sampled pair, an auto-context block on the left (filled by the package, shown
greyed) and a response block on the right (dropdown-validated where the
dimension is an enum). Dimensions that do not apply to a case — the
misclassification block on a correctly classified pair, the method-difference
note where the methods agree — are pre-filled with the ``not_applicable``
sentinel and greyed.

Excel cell protection is only advisory, so the **read-back is the source of
truth**: :func:`read_reviewer_submission` re-validates the whole submission
against the originating :class:`SCLESample` — vocabularies, per-dimension
applicability, pair coverage, and the embedded sample checksum — and raises on
any discrepancy rather than trusting the workbook.

openpyxl is an optional dependency (the ``study`` extra). Importing this module
without it raises a clear message.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from llm_dup_dev.study_eval.checksums import sha256_canonical_json
from llm_dup_dev.study_eval.sampling import SCLESample
from llm_dup_dev.study_eval.scle.form import (
    CaseDifficulty,
    InputDataLimitationCategory,
    PrimaryErrorSource,
    ReferenceLabelIssue,
    ReviewerSubmission,
    SCLEAutoContext,
    SCLECaseAssessment,
    YesNoUnclear,
    check_assessment_against_context,
)


def _require_openpyxl():
    try:
        import openpyxl  # type: ignore[import-untyped]  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised only without extra
        raise ImportError(
            "Reviewer workbooks require openpyxl. Install the study extra: "
            "pip install 'llm-dup-dev[study]'."
        ) from exc
    return __import__("openpyxl")


#: Sentinel written into response cells that do not apply to a case.
NOT_APPLICABLE = "not_applicable"

CASES_SHEET = "cases"
METADATA_SHEET = "metadata"
INSTRUCTIONS_SHEET = "instructions"


@dataclass(frozen=True)
class _Col:
    """One workbook column: an auto-context field or a reviewer response."""

    field: str
    header: str
    kind: str  # "context" | "text" | "enum"
    options: tuple[str, ...] | None = None
    applies: str | None = None  # None (always) | "misclassified" | "disagreement"


def _enum_options(enum_cls) -> tuple[str, ...]:
    return tuple(m.value for m in enum_cls)


# Auto-context columns (left block, greyed, filled by the package).
_CONTEXT_COLS: tuple[_Col, ...] = (
    _Col("pair_id", "Pair ID", "context"),
    _Col("evaluation_component", "Component", "context"),
    _Col("reference_set_id", "Reference set", "context"),
    _Col("reference_label", "Reference label", "context"),
    _Col("adjudicator_final_label", "Adjudicator output", "context"),
    _Col("screening_retrieved", "Screening retrieved", "context"),
    _Col("confusion_cell", "Confusion cell", "context"),
    _Col("joint_stratum", "Joint stratum", "context"),
    _Col("methods_disagree", "Methods disagree", "context"),
    _Col("is_misclassified", "Misclassified", "context"),
    _Col("decision_trace_summary", "Decision trace", "context"),
    _Col("packet_filename", "Packet", "context"),
)

# Reviewer response columns (right block).
_RESPONSE_COLS: tuple[_Col, ...] = (
    _Col("free_text_assessment", "Free-text assessment", "text"),
    _Col("case_difficulty", "Case difficulty", "enum", _enum_options(CaseDifficulty)),
    _Col(
        "reference_label_issue",
        "Reference-label issue",
        "enum",
        _enum_options(ReferenceLabelIssue),
    ),
    _Col(
        "never_event",
        "Never event",
        "enum",
        _enum_options(YesNoUnclear),
        applies="misclassified",
    ),
    _Col(
        "unexpected_method_behaviour",
        "Unexpected method behaviour",
        "enum",
        _enum_options(YesNoUnclear),
        applies="misclassified",
    ),
    _Col(
        "material_input_data_limitation",
        "Material input-data limitation",
        "enum",
        _enum_options(YesNoUnclear),
        applies="misclassified",
    ),
    _Col(
        "input_data_limitation_category",
        "Input-data limitation category",
        "enum",
        _enum_options(InputDataLimitationCategory),
        applies="misclassified",
    ),
    _Col(
        "primary_error_source",
        "Primary source of error",
        "enum",
        _enum_options(PrimaryErrorSource),
        applies="misclassified",
    ),
    _Col(
        "implicated_feature_domains",
        "Implicated feature domains (comma-separated)",
        "text",
        applies="misclassified",
    ),
    _Col(
        "method_difference_explanation",
        "Method-difference explanation",
        "text",
        applies="disagreement",
    ),
)

_ALL_COLS: tuple[_Col, ...] = _CONTEXT_COLS + _RESPONSE_COLS


def _applies_to(col: _Col, ctx: SCLEAutoContext) -> bool:
    """Whether a response column applies to a case, given its auto-context."""
    if col.applies is None:
        return True
    if col.applies == "misclassified":
        return ctx.is_misclassified
    if col.applies == "disagreement":
        return ctx.methods_disagree
    return True


def _context_value(field: str, ctx: SCLEAutoContext, packet_name: str | None) -> str:
    if field == "packet_filename":
        return packet_name or (ctx.packet_filename or "")
    value = getattr(ctx, field)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def write_reviewer_workbook(
    sample: SCLESample,
    reviewer_id: str,
    path: str | Path,
    *,
    packet_filenames: dict[str, str] | None = None,
    workbook_id: str | None = None,
) -> tuple[Path, str]:
    """Write one reviewer's workbook for ``sample`` to ``path``.

    Returns the written path and the ``workbook_id`` (generated if not given).
    The sample checksum embedded in the metadata sheet ties the workbook to the
    exact sampled case list. Issue two workbooks (one per reviewer) from the
    same ``sample`` for the two independent examinations.
    """
    openpyxl = _require_openpyxl()
    from openpyxl.styles import Font, PatternFill  # type: ignore[import-untyped]
    from openpyxl.worksheet.datavalidation import DataValidation  # type: ignore[import-untyped]

    workbook_id = workbook_id or uuid.uuid4().hex[:12]
    sample_checksum = sha256_canonical_json(sample)
    packet_filenames = packet_filenames or {}

    wb = openpyxl.Workbook()

    meta = wb.active
    meta.title = METADATA_SHEET
    meta_rows = {
        "workbook_id": workbook_id,
        "reviewer_id": reviewer_id,
        "evaluation_component": sample.evaluation_component,
        "sample_checksum": sample_checksum,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "n_cases": str(len(sample.cases)),
    }
    meta["A1"] = "key"
    meta["B1"] = "value"
    meta["A1"].font = meta["B1"].font = Font(bold=True)
    for i, (k, v) in enumerate(meta_rows.items(), start=2):
        meta[f"A{i}"] = k
        meta[f"B{i}"] = v

    instr = wb.create_sheet(INSTRUCTIONS_SHEET)
    for i, line in enumerate(_INSTRUCTION_LINES, start=1):
        instr[f"A{i}"] = line

    grey = PatternFill(start_color="DDDDDD", end_color="DDDDDD", fill_type="solid")
    na_fill = PatternFill(start_color="F0F0F0", end_color="F0F0F0", fill_type="solid")
    ws = wb.create_sheet(CASES_SHEET)

    # Header row.
    for c_idx, col in enumerate(_ALL_COLS, start=1):
        cell = ws.cell(row=1, column=c_idx, value=col.header)
        cell.font = Font(bold=True)
        if col.kind == "context":
            cell.fill = grey

    # One dropdown validation per enum response column, added once and applied
    # to the applicable cells in that column.
    validations: dict[str, DataValidation] = {}
    for col in _RESPONSE_COLS:
        if col.kind == "enum" and col.options:
            dv = DataValidation(
                type="list",
                formula1='"' + ",".join(col.options) + '"',
                allow_blank=True,
                showDropDown=False,
            )
            ws.add_data_validation(dv)
            validations[col.field] = dv

    for r_idx, case in enumerate(sample.cases, start=2):
        ctx = case.auto_context
        packet_name = packet_filenames.get(case.pair_id)
        for c_idx, col in enumerate(_ALL_COLS, start=1):
            cell = ws.cell(row=r_idx, column=c_idx)
            if col.kind == "context":
                cell.value = _context_value(col.field, ctx, packet_name)
                cell.fill = grey
            elif not _applies_to(col, ctx):
                cell.value = NOT_APPLICABLE
                cell.fill = na_fill
            elif col.field in validations:
                validations[col.field].add(cell)

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out, workbook_id


def _parse_domains(raw: str) -> list[str]:
    if not raw:
        return []
    parts = raw.replace(";", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


def _cell_str(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def read_reviewer_submission(
    path: str | Path,
    sample: SCLESample,
) -> ReviewerSubmission:
    """Read and fully validate a reviewer workbook against its ``sample``.

    Raises ``ValueError`` on any of: checksum mismatch, wrong component, missing
    or extra pairs, an edited ``not_applicable`` cell, an out-of-vocabulary
    value, or a dimension filled where it does not apply (or left blank where it
    does). The returned :class:`ReviewerSubmission` is trustworthy.
    """
    openpyxl = _require_openpyxl()

    wb = openpyxl.load_workbook(path, data_only=True)
    if METADATA_SHEET not in wb.sheetnames or CASES_SHEET not in wb.sheetnames:
        raise ValueError(
            f"{path}: workbook must contain '{METADATA_SHEET}' and "
            f"'{CASES_SHEET}' sheets."
        )

    meta = wb[METADATA_SHEET]
    meta_map: dict[str, str] = {}
    for row in meta.iter_rows(min_row=2, values_only=True):
        if row and row[0] is not None:
            meta_map[str(row[0])] = _cell_str(row[1])

    expected_checksum = sha256_canonical_json(sample)
    if meta_map.get("sample_checksum") != expected_checksum:
        raise ValueError(
            f"{path}: sample_checksum {meta_map.get('sample_checksum')!r} does "
            f"not match the provided sample ({expected_checksum!r}). The "
            f"workbook was generated from a different sample."
        )
    if meta_map.get("evaluation_component") != sample.evaluation_component:
        raise ValueError(
            f"{path}: component {meta_map.get('evaluation_component')!r} does "
            f"not match sample component {sample.evaluation_component!r}."
        )

    reviewer_id = meta_map.get("reviewer_id", "")
    ctx_by_pair: dict[str, SCLEAutoContext] = {
        case.pair_id: case.auto_context for case in sample.cases
    }

    ws = wb[CASES_SHEET]
    rows = list(ws.iter_rows(values_only=True))
    header = [(_cell_str(h)) for h in rows[0]]
    header_index = {h: i for i, h in enumerate(header)}
    field_by_header = {col.header: col for col in _ALL_COLS}

    assessments: list[SCLECaseAssessment] = []
    seen: set[str] = set()
    problems: list[str] = []

    for row in rows[1:]:
        if row is None or all(v is None for v in row):
            continue
        values: dict[str, object] = {}
        for header_name, idx in header_index.items():
            col = field_by_header.get(header_name)
            if col is None:
                continue
            values[col.field] = row[idx] if idx < len(row) else None

        pair_id = _cell_str(values.get("pair_id"))
        if not pair_id:
            continue
        seen.add(pair_id)
        ctx = ctx_by_pair.get(pair_id)
        if ctx is None:
            problems.append(f"{path}: pair {pair_id!r} is not in the sample.")
            continue

        assessment = _row_to_assessment(pair_id, reviewer_id, values, problems)
        if assessment is not None:
            problems.extend(check_assessment_against_context(assessment, ctx))
            assessments.append(assessment)

    missing = sorted(set(ctx_by_pair) - seen)
    if missing:
        problems.append(f"{path}: missing rows for pair(s) {missing}.")

    if problems:
        raise ValueError(
            f"{path}: reviewer submission failed validation:\n"
            + "\n".join(f"  - {p}" for p in problems)
        )

    return ReviewerSubmission(
        workbook_id=meta_map.get("workbook_id", ""),
        reviewer_id=meta_map.get("reviewer_id", ""),
        evaluation_component=sample.evaluation_component,
        sample_checksum=expected_checksum,
        assessments=assessments,
    )


def _response_or_none(field: str, values: dict[str, object]) -> str | None:
    """Return the trimmed cell value, or None for blank / not_applicable."""
    raw = _cell_str(values.get(field))
    if raw == "" or raw == NOT_APPLICABLE:
        return None
    return raw


def _row_to_assessment(
    pair_id: str,
    reviewer_id: str,
    values: dict[str, object],
    problems: list[str],
) -> SCLECaseAssessment | None:
    """Build one assessment from a row, appending any construction error."""
    domains_raw = _response_or_none("implicated_feature_domains", values)
    payload = {
        "pair_id": pair_id,
        "reviewer_id": reviewer_id,
        "free_text_assessment": _cell_str(values.get("free_text_assessment")),
        "case_difficulty": _response_or_none("case_difficulty", values),
        "reference_label_issue": _response_or_none("reference_label_issue", values),
        "never_event": _response_or_none("never_event", values),
        "unexpected_method_behaviour": _response_or_none(
            "unexpected_method_behaviour", values
        ),
        "material_input_data_limitation": _response_or_none(
            "material_input_data_limitation", values
        ),
        "input_data_limitation_category": _response_or_none(
            "input_data_limitation_category", values
        ),
        "primary_error_source": _response_or_none("primary_error_source", values),
        "implicated_feature_domains": _parse_domains(domains_raw or ""),
        "method_difference_explanation": _response_or_none(
            "method_difference_explanation", values
        ),
    }
    try:
        return SCLECaseAssessment.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - surfaced as a validation problem
        problems.append(f"pair {pair_id!r}: {exc}")
        return None


_INSTRUCTION_LINES: tuple[str, ...] = (
    "SCLE reviewer workbook — instructions",
    "",
    "1. Fill only the response columns (right of the greyed auto-context block).",
    "2. Do NOT edit greyed cells or cells reading 'not_applicable'.",
    "3. Use the dropdown vocabularies for the structured dimensions.",
    "4. Free-text assessment is required for every case.",
    "5. Misclassification dimensions apply only to misclassified cases; the "
    "method-difference note applies only where the methods disagree. Non-"
    "applicable cells are pre-filled 'not_applicable'.",
    "6. Implicated feature domains: comma-separated, from the prespecified list.",
)


__all__ = [
    "NOT_APPLICABLE",
    "write_reviewer_workbook",
    "read_reviewer_submission",
]
