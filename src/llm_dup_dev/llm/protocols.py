"""Abstract protocol for LLM calls.

Mirrors the pattern in ``llm_dup_dev.data.protocols``: the dev repo
defines the contract; the secure repo implements it against real
endpoints.

Usage in the secure repo::

    from llm_dup_dev.llm import LLMClient, LLMRequest, LLMResponse

    class AzureOpenAIClient:
        '''Implements LLMClient against Azure OpenAI.'''

        def generate(self, request: LLMRequest) -> LLMResponse:
            # … real API call, structured-output parsing, etc.
            ...

    assert isinstance(AzureOpenAIClient(), LLMClient)  # structural check
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import LLMRequest, LLMResponse


@runtime_checkable
class LLMClient(Protocol):
    """Protocol for structured LLM generation.

    Implementations accept an :class:`LLMRequest` containing prompts,
    a Pydantic response model, and inference settings, and return an
    :class:`LLMResponse` with the parsed structured output.

    The dev repo never instantiates a concrete client directly — it
    receives one via dependency injection (constructor argument or
    function parameter).

    Sampling parameters
    -------------------
    When ``request.temperature`` is ``None``, the implementation must
    **omit** the parameter from the provider payload rather than
    substituting a value.  An explicit temperature equal to the model's
    default and an omitted parameter produce the same call, but only
    omission leaves the run manifest able to state honestly that no
    temperature was requested.

    Conversely, when a caller *does* supply a temperature the model
    rejects, raise :class:`LLMConfigurationError` — never quietly
    coerce it to an accepted value.  Silently rewriting ``0.0`` to the
    model's default would make the manifest claim a determinism the run
    did not have.

    Optional ``info()`` method
    --------------------------
    Implementations *should* additionally expose::

        def info(self) -> llm_dup_dev.llm.LLMInfo: ...

    returning the model identity and default sampling parameters.
    :func:`llm_dup_dev.run_manifest.build_run_manifest` calls it via
    duck-typing to populate the manifest's ``llm_config`` block when the
    caller doesn't supply one explicitly. Implementing ``info()`` is
    encouraged for reproducibility but not enforced by the protocol —
    clients without it still satisfy the structural ``isinstance`` check.
    """

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Perform a single LLM call and return structured output.

        Parameters
        ----------
        request : LLMRequest
            Fully specified request including prompts, response model,
            and inference settings.

        Returns
        -------
        LLMResponse
            The result.  ``parsed`` is populated when the backend
            successfully produced valid structured output; otherwise
            it is ``None`` and ``raw_text`` may contain the raw
            completion for diagnostics.

        Raises
        ------
        LLMConfigurationError
            The request is invalid for this backend in a way that will
            not resolve on retry (unsupported parameter or value,
            unknown deployment, auth failure).  Pipeline code lets this
            propagate and abort the run.
        Exception
            Concrete implementations may raise backend-specific errors
            for transient failures (rate limits, timeouts, 5xx).
            Task-level code should handle these as appropriate.
        """
        ...
