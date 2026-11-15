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
        --pair-frequencies pair_frequencies.json \\
        --screening screening.json \\
        --output-dir outputs/review_html/

Expected JSON formats
---------------------
- ``reports.json``: either ``{ "<report_id>": { ...Report fields... }, ... }``
  or ``[ { ...Report fields... }, ... ]`` — both frozen shapes are accepted
- ``pairs.json``: ``[ { "pair_id": "...", "report_1_id": "...", "report_2_id": "..." }, ... ]``
- ``features.json`` (optional): ``{ "<pair_id>": { ...PairFeatures fields... }, ... }``
- ``classifications.json`` (optional): ``{ "<pair_id>": { ...ClassificationResult fields... }, ... }``
- ``pair_frequencies.json`` (optional): ``{ "<pair_id>": { ...FrozenPairBundle fields... }, ... }``
- ``narrative_demographics.json`` (optional): a ``FrozenNarrativeDemographics``
  artifact, ``{ "extractions": { "<report_id>": { ...NarrativeDemographics
  fields... }, ... } }``
- ``screening.json`` (optional): a ``ScreeningDetails`` artifact, either
  ``{ "algorithm": "vigiMatch", "entries": { "<pair_id>": { "<feature>":
  <value>, ... }, ... } }`` or the bare ``{ "<pair_id>": { "<feature>":
  <value>, ... }, ... }``
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from llm_dup_dev.data.offline import (
    FrozenPairBundle,
    load_frozen_narrative_demographics,
    load_reports,
    load_screening_details,
)
from llm_dup_dev.models.classification import ClassificationResult
from llm_dup_dev.models.narrative_demographics import NarrativeDemographics
from llm_dup_dev.models.pair_features import PairFeatures
from llm_dup_dev.models.report import Report
from llm_dup_dev.models.report_pair import (
    ALLOW_NONCANONICAL_CONTEXT_KEY,
    ReportPair,
)
from llm_dup_dev.pipeline.export import read_classifications, read_pair_features

