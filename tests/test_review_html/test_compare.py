"""Tests for llm_dup_dev.review_html.compare."""

import pytest

from llm_dup_dev.feature_generation.pipeline import generate_pair_features
from llm_dup_dev.feature_generation.probability import FrequencyStats
from llm_dup_dev.models.enums import NarrativeConsistency
from llm_dup_dev.models.report import (
    AdverseEvent,
    AgeValue,
    DateValue,
    Drug,
    MeasurementValue,
    PartialDate,
    Report,
    SourcedValue,
)
from llm_dup_dev.review_html.compare import (
    _anchor_penalty,
    _build_ae_rows,
    _build_drug_rows,
    _compare_age_values,
    _compare_date_values,
    _compare_measurement_values,
    _compare_sourced_values,
    _contrib_neg_class,
    _contrib_pos_class,
    _format_matching_features,
    _format_penalty_summary,
    build_pair_view_model,
)
from llm_dup_dev.review_html.models import (
    ComparisonStatus,
    FeatureContributionVM,
    FeaturesSummaryVM,
    FieldComparison,
    InconsistencyCountsVM,
    PairViewModel,
)


# ===================================================================
# _compare_sourced_values
# ===================================================================


class TestCompareSourcedValues:
    def test_both_empty_returns_none(self):
        assert _compare_sourced_values("Sex", [], []) is None

    def test_match(self):
        left = [SourcedValue(value="female", sources=["s"])]
        right = [SourcedValue(value="Female", sources=["s"])]
        result = _compare_sourced_values("Sex", left, right)
        assert result is not None
        assert result.status == ComparisonStatus.MATCH

    def test_mismatch(self):
        left = [SourcedValue(value="female", sources=["s"])]
        right = [SourcedValue(value="male", sources=["s"])]
        result = _compare_sourced_values("Sex", left, right)
        assert result is not None
        assert result.status == ComparisonStatus.MISMATCH

    def test_missing_left(self):
        right = [SourcedValue(value="female", sources=["s"])]
        result = _compare_sourced_values("Sex", [], right)
        assert result is not None
        assert result.status == ComparisonStatus.MISSING_LEFT

    def test_missing_right(self):
        left = [SourcedValue(value="US", sources=["s"])]
        result = _compare_sourced_values("Country", left, [])
        assert result is not None
        assert result.status == ComparisonStatus.MISSING_RIGHT


# ===================================================================
# _compare_age_values
# ===================================================================


class TestCompareAgeValues:
    def test_both_empty_returns_none(self):
        assert _compare_age_values([], []) is None

    def test_exact_match(self):
        left = [AgeValue(value=54, unit="years", sources=["s"])]
        right = [AgeValue(value=54, unit="years", sources=["s"])]
        result = _compare_age_values(left, right)
        assert result.status == ComparisonStatus.MATCH

    def test_within_tolerance_is_match(self):
        left = [AgeValue(value=54, unit="years", sources=["s"])]
        right = [AgeValue(value=56, unit="years", sources=["s"])]
        result = _compare_age_values(left, right)
        assert result.status == ComparisonStatus.MATCH

    def test_within_partial_tolerance(self):
        left = [AgeValue(value=54, unit="years", sources=["s"])]
        right = [AgeValue(value=59, unit="years", sources=["s"])]
        result = _compare_age_values(left, right)
        assert result.status == ComparisonStatus.PARTIAL_MATCH

    def test_beyond_tolerance_is_mismatch(self):
        left = [AgeValue(value=54, unit="years", sources=["s"])]
        right = [AgeValue(value=28, unit="years", sources=["s"])]
        result = _compare_age_values(left, right)
        assert result.status == ComparisonStatus.MISMATCH

    def test_multi_valued_uses_best_pair(self):
        """If any pair of values is within tolerance, status is match."""
        left = [AgeValue(value=54, unit="years", sources=["s"])]
        right = [
            AgeValue(value=28, unit="years", sources=["s"]),
            AgeValue(value=55, unit="years", sources=["n"]),
        ]
        result = _compare_age_values(left, right)
        assert result.status == ComparisonStatus.MATCH

    def test_missing_left(self):
        right = [AgeValue(value=54, unit="years", sources=["s"])]
        result = _compare_age_values([], right)
        assert result.status == ComparisonStatus.MISSING_LEFT


