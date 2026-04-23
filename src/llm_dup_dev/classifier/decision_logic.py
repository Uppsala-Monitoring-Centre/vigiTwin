"""Decision logic for the duplicate adjudication pipeline.

Adjudicates a candidate report pair by jointly evaluating the rarity-based
matching evidence (``L_match``) and the inconsistency signals.

Two layered internal labels are produced:

* an ``IndependenceAssessment`` capturing whether the matching evidence
  is strong enough to reject the null hypothesis that the two reports
  are independent draws from the comparison population
  (``NON_INDEPENDENT`` / ``INDEPENDENT`` / ``ABSTAIN``);
* when independence has been rejected, a ``DuplicateRelationship``
  sub-label that uses the inconsistency signals to distinguish a
  likely-duplicate case from one that is linked by something other
  than being the same individual case (``DUPLICATES`` /
  ``OTHERWISE_RELATED``).

These are mapped by ``ClassificationResult.final_label`` to the
user-facing labels ``Duplicates`` / ``Otherwise_related`` /
``Unrelated`` / ``Abstain``.

Inconsistencies are combined into a single weighted score::

    w_tot = w_minor × minor + w_moderate × moderate
          + w_strong × strong + w_decisive × decisive

The decision space is divided into four zones by ``L_match`` thresholds.
Each zone has its own tolerance for ``w_tot``::

    L_match > strong  →  w_tot < 4 ⇒ Non-independent / Duplicates
                          else      ⇒ Non-independent / Otherwise_related
    L_match > med     →  w_tot < 3 ⇒ Non-independent / Duplicates
                          else      ⇒ Non-independent / Otherwise_related
    L_match > weak    →  w_tot < 1 ⇒ Non-independent / Duplicates
                          else      ⇒ Non-independent / Otherwise_related
    L_match ≤ weak    →  always Independent

All three non-trivial zones share the same structure: matching evidence
above ``lmatch_weak`` is treated as sufficient to reject independence,
and ``w_tot`` only decides the duplicate-vs-otherwise-related sub-label.
Only when ``L_match`` falls at or below ``lmatch_weak`` is the matching
evidence considered too weak to reject independence at all.

Decisive inconsistencies (e.g. distinct patient identifiers from the
same study found in the narratives) participate through the same
weighted score with a large weight (``w_decisive``, default 100) that
always exceeds every zone threshold. As a result, any decisive count
forces the failing-zone branch, which combined with the zone logic
yields ``Otherwise_related`` whenever ``L_match > lmatch_weak`` and
``Independent`` otherwise — with no separate override.

See: docs/duplicate_adjudication_spec.md § Decision logic
"""

from __future__ import annotations

from llm_dup_dev.classifier.config import DecisionThresholds
from llm_dup_dev.models.classification import ClassificationResult
from llm_dup_dev.models.enums import (
    DuplicateRelationship,
    IndependenceAssessment,
    IndependenceReason,
)
from llm_dup_dev.models.narrative_source_case import (
    SourceCaseAssessment,
    SourceCaseVerdict,
)
from llm_dup_dev.models.pair_features import InconsistencyCounts, PairFeatures


# Verdicts that prove a shared source exists and therefore trigger the
# shared-source override path. Advisory verdicts
# (MULTI_PATIENT_CONTEXT_NO_LINK, NO_DECISIVE_SIGNAL, NOT_ASSESSED) fall
# through to the existing L_match zone logic.
_SHARED_SOURCE_VERDICTS = frozenset({
    SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT,
    SourceCaseVerdict.SAME_SOURCE_SAME_CASE,
    SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE,
    SourceCaseVerdict.SAME_SOURCE_CASE_UNCOMPARABLE,
})


