"""Loading and constructing study-eval inputs from artifacts a caller holds."""

from __future__ import annotations

import json

import pytest

from llm_dup_dev.models.report_pair import ReportPair
from llm_dup_dev.study_eval.loading import (
    load_report_pairs,
    reference_set_from_report_pairs,
    write_json,
)


def _pairs() -> list[ReportPair]:
    return [
        ReportPair.from_report_ids("R001", "R002"),
        ReportPair.from_report_ids("R003", "R004"),
    ]


def _write_pairs(tmp_path, pairs, name="pairs.json"):
    path = tmp_path / name
    path.write_text(json.dumps([p.model_dump(mode="json") for p in pairs]))
    return path


class TestLoadReportPairs:
    def test_reads_a_frozen_pairs_file(self, tmp_path):
        loaded = load_report_pairs(_write_pairs(tmp_path, _pairs()))

        assert [p.pair_id for p in loaded] == ["R001__R002", "R003__R004"]

    def test_non_list_is_rejected(self, tmp_path):
        path = tmp_path / "pairs.json"
        path.write_text(json.dumps({"R001__R002": {}}))

        with pytest.raises(ValueError, match="expected a JSON array"):
            load_report_pairs(path)

    def test_noncanonical_pair_is_rejected_not_repaired(self, tmp_path):
        """No escape hatch on this path: a reference set feeds real estimates."""
        path = tmp_path / "pairs.json"
        path.write_text(
            json.dumps(
                [{"pair_id": "R002__R001", "report_1_id": "R002", "report_2_id": "R001"}]
            )
        )

        with pytest.raises(ValueError):
            load_report_pairs(path)


class TestReferenceSetFromReportPairs:
    def test_renames_the_id_fields_and_carries_ids_across(self):
        result = reference_set_from_report_pairs(_pairs(), set_id="frozen_vs")

        assert [p.pair_id for p in result.pairs] == ["R001__R002", "R003__R004"]
        assert result.pairs[0].report_id1 == "R001"
        assert result.pairs[0].report_id2 == "R002"

    def test_defaults_to_a_recall_set_needing_no_labels(self):
        """The cheapest legal set: describing pairs must not require a label map."""
        result = reference_set_from_report_pairs(_pairs(), set_id="frozen_vs")

        assert result.role == "recall"
        assert all(p.reference_label is None for p in result.pairs)

    def test_screening_mode_keeps_the_model_default(self):
        """`not_evaluated` is a claim about the estimand, so it is never inferred."""
        result = reference_set_from_report_pairs(_pairs(), set_id="frozen_vs")

        assert result.screening_mode == "observed"

    def test_metadata_is_carried_through(self):
        result = reference_set_from_report_pairs(
            _pairs(),
            set_id="frozen_vs",
            label="Frozen validation set",
            provenance="vs_2026Q1",
            screening_mode="not_evaluated",
        )

        assert result.label == "Frozen validation set"
        assert result.provenance == "vs_2026Q1"
        assert result.screening_mode == "not_evaluated"

    def test_precision_role_with_labels(self):
        result = reference_set_from_report_pairs(
            _pairs(),
            set_id="precision_primary",
            role="precision",
            reference_labels={
                "R001__R002": "duplicate",
                "R003__R004": "non_duplicate",
            },
        )

        assert [p.reference_label for p in result.pairs] == [
            "duplicate",
            "non_duplicate",
        ]

    def test_precision_role_missing_a_label_is_rejected_by_the_model(self):
        """The model names the offending pairs; the helper does not invent labels."""
        with pytest.raises(ValueError, match="every pair needs a reference_label"):
            reference_set_from_report_pairs(
                _pairs(),
                set_id="precision_primary",
                role="precision",
                reference_labels={"R001__R002": "duplicate"},
            )

    def test_cluster_ids_are_applied_by_pair_id(self):
        result = reference_set_from_report_pairs(
            _pairs(),
            set_id="frozen_vs",
            cluster_ids={"R001__R002": "C1", "R003__R004": "C2"},
        )

        assert [p.cluster_id for p in result.pairs] == ["C1", "C2"]

    def test_round_trips_through_the_committed_artifact_shape(self, tmp_path):
        """The output is a normal ReferenceSet file the CLIs already read."""
        from llm_dup_dev.study_eval.loading import load_reference_set

        result = reference_set_from_report_pairs(_pairs(), set_id="frozen_vs")
        path = write_json(result, tmp_path / "set.json")

        assert load_reference_set(path) == result
