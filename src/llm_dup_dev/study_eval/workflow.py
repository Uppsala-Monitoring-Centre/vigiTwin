"""The four-step evaluation workflow.

Each step is a notebook-friendly function that reads the validated artifacts of
the previous step and writes its own (plus a provenance manifest). The study
runs interactively on the secure side, with human input between steps, so the
steps are file-mediated and idempotent.

1. :func:`run_analysis` — validate reference sets + predictions, compute the
   per-role confusion matrices and performance estimates, emit a per-pair
   prediction/truth table, and (optionally) draw the SCLE sample with ordered
   backups.
2. :func:`generate_reviewer_workbooks` — one reviewer XLSX per reviewer.
3. :func:`build_arbitration` — inter-reviewer agreement statistics + an
   arbitration workbook focused on the disagreement pairs.
4. :func:`finalize` — merge reviews + arbitration into consensus and write the
   final SCLE tables and summaries.

Secondary analyses re-run **only** step 1 with ``sample_scle=False`` and either a
different :class:`PredictionSet` or a re-interpretation ``variant``.
"""

from __future__ import annotations

import csv
import uuid
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from llm_dup_dev.study_eval import loading, results
from llm_dup_dev.study_eval.aggregate import (
    AnalysisVariant,
    PrecisionResult,
    RecallResult,
    run_precision_analysis,
    run_recall_analysis,
)
from llm_dup_dev.study_eval.manifest import build_manifest, write_manifest
from llm_dup_dev.study_eval.preflight import preflight_reference_sets
from llm_dup_dev.study_eval.sampling import (
    SCLESample,
    sample_scle_precision,
    sample_scle_recall,
)
from llm_dup_dev.study_eval.schemas import PredictionSet, ReferenceSet
from llm_dup_dev.study_eval.scle.agreement import AgreementReport, compute_agreement
from llm_dup_dev.study_eval.scle.arbitration import write_arbitration_workbook
from llm_dup_dev.study_eval.scle.consensus import SCLEConsensusResult, merge_consensus
from llm_dup_dev.study_eval.scle.discrepancy import (
    DiscrepancyReport,
    detect_discrepancies,
)
from llm_dup_dev.study_eval.scle.vocabularies import normalise_vocabulary
from llm_dup_dev.study_eval.scle.workbook import (
    read_reviewer_submission,
    write_reviewer_workbook,
)


def _new_run_id() -> str:
    return uuid.uuid4().hex[:12]


# --- Step 1: analysis ------------------------------------------------------


class AnalysisResult(BaseModel):
    """The output of :func:`run_analysis` for one variant."""

    variant: str
    configuration_id: str
    precision_results: list[PrecisionResult]
    recall_result: RecallResult | None
    #: component key -> SCLE sample. Keys: ``precision::<set_id>`` and ``recall``.
    scle_samples: dict[str, SCLESample]
    written_paths: list[str] = []


def _predictions_table_rows(
    reference_sets: list[ReferenceSet],
    predictions: PredictionSet,
    precision_results: list[PrecisionResult],
    recall_result: RecallResult | None,
) -> list[list]:
    """One row per (reference set, pair), showing the inputs the analysis used.

    ``screening_retrieved`` stays a boolean column (``yes``/``no``) and is left
    blank for a set with ``screening_mode: not_evaluated`` — no screening verdict
    was used for such a set even where the prediction record happens to carry
    one. The ``screening_mode`` column is what disambiguates that blank; a status
    string is deliberately not written into the boolean column. The
    ``joint_stratum`` column keeps its name for stability but holds a joint
    screening–adjudicator stratum only under ``observed``; under
    ``not_evaluated`` it holds an adjudicator-only stratum
    (:data:`~llm_dup_dev.study_eval.aggregate.ADJUDICATOR_STRATA`).
    """
    cells: dict[str, str] = {}
    for pr in precision_results:
        cells.update(pr.pair_cells)
    strata: dict[str, str] = {}
    if recall_result is not None:
        for s in recall_result.sets:
            strata.update(s.pair_strata)

    rows: list[list] = []
    for rset in reference_sets:
        screening_observed = rset.screening_mode == "observed"
        for pair in rset.pairs:
            pred = predictions.predictions[pair.pair_id]
            rows.append(
                [
                    pair.pair_id,
                    rset.set_id,
                    rset.role,
                    pair.reference_label or ("duplicate" if rset.role == "recall" else ""),
                    rset.screening_mode,
                    ("yes" if pred.screening_retrieved else "no")
                    if screening_observed
                    else "",
                    pred.adjudicator_label,
                    cells.get(pair.pair_id, ""),
                    strata.get(pair.pair_id, ""),
                ]
            )
    return rows


