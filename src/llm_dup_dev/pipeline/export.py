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

Formats
-------
``features.json`` / ``classifications.json`` are each a JSON object keyed by
``pair_id``::

    {"R001__R002": {<PairFeatures>}, ...}

This is the format :mod:`llm_dup_dev.review_html.cli` already accepts via
``--features`` / ``--classifications``; those readers are defined here so one
deserialisation exists rather than two.

``pair_results.json`` is the richer, whole-``PairResult`` artifact — also a JSON
object keyed by ``pair_id``, but each value carries every field of a
:class:`~llm_dup_dev.pipeline.runner.PairResult`::

    {"R001__R002": {
        "final_label": ...,
        "features": {<PairFeatures>},
        "classification": {<ClassificationResult>},
        "narrative_verdict": {<NarrativeVerdict>} | null,
        "ae_clinical_picture": {<AEClinicalPictureResult>} | null,
        "source_case_assessment": {<SourceCaseAssessment>} | null,
        "narrative_demographics_1": {<NarrativeDemographics>} | null,
        "narrative_demographics_2": {<NarrativeDemographics>} | null,
    }, ...}

``final_label`` is a derived :class:`PairResult` property, written for parity and
readability but recomputed (not trusted) on read. The nested ``features`` and
``classification`` are the same dumps :func:`write_pair_features` /
:func:`write_classifications` produce, so ``pair_results.json`` is a superset of
the split files and the two can be produced side by side.
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


def _dump_optional(model) -> dict | None:
    """Dump an optional pydantic sub-model, preserving ``None`` as JSON null."""
    return None if model is None else model.model_dump(mode="json")


def write_pair_results(
    results: Sequence[PairResult],
    path: str | Path,
) -> None:
    """Write the whole ``PairResult`` of each result, keyed by pair_id.

    Produces the ``pair_results.json`` format documented in the module
    docstring: a JSON object keyed by ``pair_id`` whose values carry every
    field of a :class:`~llm_dup_dev.pipeline.runner.PairResult`. Absent
    optional fields are written as JSON ``null``.

    ``final_label`` is a derived property rather than a stored field; it is
    written for parity with the artifact format but recomputed (not trusted)
    by :func:`read_pair_results`.

    Parameters
    ----------
    results : Sequence[PairResult]
        Output of ``run_pair`` / ``run_batch``. ``BatchResult.results``
        may be passed directly.
    path : str or Path
        Destination. Parent directories are created.
    """
    _write_keyed(
        {
            r.pair_id: {
                "final_label": r.final_label,
                "features": r.features.model_dump(mode="json"),
                "classification": r.classification.model_dump(mode="json"),
                "narrative_verdict": _dump_optional(r.narrative_verdict),
                "ae_clinical_picture": _dump_optional(r.ae_clinical_picture),
                "source_case_assessment": _dump_optional(
                    r.source_case_assessment
                ),
                "narrative_demographics_1": _dump_optional(
                    r.narrative_demographics_1
                ),
                "narrative_demographics_2": _dump_optional(
                    r.narrative_demographics_2
                ),
            }
            for r in results
        },
        Path(path),
    )


def read_pair_results(path: str | Path) -> dict[str, PairResult]:
    """Read a {pair_id: PairResult} mapping written by :func:`write_pair_results`.

    Every field of each :class:`~llm_dup_dev.pipeline.runner.PairResult` is
    reconstructed from its persisted sub-model; JSON ``null`` becomes ``None``.
    The stored ``final_label`` is ignored — it is a derived property and is
    recomputed from the restored ``classification``.

    This also reads the secure runner's ``pair_results.json`` unchanged, since
    both writers emit the identical keyed payload.
    """
    # Imported here rather than at module load: PairResult lives in
    # ``pipeline.runner``, whose import pulls the LLM/feature-generation stack.
    # Offline consumers of this module (e.g. eval.sweep) must not pay that cost.
    from llm_dup_dev.llm.tasks.narrative_comparison import NarrativeVerdict
    from llm_dup_dev.models.narrative_demographics import NarrativeDemographics
    from llm_dup_dev.models.narrative_source_case import SourceCaseAssessment
    from llm_dup_dev.models.pair_features import AEClinicalPictureResult
    from llm_dup_dev.pipeline.runner import PairResult

    def _validate(model_cls, data):
        return None if data is None else model_cls.model_validate(data)

    results: dict[str, PairResult] = {}
    for pair_id, data in _read_json(Path(path)).items():
        results[pair_id] = PairResult(
            pair_id=pair_id,
            features=PairFeatures.model_validate(data["features"]),
            classification=ClassificationResult.model_validate(
                data["classification"]
            ),
            narrative_verdict=_validate(
                NarrativeVerdict, data.get("narrative_verdict")
            ),
            ae_clinical_picture=_validate(
                AEClinicalPictureResult, data.get("ae_clinical_picture")
            ),
            source_case_assessment=_validate(
                SourceCaseAssessment, data.get("source_case_assessment")
            ),
            narrative_demographics_1=_validate(
                NarrativeDemographics, data.get("narrative_demographics_1")
            ),
            narrative_demographics_2=_validate(
                NarrativeDemographics, data.get("narrative_demographics_2")
            ),
        )
    return results
