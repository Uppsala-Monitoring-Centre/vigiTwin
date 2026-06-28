"""Decision logic for the duplicate adjudication pipeline.

Adjudicates a candidate report pair by jointly evaluating the rarity-based
matching evidence (``L_match``) and the inconsistency signals.

Two layered internal labels are produced:

* an ``IndependenceAssessment`` capturing whether the matching evidence
  is strong enough to reject the null hypothesis that the two reports
  are independent draws from the comparison population
  (``NON_INDEPENDENT`` / ``INDEPENDENT``);
* when independence has been rejected, a ``DuplicateRelationship``
  sub-label that uses the inconsistency signals to distinguish a
  likely-duplicate case from one that is linked by something other
  than being the same individual case (``DUPLICATES`` /
  ``OTHERWISE_RELATED`` / ``ABSTAIN``).

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

Abstain (same-source ambiguity)
--------------------------------
The ``Abstain`` outcome is reserved exclusively for epistemically
underdetermined same-source scenarios where:

* non-independence is established (same source identifier confirmed), but
* the available structured and narrative evidence is insufficient to
  safely resolve whether the two reports describe the same individual.

``Abstain`` is *not* generic uncertainty — it is a specific claim that
the latent information required to decide (e.g. the full publication
text, patient-level identifiers) is not present in the structured data.

It is produced only by the shared-source override path and only for the
following verdicts:

* ``SAME_SOURCE_SINGLE_PATIENT`` — when ``w_tot ≥ w_tot_med_zone``
  (substantial inconsistencies that are more plausibly explained by
  extraction differences, reporting variation, or partial information
  than by distinct patients).
* ``SAME_SOURCE_SAME_CASE`` — same rule as SINGLE_PATIENT.
* ``SAME_SOURCE_UNCOMPARABLE_CASE`` — when the pair is neither
  convincingly the same individual (which requires compelling
  patient-specific evidence ``L_patient > lmatch_med`` with essentially
  zero inconsistencies, ``w_tot < w_tot_weak_zone``) nor convincingly a
  different individual (``w_tot ≥ w_tot_weak_zone``). See
  :func:`_l_patient` for the patient-specific log-evidence definition.
  Note the lower Otherwise_related gate (``w_tot_weak_zone``) vs the
  SINGLE_PATIENT / SAME_CASE gate (``w_tot_med_zone``): literature /
  summary-case demographics are more deliberate than ad-hoc reports,
  so smaller inconsistencies carry more weight as evidence of
  different patients.
* ``MULTI_PATIENT_CONTEXT_NO_LINK`` with ``L_match > lmatch_weak`` —
  same three-way rule as UNCOMPARABLE_CASE (delegated to
  :func:`_multi_patient_three_way`). The two verdicts share the
  drug-set / AE-set inflation concern even though only UNCOMPARABLE_CASE
  carries positive shared-source evidence. Below ``lmatch_weak`` the
  verdict is treated as advisory and the pair falls through to
  ``Independent``.

See: docs/duplicate_adjudication_spec.md § Decision logic
"""

from __future__ import annotations

import math

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
# shared-source override path unconditionally. Advisory verdicts
# (NO_DECISIVE_SIGNAL, NOT_ASSESSED) fall through to the standard L_match
# zone logic. MULTI_PATIENT_CONTEXT_NO_LINK is handled separately by a
# verdict-aware shim in ``classify`` that routes it through the
# multi-patient three-way rule (the same rule used for
# UNCOMPARABLE_CASE) only when ``L_match > lmatch_weak``; below that
# threshold it still falls through to the standard logic.
_SHARED_SOURCE_VERDICTS = frozenset({
    SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT,
    SourceCaseVerdict.SAME_SOURCE_SAME_CASE,
    SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE,
    SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
})


