"""Tests for the provenance the evidence footer states.

The rendering version is always available from installed metadata. Everything
about the *run* comes from its manifest, so it is stated only when one was
supplied — and only when every case agrees about it, because a collection
spanning two runs cannot be attributed to one of them.
"""

from __future__ import annotations

import json

from vigitwin.review_html.evidence.models import EvidencePageVM, ProvenanceVM
from vigitwin.review_html.evidence.settings import (
    EvidenceRunSettings,
    load_evidence_run_settings,
    page_provenance,
)
from vigitwin.review_html.render import render_evidence_page

from tests.test_review_html.test_evidence_build import _case, _features, _report

_MANIFEST = {
    "schema_version": "1.0",
    "run_id": "eval_2026-11-15",
    "created_at": "2026-11-15T09:12:00Z",
    "dev_package": {"name": "vigitwin", "version": "1.1.3"},
    "dev_git_commit": "171f436e46be8deb92864b9f01b7639d09c5984e",
    "pipeline_config": {"epsilon": 1e-15},
    "decision_thresholds": {
        "lmatch_compelling": 3.0,
        "lmatch_intermediate": 2.0,
        "lmatch_limited": 1.0,
        "lpatient_compelling": 2.0,
        "explicit_link_enabled": True,
    },
}


def _settings(tmp_path, **overrides) -> EvidenceRunSettings:
    body = {**_MANIFEST, **overrides}
    name = str(overrides.get("run_id", len(overrides)))
    path = tmp_path / f"run_manifest_{name}.json"
    path.write_text(json.dumps(body))
    return load_evidence_run_settings(path)


class TestReadingTheManifest:
    def test_run_identity_is_read(self, tmp_path):
        s = _settings(tmp_path)
        assert s.run_id == "eval_2026-11-15"
        assert s.created_at == "2026-11-15T09:12:00Z"
        assert s.dev_package_version == "1.1.3"
        assert s.dev_git_commit is not None
        assert s.dev_git_commit.startswith("171f436")

    def test_a_manifest_without_identity_yields_none(self, tmp_path):
        path = tmp_path / "bare.json"
        path.write_text(json.dumps({"pipeline_config": {"epsilon": 1e-15}}))
        s = load_evidence_run_settings(path)
        assert s.run_id is None
        assert s.dev_git_commit is None
        assert s.epsilon == 1e-15

    def test_the_configuration_is_rendered_from_what_was_recorded(self, tmp_path):
        text = _settings(tmp_path).configuration_text
        assert "L_match thresholds 3.0 / 2.0 / 1.0" in text
        assert "L_patient 2.0" in text
        assert "epsilon 1e-15" in text
        assert "explicit links enabled" in text

    def test_nothing_recorded_yields_no_configuration_clause(self):
        assert EvidenceRunSettings().configuration_text == ""

    def test_partial_thresholds_are_not_half_quoted(self):
        """Two of three zones is not a threshold set a reader can rely on."""
        s = EvidenceRunSettings(lmatch_compelling=3.0, lmatch_limited=1.0)
        assert "L_match thresholds" not in s.configuration_text


class TestPageProvenance:
    def test_the_rendering_version_is_always_stated(self):
        assert any(line.startswith("vigiTwin ") for line in ProvenanceVM().lines)

    def test_a_single_run_is_named(self, tmp_path):
        p = page_provenance([_settings(tmp_path)])
        assert not p.several_runs
        lines = " · ".join(p.lines)
        assert "run eval_2026-11-15 of 2026-11-15" in lines
        assert "pipeline commit 171f436e46be" in lines
        assert "L_match thresholds 3.0 / 2.0 / 1.0" in lines

    def test_the_run_date_is_a_date_not_a_timestamp(self, tmp_path):
        lines = " ".join(page_provenance([_settings(tmp_path)]).lines)
        assert "09:12" not in lines

    def test_two_runs_are_not_attributed_to_one(self, tmp_path):
        p = page_provenance(
            [_settings(tmp_path), _settings(tmp_path, run_id="eval_other")]
        )
        assert p.several_runs
        assert p.run_id is None
        assert "several pipeline runs" in " ".join(p.lines)

    def test_differing_configurations_are_also_several_runs(self, tmp_path):
        """Same id, different thresholds: still not one configuration."""
        other = _settings(
            tmp_path,
            decision_thresholds={
                "lmatch_compelling": 4.0,
                "lmatch_intermediate": 2.0,
                "lmatch_limited": 1.0,
            },
        )
        assert page_provenance([_settings(tmp_path), other]).several_runs

    def test_no_settings_at_all_still_gives_the_version(self):
        p = page_provenance([None, None])
        assert not p.several_runs
        assert p.run_id is None
        assert p.lines and p.lines[0].startswith("vigiTwin ")


class TestTheFooter:
    @staticmethod
    def _html(provenance: ProvenanceVM | None = None) -> str:
        case = _case(_report("R1"), _report("R2"), _features())
        page = EvidencePageVM(
            title="t",
            cases=[case],
            **({} if provenance is None else {"provenance": provenance}),
        )
        return " ".join(render_evidence_page(page).split())

    def test_the_version_reaches_the_footer(self):
        assert "vigiTwin 1." in self._html()

    def test_run_provenance_reaches_the_footer(self, tmp_path):
        html = self._html(page_provenance([_settings(tmp_path)]))
        assert "run eval_2026-11-15 of 2026-11-15" in html
        assert "pipeline commit 171f436e46be" in html
        assert "epsilon 1e-15" in html

    def test_the_internal_review_warning_survives(self):
        assert "not a publication-safe document" in self._html()
