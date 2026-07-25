"""Tests for the protocol aggregate precision/recall calculations."""

from __future__ import annotations

from llm_dup_dev.study_eval.aggregate import (
    AnalysisVariant,
    is_positive,
    run_precision_analysis,
    run_precision_variants,
    run_recall_analysis,
)

from tests.test_study_eval.conftest import make_precision, make_recall

# 2 TP, 1 FP, 1 FN, 1 TN, 1 reference-abstain.
_PRECISION_SPEC = [
    ("duplicate", "Duplicates"),  # TP
    ("duplicate", "Duplicates"),  # TP
    ("non_duplicate", "Duplicates"),  # FP
    ("duplicate", "Otherwise_related"),  # FN
    ("non_duplicate", "Unrelated"),  # TN
    ("abstain", "Duplicates"),  # excluded
]


def test_positivity_rules():
    assert is_positive("Duplicates", AnalysisVariant.PRIMARY)
    assert not is_positive("Abstain", AnalysisVariant.PRIMARY)
    assert not is_positive("Otherwise_related", AnalysisVariant.PRIMARY)

    assert is_positive("Abstain", AnalysisVariant.ABSTAIN_POSITIVE)
    assert is_positive("Duplicates", AnalysisVariant.ABSTAIN_POSITIVE)
    assert not is_positive("Otherwise_related", AnalysisVariant.ABSTAIN_POSITIVE)

    assert is_positive("Otherwise_related", AnalysisVariant.OTHERWISE_RELATED_POSITIVE)
    assert not is_positive("Abstain", AnalysisVariant.OTHERWISE_RELATED_POSITIVE)


def test_precision_cells_and_metrics():
    rset, preds = make_precision(_PRECISION_SPEC)
    r = run_precision_analysis(rset, preds)
    assert r.n_pairs == 6
    assert r.n_reference_abstain == 1
    assert r.n_evaluable == 5
    assert r.n_reference_duplicate == 3
    assert r.n_reference_non_duplicate == 2

    # adjudicator precision = TP / (TP + FP) = 2 / 3
    assert r.adjudicator_precision.numerator == 2
    assert r.adjudicator_precision.denominator == 3
    # conditional recall = TP / reference-duplicates = 2 / 3
    assert r.conditional_recall.numerator == 2
    assert r.conditional_recall.denominator == 3
    # FP rejection = TN / reference-non-duplicates = 1 / 2
    assert r.fp_rejection.numerator == 1
    assert r.fp_rejection.denominator == 2
    # screening baseline precision = reference-duplicates / evaluable = 3 / 5
    assert r.screening_baseline_precision.numerator == 3
    assert r.screening_baseline_precision.denominator == 5

    cells = sorted(r.pair_cells.values())
    assert cells == ["FN", "FP", "TN", "TP", "TP", "excluded"]


def test_cross_classification_totals():
    rset, preds = make_precision(_PRECISION_SPEC)
    r = run_precision_analysis(rset, preds)
    total = sum(
        count for row in r.cross_classification.values() for count in row.values()
    )
    assert total == r.n_pairs


def test_abstain_positive_variant_moves_abstain_into_positives():
    rset, preds = make_precision(
        [
            ("duplicate", "Abstain"),  # primary: FN; abstain-positive: TP
            ("non_duplicate", "Unrelated"),  # TN in both
        ]
    )
    primary = run_precision_analysis(rset, preds, AnalysisVariant.PRIMARY)
    assert primary.pair_cells["A0__B0"] == "FN"

    ap = run_precision_analysis(rset, preds, AnalysisVariant.ABSTAIN_POSITIVE)
    assert ap.pair_cells["A0__B0"] == "TP"
    assert ap.conditional_recall.numerator == 1


def test_run_precision_variants_returns_three():
    rset, preds = make_precision(_PRECISION_SPEC)
    results = run_precision_variants(rset, preds)
    assert set(v.value for v in results) == {
        "primary",
        "abstain_positive",
        "otherwise_related_positive",
    }


def test_precision_rejects_non_screened_pair():
    import pytest

    rset, preds = make_precision(
        [("duplicate", "Duplicates")], screening={"A0__B0": False}
    )
    with pytest.raises(ValueError, match="not flagged as screening-retrieved"):
        run_precision_analysis(rset, preds)


def test_recall_estimands_and_invariants():
    rsets, preds = make_recall(
        {
            "fda_gold": [
                (True, "Duplicates"),  # both
                (True, "Unrelated"),  # screening_only
                (False, "Duplicates"),  # adjudicator_only
                (False, "Unrelated"),  # neither
            ]
        }
    )
    r = run_recall_analysis(rsets, preds)
    s = r.sets[0]
    assert s.n_pairs == 4
    assert s.recall_screening.numerator == 2
    assert s.recall_serial.numerator == 1  # only the "both" pair
    assert s.recall_adjudicator_ceiling.numerator == 2
    assert s.joint_counts == {
        "both": 1,
        "screening_only": 1,
        "adjudicator_only": 1,
        "neither": 1,
    }
    # Invariants.
    assert s.recall_serial.proportion <= s.recall_screening.proportion
    assert s.recall_serial.proportion <= s.recall_adjudicator_ceiling.proportion
    assert sum(s.joint_counts.values()) == s.n_pairs
    assert s.recall_reduction == s.recall_screening.proportion - s.recall_serial.proportion


def test_recall_pair_strata_cover_all_pairs():
    rsets, preds = make_recall({"lareb": [(True, "Duplicates"), (False, "Unrelated")]})
    r = run_recall_analysis(rsets, preds)
    assert set(r.sets[0].pair_strata.values()) <= {
        "both",
        "neither",
        "screening_only",
        "adjudicator_only",
    }
    assert len(r.sets[0].pair_strata) == 2
