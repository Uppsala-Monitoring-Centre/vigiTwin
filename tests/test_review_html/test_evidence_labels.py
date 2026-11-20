"""Tests for how a case is labelled and placed in its collection.

Two identifiers with different jobs. The *label* is what surrounding text
cites — the curator's own ``example_id``, stable under reordering, and the
same string as the section anchor so a reference can reach it. The *position*
is navigational, derived from the collection, and deliberately not citable:
it moves the moment an example is inserted or dropped.
"""

from __future__ import annotations

import json

import pytest

from vigitwin.review_html.evidence.build import ExamplePresentation
from vigitwin.review_html.evidence.collection import build_collection
from vigitwin.review_html.evidence.manifest import load_curated_manifest
from vigitwin.review_html.evidence.models import (
    EvidenceCaseVM,
    EvidencePageVM,
    number_cases,
)
from vigitwin.review_html.render import (
    render_evidence_page,
    write_evidence_batch_html,
    write_evidence_document,
)
from vigitwin.review_html.selection import MissingPairPolicy

from tests.test_review_html.test_evidence_build import _case, _features, _report
from tests.test_review_html.test_evidence_manifest import (  # noqa: F401
    _manifest_body,
    _write_manifest,
    run_dir,
)


def _bare(example_id: str = "", **kw) -> EvidenceCaseVM:
    return EvidenceCaseVM(pair_id="R1__R2", example_id=example_id, **kw)


def _collection(run_dir, body=None):
    return build_collection(load_curated_manifest(_write_manifest(run_dir, body)))


def _html(page: EvidencePageVM) -> str:
    return " ".join(render_evidence_page(page).split())


class TestNumberCases:
    def test_cases_are_numbered_over_the_set(self):
        numbered = number_cases([_bare("a"), _bare("b"), _bare("c")])
        assert [(c.position, c.position_total) for c in numbered] == [
            (1, 3),
            (2, 3),
            (3, 3),
        ]

    def test_the_input_is_not_mutated(self):
        original = _bare("a")
        number_cases([original])
        assert original.position is None

    def test_an_existing_position_is_kept(self):
        """A curated collection's numbering survives the batch writer."""
        placed = _bare("a", position=3, position_total=12)
        numbered = number_cases([placed, _bare("b")])
        assert (numbered[0].position, numbered[0].position_total) == (3, 12)
        assert (numbered[1].position, numbered[1].position_total) == (2, 2)

    def test_an_empty_set_is_fine(self):
        assert number_cases([]) == []


class TestPositionText:
    def test_a_place_in_a_set_reads_n_of_m(self):
        assert _bare(position=2, position_total=9).position_text == "2 of 9"

    def test_a_lone_case_is_not_one_of_one(self):
        assert _bare(position=1, position_total=1).position_text == ""

    def test_no_position_is_no_text(self):
        assert _bare().position_text == ""


class TestTheLabelComesFromTheCurator:
    def test_a_curators_id_is_the_label(self, run_dir):
        page = _collection(run_dir)
        assert [c.example_label for c in page.cases] == ["S1", "S2"]

    def test_the_label_matches_the_anchor(self, run_dir):
        """The citation property: a reference to S1 must land on "S1"."""
        page = _collection(run_dir)
        for case in page.cases:
            assert case.example_label == case.example_id

    def test_an_invented_id_gets_a_readable_label(self, run_dir):
        """``example-1`` is a usable anchor and an unusable heading."""
        body = _manifest_body()
        for example in body["examples"]:
            example.pop("example_id")
        page = _collection(run_dir, body)
        assert [c.example_id for c in page.cases] == ["example-1", "example-2"]
        assert [c.example_label for c in page.cases] == ["Example 1", "Example 2"]

    def test_a_presentation_id_is_its_own_label(self):
        case = _case(
            _report("R1"),
            _report("R2"),
            _features(),
            presentation=ExamplePresentation(example_id="S7"),
        )
        assert case.example_label == "S7"

    def test_an_unframed_case_carries_no_label(self):
        """Nothing to cite, so the heading shows no internal identifier."""
        case = _case(_report("R1"), _report("R2"), _features())
        assert case.example_label == ""
        assert case.example_id == "R1__R2"


