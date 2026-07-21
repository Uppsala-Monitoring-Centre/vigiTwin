"""Tests for the canonical input-schema validators."""

from __future__ import annotations

import warnings

import pytest
from pydantic import ValidationError

from llm_dup_dev.study_eval.schemas import (
    AdjudicatorOutput,
    MethodOutputs,
    PrecisionAnalysisInput,
    PrecisionEvaluationSet,
    PrecisionPairRecord,
    RecallEvaluationSet,
    RecallPairRecord,
)


def _prec_pair(pid, r1, r2, ref="duplicate", cluster=None):
    return PrecisionPairRecord(
        pair_id=pid, report_id1=r1, report_id2=r2, reference_label=ref, cluster_id=cluster
    )


def test_pair_id_must_match_report_ids():
    with pytest.raises(ValidationError):
        _prec_pair("WRONG", "R1", "R2")


def test_duplicate_pair_id_rejected():
    with pytest.raises(ValidationError):
        PrecisionEvaluationSet(
            set_id="s",
            pairs=[_prec_pair("R1__R2", "R1", "R2"), _prec_pair("R1__R2", "R1", "R2")],
        )


def test_flipped_orientation_of_same_pair_rejected():
    with pytest.raises(ValidationError, match="unordered pair"):
        PrecisionEvaluationSet(
            set_id="s",
            pairs=[_prec_pair("R1__R2", "R1", "R2"), _prec_pair("R2__R1", "R2", "R1")],
        )


def test_two_pairs_same_cluster_rejected():
    with pytest.raises(ValidationError, match="more than one pair"):
        PrecisionEvaluationSet(
            set_id="s",
            pairs=[
                _prec_pair("R1__R2", "R1", "R2", cluster="C1"),
                _prec_pair("R3__R4", "R3", "R4", cluster="C1"),
            ],
        )


def test_adjudicator_output_rejects_unknown_label():
    with pytest.raises(ValidationError):
        AdjudicatorOutput(pair_id="R1__R2", final_label="Bananas")


def test_precision_input_requires_full_adjudicator_coverage():
    pairs = [_prec_pair("R1__R2", "R1", "R2"), _prec_pair("R3__R4", "R3", "R4")]
    eset = PrecisionEvaluationSet(set_id="s", pairs=pairs)
    outputs = MethodOutputs(
        adjudicator={"R1__R2": AdjudicatorOutput(pair_id="R1__R2", final_label="Duplicates")},
        vigimatch_retrieved={"R1__R2": True, "R3__R4": True},
    )
    with pytest.raises(ValidationError, match="adjudicator output missing"):
        PrecisionAnalysisInput(evaluation_set=eset, outputs=outputs)


def test_precision_input_rejects_non_screened_pair():
    pairs = [_prec_pair("R1__R2", "R1", "R2")]
    eset = PrecisionEvaluationSet(set_id="s", pairs=pairs)
    outputs = MethodOutputs(
        adjudicator={"R1__R2": AdjudicatorOutput(pair_id="R1__R2", final_label="Duplicates")},
        vigimatch_retrieved={"R1__R2": False},
    )
    with pytest.raises(ValidationError, match="not flagged as"):
        PrecisionAnalysisInput(evaluation_set=eset, outputs=outputs)


def test_unknown_reference_set_warns_but_proceeds():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        RecallEvaluationSet(
            reference_set_id="atypical_set",
            reference_set_label="Atypical",
            pairs=[
                RecallPairRecord(
                    pair_id="R1__R2", report_id1="R1", report_id2="R2", cluster_id="c1"
                )
            ],
        )
    assert any("not one of the known sets" in str(w.message) for w in caught)
