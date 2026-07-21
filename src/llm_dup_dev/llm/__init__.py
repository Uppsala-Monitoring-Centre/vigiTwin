"""LLM integration boundary for the duplicate-detection pipeline.

This package defines the abstract contract for LLM calls and typed
request/response models.  The dev repo owns prompt construction,
structured output schemas, and task-level logic.  The secure repo
provides concrete client implementations (Azure OpenAI, etc.).

Quick start – dev-side usage::

    from llm_dup_dev.llm import LLMClient, LLMRequest, LLMResponse
    from llm_dup_dev.llm.fake import FakeLLMClient

    # In tests
    fake = FakeLLMClient()
    fake.seed_response(MySchema(field="value"))
    response = fake.generate(LLMRequest(
        system_prompt="You are a helpful assistant.",
        user_prompt="Classify this.",
        response_model=MySchema,
    ))

Secure-repo wiring::

    from llm_dup_dev.llm import LLMClient, LLMRequest, LLMResponse

    class AzureOpenAIClient:
        '''Implements LLMClient against Azure OpenAI.'''
        def generate(self, request: LLMRequest) -> LLMResponse: ...

    assert isinstance(AzureOpenAIClient(), LLMClient)  # structural check
"""

from .errors import LLMConfigurationError
from .models import LLMInfo, LLMRequest, LLMResponse, TokenUsage
from .protocols import LLMClient

__all__ = [
    "LLMClient",
    "LLMConfigurationError",
    "LLMInfo",
    "LLMRequest",
    "LLMResponse",
    "TokenUsage",
]
