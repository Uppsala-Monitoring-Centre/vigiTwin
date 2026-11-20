"""Age, height and weight offsets.

One offset per pair and per unit, so the difference between the two reports —
the thing a discrepancy finding quotes and a reader checks — comes through
untouched. The tests that matter most are the ones about what an offset is
*not* allowed to do: add a decimal place, cross an age band, or turn a
converted imperial weight into a figure no scale ever produced.
"""

from __future__ import annotations

from vigitwin.review_html.evidence.publication import (
    PublicationPolicy,
    format_quantity,
    imperial_amount,
    parse_quantity,
    solve_numeric_offset,
)
from vigitwin.review_html.evidence.publication.numbers import (
    check_numeric_offset,
)
from tests.test_review_html.conftest import (
    publication_page,
    scramble,
    worked_example_case,
)


def _q(text: str):
    quantity = parse_quantity(text)
    assert quantity is not None
    return quantity


class TestParsingAndWriting:
    """A figure, its unit, and the precision it was written at."""

    def test_a_whole_number_has_no_decimals(self):
        assert _q("81 years").decimals == 0

    def test_a_written_decimal_is_kept_even_when_it_is_a_zero(self):
        """``47.60`` and ``47.6`` are the same number and different claims
        about how precisely it was known."""
        assert _q("47.60 kg").decimals == 2

    def test_prose_is_not_a_quantity(self):
        assert parse_quantity("about eighty") is None
        assert parse_quantity("81") is None

    def test_a_whole_value_is_written_without_its_decimal_part(self):
        """Mirrors ``build._number_text``, so a published cell reads the way
        every other cell on the page does."""
        assert format_quantity(_q("54.0 years")) == "54 years"


class TestImperialOrigin:
    """A metric value that was a round imperial one before conversion."""

    def test_a_pound_derived_weight_is_recognised(self):
        assert imperial_amount(_q("47.63 kg")) == ("lb", 105)

    def test_an_inch_derived_height_is_recognised(self):
        assert imperial_amount(_q("167.64 cm")) == ("in", 66)

    def test_a_whole_kilogram_is_a_kilogram_measurement(self):
        """However neatly it divides into pounds. Without a decimal place
        there is no evidence of a conversion."""
        assert imperial_amount(_q("70 kg")) is None

    def test_a_value_that_is_not_near_a_whole_pound_is_not_converted(self):
        assert imperial_amount(_q("70.5 kg")) is None

    def test_a_pound_derived_weight_shifts_in_whole_pounds(self):
        solution = solve_numeric_offset(
            [_q("47.63 kg")], PublicationPolicy(), "seed", is_age=False
        )
        assert solution.offset is not None
        assert solution.offset.imperial_unit == "lb"
        # Still a plausible conversion of a whole number of pounds, at the
        # precision the original was written at.
        published = solution.values[0]
        assert published.decimals == 2
        assert imperial_amount(published) is not None

    def test_a_kilogram_weight_shifts_in_kilograms(self):
        solution = solve_numeric_offset(
            [_q("70 kg")], PublicationPolicy(), "seed", is_age=False
        )
        assert solution.offset is not None
        assert solution.offset.imperial_unit == ""
        assert solution.values[0].decimals == 0

    def test_a_mixed_pair_falls_back_to_the_stored_unit(self):
        """Shifting one side in pounds and the other in kilograms would break
        the difference between them, which is the one thing that must hold."""
        solution = solve_numeric_offset(
            [_q("47.63 kg"), _q("70 kg")],
            PublicationPolicy(),
            "seed",
            is_age=False,
        )
        assert solution.offset is not None
        assert solution.offset.imperial_unit == ""


