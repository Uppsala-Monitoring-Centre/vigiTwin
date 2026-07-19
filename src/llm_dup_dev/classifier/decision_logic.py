"""Decision logic for the duplicate adjudication pipeline.

Adjudicates a candidate report pair by answering two sequential
questions, mirroring the structure of the method specification:

1. **Independence assessment** (:func:`assess_independence`) — did the
   two reports arise independently? Independence is rejected either on
   direct evidence of shared provenance (a shared-source verdict from
   the source-case comparison) or when the rarity-based matching
   evidence is strong enough (``L_match > lmatch_limited``) to reject
   the null hypothesis that the reports are independent draws from the
   comparison population. Output: ``NON_INDEPENDENT`` / ``INDEPENDENT``
   plus an :class:`IndependenceReason` audit value.
2. **Relationship assessment** (:func:`assess_relationship`) — for
   non-independent pairs only: do the reports describe the same
   underlying case? Output: ``SAME_CASE`` / ``DIFFERENT_CASE`` /
   ``ABSTAIN``.

:func:`classify` composes the two and maps them onto a
:class:`ClassificationResult`; ``ClassificationResult.final_label``
derives the user-facing labels (``Duplicates`` / ``Otherwise_related``
/ ``Unrelated`` / ``Abstain``) at the very end.

Relationship assessment
-----------------------
Inconsistencies are combined into a single weighted score::

    w_tot = w_minor × minor + w_moderate × moderate
          + w_strong × strong + w_decisive × decisive

which is interpreted against a priority-ordered set of evidence zones.
Source-context zones (derived from the source-case verdict) take
priority; pairs without a zone-selecting verdict fall through to the
rarity zones defined by ``L_match``::

    1. Identity    (SAME_SOURCE_SINGLE_PATIENT / SAME_SOURCE_SAME_CASE):
                     w_tot < 4 ⇒ Same_case, else Abstain
    2. Distinct    (SAME_SOURCE_DIFFERENT_CASE):
                     Different_case, pinned regardless of w_tot
    3. Unresolved  (SAME_SOURCE_UNCOMPARABLE_CASE,
                    MULTI_PATIENT_CONTEXT_NO_LINK):
                     w_tot ≥ 1 ⇒ Different_case;
                     else L_patient > 2 ⇒ Same_case, else Different_case
    4. Rarity compelling   (L_match > 3): w_tot < 4 ⇒ Same_case else Different_case
    5. Rarity intermediate (L_match > 2): w_tot < 3 ⇒ Same_case else Different_case
    6. Rarity limited      (L_match > 1): w_tot < 1 ⇒ Same_case else Different_case

(Defaults shown; all bounds come from :class:`DecisionThresholds`.)

The Identity and Distinct zones reflect direct source-context evidence
about case identity. The Unresolved zone covers multi-patient source
contexts (literature case series, shared-study cohorts, …) where drug
and AE overlap is expected by construction and would inflate
``L_match``; the patient-specific log-evidence ``L_patient`` — which
excludes ``drug_set`` / ``ae_set`` contributions — decides instead. A
``MULTI_PATIENT_CONTEXT_NO_LINK`` verdict carries no shared-source
evidence, so it only reaches the Unresolved zone when independence was
rejected on rarity; below ``lmatch_limited`` it is advisory and the
pair remains Independent.

Decisive inconsistencies (e.g. distinct patient identifiers from the
same study found in the narratives) participate through the weighted
score with a large weight (``w_decisive``, default 100) that always
exceeds every zone tolerance, so any decisive count forces the
failing branch of whichever zone applies — with no separate override.

Abstain (same-source ambiguity)
-------------------------------
``Abstain`` is reserved exclusively for epistemically underdetermined
same-source scenarios: non-independence is established (same source
identifier confirmed) and the source context claims a single patient or
same case, but substantial inconsistencies (``w_tot ≥
w_tot_compelling_zone``) are more plausibly explained by extraction
differences, reporting variation, or partial information than by
distinct patients. It is *not* generic uncertainty — it is a specific
claim that the latent information required to decide (e.g. the full
publication text, patient-level identifiers) is not present in the
structured data. Only the Identity zone abstains; the Unresolved zone
resolves to ``Different_case`` when patient-specific evidence is not
compelling.

See: docs/duplicate_adjudication_spec.md § Independence assessment and § Relationship assessment
"""

from __future__ import annotations

from typing import NamedTuple

