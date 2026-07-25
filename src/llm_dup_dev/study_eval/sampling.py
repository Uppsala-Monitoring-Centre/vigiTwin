"""Deterministic, seeded SCLE case sampling.

The dev side's only sampling job is drawing the SCLE case list — stratified by
confusion cell for a precision set, by joint retrieval stratum for a recall set.
(Reference-set construction is done on the secure side.)

Reproducibility is engineered, not hoped for:

* candidate lists are sorted lexicographically before any RNG call, so dict or
  set iteration order can never leak into the draw;
* each stratum gets its own ``random.Random(f"{seed}:{procedure}:{key}")``, so
  adding or reordering one stratum cannot perturb another's draw;
* the draw is a full deterministic permutation of the stratum: the first
  ``k`` are the primary sample; the remainder are the **ordered backups**, so a
  disqualified primary pair can be replaced reproducibly (see
  :func:`promote_backup`). A stratum at or below its cap yields all pairs as
  primary and no backups.

``random.Random`` (Mersenne Twister) is used rather than numpy: it is stdlib,
its algorithm is stable across Python versions, and seeding it with a string
uses a documented, stable path. Every draw emits a :class:`SamplingRecord`.
"""

from __future__ import annotations

import random
import sys
from datetime import datetime, timezone
from importlib import metadata

from pydantic import BaseModel

from llm_dup_dev.study_eval.aggregate import (
    AnalysisVariant,
    PrecisionResult,
    RecallResult,
    is_positive,
)
from llm_dup_dev.study_eval.records import SamplingRecord
from llm_dup_dev.study_eval.schemas import (
    PairPrediction,
    PredictionSet,
    ReferenceSet,
)
from llm_dup_dev.study_eval.scle.form import EvaluationComponent, SCLEAutoContext

# Confusion cells and joint strata as ordered tuples, so the sampling loop and
# the golden tests see a stable stratum order.
_PRECISION_CELLS: tuple[str, ...] = ("TP", "FP", "FN", "TN")
_RECALL_STRATA: tuple[str, ...] = (
    "both",
    "neither",
    "screening_only",
    "adjudicator_only",
)


def _versions() -> tuple[str | None, str]:
    try:
        pkg = metadata.version("llm-dup-dev")
    except metadata.PackageNotFoundError:
        pkg = None
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    return pkg, py


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _split_primary_backups(
    rng: random.Random, items: list[str], k: int
) -> tuple[list[str], list[str]]:
    """Permute ``items`` deterministically; split into first ``k`` + the rest.

    ``items`` must already be sorted. Returns ``(primary, backups)`` where
    ``primary`` is up to ``k`` pairs and ``backups`` is the remainder in draw
    order. At or below the cap, all pairs are primary and backups is empty.
    """
    if not items:
        return [], []
    permutation = rng.sample(items, len(items))
    return permutation[:k], permutation[k:]


# --- SCLE case models ------------------------------------------------------


class SCLECase(BaseModel):
    """One sampled SCLE case: a pair plus its stratum and auto-context.

    ``backup_rank`` is ``None`` for a primary pick, or a 1-based rank within its
    stratum for a backup (backups are consulted in ascending rank order when a
    primary pair is disqualified).
    """

    pair_id: str
    evaluation_component: EvaluationComponent
    reference_set_id: str | None = None
    stratum: str
    backup_rank: int | None = None
    auto_context: SCLEAutoContext


class SCLESample(BaseModel):
    """The SCLE cases drawn for one evaluation component.

    ``cases`` are the primary picks (what goes into the reviewer workbook).
    ``backups`` are the ordered reserve pairs per stratum, drawn from the same
    permutation, used to replace a disqualified primary pick. ``records`` holds
    one :class:`SamplingRecord` per stratum, so the exact sub-seed and available
    population size are auditable.
    """

    evaluation_component: EvaluationComponent
    cases: list[SCLECase]
    backups: list[SCLECase]
    records: list[SamplingRecord]


