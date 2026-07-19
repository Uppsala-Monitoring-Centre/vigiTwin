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

    L_match > compelling    →  w_tot < 4 ⇒ Non-independent / Duplicates
                                else      ⇒ Non-independent / Otherwise_related
    L_match > intermediate  →  w_tot < 3 ⇒ Non-independent / Duplicates
                                else      ⇒ Non-independent / Otherwise_related
    L_match > limited       →  w_tot < 1 ⇒ Non-independent / Duplicates
                                else      ⇒ Non-independent / Otherwise_related
    L_match ≤ limited       →  always Independent

All three non-trivial zones share the same structure: matching evidence
above ``lmatch_limited`` is treated as sufficient to reject independence,
and ``w_tot`` only decides the duplicate-vs-otherwise-related sub-label.
Only when ``L_match`` falls at or below ``lmatch_limited`` is the matching
evidence considered too weak to reject independence at all.

Decisive inconsistencies (e.g. distinct patient identifiers from the
same study found in the narratives) participate through the same
weighted score with a large weight (``w_decisive``, default 100) that
always exceeds every zone threshold. As a result, any decisive count
forces the failing-zone branch, which combined with the zone logic
yields ``Otherwise_related`` whenever ``L_match > lmatch_limited`` and
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

* ``SAME_SOURCE_SINGLE_PATIENT`` — when ``w_tot ≥ w_tot_compelling_zone``
  (substantial inconsistencies that are more plausibly explained by
  extraction differences, reporting variation, or partial information
  than by distinct patients).
* ``SAME_SOURCE_SAME_CASE`` — same rule as SINGLE_PATIENT.

The multi-patient verdicts ``SAME_SOURCE_UNCOMPARABLE_CASE`` and
``MULTI_PATIENT_CONTEXT_NO_LINK`` (the latter gated on
``L_match > lmatch_limited``) do *not* abstain. They are routed to the
two-way rule in :func:`_multi_patient_three_way`, which resolves to
``Duplicates`` when the patient-specific evidence is compelling
(``L_patient > lpatient_compelling`` with ``w_tot < w_tot_limited_zone``)
and to ``Otherwise_related`` otherwise. ``L_patient`` is carried on
``PairFeatures``; see
:func:`llm_dup_dev.feature_generation.probability.compute_l_patient`
for its definition.

