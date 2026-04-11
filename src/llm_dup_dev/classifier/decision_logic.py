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

    w_tot = w_minor × minor + w_moderate × moderate + w_strong × strong

The decision space is divided into four zones by ``L_match`` thresholds.
Each zone has its own tolerance for ``w_tot``::

    L_match > strong  →  w_tot < 4 ⇒ Non-independent / Duplicates
                          else      ⇒ Non-independent / Otherwise_related
    L_match > med     →  w_tot < 3 ⇒ Non-independent / Duplicates
                          else      ⇒ Non-independent / Otherwise_related
    L_match > weak    →  w_tot < 1 ⇒ Non-independent / Duplicates
                          else      ⇒ Independent
    L_match ≤ weak    →  always Independent

The asymmetry in the weak zone is deliberate: at very low ``L_match``
the independence test is only marginally rejected, and any inconsistency
is enough to fall back to "couldn't reject independence".

Decisive inconsistencies (e.g. distinct patient identifiers from the
same study found in the narratives) override the zone-based logic. When
``L_match > lmatch_med`` the matching evidence still indicates
non-independence (so the pair is surfaced as ``Otherwise_related``);
otherwise the pair falls back to ``Independent``.

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

    # --- Decisive inconsistencies override the zone-based logic ---
    if counts.decisive > 0:
        if L > thresholds.lmatch_med:
            return _otherwise_related(
                pair_id=features.pair_id,
                explanation=(
                    f"Decisive inconsistency detected "
                    f"(n={counts.decisive}); matching evidence still "
                    f"indicates non-independence (L_match={L:.2f} > "
                    f"{thresholds.lmatch_med})."
                ),
            )
        return _independent(
            pair_id=features.pair_id,
            explanation=(
                f"Decisive inconsistency detected "
                f"(n={counts.decisive}); L_match={L:.2f} ≤ "
                f"{thresholds.lmatch_med} so independence cannot be rejected."
            ),
        )

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
                f"(strong={counts.strong}, moderate={counts.moderate})."
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
                f"(strong={counts.strong}, moderate={counts.moderate})."
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
        return _independent(
            pair_id=features.pair_id,
            explanation=(
                f"Weak matching evidence (L_match={L:.2f}) with "
                f"w_tot={w_tot:.1f} >= {thresholds.w_tot_weak_zone:.1f} "
                f"(strong={counts.strong}, moderate={counts.moderate}); "
                f"independence cannot be rejected."
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

    w_tot = w_minor × minor + w_moderate × moderate + w_strong × strong
    """
    return (
        thresholds.w_minor * counts.minor
        + thresholds.w_moderate * counts.moderate
        + thresholds.w_strong * counts.strong
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
