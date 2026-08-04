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
from llm_dup_dev.study_eval.scle.agreement import AgreementReport
from llm_dup_dev.study_eval.scle.consensus import SCLEConsensusResult
from llm_dup_dev.study_eval.scle.discrepancy import DiscrepancyReport
from llm_dup_dev.study_eval.scle.form import error_stage_name
from llm_dup_dev.study_eval.stats import EstimateWithCI

_ADJ_LABELS = ("Duplicates", "Otherwise_related", "Unrelated", "Abstain")
_REF_LABELS = ("duplicate", "non_duplicate", "abstain")

_PRECISION_METRIC_FIELDS = (
    "screening_baseline_precision",
    "adjudicator_precision",
    "conditional_recall",
    "fp_rejection",
    "proportion_removed_evaluable",
    "proportion_removed_all",
    "abstention_proportion",
)

_RECALL_ESTIMANDS = (
    "recall_screening",
    "recall_serial",
    "recall_adjudicator_ceiling",
)

def _consensus_dimensions(result: SCLEConsensusResult) -> list[str]:
    """The dimensions to tabulate, as recorded by the discrepancy report.

    The set depends on the study's error-stage list, so it is read off the
    artifact rather than hard-coded here.
    """
    return list(result.discrepancy_report.structured_dimensions)


def _consensus_value(assessment, dimension: str):
    """One consensus assessment's value on a dimension, as a scalar or list.

    The per-stage dimensions are stored collapsed into ``error_stages``, so they
    are expanded back to yes/no here.
    """
    stage = error_stage_name(dimension)
    if stage is not None:
        return "yes" if stage in assessment.error_stages else "no"
    return getattr(assessment, dimension)


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
    """Long-format recall estimands, one row per (variant, set, estimand).

    A set whose ``screening_mode`` is ``not_evaluated`` contributes **no rows**
    for the screening-dependent estimands rather than rows with blank values —
    blank already means "estimate undefined at n=0" (see :func:`_estimate_cells`),
    and reusing it would conflate the two. The ``screening_mode`` column tells a
    reader why the rows are absent.
    """
    rows = []
    for r in results:
        for s in r.sets:
            for estimand in _RECALL_ESTIMANDS:
                est: EstimateWithCI | None = getattr(s, estimand)
                if est is None:  # not estimated for this set
                    continue
                rows.append(
                    [r.variant, s.reference_set_id, s.screening_mode, estimand,
                     *_estimate_cells(est)]
                )
            if s.screening_mode == "observed":
                # Gated on the mode, not on the value: an observed set whose
                # reduction is undefined still gets its row, blank as before.
                reduction = (
                    "" if s.recall_reduction is None else f"{s.recall_reduction:.6f}"
                )
                rows.append(
                    [r.variant, s.reference_set_id, s.screening_mode,
                     "recall_reduction", "", "", reduction, "", "", ""]
                )
    return _write_csv(
        Path(path),
        ["variant", "reference_set", "screening_mode", "estimand", "numerator",
         "denominator", "proportion", "ci_low", "ci_high", "ci_method"],
        rows,
    )


def write_recall_joint_classification_csv(
    results: list[RecallResult], path: str | Path
) -> Path:
    """Joint screening–adjudicator strata, for the sets that have them.

    The joint strata need a screening verdict, so a ``not_evaluated`` set has
    none and contributes no rows. The ``screening_mode`` column makes the scope
    of the table explicit in the file itself.
    """
    rows = []
    for r in results:
        for s in r.sets:
            if s.joint_counts is None or s.joint_proportions is None:
                continue
            for stratum in JOINT_STRATA:
                est = s.joint_proportions[stratum]
                rows.append(
                    [r.variant, s.reference_set_id, s.screening_mode, stratum,
                     s.joint_counts[stratum],
                     "" if est.proportion is None else f"{est.proportion:.6f}",
                     "" if est.ci_low is None else f"{est.ci_low:.6f}",
                     "" if est.ci_high is None else f"{est.ci_high:.6f}"]
                )
    return _write_csv(
        Path(path),
        ["variant", "reference_set", "screening_mode", "stratum", "count",
         "proportion", "ci_low", "ci_high"],
        rows,
    )


# --- Secondary comparison (variants vs primary) ----------------------------


