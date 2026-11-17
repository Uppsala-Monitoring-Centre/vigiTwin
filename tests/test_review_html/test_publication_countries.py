"""Country masking is optional, and off by default.

A country is part of the reporting context vigiTwin judged rarity in, and in
a study whose reference set was drawn by country the countries are known from
the design. Masking them then costs the reader the population the counts
belong to and buys little, so the default is to show them.

The tests that carry the most weight here are the ones about the contextual
counts. Those counts are real and are published unchanged; the context label
is what says which country and which calendar year they came from. Nothing
the transform does may leave that label naming a population the counts did
not come from, and with the country shown as reported that is a claim a
reader could check.
"""

from __future__ import annotations

from llm_dup_dev.review_html.evidence.publication import (
    CountryMaskingOverride,
    DisclosureCode,
    PublicationApprovals,
    PublicationPolicy,
    build_publication_manifest,
    parse_country_scope,
)
from tests.test_review_html.conftest import (
    masking_policy,
    publication_page,
    scramble,
    worked_example_case,
)

#: The pair's report dates sit in February, so an offset has room either way
#: before it carries one of them into another calendar year.
_CONTEXT_YEAR = "2024"


def _case(**kw):
    return publication_page(worked_example_case(**kw))


def _context_text(result, index: int = 0) -> str:
    panel = result.page.cases[index].summary_panel
    assert panel is not None
    assert panel.context is not None
    return panel.context.text


class TestTheDefaultIsToShowTheCountry:
    def test_the_policy_default_is_off(self):
        assert PublicationPolicy().mask_countries is False

    def test_the_country_reaches_the_page_as_reported(self):
        result = scramble(_case())
        assert "SE" in _context_text(result)
        assert "Country" not in _context_text(result)

    def test_the_characteristics_row_agrees_with_the_context(self):
        """Consistency is the whole requirement: a retained country has to be
        the same string everywhere it appears."""
        result = scramble(_case())
        case = result.page.cases[0]
        country_row = next(
            row for row in case.characteristics if row.label == "Country"
        )
        assert country_row.left.text == "SE"
        assert country_row.right.text == "SE"
        scope = parse_country_scope(_context_text(result))
        assert scope is not None
        assert scope.country == "SE"

    def test_it_publishes_without_a_finding(self):
        """A deliberately retained country is not a disclosure defect, and
        the residual scan must not report it as one."""
        result = scramble(_case())
        assert not result.blocked
        assert not any(
            f.code
            in (
                DisclosureCode.RESIDUAL_ORIGINAL_VALUE,
                DisclosureCode.RESIDUAL_DATE_TOKEN,
            )
            for f in result.report.findings
        )

    def test_the_retention_is_recorded_as_a_decision(self):
        """It appears in the key record as a value the transform saw and
        deliberately left, so a reviewer meets it rather than having to
        notice its absence."""
        result = scramble(_case())
        entry = result.transforms[0].table.entries["SE"]
        assert entry.published == "SE"
        assert not entry.changed
        assert "retained" in entry.note


class TestMaskingWhenAskedFor:
    def test_the_country_becomes_a_positional_alias(self):
        result = scramble(_case(), policy=masking_policy())
        assert _context_text(result).startswith("Country 1")

    def test_equality_between_the_reports_survives(self):
        result = scramble(_case(), policy=masking_policy())
        case = result.page.cases[0]
        row = next(r for r in case.characteristics if r.label == "Country")
        assert row.left.text == row.right.text == "Country 1"


