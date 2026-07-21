"""Tests for deterministic sampling — determinism, golden values, protocol caps."""

from __future__ import annotations

from llm_dup_dev.study_eval.aggregate import run_precision_analysis, run_recall_analysis
from llm_dup_dev.study_eval.sampling import (
    sample_one_per_cluster,
    sample_scle_precision,
    sample_scle_recall,
)
from llm_dup_dev.study_eval.schemas import RecallPairRecord

from tests.test_study_eval.conftest import make_precision_input, make_recall_input

# Golden draws pinned for a fixed synthetic input + seed. A change here means
# the sampling internals changed and must be consciously re-blessed.
_CLUSTER_GOLDEN = ["P00__Q00", "P01__Q01", "P25__Q25", "P08__Q08", "P14__Q14", "P23__Q23"]
_MANUAL_GOLDEN = ["P00__Q00", "P21__Q21", "P15__Q15", "P18__Q18", "P24__Q24", "P03__Q03"]


def _golden_pairs():
    return [
        RecallPairRecord(
            pair_id=f"P{i:02d}__Q{i:02d}",
            report_id1=f"P{i:02d}",
            report_id2=f"Q{i:02d}",
            cluster_id=f"cl{i % 10}",
            provenance="manual" if i % 3 == 0 else "e2b_derived",
        )
        for i in range(30)
    ]


def test_cluster_sampling_is_deterministic():
    pairs = _golden_pairs()
    a, _ = sample_one_per_cluster(pairs, target=6, seed="golden-seed-1")
    b, _ = sample_one_per_cluster(pairs, target=6, seed="golden-seed-1")
    assert [p.pair_id for p in a] == [p.pair_id for p in b]


def test_cluster_sampling_golden():
    pairs = _golden_pairs()
    selected, record = sample_one_per_cluster(pairs, target=6, seed="golden-seed-1")
    assert [p.pair_id for p in selected] == _CLUSTER_GOLDEN
    assert record.selected_pair_ids == _CLUSTER_GOLDEN
    assert record.seed == "golden-seed-1"


def test_manual_priority_golden_and_all_manual():
    pairs = _golden_pairs()
    selected, _ = sample_one_per_cluster(
        pairs, target=6, seed="golden-seed-1", manual_priority=True
    )
    assert [p.pair_id for p in selected] == _MANUAL_GOLDEN
    # Every chosen pair is manually classified.
    assert all(p.provenance == "manual" for p in selected)


def test_one_pair_per_cluster_invariant():
    pairs = _golden_pairs()
    selected, _ = sample_one_per_cluster(pairs, target=100, seed="s")
    clusters = [p.cluster_id for p in selected]
    assert len(clusters) == len(set(clusters))
    # 10 distinct clusters exist; target 100 -> all 10 taken.
    assert len(selected) == 10


def test_different_seed_changes_draw():
    pairs = _golden_pairs()
    a, _ = sample_one_per_cluster(pairs, target=5, seed="seedA")
    b, _ = sample_one_per_cluster(pairs, target=5, seed="seedB")
    assert [p.pair_id for p in a] != [p.pair_id for p in b]


def test_ineligible_pairs_excluded():
    pairs = _golden_pairs()
    for p in pairs:
        if p.cluster_id in {"cl0", "cl1"}:
            object.__setattr__(p, "eligible", False)
    selected, _ = sample_one_per_cluster(pairs, target=100, seed="s")
    assert all(p.cluster_id not in {"cl0", "cl1"} for p in selected)


def test_scle_precision_respects_cell_cap():
    # 25 pairs, all TP.
    inp = make_precision_input([("duplicate", "Duplicates")] * 25)
    result = run_precision_analysis(inp)
    sample = sample_scle_precision(inp, result, per_cell=10, seed="s")
    tp_cases = [c for c in sample.cases if c.stratum == "TP"]
    assert len(tp_cases) == 10  # capped
    # Determinism.
    sample2 = sample_scle_precision(inp, result, per_cell=10, seed="s")
    assert [c.pair_id for c in sample.cases] == [c.pair_id for c in sample2.cases]


def test_scle_precision_takes_all_when_under_cap():
    inp = make_precision_input([("duplicate", "Duplicates")] * 3)
    result = run_precision_analysis(inp)
    sample = sample_scle_precision(inp, result, per_cell=10, seed="s")
    assert len([c for c in sample.cases if c.stratum == "TP"]) == 3


def test_scle_recall_caps_per_stratum_per_set():
    inp = make_recall_input(
        {"fda_gold": [(True, "Duplicates")] * 8 + [(False, "Unrelated")] * 8}
    )
    result = run_recall_analysis(inp)
    sample = sample_scle_recall(inp, result, per_stratum=5, seed="s")
    both = [c for c in sample.cases if c.stratum == "both"]
    neither = [c for c in sample.cases if c.stratum == "neither"]
    assert len(both) == 5
    assert len(neither) == 5
    assert all(c.reference_set_id == "fda_gold" for c in sample.cases)
