"""The shared reports loader, and the id-keyed indexing it is built on.

Two frozen conventions for the same artifact coexist in this project — the
secure runner writes a JSON list, the review CLI writes a mapping keyed by id —
so the loader accepts either. These tests pin that the tolerance stops at the
shape: everything about the *content* is still validated hard.
"""

from __future__ import annotations

import json

import pytest

from llm_dup_dev.data.offline import (
    FrozenPairBundle,
    FrozenScopeStats,
    index_by_id,
    load_reports,
)
from llm_dup_dev.models.report import Report


def _write(tmp_path, payload, name="reports.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _dump(*reports: Report) -> list[dict]:
    return [r.model_dump(mode="json") for r in reports]


class TestBothShapes:
    def test_list_shape(self, tmp_path, report_a, report_b):
        path = _write(tmp_path, _dump(report_a, report_b))

        assert sorted(load_reports(path)) == ["R001", "R002"]

    def test_mapping_shape(self, tmp_path, report_a, report_b):
        path = _write(
            tmp_path,
            {r.report_id: r.model_dump(mode="json") for r in (report_a, report_b)},
        )

        assert sorted(load_reports(path)) == ["R001", "R002"]

    def test_the_two_shapes_produce_identical_reports(
        self, tmp_path, report_a, report_b
    ):
        as_list = load_reports(_write(tmp_path, _dump(report_a, report_b), "l.json"))
        as_map = load_reports(
            _write(
                tmp_path,
                {r.report_id: r.model_dump(mode="json") for r in (report_a, report_b)},
                "m.json",
            )
        )

        assert as_list == as_map


class TestValidation:
    def test_duplicate_id_in_a_list_is_rejected(self, tmp_path, report_a):
        """Two records for one id may differ; keeping either silently is not safe."""
        variant = report_a.model_copy(update={"narrative": "different text"})
        path = _write(tmp_path, _dump(report_a, variant))

        with pytest.raises(ValueError, match="duplicate report_id='R001'"):
            load_reports(path)

    def test_mapping_key_must_match_the_record(self, tmp_path, report_a):
        """A key disagreeing with the record has two answers to "which report?"."""
        path = _write(tmp_path, {"R999": report_a.model_dump(mode="json")})

        with pytest.raises(ValueError, match="does not match report_id='R001'"):
            load_reports(path)

    def test_invalid_record_names_its_position(self, tmp_path, report_a):
        path = _write(tmp_path, [report_a.model_dump(mode="json"), {"nope": 1}])

        with pytest.raises(ValueError, match="item 1 is invalid"):
            load_reports(path)

    def test_invalid_record_names_its_key(self, tmp_path):
        path = _write(tmp_path, {"R001": {"nope": 1}})

        with pytest.raises(ValueError, match="value for 'R001' is invalid"):
            load_reports(path)

    def test_list_items_must_be_objects(self, tmp_path):
        path = _write(tmp_path, ["R001"])

        with pytest.raises(ValueError, match="item 0 must be an object"):
            load_reports(path)

    @pytest.mark.parametrize("payload", ["a string", 42, None])
    def test_other_top_level_shapes_are_rejected(self, tmp_path, payload):
        path = _write(tmp_path, payload)

        with pytest.raises(ValueError, match="must be a list or an object"):
            load_reports(path)

    def test_error_messages_name_the_file(self, tmp_path, report_a):
        path = _write(tmp_path, {"R999": report_a.model_dump(mode="json")})

        with pytest.raises(ValueError, match=str(path)):
            load_reports(path)


class TestIndexByIdIsGeneric:
    """The helper also serves pair-keyed artifacts, which is why it is generic."""

    def _bundle(self, pair_id: str) -> dict:
        return FrozenPairBundle(
            pair_id=pair_id,
            matching_drugs=["pregabalin"],
            matching_AEs=["somnolence"],
            global_stats=FrozenScopeStats(
                N_total=5000, N_drugs=200, N_AEs=300, N_drugs_AEs=40
            ),
        ).model_dump(mode="json")

    def test_indexes_bundles_by_pair_id_from_either_shape(self):
        as_list = index_by_id(
            [self._bundle("R001__R002")],
            id_attr="pair_id",
            model_cls=FrozenPairBundle,
            artifact="frozen bundles",
        )
        as_map = index_by_id(
            {"R001__R002": self._bundle("R001__R002")},
            id_attr="pair_id",
            model_cls=FrozenPairBundle,
            artifact="frozen bundles",
        )

        assert as_list == as_map
        assert list(as_list) == ["R001__R002"]

    def test_artifact_name_shapes_the_error(self):
        with pytest.raises(ValueError, match="Invalid frozen bundles artifact"):
            index_by_id(
                "nonsense",
                id_attr="pair_id",
                model_cls=FrozenPairBundle,
                artifact="frozen bundles",
            )
