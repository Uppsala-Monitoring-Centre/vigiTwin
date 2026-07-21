"""Narrative comparison task — LLM-based assessment of report narratives.

Identifies decisive evidence in free-text narratives that suggests
two pharmacovigilance reports relate to the same patient, are otherwise
related (e.g. same article), or are unrelated.

This replaces the placeholder ``NarrativeProvider`` callable with a
structured, LLM-backed implementation while keeping all prompt
construction and schema definitions in the dev repo.

Usage::

    from llm_dup_dev.llm.tasks.narrative_comparison import (
        compare_narratives,
        NarrativeVerdict,
    )

    result = compare_narratives(
        llm_client=client,
        pair_id="R001__R002",
        narrative_1="54 year old female ...",
        narrative_2="54 yo woman ...",
    )
    print(result.consistency)   # NarrativeConsistency.STRONGLY_CONSISTENT
    print(result.evidence)      # ["Both describe 54yo female with ..."]
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from llm_dup_dev.models.enums import NarrativeConsistency

from .._response_parsing import parse_structured_response
from ..models import LLMRequest, LLMResponse
from ..protocols import LLMClient


# ------------------------------------------------------------------
# Structured output schema
# ------------------------------------------------------------------

class NarrativeVerdict(BaseModel):
    """Structured LLM output for narrative comparison.

    The LLM classifies the relationship between two narratives and
    provides supporting evidence excerpts.
    """

    consistency: NarrativeConsistency = Field(
        description=(
            "Overall consistency assessment.  'strongly_consistent' when "
            "narratives clearly describe the same patient; 'consistent' "
            "when probable but not certain; 'neutral' when narratives are "
            "too sparse to judge; 'inconsistent' when they clearly describe "
            "different patients or contradict each other."
        ),
    )
    evidence: list[str] = Field(
        default_factory=list,
        description=(
            "Short excerpts or observations supporting the verdict.  "
            "Each item should be one distinct piece of evidence, e.g. "
            "'Both narratives mention a 54-year-old female with pregabalin'."
        ),
    )
    reasoning: str = Field(
        default="",
        description=(
            "Brief free-text explanation of the overall assessment.  "
            "Intended for human review, not downstream logic."
        ),
    )


# ------------------------------------------------------------------
# Prompt templates
# ------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a pharmacovigilance expert assessing whether two adverse event \
report narratives describe the same patient.

Classify the relationship between the two narratives and cite the \
decisive evidence.  Focus on patient-identifying details: demographics, \
clinical timeline, drug regimen, adverse events, and outcomes.

Be conservative: if the narratives are too short or vague to judge, \
choose 'neutral' rather than guessing.\
"""

_USER_PROMPT_TEMPLATE = """\
## Narrative 1
{narrative_1}

## Narrative 2
{narrative_2}

Compare these two pharmacovigilance report narratives.  Determine \
whether they describe the same patient, are otherwise related (e.g. \
two patients in the same study or article), or are unrelated.

Provide your assessment as structured JSON.\
"""

_USER_PROMPT_MISSING = """\
One or both narratives are missing or empty.  \
Return a 'neutral' assessment with no evidence.\
"""


# ------------------------------------------------------------------
# Task function
# ------------------------------------------------------------------

def build_narrative_request(
    narrative_1: str | None,
    narrative_2: str | None,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> LLMRequest:
    """Build an :class:`LLMRequest` for narrative comparison.

    This is exposed separately so callers can inspect or modify the
    request before sending it (e.g. for logging or batching).

    Parameters
    ----------
    narrative_1, narrative_2 : str or None
        The two report narratives to compare.
    temperature : float or None
        Sampling temperature.  ``None`` (the default) omits the
        parameter and uses the backend's own default, which is not
        generally deterministic.  Pass an explicit value only when the
        model in use accepts one.
    max_tokens : int or None
        Maximum tokens for the completion.

    Returns
    -------
    LLMRequest
    """
    has_content = bool(narrative_1 and narrative_1.strip()) and bool(
        narrative_2 and narrative_2.strip()
    )

    if has_content:
        user_prompt = _USER_PROMPT_TEMPLATE.format(
            narrative_1=narrative_1,
            narrative_2=narrative_2,
        )
    else:
        user_prompt = _USER_PROMPT_MISSING

    return LLMRequest(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        response_model=NarrativeVerdict,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def compare_narratives(
    llm_client: LLMClient,
    pair_id: str,
    narrative_1: str | None,
    narrative_2: str | None,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> NarrativeVerdict:
    """Compare two report narratives via an LLM call.

    Parameters
    ----------
    llm_client : LLMClient
        Injected LLM backend.
    pair_id : str
        Identifier of the report pair (for logging/diagnostics).
    narrative_1, narrative_2 : str or None
        The two narratives to compare.
    temperature : float or None
        Sampling temperature.  ``None`` (the default) omits the
        parameter and uses the backend's own default, which is not
        generally deterministic.  Pass an explicit value only when the
        model in use accepts one.
    max_tokens : int or None
        Maximum tokens for the completion.

    Returns
    -------
    NarrativeVerdict
        Structured assessment with consistency level, evidence, and
        reasoning.

    Raises
    ------
    ValueError
        If the LLM response does not contain a valid parsed
        :class:`NarrativeVerdict`.
    """
    request = build_narrative_request(
        narrative_1,
        narrative_2,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    response: LLMResponse = llm_client.generate(request)
    return parse_structured_response(
        response,
        NarrativeVerdict,
        context_label=f"Narrative comparison for pair {pair_id}",
    )
