"""Tests for the CLI's format and selection flags.

The default must stay the detailed review: the secure-side wrappers call this
CLI without a ``--format``, and their output should not move.
"""

from __future__ import annotations

import json

import pytest

from llm_dup_dev.pipeline.export import write_pair_results
from llm_dup_dev.review_html.cli import main

from tests.test_review_html.test_render import _make_pair_result


@pytest.fixture
def artifacts(tmp_path, report_a, report_b, report_c):
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
    raw = json.loads((tmp_path / "pair_results.json").read_text())
    (tmp_path / "features.json").write_text(
        json.dumps({k: v["features"] for k, v in raw.items()})
    )
    classifications = {k: v["classification"] for k, v in raw.items()}
    # A real run always records which branch of the decision logic fired. The
    # shared fixture leaves it unset, and the evidence format refuses to quote
    # a zone threshold without it — correctly, but it makes for a fixture that
    # cannot exercise the threshold path.
    for entry in classifications.values():
        entry["independence_reason"] = "rarity_compelling"
    (tmp_path / "classifications.json").write_text(json.dumps(classifications))
    (tmp_path / "run_manifest.json").write_text(
        json.dumps(
            {
                "pipeline_config": {"epsilon": 1e-15},
                "decision_thresholds": {
                    "lmatch_compelling": 3.0,
                    "lmatch_limited": 1.0,
                },
            }
        )
    )
    return tmp_path


def _argv(artifacts, out, *extra):
    return [
        "--reports",
        str(artifacts / "reports.json"),
        "--pairs",
        str(artifacts / "pairs.json"),
        "--features",
        str(artifacts / "features.json"),
        "--classifications",
        str(artifacts / "classifications.json"),
        "--output-dir",
        str(out),
        *extra,
    ]


class TestDefaultIsDetailed:
    """No flag means the existing review, unchanged."""

    def test_default_renders_the_detailed_format(self, artifacts, tmp_path):
        out = tmp_path / "out"
        main(_argv(artifacts, out))
        html = (out / "R001__R002.html").read_text(encoding="utf-8")
        assert "Demographics &amp; Report Metadata" in html
        assert "Green: matching evidence or anchor pattern" not in html

    def test_explicit_detailed_matches_the_default(self, artifacts, tmp_path):
        default_dir, explicit_dir = tmp_path / "a", tmp_path / "b"
        main(_argv(artifacts, default_dir))
        main(_argv(artifacts, explicit_dir, "--format", "detailed"))
        assert (default_dir / "R001__R002.html").read_text() == (
            explicit_dir / "R001__R002.html"
        ).read_text()


class TestEvidenceFormat:
    """``--format evidence`` writes the reader-facing review."""

    def test_renders_the_evidence_format(self, artifacts, tmp_path):
        out = tmp_path / "out"
        main(_argv(artifacts, out, "--format", "evidence"))
        html = (out / "R001__R002.html").read_text(encoding="utf-8")
        assert "1. Report comparison" in html
        assert "3. Relationship assessment" in html
        assert "@page" in html

    def test_set_label_names_the_reference_tile(self, artifacts, tmp_path):
        out = tmp_path / "out"
        main(
            _argv(
                artifacts, out, "--format", "evidence", "--set-label", "FDA Gold"
            )
        )
        html = (out / "R001__R002.html").read_text(encoding="utf-8")
        assert "FDA Gold label" in html

    def test_run_manifest_supplies_the_threshold(self, artifacts, tmp_path):
        out = tmp_path / "out"
        main(
            _argv(
                artifacts,
                out,
                "--format",
                "evidence",
                "--run-manifest",
                str(artifacts / "run_manifest.json"),
            )
        )
        html = (out / "R001__R002.html").read_text(encoding="utf-8")
        assert "independence threshold of 1.0" in html

    def test_an_unrecorded_reason_quotes_no_threshold(
        self, artifacts, tmp_path
    ):
        """Without a recorded reason there is no zone to quote a bound for."""
        classifications = json.loads(
            (artifacts / "classifications.json").read_text()
        )
        for entry in classifications.values():
            entry["independence_reason"] = None
        (artifacts / "classifications.json").write_text(
            json.dumps(classifications)
        )
        out = tmp_path / "out"
        main(
            _argv(
                artifacts,
                out,
                "--format",
                "evidence",
                "--run-manifest",
                str(artifacts / "run_manifest.json"),
            )
        )
        html = (out / "R001__R002.html").read_text(encoding="utf-8")
        assert "threshold of" not in html
        assert "assessed the reports as non-independent" in html

    def test_without_a_manifest_no_threshold_is_quoted(
        self, artifacts, tmp_path
    ):
        out = tmp_path / "out"
        main(_argv(artifacts, out, "--format", "evidence"))
        html = (out / "R001__R002.html").read_text(encoding="utf-8")
        assert "exceeding the independence threshold" not in html
        # The gap saying the settings were not recorded is on the case, not
        # on the page: the limitations list is gone. What the page must do is
        # name the evidence zone instead of quoting a threshold it never had.
        assert "were not recorded" not in html
        assert "threshold of" not in html


