"""Tests for feature matching between report pairs."""

import pytest

from llm_dup_dev.models.report import (
    AdverseEvent,
    DateValue,
    Drug,
    Report,
    SourcedValue,
)
from llm_dup_dev.feature_generation.matching import (
    compute_matching_aes,
    compute_matching_drugs,
    compute_matches,
)


class TestDrugMatching:
    def test_identical_drugs(self, report_a):
        matching, only_r1, only_r2 = compute_matching_drugs(report_a, report_a)
        assert matching == ["pregabalin", "sertraline"]
        assert only_r1 == []
        assert only_r2 == []

    def test_partial_overlap(self, report_a, report_b):
        matching, only_r1, only_r2 = compute_matching_drugs(report_a, report_b)
        assert matching == ["pregabalin", "sertraline"]
        assert only_r1 == []
        assert only_r2 == ["omeprazole"]

    def test_no_overlap(self, report_a, report_c):
        matching, only_r1, only_r2 = compute_matching_drugs(report_a, report_c)
        assert matching == []
        assert len(only_r1) == 2
        assert len(only_r2) == 2

    def test_empty_drugs(self, report_a, report_empty):
        matching, only_r1, only_r2 = compute_matching_drugs(
            report_a, report_empty
        )
        assert matching == []

    def test_case_insensitive(self, report_a):
        """Drug names should match case-insensitively."""
        report_upper = Report(
            report_id="R_UPPER",
            drugs=[Drug(substance="PREGABALIN", sources=["structured"])],
        )
        matching, _, _ = compute_matching_drugs(report_a, report_upper)
        assert len(matching) == 1

    def test_canonical_sorted_order(self):
        """Matching drugs must be in sorted order for reproducibility."""
        r1 = Report(
            report_id="1",
            drugs=[
                Drug(substance="zolpidem", sources=["s"]),
                Drug(substance="aspirin", sources=["s"]),
            ],
        )
        r2 = Report(
            report_id="2",
            drugs=[
                Drug(substance="aspirin", sources=["s"]),
                Drug(substance="zolpidem", sources=["s"]),
            ],
        )
        matching, _, _ = compute_matching_drugs(r1, r2)
        assert matching == ["aspirin", "zolpidem"]


class TestAEMatching:
    def test_partial_overlap(self, report_a, report_b):
        matching, only_r1, only_r2 = compute_matching_aes(report_a, report_b)
        assert matching == ["somnolence"]
        assert only_r1 == ["dizziness"]
        assert only_r2 == ["nausea"]

    def test_no_overlap(self, report_a, report_c):
        matching, _, _ = compute_matching_aes(report_a, report_c)
        assert matching == []

    def test_empty_aes(self, report_a, report_empty):
        matching, _, _ = compute_matching_aes(report_a, report_empty)
        assert matching == []


class TestComputeMatches:
    def test_full_match_result(self, report_a, report_b):
        result = compute_matches(report_a, report_b)
        assert result.matching_drugs == ["pregabalin", "sertraline"]
        assert result.matching_AEs == ["somnolence"]
        assert result.has_drug_overlap
        assert result.has_ae_overlap

    def test_demographic_sex_match(self, report_a, report_b):
        result = compute_matches(report_a, report_b)
        sex = next(
            dm
            for dm in result.demographic_matches
            if dm.feature == "patient_sex"
        )
        assert sex.matched is True

    def test_demographic_age_match_multivalued(self, report_a, report_b):
        """report_b has [54, 55]; report_a has [54] → overlap at 54."""
        result = compute_matches(report_a, report_b)
        age = next(
            dm
            for dm in result.demographic_matches
            if dm.feature == "patient_age"
        )
        assert age.matched is True

    def test_demographic_no_overlap(self, report_a, report_c):
        result = compute_matches(report_a, report_c)
        sex = next(
            dm
            for dm in result.demographic_matches
            if dm.feature == "patient_sex"
        )
        assert sex.matched is False

    def test_date_match_same_day(self, report_a, report_b):
        result = compute_matches(report_a, report_b)
        date = next(
            dm
            for dm in result.demographic_matches
            if dm.feature == "report_date"
        )
        assert date.matched is True
        assert date.date_resolution == "day"

    def test_date_match_different_resolution(self):
        """Day vs month resolution: should match at month level."""
        r1 = Report(
            report_id="1",
            report_date_values=[
                DateValue(
                    value="2020-06-12",
                    resolution="day",
                    sources=["structured"],
                )
            ],
        )
        r2 = Report(
            report_id="2",
            report_date_values=[
                DateValue(
                    value="2020-06",
                    resolution="month",
                    sources=["structured"],
                )
            ],
        )
        result = compute_matches(r1, r2)
        date = next(
            dm
            for dm in result.demographic_matches
            if dm.feature == "report_date"
        )
        assert date.matched is True
        assert date.date_resolution == "month"

    def test_date_no_match_different_months(self):
        r1 = Report(
            report_id="1",
            report_date_values=[
                DateValue(
                    value="2020-06-12",
                    resolution="day",
                    sources=["structured"],
                )
            ],
        )
        r2 = Report(
            report_id="2",
            report_date_values=[
                DateValue(
                    value="2020-07-01",
                    resolution="day",
                    sources=["structured"],
                )
            ],
        )
        result = compute_matches(r1, r2)
        date = next(
            dm
            for dm in result.demographic_matches
            if dm.feature == "report_date"
        )
        assert date.matched is False

    def test_empty_reports(self, report_empty):
        result = compute_matches(report_empty, report_empty)
        assert result.matching_drugs == []
        assert result.matching_AEs == []
        assert not result.has_drug_overlap
