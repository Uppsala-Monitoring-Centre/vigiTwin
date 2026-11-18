"""Both presentations, and what each page says about itself.

The internal page is unchanged — same template, same footer sentence — and
the publication page states plainly that it is a candidate and not a
certificate. The side-by-side output is the document a disclosure reviewer
works from, and it is secure-side: it prints the originals.
"""

from __future__ import annotations

from llm_dup_dev.review_html.evidence.models import (
    EvidenceCell,
    EvidencePageVM,
    PresentationMode,
    ValueKind,
)
from llm_dup_dev.review_html.evidence.publication import (
    REVIEW_FILENAME,
    PublicationPolicy,
    write_publication_evidence,
    write_side_by_side,
)
from llm_dup_dev.review_html.render import render_evidence_page
from tests.test_review_html.conftest import (
    case_with_human_text,
    publication_page,
    scramble,
    worked_example_case,
)


def _html(page: EvidencePageVM) -> str:
    return render_evidence_page(page)


class TestTheMaskedBand:
    """A publication page says what it is before anything else on it.

    The footer still states the transform in full. A footnote is read last,
    or not at all: a page lifted into a slide, a figure or a printed bundle
    arrives with transformed values, and the band is what travels with it.
    """

    def test_a_publication_page_carries_it(self):
        result = scramble(publication_page(worked_example_case()))
        html = " ".join(_html(result.page).split())
        assert ">MASKED<" in html
        assert '<div class="masked-banner"' in html

    def test_it_comes_before_the_content(self):
        result = scramble(publication_page(worked_example_case()))
        html = " ".join(_html(result.page).split())
        assert html.index('<div class="masked-banner"') < html.index(
            "<section class="
        )

    def test_it_says_select_values_and_leaves_the_rest_to_the_footer(self):
        """One line under the word, and it does not overstate the transform.

        A transform may leave a value as reported, so the band says *select*
        values. What this run actually did, and the caveat that the page is
        not a certificate, are in the footer, which is built from the
        effective policy rather than written as prose here.
        """
        result = scramble(publication_page(worked_example_case()))
        html = " ".join(_html(result.page).split())
        assert "Select displayed values have been transformed." in html
        assert "certified as anonymised" in html

    def test_an_internal_page_does_not_carry_it(self):
        """The stylesheet is shared, so the markup is what distinguishes them."""
        html = _html(publication_page(worked_example_case()))
        assert '<div class="masked-banner"' not in html
        assert ">MASKED<" not in html


class TestTheFooterStatesTheMode:
    def test_an_internal_page_still_says_it_is_not_publication_safe(self):
        """Unchanged wording: the existing presentation is not touched."""
        html = _html(publication_page(worked_example_case()))
        assert "This is an internal evidence review" in html
        assert "not a" in html and "publication-safe" in html

    def test_a_publication_page_says_what_was_done(self):
        result = scramble(publication_page(worked_example_case()))
        html = _html(result.page)
        assert "Prepared for publication" in html
        assert "transformed" in html

    def test_the_footer_names_what_this_run_actually_transformed(self):
        """Not every attribute category: the policy is configurable, and a
        footer that overstates what was done is the one piece of publication
        metadata a reader cannot check."""
        retained = scramble(publication_page(worked_example_case()))
        html = " ".join(_html(retained.page).split())
        assert "countries are shown as reported" in html

        masked = scramble(
            publication_page(worked_example_case()),
            policy=PublicationPolicy(mask_countries=True),
        )
        masked_html = " ".join(_html(masked.page).split())
        assert "countries are shown as reported" not in masked_html
        assert "and countries have been transformed" in masked_html

    def test_a_publication_page_claims_no_certificate(self):
        """The most consequential sentence on the page: a transform that
        preserved what the page states is not an anonymisation."""
        result = scramble(publication_page(worked_example_case()))
        html = _html(result.page)
        html = " ".join(html.split())
        assert "candidate for disclosure review" in html
        assert "certified as anonymised" in html

    def test_a_publication_page_drops_the_internal_disclaimer(self):
        result = scramble(publication_page(worked_example_case()))
        assert "This is an internal evidence review" not in _html(result.page)

    def test_internal_is_the_default_mode(self):
        assert EvidencePageVM(title="x").mode is PresentationMode.INTERNAL


class TestWithheldRendersAsItself:
    def test_a_withheld_cell_says_withheld_and_not_nothing(self):
        """"Not reported" and "not shown here" are different statements, and
        the reserved ``withheld`` flag is what keeps them apart."""
        page = publication_page(worked_example_case())
        page.mode = PresentationMode.PUBLICATION
        cell = page.cases[0].characteristics[0].left
        cell.text = ""
        cell.withheld = True
        assert "Withheld" in _html(page)

    def test_a_missing_cell_still_renders_blank(self):
        """An absence is not evidence, and it is not a withholding either."""
        page = publication_page(worked_example_case())
        cell = EvidenceCell(text="", kind=ValueKind.AGE, missing=True)
        page.cases[0].characteristics[0].left = cell
        assert "Withheld" not in _html(page)


