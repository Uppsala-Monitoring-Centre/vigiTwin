"""Tests for the narrative source/case extractor (LLM task).

Frozen JSON round-trip of ``examples/narrative_source_case_example.json``
is covered by the canonical ``tests/test_schemas_examples.py``.
"""

import pytest

from llm_dup_dev.llm.fake import FakeLLMClient
from llm_dup_dev.llm.tasks.narrative_source_case import (
    build_source_case_request,
    extract_source_case,
)
from llm_dup_dev.models.narrative_source_case import (
    CaseIdentifierFamily,
    NarrativeSourceCase,
    SourceIdentifier,
    SourceIdentifierType,
    SourceType,
)


@pytest.fixture
def fake_client():
    return FakeLLMClient()


# ==================================================================
# build_source_case_request
# ==================================================================

class TestBuildRequest:

    def test_includes_narrative(self):
        req = build_source_case_request("Patient was treated with drug X.")
        assert "drug X" in req.user_prompt

    def test_wraps_narrative_in_source_text_delimiter(self):
        req = build_source_case_request("Patient was treated with drug X.")
        assert "<source_text>" in req.user_prompt
        assert "</source_text>" in req.user_prompt
        start = req.user_prompt.index("<source_text>")
        end = req.user_prompt.index("</source_text>")
        assert "drug X" in req.user_prompt[start:end]

    def test_response_model_is_extraction(self):
        req = build_source_case_request("anything")
        assert req.response_model is NarrativeSourceCase

    def test_empty_narrative_uses_fallback_prompt(self):
        req = build_source_case_request("")
        assert "missing" in req.user_prompt.lower()

    def test_whitespace_only_uses_fallback_prompt(self):
        req = build_source_case_request("   ")
        assert "missing" in req.user_prompt.lower()

    def test_none_uses_fallback_prompt(self):
        req = build_source_case_request(None)
        assert "missing" in req.user_prompt.lower()

    def test_default_temperature_is_zero(self):
        req = build_source_case_request("N")
        assert req.temperature == 0.0

    def test_system_prompt_mentions_extraction(self):
        req = build_source_case_request("N")
        assert "extract" in req.system_prompt.lower()

    def test_system_prompt_distinguishes_identifier_kinds(self):
        req = build_source_case_request("N")
        sp = req.system_prompt.lower()
        assert "source identifier" in sp
        assert "case-within-source" in sp
        assert "report-specific" in sp


# ==================================================================
# extract_source_case — short-circuit on empty narrative
# ==================================================================

class TestShortCircuit:

    def test_empty_narrative_skips_llm(self, fake_client):
        result = extract_source_case(fake_client, "R001", "")
        assert result == NarrativeSourceCase(source_type=SourceType.UNKNOWN)
        assert fake_client.requests == []

    def test_whitespace_narrative_skips_llm(self, fake_client):
        result = extract_source_case(fake_client, "R001", "   \n  ")
        assert result.source_type == SourceType.UNKNOWN
        assert fake_client.requests == []

    def test_none_narrative_skips_llm(self, fake_client):
        result = extract_source_case(fake_client, "R001", None)
        assert result.source_type == SourceType.UNKNOWN
        assert fake_client.requests == []


# ==================================================================
# extract_source_case — happy path + normalization
# ==================================================================

