"""Tests for the curated-collection CLI.

This entry point is documented in ``docs/evidence_format.md`` and
``docs/integration_guide.md``, which makes its flags a promise. It is also the
only caller of ``write_evidence_document`` and ``write_summary_drafts``, so
without these tests both are reachable only by a human running the command.
"""

from __future__ import annotations

import json

import pytest

from llm_dup_dev.pipeline.export import write_pair_results
from llm_dup_dev.review_html.evidence.cli import main

from tests.test_review_html.test_render import _make_pair_result

MANIFEST = """\
schema_version: "1.0"
title: Synthetic collection
intro: Two synthetic examples.
note: Internal evidence review, not approved for publication.
positive_class: primary

datasets:
  synthetic:
    label: Synthetic set
    reports: reports.json
    pairs: pairs.json
    pair_results: pair_results.json
    run_manifest: run_manifest.json

examples:
  - example_id: S1
    dataset: synthetic
    pair_id: R001__R002
    title: First example
    lede: Extensive matching evidence.
  - example_id: S2
    dataset: synthetic
    pair_id: R001__R003
    title: Second example
"""


@pytest.fixture
def collection(tmp_path, report_a, report_b, report_c):
    """A curated manifest beside the synthetic run it names."""
    (tmp_path / "reports.json").write_text(
        json.dumps(
            {
                r.report_id: r.model_dump(mode="json")
                for r in (report_a, report_b, report_c)
            }
        )
    )
    (tmp_path / "pairs.json").write_text(
        json.dumps(
            [
                {
                    "pair_id": "R001__R002",
                    "report_1_id": "R001",
                    "report_2_id": "R002",
                },
                {
                    "pair_id": "R001__R003",
                    "report_1_id": "R001",
                    "report_2_id": "R003",
                },
            ]
        )
    )
    write_pair_results(
        [_make_pair_result("R001__R002"), _make_pair_result("R001__R003")],
        tmp_path / "pair_results.json",
    )
    (tmp_path / "run_manifest.json").write_text(
        json.dumps(
            {
                "pipeline_config": {"epsilon": 1e-15},
                "decision_thresholds": {"lmatch_compelling": 3.0},
            }
        )
    )
    (tmp_path / "collection.yaml").write_text(MANIFEST)
    return tmp_path


