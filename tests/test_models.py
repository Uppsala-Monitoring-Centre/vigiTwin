"""Tests for data models — serialization, validation, defaults."""

import json

import pytest

from llm_dup_dev.models import (
    ClassificationResult,
    RelationshipAssessment,
    IndependenceAssessment,
    Report,
)
from llm_dup_dev.models.report import PartialDate


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


class TestReportedOverallAeOnsetDate:
    """Tests for the explicit ``reported_overall_ae_onset_date`` field."""

    def test_default_is_none(self):
        """Field defaults to None when not provided (backwards compatible)."""
        r = Report(report_id="R1")
        assert r.reported_overall_ae_onset_date is None

    def test_accepts_partial_date(self):
        """Report accepts a PartialDate for the new field."""
        pd = PartialDate(value="2020-05-15", resolution="day")
        r = Report(report_id="R1", reported_overall_ae_onset_date=pd)
        assert r.reported_overall_ae_onset_date == pd

    @pytest.mark.parametrize(
        "value,resolution",
        [
            ("2020", "year"),
            ("2020-05", "month"),
            ("2020-05-15", "day"),
        ],
    )
    def test_partial_date_resolutions_preserved(self, value, resolution):
        """Year/month/day resolutions round-trip through the model."""
        pd = PartialDate(value=value, resolution=resolution)
        r = Report(report_id="R1", reported_overall_ae_onset_date=pd)
        restored = Report.model_validate_json(r.model_dump_json())
        assert restored.reported_overall_ae_onset_date is not None
        assert restored.reported_overall_ae_onset_date.value == value
        assert restored.reported_overall_ae_onset_date.resolution == resolution

    def test_json_roundtrip_preserves_field(self):
        """JSON serialization round-trips the explicit field."""
        pd = PartialDate(value="2020-05-15", resolution="day")
        r = Report(report_id="R1", reported_overall_ae_onset_date=pd)
        restored = Report.model_validate_json(r.model_dump_json())
        assert restored.reported_overall_ae_onset_date == pd

    def test_dict_roundtrip_preserves_field(self):
        """model_dump / model_validate round-trips the explicit field."""
        pd = PartialDate(value="2020-05", resolution="month")
        r = Report(report_id="R1", reported_overall_ae_onset_date=pd)
        restored = Report.model_validate(r.model_dump())
        assert restored == r

    def test_legacy_json_without_field_loads(self):
        """Older serialized reports without the new field still load."""
        legacy_payload = {
            "report_id": "LEGACY",
            "patient_sex_values": [],
            "drugs": [],
            "adverse_events": [],
        }
        r = Report.model_validate(legacy_payload)
        assert r.reported_overall_ae_onset_date is None


class TestClassificationResult:
    def test_duplicates_label(self):
        r = ClassificationResult(
            pair_id="A__B",
            independence_assessment=IndependenceAssessment.NON_INDEPENDENT,
            relationship_assessment=RelationshipAssessment.SAME_CASE,
        )
        assert r.final_label == "Duplicates"

    def test_otherwise_related_label(self):
        r = ClassificationResult(
            pair_id="A__B",
            independence_assessment=IndependenceAssessment.NON_INDEPENDENT,
            relationship_assessment=RelationshipAssessment.DIFFERENT_CASE,
        )
        assert r.final_label == "Otherwise_related"

    def test_independent_label(self):
        r = ClassificationResult(
            pair_id="A__B",
            independence_assessment=IndependenceAssessment.INDEPENDENT,
        )
        assert r.final_label == "Unrelated"

    def test_abstain_label(self):
        r = ClassificationResult(
            pair_id="A__B",
            independence_assessment=IndependenceAssessment.NON_INDEPENDENT,
            relationship_assessment=RelationshipAssessment.ABSTAIN,
        )
        assert r.final_label == "Abstain"

    def test_non_independent_no_relationship_defaults_to_abstain(self):
        """If non-independent but no relationship_assessment, label is Abstain."""
        r = ClassificationResult(
            pair_id="A__B",
            independence_assessment=IndependenceAssessment.NON_INDEPENDENT,
        )
        assert r.final_label == "Abstain"
