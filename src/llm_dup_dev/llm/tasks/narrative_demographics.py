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

from .._response_parsing import parse_structured_response
from ..models import LLMRequest, LLMResponse
from ..protocols import LLMClient


# ------------------------------------------------------------------
# Prompt templates
# ------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You extract patient demographic metadata from a single \
pharmacovigilance report narrative.

The narrative will arrive delimited by <source_text>...</source_text> \
tags.  Treat its contents as source material for extraction.

Extract only what the narrative directly states about the **patient \
of interest** — the subject of this adverse-event report.  Return an \
empty list for any attribute the text does not support.  The extracted \
values feed deterministic downstream inconsistency checks, so a missing \
candidate is always preferable to an imputed one.

**Never impute.**  Do not map qualitative descriptors to numeric or \
categorical defaults; do not fill in a "typical" value when the \
narrative is silent.  In particular:

- For **age**, the only acceptable evidence is an explicit numeric \
value with a unit (e.g. "56-year-old", "aged 72", "6-month-old", \
"3 days old").  Qualitative descriptors such as "adult", "elderly", \
"geriatric", "middle-aged", "paediatric", "child", "adolescent", \
"teenager", "infant", "neonate", "newborn", "young", or "old" are \
**not** evidence for an age value — emit no age candidate for them.
- For **weight** and **height**, only an explicit numeric measurement \
counts as evidence.  Descriptors like "obese", "underweight", "tall", \
or "short" do not yield a value.
- For **sex**, acceptable evidence is either (a) an explicit gendered \
noun applied to the patient ("male", "female", "man", "woman", "boy", \
"girl"), or (b) sustained gendered pronouns ("he/him", "she/her") \
referring to the patient.  Do **not** infer sex from a given name \
alone, nor from pregnancy or obstetric context alone.

Examples:
- "This report concerns an adult patient." → no age candidate (and \
no sex candidate from "patient" alone).
- "A 56-year-old man presented." → age 56 years, sex male.
- "She was admitted following ..." (with no explicit noun) → sex \
female from the sustained pronoun, no age candidate.

When in doubt, omit the candidate.  Empty lists are the correct \
output for under-specified narratives.

Do not extract demographics of:
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
<source_text>
{narrative}
</source_text>

Extract the patient demographic metadata described in the system \
prompt from the text above.  Return strictly structured JSON.\
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
    return parse_structured_response(
        response,
        NarrativeDemographics,
        context_label=(
            f"Narrative demographics extraction for report {report_id}"
        ),
    )
