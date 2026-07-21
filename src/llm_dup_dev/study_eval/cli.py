"""Stage-oriented command line for the performance-evaluation workflow.

The study runs as a sequence of file-mediated stages; each subcommand reads the
validated artifacts of the previous stage and writes its own, plus a provenance
manifest. Because the study spans weeks and separate secure-side sessions, the
stages are idempotent and communicate only through files.

Subcommands::

    sample-recall   draw one pair per cluster from a recall reference set
    aggregate       compute precision/recall tables (all variants)
    scle-sample     draw the SCLE case list (stratified)
    workbooks       write the two reviewer workbooks for a sample
    compare         diff two reviewer submissions -> discrepancy report
    arbitrate       write the arbitration workbook
    consense        merge reviews + arbitration -> consensus + tables

Packet generation needs confidential report content and is therefore a Python
API (``study_eval.scle.packets.generate_packets``) driven by the secure adapter,
not a file-only CLI stage.
"""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path

from llm_dup_dev.study_eval import loading
from llm_dup_dev.study_eval.aggregate import (
    run_precision_analysis,
    run_precision_variants,
    run_recall_analysis,
    run_recall_variants,
)
from llm_dup_dev.study_eval.manifest import build_manifest, write_manifest
from llm_dup_dev.study_eval.results import write_study_artifacts
from llm_dup_dev.study_eval.sampling import (
    SCLESample,
    sample_one_per_cluster,
    sample_scle_precision,
    sample_scle_recall,
)
from llm_dup_dev.study_eval.schemas import RecallEvaluationSet


def _new_run_id() -> str:
    return uuid.uuid4().hex[:12]


# --- sample-recall ---------------------------------------------------------


def _cmd_sample_recall(args: argparse.Namespace) -> int:
    eval_set = loading.load_recall_evaluation_set(args.recall_set)
    selected, record = sample_one_per_cluster(
        eval_set.pairs,
        target=args.target,
        seed=args.seed,
        manual_priority=args.manual_priority,
    )
    sampled = RecallEvaluationSet(
        reference_set_id=eval_set.reference_set_id,
        reference_set_label=eval_set.reference_set_label,
        sampling_record=record,
        pairs=selected,
    )
    out = loading.write_json(sampled, args.output)
    manifest = build_manifest(
        "sampling", _new_run_id(), inputs=[args.recall_set], outputs=[out]
    )
    write_manifest(Path(out).parent, manifest)
    print(f"Sampled {len(selected)} pairs -> {out}")
    return 0


# --- aggregate -------------------------------------------------------------


def _cmd_aggregate(args: argparse.Namespace) -> int:
    precision_results = []
    recall_results = []
    inputs: list[str] = []

    if args.precision_set:
        pin = loading.load_precision_analysis_input(
            args.precision_set, args.method_outputs
        )
        precision_results = list(run_precision_variants(pin).values())
        inputs += [args.precision_set, args.method_outputs]

    if args.recall_set:
        rin = loading.load_recall_analysis_input(args.recall_set, args.method_outputs)
        recall_results = list(run_recall_variants(rin).values())
        inputs += [*args.recall_set, args.method_outputs]

    if not precision_results and not recall_results:
        print("Nothing to do: pass --precision-set and/or --recall-set.")
        return 2

    written = write_study_artifacts(
        args.output_dir,
        precision_results=precision_results,
        recall_results=recall_results,
    )
    manifest = build_manifest(
        "aggregate",
        _new_run_id(),
        inputs=sorted(set(inputs)),
        outputs=written,
        ci_method="wilson",
    )
    write_manifest(args.output_dir, manifest)
    print(f"Wrote {len(written)} artifact(s) -> {args.output_dir}")
    return 0


# --- scle-sample -----------------------------------------------------------


