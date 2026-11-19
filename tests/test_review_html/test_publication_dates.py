"""Date shifting, and what it is not allowed to change.

The hard case is partial dates, and every test here is about one of two
things: that a date comes back at the precision it went in at, or that an
offset which would change what the page *says* is rejected rather than
applied. The anti-requirements carry the weight — a year-only drug start that
does **not** move is correct, and an offset that quietly widens a quoted
separation is not.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from llm_dup_dev.models.report import PartialDate
from llm_dup_dev.review_html.evidence.publication import (
    SEVERITY_BOUNDARY_DAYS,
    DisclosureCode,
    PublicationPolicy,
    check_date_offset,
    date_relations,
    offset_candidates,
    parse_partial_date,
    resolution_of,
    separation_preserved,
    severity_band,
    shift_partial_date,
)
from llm_dup_dev.review_html.evidence.publication.dates import (
    DateRole,
    offset_tiers,
    within_plausible_window,
)
from tests.test_review_html.conftest import (
    publication_page,
    scramble,
    worked_example_case,
)


def _d(value: str, resolution: str) -> PartialDate:
    return PartialDate(value=value, resolution=resolution)


class TestResolutionOf:
    """Precision is read off the writing, because some artifacts store none."""

    def test_a_full_date_is_day_precision(self):
        assert resolution_of("2023-11-13") == "day"

    def test_a_year_and_month_is_month_precision(self):
        assert resolution_of("2023-11") == "month"

    def test_a_bare_year_is_year_precision(self):
        assert resolution_of("2023") == "year"

    def test_an_impossible_date_is_not_a_date(self):
        """Fail closed: a thirteenth month is not a date to be shifted."""
        assert resolution_of("2023-13-01") is None

    def test_prose_is_not_a_date(self):
        assert resolution_of("sometime in November") is None
        assert parse_partial_date("sometime in November") is None


class TestTheShiftRule:
    """One offset, three precisions, and the precision never changes."""

    def test_a_complete_date_shifts_exactly(self):
        assert shift_partial_date(_d("2023-11-13", "day"), 30).value == "2023-12-13"

    def test_a_month_shifts_from_the_first_of_the_month(self):
        assert shift_partial_date(_d("2023-11", "month"), 30).value == "2023-12"

    def test_a_year_shifts_from_the_first_of_january(self):
        """Thirty days from 1 January 2023 is still 2023, so the year holds."""
        assert shift_partial_date(_d("2023", "year"), 30).value == "2023"

    def test_a_large_enough_offset_does_move_a_year(self):
        assert shift_partial_date(_d("2023", "year"), 400).value == "2024"

    def test_precision_survives_every_shift(self):
        for value, resolution in (
            ("2023-11-13", "day"),
            ("2023-11", "month"),
            ("2023", "year"),
        ):
            moved = shift_partial_date(_d(value, resolution), 97)
            assert moved.resolution == resolution

    def test_a_context_year_does_not_move_at_all(self):
        """It names the population the contextual counts came from, and those
        counts are published unchanged. Shifting the year would reattribute
        real counts to a year they did not come from, so the year is frozen
        and the *offset* is constrained instead — see the country-year
        population tests."""
        from llm_dup_dev.review_html.evidence.publication import scramble as mod

        assert not hasattr(mod, "shift_year")


class TestTheWorkedExample:
    """The illustrative table from the specification, offset by thirty days.

    ==========================  ===========  ===========
    Feature                     Original     Scrambled
    ==========================  ===========  ===========
    Adverse event onset, A      2023-11-13   2023-12-13
    Adverse event onset, B      2023-11-12   2023-12-12
    Drug start, both            2023         2023
    Report date, both           2024-02-20   2024-03-21
    ==========================  ===========  ===========
    """

    DATES = [
        _d("2023-11-13", "day"),
        _d("2023-11-12", "day"),
        _d("2023", "year"),
        _d("2024-02-20", "day"),
    ]

    def test_each_value_comes_out_as_the_table_says(self):
        assert [shift_partial_date(d, 30).value for d in self.DATES] == [
            "2023-12-13",
            "2023-12-12",
            "2023",
            "2024-03-21",
        ]

    def test_the_one_day_difference_between_the_onsets_is_preserved(self):
        a = shift_partial_date(self.DATES[0], 30)
        b = shift_partial_date(self.DATES[1], 30)
        assert (
            int(a.value[-2:]) - int(b.value[-2:])
            == int(self.DATES[0].value[-2:]) - int(self.DATES[1].value[-2:])
            == 1
        )

    def test_the_offset_is_accepted(self):
        assert check_date_offset(self.DATES, 30, PublicationPolicy()).accepted

    def test_but_the_year_only_start_moving_differently_is_reported(self):
        """The year-only date keeps its anchor while the complete dates move,
        so its plausible distance from them changes. The page states no such
        distance, so this is an advisory and not a rejection — but it is not
        swallowed either."""
        report = check_date_offset(self.DATES, 30, PublicationPolicy())
        assert [a.rule for a in report.advisories] == ["unasserted_separation"]
        assert "51 to 81 days" in report.advisories[0].detail


class TestWhatAnOffsetMayNotChange:
    """Each rejection rule, one class of thing it protects."""

    def test_two_dates_written_alike_stay_alike(self):
        dates = [_d("2023-11-13", "day"), _d("2023-11-13", "day")]
        assert check_date_offset(dates, 30, PublicationPolicy()).accepted

    def test_a_quoted_separation_may_not_leave_its_tolerance(self):
        """With a finding stating the gap, an offset that moves it too far is
        refused where the same offset would otherwise be taken."""
        dates = TestTheWorkedExample.DATES
        asserted = [(_d("2023", "year"), _d("2024-02-20", "day"))]
        report = check_date_offset(
            dates, 30, PublicationPolicy(), asserted=asserted
        )
        assert not report.accepted
        assert [f.rule for f in report.failures] == ["separation"]

    def test_a_quoted_separation_may_move_inside_its_tolerance(self):
        """51 days becoming 56 is within a fifth and in the same severity
        band, so the finding still says what it said."""
        dates = TestTheWorkedExample.DATES
        asserted = [(_d("2023", "year"), _d("2024-02-20", "day"))]
        assert check_date_offset(
            dates, 5, PublicationPolicy(), asserted=asserted
        ).accepted

    def test_an_offset_that_reorders_two_dates_is_refused(self):
        """Two complete dates swapping places changes what the page says."""
        dates = [_d("2024-01-10", "day"), _d("2024-01-20", "day")]
        shifted = [_d("2024-01-10", "day"), _d("2024-01-05", "day")]
        assert date_relations(dates) != date_relations(shifted)

    def test_a_date_leaving_a_coarse_span_is_an_advisory_by_default(self):
        """A complete date pushed out of a year-only date's span changes
        whether one could have preceded the other — but the year-only date
        is the one that did not move, and the alternative is a history that
        can only shift by whole years."""
        dates = [_d("2024", "year"), _d("2024-12-20", "day")]
        report = check_date_offset(dates, 30, PublicationPolicy())
        assert report.accepted
        assert "coarse_date_drift" in {a.rule for a in report.advisories}

    def test_and_a_hard_failure_where_the_policy_forbids_the_drift(self):
        dates = [_d("2024", "year"), _d("2024-12-20", "day")]
        report = check_date_offset(
            dates, 30, PublicationPolicy(allow_coarse_date_drift=False)
        )
        assert not report.accepted
        assert "interval_order" in {f.rule for f in report.failures}

    def test_a_finding_that_states_the_gap_still_forbids_it(self):
        """An asserted separation is a figure on the page, so it holds
        whatever the drift policy says."""
        dates = [_d("2024", "year"), _d("2024-12-20", "day")]
        report = check_date_offset(
            dates, 30, PublicationPolicy(), asserted=[(dates[0], dates[1])]
        )
        assert not report.accepted

    def test_an_offset_outside_the_era_window_is_refused(self):
        dates = [_d("1960-01-10", "day")]
        report = check_date_offset(dates, -100, PublicationPolicy())
        assert "plausibility" in {f.rule for f in report.failures}

    def test_an_offset_that_moves_nothing_is_allowed(self):
        """A year-only date shifted by a month publishes as reported, and that
        is a legitimate outcome. Requiring movement would tell anyone holding
        the policy that the displayed year is not the true one."""
        assert check_date_offset(
            [_d("2020", "year")], 30, PublicationPolicy()
        ).accepted

    def test_a_zero_offset_is_allowed(self):
        assert check_date_offset(
            TestTheWorkedExample.DATES, 0, PublicationPolicy()
        ).accepted

    def test_a_policy_may_insist_on_movement_and_then_this_is_refused(self):
        """The knob exists so the cost of the old behaviour is visible, not
        because the old behaviour was right."""
        policy = PublicationPolicy(allow_unchanged_values=False)
        report = check_date_offset([_d("2020", "year")], 30, policy)
        assert "effectiveness" in {f.rule for f in report.failures}

    def test_an_empty_case_has_nothing_to_break(self):
        assert check_date_offset([], 30, PublicationPolicy()).accepted


class TestPlausibilityWindow:
    def test_a_date_inside_the_window_passes(self):
        assert within_plausible_window(_d("2020", "year"), PublicationPolicy())

    def test_a_year_past_the_ceiling_does_not(self):
        policy = PublicationPolicy(date_year_ceiling=2019)
        assert not within_plausible_window(_d("2020", "year"), policy)

    def test_a_year_before_the_floor_does_not(self):
        policy = PublicationPolicy(date_year_floor=2021)
        assert not within_plausible_window(_d("2020", "year"), policy)


class TestCandidateOffsets:
    """Deterministic, never trivial, and never a quiet anniversary."""

    def test_the_same_seed_gives_the_same_order(self):
        policy = PublicationPolicy()
        assert offset_candidates(policy, "s") == offset_candidates(policy, "s")

    def test_a_different_seed_gives_a_different_order(self):
        policy = PublicationPolicy()
        assert offset_candidates(policy, "a") != offset_candidates(policy, "b")

    def test_the_first_band_holds_nothing_smaller_than_the_floor(self):
        policy = PublicationPolicy(date_offset_min_days=30)
        first, *rest = offset_tiers(policy, "s")
        assert all(abs(o) >= 30 for o in first)
        assert rest, "a floor is a preference, so the smaller band follows it"

    def test_the_bands_below_the_floor_come_after_it(self):
        """A floor that refused a case outright would turn every hard
        history into manual handling; the case that prompted the floor
        carried sixteen dates."""
        policy = PublicationPolicy(date_offset_min_days=30)
        bands = offset_tiers(policy, "s")
        assert {abs(o) for o in bands[1]} == set(range(1, 30))
        assert bands[-1] == [0]

    def test_a_floor_of_zero_is_one_band_and_then_zero(self):
        policy = PublicationPolicy(date_offset_min_days=0)
        bands = offset_tiers(policy, "s")
        assert len(bands) == 2
        assert bands[-1] == [0]

    def test_zero_is_a_candidate(self):
        """Not a fallback: a draw like any other. An offset the pool excludes
        is an offset an observer can rule out."""
        assert 0 in offset_candidates(PublicationPolicy(), "s")

    def test_a_whole_year_is_a_candidate_too(self):
        """It preserves month-and-day, which looks like nothing happened —
        and "looks unshifted" must stay indistinguishable from "is
        unshifted"."""
        offsets = set(offset_candidates(PublicationPolicy(), "s"))
        assert {365, -365} <= offsets

    def test_a_policy_that_forbids_unchanged_values_drops_zero(self):
        policy = PublicationPolicy(
            allow_unchanged_values=False, date_offset_min_days=1
        )
        assert 0 not in offset_candidates(policy, "s")


