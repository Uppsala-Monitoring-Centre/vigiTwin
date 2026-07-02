"""Tests for the :class:`NarrativeDemographics` model.

Frozen JSON round-trip of
``examples/narrative_demographics_example.json`` is covered by the
canonical ``tests/test_schemas_examples.py``.
"""

import json

import pytest
from pydantic import ValidationError

from llm_dup_dev.models.narrative_demographics import (
    NarrativeAgeValue,
    NarrativeDemographics,
    NarrativeMeasurementValue,
    NarrativeSex,
    NarrativeSexValue,
)


class TestEnum:

    def test_accepts_lowercase_male(self):
        assert NarrativeSex("male") is NarrativeSex.MALE

    def test_rejects_titlecase(self):
        with pytest.raises(ValueError):
            NarrativeSex("Male")

    def test_all_members_are_lowercase(self):
        for member in NarrativeSex:
            assert member.value == member.value.lower()


class TestValueModels:

    def test_age_default_unit_is_years(self):
        v = NarrativeAgeValue(value=55, evidence="55-year-old")
        assert v.unit == "years"

    def test_age_rejects_invalid_unit(self):
        with pytest.raises(ValidationError):
            NarrativeAgeValue(value=55, unit="hours", evidence="x")

    def test_measurement_rejects_invalid_unit(self):
        with pytest.raises(ValidationError):
            NarrativeMeasurementValue(value=70, unit="lb", evidence="x")

    def test_sex_value_uses_enum(self):
        v = NarrativeSexValue(value=NarrativeSex.FEMALE, evidence="female patient")
        assert v.value is NarrativeSex.FEMALE

    def test_sex_value_from_string(self):
        v = NarrativeSexValue(value="female", evidence="female patient")
        assert v.value is NarrativeSex.FEMALE


class TestNarrativeDemographics:

    def test_defaults_are_empty(self):
        d = NarrativeDemographics()
        assert d.ages == []
        assert d.sexes == []
        assert d.weights == []
        assert d.heights == []
        assert d.notes == ""

    def test_json_roundtrip_preserves_all_fields(self):
        original = NarrativeDemographics(
            ages=[NarrativeAgeValue(value=55, unit="years", evidence="55-year-old")],
            sexes=[NarrativeSexValue(value=NarrativeSex.MALE, evidence="male")],
            weights=[
                NarrativeMeasurementValue(value=82, unit="kg", evidence="82 kg"),
                NarrativeMeasurementValue(value=79.5, unit="kg", evidence="79.5 kg"),
            ],
            heights=[
                NarrativeMeasurementValue(value=178, unit="cm", evidence="178 cm"),
            ],
            notes="test",
        )
        roundtrip = NarrativeDemographics.model_validate_json(
            original.model_dump_json()
        )
        assert roundtrip == original

    def test_dict_validation(self):
        data = {
            "ages": [{"value": 12, "unit": "months", "evidence": "12 months"}],
            "sexes": [{"value": "male", "evidence": "a 12-month-old boy"}],
            "weights": [],
            "heights": [],
            "notes": "",
        }
        d = NarrativeDemographics.model_validate(data)
        assert d.ages[0].unit == "months"
        assert d.sexes[0].value is NarrativeSex.MALE

    def test_rejects_unknown_sex_string(self):
        with pytest.raises(ValidationError):
            NarrativeDemographics.model_validate({
                "sexes": [{"value": "m", "evidence": "m"}],
            })

    def test_unknown_sex_sentinel_is_dropped(self):
        """An explicit "sex: unknown" yields no candidate, not a value."""
        d = NarrativeDemographics.model_validate({
            "sexes": [{"value": "unknown", "evidence": "sex: unknown"}],
        })
        assert d.sexes == []

    def test_various_missing_sex_sentinels_are_dropped(self):
        for sentinel in ["unknown", "Not Reported", "unspecified", "N/A", "none"]:
            d = NarrativeDemographics.model_validate({
                "sexes": [{"value": sentinel, "evidence": sentinel}],
            })
            assert d.sexes == [], f"{sentinel!r} should be dropped"

    def test_sentinel_dropped_but_valid_sex_kept(self):
        d = NarrativeDemographics.model_validate({
            "sexes": [
                {"value": "unknown", "evidence": "sex: unknown"},
                {"value": "female", "evidence": "she was admitted"},
            ],
        })
        assert [s.value for s in d.sexes] == [NarrativeSex.FEMALE]

    def test_serializes_to_json_string(self):
        d = NarrativeDemographics(notes="hi")
        assert "hi" in json.loads(d.model_dump_json())["notes"]