def classify(
    features: PairFeatures,
    thresholds: DecisionThresholds | None = None,
) -> ClassificationResult:
    """Adjudicate a report pair based on its computed features.

    Parameters
    ----------
    features : PairFeatures
        Pre-computed feature set from the feature generation pipeline.
    thresholds : DecisionThresholds, optional
        Decision boundaries. Uses defaults if not provided.

    Returns
    -------
    ClassificationResult
        Classification with internal independence assessment, optional
        duplicate-relationship sub-label, and a human-readable explanation.
    """
    if thresholds is None:
        thresholds = DecisionThresholds()

    counts = features.inconsistency_counts
    L = features.L_match
    w_tot = _weighted_inconsistency(counts, thresholds)

    # --- Shared-source override ---
    # A reliable shared source identifier is itself evidence against
    # independence, regardless of L_match. Reject independence directly
    # and pick the sub-label from the verdict (plus conservative fallback
    # when non-source-case signals disagree).
    asm = features.source_case_assessment
    if asm is not None and asm.verdict in _SHARED_SOURCE_VERDICTS:
        return _shared_source_override(features, asm, thresholds, w_tot)

    # --- Strong zone: L_match > lmatch_strong ---
    if L > thresholds.lmatch_strong:
        if w_tot < thresholds.w_tot_strong_zone:
            return _duplicates(
                pair_id=features.pair_id,
                reason=IndependenceReason.RARITY_STRONG,
                explanation=(
                    f"Strong matching evidence (L_match={L:.2f} > "
                    f"{thresholds.lmatch_strong}); w_tot={w_tot:.1f} "
                    f"< {thresholds.w_tot_strong_zone:.1f}."
                ),
            )
        return _otherwise_related(
            pair_id=features.pair_id,
            reason=IndependenceReason.RARITY_STRONG,
            explanation=(
                f"Strong matching evidence (L_match={L:.2f}) but "
                f"w_tot={w_tot:.1f} >= {thresholds.w_tot_strong_zone:.1f} "
                f"(strong={counts.strong}, moderate={counts.moderate}, "
                f"decisive={counts.decisive})."
            ),
        )

    # --- Medium zone: L_match > lmatch_med ---
    if L > thresholds.lmatch_med:
        if w_tot < thresholds.w_tot_med_zone:
            return _duplicates(
                pair_id=features.pair_id,
                reason=IndependenceReason.RARITY_MEDIUM,
                explanation=(
                    f"Moderate matching evidence (L_match={L:.2f} > "
                    f"{thresholds.lmatch_med}); w_tot={w_tot:.1f} "
                    f"< {thresholds.w_tot_med_zone:.1f}."
                ),
            )
        return _otherwise_related(
            pair_id=features.pair_id,
            reason=IndependenceReason.RARITY_MEDIUM,
            explanation=(
                f"Moderate matching evidence (L_match={L:.2f}) but "
                f"w_tot={w_tot:.1f} >= {thresholds.w_tot_med_zone:.1f} "
                f"(strong={counts.strong}, moderate={counts.moderate}, "
                f"decisive={counts.decisive})."
            ),
        )

    # --- Weak zone: L_match > lmatch_weak ---
    if L > thresholds.lmatch_weak:
        if w_tot < thresholds.w_tot_weak_zone:
            return _duplicates(
                pair_id=features.pair_id,
                reason=IndependenceReason.RARITY_WEAK,
                explanation=(
                    f"Weak matching evidence (L_match={L:.2f} > "
                    f"{thresholds.lmatch_weak}); w_tot={w_tot:.1f} "
                    f"< {thresholds.w_tot_weak_zone:.1f}."
                ),
            )
        return _otherwise_related(
            pair_id=features.pair_id,
            reason=IndependenceReason.RARITY_WEAK,
            explanation=(
                f"Weak matching evidence (L_match={L:.2f}) but "
                f"w_tot={w_tot:.1f} >= {thresholds.w_tot_weak_zone:.1f} "
                f"(strong={counts.strong}, moderate={counts.moderate}, "
                f"decisive={counts.decisive})."
            ),
        )

    # --- Below weak threshold ---
    return _independent(
        pair_id=features.pair_id,
        reason=IndependenceReason.RARITY_INSUFFICIENT,
        explanation=(
            f"Matching evidence below weak threshold "
            f"(L_match={L:.2f} ≤ {thresholds.lmatch_weak}); "
            f"independence cannot be rejected."
        ),
    )


# ---------------------------------------------------------------------------
# Shared-source override
# ---------------------------------------------------------------------------


