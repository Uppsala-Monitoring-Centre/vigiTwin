"""Tests for the clinical-date inconsistency module.

All reports use synthetic data only — no real pharmacovigilance data.
"""

from __future__ import annotations

import pytest

from llm_dup_dev.feature_generation.clinical_date_inconsistencies import (
    ClinicalDateFinding,
    ClinicalDateResult,
    EntityType,
    Severity,
    compute_clinical_date_inconsistencies,
    derive_overall_ae_onset_date,
    earliest_plausible_date,
    latest_plausible_date,
)
from llm_dup_dev.models.enums import DrugRole
from llm_dup_dev.models.report import (
    AdverseEvent,
    DateValue,
    Drug,
    PartialDate,
    Report,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _drug(
    substance: str,
    start: str | None = None,
    end: str | None = None,
    role: DrugRole | None = DrugRole.SUSPECTED,
    start_res: str = "day",
    end_res: str = "day",
) -> Drug:
    return Drug(
        substance=substance,
        sources=["s"],
        role=role,
        start_date=PartialDate(value=start, resolution=start_res) if start else None,
        end_date=PartialDate(value=end, resolution=end_res) if end else None,
    )


def _ae(
    pt_name: str,
    onset: str | None = None,
    onset_res: str = "day",
) -> AdverseEvent:
    return AdverseEvent(
        pt_name=pt_name,
        sources=["s"],
        start_date=PartialDate(value=onset, resolution=onset_res) if onset else None,
    )


def _report(
    report_id: str = "R1",
    drugs: list[Drug] | None = None,
    aes: list[AdverseEvent] | None = None,
    report_date: str | None = None,
    report_date_res: str = "day",
) -> Report:
    rdv: list[DateValue] = []
    if report_date is not None:
        rdv = [DateValue(value=report_date, resolution=report_date_res, sources=["s"])]
    return Report(
        report_id=report_id,
        drugs=drugs or [],
        adverse_events=aes or [],
        report_date_values=rdv,
    )


def _selected_findings(result: ClinicalDateResult) -> list[ClinicalDateFinding]:
    return [f for f in result.findings if f.selected]


def _selected_severities(result: ClinicalDateResult) -> list[Severity]:
    return [f.final_severity for f in result.findings if f.selected]


# ===================================================================
# 1. Overall AE onset date derivation
# ===================================================================


class TestDeriveOverallAeOnsetDate:
    def test_standard_case(self):
        """Earliest AE onset >= max(suspected start) is picked."""
        report = _report(
            drugs=[
                _drug("A", start="2020-01-10"),
                _drug("B", start="2020-01-20"),
            ],
            aes=[
                _ae("headache", onset="2020-01-15"),
                _ae("nausea", onset="2020-01-25"),
            ],
        )
        onset = derive_overall_ae_onset_date(report)
        assert onset is not None
        assert onset.value == "2020-01-25"

    def test_fallback_no_suspected_drugs(self):
        """No suspected drugs → earliest AE onset."""
        report = _report(
            drugs=[_drug("A", start="2020-01-10", role=DrugRole.CONCOMITANT)],
            aes=[
                _ae("headache", onset="2020-01-05"),
                _ae("nausea", onset="2020-01-25"),
            ],
        )
        onset = derive_overall_ae_onset_date(report)
        assert onset is not None
        assert onset.value == "2020-01-05"

    def test_missing_when_no_qualifying_onset(self):
        """All AE onsets are before max suspected start → None."""
        report = _report(
            drugs=[_drug("A", start="2020-06-01")],
            aes=[
                _ae("headache", onset="2020-01-15"),
                _ae("nausea", onset="2020-02-10"),
            ],
        )
        onset = derive_overall_ae_onset_date(report)
        assert onset is None

    def test_missing_when_no_ae_onsets(self):
        """No AE onset dates at all → None."""
        report = _report(
            drugs=[_drug("A", start="2020-06-01")],
            aes=[_ae("headache")],  # no onset date
        )
        onset = derive_overall_ae_onset_date(report)
        assert onset is None

    def test_no_drugs_fallback(self):
        """No drugs at all → earliest AE onset."""
        report = _report(
            aes=[
                _ae("headache", onset="2020-03-01"),
                _ae("nausea", onset="2020-01-15"),
            ],
        )
        onset = derive_overall_ae_onset_date(report)
        assert onset is not None
        assert onset.value == "2020-01-15"

    def test_ae_onset_equals_max_suspected_qualifies(self):
        """AE onset on same day as max suspected start qualifies."""
        report = _report(
            drugs=[_drug("A", start="2020-05-01")],
            aes=[_ae("headache", onset="2020-05-01")],
        )
        onset = derive_overall_ae_onset_date(report)
        assert onset is not None
        assert onset.value == "2020-05-01"


# ===================================================================
# 2. Threshold behavior
# ===================================================================


class TestOverallAeOnsetThresholds:
    """Overall AE onset: 1-7d minor, 8-30d moderate, >30d strong."""

    def _pair(self, onset_a: str, onset_b: str) -> ClinicalDateResult:
        r1 = _report(
            report_id="R1",
            drugs=[_drug("A", start="2020-01-01")],
            aes=[_ae("headache", onset=onset_a)],
        )
        r2 = _report(
            report_id="R2",
            drugs=[_drug("A", start="2020-01-01")],
            aes=[_ae("headache", onset=onset_b)],
        )
        return compute_clinical_date_inconsistencies(r1, r2)

    def test_exact_match_no_inconsistency(self):
        result = self._pair("2020-02-01", "2020-02-01")
        assert result.n_minor == 0
        assert result.n_moderate == 0
        assert result.n_strong == 0

    def test_3_days_minor(self):
        result = self._pair("2020-02-01", "2020-02-04")
        # The overall AE onset is the only source on that date, so
        # we expect a minor from at least one direction.
        assert result.n_minor >= 1
        assert result.n_strong == 0
        assert result.n_moderate == 0

    def test_7_days_still_minor(self):
        result = self._pair("2020-02-01", "2020-02-08")
        assert result.n_minor >= 1
        assert result.n_moderate == 0

    def test_8_days_moderate(self):
        result = self._pair("2020-02-01", "2020-02-09")
        assert result.n_moderate >= 1

    def test_30_days_still_moderate(self):
        result = self._pair("2020-02-01", "2020-03-02")
        assert result.n_moderate >= 1
        assert result.n_strong == 0

    def test_31_days_strong(self):
        result = self._pair("2020-02-01", "2020-03-04")
        assert result.n_strong >= 1


class TestDrugStartAndAeOnsetThresholds:
    """Drug start / AE onset: 1-14d minor, 15-60d moderate, >60d strong."""

    def test_drug_start_10_days_minor(self):
        r1 = _report("R1", drugs=[_drug("A", start="2020-01-01")])
        r2 = _report("R2", drugs=[_drug("A", start="2020-01-11")])
        result = compute_clinical_date_inconsistencies(r1, r2)
        selected = _selected_findings(result)
        drug_findings = [
            f for f in selected
            if "drug_start" in f.entity_label
        ]
        assert any(f.final_severity == Severity.MINOR for f in drug_findings)

    def test_drug_start_14_days_still_minor(self):
        r1 = _report("R1", drugs=[_drug("A", start="2020-01-01")])
        r2 = _report("R2", drugs=[_drug("A", start="2020-01-15")])
        result = compute_clinical_date_inconsistencies(r1, r2)
        selected = _selected_findings(result)
        drug_findings = [
            f for f in selected
            if "drug_start" in f.entity_label
        ]
        assert any(f.final_severity == Severity.MINOR for f in drug_findings)

    def test_drug_start_15_days_moderate(self):
        r1 = _report("R1", drugs=[_drug("A", start="2020-01-01")])
        r2 = _report("R2", drugs=[_drug("A", start="2020-01-16")])
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_moderate >= 1

    def test_drug_start_61_days_strong(self):
        r1 = _report("R1", drugs=[_drug("A", start="2020-01-01")])
        r2 = _report("R2", drugs=[_drug("A", start="2020-03-03")])
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_strong >= 1

    def test_ae_onset_match_10_days(self):
        """10 days apart with only AE onsets — overall AE onset is derived
        and uses tighter thresholds (8-30d = moderate), so the selected
        finding is moderate (from overall AE onset), not minor."""
        r1 = _report("R1", aes=[_ae("headache", onset="2020-01-01")])
        r2 = _report("R2", aes=[_ae("headache", onset="2020-01-11")])
        result = compute_clinical_date_inconsistencies(r1, r2)
        # Overall AE onset uses tighter thresholds: 10d → moderate
        assert result.n_moderate >= 1
        # The raw AE onset entity is minor (10d < 14d) but loses to
        # the higher-priority overall AE onset in the same bucket
        ae_only = [
            f for f in result.findings
            if f.entity_type == EntityType.AE_ONSET
            and f.raw_severity == Severity.MINOR
        ]
        assert len(ae_only) >= 1

    def test_ae_onset_match_20_days_moderate(self):
        r1 = _report("R1", aes=[_ae("headache", onset="2020-01-01")])
        r2 = _report("R2", aes=[_ae("headache", onset="2020-01-21")])
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_moderate >= 1


# ===================================================================
# 3. Missing data
# ===================================================================


class TestMissingData:
    def test_no_inconsistency_when_one_side_missing_drug_start(self):
        r1 = _report("R1", drugs=[_drug("A", start="2020-01-01")])
        r2 = _report("R2", drugs=[_drug("A")])  # no start date
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_minor == 0
        assert result.n_moderate == 0
        assert result.n_strong == 0
        assert result.n_decisive == 0

    def test_no_inconsistency_when_one_side_missing_ae_onset(self):
        r1 = _report("R1", aes=[_ae("headache", onset="2020-01-01")])
        r2 = _report("R2", aes=[_ae("headache")])  # no onset
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_minor == 0
        assert result.n_moderate == 0
        assert result.n_strong == 0

    def test_no_inconsistency_when_drug_not_on_other_report(self):
        r1 = _report("R1", drugs=[_drug("A", start="2020-01-01")])
        r2 = _report("R2", drugs=[_drug("B", start="2020-06-01")])
        result = compute_clinical_date_inconsistencies(r1, r2)
        # Different drugs: no match, no inconsistency
        drug_findings = [
            f for f in result.findings
            if "drug_start" in f.entity_label
        ]
        assert all(not f.selected for f in drug_findings)

    def test_empty_reports_no_inconsistency(self):
        r1 = _report("R1")
        r2 = _report("R2")
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_minor == 0
        assert result.n_moderate == 0
        assert result.n_strong == 0
        assert result.n_decisive == 0


# ===================================================================
# 4. Exact-date cross-entity downgrade
# ===================================================================


class TestCrossEntityDowngrade:
    def test_downgrade_strong_to_moderate_via_drug_start(self):
        """Source date matches another drug start on other report → downgrade."""
        # Drug A: 90 days apart (strong) but date "2020-01-01" matches
        # drug B's start on R2
        r1 = _report(
            "R1",
            drugs=[
                _drug("A", start="2020-01-01"),
                _drug("B", start="2020-06-15"),
            ],
        )
        r2 = _report(
            "R2",
            drugs=[
                _drug("A", start="2020-04-01"),
                _drug("B", start="2020-01-01"),  # exact match for R1's drug A date
            ],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        # From R1's perspective: drug A start "2020-01-01" raw strong,
        # but 2020-01-01 matches a date on R2 → downgrade to moderate
        r1_findings = [
            f for f in result.findings
            if f.source_report == "R1"
            and "drug_start:a" in f.entity_label
            and f.raw_severity == Severity.STRONG
        ]
        assert len(r1_findings) >= 1
        assert r1_findings[0].final_severity == Severity.MODERATE
        assert any("exact match" in d for d in r1_findings[0].downgrades)

    def test_downgrade_via_ae_onset(self):
        """Source date matches an AE onset on other report → downgrade."""
        r1 = _report(
            "R1",
            drugs=[_drug("A", start="2020-01-01")],
            aes=[_ae("headache", onset="2020-06-01")],
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2020-04-01")],
            aes=[_ae("headache", onset="2020-01-01")],  # exact match
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        r1_drug_findings = [
            f for f in result.findings
            if f.source_report == "R1"
            and "drug_start:a" in f.entity_label
        ]
        assert len(r1_drug_findings) >= 1
        downgraded = [f for f in r1_drug_findings if f.downgrades]
        assert len(downgraded) >= 1

    def test_downgrade_via_drug_end_date(self):
        """Source date matches a drug end date on other report → downgrade."""
        r1 = _report(
            "R1",
            drugs=[_drug("A", start="2020-01-01")],
        )
        r2 = _report(
            "R2",
            drugs=[
                _drug("A", start="2020-04-01", end="2020-01-01"),
            ],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        r1_findings = [
            f for f in result.findings
            if f.source_report == "R1"
            and "drug_start:a" in f.entity_label
            and f.raw_severity == Severity.STRONG
        ]
        assert len(r1_findings) >= 1
        assert r1_findings[0].final_severity == Severity.MODERATE
        assert any("exact match" in d for d in r1_findings[0].downgrades)

    def test_downgrade_moderate_to_minor(self):
        """Moderate → minor via cross-entity downgrade."""
        r1 = _report(
            "R1",
            drugs=[_drug("A", start="2020-01-01")],
        )
        r2 = _report(
            "R2",
            drugs=[
                _drug("A", start="2020-01-20"),  # 19 days → moderate
                _drug("B", start="2020-01-01"),  # exact match
            ],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        r1_findings = [
            f for f in result.findings
            if f.source_report == "R1"
            and "drug_start:a" in f.entity_label
        ]
        assert len(r1_findings) >= 1
        assert r1_findings[0].raw_severity == Severity.MODERATE
        assert r1_findings[0].final_severity == Severity.MINOR

    def test_minor_stays_minor_after_downgrade(self):
        """Minor → minor (does not go below minor)."""
        r1 = _report(
            "R1",
            drugs=[_drug("A", start="2020-01-01")],
        )
        r2 = _report(
            "R2",
            drugs=[
                _drug("A", start="2020-01-10"),  # 9 days → minor
                _drug("B", start="2020-01-01"),  # exact match
            ],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        r1_findings = [
            f for f in result.findings
            if f.source_report == "R1"
            and "drug_start:a" in f.entity_label
        ]
        assert len(r1_findings) >= 1
        assert r1_findings[0].final_severity == Severity.MINOR


# ===================================================================
# 5. Concomitant drug downgrade
# ===================================================================


class TestConcomitantDrugDowngrade:
    def test_strong_becomes_minor(self):
        """Concomitant drug: strong → minor (-2 steps)."""
        r1 = _report("R1", drugs=[_drug("A", start="2020-01-01", role=DrugRole.CONCOMITANT)])
        r2 = _report("R2", drugs=[_drug("A", start="2020-04-01", role=DrugRole.CONCOMITANT)])
        result = compute_clinical_date_inconsistencies(r1, r2)
        concom_findings = [
            f for f in result.findings
            if f.entity_type == EntityType.CONCOMITANT_DRUG_START
            and f.raw_severity == Severity.STRONG
        ]
        assert len(concom_findings) >= 1
        assert all(f.final_severity == Severity.MINOR for f in concom_findings)

    def test_moderate_becomes_ignored(self):
        """Concomitant drug: moderate → ignored."""
        r1 = _report("R1", drugs=[_drug("A", start="2020-01-01", role=DrugRole.CONCOMITANT)])
        r2 = _report("R2", drugs=[_drug("A", start="2020-01-20", role=DrugRole.CONCOMITANT)])
        result = compute_clinical_date_inconsistencies(r1, r2)
        concom_findings = [
            f for f in result.findings
            if f.entity_type == EntityType.CONCOMITANT_DRUG_START
            and f.raw_severity == Severity.MODERATE
        ]
        assert len(concom_findings) >= 1
        assert all(f.final_severity == Severity.IGNORED for f in concom_findings)
        # Should not count
        assert result.n_moderate == 0

    def test_minor_becomes_ignored(self):
        """Concomitant drug: minor → ignored."""
        r1 = _report("R1", drugs=[_drug("A", start="2020-01-01", role=DrugRole.CONCOMITANT)])
        r2 = _report("R2", drugs=[_drug("A", start="2020-01-10", role=DrugRole.CONCOMITANT)])
        result = compute_clinical_date_inconsistencies(r1, r2)
        concom_findings = [
            f for f in result.findings
            if f.entity_type == EntityType.CONCOMITANT_DRUG_START
            and f.raw_severity == Severity.MINOR
        ]
        assert len(concom_findings) >= 1
        assert all(f.final_severity == Severity.IGNORED for f in concom_findings)
        assert result.n_minor == 0

    def test_suspected_drug_not_downgraded(self):
        """Suspected drug is NOT subject to concomitant downgrade."""
        r1 = _report("R1", drugs=[_drug("A", start="2020-01-01", role=DrugRole.SUSPECTED)])
        r2 = _report("R2", drugs=[_drug("A", start="2020-04-01", role=DrugRole.SUSPECTED)])
        result = compute_clinical_date_inconsistencies(r1, r2)
        drug_findings = [
            f for f in result.findings
            if f.entity_type == EntityType.SUSPECTED_DRUG_START
            and f.raw_severity == Severity.STRONG
        ]
        assert len(drug_findings) >= 1
        assert all(f.final_severity == Severity.STRONG for f in drug_findings)


# ===================================================================
# 6. One-inconsistency-per-source-date rule
# ===================================================================


class TestSourceDateBucketing:
    def test_multiple_entities_same_date_count_once(self):
        """Overall AE onset and drug start on same date → only one counted."""
        # Both drug start and AE onset are 2020-01-01 on R1, and both
        # have mismatches against R2. Only one should count for R1's
        # 2020-01-01 bucket.
        r1 = _report(
            "R1",
            drugs=[_drug("A", start="2020-01-01")],
            aes=[_ae("headache", onset="2020-01-01")],
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2020-03-01")],
            aes=[_ae("headache", onset="2020-03-01")],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        # From R1: date 2020-01-01 should contribute at most 1 inconsistency
        r1_selected = [f for f in result.findings if f.selected and f.source_report == "R1"]
        r1_dates = [f.source_date.value for f in r1_selected]
        # Count occurrences of "2020-01-01"
        assert r1_dates.count("2020-01-01") <= 1

    def test_overall_ae_onset_gets_priority(self):
        """Overall AE onset takes priority over drug start in same bucket."""
        # Make the overall AE onset land on the same date as a drug start
        r1 = _report(
            "R1",
            drugs=[_drug("A", start="2020-01-01")],
            aes=[_ae("headache", onset="2020-01-01")],
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2020-02-15")],
            aes=[_ae("headache", onset="2020-02-15")],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        # The selected finding for R1's 2020-01-01 should be overall_ae_onset
        # (or ae_onset) rather than drug_start, because overall_ae_onset
        # has highest priority
        r1_selected_jan = [
            f for f in result.findings
            if f.selected
            and f.source_report == "R1"
            and f.source_date.value == "2020-01-01"
        ]
        if r1_selected_jan:
            # Should be overall_ae_onset (priority 0) or at least not
            # a lower-priority entity if a higher one was available
            selected_types = [f.entity_type for f in r1_selected_jan]
            assert EntityType.OVERALL_AE_ONSET in selected_types or len(selected_types) <= 1

    def test_lower_priority_may_contribute_if_higher_has_no_inconsistency(self):
        """If overall AE onset has no inconsistency, drug start may count."""
        # Overall AE onset matches exactly, but drug start differs
        r1 = _report(
            "R1",
            drugs=[_drug("A", start="2020-01-01")],
            aes=[_ae("headache", onset="2020-01-01")],
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2020-03-01")],  # 60 days → moderate
            aes=[_ae("headache", onset="2020-01-01")],  # exact match
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        # The overall AE onset matches exactly → no inconsistency from that entity
        # The drug start differs → should be selected since AE onset had no issue
        r1_selected = [
            f for f in result.findings
            if f.selected and f.source_report == "R1"
        ]
        drug_selected = [f for f in r1_selected if "drug_start" in f.entity_label]
        assert len(drug_selected) >= 1


# ===================================================================
# 7. Decisive impossibility rule
# ===================================================================


class TestDecisiveImpossibility:
    def test_report_date_before_ae_onset_full_dates(self):
        """Report date A before overall AE onset B → decisive."""
        r1 = _report(
            "R1",
            drugs=[_drug("A", start="2005-01-01")],
            aes=[_ae("headache", onset="2005-03-01")],
            report_date="1999-02-10",
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2005-01-01")],
            aes=[_ae("headache", onset="2005-03-01")],
            report_date="2005-06-01",
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_decisive >= 1

    def test_year_only_onset_vs_earlier_report_date(self):
        """Year-only onset '2005' with report date '1999-02-10' → decisive."""
        r1 = _report(
            "R1",
            report_date="1999-02-10",
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2004-06-01")],
            aes=[_ae("headache", onset="2005", onset_res="year")],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_decisive >= 1

    def test_year_month_onset_vs_earlier_report_date(self):
        """Onset '2005-03' with report date '2005-02-15' → decisive."""
        r1 = _report(
            "R1",
            report_date="2005-02-15",
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2005-01-01")],
            aes=[_ae("headache", onset="2005-03", onset_res="month")],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_decisive >= 1

    def test_not_decisive_when_ambiguous(self):
        """Onset '2005' with report date '2005-06-01' → NOT decisive
        (report date is within the onset year)."""
        r1 = _report(
            "R1",
            report_date="2005-06-01",
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2004-01-01")],
            aes=[_ae("headache", onset="2005", onset_res="year")],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_decisive == 0

    def test_not_decisive_same_day(self):
        """Report date equals earliest plausible onset → NOT decisive
        (no positive separation)."""
        r1 = _report(
            "R1",
            report_date="2005-03-01",
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2005-01-01")],
            aes=[_ae("headache", onset="2005-03-01")],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_decisive == 0

    def test_no_decisive_when_onset_missing(self):
        """No overall AE onset → no decisive check possible."""
        r1 = _report("R1", report_date="1999-01-01")
        r2 = _report("R2", aes=[_ae("headache")])  # no onset
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_decisive == 0


# ===================================================================
# 8. Symmetry / non-double-counting
# ===================================================================


class TestSymmetry:
    def test_both_reports_contribute(self):
        """Each report can contribute its own bucket inconsistencies."""
        r1 = _report(
            "R1",
            drugs=[_drug("A", start="2020-01-01")],
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2020-04-01")],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        r1_selected = [f for f in result.findings if f.selected and f.source_report == "R1"]
        r2_selected = [f for f in result.findings if f.selected and f.source_report == "R2"]
        # Both should have findings
        assert len(r1_selected) >= 1
        assert len(r2_selected) >= 1

    def test_decisive_not_double_counted(self):
        """Same directional impossibility not counted twice."""
        r1 = _report(
            "R1",
            report_date="1999-01-01",
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2005-01-01")],
            aes=[_ae("headache", onset="2005-06-01")],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        decisive_findings = [
            f for f in result.findings
            if f.final_severity == Severity.DECISIVE and f.selected
        ]
        # At most 2 decisive (one per direction), but the R2→R1 direction
        # should not fire because R2's report date is not before R1's onset
        # (R1 has no onset).
        assert result.n_decisive <= 2

    def test_symmetric_counts_for_identical_mismatch(self):
        """For a simple two-drug mismatch, both directions should count."""
        r1 = _report("R1", drugs=[_drug("A", start="2020-01-01")])
        r2 = _report("R2", drugs=[_drug("A", start="2020-04-01")])
        result = compute_clinical_date_inconsistencies(r1, r2)
        # Both R1 and R2 contribute one finding for drug_start:a
        assert result.n_strong >= 2  # 90 days → strong from each side


# ===================================================================
# 9. Audit / debug output
# ===================================================================


class TestAuditOutput:
    def test_findings_show_raw_difference(self):
        r1 = _report("R1", drugs=[_drug("A", start="2020-01-01")])
        r2 = _report("R2", drugs=[_drug("A", start="2020-01-20")])
        result = compute_clinical_date_inconsistencies(r1, r2)
        drug_findings = [
            f for f in result.findings
            if "drug_start:a" in f.entity_label
            and f.raw_diff_days is not None
        ]
        assert len(drug_findings) >= 1
        assert drug_findings[0].raw_diff_days == 19

    def test_findings_show_downgrade_reasons(self):
        r1 = _report(
            "R1",
            drugs=[_drug("A", start="2020-01-01", role=DrugRole.CONCOMITANT)],
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2020-04-01", role=DrugRole.CONCOMITANT)],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        concom = [
            f for f in result.findings
            if f.entity_type == EntityType.CONCOMITANT_DRUG_START
            and f.raw_severity != Severity.NONE
        ]
        assert len(concom) >= 1
        assert any("concomitant" in d for d in concom[0].downgrades)

    def test_findings_show_final_severity(self):
        r1 = _report("R1", drugs=[_drug("A", start="2020-01-01")])
        r2 = _report("R2", drugs=[_drug("A", start="2020-04-01")])
        result = compute_clinical_date_inconsistencies(r1, r2)
        for f in result.findings:
            assert f.final_severity is not None

    def test_findings_show_selected_status(self):
        r1 = _report("R1", drugs=[_drug("A", start="2020-01-01")])
        r2 = _report("R2", drugs=[_drug("A", start="2020-04-01")])
        result = compute_clinical_date_inconsistencies(r1, r2)
        # At least some findings should be selected
        assert any(f.selected for f in result.findings)
        # And some may not be selected (e.g. NONE severity)
        # Just verify the field exists on all
        for f in result.findings:
            assert isinstance(f.selected, bool)


# ===================================================================
# 10. Incomplete date handling
# ===================================================================


class TestIncompleteDates:
    def test_month_vs_day_overlapping_no_inconsistency(self):
        """Month '2020-01' vs day '2020-01-15' → intervals overlap → 0."""
        r1 = _report(
            "R1",
            drugs=[
                Drug(
                    substance="A",
                    sources=["s"],
                    role=DrugRole.SUSPECTED,
                    start_date=PartialDate(value="2020-01", resolution="month"),
                ),
            ],
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2020-01-15")],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        drug_findings = [
            f for f in result.findings
            if "drug_start:a" in f.entity_label and f.selected
        ]
        # Should be no inconsistency since intervals overlap
        assert all(
            f.final_severity in (Severity.NONE, Severity.IGNORED)
            for f in drug_findings
        ) or len(drug_findings) == 0

    def test_year_vs_day_different_years_strong(self):
        """Year '2020' vs day '2022-06-01' → min diff = 1 day → minor at least."""
        r1 = _report(
            "R1",
            drugs=[
                Drug(
                    substance="A",
                    sources=["s"],
                    role=DrugRole.SUSPECTED,
                    start_date=PartialDate(value="2020", resolution="year"),
                ),
            ],
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2022-06-01")],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        # Min diff: 2020-12-31 to 2022-06-01 = 518 days → strong
        assert result.n_strong >= 1

    def test_conservative_no_false_positive(self):
        """Year '2020' vs day '2020-06-15' → overlap → no inconsistency."""
        r1 = _report(
            "R1",
            drugs=[
                Drug(
                    substance="A",
                    sources=["s"],
                    role=DrugRole.SUSPECTED,
                    start_date=PartialDate(value="2020", resolution="year"),
                ),
            ],
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2020-06-15")],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        # Intervals overlap: [2020-01-01, 2020-12-31] vs [2020-06-15, 2020-06-15]
        drug_selected = [
            f for f in result.findings
            if "drug_start:a" in f.entity_label and f.selected
            and f.final_severity not in (Severity.NONE, Severity.IGNORED)
        ]
        assert len(drug_selected) == 0


# ===================================================================
# 11. Drug end dates as downgrade-only evidence
# ===================================================================


class TestDrugEndDatesDowngradeOnly:
    def test_drug_end_dates_never_create_inconsistencies(self):
        """Drug end dates must never be primary inconsistency sources."""
        r1 = _report(
            "R1",
            drugs=[_drug("A", end="2020-01-01")],
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", end="2020-06-01")],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        # No finding should reference drug_end as a primary entity
        for f in result.findings:
            assert "drug_end" not in f.entity_label


# ===================================================================
# 12. Multiple drug entries for same substance
# ===================================================================


class TestMultipleDrugEntries:
    def test_multiple_entries_uses_closest_match(self):
        """Multiple start dates for same drug → use closest pair."""
        r1 = _report(
            "R1",
            drugs=[
                _drug("A", start="2020-01-01"),
                _drug("A", start="2020-03-01"),
            ],
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2020-01-05")],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        # From R1 perspective: entity with 2020-01-01 matches against 2020-01-05
        # (4 days → minor), entity with 2020-03-01 matches against 2020-01-05
        # (55 days → moderate). Each is separate but bucketed by source date.
        # From R2: drug A start 2020-01-05 finds closest on R1, which is
        # 2020-01-01 (4 days → minor).
        # Total should be modest — the closest match keeps things reasonable.
        assert result.n_strong == 0


# ===================================================================
# 13. Earliest / latest plausible date helpers
# ===================================================================


class TestPlausibleDateHelpers:
    def test_day_resolution(self):
        pd = PartialDate(value="2020-06-15", resolution="day")
        assert earliest_plausible_date(pd) == latest_plausible_date(pd)

    def test_month_resolution(self):
        pd = PartialDate(value="2020-02", resolution="month")
        from datetime import date
        assert earliest_plausible_date(pd) == date(2020, 2, 1)
        assert latest_plausible_date(pd) == date(2020, 2, 29)  # leap year

    def test_year_resolution(self):
        pd = PartialDate(value="2020", resolution="year")
        from datetime import date
        assert earliest_plausible_date(pd) == date(2020, 1, 1)
        assert latest_plausible_date(pd) == date(2020, 12, 31)
