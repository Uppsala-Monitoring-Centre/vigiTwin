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
from llm_dup_dev.models.enums import DuplicateRelationship, IndependenceAssessment
from llm_dup_dev.models.pair_features import InconsistencyCounts, PairFeatures


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

    # --- Strong zone: L_match > lmatch_strong ---
    if L > thresholds.lmatch_strong:
        if w_tot < thresholds.w_tot_strong_zone:
            return _duplicates(
                pair_id=features.pair_id,
                explanation=(
                    f"Strong matching evidence (L_match={L:.2f} > "
                    f"{thresholds.lmatch_strong}); w_tot={w_tot:.1f} "
                    f"< {thresholds.w_tot_strong_zone:.1f}."
                ),
            )
        return _otherwise_related(
            pair_id=features.pair_id,
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
                explanation=(
                    f"Moderate matching evidence (L_match={L:.2f} > "
                    f"{thresholds.lmatch_med}); w_tot={w_tot:.1f} "
                    f"< {thresholds.w_tot_med_zone:.1f}."
                ),
            )
        return _otherwise_related(
            pair_id=features.pair_id,
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
                explanation=(
                    f"Weak matching evidence (L_match={L:.2f} > "
                    f"{thresholds.lmatch_weak}); w_tot={w_tot:.1f} "
                    f"< {thresholds.w_tot_weak_zone:.1f}."
                ),
            )
        return _otherwise_related(
            pair_id=features.pair_id,
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
        explanation=(
            f"Matching evidence below weak threshold "
            f"(L_match={L:.2f} ≤ {thresholds.lmatch_weak}); "
            f"independence cannot be rejected."
        ),
    )


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


def _duplicates(pair_id: str, explanation: str) -> ClassificationResult:
    """Build a Non-independent / Duplicates result."""
    return ClassificationResult(
        pair_id=pair_id,
        independence_assessment=IndependenceAssessment.NON_INDEPENDENT,
        duplicate_relationship=DuplicateRelationship.DUPLICATES,
        explanation=explanation,
    )


def _otherwise_related(pair_id: str, explanation: str) -> ClassificationResult:
    """Build a Non-independent / Otherwise_related result."""
    return ClassificationResult(
        pair_id=pair_id,
        independence_assessment=IndependenceAssessment.NON_INDEPENDENT,
        duplicate_relationship=DuplicateRelationship.OTHERWISE_RELATED,
        explanation=explanation,
    )


def _independent(pair_id: str, explanation: str) -> ClassificationResult:
    """Build an Independent result (user-facing label: Unrelated)."""
    return ClassificationResult(
        pair_id=pair_id,
        independence_assessment=IndependenceAssessment.INDEPENDENT,
        duplicate_relationship=None,
        explanation=explanation,
    )