def _trace_summary(prediction: PairPrediction) -> str:
    """A short, human-readable decision-trace summary from a prediction."""
    c = prediction.classification
    if c is None:
        return f"final_label={prediction.adjudicator_label}"
    parts = [
        f"final_label={c.final_label}",
        f"independence={c.independence_assessment.value}",
    ]
    if c.independence_reason is not None:
        parts.append(f"reason={c.independence_reason.value}")
    if c.relationship_assessment is not None:
        parts.append(f"relationship={c.relationship_assessment.value}")
    if c.explanation:
        parts.append(c.explanation)
    return "; ".join(parts)


# --- SCLE sampling ---------------------------------------------------------


def sample_scle_precision(
    reference_set: ReferenceSet,
    result: PrecisionResult,
    predictions: PredictionSet,
    per_cell: int = 10,
    *,
    seed: str,
    procedure_id: str = "scle_precision_v1",
) -> SCLESample:
    """Draw up to ``per_cell`` pairs from each confusion cell (TP/FP/FN/TN).

    ``result`` must be the **primary** precision result — the SCLE confusion
    matrix is the adjudicator-vs-reference matrix under the primary rule.
    ``per_cell`` sets the per-cell sample size (default 10).
    """
    ref_by_pair = {p.pair_id: p.reference_label for p in reference_set.pairs}
    by_cell: dict[str, list[str]] = {c: [] for c in _PRECISION_CELLS}
    for pair_id, cell in result.pair_cells.items():
        if cell in by_cell:
            by_cell[cell].append(pair_id)

    def build(pair_id: str, cell: str, backup_rank: int | None) -> SCLECase:
        pred = predictions.predictions[pair_id]
        adj_positive = is_positive(pred.adjudicator_label, AnalysisVariant.PRIMARY)
        return SCLECase(
            pair_id=pair_id,
            evaluation_component="precision",
            reference_set_id=None,
            stratum=cell,
            backup_rank=backup_rank,
            auto_context=SCLEAutoContext(
                pair_id=pair_id,
                evaluation_component="precision",
                reference_label=ref_by_pair[pair_id] or "",
                adjudicator_final_label=pred.adjudicator_label,
                screening_retrieved=pred.screening_retrieved,
                confusion_cell=cell,
                joint_stratum=None,
                # In a precision set every pair is screening-positive, so the
                # methods disagree exactly when the adjudicator does not also
                # call it positive.
                methods_disagree=not adj_positive,
                is_misclassified=cell in ("FP", "FN"),
                decision_trace_summary=_trace_summary(pred),
            ),
        )

    pkg, py = _versions()
    cases: list[SCLECase] = []
    backups: list[SCLECase] = []
    records: list[SamplingRecord] = []
    for cell in _PRECISION_CELLS:
        candidates = sorted(by_cell[cell])
        rng = random.Random(f"{seed}:{procedure_id}:{cell}")
        primary, backup_ids = _split_primary_backups(rng, candidates, per_cell)
        cases.extend(build(pid, cell, None) for pid in primary)
        backups.extend(
            build(pid, cell, rank) for rank, pid in enumerate(backup_ids, start=1)
        )
        records.append(
            SamplingRecord(
                procedure_id=f"{procedure_id}:{cell}",
                seed=seed,
                created_at=_now(),
                package_version=pkg,
                python_version=py,
                per_stratum_target=per_cell,
                stratum_sizes={cell: len(candidates)},
                selected_pair_ids=list(primary),
                backup_pair_ids={cell: list(backup_ids)},
            )
        )
    return SCLESample(
        evaluation_component="precision", cases=cases, backups=backups, records=records
    )


