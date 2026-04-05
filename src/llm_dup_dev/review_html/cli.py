"""CLI entrypoint for the review HTML renderer.

Usage examples
--------------
Render all pairs from frozen JSON artifacts::

    python -m llm_dup_dev.review_html \\
        --reports reports.json \\
        --pairs pairs.json \\
        --output-dir outputs/review_html/

Optionally include features, classifications, and frozen frequency statistics::

    python -m llm_dup_dev.review_html \\
        --reports reports.json \\
        --pairs pairs.json \\
        --features features.json \\
        --classifications classifications.json \\
        --bundles bundles.json \\
        --output-dir outputs/review_html/

Expected JSON formats
---------------------
- ``reports.json``: ``{ "<report_id>": { ...Report fields... }, ... }``
- ``pairs.json``: ``[ { "pair_id": "...", "report_1_id": "...", "report_2_id": "..." }, ... ]``
- ``features.json`` (optional): ``{ "<pair_id>": { ...PairFeatures fields... }, ... }``
- ``classifications.json`` (optional): ``{ "<pair_id>": { ...ClassificationResult fields... }, ... }``
- ``bundles.json`` (optional): ``{ "<pair_id>": { ...FrozenPairBundle fields... }, ... }``
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from llm_dup_dev.data.offline import FrozenPairBundle
from llm_dup_dev.models.classification import ClassificationResult
from llm_dup_dev.models.pair_features import PairFeatures
from llm_dup_dev.models.report import Report
from llm_dup_dev.models.report_pair import ReportPair

from .compare import build_pair_view_model
from .models import PairViewModel
from .render import DEFAULT_OUTPUT_DIR, write_batch_html, write_pair_html


def _load_json(path: Path) -> dict | list:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="review_html",
        description="Generate standalone HTML review pages for report pairs.",
    )
    parser.add_argument(
        "--reports",
        type=Path,
        required=True,
        help="Path to reports JSON (dict keyed by report_id).",
    )
    parser.add_argument(
        "--pairs",
        type=Path,
        required=True,
        help="Path to pairs JSON (list of ReportPair objects).",
    )
    parser.add_argument(
        "--features",
        type=Path,
        default=None,
        help="Path to features JSON (dict keyed by pair_id). Optional.",
    )
    parser.add_argument(
        "--classifications",
        type=Path,
        default=None,
        help="Path to classifications JSON (dict keyed by pair_id). Optional.",
    )
    parser.add_argument(
        "--bundles",
        type=Path,
        default=None,
        help=(
            "Path to frozen pair bundles JSON (dict keyed by pair_id). "
            "When provided, each pair report includes a Frequency Statistics "
            "section. Optional."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="Skip generating the batch index.html.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entrypoint."""
    args = _parse_args(argv)

    # Load reports
    raw_reports = _load_json(args.reports)
    reports: dict[str, Report] = {
        rid: Report.model_validate(rdata)
        for rid, rdata in raw_reports.items()
    }

    # Load pairs
    raw_pairs = _load_json(args.pairs)
    pairs: list[ReportPair] = [
        ReportPair.model_validate(p) for p in raw_pairs
    ]

    # Load optional features
    features: dict[str, PairFeatures] = {}
    if args.features:
        raw_features = _load_json(args.features)
        features = {
            pid: PairFeatures.model_validate(fdata)
            for pid, fdata in raw_features.items()
        }

    # Load optional classifications
    classifications: dict[str, ClassificationResult] = {}
    if args.classifications:
        raw_cls = _load_json(args.classifications)
        classifications = {
            pid: ClassificationResult.model_validate(cdata)
            for pid, cdata in raw_cls.items()
        }

    # Load optional frozen pair bundles
    bundles: dict[str, FrozenPairBundle] = {}
    if args.bundles:
        raw_bundles = _load_json(args.bundles)
        bundles = {
            pid: FrozenPairBundle.model_validate(bdata)
            for pid, bdata in raw_bundles.items()
        }

    # Build view models
    view_models: list[PairViewModel] = []
    errors: list[str] = []
    for pair in pairs:
        r1 = reports.get(pair.report_1_id)
        r2 = reports.get(pair.report_2_id)
        if r1 is None or r2 is None:
            missing = []
            if r1 is None:
                missing.append(pair.report_1_id)
            if r2 is None:
                missing.append(pair.report_2_id)
            errors.append(
                f"Skipping {pair.pair_id}: missing report(s) {missing}"
            )
            continue

        vm = build_pair_view_model(
            report_1=r1,
            report_2=r2,
            pair_id=pair.pair_id,
            features=features.get(pair.pair_id),
            classification=classifications.get(pair.pair_id),
            frozen_bundle=bundles.get(pair.pair_id),
        )
        view_models.append(vm)

    if errors:
        for e in errors:
            print(f"WARNING: {e}", file=sys.stderr)

    if not view_models:
        print("No pairs to render.", file=sys.stderr)
        sys.exit(1)

    # Render
    if len(view_models) == 1 and args.no_index:
        path = write_pair_html(view_models[0], args.output_dir)
        print(f"Wrote {path}")
    else:
        out = write_batch_html(
            view_models,
            args.output_dir,
            write_index=not args.no_index,
        )
        print(
            f"Wrote {len(view_models)} pair(s) to {out}"
            + (" (with index.html)" if not args.no_index else "")
        )
