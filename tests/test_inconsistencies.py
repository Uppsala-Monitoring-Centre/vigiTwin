"""Tests for inconsistency detection."""

import pytest

from llm_dup_dev.classifier.config import InconsistencyThresholds
from llm_dup_dev.models.enums import InconsistencySeverity
from llm_dup_dev.models.report import (
    AgeValue,
    MeasurementValue,
    Report,
    SourcedValue,
)
from llm_dup_dev.feature_generation.inconsistencies import detect_inconsistencies


@pytest.fixture
def thresholds():
    return InconsistencyThresholds()


# ---- Age ----


class TestAgeInconsistency:
    def test_no_inconsistency_small_diff(self, thresholds):
        r1 = Report(
            report_id="1",
            patient_age_values=[
                AgeValue(value=54, unit="years", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            patient_age_values=[
                AgeValue(value=55, unit="years", sources=["structured"])
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        assert len(result) == 0

    def test_moderate_age_diff(self, thresholds):
        r1 = Report(
            report_id="1",
            patient_age_values=[
                AgeValue(value=50, unit="years", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            patient_age_values=[
                AgeValue(value=54, unit="years", sources=["structured"])
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        age_incs = [i for i in result if i.feature == "patient_age"]
        assert len(age_incs) == 1
        assert age_incs[0].severity == InconsistencySeverity.MODERATE
        assert age_incs[0].difference == 4.0

    def test_strong_age_diff(self, thresholds):
        r1 = Report(
            report_id="1",
            patient_age_values=[
                AgeValue(value=30, unit="years", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            patient_age_values=[
                AgeValue(value=60, unit="years", sources=["structured"])
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        age_incs = [i for i in result if i.feature == "patient_age"]
        assert len(age_incs) == 1
        assert age_incs[0].severity == InconsistencySeverity.STRONG

    def test_multi_valued_uses_closest(self, thresholds):
        """When multiple values exist, the minimum difference is used."""
        r1 = Report(
            report_id="1",
            patient_age_values=[
                AgeValue(value=50, unit="years", sources=["structured"]),
                AgeValue(value=55, unit="years", sources=["narrative_llm"]),
            ],
        )
        r2 = Report(
            report_id="2",
            patient_age_values=[
                AgeValue(value=54, unit="years", sources=["structured"])
            ],
        )
        # Minimum diff is |55-54| = 1, below moderate threshold
        result = detect_inconsistencies(r1, r2, thresholds)
        age_incs = [i for i in result if i.feature == "patient_age"]
        assert len(age_incs) == 0

    def test_missing_age_no_inconsistency(self, thresholds):
        r1 = Report(
            report_id="1",
            patient_age_values=[
                AgeValue(value=50, unit="years", sources=["structured"])
            ],
        )
        r2 = Report(report_id="2")
        result = detect_inconsistencies(r1, r2, thresholds)
        age_incs = [i for i in result if i.feature == "patient_age"]
        assert len(age_incs) == 0

    def test_age_in_months_converted(self, thresholds):
        """Infant age in months should be converted to years."""
        r1 = Report(
            report_id="1",
            patient_age_values=[
                AgeValue(value=6, unit="months", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            patient_age_values=[
                AgeValue(value=5, unit="years", sources=["structured"])
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        age_incs = [i for i in result if i.feature == "patient_age"]
        # 6 months = 0.5 years, diff = 4.5 → moderate
        assert len(age_incs) == 1
        assert age_incs[0].severity == InconsistencySeverity.MODERATE

    def test_exact_boundary_moderate(self, thresholds):
        """Exactly at the moderate threshold (3 years) → moderate."""
        r1 = Report(
            report_id="1",
            patient_age_values=[
                AgeValue(value=50, unit="years", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            patient_age_values=[
                AgeValue(value=53, unit="years", sources=["structured"])
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        age_incs = [i for i in result if i.feature == "patient_age"]
        assert len(age_incs) == 1
        assert age_incs[0].severity == InconsistencySeverity.MODERATE


# ---- Sex ----


class TestSexInconsistency:
    def test_sex_mismatch(self, thresholds):
        r1 = Report(
            report_id="1",
            patient_sex_values=[
                SourcedValue(value="male", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            patient_sex_values=[
                SourcedValue(value="female", sources=["structured"])
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        sex_incs = [i for i in result if i.feature == "patient_sex"]
        assert len(sex_incs) == 1
        assert sex_incs[0].severity == InconsistencySeverity.STRONG

    def test_sex_match(self, thresholds):
        r1 = Report(
            report_id="1",
            patient_sex_values=[
                SourcedValue(value="female", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            patient_sex_values=[
                SourcedValue(value="female", sources=["structured"])
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        sex_incs = [i for i in result if i.feature == "patient_sex"]
        assert len(sex_incs) == 0

    def test_sex_missing_one(self, thresholds):
        r1 = Report(
            report_id="1",
            patient_sex_values=[
                SourcedValue(value="male", sources=["structured"])
            ],
        )
        r2 = Report(report_id="2")
        result = detect_inconsistencies(r1, r2, thresholds)
        sex_incs = [i for i in result if i.feature == "patient_sex"]
        assert len(sex_incs) == 0


# ---- Weight ----


class TestWeightInconsistency:
    def test_moderate_weight_diff(self, thresholds):
        r1 = Report(
            report_id="1",
            patient_weight_values=[
                MeasurementValue(value=70, unit="kg", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            patient_weight_values=[
                MeasurementValue(value=76, unit="kg", sources=["structured"])
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        weight_incs = [i for i in result if i.feature == "patient_weight"]
        assert len(weight_incs) == 1
        assert weight_incs[0].severity == InconsistencySeverity.MODERATE

    def test_strong_weight_diff(self, thresholds):
        r1 = Report(
            report_id="1",
            patient_weight_values=[
                MeasurementValue(value=60, unit="kg", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            patient_weight_values=[
                MeasurementValue(value=80, unit="kg", sources=["structured"])
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        weight_incs = [i for i in result if i.feature == "patient_weight"]
        assert len(weight_incs) == 1
        assert weight_incs[0].severity == InconsistencySeverity.STRONG


# ---- Height ----


class TestHeightInconsistency:
    def test_moderate_height_diff(self, thresholds):
        r1 = Report(
            report_id="1",
            patient_height_values=[
                MeasurementValue(value=170, unit="cm", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            patient_height_values=[
                MeasurementValue(value=177, unit="cm", sources=["structured"])
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        height_incs = [i for i in result if i.feature == "patient_height"]
        assert len(height_incs) == 1
        assert height_incs[0].severity == InconsistencySeverity.MODERATE


# ---- Multiple inconsistencies ----


class TestMultipleInconsistencies:
    def test_strong_sorted_first(self, thresholds):
        """Strong inconsistencies should appear before moderate ones."""
        r1 = Report(
            report_id="1",
            patient_sex_values=[
                SourcedValue(value="male", sources=["structured"])
            ],
            patient_age_values=[
                AgeValue(value=50, unit="years", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            patient_sex_values=[
                SourcedValue(value="female", sources=["structured"])
            ],
            patient_age_values=[
                AgeValue(value=54, unit="years", sources=["structured"])
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        assert len(result) == 2
        assert result[0].severity == InconsistencySeverity.STRONG
        assert result[1].severity == InconsistencySeverity.MODERATE
