"""LLM-backed task modules for the duplicate-detection pipeline.

Each task module owns its prompt templates, structured output schemas,
and orchestration logic.  Transport is delegated to an injected
:class:`~llm_dup_dev.llm.protocols.LLMClient`.
"""
