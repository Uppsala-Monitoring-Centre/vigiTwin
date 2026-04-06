"""Typed request/response models for the LLM abstraction boundary.

These models define the data contract between pipeline task logic
(dev repo) and concrete LLM backends (secure repo).  They are
backend-agnostic and carry no provider-specific fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LLMRequest:
    """Request to an LLM for structured JSON generation.

    Parameters
    ----------
    system_prompt : str
        System-level instructions for the model.
    user_prompt : str
        The user-facing prompt containing the task input.
    response_model : type
        A Pydantic ``BaseModel`` subclass describing the expected
        structured output.  Concrete clients use this for guided
        generation or post-hoc validation.
    temperature : float
        Sampling temperature.  Defaults to 0.0 for deterministic output.
    max_tokens : int or None
        Maximum tokens in the completion.  ``None`` lets the backend
        choose its default.
    extra : dict[str, Any]
        Opaque key/value pairs forwarded to the concrete backend.
        Use sparingly — this is an escape hatch for provider-specific
        knobs that don't belong in the shared contract.
    """

    system_prompt: str
    user_prompt: str
    response_model: type
    temperature: float = 0.0
    max_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TokenUsage:
    """Token consumption metadata returned by the backend.

    All fields are optional because not every provider reports them.
    """

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class LLMResponse:
    """Response from an LLM call.

    Parameters
    ----------
    parsed : Any or None
        The structured output parsed into ``request.response_model``.
        ``None`` when the backend could not produce valid structured
        output (the caller must handle this case).
    raw_text : str or None
        The raw text completion, if available.  Useful for debugging
        when ``parsed`` is ``None``.
    model : str or None
        Identifier of the model that served the request (e.g.
        ``"gpt-4o-2024-08-06"``).  Populated by the backend.
    usage : TokenUsage or None
        Token consumption metadata, if the backend reports it.
    raw_response : Any or None
        The full provider response object, for debugging/logging only.
        Not part of the stable contract — do not depend on its shape.
    """

    parsed: Any | None = None
    raw_text: str | None = None
    model: str | None = None
    usage: TokenUsage | None = None
    raw_response: Any | None = None
