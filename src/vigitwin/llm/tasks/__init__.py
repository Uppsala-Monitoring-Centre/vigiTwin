"""LLM-backed task modules for the duplicate-detection pipeline.

Each task module owns its prompt templates, structured output schemas,
and orchestration logic.  Transport is delegated to an injected
:class:`~vigitwin.llm.protocols.LLMClient`.
"""