class TestPairSelection:
    """``--pair-ids`` narrows the run, and the index follows."""

    def test_renders_only_the_selected_pair(self, artifacts, tmp_path):
        out = tmp_path / "out"
        main(
            _argv(
                artifacts, out, "--format", "evidence", "--pair-ids", "R001__R003"
            )
        )
        names = sorted(p.name for p in out.glob("*.html"))
        assert names == ["R001__R003.html", "index.html"]

    def test_index_links_only_the_selected_file(self, artifacts, tmp_path):
        out = tmp_path / "out"
        main(
            _argv(
                artifacts, out, "--format", "evidence", "--pair-ids", "R001__R003"
            )
        )
        index = (out / "index.html").read_text(encoding="utf-8")
        assert 'href="R001__R003.html"' in index
        assert "R001__R002.html" not in index

    def test_selection_also_applies_to_the_detailed_format(
        self, artifacts, tmp_path
    ):
        out = tmp_path / "out"
        main(_argv(artifacts, out, "--pair-ids", "R001__R002"))
        names = sorted(p.name for p in out.glob("*.html"))
        assert names == ["R001__R002.html", "index.html"]

    def test_unknown_pair_id_is_an_error(self, artifacts, tmp_path):
        with pytest.raises(ValueError, match="R009__R010"):
            main(_argv(artifacts, tmp_path / "out", "--pair-ids", "R009__R010"))


class TestOutputDirDefault:
    """Each format has its own default directory, because names collide.

    Both formats write ``index.html``, and for a saved run both name a pair's
    page after the pair id. One shared default directory would mean an
    evidence run silently replacing a detailed one.
    """

    @staticmethod
    def _base(artifacts):
        return [
            "--reports", str(artifacts / "reports.json"),
            "--pairs", str(artifacts / "pairs.json"),
            "--features", str(artifacts / "features.json"),
            "--classifications", str(artifacts / "classifications.json"),
        ]

    def test_detailed_default_is_unchanged(self, artifacts, monkeypatch):
        monkeypatch.chdir(artifacts)
        main(self._base(artifacts))
        assert (artifacts / "outputs" / "review_html" / "index.html").exists()
        assert not (artifacts / "outputs" / "evidence_review").exists()

    def test_evidence_default_is_its_own(self, artifacts, monkeypatch):
        monkeypatch.chdir(artifacts)
        main([*self._base(artifacts), "--format", "evidence"])
        out = artifacts / "outputs" / "evidence_review"
        assert (out / "index.html").exists()
        assert not (artifacts / "outputs" / "review_html").exists()

    def test_explicit_output_dir_still_wins(self, artifacts):
        out = artifacts / "elsewhere"
        main([
            *self._base(artifacts),
            "--format", "evidence",
            "--output-dir", str(out),
        ])
        assert (out / "index.html").exists()


class TestScreeningVerdictOnTheCLI:
    """The evidence path must pass each pair's screening entry, not drop it."""

    @staticmethod
    def _with_screening(artifacts, entries, extra=()):
        (artifacts / "screening.json").write_text(
            json.dumps({"algorithm": "vigiMatch", "entries": entries})
        )
        main(
            [
                "--reports", str(artifacts / "reports.json"),
                "--pairs", str(artifacts / "pairs.json"),
                "--features", str(artifacts / "features.json"),
                "--classifications", str(artifacts / "classifications.json"),
                "--screening", str(artifacts / "screening.json"),
                "--format", "evidence",
                "--output-dir", str(artifacts / "out"),
                *extra,
            ]
        )
        return artifacts / "out"

    def test_a_flagged_pair_and_a_rejected_one(self, artifacts):
        out = self._with_screening(
            artifacts,
            {
                "R001__R002": {"IsSuspectedDuplicate": True},
                "R001__R003": {"IsSuspectedDuplicate": False},
            },
        )
        assert "Flagged" in (out / "R001__R002.html").read_text()
        assert "Not flagged" in (out / "R001__R003.html").read_text()

    def test_a_pair_the_artifact_does_not_cover(self, artifacts):
        out = self._with_screening(
            artifacts, {"R001__R002": {"IsSuspectedDuplicate": True}}
        )
        assert "Not evaluated" in (out / "R001__R003.html").read_text()

    def test_the_verdict_key_can_be_named(self, artifacts):
        out = self._with_screening(
            artifacts,
            {"R001__R002": {"vigiMatchHit": True}},
            extra=("--screening-verdict-key", "vigiMatchHit"),
        )
        assert "Flagged" in (out / "R001__R002.html").read_text()
