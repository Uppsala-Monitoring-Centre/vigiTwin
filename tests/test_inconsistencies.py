"""Tests for inconsistency detection.

Drug-date inconsistencies are tested in ``test_clinical_date_inconsistencies.py``.
"""

import pytest

from llm_dup_dev.classifier.config import InconsistencyThresholds
from llm_dup_dev.models.enums import InconsistencySeverity
from llm_dup_dev.models.enums import DrugRole
from llm_dup_dev.models.report import (
    AgeValue,
    Drug,
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
    def test_minor_inconsistency_small_diff(self, thresholds):
        """Difference below the moderate threshold (< 2 years) → minor."""
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
        age_incs = [i for i in result if i.feature == "patient_age"]
        assert len(age_incs) == 1
        assert age_incs[0].severity == InconsistencySeverity.MINOR

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
        # 10-year gap — above the strong threshold (5y) but just under
        # the decisive threshold (11y).
        r1 = Report(
            report_id="1",
            patient_age_values=[
                AgeValue(value=50, unit="years", sources=["structured"])
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

    def test_age_diff_at_decisive_boundary(self, thresholds):
        """Exactly at the 11-year decisive threshold → decisive."""
        r1 = Report(
            report_id="1",
            patient_age_values=[
                AgeValue(value=50, unit="years", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            patient_age_values=[
                AgeValue(value=61, unit="years", sources=["structured"])
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        age_incs = [i for i in result if i.feature == "patient_age"]
        assert len(age_incs) == 1
        assert age_incs[0].severity == InconsistencySeverity.DECISIVE

    def test_age_diff_well_above_decisive(self, thresholds):
        """Large gap (30 years) is decisive, not strong."""
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
        assert age_incs[0].severity == InconsistencySeverity.DECISIVE

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
        # Minimum diff is |55-54| = 1, below moderate threshold → minor
        result = detect_inconsistencies(r1, r2, thresholds)
        age_incs = [i for i in result if i.feature == "patient_age"]
        assert len(age_incs) == 1
        assert age_incs[0].severity == InconsistencySeverity.MINOR

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
        """Exactly at the moderate threshold (2 years) → moderate."""
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

    def test_within_completed_year_is_not_inconsistent(self, thresholds):
        """74.6 y and 74 y are the same completed age → no inconsistency
        (both floor to 74), consistent with them now matching."""
        r1 = Report(
            report_id="1",
            patient_age_values=[
                AgeValue(value=74.6, unit="years", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            patient_age_values=[
                AgeValue(value=74, unit="years", sources=["structured"])
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        assert [i for i in result if i.feature == "patient_age"] == []

    def test_completed_age_uses_floored_difference(self, thresholds):
        """74.9 y vs 76.0 y → completed ages 74 and 76 → 2-year difference →
        moderate (the fractional parts are dropped, not rounded)."""
        r1 = Report(
            report_id="1",
            patient_age_values=[
                AgeValue(value=74.9, unit="years", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            patient_age_values=[
                AgeValue(value=76.0, unit="years", sources=["structured"])
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        age_incs = [i for i in result if i.feature == "patient_age"]
        assert len(age_incs) == 1
        assert age_incs[0].severity == InconsistencySeverity.MODERATE
        assert age_incs[0].difference == 2.0

    def test_fractional_months_floor_in_recorded_unit(self, thresholds):
        """Fractional infant months floor in months, not years: 6.8 mo and
        6.1 mo are both 'completed 6 months' → no inconsistency, while a
        naive floor-in-years would zero out every infant comparison."""
        r1 = Report(
            report_id="1",
            patient_age_values=[
                AgeValue(value=6.8, unit="months", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            patient_age_values=[
                AgeValue(value=6.1, unit="months", sources=["structured"])
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        assert [i for i in result if i.feature == "patient_age"] == []


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


# ---- Country of origin ----


class TestCountryInconsistency:
    def test_country_mismatch_is_minor(self, thresholds):
        r1 = Report(
            report_id="1",
            country_of_origin_values=[
                SourcedValue(value="US", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            country_of_origin_values=[
                SourcedValue(value="DE", sources=["structured"])
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        country_incs = [i for i in result if i.feature == "patient_country"]
        assert len(country_incs) == 1
        assert country_incs[0].severity == InconsistencySeverity.MINOR

    def test_country_match(self, thresholds):
        r1 = Report(
            report_id="1",
            country_of_origin_values=[
                SourcedValue(value="US", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            country_of_origin_values=[
                SourcedValue(value="us", sources=["structured"])
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        country_incs = [i for i in result if i.feature == "patient_country"]
        assert len(country_incs) == 0

    def test_country_overlap_no_inconsistency(self, thresholds):
        r1 = Report(
            report_id="1",
            country_of_origin_values=[
                SourcedValue(value="US", sources=["structured"]),
                SourcedValue(value="DE", sources=["narrative"]),
            ],
        )
        r2 = Report(
            report_id="2",
            country_of_origin_values=[
                SourcedValue(value="DE", sources=["structured"])
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        country_incs = [i for i in result if i.feature == "patient_country"]
        assert len(country_incs) == 0

    def test_country_missing_one(self, thresholds):
        r1 = Report(
            report_id="1",
            country_of_origin_values=[
                SourcedValue(value="US", sources=["structured"])
            ],
        )
        r2 = Report(report_id="2")
        result = detect_inconsistencies(r1, r2, thresholds)
        country_incs = [i for i in result if i.feature == "patient_country"]
        assert len(country_incs) == 0


# ---- Weight ----


class TestWeightInconsistency:
    def test_sub_one_kg_diff_no_inconsistency(self, thresholds):
        """Differences below the 1 kg minor floor are treated as rounding
        noise and produce no inconsistency."""
        r1 = Report(
            report_id="1",
            patient_weight_values=[
                MeasurementValue(value=79.0, unit="kg", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            patient_weight_values=[
                MeasurementValue(value=79.5, unit="kg", sources=["structured"])
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        assert [i for i in result if i.feature == "patient_weight"] == []

    def test_one_kg_diff_minor(self, thresholds):
        """At exactly the 1 kg floor a minor inconsistency is emitted."""
        r1 = Report(
            report_id="1",
            patient_weight_values=[
                MeasurementValue(value=70.0, unit="kg", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            patient_weight_values=[
                MeasurementValue(value=71.0, unit="kg", sources=["structured"])
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        weight_incs = [i for i in result if i.feature == "patient_weight"]
        assert len(weight_incs) == 1
        assert weight_incs[0].severity == InconsistencySeverity.MINOR

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
    def test_sub_one_cm_diff_no_inconsistency(self, thresholds):
        """Differences below the 1 cm minor floor are treated as
        recording-precision noise and produce no inconsistency."""
        r1 = Report(
            report_id="1",
            patient_height_values=[
                MeasurementValue(value=170.0, unit="cm", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            patient_height_values=[
                MeasurementValue(value=170.5, unit="cm", sources=["structured"])
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        assert [i for i in result if i.feature == "patient_height"] == []

    def test_one_cm_diff_minor(self, thresholds):
        """At exactly the 1 cm floor a minor inconsistency is emitted."""
        r1 = Report(
            report_id="1",
            patient_height_values=[
                MeasurementValue(value=170.0, unit="cm", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            patient_height_values=[
                MeasurementValue(value=171.0, unit="cm", sources=["structured"])
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        height_incs = [i for i in result if i.feature == "patient_height"]
        assert len(height_incs) == 1
        assert height_incs[0].severity == InconsistencySeverity.MINOR

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
                MeasurementValue(value=174, unit="cm", sources=["structured"])
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


# ---- Drug set ----


class TestDrugSetInconsistency:
    """Directional drug-set inconsistency: 0–2 moderate inconsistencies."""

    def test_same_drugs_different_roles_no_inconsistency(self, thresholds):
        """Case 1: A(suspected),B(concomitant) vs A(concomitant),B(suspected) → 0."""
        r1 = Report(
            report_id="1",
            drugs=[
                Drug(substance="A", sources=["s"], role=DrugRole.SUSPECTED),
                Drug(substance="B", sources=["s"], role=DrugRole.CONCOMITANT),
            ],
        )
        r2 = Report(
            report_id="2",
            drugs=[
                Drug(substance="A", sources=["s"], role=DrugRole.CONCOMITANT),
                Drug(substance="B", sources=["s"], role=DrugRole.SUSPECTED),
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        drug_incs = [i for i in result if i.feature == "drug_set"]
        assert len(drug_incs) == 0

    def test_one_direction_missing_key_drug(self, thresholds):
        """Case 2: A(suspected),B(concomitant) vs B(concomitant) → 1."""
        r1 = Report(
            report_id="1",
            drugs=[
                Drug(substance="A", sources=["s"], role=DrugRole.SUSPECTED),
                Drug(substance="B", sources=["s"], role=DrugRole.CONCOMITANT),
            ],
        )
        r2 = Report(
            report_id="2",
            drugs=[
                Drug(substance="B", sources=["s"], role=DrugRole.CONCOMITANT),
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        drug_incs = [i for i in result if i.feature == "drug_set"]
        assert len(drug_incs) == 1
        assert drug_incs[0].severity == InconsistencySeverity.MODERATE

    def test_bidirectional_mismatch(self, thresholds):
        """Case 3: A(suspected) vs B(suspected) → 2."""
        r1 = Report(
            report_id="1",
            drugs=[Drug(substance="A", sources=["s"], role=DrugRole.SUSPECTED)],
        )
        r2 = Report(
            report_id="2",
            drugs=[Drug(substance="B", sources=["s"], role=DrugRole.SUSPECTED)],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        drug_incs = [i for i in result if i.feature == "drug_set"]
        assert len(drug_incs) == 2
        assert all(i.severity == InconsistencySeverity.MODERATE for i in drug_incs)

    def test_extra_concomitant_no_inconsistency(self, thresholds):
        """Case 4: A(suspected) vs A(suspected),B(concomitant),C(concomitant) → 0."""
        r1 = Report(
            report_id="1",
            drugs=[Drug(substance="A", sources=["s"], role=DrugRole.SUSPECTED)],
        )
        r2 = Report(
            report_id="2",
            drugs=[
                Drug(substance="A", sources=["s"], role=DrugRole.SUSPECTED),
                Drug(substance="B", sources=["s"], role=DrugRole.CONCOMITANT),
                Drug(substance="C", sources=["s"], role=DrugRole.CONCOMITANT),
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        drug_incs = [i for i in result if i.feature == "drug_set"]
        assert len(drug_incs) == 0

    def test_case_insensitive_matching(self, thresholds):
        """Substance name matching is case-insensitive."""
        r1 = Report(
            report_id="1",
            drugs=[Drug(substance="Pregabalin", sources=["s"], role=DrugRole.SUSPECTED)],
        )
        r2 = Report(
            report_id="2",
            drugs=[Drug(substance="pregabalin", sources=["s"], role=DrugRole.CONCOMITANT)],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        drug_incs = [i for i in result if i.feature == "drug_set"]
        assert len(drug_incs) == 0

    def test_no_roles_set_no_inconsistency(self, thresholds):
        """If no drugs have roles (all None), no drug-set inconsistencies."""
        r1 = Report(
            report_id="1",
            drugs=[Drug(substance="A", sources=["s"])],
        )
        r2 = Report(
            report_id="2",
            drugs=[Drug(substance="B", sources=["s"])],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        drug_incs = [i for i in result if i.feature == "drug_set"]
        assert len(drug_incs) == 0

    def test_multiple_missing_key_drugs_one_inconsistency_per_direction(self, thresholds):
        """Multiple missing key drugs in same direction = still 1 inconsistency."""
        r1 = Report(
            report_id="1",
            drugs=[
                Drug(substance="A", sources=["s"], role=DrugRole.SUSPECTED),
                Drug(substance="B", sources=["s"], role=DrugRole.INTERACTING),
            ],
        )
        r2 = Report(
            report_id="2",
            drugs=[
                Drug(substance="C", sources=["s"], role=DrugRole.SUSPECTED),
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        drug_incs = [i for i in result if i.feature == "drug_set"]
        # r1→r2: A and B both missing from r2 → 1 inconsistency
        # r2→r1: C missing from r1 → 1 inconsistency
        assert len(drug_incs) == 2

    def test_interacting_is_key(self, thresholds):
        """Interacting drugs count as key drugs."""
        r1 = Report(
            report_id="1",
            drugs=[Drug(substance="A", sources=["s"], role=DrugRole.INTERACTING)],
        )
        r2 = Report(report_id="2", drugs=[])
        result = detect_inconsistencies(r1, r2, thresholds)
        drug_incs = [i for i in result if i.feature == "drug_set"]
        assert len(drug_incs) == 1
        assert drug_incs[0].severity == InconsistencySeverity.MODERATE

    def test_empty_drug_lists(self, thresholds):
        """Empty drug lists produce no inconsistencies."""
        r1 = Report(report_id="1")
        r2 = Report(report_id="2")
        result = detect_inconsistencies(r1, r2, thresholds)
        drug_incs = [i for i in result if i.feature == "drug_set"]
        assert len(drug_incs) == 0

    def test_one_sided_roles(self, thresholds):
        """Only one report has role info; directional check is one-sided."""
        r1 = Report(
            report_id="1",
            drugs=[Drug(substance="A", sources=["s"], role=DrugRole.SUSPECTED)],
        )
        r2 = Report(
            report_id="2",
            drugs=[Drug(substance="B", sources=["s"])],  # role=None
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        drug_incs = [i for i in result if i.feature == "drug_set"]
        # r1→r2: A (key) missing from r2 → 1
        # r2→r1: no key drugs in r2 → 0
        assert len(drug_incs) == 1

    def test_key_drug_present_as_concomitant_in_other(self, thresholds):
        """A drug present in both reports with different roles is NOT inconsistent."""
        r1 = Report(
            report_id="1",
            drugs=[Drug(substance="A", sources=["s"], role=DrugRole.SUSPECTED)],
        )
        r2 = Report(
            report_id="2",
            drugs=[Drug(substance="A", sources=["s"], role=DrugRole.CONCOMITANT)],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        drug_incs = [i for i in result if i.feature == "drug_set"]
        assert len(drug_incs) == 0


class TestCombinationProductOverride:
    """Combination products coded as one drug on one report and as separate
    ingredients on the other must not trigger drug-set inconsistencies."""

    def test_combination_vs_separate_ingredients_no_inconsistency(self, thresholds):
        """Combination key drug vs both ingredients as separate drugs → 0."""
        r1 = Report(
            report_id="1",
            drugs=[
                Drug(
                    substance="valsartan;amlodipine",
                    sources=["s"],
                    role=DrugRole.SUSPECTED,
                ),
            ],
        )
        r2 = Report(
            report_id="2",
            drugs=[
                Drug(substance="valsartan", sources=["s"], role=DrugRole.SUSPECTED),
                Drug(substance="amlodipine", sources=["s"], role=DrugRole.SUSPECTED),
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        drug_incs = [i for i in result if i.feature == "drug_set"]
        assert len(drug_incs) == 0

    def test_separate_ingredients_vs_combination_no_inconsistency(self, thresholds):
        """Same as above with the reports swapped → 0."""
        r1 = Report(
            report_id="1",
            drugs=[
                Drug(substance="valsartan", sources=["s"], role=DrugRole.SUSPECTED),
                Drug(substance="amlodipine", sources=["s"], role=DrugRole.SUSPECTED),
            ],
        )
        r2 = Report(
            report_id="2",
            drugs=[
                Drug(
                    substance="valsartan;amlodipine",
                    sources=["s"],
                    role=DrugRole.SUSPECTED,
                ),
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        drug_incs = [i for i in result if i.feature == "drug_set"]
        assert len(drug_incs) == 0

    def test_ingredient_covered_by_concomitant_combination(self, thresholds):
        """Key ingredient covered by a combination product on the other side,
        even when the combination is concomitant → 0."""
        r1 = Report(
            report_id="1",
            drugs=[Drug(substance="valsartan", sources=["s"], role=DrugRole.SUSPECTED)],
        )
        r2 = Report(
            report_id="2",
            drugs=[
                Drug(
                    substance="valsartan;amlodipine",
                    sources=["s"],
                    role=DrugRole.CONCOMITANT,
                ),
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        drug_incs = [i for i in result if i.feature == "drug_set"]
        assert len(drug_incs) == 0

    def test_combination_with_unreported_ingredient_is_inconsistent(self, thresholds):
        """Combination key drug vs only one of its ingredients → 1 (that direction)."""
        r1 = Report(
            report_id="1",
            drugs=[
                Drug(
                    substance="valsartan;amlodipine",
                    sources=["s"],
                    role=DrugRole.SUSPECTED,
                ),
            ],
        )
        r2 = Report(
            report_id="2",
            drugs=[Drug(substance="valsartan", sources=["s"], role=DrugRole.SUSPECTED)],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        drug_incs = [i for i in result if i.feature == "drug_set"]
        # r1→r2: amlodipine component unreported in r2 → 1
        # r2→r1: valsartan covered by the combination → 0
        assert len(drug_incs) == 1
        assert drug_incs[0].severity == InconsistencySeverity.MODERATE
        assert "valsartan;amlodipine" in drug_incs[0].description

    def test_substring_names_remain_inconsistent(self, thresholds):
        """Component matching is exact, not substring: distinct substances whose
        names contain each other still mismatch bidirectionally."""
        r1 = Report(
            report_id="1",
            drugs=[
                Drug(substance="prednisolone", sources=["s"], role=DrugRole.SUSPECTED),
            ],
        )
        r2 = Report(
            report_id="2",
            drugs=[
                Drug(
                    substance="methylprednisolone",
                    sources=["s"],
                    role=DrugRole.SUSPECTED,
                ),
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        drug_incs = [i for i in result if i.feature == "drug_set"]
        assert len(drug_incs) == 2

    def test_whitespace_and_case_around_semicolon(self, thresholds):
        """Components are stripped and lowercased before comparison."""
        r1 = Report(
            report_id="1",
            drugs=[
                Drug(
                    substance="Valsartan; Amlodipine",
                    sources=["s"],
                    role=DrugRole.SUSPECTED,
                ),
            ],
        )
        r2 = Report(
            report_id="2",
            drugs=[
                Drug(substance="valsartan", sources=["s"], role=DrugRole.CONCOMITANT),
                Drug(substance="AMLODIPINE", sources=["s"], role=DrugRole.CONCOMITANT),
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        drug_incs = [i for i in result if i.feature == "drug_set"]
        assert len(drug_incs) == 0
