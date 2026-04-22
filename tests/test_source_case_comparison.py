"""Tests for the deterministic pair-level source/case comparator."""

import pytest

from llm_dup_dev.feature_generation.source_case_comparison import (
    are_case_identifiers_comparable,
    are_source_identifiers_comparable,
    compare_source_case,
    normalize_case_identifier_family,
    normalize_case_identifier_value,
    normalize_doi,
)
from llm_dup_dev.models.narrative_source_case import (
    CaseIdentifierFamily,
    NarrativeSourceCase,
    SourceCaseVerdict,
    SourceIdentifierType,
    SourceType,
)


# ==================================================================
# Normalization primitives
# ==================================================================

class TestNormalizeDoi:

    @pytest.mark.parametrize("raw,expected", [
        ("10.1234/abc", "10.1234/abc"),
        ("doi: 10.1234/ABC", "10.1234/abc"),
        ("DOI:10.1234/abc", "10.1234/abc"),
        ("https://doi.org/10.1234/ABC", "10.1234/abc"),
        ("http://doi.org/10.1234/abc", "10.1234/abc"),
        ("doi.org/10.1234/abc", "10.1234/abc"),
        ("10.1234/ABC.", "10.1234/abc"),
        ("  10.1234/abc  ", "10.1234/abc"),
    ])
    def test_variants(self, raw, expected):
        assert normalize_doi(raw) == expected

    def test_idempotent(self):
        once = normalize_doi("DOI: 10.ABC/Def.")
        twice = normalize_doi(once)
        assert once == twice


class TestNormalizeCaseIdentifierValue:

    @pytest.mark.parametrize("raw,expected", [
        ("Case 1", "1"),
        ("Case 02", "2"),
        ("Patient B", "B"),
        ("Subject-01", "1"),
        ("Pt 3", "3"),
        ("pt.4", "4"),
        ("A", "A"),
        ("", ""),
        ("  Case  5  ", "5"),
    ])
    def test_variants(self, raw, expected):
        assert normalize_case_identifier_value(raw) == expected

    def test_idempotent(self):
        once = normalize_case_identifier_value("Patient B")
        twice = normalize_case_identifier_value(once)
        assert once == twice


class TestNormalizeCaseIdentifierFamily:

    @pytest.mark.parametrize("raw,expected", [
        ("case", CaseIdentifierFamily.CASE),
        ("Cases", CaseIdentifierFamily.CASE),
        ("Patient", CaseIdentifierFamily.PATIENT),
        ("patients", CaseIdentifierFamily.PATIENT),
        ("Pt", CaseIdentifierFamily.PT),
        ("pt.", CaseIdentifierFamily.PT),
        ("subject", CaseIdentifierFamily.SUBJECT),
        ("something_else", CaseIdentifierFamily.UNKNOWN),
        ("", CaseIdentifierFamily.UNKNOWN),
    ])
    def test_variants(self, raw, expected):
        assert normalize_case_identifier_family(raw) == expected


# ==================================================================
# Comparability helpers
# ==================================================================

def _lit(
    *,
    doi: str | None = None,
    single: bool | None = None,
    multi: bool | None = None,
    case_family: CaseIdentifierFamily | None = None,
    case_value: str | None = None,
    case_surface: str | None = None,
) -> NarrativeSourceCase:
    """Compact constructor for test extractions."""
    return NarrativeSourceCase(
        source_type=SourceType.LITERATURE if doi else SourceType.UNKNOWN,
        multi_patient_source=multi,
        single_patient_source=single,
        source_identifier=doi,
        source_identifier_type=SourceIdentifierType.DOI if doi else None,
        case_identifier_within_source=case_surface,
        case_identifier_family=case_family,
        case_identifier_value_normalized=case_value,
    )


class TestSourceIdentifierComparability:

    def test_same_doi_type_comparable(self):
        a = _lit(doi="10.a/b")
        b = _lit(doi="10.c/d")
        assert are_source_identifiers_comparable(a, b) is True

    def test_different_types_not_comparable(self):
        a = _lit(doi="10.a/b")
        b = NarrativeSourceCase(
            source_identifier="AB-1",
            source_identifier_type=SourceIdentifierType.LINKED_CASE,
        )
        assert are_source_identifiers_comparable(a, b) is False

    def test_missing_identifier_not_comparable(self):
        a = _lit(doi="10.a/b")
        b = NarrativeSourceCase()
        assert are_source_identifiers_comparable(a, b) is False

    def test_unknown_type_not_comparable(self):
        a = NarrativeSourceCase(
            source_identifier="X",
            source_identifier_type=SourceIdentifierType.UNKNOWN,
        )
        b = NarrativeSourceCase(
            source_identifier="X",
            source_identifier_type=SourceIdentifierType.UNKNOWN,
        )
        assert are_source_identifiers_comparable(a, b) is False


