"""Tests for recovering structured evidence from a run's own descriptions.

These parsers exist only for runs saved before the structured fields did. What
they must never do is produce a value from a string they do not actually
recognise: a wrong substance or a wrong date would be highlighted as the run's
own finding. Every test below is either an exact producer format or a refusal.
"""

from __future__ import annotations

import pytest

from vigitwin.models.enums import InconsistencySeverity
from vigitwin.models.pair_features import FeatureContribution, Inconsistency
from vigitwin.review_html.evidence.legacy import (
    recover_clinical_date,
    recover_drug_set_terms,
    resolved_clinical_date,
    resolved_drug_set_terms,
)


class TestDrugSetRecovery:
    """The wording `_check_drug_set` writes, both directions."""

    def test_report_1_direction(self):
        assert recover_drug_set_terms(
            "Key drug(s) in report 1 not found in report 2: ['aspirin']"
        ) == (["aspirin"], "missing_in_r2")

    def test_report_2_direction(self):
        assert recover_drug_set_terms(
            "Key drug(s) in report 2 not found in report 1: ['warfarin']"
        ) == (["warfarin"], "missing_in_r1")

    def test_several_substances_keep_their_stored_order(self):
        terms, kind = recover_drug_set_terms(
            "Key drug(s) in report 1 not found in report 2: "
            "['aspirin', 'metformin', 'warfarin']"
        )
        assert terms == ["aspirin", "metformin", "warfarin"]
        assert kind == "missing_in_r2"

    def test_a_substance_containing_a_comma_survives(self):
        """The list is parsed, not split on commas."""
        terms, _ = recover_drug_set_terms(
            "Key drug(s) in report 1 not found in report 2: "
            "['amoxicillin; clavulanic acid', 'codeine, caffeine']"
        )
        assert terms == ["amoxicillin; clavulanic acid", "codeine, caffeine"]

    @pytest.mark.parametrize(
        "description",
        [
            None,
            "",
            "the drug sets differ",
            "Key drugs in report 1 not found in report 2: ['aspirin']",
            "Key drug(s) in report 1 not found in report 2: aspirin",
            "Key drug(s) in report 1 not found in report 2: []",
            "Key drug(s) in report 1 not found in report 2: ['a',",
            "Key drug(s) in report 1 not found in report 1: ['aspirin']",
            "Key drug(s) in report 3 not found in report 2: ['aspirin']",
            "Key drug(s) in report 1 not found in report 2: [1, 2]",
            "Key drug(s) in report 1 not found in report 2: {'a': 1}",
        ],
    )
    def test_anything_else_is_refused(self, description):
        assert recover_drug_set_terms(description) is None

    def test_an_expression_is_not_evaluated(self):
        """`literal_eval`, so a description cannot smuggle in an expression."""
        assert (
            recover_drug_set_terms(
                "Key drug(s) in report 1 not found in report 2: "
                "[__import__('os').name]"
            )
            is None
        )


class TestClinicalDateRecovery:
    """The wording `compute_probability` writes for one matched date."""

    @pytest.mark.parametrize("resolution", ["day", "month", "year"])
    def test_every_resolution(self, resolution):
        assert recover_clinical_date(
            f"clinical_date 2022-10-11 ({resolution}): D_f=1200"
        ) == ("2022-10-11", resolution)

    def test_a_truncated_value(self):
        assert recover_clinical_date("clinical_date 2022-10 (month): D_f=40") == (
            "2022-10",
            "month",
        )

    @pytest.mark.parametrize(
        "description",
        [
            None,
            "",
            "a clinical date matched",
            "clinical_date 2022-10-11: D_f=1200",
            "clinical_date (day): D_f=1200",
            "clinical_date 2022-10-11 (decade): D_f=1200",
            "patient_age: D_f=100",
        ],
    )
    def test_anything_else_is_refused(self, description):
        assert recover_clinical_date(description) is None


class TestTheStructuredFieldsWin:
    """Recovery is a fallback; it never second-guesses a recorded value."""

    def test_a_recorded_date_is_used_as_is(self):
        fc = FeatureContribution(
            feature="clinical_date",
            p_f=0.01,
            method="domain_size",
            description="clinical_date 1999-01-01 (day): D_f=10",
            matched_value="2022-10-11",
            value_resolution="day",
        )
        assert resolved_clinical_date(fc) == ("2022-10-11", "day", False)

    def test_a_recorded_term_is_used_as_is(self):
        inc = Inconsistency(
            feature="drug_set",
            severity=InconsistencySeverity.MODERATE,
            description="Key drug(s) in report 1 not found in report 2: ['other']",
            terms=["aspirin"],
            kind="missing_in_r2",
        )
        assert resolved_drug_set_terms(inc) == (["aspirin"], "missing_in_r2", False)

    def test_recovery_is_flagged_as_recovered(self):
        inc = Inconsistency(
            feature="drug_set",
            severity=InconsistencySeverity.MODERATE,
            description="Key drug(s) in report 1 not found in report 2: ['aspirin']",
        )
        assert resolved_drug_set_terms(inc) == (["aspirin"], "missing_in_r2", True)

    def test_neither_source_yields_nothing(self):
        inc = Inconsistency(
            feature="drug_set",
            severity=InconsistencySeverity.MODERATE,
            description="the drug sets differ",
        )
        assert resolved_drug_set_terms(inc) is None
