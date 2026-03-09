"""Tests for data models — serialization, validation, defaults."""

import json

import pytest

from llm_dup_dev.models import (
    ClassificationResult,
    PrimaryClassification,
    RelationshipClassification,
    Report,
)


class TestReportSerialization:
    def test_roundtrip(self, report_a):
        """Report survives JSON serialization and deserialization."""
        json_str = report_a.model_dump_json()
        parsed = Report.model_validate_json(json_str)
        assert parsed.report_id == report_a.report_id
        assert len(parsed.drugs) == len(report_a.drugs)
        assert parsed.drugs[0].substance == report_a.drugs[0].substance

    def test_empty_report_defaults(self):
        """Missing fields default to empty lists / None."""
        r = Report(report_id="empty")
        assert r.patient_sex_values == []
        assert r.drugs == []
        assert r.adverse_events == []
        assert r.narrative is None

    def test_dict_roundtrip(self, report_b):
        """model_dump / model_validate roundtrip preserves all data."""
        data = report_b.model_dump()
        restored = Report.model_validate(data)
        assert restored == report_b

    def test_json_schema_generation(self):
        """JSON schema can be generated without errors."""
        schema = Report.model_json_schema()
        assert "properties" in schema
        assert "report_id" in schema["properties"]


class TestClassificationResult:
    def test_same_individual_label(self):
        r = ClassificationResult(
            pair_id="A__B",
            primary=PrimaryClassification.SAME_INDIVIDUAL,
        )
        assert r.final_label == "Duplicates"

    def test_different_related_label(self):
        r = ClassificationResult(
            pair_id="A__B",
            primary=PrimaryClassification.DIFFERENT_INDIVIDUALS,
            relationship=RelationshipClassification.RELATED,
        )
        assert r.final_label == "Otherwise_related"

    def test_different_unrelated_label(self):
        r = ClassificationResult(
            pair_id="A__B",
            primary=PrimaryClassification.DIFFERENT_INDIVIDUALS,
            relationship=RelationshipClassification.UNRELATED,
        )
        assert r.final_label == "Unrelated"

    def test_abstain_label(self):
        r = ClassificationResult(
            pair_id="A__B",
            primary=PrimaryClassification.ABSTAIN,
        )
        assert r.final_label == "Abstain"

    def test_different_no_relationship_defaults_to_abstain(self):
        """If primary is Different but relationship is None, label is Abstain."""
        r = ClassificationResult(
            pair_id="A__B",
            primary=PrimaryClassification.DIFFERENT_INDIVIDUALS,
        )
        assert r.final_label == "Abstain"
