"""Tests for llm_dup_dev.review_html.compare."""

import pytest

from llm_dup_dev.feature_generation.pipeline import generate_pair_features
from llm_dup_dev.feature_generation.probability import FrequencyStats
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
from llm_dup_dev.models.classification import (
    InconsistencyPenalty,
    PenaltyTerm,
)
from llm_dup_dev.models.enums import InconsistencySeverity
from llm_dup_dev.models.pair_features import Inconsistency
from llm_dup_dev.review_html.compare import (
    _anchor_penalty,
    _apply_demographic_severity,
    _build_ae_rows,
    _build_drug_rows,
    _build_screening_vm,
    _compare_age_values,
    _compare_date_values,
    _compare_measurement_values,
    _compare_sourced_values,
    _contrib_neg_class,
    _contrib_pos_class,
    _format_matching_features,
    _format_penalty_summary,
    _format_screening_value,
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
# _apply_demographic_severity
# ===================================================================


class _StubFeatures:
    """Minimal stand-in: _apply_demographic_severity only reads .inconsistencies."""

    def __init__(self, inconsistencies):
        self.inconsistencies = inconsistencies


class TestApplyDemographicSeverity:
    def _weight_row(self, status=ComparisonStatus.MATCH):
        return FieldComparison(
            label="Weight",
            left_display="126 kg",
            right_display="131 kg",
            status=status,
        )

    def test_moderate_weight_overrides_green_match(self):
        """The reported bug: 126 vs 131 kg shows green but analysis says moderate."""
        rows = [self._weight_row(ComparisonStatus.MATCH)]
        features = _StubFeatures(
            [
                Inconsistency(
                    feature="patient_weight",
                    severity=InconsistencySeverity.MODERATE,
                    difference=5.0,
                    units="kg",
                )
            ]
        )
        _apply_demographic_severity(rows, features)
        assert rows[0].severity == "moderate"

    def test_no_inconsistency_leaves_severity_none(self):
        rows = [self._weight_row(ComparisonStatus.MATCH)]
        _apply_demographic_severity(rows, _StubFeatures([]))
        assert rows[0].severity is None

    def test_most_severe_wins_for_same_field(self):
        rows = [self._weight_row(ComparisonStatus.MATCH)]
        features = _StubFeatures(
            [
                Inconsistency(feature="patient_weight", severity=InconsistencySeverity.MINOR),
                Inconsistency(feature="patient_weight", severity=InconsistencySeverity.STRONG),
            ]
        )
        _apply_demographic_severity(rows, features)
        assert rows[0].severity == "strong"

    def test_suppressed_inconsistency_ignored(self):
        rows = [self._weight_row(ComparisonStatus.MATCH)]
        features = _StubFeatures(
            [
                Inconsistency(
                    feature="patient_weight",
                    severity=InconsistencySeverity.MODERATE,
                    suppressed=True,
                )
            ]
        )
        _apply_demographic_severity(rows, features)
        assert rows[0].severity is None

    def test_missing_side_row_not_overridden(self):
        rows = [self._weight_row(ComparisonStatus.MISSING_LEFT)]
        features = _StubFeatures(
            [Inconsistency(feature="patient_weight", severity=InconsistencySeverity.STRONG)]
        )
        _apply_demographic_severity(rows, features)
        assert rows[0].severity is None

    def test_non_demographic_feature_ignored(self):
        rows = [self._weight_row(ComparisonStatus.MATCH)]
        features = _StubFeatures(
            [Inconsistency(feature="drug_set", severity=InconsistencySeverity.STRONG)]
        )
        _apply_demographic_severity(rows, features)
        assert rows[0].severity is None


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
# Narrative evidence snippets and notes
# ===================================================================


def _nd_report(report_id: str) -> Report:
    """Minimal report carrying only a structured sex, so the demographics
    table always has an Age/Weight/Height row available to annotate."""
    return Report(
        report_id=report_id,
        patient_sex_values=[SourcedValue(value="male", sources=["s"])],
    )


class TestNarrativeEvidence:
    """``_attach_narrative_evidence`` surfaces the verbatim snippet behind
    each narrative-derived demographic candidate, per row and per side.

    The merge drops ``evidence``, so it is read straight off the
    ``NarrativeDemographics`` extraction at render time.
    """

    def test_evidence_lands_on_correct_row_and_side(self):
        from llm_dup_dev.models.narrative_demographics import (
            NarrativeAgeValue,
            NarrativeDemographics,
        )

        nd1 = NarrativeDemographics(
            ages=[NarrativeAgeValue(value=54.0, evidence="a 54-year-old man")]
        )
        vm = build_pair_view_model(
            _nd_report("R1"),
            _nd_report("R2"),
            pair_id="R1__R2",
            narrative_demographics_1=nd1,
        )
        age = next(f for f in vm.demographics if f.label == "Age")
        assert age.left_evidence == ["a 54-year-old man"]
        # Nothing was supplied for side 2.
        assert age.right_evidence == []
        # Sex had no narrative candidate, so it stays clean.
        sex = next(f for f in vm.demographics if f.label == "Sex")
        assert sex.left_evidence == []

    def test_multiple_candidates_yield_multiple_snippets(self):
        """The narrative may legitimately state more than one value for a
        field (e.g. weight on admission vs discharge); show every snippet."""
        from llm_dup_dev.models.narrative_demographics import (
            NarrativeDemographics,
            NarrativeMeasurementValue,
        )

        nd1 = NarrativeDemographics(
            weights=[
                NarrativeMeasurementValue(
                    value=72.0, unit="kg", evidence="weighed 72 kg on admission"
                ),
                NarrativeMeasurementValue(
                    value=68.0, unit="kg", evidence="68 kg at discharge"
                ),
            ]
        )
        vm = build_pair_view_model(
            _nd_report("R1"),
            _nd_report("R2"),
            pair_id="R1__R2",
            narrative_demographics_1=nd1,
        )
        weight = next(f for f in vm.demographics if f.label == "Weight")
        assert weight.left_evidence == [
            "weighed 72 kg on admission",
            "68 kg at discharge",
        ]

    def test_both_sides_annotated_independently(self):
        from llm_dup_dev.models.narrative_demographics import (
            NarrativeAgeValue,
            NarrativeDemographics,
        )

        nd1 = NarrativeDemographics(
            ages=[NarrativeAgeValue(value=54.0, evidence="54-year-old")]
        )
        nd2 = NarrativeDemographics(
            ages=[NarrativeAgeValue(value=54.0, evidence="aged 54 years")]
        )
        vm = build_pair_view_model(
            _nd_report("R1"),
            _nd_report("R2"),
            pair_id="R1__R2",
            narrative_demographics_1=nd1,
            narrative_demographics_2=nd2,
        )
        age = next(f for f in vm.demographics if f.label == "Age")
        assert age.left_evidence == ["54-year-old"]
        assert age.right_evidence == ["aged 54 years"]

    def test_no_extraction_leaves_evidence_empty(self):
        """The common case — structured-only reports — must be untouched."""
        vm = build_pair_view_model(
            _nd_report("R1"), _nd_report("R2"), pair_id="R1__R2"
        )
        assert all(f.left_evidence == [] for f in vm.demographics)
        assert all(f.right_evidence == [] for f in vm.demographics)

    def test_blank_evidence_is_skipped(self):
        """A candidate the extractor left unquoted contributes no snippet
        rather than an empty italic line."""
        from llm_dup_dev.models.narrative_demographics import (
            NarrativeAgeValue,
            NarrativeDemographics,
        )

        nd1 = NarrativeDemographics(
            ages=[
                NarrativeAgeValue(value=54.0, evidence=""),
                NarrativeAgeValue(value=55.0, evidence="aged 55"),
            ]
        )
        vm = build_pair_view_model(
            _nd_report("R1"),
            _nd_report("R2"),
            pair_id="R1__R2",
            narrative_demographics_1=nd1,
        )
        age = next(f for f in vm.demographics if f.label == "Age")
        assert age.left_evidence == ["aged 55"]

    def test_notes_surface_on_view_model(self):
        from llm_dup_dev.models.narrative_demographics import (
            NarrativeDemographics,
        )

        nd1 = NarrativeDemographics(notes="Age stated only in the discharge summary.")
        nd2 = NarrativeDemographics(notes="No demographics in narrative.")
        vm = build_pair_view_model(
            _nd_report("R1"),
            _nd_report("R2"),
            pair_id="R1__R2",
            narrative_demographics_1=nd1,
            narrative_demographics_2=nd2,
        )
        assert vm.narrative_notes_1 == "Age stated only in the discharge summary."
        assert vm.narrative_notes_2 == "No demographics in narrative."

    def test_notes_default_empty_without_extraction(self):
        vm = build_pair_view_model(
            _nd_report("R1"), _nd_report("R2"), pair_id="R1__R2"
        )
        assert vm.narrative_notes_1 == ""
        assert vm.narrative_notes_2 == ""


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
        )

    def test_features_summary_has_clinical_date_counts(
        self, report_a, report_b, features_ab
    ):
        """features_summary.clinical_date_counts must be populated."""
        from llm_dup_dev.models.classification import ClassificationResult
        from llm_dup_dev.models.enums import (
            RelationshipAssessment,
            IndependenceAssessment,
        )

        classification = ClassificationResult(
            pair_id="R001__R002",
            independence_assessment=IndependenceAssessment.NON_INDEPENDENT,
            relationship_assessment=RelationshipAssessment.SAME_CASE,
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
            RelationshipAssessment,
            IndependenceAssessment,
        )

        classification = ClassificationResult(
            pair_id="R001__R002",
            independence_assessment=IndependenceAssessment.NON_INDEPENDENT,
            relationship_assessment=RelationshipAssessment.SAME_CASE,
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
# Decision summary: source_case row
# ===================================================================


class TestDecisionSummarySourceCase:
    """The Decision summary must surface source_case_assessment (the
    classifier consumes it via the source-context zones)."""

    def _classification(self):
        from llm_dup_dev.models.classification import ClassificationResult
        from llm_dup_dev.models.enums import (
            RelationshipAssessment,
            IndependenceAssessment,
        )
        return ClassificationResult(
            pair_id="R001__R002",
            independence_assessment=IndependenceAssessment.NON_INDEPENDENT,
            relationship_assessment=RelationshipAssessment.SAME_CASE,
            explanation="test",
        )

    def _features_with_assessment(self, report_a, report_b, assessment):
        stats = FrequencyStats(
            N_total=100_000, N_drugs=48, N_AEs=200, N_drugs_AEs=12
        )
        return generate_pair_features(
            r1=report_a, r2=report_b, stats=stats,
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
    """Rendering of the penalty the classifier recorded.

    These render an InconsistencyPenalty directly. They deliberately do not
    fabricate explanation strings: the previous versions did, which made them
    self-consistent by construction and structurally unable to catch either
    the custom-weights disagreement or the dropped multi-patient row.
    """

    def test_breakdown_from_recorded_terms(self):
        """'n × w + ... = total op Y', with the weights the run applied."""
        penalty = InconsistencyPenalty(
            w_tot=2.2,
            threshold=4.0,
            terms=[
                PenaltyTerm(severity=InconsistencySeverity.MINOR, count=3, weight=0.4),
                PenaltyTerm(severity=InconsistencySeverity.MODERATE, count=1, weight=1.0),
            ],
        )
        assert _format_penalty_summary(penalty) == "3 × 0.4 + 1 × 1.0 = 2.2 < 4.0"

    def test_failing_comparison_uses_gte(self):
        """w_tot >= threshold renders the >= operator."""
        penalty = InconsistencyPenalty(
            w_tot=6.0,
            threshold=4.0,
            terms=[PenaltyTerm(severity=InconsistencySeverity.STRONG, count=2, weight=3.0)],
        )
        assert _format_penalty_summary(penalty) == "2 × 3.0 = 6.0 >= 4.0"

    def test_boundary_is_not_satisfied(self):
        """At w_tot == threshold the tolerance is exceeded — strict <."""
        penalty = InconsistencyPenalty(w_tot=4.0, threshold=4.0)
        assert penalty.satisfied is False
        assert _format_penalty_summary(penalty) == "4.0 >= 4.0"

    def test_decisive_included_in_breakdown(self):
        """Decisive contributes like any other severity."""
        penalty = InconsistencyPenalty(
            w_tot=100.0,
            threshold=4.0,
            terms=[
                PenaltyTerm(severity=InconsistencySeverity.DECISIVE, count=1, weight=100.0)
            ],
        )
        assert _format_penalty_summary(penalty) == "1 × 100.0 = 100.0 >= 4.0"

    def test_no_terms_shows_total_only(self):
        """Zero inconsistencies → no breakdown, just the comparison."""
        penalty = InconsistencyPenalty(w_tot=0.0, threshold=4.0)
        assert _format_penalty_summary(penalty) == "0.0 < 4.0"

    def test_no_comparison_is_reported_as_such(self):
        """Where the decision never compared w_tot, do not invent a threshold.

        Replaces the old notional '>= 0.0', which asserted a comparison that
        never happened.
        """
        penalty = InconsistencyPenalty(w_tot=0.8, threshold=None, terms=[
            PenaltyTerm(severity=InconsistencySeverity.MINOR, count=2, weight=0.4),
        ])
        assert penalty.satisfied is None
        assert _format_penalty_summary(penalty) == "2 × 0.4 = 0.8 (not compared)"

    def test_missing_penalty_returns_none(self):
        """No penalty recorded → None (template falls back to '--')."""
        assert _format_penalty_summary(None) is None

    def test_uses_recorded_weights_not_defaults(self):
        """The rendered weights are the run's, not DecisionThresholds()."""
        # w_strong default is 3.0; this run used 5.0.
        penalty = InconsistencyPenalty(
            w_tot=5.0,
            threshold=6.0,
            terms=[PenaltyTerm(severity=InconsistencySeverity.STRONG, count=1, weight=5.0)],
        )
        result = _format_penalty_summary(penalty)
        assert result == "1 × 5.0 = 5.0 < 6.0"
        assert "3.0" not in result


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
        l_patient=0.0,
        p_match=1.0,
        anchor_type="drugs_aes",
        anchor_n=10,
        reporting_context="Country (US, 2020)",
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


# ===================================================================
# Penalty regressions — driven through classify(), not fabricated text
# ===================================================================


def _penalty_features(L_match, minor=0, moderate=0, strong=0, verdict=None,
                      contributions=None):
    """A PairFeatures shaped for the penalty regressions."""
    from llm_dup_dev.models.enums import AnchorType, ReportingContextLevel
    from llm_dup_dev.models.narrative_source_case import (
        SourceCaseAssessment, SourceIdentifierType,
    )
    from llm_dup_dev.models.pair_features import (
        AnchorPopulation, InconsistencyCounts, PairFeatures,
        ProbabilityEstimate, ReportingContext,
    )
    from llm_dup_dev.feature_generation.probability import compute_l_patient

    contributions = contributions or []
    assessment = None
    if verdict is not None:
        assessment = SourceCaseAssessment(
            verdict=verdict,
            shared_source_identifier="10.1/abc",
            shared_source_identifier_type=SourceIdentifierType.DOI,
            case_identifiers_compared=False,
            case_identifiers_comparable=False,
            reasoning="fixture",
        )
    return PairFeatures(
        pair_id="R001__R002",
        reporting_context=ReportingContext(level=ReportingContextLevel.GLOBAL),
        anchor=AnchorPopulation(N_drugs=100, N_AEs=100, N_drugs_AEs=50,
                                type=AnchorType.DRUGS_AES, N_anchor=50),
        probability=ProbabilityEstimate(p_joint=1e-5, p_match=0.01,
                                        feature_contributions=contributions),
        L_match=L_match,
        L_patient=compute_l_patient(contributions),
        inconsistency_counts=InconsistencyCounts(
            minor=minor, moderate=moderate, strong=strong
        ),
        source_case_assessment=assessment,
    )


class TestPenaltyAgreesWithClassifier:
    """The rendered penalty must never contradict the decision it describes.

    Regression: the display used to scrape the threshold out of the
    explanation and recompute w_tot with DecisionThresholds() DEFAULTS, so a
    run with custom weights rendered a row that disagreed with the sentence
    directly above it.
    """

    def test_custom_weights_are_respected(self):
        """A run with w_strong=5.0 must not be rendered with the default 3.0."""
        from llm_dup_dev.classifier.config import DecisionThresholds
        from llm_dup_dev.classifier.decision_logic import classify

        thresholds = DecisionThresholds(w_strong=5.0, w_tot_compelling_zone=6.0)
        features = _penalty_features(L_match=4.0, strong=1)
        result = classify(features, thresholds)

        rendered = _format_penalty_summary(result.penalty)
        # The classifier says w_tot=5.0 < 6.0; the row must say the same.
        assert "w_tot=5.0 < 6.0" in result.explanation
        assert rendered == "1 × 5.0 = 5.0 < 6.0"
        # The old code rendered "1 × 3.0 = 3.0 < 6.0" using the default weight.
        assert "3.0" not in rendered

    def test_rendered_total_always_matches_the_recorded_w_tot(self):
        """Across weight settings, the displayed total is the classifier's."""
        from llm_dup_dev.classifier.config import DecisionThresholds
        from llm_dup_dev.classifier.decision_logic import classify

        for w_moderate in (1.0, 2.0, 7.5):
            thresholds = DecisionThresholds(w_moderate=w_moderate)
            features = _penalty_features(L_match=4.0, moderate=1)
            result = classify(features, thresholds)
            rendered = _format_penalty_summary(result.penalty)
            assert f"= {result.penalty.w_tot:.1f}" in rendered
            assert f"w_tot={result.penalty.w_tot:.1f}" in result.explanation


class TestPenaltyIsRecordedOnEveryBranch:
    """Every branch records the score; only some record a comparison."""

    def test_multi_patient_default_has_a_penalty_row(self):
        """Regression: this row used to vanish.

        The branch formats as 'w_tot=0.0 (threshold 1.0)' — no '<' or '>=' —
        so the old regex missed it and the row silently disappeared from both
        the pair page and the index.
        """
        from llm_dup_dev.classifier.decision_logic import classify
        from llm_dup_dev.models.narrative_source_case import SourceCaseVerdict
        from llm_dup_dev.models.pair_features import FeatureContribution

        features = _penalty_features(
            L_match=0.5,
            verdict=SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
            # sex only -> L_patient 0.30, below the bar -> branch C default
            contributions=[
                FeatureContribution(feature="patient_sex", p_f=0.5,
                                    method="domain_size")
            ],
        )
        result = classify(features)
        assert result.final_label == "Otherwise_related"
        # w_tot passed the branch-B gate, so the comparison is real.
        assert _format_penalty_summary(result.penalty) == "0.0 < 1.0"

    def test_below_limited_records_no_comparison(self):
        """L_match decides here; w_tot is never consulted."""
        from llm_dup_dev.classifier.decision_logic import classify

        features = _penalty_features(L_match=0.5, minor=2)
        result = classify(features)
        assert result.final_label == "Unrelated"
        assert result.penalty.threshold is None
        # Previously rendered as a notional "2 × 0.4 = 0.8 >= 0.0".
        assert _format_penalty_summary(result.penalty) == "2 × 0.4 = 0.8 (not compared)"

    def test_different_case_records_no_comparison(self):
        """SAME_SOURCE_DIFFERENT_CASE is pinned regardless of w_tot."""
        from llm_dup_dev.classifier.decision_logic import classify
        from llm_dup_dev.models.narrative_source_case import SourceCaseVerdict

        features = _penalty_features(
            L_match=4.0, moderate=1,
            verdict=SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE,
        )
        result = classify(features)
        assert result.final_label == "Otherwise_related"
        assert result.penalty.threshold is None
        assert _format_penalty_summary(result.penalty) == "1 × 1.0 = 1.0 (not compared)"

    def test_every_branch_records_a_score(self):
        """No classification may lose its w_tot."""
        from llm_dup_dev.classifier.decision_logic import classify
        from llm_dup_dev.models.narrative_source_case import SourceCaseVerdict

        cases = [
            _penalty_features(L_match=4.0),                       # compelling
            _penalty_features(L_match=4.0, strong=2),             # compelling, failing
            _penalty_features(L_match=2.5),                       # intermediate
            _penalty_features(L_match=1.5),                       # limited
            _penalty_features(L_match=0.5),                       # below limited
            _penalty_features(L_match=4.0,
                              verdict=SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT),
            _penalty_features(L_match=4.0, strong=2,
                              verdict=SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT),
            _penalty_features(L_match=4.0,
                              verdict=SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE),
            _penalty_features(L_match=0.5,
                              verdict=SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE),
        ]
        for features in cases:
            result = classify(features)
            assert result.penalty is not None, result.explanation
            assert _format_penalty_summary(result.penalty) is not None


# ===================================================================
# Screening algorithm view model
# ===================================================================


class TestFormatScreeningValue:
    """Display formatting for one screening feature value."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, "—"),
            (True, "Yes"),
            (False, "No"),
            (0, "0"),
            (42, "42"),
            (-7, "-7"),
            (0.289343890439667, "0.29"),
            (6.4237356205981, "6.42"),
            (0.0, "0.00"),
            ("vigiMatch", "vigiMatch"),
            ("", ""),
            ([], "—"),
            (["a", 1, None], "a, 1, —"),
        ],
    )
    def test_formats_scalars_and_lists(self, value, expected):
        assert _format_screening_value(value) == expected

    def test_booleans_are_not_rendered_as_integers(self):
        """bool is an int subclass; the bool branch must win."""
        assert _format_screening_value(True) != "1"
        assert _format_screening_value(False) != "0"

    @pytest.mark.parametrize(
        "value,expected",
        [
            (0.0034, "0.003"),
            (0.00012, "0.0001"),
            (1.2e-5, "0.00001"),
            (-0.0034, "-0.003"),
        ],
    )
    def test_precision_extends_to_reach_a_significant_digit(self, value, expected):
        """A value that would round to 0.00 must not read as absent."""
        assert _format_screening_value(value) == expected

    def test_a_value_below_the_precision_cap_still_renders(self):
        """Beyond the decimal cap the value is shown as-is, not dropped."""
        assert _format_screening_value(1e-30) == f"{1e-30:.12f}"

    def test_non_finite_values_pass_through(self):
        assert _format_screening_value(float("nan")) == "nan"
        assert _format_screening_value(float("inf")) == "inf"


class TestBuildScreeningVM:
    """Assembly of the screening section's view model."""

    def test_none_and_empty_yield_no_section(self):
        assert _build_screening_vm(None) is None
        assert _build_screening_vm({}) is None

    def test_key_order_is_preserved(self):
        entry = {"TotalScore": 6.42, "Threshold": 4.07, "vaccine_pair": False}
        vm = _build_screening_vm(entry)
        assert [f.key for f in vm.features] == list(entry)

    def test_keys_are_carried_verbatim(self):
        """Algorithm-defined names are never reworded."""
        vm = _build_screening_vm({"Score_PatientOnsetAge": 1.187})
        assert vm.features[0].key == "Score_PatientOnsetAge"
        assert vm.features[0].value == "1.19"

    def test_algorithm_name_is_carried(self):
        vm = _build_screening_vm({"TotalScore": 1.0}, "vigiMatch")
        assert vm.algorithm == "vigiMatch"

    def test_algorithm_is_optional(self):
        assert _build_screening_vm({"TotalScore": 1.0}).algorithm is None