# ===================================================================
# _compare_measurement_values
# ===================================================================


class TestCompareMeasurementValues:
    def test_both_empty_returns_none(self):
        assert _compare_measurement_values("Height", [], [], 5.0, 10.0) is None

    def test_match_within_tolerance(self):
        left = [MeasurementValue(value=165, unit="cm", sources=["s"])]
        right = [MeasurementValue(value=168, unit="cm", sources=["s"])]
        result = _compare_measurement_values("Height", left, right, 5.0, 10.0)
        assert result.status == ComparisonStatus.MATCH

    def test_partial_match(self):
        left = [MeasurementValue(value=165, unit="cm", sources=["s"])]
        right = [MeasurementValue(value=173, unit="cm", sources=["s"])]
        result = _compare_measurement_values("Height", left, right, 5.0, 10.0)
        assert result.status == ComparisonStatus.PARTIAL_MATCH

    def test_mismatch(self):
        left = [MeasurementValue(value=165, unit="cm", sources=["s"])]
        right = [MeasurementValue(value=180, unit="cm", sources=["s"])]
        result = _compare_measurement_values("Height", left, right, 5.0, 10.0)
        assert result.status == ComparisonStatus.MISMATCH


# ===================================================================
# _compare_date_values
# ===================================================================


class TestCompareDateValues:
    def test_both_empty_returns_none(self):
        assert _compare_date_values("Report date", [], []) is None

    def test_same_day_match(self):
        left = [DateValue(value="2020-06-12", resolution="day", sources=["s"])]
        right = [DateValue(value="2020-06-12", resolution="day", sources=["s"])]
        result = _compare_date_values("Report date", left, right)
        assert result.status == ComparisonStatus.MATCH

    def test_cross_resolution_match(self):
        """Day vs month: truncate to month, should match."""
        left = [DateValue(value="2020-06-12", resolution="day", sources=["s"])]
        right = [DateValue(value="2020-06", resolution="month", sources=["s"])]
        result = _compare_date_values("Report date", left, right)
        assert result.status == ComparisonStatus.MATCH

    def test_mismatch(self):
        left = [DateValue(value="2020-06-12", resolution="day", sources=["s"])]
        right = [DateValue(value="2022-01-10", resolution="day", sources=["s"])]
        result = _compare_date_values("Report date", left, right)
        assert result.status == ComparisonStatus.MISMATCH


# ===================================================================
# _build_drug_rows
# ===================================================================


class TestBuildDrugRows:
    def test_empty_reports(self):
        r1 = Report(report_id="R1")
        r2 = Report(report_id="R2")
        rows = _build_drug_rows(r1, r2)
        assert rows == []

    def test_shared_drug(self):
        r1 = Report(
            report_id="R1",
            drugs=[Drug(substance="pregabalin", sources=["s"])],
        )
        r2 = Report(
            report_id="R2",
            drugs=[Drug(substance="Pregabalin", sources=["s"])],
        )
        rows = _build_drug_rows(r1, r2)
        assert len(rows) == 1
        assert rows[0].status == ComparisonStatus.MATCH
        assert len(rows[0].left_details) == 1
        assert len(rows[0].right_details) == 1

    def test_drug_only_in_left(self):
        r1 = Report(
            report_id="R1",
            drugs=[Drug(substance="aspirin", sources=["s"])],
        )
        r2 = Report(report_id="R2")
        rows = _build_drug_rows(r1, r2)
        assert len(rows) == 1
        assert rows[0].status == ComparisonStatus.MISSING_RIGHT

    def test_drug_only_in_right(self):
        r1 = Report(report_id="R1")
        r2 = Report(
            report_id="R2",
            drugs=[Drug(substance="aspirin", sources=["s"])],
        )
        rows = _build_drug_rows(r1, r2)
        assert len(rows) == 1
        assert rows[0].status == ComparisonStatus.MISSING_LEFT

    def test_partial_match_on_dates(self):
        """Drug present in both reports but with conflicting start dates → PARTIAL_MATCH (yellow)."""
        r1 = Report(
            report_id="R1",
            drugs=[
                Drug(
                    substance="pregabalin",
                    sources=["s"],
                    start_date=PartialDate(value="2020-05-01", resolution="day"),
                )
            ],
        )
        r2 = Report(
            report_id="R2",
            drugs=[
                Drug(
                    substance="pregabalin",
                    sources=["s"],
                    start_date=PartialDate(value="2020-08-01", resolution="day"),
                )
            ],
        )
        rows = _build_drug_rows(r1, r2)
        assert len(rows) == 1
        assert rows[0].status == ComparisonStatus.PARTIAL_MATCH

    def test_match_despite_dose_difference(self):
        """Drug present in both with same start date but different strength → MATCH (green row)."""
        r1 = Report(
            report_id="R1",
            drugs=[Drug(substance="pregabalin", sources=["s"], strength="75 mg")],
        )
        r2 = Report(
            report_id="R2",
            drugs=[Drug(substance="pregabalin", sources=["s"], strength="150 mg")],
        )
        rows = _build_drug_rows(r1, r2)
        assert len(rows) == 1
        assert rows[0].status == ComparisonStatus.MATCH
        # Sub-field should still flag the strength difference
        assert rows[0].left_details[0].subfield_status.strength == ComparisonStatus.MISMATCH


