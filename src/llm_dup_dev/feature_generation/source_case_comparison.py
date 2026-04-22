"""Deterministic pair-level comparator for narrative source/case signals.

Consumes two :class:`NarrativeSourceCase` extractions (each produced
independently per report by the LLM task
:mod:`llm_dup_dev.llm.tasks.narrative_source_case`) and returns a
:class:`SourceCaseAssessment` describing what, if anything, the pair of
extractions jointly implies for duplicate adjudication.

No LLM is involved here — all logic is deterministic, stateless, and
DB-free, following the same conventions as the other feature-generation
modules (:mod:`llm_dup_dev.feature_generation.matching`,
:mod:`llm_dup_dev.feature_generation.inconsistencies`, …).

The comparator is deliberately conservative:

- A shared source identifier requires both sides to populate it, with
  matching type and matching normalized value.
- Two case-within-source identifiers count as *comparable* only when
  they belong to the same non-unknown family and both carry non-empty
  normalized values.  Otherwise the pair is marked uncomparable rather
  than different; mismatch alone never becomes evidence of different
  individuals.
- In absence of a reliable shared source identifier, two reports that
  both describe a multi-patient source but lack case labels are flagged
  conservatively (``MULTI_PATIENT_CONTEXT_NO_LINK``) rather than being
  inferred as duplicates.

See: docs/data_contracts.md § Derived Narrative Signals
"""

from __future__ import annotations

from llm_dup_dev.models.narrative_source_case import (
    CaseIdentifierFamily,
    NarrativeSourceCase,
    SourceCaseAssessment,
    SourceCaseVerdict,
    SourceIdentifierType,
)


# ==================================================================
# Normalization primitives (also used by the LLM task for post-parse)
# ==================================================================

_DOI_PREFIXES: tuple[str, ...] = (
    "https://doi.org/",
    "http://doi.org/",
    "doi.org/",
    "doi:",
    "doi ",
)
_DOI_TRAILING_PUNCT: str = ".,;:)]>"


def normalize_doi(s: str) -> str:
    """Canonicalize a DOI surface form.

    Strips common prefixes (``doi:``, ``https://doi.org/``, …), lowercases
    the result, and trims trailing punctuation. Idempotent.
    """
    value = s.strip().lower()
    for prefix in _DOI_PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix):].lstrip()
            break
    return value.rstrip(_DOI_TRAILING_PUNCT).strip()


_FAMILY_ALIASES: dict[str, CaseIdentifierFamily] = {
    "case": CaseIdentifierFamily.CASE,
    "cases": CaseIdentifierFamily.CASE,
    "patient": CaseIdentifierFamily.PATIENT,
    "patients": CaseIdentifierFamily.PATIENT,
    "pt": CaseIdentifierFamily.PT,
    "pts": CaseIdentifierFamily.PT,
    "subject": CaseIdentifierFamily.SUBJECT,
    "subjects": CaseIdentifierFamily.SUBJECT,
}


def normalize_case_identifier_family(s: str) -> CaseIdentifierFamily:
    """Map a free-form family label to :class:`CaseIdentifierFamily`.

    Unknown labels collapse to :attr:`CaseIdentifierFamily.UNKNOWN`.
    """
    key = s.strip().lower().rstrip(".")
    return _FAMILY_ALIASES.get(key, CaseIdentifierFamily.UNKNOWN)


def normalize_case_identifier_value(s: str) -> str:
    """Canonicalize a case-within-source value for comparison.

    Rules:
      - trim whitespace and uppercase letters;
      - for a purely numeric value, strip leading zeros (``"01"`` -> ``"1"``);
      - for values with a family prefix ("Case 2", "Patient B", "Subject-01",
        "Pt 3"), strip the prefix and any non-alphanumeric separators,
        then apply the numeric rule if the remainder is purely digits.

    Idempotent.
    """
    raw = s.strip()
    if not raw:
        return ""

    upper = raw.upper()
    lowered = raw.lower()
    # Longest alias wins ("patients" before "patient", "pts" before "pt") so
    # prefix-collision cases like "pts 3" strip the full prefix.
    for alias in sorted(_FAMILY_ALIASES, key=len, reverse=True):
        if not lowered.startswith(alias):
            continue
        # Require a word boundary after the alias to avoid matching e.g.
        # "casey" against "case".
        tail = lowered[len(alias):]
        if tail and tail[0].isalpha():
            continue
        rest = raw[len(alias):].lstrip(" \t-_:.#").strip()
        if rest:
            upper = rest.upper()
        break

    if upper.isdigit():
        return str(int(upper))
    return upper


def _normalize_source_identifier_value(
    value: str,
    id_type: SourceIdentifierType | None,
) -> str:
    """Light normalization of a source identifier for equality comparison."""
    if id_type == SourceIdentifierType.DOI:
        return normalize_doi(value)
    return value.strip().lower()


# ==================================================================
# Comparability helpers
# ==================================================================

