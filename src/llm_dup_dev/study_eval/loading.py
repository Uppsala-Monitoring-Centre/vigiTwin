"""Read and write the canonical study-eval models as JSON.

The models are ordinary pydantic, so serialization is just
``model_dump_json`` / ``model_validate_json``; the value this module adds is a
named, typed entry point per artifact (so a caller does not import model
classes to load a file) and the assembly helpers that combine a separately
stored evaluation set and method-output bundle into an analysis input with the
cross-file coverage checks those inputs enforce.

The secure adapter typically writes the evaluation set once (it is fixed by the
freeze) and the method outputs once per frozen run, then assembles the analysis
input at calculation time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from llm_dup_dev.study_eval.schemas import (
    MethodOutputs,
    PrecisionAnalysisInput,
    PrecisionEvaluationSet,
    RecallAnalysisInput,
    RecallEvaluationSet,
)

_M = TypeVar("_M", bound=BaseModel)


def _read_model(model: type[_M], path: str | Path) -> _M:
    return model.model_validate_json(Path(path).read_text())


def _write_model(instance: BaseModel, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(instance.model_dump_json(indent=2))
    return out


# --- Precision -------------------------------------------------------------


def load_precision_evaluation_set(path: str | Path) -> PrecisionEvaluationSet:
    """Load a :class:`PrecisionEvaluationSet` from JSON."""
    return _read_model(PrecisionEvaluationSet, path)


def load_method_outputs(path: str | Path) -> MethodOutputs:
    """Load a :class:`MethodOutputs` bundle from JSON."""
    return _read_model(MethodOutputs, path)


def load_precision_analysis_input(
    evaluation_set_path: str | Path,
    method_outputs_path: str | Path,
) -> PrecisionAnalysisInput:
    """Assemble and validate a precision analysis input from two files."""
    return PrecisionAnalysisInput(
        evaluation_set=load_precision_evaluation_set(evaluation_set_path),
        outputs=load_method_outputs(method_outputs_path),
    )


# --- Recall ----------------------------------------------------------------


def load_recall_evaluation_set(path: str | Path) -> RecallEvaluationSet:
    """Load a single :class:`RecallEvaluationSet` from JSON."""
    return _read_model(RecallEvaluationSet, path)


def load_recall_analysis_input(
    evaluation_set_paths: list[str | Path],
    method_outputs_path: str | Path,
) -> RecallAnalysisInput:
    """Assemble and validate a recall analysis input from several files."""
    return RecallAnalysisInput(
        evaluation_sets=[load_recall_evaluation_set(p) for p in evaluation_set_paths],
        outputs=load_method_outputs(method_outputs_path),
    )


# --- Generic write ---------------------------------------------------------


def write_json(instance: BaseModel, path: str | Path) -> Path:
    """Write any study-eval pydantic model to ``path`` as indented JSON."""
    return _write_model(instance, path)


def write_json_dict(data: dict, path: str | Path) -> Path:
    """Write a plain dict (e.g. a result table) to ``path`` as indented JSON."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2, sort_keys=True))
    return out


__all__ = [
    "load_precision_evaluation_set",
    "load_method_outputs",
    "load_precision_analysis_input",
    "load_recall_evaluation_set",
    "load_recall_analysis_input",
    "write_json",
    "write_json_dict",
]
