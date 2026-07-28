"""Tests for deterministic SCLE sampling — determinism, golden order, backups."""

from __future__ import annotations

import pytest

from llm_dup_dev.study_eval.aggregate import run_precision_analysis, run_recall_analysis
from llm_dup_dev.study_eval.sampling import (
    promote_backup,
    sample_scle_precision,
    sample_scle_recall,
)

from tests.test_study_eval.conftest import (
    build_scle_precision_sample,
    make_precision,
    make_recall,
)

# Golden order pinned for a fixed synthetic input + seed (20 TP pairs, per_cell=3).
# A change here means the sampling internals changed and must be re-blessed.
_PRIMARY_GOLDEN = ["A5__B5", "A10__B10", "A19__B19"]
_BACKUP_HEAD_GOLDEN = ["A6__B6", "A0__B0", "A11__B11", "A8__B8"]


def _all_tp(n: int):
    return make_precision([("duplicate", "Duplicates")] * n)


def test_precision_sampling_is_deterministic():
    rset, preds = _all_tp(25)
    res = run_precision_analysis(rset, preds)
    a = sample_scle_precision(rset, res, preds, per_cell=10, seed="s")
    b = sample_scle_precision(rset, res, preds, per_cell=10, seed="s")
    assert [c.pair_id for c in a.cases] == [c.pair_id for c in b.cases]
    assert [c.pair_id for c in a.backups] == [c.pair_id for c in b.backups]


def test_precision_sampling_golden_order():
    rset, preds = _all_tp(20)
    res = run_precision_analysis(rset, preds)
    s = sample_scle_precision(rset, res, preds, per_cell=3, seed="golden-seed-1")
    assert [c.pair_id for c in s.cases] == _PRIMARY_GOLDEN
    assert [c.pair_id for c in s.backups[:4]] == _BACKUP_HEAD_GOLDEN
    assert [c.backup_rank for c in s.backups[:4]] == [1, 2, 3, 4]


def test_precision_cap_and_backups():
    rset, preds = _all_tp(25)
    res = run_precision_analysis(rset, preds)
    s = sample_scle_precision(rset, res, preds, per_cell=10, seed="s")
    tp_primary = [c for c in s.cases if c.stratum == "TP"]
    tp_backups = [c for c in s.backups if c.stratum == "TP"]
    assert len(tp_primary) == 10
    assert len(tp_backups) == 15  # 25 - 10
    # Primary and backups are disjoint and cover the whole stratum.
    assert set(c.pair_id for c in tp_primary).isdisjoint(c.pair_id for c in tp_backups)
    assert len(tp_primary) + len(tp_backups) == 25


def test_takes_all_when_under_cap_no_backups():
    rset, preds = _all_tp(3)
    res = run_precision_analysis(rset, preds)
    s = sample_scle_precision(rset, res, preds, per_cell=10, seed="s")
    assert len([c for c in s.cases if c.stratum == "TP"]) == 3
    assert s.backups == []


def test_precision_preserves_reference_set_id():
    # 15 pairs in one cell with per_cell=5 -> 5 primary + 10 backups, so the
    # assertions below exercise both primary and backup cases.
    spec = [("duplicate", "Duplicates")] * 15
    reference_set, _preds, s = build_scle_precision_sample(spec, per_cell=5)
    assert reference_set.set_id == "p_set"
    assert s.cases and s.backups  # guard: the assertions below must see both kinds
    for case in [*s.cases, *s.backups]:
        assert case.reference_set_id == "p_set"
        assert case.auto_context.reference_set_id == "p_set"


def test_different_seed_changes_draw():
    rset, preds = _all_tp(20)
    res = run_precision_analysis(rset, preds)
    a = sample_scle_precision(rset, res, preds, per_cell=5, seed="A")
    b = sample_scle_precision(rset, res, preds, per_cell=5, seed="B")
    assert [c.pair_id for c in a.cases] != [c.pair_id for c in b.cases]


def test_recall_caps_per_stratum_per_set():
    rsets, preds = make_recall(
        {"fda_gold": [(True, "Duplicates")] * 8 + [(False, "Unrelated")] * 8}
    )
    result = run_recall_analysis(rsets, preds)
    s = sample_scle_recall(result, preds, per_stratum=5, seed="s")
    both = [c for c in s.cases if c.stratum == "both"]
    neither = [c for c in s.cases if c.stratum == "neither"]
    assert len(both) == 5
    assert len(neither) == 5
    assert all(c.reference_set_id == "fda_gold" for c in s.cases)
    # 8 per stratum, cap 5 -> 3 backups each.
    assert len([c for c in s.backups if c.stratum == "both"]) == 3


def test_recall_preserves_per_set_reference_set_id():
    # Two reference sets in one recall sample; each pair's provenance must match
    # the set it came from (pairs are named "{set_id}_{i}__x").
    rsets, preds = make_recall(
        {
            "fda_gold": [(True, "Duplicates")] * 6 + [(False, "Unrelated")] * 6,
            "cochrane_gold": [(True, "Duplicates")] * 6 + [(False, "Unrelated")] * 6,
        }
    )
    result = run_recall_analysis(rsets, preds)
    s = sample_scle_recall(result, preds, per_stratum=3, seed="s")
    seen = set()
    for case in [*s.cases, *s.backups]:
        expected = case.pair_id.split("_")[0] + "_gold"
        assert case.reference_set_id == expected
        assert case.auto_context.reference_set_id == expected
        seen.add(case.reference_set_id)
    assert seen == {"fda_gold", "cochrane_gold"}  # both sets are represented


def test_promote_backup_swaps_in_next_ranked():
    rset, preds = _all_tp(20)
    res = run_precision_analysis(rset, preds)
    s = sample_scle_precision(rset, res, preds, per_cell=3, seed="golden-seed-1")
    disq = s.cases[0].pair_id
    promoted_expected = s.backups[0].pair_id  # rank 1, same TP stratum

    new = promote_backup(s, disq)

    assert disq not in {c.pair_id for c in new.cases}
    assert promoted_expected in {c.pair_id for c in new.cases}
    assert len(new.cases) == len(s.cases)
    assert len(new.backups) == len(s.backups) - 1
    # Original untouched.
    assert disq in {c.pair_id for c in s.cases}


def test_promote_backup_errors_when_no_backup():
    rset, preds = _all_tp(3)  # cell has 3 == cap -> no backups
    res = run_precision_analysis(rset, preds)
    s = sample_scle_precision(rset, res, preds, per_cell=3, seed="s")
    with pytest.raises(ValueError, match="no backup available"):
        promote_backup(s, s.cases[0].pair_id)


def test_promote_backup_rejects_unknown_pair():
    rset, preds = _all_tp(10)
    res = run_precision_analysis(rset, preds)
    s = sample_scle_precision(rset, res, preds, per_cell=3, seed="s")
    with pytest.raises(ValueError, match="not a primary"):
        promote_backup(s, "does__notexist")
