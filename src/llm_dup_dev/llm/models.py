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
    temperature : float or None
        Sampling temperature.  ``None`` (the default) means *omit the
        parameter entirely* and let the backend apply its own default —
        the only option some models accept, as newer reasoning-style
        models reject any explicit value other than their fixed default.

        Because the backend default is generally not 0.0, leaving this
        as ``None`` implies a potentially **non-deterministic** run.
        Callers that need reproducible output must pass an explicit
        value *and* use a model that honours it; a backend asked for a
        temperature it cannot serve raises
        :class:`~llm_dup_dev.llm.LLMConfigurationError` rather than
        silently substituting one.
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
    temperature: float | None = None
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
class LLMInfo:
    """Identity and default sampling parameters of an :class:`LLMClient`.

    Returned by an optional ``info()`` method on the client so that
    :func:`llm_dup_dev.run_manifest.build_run_manifest` can record what
    model and configuration a pipeline run used. Adding ``info()`` is
    encouraged but not required by the :class:`LLMClient` protocol —
    secure-side clients can implement it when convenient. When absent,
    the manifest's ``llm_config`` is left empty (or whatever the caller
    explicitly passes).

    Parameters
    ----------
    provider : str
        Stable identifier for the backend family, e.g. ``"azure_openai"``,
        ``"openai"``, ``"anthropic"``, ``"fake"``.
    model : str
        Identifier of the model in use, e.g. ``"gpt-4o-2024-08-06"``.
    deployment : str or None
        Provider-specific deployment / endpoint identifier (Azure
        deployment name, served-model id, …). ``None`` when not
        applicable.
    api_version : str or None
        Provider API version pin (Azure ``api-version``, OpenAI Beta
        header, etc.).
    default_temperature : float or None
        The sampling temperature the backend *actually applies* when an
        :class:`LLMRequest` leaves ``temperature`` as ``None``.  Report
        the effective value, not the one a caller asked for — for
        temperature-pinned models this is the model's fixed default.
        Together with the run manifest's recorded requested value, this
        is what lets a reader tell a reproducible run from a
        non-reproducible one after the fact.
    default_max_tokens : int or None
        Default ``max_tokens`` cap applied when not overridden.
    extra : dict[str, Any]
        Free-form provider-specific metadata that is useful for
        reproducibility but doesn't fit the structured fields (region,
        seed policy, content-filter version, …). Keep small.
    """

    provider: str
    model: str
    deployment: str | None = None
    api_version: str | None = None
    default_temperature: float | None = None
    default_max_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


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