class TestAPerExampleOverride:
    @staticmethod
    def _override(example_id: str, mask: bool) -> PublicationApprovals:
        return PublicationApprovals(
            country_masking=[
                CountryMaskingOverride(
                    example_id=example_id,
                    mask=mask,
                    decided_by="reviewer",
                    decided_at="2026-01-01",
                    rationale="synthetic test",
                )
            ]
        )

    def test_one_example_may_be_masked_against_the_default(self):
        page = publication_page(
            worked_example_case(report_ids=("R1", "R2")),
            worked_example_case(report_ids=("R3", "R4")),
        )
        first = page.cases[0].example_id
        result = scramble(page, approvals=self._override(first, True))
        assert "Country 1" in _context_text(result, 0)
        assert "SE" in _context_text(result, 1)

    def test_one_example_may_be_retained_against_a_masking_default(self):
        page = publication_page(
            worked_example_case(report_ids=("R1", "R2")),
            worked_example_case(report_ids=("R3", "R4")),
        )
        second = page.cases[1].example_id
        result = scramble(
            page,
            policy=masking_policy(),
            approvals=self._override(second, False),
        )
        assert "Country 1" in _context_text(result, 0)
        assert "SE" in _context_text(result, 1)

    def test_the_decision_is_recorded_per_case(self):
        page = publication_page(worked_example_case())
        result = scramble(
            page, approvals=self._override(page.cases[0].example_id, True)
        )
        assert result.transforms[0].countries_masked is True


class TestTheCountryYearPopulation:
    """The counts are real; the label must go on naming their population."""

    def test_the_context_year_is_never_shifted(self):
        """Whether or not the country is masked. A shifted year would
        reattribute real counts to a year they did not come from."""
        for policy in (None, masking_policy()):
            result = scramble(_case(), policy=policy)
            assert _context_text(result).endswith(_CONTEXT_YEAR)

    def test_an_offset_that_moves_a_report_date_out_of_that_year_is_refused(
        self,
    ):
        """``reporting_context`` takes the year the two reports *share*, so
        an offset carrying either of them into another year leaves the page
        naming a country-year its own dates do not support."""
        from llm_dup_dev.review_html.evidence.publication.scramble import (
            _collect_dates,
            CaseTransform,
        )

        case = worked_example_case()
        transform = CaseTransform(
            example_id=case.example_id,
            pair_id=case.pair_id,
            position=1,
            published_example_id="pair-01",
            published_example_label="Pair 1",
        )
        guard = _collect_dates(case, transform).guard
        assert guard.year == 2024
        # The report dates are 2024-02-20; a month either way stays in 2024.
        assert guard.holds_after(30)
        assert guard.holds_after(-30)
        # Three hundred days does not.
        assert not guard.holds_after(330)
        assert not guard.holds_after(-60)

    def test_the_published_report_date_stays_in_the_context_year(self):
        result = scramble(_case())
        case = result.page.cases[0]
        row = next(
            r for r in case.characteristics if r.label == "Report date"
        )
        assert row.left.text[:4] == _CONTEXT_YEAR
        assert row.right.text[:4] == _CONTEXT_YEAR

    def test_a_case_with_no_context_year_is_unconstrained(self):
        """A global or country-level context displays no year, so there is no
        population attribution for an offset to break."""
        from llm_dup_dev.review_html.evidence.publication.scramble import (
            _ContextGuard,
        )

        assert _ContextGuard().holds_after(9999)


class TestTheManifestRecordsThePolicy:
    def test_it_states_the_default(self):
        masked = build_publication_manifest(
            scramble(_case(), policy=masking_policy())
        ).country_masking
        retained = build_publication_manifest(
            scramble(_case())
        ).country_masking
        assert masked.default_masked is True
        assert retained.default_masked is False

    def test_it_counts_the_cases_either_way(self):
        page = publication_page(
            worked_example_case(report_ids=("R1", "R2")),
            worked_example_case(report_ids=("R3", "R4")),
        )
        result = scramble(
            page,
            approvals=PublicationApprovals(
                country_masking=[
                    CountryMaskingOverride(
                        example_id=page.cases[0].example_id,
                        mask=True,
                        decided_by="reviewer",
                        decided_at="2026-01-01",
                    )
                ]
            ),
        )
        summary = build_publication_manifest(result).country_masking
        assert (summary.cases_masked, summary.cases_retained) == (1, 1)

    def test_it_says_which_a_reader_is_looking_at(self):
        summary = build_publication_manifest(
            scramble(_case())
        ).country_masking
        assert "shown as reported" in summary.statement
        assert "calendar year" in summary.statement

    def test_it_names_no_country(self):
        for policy in (None, masking_policy()):
            manifest = build_publication_manifest(
                scramble(_case(), policy=policy)
            )
            assert "SE" not in manifest.model_dump_json()
