"""Final machine-readable result tables and a human-readable summary.

Every table is written as long-format CSV (for statistical review) and the whole
set is also captured in a typed :class:`StudyResults` JSON (machine-readable),
plus one markdown summary in the style of :mod:`llm_dup_dev.eval.report`.

The functions here are pure formatting over the result objects produced by
:mod:`llm_dup_dev.study_eval.aggregate` and the SCLE workflow; they do no
computation of their own beyond arranging rows.
"""

from __future__ import annotations

import csv
from pathlib import Path

from pydantic import BaseModel

from llm_dup_dev.study_eval.aggregate import (
    JOINT_STRATA,
    PrecisionResult,
    RecallResult,
)
from llm_dup_dev.study_eval.scle.consensus import SCLEConsensusResult
from llm_dup_dev.study_eval.scle.discrepancy import DiscrepancyReport
from llm_dup_dev.study_eval.stats import EstimateWithCI

_ADJ_LABELS = ("Duplicates", "Otherwise_related", "Unrelated", "Abstain")
_REF_LABELS = ("duplicate", "non_duplicate", "abstain")

_PRECISION_METRIC_FIELDS = (
    "vigimatch_baseline_precision",
    "adjudicator_precision",
    "conditional_recall",
    "fp_rejection",
    "proportion_removed_evaluable",
    "proportion_removed_all",
    "abstention_proportion",
)

_RECALL_ESTIMANDS = (
    "recall_vigimatch",
    "recall_serial",
    "recall_adjudicator_ceiling",
)

_CONSENSUS_DIMENSIONS = (
    "case_difficulty",
    "reference_label_issue",
    "never_event",
    "unexpected_method_behaviour",
    "material_input_data_limitation",
    "input_data_limitation_category",
    "primary_error_source",
    "implicated_feature_domains",
)


class StudyResults(BaseModel):
    """The whole machine-readable result set for one study run."""

    precision_results: list[PrecisionResult] = []
    recall_results: list[RecallResult] = []
    consensus: dict[str, SCLEConsensusResult] = {}


