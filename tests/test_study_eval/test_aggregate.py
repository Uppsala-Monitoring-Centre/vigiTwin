"""Tests for the protocol aggregate precision/recall calculations."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from llm_dup_dev.study_eval.aggregate import (
    ABSTAIN_POSITIVE,
    ADJUDICATOR_LABELS,
    OTHERWISE_RELATED_POSITIVE,
    PRIMARY,
    PositiveClass,
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


class TestPositiveClassValidation:
    """A collapse rule is study configuration, so it is validated as such."""

    def test_labels_outside_the_output_vocabulary_are_rejected(self):
        with pytest.raises(ValidationError, match="Duplicate'"):
            PositiveClass(
                name="typo", positive_labels=frozenset({"Duplicate"})
            )

    def test_empty_positive_labels_is_rejected(self):
        with pytest.raises(ValidationError, match="non-empty"):
            PositiveClass(name="empty", positive_labels=frozenset())

    def test_blank_name_is_rejected(self):
        with pytest.raises(ValidationError, match="blank"):
            PositiveClass(name="   ", positive_labels=frozenset({"Duplicates"}))

    def test_name_is_trimmed(self):
        assert (
            PositiveClass(
                name="  primary  ", positive_labels=frozenset({"Duplicates"})
            ).name
            == "primary"
        )

    def test_is_frozen_and_hashable(self):
        pc = PositiveClass(name="primary", positive_labels=frozenset({"Duplicates"}))
        assert {pc: 1}[pc] == 1
        with pytest.raises(ValidationError):
            pc.name = "other"

    def test_every_named_constant_is_within_the_vocabulary(self):
        for pc in (PRIMARY, ABSTAIN_POSITIVE, OTHERWISE_RELATED_POSITIVE):
            assert pc.positive_labels <= set(ADJUDICATOR_LABELS)


def test_a_study_supplied_rule_the_package_never_named_is_usable():
    """The protocol's "Abstain *and* Otherwise related positive" analysis.

    This three-label collapse is what the old fixed enum could not express, and is
    the reason the rule became caller-supplied.
    """
    rule = PositiveClass(
        name="abstain_and_otherwise_positive",
        positive_labels=frozenset({"Duplicates", "Abstain", "Otherwise_related"}),
    )
    rset, preds = make_precision(_PRECISION_SPEC)
    r = run_precision_analysis(rset, preds, rule)

    assert r.variant == "abstain_and_otherwise_positive"
    # The FN under the primary rule (duplicate called Otherwise_related) is a TP
    # here, and the reference-abstain pair stays excluded regardless of the rule.
    assert r.pair_cells["A3__B3"] == "TP"
    assert r.pair_cells["A5__B5"] == "excluded"
    assert r.n_reference_abstain == 1
    assert r.conditional_recall.numerator == 3


def test_variants_helpers_reject_an_empty_or_ambiguous_rule_list():
    rset, preds = make_precision(_PRECISION_SPEC)
    with pytest.raises(ValueError, match="at least one"):
        run_precision_variants(rset, preds, [])
    twice = PositiveClass(name="primary", positive_labels=frozenset({"Abstain"}))
    with pytest.raises(ValueError, match="duplicate PositiveClass name"):
        run_precision_variants(rset, preds, [PRIMARY, twice])


def test_positivity_rules():
    assert is_positive("Duplicates", PRIMARY)
    assert not is_positive("Abstain", PRIMARY)
    assert not is_positive("Otherwise_related", PRIMARY)

    assert is_positive("Abstain", ABSTAIN_POSITIVE)
    assert is_positive("Duplicates", ABSTAIN_POSITIVE)
    assert not is_positive("Otherwise_related", ABSTAIN_POSITIVE)

    assert is_positive("Otherwise_related", OTHERWISE_RELATED_POSITIVE)
    assert not is_positive("Abstain", OTHERWISE_RELATED_POSITIVE)


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
    primary = run_precision_analysis(rset, preds, PRIMARY)
    assert primary.pair_cells["A0__B0"] == "FN"

    ap = run_precision_analysis(rset, preds, ABSTAIN_POSITIVE)
    assert ap.pair_cells["A0__B0"] == "TP"
    assert ap.conditional_recall.numerator == 1


def test_run_precision_variants_returns_three():
    rset, preds = make_precision(_PRECISION_SPEC)
    results = run_precision_variants(
        rset, preds, [PRIMARY, ABSTAIN_POSITIVE, OTHERWISE_RELATED_POSITIVE]
    )
    assert set(results) == {
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


# --- screening_mode: not_evaluated -------------------------------------------


def _not_evaluated_set():
    # 2 of 3 supplied pairs are adjudicator-positive.
    return make_recall(
        {
            "manual": [
                (None, "Duplicates"),
                (None, "Duplicates"),
                (None, "Unrelated"),
            ]
        },
        modes={"manual": "not_evaluated"},
    )


def test_not_evaluated_omits_every_screening_estimand():
    rsets, preds = _not_evaluated_set()
    s = run_recall_analysis(rsets, preds).sets[0]

    assert s.screening_mode == "not_evaluated"
    # Absent, not estimated as zero, one, or an undefined estimate at n=0.
    assert s.recall_screening is None
    assert s.recall_serial is None
    assert s.recall_reduction is None
    assert s.joint_counts is None
    assert s.joint_proportions is None


def test_not_evaluated_still_estimates_adjudicator_recall():
    rsets, preds = _not_evaluated_set()
    s = run_recall_analysis(rsets, preds).sets[0]

    assert s.n_pairs == 3
    assert s.recall_adjudicator_ceiling.numerator == 2
    assert s.recall_adjudicator_ceiling.denominator == 3


def test_not_evaluated_strata_are_adjudicator_only():
    rsets, preds = _not_evaluated_set()
    s = run_recall_analysis(rsets, preds).sets[0]

    assert set(s.pair_strata.values()) <= {
        "adjudicator_positive",
        "adjudicator_negative",
    }
    assert len(s.pair_strata) == 3


def test_not_evaluated_ignores_a_supplied_screening_verdict():
    """No screening figure appears, whether or not the data happen to exist."""
    with_data, preds_with = make_recall(
        {"m": [(True, "Duplicates"), (True, "Unrelated")]},
        modes={"m": "not_evaluated"},
    )
    without_data, preds_without = make_recall(
        {"m": [(None, "Duplicates"), (None, "Unrelated")]},
        modes={"m": "not_evaluated"},
    )
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        a = run_recall_analysis(with_data, preds_with).sets[0]
    b = run_recall_analysis(without_data, preds_without).sets[0]

    assert a.model_dump() == b.model_dump()


def test_mixed_modes_leave_the_observed_set_unchanged():
    mixed, preds = make_recall(
        {
            "fda_gold": [
                (True, "Duplicates"),
                (True, "Unrelated"),
                (False, "Duplicates"),
                (False, "Unrelated"),
            ],
            "manual": [(None, "Duplicates"), (None, "Unrelated")],
        },
        modes={"manual": "not_evaluated"},
    )
    result = run_recall_analysis(mixed, preds)
    observed = next(s for s in result.sets if s.reference_set_id == "fda_gold")

    # Identical to the single-set observed expectations above.
    assert observed.recall_screening.numerator == 2
    assert observed.recall_serial.numerator == 1
    assert observed.joint_counts == {
        "both": 1,
        "screening_only": 1,
        "adjudicator_only": 1,
        "neither": 1,
    }


def test_mixed_modes_expose_the_screening_scope():
    mixed, preds = make_recall(
        {
            "fda_gold": [(True, "Duplicates")],
            "manual": [(None, "Duplicates")],
        },
        modes={"manual": "not_evaluated"},
    )
    result = run_recall_analysis(mixed, preds)

    assert result.screening_evaluated_set_ids == ["fda_gold"]
    assert result.screening_not_evaluated_set_ids == ["manual"]
