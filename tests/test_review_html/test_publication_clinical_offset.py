"""Two offsets: the report dates, and the clinical history.

They are independent in the source — a report date says when a report
reached the centre, a clinical date says when something happened to the
patient, and reports routinely arrive years after the event — and they are
differently constrained in the page. A report date is pinned near its
reporting-context year. A clinical history mixing year-, month- and
day-precision dates can usually only move by whole years, because anything
else moves a bare ``2006`` relative to a ``2006-05-30``.

Under one offset those two demands met only at zero, and a real sixteen-date
history was published exactly as reported. These check the split that fixes
it, and the fences around it.
"""

from __future__ import annotations

from llm_dup_dev.models.enums import DrugRole, ReportingContextLevel
from llm_dup_dev.models.pair_features import (
    FeatureContribution,
    ProbabilityEstimate,
    ReportingContext,
)
from llm_dup_dev.models.report import (
    AdverseEvent,
    AgeValue,
    DateValue,
    Drug,
    PartialDate,
    Report,
    SourcedValue,
)
from llm_dup_dev.review_html.evidence.build import build_evidence_case
from llm_dup_dev.review_html.evidence.models import ValueKind
from llm_dup_dev.review_html.evidence.publication import (
    DateRole,
    DisclosureCode,
    PublicationPolicy,
    build_key_record,
    check_cross_block,
    offset_candidates,
)
from llm_dup_dev.review_html.evidence.publication.substitute import walk_cells
from tests.test_review_html.conftest import (
    _classification,
    _features,
    publication_page,
    scramble,
    worked_example_case,
)

#: The clinical history of the pair that prompted this, in its own shape: a
#: decade of drug exposure at three precisions, every date years before the
#: reports describing it.
HISTORY: tuple[tuple[str, str, str, str], ...] = (
    # substance, start, end, resolution of both
    ("metformin", "1999", "2002", "year"),
    ("ramipril", "2003", "2005", "year"),
    ("atorvastatin", "2006-05", "2006-12", "month"),
    ("warfarin", "2006-05-30", "2006-06-14", "day"),
    ("digoxin", "2006-06-06", "2006-12-05", "day"),
    ("furosemide", "2007-06-05", "2007-10-09", "day"),
)


def _long_history_report(report_id: str, *, report_date: str, onset: str) -> Report:
    return Report(
        report_id=report_id,
        patient_sex_values=[SourcedValue(value="female", sources=["structured"])],
        patient_age_values=[AgeValue(value=74, unit="years", sources=["structured"])],
        country_of_origin_values=[SourcedValue(value="SE", sources=["structured"])],
        report_date_values=[
            DateValue(value=report_date, resolution="day", sources=["structured"])
        ],
        drugs=[
            Drug(
                substance=substance,
                sources=["structured"],
                role=DrugRole.SUSPECTED,
                start_date=PartialDate(value=start, resolution=resolution),
                end_date=PartialDate(value=end, resolution=resolution),
            )
            for substance, start, end, resolution in HISTORY
        ],
        adverse_events=[
            AdverseEvent(
                pt_name="hepatic failure",
                sources=["structured"],
                start_date=PartialDate(value=onset, resolution="day"),
            )
        ],
    )


def long_history_case(*, context_year: int | None = 2011):
    """Two reports of one long history, arriving years after it."""
    r1 = _long_history_report("L1", report_date="2011-01-31", onset="2007-10-09")
    r2 = _long_history_report("L2", report_date="2011-02-14", onset="2007-10-09")
    context = (
        ReportingContext(
            level=ReportingContextLevel.COUNTRY_YEAR, country="SE", year=context_year
        )
        if context_year is not None
        else ReportingContext(level=ReportingContextLevel.COUNTRY, country="SE")
    )
    return build_evidence_case(
        report_1=r1,
        report_2=r2,
        pair_id="L1__L2",
        features=_features(
            pair_id="L1__L2",
            matching_drugs=["warfarin", "digoxin"],
            matching_AEs=["hepatic failure"],
            reporting_context=context,
            probability=ProbabilityEstimate(
                p_joint=1e-9,
                p_match=1e-9,
                feature_contributions=[
                    FeatureContribution(
                        feature="patient_sex", p_f=0.5, method="domain_size"
                    )
                ],
            ),
        ),
        classification=_classification(pair_id="L1__L2"),
    )


def _published(case, kind: ValueKind) -> set[str]:
    return {
        component.strip()
        for _, cell in walk_cells(case)
        if cell.kind is kind
        for component in cell.text.split(",")
        if component.strip()
    }