def _write_csv(path: Path, header: list[str], rows: list[list]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def _estimate_cells(est: EstimateWithCI) -> list:
    return [
        est.numerator,
        est.denominator,
        "" if est.proportion is None else f"{est.proportion:.6f}",
        "" if est.ci_low is None else f"{est.ci_low:.6f}",
        "" if est.ci_high is None else f"{est.ci_high:.6f}",
        est.ci_method,
    ]


# --- Precision tables ------------------------------------------------------


def write_precision_metrics_csv(results: list[PrecisionResult], path: str | Path) -> Path:
    rows = []
    for r in results:
        for field in _PRECISION_METRIC_FIELDS:
            est: EstimateWithCI = getattr(r, field)
            rows.append([r.variant, r.set_id, field, *_estimate_cells(est)])
    return _write_csv(
        Path(path),
        ["variant", "set_id", "metric", "numerator", "denominator", "proportion",
         "ci_low", "ci_high", "ci_method"],
        rows,
    )


def write_precision_cross_classification_csv(
    results: list[PrecisionResult], path: str | Path
) -> Path:
    rows = []
    for r in results:
        for adj in _ADJ_LABELS:
            for ref in _REF_LABELS:
                rows.append([r.variant, r.set_id, adj, ref, r.cross_classification[adj][ref]])
    return _write_csv(
        Path(path),
        ["variant", "set_id", "adjudicator_label", "reference_label", "count"],
        rows,
    )


# --- Recall tables ---------------------------------------------------------


def write_recall_metrics_csv(results: list[RecallResult], path: str | Path) -> Path:
    rows = []
    for r in results:
        for s in r.sets:
            for estimand in _RECALL_ESTIMANDS:
                est: EstimateWithCI = getattr(s, estimand)
                rows.append([r.variant, s.reference_set_id, estimand, *_estimate_cells(est)])
            reduction = "" if s.recall_reduction is None else f"{s.recall_reduction:.6f}"
            rows.append(
                [r.variant, s.reference_set_id, "recall_reduction", "", "", reduction, "", "", ""]
            )
    return _write_csv(
        Path(path),
        ["variant", "reference_set", "estimand", "numerator", "denominator",
         "proportion", "ci_low", "ci_high", "ci_method"],
        rows,
    )


def write_recall_joint_classification_csv(
    results: list[RecallResult], path: str | Path
) -> Path:
    rows = []
    for r in results:
        for s in r.sets:
            for stratum in JOINT_STRATA:
                est = s.joint_proportions[stratum]
                rows.append(
                    [r.variant, s.reference_set_id, stratum, s.joint_counts[stratum],
                     "" if est.proportion is None else f"{est.proportion:.6f}",
                     "" if est.ci_low is None else f"{est.ci_low:.6f}",
                     "" if est.ci_high is None else f"{est.ci_high:.6f}"]
                )
    return _write_csv(
        Path(path),
        ["variant", "reference_set", "stratum", "count", "proportion", "ci_low", "ci_high"],
        rows,
    )


# --- Secondary comparison (variants vs primary) ----------------------------


def write_secondary_comparison_csv(
    precision_results: list[PrecisionResult],
    recall_results: list[RecallResult],
    path: str | Path,
) -> Path:
    rows = []
    primary_p = next((r for r in precision_results if r.variant == "primary"), None)
    if primary_p is not None:
        for pr in precision_results:
            if pr.variant == "primary":
                continue
            for field in _PRECISION_METRIC_FIELDS:
                base = getattr(primary_p, field).proportion
                var = getattr(pr, field).proportion
                rows.append(["precision", primary_p.set_id, field, pr.variant,
                             _fmt(base), _fmt(var), _delta(base, var)])
    primary_r = next((r for r in recall_results if r.variant == "primary"), None)
    if primary_r is not None:
        base_by_set = {s.reference_set_id: s for s in primary_r.sets}
        for rr in recall_results:
            if rr.variant == "primary":
                continue
            for s in rr.sets:
                base_set = base_by_set[s.reference_set_id]
                for estimand in _RECALL_ESTIMANDS:
                    bp = getattr(base_set, estimand).proportion
                    vp = getattr(s, estimand).proportion
                    rows.append(["recall", s.reference_set_id, estimand, rr.variant,
                                 _fmt(bp), _fmt(vp), _delta(bp, vp)])
    return _write_csv(
        Path(path),
        ["component", "set", "metric", "variant", "primary_proportion",
         "variant_proportion", "delta"],
        rows,
    )


def write_evaluable_counts_csv(
    precision_results: list[PrecisionResult],
    recall_results: list[RecallResult],
    path: str | Path,
) -> Path:
    rows = []
    for pr in precision_results:
        rows.append(["precision", pr.variant, pr.set_id, pr.n_pairs, pr.n_evaluable,
                     pr.n_reference_abstain, pr.n_adjudicator_abstain])
    for rr in recall_results:
        for s in rr.sets:
            rows.append(["recall", rr.variant, s.reference_set_id, s.n_pairs, s.n_pairs,
                         0, s.n_adjudicator_abstain])
    return _write_csv(
        Path(path),
        ["component", "variant", "set", "n_pairs", "n_evaluable",
         "n_reference_abstain", "n_adjudicator_abstain"],
        rows,
    )


# --- SCLE tables -----------------------------------------------------------


def write_scle_consensus_csv(result: SCLEConsensusResult, path: str | Path) -> Path:
    header = ["pair_id"]
    for dim in _CONSENSUS_DIMENSIONS:
        header.append(dim)
        header.append(f"{dim}_source")
    header.extend(["free_text_materially_different", "arbiter_rationale"])
    rows = []
    for a in result.consensus:
        row = [a.pair_id]
        for dim in _CONSENSUS_DIMENSIONS:
            value = getattr(a, dim)
            if isinstance(value, list):
                value = ";".join(value)
            row.append("" if value is None else value)
            row.append(a.dimension_sources.get(dim, ""))
        row.extend([str(a.free_text_materially_different), a.arbiter_rationale])
        rows.append(row)
    return _write_csv(Path(path), header, rows)


def write_scle_dimension_frequencies_csv(
    result: SCLEConsensusResult, path: str | Path
) -> Path:
    counts: dict[tuple[str, str], int] = {}
    for a in result.consensus:
        for dim in _CONSENSUS_DIMENSIONS:
            value = getattr(a, dim)
            if isinstance(value, list):
                for v in value:
                    counts[(dim, v)] = counts.get((dim, v), 0) + 1
                continue
            key = (dim, "" if value is None else str(value))
            counts[key] = counts.get(key, 0) + 1
    rows = [[dim, value, n] for (dim, value), n in sorted(counts.items())]
    return _write_csv(Path(path), ["dimension", "value", "count"], rows)


def write_scle_discrepancies_csv(report: DiscrepancyReport, path: str | Path) -> Path:
    rows = []
    for pd in report.pair_discrepancies:
        for d in pd.dimension_disagreements:
            rows.append([pd.pair_id, d.field, d.value_a or "", d.value_b or ""])
    return _write_csv(
        Path(path), ["pair_id", "field", "value_a", "value_b"], rows
    )


def write_scle_reference_label_concerns_csv(
    result: SCLEConsensusResult, path: str | Path
) -> Path:
    concern_set = set(result.reference_label_concerns)
    rows = [
        [a.pair_id, a.reference_label_issue or ""]
        for a in result.consensus
        if a.pair_id in concern_set
    ]
    return _write_csv(Path(path), ["pair_id", "reference_label_issue"], rows)


# --- Summary + orchestration ----------------------------------------------


def _fmt(x: float | None) -> str:
    return "" if x is None else f"{x:.4f}"


def _delta(a: float | None, b: float | None) -> str:
    if a is None or b is None:
        return ""
    return f"{b - a:.4f}"


def summary_to_markdown(
    precision_results: list[PrecisionResult],
    recall_results: list[RecallResult],
) -> str:
    lines = ["# Duplicate-adjudication performance evaluation — summary", ""]
    primary_p = next((r for r in precision_results if r.variant == "primary"), None)
    if primary_p is not None:
        lines += [
            "## Precision (primary)",
            "",
            f"- Evaluable pairs: {primary_p.n_evaluable} "
            f"(excluded reference-abstain: {primary_p.n_reference_abstain})",
            f"- vigiMatch2025 baseline precision: "
            f"{_ci_str(primary_p.vigimatch_baseline_precision)}",
            f"- Adjudicator precision: {_ci_str(primary_p.adjudicator_precision)}",
            f"- Conditional recall: {_ci_str(primary_p.conditional_recall)}",
            f"- False positives rejected: {_ci_str(primary_p.fp_rejection)}",
            f"- Candidates removed (evaluable): "
            f"{_ci_str(primary_p.proportion_removed_evaluable)}",
            f"- Adjudicator abstentions: {_ci_str(primary_p.abstention_proportion)}",
            "",
        ]
    primary_r = next((r for r in recall_results if r.variant == "primary"), None)
    if primary_r is not None:
        lines += ["## Recall (primary), by reference set", ""]
        for s in primary_r.sets:
            reduction = "n/a" if s.recall_reduction is None else f"{s.recall_reduction:.4f}"
            lines.append(
                f"- **{s.reference_set_label}** (n={s.n_pairs}): "
                f"vigiMatch {_ci_str(s.recall_vigimatch)}, "
                f"serial {_ci_str(s.recall_serial)}, "
                f"ceiling {_ci_str(s.recall_adjudicator_ceiling)}, "
                f"reduction {reduction}"
            )
        lines.append("")
    return "\n".join(lines)


def _ci_str(est: EstimateWithCI) -> str:
    if est.proportion is None:
        return f"n/a ({est.numerator}/{est.denominator})"
    return (
        f"{est.proportion:.3f} [{est.ci_low:.3f}, {est.ci_high:.3f}] "
        f"({est.numerator}/{est.denominator})"
    )


def write_study_artifacts(
    output_dir: str | Path,
    *,
    precision_results: list[PrecisionResult] | None = None,
    recall_results: list[RecallResult] | None = None,
    consensus: dict[str, SCLEConsensusResult] | None = None,
    discrepancies: dict[str, DiscrepancyReport] | None = None,
) -> list[Path]:
    """Write the full set of result artifacts under ``output_dir``.

    Returns the list of written paths. Only the tables for the provided inputs
    are written, so aggregate-only and SCLE-only runs both work.
    """
    out = Path(output_dir)
    precision_results = precision_results or []
    recall_results = recall_results or []
    written: list[Path] = []

    if precision_results:
        written.append(write_precision_metrics_csv(precision_results, out / "precision_metrics.csv"))
        written.append(
            write_precision_cross_classification_csv(
                precision_results, out / "precision_cross_classification.csv"
            )
        )
    if recall_results:
        written.append(write_recall_metrics_csv(recall_results, out / "recall_metrics.csv"))
        written.append(
            write_recall_joint_classification_csv(
                recall_results, out / "recall_joint_classification.csv"
            )
        )
    if precision_results or recall_results:
        written.append(
            write_secondary_comparison_csv(
                precision_results, recall_results, out / "secondary_comparison.csv"
            )
        )
        written.append(
            write_evaluable_counts_csv(
                precision_results, recall_results, out / "evaluable_counts.csv"
            )
        )
        summary = summary_to_markdown(precision_results, recall_results)
        summary_path = out / "summary.md"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(summary, encoding="utf-8")
        written.append(summary_path)

    for component, report in (discrepancies or {}).items():
        written.append(
            write_scle_discrepancies_csv(report, out / f"scle_discrepancies_{component}.csv")
        )
    for component, result in (consensus or {}).items():
        written.append(
            write_scle_consensus_csv(result, out / f"scle_consensus_{component}.csv")
        )
        written.append(
            write_scle_dimension_frequencies_csv(
                result, out / f"scle_dimension_frequencies_{component}.csv"
            )
        )
        written.append(
            write_scle_reference_label_concerns_csv(
                result, out / f"scle_reference_label_concerns_{component}.csv"
            )
        )

    study = StudyResults(
        precision_results=precision_results,
        recall_results=recall_results,
        consensus=consensus or {},
    )
    results_path = out / "study_results.json"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(study.model_dump_json(indent=2), encoding="utf-8")
    written.append(results_path)

    return written


__all__ = [
    "StudyResults",
    "write_precision_metrics_csv",
    "write_precision_cross_classification_csv",
    "write_recall_metrics_csv",
    "write_recall_joint_classification_csv",
    "write_secondary_comparison_csv",
    "write_evaluable_counts_csv",
    "write_scle_consensus_csv",
    "write_scle_dimension_frequencies_csv",
    "write_scle_discrepancies_csv",
    "write_scle_reference_label_concerns_csv",
    "summary_to_markdown",
    "write_study_artifacts",
]
