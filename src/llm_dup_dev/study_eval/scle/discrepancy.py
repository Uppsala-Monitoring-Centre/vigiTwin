"""Structured comparison of two independent reviewer submissions.

Only the *structured* dimensions are compared here, exactly (enums by value,
feature domains as sets). Free-text assessments are never auto-compared: the
protocol treats "a materially different interpretation in the free-text" as a
judgement the arbiter makes, so both texts are carried forward to the
arbitration workbook rather than diffed mechanically.
"""

from __future__ import annotations

from pydantic import BaseModel

from llm_dup_dev.study_eval.scle.form import ReviewerSubmission, SCLECaseAssessment

#: Structured dimensions compared exactly. Free text is excluded by design.
STRUCTURED_DIMENSIONS: tuple[str, ...] = (
    "case_difficulty",
    "reference_label_issue",
    "never_event",
    "unexpected_method_behaviour",
    "material_input_data_limitation",
    "input_data_limitation_category",
    "primary_error_source",
    "implicated_feature_domains",
)


class DimensionDisagreement(BaseModel):
    """A single dimension on which the two reviewers differ."""

    field: str
    value_a: str | None
    value_b: str | None


class PairDiscrepancy(BaseModel):
    """All structured disagreements on one pair (empty list means full agreement)."""

    pair_id: str
    dimension_disagreements: list[DimensionDisagreement]


class DiscrepancyReport(BaseModel):
    """The structured-disagreement picture between two submissions."""

    evaluation_component: str
    reviewer_a: str
    reviewer_b: str
    pair_discrepancies: list[PairDiscrepancy]

    @property
    def n_pairs_with_disagreement(self) -> int:
        return sum(1 for p in self.pair_discrepancies if p.dimension_disagreements)


def _dimension_value(assessment: SCLECaseAssessment, field: str) -> str | None:
    value = getattr(assessment, field)
    if field == "implicated_feature_domains":
        # Order-independent comparison; render as a stable joined string.
        return ",".join(sorted(value)) if value else None
    if value is None:
        return None
    return getattr(value, "value", value)


def detect_discrepancies(
    sub_a: ReviewerSubmission,
    sub_b: ReviewerSubmission,
) -> DiscrepancyReport:
    """Compare two submissions dimension by dimension.

    Both submissions must be for the same component and cover the same pairs
    (they are generated from one sample), otherwise a ``ValueError`` is raised.
    """
    if sub_a.evaluation_component != sub_b.evaluation_component:
        raise ValueError(
            f"components differ: {sub_a.evaluation_component!r} vs "
            f"{sub_b.evaluation_component!r}."
        )
    by_pair_a = {a.pair_id: a for a in sub_a.assessments}
    by_pair_b = {b.pair_id: b for b in sub_b.assessments}
    if set(by_pair_a) != set(by_pair_b):
        only_a = sorted(set(by_pair_a) - set(by_pair_b))
        only_b = sorted(set(by_pair_b) - set(by_pair_a))
        raise ValueError(
            f"submissions cover different pairs; only in A: {only_a}; "
            f"only in B: {only_b}."
        )

    discrepancies: list[PairDiscrepancy] = []
    for pair_id in sorted(by_pair_a):
        a = by_pair_a[pair_id]
        b = by_pair_b[pair_id]
        diffs: list[DimensionDisagreement] = []
        for field in STRUCTURED_DIMENSIONS:
            va = _dimension_value(a, field)
            vb = _dimension_value(b, field)
            if va != vb:
                diffs.append(
                    DimensionDisagreement(field=field, value_a=va, value_b=vb)
                )
        discrepancies.append(
            PairDiscrepancy(pair_id=pair_id, dimension_disagreements=diffs)
        )

    return DiscrepancyReport(
        evaluation_component=sub_a.evaluation_component,
        reviewer_a=sub_a.reviewer_id,
        reviewer_b=sub_b.reviewer_id,
        pair_discrepancies=discrepancies,
    )


__all__ = [
    "STRUCTURED_DIMENSIONS",
    "DimensionDisagreement",
    "PairDiscrepancy",
    "DiscrepancyReport",
    "detect_discrepancies",
]