def run_analysis(
    reference_sets: list[ReferenceSet],
    predictions: PredictionSet,
    *,
    seed: str,
    sample_scle: bool = True,
    scle_precision_per_cell: int = 10,
    scle_recall_per_stratum: int = 10,
    variant: AnalysisVariant = AnalysisVariant.PRIMARY,
    output_dir: str | Path | None = None,
    repo_path: str | Path | None = None,
) -> AnalysisResult:
    """Step 1 — validate, compute estimates, and (optionally) sample SCLE cases.

    ``reference_sets`` may mix precision- and recall-role sets. ``sample_scle``
    draws the SCLE case list; its strata (confusion cells / joint strata) are
    computed under ``variant``. A study typically samples SCLE under the
    ``PRIMARY`` configuration and runs secondary analyses aggregate-only
    (``sample_scle=False``); sampling SCLE under a non-primary variant is allowed
    but warns, since the resulting strata then reflect that variant's positivity
    rule. Enforcing "SCLE only on primary" is a study-execution policy, so it
    lives in the caller, not here.
    """
    if sample_scle and variant != AnalysisVariant.PRIMARY:
        import warnings

        warnings.warn(
            f"Sampling SCLE cases under a non-primary variant "
            f"({variant.value!r}): the SCLE strata will reflect that variant's "
            f"positivity rule rather than the primary confusion matrix. Studies "
            f"usually sample SCLE under the primary configuration — pass "
            f"variant=AnalysisVariant.PRIMARY (or sample_scle=False) if that is "
            f"what you intend.",
            stacklevel=2,
        )

    # One preflight over the whole bundle first, so a caller sees every blocking
    # input problem at once rather than the first one the analysis trips over.
    preflight_reference_sets(reference_sets, predictions)

    precision_sets = [s for s in reference_sets if s.role == "precision"]
    recall_sets = [s for s in reference_sets if s.role == "recall"]

    precision_results = [
        run_precision_analysis(s, predictions, variant) for s in precision_sets
    ]
    recall_result = (
        run_recall_analysis(recall_sets, predictions, variant) if recall_sets else None
    )

    scle_samples: dict[str, SCLESample] = {}
    if sample_scle:
        for rset, pres in zip(precision_sets, precision_results):
            scle_samples[f"precision::{rset.set_id}"] = sample_scle_precision(
                rset, pres, predictions, per_cell=scle_precision_per_cell, seed=seed
            )
        if recall_result is not None:
            scle_samples["recall"] = sample_scle_recall(
                recall_result, predictions, per_stratum=scle_recall_per_stratum, seed=seed
            )

    written: list[str] = []
    if output_dir is not None:
        out = Path(output_dir)
        paths = results.write_study_artifacts(
            out,
            precision_results=precision_results,
            recall_results=[recall_result] if recall_result else [],
        )
        table = out / "predictions_table.csv"
        table.parent.mkdir(parents=True, exist_ok=True)
        with open(table, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                ["pair_id", "reference_set", "role", "reference_label",
                 "screening_mode", "screening_retrieved", "adjudicator_label",
                 "confusion_cell", "joint_stratum"]
            )
            writer.writerows(
                _predictions_table_rows(
                    reference_sets, predictions, precision_results, recall_result
                )
            )
        paths.append(table)
        for key, sample in scle_samples.items():
            safe = key.replace("::", "_")
            paths.append(loading.write_json(sample, out / f"scle_sample_{safe}.json"))
        written = [str(p) for p in paths]
        manifest = build_manifest(
            "analysis",
            _new_run_id(),
            outputs=paths,
            analysis_variant=variant.value,
            ci_method="wilson",
            screening_modes={s.set_id: s.screening_mode for s in reference_sets},
            repo_path=repo_path,
        )
        write_manifest(out, manifest)

    return AnalysisResult(
        variant=variant.value,
        configuration_id=predictions.configuration_id,
        precision_results=precision_results,
        recall_result=recall_result,
        scle_samples=scle_samples,
        written_paths=written,
    )


