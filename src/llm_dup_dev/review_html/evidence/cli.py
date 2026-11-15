"""CLI for generating evidence reviews from a curated example manifest.

Usage::

    python -m llm_dup_dev.review_html.evidence \\
        --manifest collection.yaml \\
        --output-dir outputs/supplement/

``examples/evidence_collection_example.yaml`` shows the manifest's shape; its
artifact paths name a saved run that is not committed, so copy it and point it
at your own.

For a whole saved run, or an ad-hoc selection of pair ids, use the main review
CLI with ``--format evidence`` instead; this entry point exists for the
curated case, where the ordering and the framing are editorial choices that
belong in a file rather than on a command line.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from ..render import write_evidence_batch_html, write_evidence_document
from ..selection import MissingPairPolicy
from .collection import build_collection, write_summary_drafts
from .manifest import load_curated_manifest

DEFAULT_OUTPUT_DIR = Path("outputs/evidence_review")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="review_html.evidence",
        description=(
            "Generate evidence reviews for a curated collection of report "
            "pairs."
        ),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help=(
            "Curated example manifest (.yaml, .yml or .json). Artifact paths "
            "inside it resolve against its own directory."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory to write into (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="Skip the index.html linking the generated reviews.",
    )
    parser.add_argument(
        "--single-document",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Also write every example into one HTML file at PATH. This is an "
            "internal review containing original identifiers and narratives, "
            "not a publication-ready supplement."
        ),
    )
    parser.add_argument(
        "--export-drafts",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Write the generated assessment summaries to PATH as a starting "
            "point for human editing. Edited text belongs in the manifest's "
            "case_interpretation; this file is never read back."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow --export-drafts to overwrite an existing drafts file.",
    )
    parser.add_argument(
        "--skip-missing",
        action="store_true",
        help=(
            "Render the rest of the collection when a named pair cannot be "
            "loaded, instead of failing. Off by default: a curated collection "
            "quietly missing an example is worse than one that does not build."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entrypoint."""
    args = _parse_args(argv)

    loaded = load_curated_manifest(args.manifest)
    page = build_collection(
        loaded,
        on_missing=(
            MissingPairPolicy.SKIP if args.skip_missing else MissingPairPolicy.ERROR
        ),
        strict=not args.skip_missing,
    )

    if not page.cases:
        print("No examples to render.", file=sys.stderr)
        sys.exit(1)

    out = write_evidence_batch_html(
        page.cases,
        args.output_dir,
        write_index=not args.no_index,
        index_title=page.title,
        collection_intro=page.intro,
        collection_note=page.note,
        provenance=page.provenance,
    )
    print(
        f"Wrote {len(page.cases)} evidence review(s) to {out}"
        + (" (with index.html)" if not args.no_index else "")
    )

    if args.single_document:
        path = write_evidence_document(
            page.cases,
            args.single_document,
            title=page.title,
            intro=page.intro,
            note=page.note,
            provenance=page.provenance,
        )
        print(f"Wrote combined document to {path}")

    if args.export_drafts:
        path = write_summary_drafts(page, args.export_drafts, force=args.force)
        print(f"Wrote assessment summary drafts to {path}")

    for gap in page.gaps:
        print(f"NOTE: {gap.message}", file=sys.stderr)
