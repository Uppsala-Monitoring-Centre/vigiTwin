"""Deterministic pair-level comparator for explicit inter-report links.

Consumes the two reports' structured case-identifier fields — each
report's own case numbers (``Report.case_identifiers``) and the
references it makes to other reports (``Report.other_case_identifiers``,
the ICH E2B(R3) C.1.9 field) — and returns an
:class:`~llm_dup_dev.models.explicit_link.ExplicitLinkAssessment`.

No LLM is involved; the logic is deterministic, stateless and DB-free,
following the same conventions as the other feature-generation modules
(:mod:`llm_dup_dev.feature_generation.source_case_comparison`,
:mod:`llm_dup_dev.feature_generation.matching`, …).

Three deliberate restrictions keep the signal precise, because it drives
a strong conclusion (independence rejected, compelling-zone tolerance):

- **Direct links only.** A link requires one report to name the *other*.
  Both reports pointing at a common third case does **not** link them —
  that pattern also arises from a shared master or study record covering
  distinct patients.
- **Self-pointers dropped.** C.1.9 is frequently populated with the
  sender's own prior transmission IDs (follow-up versions of the same
  report). A pointer whose value is also one of the pointing report's own
  identifiers carries no information about the other report.
- **Degenerate values never match.** Short values and absence sentinels
  are excluded, so a stray ``"0"`` or ``"UNKNOWN"`` on both sides cannot
  manufacture a link.

Matching is on the normalized value **alone**. The C.1.9 field records
only identifiers, with no qualifier saying which kind of case number each
one is, so a pointer is compared against every identifier the other
report holds — whichever of its three own fields that value came from.
This is not a policy choice but the shape of the data, and it is also the
correct behaviour: any of the three fields can carry a worldwide-unique
identifier, so the same ID may be filed under ``safety_report_id`` by one
sender and ``authority_number`` by the other. The matched target's field
is recorded on each match for audit and display, and the minimum-length
guard below carries the whole precision burden.

See: docs/duplicate_adjudication_spec.md § Explicit inter-report links
"""

from __future__ import annotations

import re
import unicodedata

from llm_dup_dev.models.explicit_link import (
    ExplicitLinkAssessment,
    ExplicitLinkDirection,
    ExplicitLinkMatch,
    ExplicitLinkVerdict,
)
from llm_dup_dev.models.report import CaseIdentifierKind, CaseIdentifierRef, Report


# ==================================================================
# Normalization and guards
# ==================================================================

# Identifiers are structured strings (``US-PFIZER-2020123456``,
# ``GB-MHRA-ADR 12345678``), so internal hyphens and digits are meaningful
# and preserved. Only surrounding wrapping punctuation is trimmed.
_TRAILING_PUNCT = ".,;:()[]{}<>\"'`"

# Minimum length for a normalized value to be eligible for matching.
# Real safety-report / company / authority numbers comfortably exceed
# this; the bar exists so that short sequence numbers cannot collide.
# Since the C.1.9 pointers are untyped, there is no field constraint to
# fall back on and this carries the whole precision burden.
MIN_IDENTIFIER_LENGTH = 6

# Values that signal absence of information rather than an identifier.
# Compared after normalization (which casefolds), in the same spirit as
# ``models.demographics_vocab.drop_uncertainty_values``.
_SENTINEL_VALUES: frozenset[str] = frozenset({
    "unknown",
    "not known",
    "not available",
    "not applicable",
    "no information",
    "missing",
    "none",
    "null",
})


def normalize_case_identifier(value: str) -> str:
    """Canonicalize a case-identifier surface form.

    NFKC-folds (so full-width and composed characters collapse), trims
    surrounding whitespace and wrapping punctuation, collapses internal
    whitespace runs to a single space, and casefolds. Internal hyphens,
    slashes and digits are preserved. Idempotent.
    """
    folded = unicodedata.normalize("NFKC", value)
    trimmed = folded.strip().strip(_TRAILING_PUNCT).strip()
    collapsed = re.sub(r"\s+", " ", trimmed)
    return collapsed.casefold()


def is_matchable_identifier(normalized: str) -> bool:
    """Whether a normalized identifier is specific enough to match on."""
    return (
        len(normalized) >= MIN_IDENTIFIER_LENGTH
        and normalized not in _SENTINEL_VALUES
    )