class TestThePublishedPageCarriesNoOriginal:
    def test_no_original_date_country_identifier_or_age_survives(self):
        result = scramble(publication_page(worked_example_case()))
        html = _html(result.page)
        for original in (
            "2023-11-13",
            "2023-11-12",
            "2024-02-20",
            "R1",
            "R2",
            "81 years",
        ):
            assert original not in html, original

    def test_the_drug_and_adverse_event_terms_are_kept(self):
        """Clinical terminology is what the page exists to convey."""
        result = scramble(publication_page(worked_example_case()))
        html = _html(result.page)
        assert "levonorgestrel" in html
        assert "device expulsion" in html

    def test_sex_is_kept(self):
        result = scramble(publication_page(worked_example_case()))
        assert "female" in _html(result.page).lower()

    def test_the_rendered_html_is_scanned_before_it_is_written(self, tmp_path):
        """Reading, never rewriting: a page that only looks clean after its
        markup was edited is a page whose model was wrong."""
        result = scramble(publication_page(worked_example_case()))
        out = write_publication_evidence(result, tmp_path / "pub")
        assert (out / "pair-01.html").exists()


class TestSideBySide:
    def test_it_writes_both_presentations_and_the_review_page(self, tmp_path):
        page = publication_page(worked_example_case())
        result = scramble(page)
        out = write_side_by_side(page, result, tmp_path / "review")
        assert (out / "internal" / "index.html").exists()
        assert (out / "publication" / "index.html").exists()
        assert (out / REVIEW_FILENAME).exists()

    def test_the_review_page_lists_every_value_that_changed(self, tmp_path):
        page = publication_page(worked_example_case())
        result = scramble(page, policy=PublicationPolicy(mask_countries=True))
        out = write_side_by_side(page, result, tmp_path / "review")
        review = (out / REVIEW_FILENAME).read_text()
        assert "2023-11-13" in review
        assert "Country 1" in review
        assert "SE" in review

    def test_the_review_page_links_the_two_renderings(self, tmp_path):
        page = publication_page(worked_example_case())
        result = scramble(page)
        out = write_side_by_side(page, result, tmp_path / "review")
        review = (out / REVIEW_FILENAME).read_text()
        assert 'href="internal/R1__R2.html"' in review
        assert 'href="publication/pair-01.html"' in review

    def test_the_review_page_separates_moved_from_unchanged(self, tmp_path):
        """Three categories, because a reviewer has to tell a value that was
        transformed and came out the same from one no transform reached."""
        page = publication_page(worked_example_case())
        out = write_side_by_side(page, scramble(page), tmp_path / "review")
        review = (out / REVIEW_FILENAME).read_text()
        assert "Transformed — moved" in review
        assert "Failed transformations and findings" in review

    def test_the_review_page_states_when_no_date_moved(self, tmp_path):
        """The re-identification question the reviewer most needs put to
        them: this page carries the pair's real timeline."""
        page = publication_page(worked_example_case())
        result = scramble(
            page,
            policy=PublicationPolicy(
                date_offset_min_days=0, date_offset_max_days=0
            ),
        )
        out = write_side_by_side(page, result, tmp_path / "review")
        review = (out / REVIEW_FILENAME).read_text()
        assert "Every date is published as reported" in review
        assert "Transformed — unchanged" in review
        assert "0 of" in review

    def test_the_review_page_counts_the_dates_that_moved(self, tmp_path):
        page = publication_page(worked_example_case())
        out = write_side_by_side(page, scramble(page), tmp_path / "review")
        assert "date value(s) moved" in (out / REVIEW_FILENAME).read_text()

    def test_a_blocked_candidate_still_gets_a_review_page(self, tmp_path):
        """The review page is how a reviewer resolves the block, so it has to
        be written before the block is cleared."""
        page = publication_page(case_with_human_text())
        result = scramble(page)
        out = write_side_by_side(page, result, tmp_path / "review")
        review = (out / REVIEW_FILENAME).read_text()
        assert "publication export is blocked" in review
        assert "human_text_review_required" in review

    def test_the_review_page_says_it_must_not_be_published(self, tmp_path):
        page = publication_page(worked_example_case())
        out = write_side_by_side(page, scramble(page), tmp_path / "review")
        review = (out / REVIEW_FILENAME).read_text()
        assert "must not be published" in review