class TestTheCandidatePoolIsSearchedNotSampled:
    """The regression tests for a cap that sat below the band it protected.

    ``date_offset_candidates`` was 2048 and documented as exceeding every
    band. The clinical band is 3594 candidates, so it was sampled, and a
    sample drops whole-year multiples — which a mixed-precision history is
    often solvable only at. ``TestCandidateOffsets`` missed it because it
    asserts ``{365, -365}`` against the *report* role, whose band is 742 and
    was never truncated.
    """

    def test_every_whole_year_survives_in_the_clinical_band(self):
        policy = PublicationPolicy()
        wanted = {sign * 365 * k for k in range(1, 6) for sign in (-1, 1)}
        for seed in (f"seed{i}" for i in range(20)):
            band = offset_tiers(policy, seed, role=DateRole.CLINICAL)[0]
            assert wanted <= set(band), seed

    def test_the_default_cap_covers_every_band(self):
        policy = PublicationPolicy()
        assert policy.date_offset_candidates >= max(
            policy.offset_band_sizes().values()
        )

    def test_the_band_sizes_match_the_bands_actually_built(self):
        """The arithmetic the validator trusts is the arithmetic the pool
        uses: the two disagreeing is what shipped the defect."""
        policy = PublicationPolicy()
        sizes = policy.offset_band_sizes()
        for name, role in (
            ("report", DateRole.REPORT),
            ("clinical", DateRole.CLINICAL),
        ):
            tiers = offset_tiers(policy, "s", role=role)
            assert len(tiers[0]) == sizes[name]
            assert len(tiers[1]) == sizes[f"{name}:below_floor"]

    def test_a_cap_below_a_band_is_refused(self):
        """Loudly, at construction. The old default is now a ValueError."""
        with pytest.raises(ValidationError, match="would sample these bands"):
            PublicationPolicy(date_offset_candidates=2048)

    def test_the_refusal_names_the_band_and_the_size_it_needs(self):
        with pytest.raises(ValidationError) as caught:
            PublicationPolicy(date_offset_candidates=100)
        message = str(caught.value)
        assert "clinical needs 3594" in message
        assert "report needs 742" in message

    def test_a_narrower_range_may_use_a_smaller_cap(self):
        """The cap is not a magic number; it is a bound on the range."""
        policy = PublicationPolicy(
            clinical_offset_max_days=400, date_offset_candidates=742
        )
        assert policy.offset_band_sizes()["clinical"] == 742


