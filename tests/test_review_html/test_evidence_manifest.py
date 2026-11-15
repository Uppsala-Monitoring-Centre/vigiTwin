"""Tests for the curated example manifest and the collection it builds.

Everything here is synthetic. A real collection carries original identifiers
and clinical interpretations and is maintained outside this repository.
"""

from __future__ import annotations

import json

import pytest
import yaml

from llm_dup_dev.pipeline.export import write_pair_results
from llm_dup_dev.review_html.evidence.collection import (
    build_collection,
    export_summary_drafts,
    write_summary_drafts,
)
from llm_dup_dev.review_html.evidence.manifest import (
    CuratedExampleManifest,
    SummaryMode,
    load_curated_manifest,
    write_curated_manifest,
)
from llm_dup_dev.review_html.selection import MissingPairPolicy

from tests.test_review_html.test_render import _make_pair_result


@pytest.fixture
def run_dir(tmp_path, report_a, report_b, report_c):
    """A synthetic saved run of two pairs."""
    run = tmp_path / "synthetic_run"
    run.mkdir()
    (run / "reports.json").write_text(
        json.dumps(
            {
                r.report_id: r.model_dump(mode="json")
                for r in (report_a, report_b, report_c)
            }
        )
    )
    (run / "pairs.json").write_text(
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
        run / "pair_results.json",
    )
    (run / "run_manifest.json").write_text(
        json.dumps(
            {
                "pipeline_config": {"epsilon": 1e-15},
                "decision_thresholds": {"lmatch_compelling": 3.0},
            }
        )
    )
    return tmp_path


def _manifest_body(**overrides):
    body = {
        "title": "Illustrative report pairs",
        "datasets": {
            "synthetic_set": {
                "label": "Synthetic set",
                "reports": "synthetic_run/reports.json",
                "pairs": "synthetic_run/pairs.json",
                "pair_results": "synthetic_run/pair_results.json",
                "run_manifest": "synthetic_run/run_manifest.json",
            }
        },
        "examples": [
            {
                "example_id": "S1",
                "dataset": "synthetic_set",
                "pair_id": "R001__R002",
                "title": "First example",
            },
            {
                "example_id": "S2",
                "dataset": "synthetic_set",
                "pair_id": "R001__R003",
                "title": "Second example",
            },
        ],
    }
    body.update(overrides)
    return body


def _write_manifest(run_dir, body=None, name="collection.yaml"):
    path = run_dir / name
    payload = body if body is not None else _manifest_body()
    if path.suffix == ".json":
        path.write_text(json.dumps(payload))
    else:
        path.write_text(yaml.safe_dump(payload, sort_keys=False))
    return path


class TestFormats:
    """YAML by default; JSON accepted; both validate identically."""

    def test_yaml_round_trip(self, run_dir, tmp_path):
        loaded = load_curated_manifest(_write_manifest(run_dir))
        out = write_curated_manifest(tmp_path / "out.yaml", loaded.manifest)
        again = CuratedExampleManifest.model_validate(
            yaml.safe_load(out.read_text())
        )
        assert again.title == loaded.manifest.title
        assert [e.example_id for e in again.examples] == ["S1", "S2"]

    def test_json_is_accepted(self, run_dir):
        loaded = load_curated_manifest(
            _write_manifest(run_dir, name="collection.json")
        )
        assert [e.example_id for e in loaded.manifest.examples] == ["S1", "S2"]

    def test_only_safe_load_is_used(self):
        """A manifest is data; the loader that builds objects has no place here."""
        from pathlib import Path

        src = Path("src/llm_dup_dev").rglob("*.py")
        offenders = [
            str(p) for p in src if "yaml.load(" in p.read_text(encoding="utf-8")
        ]
        assert offenders == []

    def test_committed_example_manifest_validates(self):
        loaded = load_curated_manifest(
            "examples/evidence_collection_example.yaml", check_artifacts=False
        )
        assert loaded.manifest.examples


