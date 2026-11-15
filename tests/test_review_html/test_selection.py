"""Tests for selecting a subset of a saved run's pairs.

The first class is the important one: passing no selection must behave exactly
as it did before selection existed, because every current caller does that.
"""

from __future__ import annotations

import pytest

from llm_dup_dev.review_html.saved_run import load_saved_run
from llm_dup_dev.review_html.selection import (
    MissingPairPolicy,
    PairSelection,
    apply_selection,
    coerce_selection,
)

from tests.test_review_html.test_render import _make_pair_result
from tests.test_review_html.test_saved_run import (
    _pair,
    _write_pairs,
    _write_reports,
)
from llm_dup_dev.pipeline.export import write_pair_results


@pytest.fixture
def run_dir(tmp_path, report_a, report_b, report_c):
    """A saved run of three pairs over four synthetic reports."""
    from llm_dup_dev.models.report import Report

    report_d = Report(report_id="R004")
    _write_reports(tmp_path, report_a, report_b, report_c, report_d)
    _write_pairs(
        tmp_path,
        _pair("R001__R002", "R001", "R002"),
        _pair("R001__R003", "R001", "R003"),
        _pair("R003__R004", "R003", "R004"),
    )
    write_pair_results(
        [
            _make_pair_result("R001__R002"),
            _make_pair_result("R001__R003"),
            _make_pair_result("R003__R004"),
        ],
        tmp_path / "pair_results.json",
    )
    return tmp_path


def _load(run_dir, **kwargs):
    return load_saved_run(
        reports_path=run_dir / "reports.json",
        pairs_path=run_dir / "pairs.json",
        pair_results_path=run_dir / "pair_results.json",
        **kwargs,
    )


class TestNoSelectionIsUnchanged:
    """Every existing caller passes nothing; nothing may move for them."""

    def test_all_pairs_are_loaded_in_artifact_order(self, run_dir):
        items, summary = _load(run_dir)
        assert [i.result.pair_id for i in items] == [
            "R001__R002",
            "R001__R003",
            "R003__R004",
        ]
        assert summary.n_pairs == 3
        assert summary.n_loaded == 3

    def test_available_count_matches_when_nothing_is_selected(self, run_dir):
        _, summary = _load(run_dir)
        assert summary.n_available == summary.n_pairs


class TestExplicitSelection:
    """Selection narrows the run and keeps the order it was asked for."""

    def test_two_of_three_pairs(self, run_dir):
        items, summary = _load(run_dir, select=["R003__R004", "R001__R002"])
        assert [i.result.pair_id for i in items] == [
            "R003__R004",
            "R001__R002",
        ]
        assert summary.n_pairs == 2
        assert summary.n_available == 3

    def test_artifact_order_can_be_requested(self, run_dir):
        items, _ = _load(
            run_dir,
            select=PairSelection(
                pair_ids=("R003__R004", "R001__R002"), preserve_order=False
            ),
        )
        assert [i.result.pair_id for i in items] == [
            "R001__R002",
            "R003__R004",
        ]

    def test_a_single_pair(self, run_dir):
        items, _ = _load(run_dir, select=["R001__R003"])
        assert len(items) == 1


class TestUnknownIdsAreAnError:
    """An id matching nothing is a mistake in the request."""

    def test_every_unknown_id_is_named_at_once(self, run_dir):
        with pytest.raises(ValueError) as excinfo:
            _load(run_dir, select=["R001__R002", "R009__R010", "R011__R012"])
        message = str(excinfo.value)
        assert "R009__R010" in message
        assert "R011__R012" in message
        assert "R001__R002" not in message

    def test_unknown_id_raises_even_when_not_strict(self, run_dir):
        with pytest.raises(ValueError):
            _load(run_dir, select=["R009__R010"], strict=False)

    def test_the_error_mentions_pair_id_orientation(self, run_dir):
        """Reversed ids are the likeliest cause, so the error says so."""
        with pytest.raises(ValueError, match="orientation"):
            _load(run_dir, select=["R002__R001"])


class TestMissingPairPolicy:
    """A pair the caller named is not silently dropped."""

    @pytest.fixture
    def incomplete_run(self, run_dir):
        """A run whose pair_results lacks one of the three pairs."""
        write_pair_results(
            [
                _make_pair_result("R001__R002"),
                _make_pair_result("R003__R004"),
            ],
            run_dir / "pair_results.json",
        )
        return run_dir

    def test_selected_but_unloadable_pair_raises_by_default(
        self, incomplete_run
    ):
        with pytest.raises(RuntimeError, match="explicitly selected"):
            _load(incomplete_run, select=["R001__R003"], strict=False)

    def test_skip_policy_renders_the_rest(self, incomplete_run):
        items, summary = _load(
            incomplete_run,
            select=PairSelection(
                pair_ids=("R001__R002", "R001__R003"),
                on_missing=MissingPairPolicy.SKIP,
            ),
            strict=False,
        )
        assert [i.result.pair_id for i in items] == ["R001__R002"]
        assert [s.pair_id for s in summary.skipped] == ["R001__R003"]

    def test_strict_still_governs_unselected_pairs(self, incomplete_run):
        """Without a selection, `strict` keeps its existing meaning."""
        with pytest.raises(RuntimeError):
            _load(incomplete_run)
        items, summary = _load(incomplete_run, strict=False)
        assert len(items) == 2
        assert len(summary.skipped) == 1


class TestSelectionModel:
    """The selection object rejects requests that cannot mean anything."""

    def test_empty_selection_is_rejected(self):
        with pytest.raises(ValueError, match="at least one pair id"):
            PairSelection(pair_ids=())

    def test_duplicate_ids_are_rejected(self):
        with pytest.raises(ValueError, match="duplicate"):
            PairSelection(pair_ids=("R1__R2", "R1__R2"))

    def test_a_bare_iterable_is_promoted(self):
        selection = coerce_selection(["R1__R2"])
        assert isinstance(selection, PairSelection)
        assert selection.on_missing is MissingPairPolicy.ERROR

    def test_none_stays_none(self):
        assert coerce_selection(None) is None

    def test_apply_selection_without_a_selection_is_identity(self):
        rows = [{"pair_id": "a"}, {"pair_id": "b"}]
        assert apply_selection(rows, None, key=lambda r: r["pair_id"]) == rows