from .compare import build_pair_view_model
from .evidence.build import build_evidence_case
from .evidence.screening import screening_verdict
from .evidence.settings import EvidenceRunSettings, load_evidence_run_settings
from .formats import ReviewFormat, reference_from_annotation
from .models import PairViewModel
from .render import (
    DEFAULT_EVIDENCE_OUTPUT_DIR,
    DEFAULT_OUTPUT_DIR,
    write_batch_html,
    write_evidence_batch_html,
    write_pair_html,
)
from .selection import PairSelection, apply_selection


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
        help=(
            "Path to reports JSON — either a list of Report objects or a dict "
            "keyed by report_id."
        ),
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
        "--pair-frequencies",
        type=Path,
        default=None,
        help=(
            "Path to frozen pair frequencies JSON (dict keyed by pair_id). "
            "When provided, each pair report includes a Frequency Statistics "
            "section. Optional."
        ),
    )
    parser.add_argument(
        "--screening-verdict-key",
        default=None,
        help=(
            "The key the screening entries store their own flagged/not-flagged "
            "verdict under, when it is not one of the recognised names "
            "(IsSuspectedDuplicate, screening_retrieved, retrieved, flagged). "
            "Evidence format only. Optional."
        ),
    )
    parser.add_argument(
        "--narrative-demographics",
        type=Path,
        default=None,
        help=(
            "Path to a frozen narrative demographics bundle JSON (keyed by "
            "report_id). When provided, narrative-extracted demographic "
            "candidates appear in the demographics table alongside structured "
            "fields, with the supporting narrative snippets. Optional."
        ),
    )
    parser.add_argument(
        "--screening",
        type=Path,
        default=None,
        help=(
            "Path to a screening-details JSON (keyed by pair_id, either bare "
            "or wrapped with an 'algorithm' name). When provided, each pair "
            "report includes a Screening Algorithm section showing the basis "
            "for the screening algorithm's classification. Optional."
        ),
    )
    parser.add_argument(
        "--format",
        choices=[f.value for f in ReviewFormat],
        default=ReviewFormat.DETAILED.value,
        help=(
            "Which review to generate. 'detailed' (default) is the existing "
            "pair review, built for annotation and debugging. 'evidence' is "
            "the reader-facing review of vigiTwin's reasoning, with values "
            "coloured by the stored assessment rather than by comparing them."
        ),
    )
    parser.add_argument(
        "--pair-ids",
        nargs="+",
        default=None,
        metavar="PAIR_ID",
        help=(
            "Render only these pairs, in the order given, instead of every "
            "pair in the artifact. Ids are canonical "
            "({report_id1}__{report_id2}, ids ascending). An id matching no "
            "pair is an error."
        ),
    )
    parser.add_argument(
        "--run-manifest",
        type=Path,
        default=None,
        help=(
            "Evidence format only. The run's manifest, read for the decision "
            "thresholds and the numerical stabiliser it used. Without it the "
            "review names evidence zones but quotes no thresholds, rather "
            "than assuming today's defaults applied to that run."
        ),
    )
    parser.add_argument(
        "--set-label",
        default=None,
        help=(
            "Evidence format only. The reference set's name, used to label "
            "the reference-label tile (e.g. 'FDA Gold')."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            f"Output directory. Defaults to {DEFAULT_OUTPUT_DIR} for the "
            f"detailed format and {DEFAULT_EVIDENCE_OUTPUT_DIR} for the "
            "evidence format: the two write files of the same name, so one "
            "directory cannot hold both."
        ),
    )
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="Skip generating the batch index.html.",
    )
    parser.add_argument(
        "--allow-noncanonical-pairs",
        action="store_true",
        help=(
            "Render a pairs.json written before pair ids were canonicalised, "
            "whose report ids are not in canonical order. For viewing old runs "
            "only — anything feeding an evaluation must be regenerated. Blank "
            "report ids and self-pairs are still rejected."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entrypoint."""
    args = _parse_args(argv)

    # Load reports (either frozen shape — see data.offline.load_reports)
    reports: dict[str, Report] = load_reports(args.reports)

    # Load pairs
    raw_pairs = _load_json(args.pairs)
    pair_context = (
        {ALLOW_NONCANONICAL_CONTEXT_KEY: True}
        if args.allow_noncanonical_pairs
        else None
    )
    pairs: list[ReportPair] = [
        ReportPair.model_validate(p, context=pair_context) for p in raw_pairs
    ]
    if args.pair_ids:
        pairs = apply_selection(
            pairs,
            PairSelection(pair_ids=tuple(args.pair_ids)),
            key=lambda pair: pair.pair_id,
        )

    # Load optional features / classifications. The readers live in
    # pipeline.export alongside the writers that produce these files.
    features: dict[str, PairFeatures] = {}
    if args.features:
        features = read_pair_features(args.features)

    classifications: dict[str, ClassificationResult] = {}
    if args.classifications:
        classifications = read_classifications(args.classifications)

    # Load optional frozen pair frequencies
    pair_frequencies: dict[str, FrozenPairBundle] = {}
    if args.pair_frequencies:
        raw_pair_frequencies = _load_json(args.pair_frequencies)
        if not isinstance(raw_pair_frequencies, dict):
            raise ValueError(
                f"{args.pair_frequencies}: expected a JSON object keyed by "
                f"pair_id, got a {type(raw_pair_frequencies).__name__}."
            )
        pair_frequencies = {
            pid: FrozenPairBundle.model_validate(bdata)
            for pid, bdata in raw_pair_frequencies.items()
        }

    # Load optional narrative demographics extractions. Unlike the pair
    # frequencies
    # above this is a wrapper model keyed by report_id, so it has its own
    # loader rather than a bare dict of models.
    narrative_demographics: dict[str, NarrativeDemographics] = {}
    if args.narrative_demographics:
        narrative_demographics = load_frozen_narrative_demographics(
            args.narrative_demographics
        ).extractions

    # Load optional screening details. Its loader accepts both the bare
    # pair-keyed mapping and the wrapper that names the algorithm.
    screening_entries: dict[str, dict] = {}
    screening_algorithm: str | None = None
    if args.screening:
        screening_details = load_screening_details(args.screening)
        screening_entries = screening_details.entries
        screening_algorithm = screening_details.algorithm

    review_format = ReviewFormat(args.format)
    if args.output_dir is None:
        # Per-format default. An evidence run and a detailed run of the same
        # pairs produce files of the same name, index.html included, so
        # sharing a directory would have each quietly overwrite the other.
        args.output_dir = (
            DEFAULT_EVIDENCE_OUTPUT_DIR
            if review_format is ReviewFormat.EVIDENCE
            else DEFAULT_OUTPUT_DIR
        )
    if review_format is ReviewFormat.EVIDENCE:
        _run_evidence(
            args,
            pairs=pairs,
            reports=reports,
            features=features,
            classifications=classifications,
            pair_frequencies=pair_frequencies,
            narrative_demographics=narrative_demographics,
            screening_entries=screening_entries,
            screening_algorithm=screening_algorithm,
        )
        return

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
            frozen_bundle=pair_frequencies.get(pair.pair_id),
            narrative_demographics_1=narrative_demographics.get(
                pair.report_1_id
            ),
            narrative_demographics_2=narrative_demographics.get(
                pair.report_2_id
            ),
            screening=screening_entries.get(pair.pair_id),
            screening_algorithm=screening_algorithm,
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


def _run_evidence(
    args: argparse.Namespace,
    *,
    pairs: Sequence[ReportPair],
    reports: dict[str, Report],
    features: dict[str, PairFeatures],
    classifications: dict[str, ClassificationResult],
    pair_frequencies: dict[str, FrozenPairBundle],
    narrative_demographics: dict[str, NarrativeDemographics],
    screening_entries: dict[str, dict],
    screening_algorithm: str | None,
) -> None:
    """Render the evidence format for the pairs already selected and loaded.

    Shares the CLI's artifact loading rather than duplicating it; only the
    per-pair build and the writer differ. A pair missing its features still
    renders, with the page saying it carries no stored evidence — this format
    never invents highlighting from a comparison it does not perform.
    """
    run_settings: EvidenceRunSettings | None = None
    if args.run_manifest:
        run_settings = load_evidence_run_settings(args.run_manifest)

    cases = []
    errors: list[str] = []
    for pair in pairs:
        report_1 = reports.get(pair.report_1_id)
        report_2 = reports.get(pair.report_2_id)
        if report_1 is None or report_2 is None:
            missing = [
                rid
                for rid, report in (
                    (pair.report_1_id, report_1),
                    (pair.report_2_id, report_2),
                )
                if report is None
            ]
            errors.append(f"Skipping {pair.pair_id}: missing report(s) {missing}")
            continue
        verdict = screening_verdict(
            screening_entries.get(pair.pair_id),
            key=args.screening_verdict_key,
        )
        cases.append(
            build_evidence_case(
                report_1=report_1,
                report_2=report_2,
                pair_id=pair.pair_id,
                features=features.get(pair.pair_id),
                classification=classifications.get(pair.pair_id),
                frozen_bundle=pair_frequencies.get(pair.pair_id),
                narrative_demographics_1=narrative_demographics.get(
                    pair.report_1_id
                ),
                narrative_demographics_2=narrative_demographics.get(
                    pair.report_2_id
                ),
                reference=reference_from_annotation(
                    None,
                    set_label=args.set_label,
                    screening_retrieved=verdict.retrieved,
                    screening_note=verdict.note,
                    screening_algorithm=screening_algorithm or "vigiMatch",
                ),
                run_settings=run_settings,
            )
        )

    for message in errors:
        print(f"WARNING: {message}", file=sys.stderr)
    if not cases:
        print("No pairs to render.", file=sys.stderr)
        sys.exit(1)

    out = write_evidence_batch_html(
        cases, args.output_dir, write_index=not args.no_index
    )
    print(
        f"Wrote {len(cases)} evidence review(s) to {out}"
        + (" (with index.html)" if not args.no_index else "")
    )
