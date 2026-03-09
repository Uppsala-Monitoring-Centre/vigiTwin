"""Classification result models.

Maps the pipeline's internal terminology to the evaluation-set
label vocabulary used by the eval framework and human annotators.

See: docs/same_individual_assessment_spec.md § Decision logic
"""

from __future__ import annotations

from pydantic import BaseModel

from .enums import PrimaryClassification, RelationshipClassification


class ClassificationResult(BaseModel):
    """Full classification output for a report pair.

    The primary classification determines whether the reports describe
    the same individual. When primary is 'Different individuals', the
    relationship sub-classification assesses clinical relatedness.
    """

    pair_id: str
    primary: PrimaryClassification
    relationship: RelationshipClassification | None = None
    explanation: str = ""

    @property
    def final_label(self) -> str:
        """Map to the eval-set label vocabulary.

        Mapping:
            Same individual             → Duplicates
            Different individuals + Related   → Otherwise_related
            Different individuals + Unrelated → Unrelated
            Abstain                     → Abstain
        """
        if self.primary == PrimaryClassification.SAME_INDIVIDUAL:
            return "Duplicates"
        if self.primary == PrimaryClassification.ABSTAIN:
            return "Abstain"
        # Different individuals — check relationship
        if self.relationship == RelationshipClassification.RELATED:
            return "Otherwise_related"
        if self.relationship == RelationshipClassification.UNRELATED:
            return "Unrelated"
        return "Abstain"
