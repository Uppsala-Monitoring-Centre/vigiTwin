"""Decision logic for classifying report pairs.

Jointly evaluates matching evidence (L_match) and inconsistencies to
produce a final Same / Different / Abstain classification with an
optional Related / Unrelated sub-classification.

Inconsistencies are combined into a single weighted score:

    w_tot = w_minor × minor + w_moderate × moderate + w_strong × strong

The decision space is divided into four zones by L_match thresholds.
Each zone has its own tolerance for w_tot:

    L_match > strong  →  w_tot < 4 allows Same
    L_match > med     →  w_tot < 3 allows Same
    L_match > weak    →  w_tot < 1 allows Same
    L_match ≤ weak    →  always Different / Unrelated

Decisive inconsistencies (e.g. distinct patient IDs from the same
study found in the narratives) override all matching evidence and
force a Different classification.

See: docs/same_individual_assessment_spec.md § Decision logic
"""

from __future__ import annotations

from llm_dup_dev.classifier.config import DecisionThresholds
from llm_dup_dev.models.classification import ClassificationResult
from llm_dup_dev.models.enums import PrimaryClassification, RelationshipClassification
from llm_dup_dev.models.pair_features import InconsistencyCounts, PairFeatures


def classify(
    features: PairFeatures,
    thresholds: DecisionThresholds | None = None,
) -> ClassificationResult:
    """Classify a report pair based on its computed features.

    Parameters
    ----------
    features : PairFeatures
        Pre-computed feature set from the feature generation pipeline.
    thresholds : DecisionThresholds, optional
        Decision boundaries. Uses defaults if not provided.

    Returns
    -------
    ClassificationResult
        Classification with primary label, optional relationship
        sub-label, and human-readable explanation.
    """
    if thresholds is None:
        thresholds = DecisionThresholds()

    counts = features.inconsistency_counts
    L = features.L_match
    w_tot = _weighted_inconsistency(counts, thresholds)

    # --- Decisive inconsistencies override everything ---
    if counts.decisive > 0:
        return _different(
            pair_id=features.pair_id,
            relationship=_relationship_for_decisive(L, thresholds),
            explanation=(
                f"Decisive inconsistency detected "
                f"(n={counts.decisive}); overrides L_match={L:.2f}."
            ),
        )

    # --- Strong zone: L_match > lmatch_strong ---
    if L > thresholds.lmatch_strong:
        if w_tot < thresholds.w_tot_strong_zone:
            return _same(
                pair_id=features.pair_id,
                explanation=(
                    f"Strong matching evidence (L_match={L:.2f} > "
                    f"{thresholds.lmatch_strong}); w_tot={w_tot:.1f} "
                    f"< {thresholds.w_tot_strong_zone:.1f}."
                ),
            )
        return _different(
            pair_id=features.pair_id,
            relationship=RelationshipClassification.RELATED,
            explanation=(
                f"Strong matching evidence (L_match={L:.2f}) but "
                f"w_tot={w_tot:.1f} >= {thresholds.w_tot_strong_zone:.1f} "
                f"(strong={counts.strong}, moderate={counts.moderate})."
            ),
        )

    # --- Medium zone: L_match > lmatch_med ---
    if L > thresholds.lmatch_med:
        if w_tot < thresholds.w_tot_med_zone:
            return _same(
                pair_id=features.pair_id,
                explanation=(
                    f"Moderate matching evidence (L_match={L:.2f} > "
                    f"{thresholds.lmatch_med}); w_tot={w_tot:.1f} "
                    f"< {thresholds.w_tot_med_zone:.1f}."
                ),
            )
        return _different(
            pair_id=features.pair_id,
            relationship=RelationshipClassification.RELATED,
            explanation=(
                f"Moderate matching evidence (L_match={L:.2f}) but "
                f"w_tot={w_tot:.1f} >= {thresholds.w_tot_med_zone:.1f} "
                f"(strong={counts.strong}, moderate={counts.moderate})."
            ),
        )

    # --- Weak zone: L_match > lmatch_weak ---
    if L > thresholds.lmatch_weak:
        if w_tot < thresholds.w_tot_weak_zone:
            return _same(
                pair_id=features.pair_id,
                explanation=(
                    f"Weak matching evidence (L_match={L:.2f} > "
                    f"{thresholds.lmatch_weak}); w_tot={w_tot:.1f} "
                    f"< {thresholds.w_tot_weak_zone:.1f}."
                ),
            )
        return _different(
            pair_id=features.pair_id,
            relationship=RelationshipClassification.UNRELATED,
            explanation=(
                f"Weak matching evidence (L_match={L:.2f}) with "
                f"w_tot={w_tot:.1f} >= {thresholds.w_tot_weak_zone:.1f} "
                f"(strong={counts.strong}, moderate={counts.moderate}); "
                f"classified as unrelated."
            ),
        )

    # --- Below weak threshold ---
    return _different(
        pair_id=features.pair_id,
        relationship=RelationshipClassification.UNRELATED,
        explanation=(
            f"Matching evidence below weak threshold "
            f"(L_match={L:.2f} ≤ {thresholds.lmatch_weak})."
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


def _relationship_for_decisive(
    L_match: float,
    thresholds: DecisionThresholds,
) -> RelationshipClassification:
    """Determine Related/Unrelated when a decisive inconsistency is present.

    If L_match > lmatch_med the reports share meaningful clinical
    context → Related.  Otherwise → Unrelated.
    """
    if L_match > thresholds.lmatch_med:
        return RelationshipClassification.RELATED
    return RelationshipClassification.UNRELATED


# ---------------------------------------------------------------------------
# Result constructors
# ---------------------------------------------------------------------------


def _same(pair_id: str, explanation: str) -> ClassificationResult:
    """Build a Same individual result."""
    return ClassificationResult(
        pair_id=pair_id,
        primary=PrimaryClassification.SAME_INDIVIDUAL,
        relationship=None,
        explanation=explanation,
    )


def _different(
    pair_id: str,
    relationship: RelationshipClassification,
    explanation: str,
) -> ClassificationResult:
    """Build a Different individuals result."""
    return ClassificationResult(
        pair_id=pair_id,
        primary=PrimaryClassification.DIFFERENT_INDIVIDUALS,
        relationship=relationship,
        explanation=explanation,
    )