from llm_dup_dev.classifier.config import DecisionThresholds
from llm_dup_dev.models.classification import (
    ClassificationResult,
    InconsistencyPenalty,
    PenaltyTerm,
)
from llm_dup_dev.models.enums import (
    IndependenceAssessment,
    IndependenceReason,
    InconsistencySeverity,
    RelationshipAssessment,
)
from llm_dup_dev.models.narrative_source_case import (
    SourceCaseAssessment,
    SourceCaseVerdict,
)
from llm_dup_dev.models.pair_features import InconsistencyCounts, PairFeatures


# Verdicts that prove a shared source exists: direct evidence of shared
# provenance, rejecting independence regardless of L_match. Advisory
# verdicts (NO_DECISIVE_SIGNAL, NOT_ASSESSED) and
# MULTI_PATIENT_CONTEXT_NO_LINK (a multi-patient context claim without a
# shared identifier) carry no such evidence; for them independence is
# assessed on the rarity-based matching evidence alone.
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

    Composes the two assessments: independence first, then — only when
    independence has been rejected — the relationship assessment.

    Parameters
    ----------
    features : PairFeatures
        Pre-computed feature set from the feature generation pipeline.
    thresholds : DecisionThresholds, optional
        Decision boundaries. Uses defaults if not provided.

    Returns
    -------
    ClassificationResult
        Classification with the independence assessment, the
        relationship assessment (non-independent pairs only), and a
        human-readable explanation.
    """
    if thresholds is None:
        thresholds = DecisionThresholds()

    penalty = _inconsistency_penalty(features.inconsistency_counts, thresholds)
    independence, reason = assess_independence(features, thresholds)

    if independence == IndependenceAssessment.INDEPENDENT:
        # L_match decides here; w_tot is never consulted, so the penalty
        # is recorded without a comparison.
        return ClassificationResult(
            pair_id=features.pair_id,
            independence_assessment=independence,
            relationship_assessment=None,
            independence_reason=reason,
            explanation=(
                f"Matching evidence below limited threshold "
                f"(L_match={features.L_match:.2f} ≤ {thresholds.lmatch_limited}); "
                f"independence cannot be rejected."
            ),
            penalty=penalty,
        )

    decision = assess_relationship(features, thresholds, penalty)
    return ClassificationResult(
        pair_id=features.pair_id,
        independence_assessment=independence,
        relationship_assessment=decision.relationship,
        independence_reason=reason,
        explanation=decision.explanation,
        penalty=decision.penalty,
    )


# ---------------------------------------------------------------------------
# Independence assessment
# ---------------------------------------------------------------------------


def assess_independence(
    features: PairFeatures,
    thresholds: DecisionThresholds,
) -> tuple[IndependenceAssessment, IndependenceReason]:
    """Decide whether the pair's independence can be rejected.

    Two routes reject independence:

    * **Direct evidence of shared provenance** — a shared-source verdict
      from the source-case comparison. A reliable shared source
      identifier is itself evidence against independence, regardless of
      ``L_match``.
    * **Rarity-based matching evidence** — ``L_match`` above
      ``lmatch_limited``: the matched features are too unlikely to have
      coincided if the reports were independent draws from the
      comparison population. The returned ``RARITY_*`` reason records
      which zone the evidence reached.

    Otherwise the pair is ``INDEPENDENT`` (reason
    ``RARITY_INSUFFICIENT``): independence cannot be rejected.
    """
    asm = features.source_case_assessment
    if asm is not None and asm.verdict in _SHARED_SOURCE_VERDICTS:
        return IndependenceAssessment.NON_INDEPENDENT, IndependenceReason.SHARED_SOURCE

    reason = _rarity_zone(features.L_match, thresholds)
    if reason == IndependenceReason.RARITY_INSUFFICIENT:
        return IndependenceAssessment.INDEPENDENT, reason
    return IndependenceAssessment.NON_INDEPENDENT, reason


def _rarity_zone(L_match: float, thresholds: DecisionThresholds) -> IndependenceReason:
    """Map ``L_match`` to its rarity zone (strict ``>`` at every bound)."""
    if L_match > thresholds.lmatch_compelling:
        return IndependenceReason.RARITY_COMPELLING
    if L_match > thresholds.lmatch_intermediate:
        return IndependenceReason.RARITY_INTERMEDIATE
    if L_match > thresholds.lmatch_limited:
        return IndependenceReason.RARITY_LIMITED
    return IndependenceReason.RARITY_INSUFFICIENT


# ---------------------------------------------------------------------------
# Relationship assessment
# ---------------------------------------------------------------------------


class _RelationshipDecision(NamedTuple):
    """Outcome of the relationship assessment for a non-independent pair."""

    relationship: RelationshipAssessment
    penalty: InconsistencyPenalty
    explanation: str


def assess_relationship(
    features: PairFeatures,
    thresholds: DecisionThresholds,
    penalty: InconsistencyPenalty,
) -> _RelationshipDecision:
    """Decide same case vs different case for a non-independent pair.

    Selects the highest-priority applicable evidence zone (see the
    module docstring's zone table): source-context zones first —
    Identity, Distinct, Unresolved, derived from the source-case
    verdict — then the rarity zones by ``L_match``.

    Only meaningful for pairs whose independence has been rejected; for
    a ``MULTI_PATIENT_CONTEXT_NO_LINK`` verdict that guarantee implies
    ``L_match > lmatch_limited`` (the verdict itself carries no
    shared-source evidence).
    """
    asm = features.source_case_assessment
    verdict = asm.verdict if asm is not None else None

    if verdict in (
        SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT,
        SourceCaseVerdict.SAME_SOURCE_SAME_CASE,
    ):
        return _identity_zone(features, verdict, asm, penalty, thresholds)

    if verdict == SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE:
        return _RelationshipDecision(
            relationship=RelationshipAssessment.DIFFERENT_CASE,
            # Pinned regardless of w_tot, so the penalty carries no comparison.
            penalty=penalty,
            explanation=(
                f"Shared source ({verdict.value}{_format_shared_identifier(asm)}); "
                f"relationship pinned to Different_case."
            ),
        )

    if verdict == SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE:
        prefix = f"Shared source ({verdict.value}{_format_shared_identifier(asm)})"
        return _unresolved_zone(features, prefix, penalty, thresholds)

    if verdict == SourceCaseVerdict.MULTI_PATIENT_CONTEXT_NO_LINK:
        prefix = f"Multi-patient context ({verdict.value})"
        return _unresolved_zone(features, prefix, penalty, thresholds)

    return _rarity_relationship(features, penalty, thresholds)


def _identity_zone(
    features: PairFeatures,
    verdict: SourceCaseVerdict,
    assessment: SourceCaseAssessment,
    penalty: InconsistencyPenalty,
    thresholds: DecisionThresholds,
) -> _RelationshipDecision:
    """Identity zone: the source context claims a single patient / same case.

    Defaults to ``SAME_CASE``, raised to ``ABSTAIN`` when ``w_tot ≥
    w_tot_compelling_zone``. The rationale: for a genuinely
    single-patient source (or one where the same case ID is confirmed),
    substantial inconsistencies are more plausibly extraction artefacts,
    reporting differences, or partial information than evidence of
    distinct individuals — so the pair is epistemically underdetermined
    rather than confidently Different_case. The tolerance is shared with
    the rarity compelling zone (default 4.0): a confirmed shared
    identifier with a single-patient / same-case claim is at least as
    strong evidence of identity as compelling rarity-based matching, so
    it deserves the same inconsistency budget. A single strong
    inconsistency is tolerated; reaching the threshold requires one
    strong + one moderate, two strongs, or equivalent accumulated
    severity. Decisive inconsistencies demote via their large weight
    (``w_decisive = 100``).
    """
    prefix = f"Shared source ({verdict.value}{_format_shared_identifier(assessment)})"
    w_tot = penalty.w_tot
    bound = thresholds.w_tot_compelling_zone

    if w_tot < bound:
        return _RelationshipDecision(
            relationship=RelationshipAssessment.SAME_CASE,
            penalty=penalty.compared_to(bound),
            explanation=f"{prefix}; w_tot={w_tot:.1f} < {bound:.1f}.",
        )

    counts = features.inconsistency_counts
    return _RelationshipDecision(
        relationship=RelationshipAssessment.ABSTAIN,
        penalty=penalty.compared_to(bound),
        explanation=(
            f"{prefix}; w_tot={w_tot:.1f} >= {bound:.1f} "
            f"(strong={counts.strong}, moderate={counts.moderate}, "
            f"decisive={counts.decisive}); abstained — inconsistencies "
            f"may reflect extraction or reporting differences rather "
            f"than distinct individuals."
        ),
    )


def _unresolved_zone(
    features: PairFeatures,
    prefix: str,
    penalty: InconsistencyPenalty,
    thresholds: DecisionThresholds,
) -> _RelationshipDecision:
    """Unresolved zone: a multi-patient source context.

    Used for two verdicts that share the same inflation concern —
    drug-set and AE-set overlap inflating ``L_match`` while patient
    identity is underdetermined:

    * ``SAME_SOURCE_UNCOMPARABLE_CASE`` — positive shared-source
      evidence (DOI / PMID / …) is present; case identifiers are not
      safely comparable.
    * ``MULTI_PATIENT_CONTEXT_NO_LINK`` — no shared source identifier,
      but both narratives indicate a multi-patient context; reaches
      this zone only when independence was rejected on rarity.

    Branches:

    * ``w_tot ≥ w_tot_limited_zone`` → ``DIFFERENT_CASE``. Any
      moderate-or-stronger inconsistency (or three accumulated minors)
      clears the bar. A single minor is still tolerated.
    * ``L_patient > lpatient_compelling`` → ``SAME_CASE``. The
      patient-specific log-evidence excludes ``drug_set`` / ``ae_set``
      contributions, so the matching evidence must come from
      demographics + clinical-date coincidences. See
      :func:`llm_dup_dev.feature_generation.probability.compute_l_patient`.
    * Otherwise → ``DIFFERENT_CASE``. Patient-specific evidence is not
      compelling enough to call ``SAME_CASE``, but non-independence is
      established, so the pair defaults to ``DIFFERENT_CASE`` rather
      than abstaining. The rule is therefore effectively two-way.
    """
    counts = features.inconsistency_counts
    w_tot = penalty.w_tot

    if w_tot >= thresholds.w_tot_limited_zone:
        return _RelationshipDecision(
            relationship=RelationshipAssessment.DIFFERENT_CASE,
            penalty=penalty.compared_to(thresholds.w_tot_limited_zone),
            explanation=(
                f"{prefix}; "
                f"w_tot={w_tot:.1f} >= {thresholds.w_tot_limited_zone:.1f} "
                f"(minor={counts.minor}, moderate={counts.moderate}, "
                f"strong={counts.strong}, decisive={counts.decisive}); "
                f"inconsistencies indicate different individuals."
            ),
        )

    l_patient = features.L_patient
    if l_patient > thresholds.lpatient_compelling:
        return _RelationshipDecision(
            relationship=RelationshipAssessment.SAME_CASE,
            penalty=penalty.compared_to(thresholds.w_tot_limited_zone),
            explanation=(
                f"{prefix}; "
                f"L_patient={l_patient:.2f} > {thresholds.lpatient_compelling:.1f} "
                f"and w_tot={w_tot:.1f} < {thresholds.w_tot_limited_zone:.1f}; "
                f"compelling patient-specific evidence with minimal "
                f"inconsistency."
            ),
        )

    return _RelationshipDecision(
        relationship=RelationshipAssessment.DIFFERENT_CASE,
        penalty=penalty.compared_to(thresholds.w_tot_limited_zone),
        explanation=(
            f"{prefix}; "
            f"L_patient={l_patient:.2f} (threshold "
            f"{thresholds.lpatient_compelling:.1f}), w_tot={w_tot:.1f} "
            f"(threshold {thresholds.w_tot_limited_zone:.1f}); "
            f"patient-specific evidence insufficient to call Same_case "
            f"in a multi-patient source context — defaulting to "
            f"Different_case."
        ),
    )


def _rarity_relationship(
    features: PairFeatures,
    penalty: InconsistencyPenalty,
    thresholds: DecisionThresholds,
) -> _RelationshipDecision:
    """Rarity zones: no zone-selecting source-context verdict.

    Each zone tolerates progressively less inconsistency: the stronger
    the matching evidence, the more of the observed inconsistency can
    plausibly be extraction or reporting noise. ``L_match >
    lmatch_limited`` is guaranteed here (independence was rejected on
    rarity), so the limited zone is a total fall-through.
    """
    L = features.L_match
    if L > thresholds.lmatch_compelling:
        zone_name = "Compelling"
        lmatch_bound = thresholds.lmatch_compelling
        w_bound = thresholds.w_tot_compelling_zone
    elif L > thresholds.lmatch_intermediate:
        zone_name = "Intermediate"
        lmatch_bound = thresholds.lmatch_intermediate
        w_bound = thresholds.w_tot_intermediate_zone
    else:
        zone_name = "Limited"
        lmatch_bound = thresholds.lmatch_limited
        w_bound = thresholds.w_tot_limited_zone

    w_tot = penalty.w_tot
    if w_tot < w_bound:
        return _RelationshipDecision(
            relationship=RelationshipAssessment.SAME_CASE,
            penalty=penalty.compared_to(w_bound),
            explanation=(
                f"{zone_name} matching evidence (L_match={L:.2f} > "
                f"{lmatch_bound}); w_tot={w_tot:.1f} < {w_bound:.1f}."
            ),
        )

    counts = features.inconsistency_counts
    return _RelationshipDecision(
        relationship=RelationshipAssessment.DIFFERENT_CASE,
        penalty=penalty.compared_to(w_bound),
        explanation=(
            f"{zone_name} matching evidence (L_match={L:.2f}) but "
            f"w_tot={w_tot:.1f} >= {w_bound:.1f} "
            f"(strong={counts.strong}, moderate={counts.moderate}, "
            f"decisive={counts.decisive})."
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
