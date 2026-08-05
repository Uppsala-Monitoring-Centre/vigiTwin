"""Thin command line over the four-step workflow.

    python -m llm_dup_dev.study_eval <analyze|workbooks|arbitrate|finalize> [options]

Each subcommand wraps the matching function in
:mod:`llm_dup_dev.study_eval.workflow`. The workflow is notebook-first; this CLI
exists for scripted or one-off runs. Reviewer/arbitration workbooks require the
optional ``study`` extra (openpyxl).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from llm_dup_dev.study_eval import loading, workflow
from llm_dup_dev.study_eval.aggregate import (
    ABSTAIN_POSITIVE,
    ADJUDICATOR_LABELS,
    OTHERWISE_RELATED_POSITIVE,
    PRIMARY,
    PositiveClass,
)
from llm_dup_dev.study_eval.sampling import SCLESample

#: The named rules this CLI offers. The CLI is a dev-side smoke-test surface, so
#: it carries the current study's rules for convenience; the secure-side runner
#: calls the API directly and supplies whatever the protocol prespecifies. Use
#: ``--positive-label`` to build an ad-hoc rule instead.
_NAMED_RULES: dict[str, PositiveClass] = {
    pc.name: pc for pc in (PRIMARY, ABSTAIN_POSITIVE, OTHERWISE_RELATED_POSITIVE)
}


def _resolve_positive_class(args: argparse.Namespace) -> PositiveClass:
    """Build the collapse rule from ``--variant`` and any ``--positive-label``.

    Without ``--positive-label`` the name must be one of the rules this CLI
    carries; with it, any name is accepted and the labels define the rule.
    """
    if args.positive_label:
        return PositiveClass(
            name=args.variant, positive_labels=frozenset(args.positive_label)
        )
    if args.variant not in _NAMED_RULES:
        raise SystemExit(
            f"unknown --variant {args.variant!r}: pass one of "
            f"{sorted(_NAMED_RULES)}, or give --positive-label to define a rule."
        )
    return _NAMED_RULES[args.variant]


def _load_sample(path: str) -> SCLESample:
    return SCLESample.model_validate_json(Path(path).read_text())


def _cmd_analyze(args: argparse.Namespace) -> int:
    reference_sets = loading.load_reference_sets(args.reference_set)
    predictions = loading.load_prediction_set(args.predictions)
    result = workflow.run_analysis(
        reference_sets,
        predictions,
        seed=args.seed,
        sample_scle=not args.no_scle,
        scle_precision_per_cell=args.per_cell,
        scle_recall_per_stratum=args.per_stratum,
        positive_class=_resolve_positive_class(args),
        output_dir=args.output_dir,
    )
    print(
        f"Analysis ({result.variant}): "
        f"{len(result.precision_results)} precision set(s), "
        f"{len(result.recall_result.sets) if result.recall_result else 0} recall set(s), "
        f"{len(result.scle_samples)} SCLE sample(s) -> {args.output_dir}"
    )
    for key, sample in result.scle_samples.items():
        print(f"  {key}: {len(sample.cases)} cases, {len(sample.backups)} backups")
    return 0


def _cmd_workbooks(args: argparse.Namespace) -> int:
    sample = _load_sample(args.sample)
    written = workflow.generate_reviewer_workbooks(
        sample,
        args.reviewer,
        args.output_dir,
        error_stages=args.error_stage,
        feature_domains=args.feature_domain,
    )
    for p in written:
        print(f"Wrote {p}")
    return 0


def _cmd_arbitrate(args: argparse.Namespace) -> int:
    sample = _load_sample(args.sample)
    build = workflow.build_arbitration(
        sample, args.workbook, args.output_dir, arbiter_id=args.arbiter or ""
    )
    print(
        f"Agreement overall: {build.agreement.overall_percent_agreement}; "
        f"{build.discrepancy.n_pairs_with_disagreement} disagreement pair(s) "
        f"-> {build.arbitration_workbook}"
    )
    return 0


def _cmd_finalize(args: argparse.Namespace) -> int:
    sample = _load_sample(args.sample)
    result = workflow.finalize(
        sample, args.workbook, args.arbitration, args.output_dir
    )
    print(
        f"Consensus for {len(result.consensus.consensus)} case(s); "
        f"{len(result.consensus.reference_label_concerns)} reference-label "
        f"concern(s) -> {args.output_dir}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="study_eval", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("analyze", help="step 1: estimates + SCLE sampling")
    p.add_argument("--reference-set", action="append", required=True)
    p.add_argument("--predictions", required=True)
    p.add_argument("--seed", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--no-scle", action="store_true", help="skip SCLE sampling (secondary analyses)")
    p.add_argument("--per-cell", type=int, default=10)
    p.add_argument("--per-stratum", type=int, default=10)
    p.add_argument(
        "--variant",
        default=PRIMARY.name,
        help=(
            "name of the collapse rule: one of "
            f"{sorted(_NAMED_RULES)}, or any name when --positive-label is given"
        ),
    )
    p.add_argument(
        "--positive-label",
        action="append",
        choices=list(ADJUDICATOR_LABELS),
        help=(
            "adjudicator label counted as positive (repeatable). When given, "
            "builds an ad-hoc rule named by --variant instead of using a named one."
        ),
    )
    p.set_defaults(func=_cmd_analyze)

    p = sub.add_parser("workbooks", help="step 2: reviewer workbooks")
    p.add_argument("--sample", required=True)
    p.add_argument("--reviewer", action="append", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument(
        "--error-stage",
        action="append",
        help="method component a reviewer may flag as contributing to a "
        "misclassification; give once per stage, in the study's frozen order. "
        "Omit to leave the question out of the examination.",
    )
    p.add_argument(
        "--feature-domain",
        action="append",
        help="feature domain a reviewer may implicate; give once per domain, in "
        "the study's frozen order. Omit to leave the question out of the "
        "examination.",
    )
    p.set_defaults(func=_cmd_workbooks)

    p = sub.add_parser("arbitrate", help="step 3: agreement + arbitration workbook")
    p.add_argument("--sample", required=True)
    p.add_argument("--workbook", action="append", required=True, help="completed reviewer workbook (give twice)")
    p.add_argument("--arbiter")
    p.add_argument("--output-dir", required=True)
    p.set_defaults(func=_cmd_arbitrate)

    p = sub.add_parser("finalize", help="step 4: consensus + final tables")
    p.add_argument("--sample", required=True)
    p.add_argument("--workbook", action="append", required=True, help="completed reviewer workbook (give twice)")
    p.add_argument("--arbitration", required=True, help="completed arbitration workbook")
    p.add_argument("--output-dir", required=True)
    p.set_defaults(func=_cmd_finalize)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


__all__ = ["build_parser", "main"]
