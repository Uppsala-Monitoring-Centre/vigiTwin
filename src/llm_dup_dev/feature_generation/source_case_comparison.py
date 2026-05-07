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

- Each report may carry several typed source identifiers (DOI, study
  name, NCT, etc.). Two reports share a source iff their identifier
  lists intersect on at least one ``(type, normalized_value)`` tuple
  with a non-UNKNOWN type. The first matching tuple, ordered by a fixed
  type preference, is surfaced on the assessment.
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

import re

from llm_dup_dev.models.narrative_source_case import (
    CaseIdentifierFamily,
    NarrativeSourceCase,
    SourceCaseAssessment,
    SourceCaseVerdict,
    SourceIdentifier,
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


_STUDY_NAME_LEADING_PREFIXES: tuple[str, ...] = (
    "study name:",
    "study:",
    "trial name:",
    "trial:",
)
_STUDY_NAME_TRAILING_TOKENS: tuple[str, ...] = (
    "registry study",
    "trial study",
    "study",
    "trial",
    "registry",
)


def normalize_study_name(s: str) -> str:
    """Canonicalize a study/trial name (e.g. "EpcoReal", "PARADIGM-HF").

    - lowercase, collapse internal whitespace, strip surrounding quotes
      and outer punctuation;
    - drop a leading ``study name:`` / ``study:`` / ``trial:`` if the
      model preserved it;
    - drop a single trailing token ``study`` / ``trial`` /
      ``registry`` (also the two-word combinations ``trial study`` and
      ``registry study``) so "EpcoReal Study" and "EpcoReal" collapse.

    Hyphens and digits inside the name (``paradigm-hf``, ``keynote-001``)
    are preserved. Idempotent.
    """
    value = s.strip().strip("\"'`“”‘’").strip()
    if not value:
        return ""
    value = value.lower()
    for prefix in _STUDY_NAME_LEADING_PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix):].lstrip()
            break
    value = re.sub(r"\s+", " ", value).strip(" \t.,;:")
    for token in _STUDY_NAME_TRAILING_TOKENS:
        if value.endswith(" " + token):
            value = value[: -(len(token) + 1)].rstrip(" \t.,;:")
            break
    return value


_TRIAL_REG_LEADING_PREFIXES: tuple[str, ...] = (
    "clinicaltrials.gov:",
    "clinicaltrials.gov",
    "clinical trials.gov:",
    "registry id:",
    "trial registration:",
    "trial registration number:",
)


def normalize_trial_registration(s: str) -> str:
    """Canonicalize a trial-registry ID (NCT, EudraCT, ISRCTN, …).

    - strip surrounding whitespace and punctuation;
    - drop common leading labels (``clinicaltrials.gov:``,
      ``trial registration:``, ``registry id:``);
    - uppercase the result;
    - collapse internal whitespace to a single space.

    Hyphens and digits are preserved (e.g. ``EUDRACT 2020-001234-12``).
    Idempotent.
    """
    value = s.strip().strip(".,;:()[]")
    if not value:
        return ""
    lowered = value.lower()
    for prefix in _TRIAL_REG_LEADING_PREFIXES:
        if lowered.startswith(prefix):
            value = value[len(prefix):].lstrip(" \t:")
            break
    value = re.sub(r"\s+", " ", value).strip()
    return value.upper()


def normalize_pmid(s: str) -> str:
    """Canonicalize a PubMed ID — keep only digits."""
    digits = re.sub(r"\D+", "", s)
    return digits


def normalize_protocol_number(s: str) -> str:
    """Canonicalize a sponsor protocol number.

    Uppercases the value, strips surrounding whitespace and outer
    punctuation, and collapses internal whitespace. Hyphens and slashes
    are preserved (``GS-US-565-1234``). Idempotent.
    """
    value = s.strip().strip(".,;:()[]")
    if not value:
        return ""
    value = re.sub(r"\s+", " ", value).strip()
    return value.upper()


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


