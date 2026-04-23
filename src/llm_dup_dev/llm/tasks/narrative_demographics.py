"""Narrative demographics extraction — per-report LLM task.

Extracts candidate **age**, **sex**, **weight**, and **height** values
from a single report narrative, together with a short verbatim evidence
snippet supporting each candidate. Designed to run once per report,
independently of any pair, so results can be frozen to JSON and reused
offline.

Second member of the per-report narrative-extractor family, alongside
:mod:`llm_dup_dev.llm.tasks.narrative_source_case`. The structured
output is consumed by the deterministic merge helper
:func:`llm_dup_dev.feature_generation.narrative_demographics.merge_narrative_demographics`,
which appends the extracted values to the corresponding
``patient_*_values`` lists on the
:class:`~llm_dup_dev.models.report.Report` with
``sources=["narrative_llm"]``. The existing inconsistency checks then
operate over the combined structured + narrative candidates using the
generous min-difference / set-intersection rules.

Usage::

    from llm_dup_dev.llm.tasks.narrative_demographics import (
        extract_narrative_demographics,
    )

    extraction = extract_narrative_demographics(
        llm_client=client,
        report_id="R001",
        narrative="A 55-year-old male presented with ...",
    )
    for age in extraction.ages:
        print(age.value, age.unit, age.evidence)
"""

from __future__ import annotations

from llm_dup_dev.models.narrative_demographics import NarrativeDemographics

from ..models import LLMRequest, LLMResponse
from ..protocols import LLMClient


# ------------------------------------------------------------------
# Prompt templates
# ------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a pharmacovigilance expert extracting patient demographic \
signals from a single report narrative.

Your task is precision-oriented.  Leave lists empty when the narrative \
does not give clear support for the patient of interest.  Do not guess.  \
The extracted values feed deterministic downstream inconsistency \
checks, so a confident wrong answer is worse than an empty list.

Extract only demographics of the **patient of interest** — the subject \
of this adverse-event report.  Never extract demographics of:
- family members (e.g. "the patient's 70-year-old mother"),
- bystanders or treating clinicians,
- comparators or control subjects in cited studies,
- historical patients mentioned for context.

A narrative may legitimately mention multiple values for the same \
attribute (e.g. weight on admission vs weight on discharge, or age at \
exposure vs age at event).  List every distinct candidate the \
narrative supports, in the order they appear.

For every extracted value, attach a short verbatim evidence snippet \
copied from the narrative — long enough to justify the extraction, \
short enough to quote (typically under 20 words).

Units and controlled vocabulary:
- Ages: unit is one of "years", "months", "days".  Prefer the unit \
used in the narrative; do not convert.
- Sex: one of "male", "female", "other", "unknown" (lowercase).
- Weight: unit "kg".  Convert from pounds to kilograms only if the \
narrative clearly states pounds; otherwise leave the value out.
- Height: unit "cm".  Convert from inches / feet only if the \
narrative clearly states those units; otherwise leave the value out.

Keep notes brief; they are for human review, not downstream logic.\
"""

_USER_PROMPT_TEMPLATE = """\
## Narrative
{narrative}

Extract patient demographic signals from this single pharmacovigilance \
report narrative.  Follow the guidance in the system prompt.  Return \
strictly structured JSON.\
"""

_USER_PROMPT_MISSING = """\
The narrative is missing or empty.  Return an extraction with all \
lists empty.\
"""


# ------------------------------------------------------------------
# Request builder
# ------------------------------------------------------------------

def build_narrative_demographics_request(
    narrative: str | None,
    *,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> LLMRequest:
    """Build an :class:`LLMRequest` for narrative demographics extraction.

    Exposed separately so callers can inspect the request before sending
    (e.g. for logging, batching, or offline freezing workflows).
    """
    has_content = bool(narrative and narrative.strip())
    user_prompt = (
        _USER_PROMPT_TEMPLATE.format(narrative=narrative)
        if has_content
        else _USER_PROMPT_MISSING
    )
    return LLMRequest(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        response_model=NarrativeDemographics,
        temperature=temperature,
        max_tokens=max_tokens,
    )


# ------------------------------------------------------------------
# Task function
# ------------------------------------------------------------------

def extract_narrative_demographics(
    llm_client: LLMClient,
    report_id: str,
    narrative: str | None,
    *,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> NarrativeDemographics:
    """Extract patient demographics from a single report narrative.

    Parameters
    ----------
    llm_client : LLMClient
        Injected LLM backend.
    report_id : str
        Identifier of the report (for logging / diagnostics).
    narrative : str or None
        The narrative text.  Empty / whitespace / None triggers a short-
        circuit: no LLM call is made and an empty
        :class:`NarrativeDemographics` is returned.
    temperature : float
        Sampling temperature.  Defaults to 0.0.
    max_tokens : int or None
        Maximum tokens for the completion.

    Returns
    -------
    NarrativeDemographics
        Structured extraction with per-value evidence quotes.

    Raises
    ------
    ValueError
        If the LLM response does not contain a valid parsed
        :class:`NarrativeDemographics`.
    """
    if not (narrative and narrative.strip()):
        return NarrativeDemographics()

    request = build_narrative_demographics_request(
        narrative,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    response: LLMResponse = llm_client.generate(request)

    if response.parsed is None:
        raise ValueError(
            f"Narrative demographics extraction for report {report_id} "
            f"failed: LLM did not return structured output.  Raw text: "
            f"{response.raw_text!r}"
        )

    if isinstance(response.parsed, NarrativeDemographics):
        return response.parsed
    if isinstance(response.parsed, dict):
        return NarrativeDemographics.model_validate(response.parsed)

    raise ValueError(
        f"Narrative demographics extraction for report {report_id}: "
        f"unexpected parsed type {type(response.parsed).__name__}.  "
        f"Expected NarrativeDemographics."
    )
