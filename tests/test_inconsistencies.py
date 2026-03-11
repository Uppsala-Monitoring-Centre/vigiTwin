"""Tests for inconsistency detection."""

import pytest

from llm_dup_dev.classifier.config import InconsistencyThresholds
from llm_dup_dev.models.enums import InconsistencySeverity
from llm_dup_dev.models.enums import DrugRole
from llm_dup_dev.models.report import (
    AgeValue,
    Drug,
    MeasurementValue,
    PartialDate,
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


# ---- Drug dates ----


def _drug(substance: str, start: str | None = None, end: str | None = None, role: DrugRole | None = None) -> Drug:
    """Helper: build a Drug with optional PartialDate fields (day resolution)."""
    return Drug(
        substance=substance,
        sources=["s"],
        role=role,
        start_date=PartialDate(value=start, resolution="day") if start else None,
        end_date=PartialDate(value=end, resolution="day") if end else None,
    )


class TestDrugDateInconsistency:
    """Drug date (timing) inconsistency detection."""

    def test_identical_start_stop_dates(self, thresholds):
        """Identical dates → diff = 0 → weight 0 → no inconsistency."""
        r1 = Report(report_id="1", drugs=[_drug("warfarin", start="2023-01-01", end="2023-02-01")])
        r2 = Report(report_id="2", drugs=[_drug("warfarin", start="2023-01-01", end="2023-02-01")])
        result = detect_inconsistencies(r1, r2, thresholds)
        date_incs = [i for i in result if i.feature == "drug_dates"]
        assert len(date_incs) == 0

    def test_small_diff_start_only_no_inconsistency(self, thresholds):
        """Start diff 4 days (weight 0.5), no stop dates → total 0.5 < 1 → 0."""
        r1 = Report(report_id="1", drugs=[_drug("warfarin", start="2023-01-01")])
        r2 = Report(report_id="2", drugs=[_drug("warfarin", start="2023-01-05")])
        result = detect_inconsistencies(r1, r2, thresholds)
        date_incs = [i for i in result if i.feature == "drug_dates"]
        assert len(date_incs) == 0

    def test_small_diff_start_and_stop_moderate(self, thresholds):
        """Start diff 3d (0.5) + stop diff 3d (0.5) = 1.0 → moderate."""
        r1 = Report(report_id="1", drugs=[_drug("warfarin", start="2023-01-01", end="2023-03-01")])
        r2 = Report(report_id="2", drugs=[_drug("warfarin", start="2023-01-04", end="2023-03-04")])
        result = detect_inconsistencies(r1, r2, thresholds)
        date_incs = [i for i in result if i.feature == "drug_dates"]
        assert len(date_incs) == 1
        assert date_incs[0].severity == InconsistencySeverity.MODERATE

    def test_moderate_diff_start_only_moderate(self, thresholds):
        """Start diff 15 days (weight 1.0) → total 1.0 → moderate."""
        r1 = Report(report_id="1", drugs=[_drug("warfarin", start="2023-01-01")])
        r2 = Report(report_id="2", drugs=[_drug("warfarin", start="2023-01-16")])
        result = detect_inconsistencies(r1, r2, thresholds)
        date_incs = [i for i in result if i.feature == "drug_dates"]
        assert len(date_incs) == 1
        assert date_incs[0].severity == InconsistencySeverity.MODERATE

    def test_large_diff_stop_only_strong(self, thresholds):
        """Stop diff 89 days (weight 3.0) → total 3.0 → strong."""
        r1 = Report(report_id="1", drugs=[_drug("warfarin", end="2023-02-01")])
        r2 = Report(report_id="2", drugs=[_drug("warfarin", end="2023-05-01")])
        result = detect_inconsistencies(r1, r2, thresholds)
        date_incs = [i for i in result if i.feature == "drug_dates"]
        assert len(date_incs) == 1
        assert date_incs[0].severity == InconsistencySeverity.STRONG

    def test_partial_date_month_overlaps_day_no_inconsistency(self, thresholds):
        """A month-resolution date overlapping a day-resolution date → diff 0 → no inconsistency."""
        r1 = Report(
            report_id="1",
            drugs=[
                Drug(
                    substance="warfarin",
                    sources=["s"],
                    start_date=PartialDate(value="2023-05", resolution="month"),
                )
            ],
        )
        r2 = Report(
            report_id="2",
            drugs=[
                Drug(
                    substance="warfarin",
                    sources=["s"],
                    start_date=PartialDate(value="2023-05-15", resolution="day"),
                )
            ],
        )
        result = detect_inconsistencies(r1, r2, thresholds)
        date_incs = [i for i in result if i.feature == "drug_dates"]
        assert len(date_incs) == 0

    def test_concomitant_downweighting(self, thresholds):
        """Large stop diff (weight 3.0) × 0.5 concomitant factor = 1.5 → moderate (not strong)."""
        r1 = Report(report_id="1", drugs=[_drug("aspirin", end="2023-02-01", role=DrugRole.CONCOMITANT)])
        r2 = Report(report_id="2", drugs=[_drug("aspirin", end="2023-05-01", role=DrugRole.CONCOMITANT)])
        result = detect_inconsistencies(r1, r2, thresholds)
        date_incs = [i for i in result if i.feature == "drug_dates"]
        assert len(date_incs) == 1
        assert date_incs[0].severity == InconsistencySeverity.MODERATE

    def test_missing_dates_one_side(self, thresholds):
        """Only r1 has a start date; no comparison possible → 0 inconsistencies."""
        r1 = Report(report_id="1", drugs=[_drug("warfarin", start="2023-01-01")])
        r2 = Report(report_id="2", drugs=[_drug("warfarin")])
        result = detect_inconsistencies(r1, r2, thresholds)
        date_incs = [i for i in result if i.feature == "drug_dates"]
        assert len(date_incs) == 0

    def test_multiple_start_dates_uses_minimum_distance(self, thresholds):
        """Multiple start dates: use the pair with minimum distance.

        r1 starts: Jan 1, Jan 10 — r2 start: Jan 5
        Distances: Jan1–Jan5 = 4d, Jan10–Jan5 = 5d → min = 4d → weight 0.5
        Total 0.5 < 1.0 → no inconsistency.
        """
        r1 = Report(
            report_id="1",
            drugs=[
                _drug("warfarin", start="2023-01-01"),
                _drug("warfarin", start="2023-01-10"),
            ],
        )
        r2 = Report(report_id="2", drugs=[_drug("warfarin", start="2023-01-05")])
        result = detect_inconsistencies(r1, r2, thresholds)
        date_incs = [i for i in result if i.feature == "drug_dates"]
        assert len(date_incs) == 0

    def test_score_cap_per_drug(self, thresholds):
        """Start diff > 30d (3.0) + stop diff > 30d (3.0) = 6.0 → capped at 3.0 → strong."""
        r1 = Report(report_id="1", drugs=[_drug("warfarin", start="2023-01-01", end="2023-01-01")])
        r2 = Report(report_id="2", drugs=[_drug("warfarin", start="2023-06-01", end="2023-06-01")])
        result = detect_inconsistencies(r1, r2, thresholds)
        date_incs = [i for i in result if i.feature == "drug_dates"]
        assert len(date_incs) == 1
        assert date_incs[0].severity == InconsistencySeverity.STRONG
        assert date_incs[0].difference == 3.0  # capped

    def test_warfarin_example_from_spec(self, thresholds):
        """Spec example: start 4d (0.5) + stop 89d (3.0) → min(3, 3.5) = 3.0 → strong."""
        r1 = Report(report_id="1", drugs=[_drug("warfarin", start="2023-01-01", end="2023-02-01")])
        r2 = Report(report_id="2", drugs=[_drug("warfarin", start="2023-01-05", end="2023-05-01")])
        result = detect_inconsistencies(r1, r2, thresholds)
        date_incs = [i for i in result if i.feature == "drug_dates"]
        assert len(date_incs) == 1
        assert date_incs[0].severity == InconsistencySeverity.STRONG
        assert date_incs[0].difference == 3.0

    def test_no_dates_on_shared_drugs(self, thresholds):
        """Shared drugs with no dates at all → 0 inconsistencies."""
        r1 = Report(report_id="1", drugs=[Drug(substance="aspirin", sources=["s"])])
        r2 = Report(report_id="2", drugs=[Drug(substance="aspirin", sources=["s"])])
        result = detect_inconsistencies(r1, r2, thresholds)
        date_incs = [i for i in result if i.feature == "drug_dates"]
        assert len(date_incs) == 0