# --- Step 2: reviewer workbooks --------------------------------------------


def generate_reviewer_workbooks(
    sample: SCLESample,
    reviewers: list[str],
    output_dir: str | Path,
    *,
    packet_dir: str | None = "packets",
    error_stages: Sequence[str] | None = None,
    feature_domains: Sequence[str] | None = None,
) -> list[Path]:
    """Step 2 — write one reviewer workbook per reviewer for ``sample``.

    Each row's Packet cell links to ``{packet_dir}/<pair_id>.html`` (relative to
    the workbook). Pass ``packet_dir=None`` to omit the links.

    ``error_stages`` (the method components a reviewer may flag as contributing
    to a misclassification) and ``feature_domains`` (the feature families a
    reviewer may implicate) are the study's frozen, ordered vocabularies,
    supplied by the caller — the package has no defaults. Each entry becomes one
    tick-only yes/no column and each list is embedded in the workbook, making it
    self-describing. **Omitting a list drops that question from the examination
    entirely.** Both are validated up front, so a bad configuration fails before
    any file is written.
    """
    from llm_dup_dev.study_eval.scle.packets import packet_targets

    stages = (
        normalise_vocabulary(error_stages, label="error_stages")
        if error_stages is not None
        else None
    )
    domains = (
        normalise_vocabulary(feature_domains, label="feature_domains")
        if feature_domains is not None
        else None
    )
    out = Path(output_dir)
    targets = packet_targets(sample, packet_dir) if packet_dir else None
    written: list[Path] = []
    for reviewer in reviewers:
        path = out / f"reviewer_{reviewer}.xlsx"
        p, _ = write_reviewer_workbook(
            sample,
            reviewer,
            path,
            packet_targets=targets,
            error_stages=stages,
            feature_domains=domains,
        )
        written.append(p)
    manifest = build_manifest("workbooks", _new_run_id(), outputs=written)
    write_manifest(out, manifest)
    return written


# --- Step 3: arbitration ---------------------------------------------------


def _check_submission_vocabularies(
    submission,
    error_stages: Sequence[str] | None,
    feature_domains: Sequence[str] | None,
) -> None:
    """Assert a submission was answered under the expected frozen vocabularies.

    Each workbook embeds the lists it was generated from and the read-back
    recovers them, so this is a guard against a workbook generated from a
    different configuration being fed into arbitration or consensus — not the
    primary validation, which happens at read-back.
    """
    expected = {
        "error_stages": error_stages,
        "feature_domains": feature_domains,
    }
    for name, configured in expected.items():
        if configured is None:
            continue
        wanted = list(normalise_vocabulary(configured, label=name))
        found = list(getattr(submission, name))
        if found != wanted:
            raise ValueError(
                f"reviewer {submission.reviewer_id!r}: workbook was generated "
                f"with {name} {found} but {wanted} was configured."
            )


class ArbitrationBuild(BaseModel):
    """The output of :func:`build_arbitration`."""

    agreement: AgreementReport
    discrepancy: DiscrepancyReport
    arbitration_workbook: str
    arbitration_id: str
    written_paths: list[str] = []


