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

from llm_dup_dev.review_html.evidence.models import (
    EvidencePageVM,
    PresentationMode,
)
from llm_dup_dev.review_html.evidence.publication.substitute import (
    SubstitutionTable,
    scan_rendered_html,
)
from llm_dup_dev.review_html.render import render_evidence_page
from tests.test_review_html.conftest import publication_page, worked_example_case

TEMPLATES = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "llm_dup_dev"
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