class TestALongHistoryNoLongerPublishesItself:
    def test_the_clinical_dates_move(self):
        result = scramble(publication_page(long_history_case()))
        assert not result.blocked, result.report.summary()
        transform = result.transforms[0]
        assert transform.clinical_offset_days is not None
        assert transform.clinical_offset_days != 0
        assert not transform.date_pattern_unchanged

    def test_the_clinical_offset_is_a_whole_number_of_years(self):
        """Nothing else keeps a bare 2006 in the same relation to a
        2006-05-30 — so a day-precision date comes out on the same day of
        the same month, in another year."""
        result = scramble(publication_page(long_history_case()))
        table = result.transforms[0].table
        day_dates = [
            start for _, start, _, resolution in HISTORY if resolution == "day"
        ]
        assert day_dates
        for original in day_dates:
            published = table.published_for(original)
            assert published is not None
            assert published[4:] == original[4:]
            assert published[:4] != original[:4]

    def test_it_may_move_either_way(self):
        """Forward is safe because the guard is checked directly, so the
        direction itself is not restricted — see the guard tests below."""
        policy = PublicationPolicy()
        assert not policy.clinical_offset_backward_only

    def test_the_two_blocks_took_different_offsets(self):
        transform = scramble(publication_page(long_history_case())).transforms[0]
        assert transform.date_offset_days != transform.clinical_offset_days

    def test_every_clinical_date_moved(self):
        """Not that no original *string* is on the page — shifting a decade
        back maps 2006 onto 2002, which was also reported — but that no date
        is published where it was reported."""
        result = scramble(publication_page(long_history_case()))
        table = result.transforms[0].table
        for _, start, end, _ in HISTORY:
            for original in (start, end):
                entry = table.entries[original]
                assert entry.changed, f"{original} was published as reported"

    def test_one_offset_cannot_reach_a_year_at_all(self):
        """The behaviour this replaced, kept as a test so the difference is
        legible rather than remembered. The report dates hold the offset
        inside a year, and a year-only clinical date needs more than that to
        move, so it is published exactly as reported."""
        coupled = scramble(
            publication_page(long_history_case()),
            policy=PublicationPolicy(decouple_clinical_dates=False),
        )
        offset = coupled.transforms[0].date_offset_days
        assert offset is not None
        assert abs(offset) <= PublicationPolicy().date_offset_max_days

        year_only = [
            start for _, start, _, resolution in HISTORY if resolution == "year"
        ]
        assert year_only
        table = coupled.transforms[0].table
        assert not any(table.entries[value].changed for value in year_only)

        split = scramble(publication_page(long_history_case())).transforms[0]
        assert all(split.table.entries[value].changed for value in year_only)


class TestTheFencesAroundIt:
    def test_the_report_dates_stay_in_their_context_year(self):
        result = scramble(publication_page(long_history_case(context_year=2011)))
        published = _published(result.page.cases[0], ValueKind.REPORT_DATE)
        assert published
        assert all(value.startswith("2011") for value in published)

    def test_no_event_moves_past_the_report_describing_it(self):
        result = scramble(publication_page(long_history_case()))
        case = result.page.cases[0]
        latest_clinical = max(
            _published(case, ValueKind.DRUG_END_DATE)
            | _published(case, ValueKind.AE_ONSET_DATE)
        )
        assert latest_clinical < min(_published(case, ValueKind.REPORT_DATE))

    def test_a_finding_spanning_the_two_blocks_couples_them(self):
        """Its figure has to survive exactly, which only one shared offset
        can promise."""
        result = scramble(
            publication_page(worked_example_case(clinical_date_finding=True))
        )
        transform = result.transforms[0]
        assert transform.date_offset_days == transform.clinical_offset_days


