"""The last look at the rendered page, and what it must not look at.

``scan_rendered_html`` exists for the one thing the model walk cannot see: a
value a template composes into markup rather than into a string field. What it
must not do is read the page's *static* regions. They hold no report value by
construction, and their prose carries figures of its own -- the stylesheet
comment explaining why a penalty weight needs a space before it quotes
``"2005 1.2"``, and a case whose year-only date of 2005 moved matched it.

That finding was unresolvable, because the rendered scan runs after
``partition``, and it was about four characters of CSS prose no reader sees.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from vigitwin.review_html.evidence.models import (
    EvidencePageVM,
    PresentationMode,
)
from vigitwin.review_html.evidence.publication.substitute import (
    SubstitutionTable,
    scan_rendered_html,
)
from vigitwin.review_html.evidence.publication import (
    verify_combined_document,
    verify_rendered_output,
    write_publication_evidence,
)
from vigitwin.review_html.render import render_evidence_page
from tests.test_review_html.conftest import (
    publication_page,
    scramble,
    worked_example_case,
)

TEMPLATES = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "vigitwin"
    / "review_html"
    / "templates"
)

#: The chain ``render_evidence_page`` renders. The compare and index templates
#: are not scanned, so their style blocks are not this invariant's business.
EVIDENCE_CHAIN = (
    "evidence_page.html.j2",
    "_evidence_case.html.j2",
    "_evidence.css.j2",
)

_STATIC = re.compile(
    r"<style\b[^>]*>.*?</style>|<script\b[^>]*>.*?</script>|<!--.*?-->",
    re.DOTALL | re.IGNORECASE,
)
_INCLUDE = re.compile(r"\{%\s*include\s+\"([^\"]+)\"\s*%\}")


def _table(original: str = "2005", published: str = "2006") -> SubstitutionTable:
    table = SubstitutionTable()
    table.add(original, published, kind="date:year")
    return table


class TestWhatTheScanReads:
    def test_a_value_in_the_body_is_a_finding(self):
        findings = scan_rendered_html(
            "<p>Reported in 2005.</p>", _table(), example_id="S2"
        )
        assert len(findings) == 1
        assert findings[0].field_path == "<rendered html>"

    def test_a_value_composed_into_an_attribute_is_a_finding(self):
        """The case the scan exists for: markup, not a string field."""
        findings = scan_rendered_html(
            '<section id="ctx-2005"><p>Clean.</p></section>',
            _table(),
            example_id="S2",
        )
        assert len(findings) == 1

    def test_the_stylesheet_is_not_read(self):
        findings = scan_rendered_html(
            '<style>/* "2005 1.2" read as one number. */</style><p>Clean.</p>',
            _table(),
            example_id="S2",
        )
        assert not findings

    def test_a_script_is_not_read(self):
        findings = scan_rendered_html(
            "<script>const since = 2005;</script><p>Clean.</p>",
            _table(),
            example_id="S2",
        )
        assert not findings

    def test_a_comment_is_not_read(self):
        findings = scan_rendered_html(
            "<!-- laid out in 2005 --><p>Clean.</p>", _table(), example_id="S2"
        )
        assert not findings

    def test_a_full_date_is_not_matched_by_its_year(self):
        """A bare year is date-shaped, so it is compared against maximal
        tokens: 2005-03-14 is not an occurrence of 2005."""
        assert not scan_rendered_html(
            "<p>Reported 2005-03-14.</p>", _table(), example_id="S2"
        )


class TestTheRealPage:
    """The regression: the stylesheet comment is in every page rendered."""

    def test_the_comment_is_still_there(self):
        html = render_evidence_page(
            EvidencePageVM(
                title="Evidence",
                mode=PresentationMode.PUBLICATION,
                cases=publication_page(worked_example_case()).cases,
            )
        )
        assert '"2005 1.2"' in html

    def test_and_a_moved_year_of_2005_no_longer_trips_on_it(self):
        html = render_evidence_page(
            EvidencePageVM(
                title="Evidence",
                mode=PresentationMode.PUBLICATION,
                cases=publication_page(worked_example_case()).cases,
            )
        )
        assert not scan_rendered_html(html, _table(), example_id="S2")


class TestStrippingThoseRegionsCannotHideALeak:
    """The invariant that makes the exemption safe rather than convenient."""

    def test_no_value_is_composed_into_a_static_region(self):
        for name in EVIDENCE_CHAIN:
            text = (TEMPLATES / name).read_text(encoding="utf-8")
            for region in _STATIC.findall(text):
                for construct in re.findall(r"\{\{.*?\}\}|\{%.*?%\}", region):
                    included = _INCLUDE.fullmatch(construct.strip())
                    assert included, (
                        f"{name} composes {construct!r} inside a region the "
                        "rendered scan does not read"
                    )
                    partial = (TEMPLATES / included.group(1)).read_text(
                        encoding="utf-8"
                    )
                    assert "{{" not in partial and "{%" not in partial, (
                        f"{included.group(1)} is included into a static "
                        "region and now carries Jinja of its own"
                    )


class TestTheScanReadsWhatIsWritten:
    """The premise of the whole pass, and it was not holding.

    The probe page carried the title and the cases alone, so the collection
    intro, the note, the transform note and the per-sheet footer were never
    read — while `write_publication_evidence` rendered all of them. Worse,
    the CLI handed the writer the *pre-transform* intro, so a collection
    introduction naming a date or a country reached every published page
    unrewritten and nothing looked at it.
    """

    @staticmethod
    def _page_with_intro(intro: str) -> EvidencePageVM:
        page = publication_page(worked_example_case())
        page.intro = intro
        return page

    def test_a_value_in_the_collection_intro_is_rewritten(self):
        """It goes through the union table like any other derived prose."""
        result = scramble(self._page_with_intro("Both describe 2023-11-13."))
        assert "2023-11-13" not in result.page.intro

    def test_an_original_left_in_the_intro_is_a_finding(self):
        """Belt as well as braces: if a value survived the rewrite, the
        rendered scan has to be the thing that notices."""
        result = scramble(self._page_with_intro("Both describe 2023-11-13."))
        result.page.intro = "Both describe 2023-11-13."  # as if unrewritten
        report = verify_rendered_output(result)
        assert report.blocked
        assert any(
            "2023-11-13" in f.detail or "2023-11-13" in f.message
            for f in report.findings
        )

    def test_the_writer_takes_the_intro_from_the_result(self, tmp_path):
        """Not from its caller. The caller had the untransformed copy, and
        there was no caller for which that was the right text."""
        import inspect

        signature = inspect.signature(write_publication_evidence)
        for gone in ("index_title", "collection_intro", "collection_note"):
            assert gone not in signature.parameters

    def test_the_published_pages_carry_the_rewritten_intro(self, tmp_path):
        result = scramble(self._page_with_intro("Both describe 2023-11-13."))
        out = write_publication_evidence(result, tmp_path / "publication")
        for path in out.rglob("*.html"):
            assert "2023-11-13" not in path.read_text(encoding="utf-8")


class TestTheCombinedDocumentIsScanned:
    """One render, each case's slice with its own table."""

    def test_a_planted_original_is_found_in_its_own_slice(self):
        page = publication_page(worked_example_case(), worked_example_case())
        result = scramble(page)
        # Put one case's original back into its own published markup.
        original = next(
            s.original
            for s in result.transforms[0].table.entries.values()
            if s.original != s.published
        )
        result.page.cases[0].title = f"Leaked {original}"
        findings = verify_combined_document(result)
        assert findings
        assert {f.example_id for f in findings} == {
            result.transforms[0].example_id
        }

    def test_one_cases_published_value_is_not_a_leak_in_another(self):
        """The merged-table regression guard. Each pair has its own offset,
        so a value case B published is routinely a value case A reported —
        and a single merged table would call that a leak."""
        page = publication_page(worked_example_case(), worked_example_case())
        result = scramble(page)
        assert verify_combined_document(result) == []

    def test_the_slice_count_must_match_the_transforms(self):
        """A misaligned slice would quietly check the wrong case, so it fails
        loudly instead."""
        page = publication_page(worked_example_case(), worked_example_case())
        result = scramble(page)
        result.page.cases.pop()
        with pytest.raises(AssertionError, match="case section"):
            verify_combined_document(result)
