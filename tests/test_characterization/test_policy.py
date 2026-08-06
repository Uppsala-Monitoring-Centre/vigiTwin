"""The resolution policy: which candidate wins, and what counts as a conflict.

These are the tests that pin the judgment calls. Every number in a
characterization artifact depends on this module reducing several candidate
values to one, so a silent change here would move medians and category counts
with nothing else failing.
"""

from __future__ import annotations

import pytest

from llm_dup_dev.characterization import policy
from llm_dup_dev.feature_generation.reporting_context import _extract_years
from llm_dup_dev.models.enums import DrugRole

from .conftest import age, date, drug, event, make_report, sourced


class TestSourcePreference:
    def test_structured_wins_over_narrative(self):
        report = make_report(
            "R1", ages=[age(54), age(55, source="narrative_llm")]
        )
        resolution = policy.resolve_age_years(report)

        assert resolution.status == policy.RESOLVED
        assert resolution.key == "54"

    def test_narrative_used_when_no_structured_value(self):
        report = make_report("R1", ages=[age(55, source="narrative_llm")])

        assert policy.resolve_age_years(report).key == "55"

    def test_narrative_candidates_never_create_a_conflict(self):
        """A narrative value disagreeing with a structured one is not a conflict.

        Structured wins outright, so the narrative candidate is not even
        considered — otherwise every LLM-extracted age that rounded differently
        would show up as a conflict rather than as a resolved value.
        """
        report = make_report(
            "R1", ages=[age(54), age(80, source="narrative_llm")]
        )

        assert policy.resolve_age_years(report).status == policy.RESOLVED


class TestConflicts:
    def test_two_structured_countries_conflict(self):
        report = make_report("R1", countries=[sourced("US"), sourced("DE")])

        assert policy.resolve_country(report).status == policy.CONFLICTING

    def test_same_age_in_different_units_does_not_conflict(self):
        """54 years and 648 months are the same age, so they agree."""
        report = make_report("R1", ages=[age(54), age(648, unit="months")])
        resolution = policy.resolve_age_years(report)

        assert resolution.status == policy.RESOLVED
        assert resolution.key == "54"

    def test_ages_within_the_same_completed_year_do_not_conflict(self):
        report = make_report("R1", ages=[age(54.2), age(54.9)])
        resolution = policy.resolve_age_years(report)

        assert resolution.status == policy.RESOLVED
        assert resolution.key == "54"

    def test_dates_of_different_resolution_in_one_year_do_not_conflict(self):
        report = make_report(
            "R1",
            dates=[date("2020-06-12"), date("2020", resolution="year")],
        )

        assert policy.resolve_report_year(report).key == "2020"

    def test_genuinely_different_years_conflict(self):
        report = make_report("R1", dates=[date("2020-06-12"), date("2021-01-02")])

        assert policy.resolve_report_year(report).status == policy.CONFLICTING


class TestMissingAndUnrecognised:
    def test_empty_list_is_missing(self):
        report = make_report("R1")

        assert policy.resolve_country(report).status == policy.MISSING
        assert policy.resolve_sex(report).status == policy.MISSING
        assert policy.resolve_age_years(report).status == policy.MISSING
        assert policy.resolve_report_year(report).status == policy.MISSING

    def test_uncertainty_sentinel_becomes_missing_via_the_model(self):
        """The contract rule is inherited from ``Report``, not re-implemented.

        ``"unknown"`` never reaches this module: the model validator drops it,
        leaving an empty list. Asserting it here pins that there is exactly one
        definition of "value that means absence of information".
        """
        report = make_report("R1", countries=[sourced("unknown")])

        assert report.country_of_origin_values == []
        assert policy.resolve_country(report).status == policy.MISSING

    def test_unknown_age_unit_is_unrecognised_not_assumed_to_be_years(self):
        report = make_report("R1", ages=[age(5, unit="decades")])

        assert policy.resolve_age_years(report).status == policy.UNRECOGNISED

    def test_unrecognised_is_distinct_from_missing(self):
        """Something was recorded; it just could not be read."""
        assert policy.UNRECOGNISED != policy.MISSING


