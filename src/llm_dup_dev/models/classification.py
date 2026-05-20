"""Classification result models for the duplicate adjudication pipeline.

Combines the internal independence assessment (``IndependenceAssessment``)
and the duplicate-relationship sub-label (``DuplicateRelationship``) into
the user-facing label vocabulary used by the eval framework, the review
HTML, and human annotators.

See: docs/duplicate_adjudication_spec.md § Decision logic
"""

from __future__ import annotations

from pydantic import BaseModel

from .enums import DuplicateRelationship, IndependenceAssessment, IndependenceReason


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
    fields.
    """

    pair_id: str
    independence_assessment: IndependenceAssessment
    duplicate_relationship: DuplicateRelationship | None = None
    independence_reason: IndependenceReason | None = None
    explanation: str = ""

    @property
    def final_label(self) -> str:
        """Map the internal labels to the user-facing label vocabulary.

        Mapping:
            Non-independent + Duplicates         → Duplicates
            Non-independent + Otherwise_related  → Otherwise_related
            Independent                          → Unrelated
            Abstain                              → Abstain (defensive)

        The decision logic only ever emits ``NON_INDEPENDENT`` or
        ``INDEPENDENT``; the ``Abstain`` arms below are a defensive
        backstop for future abstain paths or malformed states and are
        not produced by the standard rarity-zone / shared-source logic.
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
