"""Read and write the canonical study-eval models as JSON.

The models are ordinary pydantic, so serialization is just ``model_dump_json`` /
``model_validate_json``; the value this module adds is a named, typed entry point
per artifact (so a caller does not import model classes to load a file). The
secure adapter typically writes the reference sets and the prediction set once
(fixed by the freeze), then the workflow steps read them back.

:func:`reference_set_from_report_pairs` is the one constructor here rather than
a reader. It exists because the two pair models differ in field names
(``ReportPair.report_1_id`` vs ``ReferencePair.report_id1``) while meaning the
same thing, and every caller with a frozen ``pairs.json`` in hand was
hand-writing the same rename. Set *construction* proper — which pairs a study
evaluates, sampled how — remains the secure side's job; this only re-shapes
pairs a caller already has.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence, TypeVar

from pydantic import BaseModel

from llm_dup_dev.models.report_pair import ReportPair
from llm_dup_dev.study_eval.schemas import (
    AnalysisRole,
    PredictionSet,
    ReferenceLabel,
    ReferencePair,
    ReferenceSet,
    ScreeningMode,
)

_M = TypeVar("_M", bound=BaseModel)


def _read_model(model: type[_M], path: str | Path) -> _M:
    return model.model_validate_json(Path(path).read_text())


def load_reference_set(path: str | Path) -> ReferenceSet:
    """Load a :class:`ReferenceSet` from JSON."""
    return _read_model(ReferenceSet, path)


def load_reference_sets(paths: list[str | Path]) -> list[ReferenceSet]:
    """Load several :class:`ReferenceSet` files."""
    return [load_reference_set(p) for p in paths]


def load_prediction_set(path: str | Path) -> PredictionSet:
    """Load a :class:`PredictionSet` from JSON."""
    return _read_model(PredictionSet, path)


def load_report_pairs(path: str | Path) -> list[ReportPair]:
    """Load a JSON array of :class:`ReportPair` — a frozen ``pairs.json``.

    Pair identity is validated, not repaired: an artifact whose ids predate the
    canonical convention is rejected here with no escape hatch, because a
    reference set feeds real estimates and rewriting a ``pair_id`` in place
    would silently break its join against annotations and predictions still
    keyed on the old one. (``review_html`` can still *render* such an artifact —
    see ``ALLOW_NONCANONICAL_CONTEXT_KEY`` — but that path only displays.)
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(
            f"{path}: expected a JSON array of ReportPair objects, got "
            f"{type(raw).__name__}"
        )
    return [ReportPair.model_validate(item) for item in raw]


def reference_set_from_report_pairs(
    pairs: Sequence[ReportPair],
    *,
    set_id: str,
    role: AnalysisRole = "recall",
    label: str | None = None,
    provenance: str | None = None,
    screening_mode: ScreeningMode = "observed",
    reference_labels: Mapping[str, ReferenceLabel] | None = None,
    cluster_ids: Mapping[str, str] | None = None,
) -> ReferenceSet:
    """Build a :class:`ReferenceSet` from pairs a caller already holds.

    Handles the field rename between the two pair models and nothing else — the
    ids are carried across as they are, so a non-canonical pair fails at
    ``ReportPair`` load time rather than being quietly re-derived here.

    ``reference_labels`` and ``cluster_ids`` are keyed by ``pair_id``. A
    precision set needs a label for every pair (the model enforces it and names
    the ones missing); a recall set is positive controls, so leave labels unset.

    ``role`` defaults to ``"recall"`` because that is the cheapest legal set: it
    needs no per-pair labels, which matters for a caller who only wants to
    *describe* the pairs (see
    :mod:`llm_dup_dev.characterization`) and should not have to invent a mapping
    from the annotation vocabulary (``duplicates`` / ``otherwise_related`` /
    ``unrelated``) onto the human-reference one (``duplicate`` /
    ``non_duplicate``) to do it. That collapse is a study decision, and this
    function deliberately will not make it for you.

    ``screening_mode`` keeps the model's ``"observed"`` default. Declaring
    ``"not_evaluated"`` says screening performance is outside this set's
    estimand, which is a claim about the study — never inferred from a file
    being absent.
    """
    reference_labels = reference_labels or {}
    cluster_ids = cluster_ids or {}

    return ReferenceSet(
        set_id=set_id,
        role=role,
        label=label,
        provenance=provenance,
        screening_mode=screening_mode,
        pairs=[
            ReferencePair(
                pair_id=pair.pair_id,
                report_id1=pair.report_1_id,
                report_id2=pair.report_2_id,
                cluster_id=cluster_ids.get(pair.pair_id),
                reference_label=reference_labels.get(pair.pair_id),
            )
            for pair in pairs
        ],
    )


def write_json(instance: BaseModel, path: str | Path) -> Path:
    """Write any study-eval pydantic model to ``path`` as indented JSON."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(instance.model_dump_json(indent=2))
    return out


def write_json_dict(data: dict, path: str | Path) -> Path:
    """Write a plain dict (e.g. a result table) to ``path`` as indented JSON."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2, sort_keys=True))
    return out


__all__ = [
    "load_reference_set",
    "load_reference_sets",
    "load_prediction_set",
    "load_report_pairs",
    "reference_set_from_report_pairs",
    "write_json",
    "write_json_dict",
]