def _cmd_scle_sample(args: argparse.Namespace) -> int:
    if args.component == "precision":
        pin = loading.load_precision_analysis_input(
            args.precision_set, args.method_outputs
        )
        p_result = run_precision_analysis(pin)
        sample = sample_scle_precision(
            pin, p_result, per_cell=args.per_cell, seed=args.seed
        )
        inputs = [args.precision_set, args.method_outputs]
    else:
        rin = loading.load_recall_analysis_input(args.recall_set, args.method_outputs)
        r_result = run_recall_analysis(rin)
        sample = sample_scle_recall(
            rin, r_result, per_stratum=args.per_stratum, seed=args.seed
        )
        inputs = [*args.recall_set, args.method_outputs]

    out = loading.write_json(sample, args.output)
    manifest = build_manifest(
        "sampling", _new_run_id(), inputs=inputs, outputs=[out]
    )
    write_manifest(Path(out).parent, manifest)
    print(f"Sampled {len(sample.cases)} SCLE case(s) -> {out}")
    return 0


# --- workbooks -------------------------------------------------------------


def _cmd_workbooks(args: argparse.Namespace) -> int:
    from llm_dup_dev.study_eval.scle.workbook import write_reviewer_workbook

    sample = SCLESample.model_validate_json(Path(args.sample).read_text())
    out_dir = Path(args.output_dir)
    written = []
    for reviewer in args.reviewer:
        path = out_dir / f"reviewer_{reviewer}.xlsx"
        p, _ = write_reviewer_workbook(sample, reviewer, path)
        written.append(p)
        print(f"Wrote workbook for {reviewer} -> {p}")
    manifest = build_manifest(
        "workbooks", _new_run_id(), inputs=[args.sample], outputs=written
    )
    write_manifest(out_dir, manifest)
    return 0


# --- compare ---------------------------------------------------------------


def _cmd_compare(args: argparse.Namespace) -> int:
    from llm_dup_dev.study_eval.scle.discrepancy import detect_discrepancies
    from llm_dup_dev.study_eval.scle.workbook import read_reviewer_submission

    sample = SCLESample.model_validate_json(Path(args.sample).read_text())
    sub_a = read_reviewer_submission(args.workbook_a, sample)
    sub_b = read_reviewer_submission(args.workbook_b, sample)
    report = detect_discrepancies(sub_a, sub_b)
    out = loading.write_json(report, args.output)
    manifest = build_manifest(
        "submission_validation",
        _new_run_id(),
        inputs=[args.sample, args.workbook_a, args.workbook_b],
        outputs=[out],
    )
    write_manifest(Path(out).parent, manifest)
    print(
        f"{report.n_pairs_with_disagreement} pair(s) with structured "
        f"disagreement -> {out}"
    )
    return 0


# --- arbitrate -------------------------------------------------------------


def _cmd_arbitrate(args: argparse.Namespace) -> int:
    from llm_dup_dev.study_eval.scle.arbitration import write_arbitration_workbook
    from llm_dup_dev.study_eval.scle.discrepancy import detect_discrepancies
    from llm_dup_dev.study_eval.scle.workbook import read_reviewer_submission

    sample = SCLESample.model_validate_json(Path(args.sample).read_text())
    sub_a = read_reviewer_submission(args.workbook_a, sample)
    sub_b = read_reviewer_submission(args.workbook_b, sample)
    report = detect_discrepancies(sub_a, sub_b)
    p, _ = write_arbitration_workbook(
        report, sub_a, sub_b, sample, args.output, arbiter_id=args.arbiter or ""
    )
    manifest = build_manifest(
        "arbitration",
        _new_run_id(),
        inputs=[args.sample, args.workbook_a, args.workbook_b],
        outputs=[p],
    )
    write_manifest(Path(p).parent, manifest)
    print(f"Wrote arbitration workbook -> {p}")
    return 0


# --- consense --------------------------------------------------------------


