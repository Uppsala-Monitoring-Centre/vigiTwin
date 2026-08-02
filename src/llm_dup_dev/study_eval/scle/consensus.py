"""Merge two reviews and an arbitration into a consensus, preserving originals.

The consensus is built dimension by dimension: where the two reviewers already
agreed, the agreed value is carried through (source ``reviewer_agreement``);
where they disagreed, the arbiter's value is taken (source ``arbitration``).
Every consensus dimension therefore records its provenance. The two original
:class:`ReviewerSubmission` objects are stored verbatim and never mutated, so
the record retains exactly what each reviewer said.

Pairs whose consensus reference-label assessment is anything other than "no
apparent issue" are collected into ``reference_label_concerns`` — the transparent
reporting / optional sensitivity-analysis path. The primary reference labels are
never altered here.
"""

from __future__ import annotations

from pydantic import BaseModel

from llm_dup_dev.study_eval.checksums import sha256_canonical_json
from llm_dup_dev.study_eval.scle.arbitration import ArbitrationSubmission
from llm_dup_dev.study_eval.scle.discrepancy import (
    DiscrepancyReport,
    _dimension_value,
    report_dimensions,
)
from llm_dup_dev.study_eval.scle.form import ReviewerSubmission, error_stage_name

_SOURCE_AGREEMENT = "reviewer_agreement"
_SOURCE_ARBITRATION = "arbitration"


class ConsensusAssessment(BaseModel):
    """The resolved assessment for one pair, with per-dimension provenance.

    Structured dimension values are stored as their string form (enum value, or
    ``None`` when the dimension does not apply). ``dimension_sources`` maps each
    structured dimension to ``reviewer_agreement`` or ``arbitration``; since the
    error stages are one dimension each, their provenance is recorded per stage
    even though the resolved answers collapse into the ``error_stages`` list.

    Free text is never merged automatically: both reviewers' assessments are
    retained in ``reviewer_free_texts``. ``consensus_free_text`` holds the
    arbiter's text for a pair that went to arbitration (a structural
    disagreement); it is empty for an agreement pair, where the two reviewer
    texts stand on their own.
    """

    pair_id: str

    case_difficulty: str | None = None
    reference_label_issue: str | None = None
    never_event: str | None = None
    unexpected_method_behaviour: str | None = None
    incorrect_or_imprecise_input_data: str | None = None
    error_stages: list[str] = []
    implicated_feature_domains: list[str] = []

    dimension_sources: dict[str, str] = {}
    reviewer_free_texts: dict[str, str] = {}
    consensus_free_text: str = ""
    free_text_materially_different: bool = False
    arbiter_rationale: str = ""
    arbitrated: bool = False


class SCLEConsensusResult(BaseModel):
    """The complete consensus record for one evaluation component.

    Holds the two original reviews verbatim (with their checksums), the
    structured discrepancy report, the arbitration, the resolved consensus, and
    the list of pairs flagged for a possible reference-label issue.
    """

    evaluation_component: str
    reviewer_submissions: list[ReviewerSubmission]
    submission_checksums: dict[str, str]
    discrepancy_report: DiscrepancyReport
    arbitration: ArbitrationSubmission
    consensus: list[ConsensusAssessment]
    reference_label_concerns: list[str]


def _parse_domains(raw: str) -> list[str]:
    if not raw:
        return []
    parts = raw.replace(";", ",").split(",")
    return [p.strip() for p in parts if p.strip()]


def merge_consensus(
    sub_a: ReviewerSubmission,
    sub_b: ReviewerSubmission,
    arbitration: ArbitrationSubmission,
    discrepancy_report: DiscrepancyReport,
) -> SCLEConsensusResult:
    """Build the consensus from two reviews and the arbitration.

    Raises ``ValueError`` if the inputs are for different components, cover
    different pairs, or if a disagreed dimension has no arbitration value.
    """
    if not (
        sub_a.evaluation_component
        == sub_b.evaluation_component
        == arbitration.evaluation_component
    ):
        raise ValueError("component mismatch among the two reviews and arbitration.")

    dimensions = report_dimensions(sub_a, sub_b)
    a_by_pair = {a.pair_id: a for a in sub_a.assessments}
    b_by_pair = {b.pair_id: b for b in sub_b.assessments}
    arb_by_pair = {d.pair_id: d for d in arbitration.decisions}
    if set(a_by_pair) != set(b_by_pair):
        raise ValueError("the two reviews cover different pairs.")

    consensus: list[ConsensusAssessment] = []
    concerns: list[str] = []

    for pair_id in sorted(a_by_pair):
        a = a_by_pair[pair_id]
        b = b_by_pair[pair_id]
        # Arbitration only covers the structurally-disagreeing pairs; an
        # agreement pair simply has no decision.
        decision = arb_by_pair.get(pair_id)

        resolved: dict[str, str | None] = {}
        domains: list[str] = []
        stages: list[str] = []
        sources: dict[str, str] = {}
        for field in dimensions:
            va = _dimension_value(a, field)  # None or string
            vb = _dimension_value(b, field)
            if va == vb:
                value = va
                sources[field] = _SOURCE_AGREEMENT
            else:
                if decision is None or field not in decision.consensus_dimensions:
                    raise ValueError(
                        f"pair {pair_id!r}: reviewers disagree on {field!r} "
                        f"({va!r} vs {vb!r}) but arbitration provides no "
                        f"consensus value (is it in the arbitration workbook?)."
                    )
                value = decision.consensus_dimensions[field]
                sources[field] = _SOURCE_ARBITRATION
            stage = error_stage_name(field)
            if stage is not None:
                if value == "yes":
                    stages.append(stage)
            elif field == "implicated_feature_domains":
                domains = _parse_domains(value or "")
            else:
                resolved[field] = value

        assessment = ConsensusAssessment(
            pair_id=pair_id,
            case_difficulty=resolved["case_difficulty"],
            reference_label_issue=resolved["reference_label_issue"],
            never_event=resolved["never_event"],
            unexpected_method_behaviour=resolved["unexpected_method_behaviour"],
            incorrect_or_imprecise_input_data=resolved[
                "incorrect_or_imprecise_input_data"
            ],
            error_stages=stages,
            implicated_feature_domains=domains,
            dimension_sources=sources,
            reviewer_free_texts={
                a.reviewer_id: a.free_text_assessment,
                b.reviewer_id: b.free_text_assessment,
            },
            consensus_free_text=decision.consensus_free_text if decision else "",
            free_text_materially_different=(
                decision.free_text_materially_different if decision else False
            ),
            arbiter_rationale=decision.arbiter_rationale if decision else "",
            arbitrated=decision is not None,
        )
        consensus.append(assessment)
        if assessment.reference_label_issue not in (None, "no_apparent_issue"):
            concerns.append(pair_id)

    return SCLEConsensusResult(
        evaluation_component=sub_a.evaluation_component,
        reviewer_submissions=[sub_a, sub_b],
        submission_checksums={
            sub_a.reviewer_id: sha256_canonical_json(sub_a),
            sub_b.reviewer_id: sha256_canonical_json(sub_b),
        },
        discrepancy_report=discrepancy_report,
        arbitration=arbitration,
        consensus=consensus,
        reference_label_concerns=concerns,
    )


__all__ = [
    "ConsensusAssessment",
    "SCLEConsensusResult",
    "merge_consensus",
]
