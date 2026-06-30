"""Tests for :func:`merge_narrative_demographics`."""

from llm_dup_dev.classifier.config import InconsistencyThresholds
from llm_dup_dev.feature_generation.inconsistencies import detect_inconsistencies
from llm_dup_dev.feature_generation.narrative_demographics import (
    merge_narrative_demographics,
)
from llm_dup_dev.models.narrative_demographics import (
    NarrativeAgeValue,
    NarrativeDemographics,
    NarrativeMeasurementValue,
    NarrativeSex,
    NarrativeSexValue,
)
from llm_dup_dev.models.report import (
    AgeValue,
    MeasurementValue,
    Report,
    SourcedValue,
)


def _make_report(**kwargs) -> Report:
    return Report(report_id="R001", **kwargs)


class TestEmptyExtraction:

    def test_noop_preserves_structured_values(self):
        r = _make_report(
            patient_age_values=[AgeValue(value=55, unit="years", sources=["structured"])],
            patient_sex_values=[SourcedValue(value="male", sources=["structured"])],
        )
        out = merge_narrative_demographics(r, NarrativeDemographics())
        assert out.patient_age_values == r.patient_age_values
        assert out.patient_sex_values == r.patient_sex_values

    def test_noop_on_empty_report(self):
        r = _make_report()
        out = merge_narrative_demographics(r, NarrativeDemographics())
        assert out.patient_age_values == []
        assert out.patient_sex_values == []
        assert out.patient_weight_values == []
        assert out.patient_height_values == []


class TestAppends:

    def test_appends_age(self):
        r = _make_report(
            patient_age_values=[AgeValue(value=55, unit="years", sources=["structured"])],
        )
        ext = NarrativeDemographics(
            ages=[NarrativeAgeValue(value=56, unit="years", evidence="56-year-old")],
        )
        out = merge_narrative_demographics(r, ext)
        assert len(out.patient_age_values) == 2
        assert out.patient_age_values[0].sources == ["structured"]
        assert out.patient_age_values[1].value == 56
        assert out.patient_age_values[1].unit == "years"
        assert out.patient_age_values[1].sources == ["narrative_llm"]

    def test_appends_sex(self):
        r = _make_report(
            patient_sex_values=[SourcedValue(value="male", sources=["structured"])],
        )
        ext = NarrativeDemographics(
            sexes=[NarrativeSexValue(value=NarrativeSex.FEMALE, evidence="female patient")],
        )
        out = merge_narrative_demographics(r, ext)
        assert len(out.patient_sex_values) == 2
        assert out.patient_sex_values[1].value == "female"
        assert out.patient_sex_values[1].sources == ["narrative_llm"]

    def test_appends_weight(self):
        r = _make_report(
            patient_weight_values=[
                MeasurementValue(value=80, unit="kg", sources=["structured"]),
            ],
        )
        ext = NarrativeDemographics(
            weights=[
                NarrativeMeasurementValue(value=82, unit="kg", evidence="82 kg"),
                NarrativeMeasurementValue(value=79.5, unit="kg", evidence="79.5 kg"),
            ],
        )
        out = merge_narrative_demographics(r, ext)
        assert len(out.patient_weight_values) == 3
        assert [w.value for w in out.patient_weight_values] == [80, 82, 79.5]
        assert out.patient_weight_values[1].sources == ["narrative_llm"]
        assert out.patient_weight_values[2].sources == ["narrative_llm"]

    def test_appends_height(self):
        r = _make_report()
        ext = NarrativeDemographics(
            heights=[NarrativeMeasurementValue(value=178, unit="cm", evidence="178 cm")],
        )
        out = merge_narrative_demographics(r, ext)
        assert len(out.patient_height_values) == 1
        assert out.patient_height_values[0].value == 178
        assert out.patient_height_values[0].unit == "cm"
        assert out.patient_height_values[0].sources == ["narrative_llm"]

    def test_appends_no_dedupe_for_identical_value(self):
        r = _make_report(
            patient_age_values=[AgeValue(value=55, unit="years", sources=["structured"])],
        )
        ext = NarrativeDemographics(
            ages=[NarrativeAgeValue(value=55, unit="years", evidence="55-year-old")],
        )
        out = merge_narrative_demographics(r, ext)
        assert len(out.patient_age_values) == 2
        assert out.patient_age_values[0].sources == ["structured"]
        assert out.patient_age_values[1].sources == ["narrative_llm"]


class TestImmutability:

    def test_returns_copy_not_input(self):
        r = _make_report(
            patient_age_values=[AgeValue(value=55, unit="years", sources=["structured"])],
        )
        original_list_id = id(r.patient_age_values)
        ext = NarrativeDemographics(
            ages=[NarrativeAgeValue(value=56, unit="years", evidence="56")],
        )
        out = merge_narrative_demographics(r, ext)
        assert out is not r
        # Input report's list is untouched.
        assert len(r.patient_age_values) == 1
        assert id(r.patient_age_values) == original_list_id


class TestCustomSourceName:

    def test_source_name_kwarg_threads_through(self):
        r = _make_report()
        ext = NarrativeDemographics(
            ages=[NarrativeAgeValue(value=55, evidence="55-year-old")],
        )
        out = merge_narrative_demographics(r, ext, source_name="custom_tag")
        assert out.patient_age_values[0].sources == ["custom_tag"]


class TestGenerousMatchingProperty:
    """End-to-end smoke check of generous structured+narrative matching."""

    def test_structured_vs_narrative_same_age_no_inconsistency(self):
        """r1 has only structured age 55; r2 has only narrative age 55.

        The existing ``_check_age`` uses min-difference across the
        cross-product, so merging a narrative candidate should make
        this pair consistent on age.
        """
        r1 = Report(
            report_id="R001",
            patient_age_values=[AgeValue(value=55, unit="years", sources=["structured"])],
        )
        r2_base = Report(report_id="R002")
        r2 = merge_narrative_demographics(
            r2_base,
            NarrativeDemographics(
                ages=[NarrativeAgeValue(value=55, unit="years", evidence="55 y/o")],
            ),
        )
        incs = detect_inconsistencies(r1, r2, InconsistencyThresholds())
        assert [i for i in incs if i.feature == "age"] == []

    def test_multi_weight_admission_vs_discharge_matches_either(self):
        """r1 weight is 79 kg; r2 narrative has both 82 and 81.

        The alignment must pick the closer counterpart (81 kg → min-diff
        2 kg), which sits above the 1 kg minor floor and below the 3 kg
        moderate threshold, so the weight comparison surfaces a minor
        inconsistency rather than the moderate one the further pairing
        (|79 - 82| = 3 kg) would have produced.
        """
        r1 = Report(
            report_id="R001",
            patient_weight_values=[
                MeasurementValue(value=79, unit="kg", sources=["structured"]),
            ],
        )
        r2 = merge_narrative_demographics(
            Report(report_id="R002"),
            NarrativeDemographics(
                weights=[
                    NarrativeMeasurementValue(value=82, unit="kg", evidence="82 kg"),
                    NarrativeMeasurementValue(value=81, unit="kg", evidence="81 kg"),
                ],
            ),
        )
        incs = detect_inconsistencies(r1, r2, InconsistencyThresholds())
        weight_incs = [i for i in incs if i.feature == "weight"]
        assert all(i.severity.value == "minor" for i in weight_incs)