_SOURCE_ID_NORMALIZERS = {
    SourceIdentifierType.DOI: normalize_doi,
    SourceIdentifierType.PMID: normalize_pmid,
    SourceIdentifierType.TRIAL_REGISTRATION: normalize_trial_registration,
    SourceIdentifierType.PROTOCOL_NUMBER: normalize_protocol_number,
    SourceIdentifierType.STUDY_NAME: normalize_study_name,
}


def _normalize_source_identifier_value(
    value: str,
    id_type: SourceIdentifierType | None,
) -> str:
    """Light normalization of a source identifier for equality comparison.

    Dispatches per type. ``LINKED_CASE`` and ``ARTICLE_REFERENCE`` use the
    default lowercase-and-strip rule because their raw form is already the
    intended comparable string. ``UNKNOWN`` / ``None`` fall through to the
    same default.
    """
    if id_type is None:
        return value.strip().lower()
    normalizer = _SOURCE_ID_NORMALIZERS.get(id_type)
    if normalizer is not None:
        return normalizer(value)
    return value.strip().lower()


# Order of preference for which matching identifier to surface on the
# pair-level assessment when several types match across two reports. More
# specific identifiers come first.
_IDENTIFIER_PREFERENCE: tuple[SourceIdentifierType, ...] = (
    SourceIdentifierType.DOI,
    SourceIdentifierType.PMID,
    SourceIdentifierType.TRIAL_REGISTRATION,
    SourceIdentifierType.LINKED_CASE,
    SourceIdentifierType.PROTOCOL_NUMBER,
    SourceIdentifierType.STUDY_NAME,
    SourceIdentifierType.ARTICLE_REFERENCE,
)


def _normalized_identifier_set(
    extraction: NarrativeSourceCase,
) -> set[tuple[SourceIdentifierType, str]]:
    """Build the set of comparable ``(type, normalized_value)`` tuples."""
    out: set[tuple[SourceIdentifierType, str]] = set()
    for ident in extraction.source_identifiers:
        if ident.type == SourceIdentifierType.UNKNOWN:
            continue
        normalized = _normalize_source_identifier_value(ident.value, ident.type)
        if not normalized:
            continue
        out.add((ident.type, normalized))
    return out


# ==================================================================
# Comparability helpers
# ==================================================================

def are_source_identifiers_comparable(
    a: NarrativeSourceCase,
    b: NarrativeSourceCase,
) -> bool:
    """Return True iff both extractions carry an identifier type in common.

    "Comparable" means: each side carries at least one non-UNKNOWN
    identifier, and the two sides share at least one type. Whether the
    actual values match is decided separately by :func:`_shared_source`.
    """
    types_a = {
        ident.type
        for ident in a.source_identifiers
        if ident.type != SourceIdentifierType.UNKNOWN
    }
    types_b = {
        ident.type
        for ident in b.source_identifiers
        if ident.type != SourceIdentifierType.UNKNOWN
    }
    if not types_a or not types_b:
        return False
    return bool(types_a & types_b)


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
    """Return one normalized shared ``(value, type)`` from the intersection.

    When several types match across the two reports, the most specific
    one (per :data:`_IDENTIFIER_PREFERENCE`) is surfaced.
    """
    set_a = _normalized_identifier_set(a)
    set_b = _normalized_identifier_set(b)
    intersection = set_a & set_b
    if not intersection:
        return None
    by_type: dict[SourceIdentifierType, str] = {}
    for id_type, value in intersection:
        # If somehow two normalized values for the same type both match
        # (e.g. duplicate entries that survived dedup), keep the first.
        by_type.setdefault(id_type, value)
    for preferred in _IDENTIFIER_PREFERENCE:
        if preferred in by_type:
            return by_type[preferred], preferred
    # Defensive fallback: any element of the intersection.
    id_type, value = next(iter(intersection))
    return value, id_type


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