class TestSolvingAWholeCase:
    """The search as the transform actually runs it."""

    def test_the_worked_example_publishes(self):
        result = scramble(publication_page(worked_example_case()))
        assert not result.blocked

    def test_the_search_prefers_an_offset_that_raises_no_advisory(self):
        """Two passes over the same order: an offset that leaves every
        relationship intact beats one that leaves only the stated ones
        intact, without either becoming a requirement."""
        result = scramble(publication_page(worked_example_case()))
        assert not result.report.advisories

    def test_a_finding_that_quotes_the_gap_still_publishes(self):
        """The gap may move within its tolerance and its severity band, so an
        offset exists where under exact preservation none did."""
        result = scramble(
            publication_page(worked_example_case(clinical_date_finding=True))
        )
        assert not result.blocked
        assert result.transforms[0].date_offset_days is not None

    def test_a_tolerance_of_zero_blocks_that_case_instead(self):
        """Fail-closed is still fail-closed: with no tolerance, no movement
        allowed and a narrow pool, there is no offset and the example is
        flagged for manual handling rather than published with an altered
        meaning."""
        policy = PublicationPolicy(
            separation_relative_tolerance=0.0,
            allow_unchanged_values=False,
            date_offset_min_days=1,
            date_offset_max_days=2,
        )
        result = scramble(
            publication_page(worked_example_case(clinical_date_finding=True)),
            policy=policy,
        )
        assert result.blocked
        assert DisclosureCode.DATE_TRANSFORM_REJECTED in {
            f.code for f in result.report.findings
        }

    def test_the_rejection_says_how_many_offsets_were_tried(self):
        policy = PublicationPolicy(
            separation_relative_tolerance=0.0,
            allow_unchanged_values=False,
            date_offset_min_days=1,
            date_offset_max_days=2,
        )
        result = scramble(
            publication_page(worked_example_case(clinical_date_finding=True)),
            policy=policy,
        )
        rejection = next(
            f
            for f in result.report.findings
            if f.code is DisclosureCode.DATE_TRANSFORM_REJECTED
        )
        assert "solved together or apart" in rejection.message
        # Both schemes, both passes, every band: the count is the work done,
        # and the point of recording it is that nothing was left untried.
        assert result.transforms[0].date_candidates_tried > 8

    def test_the_same_salt_gives_the_same_offset(self):
        first = scramble(publication_page(worked_example_case()))
        second = scramble(publication_page(worked_example_case()))
        assert (
            first.transforms[0].date_offset_days
            == second.transforms[0].date_offset_days
        )

    def test_a_different_salt_gives_a_different_offset(self):
        first = scramble(publication_page(worked_example_case()), salt="one")
        second = scramble(publication_page(worked_example_case()), salt="two")
        assert (
            first.transforms[0].date_offset_days
            != second.transforms[0].date_offset_days
        )


