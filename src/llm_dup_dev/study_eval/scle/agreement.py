"""Inter-reviewer agreement statistics for the structured SCLE dimensions.

Computed per structured dimension over the pairs where that dimension applies to
both reviewers (a dimension is either applicable to both or neither, enforced at
submission read-back). For each dimension: the number of comparable pairs, raw
percent agreement, and **Cohen's kappa** (``None`` when kappa is undefined — no
comparable pairs, or both reviewers used a single identical category). Free-text
assessments are not compared here (that is an arbiter judgement).
"""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel

from llm_dup_dev.study_eval.scle.discrepancy import (
    STRUCTURED_DIMENSIONS,
    _dimension_value,
)
from llm_dup_dev.study_eval.scle.form import ReviewerSubmission


class DimensionAgreement(BaseModel):
    """Agreement on one structured dimension."""

    field: str
    n_compared: int
    n_agree: int
    percent_agreement: float | None
    cohens_kappa: float | None


class AgreementReport(BaseModel):
    """Inter-reviewer agreement across all structured dimensions."""

    evaluation_component: str
    reviewer_a: str
    reviewer_b: str
    n_pairs: int
    dimensions: list[DimensionAgreement]
    overall_percent_agreement: float | None


def _cohens_kappa(paired: list[tuple[str, str]]) -> float | None:
    """Cohen's kappa for a list of ``(rater_a, rater_b)`` categorical labels.

    Returns ``None`` when kappa is undefined: no pairs, or the raters between
    them used only a single category (perfect but degenerate agreement), or the
    expected-agreement denominator collapses to zero.
    """
    n = len(paired)
    if n == 0:
        return None
    categories = {c for pair in paired for c in pair}
    if len(categories) < 2:
        return None
    p_o = sum(1 for a, b in paired if a == b) / n
    count_a = Counter(a for a, _ in paired)
    count_b = Counter(b for _, b in paired)
    p_e = sum((count_a[c] / n) * (count_b[c] / n) for c in categories)
    if p_e >= 1.0:
        return None
    return (p_o - p_e) / (1.0 - p_e)


def compute_agreement(
    sub_a: ReviewerSubmission, sub_b: ReviewerSubmission
) -> AgreementReport:
    """Compute inter-reviewer agreement between two submissions.

    Both submissions must be for the same component and cover the same pairs
    (raises ``ValueError`` otherwise).
    """
    if sub_a.evaluation_component != sub_b.evaluation_component:
        raise ValueError(
            f"components differ: {sub_a.evaluation_component!r} vs "
            f"{sub_b.evaluation_component!r}."
        )
    by_pair_a = {a.pair_id: a for a in sub_a.assessments}
    by_pair_b = {b.pair_id: b for b in sub_b.assessments}
    if set(by_pair_a) != set(by_pair_b):
        raise ValueError("submissions cover different pairs.")

    pair_ids = sorted(by_pair_a)
    dimensions: list[DimensionAgreement] = []
    total_compared = total_agree = 0

    for field in STRUCTURED_DIMENSIONS:
        paired: list[tuple[str, str]] = []
        for pid in pair_ids:
            va = _dimension_value(by_pair_a[pid], field)
            vb = _dimension_value(by_pair_b[pid], field)
            if va is None and vb is None:
                continue  # dimension not applicable to this pair
            paired.append((va or "", vb or ""))
        n = len(paired)
        n_agree = sum(1 for a, b in paired if a == b)
        total_compared += n
        total_agree += n_agree
        dimensions.append(
            DimensionAgreement(
                field=field,
                n_compared=n,
                n_agree=n_agree,
                percent_agreement=(n_agree / n) if n else None,
                cohens_kappa=_cohens_kappa(paired),
            )
        )

    overall = (total_agree / total_compared) if total_compared else None
    return AgreementReport(
        evaluation_component=sub_a.evaluation_component,
        reviewer_a=sub_a.reviewer_id,
        reviewer_b=sub_b.reviewer_id,
        n_pairs=len(pair_ids),
        dimensions=dimensions,
        overall_percent_agreement=overall,
    )


__all__ = [
    "DimensionAgreement",
    "AgreementReport",
    "compute_agreement",
]