def _index_own_identifiers(
    refs: list[CaseIdentifierRef],
) -> dict[str, tuple[CaseIdentifierKind, str]]:
    """Map normalized value → (kind, verbatim) for a report's own identifiers.

    Unmatchable values are dropped so they can never serve as a link
    target. On duplicate normalized values the first entry wins, which
    keeps the surfaced provenance stable under list reordering only
    insofar as the loader's own order is stable — the ordering of
    ``matches`` is normalised separately in :func:`compare_explicit_links`.
    """
    index: dict[str, tuple[CaseIdentifierKind, str]] = {}
    for ref in refs:
        normalized = normalize_case_identifier(ref.value)
        if not is_matchable_identifier(normalized):
            continue
        index.setdefault(normalized, (ref.kind, ref.value))
    return index


def _find_matches(
    source: Report,
    target: Report,
    direction: ExplicitLinkDirection,
    target_index: dict[str, tuple[CaseIdentifierKind, str]],
    source_own: dict[str, tuple[CaseIdentifierKind, str]],
) -> list[ExplicitLinkMatch]:
    """Resolve ``source``'s pointers against ``target``'s own identifiers."""
    matches: list[ExplicitLinkMatch] = []
    seen: set[str] = set()
    for pointer in source.other_case_identifiers:
        normalized = normalize_case_identifier(pointer)
        if not is_matchable_identifier(normalized):
            continue
        # Self-pointer: the source is referring to its own earlier
        # transmission, which says nothing about the other report.
        if normalized in source_own:
            continue
        if normalized in seen:
            continue
        target_entry = target_index.get(normalized)
        if target_entry is None:
            continue
        seen.add(normalized)
        target_kind, target_value = target_entry
        matches.append(
            ExplicitLinkMatch(
                direction=direction,
                normalized_value=normalized,
                target_kind=target_kind,
                pointer_value=pointer,
                target_value=target_value,
            )
        )
    return matches


# ==================================================================
# Public comparator
# ==================================================================


def compare_explicit_links(r1: Report, r2: Report) -> ExplicitLinkAssessment:
    """Decide whether either report explicitly names the other.

    Parameters
    ----------
    r1, r2 : Report
        The two reports of the candidate pair. Only their
        ``case_identifiers`` and ``other_case_identifiers`` fields are
        read.

    Returns
    -------
    ExplicitLinkAssessment
        ``EXPLICIT_LINK`` with every resolved pointer in ``matches`` when
        either report names the other; ``NO_LINK`` when identifier data
        is present but nothing resolves; ``NOT_ASSESSED`` when neither
        report carries any identifier data at all (the loader does not
        supply these fields), which downstream code must read as *no
        information* rather than as an assessed absence of a link.
    """
    has_any_data = any(
        (report.case_identifiers or report.other_case_identifiers)
        for report in (r1, r2)
    )
    if not has_any_data:
        return ExplicitLinkAssessment(
            verdict=ExplicitLinkVerdict.NOT_ASSESSED,
            reasoning=(
                "Neither report carries case-identifier data; explicit "
                "inter-report links were not assessed."
            ),
        )

    own_1 = _index_own_identifiers(r1.case_identifiers)
    own_2 = _index_own_identifiers(r2.case_identifiers)

    matches = _find_matches(
        r1, r2, ExplicitLinkDirection.R1_TO_R2, own_2, own_1
    ) + _find_matches(
        r2, r1, ExplicitLinkDirection.R2_TO_R1, own_1, own_2
    )

    if not matches:
        return ExplicitLinkAssessment(
            verdict=ExplicitLinkVerdict.NO_LINK,
            reasoning=(
                "Case-identifier data present, but neither report "
                "references an identifier belonging to the other."
            ),
        )

    matches.sort(
        key=lambda m: (
            m.direction.value,
            m.normalized_value,
            m.target_kind.value,
        )
    )
    return ExplicitLinkAssessment(
        verdict=ExplicitLinkVerdict.EXPLICIT_LINK,
        matches=matches,
        reasoning=_describe(matches),
    )


def _describe(matches: list[ExplicitLinkMatch]) -> str:
    """Render a short prose summary of the resolved links.

    The named kind is the *target's* field — the side the pointer
    resolved against — since the reference itself is untyped.
    """
    parts = [
        f"{_DIRECTION_PROSE[m.direction]} via "
        f"{m.target_kind.value}={m.pointer_value.strip()}"
        for m in matches
    ]
    return "Explicit inter-report link: " + "; ".join(parts) + "."


_DIRECTION_PROSE: dict[ExplicitLinkDirection, str] = {
    ExplicitLinkDirection.R1_TO_R2: "report 1 references report 2",
    ExplicitLinkDirection.R2_TO_R1: "report 2 references report 1",
}
