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

``--mode publication`` runs the same collection through the publication
transform. It needs the salt in ``VIGITWIN_PUBLICATION_SALT`` and it refuses
to write while any disclosure finding is unresolved, so the useful shape of
a first run is::

    python -m llm_dup_dev.review_html.evidence \\
        --manifest collection.yaml \\
        --mode publication \\
        --side-by-side-dir secure/review/ \\
        --key-out secure/scrambling_key.json

which writes the review page and the key whether or not the candidate is
clear, and tells you what a reviewer has to decide.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence, TypeVar

from pydantic import BaseModel

from ..render import write_evidence_batch_html, write_evidence_document
from ..selection import MissingPairPolicy
from .collection import build_collection, write_summary_drafts
from .manifest import load_curated_manifest
from .publication import (
    PublicationApprovals,
    PublicationBlocked,
    PublicationPolicy,
    PublicationSecrets,
    scramble_page,
    write_approvals_form,
    write_publication_evidence,
    write_scrambling_key,
    write_side_by_side,
)

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
        help=(
            "Allow --export-drafts or --export-approvals to overwrite an "
            "existing file. Both refuse by default: by the second run they "
            "hold edited text or signatures."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=("internal", "publication"),
        default="internal",
        help=(
            "Presentation to write. 'internal' is the existing review, "
            "unchanged. 'publication' applies the scrambling transform and "
            "refuses to write while any disclosure finding is unresolved; it "
            "reads the salt from VIGITWIN_PUBLICATION_SALT."
        ),
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Publication policy as JSON. Defaults to the built-in policy, "
            "whose version is recorded in both emitted artifacts."
        ),
    )
    parser.add_argument(
        "--approvals",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Approved replacement texts and finding resolutions as JSON. A "
            "secure-side file: its rationales name real cases."
        ),
    )
    parser.add_argument(
        "--export-approvals",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Write an unsigned approvals form to PATH: one approve-as-written "
            "entry, with a digest, for every field that blocks. Fill in "
            "approved_by and approved_at and pass it back as --approvals. "
            "Read the text itself in the side-by-side review, which prints "
            "the originals; the form carries paths and digests only."
        ),
    )
    parser.add_argument(
        "--key-out",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Write the scrambling key record to PATH. Holds the offsets and "
            "every original value, so it belongs in the secure key store and "
            "never beside the supplement."
        ),
    )
    parser.add_argument(
        "--side-by-side-dir",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Write both presentations and the disclosure review page under "
            "PATH. Secure-side: the review page prints the originals."
        ),
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

    if args.mode == "publication":
        _run_publication(args, page)
        return

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


_Settings = TypeVar("_Settings", bound=BaseModel)


def _load(path: Path | None, model: type[_Settings]) -> _Settings:
    """A policy or approvals file, or the model's own defaults."""
    if path is None:
        return model()
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _run_publication(args: argparse.Namespace, page) -> None:
    """Transform the collection, write what is safe, and say what is not.

    The secure outputs — the key record and the side-by-side review — are
    written first and unconditionally, because they are what a reviewer needs
    in order to clear a block. The supplement itself is written last and only
    if nothing is outstanding.
    """
    if args.export_approvals:
        form = write_approvals_form(
            page, args.export_approvals, force=args.force
        )
        print(
            f"Wrote approvals form to {form}. It is unsigned, so it will not "
            "load until approved_by and approved_at are filled in."
        )

    secrets = PublicationSecrets.from_env()
    policy = _load(args.policy, PublicationPolicy)
    approvals = _load(args.approvals, PublicationApprovals)
    result = scramble_page(
        page, policy=policy, secrets=secrets, approvals=approvals
    )

    if args.key_out:
        print(f"Wrote scrambling key to {write_scrambling_key(result, args.key_out)}")
    if args.side_by_side_dir:
        out = write_side_by_side(page, result, args.side_by_side_dir)
        print(f"Wrote side-by-side review to {out}")

    for advisory in result.report.advisories:
        print(f"ADVISORY: {advisory.detail}", file=sys.stderr)

    try:
        out = write_publication_evidence(
            result,
            args.output_dir,
            index_title=page.title,
            collection_intro=page.intro,
            collection_note=page.note,
            single_document=args.single_document,
        )
    except PublicationBlocked as blocked:
        print(f"BLOCKED: {blocked}", file=sys.stderr)
        for finding in blocked.report.findings:
            where = f" at {finding.field_path}" if finding.field_path else ""
            print(
                f"  {finding.code.value} [{finding.example_id}{where}]: "
                f"{finding.message}",
                file=sys.stderr,
            )
        sys.exit(2)

    print(
        f"Wrote {len(result.page.cases)} publication review(s) to {out}. "
        "Treat them as a candidate for disclosure review."
    )
