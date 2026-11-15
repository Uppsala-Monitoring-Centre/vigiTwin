"""Tests for the screening verdict an evidence review reports.

Everything here is synthetic. The screening payload is untyped by design, so
these tests pin the one thing the review must not get wrong: a verdict is read
from a named boolean, never inferred from an entry merely existing, and never
coerced from a truthy value.
"""

from __future__ import annotations

import json

import pytest

from llm_dup_dev.review_html.evidence import screening_verdict
from llm_dup_dev.review_html.evidence.build import (
    ReferenceInfo,
    build_evidence_case,
)
from llm_dup_dev.review_html.evidence.collection import build_collection
from llm_dup_dev.review_html.evidence.manifest import load_curated_manifest

from tests.test_review_html.test_evidence_manifest import (  # noqa: F401
    _manifest_body,
    _write_manifest,
    run_dir,
)


def _screening_tile(case):
    return next(t for t in case.outcomes if "screening" in t.label)


def _case(reference: ReferenceInfo | None = None, **kw):
    from llm_dup_dev.models.report import Report

    return build_evidence_case(
        report_1=Report(report_id="R001"),
        report_2=Report(report_id="R002"),
        pair_id="R001__R002",
        reference=reference,
        **kw,
    )


class TestVerdictFromAnEntry:
    """What `screening_verdict` reads, and what it refuses to read."""

    @pytest.mark.parametrize(
        "key", ["IsSuspectedDuplicate", "screening_retrieved", "retrieved", "flagged"]
    )
    @pytest.mark.parametrize("value", [True, False])
    def test_every_recognised_key_in_both_states(self, key, value):
        assert screening_verdict({key: value}).retrieved is value

    def test_no_entry_is_no_verdict(self):
        assert screening_verdict(None).retrieved is None
        assert screening_verdict({}).retrieved is None

    def test_an_entry_without_a_verdict_key_is_no_verdict(self):
        """Presence is not a verdict: an entry may describe a rejected pair."""
        verdict = screening_verdict({"score": 0.93, "N_terms": 4})
        assert verdict.retrieved is None
        assert verdict.note == ""

    @pytest.mark.parametrize("value", ["yes", "true", 1, 0, None, [], {"a": 1}])
    def test_a_non_boolean_verdict_is_reported_not_coerced(self, value):
        verdict = screening_verdict({"IsSuspectedDuplicate": value})
        assert verdict.retrieved is None
        assert "not a true or false verdict" in verdict.note

    def test_an_explicit_key_is_the_only_one_consulted(self):
        entry = {"MyFlag": True, "IsSuspectedDuplicate": False}
        assert screening_verdict(entry, key="MyFlag").retrieved is True

    def test_a_named_key_that_is_absent_is_reported(self):
        """Naming a key is a claim it is there; silence would hide a typo."""
        verdict = screening_verdict({"IsSuspectedDuplicate": True}, key="MyFlag")
        assert verdict.retrieved is None
        assert "'MyFlag' is not present" in verdict.note


class TestTheTile:
    """Three states, and the third is never confused with the second."""

    def test_true_reads_flagged(self):
        case = _case(ReferenceInfo(screening_retrieved=True))
        assert _screening_tile(case).value == "Flagged"

    def test_false_reads_not_flagged(self):
        case = _case(ReferenceInfo(screening_retrieved=False))
        assert _screening_tile(case).value == "Not flagged"

    def test_none_reads_not_evaluated(self):
        case = _case(ReferenceInfo(screening_retrieved=None))
        assert _screening_tile(case).value == "Not evaluated"

    def test_an_unreadable_verdict_becomes_a_gap(self):
        case = _case(
            ReferenceInfo(screening_note="The screening entry's 'x' is 'yes'.")
        )
        assert "screening_verdict_unreadable" in [g.code.value for g in case.gaps]
        assert _screening_tile(case).value == "Not evaluated"


class TestTheCollectionPath:
    """The regression: a manifest's screening artifact must reach the tile."""

    @staticmethod
    def _with_screening(run_dir, entries, **dataset_overrides):
        (run_dir / "synthetic_run" / "screening.json").write_text(
            json.dumps({"algorithm": "vigiMatch", "entries": entries})
        )
        body = _manifest_body()
        body["datasets"]["synthetic_set"]["screening"] = (
            "synthetic_run/screening.json"
        )
        body["datasets"]["synthetic_set"].update(dataset_overrides)
        return build_collection(load_curated_manifest(_write_manifest(run_dir, body)))

    def test_a_covered_pair_is_flagged(self, run_dir):
        page = self._with_screening(
            run_dir,
            {
                "R001__R002": {"IsSuspectedDuplicate": True},
                "R001__R003": {"IsSuspectedDuplicate": True},
            },
        )
        assert [_screening_tile(c).value for c in page.cases] == [
            "Flagged",
            "Flagged",
        ]

    def test_a_rejected_pair_is_not_flagged(self, run_dir):
        page = self._with_screening(
            run_dir,
            {
                "R001__R002": {"IsSuspectedDuplicate": False},
                "R001__R003": {"IsSuspectedDuplicate": True},
            },
        )
        assert [_screening_tile(c).value for c in page.cases] == [
            "Not flagged",
            "Flagged",
        ]

    def test_a_pair_the_artifact_does_not_cover(self, run_dir):
        page = self._with_screening(
            run_dir, {"R001__R002": {"IsSuspectedDuplicate": True}}
        )
        assert [_screening_tile(c).value for c in page.cases] == [
            "Flagged",
            "Not evaluated",
        ]

    def test_no_screening_artifact_at_all(self, run_dir):
        page = build_collection(load_curated_manifest(_write_manifest(run_dir)))
        assert all(
            _screening_tile(c).value == "Not evaluated" for c in page.cases
        )

    def test_the_manifest_can_name_the_verdict_key(self, run_dir):
        page = self._with_screening(
            run_dir,
            {"R001__R002": {"vigiMatchHit": True}},
            screening_verdict_key="vigiMatchHit",
        )
        assert _screening_tile(page.cases[0]).value == "Flagged"
