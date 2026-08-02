"""The study's prespecified SCLE vocabularies: validation and workbook sheets.

Two lists are prespecified and frozen by the study *before* workbooks are
generated (on the secure side, in YAML):

* **error stages** — the method components a reviewer may flag as contributing
  to a misclassification;
* **feature domains** — the feature families a reviewer may implicate.

This module keeps the generic dev-side package from owning either list. The
caller supplies each frozen ordered list; these helpers validate it as
configuration (:func:`normalise_vocabulary`), embed it in a dedicated worksheet
so each workbook is self-describing (:func:`write_vocabulary_sheet`), and read it
back (:func:`read_vocabulary_sheet`).

Both lists determine which *columns* the reviewer workbook has — one tick-only
yes/no column per entry — so they must be known when the workbook is written, and
read-back must recover them before it can interpret the header row. That is why
they are embedded rather than re-supplied by the caller at every step. A list the
caller omits simply contributes no columns and no dimensions: the package has no
default vocabulary to fall back on.

These helpers deliberately take already-open openpyxl objects rather than paths,
so this module has no import-time or run-time dependency on openpyxl (the
optional ``study`` extra) and no import cycle with
:mod:`~llm_dup_dev.study_eval.scle.workbook`.
"""

from __future__ import annotations

from collections.abc import Sequence

#: Name of the worksheet listing the prespecified feature domains.
FEATURE_DOMAINS_SHEET = "Feature domains"

#: Name of the worksheet listing the prespecified error stages.
ERROR_STAGES_SHEET = "Error stages"


def normalise_vocabulary(
    values: Sequence[str], *, label: str = "vocabulary"
) -> tuple[str, ...]:
    """Validate a supplied vocabulary list and return the trimmed tuple.

    Raises ``ValueError`` when the list is empty, an entry is not a string, an
    entry is blank after trimming, an entry contains a comma, or two entries are
    identical after trimming. The supplied order is preserved — the list is
    never reordered, because it is the workbook's column order.

    Commas are rejected because the implicated feature domains are compared and
    arbitrated as one comma-joined set-valued dimension, so a comma inside a name
    would not survive the round trip.

    ``label`` names the list in error messages (e.g. ``"feature_domains"``).

    Idempotent: passing an already-normalised list returns an equal tuple.
    """
    if len(values) == 0:
        raise ValueError(f"{label} must be a non-empty list.")

    normalised: list[str] = []
    seen: set[str] = set()
    for i, entry in enumerate(values):
        if not isinstance(entry, str):
            raise ValueError(f"{label}[{i}] is not a string: {entry!r}.")
        value = entry.strip()
        if value == "":
            raise ValueError(f"{label}[{i}] is blank after trimming: {entry!r}.")
        if "," in value:
            raise ValueError(
                f"{label}[{i}] contains a comma ({value!r}); commas separate "
                f"the values of a set-valued dimension and cannot appear in a "
                f"name."
            )
        if value in seen:
            raise ValueError(
                f"{label}[{i}] duplicates an earlier entry: {value!r}."
            )
        seen.add(value)
        normalised.append(value)
    return tuple(normalised)


def write_vocabulary_sheet(wb, title: str, values: Sequence[str], Font) -> None:
    """Add a worksheet to ``wb`` listing ``values``, one exact value per row.

    ``title`` is the sheet name (:data:`FEATURE_DOMAINS_SHEET` or
    :data:`ERROR_STAGES_SHEET`); ``Font`` is ``openpyxl.styles.Font``, passed in
    so this module never imports the optional dependency itself.
    """
    ws = wb.create_sheet(title)
    heading = ws.cell(row=1, column=1, value=title)
    heading.font = Font(bold=True)
    for offset, value in enumerate(values, start=2):
        ws.cell(row=offset, column=1, value=value)
    ws.freeze_panes = "A2"
    ws.column_dimensions["A"].width = 40


def read_vocabulary_sheet(ws) -> tuple[str, ...]:
    """Read a vocabulary list back from a worksheet written by the writer above.

    Returns the exact ordered tuple of values below the header row, blanks
    skipped.
    """
    values: list[str] = []
    for row in ws.iter_rows(min_row=2, max_col=1, values_only=True):
        value = row[0]
        if value is None:
            continue
        text = str(value).strip()
        if text:
            values.append(text)
    return tuple(values)


__all__ = [
    "FEATURE_DOMAINS_SHEET",
    "ERROR_STAGES_SHEET",
    "normalise_vocabulary",
    "write_vocabulary_sheet",
    "read_vocabulary_sheet",
]