# ===================================================================
# _build_ae_rows
# ===================================================================


class TestBuildAERows:
    def test_empty_reports(self):
        r1 = Report(report_id="R1")
        r2 = Report(report_id="R2")
        rows = _build_ae_rows(r1, r2)
        assert rows == []

    def test_shared_ae(self):
        r1 = Report(
            report_id="R1",
            adverse_events=[AdverseEvent(pt_name="somnolence", sources=["s"])],
        )
        r2 = Report(
            report_id="R2",
            adverse_events=[AdverseEvent(pt_name="Somnolence", sources=["s"])],
        )
        rows = _build_ae_rows(r1, r2)
        assert len(rows) == 1
        assert rows[0].status == ComparisonStatus.MATCH

    def test_union_includes_all(self):
        r1 = Report(
            report_id="R1",
            adverse_events=[
                AdverseEvent(pt_name="somnolence", sources=["s"]),
                AdverseEvent(pt_name="dizziness", sources=["s"]),
            ],
        )
        r2 = Report(
            report_id="R2",
            adverse_events=[
                AdverseEvent(pt_name="somnolence", sources=["s"]),
                AdverseEvent(pt_name="nausea", sources=["s"]),
            ],
        )
        rows = _build_ae_rows(r1, r2)
        assert len(rows) == 3
        pt_names = {r.pt_name.lower() for r in rows}
        assert pt_names == {"somnolence", "dizziness", "nausea"}


# ===================================================================
# build_pair_view_model (integration)
# ===================================================================


