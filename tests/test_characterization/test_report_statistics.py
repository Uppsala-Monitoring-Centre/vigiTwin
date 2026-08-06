"""Report-level tables: denominators, missingness, ranking, and unit counting."""

from __future__ import annotations

import pytest

from llm_dup_dev.characterization.builder import (
    NO_DRUG_ROLE_REASON,
    NO_HIERARCHY_REASON,
    build_reference_set_characteristics,
)
from llm_dup_dev.models.enums import DrugRole

from .conftest import (
    age,
    by_id,
    date,
    drug,
    event,
    make_reference_set,
    make_report,
    sourced,
)


def _characterise(reports, pair_specs, *, top_n=10):
    reference_set = make_reference_set(pair_specs)
    return build_reference_set_characteristics(
        reference_set, by_id(*reports), top_n=top_n
    )


def _counts(distribution) -> dict[str, int]:
    return {row.key: row.count for row in distribution.categories}


def _partitions(distribution) -> bool:
    return (
        distribution.n_resolved
        + distribution.n_missing
        + distribution.n_conflicting
        + distribution.n_unrecognised
        + distribution.n_out_of_range
        == distribution.denominator
    )


class TestEmptyReports:
    """Two reports carrying nothing at all must produce a legible empty table."""

    @pytest.fixture
    def result(self, report_empty, report_empty_2):
        return _characterise(
            [report_empty, report_empty_2], [("R_EMPTY", "R_EMPTY2")]
        )

    def test_denominators_are_the_report_count_not_zero(self, result):
        reports = result.reports
        assert reports.n_unique_reports == 2
        assert reports.report_year.denominator == 2
        assert reports.country_of_origin.denominator == 2

    def test_everything_is_missing_rather_than_absent(self, result):
        for distribution in (
            result.reports.report_year,
            result.reports.country_of_origin,
            result.reports.patient_age_bands,
        ):
            assert distribution.n_missing == 2
            assert distribution.categories == []

    def test_sex_still_shows_its_whole_vocabulary(self, result):
        """An empty sex table must not look like a field that does not exist."""
        assert _counts(result.reports.patient_sex) == {"female": 0, "male": 0}
        assert result.reports.patient_sex.n_distinct == 0

    def test_medians_are_none_not_zero(self, result):
        summary = result.reports.patient_age_years
        assert summary.n_used == 0
        assert (summary.median, summary.q1, summary.q3) == (None, None, None)
        assert (summary.minimum, summary.maximum) == (None, None)

    def test_proportions_are_none_rather_than_dividing_by_zero(self, result):
        for row in result.reports.patient_sex.categories:
            assert row.proportion == 0.0  # denominator is 2 reports, not 0


class TestAgeBands:
    @pytest.mark.parametrize(
        "years,band", [(9, "0-9"), (10, "10-19"), (99, "90-99"), (105, "100+")]
    )
    def test_band_assignment(self, years, band):
        reports = [
            make_report("R1", ages=[age(years)]),
            make_report("R2"),
        ]
        result = _characterise(reports, [("R1", "R2")])

        assert _counts(result.reports.patient_age_bands) == {band: 1}

    @pytest.mark.parametrize("years", [-1, 200])
    def test_implausible_age_excluded_from_median_and_bands(self, years):
        reports = [
            make_report("R1", ages=[age(years)]),
            make_report("R2", ages=[age(40)]),
        ]
        result = _characterise(reports, [("R1", "R2")])

        summary = result.reports.patient_age_years
        assert summary.n_used == 1
        assert summary.n_out_of_range == 1
        assert summary.median == 40

        bands = result.reports.patient_age_bands
        assert _counts(bands) == {"40-49": 1}
        assert bands.n_out_of_range == 1
        assert _partitions(bands)


class TestQuartiles:
    """Type-7 quartiles, including the sizes ``statistics.quantiles`` refuses."""

    def _median_iqr(self, ages):
        reports = [make_report(f"R{i}", ages=[age(a)]) for i, a in enumerate(ages)]
        reports.append(make_report("RX"))
        pairs = [(reports[i].report_id, "RX") for i in range(len(ages))]
        result = _characterise(reports, pairs)
        summary = result.reports.patient_age_years
        return summary.n_used, summary.median, summary.q1, summary.q3

    def test_single_observation_is_degenerate_not_an_error(self):
        assert self._median_iqr([54]) == (1, 54.0, 54.0, 54.0)

    def test_two_observations(self):
        assert self._median_iqr([50, 60]) == (2, 55.0, 52.5, 57.5)

    def test_four_observations_match_hand_computed_type_7(self):
        n, median, q1, q3 = self._median_iqr([10, 20, 30, 40])
        assert (n, median, q1, q3) == (4, 25.0, 17.5, 32.5)


class TestTopN:
    def _drug_table(self, per_report_substances, top_n=10):
        reports = [
            make_report(f"R{i}", drugs=[drug(s) for s in substances])
            for i, substances in enumerate(per_report_substances)
        ]
        reports.append(make_report("RX"))
        pairs = [(r.report_id, "RX") for r in reports[:-1]]
        return _characterise(reports, pairs, top_n=top_n).reports.top_drugs

    def test_twelve_singletons_are_cut_to_ten_lexicographically(self):
        substances = [f"drug_{i:02d}" for i in range(12)]
        table = self._drug_table([[s] for s in substances], top_n=10)

        assert len(table.categories) == 10
        assert table.truncated_to == 10
        assert table.n_distinct == 12
        assert table.boundary_tie is True
        assert [row.key for row in table.categories] == substances[:10]

    def test_clean_cut_is_not_reported_as_a_tie(self):
        # Eleven substances; the tenth is strictly commoner than the eleventh.
        per_report = [[f"drug_{i:02d}" for i in range(10)] for _ in range(2)]
        per_report.append(["drug_10"])
        table = self._drug_table(per_report, top_n=10)

        assert table.truncated_to == 10
        assert table.boundary_tie is False

    def test_no_truncation_flag_when_everything_fits(self):
        table = self._drug_table([["pregabalin"], ["sertraline"]], top_n=10)

        assert table.truncated_to is None
        assert table.boundary_tie is False

    def test_counts_are_reports_not_occurrences(self):
        table = self._drug_table([["Pregabalin", "pregabalin"], ["pregabalin"]])

        assert _counts(table) == {"pregabalin": 2}


