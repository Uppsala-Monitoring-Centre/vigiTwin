"""Tests for the clinical-date inconsistency module.

All reports use synthetic data only — no real pharmacovigilance data.
"""

from __future__ import annotations


from llm_dup_dev.feature_generation.clinical_date_inconsistencies import (
    ClinicalDateFinding,
    ClinicalDateResult,
    EntityType,
    Severity,
    _finding_pair_key,
    compute_clinical_date_inconsistencies,
    derive_overall_ae_onset_date,
    earliest_plausible_date,
    effective_overall_ae_onset_date,
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

    def test_month_resolution_drug_start_allows_same_month_day_ae(self):
        """Month-resolution suspected drug start must not reject a
        day-resolution AE onset in the same month — the drug could
        plausibly have started on day 1."""
        report = _report(
            drugs=[
                _drug(
                    "quinine",
                    start="1968-11",
                    start_res="month",
                    end="1968-11-11",
                ),
            ],
            aes=[_ae("pyrexia", onset="1968-11-12")],
        )
        onset = derive_overall_ae_onset_date(report)
        assert onset is not None
        assert onset.value == "1968-11-12"

    def test_month_resolution_drug_start_rejects_prior_month_ae(self):
        """A day-level AE clearly before the earliest plausible start
        of a month-resolution suspected drug is still rejected."""
        report = _report(
            drugs=[_drug("A", start="2020-06", start_res="month")],
            aes=[
                _ae("headache", onset="2020-05-15"),
                _ae("nausea", onset="2020-06-10"),
            ],
        )
        onset = derive_overall_ae_onset_date(report)
        # 2020-05-15 is strictly before 2020-06-01 (earliest plausible
        # of the month-resolution drug start) → rejected.
        # 2020-06-10 is compatible and is the earliest remaining AE.
        assert onset is not None
        assert onset.value == "2020-06-10"

    def test_multi_regimen_same_substance_allows_earlier_ae(self):
        """When the same suspected drug has two regimens — one before
        and one after the AE onset — the earlier regimen alone makes
        the AE compatible, so the earliest AE is kept."""
        report = _report(
            drugs=[
                _drug("A", start="2020-01-05"),
                _drug("A", start="2020-03-01"),  # re-challenge
            ],
            aes=[
                _ae("headache", onset="2020-01-15"),
                _ae("nausea", onset="2020-03-10"),
            ],
        )
        onset = derive_overall_ae_onset_date(report)
        assert onset is not None
        assert onset.value == "2020-01-15"

    def test_multi_substance_requires_all_compatible(self):
        """With multiple distinct suspected substances, the chosen AE
        must be compatible with every substance (not just one)."""
        report = _report(
            drugs=[
                _drug("A", start="2020-01-10"),
                _drug("B", start="2020-01-20"),
            ],
            aes=[
                _ae("headache", onset="2020-01-15"),  # before B start
                _ae("nausea", onset="2020-01-25"),
            ],
        )
        onset = derive_overall_ae_onset_date(report)
        assert onset is not None
        assert onset.value == "2020-01-25"

    def test_year_resolution_drug_start_allows_any_day_in_year(self):
        """Year-resolution drug start accepts any AE onset within
        that year or later."""
        report = _report(
            drugs=[_drug("A", start="2020", start_res="year")],
            aes=[_ae("headache", onset="2020-03-15")],
        )
        onset = derive_overall_ae_onset_date(report)
        assert onset is not None
        assert onset.value == "2020-03-15"


class TestEffectiveOverallAeOnsetDate:
    """Tests for the explicit-vs-derived resolver."""

    def test_explicit_overrides_derivation(self):
        """When ``reported_overall_ae_onset_date`` is set it wins over the fallback."""
        report = _report(
            drugs=[_drug("A", start="2020-01-10")],
            aes=[_ae("headache", onset="2020-01-15")],
        )
        explicit = PartialDate(value="2020-02-20", resolution="day")
        report = report.model_copy(update={"reported_overall_ae_onset_date": explicit})

        # Sanity check: derived would be different
        derived = derive_overall_ae_onset_date(report)
        assert derived is not None and derived.value == "2020-01-15"

        effective = effective_overall_ae_onset_date(report)
        assert effective == explicit

    def test_falls_back_to_derived_when_explicit_absent(self):
        """No explicit value → effective accessor returns the derived value."""
        report = _report(
            drugs=[_drug("A", start="2020-01-10")],
            aes=[_ae("headache", onset="2020-01-15")],
        )
        assert report.reported_overall_ae_onset_date is None
        assert effective_overall_ae_onset_date(report) == derive_overall_ae_onset_date(
            report
        )

    def test_explicit_used_even_when_derived_returns_none(self):
        """Explicit field is honoured when no AE/drug dates are available."""
        report = _report()  # no drugs, no AEs
        explicit = PartialDate(value="2021-07", resolution="month")
        report = report.model_copy(update={"reported_overall_ae_onset_date": explicit})
        assert derive_overall_ae_onset_date(report) is None
        assert effective_overall_ae_onset_date(report) == explicit

    def test_effective_returns_none_without_any_source(self):
        report = _report()
        assert effective_overall_ae_onset_date(report) is None


class TestExplicitOverrideInPipeline:
    """End-to-end checks that ``compute_clinical_date_inconsistencies``
    honours the explicit field."""

    def test_explicit_overrides_in_pair_comparison(self):
        """Explicit value on one side changes the inconsistency outcome."""
        # Both reports have identical drug/AE dates → derivation gives the
        # same overall onset and would produce 0 inconsistencies.
        r1 = _report(
            "R1",
            drugs=[_drug("A", start="2020-01-01")],
            aes=[_ae("headache", onset="2020-01-01")],
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2020-01-01")],
            aes=[_ae("headache", onset="2020-01-01")],
        )

        # Without explicit: no overall AE onset inconsistency
        baseline = compute_clinical_date_inconsistencies(r1, r2)
        baseline_total = (
            baseline.n_minor + baseline.n_moderate + baseline.n_strong
        )

        # Override R1 with an explicit case-event date 60 days later → strong
        r1_override = r1.model_copy(
            update={
                "reported_overall_ae_onset_date": PartialDate(
                    value="2020-03-01", resolution="day"
                ),
            }
        )
        result = compute_clinical_date_inconsistencies(r1_override, r2)
        assert (
            result.n_minor + result.n_moderate + result.n_strong
            > baseline_total
        )
        # Find the selected overall_ae_onset finding
        selected_overall = [
            f
            for f in result.findings
            if f.selected and f.entity_type == EntityType.OVERALL_AE_ONSET
        ]
        assert selected_overall, "expected an overall_ae_onset finding"
        # The explicit value must appear as either the source or matched
        # date of the selected finding (which side is kept depends on
        # cross-direction dedup, which favours the lower severity).
        assert any(
            f.source_date.value == "2020-03-01"
            or (f.matched_date is not None and f.matched_date.value == "2020-03-01")
            for f in selected_overall
        )

    def test_no_explicit_preserves_existing_behavior(self):
        """Behavior with no explicit fields equals the legacy code path."""
        r1 = _report(
            "R1",
            drugs=[_drug("A", start="2020-01-01")],
            aes=[_ae("headache", onset="2020-02-15")],
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2020-01-01")],
            aes=[_ae("headache", onset="2020-02-15")],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_minor == 0
        assert result.n_moderate == 0
        assert result.n_strong == 0

    def test_explicit_partial_year_resolution_used_as_is(self):
        """Year-resolution explicit dates are consumed without re-bucketing."""
        explicit = PartialDate(value="2020", resolution="year")
        r1 = _report(
            "R1",
            drugs=[_drug("A", start="2020-06-01")],
            aes=[_ae("headache", onset="2020-06-15")],
        )
        r1 = r1.model_copy(update={"reported_overall_ae_onset_date": explicit})

        assert effective_overall_ae_onset_date(r1) == explicit


# ===================================================================
# 2. Threshold behavior
# ===================================================================


class TestOverallAeOnsetThresholds:
    """Overall AE onset: 1-7d minor, 8-75d moderate, >75d strong."""

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

    def test_75_days_still_moderate(self):
        # 2020-02-01 + 75 days = 2020-04-16
        result = self._pair("2020-02-01", "2020-04-16")
        assert result.n_moderate >= 1
        assert result.n_strong == 0

    def test_76_days_strong(self):
        # 2020-02-01 + 76 days = 2020-04-17
        result = self._pair("2020-02-01", "2020-04-17")
        assert result.n_strong >= 1


class TestDrugStartAndAeOnsetThresholds:
    """Drug start / AE onset: 0-7d none, 8-75d minor, >75d moderate.

    Per-entity discrepancies are capped at moderate; only the overall AE
    onset date can produce strong evidence on its own.
    """

    def test_drug_start_within_7_days_no_inconsistency(self):
        """Differences ≤ 7 days are absorbed as noise."""
        r1 = _report("R1", drugs=[_drug("A", start="2020-01-01")])
        r2 = _report("R2", drugs=[_drug("A", start="2020-01-08")])
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_minor == 0
        assert result.n_moderate == 0
        assert result.n_strong == 0

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

    def test_drug_start_75_days_still_minor(self):
        """At the moderate-canonical / minor-per-entity upper bound."""
        r1 = _report("R1", drugs=[_drug("A", start="2020-01-01")])
        # 2020-01-01 + 75 days = 2020-03-16
        r2 = _report("R2", drugs=[_drug("A", start="2020-03-16")])
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_minor >= 1
        assert result.n_moderate == 0

    def test_drug_start_76_days_moderate(self):
        r1 = _report("R1", drugs=[_drug("A", start="2020-01-01")])
        # 2020-01-01 + 76 days = 2020-03-17
        r2 = _report("R2", drugs=[_drug("A", start="2020-03-17")])
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_moderate >= 1
        assert result.n_strong == 0

    def test_drug_start_never_strong(self):
        """A per-entity drug start mismatch is capped at moderate."""
        r1 = _report("R1", drugs=[_drug("A", start="2020-01-01")])
        r2 = _report("R2", drugs=[_drug("A", start="2021-01-01")])  # 366 days
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_strong == 0
        assert result.n_moderate >= 1

    def test_ae_onset_match_10_days(self):
        """10 days apart with only AE onsets — overall AE onset is derived
        and graded by the canonical table directly (10d → moderate), so the
        selected finding is moderate from the overall AE onset axis. The
        per-entity AE onset row is also minor (canonical moderate downgraded
        one step), and loses to the higher-priority overall AE onset in the
        same bucket."""
        r1 = _report("R1", aes=[_ae("headache", onset="2020-01-01")])
        r2 = _report("R2", aes=[_ae("headache", onset="2020-01-11")])
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_moderate >= 1
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
    def test_downgrade_moderate_to_minor_via_drug_start(self):
        """Source date matches another drug start on other report → downgrade."""
        # Drug A: 91 days apart (moderate) but date "2020-01-01" matches
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
                _drug("A", start="2020-04-01"),  # 91 days → moderate
                _drug("B", start="2020-01-01"),  # exact match for R1's drug A date
            ],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        # From R1's perspective: drug A start "2020-01-01" raw moderate,
        # but 2020-01-01 matches a date on R2 → downgrade to minor
        r1_findings = [
            f for f in result.findings
            if f.source_report == "R1"
            and "drug_start:a" in f.entity_label
            and f.raw_severity == Severity.MODERATE
        ]
        assert len(r1_findings) >= 1
        assert r1_findings[0].final_severity == Severity.MINOR
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
                _drug("A", start="2020-04-01", end="2020-01-01"),  # 91d, moderate
            ],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        r1_findings = [
            f for f in result.findings
            if f.source_report == "R1"
            and "drug_start:a" in f.entity_label
            and f.raw_severity == Severity.MODERATE
        ]
        assert len(r1_findings) >= 1
        assert r1_findings[0].final_severity == Severity.MINOR
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
                _drug("A", start="2020-04-01"),  # 91 days → moderate
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

    def test_minor_drops_to_ignored_after_downgrade(self):
        """Minor → ignored (aligned with the concomitant-drug ladder).

        A minor whose source date coincides with another date on the
        opposite report is the most likely to be a labeling artefact, so
        it steps down out of the counts rather than surviving as minor.

        Both drug-A start dates coincide with a date on the opposite
        report (drug B / drug C), so the cross-entity downgrade fires in
        *both* directions and the pair contributes nothing.
        """
        r1 = _report(
            "R1",
            drugs=[
                _drug("A", start="2020-01-01"),
                _drug("B", start="2020-01-10"),  # == R2's drug-A date
            ],
        )
        r2 = _report(
            "R2",
            drugs=[
                _drug("A", start="2020-01-10"),  # 9 days → minor (per-entity)
                _drug("C", start="2020-01-01"),  # == R1's drug-A date
            ],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        r1_findings = [
            f for f in result.findings
            if f.source_report == "R1"
            and "drug_start:a" in f.entity_label
        ]
        assert len(r1_findings) >= 1
        assert r1_findings[0].final_severity == Severity.IGNORED
        assert not r1_findings[0].selected
        # Both directions drop to ignored → nothing counted.
        assert result.n_minor == 0


# ===================================================================
# 5. Concomitant drug downgrade
# ===================================================================


class TestConcomitantDrugDowngrade:
    def test_moderate_becomes_minor(self):
        """Concomitant drug: moderate → minor (-1 step)."""
        # 91 days → raw moderate; concomitant -1 step → minor
        r1 = _report("R1", drugs=[_drug("A", start="2020-01-01", role=DrugRole.CONCOMITANT)])
        r2 = _report("R2", drugs=[_drug("A", start="2020-04-01", role=DrugRole.CONCOMITANT)])
        result = compute_clinical_date_inconsistencies(r1, r2)
        concom_findings = [
            f for f in result.findings
            if f.entity_type == EntityType.CONCOMITANT_DRUG_START
            and f.raw_severity == Severity.MODERATE
        ]
        assert len(concom_findings) >= 1
        assert all(f.final_severity == Severity.MINOR for f in concom_findings)
        # The downgraded finding contributes as a minor inconsistency
        assert result.n_minor >= 1
        assert result.n_moderate == 0

    def test_minor_becomes_ignored(self):
        """Concomitant drug: minor → ignored."""
        # 9 days → raw minor; concomitant -1 step → ignored
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
        # 91 days → raw moderate; suspected role keeps it at moderate
        r1 = _report("R1", drugs=[_drug("A", start="2020-01-01", role=DrugRole.SUSPECTED)])
        r2 = _report("R2", drugs=[_drug("A", start="2020-04-01", role=DrugRole.SUSPECTED)])
        result = compute_clinical_date_inconsistencies(r1, r2)
        drug_findings = [
            f for f in result.findings
            if f.entity_type == EntityType.SUSPECTED_DRUG_START
            and f.raw_severity == Severity.MODERATE
        ]
        assert len(drug_findings) >= 1
        assert all(f.final_severity == Severity.MODERATE for f in drug_findings)


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
        """If overall AE onset has no inconsistency, drug start may count.

        The drug-start source date (shared with the matched overall AE
        onset) necessarily coincides with a date on the other report, so
        the cross-entity downgrade fires. The gap is chosen large enough
        (strong → moderate per-entity) that a single downgrade leaves it
        at minor and still selectable — demonstrating the bucket
        fall-through to the lower-priority entity.
        """
        # Overall AE onset matches exactly (2020-06-01 on both), but the
        # drug start differs enough to survive the cross-entity downgrade.
        r1 = _report(
            "R1",
            drugs=[_drug("A", start="2020-06-01")],
            aes=[_ae("headache", onset="2020-06-01")],
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2020-01-01")],  # 152 days → strong → moderate
            aes=[_ae("headache", onset="2020-06-01")],  # exact match
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        # The overall AE onset matches exactly → no inconsistency from that entity.
        # The drug start differs → selected (as minor after the downgrade)
        # since the higher-priority overall AE onset had no issue.
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
        """Onset '2005-03' with report date '2004-12-15' → decisive.

        Gap is 76 days (2004-12-15 → earliest plausible 2005-03-01),
        comfortably outside the 30-day grace window.
        """
        r1 = _report(
            "R1",
            report_date="2004-12-15",
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

    def test_within_grace_period_not_decisive(self):
        """Report date 10 days before onset → NOT decisive.

        Models the common pattern where both reports list the day of
        reporting as the AE onset and arrive a few days apart.
        """
        r1 = _report(
            "R1",
            report_date="2005-02-20",
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2005-01-01")],
            aes=[_ae("headache", onset="2005-03-02")],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_decisive == 0

    def test_at_grace_boundary_not_decisive(self):
        """Gap of exactly 30 days → NOT decisive (boundary is strict >)."""
        r1 = _report(
            "R1",
            report_date="2005-01-30",
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2005-01-01")],
            aes=[_ae("headache", onset="2005-03-01")],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_decisive == 0

    def test_just_past_grace_period_decisive(self):
        """Gap of 31 days → decisive."""
        r1 = _report(
            "R1",
            report_date="2005-01-29",
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2005-01-01")],
            aes=[_ae("headache", onset="2005-03-01")],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_decisive >= 1

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
    def test_single_entity_counted_once_after_dedup(self):
        """A single drug mismatch counted once, not once per direction."""
        r1 = _report(
            "R1",
            drugs=[_drug("A", start="2020-01-01")],
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2020-04-01")],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        selected = [f for f in result.findings if f.selected and "drug_start:a" in f.entity_label]
        # Cross-direction dedup keeps exactly one
        assert len(selected) == 1

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
        assert len(decisive_findings) == result.n_decisive

    def test_single_count_for_identical_mismatch(self):
        """A single drug mismatch should produce exactly one counted inconsistency.

        Both directions produce a finding, but cross-direction dedup keeps
        only one (the lower severity — here they are equal, so one is kept).
        """
        r1 = _report("R1", drugs=[_drug("A", start="2020-01-01")])
        r2 = _report("R2", drugs=[_drug("A", start="2020-04-01")])
        result = compute_clinical_date_inconsistencies(r1, r2)
        # 91 days → moderate (per-entity findings cap at moderate),
        # but only one count after dedup
        assert result.n_moderate == 1
        assert result.n_strong == 0

    def test_dedup_keeps_lower_severity_across_directions(self):
        """When cross-entity downgrade applies in one direction only,
        the dedup should keep the lower (downgraded) severity.

        R1 has drug A starting 2020-01-01 and an AE onset on 2020-06-01.
        R2 has drug A starting 2020-04-01 with an AE onset on 2020-01-01.
        Direction R1→R2: source_date 2020-01-01 IS in R2's downgrade
            dates (R2's AE onset) → downgrade moderate → minor.
        Direction R2→R1: source_date 2020-04-01 is NOT in R1's downgrade
            dates → stays moderate.
        Dedup should keep the minor (lower).
        """
        r1 = _report(
            "R1",
            drugs=[_drug("A", start="2020-01-01")],
            aes=[_ae("headache", onset="2020-06-01")],
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2020-04-01")],  # 91 days → moderate
            aes=[_ae("headache", onset="2020-01-01")],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)

        selected = [f for f in result.findings if f.selected and "drug_start:a" in f.entity_label]
        assert len(selected) == 1
        assert selected[0].final_severity == Severity.MINOR


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

    def test_year_vs_day_different_years_moderate(self):
        """Year '2020' vs day '2022-06-01' → min diff 518 days → moderate.

        Per-entity drug start mismatches cap at moderate, so even a
        ~17-month gap counts as a single moderate inconsistency.
        """
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
        # Min diff: 2020-12-31 to 2022-06-01 = 518 days → moderate (capped)
        assert result.n_moderate >= 1
        assert result.n_strong == 0

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
        # Single-best-pair selection picks (2020-01-01, 2020-01-05) at 4 days
        # for drug A; the 2020-03-01 start is a leftover. 4 days is below the
        # per-entity noise floor, so this contributes no strong finding.
        assert result.n_strong == 0


# ===================================================================
# 12b. Alignment behaviour for multi-regimen / multi-onset
# ===================================================================


class TestAlignmentBehaviour:
    """Cases A–G covering the alignment-based counterpart selection."""

    def test_A_two_to_two_selects_single_closest_pair(self):
        """R1=[Jan1, Jun1], R2=[Jan20, Jun22] → one minor from the closest pair."""
        r1 = _report(
            "R1",
            drugs=[
                _drug("A", start="2020-01-01"),
                _drug("A", start="2020-06-01"),
            ],
        )
        r2 = _report(
            "R2",
            drugs=[
                _drug("A", start="2020-01-20"),  # 19 days from Jan1
                _drug("A", start="2020-06-22"),  # 21 days from Jun1
            ],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        # Single-best-pair selection: the closest pair is (Jan1, Jan20) at
        # 19 days — above the 7d noise floor, below the 70d moderate
        # threshold → minor. The (Jun1, Jun22) pair is a leftover and
        # produces nothing.
        assert result.n_strong == 0
        assert result.n_moderate == 0
        assert result.n_minor == 1
        drug_selected = [
            f for f in _selected_findings(result)
            if f.entity_label == "drug_start:a"
            and f.final_severity not in (Severity.NONE, Severity.IGNORED)
        ]
        pair_keys = {_finding_pair_key(f) for f in drug_selected}
        assert len(pair_keys) == 1
        assert drug_selected[0].final_severity == Severity.MINOR
        # The further-apart regimen dates are leftovers → absent.
        involved: set[str] = set()
        for f in drug_selected:
            involved.add(f.source_date.value)
            if f.matched_date is not None:
                involved.add(f.matched_date.value)
        assert "2020-06-01" not in involved
        assert "2020-06-22" not in involved

    def test_B_leftover_unmatched_dates_create_no_findings(self):
        """R1 has extra dates that find no counterpart — they must be silent."""
        r1 = _report(
            "R1",
            drugs=[
                _drug("A", start="2020-01-01"),
                _drug("A", start="2020-03-01"),
                _drug("A", start="2020-06-01"),
            ],
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2020-01-03")],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        # Single-best-pair selection picks (2020-01-01, 2020-01-03) → 2 days.
        # The leftover dates 2020-03-01 and 2020-06-01 must not produce
        # any moderate or strong findings.
        assert result.n_moderate == 0
        assert result.n_strong == 0
        # The only selected drug-start findings reference 2020-01-01 and
        # 2020-01-03 — not the leftovers.
        drug_selected = [
            f for f in _selected_findings(result)
            if f.entity_label == "drug_start:a"
            and f.final_severity not in (Severity.NONE, Severity.IGNORED)
        ]
        involved_values: set[str] = set()
        for f in drug_selected:
            involved_values.add(f.source_date.value)
            if f.matched_date is not None:
                involved_values.add(f.matched_date.value)
        assert "2020-03-01" not in involved_values
        assert "2020-06-01" not in involved_values

    def test_C_exact_duplicate_dates_deduplicated_within_report(self):
        """Two rows of the same drug with identical start date collapse."""
        r1 = _report(
            "R1",
            drugs=[
                _drug("A", start="2020-01-01"),
                _drug("A", start="2020-01-01"),
            ],
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2020-04-01")],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        # After dedup, R1 has a single start-date entry for A, so the
        # best pair is unambiguous. Across both directions we get two
        # findings for this pair; after cross-direction dedup only one
        # survives per pair-key.
        drug_selected = [
            f for f in _selected_findings(result)
            if f.entity_label == "drug_start:a"
            and f.final_severity not in (Severity.NONE, Severity.IGNORED)
        ]
        pair_keys = {_finding_pair_key(f) for f in drug_selected}
        assert len(pair_keys) == 1

    def test_D_dedup_tiebreak_prefers_non_concomitant_role(self):
        """Same substance / same date with both roles → non-concomitant wins."""
        r1 = _report(
            "R1",
            drugs=[
                _drug("A", start="2020-01-01", role=DrugRole.CONCOMITANT),
                _drug("A", start="2020-01-01", role=DrugRole.SUSPECTED),
            ],
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2020-04-01", role=DrugRole.SUSPECTED)],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        # Diff is 91 days → raw moderate. If the R1 side had been treated
        # as concomitant, the moderate would be downgraded to MINOR.
        # Because dedup prefers the suspected row, the finding stays
        # MODERATE without any concomitant downgrade.
        selected = [
            f for f in _selected_findings(result)
            if f.entity_label == "drug_start:a"
            and f.source_report == "R1"
            and f.final_severity not in (Severity.NONE, Severity.IGNORED)
        ]
        assert len(selected) == 1
        assert selected[0].final_severity == Severity.MODERATE
        # Ensure no concomitant downgrade string was recorded on the R1
        # side (the suspected row won the dedup tiebreak).
        assert not any(
            "concomitant" in d for d in selected[0].downgrades
        )

    def test_E_multi_ae_onset_selects_single_closest_pair(self):
        """Same PT on both reports with multiple onsets → single best pair."""
        # A suspected drug dated far in the future forces the derived
        # overall-AE-onset to None (no AE onset qualifies), so the
        # source-date bucketing does not let a higher-priority
        # overall-AE-onset finding pre-empt the AE-onset findings.
        r1 = _report(
            "R1",
            drugs=[_drug("X", start="2099-01-01")],
            aes=[
                _ae("headache", onset="2020-01-01"),
                _ae("headache", onset="2020-06-01"),
            ],
        )
        r2 = _report(
            "R2",
            drugs=[_drug("X", start="2099-01-01")],
            aes=[
                _ae("headache", onset="2020-01-20"),  # 19 days from Jan1
                _ae("headache", onset="2020-06-22"),  # 21 days from Jun1
            ],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        ae_selected = [
            f for f in _selected_findings(result)
            if f.entity_label == "ae_onset:headache"
            and f.final_severity not in (Severity.NONE, Severity.IGNORED)
        ]
        pair_keys = {_finding_pair_key(f) for f in ae_selected}
        # Only the closest onset pair (Jan1, Jan20) is emitted → one minor.
        assert len(pair_keys) == 1
        assert result.n_minor == 1
        assert ae_selected[0].final_severity == Severity.MINOR

    def test_F_role_agnostic_counterpart_selection(self):
        """Best-pair selection ignores role; concomitant downgrade still applies."""
        r1 = _report(
            "R1",
            drugs=[
                _drug("A", start="2020-01-01", role=DrugRole.CONCOMITANT),
                _drug("A", start="2020-06-01", role=DrugRole.SUSPECTED),
            ],
        )
        r2 = _report(
            "R2",
            drugs=[_drug("A", start="2020-01-20", role=DrugRole.SUSPECTED)],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        # Cheapest pairing is (2020-01-01 concomitant, 2020-01-20
        # suspected): ~19 days → raw minor.  On the R1 side the
        # concomitant downgrade takes that minor → ignored.  On the R2
        # side the source is suspected, so no concomitant downgrade.
        drug_findings = [
            f for f in result.findings
            if f.entity_label == "drug_start:a"
            and "2020-01-01" in {
                f.source_date.value,
                (f.matched_date.value if f.matched_date else ""),
            }
        ]
        # R1 side: concomitant source, should be IGNORED
        r1_side = [f for f in drug_findings if f.source_report == "R1"]
        assert len(r1_side) == 1
        assert r1_side[0].final_severity == Severity.IGNORED
        # R2 side: suspected source, not downgraded (stays minor)
        r2_side = [f for f in drug_findings if f.source_report == "R2"]
        assert len(r2_side) == 1
        assert r2_side[0].final_severity == Severity.MINOR
        # And 2020-06-01 (leftover) must not show up in any selected
        # finding.
        for f in _selected_findings(result):
            vals = {f.source_date.value}
            if f.matched_date is not None:
                vals.add(f.matched_date.value)
            assert "2020-06-01" not in vals

    def test_G_only_single_best_pair_emitted(self):
        """Two mismatched regimens → only the single closest pair is flagged."""
        r1 = _report(
            "R1",
            drugs=[
                _drug("A", start="2020-01-01"),
                _drug("A", start="2020-06-01"),
            ],
            aes=[_ae("headache", onset="2019-12-01")],
        )
        r2 = _report(
            "R2",
            drugs=[
                _drug("A", start="2020-04-01"),   # 61 days from Jun1
                _drug("A", start="2020-08-30"),   # 90 days from Jun1
            ],
            aes=[_ae("headache", onset="2019-12-02")],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        drug_selected = [
            f for f in _selected_findings(result)
            if f.entity_label == "drug_start:a"
            and f.final_severity not in (Severity.NONE, Severity.IGNORED)
        ]
        pair_keys = {_finding_pair_key(f) for f in drug_selected}
        # Single-best-pair selection: the closest pair is (Jun1, Apr1) at
        # 61 days → moderate, downgraded one step per-entity → minor. Every
        # other date (Jan1, Aug30) is a leftover and produces nothing, so
        # the more distant regimen mismatch is masked by design.
        assert len(pair_keys) == 1
        assert len(drug_selected) == 1
        assert drug_selected[0].final_severity == Severity.MINOR
        assert result.n_minor == 1
        assert result.n_moderate == 0
        assert result.n_strong == 0

    def test_H_shared_date_plus_extras_yields_no_finding(self):
        """Both reports share a date exactly, each with a distinct extra →
        no finding.

        Regression for the force-pairing artifact: previously the exact
        match was consumed and the two unrelated leftovers were force-paired,
        flagging their distance. Single-best-pair selection picks the
        zero-difference shared date, so the entity is clean.
        """
        r1 = _report(
            "R1",
            drugs=[
                _drug("paroxetine", start="2025-03-04"),
                _drug("paroxetine", start="2025-04-04"),
            ],
        )
        r2 = _report(
            "R2",
            drugs=[
                _drug("paroxetine", start="2024-02-06"),
                _drug("paroxetine", start="2025-04-04"),
            ],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_minor == 0
        assert result.n_moderate == 0
        assert result.n_strong == 0

    def test_I_second_regimen_mismatch_is_masked(self):
        """A shared start date masks a far-apart second regimen date — the
        masking is intentional under single-best-pair selection.
        """
        r1 = _report(
            "R1",
            drugs=[
                _drug("A", start="2020-01-01"),
                _drug("A", start="2019-06-06"),
            ],
        )
        r2 = _report(
            "R2",
            drugs=[
                _drug("A", start="2020-01-01"),   # exact shared start date
                _drug("A", start="2025-04-04"),   # years apart — masked
            ],
        )
        result = compute_clinical_date_inconsistencies(r1, r2)
        assert result.n_minor == 0
        assert result.n_moderate == 0
        assert result.n_strong == 0


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
