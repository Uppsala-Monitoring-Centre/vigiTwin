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
        Exception
            Concrete implementations may raise backend-specific errors
            (rate limits, timeouts, auth failures).  Task-level code
            should handle these as appropriate.
        """
        ...