class TestExtractHappyPath:

    def test_returns_parsed_extraction(self, fake_client):
        seeded = NarrativeSourceCase(
            source_type=SourceType.LITERATURE,
            multi_patient_source=True,
            source_identifiers=[
                SourceIdentifier(
                    type=SourceIdentifierType.DOI, value="10.1234/abc",
                ),
            ],
            case_identifier_within_source="Case 2",
            case_identifier_family=CaseIdentifierFamily.CASE,
            case_identifier_value_normalized="2",
        )
        fake_client.seed_response(seeded)
        result = extract_source_case(fake_client, "R001", "Some narrative text.")
        assert result.source_type == SourceType.LITERATURE
        assert result.source_identifiers == [
            SourceIdentifier(type=SourceIdentifierType.DOI, value="10.1234/abc"),
        ]
        assert len(fake_client.requests) == 1

    def test_post_normalizes_doi_prefix(self, fake_client):
        fake_client.seed_response(NarrativeSourceCase(
            source_identifiers=[
                SourceIdentifier(
                    type=SourceIdentifierType.DOI, value="DOI: 10.XXXX/ABC.",
                ),
            ],
        ))
        result = extract_source_case(fake_client, "R001", "narrative")
        assert result.source_identifiers == [
            SourceIdentifier(type=SourceIdentifierType.DOI, value="10.xxxx/abc"),
        ]

    def test_post_normalizes_doi_with_url(self, fake_client):
        fake_client.seed_response(NarrativeSourceCase(
            source_identifiers=[
                SourceIdentifier(
                    type=SourceIdentifierType.DOI,
                    value="https://doi.org/10.ABC/Def",
                ),
            ],
        ))
        result = extract_source_case(fake_client, "R001", "narrative")
        assert result.source_identifiers == [
            SourceIdentifier(type=SourceIdentifierType.DOI, value="10.abc/def"),
        ]

    def test_post_normalizes_study_name_strips_trailing_token(self, fake_client):
        """Looser normalization drops trailing 'study' / 'trial' so EpcoReal
        and EpcoReal Study collapse to the same canonical value."""
        fake_client.seed_response(NarrativeSourceCase(
            source_identifiers=[
                SourceIdentifier(
                    type=SourceIdentifierType.STUDY_NAME,
                    value="EpcoReal Study",
                ),
            ],
        ))
        result = extract_source_case(fake_client, "R001", "narrative")
        assert result.source_identifiers == [
            SourceIdentifier(
                type=SourceIdentifierType.STUDY_NAME, value="epcoreal",
            ),
        ]

    def test_post_normalizes_trial_registration(self, fake_client):
        fake_client.seed_response(NarrativeSourceCase(
            source_identifiers=[
                SourceIdentifier(
                    type=SourceIdentifierType.TRIAL_REGISTRATION,
                    value="ClinicalTrials.gov: nct01234567",
                ),
            ],
        ))
        result = extract_source_case(fake_client, "R001", "narrative")
        assert result.source_identifiers == [
            SourceIdentifier(
                type=SourceIdentifierType.TRIAL_REGISTRATION,
                value="NCT01234567",
            ),
        ]

    def test_post_normalizes_pmid_keeps_digits_only(self, fake_client):
        fake_client.seed_response(NarrativeSourceCase(
            source_identifiers=[
                SourceIdentifier(
                    type=SourceIdentifierType.PMID,
                    value="PMID: 12345678",
                ),
            ],
        ))
        result = extract_source_case(fake_client, "R001", "narrative")
        assert result.source_identifiers == [
            SourceIdentifier(type=SourceIdentifierType.PMID, value="12345678"),
        ]

    def test_post_normalizes_protocol_number(self, fake_client):
        fake_client.seed_response(NarrativeSourceCase(
            source_identifiers=[
                SourceIdentifier(
                    type=SourceIdentifierType.PROTOCOL_NUMBER,
                    value="  gs-us-565-1234  ",
                ),
            ],
        ))
        result = extract_source_case(fake_client, "R001", "narrative")
        assert result.source_identifiers == [
            SourceIdentifier(
                type=SourceIdentifierType.PROTOCOL_NUMBER,
                value="GS-US-565-1234",
            ),
        ]

    def test_post_normalize_dedupes_identifiers(self, fake_client):
        """Two entries that normalize to the same (type, value) collapse to one."""
        fake_client.seed_response(NarrativeSourceCase(
            source_identifiers=[
                SourceIdentifier(
                    type=SourceIdentifierType.STUDY_NAME, value="EpcoReal",
                ),
                SourceIdentifier(
                    type=SourceIdentifierType.STUDY_NAME,
                    value="EPCOREAL Trial",
                ),
            ],
        ))
        result = extract_source_case(fake_client, "R001", "narrative")
        assert result.source_identifiers == [
            SourceIdentifier(
                type=SourceIdentifierType.STUDY_NAME, value="epcoreal",
            ),
        ]

    def test_post_normalize_drops_empty_after_normalization(self, fake_client):
        """A PMID that contains no digits normalizes to '' and is dropped."""
        fake_client.seed_response(NarrativeSourceCase(
            source_identifiers=[
                SourceIdentifier(
                    type=SourceIdentifierType.PMID, value="not-a-pmid",
                ),
                SourceIdentifier(
                    type=SourceIdentifierType.DOI, value="10.1/x",
                ),
            ],
        ))
        result = extract_source_case(fake_client, "R001", "narrative")
        assert result.source_identifiers == [
            SourceIdentifier(type=SourceIdentifierType.DOI, value="10.1/x"),
        ]

    def test_post_normalizes_case_value_with_prefix(self, fake_client):
        fake_client.seed_response(NarrativeSourceCase(
            case_identifier_within_source="Pt 03",
            case_identifier_family=CaseIdentifierFamily.PT,
        ))
        result = extract_source_case(fake_client, "R001", "narrative")
        assert result.case_identifier_value_normalized == "3"

    def test_post_normalizes_numeric_case_value(self, fake_client):
        fake_client.seed_response(NarrativeSourceCase(
            case_identifier_within_source="Case 2",
            case_identifier_family=CaseIdentifierFamily.CASE,
            case_identifier_value_normalized="02",
        ))
        result = extract_source_case(fake_client, "R001", "narrative")
        assert result.case_identifier_value_normalized == "2"

    def test_post_maps_family_from_type_when_missing(self, fake_client):
        fake_client.seed_response(NarrativeSourceCase(
            case_identifier_within_source="Patient B",
            case_identifier_type="patient",
            case_identifier_value_normalized="B",
        ))
        result = extract_source_case(fake_client, "R001", "narrative")
        assert result.case_identifier_family == CaseIdentifierFamily.PATIENT

    def test_preserves_report_specific_identifiers(self, fake_client):
        fake_client.seed_response(NarrativeSourceCase(
            likely_report_specific_identifiers=["Other Identification Number 48392"],
        ))
        result = extract_source_case(fake_client, "R001", "narrative")
        assert result.likely_report_specific_identifiers == [
            "Other Identification Number 48392"
        ]

    def test_accepts_dict_payload(self, fake_client):
        fake_client.seed_response({
            "source_type": "literature",
            "source_identifiers": [
                {"type": "doi", "value": "10.ABC/def"},
            ],
        })
        result = extract_source_case(fake_client, "R001", "narrative")
        assert result.source_type == SourceType.LITERATURE
        assert result.source_identifiers == [
            SourceIdentifier(type=SourceIdentifierType.DOI, value="10.abc/def"),
        ]


# ==================================================================
# extract_source_case — failure paths
# ==================================================================

class TestExtractFailures:

    def test_raw_only_response_raises(self, fake_client):
        fake_client.seed_raw("not valid JSON")
        with pytest.raises(ValueError, match="did not return structured output"):
            extract_source_case(fake_client, "R001", "narrative")

    def test_unexpected_parsed_type_raises(self, fake_client):
        # Seed a parsed payload that's neither a NarrativeSourceCase nor a dict.
        fake_client.seed_response(parsed=["not", "valid"])
        with pytest.raises(ValueError, match="unexpected parsed type"):
            extract_source_case(fake_client, "R001", "narrative")
