"""Pair-level tables: three-way concordance, differences, and shared features."""

from __future__ import annotations

from llm_dup_dev.characterization.builder import build_reference_set_characteristics
from llm_dup_dev.feature_generation.matching import (
    compute_matching_aes,
    compute_matching_drugs,
)

from .conftest import age, by_id, date, drug, event, make_reference_set, make_report, sourced


def _characterise(reports, pair_specs):
    return build_reference_set_characteristics(
        make_reference_set(pair_specs), by_id(*reports)
    )


class TestConcordance:
    def test_same_different_and_not_comparable_are_three_buckets(self):
        reports = [
            make_report("R1", countries=[sourced("US")]),
            make_report("R2", countries=[sourced("US")]),
            make_report("R3", countries=[sourced("DE")]),
            make_report("R4", countries=[sourced("SE")]),
            make_report("R5", countries=[sourced("US")]),
            make_report("R6"),  # no country — not comparable
        ]
        result = _characterise(reports, [("R1", "R2"), ("R3", "R4"), ("R5", "R6")])
        country = result.pairs.country

        assert (country.n_same, country.n_different, country.n_not_comparable) == (1, 1, 1)

    def test_a_silent_report_is_not_a_disagreement(self):
        """Two reports with no country have not disagreed about country."""
        reports = [make_report("R1"), make_report("R2")]
        result = _characterise(reports, [("R1", "R2")])
        country = result.pairs.country

        assert country.n_different == 0
        assert country.n_not_comparable == 1
        assert country.proportion_same_comparable is None

    def test_both_proportions_are_reported(self):
        reports = [
            make_report("R1", countries=[sourced("US")]),
            make_report("R2", countries=[sourced("US")]),
            make_report("R3", countries=[sourced("DE")]),
            make_report("R4"),
        ]
        result = _characterise(reports, [("R1", "R2"), ("R3", "R4")])
        country = result.pairs.country

        assert country.proportion_same_comparable == 1.0
        assert country.proportion_same_all == 0.5

    def test_country_keys_are_matched_case_insensitively(self):
        reports = [
            make_report("R1", countries=[sourced("US")]),
            make_report("R2", countries=[sourced("us")]),
        ]
        result = _characterise(reports, [("R1", "R2")])

        assert result.pairs.country.n_same == 1

    def test_year_and_sex_concordance(self):
        reports = [
            make_report("R1", dates=[date("2020-06-12")], sexes=[sourced("female")]),
            make_report(
                "R2",
                dates=[date("2020", resolution="year")],
                sexes=[sourced("male")],
            ),
        ]
        result = _characterise(reports, [("R1", "R2")])

        assert result.pairs.report_year.n_same == 1
        assert result.pairs.patient_sex.n_different == 1


class TestDifferences:
    def test_structured_preference_changes_the_age_difference(self, report_a, report_b):
        """``report_b`` carries 54 structured and 55 narrative.

        Under "structured wins" both reports are 54 and the difference is 0.
        A naive first-value or maximum rule would report 1 here, so this test
        pins the policy's effect on a pair-level number.
        """
        result = _characterise([report_a, report_b], [("R001", "R002")])

        assert result.pairs.age_difference_years.median == 0
        assert result.pairs.n_pairs_with_identical_age == 1

    def test_year_difference(self, report_a, report_c):
        result = _characterise([report_a, report_c], [("R001", "R003")])

        assert result.pairs.year_difference.median == 2

    def test_conflicting_side_is_counted_separately_from_missing(self):
        reports = [
            make_report("R1", ages=[age(50), age(60)]),  # conflicting
            make_report("R2", ages=[age(50)]),
            make_report("R3"),  # missing
            make_report("R4", ages=[age(50)]),
        ]
        result = _characterise(reports, [("R1", "R2"), ("R3", "R4")])
        difference = result.pairs.age_difference_years

        assert difference.n_used == 0
        assert difference.n_conflicting == 1
        assert difference.n_missing == 1

    def test_implausible_age_is_kept_out_of_the_difference(self):
        reports = [
            make_report("R1", ages=[age(200)]),
            make_report("R2", ages=[age(50)]),
        ]
        result = _characterise(reports, [("R1", "R2")])

        assert result.pairs.age_difference_years.n_used == 0


class TestSharedFeatures:
    def test_shared_counts_agree_with_the_pipelines_own_matching(
        self, report_a, report_b
    ):
        """Reuse pinned: "shared drugs" must mean what it means to the adjudicator.

        If ``matching.compute_matching_drugs`` changes its keying, this fails
        rather than letting the two definitions drift apart silently.
        """
        result = _characterise([report_a, report_b], [("R001", "R002")])

        expected_drugs = len(compute_matching_drugs(report_a, report_b)[0])
        expected_aes = len(compute_matching_aes(report_a, report_b)[0])

        assert result.pairs.shared_drugs.median == expected_drugs
        assert result.pairs.shared_adverse_events_pt.median == expected_aes

    def test_any_shared_counts(self):
        reports = [
            make_report("R1", drugs=[drug("pregabalin")], events=[event("somnolence")]),
            make_report("R2", drugs=[drug("pregabalin")], events=[event("nausea")]),
            make_report("R3", drugs=[drug("metformin")]),
            make_report("R4", drugs=[drug("lisinopril")]),
        ]
        result = _characterise(reports, [("R1", "R2"), ("R3", "R4")])

        assert result.pairs.n_pairs_with_any_shared_drug == 1
        assert result.pairs.n_pairs_with_any_shared_adverse_event == 0

    def test_shared_drug_count_ignores_role(self):
        """Sharing is about the reports being alike, not about clinical centrality.

        The top-drugs table restricts to suspected/interacting; pair overlap
        deliberately does not, because it reuses the pipeline's own matching,
        which is what the adjudicator reasoned over.
        """
        reports = [
            make_report("R1", drugs=[drug("omeprazole", None)]),
            make_report("R2", drugs=[drug("omeprazole", None)]),
        ]
        result = _characterise(reports, [("R1", "R2")])

        assert result.pairs.n_pairs_with_any_shared_drug == 1
        assert result.reports.top_drugs.categories == []
