"""Decision logic for classifying report pairs.

Jointly evaluates matching evidence (L_match), inconsistencies, and
narrative consistency to produce a final Same / Different / Abstain
classification with an optional Related / Unrelated sub-classification.

The decision space is divided into four zones by L_match thresholds.
Each zone has its own tolerance for inconsistencies:

    L_match > strong  →  tolerates limited inconsistencies
    L_match > med     →  requires zero strong inconsistencies
    L_match > weak    →  requires zero inconsistencies of any kind
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
        if _strong_zone_allows_same(counts):
            return _same(
                pair_id=features.pair_id,
                explanation=(
                    f"Strong matching evidence (L_match={L:.2f} > "
                    f"{thresholds.lmatch_strong}); inconsistencies "
                    f"within tolerance (strong={counts.strong}, "
                    f"moderate={counts.moderate})."
                ),
            )
        return _different(
            pair_id=features.pair_id,
            relationship=RelationshipClassification.RELATED,
            explanation=(
                f"Strong matching evidence (L_match={L:.2f}) but "
                f"inconsistencies exceed tolerance for strong zone "
                f"(strong={counts.strong}, moderate={counts.moderate})."
            ),
        )

    # --- Medium zone: L_match > lmatch_med ---
    if L > thresholds.lmatch_med:
        if _medium_zone_allows_same(counts):
            return _same(
                pair_id=features.pair_id,
                explanation=(
                    f"Moderate matching evidence (L_match={L:.2f} > "
                    f"{thresholds.lmatch_med}); no strong "
                    f"inconsistencies, moderate={counts.moderate}."
                ),
            )
        return _different(
            pair_id=features.pair_id,
            relationship=RelationshipClassification.RELATED,
            explanation=(
                f"Moderate matching evidence (L_match={L:.2f}) but "
                f"inconsistencies exceed tolerance for medium zone "
                f"(strong={counts.strong}, moderate={counts.moderate})."
            ),
        )

    # --- Weak zone: L_match > lmatch_weak ---
    if L > thresholds.lmatch_weak:
        if _weak_zone_allows_same(counts):
            return _same(
                pair_id=features.pair_id,
                explanation=(
                    f"Weak matching evidence (L_match={L:.2f} > "
                    f"{thresholds.lmatch_weak}); no inconsistencies."
                ),
            )
        return _different(
            pair_id=features.pair_id,
            relationship=RelationshipClassification.UNRELATED,
            explanation=(
                f"Weak matching evidence (L_match={L:.2f}) with "
                f"inconsistencies present (strong={counts.strong}, "
                f"moderate={counts.moderate}); classified as unrelated."
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
# Zone-specific inconsistency tolerance rules
# ---------------------------------------------------------------------------


def _strong_zone_allows_same(counts: InconsistencyCounts) -> bool:
    """Strong zone: tolerates limited inconsistencies.

    Requires:
        strong < 2  AND  strong + 2 * moderate < 4
    """
    return (
        counts.strong < 2
        and counts.strong + 2 * counts.moderate < 4
    )


def _medium_zone_allows_same(counts: InconsistencyCounts) -> bool:
    """Medium zone: requires zero strong inconsistencies.

    Requires:
        strong == 0  AND  moderate < 3
    """
    return counts.strong == 0 and counts.moderate < 3


def _weak_zone_allows_same(counts: InconsistencyCounts) -> bool:
    """Weak zone: requires zero inconsistencies of any kind.

    Requires:
        strong + moderate == 0
    """
    return counts.strong + counts.moderate == 0


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