class TestWhatAnOffsetMayNotChange:
    def test_the_difference_between_the_two_reports_is_preserved(self):
        values = [_q("54 years"), _q("61 years")]
        solution = solve_numeric_offset(
            values, PublicationPolicy(), "seed", is_age=True
        )
        assert solution.offset is not None
        assert (
            solution.values[0].value - solution.values[1].value
            == values[0].value - values[1].value
        )

    def test_an_offset_that_crosses_an_age_band_is_refused(self):
        """A 17-year-old who publishes as 19 has had the clinical reading of
        the case changed, which is the one thing this layer must not do."""
        policy = PublicationPolicy()
        report = check_numeric_offset(
            [_q("17 years")], [_q("19 years")], policy, is_age=True
        )
        assert "age_band" in {f.rule for f in report.failures}

    def test_an_offset_that_adds_a_decimal_place_is_refused(self):
        """The pipeline matches numeric demographics on a grid read off the
        decimals as written, so adding one changes what counts as a match."""
        policy = PublicationPolicy()
        report = check_numeric_offset(
            [_q("70 kg")], [_q("70.5 kg")], policy, is_age=False
        )
        assert "written_precision" in {f.rule for f in report.failures}

    def test_a_non_positive_result_is_refused(self):
        policy = PublicationPolicy()
        report = check_numeric_offset(
            [_q("1 years")], [_q("-2 years")], policy, is_age=True
        )
        assert "positive" in {f.rule for f in report.failures}

    def test_an_offset_that_moves_nothing_is_allowed(self):
        """An age published as reported is a legitimate outcome. Requiring
        movement would tell anyone holding the policy that the displayed age
        is not the true one."""
        policy = PublicationPolicy()
        assert check_numeric_offset(
            [_q("54 years")], [_q("54 years")], policy, is_age=True
        ).accepted

    def test_a_published_value_may_equal_the_other_reports_value(self):
        """54 publishing as 55 where 55 is also reported is allowed, for the
        same reason: forbidding it narrows what an observer has to search."""
        policy = PublicationPolicy()
        assert check_numeric_offset(
            [_q("54 years"), _q("55 years")],
            [_q("55 years"), _q("56 years")],
            policy,
            is_age=True,
        ).accepted

    def test_a_policy_may_insist_on_movement(self):
        policy = PublicationPolicy(allow_unchanged_values=False)
        report = check_numeric_offset(
            [_q("54 years")], [_q("54 years")], policy, is_age=True
        )
        assert "effectiveness" in {f.rule for f in report.failures}

    def test_two_values_that_matched_still_match(self):
        """75.2 and 75 fall on the same one-year grid point; an offset that
        separated them would turn a scored match into a non-match."""
        policy = PublicationPolicy()
        report = check_numeric_offset(
            [_q("75.2 years"), _q("75 years")],
            [_q("76.2 years"), _q("77 years")],
            policy,
            is_age=True,
        )
        assert "match_grid" in {f.rule for f in report.failures}

    def test_an_infant_stays_an_infant(self):
        """Months are their own unit with their own offset range."""
        solution = solve_numeric_offset(
            [_q("6 months")], PublicationPolicy(), "seed", is_age=True
        )
        assert solution.offset is not None
        assert solution.values[0].unit == "months"
        assert 0 < solution.values[0].value < 24

    def test_a_zero_offset_is_always_available_as_a_draw(self):
        """So a numeric transform does not fail: the worst case is a value
        published as reported, which the review page reports as such."""
        policy = PublicationPolicy(age_offset_max_years=1)
        solution = solve_numeric_offset(
            [_q("1 years")], policy, "seed", is_age=True
        )
        assert solution.offset is not None

    def test_without_it_an_impossible_group_returns_nothing(self):
        """An age at the bottom of its band with a one-year range has nowhere
        to go, and saying so is the correct outcome."""
        policy = PublicationPolicy(
            age_offset_max_years=1, allow_unchanged_values=False
        )
        solution = solve_numeric_offset(
            [_q("1 years")], policy, "seed", is_age=True
        )
        assert solution.offset is None
        assert solution.failures


class TestAsTheTransformRunsIt:
    def test_both_reports_ages_move_together(self):
        result = scramble(publication_page(worked_example_case()))
        ages = {
            entry.published
            for entry in result.transforms[0].table.entries.values()
            if entry.kind == "age"
        }
        assert len(ages) == 1

    def test_a_pound_derived_weight_survives_the_whole_pipeline(self):
        result = scramble(publication_page(worked_example_case(weight=47.63)))
        weights = [
            entry
            for entry in result.transforms[0].table.entries.values()
            if entry.kind == "weight"
        ]
        assert weights
        published = parse_quantity(weights[0].published)
        assert published is not None
        assert imperial_amount(published) is not None
