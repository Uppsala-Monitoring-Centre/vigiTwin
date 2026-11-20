"""Countries and identifiers, replaced by positional aliases.

The alias is a function of reading order, never of the value, so the
publication artifact carries no means of reversing itself. What it must carry
is the *relations*: two reports from one country still say one country, and a
report that names its counterpart still names it.
"""

from __future__ import annotations

from vigitwin.review_html.evidence.publication import (
    AliasBook,
    parse_country_scope,
)
from vigitwin.review_html.evidence.publication.aliases import (
    NON_COUNTRY_CONTEXT,
    case_slug,
)
from tests.test_review_html.conftest import (
    masking_policy,
    publication_page,
    scramble,
    worked_example_case,
)


class TestParsingACountryCell:
    """A country reaches the page in three shapes and all three must agree."""

    def test_a_bare_code(self):
        scope = parse_country_scope("SE")
        assert scope is not None
        assert (scope.country, scope.year) == ("SE", None)

    def test_the_context_cell_form(self):
        """``build._context_cell`` writes the year after a space."""
        scope = parse_country_scope("SE 2003")
        assert scope is not None
        assert (scope.country, scope.year, scope.separator) == ("SE", 2003, " ")

    def test_the_scope_column_form(self):
        """``build._scope_label`` writes it after a comma, and the published
        value keeps that spelling rather than being normalised."""
        scope = parse_country_scope("SE, 2003")
        assert scope is not None
        assert scope.separator == ", "
        assert scope.rebuild("Country 1", 2004) == "Country 1, 2004"

    def test_a_reporting_level_names_nobody(self):
        for label in NON_COUNTRY_CONTEXT:
            assert parse_country_scope(label) is None

    def test_global_is_left_alone(self):
        assert parse_country_scope("Global") is None


class TestTheAliasBook:
    def test_the_same_country_gets_the_same_alias(self):
        book = AliasBook()
        assert book.country("SE") == book.country("SE") == "Country 1"

    def test_a_different_country_gets_a_different_alias(self):
        book = AliasBook()
        assert book.country("SE") != book.country("NO")

    def test_the_pairs_own_reports_alias_as_the_page_columns(self):
        """The page already heads its columns "Report A" and "Report B", so
        an identifier naming one of them says so in the reader's own terms."""
        book = AliasBook()
        book.bind_reports(("R1", "R2"))
        assert book.identifier("R1") == "Report A"
        assert book.identifier("R2") == "Report B"

    def test_an_outside_identifier_is_numbered_separately(self):
        book = AliasBook()
        book.bind_reports(("R1", "R2"))
        assert book.identifier("XX-99") == "Identifier 1"
        assert book.identifier("YY-11") == "Identifier 2"
        assert book.identifier("XX-99") == "Identifier 1"

    def test_a_case_slug_is_positional_and_sortable(self):
        assert case_slug(3) == "pair-03"


class TestAsTheTransformRunsIt:
    def test_the_country_is_masked_everywhere_it_appears(self):
        result = scramble(
            publication_page(worked_example_case()), policy=masking_policy()
        )
        table = result.transforms[0].table
        assert table.published_for("SE") == "Country 1"

    def test_the_context_cell_keeps_its_shape(self):
        """Masked country, unshifted year, same composite form."""
        result = scramble(
            publication_page(worked_example_case()), policy=masking_policy()
        )
        published = result.transforms[0].table.published_for("SE 2024")
        assert published == "Country 1 2024"

    def test_the_context_year_still_matches_the_report_dates(self):
        """An offset that broke the agreement would be rejected before it
        reached the page, so the published context belongs to the published
        reports."""
        result = scramble(
            publication_page(worked_example_case()), policy=masking_policy()
        )
        case = result.page.cases[0]
        assert case.summary_panel is not None
        context = case.summary_panel.context
        assert context is not None
        context_year = context.text.split()[-1]
        report_date = next(
            row for row in case.characteristics if row.label == "Report date"
        )
        assert report_date.left.text[:4] == context_year

    def test_the_case_is_cited_by_position_not_by_pair(self):
        result = scramble(publication_page(worked_example_case()))
        case = result.page.cases[0]
        assert case.example_id == "pair-01"
        assert case.example_label == "Pair 1"
        assert "R1" not in case.pair_id and "R2" not in case.pair_id

    def test_two_cases_number_independently(self):
        page = publication_page(worked_example_case(), worked_example_case())
        result = scramble(page)
        assert [c.example_label for c in result.page.cases] == [
            "Pair 1",
            "Pair 2",
        ]

    def test_the_provenance_path_is_replaced(self):
        page = publication_page(worked_example_case())
        page.provenance.source = "/secure/studies/duplicates/run_manifest.yaml"
        result = scramble(page)
        assert "secure/studies" not in result.page.provenance.source
