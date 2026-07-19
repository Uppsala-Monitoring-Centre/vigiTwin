"""Classification result models for the duplicate adjudication pipeline.

Combines the internal independence assessment (``IndependenceAssessment``)
and the relationship assessment (``RelationshipAssessment``) into
the user-facing label vocabulary used by the eval framework, the review
HTML, and human annotators.

See: docs/duplicate_adjudication_spec.md § Relationship assessment
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .enums import (
    RelationshipAssessment,
    IndependenceAssessment,
    IndependenceReason,
    InconsistencySeverity,
)


class PenaltyTerm(BaseModel):
    """One severity's contribution to ``w_tot``.

    Carries the ``weight`` **actually applied** by the run, not the default,
    so the arithmetic can be reproduced from the result alone.
    """

    severity: InconsistencySeverity
    count: int
    weight: float


class InconsistencyPenalty(BaseModel):
    """The weighted inconsistency score, and the comparison the decision made.

        w_tot = Σ (count × weight)  over the severities present

    Self-describing by design: the classifier is the only component that knows
    both the inconsistency counts and the configured weights, so it records the
    result rather than leaving consumers to recompute it. Recomputing requires
    the run's ``DecisionThresholds``, and a consumer that assumes the defaults
    will silently disagree with the decision whenever the run used custom
    weights.

    ``threshold`` is the bar this score was tested against, or ``None`` when the
    decision made no ``w_tot`` comparison at all — the below-limited zone (where
    ``L_match`` decides) and ``SAME_SOURCE_DIFFERENT_CASE`` (pinned regardless of
    ``w_tot``). In those cases the score is still recorded; there is simply no
    comparison to report.

    See: docs/duplicate_adjudication_spec.md § Relationship assessment
    """

    w_tot: float
    threshold: float | None = None
    terms: list[PenaltyTerm] = Field(default_factory=list)

    @property
    def satisfied(self) -> bool | None:
        """Whether ``w_tot < threshold``; ``None`` when no comparison was made.

        Mirrors the strict ``<`` used by every zone gate: at ``w_tot ==
        threshold`` the tolerance is exceeded.
        """
        if self.threshold is None:
            return None
        return self.w_tot < self.threshold

    def compared_to(self, threshold: float) -> InconsistencyPenalty:
        """Return a copy recording the threshold this score was tested against."""
        return self.model_copy(update={"threshold": threshold})


class ClassificationResult(BaseModel):
    """Full duplicate-adjudication output for a report pair.

    The decision logic answers two sequential questions:

    * ``independence_assessment`` — did the two reports arise
      independently? Independence is rejected either on direct evidence
      of shared provenance (a shared source identifier) or when the
      rarity-based matching evidence is strong enough to reject the
      null hypothesis that the reports are independent draws from the
      comparison population.
    * ``relationship_assessment`` — for non-independent pairs, do the
      reports describe the same underlying case (``Same_case``), a
      demonstrably different case (``Different_case``), or is the
      evidence insufficient to safely resolve the question
      (``Abstain``)?

    Use ``final_label`` for the user-facing label (``Duplicates`` /
    ``Otherwise_related`` / ``Unrelated`` / ``Abstain``); downstream
    consumers should depend on this rather than on the raw internal
    fields. ``penalty`` is the exception: it is diagnostic, carried so that
    the inconsistency arithmetic behind the decision can be displayed and
    audited without reconstructing it.
    """

    pair_id: str
    independence_assessment: IndependenceAssessment
    relationship_assessment: RelationshipAssessment | None = None
    independence_reason: IndependenceReason | None = None
    explanation: str = ""
    # Optional diagnostic: the weighted inconsistency score and the comparison
    # the decision made. Always populated by the pipeline; optional because it
    # feeds no decision — it records one already made, so its absence degrades
    # display rather than changing an outcome.
    penalty: InconsistencyPenalty | None = None

    @property
    def final_label(self) -> str:
        """Map the two assessments to the user-facing label vocabulary.

        Mapping:
            Non-independent + Same_case       → Duplicates
            Non-independent + Different_case  → Otherwise_related
            Non-independent + Abstain         → Abstain
            Independent                       → Unrelated

        A non-independent result with no relationship assessment maps to
        ``Abstain`` as a defensive fallback; the decision logic always
        sets the relationship assessment for non-independent pairs.
        """
        if self.independence_assessment == IndependenceAssessment.INDEPENDENT:
            return "Unrelated"
        # Non-independent — use the relationship assessment
        if self.relationship_assessment == RelationshipAssessment.SAME_CASE:
            return "Duplicates"
        if self.relationship_assessment == RelationshipAssessment.DIFFERENT_CASE:
            return "Otherwise_related"
        return "Abstain"
