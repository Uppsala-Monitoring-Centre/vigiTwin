"""Tests for LLM task modules."""

import pytest

from llm_dup_dev.llm import LLMRequest, LLMResponse
from llm_dup_dev.llm.fake import FakeLLMClient
from llm_dup_dev.llm.tasks.narrative_comparison import (
    NarrativeVerdict,
    build_narrative_request,
    compare_narratives,
)
from llm_dup_dev.models.enums import NarrativeConsistency


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def fake_client():
    return FakeLLMClient()


# ===================================================================
# build_narrative_request
# ===================================================================

class TestBuildRequest:

    def test_includes_both_narratives(self):
        req = build_narrative_request("Narrative A.", "Narrative B.")
        assert "Narrative A." in req.user_prompt
        assert "Narrative B." in req.user_prompt

    def test_response_model_is_verdict(self):
        req = build_narrative_request("A", "B")
        assert req.response_model is NarrativeVerdict

    def test_missing_narrative_uses_fallback_prompt(self):
        req = build_narrative_request(None, "Some text")
        assert "missing" in req.user_prompt.lower()

    def test_empty_narrative_uses_fallback_prompt(self):
        req = build_narrative_request("", "Some text")
        assert "missing" in req.user_prompt.lower()

    def test_whitespace_only_uses_fallback_prompt(self):
        req = build_narrative_request("   ", "Some text")
        assert "missing" in req.user_prompt.lower()

    def test_both_none_uses_fallback_prompt(self):
        req = build_narrative_request(None, None)
        assert "missing" in req.user_prompt.lower()

    def test_default_temperature_is_omitted(self):
        req = build_narrative_request("A", "B")
        assert req.temperature is None

    def test_custom_temperature(self):
        req = build_narrative_request("A", "B", temperature=0.3)
        assert req.temperature == 0.3

    def test_system_prompt_mentions_pharmacovigilance(self):
        req = build_narrative_request("A", "B")
        assert "pharmacovigilance" in req.system_prompt.lower()


# ===================================================================
# compare_narratives — happy path
# ===================================================================

class TestCompareNarratives:

    def test_returns_verdict(self, fake_client):
        verdict = NarrativeVerdict(
            consistency=NarrativeConsistency.STRONGLY_CONSISTENT,
            evidence=["Both describe 54yo female with pregabalin"],
            reasoning="Demographics and drug regimen match closely.",
        )
        fake_client.seed_response(verdict)

        result = compare_narratives(
            fake_client,
            pair_id="R001__R002",
            narrative_1="54 year old female taking pregabalin...",
            narrative_2="54 yo woman on pregabalin...",
        )

        assert result.consistency == NarrativeConsistency.STRONGLY_CONSISTENT
        assert len(result.evidence) == 1

    def test_request_sent_to_client(self, fake_client):
        fake_client.seed_response(NarrativeVerdict(
            consistency=NarrativeConsistency.NEUTRAL,
        ))
        compare_narratives(
            fake_client, "P1", "Narrative A", "Narrative B",
        )
        assert len(fake_client.requests) == 1
        assert "Narrative A" in fake_client.requests[0].user_prompt

    def test_handles_dict_parsed_output(self, fake_client):
        """Some backends return dicts instead of model instances."""
        fake_client._queue.append(LLMResponse(
            parsed={
                "consistency": "consistent",
                "evidence": ["age matches"],
                "reasoning": "ok",
            },
            model="fake-model",
        ))

        result = compare_narratives(
            fake_client, "P1", "A", "B",
        )
        assert result.consistency == NarrativeConsistency.CONSISTENT
        assert isinstance(result, NarrativeVerdict)


# ===================================================================
# compare_narratives — failure paths
# ===================================================================

class TestCompareNarrativesFailures:

    def test_raises_on_no_parsed_output(self, fake_client):
        fake_client.seed_raw("I cannot parse this")

        with pytest.raises(ValueError, match="did not return structured output"):
            compare_narratives(
                fake_client, "P1", "A", "B",
            )

    def test_raises_on_wrong_parsed_type(self, fake_client):
        fake_client._queue.append(LLMResponse(
            parsed=42,  # wrong type
            model="fake-model",
        ))

        with pytest.raises(ValueError, match="unexpected parsed type"):
            compare_narratives(
                fake_client, "P1", "A", "B",
            )

    def test_propagates_client_error(self, fake_client):
        fake_client.seed_error(ConnectionError("network failure"))

        with pytest.raises(ConnectionError, match="network failure"):
            compare_narratives(
                fake_client, "P1", "A", "B",
            )

    def test_error_message_includes_pair_id(self, fake_client):
        fake_client.seed_raw("bad output")

        with pytest.raises(ValueError, match="R001__R002"):
            compare_narratives(
                fake_client, "R001__R002", "A", "B",
            )


# ===================================================================
# NarrativeVerdict schema
# ===================================================================

class TestNarrativeVerdict:

    def test_roundtrip_json(self):
        v = NarrativeVerdict(
            consistency=NarrativeConsistency.INCONSISTENT,
            evidence=["Different ages", "Different countries"],
            reasoning="Clearly different patients.",
        )
        data = v.model_dump()
        v2 = NarrativeVerdict.model_validate(data)
        assert v2 == v

    def test_defaults(self):
        v = NarrativeVerdict(consistency=NarrativeConsistency.NEUTRAL)
        assert v.evidence == []
        assert v.reasoning == ""

    def test_json_schema_generation(self):
        schema = NarrativeVerdict.model_json_schema()
        assert "consistency" in schema["properties"]
        assert "evidence" in schema["properties"]

    def test_all_consistency_values_accepted(self):
        for level in NarrativeConsistency:
            v = NarrativeVerdict(consistency=level)
            assert v.consistency == level
