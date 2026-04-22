"""Derived narrative signals for source / case identification.

These types capture structured signals extracted from a single report
narrative by a report-level LLM task (:mod:`llm_dup_dev.llm.tasks.narrative_source_case`),
together with the pair-level deterministic verdict produced from two such
extractions by the non-LLM comparator
(:mod:`llm_dup_dev.feature_generation.source_case_comparison`).

This module is a **derived artifact** in the sense of the data-contract
document: it is not part of the raw Report contract (which mirrors what
the database provides), but a downstream interpretation layer that the
dev repo owns end-to-end (prompt, schema, normalization, comparison).
The secure repo is expected to call the LLM extractor on plain narrative
text and optionally freeze the per-report results as JSON for offline
reuse — the same pattern used by ``FrozenPairBundle`` in
:mod:`llm_dup_dev.data.offline`.

See: docs/data_contracts.md § Derived Narrative Signals
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


# ------------------------------------------------------------------
# Controlled vocabularies
# ------------------------------------------------------------------

class SourceType(str, Enum):
    """Kind of underlying source the narrative appears to describe."""

    LITERATURE = "literature"
    SUMMARY_CASE = "summary_case"
    SPONTANEOUS_OR_OTHER = "spontaneous_or_other"
    UNKNOWN = "unknown"


class SourceIdentifierType(str, Enum):
    """Kind of identifier that would link sibling reports to the same source.

    ``linked_case`` covers strings like ``"Linked case: AB-123456789"`` that
    are expected to appear verbatim across reports sharing the same
    underlying case. It is classified here as a *source* identifier, not a
    *case-within-source* identifier, because its defining property is that
    sibling reports carry the identical string.
    """

    DOI = "doi"
    LINKED_CASE = "linked_case"
    ARTICLE_REFERENCE = "article_reference"
    UNKNOWN = "unknown"


class CaseIdentifierFamily(str, Enum):
    """Normalized family/namespace of a case-within-source identifier.

    Used by the deterministic comparator to decide whether two identifiers
    are safely comparable. Two identifiers with different families (or one
    in ``UNKNOWN``) are *not* treated as comparable; their mismatch never
    becomes evidence of different individuals.
    """

    CASE = "case"
    PATIENT = "patient"
    SUBJECT = "subject"
    PT = "pt"
    UNKNOWN = "unknown"


class SourceCaseVerdict(str, Enum):
    """Deterministic pair-level verdict derived from two narrative extractions."""

    SAME_SOURCE_SINGLE_PATIENT = "same_source_single_patient"
    SAME_SOURCE_SAME_CASE = "same_source_same_case"
    SAME_SOURCE_DIFFERENT_CASE = "same_source_different_case"
    SAME_SOURCE_CASE_UNCOMPARABLE = "same_source_case_uncomparable"
    MULTI_PATIENT_CONTEXT_NO_LINK = "multi_patient_context_no_link"
    NO_DECISIVE_SIGNAL = "no_decisive_signal"
    NOT_ASSESSED = "not_assessed"


# ------------------------------------------------------------------
# Per-report extraction
# ------------------------------------------------------------------

class EvidenceSpan(BaseModel):
    """Short verbatim snippet supporting one extracted field."""

    field: str = Field(
        description="Name of the NarrativeSourceCase field this snippet supports.",
    )
    quote: str = Field(
        description="Short verbatim snippet copied from the narrative.",
    )


class NarrativeSourceCase(BaseModel):
    """Structured source / case signals extracted from one report narrative.

    All fields are optional by design: the extractor is precision-oriented
    and leaves fields null when the narrative does not provide clear
    support. Deterministic normalization (DOI form, family mapping, value
    canonicalization) is applied after LLM parsing so downstream
    comparison can rely on ``source_identifier`` and
    ``case_identifier_value_normalized`` without re-normalizing.
    """

    source_type: SourceType = SourceType.UNKNOWN

    multi_patient_source: bool | None = Field(
        default=None,
        description=(
            "Whether the narrative indicates the source describes multiple "
            "patients or cases. Null when unclear."
        ),
    )
    single_patient_source: bool | None = Field(
        default=None,
        description=(
            "Whether the narrative indicates the source describes a single "
            "patient or case. Intentionally *not* the negation of "
            "multi_patient_source: both may be null when evidence is weak."
        ),
    )

    source_identifier: str | None = Field(
        default=None,
        description=(
            "Stable identifier of the underlying source, when present. For "
            "literature this should be a DOI (normalized to lowercase, "
            "without protocol prefix). For summary-case-like contexts it "
            "may be a 'Linked case: ...' string that appears identically "
            "across sibling reports."
        ),
    )
    source_identifier_type: SourceIdentifierType | None = None

    case_identifier_within_source: str | None = Field(
        default=None,
        description=(
            "Surface form of a label for the patient or case *inside* a "
            "multi-patient source (e.g. 'Case 2', 'Patient B', "
            "'Subject-01'). Do not confuse with report-specific IDs."
        ),
    )
    case_identifier_type: str | None = Field(
        default=None,
        description="Free-form label from the model, e.g. 'case', 'patient_letter'.",
    )
    case_identifier_family: CaseIdentifierFamily | None = None
    case_identifier_value_normalized: str | None = Field(
        default=None,
        description=(
            "Normalized comparable value within the identifier family, e.g. "
            "'Case 2' -> '2', 'Patient B' -> 'B', 'Subject-01' -> '1'."
        ),
    )

    likely_report_specific_identifiers: list[str] = Field(
        default_factory=list,
        description=(
            "Identifier-like strings that more likely identify *this* report "
            "than the shared source or a case-within-source (e.g. 'Other "
            "Identification Number 48392'). Kept for transparency; never "
            "used as a source_identifier."
        ),
    )

    evidence: list[EvidenceSpan] = Field(
        default_factory=list,
        description="Short verbatim snippets supporting non-null fields.",
    )
    notes: str = Field(
        default="",
        description="Brief free-text explanation for human review.",
    )


# ------------------------------------------------------------------
# Pair-level deterministic assessment
# ------------------------------------------------------------------

class SourceCaseAssessment(BaseModel):
    """Pair-level deterministic result of comparing two extractions.

    Produced by
    :func:`llm_dup_dev.feature_generation.source_case_comparison.compare_source_case`.
    No LLM is involved at this stage.
    """

    verdict: SourceCaseVerdict
    shared_source_identifier: str | None = Field(
        default=None,
        description=(
            "Normalized shared identifier when both extractions agree on "
            "one; null otherwise."
        ),
    )
    shared_source_identifier_type: SourceIdentifierType | None = None
    case_identifiers_compared: bool = Field(
        default=False,
        description=(
            "True when both extractions had non-null case identifiers and "
            "the comparator attempted to compare them."
        ),
    )
    case_identifiers_comparable: bool = Field(
        default=False,
        description=(
            "True when the two case identifiers were judged safely "
            "comparable (same family, non-empty normalized values)."
        ),
    )
    reasoning: str = Field(
        default="",
        description="Short explanation of which rule fired.",
    )
