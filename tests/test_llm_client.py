"""Tests for the LLM abstraction layer: protocol, models, and fake client."""

import pytest
from pydantic import BaseModel

from llm_dup_dev.llm import LLMClient, LLMInfo, LLMRequest, LLMResponse, TokenUsage
from llm_dup_dev.llm.fake import FakeLLMClient


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

class DummySchema(BaseModel):
    answer: str
    score: float = 0.0


def _make_request(**overrides) -> LLMRequest:
    defaults = dict(
        system_prompt="system",
        user_prompt="user",
        response_model=DummySchema,
    )
    defaults.update(overrides)
    return LLMRequest(**defaults)


# ===================================================================
# Protocol conformance
# ===================================================================

class TestProtocol:

    def test_fake_client_satisfies_protocol(self):
        fake = FakeLLMClient()
        assert isinstance(fake, LLMClient)

    def test_custom_class_satisfies_protocol(self):
        class MyClient:
            def generate(self, request: LLMRequest) -> LLMResponse:
                return LLMResponse(parsed=None)

        assert isinstance(MyClient(), LLMClient)


# ===================================================================
# Request / Response models
# ===================================================================

class TestModels:

    def test_request_defaults(self):
        req = _make_request()
        # None means "omit temperature; let the backend default apply" —
        # the only form some models accept. Determinism is opt-in.
        assert req.temperature is None
        assert req.max_tokens is None
        assert req.extra == {}

    def test_request_accepts_explicit_temperature(self):
        req = _make_request(temperature=0.0)
        assert req.temperature == 0.0

    def test_request_is_frozen(self):
        req = _make_request()
        with pytest.raises(AttributeError):
            req.temperature = 0.5  # type: ignore[misc]

    def test_request_with_extras(self):
        req = _make_request(extra={"top_p": 0.9})
        assert req.extra["top_p"] == 0.9

    def test_response_defaults(self):
        resp = LLMResponse()
        assert resp.parsed is None
        assert resp.raw_text is None
        assert resp.model is None
        assert resp.usage is None
        assert resp.raw_response is None

    def test_response_with_usage(self):
        usage = TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        resp = LLMResponse(parsed="ok", usage=usage)
        assert resp.usage.total_tokens == 30

    def test_token_usage_partial(self):
        usage = TokenUsage(prompt_tokens=10)
        assert usage.completion_tokens is None
        assert usage.total_tokens is None


# ===================================================================
# FakeLLMClient
# ===================================================================

class TestFakeClient:

    def test_seed_and_retrieve(self):
        fake = FakeLLMClient()
        parsed = DummySchema(answer="yes", score=0.9)
        fake.seed_response(parsed)

        resp = fake.generate(_make_request())
        assert resp.parsed == parsed
        assert resp.model == "fake-model"

    def test_fifo_order(self):
        fake = FakeLLMClient()
        fake.seed_response(DummySchema(answer="first"))
        fake.seed_response(DummySchema(answer="second"))

        r1 = fake.generate(_make_request())
        r2 = fake.generate(_make_request())
        assert r1.parsed.answer == "first"
        assert r2.parsed.answer == "second"

    def test_records_requests(self):
        fake = FakeLLMClient()
        fake.seed_response(DummySchema(answer="ok"))

        req = _make_request(user_prompt="specific prompt")
        fake.generate(req)

        assert len(fake.requests) == 1
        assert fake.requests[0].user_prompt == "specific prompt"

    def test_default_response(self):
        default = LLMResponse(parsed=DummySchema(answer="default"))
        fake = FakeLLMClient(default_response=default)

        resp = fake.generate(_make_request())
        assert resp.parsed.answer == "default"

    def test_raises_when_exhausted(self):
        fake = FakeLLMClient()
        with pytest.raises(RuntimeError, match="no seeded responses"):
            fake.generate(_make_request())

    def test_seed_raw(self):
        fake = FakeLLMClient()
        fake.seed_raw("unparseable text")

        resp = fake.generate(_make_request())
        assert resp.parsed is None
        assert resp.raw_text == "unparseable text"

    def test_seed_error(self):
        fake = FakeLLMClient()
        fake.seed_error(TimeoutError("LLM timed out"))

        with pytest.raises(TimeoutError, match="LLM timed out"):
            fake.generate(_make_request())

        # Request should still be recorded
        assert len(fake.requests) == 1

    def test_seed_response_with_usage(self):
        fake = FakeLLMClient()
        usage = TokenUsage(prompt_tokens=50, completion_tokens=100, total_tokens=150)
        fake.seed_response(DummySchema(answer="ok"), usage=usage)

        resp = fake.generate(_make_request())
        assert resp.usage.total_tokens == 150

    def test_custom_model_name(self):
        fake = FakeLLMClient(model_name="test-gpt-4o")
        fake.seed_response(DummySchema(answer="ok"))

        resp = fake.generate(_make_request())
        assert resp.model == "test-gpt-4o"

    def test_info_reports_identity(self):
        """info() returns an LLMInfo carrying the client's model name."""
        fake = FakeLLMClient(model_name="test-gpt-4o")
        info = fake.info()
        assert isinstance(info, LLMInfo)
        assert info.provider == "fake"
        assert info.model == "test-gpt-4o"
        assert info.default_temperature == 0.0

    def test_mixed_sequence(self):
        """Seed a mix of successes, raw responses, and errors."""
        fake = FakeLLMClient()
        fake.seed_response(DummySchema(answer="ok"))
        fake.seed_raw("bad json")
        fake.seed_error(ValueError("parse error"))

        r1 = fake.generate(_make_request())
        assert r1.parsed is not None

        r2 = fake.generate(_make_request())
        assert r2.parsed is None
        assert r2.raw_text == "bad json"

        with pytest.raises(ValueError, match="parse error"):
            fake.generate(_make_request())

        assert len(fake.requests) == 3
