"""Classification result models for the duplicate adjudication pipeline.

Combines the internal independence assessment (``IndependenceAssessment``)
and the duplicate-relationship sub-label (``DuplicateRelationship``) into
the user-facing label vocabulary used by the eval framework, the review
HTML, and human annotators.

See: docs/duplicate_adjudication_spec.md § Decision logic
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .enums import (
    DuplicateRelationship,
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
    ``L_match`` decides), ``SAME_SOURCE_DIFFERENT_CASE`` (pinned regardless of
    ``w_tot``), and the multi-patient Abstain branch. In those cases the score is
    still recorded; there is simply no comparison to report.

    See: docs/duplicate_adjudication_spec.md § Decision logic
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

    Two layered internal labels are produced by the decision logic:

    * ``independence_assessment`` — whether the rarity-based matching
      evidence is strong enough to reject the null hypothesis that the
      two reports are independent draws from the comparison population.
    * ``duplicate_relationship`` — when independence has been rejected,
      a sub-label that uses inconsistency signals to distinguish a
      likely-duplicate case from one that is linked by something other
      than being the same individual case (e.g. same reporter, same
      study).

    Use ``final_label`` for the user-facing label
    (``Duplicates`` / ``Otherwise_related`` / ``Unrelated``); downstream
    consumers should depend on this rather than on the raw internal
    fields. ``penalty`` is the exception: it is diagnostic, carried so that
    the inconsistency arithmetic behind the decision can be displayed and
    audited without reconstructing it.
    """

    pair_id: str
    independence_assessment: IndependenceAssessment
    duplicate_relationship: DuplicateRelationship | None = None
    independence_reason: IndependenceReason | None = None
    explanation: str = ""
    # Optional diagnostic: the weighted inconsistency score and the comparison
    # the decision made. Always populated by the pipeline; optional because it
    # feeds no decision — it records one already made, so its absence degrades
    # display rather than changing an outcome.
    penalty: InconsistencyPenalty | None = None

    @property
    def final_label(self) -> str:
        """Map the internal labels to the user-facing label vocabulary.

        Mapping:
            Non-independent + Duplicates         → Duplicates
            Non-independent + Otherwise_related  → Otherwise_related
            Non-independent + Abstain            → Abstain
            Independent                          → Unrelated
            Abstain (assessment)                 → Abstain (defensive)

        The rarity-zone path emits only ``NON_INDEPENDENT`` or
        ``INDEPENDENT``. The shared-source override path additionally
        emits ``NON_INDEPENDENT + DuplicateRelationship.ABSTAIN`` for
        epistemically underdetermined same-source scenarios.
        ``IndependenceAssessment.ABSTAIN`` is a defensive backstop.
        """
        if self.independence_assessment == IndependenceAssessment.ABSTAIN:
            return "Abstain"
        if self.independence_assessment == IndependenceAssessment.INDEPENDENT:
            return "Unrelated"
        # Non-independent — use the duplicate relationship sub-label
        if self.duplicate_relationship == DuplicateRelationship.DUPLICATES:
            return "Duplicates"
        if self.duplicate_relationship == DuplicateRelationship.OTHERWISE_RELATED:
            return "Otherwise_related"
        return "Abstain"