class TestBuildPairViewModel:
    def test_near_duplicate_pair(self, report_a, report_b):
        """End-to-end test with the shared near-duplicate fixtures."""
        vm = build_pair_view_model(report_a, report_b, pair_id="R001__R002")

        assert vm.pair_id == "R001__R002"
        assert vm.report_1_id == "R001"
        assert vm.report_2_id == "R002"

        # Demographics should be present (sex, age, country, report_date, height, weight)
        labels = [f.label for f in vm.demographics]
        assert "Sex" in labels
        assert "Age" in labels
        assert "Country" in labels

        # Sex should match
        sex_field = next(f for f in vm.demographics if f.label == "Sex")
        assert sex_field.status == ComparisonStatus.MATCH

        # Weight should show difference (68 vs 70 = 2kg, within 5kg tolerance)
        weight_field = next(f for f in vm.demographics if f.label == "Weight")
        assert weight_field.status == ComparisonStatus.MATCH

        # Drugs: pregabalin + sertraline shared, omeprazole only in R002
        assert len(vm.drugs) == 3
        drug_names = {d.substance.lower() for d in vm.drugs}
        assert drug_names == {"pregabalin", "sertraline", "omeprazole"}

        # AEs: somnolence shared, dizziness only R001, nausea only R002
        assert len(vm.adverse_events) == 3

        # Narratives should be present
        assert vm.narratives.left_text is not None
        assert vm.narratives.right_text is not None

    def test_empty_reports_minimal_output(self, report_empty):
        """Two empty reports produce no demographic rows."""
        r2 = Report(report_id="R_EMPTY2")
        vm = build_pair_view_model(report_empty, r2, pair_id="EMPTY__EMPTY")

        assert vm.demographics == []
        assert vm.drugs == []
        assert vm.adverse_events == []

    def test_clearly_different_pair(self, report_a, report_c):
        """Reports with different demographics should show mismatches."""
        vm = build_pair_view_model(report_a, report_c, pair_id="R001__R003")

        sex_field = next(f for f in vm.demographics if f.label == "Sex")
        assert sex_field.status == ComparisonStatus.MISMATCH

        age_field = next(f for f in vm.demographics if f.label == "Age")
        assert age_field.status == ComparisonStatus.MISMATCH

        country_field = next(f for f in vm.demographics if f.label == "Country")
        assert country_field.status == ComparisonStatus.MISMATCH

    def test_narrative_demographics_merged_into_view_model(self):
        """Narrative-only weight should appear in the demographics table,
        wrapped in <em> tags to indicate narrative provenance."""
        from llm_dup_dev.models.narrative_demographics import (
            NarrativeDemographics,
            NarrativeMeasurementValue,
        )

        # r1 has no structured weight; r2 has a structured weight.
        r1 = Report(
            report_id="R1",
            patient_sex_values=[SourcedValue(value="male", sources=["s"])],
        )
        r2 = Report(
            report_id="R2",
            patient_sex_values=[SourcedValue(value="male", sources=["s"])],
            patient_weight_values=[
                MeasurementValue(value=70.0, unit="kg", sources=["s"])
            ],
        )

        # Without narrative demographics: weight row is missing-left.
        vm_plain = build_pair_view_model(r1, r2, pair_id="R1__R2")
        weight_plain = next(
            (f for f in vm_plain.demographics if f.label == "Weight"), None
        )
        assert weight_plain is not None
        assert weight_plain.status == ComparisonStatus.MISSING_LEFT
        assert "<em>" not in weight_plain.left_display

        # With a narrative-extracted weight on r1: weight row now matches
        # and the narrative-derived value is italicised.
        nd1 = NarrativeDemographics(
            weights=[
                NarrativeMeasurementValue(
                    value=72.0, unit="kg", evidence="weighed 72 kg"
                )
            ]
        )
        vm_merged = build_pair_view_model(
            r1, r2, pair_id="R1__R2", narrative_demographics_1=nd1
        )
        weight_merged = next(
            f for f in vm_merged.demographics if f.label == "Weight"
        )
        assert weight_merged.status == ComparisonStatus.MATCH
        assert "<em>72 kg</em>" in weight_merged.left_display
        # r2's weight came from a structured field — not italicised.
        assert "<em>" not in weight_merged.right_display


# ===================================================================
# Clinical date counts in view models
# ===================================================================