class TestTheGuardThatMakesForwardSafe:
    """A clinical date stays before the report date of its own report.

    Checking that directly is what lets the clinical block move forwards.
    Restricting the direction instead would halve the pool for a case that
    may have little room in it, and buy nothing the guard does not promise.
    """

    def test_both_directions_are_in_the_clinical_pool(self):
        pool = offset_candidates(
            PublicationPolicy(), "s", role=DateRole.CLINICAL
        )
        assert any(o > 0 for o in pool)
        assert any(o < 0 for o in pool)

    def test_a_shift_carrying_an_event_past_its_report_is_refused(self):
        onset = PartialDate(value="2011-01-10", resolution="day")
        reported = PartialDate(value="2011-02-01", resolution="day")
        verdict = check_cross_block([onset], [reported], 60, 0)
        assert not verdict.accepted
        assert "clinical_before_report" in {f.rule for f in verdict.failures}

    def test_a_shift_that_keeps_it_before_is_allowed(self):
        onset = PartialDate(value="2011-01-10", resolution="day")
        reported = PartialDate(value="2011-02-01", resolution="day")
        assert check_cross_block([onset], [reported], 10, 0).accepted

    def test_the_other_report_s_date_does_not_hold_it(self):
        """Per report, because that is the relationship the page states."""
        onset = PartialDate(value="2010-10-01", resolution="day")
        own = PartialDate(value="2011-06-01", resolution="day")
        other = PartialDate(value="2010-11-01", resolution="day")
        assert check_cross_block([onset], [own], 100, 0).accepted
        assert not check_cross_block([onset], [other], 100, 0).accepted

    def test_every_published_event_precedes_its_own_report(self):
        result = scramble(publication_page(long_history_case()))
        case = result.page.cases[0]
        events = _published(case, ValueKind.DRUG_END_DATE) | _published(
            case, ValueKind.AE_ONSET_DATE
        )
        assert max(events) < min(_published(case, ValueKind.REPORT_DATE))


class TestACoarseDateMayStayPut:
    """A year-only value does not move under a shift smaller than a year.

    With that treated as a hard failure, a mixed-precision history could only
    take whole-year offsets. It is an advisory instead: an unmoved year is
    close to no disclosure on its own, and the precise dates beside it do
    move.
    """

    def test_a_whole_year_is_still_preferred_where_it_is_available(self):
        """It raises no advisory at all, and the first pass takes that."""
        transform = scramble(publication_page(long_history_case())).transforms[0]
        assert not transform.advisories
        assert transform.clinical_offset_days is not None
        assert abs(transform.clinical_offset_days) >= 365

    def test_a_smaller_offset_is_available_when_a_year_is_not(self):
        policy = PublicationPolicy(
            clinical_offset_min_days=30, clinical_offset_max_days=120
        )
        result = scramble(publication_page(long_history_case()), policy=policy)
        transform = result.transforms[0]
        assert not result.blocked
        assert transform.clinical_offset_days is not None
        assert abs(transform.clinical_offset_days) <= 120

    def test_a_month_precision_date_moves_with_a_modest_offset(self):
        """Only a *year* holds against one: ninety days moves 2006-12 to
        2007-03. Which is why the drift that matters is a day-level date
        leaving a year-only date's span — see test_publication_dates."""
        policy = PublicationPolicy(
            clinical_offset_min_days=30, clinical_offset_max_days=120
        )
        result = scramble(publication_page(long_history_case()), policy=policy)
        table = result.transforms[0].table
        months = [
            start for _, start, _, resolution in HISTORY if resolution == "month"
        ]
        assert months
        assert all(table.entries[value].changed for value in months)


class TestWhatTheArtifactsSay:
    def test_the_key_record_carries_both_offsets(self):
        result = scramble(publication_page(long_history_case()))
        case = build_key_record(result).cases[0]
        assert case.clinical_offset_days != case.date_offset_days
        assert case.clinical_offset_days is not None

    def test_the_footer_says_the_interval_is_not_the_real_one(self):
        result = scramble(publication_page(long_history_case()))
        assert "not the reported interval" in result.page.transform_note

    def test_a_coupled_run_says_nothing_about_intervals(self):
        result = scramble(
            publication_page(long_history_case()),
            policy=PublicationPolicy(decouple_clinical_dates=False),
        )
        assert "interval" not in result.page.transform_note


class TestASmallOffsetIsReportedRatherThanRefused:
    def test_a_case_that_only_admits_a_small_shift_says_so(self):
        """The floor is a preference. A case whose relationships survive
        nothing larger is better published four days out than refused — and
        a reviewer is told the obfuscation is weak."""
        policy = PublicationPolicy(
            date_offset_min_days=300,
            date_offset_max_days=400,
            decouple_clinical_dates=False,
        )
        result = scramble(
            publication_page(worked_example_case(clinical_date_finding=True)),
            policy=policy,
        )
        transform = result.transforms[0]
        assert not result.blocked
        assert transform.date_offset_days is not None
        assert 0 < abs(transform.date_offset_days) < 300
        assert DisclosureCode.DATE_OFFSET_BELOW_FLOOR in {
            f.code for f in result.report.advisories
        }

    def test_and_it_does_not_block(self):
        """An advisory, not a finding: the dates did move."""
        policy = PublicationPolicy(
            date_offset_min_days=300,
            date_offset_max_days=400,
            decouple_clinical_dates=False,
        )
        result = scramble(
            publication_page(worked_example_case(clinical_date_finding=True)),
            policy=policy,
        )
        assert not result.blocked