class TestPathResolution:
    """Relative paths resolve against the manifest, so a collection can move."""

    def test_relative_paths_resolve(self, run_dir):
        loaded = load_curated_manifest(_write_manifest(run_dir))
        paths = loaded.dataset_paths("synthetic_set")
        assert paths["reports_path"] == run_dir / "synthetic_run/reports.json"

    def test_pair_frequencies_maps_to_the_loader_kwarg(self, run_dir):
        """The manifest field is named for the artifact, the kwarg for the API."""
        (run_dir / "synthetic_run" / "pair_frequencies.json").write_text("{}")
        body = _manifest_body()
        body["datasets"]["synthetic_set"]["pair_frequencies"] = (
            "synthetic_run/pair_frequencies.json"
        )
        loaded = load_curated_manifest(_write_manifest(run_dir, body))
        paths = loaded.dataset_paths("synthetic_set")
        assert paths["pair_frequencies_path"] == (
            run_dir / "synthetic_run/pair_frequencies.json"
        )

    def test_the_former_bundles_key_is_rejected(self, run_dir):
        """Renamed in 1.1.1 with no alias, so a stale manifest fails loudly."""
        body = _manifest_body()
        body["datasets"]["synthetic_set"]["bundles"] = "synthetic_run/b.json"
        with pytest.raises(ValueError, match="bundles"):
            load_curated_manifest(_write_manifest(run_dir, body))

    def test_every_missing_artifact_is_named_at_once(self, run_dir):
        body = _manifest_body()
        body["datasets"]["synthetic_set"]["reports"] = "nope/reports.json"
        body["datasets"]["synthetic_set"]["pairs"] = "nope/pairs.json"
        with pytest.raises(FileNotFoundError) as excinfo:
            load_curated_manifest(_write_manifest(run_dir, body))
        message = str(excinfo.value)
        assert "nope/reports.json" in message
        assert "nope/pairs.json" in message


class TestValidation:
    """The manifest refuses requests that cannot mean what they say."""

    def test_unknown_dataset_reference(self, run_dir):
        body = _manifest_body()
        body["examples"][0]["dataset"] = "missing_set"
        with pytest.raises(ValueError, match="missing_set"):
            load_curated_manifest(_write_manifest(run_dir, body))

    def test_duplicate_example_ids(self, run_dir):
        body = _manifest_body()
        body["examples"][1]["example_id"] = "S1"
        with pytest.raises(ValueError, match="duplicate example_id"):
            load_curated_manifest(_write_manifest(run_dir, body))

    def test_filename_collision_is_rejected(self, run_dir):
        """Two ids reducing to one filename would overwrite each other.

        Both ids here are well-formed anchors, so the shape check passes and
        the collision check is what catches them — ``.`` is the one allowed
        character that ``_safe_filename`` rewrites.
        """
        body = _manifest_body()
        body["examples"][0]["example_id"] = "S.1"
        body["examples"][1]["example_id"] = "S_1"
        with pytest.raises(ValueError, match="collide"):
            load_curated_manifest(_write_manifest(run_dir, body))

    @pytest.mark.parametrize("bad", ["S 1", "S/1", "1S", "", "-S1", "S#1"])
    def test_an_id_that_cannot_be_an_anchor_is_rejected(self, run_dir, bad):
        """The id is an HTML anchor and a URL fragment, not just a name."""
        body = _manifest_body()
        body["examples"][0]["example_id"] = bad
        with pytest.raises(ValueError, match="section anchor"):
            load_curated_manifest(_write_manifest(run_dir, body))

    @pytest.mark.parametrize("good", ["S1", "S-1", "S_1", "S1.a", "Example12"])
    def test_well_formed_ids_are_accepted(self, run_dir, good):
        body = _manifest_body()
        body["examples"][0]["example_id"] = good
        manifest = load_curated_manifest(_write_manifest(run_dir, body))
        assert manifest.manifest.examples[0].example_id == good

    def test_repeated_pair_is_rejected(self, run_dir):
        body = _manifest_body()
        body["examples"][1]["pair_id"] = "R001__R002"
        with pytest.raises(ValueError, match="more than once"):
            load_curated_manifest(_write_manifest(run_dir, body))

    def test_both_artifact_sources_is_rejected(self, run_dir):
        body = _manifest_body()
        body["datasets"]["synthetic_set"]["features"] = "synthetic_run/f.json"
        body["datasets"]["synthetic_set"]["classifications"] = (
            "synthetic_run/c.json"
        )
        with pytest.raises(ValueError, match="not both"):
            load_curated_manifest(_write_manifest(run_dir, body))

    def test_no_artifact_source_is_rejected(self, run_dir):
        body = _manifest_body()
        del body["datasets"]["synthetic_set"]["pair_results"]
        with pytest.raises(ValueError, match="either 'pair_results'"):
            load_curated_manifest(_write_manifest(run_dir, body))

    def test_a_reference_label_in_the_manifest_is_rejected(self, run_dir):
        """Labels come from the artifacts; restating one here could drift."""
        body = _manifest_body()
        body["examples"][0]["reference_label"] = "duplicate"
        with pytest.raises(ValueError, match="reference_label"):
            load_curated_manifest(_write_manifest(run_dir, body))

    def test_empty_examples_rejected(self, run_dir):
        body = _manifest_body()
        body["examples"] = []
        with pytest.raises(ValueError, match="at least one example"):
            load_curated_manifest(_write_manifest(run_dir, body))


