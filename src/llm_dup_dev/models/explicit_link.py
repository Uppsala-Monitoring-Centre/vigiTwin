"""Pair-level signal for explicit inter-report links.

Reports carry an ICH E2B(R3) C.1.9 "Other case identifiers in previous
transmissions" field — a one-to-many list of case numbers referring to
*other* reports. When one report of a candidate pair names the other
there, the sending organisation has explicitly declared the two reports
to describe the same case. That is the most direct evidence of shared
provenance the structured data can offer.

These types capture the deterministic pair-level verdict produced from
the two reports' :class:`~llm_dup_dev.models.report.CaseIdentifierRef`
lists by
:mod:`llm_dup_dev.feature_generation.explicit_link_comparison`. No LLM
is involved: both the pointers and the identifiers they resolve against
come from structured database fields on the ``Report`` contract.

The verdict is carried on ``PairFeatures.explicit_link`` and consumed by
the decision logic — it rejects independence outright and selects the
explicit-link evidence zone in the relationship assessment.

See: docs/data_contracts.md § Explicit inter-report links
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from llm_dup_dev.models.report import CaseIdentifierKind


class ExplicitLinkDirection(str, Enum):
    """Which report carries the pointer at the other.

    Both directions may fire for one pair (each report naming the
    other); each is recorded as a separate
    :class:`ExplicitLinkMatch`.
    """

    R1_TO_R2 = "report_1_to_report_2"
    R2_TO_R1 = "report_2_to_report_1"


class ExplicitLinkVerdict(str, Enum):
    """Deterministic pair-level verdict over the two reports' case identifiers.

    ``NOT_ASSESSED`` distinguishes *did not look* from *looked and found
    nothing*: it is emitted when neither report carries any case
    identifier data at all (the loader does not supply the fields, or the
    features predate them). Downstream code must treat it as no
    information, never as "assessed and unlinked".
    """

    EXPLICIT_LINK = "explicit_link"
    NO_LINK = "no_link"
    NOT_ASSESSED = "not_assessed"


class ExplicitLinkMatch(BaseModel):
    """One resolved pointer: a reference on one report naming the other.

    ``target_kind`` records which of the *target's* own case-number
    fields the pointer resolved against. It is the only provenance
    available — the referencing side is an untyped C.1.9 value — and it
    is audit/display information, never a constraint: matching is on
    ``normalized_value`` alone.
    """

    direction: ExplicitLinkDirection
    normalized_value: str
    target_kind: CaseIdentifierKind
    pointer_value: str  # verbatim, from the pointing report's other_case_identifiers
    target_value: str  # verbatim, from the target report's case_identifiers


class ExplicitLinkAssessment(BaseModel):
    """Pair-level explicit-link verdict with its supporting matches.

    ``matches`` is empty for every verdict except ``EXPLICIT_LINK``, and
    carries every resolved pointer found (not just the first) so a
    reviewer can see the full basis for the link.
    """

    verdict: ExplicitLinkVerdict
    matches: list[ExplicitLinkMatch] = Field(default_factory=list)
    reasoning: str = ""