def write_secondary_comparison_csv(
    precision_results: list[PrecisionResult],
    recall_results: list[RecallResult],
    path: str | Path,
) -> Path:
    """Each variant estimate against its own set's primary estimate.

    Both components key their baselines **by set**, so a run pooling several
    reference sets across variants compares like with like: a variant result is
    only ever differenced against the primary result for the same set, and the
    ``set`` column names the set that result actually came from. A variant
    result for a set with no primary result in ``precision_results`` /
    ``recall_results`` raises ``KeyError`` — the caller assembled an
    incomparable bundle, and a silently dropped row would hide that.
    """
    rows = []
    base_by_precision_set = {
        r.set_id: r for r in precision_results if r.variant == "primary"
    }
    if base_by_precision_set:
        for pr in precision_results:
            if pr.variant == "primary":
                continue
            base_result = base_by_precision_set[pr.set_id]
            for field in _PRECISION_METRIC_FIELDS:
                base = getattr(base_result, field).proportion
                var = getattr(pr, field).proportion
                rows.append(["precision", pr.set_id, field, pr.variant,
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
                    base_est = getattr(base_set, estimand)
                    var_est = getattr(s, estimand)
                    if base_est is None or var_est is None:
                        # Not estimated for this set (screening not evaluated);
                        # there is nothing to compare a variant against.
                        continue
                    bp = base_est.proportion
                    vp = var_est.proportion
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
    dimensions = _consensus_dimensions(result)
    header = ["pair_id"]
    for dim in dimensions:
        header.append(dim)
        header.append(f"{dim}_source")
    header.extend(["free_text_materially_different", "arbiter_rationale"])
    rows = []
    for a in result.consensus:
        row = [a.pair_id]
        for dim in dimensions:
            value = _consensus_value(a, dim)
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
    dimensions = _consensus_dimensions(result)
    counts: dict[tuple[str, str], int] = {}
    for a in result.consensus:
        for dim in dimensions:
            value = _consensus_value(a, dim)
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


def write_agreement_csv(report: AgreementReport, path: str | Path) -> Path:
    rows = []
    for d in report.dimensions:
        rows.append(
            [
                d.field,
                d.n_compared,
                d.n_agree,
                "" if d.percent_agreement is None else f"{d.percent_agreement:.4f}",
                "" if d.cohens_kappa is None else f"{d.cohens_kappa:.4f}",
            ]
        )
    rows.append(
        [
            "OVERALL",
            "",
            "",
            "" if report.overall_percent_agreement is None
            else f"{report.overall_percent_agreement:.4f}",
            "",
        ]
    )
    return _write_csv(
        Path(path),
        ["dimension", "n_compared", "n_agree", "percent_agreement", "cohens_kappa"],
        rows,
    )


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
            f"- Screening baseline precision: "
            f"{_ci_str(primary_p.screening_baseline_precision)}",
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
            if s.screening_mode == "observed":
                reduction = (
                    "n/a" if s.recall_reduction is None else f"{s.recall_reduction:.4f}"
                )
                lines.append(
                    f"- **{s.reference_set_label}** (n={s.n_pairs}): "
                    f"screening {_ci_str(s.recall_screening)}, "
                    f"serial {_ci_str(s.recall_serial)}, "
                    f"ceiling {_ci_str(s.recall_adjudicator_ceiling)}, "
                    f"reduction {reduction}"
                )
            else:
                # No screening figure of any kind is printed — not a number, and
                # not `n/a` either, which here means "undefined at n=0". Under
                # this mode the adjudicator estimand is a direct estimate over
                # the supplied pairs, not a ceiling, so it is labelled as such.
                lines.append(
                    f"- **{s.reference_set_label}** (n={s.n_pairs}): "
                    f"screening not evaluated — no screening-algorithm results "
                    f"were supplied for this set, so screening performance is "
                    f"outside the estimand and all {s.n_pairs} supplied pairs "
                    f"went directly to the adjudicator; "
                    f"adjudicator recall on the supplied reference set "
                    f"{_ci_str(s.recall_adjudicator_ceiling)}"
                )
        lines.append("")
        if primary_r.screening_not_evaluated_set_ids:
            lines += [
                f"Screening estimands above cover only the reference set(s) with "
                f"observed screening results "
                f"({', '.join(primary_r.screening_evaluated_set_ids) or 'none'}). "
                f"They are not estimated for "
                f"{', '.join(primary_r.screening_not_evaluated_set_ids)}, which "
                f"contribute nothing to any screening figure — an absent value "
                f"there is not a zero and not a perfect score.",
                "",
            ]
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
    agreements: dict[str, AgreementReport] | None = None,
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

    for component, agreement in (agreements or {}).items():
        written.append(
            write_agreement_csv(agreement, out / f"scle_agreement_{component}.csv")
        )
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
    "write_agreement_csv",
    "write_scle_consensus_csv",
    "write_scle_dimension_frequencies_csv",
    "write_scle_discrepancies_csv",
    "write_scle_reference_label_concerns_csv",
    "summary_to_markdown",
    "write_study_artifacts",
]