# Feature contributions to drop when computing the patient-specific
# log-evidence (``L_patient``) used by the SAME_SOURCE_UNCOMPARABLE_CASE
# Duplicates branch. Drugs and adverse events overlap by construction
# within a multi-patient publication / shared study context, so they are
# weak identifiers of patient identity in that setting.
_NON_PATIENT_SPECIFIC_FEATURES = frozenset({"drug_set", "ae_set"})


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

    # --- Multi-patient context (no shared-source identifier) ---
    # MULTI_PATIENT_CONTEXT_NO_LINK shares the drug-set / AE-set inflation
    # concern with UNCOMPARABLE_CASE: both reports come from a multi-patient
    # context (literature series, shared-study cohort, …) where drugs and
    # AEs overlap by construction. Above lmatch_weak we route through the
    # same three-way rule that UNCOMPARABLE_CASE uses, so a single moderate
    # is enough to move the pair out of Duplicates. Below lmatch_weak the
    # verdict is still purely advisory and falls through to the standard
    # weak-threshold branch (→ Independent).
    if (
        asm is not None
        and asm.verdict == SourceCaseVerdict.MULTI_PATIENT_CONTEXT_NO_LINK
        and L > thresholds.lmatch_weak
    ):
        return _multi_patient_three_way(
            features, asm.verdict,
            _format_shared_identifier(asm), w_tot, thresholds,
        )

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

    Sub-label mapping by verdict:

    ``SAME_SOURCE_DIFFERENT_CASE``
        Pinned to ``OTHERWISE_RELATED`` regardless of ``w_tot``. A
        decisive inconsistency is also emitted by the feature-generation
        layer (informational; does not affect this pin).

    ``SAME_SOURCE_UNCOMPARABLE_CASE``
        Delegates to :func:`_multi_patient_three_way`, which decides
        the sub-label from the patient-specific log-evidence
        ``L_patient`` and ``w_tot``. The same helper is also used by
        the multi-patient shim in :func:`classify` for
        ``MULTI_PATIENT_CONTEXT_NO_LINK`` pairs above ``lmatch_weak``,
        which share the same drug-set / AE-set inflation concern.

    ``SAME_SOURCE_SINGLE_PATIENT`` / ``SAME_SOURCE_SAME_CASE``
        Default to ``DUPLICATES``, but are raised to ``ABSTAIN`` when
        ``w_tot ≥ w_tot_med_zone``. The rationale: for a genuinely
        single-patient source (or one where the same case ID is
        confirmed), substantial inconsistencies are more plausibly
        extraction artefacts, reporting differences, or partial
        information than evidence of distinct individuals — so the
        pair is epistemically underdetermined rather than confidently
        Otherwise_related. The threshold is shared with the medium
        L_match zone (default 3.0): a single strong inconsistency, or
        equivalent accumulated severity, is required to leave the
        Duplicates branch. A single moderate or a handful of minors is
        tolerated. Decisive inconsistencies demote via their large
        weight (``w_decisive = 100``).
    """
    verdict = assessment.verdict
    ident_str = _format_shared_identifier(assessment)

    if verdict == SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE:
        return _otherwise_related(
            pair_id=features.pair_id,
            reason=IndependenceReason.SHARED_SOURCE,
            explanation=(
                f"Shared-source override ({verdict.value}{ident_str}); "
                f"sub-label pinned to Otherwise_related."
            ),
        )

    if verdict == SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE:
        return _multi_patient_three_way(
            features, verdict, ident_str, w_tot, thresholds,
        )

    # SAME_SOURCE_SINGLE_PATIENT or SAME_SOURCE_SAME_CASE
    if w_tot < thresholds.w_tot_med_zone:
        return _duplicates(
            pair_id=features.pair_id,
            reason=IndependenceReason.SHARED_SOURCE,
            explanation=(
                f"Shared-source override ({verdict.value}{ident_str}); "
                f"w_tot={w_tot:.1f} < {thresholds.w_tot_med_zone:.1f}."
            ),
        )

    # w_tot >= w_tot_med_zone: substantial inconsistencies, but for a
    # confirmed single-patient / same-case source these are more plausibly
    # extraction artefacts or reporting differences than evidence of distinct
    # individuals. Epistemically underdetermined → Abstain.
    return _abstain(
        pair_id=features.pair_id,
        reason=IndependenceReason.SHARED_SOURCE,
        explanation=(
            f"Shared-source override ({verdict.value}{ident_str}); "
            f"w_tot={w_tot:.1f} >= {thresholds.w_tot_med_zone:.1f} "
            f"(strong={features.inconsistency_counts.strong}, "
            f"moderate={features.inconsistency_counts.moderate}, "
            f"decisive={features.inconsistency_counts.decisive}); "
            f"abstained — inconsistencies may reflect extraction or "
            f"reporting differences rather than distinct individuals."
        ),
    )


def _multi_patient_three_way(
    features: PairFeatures,
    verdict: SourceCaseVerdict,
    ident_str: str,
    w_tot: float,
    thresholds: DecisionThresholds,
) -> ClassificationResult:
    """Three-way rule for multi-patient source contexts.

    Used by two verdicts that share the same inflation concern — drug-set
    and AE-set overlap inflating ``L_match`` while patient identity is
    underdetermined:

    * ``SAME_SOURCE_UNCOMPARABLE_CASE`` — routed here unconditionally
      from :func:`_shared_source_override`. Positive shared-source
      evidence (DOI / PMID / …) is present; case identifiers are not
      safely comparable.
    * ``MULTI_PATIENT_CONTEXT_NO_LINK`` — routed here from
      :func:`classify` only when ``L_match > lmatch_weak``. No shared
      source identifier exists, but both narratives indicate the report
      originates from a multi-patient context. Below ``lmatch_weak`` the
      verdict still falls through to the standard zone logic and
      resolves to ``Independent``.

    Branches:

    * ``w_tot >= w_tot_weak_zone`` → ``Otherwise_related``. Any
      moderate-or-stronger inconsistency (or three accumulated minors)
      clears the bar. A single minor is still tolerated.
    * ``L_patient > lmatch_med`` (with ``w_tot < w_tot_weak_zone``
      implied) → ``Duplicates``. Patient-specific log-evidence excludes
      ``drug_set`` / ``ae_set`` contributions, so the matching evidence
      must come from demographics + clinical-date coincidences.
    * Otherwise → ``Abstain``. Neither convincingly the same nor
      convincingly different individuals.
    """
    counts = features.inconsistency_counts

    if w_tot >= thresholds.w_tot_weak_zone:
        return _otherwise_related(
            pair_id=features.pair_id,
            reason=IndependenceReason.SHARED_SOURCE,
            explanation=(
                f"Shared-source override ({verdict.value}{ident_str}); "
                f"w_tot={w_tot:.1f} >= {thresholds.w_tot_weak_zone:.1f} "
                f"(minor={counts.minor}, moderate={counts.moderate}, "
                f"strong={counts.strong}, decisive={counts.decisive}); "
                f"inconsistencies indicate different individuals."
            ),
        )

    l_patient = _l_patient(features)
    if l_patient > thresholds.lmatch_med:
        return _duplicates(
            pair_id=features.pair_id,
            reason=IndependenceReason.SHARED_SOURCE,
            explanation=(
                f"Shared-source override ({verdict.value}{ident_str}); "
                f"L_patient={l_patient:.2f} > {thresholds.lmatch_med:.1f} "
                f"and w_tot={w_tot:.1f} < {thresholds.w_tot_weak_zone:.1f}; "
                f"compelling patient-specific evidence with minimal "
                f"inconsistency."
            ),
        )

    return _abstain(
        pair_id=features.pair_id,
        reason=IndependenceReason.SHARED_SOURCE,
        explanation=(
            f"Shared-source override ({verdict.value}{ident_str}); "
            f"L_patient={l_patient:.2f} (threshold "
            f"{thresholds.lmatch_med:.1f}), w_tot={w_tot:.1f} "
            f"(threshold {thresholds.w_tot_weak_zone:.1f}); "
            f"identity indeterminate — multi-patient source context "
            f"with insufficient patient-specific evidence."
        ),
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
# Patient-specific log-evidence
# ---------------------------------------------------------------------------


def _l_patient(features: PairFeatures) -> float:
    """Patient-specific log-evidence score for the UNCOMPARABLE_CASE branch.

    Defined as the sum of ``-log10(p_f)`` over the entries of
    ``features.probability.feature_contributions`` whose ``feature`` name
    is *not* in :data:`_NON_PATIENT_SPECIFIC_FEATURES` (i.e. excluding
    ``drug_set`` and ``ae_set``). Two deliberate departures from the
    standard ``L_match``:

    * Drug-set and AE-set contributions are dropped. Drugs and AEs
      overlap by construction in a multi-patient publication / shared
      study context, so they are weak identifiers of patient identity
      in that setting.
    * The anchor (``N_pairs``) penalty is **not** included. The anchor
      population was the comparison universe for the full ``L_match``
      (e.g. "all reports on drug X"); for ``UNCOMPARABLE_CASE`` the
      relevant comparison universe is the unknown small set of K
      patients within the shared publication, so the anchor penalty
      would correct for a multiple-testing scenario that does not
      apply. Dropping it yields a pure measure of patient-specific
      concordance strength on the log10 scale — the same scale as the
      per-feature ``(+X.X)`` chips already shown in the review HTML.

    Returns ``0.0`` when ``feature_contributions`` is empty (e.g. on
    offline / fixture paths that skip probability decomposition); the
    Duplicates branch then cannot fire and the pair falls into Abstain
    or Otherwise_related, which is the desired conservative default.
    """
    total = 0.0
    for fc in features.probability.feature_contributions:
        if fc.feature in _NON_PATIENT_SPECIFIC_FEATURES:
            continue
        if fc.p_f <= 0:
            continue
        total += -math.log10(fc.p_f)
    return total


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


def _abstain(
    pair_id: str,
    explanation: str,
    reason: IndependenceReason,
) -> ClassificationResult:
    """Build a Non-independent / Abstain result (user-facing label: Abstain).

    Represents an epistemically underdetermined same-source scenario:
    non-independence is established (same source identifier confirmed)
    but the available evidence is insufficient to safely resolve whether
    the two reports describe the same individual.
    """
    return ClassificationResult(
        pair_id=pair_id,
        independence_assessment=IndependenceAssessment.NON_INDEPENDENT,
        duplicate_relationship=DuplicateRelationship.ABSTAIN,
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
