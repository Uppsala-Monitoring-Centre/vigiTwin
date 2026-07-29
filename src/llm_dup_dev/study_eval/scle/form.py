"""The SCLE assessment form: controlled vocabularies and data models.

This module defines the structured dimensions of the SCLE form (as enums), the
auto-populated context that the package fills in for each case, the per-reviewer
assessment model, and the applicability rules that say which conditional
dimensions apply to a given case.

Applicability (from the protocol):

* **Always** — free-text assessment, case difficulty, reference-label issue.
* **Only for misclassified pairs** — never event, unexpected method behaviour,
  material input-data limitation (+ its sub-category when present), primary
  source of error, implicated feature domains.
* **Only where the two methods disagree** — the free-text explanation of the
  difference between methods.

"Misclassified" and "methods disagree" are properties of the frozen results,
so the package computes them into :class:`SCLEAutoContext` at sampling time;
reviewers never set them.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# --- Structured dimensions (controlled vocabularies) -----------------------


class CaseDifficulty(str, Enum):
    """Whether correct binary classification would be trivial for the method."""

    TRIVIAL = "trivial"
    NON_TRIVIAL = "non_trivial"
    UNCLEAR = "unclear"


class ReferenceLabelIssue(str, Enum):
    """Whether the reference label looks sound, suspect, or genuinely ambiguous."""

    NO_APPARENT_ISSUE = "no_apparent_issue"
    POSSIBLY_INCORRECT = "possibly_incorrect"
    GENUINELY_AMBIGUOUS = "genuinely_ambiguous"


class YesNoUnclear(str, Enum):
    """Three-valued answer used by several misclassification dimensions."""

    YES = "yes"
    NO = "no"
    UNCLEAR = "unclear"


class InputDataLimitationCategory(str, Enum):
    """Category of a material input-data limitation, when one is present."""

    DATA_QUALITY_OR_ENCODING = "data_quality_or_encoding"
    MISSING_INFORMATION = "missing_information"
    UNAVAILABLE_TO_METHOD = "unavailable_to_method"
    OTHER = "other"


class PrimaryErrorSource(str, Enum):
    """Method component principally responsible for a misclassification."""

    EVIDENCE_EXTRACTION = "evidence_extraction"
    INDEPENDENCE_ASSESSMENT = "independence_assessment"
    RELATIONSHIP_ASSESSMENT = "relationship_assessment"
    FINAL_DECISION_MAPPING = "final_decision_mapping"
    COMPONENT_INTERACTION = "component_interaction"
    INPUT_OR_REFERENCE_ISSUE = "input_or_reference_issue"
    UNCLEAR = "unclear"


#: The short prespecified list of feature domains a reviewer may implicate.
#: MUST be frozen before workbooks are generated — mirrors the method's feature
#: families (see docs/duplicate_adjudication_spec.md). Kept as a tuple so the
#: workbook can offer it as a fixed multi-select vocabulary.
FEATURE_DOMAINS: tuple[str, ...] = (
    "demographics",
    "drugs",
    "adverse_events",
    "dates",
    "narrative",
    "source_case_identifiers",
    "ae_clinical_picture",
    "reporting_context",
)

#: The two evaluation components an SCLE case can belong to.
EvaluationComponent = Literal["precision", "recall"]


# --- Auto-populated context ------------------------------------------------


class SCLEAutoContext(BaseModel):
    """Case context populated from the frozen results, never by reviewers.

    Carries everything the reviewer should *see but not assess*: the reference
    label, the method output, the confusion cell (precision) or joint stratum
    (recall), whether the methods disagree, whether the case is a
    misclassification by the adjudicator, a short decision-trace summary, and
    the packet filename. ``is_misclassified`` and ``methods_disagree`` drive the
    applicability of the conditional dimensions.

    ``screening_retrieved`` and ``methods_disagree`` are ``None`` when the case
    comes from a reference set with ``screening_mode: not_evaluated``: without a
    screening verdict there is no second method to agree or disagree with, so
    the disagreement dimension does not apply to such a case.
    """

    pair_id: str
    evaluation_component: EvaluationComponent
    reference_set_id: str | None = None

    reference_label: str
    adjudicator_final_label: str
    screening_retrieved: bool | None = None

    confusion_cell: str | None = None
    joint_stratum: str | None = None

    methods_disagree: bool | None
    is_misclassified: bool

    decision_trace_summary: str = ""
    packet_filename: str | None = None


# --- Applicability ---------------------------------------------------------

#: Dimensions present on every case.
ALWAYS_DIMENSIONS: tuple[str, ...] = (
    "free_text_assessment",
    "case_difficulty",
    "reference_label_issue",
)

#: Dimensions applicable only when the adjudicator misclassified the pair.
MISCLASSIFICATION_DIMENSIONS: tuple[str, ...] = (
    "never_event",
    "unexpected_method_behaviour",
    "material_input_data_limitation",
    "primary_error_source",
)

#: Dimension applicable only where the two methods disagree.
DISAGREEMENT_DIMENSIONS: tuple[str, ...] = ("method_difference_explanation",)


def applicable_dimensions(context: SCLEAutoContext) -> set[str]:
    """Return the set of dimension field names applicable to ``context``."""
    dims = set(ALWAYS_DIMENSIONS)
    if context.is_misclassified:
        dims.update(MISCLASSIFICATION_DIMENSIONS)
    if context.methods_disagree:
        dims.update(DISAGREEMENT_DIMENSIONS)
    return dims


# --- Reviewer assessment ---------------------------------------------------


class SCLECaseAssessment(BaseModel):
    """One reviewer's assessment of one SCLE case.

    Conditional dimensions are ``None`` when they do not apply. The model
    enforces *internal* consistency (the misclassification block is all-present
    or all-absent; a limitation category is present exactly when a material
    limitation is reported). The full check that presence matches the paired
    :class:`SCLEAutoContext` is done by
    :func:`check_assessment_against_context`, because the model does not carry
    the context.
    """

    pair_id: str
    reviewer_id: str

    free_text_assessment: str
    case_difficulty: CaseDifficulty
    reference_label_issue: ReferenceLabelIssue

    never_event: YesNoUnclear | None = None
    unexpected_method_behaviour: YesNoUnclear | None = None
    material_input_data_limitation: YesNoUnclear | None = None
    input_data_limitation_category: InputDataLimitationCategory | None = None
    primary_error_source: PrimaryErrorSource | None = None
    implicated_feature_domains: list[str] = Field(default_factory=list)

    method_difference_explanation: str | None = None

    @model_validator(mode="after")
    def _check_internal_consistency(self) -> "SCLECaseAssessment":
        block = {
            "never_event": self.never_event,
            "unexpected_method_behaviour": self.unexpected_method_behaviour,
            "material_input_data_limitation": self.material_input_data_limitation,
            "primary_error_source": self.primary_error_source,
        }
        set_fields = [name for name, val in block.items() if val is not None]
        if set_fields and len(set_fields) != len(block):
            missing = [name for name, val in block.items() if val is None]
            raise ValueError(
                f"pair {self.pair_id!r}: misclassification dimensions must be "
                f"filled as a group; set {set_fields} but missing {missing}."
            )

        limitation_present = (
            self.material_input_data_limitation == YesNoUnclear.YES
        )
        if limitation_present and self.input_data_limitation_category is None:
            raise ValueError(
                f"pair {self.pair_id!r}: material_input_data_limitation is "
                f"'yes' but input_data_limitation_category is missing."
            )
        if not limitation_present and self.input_data_limitation_category is not None:
            raise ValueError(
                f"pair {self.pair_id!r}: input_data_limitation_category is set "
                f"but material_input_data_limitation is not 'yes'."
            )

        unknown = [d for d in self.implicated_feature_domains if d not in FEATURE_DOMAINS]
        if unknown:
            raise ValueError(
                f"pair {self.pair_id!r}: unknown feature domain(s) {unknown}. "
                f"Allowed: {FEATURE_DOMAINS}."
            )
        return self


def check_assessment_against_context(
    assessment: SCLECaseAssessment, context: SCLEAutoContext
) -> list[str]:
    """Return a list of problems where ``assessment`` mismatches ``context``.

    An empty list means the assessment fills exactly the applicable dimensions
    (and no inapplicable ones). This is the source-of-truth check run when a
    reviewer submission is read back.
    """
    problems: list[str] = []
    if assessment.pair_id != context.pair_id:
        problems.append(
            f"pair_id mismatch: assessment {assessment.pair_id!r} vs context "
            f"{context.pair_id!r}"
        )
    applicable = applicable_dimensions(context)

    conditional = set(MISCLASSIFICATION_DIMENSIONS) | set(DISAGREEMENT_DIMENSIONS)
    for field in conditional:
        value = getattr(assessment, field)
        is_applicable = field in applicable
        if is_applicable and value in (None, ""):
            problems.append(
                f"{context.pair_id}: dimension {field!r} is applicable "
                f"(misclassified={context.is_misclassified}, "
                f"methods_disagree={context.methods_disagree}) but not filled."
            )
        if not is_applicable and value not in (None, ""):
            problems.append(
                f"{context.pair_id}: dimension {field!r} is not applicable but "
                f"was filled with {value!r}."
            )
    if not context.is_misclassified and assessment.implicated_feature_domains:
        problems.append(
            f"{context.pair_id}: implicated_feature_domains set on a correctly "
            f"classified case."
        )
    return problems


# --- Reviewer submission ---------------------------------------------------


class ReviewerSubmission(BaseModel):
    """A single reviewer's complete set of assessments for one component.

    ``sample_checksum`` is the sha256 of the ``SCLESample`` JSON the workbook
    was generated from, tying the submission to the exact sampled case list so a
    stale or mismatched workbook is caught on read-back.
    """

    workbook_id: str
    reviewer_id: str
    evaluation_component: EvaluationComponent
    sample_checksum: str
    assessments: list[SCLECaseAssessment]


__all__ = [
    "CaseDifficulty",
    "ReferenceLabelIssue",
    "YesNoUnclear",
    "InputDataLimitationCategory",
    "PrimaryErrorSource",
    "FEATURE_DOMAINS",
    "EvaluationComponent",
    "SCLEAutoContext",
    "ALWAYS_DIMENSIONS",
    "MISCLASSIFICATION_DIMENSIONS",
    "DISAGREEMENT_DIMENSIONS",
    "applicable_dimensions",
    "SCLECaseAssessment",
    "check_assessment_against_context",
    "ReviewerSubmission",
]