class TestSCLEEntry:
    """The four states are kept honest against the issue they carry."""

    def test_assessed_needs_an_issue(self, run_dir):
        body = _manifest_body()
        body["examples"][0]["scle_assessment"] = {"state": "assessed"}
        with pytest.raises(ValueError, match="needs an issue"):
            load_curated_manifest(_write_manifest(run_dir, body))

    def test_inconclusive_implies_genuinely_ambiguous(self, run_dir):
        body = _manifest_body()
        body["examples"][0]["scle_assessment"] = {"state": "inconclusive"}
        loaded = load_curated_manifest(_write_manifest(run_dir, body))
        entry = loaded.manifest.examples[0].scle_assessment
        assert entry is not None
        assert entry.issue is not None
        assert entry.issue.value == "genuinely_ambiguous"

    def test_genuinely_ambiguous_under_assessed_is_redirected(self, run_dir):
        body = _manifest_body()
        body["examples"][0]["scle_assessment"] = {
            "state": "assessed",
            "issue": "genuinely_ambiguous",
        }
        with pytest.raises(ValueError, match="state: inconclusive"):
            load_curated_manifest(_write_manifest(run_dir, body))

    def test_not_assessed_cannot_carry_an_issue(self, run_dir):
        body = _manifest_body()
        body["examples"][0]["scle_assessment"] = {
            "state": "not_assessed",
            "issue": "possibly_incorrect",
        }
        with pytest.raises(ValueError, match="cannot carry an issue"):
            load_curated_manifest(_write_manifest(run_dir, body))


class TestSummaryMode:
    """An interpretation replaces the generated summary unless told otherwise."""

    def test_interpretation_is_the_default_when_one_is_supplied(self):
        from llm_dup_dev.review_html.evidence.manifest import CuratedExample

        example = CuratedExample(
            dataset="d", pair_id="p", case_interpretation="text"
        )
        assert example.resolved_summary_mode() is SummaryMode.INTERPRETATION

    def test_deterministic_without_an_interpretation(self):
        from llm_dup_dev.review_html.evidence.manifest import CuratedExample

        example = CuratedExample(dataset="d", pair_id="p")
        assert example.resolved_summary_mode() is SummaryMode.DETERMINISTIC

    def test_explicit_mode_wins(self):
        from llm_dup_dev.review_html.evidence.manifest import CuratedExample

        example = CuratedExample(
            dataset="d",
            pair_id="p",
            case_interpretation="text",
            summary_mode="both",
        )
        assert example.resolved_summary_mode() is SummaryMode.BOTH