class TestClinicalDateCountsInViewModels:
    """Verify clinical_date_counts is propagated into FeaturesSummaryVM
    and DecisionSummaryVM when PairFeatures are supplied."""

    @pytest.fixture
    def features_ab(self, report_a, report_b):
        stats = FrequencyStats(
            N_total=100_000, N_drugs=48, N_AEs=200, N_drugs_AEs=12
        )
        return generate_pair_features(
            r1=report_a,
            r2=report_b,
            stats=stats,
            narrative_consistency=NarrativeConsistency.NEUTRAL,
        )

    def test_features_summary_has_clinical_date_counts(
        self, report_a, report_b, features_ab
    ):
        """features_summary.clinical_date_counts must be populated."""
        from llm_dup_dev.models.classification import ClassificationResult
        from llm_dup_dev.models.enums import (
            DuplicateRelationship,
            IndependenceAssessment,
        )

        classification = ClassificationResult(
            pair_id="R001__R002",
            independence_assessment=IndependenceAssessment.NON_INDEPENDENT,
            duplicate_relationship=DuplicateRelationship.DUPLICATES,
            explanation="test",
        )
        vm = build_pair_view_model(
            report_a, report_b,
            pair_id="R001__R002",
            features=features_ab,
            classification=classification,
        )

        assert vm.features_summary is not None
        cdc = vm.features_summary.clinical_date_counts
        assert cdc is not None
        assert isinstance(cdc.n_minor, int)
        assert isinstance(cdc.n_moderate, int)
        assert isinstance(cdc.n_strong, int)
        assert isinstance(cdc.n_decisive, int)

    def test_decision_summary_has_clinical_date_counts(
        self, report_a, report_b, features_ab
    ):
        """decision_summary.clinical_date_counts must be populated."""
        from llm_dup_dev.models.classification import ClassificationResult
        from llm_dup_dev.models.enums import (
            DuplicateRelationship,
            IndependenceAssessment,
        )

        classification = ClassificationResult(
            pair_id="R001__R002",
            independence_assessment=IndependenceAssessment.NON_INDEPENDENT,
            duplicate_relationship=DuplicateRelationship.DUPLICATES,
            explanation="test",
        )
        vm = build_pair_view_model(
            report_a, report_b,
            pair_id="R001__R002",
            features=features_ab,
            classification=classification,
        )

        assert vm.decision_summary is not None
        cdc = vm.decision_summary.clinical_date_counts
        assert cdc is not None
        assert isinstance(cdc.n_minor, int)
        assert isinstance(cdc.n_decisive, int)


# ===================================================================
# Decision summary: source_case row + dropped narrative_consistency
# ===================================================================


class TestDecisionSummarySourceCase:
    """The Decision summary must surface source_case_assessment (the
    classifier consumes it via the shared-source override) and must
    no longer surface narrative_consistency (which the classifier
    does not consult)."""

    def _classification(self):
        from llm_dup_dev.models.classification import ClassificationResult
        from llm_dup_dev.models.enums import (
            DuplicateRelationship,
            IndependenceAssessment,
        )
        return ClassificationResult(
            pair_id="R001__R002",
            independence_assessment=IndependenceAssessment.NON_INDEPENDENT,
            duplicate_relationship=DuplicateRelationship.DUPLICATES,
            explanation="test",
        )

    def _features_with_assessment(self, report_a, report_b, assessment):
        stats = FrequencyStats(
            N_total=100_000, N_drugs=48, N_AEs=200, N_drugs_AEs=12
        )
        return generate_pair_features(
            r1=report_a, r2=report_b, stats=stats,
            narrative_consistency=NarrativeConsistency.NEUTRAL,
            source_case_assessment=assessment,
        )

    def test_source_case_populated_when_assessment_present(
        self, report_a, report_b,
    ):
        from llm_dup_dev.models.narrative_source_case import (
            SourceCaseAssessment, SourceCaseVerdict, SourceIdentifierType,
        )
        asm = SourceCaseAssessment(
            verdict=SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT,
            shared_source_identifier="10.abc/def",
            shared_source_identifier_type=SourceIdentifierType.DOI,
            reasoning="Shared DOI and single-patient narrative.",
        )
        features = self._features_with_assessment(report_a, report_b, asm)
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=features, classification=self._classification(),
        )
        sc = vm.decision_summary.source_case
        assert sc is not None
        assert sc.verdict == "same_source_single_patient"
        assert sc.shared_source_identifier == "10.abc/def"
        assert sc.shared_source_identifier_type == "doi"
        assert "single-patient" in sc.reasoning.lower()

    def test_source_case_absent_when_not_assessed(
        self, report_a, report_b,
    ):
        """NOT_ASSESSED suppresses the panel row entirely."""
        from llm_dup_dev.models.narrative_source_case import (
            SourceCaseAssessment, SourceCaseVerdict,
        )
        asm = SourceCaseAssessment(
            verdict=SourceCaseVerdict.NOT_ASSESSED,
            reasoning="One or both extractions missing.",
        )
        features = self._features_with_assessment(report_a, report_b, asm)
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=features, classification=self._classification(),
        )
        assert vm.decision_summary.source_case is None

    def test_no_decisive_signal_still_surfaced(
        self, report_a, report_b,
    ):
        """A comparator pass that found nothing actionable is still useful
        information for reviewers, so the row is shown."""
        from llm_dup_dev.models.narrative_source_case import (
            SourceCaseAssessment, SourceCaseVerdict,
        )
        asm = SourceCaseAssessment(
            verdict=SourceCaseVerdict.NO_DECISIVE_SIGNAL,
            reasoning="No decisive source or case signal from the narratives.",
        )
        features = self._features_with_assessment(report_a, report_b, asm)
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=features, classification=self._classification(),
        )
        sc = vm.decision_summary.source_case
        assert sc is not None
        assert sc.verdict == "no_decisive_signal"
        assert sc.shared_source_identifier is None

    def test_decision_summary_has_no_narrative_consistency_field(
        self, report_a, report_b,
    ):
        """narrative_consistency is unused by the classifier and was
        dropped from the Decision summary view model."""
        features = self._features_with_assessment(report_a, report_b, None)
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=features, classification=self._classification(),
        )
        assert not hasattr(vm.decision_summary, "narrative_consistency")


