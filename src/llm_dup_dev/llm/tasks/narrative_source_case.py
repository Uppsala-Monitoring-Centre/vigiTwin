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
    _normalize_source_identifier_value,
    normalize_case_identifier_family,
    normalize_case_identifier_value,
)
from llm_dup_dev.models.narrative_source_case import (
    CaseIdentifierFamily,
    NarrativeSourceCase,
    SourceIdentifier,
    SourceIdentifierType,
    SourceType,
)

from .._response_parsing import parse_structured_response
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

1. Source identifiers — identifiers that tie this report to the shared \
underlying source across multiple sibling reports.  This field is a \
LIST: emit every such identifier the narrative provides, not just one.  \
Each entry has a type and a value.

   Valid types and one example each:
   - doi               -> "10.1234/abc.2024.567"
   - pmid              -> "PMID 12345678"
   - trial_registration -> "NCT01234567", "EudraCT 2020-001234-12", \
"ISRCTN12345678"
   - study_name        -> "EpcoReal", "PARADIGM-HF", "KEYNOTE-001"
   - protocol_number   -> "GS-US-565-1234", "MK-1234-001"
   - linked_case       -> "Linked case: AB-123456789" (the verbatim \
string that appears identically in sibling reports from the same \
underlying case)
   - article_reference -> a free-text citation when no more specific \
identifier is available
   The 'unknown' type is reserved and should not be emitted alongside a \
real value.

   Worked example.  If the narrative opens with \
"STUDY NAME: EpcoReal: A multi-country, prospective observational study \
of epcoritamab in NHL ...", emit at minimum \
{type: "study_name", value: "EpcoReal"} (and any NCT / EudraCT / DOI \
that also appears).  Set multi_patient_source = true.

   Do NOT include identifiers that are specific to this single report \
(see category 3 below).  Leave the list empty when the narrative does \
not clearly reference a sibling-shared identifier.

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
source_identifiers.

Other guidance:

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

    if extraction.source_identifiers:
        normalized: list[SourceIdentifier] = []
        seen: set[tuple[SourceIdentifierType, str]] = set()
        for ident in extraction.source_identifiers:
            value = _normalize_source_identifier_value(ident.value, ident.type)
            if not value:
                continue
            key = (ident.type, value)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(SourceIdentifier(type=ident.type, value=value))
        updates["source_identifiers"] = normalized

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
    extraction = parse_structured_response(
        response,
        NarrativeSourceCase,
        context_label=f"Source/case extraction for report {report_id}",
    )
    return _post_normalize(extraction)