class TestDrugRoles:
    def test_only_suspected_and_interacting_reach_the_top_table(self):
        reports = [
            make_report(
                "R1",
                drugs=[
                    drug("pregabalin", DrugRole.SUSPECTED),
                    drug("omeprazole", DrugRole.CONCOMITANT),
                ],
            ),
            make_report("R2", drugs=[drug("ritonavir", DrugRole.INTERACTING)]),
        ]
        result = _characterise(reports, [("R1", "R2")])

        assert _counts(result.reports.top_drugs) == {
            "pregabalin": 1,
            "ritonavir": 1,
        }
        assert result.reports.top_drugs.unavailable_reason is None

    def test_no_roles_recorded_yields_a_labelled_empty_table(self):
        """An empty table must not read as "these reports mention no drugs"."""
        reports = [
            make_report("R1", drugs=[drug("pregabalin", None)]),
            make_report("R2", drugs=[drug("metformin", None)]),
        ]
        result = _characterise(reports, [("R1", "R2")])

        assert result.reports.top_drugs.categories == []
        assert result.reports.top_drugs.unavailable_reason == NO_DRUG_ROLE_REASON
        assert _counts(result.reports.drug_roles) == {"unrecorded": 2}
        assert result.reports.availability.n_reports_with_any_drug == 2
        assert result.reports.availability.n_reports_with_any_drug_role_recorded == 0

    def test_roles_recorded_but_none_countable_is_not_flagged_unavailable(self):
        """The field was populated; it simply holds no central drugs."""
        reports = [
            make_report("R1", drugs=[drug("omeprazole", DrugRole.CONCOMITANT)]),
            make_report("R2", drugs=[drug("aspirin", DrugRole.CONCOMITANT)]),
        ]
        result = _characterise(reports, [("R1", "R2")])

        assert result.reports.top_drugs.categories == []
        assert result.reports.top_drugs.unavailable_reason is None
        assert _counts(result.reports.drug_roles) == {"concomitant": 2}


class TestAdverseEvents:
    def test_pt_table_populated_while_hlt_is_flagged_unavailable(self):
        reports = [
            make_report("R1", events=[event("somnolence")]),
            make_report("R2", events=[event("nausea")]),
        ]
        result = _characterise(reports, [("R1", "R2")])

        assert _counts(result.reports.top_adverse_events_pt) == {
            "somnolence": 1,
            "nausea": 1,
        }
        hlt = result.reports.top_adverse_events_hlt
        assert hlt.categories == []
        assert hlt.unavailable_reason == NO_HIERARCHY_REASON
        assert result.reports.availability.n_reports_with_any_adverse_event == 2
        assert result.reports.availability.n_reports_with_any_hlt == 0

    def test_partial_hierarchy_coverage_is_counted_not_flagged(self):
        reports = [
            make_report("R1", events=[event("somnolence", "disturbances nec")]),
            make_report("R2", events=[event("nausea")]),
        ]
        result = _characterise(reports, [("R1", "R2")])

        hlt = result.reports.top_adverse_events_hlt
        assert _counts(hlt) == {"disturbances nec": 1}
        assert hlt.unavailable_reason is None
        assert hlt.n_missing == 1
        assert result.reports.availability.n_reports_with_any_hlt == 1


class TestUniqueVersusInstanceCounting:
    def test_a_report_in_two_pairs_is_counted_once(self):
        shared = make_report(
            "R1",
            ages=[age(54)],
            countries=[sourced("US")],
            drugs=[drug("pregabalin")],
            dates=[date("2020-06-12")],
        )
        reports = [shared, make_report("R2"), make_report("R3")]
        result = _characterise(reports, [("R1", "R2"), ("R1", "R3")])

        reports_block = result.reports
        assert reports_block.n_pairs == 2
        assert reports_block.n_report_instances == 4
        assert reports_block.n_unique_reports == 3
        assert reports_block.n_reports_in_multiple_pairs == 1

        # R1's drug and country contribute one report each, not two.
        assert _counts(reports_block.top_drugs) == {"pregabalin": 1}
        assert _counts(reports_block.country_of_origin) == {"us": 1}
        assert reports_block.patient_age_years.n_used == 1


class TestPartitionInvariant:
    def test_every_distribution_accounts_for_every_report(self, simple_reports):
        result = build_reference_set_characteristics(
            make_reference_set([("R001", "R002"), ("R003", "R004")]),
            simple_reports,
        )
        reports = result.reports

        for distribution in (
            reports.report_year,
            reports.country_of_origin,
            reports.patient_sex,
            reports.patient_age_bands,
            reports.drug_roles,
            reports.top_drugs,
            reports.top_adverse_events_pt,
            reports.top_adverse_events_hlt,
        ):
            assert _partitions(distribution), distribution.field
            assert distribution.denominator == reports.n_unique_reports