class TestCollectionCLI:
    """The documented flags of ``python -m llm_dup_dev.review_html.evidence``."""

    def test_writes_one_file_per_example_plus_index(self, collection):
        out = collection / "out"
        main(
            [
                "--manifest",
                str(collection / "collection.yaml"),
                "--output-dir",
                str(out),
            ]
        )
        # Named by example id, not pair id: a collection refers to its
        # examples as S1 and S2, so its files should too.
        assert (out / "S1.html").exists()
        assert (out / "S2.html").exists()
        assert not (out / "R001__R002.html").exists()

        index = (out / "index.html").read_text(encoding="utf-8")
        assert "Synthetic collection" in index
        assert "S1.html" in index and "S2.html" in index

        page = (out / "S1.html").read_text(encoding="utf-8")
        assert "First example" in page
        assert "Extensive matching evidence." in page
        # The curated framing reaches the page, and so does the collection's
        # own note — the footer that says this is an internal review.
        assert "not approved for publication" in page

    def test_manifest_order_is_preserved(self, collection):
        """Index order is the manifest's, not the artifact's."""
        reordered = MANIFEST.replace(
            """\
  - example_id: S1
    dataset: synthetic
    pair_id: R001__R002
    title: First example
    lede: Extensive matching evidence.
  - example_id: S2
    dataset: synthetic
    pair_id: R001__R003
    title: Second example
""",
            """\
  - example_id: S2
    dataset: synthetic
    pair_id: R001__R003
    title: Second example
  - example_id: S1
    dataset: synthetic
    pair_id: R001__R002
    title: First example
""",
        )
        path = collection / "reordered.yaml"
        path.write_text(reordered)
        out = collection / "out"
        main(["--manifest", str(path), "--output-dir", str(out)])

        index = (out / "index.html").read_text(encoding="utf-8")
        assert index.index("S2.html") < index.index("S1.html")

    def test_no_index(self, collection):
        out = collection / "out"
        main(
            [
                "--manifest",
                str(collection / "collection.yaml"),
                "--output-dir",
                str(out),
                "--no-index",
            ]
        )
        assert (out / "S1.html").exists()
        assert not (out / "index.html").exists()

    def test_single_document_holds_every_case(self, collection):
        out = collection / "out"
        combined = collection / "combined.html"
        main(
            [
                "--manifest",
                str(collection / "collection.yaml"),
                "--output-dir",
                str(out),
                "--single-document",
                str(combined),
            ]
        )
        html = combined.read_text(encoding="utf-8")
        # One document, two cases: the concatenation mechanism a later
        # supplement is built on.
        assert html.count('<section class="case"') == 2
        assert "First example" in html and "Second example" in html

    def test_export_drafts_refuses_to_overwrite(self, collection):
        out = collection / "out"
        drafts = collection / "drafts.json"
        argv = [
            "--manifest",
            str(collection / "collection.yaml"),
            "--output-dir",
            str(out),
            "--export-drafts",
            str(drafts),
        ]
        main(argv)

        written = json.loads(drafts.read_text())
        assert set(written) == {"S1", "S2"}
        assert written["S1"].strip()

        # By the second run this file usually holds edited text.
        drafts.write_text(json.dumps({"S1": "edited by hand"}))
        with pytest.raises(FileExistsError):
            main(argv)
        assert json.loads(drafts.read_text()) == {"S1": "edited by hand"}

        main([*argv, "--force"])
        assert set(json.loads(drafts.read_text())) == {"S1", "S2"}


class TestCollectionCLIMissingPairs:
    """A named pair that cannot be loaded fails the build by default."""

    @pytest.fixture
    def with_unloadable_pair(self, collection):
        """A third example whose second report is absent from reports.json."""
        pairs = json.loads((collection / "pairs.json").read_text())
        pairs.append(
            {
                "pair_id": "R001__R404",
                "report_1_id": "R001",
                "report_2_id": "R404",
            }
        )
        (collection / "pairs.json").write_text(json.dumps(pairs))
        (collection / "collection.yaml").write_text(
            MANIFEST
            + """\
  - example_id: S3
    dataset: synthetic
    pair_id: R001__R404
    title: Third example
"""
        )
        return collection

    def test_fails_by_default(self, with_unloadable_pair):
        """Silently short a curated collection is the worse failure."""
        with pytest.raises(Exception) as excinfo:
            main(
                [
                    "--manifest",
                    str(with_unloadable_pair / "collection.yaml"),
                    "--output-dir",
                    str(with_unloadable_pair / "out"),
                ]
            )
        assert "R001__R404" in str(excinfo.value) or "R404" in str(excinfo.value)

    def test_skip_missing_renders_the_rest(self, with_unloadable_pair):
        out = with_unloadable_pair / "out"
        main(
            [
                "--manifest",
                str(with_unloadable_pair / "collection.yaml"),
                "--output-dir",
                str(out),
                "--skip-missing",
            ]
        )
        assert (out / "S1.html").exists()
        assert (out / "S2.html").exists()
        assert not (out / "S3.html").exists()

    def test_unknown_pair_id_always_fails(self, collection):
        """An id matching no pair is a mistake in the request, not the data."""
        (collection / "stale.yaml").write_text(
            MANIFEST.replace("pair_id: R001__R003", "pair_id: R001__R999")
        )
        with pytest.raises(ValueError, match="R001__R999"):
            main(
                [
                    "--manifest",
                    str(collection / "stale.yaml"),
                    "--output-dir",
                    str(collection / "out"),
                    "--skip-missing",
                ]
            )