class TestCaseIdentifierComparability:

    def test_same_family_and_values_comparable(self):
        a = _lit(case_family=CaseIdentifierFamily.CASE, case_value="1")
        b = _lit(case_family=CaseIdentifierFamily.CASE, case_value="2")
        assert are_case_identifiers_comparable(a, b) is True

    def test_different_family_not_comparable(self):
        a = _lit(case_family=CaseIdentifierFamily.CASE, case_value="1")
        b = _lit(case_family=CaseIdentifierFamily.PATIENT, case_value="B")
        assert are_case_identifiers_comparable(a, b) is False

    def test_unknown_family_not_comparable(self):
        a = _lit(case_family=CaseIdentifierFamily.UNKNOWN, case_value="1")
        b = _lit(case_family=CaseIdentifierFamily.CASE, case_value="1")
        assert are_case_identifiers_comparable(a, b) is False

    def test_missing_value_not_comparable(self):
        a = _lit(case_family=CaseIdentifierFamily.CASE, case_value=None)
        b = _lit(case_family=CaseIdentifierFamily.CASE, case_value="1")
        assert are_case_identifiers_comparable(a, b) is False

    def test_null_family_not_comparable(self):
        a = _lit(case_family=None, case_value="1")
        b = _lit(case_family=CaseIdentifierFamily.CASE, case_value="1")
        assert are_case_identifiers_comparable(a, b) is False


# ==================================================================
# compare_source_case — scenarios from the request
# ==================================================================

