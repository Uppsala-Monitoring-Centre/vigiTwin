"""Read and write the canonical study-eval models as JSON.

The models are ordinary pydantic, so serialization is just ``model_dump_json`` /
``model_validate_json``; the value this module adds is a named, typed entry point
per artifact (so a caller does not import model classes to load a file). The
secure adapter typically writes the reference sets and the prediction set once
(fixed by the freeze), then the workflow steps read them back.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from llm_dup_dev.study_eval.schemas import PredictionSet, ReferenceSet

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
    "write_json",
    "write_json_dict",
]
