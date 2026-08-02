"""Reviewer workbooks: XLSX generation and validated read-back.

Each reviewer receives an identical workbook: a ``cases`` sheet with one row per
sampled pair, an auto-context block on the left (filled by the package, shown
greyed) and a response block on the right (dropdown-validated where the
dimension is an enum). Dimensions that do not apply to a case — the
misclassification block on a correctly classified pair, the method-difference
note where the methods agree — are pre-filled with the ``not_applicable``
sentinel and greyed.

The response block is **partly generated from the study's configuration**: one
tick-only yes/no column per configured error stage and per configured feature
domain. Both lists are embedded in their own worksheets, so a workbook describes
its own columns and the read-back needs no configuration passed alongside it.
Omitting a list means those questions are simply absent.

Every question carries an interpretation: a comment on its header cell in
``cases`` (hover to read) and a row on the dedicated ``guidance`` sheet.

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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from llm_dup_dev.study_eval.checksums import sha256_canonical_json
from llm_dup_dev.study_eval.sampling import SCLESample
from llm_dup_dev.study_eval.scle.form import (
    CaseDifficulty,
    ReferenceLabelIssue,
    ReviewerSubmission,
    SCLEAutoContext,
    SCLECaseAssessment,
    YesNo,
    check_assessment_against_context,
)
from llm_dup_dev.study_eval.scle.vocabularies import (
    ERROR_STAGES_SHEET,
    FEATURE_DOMAINS_SHEET,
    normalise_vocabulary,
    read_vocabulary_sheet,
    write_vocabulary_sheet,
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
GUIDANCE_SHEET = "guidance"

#: Header prefixes for the two generated column groups.
STAGE_HEADER_PREFIX = "Stage contributing: "
DOMAIN_HEADER_PREFIX = "Implicated: "


@dataclass(frozen=True)
class _Col:
    """One workbook column: an auto-context field or a reviewer response."""

    field: str
    header: str
    kind: str  # "context" | "text" | "enum"
    options: tuple[str, ...] | None = None
    applies: str | None = None  # None (always) | "misclassified" | "disagreement"
    #: For a generated column, the list it came from and the entry it stands for.
    group: str | None = None  # None | "error_stage" | "feature_domain"
    entry: str | None = None


def _enum_options(enum_cls) -> tuple[str, ...]:
    return tuple(m.value for m in enum_cls)


_YES_NO = _enum_options(YesNo)


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

# Reviewer response columns that exist regardless of the study's configuration.
_FIXED_RESPONSE_COLS: tuple[_Col, ...] = (
    _Col("free_text_assessment", "Free-text assessment", "text"),
    _Col("case_difficulty", "Case difficulty", "enum", _enum_options(CaseDifficulty)),
    _Col(
        "reference_label_issue",
        "Reference-label issue",
        "enum",
        _enum_options(ReferenceLabelIssue),
    ),
    _Col("never_event", "Never event", "enum", _YES_NO, applies="misclassified"),
    _Col(
        "unexpected_method_behaviour",
        "Unexpected method behaviour",
        "enum",
        _YES_NO,
        applies="misclassified",
    ),
    _Col(
        "incorrect_or_imprecise_input_data",
        "Incorrect or imprecise input data",
        "enum",
        _YES_NO,
        applies="misclassified",
    ),
    _Col(
        "error_source_description",
        "Source of error",
        "text",
        applies="misclassified",
    ),
)

_METHOD_DIFFERENCE_COL = _Col(
    "method_difference_explanation",
    "Method-difference explanation",
    "text",
    applies="disagreement",
)


def _generated_cols(
    group: str, prefix: str, entries: Sequence[str]
) -> tuple[_Col, ...]:
    """One tick-only yes/no column per configured vocabulary entry."""
    return tuple(
        _Col(
            field=f"{group}::{entry}",
            header=f"{prefix}{entry}",
            kind="enum",
            options=_YES_NO,
            applies="misclassified",
            group=group,
            entry=entry,
        )
        for entry in entries
    )


def _response_cols(
    error_stages: Sequence[str] = (),
    feature_domains: Sequence[str] = (),
) -> tuple[_Col, ...]:
    """Build the response block for a study configuration.

    The generated stage and domain columns sit between the free-text source of
    error and the method-difference note, in the order the study supplied them.
    """
    return (
        *_FIXED_RESPONSE_COLS,
        *_generated_cols("error_stage", STAGE_HEADER_PREFIX, error_stages),
        *_generated_cols("feature_domain", DOMAIN_HEADER_PREFIX, feature_domains),
        _METHOD_DIFFERENCE_COL,
    )


def _applies_to(col: _Col, ctx: SCLEAutoContext) -> bool:
    """Whether a response column applies to a case, given its auto-context."""
    if col.applies is None:
        return True
    if col.applies == "misclassified":
        return ctx.is_misclassified
    if col.applies == "disagreement":
        # ``None`` means the case has no screening verdict to disagree with
        # (screening_mode: not_evaluated), so the dimension does not apply.
        return bool(ctx.methods_disagree)
    return True


#: Context fields whose ``None`` means "does not apply to this case" rather than
#: "not recorded" — both are absent exactly when screening was not evaluated for
#: the case's reference set, and a reviewer should see that stated.
_NOT_APPLICABLE_WHEN_NONE: frozenset[str] = frozenset(
    {"screening_retrieved", "methods_disagree"}
)


def _context_value(field: str, ctx: SCLEAutoContext) -> str:
    value = getattr(ctx, field)
    if value is None:
        return NOT_APPLICABLE if field in _NOT_APPLICABLE_WHEN_NONE else ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _set_packet_hyperlink(cell, target: str) -> None:
    """Render ``cell`` as a clickable hyperlink to a local packet HTML file.

    The displayed text is the target's basename; the link target is the given
    relative path (resolved by Excel relative to the workbook's location).
    """
    from openpyxl.styles import Font  # type: ignore[import-untyped]

    cell.value = target.rsplit("/", 1)[-1]
    cell.hyperlink = target
    cell.font = Font(color="0563C1", underline="single")


# --- Per-question guidance --------------------------------------------------

#: Interpretation shown to reviewers for each fixed column, as a header comment
#: and on the ``guidance`` sheet. Kept here so the wording lives beside the
#: column definitions it explains.
_GUIDANCE: dict[str, str] = {
    # Auto-context.
    "pair_id": "The report pair under examination. Filled automatically.",
    "evaluation_component": "Whether this case was sampled for the precision or "
    "the recall evaluation. Filled automatically.",
    "reference_set_id": "The reference set the pair was drawn from. Filled "
    "automatically.",
    "reference_label": "The reference (ground-truth) classification. Filled "
    "automatically — do not re-adjudicate it here; use 'Reference-label issue' "
    "to flag a label you doubt.",
    "adjudicator_final_label": "What the adjudication method output for this "
    "pair. Filled automatically.",
    "screening_retrieved": "Whether the screening method (vigiMatch2025) "
    "retrieved this pair. 'not_applicable' means screening was not evaluated "
    "for this reference set.",
    "confusion_cell": "The pair's cell in the adjudicator-versus-reference "
    "confusion matrix (TP/FP/FN/TN). Precision cases only.",
    "joint_stratum": "The pair's joint screening-adjudicator stratum. Recall "
    "cases only.",
    "methods_disagree": "Whether screening and the adjudicator reached "
    "different conclusions. Drives whether the method-difference note applies.",
    "is_misclassified": "Whether the adjudicator's output differs from the "
    "reference label. Drives which conditional questions apply.",
    "decision_trace_summary": "A short summary of the method's recorded "
    "decision path for this pair.",
    "packet_filename": "Click to open the pair's HTML review packet.",
    # Responses.
    "free_text_assessment": "Required for every case. For a correctly "
    "classified pair, briefly describe the decisive evidence supporting the "
    "classification. For a misclassified pair, describe the apparent root "
    "cause.",
    "case_difficulty": "How hard correct classification would be, given the "
    "information available to the method. 'trivial' should be very rare — "
    "reserve it for pairs where nothing is left to judge (reports matching "
    "exactly, with sufficient information and explicit links between them). Use "
    "'clear' where the correct label is clear to you but a computational method "
    "would still have to extract that reliably — including pairs whose "
    "narratives plainly describe the same case. Use 'non_trivial' where "
    "classification is genuinely difficult, and 'unclear' only where the case is "
    "so difficult that you would have abstained.",
    "reference_label_issue": "Whether the reference label itself looks sound. "
    "Flagging a label here does not change it: flagged pairs are reported "
    "transparently and may support a labelled sensitivity analysis.",
    "never_event": "Misclassified pairs only. Would an error of this type be "
    "unacceptable in the intended operational use, or likely to seriously "
    "undermine trust in the method?",
    "unexpected_method_behaviour": "Misclassified pairs only. Was the "
    "classification inconsistent with, or not readily explainable from, the "
    "prespecified method logic and the recorded decision trace?",
    "incorrect_or_imprecise_input_data": "Misclassified pairs only. Did "
    "incorrect or imprecise input data materially contribute to the "
    "misclassification?",
    "error_source_description": "Misclassified pairs only, and required there. "
    "Describe in free text what went wrong. Errors often involve an interaction "
    "between components, so describe the interaction rather than forcing a "
    "single cause; use the stage columns to flag every stage involved.",
    "method_difference_explanation": "Only where screening and the adjudicator "
    "disagree. Describe the evidence or decision mechanism that most plausibly "
    "explains the difference between the two methods.",
}

#: Guidance shared by every column generated from a vocabulary list.
_GROUP_GUIDANCE: dict[str, str] = {
    "error_stage": "Misclassified pairs only. Tick 'yes' if this stage of the "
    "method contributed to the misclassification. Tick every stage that "
    "contributed — they are not mutually exclusive. Leave the cell blank if the "
    "stage did not contribute.",
    "feature_domain": "Misclassified pairs only. Tick 'yes' if this feature "
    "domain was implicated in the misclassification. Tick as many as apply, or "
    "leave blank where the domain was not implicated.",
}

_APPLIES_LABEL = {
    None: "Every case",
    "misclassified": "Misclassified cases only",
    "disagreement": "Cases where the methods disagree",
}


def _guidance_for(col: _Col) -> str:
    if col.group is not None:
        return _GROUP_GUIDANCE[col.group]
    return _GUIDANCE.get(col.field, "")


def _write_guidance_sheet(wb, cols: Sequence[_Col], Font) -> None:
    """Write the sheet explaining every question in the workbook."""
    ws = wb.create_sheet(GUIDANCE_SHEET)
    for c_idx, title in enumerate(("Question", "Applies to", "Guidance"), start=1):
        cell = ws.cell(row=1, column=c_idx, value=title)
        cell.font = Font(bold=True)
    row = 2
    for col in cols:
        guidance = _guidance_for(col)
        if not guidance:
            continue
        applies = "Filled automatically" if col.kind == "context" else (
            _APPLIES_LABEL[col.applies]
        )
        ws.cell(row=row, column=1, value=col.header)
        ws.cell(row=row, column=2, value=applies)
        ws.cell(row=row, column=3, value=guidance)
        row += 1
    ws.freeze_panes = "A2"
    ws.column_dimensions["A"].width = 40
    ws.column_dimensions["B"].width = 32
    ws.column_dimensions["C"].width = 100


def write_reviewer_workbook(
    sample: SCLESample,
    reviewer_id: str,
    path: str | Path,
    *,
    packet_targets: dict[str, str] | None = None,
    workbook_id: str | None = None,
    error_stages: Sequence[str] | None = None,
    feature_domains: Sequence[str] | None = None,
) -> tuple[Path, str]:
    """Write one reviewer's workbook for ``sample`` to ``path``.

    Returns the written path and the ``workbook_id`` (generated if not given).
    The sample checksum embedded in the metadata sheet ties the workbook to the
    exact sampled case list. Issue two workbooks (one per reviewer) from the
    same ``sample`` for the two independent examinations.

    ``error_stages`` and ``feature_domains`` are the study's frozen, ordered
    vocabularies. Each entry becomes one tick-only yes/no column, in the order
    supplied, and the list is embedded in its own worksheet so the workbook
    describes its own columns. A list left as ``None`` contributes no columns:
    the package has no default vocabulary, so omitting one drops that question
    from the examination entirely.
    """
    openpyxl = _require_openpyxl()
    from openpyxl.comments import Comment  # type: ignore[import-untyped]
    from openpyxl.styles import Font, PatternFill  # type: ignore[import-untyped]
    from openpyxl.worksheet.datavalidation import DataValidation  # type: ignore[import-untyped]

    stages = (
        normalise_vocabulary(error_stages, label="error_stages")
        if error_stages is not None
        else ()
    )
    domains = (
        normalise_vocabulary(feature_domains, label="feature_domains")
        if feature_domains is not None
        else ()
    )
    response_cols = _response_cols(stages, domains)
    all_cols = _CONTEXT_COLS + response_cols

    workbook_id = workbook_id or uuid.uuid4().hex[:12]
    sample_checksum = sha256_canonical_json(sample)
    packet_targets = packet_targets or {}

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
    for i, line in enumerate(_instruction_lines(stages, domains), start=1):
        instr[f"A{i}"] = line
    instr.column_dimensions["A"].width = 110

    _write_guidance_sheet(wb, all_cols, Font)
    if stages:
        write_vocabulary_sheet(wb, ERROR_STAGES_SHEET, stages, Font)
    if domains:
        write_vocabulary_sheet(wb, FEATURE_DOMAINS_SHEET, domains, Font)

    grey = PatternFill(start_color="DDDDDD", end_color="DDDDDD", fill_type="solid")
    na_fill = PatternFill(start_color="F0F0F0", end_color="F0F0F0", fill_type="solid")
    ws = wb.create_sheet(CASES_SHEET)

    # Header row, each cell carrying its question's interpretation as a comment.
    for c_idx, col in enumerate(all_cols, start=1):
        cell = ws.cell(row=1, column=c_idx, value=col.header)
        cell.font = Font(bold=True)
        if col.kind == "context":
            cell.fill = grey
        guidance = _guidance_for(col)
        if guidance:
            comment = Comment(guidance, "SCLE")
            comment.width = 400
            comment.height = 160
            cell.comment = comment

    # One dropdown validation per enum response column, added once and applied
    # to the applicable cells in that column.
    validations: dict[str, DataValidation] = {}
    for col in response_cols:
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
        target = packet_targets.get(case.pair_id)
        for c_idx, col in enumerate(all_cols, start=1):
            cell = ws.cell(row=r_idx, column=c_idx)
            if col.kind == "context":
                cell.fill = grey
                if col.field == "packet_filename" and target:
                    _set_packet_hyperlink(cell, target)
                else:
                    cell.value = _context_value(col.field, ctx)
            elif not _applies_to(col, ctx):
                cell.value = NOT_APPLICABLE
                cell.fill = na_fill
            elif col.field in validations:
                validations[col.field].add(cell)

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out, workbook_id


def workbook_vocabularies(path: str | Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Read ``(error_stages, feature_domains)`` back from a reviewer workbook.

    Returns the exact ordered tuples embedded by :func:`write_reviewer_workbook`.
    A list the workbook does not carry comes back empty.
    """
    openpyxl = _require_openpyxl()

    wb = openpyxl.load_workbook(path, data_only=True)
    return _workbook_vocabularies(wb)


def _workbook_vocabularies(wb) -> tuple[tuple[str, ...], tuple[str, ...]]:
    stages = (
        read_vocabulary_sheet(wb[ERROR_STAGES_SHEET])
        if ERROR_STAGES_SHEET in wb.sheetnames
        else ()
    )
    domains = (
        read_vocabulary_sheet(wb[FEATURE_DOMAINS_SHEET])
        if FEATURE_DOMAINS_SHEET in wb.sheetnames
        else ()
    )
    return stages, domains


def _cell_str(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def read_reviewer_submission(
    path: str | Path,
    sample: SCLESample,
) -> ReviewerSubmission:
    """Read and fully validate a reviewer workbook against its ``sample``.

    The workbook's embedded vocabularies determine which generated columns are
    expected; the header row is checked against them, so a workbook whose
    columns and vocabulary sheets disagree is rejected rather than silently
    half-read.

    Raises ``ValueError`` on any of: checksum mismatch, wrong component, missing
    or extra pairs, a missing or unexpected generated column, an edited
    ``not_applicable`` cell, an out-of-vocabulary value, or a dimension filled
    where it does not apply (or left blank where it does). The returned
    :class:`ReviewerSubmission` is trustworthy.
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

    stages, domains = _workbook_vocabularies(wb)
    response_cols = _response_cols(stages, domains)
    all_cols = _CONTEXT_COLS + response_cols

    reviewer_id = meta_map.get("reviewer_id", "")
    ctx_by_pair: dict[str, SCLEAutoContext] = {
        case.pair_id: case.auto_context for case in sample.cases
    }

    ws = wb[CASES_SHEET]
    rows = list(ws.iter_rows(values_only=True))
    header = [(_cell_str(h)) for h in rows[0]]
    header_index = {h: i for i, h in enumerate(header)}
    field_by_header = {col.header: col for col in all_cols}

    assessments: list[SCLECaseAssessment] = []
    seen: set[str] = set()
    problems: list[str] = []

    # The generated columns must match the embedded vocabularies exactly, in
    # both directions: a missing column would silently drop an answer, and a
    # stray one would silently ignore it.
    expected_generated = {col.header for col in response_cols if col.group}
    found_generated = {
        h
        for h in header
        if h.startswith(STAGE_HEADER_PREFIX) or h.startswith(DOMAIN_HEADER_PREFIX)
    }
    for missing_header in sorted(expected_generated - found_generated):
        problems.append(f"{path}: column {missing_header!r} is missing.")
    for extra_header in sorted(found_generated - expected_generated):
        problems.append(
            f"{path}: column {extra_header!r} is not in the workbook's "
            f"embedded vocabularies."
        )

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

        assessment = _row_to_assessment(
            pair_id, reviewer_id, values, response_cols, problems
        )
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
        error_stages=list(stages),
        feature_domains=list(domains),
        assessments=assessments,
    )


def _response_or_none(field: str, values: dict[str, object]) -> str | None:
    """Return the trimmed cell value, or None for blank / not_applicable."""
    raw = _cell_str(values.get(field))
    if raw == "" or raw == NOT_APPLICABLE:
        return None
    return raw


def _ticked_entries(
    pair_id: str,
    group: str,
    values: dict[str, object],
    response_cols: Sequence[_Col],
    problems: list[str],
) -> list[str]:
    """Collect the entries ticked 'yes' in one group of generated columns.

    The columns are tick-only: blank means "no". An explicit ``no`` is accepted
    (and equivalent to blank); anything else is a validation problem.
    """
    ticked: list[str] = []
    for col in response_cols:
        if col.group != group:
            continue
        raw = _response_or_none(col.field, values)
        if raw is None or raw == YesNo.NO.value:
            continue
        if raw != YesNo.YES.value:
            problems.append(
                f"pair {pair_id!r}: column {col.header!r} has value {raw!r}; "
                f"expected {YesNo.YES.value!r}, {YesNo.NO.value!r}, or blank."
            )
            continue
        ticked.append(col.entry or "")
    return ticked


def _row_to_assessment(
    pair_id: str,
    reviewer_id: str,
    values: dict[str, object],
    response_cols: Sequence[_Col],
    problems: list[str],
) -> SCLECaseAssessment | None:
    """Build one assessment from a row, appending any construction error."""
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
        "incorrect_or_imprecise_input_data": _response_or_none(
            "incorrect_or_imprecise_input_data", values
        ),
        "error_source_description": _response_or_none(
            "error_source_description", values
        ),
        "error_stages": _ticked_entries(
            pair_id, "error_stage", values, response_cols, problems
        ),
        "implicated_feature_domains": _ticked_entries(
            pair_id, "feature_domain", values, response_cols, problems
        ),
        "method_difference_explanation": _response_or_none(
            "method_difference_explanation", values
        ),
    }
    try:
        return SCLECaseAssessment.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - surfaced as a validation problem
        problems.append(f"pair {pair_id!r}: {exc}")
        return None


def _instruction_lines(
    error_stages: Sequence[str], feature_domains: Sequence[str]
) -> tuple[str, ...]:
    """The ``instructions`` sheet text for one workbook configuration."""
    lines = [
        "SCLE reviewer workbook — instructions",
        "",
        "1. Fill only the response columns (right of the greyed auto-context block).",
        "2. Do NOT edit greyed cells or cells reading 'not_applicable'.",
        "3. Use the dropdown vocabularies for the structured dimensions.",
        "4. Free-text assessment is required for every case.",
        "5. Misclassification dimensions apply only to misclassified cases; the "
        "method-difference note applies only where the methods disagree. Non-"
        "applicable cells are pre-filled 'not_applicable'.",
    ]
    # The column prefixes are quoted verbatim so a reviewer can match them
    # against the header row by eye.
    tick_only: list[str] = []
    sheets: list[str] = []
    if error_stages:
        tick_only.append(f"'{STAGE_HEADER_PREFIX}...'")
        sheets.append(f"'{ERROR_STAGES_SHEET}'")
    if feature_domains:
        tick_only.append(f"'{DOMAIN_HEADER_PREFIX}...'")
        sheets.append(f"'{FEATURE_DOMAINS_SHEET}'")
    if tick_only:
        lines.append(
            f"6. The {' and '.join(tick_only)} columns are tick-only: set 'yes' "
            f"on every entry that applies and leave the rest blank. More than "
            f"one may apply to the same case. The full "
            f"{'lists are' if len(sheets) > 1 else 'list is'} on the "
            f"{' and '.join(sheets)} {'sheets' if len(sheets) > 1 else 'sheet'}."
        )
    else:
        lines.append(
            "6. This workbook was generated without a prespecified error-stage "
            "or feature-domain list, so those questions are not asked."
        )
    lines.extend(
        [
            "7. The Packet column links to the pair's HTML review file (in the "
            "packets/ folder beside this workbook); click it to open the case.",
            f"8. Every question has an interpretation: hover over a header cell "
            f"in '{CASES_SHEET}' to read it, or see the '{GUIDANCE_SHEET}' sheet "
            f"for all of them together.",
        ]
    )
    return tuple(lines)


__all__ = [
    "NOT_APPLICABLE",
    "CASES_SHEET",
    "METADATA_SHEET",
    "INSTRUCTIONS_SHEET",
    "GUIDANCE_SHEET",
    "STAGE_HEADER_PREFIX",
    "DOMAIN_HEADER_PREFIX",
    "write_reviewer_workbook",
    "read_reviewer_submission",
    "workbook_vocabularies",
    "_set_packet_hyperlink",
]