class TestCompareSourceCase:

    # -- missing inputs ---------------------------------------------

    def test_missing_a(self):
        result = compare_source_case(None, _lit(doi="10.a/b"))
        assert result.verdict == SourceCaseVerdict.NOT_ASSESSED

    def test_missing_b(self):
        result = compare_source_case(_lit(doi="10.a/b"), None)
        assert result.verdict == SourceCaseVerdict.NOT_ASSESSED

    def test_both_missing(self):
        result = compare_source_case(None, None)
        assert result.verdict == SourceCaseVerdict.NOT_ASSESSED

    # -- same-source scenarios --------------------------------------

    def test_same_doi_single_patient(self):
        """Case 2: same DOI, narrative indicates single-patient source."""
        a = _lit(doi="10.a/b", single=True)
        b = _lit(doi="10.a/b", single=True)
        result = compare_source_case(a, b)
        assert result.verdict == SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT
        assert result.shared_source_identifier == "10.a/b"
        assert result.shared_source_identifier_type == SourceIdentifierType.DOI

    def test_same_doi_single_patient_one_sided(self):
        """Only one side reports single_patient_source=True."""
        a = _lit(doi="10.a/b", single=True)
        b = _lit(doi="10.a/b")
        result = compare_source_case(a, b)
        assert result.verdict == SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT

    def test_same_doi_same_case(self):
        """Case 7: same DOI, both 'Case 2'."""
        a = _lit(
            doi="10.a/b", multi=True,
            case_family=CaseIdentifierFamily.CASE, case_value="2",
            case_surface="Case 2",
        )
        b = _lit(
            doi="10.a/b", multi=True,
            case_family=CaseIdentifierFamily.CASE, case_value="2",
            case_surface="Case 2",
        )
        result = compare_source_case(a, b)
        assert result.verdict == SourceCaseVerdict.SAME_SOURCE_SAME_CASE
        assert result.case_identifiers_comparable is True

    def test_same_doi_different_case(self):
        """Case 1: same DOI, 'Case 1' vs 'Case 2'."""
        a = _lit(
            doi="10.a/b", multi=True,
            case_family=CaseIdentifierFamily.CASE, case_value="1",
            case_surface="Case 1",
        )
        b = _lit(
            doi="10.a/b", multi=True,
            case_family=CaseIdentifierFamily.CASE, case_value="2",
            case_surface="Case 2",
        )
        result = compare_source_case(a, b)
        assert result.verdict == SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE
        assert result.case_identifiers_comparable is True

    def test_same_doi_case_uncomparable(self):
        """Case 6-like: same DOI, one side 'Case 1' other side unknown family."""
        a = _lit(
            doi="10.a/b", multi=True,
            case_family=CaseIdentifierFamily.CASE, case_value="1",
            case_surface="Case 1",
        )
        b = _lit(doi="10.a/b", multi=True)  # no case identifier
        result = compare_source_case(a, b)
        assert result.verdict == SourceCaseVerdict.SAME_SOURCE_CASE_UNCOMPARABLE

    def test_same_doi_case_families_differ(self):
        """Same DOI, 'Case 1' vs 'Patient B' — mismatch-but-not-different."""
        a = _lit(
            doi="10.a/b", multi=True,
            case_family=CaseIdentifierFamily.CASE, case_value="1",
            case_surface="Case 1",
        )
        b = _lit(
            doi="10.a/b", multi=True,
            case_family=CaseIdentifierFamily.PATIENT, case_value="B",
            case_surface="Patient B",
        )
        result = compare_source_case(a, b)
        assert result.verdict == SourceCaseVerdict.SAME_SOURCE_CASE_UNCOMPARABLE
        assert result.case_identifiers_comparable is False

    # -- linked_case (summary-case-like) ----------------------------

    def test_shared_linked_case(self):
        """Case 3: two narratives share 'Linked case: AB-123456789'."""
        a = NarrativeSourceCase(
            source_type=SourceType.SUMMARY_CASE,
            single_patient_source=True,
            source_identifier="AB-123456789",
            source_identifier_type=SourceIdentifierType.LINKED_CASE,
        )
        b = NarrativeSourceCase(
            source_type=SourceType.SUMMARY_CASE,
            single_patient_source=True,
            source_identifier="AB-123456789",
            source_identifier_type=SourceIdentifierType.LINKED_CASE,
        )
        result = compare_source_case(a, b)
        assert result.verdict == SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT
        assert result.shared_source_identifier_type == SourceIdentifierType.LINKED_CASE

    # -- report-specific identifier mismatch must NOT decide --------

    def test_mismatched_report_specific_ids_no_decisive_signal(self):
        """Case 4: two narratives each have a different 'Other Identification
        Number', no DOI, no linked_case.  Extractor correctly buckets them as
        report-specific → comparator must report NO_DECISIVE_SIGNAL, never
        treat the mismatch as meaningful."""
        a = NarrativeSourceCase(
            likely_report_specific_identifiers=["Other Identification Number 48392"],
        )
        b = NarrativeSourceCase(
            likely_report_specific_identifiers=["Other Identification Number 99999"],
        )
        result = compare_source_case(a, b)
        assert result.verdict == SourceCaseVerdict.NO_DECISIVE_SIGNAL

    # -- multi-patient without shared source ------------------------

    def test_both_multi_patient_no_shared_source(self):
        """Case 8: both multi-patient, no shared identifier → conservative."""
        a = NarrativeSourceCase(
            source_type=SourceType.SUMMARY_CASE, multi_patient_source=True,
        )
        b = NarrativeSourceCase(
            source_type=SourceType.SUMMARY_CASE, multi_patient_source=True,
        )
        result = compare_source_case(a, b)
        assert result.verdict == SourceCaseVerdict.MULTI_PATIENT_CONTEXT_NO_LINK

    # -- otherwise no decisive signal -------------------------------

    def test_empty_extractions_no_decisive_signal(self):
        a = NarrativeSourceCase()
        b = NarrativeSourceCase()
        result = compare_source_case(a, b)
        assert result.verdict == SourceCaseVerdict.NO_DECISIVE_SIGNAL

    def test_different_dois_no_decisive_signal(self):
        a = _lit(doi="10.a/b", multi=True)
        b = _lit(doi="10.c/d", multi=True)
        result = compare_source_case(a, b)
        # Multi-patient fallback still fires since there is no *shared*
        # identifier; the two DOIs differ.
        assert result.verdict == SourceCaseVerdict.MULTI_PATIENT_CONTEXT_NO_LINK

    def test_different_dois_no_multi_flag(self):
        a = _lit(doi="10.a/b")
        b = _lit(doi="10.c/d")
        result = compare_source_case(a, b)
        assert result.verdict == SourceCaseVerdict.NO_DECISIVE_SIGNAL

    # -- identifier-value normalization at compare time -------------

    def test_doi_normalized_at_compare_time(self):
        """Defensive: compare_source_case renormalizes DOIs in case the
        extraction stage did not."""
        a = NarrativeSourceCase(
            source_identifier="DOI: 10.ABC/Def",
            source_identifier_type=SourceIdentifierType.DOI,
            single_patient_source=True,
        )
        b = NarrativeSourceCase(
            source_identifier="https://doi.org/10.abc/def",
            source_identifier_type=SourceIdentifierType.DOI,
            single_patient_source=True,
        )
        result = compare_source_case(a, b)
        assert result.verdict == SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT
        assert result.shared_source_identifier == "10.abc/def"

    def test_case_1_vs_linked_case_not_comparable(self):
        """Case 6 (explicit): same DOI, one side 'Case 1', other side a
        linked-case-shaped string in case_identifier_within_source — these
        must not be treated as comparable."""
        a = _lit(
            doi="10.a/b", multi=True,
            case_family=CaseIdentifierFamily.CASE, case_value="1",
            case_surface="Case 1",
        )
        b = _lit(
            doi="10.a/b", multi=True,
            case_family=CaseIdentifierFamily.UNKNOWN,
            case_value="XXXXXXX-X",
            case_surface="Linked case: XXXXXXX-X",
        )
        result = compare_source_case(a, b)
        assert result.verdict == SourceCaseVerdict.SAME_SOURCE_CASE_UNCOMPARABLE
        assert result.case_identifiers_comparable is False
