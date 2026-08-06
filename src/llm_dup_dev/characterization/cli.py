"""Command-line entry point for reference-set characteristics.

Deliberately a separate command rather than a ``study_eval`` subcommand.
``study_eval`` never touches ``Report`` — its whole input contract is pair ids,
labels and predictions — and threading reports through it to produce a Table 1
would put confidential-adjacent data into the signature of the performance
analysis. Running this first, standalone, keeps that boundary intact::

    python -m llm_dup_dev.characterization \\
        --reference-set precision.json --reference-set fda_gold.json \\
        --reports validation_reports.json \\
        --output-dir results/characteristics

The ``--reference-set`` files are the same ``study_reference_set.schema.json``
documents ``study_eval analyze`` reads, so no separate export is needed.
``--reports`` is a JSON array of ``Report`` objects, matching the convention of
``scripts/extract_frozen_*.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from llm_dup_dev.characterization.builder import build_characterization_report
from llm_dup_dev.characterization.writer import write_characterization_report
from llm_dup_dev.models.report import Report
from llm_dup_dev.study_eval.loading import load_reference_sets


def _load_reports(path: Path) -> dict[str, Report]:
    """Read a JSON array of ``Report`` objects into a ``{report_id: Report}`` map.

    A repeated ``report_id`` is an error rather than a last-one-wins overwrite:
    the two entries may differ, and silently keeping one would change every
    number downstream with no trace.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(
            f"{path}: expected a JSON array of Report objects, got "
            f"{type(raw).__name__}"
        )
    by_id: dict[str, Report] = {}
    for item in raw:
        report = Report.model_validate(item)
        if report.report_id in by_id:
            raise ValueError(
                f"{path}: report_id {report.report_id!r} appears more than once; "
                f"the two entries may differ, so pick one before characterising."
            )
        by_id[report.report_id] = report
    return by_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m llm_dup_dev.characterization",
        description="Describe the reports and pairs of one or more reference sets.",
    )
    parser.add_argument(
        "--reference-set",
        dest="reference_sets",
        action="append",
        required=True,
        type=Path,
        help="Path to a ReferenceSet JSON file. Repeat for several sets.",
    )
    parser.add_argument(
        "--reports",
        required=True,
        type=Path,
        help="Path to a JSON array of Report objects covering every referenced id.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory to write the characteristics artifact into.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="How many rows the ranked drug/AE tables keep (default: 10).",
    )
    parser.add_argument(
        "--dataset-id",
        default=None,
        help="DatasetManifest.dataset_id of the validation set these reports came from.",
    )
    parser.add_argument(
        "--format",
        dest="formats",
        action="append",
        choices=["json", "md", "csv"],
        default=None,
        help="Output format; repeatable. Defaults to json and md.",
    )
    parser.add_argument(
        "--allow-missing-reports",
        action="store_true",
        help=(
            "Characterise the reports that are available instead of failing, "
            "recording the gap as n_reports_unavailable. Off by default, since "
            "a quietly smaller denominator is hard to notice downstream."
        ),
    )
    parser.add_argument(
        "--notes",
        default=None,
        help=(
            "Free text recorded in the artifact. Worth stating which Report "
            "objects were passed (e.g. pre- or post narrative-demographics "
            "merge), since that changes the numbers."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    reference_sets = load_reference_sets(list(args.reference_sets))
    reports = _load_reports(args.reports)

    report = build_characterization_report(
        reference_sets,
        reports,
        top_n=args.top_n,
        on_missing_report="skip" if args.allow_missing_reports else "raise",
        dataset_id=args.dataset_id,
        notes=args.notes,
    )
    written = write_characterization_report(
        args.output_dir, report, formats=tuple(args.formats or ("json", "md"))
    )
    for path in written.values():
        print(f"wrote {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