class TestBuildCollection:
    """Cases come back in manifest order, with one read per dataset."""

    def test_manifest_order_is_preserved(self, run_dir):
        loaded = load_curated_manifest(_write_manifest(run_dir))
        page = build_collection(loaded)
        assert [c.example_id for c in page.cases] == ["S1", "S2"]
        assert page.title == "Illustrative report pairs"

    def test_reversed_manifest_order_is_followed(self, run_dir):
        body = _manifest_body()
        body["examples"] = list(reversed(body["examples"]))
        loaded = load_curated_manifest(_write_manifest(run_dir, body))
        page = build_collection(loaded)
        assert [c.example_id for c in page.cases] == ["S2", "S1"]

    def test_each_dataset_is_loaded_once(self, run_dir, monkeypatch):
        import llm_dup_dev.review_html.saved_run as saved_run

        calls = {"n": 0}
        original = saved_run.load_saved_run

        def counting(*args, **kwargs):
            calls["n"] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(saved_run, "load_saved_run", counting)
        build_collection(load_curated_manifest(_write_manifest(run_dir)))
        assert calls["n"] == 1

    def test_run_manifest_supplies_the_settings(self, run_dir):
        loaded = load_curated_manifest(_write_manifest(run_dir))
        page = build_collection(loaded)
        assert not [
            g for g in page.gaps if g.code.value == "run_settings_missing"
        ]

    def test_unknown_pair_id_is_an_error(self, run_dir):
        body = _manifest_body()
        body["examples"][0]["pair_id"] = "R009__R010"
        loaded = load_curated_manifest(_write_manifest(run_dir, body))
        with pytest.raises(ValueError, match="R009__R010"):
            build_collection(loaded)

    def test_skip_policy_drops_only_the_missing_example(self, run_dir):
        write_pair_results(
            [_make_pair_result("R001__R002")],
            run_dir / "synthetic_run" / "pair_results.json",
        )
        loaded = load_curated_manifest(_write_manifest(run_dir))
        page = build_collection(
            loaded, on_missing=MissingPairPolicy.SKIP, strict=False
        )
        assert [c.example_id for c in page.cases] == ["S1"]


class TestDrafts:
    """Generated drafts are exported, never written back into the manifest."""

    def test_drafts_are_keyed_by_example_id(self, run_dir):
        page = build_collection(load_curated_manifest(_write_manifest(run_dir)))
        drafts = export_summary_drafts(page)
        assert sorted(drafts) == ["S1", "S2"]
        assert "The reports share" in drafts["S1"]

    def test_a_human_interpretation_has_no_draft(self, run_dir):
        body = _manifest_body()
        body["examples"][0]["case_interpretation"] = "A reviewer reading."
        page = build_collection(
            load_curated_manifest(_write_manifest(run_dir, body))
        )
        assert sorted(export_summary_drafts(page)) == ["S2"]

    def test_writing_refuses_to_overwrite(self, run_dir, tmp_path):
        page = build_collection(load_curated_manifest(_write_manifest(run_dir)))
        path = tmp_path / "drafts.json"
        write_summary_drafts(page, path)
        with pytest.raises(FileExistsError, match="force"):
            write_summary_drafts(page, path)

    def test_force_allows_replacement(self, run_dir, tmp_path):
        page = build_collection(load_curated_manifest(_write_manifest(run_dir)))
        path = tmp_path / "drafts.json"
        write_summary_drafts(page, path)
        write_summary_drafts(page, path, force=True)
        assert json.loads(path.read_text())

    def test_regeneration_preserves_human_text(self, run_dir):
        """The manifest is the only home for human text, and is never written."""
        body = _manifest_body()
        body["examples"][0]["case_interpretation"] = "A reviewer reading."
        manifest_path = _write_manifest(run_dir, body)
        before = manifest_path.read_text()
        for _ in range(2):
            build_collection(load_curated_manifest(manifest_path))
        assert manifest_path.read_text() == before