class TestSeverityBands:
    """The thresholds a clinical-date verdict turns on, and nothing else.

    Imported from the pipeline rather than re-spelled here, so a change to
    the assessment's own grading cannot leave the transform grading
    differently without anything noticing.
    """

    def test_the_boundaries_come_from_the_pipeline(self):
        from llm_dup_dev.feature_generation.clinical_date_inconsistencies import (  # noqa: E501
            _CLINICAL_DATE_MINOR_DAYS,
            _CLINICAL_DATE_MODERATE_DAYS,
            _DECISIVE_IMPOSSIBILITY_GRACE_DAYS,
        )

        assert set(SEVERITY_BOUNDARY_DAYS) == {
            0,
            _CLINICAL_DATE_MINOR_DAYS,
            _DECISIVE_IMPOSSIBILITY_GRACE_DAYS,
            _CLINICAL_DATE_MODERATE_DAYS,
        }

    def test_zero_is_its_own_band(self):
        """Two dates that could be the same date are a different statement
        from two a day apart."""
        assert severity_band(0) != severity_band(1)

    def test_a_week_and_a_week_and_a_day_are_different_bands(self):
        assert severity_band(7) != severity_band(8)


class TestWhatASeparationMustKeep:
    POLICY = PublicationPolicy()

    def test_zero_stays_zero(self):
        assert separation_preserved(0, 0, self.POLICY)
        assert not separation_preserved(0, 1, self.POLICY)

    def test_a_one_day_discrepancy_is_preserved_to_the_day(self):
        """Evidence a reader weighs directly, so it is never rounded."""
        assert separation_preserved(1, 1, self.POLICY)
        assert not separation_preserved(1, 2, self.POLICY)

    def test_the_whole_minor_band_is_exact_by_default(self):
        assert not separation_preserved(7, 8, self.POLICY)
        assert not separation_preserved(5, 6, self.POLICY)

    def test_a_large_separation_may_move_within_a_fifth(self):
        assert separation_preserved(796, 900, self.POLICY)
        assert not separation_preserved(796, 1200, self.POLICY)

    def test_but_never_across_a_severity_boundary(self):
        """76 days is within a fifth of 75 and is a different severity, so
        the band binds where the tolerance would not."""
        assert not separation_preserved(75, 76, self.POLICY)
        assert not separation_preserved(100, 70, self.POLICY)

    def test_the_tolerance_is_configurable(self):
        tight = PublicationPolicy(separation_relative_tolerance=0.01)
        assert not separation_preserved(796, 900, tight)

    def test_the_exact_window_is_configurable(self):
        wide = PublicationPolicy(separation_exact_days=60)
        assert not separation_preserved(51, 56, wide)
        assert separation_preserved(51, 56, PublicationPolicy())
