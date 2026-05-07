"""Tests for the deterministic pair-level source/case comparator."""

import pytest

from llm_dup_dev.feature_generation.source_case_comparison import (
    are_case_identifiers_comparable,
    are_source_identifiers_comparable,
    compare_source_case,
    normalize_case_identifier_family,
    normalize_case_identifier_value,
    normalize_doi,
    normalize_pmid,
    normalize_protocol_number,
    normalize_study_name,
    normalize_trial_registration,
)
from llm_dup_dev.models.narrative_source_case import (
    CaseIdentifierFamily,
    NarrativeSourceCase,
    SourceCaseVerdict,
    SourceIdentifier,
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


class TestNormalizeStudyName:

    @pytest.mark.parametrize("raw,expected", [
        ("EpcoReal", "epcoreal"),
        ("EPCOREAL", "epcoreal"),
        ("EpcoReal Study", "epcoreal"),
        ("EpcoReal Trial", "epcoreal"),
        ("EpcoReal Registry Study", "epcoreal"),
        ("PARADIGM-HF", "paradigm-hf"),
        ("KEYNOTE-001", "keynote-001"),
        ("STUDY NAME: EpcoReal", "epcoreal"),
        ("Trial: PARADIGM-HF Study", "paradigm-hf"),
        ('"EpcoReal"', "epcoreal"),
        ("  EpcoReal  ", "epcoreal"),
        ("", ""),
    ])
    def test_variants(self, raw, expected):
        assert normalize_study_name(raw) == expected

    def test_idempotent(self):
        once = normalize_study_name("EpcoReal Study")
        twice = normalize_study_name(once)
        assert once == twice


class TestNormalizeTrialRegistration:

    @pytest.mark.parametrize("raw,expected", [
        ("NCT01234567", "NCT01234567"),
        ("nct01234567", "NCT01234567"),
        ("ClinicalTrials.gov: NCT01234567", "NCT01234567"),
        ("EudraCT 2020-001234-12", "EUDRACT 2020-001234-12"),
        ("Trial registration: NCT01234567", "NCT01234567"),
        ("ISRCTN12345678", "ISRCTN12345678"),
        ("  nct01234567  ", "NCT01234567"),
        ("", ""),
    ])
    def test_variants(self, raw, expected):
        assert normalize_trial_registration(raw) == expected

    def test_idempotent(self):
        once = normalize_trial_registration("ClinicalTrials.gov: nct01234567")
        twice = normalize_trial_registration(once)
        assert once == twice


class TestNormalizePmid:

    @pytest.mark.parametrize("raw,expected", [
        ("12345678", "12345678"),
        ("PMID 12345678", "12345678"),
        ("PMID: 12345678", "12345678"),
        ("pubmed 12345678", "12345678"),
        ("not-a-pmid", ""),
        ("", ""),
    ])
    def test_variants(self, raw, expected):
        assert normalize_pmid(raw) == expected

    def test_idempotent(self):
        once = normalize_pmid("PMID: 12345678")
        twice = normalize_pmid(once)
        assert once == twice


class TestNormalizeProtocolNumber:

    @pytest.mark.parametrize("raw,expected", [
        ("GS-US-565-1234", "GS-US-565-1234"),
        ("gs-us-565-1234", "GS-US-565-1234"),
        ("MK-1234-001", "MK-1234-001"),
        ("  gs-us-565-1234  ", "GS-US-565-1234"),
        ("", ""),
    ])
    def test_variants(self, raw, expected):
        assert normalize_protocol_number(raw) == expected

    def test_idempotent(self):
        once = normalize_protocol_number("  gs-us-565-1234  ")
        twice = normalize_protocol_number(once)
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
    identifiers = (
        [SourceIdentifier(type=SourceIdentifierType.DOI, value=doi)]
        if doi
        else []
    )
    return NarrativeSourceCase(
        source_type=SourceType.LITERATURE if doi else SourceType.UNKNOWN,
        multi_patient_source=multi,
        single_patient_source=single,
        source_identifiers=identifiers,
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
            source_identifiers=[
                SourceIdentifier(type=SourceIdentifierType.LINKED_CASE, value="AB-1"),
            ],
        )
        assert are_source_identifiers_comparable(a, b) is False

    def test_missing_identifier_not_comparable(self):
        a = _lit(doi="10.a/b")
        b = NarrativeSourceCase()
        assert are_source_identifiers_comparable(a, b) is False

    def test_unknown_type_not_comparable(self):
        a = NarrativeSourceCase(
            source_identifiers=[
                SourceIdentifier(type=SourceIdentifierType.UNKNOWN, value="X"),
            ],
        )
        b = NarrativeSourceCase(
            source_identifiers=[
                SourceIdentifier(type=SourceIdentifierType.UNKNOWN, value="X"),
            ],
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
            source_identifiers=[
                SourceIdentifier(
                    type=SourceIdentifierType.LINKED_CASE,
                    value="AB-123456789",
                ),
            ],
        )
        b = NarrativeSourceCase(
            source_type=SourceType.SUMMARY_CASE,
            single_patient_source=True,
            source_identifiers=[
                SourceIdentifier(
                    type=SourceIdentifierType.LINKED_CASE,
                    value="AB-123456789",
                ),
            ],
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
            source_identifiers=[
                SourceIdentifier(
                    type=SourceIdentifierType.DOI, value="DOI: 10.ABC/Def",
                ),
            ],
            single_patient_source=True,
        )
        b = NarrativeSourceCase(
            source_identifiers=[
                SourceIdentifier(
                    type=SourceIdentifierType.DOI,
                    value="https://doi.org/10.abc/def",
                ),
            ],
            single_patient_source=True,
        )
        result = compare_source_case(a, b)
        assert result.verdict == SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT
        assert result.shared_source_identifier == "10.abc/def"

    # -- multi-identifier scenarios ---------------------------------

    def test_shared_study_name_multi_patient(self):
        """EpcoReal-style: two reports each cite 'EpcoReal' (no DOI yet),
        both indicate a multi-patient source with no per-case label."""
        a = NarrativeSourceCase(
            source_type=SourceType.LITERATURE,
            multi_patient_source=True,
            source_identifiers=[
                SourceIdentifier(
                    type=SourceIdentifierType.STUDY_NAME,
                    value="EpcoReal Study",
                ),
            ],
        )
        b = NarrativeSourceCase(
            source_type=SourceType.LITERATURE,
            multi_patient_source=True,
            source_identifiers=[
                SourceIdentifier(
                    type=SourceIdentifierType.STUDY_NAME,
                    value="EPCOREAL",
                ),
            ],
        )
        # Surface forms differ; the post-parse normalizer collapses both to
        # 'epcoreal' so the comparator sees the shared identifier.
        from llm_dup_dev.llm.tasks.narrative_source_case import _post_normalize
        result = compare_source_case(_post_normalize(a), _post_normalize(b))
        assert result.verdict == SourceCaseVerdict.SAME_SOURCE_CASE_UNCOMPARABLE
        assert result.shared_source_identifier == "epcoreal"
        assert result.shared_source_identifier_type == SourceIdentifierType.STUDY_NAME

    def test_match_on_one_of_several_identifiers(self):
        """A carries DOI + study_name; B carries only study_name. They share
        the study_name and the comparator surfaces it."""
        from llm_dup_dev.llm.tasks.narrative_source_case import _post_normalize
        a = _post_normalize(NarrativeSourceCase(
            source_type=SourceType.LITERATURE,
            multi_patient_source=True,
            source_identifiers=[
                SourceIdentifier(type=SourceIdentifierType.DOI, value="10.A/b"),
                SourceIdentifier(
                    type=SourceIdentifierType.STUDY_NAME,
                    value="EpcoReal",
                ),
            ],
        ))
        b = _post_normalize(NarrativeSourceCase(
            source_type=SourceType.LITERATURE,
            multi_patient_source=True,
            source_identifiers=[
                SourceIdentifier(
                    type=SourceIdentifierType.STUDY_NAME,
                    value="EpcoReal",
                ),
            ],
        ))
        result = compare_source_case(a, b)
        assert result.verdict == SourceCaseVerdict.SAME_SOURCE_CASE_UNCOMPARABLE
        assert result.shared_source_identifier_type == SourceIdentifierType.STUDY_NAME
        assert result.shared_source_identifier == "epcoreal"

    def test_preference_order_doi_wins_over_study_name(self):
        """When both DOI and study_name match, DOI is surfaced."""
        from llm_dup_dev.llm.tasks.narrative_source_case import _post_normalize
        a = _post_normalize(NarrativeSourceCase(
            source_type=SourceType.LITERATURE,
            multi_patient_source=True,
            source_identifiers=[
                SourceIdentifier(type=SourceIdentifierType.DOI, value="10.A/b"),
                SourceIdentifier(
                    type=SourceIdentifierType.STUDY_NAME,
                    value="EpcoReal",
                ),
            ],
        ))
        b = _post_normalize(NarrativeSourceCase(
            source_type=SourceType.LITERATURE,
            multi_patient_source=True,
            source_identifiers=[
                SourceIdentifier(type=SourceIdentifierType.DOI, value="10.A/b"),
                SourceIdentifier(
                    type=SourceIdentifierType.STUDY_NAME,
                    value="EpcoReal",
                ),
            ],
        ))
        result = compare_source_case(a, b)
        assert result.shared_source_identifier_type == SourceIdentifierType.DOI
        assert result.shared_source_identifier == "10.a/b"

    def test_same_type_different_values_no_match(self):
        """Two reports each have a study_name, but they differ — no shared
        source. With both multi-patient flags set, falls back to
        MULTI_PATIENT_CONTEXT_NO_LINK."""
        from llm_dup_dev.llm.tasks.narrative_source_case import _post_normalize
        a = _post_normalize(NarrativeSourceCase(
            multi_patient_source=True,
            source_identifiers=[
                SourceIdentifier(
                    type=SourceIdentifierType.STUDY_NAME, value="EpcoReal",
                ),
            ],
        ))
        b = _post_normalize(NarrativeSourceCase(
            multi_patient_source=True,
            source_identifiers=[
                SourceIdentifier(
                    type=SourceIdentifierType.STUDY_NAME, value="PARADIGM-HF",
                ),
            ],
        ))
        result = compare_source_case(a, b)
        assert result.verdict == SourceCaseVerdict.MULTI_PATIENT_CONTEXT_NO_LINK

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
