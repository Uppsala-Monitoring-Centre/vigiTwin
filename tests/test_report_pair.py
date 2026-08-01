"""Tests for the canonical identity of llm_dup_dev.models.ReportPair.

A pair has one identity regardless of the order its two report ids arrive in.
Construction canonicalises; reading an existing record validates and refuses,
because ``pair_id`` is the sole join key against stored features, annotations
and predictions — silently rewriting it would break exactly the joins the
invariant exists to protect.
"""

import pytest
from pydantic import ValidationError

from llm_dup_dev.models.report import Report
from llm_dup_dev.models.report_pair import (
    ALLOW_NONCANONICAL_CONTEXT_KEY,
    ReportPair,
)

_RELAXED = {ALLOW_NONCANONICAL_CONTEXT_KEY: True}


def _raw(pair_id, r1, r2):
    return {"pair_id": pair_id, "report_1_id": r1, "report_2_id": r2}


# ===================================================================
# Construction canonicalises
# ===================================================================

class TestFromReportIds:
    def test_reversed_inputs_produce_the_same_pair(self):
        assert ReportPair.from_report_ids(
            "R002", "R001"
        ) == ReportPair.from_report_ids("R001", "R002")

    def test_report_ids_are_stored_in_canonical_order(self):
        pair = ReportPair.from_report_ids("R002", "R001")
        assert (pair.report_1_id, pair.report_2_id) == ("R001", "R002")
        assert pair.pair_id == "R001__R002"

    def test_numeric_ids_order_numerically(self):
        """"10" sorts before "9" as a string; report ids must not."""
        pair = ReportPair.from_report_ids("10", "9")
        assert (pair.report_1_id, pair.report_2_id) == ("9", "10")
        assert pair.pair_id == "9__10"

    def test_self_pair_rejected(self):
        with pytest.raises(ValueError, match="two distinct reports"):
            ReportPair.from_report_ids("R001", "R001")

    def test_from_reports_canonicalises_too(self):
        r1, r2 = Report(report_id="R002"), Report(report_id="R001")
        pair = ReportPair.from_reports(r1, r2)
        assert pair.pair_id == "R001__R002"
        assert pair == ReportPair.from_reports(r2, r1)


# ===================================================================
# Reading an existing record validates rather than repairs
# ===================================================================

class TestValidation:
    def test_accepts_a_canonical_record(self):
        pair = ReportPair.model_validate(_raw("R001__R002", "R001", "R002"))
        assert pair.pair_id == "R001__R002"

    def test_rejects_reversed_report_order(self):
        with pytest.raises(ValidationError, match="not in canonical order"):
            ReportPair.model_validate(_raw("R002__R001", "R002", "R001"))

    def test_rejects_noncanonical_numeric_order(self):
        with pytest.raises(ValidationError, match="not in canonical order"):
            ReportPair.model_validate(_raw("10__9", "10", "9"))

    def test_rejects_a_pair_id_inconsistent_with_its_report_ids(self):
        with pytest.raises(ValidationError, match="inconsistent with its report ids"):
            ReportPair.model_validate(_raw("p1", "R001", "R002"))

    def test_rejects_a_self_pair(self):
        with pytest.raises(ValidationError, match="two distinct reports"):
            ReportPair.model_validate(_raw("R001__R001", "R001", "R001"))

    def test_rejects_a_blank_report_id(self):
        with pytest.raises(ValidationError, match="non-blank string"):
            ReportPair.model_validate(_raw("R001__", "R001", ""))

    def test_rejects_a_separator_inside_a_report_id(self):
        """It would make the pair_id impossible to split unambiguously."""
        with pytest.raises(ValidationError, match="pair_id separator"):
            ReportPair.model_validate(_raw("A__B__C", "A__B", "C"))

    def test_error_says_to_regenerate_rather_than_reorder(self):
        with pytest.raises(ValidationError, match="Regenerate the artifact"):
            ReportPair.model_validate(_raw("R002__R001", "R002", "R001"))

    def test_does_not_coerce_int_report_ids(self):
        """Numeric ids out of SQL must be str()-ed by the caller."""
        with pytest.raises(ValidationError):
            ReportPair.model_validate(_raw("9__10", 9, 10))


# ===================================================================
# The escape hatch for pre-canonicalisation artifacts
# ===================================================================

class TestNoncanonicalContext:
    def test_relaxed_context_accepts_a_legacy_reversed_pair(self):
        pair = ReportPair.model_validate(
            _raw("R002__R001", "R002", "R001"), context=_RELAXED
        )
        assert (pair.report_1_id, pair.report_2_id) == ("R002", "R001")
        assert pair.pair_id == "R002__R001"

    def test_relaxed_context_leaves_the_record_unchanged(self):
        """The hatch renders old data as-written; it never rewrites the id."""
        raw = _raw("legacy-id", "R002", "R001")
        pair = ReportPair.model_validate(raw, context=_RELAXED)
        assert pair.model_dump() == raw

    def test_relaxed_context_still_rejects_a_self_pair(self):
        with pytest.raises(ValidationError, match="two distinct reports"):
            ReportPair.model_validate(
                _raw("R001__R001", "R001", "R001"), context=_RELAXED
            )

    def test_relaxed_context_still_rejects_a_blank_report_id(self):
        with pytest.raises(ValidationError, match="non-blank string"):
            ReportPair.model_validate(_raw("x", "R001", ""), context=_RELAXED)

    def test_an_unrelated_context_does_not_relax_anything(self):
        with pytest.raises(ValidationError, match="not in canonical order"):
            ReportPair.model_validate(
                _raw("R002__R001", "R002", "R001"), context={"something": True}
            )
