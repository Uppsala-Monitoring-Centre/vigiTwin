"""Shared helper for coercing structured LLM responses into Pydantic models.

The four per-report / per-pair LLM tasks
(:mod:`llm_dup_dev.llm.tasks.narrative_source_case`,
:mod:`llm_dup_dev.llm.tasks.narrative_demographics`,
:mod:`llm_dup_dev.llm.tasks.narrative_comparison`,
:mod:`llm_dup_dev.llm.tasks.ae_clinical_picture`) all need to coerce
``LLMResponse.parsed`` into a concrete Pydantic model and raise a
descriptive ``ValueError`` when the backend did not produce a usable
structured output. This helper centralises that pattern so the tasks
themselves stay focused on prompt construction and post-processing.

Private to the ``llm`` package; not part of the public API.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from .models import LLMResponse

T = TypeVar("T", bound=BaseModel)


def parse_structured_response(
    response: LLMResponse,
    model_cls: type[T],
    *,
    context_label: str,
) -> T:
    """Coerce ``response.parsed`` into a ``model_cls`` instance.

    Parameters
    ----------
    response : LLMResponse
        The response from an :class:`LLMClient`.
    model_cls : type[T]
        The Pydantic model the task expects ``response.parsed`` to be.
    context_label : str
        Human-readable label embedded in error messages
        (e.g. ``"Source/case extraction for report R001"``).

    Returns
    -------
    T
        An instance of ``model_cls``.

    Raises
    ------
    ValueError
        If ``response.parsed`` is ``None`` or has an unexpected type.
    """
    if response.parsed is None:
        raise ValueError(
            f"{context_label} failed: LLM did not return structured "
            f"output. Raw text: {response.raw_text!r}"
        )
    if isinstance(response.parsed, model_cls):
        return response.parsed
    if isinstance(response.parsed, dict):
        return model_cls.model_validate(response.parsed)
    raise ValueError(
        f"{context_label}: unexpected parsed type "
        f"{type(response.parsed).__name__}. Expected "
        f"{model_cls.__name__}."
    )