# ===================================================================
# NarrativeComparison: literature_reference surfacing
# ===================================================================


class TestNarrativeLiteratureReference:
    """The Narratives section now carries the raw literature_reference
    string from each Report alongside the narrative text."""

    def test_literature_reference_propagates_when_present(
        self, report_a, report_b,
    ):
        report_a_with = report_a.model_copy(
            update={"literature_reference": "Author N. Title. Journal. 2020;1(2):3-4."}
        )
        report_b_with = report_b.model_copy(
            update={"literature_reference": "Author N. Title. Journal. 2020;1 (suppl 2):3-4"}
        )
        vm = build_pair_view_model(
            report_a_with, report_b_with, pair_id="R001__R002",
        )
        assert vm.narratives.left_literature_reference == (
            "Author N. Title. Journal. 2020;1(2):3-4."
        )
        assert vm.narratives.right_literature_reference == (
            "Author N. Title. Journal. 2020;1 (suppl 2):3-4"
        )

    def test_literature_reference_none_when_absent(
        self, report_a, report_b,
    ):
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
        )
        assert vm.narratives.left_literature_reference is None
        assert vm.narratives.right_literature_reference is None


# ===================================================================
# _format_penalty_summary
# ===================================================================


class TestFormatPenaltySummary:
    """Transparent summation of the aggregated discrepancy penalty."""

    def test_zone_based_explanation_produces_breakdown(self):
        """A typical zone-based explanation yields 'n × w + ... = total op Y'."""
        counts = InconsistencyCountsVM(minor=3, moderate=1, strong=0)
        explanation = (
            "Strong matching evidence (L_match=3.50 > 3.0); "
            "w_tot=2.2 < 4.0."
        )
        result = _format_penalty_summary(counts, explanation)
        # Default thresholds: w_minor=0.4, w_moderate=1.0, w_strong=3.0
        # 3*0.4 + 1*1.0 = 2.2
        assert result == "3 × 0.4 + 1 × 1.0 = 2.2 < 4.0"

    def test_failing_zone_uses_gte_operator(self):
        """Explanation with 'w_tot=X >= Y' keeps the >= operator."""
        counts = InconsistencyCountsVM(minor=0, moderate=0, strong=2)
        explanation = (
            "Strong matching evidence (L_match=3.50) but "
            "w_tot=6.0 >= 4.0 (strong=2, moderate=0)."
        )
        result = _format_penalty_summary(counts, explanation)
        # 2*3.0 = 6.0
        assert result == "2 × 3.0 = 6.0 >= 4.0"

    def test_decisive_included_in_breakdown(self):
        """Decisive counts now contribute via w_decisive in the breakdown."""
        counts = InconsistencyCountsVM(minor=0, moderate=0, strong=0, decisive=1)
        explanation = (
            "Strong matching evidence (L_match=3.50) but "
            "w_tot=100.0 >= 4.0 (strong=0, moderate=0, decisive=1)."
        )
        # Default w_decisive=100.0 → 1 × 100.0 = 100.0
        result = _format_penalty_summary(counts, explanation)
        assert result == "1 × 100.0 = 100.0 >= 4.0"

    def test_below_limited_uses_zero_threshold(self):
        """Below-limited branch: always Unrelated → show breakdown with '>= 0.0'."""
        explanation = (
            "Matching evidence below limited threshold "
            "(L_match=0.50 ≤ 1.0); independence cannot be rejected."
        )
        # Zero inconsistencies → no terms, just the total comparison.
        result = _format_penalty_summary(
            InconsistencyCountsVM(minor=0, moderate=0, strong=0), explanation
        )
        assert result == "0.0 >= 0.0"
        # Non-zero minor → full breakdown.
        result = _format_penalty_summary(
            InconsistencyCountsVM(minor=2, moderate=0, strong=0), explanation
        )
        assert result == "2 × 0.4 = 0.8 >= 0.0"

    def test_zero_inconsistencies_strong_zone(self):
        """Clean strong-zone pass with zero inconsistencies → no terms, just '0.0 < Y'."""
        counts = InconsistencyCountsVM(minor=0, moderate=0, strong=0)
        explanation = (
            "Strong matching evidence (L_match=4.00 > 3.0); "
            "w_tot=0.0 < 4.0."
        )
        result = _format_penalty_summary(counts, explanation)
        assert result == "0.0 < 4.0"

    def test_missing_counts_returns_none(self):
        """No inconsistency_counts available → None (template falls back to '--')."""
        assert _format_penalty_summary(None, "w_tot=1.0 < 4.0") is None