def are_source_identifiers_comparable(
    a: NarrativeSourceCase,
    b: NarrativeSourceCase,
) -> bool:
    """Return True iff both extractions have comparable source identifiers.

    "Comparable" means: both sides have a non-empty ``source_identifier``,
    both sides have a non-null, non-UNKNOWN ``source_identifier_type``,
    and the two types are equal.
    """
    if not (a.source_identifier and b.source_identifier):
        return False
    if a.source_identifier_type is None or b.source_identifier_type is None:
        return False
    if a.source_identifier_type == SourceIdentifierType.UNKNOWN:
        return False
    if b.source_identifier_type == SourceIdentifierType.UNKNOWN:
        return False
    return a.source_identifier_type == b.source_identifier_type


def are_case_identifiers_comparable(
    a: NarrativeSourceCase,
    b: NarrativeSourceCase,
) -> bool:
    """Return True iff two case-within-source identifiers are safely comparable.

    Both sides must have a non-null, non-UNKNOWN
    ``case_identifier_family`` with matching values, and both sides must
    have a non-empty ``case_identifier_value_normalized``.
    """
    if a.case_identifier_family is None or b.case_identifier_family is None:
        return False
    if a.case_identifier_family == CaseIdentifierFamily.UNKNOWN:
        return False
    if b.case_identifier_family == CaseIdentifierFamily.UNKNOWN:
        return False
    if a.case_identifier_family != b.case_identifier_family:
        return False
    if not (
        a.case_identifier_value_normalized
        and b.case_identifier_value_normalized
    ):
        return False
    return True


# ==================================================================
# Pair-level verdict
# ==================================================================

def _shared_source(
    a: NarrativeSourceCase,
    b: NarrativeSourceCase,
) -> tuple[str, SourceIdentifierType] | None:
    """Return the normalized shared source identifier and type, if any."""
    if not are_source_identifiers_comparable(a, b):
        return None
    id_type = a.source_identifier_type
    assert id_type is not None  # guaranteed by are_source_identifiers_comparable
    va = _normalize_source_identifier_value(a.source_identifier or "", id_type)
    vb = _normalize_source_identifier_value(b.source_identifier or "", id_type)
    if va and va == vb:
        return va, id_type
    return None


def compare_source_case(
    a: NarrativeSourceCase | None,
    b: NarrativeSourceCase | None,
) -> SourceCaseAssessment:
    """Compare two per-report narrative extractions deterministically.

    Parameters
    ----------
    a, b : NarrativeSourceCase or None
        Extractions for the two reports in a candidate pair. ``None``
        means the extraction was not available for that report.

    Returns
    -------
    SourceCaseAssessment
        Verdict plus provenance (shared identifier, comparability flags,
        one-line reasoning).
    """
    if a is None or b is None:
        return SourceCaseAssessment(
            verdict=SourceCaseVerdict.NOT_ASSESSED,
            reasoning="One or both extractions missing.",
        )

    shared = _shared_source(a, b)
    case_comparable = are_case_identifiers_comparable(a, b)
    case_compared = bool(
        a.case_identifier_within_source and b.case_identifier_within_source
    )

    if shared is not None:
        shared_value, shared_type = shared

        if a.single_patient_source or b.single_patient_source:
            return SourceCaseAssessment(
                verdict=SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT,
                shared_source_identifier=shared_value,
                shared_source_identifier_type=shared_type,
                case_identifiers_compared=case_compared,
                case_identifiers_comparable=case_comparable,
                reasoning=(
                    "Same source identifier and at least one narrative "
                    "indicates a single-patient source."
                ),
            )

        if case_comparable:
            if (
                a.case_identifier_value_normalized
                == b.case_identifier_value_normalized
            ):
                return SourceCaseAssessment(
                    verdict=SourceCaseVerdict.SAME_SOURCE_SAME_CASE,
                    shared_source_identifier=shared_value,
                    shared_source_identifier_type=shared_type,
                    case_identifiers_compared=True,
                    case_identifiers_comparable=True,
                    reasoning=(
                        "Same source identifier and matching comparable "
                        "case-within-source identifier."
                    ),
                )
            return SourceCaseAssessment(
                verdict=SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE,
                shared_source_identifier=shared_value,
                shared_source_identifier_type=shared_type,
                case_identifiers_compared=True,
                case_identifiers_comparable=True,
                reasoning=(
                    "Same source identifier and different comparable "
                    "case-within-source identifiers."
                ),
            )

        return SourceCaseAssessment(
            verdict=SourceCaseVerdict.SAME_SOURCE_CASE_UNCOMPARABLE,
            shared_source_identifier=shared_value,
            shared_source_identifier_type=shared_type,
            case_identifiers_compared=case_compared,
            case_identifiers_comparable=False,
            reasoning=(
                "Same source identifier but case identifiers are missing "
                "or not safely comparable."
            ),
        )

    if a.multi_patient_source and b.multi_patient_source:
        return SourceCaseAssessment(
            verdict=SourceCaseVerdict.MULTI_PATIENT_CONTEXT_NO_LINK,
            case_identifiers_compared=case_compared,
            case_identifiers_comparable=case_comparable,
            reasoning=(
                "Both narratives indicate a multi-patient source but no "
                "reliable shared source identifier is available."
            ),
        )

    return SourceCaseAssessment(
        verdict=SourceCaseVerdict.NO_DECISIVE_SIGNAL,
        case_identifiers_compared=case_compared,
        case_identifiers_comparable=case_comparable,
        reasoning="No decisive source or case signal from the narratives.",
    )
