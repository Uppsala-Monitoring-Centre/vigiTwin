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
            source_identifier="10.1234/abc",
            source_identifier_type=SourceIdentifierType.DOI,
            case_identifier_within_source="Case 2",
            case_identifier_family=CaseIdentifierFamily.CASE,
            case_identifier_value_normalized="2",
        )
        fake_client.seed_response(seeded)
        result = extract_source_case(fake_client, "R001", "Some narrative text.")
        assert result.source_type == SourceType.LITERATURE
        assert result.source_identifier == "10.1234/abc"
        assert len(fake_client.requests) == 1

    def test_post_normalizes_doi_prefix(self, fake_client):
        fake_client.seed_response(NarrativeSourceCase(
            source_identifier="DOI: 10.XXXX/ABC.",
            source_identifier_type=SourceIdentifierType.DOI,
        ))
        result = extract_source_case(fake_client, "R001", "narrative")
        assert result.source_identifier == "10.xxxx/abc"

    def test_post_normalizes_doi_with_url(self, fake_client):
        fake_client.seed_response(NarrativeSourceCase(
            source_identifier="https://doi.org/10.ABC/Def",
            source_identifier_type=SourceIdentifierType.DOI,
        ))
        result = extract_source_case(fake_client, "R001", "narrative")
        assert result.source_identifier == "10.abc/def"

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
            "source_identifier": "10.ABC/def",
            "source_identifier_type": "doi",
        })
        result = extract_source_case(fake_client, "R001", "narrative")
        assert result.source_type == SourceType.LITERATURE
        assert result.source_identifier == "10.abc/def"


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
