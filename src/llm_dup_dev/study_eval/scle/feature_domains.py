"""The prespecified feature-domain list: config validation, workbook wiring.

The set of feature domains a reviewer may implicate is prespecified and frozen by
the study *before* workbooks are generated (on the secure side, in YAML). This
module keeps the generic dev-side package from owning that list: the caller
supplies the frozen ordered list, and these helpers

* validate it as configuration (:func:`normalise_feature_domains`);
* make each reviewer workbook self-contained by embedding the list
  (:func:`augment_reviewer_workbook`) and reading it back
  (:func:`workbook_feature_domains`);
* validate reviewer-entered domains against it before agreement / arbitration /
  consensus (:func:`validate_submission_feature_domains`).

The package-level :data:`~llm_dup_dev.study_eval.scle.form.FEATURE_DOMAINS` is a
default vocabulary used by the ``SCLECaseAssessment`` model; the workflow does
**not** consult it. When a frozen list is threaded through the workflow it is the
authority, and these helpers are the gate.

openpyxl is an optional dependency (the ``study`` extra); the workbook helpers
raise a clear message when it is missing (see
:func:`~llm_dup_dev.study_eval.scle.workbook._require_openpyxl`).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from llm_dup_dev.study_eval.scle.form import ReviewerSubmission
from llm_dup_dev.study_eval.scle.workbook import (
    INSTRUCTIONS_SHEET,
    _require_openpyxl,
)

#: Name of the dedicated worksheet listing the prespecified feature domains.
FEATURE_DOMAINS_SHEET = "Feature domains"

#: Instruction-6 text used once a frozen list is embedded in the workbook.
_INSTRUCTION_6 = (
    "6. Implicated feature domains: enter zero or more values from the "
    "prespecified list below, separated by commas. Use the terms exactly as "
    "written. Leave blank when no domain is implicated."
)


def normalise_feature_domains(feature_domains: Sequence[str]) -> tuple[str, ...]:
    """Validate a supplied feature-domain list and return the trimmed tuple.

    Raises ``ValueError`` when the list is empty, an entry is not a string, an
    entry is blank after trimming, an entry contains a comma (commas are the
    reviewer multi-value separator), or two entries are identical after
    trimming. The supplied order is preserved — the list is never reordered.

    Idempotent: passing an already-normalised list returns an equal tuple.
    """
    if len(feature_domains) == 0:
        raise ValueError("feature_domains must be a non-empty list.")

    normalised: list[str] = []
    seen: set[str] = set()
    for i, entry in enumerate(feature_domains):
        if not isinstance(entry, str):
            raise ValueError(
                f"feature_domains[{i}] is not a string: {entry!r}."
            )
        value = entry.strip()
        if value == "":
            raise ValueError(
                f"feature_domains[{i}] is blank after trimming: {entry!r}."
            )
        if "," in value:
            raise ValueError(
                f"feature_domains[{i}] contains a comma ({value!r}); commas "
                f"separate the reviewer's multiple values and cannot appear in a "
                f"domain name."
            )
        if value in seen:
            raise ValueError(
                f"feature_domains[{i}] duplicates an earlier entry: {value!r}."
            )
        seen.add(value)
        normalised.append(value)
    return tuple(normalised)


def augment_reviewer_workbook(
    path: str | Path, feature_domains: Sequence[str]
) -> None:
    """Embed the frozen feature-domain list into an existing reviewer workbook.

    Rewrites instruction 6 and appends the ordered list to the ``instructions``
    sheet, and adds a dedicated :data:`FEATURE_DOMAINS_SHEET` worksheet with one
    exact value per row (order preserved, header row frozen). Macro-free. The
    list is validated with :func:`normalise_feature_domains` first.
    """
    openpyxl = _require_openpyxl()
    from openpyxl.styles import Font  # type: ignore[import-untyped]

    domains = normalise_feature_domains(feature_domains)
    wb = openpyxl.load_workbook(path)

    _augment_instructions_sheet(wb[INSTRUCTIONS_SHEET], domains, Font)
    _write_feature_domains_sheet(wb, domains, Font)

    wb.save(path)


def _augment_instructions_sheet(instr, domains: tuple[str, ...], Font) -> None:
    """Rewrite instruction 6 and append the ordered list onto ``instr``."""
    six_row: int | None = None
    last_row = instr.max_row
    for row in range(1, last_row + 1):
        value = instr.cell(row=row, column=1).value
        if isinstance(value, str) and value.lstrip().startswith("6."):
            six_row = row
            break
    if six_row is not None:
        instr.cell(row=six_row, column=1).value = _INSTRUCTION_6

    start = last_row + 2  # one blank spacer row after the existing instructions
    heading = instr.cell(row=start, column=1, value="Prespecified feature domains:")
    heading.font = Font(bold=True)
    for offset, domain in enumerate(domains, start=1):
        instr.cell(row=start + offset, column=1, value=domain)


def _write_feature_domains_sheet(wb, domains: tuple[str, ...], Font) -> None:
    """Create the dedicated feature-domains worksheet on ``wb``."""
    ws = wb.create_sheet(FEATURE_DOMAINS_SHEET)
    heading = ws.cell(row=1, column=1, value=FEATURE_DOMAINS_SHEET)
    heading.font = Font(bold=True)
    for offset, domain in enumerate(domains, start=2):
        ws.cell(row=offset, column=1, value=domain)
    ws.freeze_panes = "A2"
    ws.column_dimensions["A"].width = 40


def workbook_feature_domains(path: str | Path) -> tuple[str, ...]:
    """Read the embedded feature-domain list back from a reviewer workbook.

    Returns the exact ordered tuple written by :func:`augment_reviewer_workbook`
    (values below the header row of :data:`FEATURE_DOMAINS_SHEET`, blanks
    skipped). Raises ``ValueError`` if the sheet is absent.
    """
    openpyxl = _require_openpyxl()

    wb = openpyxl.load_workbook(path, data_only=True)
    if FEATURE_DOMAINS_SHEET not in wb.sheetnames:
        raise ValueError(
            f"{path}: workbook has no {FEATURE_DOMAINS_SHEET!r} sheet."
        )
    ws = wb[FEATURE_DOMAINS_SHEET]
    domains: list[str] = []
    for row in ws.iter_rows(min_row=2, max_col=1, values_only=True):
        value = row[0]
        if value is None:
            continue
        text = str(value).strip()
        if text:
            domains.append(text)
    return tuple(domains)


def validate_submission_feature_domains(
    submission: ReviewerSubmission, feature_domains: Sequence[str]
) -> None:
    """Validate a reviewer submission's feature domains against the frozen list.

    For each assessment: trims each entered domain, rejects values not in
    ``feature_domains``, and rejects a value duplicated within the same
    assessment. Raises a single ``ValueError`` naming the reviewer ID, pair ID,
    the invalid values, and the allowed values. Synonyms are not mapped and
    capitalisation is not changed.
    """
    allowed = normalise_feature_domains(feature_domains)
    allowed_set = set(allowed)

    problems: list[str] = []
    for assessment in submission.assessments:
        trimmed = [d.strip() for d in assessment.implicated_feature_domains]
        unknown = [d for d in trimmed if d not in allowed_set]
        if unknown:
            problems.append(
                f"reviewer {submission.reviewer_id!r}, pair "
                f"{assessment.pair_id!r}: unknown feature domain(s) {unknown}. "
                f"Allowed: {list(allowed)}."
            )
        seen: set[str] = set()
        duplicates: list[str] = []
        for d in trimmed:
            if d in seen and d not in duplicates:
                duplicates.append(d)
            seen.add(d)
        if duplicates:
            problems.append(
                f"reviewer {submission.reviewer_id!r}, pair "
                f"{assessment.pair_id!r}: duplicate feature domain(s) "
                f"{duplicates}."
            )

    if problems:
        raise ValueError(
            "reviewer feature-domain validation failed:\n"
            + "\n".join(f"  - {p}" for p in problems)
        )


__all__ = [
    "FEATURE_DOMAINS_SHEET",
    "normalise_feature_domains",
    "augment_reviewer_workbook",
    "workbook_feature_domains",
    "validate_submission_feature_domains",
]