def build_arbitration(
    sample: SCLESample,
    reviewer_workbooks: list[Path | str],
    output_dir: str | Path,
    *,
    arbiter_id: str = "",
    packet_dir: str | None = "packets",
    error_stages: Sequence[str] | None = None,
    feature_domains: Sequence[str] | None = None,
) -> ArbitrationBuild:
    """Step 3 — agreement statistics + a disagreement-focused arbitration book.

    Exactly two completed reviewer workbooks are expected (the protocol's two
    independent reviewers). Each arbitration row's Packet cell links to
    ``{packet_dir}/<pair_id>.html``; pass ``packet_dir=None`` to omit the links.

    The workbooks carry the vocabularies they were answered under, so the
    structured dimensions are derived from them. Passing the same frozen
    ``error_stages`` / ``feature_domains`` used to generate them is optional and
    acts as a cross-check that the right workbooks are being processed.
    """
    from llm_dup_dev.study_eval.scle.packets import packet_targets

    if len(reviewer_workbooks) != 2:
        raise ValueError(
            f"expected exactly two reviewer workbooks, got {len(reviewer_workbooks)}."
        )
    out = Path(output_dir)
    sub_a = read_reviewer_submission(reviewer_workbooks[0], sample)
    sub_b = read_reviewer_submission(reviewer_workbooks[1], sample)

    _check_submission_vocabularies(sub_a, error_stages, feature_domains)
    _check_submission_vocabularies(sub_b, error_stages, feature_domains)

    agreement = compute_agreement(sub_a, sub_b)
    discrepancy = detect_discrepancies(sub_a, sub_b)

    component = sample.evaluation_component
    arb_path, arbitration_id = write_arbitration_workbook(
        discrepancy, sub_a, sub_b, sample, out / f"arbitration_{component}.xlsx",
        arbiter_id=arbiter_id,
        packet_targets=packet_targets(sample, packet_dir) if packet_dir else None,
    )
    tables = results.write_study_artifacts(
        out,
        discrepancies={component: discrepancy},
        agreements={component: agreement},
    )
    written = [str(arb_path), *[str(t) for t in tables]]
    manifest = build_manifest(
        "arbitration",
        _new_run_id(),
        inputs=[Path(p) for p in reviewer_workbooks],
        outputs=[arb_path, *tables],
    )
    write_manifest(out, manifest)
    return ArbitrationBuild(
        agreement=agreement,
        discrepancy=discrepancy,
        arbitration_workbook=str(arb_path),
        arbitration_id=arbitration_id,
        written_paths=written,
    )


# --- Step 4: finalize ------------------------------------------------------


class FinalizeResult(BaseModel):
    """The output of :func:`finalize`."""

    consensus: SCLEConsensusResult
    written_paths: list[str] = []


def finalize(
    sample: SCLESample,
    reviewer_workbooks: list[Path | str],
    arbitration_workbook: Path | str,
    output_dir: str | Path,
    *,
    error_stages: Sequence[str] | None = None,
    feature_domains: Sequence[str] | None = None,
) -> FinalizeResult:
    """Step 4 — merge reviews + arbitration into consensus and final tables.

    As in :func:`build_arbitration`, passing the study's frozen vocabularies is
    an optional cross-check; the workbooks carry their own.
    """
    from llm_dup_dev.study_eval.scle.arbitration import read_arbitration

    if len(reviewer_workbooks) != 2:
        raise ValueError(
            f"expected exactly two reviewer workbooks, got {len(reviewer_workbooks)}."
        )
    out = Path(output_dir)
    sub_a = read_reviewer_submission(reviewer_workbooks[0], sample)
    sub_b = read_reviewer_submission(reviewer_workbooks[1], sample)
    _check_submission_vocabularies(sub_a, error_stages, feature_domains)
    _check_submission_vocabularies(sub_b, error_stages, feature_domains)
    discrepancy = detect_discrepancies(sub_a, sub_b)
    arbitration = read_arbitration(arbitration_workbook, sample)
    consensus = merge_consensus(sub_a, sub_b, arbitration, discrepancy)

    component = sample.evaluation_component
    consensus_json = loading.write_json(consensus, out / f"consensus_{component}.json")
    tables = results.write_study_artifacts(
        out,
        consensus={component: consensus},
        discrepancies={component: discrepancy},
    )
    written = [str(consensus_json), *[str(t) for t in tables]]
    manifest = build_manifest(
        "finalize",
        _new_run_id(),
        inputs=[Path(p) for p in reviewer_workbooks] + [Path(arbitration_workbook)],
        outputs=[consensus_json, *tables],
    )
    write_manifest(out, manifest)
    return FinalizeResult(consensus=consensus, written_paths=written)


__all__ = [
    "AnalysisResult",
    "ArbitrationBuild",
    "FinalizeResult",
    "run_analysis",
    "generate_reviewer_workbooks",
    "build_arbitration",
    "finalize",
]
