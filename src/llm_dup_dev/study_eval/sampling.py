"""Deterministic, seeded sampling for the study.

Two sampling jobs live here: drawing the recall evaluation sets (one pair per
duplicate cluster) and drawing the SCLE case list (stratified by confusion cell
for precision, by joint retrieval stratum for recall).

Reproducibility is engineered, not hoped for:

* candidate lists are sorted lexicographically before any RNG call, so dict or
  set iteration order can never leak into the draw;
* each stratum / reference set / cluster gets its own
  ``random.Random(f"{seed}:{procedure}:{key}")``, so adding or reordering one
  stratum cannot perturb another's draw;
* a stratum at or below its cap is taken whole (protocol: "if fewer than N, all
  will be included"); above the cap, ``rng.sample`` draws without replacement.

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
    AdjudicatorOutput,
    PrecisionAnalysisInput,
    RecallAnalysisInput,
    RecallPairRecord,
)
from llm_dup_dev.study_eval.scle.form import EvaluationComponent, SCLEAutoContext

# Confusion cells and joint strata as ordered tuples, so the sampling loop and
# the golden tests see a stable stratum order.
_PRECISION_CELLS: tuple[str, ...] = ("TP", "FP", "FN", "TN")
_RECALL_STRATA: tuple[str, ...] = (
    "both",
    "neither",
    "vigimatch_only",
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


def _draw(rng: random.Random, items: list[str], k: int) -> list[str]:
    """Return ``k`` items from the (already sorted) ``items``.

    At or below the cap, returns all items in their given (sorted) order; above
    it, ``rng.sample`` draws ``k`` without replacement, in draw order.
    """
    if k >= len(items):
        return list(items)
    return rng.sample(items, k)


# --- Recall set sampling ---------------------------------------------------


def sample_one_per_cluster(
    pairs: list[RecallPairRecord],
    target: int = 100,
    *,
    seed: str,
    procedure_id: str = "one_per_cluster_v1",
    manual_priority: bool = False,
) -> tuple[list[RecallPairRecord], SamplingRecord]:
    """Sample up to ``target`` pairs, one per eligible duplicate cluster.

    Clusters are sampled first; then one eligible pair is chosen per sampled
    cluster. When ``manual_priority`` is set (for the Spanish/Dutch sets),
    clusters containing at least one manually classified pair are sampled first
    and topped up with the remaining clusters only if the target is not met;
    within a chosen cluster a manually classified pair is preferred over an
    E2B-derived one. Ineligible pairs are excluded before anything else.
    """
    eligible = [p for p in pairs if p.eligible]
    by_cluster: dict[str, list[RecallPairRecord]] = {}
    for p in sorted(eligible, key=lambda x: x.pair_id):
        by_cluster.setdefault(p.cluster_id, []).append(p)
    cluster_ids = sorted(by_cluster)

    cluster_rng = random.Random(f"{seed}:{procedure_id}:clusters")
    if manual_priority:
        manual_clusters = [
            c
            for c in cluster_ids
            if any(p.provenance == "manual" for p in by_cluster[c])
        ]
        other_clusters = [c for c in cluster_ids if c not in set(manual_clusters)]
        chosen = _draw(cluster_rng, manual_clusters, target)
        if len(chosen) < target:
            chosen += _draw(cluster_rng, other_clusters, target - len(chosen))
    else:
        chosen = _draw(cluster_rng, cluster_ids, target)

    selected: list[RecallPairRecord] = []
    for cluster_id in chosen:
        candidates = by_cluster[cluster_id]
        manual = [p for p in candidates if p.provenance == "manual"]
        pool = manual if (manual_priority and manual) else candidates
        pool = sorted(pool, key=lambda x: x.pair_id)
        if len(pool) == 1:
            pick = pool[0]
        else:
            pick_rng = random.Random(f"{seed}:{procedure_id}:pick:{cluster_id}")
            pick = pick_rng.choice(pool)
        selected.append(pick)

    provenance_counts: dict[str, int] = {}
    for p in eligible:
        provenance_counts[p.provenance] = provenance_counts.get(p.provenance, 0) + 1

    pkg, py = _versions()
    record = SamplingRecord(
        procedure_id=procedure_id,
        seed=seed,
        created_at=_now(),
        package_version=pkg,
        python_version=py,
        target=target,
        n_eligible_clusters=len(cluster_ids),
        tier_counts=provenance_counts,
        selected_pair_ids=[p.pair_id for p in selected],
    )
    return selected, record


# --- SCLE case models ------------------------------------------------------


class SCLECase(BaseModel):
    """One sampled SCLE case: a pair plus its stratum and auto-context."""

    pair_id: str
    evaluation_component: EvaluationComponent
    reference_set_id: str | None = None
    stratum: str
    auto_context: SCLEAutoContext


class SCLESample(BaseModel):
    """The full set of SCLE cases drawn for one evaluation component.

    ``records`` holds one :class:`SamplingRecord` per stratum, so the exact
    sub-seed and available population size for each cell/stratum are auditable.
    """

    evaluation_component: EvaluationComponent
    cases: list[SCLECase]
    records: list[SamplingRecord]


def _trace_summary(output: AdjudicatorOutput) -> str:
    """A short, human-readable decision-trace summary from an adjudicator output."""
    c = output.classification
    if c is None:
        return f"final_label={output.final_label}"
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
    input: PrecisionAnalysisInput,
    result: PrecisionResult,
    per_cell: int = 10,
    *,
    seed: str,
    procedure_id: str = "scle_precision_v1",
) -> SCLESample:
    """Draw up to ``per_cell`` pairs from each confusion cell (TP/FP/FN/TN).

    ``result`` must be the **primary** precision result — the SCLE confusion
    matrix is the adjudicator-vs-reference matrix under the primary rule.
    """
    ref_by_pair = {p.pair_id: p.reference_label for p in input.evaluation_set.pairs}
    by_cell: dict[str, list[str]] = {c: [] for c in _PRECISION_CELLS}
    for pair_id, cell in result.pair_cells.items():
        if cell in by_cell:
            by_cell[cell].append(pair_id)

    pkg, py = _versions()
    cases: list[SCLECase] = []
    records: list[SamplingRecord] = []
    for cell in _PRECISION_CELLS:
        candidates = sorted(by_cell[cell])
        rng = random.Random(f"{seed}:{procedure_id}:{cell}")
        picked = _draw(rng, candidates, per_cell)
        records.append(
            SamplingRecord(
                procedure_id=f"{procedure_id}:{cell}",
                seed=seed,
                created_at=_now(),
                package_version=pkg,
                python_version=py,
                target=per_cell,
                stratum_sizes={cell: len(candidates)},
                selected_pair_ids=list(picked),
            )
        )
        for pair_id in picked:
            output = input.outputs.adjudicator[pair_id]
            is_misclassified = cell in ("FP", "FN")
            adj_positive = is_positive(output.final_label, AnalysisVariant.PRIMARY)
            cases.append(
                SCLECase(
                    pair_id=pair_id,
                    evaluation_component="precision",
                    reference_set_id=None,
                    stratum=cell,
                    auto_context=SCLEAutoContext(
                        pair_id=pair_id,
                        evaluation_component="precision",
                        reference_label=ref_by_pair[pair_id],
                        adjudicator_final_label=output.final_label,
                        vigimatch_retrieved=input.outputs.vigimatch_retrieved.get(
                            pair_id
                        ),
                        confusion_cell=cell,
                        joint_stratum=None,
                        # In the precision set every pair is vigiMatch-positive,
                        # so the methods disagree exactly when the adjudicator
                        # does not also call it positive.
                        methods_disagree=not adj_positive,
                        is_misclassified=is_misclassified,
                        decision_trace_summary=_trace_summary(output),
                    ),
                )
            )
    return SCLESample(evaluation_component="precision", cases=cases, records=records)


def sample_scle_recall(
    input: RecallAnalysisInput,
    result: RecallResult,
    per_stratum: int = 5,
    *,
    seed: str,
    procedure_id: str = "scle_recall_v1",
) -> SCLESample:
    """Draw up to ``per_stratum`` pairs per joint stratum, per reference set.

    ``result`` must be the **primary** recall result. Sampling by joint stratum
    (rather than per method) avoids double-counting a disagreement pair.
    """
    pkg, py = _versions()
    cases: list[SCLECase] = []
    records: list[SamplingRecord] = []

    for set_result in result.sets:
        ref_set_id = set_result.reference_set_id
        # Group this set's pairs by their joint stratum.
        by_stratum: dict[str, list[str]] = {s: [] for s in _RECALL_STRATA}
        for pair_id, stratum in set_result.pair_strata.items():
            by_stratum[stratum].append(pair_id)

        for stratum in _RECALL_STRATA:
            candidates = sorted(by_stratum[stratum])
            rng = random.Random(f"{seed}:{procedure_id}:{ref_set_id}:{stratum}")
            picked = _draw(rng, candidates, per_stratum)
            records.append(
                SamplingRecord(
                    procedure_id=f"{procedure_id}:{ref_set_id}:{stratum}",
                    seed=seed,
                    created_at=_now(),
                    package_version=pkg,
                    python_version=py,
                    target=per_stratum,
                    stratum_sizes={f"{ref_set_id}:{stratum}": len(candidates)},
                    selected_pair_ids=list(picked),
                )
            )
            for pair_id in picked:
                output = input.outputs.adjudicator[pair_id]
                adj_positive = is_positive(output.final_label, AnalysisVariant.PRIMARY)
                cases.append(
                    SCLECase(
                        pair_id=pair_id,
                        evaluation_component="recall",
                        reference_set_id=ref_set_id,
                        stratum=stratum,
                        auto_context=SCLEAutoContext(
                            pair_id=pair_id,
                            evaluation_component="recall",
                            reference_set_id=ref_set_id,
                            # Every recall pair is a known duplicate.
                            reference_label="duplicate",
                            adjudicator_final_label=output.final_label,
                            vigimatch_retrieved=input.outputs.vigimatch_retrieved.get(
                                pair_id
                            ),
                            confusion_cell=None,
                            joint_stratum=stratum,
                            methods_disagree=stratum
                            in ("vigimatch_only", "adjudicator_only"),
                            # The adjudicator "misclassifies" a known duplicate
                            # when it fails to call it positive.
                            is_misclassified=not adj_positive,
                            decision_trace_summary=_trace_summary(output),
                        ),
                    )
                )
    return SCLESample(evaluation_component="recall", cases=cases, records=records)


__all__ = [
    "SamplingRecord",
    "sample_one_per_cluster",
    "SCLECase",
    "SCLESample",
    "sample_scle_precision",
    "sample_scle_recall",
]
