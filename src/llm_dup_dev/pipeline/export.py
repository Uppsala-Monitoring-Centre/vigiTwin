"""Persist pipeline output to disk, and read it back.

The pipeline is expensive: each pair costs LLM calls and database queries.
Its output, however, is cheap to re-decide — :func:`classify` is a pure
function of ``(PairFeatures, DecisionThresholds)``. Persisting ``PairFeatures``
therefore lets threshold calibration run offline, days later, with no LLM, no
database and no network. See :mod:`llm_dup_dev.eval.sweep`.

It also matters for where that work can happen. This repository is a library
with no data; the reports live in the secure repository. ``PairFeatures`` is a
non-identifiable feature summary, so persisting it is what allows calibration
to run on the development side.

Format
------
A JSON object keyed by ``pair_id``::

    {"R001__R002": {<PairFeatures>}, ...}

This is the format :mod:`llm_dup_dev.review_html.cli` already accepts via
``--features`` / ``--classifications``; those readers are defined here so one
deserialisation exists rather than two.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from llm_dup_dev.models.classification import ClassificationResult
from llm_dup_dev.models.pair_features import PairFeatures

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from llm_dup_dev.pipeline.runner import PairResult


def _write_keyed(payload: dict[str, dict], path: Path) -> None:
    """Write a {pair_id: model} mapping as indented JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str))


def _read_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def write_pair_features(
    results: Sequence[PairResult],
    path: str | Path,
) -> None:
    """Write the PairFeatures of each result, keyed by pair_id.

    Parameters
    ----------
    results : Sequence[PairResult]
        Output of ``run_pair`` / ``run_batch``. ``BatchResult.results``
        may be passed directly.
    path : str or Path
        Destination. Parent directories are created.
    """
    _write_keyed(
        {r.pair_id: r.features.model_dump(mode="json") for r in results},
        Path(path),
    )


def read_pair_features(path: str | Path) -> dict[str, PairFeatures]:
    """Read a {pair_id: PairFeatures} mapping written by :func:`write_pair_features`."""
    return {
        pair_id: PairFeatures.model_validate(data)
        for pair_id, data in _read_json(Path(path)).items()
    }


def write_classifications(
    results: Sequence[PairResult],
    path: str | Path,
) -> None:
    """Write the ClassificationResult of each result, keyed by pair_id.

    Note ``final_label`` and ``penalty.satisfied`` are properties rather than
    fields, so they are not written; they are pure derivations and are
    recovered on read.
    """
    _write_keyed(
        {r.pair_id: r.classification.model_dump(mode="json") for r in results},
        Path(path),
    )


def read_classifications(path: str | Path) -> dict[str, ClassificationResult]:
    """Read a {pair_id: ClassificationResult} mapping."""
    return {
        pair_id: ClassificationResult.model_validate(data)
        for pair_id, data in _read_json(Path(path)).items()
    }
