"""Load human annotations (gold labels) from a delimited file.

The annotation file identifies a pair by its two report ids rather than by
``pair_id``::

    report_id1  report_id2  duplicates_assessment
    R001        R002        duplicates

``pair_id`` is derived as ``{report_id1}__{report_id2}``, matching how the
feature-generation pipeline constructs it
(:mod:`llm_dup_dev.feature_generation.pipeline`).

The loaded dicts are what :func:`llm_dup_dev.eval.comparison.evaluate` consumes:
it reads ``pair_id`` and ``duplicates_assessment`` and ignores everything else,
so any additional columns are preserved verbatim and remain available for
stratifying an analysis.

Two things are checked here rather than left to fail later:

* **One row per pair.** ``evaluate`` indexes annotations with
  ``{a["pair_id"]: a for a in ...}``, so a repeated pair would be silently
  de-duplicated with whichever row came last winning. That is a quiet way to
  score against the wrong ground truth, so a duplicate raises instead.
* **The label vocabulary.** ``evaluate`` calls ``normalise_label`` per row
  mid-run, so a typo surfaces only after the work has started. Validating on
  load fails before a caller spends anything.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .labels import is_abstain, normalise_label

#: Columns the file must provide.
REQUIRED_COLUMNS = ("report_id1", "report_id2", "duplicates_assessment")

#: Delimiters accepted when sniffing. Tab first: the observed files are
#: tab-separated even where the extension says csv.
_DELIMITERS = "\t,;"


def derive_pair_id(report_id1: str, report_id2: str) -> str:
    """Return the pair_id for two report ids: ``{report_id1}__{report_id2}``.

    Mirrors ``feature_generation.pipeline.build_pair_features``. Note the id is
    **orientation-sensitive** — there is no canonical ordering of a pair
    anywhere in the pipeline, so ``A__B`` and ``B__A`` are different ids for
    the same two reports. Matching annotations to features must therefore
    tolerate a flipped file; :func:`llm_dup_dev.eval.sweep.check_coverage`
    reports that rather than silently scoring nothing.
    """
    return f"{report_id1}__{report_id2}"


def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=_DELIMITERS).delimiter
    except csv.Error:
        # A single-column or otherwise unsniffable file: tab is the observed
        # default, and a missing-column error below will be clearer than
        # anything raised from here.
        return "\t"


def load_annotations(path: str | Path) -> list[dict[str, Any]]:
    """Load gold labels from a delimited file.

    Parameters
    ----------
    path : str or Path
        A delimited file with a header row providing at least
        ``report_id1``, ``report_id2`` and ``duplicates_assessment``.
        The delimiter is sniffed (tab, comma or semicolon).

    Returns
    -------
    list of dict
        One dict per row, ready for
        :func:`llm_dup_dev.eval.comparison.evaluate`. Each carries the derived
        ``pair_id``, the original ``duplicates_assessment``, and every other
        column from the file unchanged.

    Raises
    ------
    ValueError
        If a required column is missing, a pair_id repeats, a report id is
        blank, or an assessment is outside the label vocabulary.
    """
    path = Path(path)
    text = path.read_text()
    if not text.strip():
        raise ValueError(f"Annotation file is empty: {path}")

    reader = csv.DictReader(text.splitlines(), delimiter=_sniff_delimiter(text[:4096]))
    fieldnames = reader.fieldnames or []
    missing = [c for c in REQUIRED_COLUMNS if c not in fieldnames]
    if missing:
        raise ValueError(
            f"{path}: missing required column(s) {missing}. "
            f"Found: {fieldnames}"
        )

    annotations: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for line_no, row in enumerate(reader, start=2):  # 1 is the header
        record = {k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()}

        r1, r2 = record["report_id1"], record["report_id2"]
        if not r1 or not r2:
            raise ValueError(
                f"{path}:{line_no}: blank report id (report_id1={r1!r}, "
                f"report_id2={r2!r})"
            )

        assessment = record["duplicates_assessment"]
        if not is_abstain(assessment):
            try:
                normalise_label(assessment)
            except ValueError as exc:
                raise ValueError(
                    f"{path}:{line_no}: {exc}. Expected one of duplicates, "
                    f"otherwise_related, unrelated, abstain."
                ) from exc

        pair_id = derive_pair_id(r1, r2)
        if pair_id in seen:
            raise ValueError(
                f"{path}:{line_no}: duplicate pair_id {pair_id!r} (first seen "
                f"on line {seen[pair_id]}). Evaluation indexes annotations by "
                f"pair_id and would silently keep only the last row."
            )
        seen[pair_id] = line_no

        record["pair_id"] = pair_id
        annotations.append(record)

    return annotations
