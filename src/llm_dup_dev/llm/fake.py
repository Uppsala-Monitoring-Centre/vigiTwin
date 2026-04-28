"""Fake LLM client for unit tests.

Provides a deterministic, in-memory implementation of :class:`LLMClient`
that lets tests pre-seed responses and inspect requests without making
real API calls.

Usage::

    from llm_dup_dev.llm.fake import FakeLLMClient

    fake = FakeLLMClient()
    fake.seed_response(MySchema(answer="yes"))

    response = fake.generate(LLMRequest(
        system_prompt="...",
        user_prompt="...",
        response_model=MySchema,
    ))
    assert response.parsed == MySchema(answer="yes")
    assert len(fake.requests) == 1
"""

from __future__ import annotations

from collections import deque
from typing import Any

from .models import LLMInfo, LLMRequest, LLMResponse, TokenUsage


class FakeLLMClient:
    """Test double for :class:`~llm_dup_dev.llm.protocols.LLMClient`.

    Seeded responses are returned in FIFO order.  When the queue is
    exhausted, a ``default_response`` is used (if configured), otherwise
    ``generate`` raises :class:`RuntimeError`.

    Attributes
    ----------
    requests : list[LLMRequest]
        Every request received, in order.  Useful for asserting that
        task code constructs prompts correctly.
    """

    def __init__(
        self,
        *,
        default_response: LLMResponse | None = None,
        model_name: str = "fake-model",
    ) -> None:
        self._queue: deque[LLMResponse] = deque()
        self._default_response = default_response
        self._model_name = model_name
        self.requests: list[LLMRequest] = []

    # ------------------------------------------------------------------
    # Seeding helpers
    # ------------------------------------------------------------------

    def seed_response(
        self,
        parsed: Any,
        *,
        raw_text: str | None = None,
        usage: TokenUsage | None = None,
    ) -> None:
        """Enqueue a successful structured response.

        Parameters
        ----------
        parsed : Any
            A Pydantic model instance matching the expected
            ``response_model``.
        raw_text : str or None
            Optional raw text to include.
        usage : TokenUsage or None
            Optional token usage metadata.
        """
        self._queue.append(LLMResponse(
            parsed=parsed,
            raw_text=raw_text,
            model=self._model_name,
            usage=usage,
        ))

    def seed_raw(self, raw_text: str) -> None:
        """Enqueue a response with raw text only (no parsed output).

        Useful for testing failure/fallback paths where structured
        parsing was not possible.
        """
        self._queue.append(LLMResponse(
            parsed=None,
            raw_text=raw_text,
            model=self._model_name,
        ))

    def seed_error(self, error: Exception) -> None:
        """Enqueue an exception to be raised on the next ``generate`` call.

        Parameters
        ----------
        error : Exception
            The exception to raise.
        """
        self._queue.append(error)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # LLMClient protocol
    # ------------------------------------------------------------------

    def info(self) -> LLMInfo:
        """Return the fake client's identity for run-manifest logging."""
        return LLMInfo(
            provider="fake",
            model=self._model_name,
            default_temperature=0.0,
        )

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Return the next seeded response or the default.

        Raises
        ------
        RuntimeError
            If the queue is empty and no ``default_response`` was
            configured.
        Exception
            If the next seeded item is an exception (via
            :meth:`seed_error`).
        """
        self.requests.append(request)

        if self._queue:
            item = self._queue.popleft()
            if isinstance(item, Exception):
                raise item
            return item

        if self._default_response is not None:
            return self._default_response

        raise RuntimeError(
            "FakeLLMClient: no seeded responses left and no default_response "
            "configured.  Call seed_response() or pass default_response= to "
            "the constructor."
        )