class TestCountryKeying:
    def test_case_and_whitespace_variants_share_one_key(self):
        for text in ("us", "US ", " Us"):
            assert policy.resolve_country(
                make_report("R1", countries=[sourced(text)])
            ).key == "us"

    def test_label_is_the_most_frequent_original_spelling(self):
        report = make_report(
            "R1", countries=[sourced("US"), sourced("US"), sourced("us")]
        )
        resolution = policy.resolve_country(report)

        assert resolution.key == "us"
        assert resolution.label == "US"

    def test_label_ties_break_lexicographically(self):
        report = make_report("R1", countries=[sourced("US"), sourced("us")])

        assert policy.resolve_country(report).label == "US"


class TestAgeConversion:
    @pytest.mark.parametrize(
        "value,unit,expected",
        [
            (54, "years", 54.0),
            (54.9, "years", 54.0),
            (648, "months", 54.0),
            (18, "months", 1.0),
            (400, "days", 1.0),
            (0.5, "years", 0.0),
        ],
    )
    def test_completed_years(self, value, unit, expected):
        assert policy.age_in_completed_years(age(value, unit=unit)) == expected

    def test_unknown_unit_returns_none(self):
        assert policy.age_in_completed_years(age(5, unit="decades")) is None

    @pytest.mark.parametrize(
        "years,band",
        [(0, "0-9"), (9, "0-9"), (10, "10-19"), (54, "50-59"), (99, "90-99"),
         (100, "100+"), (105, "100+")],
    )
    def test_band_labels(self, years, band):
        assert policy.age_band(years) == band

    @pytest.mark.parametrize("years", [-1, 131, 200])
    def test_implausible_ages_rejected(self, years):
        assert not policy.age_is_plausible(years)

    @pytest.mark.parametrize("years", [0, 54, 130])
    def test_plausible_ages_accepted(self, years):
        assert policy.age_is_plausible(years)


class TestYearAgreesWithReportingContext:
    def test_matches_the_pipelines_own_year_extraction(self, simple_reports):
        """The same date must not yield one year here and another there.

        ``reporting_context`` picks the year that scopes the anchor population;
        if Table 1 disagreed with it, the reported reporting-year distribution
        would not describe the population the method actually reasoned over.
        """
        for report in simple_reports.values():
            resolution = policy.resolve_report_year(report)
            years = _extract_years(report)
            if resolution.status == policy.RESOLVED:
                assert years == {int(resolution.key)}
            elif resolution.status == policy.MISSING:
                assert years == set()


class TestClinicalFeatureKeys:
    def test_repeated_substance_counts_once(self):
        report = make_report("R1", drugs=[drug("Pregabalin"), drug("pregabalin")])

        assert policy.drug_keys(report) == {"pregabalin"}

    def test_only_suspected_and_interacting_drugs_count(self):
        report = make_report(
            "R1",
            drugs=[
                drug("pregabalin", DrugRole.SUSPECTED),
                drug("ritonavir", DrugRole.INTERACTING),
                drug("omeprazole", DrugRole.CONCOMITANT),
                drug("aspirin", None),
            ],
        )

        assert policy.drug_keys(report) == {"pregabalin", "ritonavir"}

    def test_role_coverage_sees_every_drug(self):
        """Coverage is about whether the field was populated at all."""
        assert policy.has_any_drug_role(
            make_report("R1", drugs=[drug("omeprazole", DrugRole.CONCOMITANT)])
        )
        assert not policy.has_any_drug_role(
            make_report("R1", drugs=[drug("omeprazole", None)])
        )

    def test_pts_are_keyed_case_insensitively_and_deduplicated(self):
        report = make_report("R1", events=[event("Somnolence"), event("somnolence")])

        assert policy.pt_keys(report) == {"somnolence": "Somnolence"}

    def test_events_without_a_hierarchy_contribute_no_hlt(self):
        report = make_report(
            "R1", events=[event("somnolence", "disturbances nec"), event("dizziness")]
        )

        assert policy.pt_keys(report).keys() == {"somnolence", "dizziness"}
        assert policy.hlt_keys(report).keys() == {"disturbances nec"}
