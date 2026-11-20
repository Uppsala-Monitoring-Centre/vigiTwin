"""The SCLE assessment form: controlled vocabularies and data models.

This module defines the structured dimensions of the SCLE form (as enums), the
auto-populated context that the package fills in for each case, the per-reviewer
assessment model, and the applicability rules that say which conditional
dimensions apply to a given case.

Applicability (from the protocol):

* **Always** — free-text assessment, case difficulty, reference-label issue.
* **Only for misclassified pairs** — never event, unexpected method behaviour,
  incorrect or imprecise input data, the free-text source of error, the
  contributing method stages, and the implicated feature domains.
* **Only where the two methods disagree** — the free-text explanation of the
  difference between methods.

"Misclassified" and "methods disagree" are properties of the frozen results,
so the package computes them into :class:`SCLEAutoContext` at sampling time;
reviewers never set them.

Two of the misclassification dimensions are *list-valued* and study-configured:
``error_stages`` (which method components contributed) and
``implicated_feature_domains``. The study supplies the two vocabularies; this
package hard-codes neither. Both are answered tick-only in the workbook — an
unticked entry means "no", so an empty list is a valid answer.

A blank cell means "no" for the yes/no misclassification dimensions too (see
:data:`MISCLASSIFICATION_YESNO_DIMENSIONS`), resolved at workbook read-back.
Only :data:`MISCLASSIFICATION_REQUIRED_DIMENSIONS` — the free-text description of
what went wrong, which has no sensible blank default — must be filled explicitly
wherever it applies.

The contributing stages are compared one dimension per stage (see
:func:`structured_dimensions`), while the feature domains are compared as a
single set-valued dimension.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# --- Structured dimensions (controlled vocabularies) -----------------------


class CaseDifficulty(str, Enum):
    """How hard correct binary classification would be for the method.

    * ``trivial`` — expected to be very rare. Duplicate identification is
      inherently ambiguous, so reserve this for pairs where nothing is left to
      judge: the reports match exactly, carry sufficient information, and are
      explicitly linked to one another.
    * ``clear`` — the correct label is clear to a human reviewer, but arriving at
      it is not trivial for a computational method. Two reports whose narratives
      plainly describe the same case belong here, because the method would still
      have to extract that information reliably.
    * ``non_trivial`` — genuinely difficult to classify.
    * ``unclear`` — so difficult that the reviewer would have abstained.
    """

    TRIVIAL = "trivial"
    CLEAR = "clear"
    NON_TRIVIAL = "non_trivial"
    UNCLEAR = "unclear"


class ReferenceLabelIssue(str, Enum):
    """Whether the reference label looks sound, suspect, or genuinely ambiguous."""

    NO_APPARENT_ISSUE = "no_apparent_issue"
    POSSIBLY_INCORRECT = "possibly_incorrect"
    GENUINELY_AMBIGUOUS = "genuinely_ambiguous"


class YesNo(str, Enum):
    """Binary answer used by every yes/no misclassification dimension."""

    YES = "yes"
    NO = "no"


#: The two evaluation components an SCLE case can belong to.
EvaluationComponent = Literal["precision", "recall"]

#: Prefix distinguishing a per-stage dimension key from a fixed field name.
ERROR_STAGE_PREFIX = "error_stage::"


def error_stage_dimension(stage: str) -> str:
    """Return the structured-dimension key for a contributing method stage."""
    return f"{ERROR_STAGE_PREFIX}{stage}"


def error_stage_name(dimension: str) -> str | None:
    """Return the stage a dimension key refers to, or ``None`` if it is not one."""
    if dimension.startswith(ERROR_STAGE_PREFIX):
        return dimension[len(ERROR_STAGE_PREFIX) :]
    return None


#: Structured dimensions that exist regardless of the study's configuration.
FIXED_STRUCTURED_DIMENSIONS: tuple[str, ...] = (
    "case_difficulty",
    "reference_label_issue",
    "never_event",
    "unexpected_method_behaviour",
    "incorrect_or_imprecise_input_data",
)


def structured_dimensions(error_stages: Sequence[str]) -> tuple[str, ...]:
    """Return the dimensions compared between reviewers, in workbook order.

    One dimension per configured stage, so each stage gets its own agreement
    statistic and its own arbiter consensus column. The feature domains stay a
    single set-valued dimension however many are configured. Free-text fields
    are never included — those are an arbiter judgement.
    """
    return (
        *FIXED_STRUCTURED_DIMENSIONS,
        *(error_stage_dimension(s) for s in error_stages),
        "implicated_feature_domains",
    )


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

#: Yes/no dimensions a misclassified pair is asked but need not tick: a blank
#: cell reads as "no" at workbook read-back, the same convention the tick-only
#: list columns use. A correctly classified pair must leave them empty.
MISCLASSIFICATION_YESNO_DIMENSIONS: tuple[str, ...] = (
    "never_event",
    "unexpected_method_behaviour",
    "incorrect_or_imprecise_input_data",
)

#: Dimensions a misclassified pair must answer explicitly. Free text has no
#: sensible blank default, so it stays required wherever it applies.
MISCLASSIFICATION_REQUIRED_DIMENSIONS: tuple[str, ...] = (
    "error_source_description",
)

#: Every dimension the misclassification block covers, excluding the two
#: list-valued fields below. Drives applicability — and so the ``N/A`` prefill —
#: without saying anything about which members must be filled.
MISCLASSIFICATION_DIMENSIONS: tuple[str, ...] = (
    *MISCLASSIFICATION_YESNO_DIMENSIONS,
    *MISCLASSIFICATION_REQUIRED_DIMENSIONS,
)

#: List-valued fields that a misclassified pair *may* answer and a correctly
#: classified pair must leave empty.
MISCLASSIFICATION_LIST_DIMENSIONS: tuple[str, ...] = (
    "error_stages",
    "implicated_feature_domains",
)

#: Dimension applicable only where the two methods disagree.
DISAGREEMENT_DIMENSIONS: tuple[str, ...] = ("method_difference_explanation",)


def applicable_dimensions(context: SCLEAutoContext) -> set[str]:
    """Return the set of dimension field names applicable to ``context``."""
    dims = set(ALWAYS_DIMENSIONS)
    if context.is_misclassified:
        dims.update(MISCLASSIFICATION_DIMENSIONS)
        dims.update(MISCLASSIFICATION_LIST_DIMENSIONS)
    if context.methods_disagree:
        dims.update(DISAGREEMENT_DIMENSIONS)
    return dims


# --- Reviewer assessment ---------------------------------------------------


class SCLECaseAssessment(BaseModel):
    """One reviewer's assessment of one SCLE case.

    Conditional dimensions are ``None`` (or an empty list) when they do not
    apply. The model enforces *internal* consistency — the yes/no
    misclassification dimensions are all-present or all-absent. The full check
    that presence matches the paired :class:`SCLEAutoContext` is done by
    :func:`check_assessment_against_context`, because the model does not carry
    the context; validation of the two list-valued fields against the study's
    frozen vocabularies happens at workbook read-back, which is where those
    lists are available.
    """

    pair_id: str
    reviewer_id: str

    free_text_assessment: str
    case_difficulty: CaseDifficulty
    reference_label_issue: ReferenceLabelIssue

    never_event: YesNo | None = None
    unexpected_method_behaviour: YesNo | None = None
    incorrect_or_imprecise_input_data: YesNo | None = None
    error_source_description: str | None = None
    error_stages: list[str] = Field(default_factory=list)
    implicated_feature_domains: list[str] = Field(default_factory=list)

    method_difference_explanation: str | None = None

    @property
    def misclassification_answered(self) -> bool:
        """Whether the misclassification block was applicable to this case.

        The yes/no block is filled all-or-nothing (enforced below), so any one
        of its members answers the question. Comparison code uses this to tell
        "the reviewer ticked nothing" apart from "the question was never asked",
        which matters for the two tick-only list dimensions.

        This relies on a **read-back invariant**: because a blank yes/no cell is
        resolved to "no" on a misclassified pair, an assessment read from a
        workbook has the block set exactly when the pair is misclassified. An
        assessment built directly in code that leaves the yes/no fields ``None``
        on a misclassified pair breaks that invariant, and its stage and domain
        ticks will compare as "not asked" rather than as "nothing ticked".
        """
        return self.never_event is not None

    @model_validator(mode="after")
    def _check_internal_consistency(self) -> "SCLECaseAssessment":
        block = {
            name: getattr(self, name)
            for name in MISCLASSIFICATION_YESNO_DIMENSIONS
        }
        set_fields = [
            name for name, val in block.items() if val not in (None, "")
        ]
        if set_fields and len(set_fields) != len(block):
            missing = [name for name, val in block.items() if val in (None, "")]
            raise ValueError(
                f"pair {self.pair_id!r}: the yes/no misclassification dimensions "
                f"must be filled as a group; set {set_fields} but missing "
                f"{missing}."
            )

        for name in MISCLASSIFICATION_LIST_DIMENSIONS:
            values = getattr(self, name)
            duplicates = sorted({v for v in values if values.count(v) > 1})
            if duplicates:
                raise ValueError(
                    f"pair {self.pair_id!r}: {name} repeats {duplicates}."
                )
        return self


def check_assessment_against_context(
    assessment: SCLECaseAssessment, context: SCLEAutoContext
) -> list[str]:
    """Return a list of problems where ``assessment`` mismatches ``context``.

    An empty list means the assessment fills every dimension that must be
    filled, and none that do not apply. This is the source-of-truth check run
    when a reviewer submission is read back.

    Three groups are checked one-sidedly — they may be absent where they apply
    but must be absent where they do not. The two list-valued dimensions may be
    empty on a misclassified case because nothing was implicated, and the yes/no
    misclassification dimensions because a blank cell means "no" (read-back
    resolves those to an explicit "no", so they normally arrive filled).
    """
    problems: list[str] = []
    if assessment.pair_id != context.pair_id:
        problems.append(
            f"pair_id mismatch: assessment {assessment.pair_id!r} vs context "
            f"{context.pair_id!r}"
        )
    applicable = applicable_dimensions(context)

    required = set(MISCLASSIFICATION_REQUIRED_DIMENSIONS) | set(
        DISAGREEMENT_DIMENSIONS
    )
    for field in required | set(MISCLASSIFICATION_YESNO_DIMENSIONS):
        value = getattr(assessment, field)
        is_applicable = field in applicable
        if is_applicable and field in required and value in (None, ""):
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
    if not context.is_misclassified:
        for field in MISCLASSIFICATION_LIST_DIMENSIONS:
            if getattr(assessment, field):
                problems.append(
                    f"{context.pair_id}: {field} set on a correctly classified "
                    f"case."
                )
    return problems


# --- Reviewer submission ---------------------------------------------------


class ReviewerSubmission(BaseModel):
    """A single reviewer's complete set of assessments for one component.

    ``sample_checksum`` is the sha256 of the ``SCLESample`` JSON the workbook
    was generated from, tying the submission to the exact sampled case list so a
    stale or mismatched workbook is caught on read-back.

    ``error_stages`` and ``feature_domains`` are the study's frozen vocabularies
    as embedded in the workbook the submission was read from. They travel with
    the submission so that agreement, discrepancy detection, arbitration, and
    consensus can derive the dimension set from the data rather than having the
    configuration threaded into each of them.
    """

    workbook_id: str
    reviewer_id: str
    evaluation_component: EvaluationComponent
    sample_checksum: str
    error_stages: list[str] = Field(default_factory=list)
    feature_domains: list[str] = Field(default_factory=list)
    assessments: list[SCLECaseAssessment]


__all__ = [
    "CaseDifficulty",
    "ReferenceLabelIssue",
    "YesNo",
    "EvaluationComponent",
    "ERROR_STAGE_PREFIX",
    "error_stage_dimension",
    "error_stage_name",
    "FIXED_STRUCTURED_DIMENSIONS",
    "structured_dimensions",
    "SCLEAutoContext",
    "ALWAYS_DIMENSIONS",
    "MISCLASSIFICATION_DIMENSIONS",
    "MISCLASSIFICATION_YESNO_DIMENSIONS",
    "MISCLASSIFICATION_REQUIRED_DIMENSIONS",
    "MISCLASSIFICATION_LIST_DIMENSIONS",
    "DISAGREEMENT_DIMENSIONS",
    "applicable_dimensions",
    "SCLECaseAssessment",
    "check_assessment_against_context",
    "ReviewerSubmission",
]
