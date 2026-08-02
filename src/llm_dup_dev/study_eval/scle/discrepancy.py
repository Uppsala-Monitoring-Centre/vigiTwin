"""Structured comparison of two independent reviewer submissions.

Only the *structured* dimensions are compared here, exactly (enums by value,
feature domains as sets). Free-text assessments are never auto-compared: the
protocol treats "a materially different interpretation in the free-text" as a
judgement the arbiter makes, so both texts are carried forward to the
arbitration workbook rather than diffed mechanically.

The dimension set is not fixed: the study's error-stage list contributes one
dimension per stage. Both submissions carry the vocabularies they were answered
under (embedded in their workbooks), so the set is derived from the data and
recorded on the :class:`DiscrepancyReport` — arbitration, consensus, and the
result tables read it from there rather than being handed the configuration
again.
"""

from __future__ import annotations

from pydantic import BaseModel

from llm_dup_dev.study_eval.scle.form import (
    ReviewerSubmission,
    SCLECaseAssessment,
    error_stage_name,
    structured_dimensions,
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
    """The structured-disagreement picture between two submissions.

    ``structured_dimensions`` records the dimensions actually compared, in
    workbook order, so downstream steps reproduce exactly this set.
    """

    evaluation_component: str
    reviewer_a: str
    reviewer_b: str
    structured_dimensions: list[str] = []
    pair_discrepancies: list[PairDiscrepancy]

    @property
    def n_pairs_with_disagreement(self) -> int:
        return sum(1 for p in self.pair_discrepancies if p.dimension_disagreements)


def _dimension_value(assessment: SCLECaseAssessment, field: str) -> str | None:
    """Render one dimension of an assessment for exact comparison.

    ``None`` means "the question was not asked of this case". The two tick-only
    list dimensions are distinguished from that: where the misclassification
    block applied but nothing was ticked, they compare as an empty string, so
    two reviewers who both ticked nothing count as agreeing rather than being
    skipped.
    """
    stage = error_stage_name(field)
    if stage is not None:
        if not assessment.misclassification_answered:
            return None
        return "yes" if stage in assessment.error_stages else "no"
    if field == "implicated_feature_domains":
        if not assessment.misclassification_answered:
            return None
        # Order-independent comparison; render as a stable joined string.
        return ",".join(sorted(assessment.implicated_feature_domains))
    value = getattr(assessment, field)
    if value is None:
        return None
    return getattr(value, "value", value)


def report_dimensions(
    sub_a: ReviewerSubmission, sub_b: ReviewerSubmission
) -> tuple[str, ...]:
    """Return the structured dimensions two submissions are comparable on.

    Raises ``ValueError`` if the two workbooks were generated under different
    vocabularies, which would make a dimension-by-dimension comparison
    meaningless.
    """
    if sub_a.error_stages != sub_b.error_stages:
        raise ValueError(
            f"the two submissions carry different error-stage vocabularies: "
            f"{sub_a.error_stages} vs {sub_b.error_stages}."
        )
    if sub_a.feature_domains != sub_b.feature_domains:
        raise ValueError(
            f"the two submissions carry different feature-domain vocabularies: "
            f"{sub_a.feature_domains} vs {sub_b.feature_domains}."
        )
    return structured_dimensions(sub_a.error_stages)


def detect_discrepancies(
    sub_a: ReviewerSubmission,
    sub_b: ReviewerSubmission,
) -> DiscrepancyReport:
    """Compare two submissions dimension by dimension.

    Both submissions must be for the same component, cover the same pairs (they
    are generated from one sample), and carry the same vocabularies, otherwise a
    ``ValueError`` is raised.
    """
    if sub_a.evaluation_component != sub_b.evaluation_component:
        raise ValueError(
            f"components differ: {sub_a.evaluation_component!r} vs "
            f"{sub_b.evaluation_component!r}."
        )
    dimensions = report_dimensions(sub_a, sub_b)
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
        for field in dimensions:
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
        structured_dimensions=list(dimensions),
        pair_discrepancies=discrepancies,
    )


__all__ = [
    "DimensionDisagreement",
    "PairDiscrepancy",
    "DiscrepancyReport",
    "report_dimensions",
    "detect_discrepancies",
]