def _cmd_consense(args: argparse.Namespace) -> int:
    from llm_dup_dev.study_eval.scle.arbitration import read_arbitration
    from llm_dup_dev.study_eval.scle.consensus import merge_consensus
    from llm_dup_dev.study_eval.scle.discrepancy import detect_discrepancies
    from llm_dup_dev.study_eval.scle.workbook import read_reviewer_submission

    sample = SCLESample.model_validate_json(Path(args.sample).read_text())
    sub_a = read_reviewer_submission(args.workbook_a, sample)
    sub_b = read_reviewer_submission(args.workbook_b, sample)
    report = detect_discrepancies(sub_a, sub_b)
    arbitration = read_arbitration(args.arbitration, sample)
    result = merge_consensus(sub_a, sub_b, arbitration, report)

    component = sample.evaluation_component
    out_dir = Path(args.output_dir)
    consensus_json = loading.write_json(result, out_dir / f"consensus_{component}.json")
    tables = write_study_artifacts(
        out_dir,
        consensus={component: result},
        discrepancies={component: report},
    )
    manifest = build_manifest(
        "consensus",
        _new_run_id(),
        inputs=[args.sample, args.workbook_a, args.workbook_b, args.arbitration],
        outputs=[consensus_json, *tables],
    )
    write_manifest(out_dir, manifest)
    print(
        f"Consensus for {len(result.consensus)} case(s); "
        f"{len(result.reference_label_concerns)} reference-label concern(s) "
        f"-> {out_dir}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="study_eval", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sample-recall", help="draw one pair per cluster")
    p.add_argument("--recall-set", required=True)
    p.add_argument("--target", type=int, default=100)
    p.add_argument("--seed", required=True)
    p.add_argument("--manual-priority", action="store_true")
    p.add_argument("--output", required=True)
    p.set_defaults(func=_cmd_sample_recall)

    p = sub.add_parser("aggregate", help="compute precision/recall tables")
    p.add_argument("--precision-set")
    p.add_argument("--recall-set", action="append", default=[])
    p.add_argument("--method-outputs", required=True)
    p.add_argument("--output-dir", required=True)
    p.set_defaults(func=_cmd_aggregate)

    p = sub.add_parser("scle-sample", help="draw the SCLE case list")
    p.add_argument("--component", choices=("precision", "recall"), required=True)
    p.add_argument("--precision-set")
    p.add_argument("--recall-set", action="append", default=[])
    p.add_argument("--method-outputs", required=True)
    p.add_argument("--per-cell", type=int, default=10)
    p.add_argument("--per-stratum", type=int, default=5)
    p.add_argument("--seed", required=True)
    p.add_argument("--output", required=True)
    p.set_defaults(func=_cmd_scle_sample)

    p = sub.add_parser("workbooks", help="write reviewer workbooks")
    p.add_argument("--sample", required=True)
    p.add_argument("--reviewer", action="append", required=True)
    p.add_argument("--output-dir", required=True)
    p.set_defaults(func=_cmd_workbooks)

    p = sub.add_parser("compare", help="diff two reviewer submissions")
    p.add_argument("--sample", required=True)
    p.add_argument("--workbook-a", required=True)
    p.add_argument("--workbook-b", required=True)
    p.add_argument("--output", required=True)
    p.set_defaults(func=_cmd_compare)

    p = sub.add_parser("arbitrate", help="write the arbitration workbook")
    p.add_argument("--sample", required=True)
    p.add_argument("--workbook-a", required=True)
    p.add_argument("--workbook-b", required=True)
    p.add_argument("--arbiter")
    p.add_argument("--output", required=True)
    p.set_defaults(func=_cmd_arbitrate)

    p = sub.add_parser("consense", help="merge reviews + arbitration")
    p.add_argument("--sample", required=True)
    p.add_argument("--workbook-a", required=True)
    p.add_argument("--workbook-b", required=True)
    p.add_argument("--arbitration", required=True)
    p.add_argument("--output-dir", required=True)
    p.set_defaults(func=_cmd_consense)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


__all__ = ["build_parser", "main"]
