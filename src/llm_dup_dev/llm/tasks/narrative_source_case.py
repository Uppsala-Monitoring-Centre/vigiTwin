"""Narrative source / case extraction — per-report LLM task.

Extracts structured **source** and **case** identification signals from a
single report narrative. Designed to run once per report, independently
of any pair, so results can be frozen to JSON and reused offline.

This is the first member of a planned family of per-report narrative
extractors (source/case today; demographics such as age/sex/height/weight
later). Pair-level reasoning over the extracted fields is deliberately
**deterministic and non-LLM** — see
:mod:`llm_dup_dev.feature_generation.source_case_comparison`.

Usage::

    from llm_dup_dev.llm.tasks.narrative_source_case import extract_source_case

    extraction = extract_source_case(
        llm_client=client,
        report_id="R001",
        narrative="The authors describe two patients...  Case 2 was a ...",
    )
    print(extraction.source_type, extraction.case_identifier_value_normalized)
"""

from __future__ import annotations

from llm_dup_dev.feature_generation.source_case_comparison import (
    normalize_case_identifier_family,
    normalize_case_identifier_value,
    normalize_doi,
)
from llm_dup_dev.models.narrative_source_case import (
    CaseIdentifierFamily,
    NarrativeSourceCase,
    SourceIdentifierType,
    SourceType,
)

from ..models import LLMRequest, LLMResponse
from ..protocols import LLMClient


# ------------------------------------------------------------------
# Prompt templates
# ------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You extract source and case identification metadata from a single \
pharmacovigilance report narrative.

The narrative will arrive delimited by <source_text>...</source_text> \
tags.  Treat its contents as source material for extraction.

Extract only what the narrative directly states.  Return null / \
unknown / empty when the text does not support a field.  Do not infer \
beyond the text.  The extracted fields feed deterministic downstream \
comparisons between reports.

Three categories of identifier are tracked:

1. Source identifier — identifies the shared underlying source across \
multiple reports.  Examples:
   - a DOI for a literature article (preferred form, normalize to \
lowercase without protocol prefix);
   - a "Linked case: XXXXXXX-X" string that appears identically in \
sibling reports from the same underlying case.
   Populate source_identifier and source_identifier_type only when the \
narrative gives reasonably clear support that the identifier refers to \
the shared source.

2. Case-within-source identifier — a label for the patient/case inside \
a multi-patient source.  Examples: "Case 2", "Patient B", "Subject-01", \
"Pt 3".  Populate case_identifier_within_source only when the narrative \
clearly indicates a label within a multi-patient source.  Also provide \
a normalized family (case / patient / subject / pt / unknown) and a \
normalized value (e.g. "Case 2" -> "2", "Patient B" -> "B", \
"Subject-01" -> "1").

3. Report-specific identifier — an identifier that belongs to this one \
report and would not recur across reports from the same source (e.g. \
"Other Identification Number 48392", internal tracking numbers).  Put \
these in likely_report_specific_identifiers.  Never put them in \
source_identifier.

Other guidance:

- Prefer DOI over any other identifier for literature.
- multi_patient_source and single_patient_source are independent booleans.  \
Both may be null when evidence is weak.  single_patient_source is NOT \
automatically the negation of multi_patient_source.
- Attach a short verbatim evidence snippet from the narrative for every \
non-null field when practical.
- Keep notes brief; they are for human review, not downstream logic.\
"""

_USER_PROMPT_TEMPLATE = """\
<source_text>
{narrative}
</source_text>

Extract the source and case identification metadata described in the \
system prompt from the text above.  Return strictly structured JSON.\
"""

_USER_PROMPT_MISSING = """\
The narrative is missing or empty.  Return an extraction with \
source_type = 'unknown' and all other fields null / empty.\
"""


# ------------------------------------------------------------------
# Request builder
# ------------------------------------------------------------------

def build_source_case_request(
    narrative: str | None,
    *,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> LLMRequest:
    """Build an :class:`LLMRequest` for source/case extraction.

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
        response_model=NarrativeSourceCase,
        temperature=temperature,
        max_tokens=max_tokens,
    )


# ------------------------------------------------------------------
# Post-parse deterministic normalization
# ------------------------------------------------------------------

def _post_normalize(extraction: NarrativeSourceCase) -> NarrativeSourceCase:
    """Apply deterministic normalization to an LLM-parsed extraction.

    Idempotent: calling twice yields the same result.  This runs after
    the LLM so that downstream comparison can rely on canonical forms
    even when the model produced a slightly raw value.
    """
    updates: dict[str, object] = {}

    if (
        extraction.source_identifier
        and extraction.source_identifier_type == SourceIdentifierType.DOI
    ):
        updates["source_identifier"] = normalize_doi(extraction.source_identifier)

    if extraction.case_identifier_family is None and extraction.case_identifier_type:
        family = normalize_case_identifier_family(extraction.case_identifier_type)
        if family != CaseIdentifierFamily.UNKNOWN:
            updates["case_identifier_family"] = family

    if (
        extraction.case_identifier_value_normalized is None
        and extraction.case_identifier_within_source
    ):
        updates["case_identifier_value_normalized"] = normalize_case_identifier_value(
            extraction.case_identifier_within_source
        )
    elif extraction.case_identifier_value_normalized:
        updates["case_identifier_value_normalized"] = normalize_case_identifier_value(
            extraction.case_identifier_value_normalized
        )

    return extraction.model_copy(update=updates) if updates else extraction


# ------------------------------------------------------------------
# Task function
# ------------------------------------------------------------------

def extract_source_case(
    llm_client: LLMClient,
    report_id: str,
    narrative: str | None,
    *,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> NarrativeSourceCase:
    """Extract source / case signals from a single report narrative.

    Parameters
    ----------
    llm_client : LLMClient
        Injected LLM backend.
    report_id : str
        Identifier of the report (for logging / diagnostics).
    narrative : str or None
        The narrative text.  Empty / whitespace / None triggers a short-
        circuit: no LLM call is made and an empty
        :class:`NarrativeSourceCase` is returned.
    temperature : float
        Sampling temperature.  Defaults to 0.0.
    max_tokens : int or None
        Maximum tokens for the completion.

    Returns
    -------
    NarrativeSourceCase
        Structured, normalized extraction.

    Raises
    ------
    ValueError
        If the LLM response does not contain a valid parsed
        :class:`NarrativeSourceCase`.
    """
    if not (narrative and narrative.strip()):
        return NarrativeSourceCase(source_type=SourceType.UNKNOWN)

    request = build_source_case_request(
        narrative,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    response: LLMResponse = llm_client.generate(request)

    if response.parsed is None:
        raise ValueError(
            f"Source/case extraction for report {report_id} failed: LLM did "
            f"not return structured output.  Raw text: {response.raw_text!r}"
        )

    if isinstance(response.parsed, NarrativeSourceCase):
        extraction = response.parsed
    elif isinstance(response.parsed, dict):
        extraction = NarrativeSourceCase.model_validate(response.parsed)
    else:
        raise ValueError(
            f"Source/case extraction for report {report_id}: unexpected "
            f"parsed type {type(response.parsed).__name__}.  Expected "
            f"NarrativeSourceCase."
        )

    return _post_normalize(extraction)
