"""Tests for feature matching between report pairs."""

import pytest

from llm_dup_dev.models.report import (
    AdverseEvent,
    DateValue,
    Drug,
    PartialDate,
    Report,
    SourcedValue,
)
from llm_dup_dev.feature_generation.matching import (
    _compare_partial_dates,
    compute_matching_aes,
    compute_matching_clinical_dates,
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

    def test_clinical_dates_populated_in_match_result(self):
        """compute_matches should populate matched_clinical_dates."""
        r1 = Report(
            report_id="1",
            drugs=[
                Drug(
                    substance="aspirin",
                    sources=["s"],
                    start_date=PartialDate(value="2020-06-01", resolution="day"),
                ),
            ],
            adverse_events=[],
        )
        r2 = Report(
            report_id="2",
            drugs=[
                Drug(
                    substance="aspirin",
                    sources=["s"],
                    start_date=PartialDate(value="2020-06-01", resolution="day"),
                ),
            ],
            adverse_events=[],
        )
        result = compute_matches(r1, r2)
        assert len(result.matched_clinical_dates) == 1
        assert result.matched_clinical_dates[0].matched_value == "2020-06-01"
        assert result.matched_clinical_dates[0].resolution == "day"


# ---------------------------------------------------------------------------
# Partial date comparison helper
# ---------------------------------------------------------------------------


class TestComparePartialDates:
    def test_both_none(self):
        assert _compare_partial_dates(None, None) is None

    def test_one_none(self):
        d = PartialDate(value="2020-06-12", resolution="day")
        assert _compare_partial_dates(d, None) is None
        assert _compare_partial_dates(None, d) is None

    def test_same_day(self):
        d1 = PartialDate(value="2020-06-12", resolution="day")
        d2 = PartialDate(value="2020-06-12", resolution="day")
        result = _compare_partial_dates(d1, d2)
        assert result == ("2020-06-12", "day")

    def test_different_days(self):
        d1 = PartialDate(value="2020-06-12", resolution="day")
        d2 = PartialDate(value="2020-06-15", resolution="day")
        assert _compare_partial_dates(d1, d2) is None

    def test_day_vs_month_match(self):
        """Day vs month: should match at month resolution."""
        d1 = PartialDate(value="2020-06-12", resolution="day")
        d2 = PartialDate(value="2020-06", resolution="month")
        result = _compare_partial_dates(d1, d2)
        assert result == ("2020-06", "month")

    def test_day_vs_month_no_match(self):
        d1 = PartialDate(value="2020-06-12", resolution="day")
        d2 = PartialDate(value="2020-07", resolution="month")
        assert _compare_partial_dates(d1, d2) is None

    def test_month_vs_year_match(self):
        d1 = PartialDate(value="2020-06", resolution="month")
        d2 = PartialDate(value="2020", resolution="year")
        result = _compare_partial_dates(d1, d2)
        assert result == ("2020", "year")

    def test_day_vs_year_match(self):
        d1 = PartialDate(value="2020-06-12", resolution="day")
        d2 = PartialDate(value="2020", resolution="year")
        result = _compare_partial_dates(d1, d2)
        assert result == ("2020", "year")


# ---------------------------------------------------------------------------
# Clinical date matching
# ---------------------------------------------------------------------------


def _make_report(report_id, drugs=None, adverse_events=None,
                  reported_overall_ae_onset_date=None):
    """Helper to build minimal reports for clinical date tests."""
    return Report(
        report_id=report_id,
        drugs=drugs or [],
        adverse_events=adverse_events or [],
        reported_overall_ae_onset_date=reported_overall_ae_onset_date,
    )


class TestClinicalDateMatching:
    """Tests for compute_matching_clinical_dates.

    All tests verify explicit date-field coincidence logic — NOT
    interval overlap.
    """

    # -- Same-drug start-date matches --

    def test_same_drug_start_date_match(self):
        """Matching drug with same start date should produce one match."""
        r1 = _make_report("1", drugs=[
            Drug(substance="aspirin", sources=["s"],
                 start_date=PartialDate(value="1994-03-30", resolution="day")),
        ])
        r2 = _make_report("2", drugs=[
            Drug(substance="aspirin", sources=["s"],
                 start_date=PartialDate(value="1994-03-30", resolution="day")),
        ])
        result = compute_matching_clinical_dates(r1, r2, ["aspirin"], [])
        assert len(result) == 1
        assert result[0].matched_value == "1994-03-30"
        assert result[0].resolution == "day"

    def test_same_drug_start_date_no_match(self):
        """Different start dates should not match."""
        r1 = _make_report("1", drugs=[
            Drug(substance="aspirin", sources=["s"],
                 start_date=PartialDate(value="1994-03-30", resolution="day")),
        ])
        r2 = _make_report("2", drugs=[
            Drug(substance="aspirin", sources=["s"],
                 start_date=PartialDate(value="1994-05-12", resolution="day")),
        ])
        result = compute_matching_clinical_dates(r1, r2, ["aspirin"], [])
        assert len(result) == 0

    def test_start_date_missing_on_one_side(self):
        """Missing start date on one report → no match for that field."""
        r1 = _make_report("1", drugs=[
            Drug(substance="aspirin", sources=["s"],
                 start_date=PartialDate(value="1994-03-30", resolution="day")),
        ])
        r2 = _make_report("2", drugs=[
            Drug(substance="aspirin", sources=["s"]),  # no start_date
        ])
        result = compute_matching_clinical_dates(r1, r2, ["aspirin"], [])
        assert len(result) == 0

    def test_duplicate_substance_across_roles_matches_dates(self):
        """Same substance listed under multiple roles must still match dates.

        Regression: the lookup used to be a dict keyed by substance, so when
        a report listed the same drug twice (e.g. once as ``concomitant``
        with dates and once as ``suspected`` without dates), the dateless
        entry could shadow the dateful one and suppress a genuine match.
        The drug set intersection should be role-agnostic, and so should
        the date coincidence check.
        """
        r1 = _make_report("1", drugs=[
            # Concomitant entry carries the dates.
            Drug(
                substance="casirivimab;imdevimab",
                sources=["structured"],
                role="concomitant",
                start_date=PartialDate(value="2021-09-24", resolution="day"),
                end_date=PartialDate(value="2021-09-24", resolution="day"),
            ),
            # Suspected entry has no dates — must not mask the above.
            Drug(
                substance="casirivimab;imdevimab",
                sources=["structured"],
                role="suspected",
            ),
        ])
        r2 = _make_report("2", drugs=[
            Drug(
                substance="casirivimab;imdevimab",
                sources=["structured"],
                role="suspected",
                start_date=PartialDate(value="2021-09-24", resolution="day"),
                end_date=PartialDate(value="2021-09-24", resolution="day"),
            ),
        ])
        result = compute_matching_clinical_dates(
            r1, r2, ["casirivimab;imdevimab"], []
        )
        assert len(result) == 1
        assert result[0].matched_value == "2021-09-24"
        assert result[0].resolution == "day"

    # -- Same-drug stop/end-date matches --

    def test_same_drug_end_date_match(self):
        """Matching drug with same end date should produce one match."""
        r1 = _make_report("1", drugs=[
            Drug(substance="aspirin", sources=["s"],
                 end_date=PartialDate(value="1994-05-12", resolution="day")),
        ])
        r2 = _make_report("2", drugs=[
            Drug(substance="aspirin", sources=["s"],
                 end_date=PartialDate(value="1994-05-12", resolution="day")),
        ])
        result = compute_matching_clinical_dates(r1, r2, ["aspirin"], [])
        assert len(result) == 1
        assert result[0].matched_value == "1994-05-12"
        assert result[0].resolution == "day"

    # -- Same-AE onset-date matches --

    def test_same_ae_onset_date_match(self):
        """Matching AE with same onset date should produce one match."""
        r1 = _make_report("1", adverse_events=[
            AdverseEvent(pt_name="arrhythmia", sources=["s"],
                         start_date=PartialDate(value="1994-05-12", resolution="day")),
        ])
        r2 = _make_report("2", adverse_events=[
            AdverseEvent(pt_name="arrhythmia", sources=["s"],
                         start_date=PartialDate(value="1994-05-12", resolution="day")),
        ])
        result = compute_matching_clinical_dates(r1, r2, [], ["arrhythmia"])
        assert len(result) == 1
        assert result[0].matched_value == "1994-05-12"
        assert result[0].resolution == "day"

    def test_ae_onset_missing_on_one_side(self):
        r1 = _make_report("1", adverse_events=[
            AdverseEvent(pt_name="arrhythmia", sources=["s"],
                         start_date=PartialDate(value="1994-05-12", resolution="day")),
        ])
        r2 = _make_report("2", adverse_events=[
            AdverseEvent(pt_name="arrhythmia", sources=["s"]),  # no onset
        ])
        result = compute_matching_clinical_dates(r1, r2, [], ["arrhythmia"])
        assert len(result) == 0

    # -- Cross-field non-matches (start vs stop must NOT count) --

    def test_cross_field_start_vs_stop_not_counted(self):
        """A drug's start date on one report matching its stop date on the
        other should NOT count — only same-field comparisons are valid.

        This is the mitoxantrone anti-example from the spec:
          R1: start=1994-03-30, stop=1994-05-12
          R2: start=1994-05-12, stop=missing
        The start(R2)=stop(R1) coincidence must NOT be counted.
        """
        r1 = _make_report("1", drugs=[
            Drug(substance="mitoxantrone", sources=["s"],
                 start_date=PartialDate(value="1994-03-30", resolution="day"),
                 end_date=PartialDate(value="1994-05-12", resolution="day")),
        ])
        r2 = _make_report("2", drugs=[
            Drug(substance="mitoxantrone", sources=["s"],
                 start_date=PartialDate(value="1994-05-12", resolution="day")),
        ])
        result = compute_matching_clinical_dates(r1, r2, ["mitoxantrone"], [])
        # start↔start: 1994-03-30 ≠ 1994-05-12 → no match
        # end↔end: end missing on R2 → no match
        # start↔end cross-field: NOT compared
        assert len(result) == 0

    # -- Interval-overlap non-matches --

    def test_interval_overlap_not_counted(self):
        """Overlapping treatment intervals should NOT count as date matches.

        Drug exposure periods are NOT expanded into sets of intermediate dates.
        Only explicitly recorded start/end values are compared.
        """
        r1 = _make_report("1", drugs=[
            Drug(substance="drugA", sources=["s"],
                 start_date=PartialDate(value="2020-01-01", resolution="day"),
                 end_date=PartialDate(value="2020-06-30", resolution="day")),
        ])
        r2 = _make_report("2", drugs=[
            Drug(substance="drugA", sources=["s"],
                 start_date=PartialDate(value="2020-03-15", resolution="day"),
                 end_date=PartialDate(value="2020-09-30", resolution="day")),
        ])
        # The treatment periods 2020-01-01..2020-06-30 and 2020-03-15..2020-09-30
        # overlap, but no explicit field value matches:
        #   start↔start: 2020-01-01 ≠ 2020-03-15
        #   end↔end: 2020-06-30 ≠ 2020-09-30
        result = compute_matching_clinical_dates(r1, r2, ["drugA"], [])
        assert len(result) == 0

    # -- Deduplication --

    def test_same_date_across_drug_and_ae_counted_once(self):
        """If the same calendar date matches for a drug start AND an AE onset,
        it should contribute only once to the unique matched dates."""
        r1 = _make_report("1",
            drugs=[
                Drug(substance="aspirin", sources=["s"],
                     start_date=PartialDate(value="1994-05-12", resolution="day")),
            ],
            adverse_events=[
                AdverseEvent(pt_name="rash", sources=["s"],
                             start_date=PartialDate(value="1994-05-12", resolution="day")),
            ],
        )
        r2 = _make_report("2",
            drugs=[
                Drug(substance="aspirin", sources=["s"],
                     start_date=PartialDate(value="1994-05-12", resolution="day")),
            ],
            adverse_events=[
                AdverseEvent(pt_name="rash", sources=["s"],
                             start_date=PartialDate(value="1994-05-12", resolution="day")),
            ],
        )
        result = compute_matching_clinical_dates(
            r1, r2, ["aspirin"], ["rash"]
        )
        assert len(result) == 1
        assert result[0].matched_value == "1994-05-12"

    def test_same_date_across_two_drugs_counted_once(self):
        """Same start date for two different matched drugs → counted once."""
        r1 = _make_report("1", drugs=[
            Drug(substance="drugA", sources=["s"],
                 start_date=PartialDate(value="2020-01-15", resolution="day")),
            Drug(substance="drugB", sources=["s"],
                 start_date=PartialDate(value="2020-01-15", resolution="day")),
        ])
        r2 = _make_report("2", drugs=[
            Drug(substance="drugA", sources=["s"],
                 start_date=PartialDate(value="2020-01-15", resolution="day")),
            Drug(substance="drugB", sources=["s"],
                 start_date=PartialDate(value="2020-01-15", resolution="day")),
        ])
        result = compute_matching_clinical_dates(
            r1, r2, ["drugA", "drugB"], []
        )
        assert len(result) == 1

    def test_same_date_start_and_end_counted_once(self):
        """Same date as drug start AND end for same drug → counted once."""
        r1 = _make_report("1", drugs=[
            Drug(substance="drugA", sources=["s"],
                 start_date=PartialDate(value="2020-01-15", resolution="day"),
                 end_date=PartialDate(value="2020-01-15", resolution="day")),
        ])
        r2 = _make_report("2", drugs=[
            Drug(substance="drugA", sources=["s"],
                 start_date=PartialDate(value="2020-01-15", resolution="day"),
                 end_date=PartialDate(value="2020-01-15", resolution="day")),
        ])
        result = compute_matching_clinical_dates(r1, r2, ["drugA"], [])
        assert len(result) == 1

    def test_coarser_match_absorbed_by_finer_same_drug(self):
        """Month-only match on one side that sits inside a finer day match
        must not contribute a second reward — the coarser "2021-11" is just
        the month-truncated view of the day "2021-11-08"."""
        r1 = _make_report("1", drugs=[
            Drug(substance="drugA", sources=["s"],
                 start_date=PartialDate(value="2021-11-08", resolution="day"),
                 end_date=PartialDate(value="2021-11", resolution="month")),
        ])
        r2 = _make_report("2", drugs=[
            Drug(substance="drugA", sources=["s"],
                 start_date=PartialDate(value="2021-11-08", resolution="day"),
                 end_date=PartialDate(value="2021-11-08", resolution="day")),
        ])
        result = compute_matching_clinical_dates(r1, r2, ["drugA"], [])
        # start↔start matches as day "2021-11-08".
        # end↔end matches as month "2021-11" (coarser of month/day), which is
        # subsumed by the day match above — should not count again.
        assert len(result) == 1
        assert result[0].matched_value == "2021-11-08"
        assert result[0].resolution == "day"

    def test_year_match_absorbed_by_finer_day_match_same_drug(self):
        """User's reported scenario: a year-only match (2000) on top of a
        day match (2000-04-08) should collapse to the day match only."""
        r1 = _make_report("1", drugs=[
            Drug(substance="drugA", sources=["s"],
                 start_date=PartialDate(value="2000-04-08", resolution="day"),
                 end_date=PartialDate(value="2000", resolution="year")),
        ])
        r2 = _make_report("2", drugs=[
            Drug(substance="drugA", sources=["s"],
                 start_date=PartialDate(value="2000-04-08", resolution="day"),
                 end_date=PartialDate(value="2000", resolution="year")),
        ])
        result = compute_matching_clinical_dates(r1, r2, ["drugA"], [])
        assert len(result) == 1
        assert result[0].matched_value == "2000-04-08"
        assert result[0].resolution == "day"

    def test_coarser_match_absorbed_across_entities(self):
        """Subsumption works across different matched entities: a year-only
        match on drugB gets absorbed by a day match on drugA within the same
        year."""
        r1 = _make_report("1", drugs=[
            Drug(substance="drugA", sources=["s"],
                 start_date=PartialDate(value="2021-11-08", resolution="day")),
            Drug(substance="drugB", sources=["s"],
                 start_date=PartialDate(value="2021", resolution="year")),
        ])
        r2 = _make_report("2", drugs=[
            Drug(substance="drugA", sources=["s"],
                 start_date=PartialDate(value="2021-11-08", resolution="day")),
            Drug(substance="drugB", sources=["s"],
                 start_date=PartialDate(value="2021", resolution="year")),
        ])
        result = compute_matching_clinical_dates(
            r1, r2, ["drugA", "drugB"], []
        )
        assert len(result) == 1
        assert result[0].matched_value == "2021-11-08"
        assert result[0].resolution == "day"

    def test_coarser_match_kept_when_not_subsumed(self):
        """A year-only match in a different calendar year from any finer
        match must still count independently."""
        r1 = _make_report("1", drugs=[
            Drug(substance="drugA", sources=["s"],
                 start_date=PartialDate(value="2021-11-08", resolution="day")),
            Drug(substance="drugB", sources=["s"],
                 start_date=PartialDate(value="2022", resolution="year")),
        ])
        r2 = _make_report("2", drugs=[
            Drug(substance="drugA", sources=["s"],
                 start_date=PartialDate(value="2021-11-08", resolution="day")),
            Drug(substance="drugB", sources=["s"],
                 start_date=PartialDate(value="2022", resolution="year")),
        ])
        result = compute_matching_clinical_dates(
            r1, r2, ["drugA", "drugB"], []
        )
        assert len(result) == 2
        by_res = {cd.resolution: cd.matched_value for cd in result}
        assert by_res == {"day": "2021-11-08", "year": "2022"}

    def test_three_way_subsumption_collapses_to_finest(self):
        """Year, month, and day matches for the same underlying calendar
        date should all collapse to the single day entry."""
        r1 = _make_report("1", drugs=[
            Drug(substance="drugA", sources=["s"],
                 start_date=PartialDate(value="2021-11-08", resolution="day")),
            Drug(substance="drugB", sources=["s"],
                 start_date=PartialDate(value="2021-11", resolution="month")),
            Drug(substance="drugC", sources=["s"],
                 start_date=PartialDate(value="2021", resolution="year")),
        ])
        r2 = _make_report("2", drugs=[
            Drug(substance="drugA", sources=["s"],
                 start_date=PartialDate(value="2021-11-08", resolution="day")),
            Drug(substance="drugB", sources=["s"],
                 start_date=PartialDate(value="2021-11", resolution="month")),
            Drug(substance="drugC", sources=["s"],
                 start_date=PartialDate(value="2021", resolution="year")),
        ])
        result = compute_matching_clinical_dates(
            r1, r2, ["drugA", "drugB", "drugC"], []
        )
        assert len(result) == 1
        assert result[0].matched_value == "2021-11-08"
        assert result[0].resolution == "day"

    # -- Partial-date behavior --

    def test_partial_date_month_match(self):
        """Month-resolution dates should match."""
        r1 = _make_report("1", drugs=[
            Drug(substance="drugA", sources=["s"],
                 start_date=PartialDate(value="2020-06", resolution="month")),
        ])
        r2 = _make_report("2", drugs=[
            Drug(substance="drugA", sources=["s"],
                 start_date=PartialDate(value="2020-06", resolution="month")),
        ])
        result = compute_matching_clinical_dates(r1, r2, ["drugA"], [])
        assert len(result) == 1
        assert result[0].matched_value == "2020-06"
        assert result[0].resolution == "month"

    def test_partial_date_day_vs_month_match(self):
        """Day vs month resolution should match at month level."""
        r1 = _make_report("1", drugs=[
            Drug(substance="drugA", sources=["s"],
                 start_date=PartialDate(value="2020-06-15", resolution="day")),
        ])
        r2 = _make_report("2", drugs=[
            Drug(substance="drugA", sources=["s"],
                 start_date=PartialDate(value="2020-06", resolution="month")),
        ])
        result = compute_matching_clinical_dates(r1, r2, ["drugA"], [])
        assert len(result) == 1
        assert result[0].matched_value == "2020-06"
        assert result[0].resolution == "month"

    def test_partial_date_year_match(self):
        """Year-resolution dates should match."""
        r1 = _make_report("1", drugs=[
            Drug(substance="drugA", sources=["s"],
                 start_date=PartialDate(value="2020", resolution="year")),
        ])
        r2 = _make_report("2", drugs=[
            Drug(substance="drugA", sources=["s"],
                 start_date=PartialDate(value="2020", resolution="year")),
        ])
        result = compute_matching_clinical_dates(r1, r2, ["drugA"], [])
        assert len(result) == 1
        assert result[0].matched_value == "2020"
        assert result[0].resolution == "year"

    # -- Unmatched entities excluded --

    def test_unmatched_drug_dates_not_included(self):
        """Dates from drugs NOT in matching_drugs should be ignored."""
        r1 = _make_report("1", drugs=[
            Drug(substance="aspirin", sources=["s"],
                 start_date=PartialDate(value="2020-06-01", resolution="day")),
            Drug(substance="ibuprofen", sources=["s"],
                 start_date=PartialDate(value="2020-06-01", resolution="day")),
        ])
        r2 = _make_report("2", drugs=[
            Drug(substance="aspirin", sources=["s"],
                 start_date=PartialDate(value="2020-06-01", resolution="day")),
            Drug(substance="ibuprofen", sources=["s"],
                 start_date=PartialDate(value="2020-06-01", resolution="day")),
        ])
        # Only aspirin is in matching_drugs — ibuprofen dates are ignored
        result = compute_matching_clinical_dates(r1, r2, ["aspirin"], [])
        assert len(result) == 1

    def test_unmatched_ae_dates_not_included(self):
        """Dates from AEs NOT in matching_AEs should be ignored."""
        r1 = _make_report("1", adverse_events=[
            AdverseEvent(pt_name="rash", sources=["s"],
                         start_date=PartialDate(value="2020-06-01", resolution="day")),
            AdverseEvent(pt_name="nausea", sources=["s"],
                         start_date=PartialDate(value="2020-06-01", resolution="day")),
        ])
        r2 = _make_report("2", adverse_events=[
            AdverseEvent(pt_name="rash", sources=["s"],
                         start_date=PartialDate(value="2020-06-01", resolution="day")),
            AdverseEvent(pt_name="nausea", sources=["s"],
                         start_date=PartialDate(value="2020-06-01", resolution="day")),
        ])
        result = compute_matching_clinical_dates(r1, r2, [], ["rash"])
        assert len(result) == 1

    # -- Full example from the spec --

    def test_full_spec_example(self):
        """The concrete example from the specification.

        Matched drugs: cyclophosphamide, mitoxantrone, vincristine
        Matched AE: arrhythmia

        cyclophosphamide: R1 start=1994-03-30, R2 start=1994-03-30 → MATCH
        mitoxantrone:     R1 start=1994-03-30 stop=1994-05-12,
                          R2 start=1994-05-12 → start≠start, end missing → no match
        vincristine:      R1 start=1994-03-30 stop=1994-05-12,
                          R2 start=1994-05-12 → start≠start, end missing → no match
        arrhythmia:       R1 onset=1994-05-12, R2 onset=1994-05-12 → MATCH

        Expected unique matched dates: {1994-03-30, 1994-05-12}
        """
        r1 = _make_report("1",
            drugs=[
                Drug(substance="cyclophosphamide", sources=["s"],
                     start_date=PartialDate(value="1994-03-30", resolution="day")),
                Drug(substance="mitoxantrone", sources=["s"],
                     start_date=PartialDate(value="1994-03-30", resolution="day"),
                     end_date=PartialDate(value="1994-05-12", resolution="day")),
                Drug(substance="vincristine", sources=["s"],
                     start_date=PartialDate(value="1994-03-30", resolution="day"),
                     end_date=PartialDate(value="1994-05-12", resolution="day")),
            ],
            adverse_events=[
                AdverseEvent(pt_name="arrhythmia", sources=["s"],
                             start_date=PartialDate(value="1994-05-12", resolution="day")),
            ],
        )
        r2 = _make_report("2",
            drugs=[
                Drug(substance="cyclophosphamide", sources=["s"],
                     start_date=PartialDate(value="1994-03-30", resolution="day")),
                Drug(substance="mitoxantrone", sources=["s"],
                     start_date=PartialDate(value="1994-05-12", resolution="day")),
                Drug(substance="vincristine", sources=["s"],
                     start_date=PartialDate(value="1994-05-12", resolution="day")),
            ],
            adverse_events=[
                AdverseEvent(pt_name="arrhythmia", sources=["s"],
                             start_date=PartialDate(value="1994-05-12", resolution="day")),
            ],
        )

        matching_drugs = ["cyclophosphamide", "mitoxantrone", "vincristine"]
        matching_aes = ["arrhythmia"]

        result = compute_matching_clinical_dates(
            r1, r2, matching_drugs, matching_aes
        )

        matched_values = {cd.matched_value for cd in result}
        assert matched_values == {"1994-03-30", "1994-05-12"}
        assert len(result) == 2

    def test_case_insensitive_entity_lookup(self):
        """Drug/AE names in matching lists may differ in case from Report."""
        r1 = _make_report("1", drugs=[
            Drug(substance="Aspirin", sources=["s"],
                 start_date=PartialDate(value="2020-01-01", resolution="day")),
        ])
        r2 = _make_report("2", drugs=[
            Drug(substance="ASPIRIN", sources=["s"],
                 start_date=PartialDate(value="2020-01-01", resolution="day")),
        ])
        # matching_drugs uses the casing from r1 (per compute_matching_drugs)
        result = compute_matching_clinical_dates(r1, r2, ["Aspirin"], [])
        assert len(result) == 1

    def test_no_matching_entities_returns_empty(self):
        r1 = _make_report("1", drugs=[
            Drug(substance="aspirin", sources=["s"],
                 start_date=PartialDate(value="2020-01-01", resolution="day")),
        ])
        r2 = _make_report("2", drugs=[
            Drug(substance="aspirin", sources=["s"],
                 start_date=PartialDate(value="2020-01-01", resolution="day")),
        ])
        result = compute_matching_clinical_dates(r1, r2, [], [])
        assert len(result) == 0

    def test_results_sorted_by_date(self):
        """Matched clinical dates should be sorted by matched_value."""
        r1 = _make_report("1",
            drugs=[
                Drug(substance="drugA", sources=["s"],
                     start_date=PartialDate(value="2020-09-01", resolution="day")),
                Drug(substance="drugB", sources=["s"],
                     start_date=PartialDate(value="2020-01-15", resolution="day")),
            ],
        )
        r2 = _make_report("2",
            drugs=[
                Drug(substance="drugA", sources=["s"],
                     start_date=PartialDate(value="2020-09-01", resolution="day")),
                Drug(substance="drugB", sources=["s"],
                     start_date=PartialDate(value="2020-01-15", resolution="day")),
            ],
        )
        result = compute_matching_clinical_dates(
            r1, r2, ["drugA", "drugB"], []
        )
        assert len(result) == 2
        assert result[0].matched_value == "2020-01-15"
        assert result[1].matched_value == "2020-09-01"

    # -- Overall AE onset date matching --

    def test_overall_ae_onset_date_match(self):
        """Matching reported_overall_ae_onset_date should produce a clinical
        date match alongside drug dates, without requiring shared AE terms.

        Regression test for the ranitidine / prostate cancer scenario where
        both reports carry reported_overall_ae_onset_date = 2012-11 and
        shared drug dates (2001-01, 2019-12) but the AE has no onset date.
        """
        r1 = _make_report("1",
            drugs=[
                Drug(substance="ranitidine", sources=["structured"],
                     role="suspected",
                     start_date=PartialDate(value="2001-01", resolution="month"),
                     end_date=PartialDate(value="2019-12", resolution="month")),
            ],
            adverse_events=[
                AdverseEvent(pt_name="prostate cancer", sources=["structured"]),
            ],
            reported_overall_ae_onset_date=PartialDate(
                value="2012-11", resolution="month"
            ),
        )
        r2 = _make_report("2",
            drugs=[
                Drug(substance="ranitidine", sources=["structured"],
                     role="suspected", form="FILM-COATED TABLET",
                     start_date=PartialDate(value="2001-01", resolution="month"),
                     end_date=PartialDate(value="2019-12", resolution="month")),
                Drug(substance="ranitidine", sources=["structured"],
                     role="suspected", form="TABLET",
                     start_date=PartialDate(value="2001-01", resolution="month"),
                     end_date=PartialDate(value="2019-12", resolution="month")),
            ],
            adverse_events=[
                AdverseEvent(pt_name="prostate cancer", sources=["structured"]),
            ],
            reported_overall_ae_onset_date=PartialDate(
                value="2012-11", resolution="month"
            ),
        )
        result = compute_matching_clinical_dates(
            r1, r2, ["ranitidine"], ["prostate cancer"]
        )
        matched_values = {cd.matched_value for cd in result}
        assert matched_values == {"2001-01", "2012-11", "2019-12"}
        assert len(result) == 3
        # All at month resolution
        assert all(cd.resolution == "month" for cd in result)

    def test_overall_ae_onset_date_dedup_with_ae_onset(self):
        """If overall AE onset matches a per-AE onset date, it is deduplicated."""
        r1 = _make_report("1",
            adverse_events=[
                AdverseEvent(pt_name="rash", sources=["s"],
                             start_date=PartialDate(value="2020-06-15", resolution="day")),
            ],
            reported_overall_ae_onset_date=PartialDate(
                value="2020-06-15", resolution="day"
            ),
        )
        r2 = _make_report("2",
            adverse_events=[
                AdverseEvent(pt_name="rash", sources=["s"],
                             start_date=PartialDate(value="2020-06-15", resolution="day")),
            ],
            reported_overall_ae_onset_date=PartialDate(
                value="2020-06-15", resolution="day"
            ),
        )
        result = compute_matching_clinical_dates(r1, r2, [], ["rash"])
        # Same date from AE onset and overall onset — counted once
        assert len(result) == 1
        assert result[0].matched_value == "2020-06-15"

    def test_overall_ae_onset_only_one_report_no_match(self):
        """Overall AE onset date on only one report → no match."""
        r1 = _make_report("1",
            reported_overall_ae_onset_date=PartialDate(
                value="2012-11", resolution="month"
            ),
        )
        r2 = _make_report("2")
        result = compute_matching_clinical_dates(r1, r2, [], [])
        assert len(result) == 0

    def test_overall_ae_onset_via_compute_matches(self):
        """End-to-end: compute_matches includes overall AE onset in
        matched_clinical_dates."""
        r1 = Report(
            report_id="r1",
            drugs=[Drug(substance="ranitidine", sources=["structured"],
                        role="suspected",
                        start_date=PartialDate(value="2001-01", resolution="month"),
                        end_date=PartialDate(value="2019-12", resolution="month"))],
            adverse_events=[AdverseEvent(pt_name="prostate cancer",
                                         sources=["structured"])],
            reported_overall_ae_onset_date=PartialDate(
                value="2012-11", resolution="month"
            ),
        )
        r2 = Report(
            report_id="r2",
            drugs=[Drug(substance="ranitidine", sources=["structured"],
                        role="suspected",
                        start_date=PartialDate(value="2001-01", resolution="month"),
                        end_date=PartialDate(value="2019-12", resolution="month"))],
            adverse_events=[AdverseEvent(pt_name="prostate cancer",
                                         sources=["structured"])],
            reported_overall_ae_onset_date=PartialDate(
                value="2012-11", resolution="month"
            ),
        )
        match_result = compute_matches(r1, r2)
        matched_values = {cd.matched_value
                          for cd in match_result.matched_clinical_dates}
        assert "2012-11" in matched_values
        assert len(match_result.matched_clinical_dates) == 3