See: docs/duplicate_adjudication_spec.md § Independence assessment and § Relationship assessment
"""

from __future__ import annotations

from llm_dup_dev.classifier.config import DecisionThresholds
from llm_dup_dev.models.classification import (
    ClassificationResult,
    InconsistencyPenalty,
    PenaltyTerm,
)
from llm_dup_dev.models.enums import (
    DuplicateRelationship,
    IndependenceAssessment,
    IndependenceReason,
    InconsistencySeverity,
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
# multi-patient two-way rule (the same rule used for
# UNCOMPARABLE_CASE) only when ``L_match > lmatch_limited``; below that
# threshold it still falls through to the standard logic.
_SHARED_SOURCE_VERDICTS = frozenset({
    SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT,
    SourceCaseVerdict.SAME_SOURCE_SAME_CASE,
    SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE,
    SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
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
    penalty = _inconsistency_penalty(counts, thresholds)
    w_tot = penalty.w_tot

    # --- Shared-source override ---
    # A reliable shared source identifier is itself evidence against
    # independence, regardless of L_match. Reject independence directly
    # and pick the sub-label from the verdict (plus conservative fallback
    # when non-source-case signals disagree).
    asm = features.source_case_assessment
    if asm is not None and asm.verdict in _SHARED_SOURCE_VERDICTS:
        return _shared_source_override(features, asm, thresholds, penalty)

    # --- Multi-patient context (no shared-source identifier) ---
    # MULTI_PATIENT_CONTEXT_NO_LINK shares the drug-set / AE-set inflation
    # concern with UNCOMPARABLE_CASE: both reports come from a multi-patient
    # context (literature series, shared-study cohort, …) where drugs and
    # AEs overlap by construction. Above lmatch_limited we route through the
    # same two-way rule that UNCOMPARABLE_CASE uses, so a single moderate
    # is enough to move the pair out of Duplicates. Below lmatch_limited the
    # verdict is still purely advisory and falls through to the standard
    # limited-threshold branch (→ Independent).
    if (
        asm is not None
        and asm.verdict == SourceCaseVerdict.MULTI_PATIENT_CONTEXT_NO_LINK
        and L > thresholds.lmatch_limited
    ):
        return _multi_patient_three_way(
            features, asm.verdict,
            _format_shared_identifier(asm), penalty, thresholds,
        )

    # --- Compelling zone: L_match > lmatch_compelling ---
    if L > thresholds.lmatch_compelling:
        if w_tot < thresholds.w_tot_compelling_zone:
            return _duplicates(
                pair_id=features.pair_id,
                reason=IndependenceReason.RARITY_COMPELLING,
                penalty=penalty.compared_to(thresholds.w_tot_compelling_zone),
                explanation=(
                    f"Compelling matching evidence (L_match={L:.2f} > "
                    f"{thresholds.lmatch_compelling}); w_tot={w_tot:.1f} "
                    f"< {thresholds.w_tot_compelling_zone:.1f}."
                ),
            )
        return _otherwise_related(
            pair_id=features.pair_id,
            reason=IndependenceReason.RARITY_COMPELLING,
            penalty=penalty.compared_to(thresholds.w_tot_compelling_zone),
            explanation=(
                f"Compelling matching evidence (L_match={L:.2f}) but "
                f"w_tot={w_tot:.1f} >= {thresholds.w_tot_compelling_zone:.1f} "
                f"(strong={counts.strong}, moderate={counts.moderate}, "
                f"decisive={counts.decisive})."
            ),
        )

    # --- Intermediate zone: L_match > lmatch_intermediate ---
    if L > thresholds.lmatch_intermediate:
        if w_tot < thresholds.w_tot_intermediate_zone:
            return _duplicates(
                pair_id=features.pair_id,
                reason=IndependenceReason.RARITY_INTERMEDIATE,
                penalty=penalty.compared_to(thresholds.w_tot_intermediate_zone),
                explanation=(
                    f"Intermediate matching evidence (L_match={L:.2f} > "
                    f"{thresholds.lmatch_intermediate}); w_tot={w_tot:.1f} "
                    f"< {thresholds.w_tot_intermediate_zone:.1f}."
                ),
            )
        return _otherwise_related(
            pair_id=features.pair_id,
            reason=IndependenceReason.RARITY_INTERMEDIATE,
            penalty=penalty.compared_to(thresholds.w_tot_intermediate_zone),
            explanation=(
                f"Intermediate matching evidence (L_match={L:.2f}) but "
                f"w_tot={w_tot:.1f} >= {thresholds.w_tot_intermediate_zone:.1f} "
                f"(strong={counts.strong}, moderate={counts.moderate}, "
                f"decisive={counts.decisive})."
            ),
        )

    # --- Limited zone: L_match > lmatch_limited ---
    if L > thresholds.lmatch_limited:
        if w_tot < thresholds.w_tot_limited_zone:
            return _duplicates(
                pair_id=features.pair_id,
                reason=IndependenceReason.RARITY_LIMITED,
                penalty=penalty.compared_to(thresholds.w_tot_limited_zone),
                explanation=(
                    f"Limited matching evidence (L_match={L:.2f} > "
                    f"{thresholds.lmatch_limited}); w_tot={w_tot:.1f} "
                    f"< {thresholds.w_tot_limited_zone:.1f}."
                ),
            )
        return _otherwise_related(
            pair_id=features.pair_id,
            reason=IndependenceReason.RARITY_LIMITED,
            penalty=penalty.compared_to(thresholds.w_tot_limited_zone),
            explanation=(
                f"Limited matching evidence (L_match={L:.2f}) but "
                f"w_tot={w_tot:.1f} >= {thresholds.w_tot_limited_zone:.1f} "
                f"(strong={counts.strong}, moderate={counts.moderate}, "
                f"decisive={counts.decisive})."
            ),
        )

    # --- Below limited threshold ---
    # L_match decides here; w_tot is never consulted, so the penalty is
    # recorded without a comparison.
    return _independent(
        pair_id=features.pair_id,
        reason=IndependenceReason.RARITY_INSUFFICIENT,
        penalty=penalty,
        explanation=(
            f"Matching evidence below limited threshold "
            f"(L_match={L:.2f} ≤ {thresholds.lmatch_limited}); "
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
    penalty: InconsistencyPenalty,
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
        ``MULTI_PATIENT_CONTEXT_NO_LINK`` pairs above ``lmatch_limited``,
        which share the same drug-set / AE-set inflation concern.

    ``SAME_SOURCE_SINGLE_PATIENT`` / ``SAME_SOURCE_SAME_CASE``
        Default to ``DUPLICATES``, but are raised to ``ABSTAIN`` when
        ``w_tot ≥ w_tot_compelling_zone``. The rationale: for a genuinely
        single-patient source (or one where the same case ID is
        confirmed), substantial inconsistencies are more plausibly
        extraction artefacts, reporting differences, or partial
        information than evidence of distinct individuals — so the
        pair is epistemically underdetermined rather than confidently
        Otherwise_related. The threshold is shared with the compelling
        L_match zone (default 4.0): a confirmed shared identifier with
        a single-patient / same-case claim is at least as strong evidence
        of identity as compelling rarity-based matching, so it deserves
        the same inconsistency budget. A single strong inconsistency is
        tolerated; reaching the threshold requires one strong + one
        moderate, two strongs, or equivalent accumulated severity.
        Decisive inconsistencies demote via their large weight
        (``w_decisive = 100``).
    """
    verdict = assessment.verdict
    ident_str = _format_shared_identifier(assessment)
    w_tot = penalty.w_tot

    if verdict == SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE:
        # Pinned regardless of w_tot, so the penalty carries no comparison.
        return _otherwise_related(
            pair_id=features.pair_id,
            reason=IndependenceReason.SHARED_SOURCE,
            penalty=penalty,
            explanation=(
                f"Shared-source override ({verdict.value}{ident_str}); "
                f"sub-label pinned to Otherwise_related."
            ),
        )

    if verdict == SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE:
        return _multi_patient_three_way(
            features, verdict, ident_str, penalty, thresholds,
        )

    # SAME_SOURCE_SINGLE_PATIENT or SAME_SOURCE_SAME_CASE
    if w_tot < thresholds.w_tot_compelling_zone:
        return _duplicates(
            pair_id=features.pair_id,
            reason=IndependenceReason.SHARED_SOURCE,
            penalty=penalty.compared_to(thresholds.w_tot_compelling_zone),
            explanation=(
                f"Shared-source override ({verdict.value}{ident_str}); "
                f"w_tot={w_tot:.1f} < {thresholds.w_tot_compelling_zone:.1f}."
            ),
        )

    # w_tot >= w_tot_compelling_zone: substantial inconsistencies, but for a
    # confirmed single-patient / same-case source these are more plausibly
    # extraction artefacts or reporting differences than evidence of distinct
    # individuals. Epistemically underdetermined → Abstain.
    return _abstain(
        pair_id=features.pair_id,
        reason=IndependenceReason.SHARED_SOURCE,
        penalty=penalty.compared_to(thresholds.w_tot_compelling_zone),
        explanation=(
            f"Shared-source override ({verdict.value}{ident_str}); "
            f"w_tot={w_tot:.1f} >= {thresholds.w_tot_compelling_zone:.1f} "
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
    penalty: InconsistencyPenalty,
    thresholds: DecisionThresholds,
) -> ClassificationResult:
    """Two-way rule for multi-patient source contexts.

    Used by two verdicts that share the same inflation concern — drug-set
    and AE-set overlap inflating ``L_match`` while patient identity is
    underdetermined:

    * ``SAME_SOURCE_UNCOMPARABLE_CASE`` — routed here unconditionally
      from :func:`_shared_source_override`. Positive shared-source
      evidence (DOI / PMID / …) is present; case identifiers are not
      safely comparable.
    * ``MULTI_PATIENT_CONTEXT_NO_LINK`` — routed here from
      :func:`classify` only when ``L_match > lmatch_limited``. No shared
      source identifier exists, but both narratives indicate the report
      originates from a multi-patient context. Below ``lmatch_limited`` the
      verdict still falls through to the standard zone logic and
      resolves to ``Independent``.

    Branches:

    * ``w_tot >= w_tot_limited_zone`` → ``Otherwise_related``. Any
      moderate-or-stronger inconsistency (or three accumulated minors)
      clears the bar. A single minor is still tolerated.
    * ``L_patient > lpatient_compelling`` (with ``w_tot < w_tot_limited_zone``
      implied) → ``Duplicates``. Patient-specific log-evidence excludes
      ``drug_set`` / ``ae_set`` contributions, so the matching evidence
      must come from demographics + clinical-date coincidences.
    * Otherwise → ``Otherwise_related``. Patient-specific evidence is not
      compelling enough to call ``Duplicates``, but non-independence is
      established, so the pair defaults to ``Otherwise_related`` rather
      than abstaining. The rule is therefore effectively two-way:
      ``Duplicates`` only when ``L_patient`` clears the bar, else
      ``Otherwise_related``.
    """
    counts = features.inconsistency_counts
    w_tot = penalty.w_tot

    if w_tot >= thresholds.w_tot_limited_zone:
        return _otherwise_related(
            pair_id=features.pair_id,
            reason=IndependenceReason.SHARED_SOURCE,
            penalty=penalty.compared_to(thresholds.w_tot_limited_zone),
            explanation=(
                f"Shared-source override ({verdict.value}{ident_str}); "
                f"w_tot={w_tot:.1f} >= {thresholds.w_tot_limited_zone:.1f} "
                f"(minor={counts.minor}, moderate={counts.moderate}, "
                f"strong={counts.strong}, decisive={counts.decisive}); "
                f"inconsistencies indicate different individuals."
            ),
        )

    l_patient = features.L_patient
    if l_patient > thresholds.lpatient_compelling:
        return _duplicates(
            pair_id=features.pair_id,
            reason=IndependenceReason.SHARED_SOURCE,
            penalty=penalty.compared_to(thresholds.w_tot_limited_zone),
            explanation=(
                f"Shared-source override ({verdict.value}{ident_str}); "
                f"L_patient={l_patient:.2f} > {thresholds.lpatient_compelling:.1f} "
                f"and w_tot={w_tot:.1f} < {thresholds.w_tot_limited_zone:.1f}; "
                f"compelling patient-specific evidence with minimal "
                f"inconsistency."
            ),
        )

    return _otherwise_related(
        pair_id=features.pair_id,
        reason=IndependenceReason.SHARED_SOURCE,
        penalty=penalty.compared_to(thresholds.w_tot_limited_zone),
        explanation=(
            f"Shared-source override ({verdict.value}{ident_str}); "
            f"L_patient={l_patient:.2f} (threshold "
            f"{thresholds.lpatient_compelling:.1f}), w_tot={w_tot:.1f} "
            f"(threshold {thresholds.w_tot_limited_zone:.1f}); "
            f"patient-specific evidence insufficient to call Duplicates "
            f"in a multi-patient source context — defaulting to "
            f"Otherwise_related."
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
# Weighted inconsistency penalty
# ---------------------------------------------------------------------------


def _inconsistency_penalty(
    counts: InconsistencyCounts,
    thresholds: DecisionThresholds,
) -> InconsistencyPenalty:
    """Compute the weighted inconsistency score and its breakdown.

    w_tot = w_minor × minor + w_moderate × moderate
          + w_strong × strong + w_decisive × decisive

    Records the weights actually applied alongside the total, so the result is
    self-describing: no consumer has to know the run's ``DecisionThresholds`` to
    reproduce the arithmetic. Only the severities present contribute a term.

    The returned penalty carries no ``threshold`` — each branch attaches the bar
    it tested against via :meth:`InconsistencyPenalty.compared_to`.
    """
    weights = (
        (InconsistencySeverity.MINOR, counts.minor, thresholds.w_minor),
        (InconsistencySeverity.MODERATE, counts.moderate, thresholds.w_moderate),
        (InconsistencySeverity.STRONG, counts.strong, thresholds.w_strong),
        (InconsistencySeverity.DECISIVE, counts.decisive, thresholds.w_decisive),
    )
    terms = [
        PenaltyTerm(severity=severity, count=count, weight=weight)
        for severity, count, weight in weights
        if count > 0
    ]
    return InconsistencyPenalty(
        w_tot=sum(count * weight for _, count, weight in weights),
        terms=terms,
    )


# ---------------------------------------------------------------------------
# Result constructors
# ---------------------------------------------------------------------------


def _duplicates(
    pair_id: str,
    explanation: str,
    reason: IndependenceReason,
    penalty: InconsistencyPenalty | None = None,
) -> ClassificationResult:
    """Build a Non-independent / Duplicates result."""
    return ClassificationResult(
        pair_id=pair_id,
        independence_assessment=IndependenceAssessment.NON_INDEPENDENT,
        duplicate_relationship=DuplicateRelationship.DUPLICATES,
        independence_reason=reason,
        explanation=explanation,
        penalty=penalty,
    )


def _otherwise_related(
    pair_id: str,
    explanation: str,
    reason: IndependenceReason,
    penalty: InconsistencyPenalty | None = None,
) -> ClassificationResult:
    """Build a Non-independent / Otherwise_related result."""
    return ClassificationResult(
        pair_id=pair_id,
        independence_assessment=IndependenceAssessment.NON_INDEPENDENT,
        duplicate_relationship=DuplicateRelationship.OTHERWISE_RELATED,
        independence_reason=reason,
        explanation=explanation,
        penalty=penalty,
    )


def _abstain(
    pair_id: str,
    explanation: str,
    reason: IndependenceReason,
    penalty: InconsistencyPenalty | None = None,
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
        penalty=penalty,
    )


def _independent(
    pair_id: str,
    explanation: str,
    reason: IndependenceReason,
    penalty: InconsistencyPenalty | None = None,
) -> ClassificationResult:
    """Build an Independent result (user-facing label: Unrelated)."""
    return ClassificationResult(
        pair_id=pair_id,
        independence_assessment=IndependenceAssessment.INDEPENDENT,
        duplicate_relationship=None,
        independence_reason=reason,
        explanation=explanation,
        penalty=penalty,
    )