def _shared_source_override(
    features: PairFeatures,
    assessment: SourceCaseAssessment,
    thresholds: DecisionThresholds,
    w_tot: float,
) -> ClassificationResult:
    """Reject independence based on a reliable shared source identifier.

    Sub-label mapping:

    * ``SAME_SOURCE_DIFFERENT_CASE`` / ``SAME_SOURCE_CASE_UNCOMPARABLE``
      → ``OTHERWISE_RELATED``. The comparator emits a decisive / moderate
      ``Inconsistency`` for these verdicts respectively, but the sub-label
      is pinned regardless of ``w_tot``.
    * ``SAME_SOURCE_SINGLE_PATIENT`` / ``SAME_SOURCE_SAME_CASE`` →
      ``DUPLICATES`` unless ``w_tot`` already crosses the (strictest)
      weak-zone threshold, in which case the pair is downgraded to
      ``OTHERWISE_RELATED``. These verdicts emit no source-case
      inconsistency, so ``w_tot`` is free of source-case contribution by
      construction and the comparison is apples-to-apples.
    """
    verdict = assessment.verdict
    ident_str = _format_shared_identifier(assessment)

    if verdict in (
        SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE,
        SourceCaseVerdict.SAME_SOURCE_CASE_UNCOMPARABLE,
    ):
        explanation = (
            f"Shared-source override ({verdict.value}{ident_str}); "
            f"sub-label pinned to Otherwise_related."
        )
        return _otherwise_related(
            pair_id=features.pair_id,
            reason=IndependenceReason.SHARED_SOURCE,
            explanation=explanation,
        )

    # SAME_SOURCE_SINGLE_PATIENT or SAME_SOURCE_SAME_CASE
    if w_tot < thresholds.w_tot_weak_zone:
        explanation = (
            f"Shared-source override ({verdict.value}{ident_str}); "
            f"w_tot={w_tot:.1f} < {thresholds.w_tot_weak_zone:.1f}."
        )
        return _duplicates(
            pair_id=features.pair_id,
            reason=IndependenceReason.SHARED_SOURCE,
            explanation=explanation,
        )

    explanation = (
        f"Shared-source override ({verdict.value}{ident_str}) but "
        f"w_tot={w_tot:.1f} >= {thresholds.w_tot_weak_zone:.1f}; "
        f"downgraded to Otherwise_related."
    )
    return _otherwise_related(
        pair_id=features.pair_id,
        reason=IndependenceReason.SHARED_SOURCE,
        explanation=explanation,
    )


def _format_shared_identifier(assessment: SourceCaseAssessment) -> str:
    """Return ", type=value" suffix for explanation strings (empty if absent)."""
    if not assessment.shared_source_identifier:
        return ""
    id_type = (
        assessment.shared_source_identifier_type.value
        if assessment.shared_source_identifier_type is not None
        else "source_identifier"
    )
    return f", {id_type}={assessment.shared_source_identifier}"


# ---------------------------------------------------------------------------
# Weighted inconsistency score
# ---------------------------------------------------------------------------


def _weighted_inconsistency(
    counts: InconsistencyCounts,
    thresholds: DecisionThresholds,
) -> float:
    """Compute the weighted inconsistency score.

    w_tot = w_minor × minor + w_moderate × moderate
          + w_strong × strong + w_decisive × decisive
    """
    return (
        thresholds.w_minor * counts.minor
        + thresholds.w_moderate * counts.moderate
        + thresholds.w_strong * counts.strong
        + thresholds.w_decisive * counts.decisive
    )


# ---------------------------------------------------------------------------
# Result constructors
# ---------------------------------------------------------------------------


def _duplicates(
    pair_id: str,
    explanation: str,
    reason: IndependenceReason,
) -> ClassificationResult:
    """Build a Non-independent / Duplicates result."""
    return ClassificationResult(
        pair_id=pair_id,
        independence_assessment=IndependenceAssessment.NON_INDEPENDENT,
        duplicate_relationship=DuplicateRelationship.DUPLICATES,
        independence_reason=reason,
        explanation=explanation,
    )


def _otherwise_related(
    pair_id: str,
    explanation: str,
    reason: IndependenceReason,
) -> ClassificationResult:
    """Build a Non-independent / Otherwise_related result."""
    return ClassificationResult(
        pair_id=pair_id,
        independence_assessment=IndependenceAssessment.NON_INDEPENDENT,
        duplicate_relationship=DuplicateRelationship.OTHERWISE_RELATED,
        independence_reason=reason,
        explanation=explanation,
    )


def _independent(
    pair_id: str,
    explanation: str,
    reason: IndependenceReason,
) -> ClassificationResult:
    """Build an Independent result (user-facing label: Unrelated)."""
    return ClassificationResult(
        pair_id=pair_id,
        independence_assessment=IndependenceAssessment.INDEPENDENT,
        duplicate_relationship=None,
        independence_reason=reason,
        explanation=explanation,
    )
