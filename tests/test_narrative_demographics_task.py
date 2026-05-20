"""Tests for the narrative demographics extractor (LLM task)."""

import pytest

from llm_dup_dev.llm.fake import FakeLLMClient
from llm_dup_dev.llm.tasks.narrative_demographics import (
    build_narrative_demographics_request,
    extract_narrative_demographics,
)
from llm_dup_dev.models.narrative_demographics import (
    NarrativeAgeValue,
    NarrativeDemographics,
    NarrativeMeasurementValue,
    NarrativeSex,
    NarrativeSexValue,
)


@pytest.fixture
def fake_client():
    return FakeLLMClient()


# ==================================================================
# build_narrative_demographics_request
# ==================================================================

class TestBuildRequest:

    def test_includes_narrative(self):
        req = build_narrative_demographics_request("A 55-year-old male presented.")
        assert "55-year-old" in req.user_prompt

    def test_wraps_narrative_in_source_text_delimiter(self):
        req = build_narrative_demographics_request("A 55-year-old male presented.")
        assert "<source_text>" in req.user_prompt
        assert "</source_text>" in req.user_prompt
        start = req.user_prompt.index("<source_text>")
        end = req.user_prompt.index("</source_text>")
        assert "55-year-old" in req.user_prompt[start:end]

    def test_response_model_is_extraction(self):
        req = build_narrative_demographics_request("any")
        assert req.response_model is NarrativeDemographics

    def test_empty_narrative_uses_fallback_prompt(self):
        req = build_narrative_demographics_request("")
        assert "missing" in req.user_prompt.lower()

    def test_whitespace_only_uses_fallback_prompt(self):
        req = build_narrative_demographics_request("   ")
        assert "missing" in req.user_prompt.lower()

    def test_none_uses_fallback_prompt(self):
        req = build_narrative_demographics_request(None)
        assert "missing" in req.user_prompt.lower()

    def test_default_temperature_is_zero(self):
        req = build_narrative_demographics_request("N")
        assert req.temperature == 0.0

    def test_system_prompt_mentions_extraction(self):
        req = build_narrative_demographics_request("N")
        assert "extract" in req.system_prompt.lower()

    def test_system_prompt_excludes_family_members(self):
        req = build_narrative_demographics_request("N")
        sp = req.system_prompt.lower()
        assert "family" in sp or "relatives" in sp

    def test_system_prompt_requires_evidence(self):
        req = build_narrative_demographics_request("N")
        assert "evidence" in req.system_prompt.lower()

    def test_system_prompt_forbids_imputation_from_descriptors(self):
        req = build_narrative_demographics_request("N")
        sp = req.system_prompt.lower()
        assert "never impute" in sp or "do not impute" in sp
        assert any(
            descriptor in sp
            for descriptor in ("adult", "elderly", "child", "infant")
        )

    def test_system_prompt_requires_explicit_numeric_age(self):
        req = build_narrative_demographics_request("N")
        sp = req.system_prompt.lower()
        assert "explicit numeric value" in sp or "explicit number" in sp


# ==================================================================
# extract_narrative_demographics — short-circuit
# ==================================================================

class TestShortCircuit:

    def test_empty_narrative_skips_llm(self, fake_client):
        result = extract_narrative_demographics(fake_client, "R001", "")
        assert result == NarrativeDemographics()
        assert fake_client.requests == []

    def test_whitespace_narrative_skips_llm(self, fake_client):
        result = extract_narrative_demographics(fake_client, "R001", "   \n ")
        assert result == NarrativeDemographics()
        assert fake_client.requests == []

    def test_none_narrative_skips_llm(self, fake_client):
        result = extract_narrative_demographics(fake_client, "R001", None)
        assert result == NarrativeDemographics()
        assert fake_client.requests == []


# ==================================================================
# extract_narrative_demographics — happy path
# ==================================================================

class TestExtractHappyPath:

    def test_returns_parsed_extraction(self, fake_client):
        seeded = NarrativeDemographics(
            ages=[NarrativeAgeValue(value=55, unit="years", evidence="55-year-old")],
            sexes=[NarrativeSexValue(value=NarrativeSex.MALE, evidence="male")],
            weights=[
                NarrativeMeasurementValue(value=82, unit="kg", evidence="82 kg"),
            ],
            heights=[
                NarrativeMeasurementValue(value=178, unit="cm", evidence="178 cm"),
            ],
        )
        fake_client.seed_response(seeded)
        result = extract_narrative_demographics(
            fake_client, "R001", "A 55-year-old male, 82 kg, 178 cm."
        )
        assert result == seeded
        assert len(fake_client.requests) == 1

    def test_accepts_dict_payload(self, fake_client):
        fake_client.seed_response({
            "ages": [{"value": 42, "unit": "years", "evidence": "42 yo"}],
            "sexes": [{"value": "female", "evidence": "female"}],
            "weights": [],
            "heights": [],
            "notes": "",
        })
        result = extract_narrative_demographics(fake_client, "R001", "narrative")
        assert result.ages[0].value == 42
        assert result.sexes[0].value is NarrativeSex.FEMALE

    def test_multiple_weights_preserved(self, fake_client):
        seeded = NarrativeDemographics(
            weights=[
                NarrativeMeasurementValue(
                    value=82, unit="kg", evidence="weight on admission: 82 kg"
                ),
                NarrativeMeasurementValue(
                    value=79.5, unit="kg", evidence="on discharge: 79.5 kg"
                ),
            ],
        )
        fake_client.seed_response(seeded)
        result = extract_narrative_demographics(fake_client, "R001", "narrative")
        assert [w.value for w in result.weights] == [82, 79.5]


# ==================================================================
# extract_narrative_demographics — failure paths
# ==================================================================

class TestExtractFailures:

    def test_raw_only_response_raises(self, fake_client):
        fake_client.seed_raw("not valid JSON")
        with pytest.raises(ValueError, match="did not return structured output"):
            extract_narrative_demographics(fake_client, "R001", "narrative")

    def test_unexpected_parsed_type_raises(self, fake_client):
        fake_client.seed_response(parsed=["not", "valid"])
        with pytest.raises(ValueError, match="unexpected parsed type"):
            extract_narrative_demographics(fake_client, "R001", "narrative")