def sample_scle_recall(
    result: RecallResult,
    predictions: PredictionSet,
    per_stratum: int = 10,
    *,
    seed: str,
    procedure_id: str = "scle_recall_v1",
) -> SCLESample:
    """Draw up to ``per_stratum`` pairs per joint stratum, per reference set.

    ``result`` must be the **primary** recall result. Sampling by joint stratum
    (rather than per method) avoids double-counting a disagreement pair.
    ``per_stratum`` sets the per-stratum sample size (default 10).
    """

    def build(
        pair_id: str, ref_set_id: str, stratum: str, backup_rank: int | None
    ) -> SCLECase:
        pred = predictions.predictions[pair_id]
        adj_positive = is_positive(pred.adjudicator_label, AnalysisVariant.PRIMARY)
        return SCLECase(
            pair_id=pair_id,
            evaluation_component="recall",
            reference_set_id=ref_set_id,
            stratum=stratum,
            backup_rank=backup_rank,
            auto_context=SCLEAutoContext(
                pair_id=pair_id,
                evaluation_component="recall",
                reference_set_id=ref_set_id,
                # Every recall pair is a known duplicate.
                reference_label="duplicate",
                adjudicator_final_label=pred.adjudicator_label,
                screening_retrieved=pred.screening_retrieved,
                confusion_cell=None,
                joint_stratum=stratum,
                methods_disagree=stratum in ("screening_only", "adjudicator_only"),
                # The adjudicator "misclassifies" a known duplicate when it fails
                # to call it positive.
                is_misclassified=not adj_positive,
                decision_trace_summary=_trace_summary(pred),
            ),
        )

    pkg, py = _versions()
    cases: list[SCLECase] = []
    backups: list[SCLECase] = []
    records: list[SamplingRecord] = []

    for set_result in result.sets:
        ref_set_id = set_result.reference_set_id
        by_stratum: dict[str, list[str]] = {s: [] for s in _RECALL_STRATA}
        for pair_id, stratum in set_result.pair_strata.items():
            by_stratum[stratum].append(pair_id)

        for stratum in _RECALL_STRATA:
            candidates = sorted(by_stratum[stratum])
            rng = random.Random(f"{seed}:{procedure_id}:{ref_set_id}:{stratum}")
            primary, backup_ids = _split_primary_backups(rng, candidates, per_stratum)
            cases.extend(build(pid, ref_set_id, stratum, None) for pid in primary)
            backups.extend(
                build(pid, ref_set_id, stratum, rank)
                for rank, pid in enumerate(backup_ids, start=1)
            )
            records.append(
                SamplingRecord(
                    procedure_id=f"{procedure_id}:{ref_set_id}:{stratum}",
                    seed=seed,
                    created_at=_now(),
                    package_version=pkg,
                    python_version=py,
                    per_stratum_target=per_stratum,
                    stratum_sizes={f"{ref_set_id}:{stratum}": len(candidates)},
                    selected_pair_ids=list(primary),
                    backup_pair_ids={f"{ref_set_id}:{stratum}": list(backup_ids)},
                )
            )
    return SCLESample(
        evaluation_component="recall", cases=cases, backups=backups, records=records
    )


# --- Backup promotion ------------------------------------------------------


def promote_backup(sample: SCLESample, disqualified_pair_id: str) -> SCLESample:
    """Return a new sample with a disqualified primary pair swapped for a backup.

    The lowest-ranked backup in the same stratum (and reference set) replaces the
    disqualified pair; that backup is removed from the reserve. Raises
    ``ValueError`` if the pair is not a current primary pick or no backup remains
    for its stratum. The original ``sample`` is not mutated.
    """
    target = next((c for c in sample.cases if c.pair_id == disqualified_pair_id), None)
    if target is None:
        raise ValueError(
            f"{disqualified_pair_id!r} is not a primary SCLE case in this sample."
        )
    key = (target.reference_set_id, target.stratum)
    candidates = [
        c
        for c in sample.backups
        if (c.reference_set_id, c.stratum) == key
    ]
    if not candidates:
        raise ValueError(
            f"no backup available for stratum {target.stratum!r} "
            f"(reference set {target.reference_set_id!r})."
        )
    promoted = min(candidates, key=lambda c: c.backup_rank or 0)

    new_cases = [
        promoted.model_copy(update={"backup_rank": None})
        if c.pair_id == disqualified_pair_id
        else c
        for c in sample.cases
    ]
    new_backups = [c for c in sample.backups if c.pair_id != promoted.pair_id]
    return sample.model_copy(update={"cases": new_cases, "backups": new_backups})


__all__ = [
    "SamplingRecord",
    "SCLECase",
    "SCLESample",
    "sample_scle_precision",
    "sample_scle_recall",
    "promote_backup",
]