class TestThePositionComesFromTheCollection:
    def test_a_collection_places_every_case(self, run_dir):
        page = _collection(run_dir)
        assert [c.position_text for c in page.cases] == ["1 of 2", "2 of 2"]

    def test_the_total_counts_what_renders_not_what_was_asked_for(self, run_dir):
        """A skipped example must not leave a reader hunting for it.

        The third pair is listed in the run but has no stored result, which
        is the one case ``MissingPairPolicy.SKIP`` governs — an id absent
        from the pairs artifact raises whatever the policy.
        """
        pairs_path = run_dir / "synthetic_run" / "pairs.json"
        pairs = json.loads(pairs_path.read_text())
        pairs.append(
            {
                "pair_id": "R002__R003",
                "report_1_id": "R002",
                "report_2_id": "R003",
            }
        )
        pairs_path.write_text(json.dumps(pairs))

        body = _manifest_body()
        body["examples"].append(
            {
                "example_id": "S3",
                "dataset": "synthetic_set",
                "pair_id": "R002__R003",
                "title": "No stored result",
            }
        )
        page = build_collection(
            load_curated_manifest(_write_manifest(run_dir, body)),
            on_missing=MissingPairPolicy.SKIP,
            strict=False,
        )
        assert [c.position_text for c in page.cases] == ["1 of 2", "2 of 2"]


class TestRendering:
    def test_the_label_heads_the_case(self, run_dir):
        html = _html(_collection(run_dir))
        assert '<span class="example-label">S1</span>' in html

    def test_the_label_and_the_title_are_separated(self, run_dir):
        html = _html(_collection(run_dir))
        assert "S1</span> &middot; First example" in html

    def test_the_position_is_rendered_apart_from_the_label(self, run_dir):
        html = _html(_collection(run_dir))
        assert '<span class="position">1 of 2</span>' in html
        assert '<span class="position">2 of 2</span>' in html

    def test_a_single_case_page_shows_no_position(self):
        case = _case(
            _report("R1"),
            _report("R2"),
            _features(),
            presentation=ExamplePresentation(example_id="S1", title="A pair"),
        )
        html = _html(EvidencePageVM(title="t", cases=[case]))
        assert 'class="position"' not in html
        assert '<span class="example-label">S1</span>' in html

    def test_a_labelled_case_without_a_title_still_gets_a_heading(self):
        case = _case(
            _report("R1"),
            _report("R2"),
            _features(),
            presentation=ExamplePresentation(example_id="S1"),
        )
        html = _html(EvidencePageVM(title="t", cases=[case]))
        assert '<span class="example-label">S1</span>' in html
        assert "&middot; </h2>" not in html

    def test_an_unframed_case_renders_no_label_span(self):
        case = _case(_report("R1"), _report("R2"), _features())
        assert 'class="example-label"' not in _html(
            EvidencePageVM(title="t", cases=[case])
        )


class TestTheWriters:
    """One file per case must still say where the case sits."""

    def test_each_file_of_a_batch_keeps_its_place(self, tmp_path, run_dir):
        page = _collection(run_dir)
        out = write_evidence_batch_html(page.cases, tmp_path / "batch")
        first = " ".join((out / "S1.html").read_text().split())
        second = " ".join((out / "S2.html").read_text().split())
        assert '<span class="position">1 of 2</span>' in first
        assert '<span class="position">2 of 2</span>' in second

    def test_the_index_links_by_label(self, tmp_path, run_dir):
        page = _collection(run_dir)
        out = write_evidence_batch_html(page.cases, tmp_path / "batch")
        index = " ".join((out / "index.html").read_text().split())
        assert '<a href="S1.html">S1</a>' in index

    def test_an_unnumbered_batch_is_numbered_by_the_writer(self, tmp_path):
        """An ad-hoc batch off a saved run gets the same placement."""
        cases = [
            _case(
                _report("R1"),
                _report("R2"),
                _features(),
                presentation=ExamplePresentation(example_id=f"P{n}"),
            )
            for n in (1, 2)
        ]
        out = write_evidence_batch_html(cases, tmp_path / "batch")
        assert '<span class="position">2 of 2</span>' in " ".join(
            (out / "P2.html").read_text().split()
        )

    def test_the_document_writer_numbers_too(self, tmp_path, run_dir):
        page = _collection(run_dir)
        path = write_evidence_document(
            page.cases, tmp_path / "collection.html", title="Collection"
        )
        html = " ".join(path.read_text().split())
        assert '<span class="position">1 of 2</span>' in html
        assert '<span class="position">2 of 2</span>' in html


@pytest.mark.parametrize("ordinal,total,expected", [(1, 2, "1 of 2"), (12, 12, "12 of 12")])
def test_position_text_formats(ordinal, total, expected):
    assert _bare(position=ordinal, position_total=total).position_text == expected