# ===================================================================
# Per-element L_match contribution chips
# ===================================================================


class TestContribClass:
    """The five magnitude bins for green / red contribution chips."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            (0.0, "contrib-pos-vw"),
            (0.29, "contrib-pos-vw"),
            (0.30, "contrib-pos-w"),
            (0.99, "contrib-pos-w"),
            (1.00, "contrib-pos-m"),
            (1.99, "contrib-pos-m"),
            (2.00, "contrib-pos-s"),
            (3.49, "contrib-pos-s"),
            (3.50, "contrib-pos-vs"),
            (10.0, "contrib-pos-vs"),
        ],
    )
    def test_pos_bins(self, value, expected):
        assert _contrib_pos_class(value) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            (0.0, "contrib-neg-vw"),
            (-0.29, "contrib-neg-vw"),
            (-0.30, "contrib-neg-w"),
            (-0.99, "contrib-neg-w"),
            (-1.00, "contrib-neg-m"),
            (-1.99, "contrib-neg-m"),
            (-2.00, "contrib-neg-s"),
            (-3.49, "contrib-neg-s"),
            (-3.50, "contrib-neg-vs"),
            (-10.0, "contrib-neg-vs"),
        ],
    )
    def test_neg_bins_use_magnitude(self, value, expected):
        assert _contrib_neg_class(value) == expected


class TestAnchorPenalty:
    """Anchor penalty -log10(N*(N-1)/2) is the additive starting point of L_match."""

    @pytest.mark.parametrize("n", [None, 0, 1])
    def test_undefined_below_two(self, n):
        assert _anchor_penalty(n) is None

    def test_n_two_yields_zero(self):
        """N=2 → N_pairs=1 → -log10(1) = 0; rendered chip is '(-0.0)'."""
        assert _anchor_penalty(2) == pytest.approx(0.0)

    def test_n_ten(self):
        # 10 * 9 / 2 = 45 → -log10(45) ≈ -1.65
        assert _anchor_penalty(10) == pytest.approx(-1.6532, abs=1e-3)

    def test_n_thousand(self):
        # 1000 * 999 / 2 ≈ 5e5 → -log10 ≈ -5.70
        assert _anchor_penalty(1000) == pytest.approx(-5.6989, abs=1e-3)


def _make_pair_vm_for_format(
    feature_contributions, matching_drugs=None, matching_aes=None
) -> PairViewModel:
    """Build a PairViewModel with just enough scaffolding to call
    _format_matching_features."""
    fs = FeaturesSummaryVM(
        l_match=0.0,
        expected_collisions=1.0,
        anchor_type="drugs_aes",
        anchor_n=10,
        reporting_context="Country (US, 2020)",
        narrative_consistency="consistent",
        matching_drugs=matching_drugs or [],
        matching_aes=matching_aes or [],
        feature_contributions=feature_contributions,
    )
    demographics = [
        FieldComparison(
            label="Sex", left_display="female", right_display="female",
            status=ComparisonStatus.MATCH,
        ),
        FieldComparison(
            label="Age", left_display="10 years", right_display="10 years",
            status=ComparisonStatus.MATCH,
        ),
    ]
    return PairViewModel(
        pair_id="P", report_1_id="R1", report_2_id="R2",
        demographics=demographics, features_summary=fs,
    )


class TestFormatMatchingFeatures:
    """The Batch Index 'Additional matching features' cell now ranks rows
    by descending log-rarity contribution and shades each one."""

    def test_demographics_sorted_by_contribution_desc(self):
        # patient_age (p_f=0.01 → +2.0) should appear before patient_sex
        # (p_f=0.5 → +0.30) regardless of input order.
        fcs = [
            FeatureContributionVM(
                feature="patient_sex", p_f=0.5, method="domain_size",
            ),
            FeatureContributionVM(
                feature="patient_age", p_f=0.01, method="domain_size",
            ),
        ]
        vm = _make_pair_vm_for_format(fcs)
        html = _format_matching_features(vm, key_drugs=set())
        assert html is not None
        # Age row precedes sex row.
        assert html.index("10 years") < html.index("female")
        # Both chips carry the right magnitude.
        assert "(+2.0)" in html
        assert "(+0.3)" in html
        # And the right colour bins (bin upper bounds are exclusive: 2.0
        # falls into the "strong" bin, 0.3 into the "weak" bin).
        assert "contrib-pos-s" in html
        assert "contrib-pos-w" in html

    def test_clinical_dates_grouped_on_one_row(self):
        """All clinical_date contributions render on a single row, sorted
        within the row by descending contribution."""
        fcs = [
            FeatureContributionVM(
                feature="clinical_date", p_f=1.0 / 30, method="domain_size",
                description="clinical_date 2009-11-16 (day): D_f=30",
            ),
            FeatureContributionVM(
                feature="clinical_date", p_f=1.0 / 365, method="domain_size",
                description="clinical_date 2009-11-18 (day): D_f=365",
            ),
        ]
        vm = _make_pair_vm_for_format(fcs)
        html = _format_matching_features(vm, key_drugs=set())
        assert html is not None
        # Only one row → no <br> separator between the two dates (they
        # share the clinical-date row even after sorting).
        first, second = "2009-11-18", "2009-11-16"
        # The rarer date (D_f=365 → +2.6) outranks the common one (+1.5).
        assert html.index(first) < html.index(second)
        between = html[html.index(first):html.index(second)]
        assert "<br>" not in between

    def test_drug_set_one_chip_for_joined_list(self):
        """Drug-set carries ONE shared contribution covering all matched drugs."""
        fcs = [
            FeatureContributionVM(
                feature="drug_set", p_f=0.001, method="leave_pair_out",
            ),
        ]
        vm = _make_pair_vm_for_format(
            fcs, matching_drugs=["sertraline", "paroxetine"]
        )
        html = _format_matching_features(vm, key_drugs=set())
        assert html is not None
        # Exactly one (+X.X) chip in the cell.
        assert html.count("(+") == 1
        # Both drugs in one chip.
        assert "sertraline, paroxetine" in html
        # -log10(0.001) = 3.0 → moderate / strong boundary; should be the
        # 'strong' bin (contrib-pos-s).
        assert "(+3.0)" in html
        assert "contrib-pos-s" in html

    def test_returns_none_when_no_contributions(self):
        vm = _make_pair_vm_for_format([])
        assert _format_matching_features(vm, key_drugs=set()) is None
