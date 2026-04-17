"""AE clinical-picture inconsistency assessment — LLM-based task.

Identifies *semantic* differences in the adverse-event pictures of two
pharmacovigilance reports (i.e. clinically meaningful AE-set
discrepancies apart from chronology). Chronological / onset-date
inconsistencies are handled by the rule-based
``feature_generation.clinical_date_inconsistencies`` module and are
explicitly out of scope here.

Output shape
------------
The LLM emits a list of distinct findings rather than a single global
label. Each finding carries a kind, a severity (minor / moderate /
strong / decisive), a short rationale, and the AE terms involved. The
task post-processes the list with :func:`collapse_subordinate_findings`
to suppress lower-severity findings that are already accounted for by a
decisive global mismatch, then bucketed counts and unified
:class:`Inconsistency` entries are returned for the pipeline to merge
into ``PairFeatures``.

No-LLM behaviour
----------------
This module never falls back to a heuristic semantic check. When called
through the pipeline without a configured LLM client, the upstream
caller (:class:`PipelineRunner`) is expected to skip invocation and
record :class:`AEClinicalPictureStatus.NOT_ASSESSED` with reason
``"no_llm_client"``. If invoked here, a trivially-matched short-circuit
is the only branch that returns ``NOT_ASSESSED``.

Trivially-matched short-circuit
-------------------------------
When the two reports' AE PT sets are exactly equal (case-folded /
whitespace-normalised) and *both* narratives are empty, no LLM call is
made: the LLM has no information that would let it find a discrepancy,
so the result is ``NOT_ASSESSED`` with reason ``"trivially_matched"``.
This is deliberately distinct from "assessed and found nothing".

Usage::

    from llm_dup_dev.llm.tasks.ae_clinical_picture import (
        assess_ae_clinical_picture,
    )

    result = assess_ae_clinical_picture(
        llm_client=client,
        pair_id="R001__R002",
        r1=report_1,
        r2=report_2,
    )
    print(result.status)         # AEClinicalPictureStatus.ASSESSED
    print(result.counts.n_minor) # 1
    print(len(result.findings))  # 1
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from llm_dup_dev.models.enums import (
    AEClinicalPictureStatus,
    InconsistencySeverity,
)
from llm_dup_dev.models.pair_features import (
    AEClinicalPictureCounts,
    AEClinicalPictureResult,
    Inconsistency,
)
from llm_dup_dev.models.report import Report

from ..models import LLMRequest, LLMResponse
from ..protocols import LLMClient

# Provenance string used on every Inconsistency emitted by this task.
SOURCE_LABEL = "llm_ae_clinical_picture"

# Reason code used for the suppressed_by field when a finding is
# subsumed by a decisive global mismatch.
SUPPRESSED_BY_DECISIVE_GLOBAL = "decisive_global_mismatch"


# ------------------------------------------------------------------
# Structured output schema (LLM-facing)
# ------------------------------------------------------------------


class AEInconsistencyKind(str, Enum):
    """Kind of AE clinical-picture inconsistency finding.

    Values
    ------
    UNMATCHED_CLUSTER : a non-redundant cluster of one or more AEs
        present on one report but not on the other (or vice versa)
        that constitutes a clinically distinct picture component.
    MATCHED_CHARACTERIZATION : the two reports share a matching AE
        but characterise it in clinically inconsistent ways (e.g.
        severity, type) that go beyond mere wording differences.
    GLOBAL_PICTURE_MISMATCH : the overall AE pictures of the two
        reports are clearly different at the case level. When emitted
        with severity DECISIVE, all other findings are suppressed
        from the severity counts.
    """

    UNMATCHED_CLUSTER = "unmatched_cluster"
    MATCHED_CHARACTERIZATION = "matched_characterization"
    GLOBAL_PICTURE_MISMATCH = "global_picture_mismatch"


class AEInconsistencyFindingLLM(BaseModel):
    """One AE clinical-picture inconsistency finding produced by the LLM."""

    kind: AEInconsistencyKind = Field(
        description=(
            "Kind of finding: 'unmatched_cluster' for a clinically "
            "distinct cluster of AEs present on only one report; "
            "'matched_characterization' for a meaningful characterisation "
            "difference of an AE that appears on both reports; "
            "'global_picture_mismatch' for an overall picture-level "
            "discrepancy. Reserve 'global_picture_mismatch' for cases "
            "where the two reports' AE pictures are clearly different "
            "at the case level."
        ),
    )
    severity: InconsistencySeverity = Field(
        description=(
            "Severity per the rubric: 'minor' for small, peripheral, "
            "or compatible-but-broader differences; 'moderate' for "
            "clinically meaningful non-redundant differences that do "
            "not dominate the picture; 'strong' for substantial "
            "differences that materially weaken same-case "
            "interpretation; 'decisive' only when there is no shared "
            "or clinically compatible AE overlap and the AE pictures "
            "are clearly different."
        ),
    )
    rationale: str = Field(
        default="",
        description=(
            "Short clinical rationale for the finding (one or two "
            "sentences). Intended for human review."
        ),
    )
    ae_terms: list[str] = Field(
        default_factory=list,
        description=(
            "AE Preferred Terms involved in this finding (from either "
            "report). For an unmatched cluster, list the cluster's "
            "AEs. For a global mismatch, list representative terms."
        ),
    )
    cluster_label: str = Field(
        default="",
        description=(
            "Optional short human-readable label for the AE cluster "
            "(e.g. 'cutaneous syndrome', 'hepatic injury'). Empty "
            "when not applicable."
        ),
    )


class AEClinicalPictureVerdict(BaseModel):
    """Structured LLM output for AE clinical-picture inconsistency assessment."""

    findings: list[AEInconsistencyFindingLLM] = Field(
        default_factory=list,
        description=(
            "Distinct, clinically non-redundant inconsistency findings. "
            "Group closely related unmatched AEs into one finding. Do "
            "not produce one finding per PT in long AE lists. Return "
            "an empty list when the two AE pictures are clinically "
            "compatible."
        ),
    )
    reasoning: str = Field(
        default="",
        description=(
            "Brief overall summary of the AE clinical-picture comparison. "
            "Intended for human review, not downstream logic."
        ),
    )


# ------------------------------------------------------------------
# Prompt templates
# ------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a pharmacovigilance expert assessing whether two adverse event \
reports describe clinically inconsistent adverse-event pictures.

Scope
-----
- Focus exclusively on the *clinical picture* of the adverse events: \
which AEs are present, whether they describe the same syndrome, and \
whether their characterisation is consistent.
- Do NOT focus on chronology, dates, onset timing, or temporal \
ordering. Date-based inconsistencies are handled by a separate \
rule-based module and are out of scope for your assessment.
- Do NOT comment on demographics, drugs, or narrative-only attributes \
unless they directly characterise an adverse event.

Severity rubric (use exactly these levels)
------------------------------------------
- minor: small, peripheral, or limited clinical-picture difference. \
Often reflects partial overlap, slightly different framing, or one \
report listing some extra clinically compatible terms. Should not \
carry major weight against duplication by itself.
- moderate: clinically meaningful non-redundant difference beyond a \
minor discrepancy. Still not enough to amount to a major \
incompatibility. Use for distinct but not dominant discrepancies, \
especially when there is still substantial AE overlap.
- strong: substantial AE-picture difference that materially weakens \
same-case interpretation. Use when the mismatch is clearly more than \
minor or moderate, but not so globally incompatible that it should \
be decisive. Typical shape: little or no PT-level overlap together \
with a large severity, depth, or organ-system-reach gap, while the \
two pictures still sit within a shared broad mechanism that keeps \
them from being decisive.
- decisive: no shared or clinically compatible AEs and clearly \
different AE pictures at the case level. Use only when the difference \
is clearly positive and clinically meaningful, not merely because the \
reports are sparse, vague, or incompletely coded.

Calibration guidance
--------------------
- Wording or coding-granularity differences for the same underlying \
AE produce NO finding (e.g. nominally different terms that describe \
the same clinical entity, or the same syndrome described by a single \
broader term on one report and by its component PTs on the other).
- Broader-but-compatible differences and partial decompositions of \
the same syndrome should generally produce at most a minor finding.
- When both reports share a substantial AE core but each contains a \
distinct clinically meaningful additional AE cluster of a different \
type, emit one moderate finding per such cluster. Do not collapse \
this into a single strong finding unless the divergence dominates \
the picture.
- Reserve a decisive global picture mismatch for cases where there \
is no shared or clinically compatible AE overlap and the two \
pictures are clearly different at the case level. This applies \
even when each report contains only one or a few AEs, provided \
each AE is clearly identifiable and clinically unrelated to \
anything in the other report — short but well-defined distinct \
pictures are the prototypical decisive case, not a reason to \
downgrade to moderate.
- When the two pictures share a broad mechanism or pathophysiology \
family (e.g. both hepatic, both cardiac, both haematologic) but \
have no PT-level overlap AND differ materially in severity, depth, \
or organ-system reach, use strong. An illustrative pattern: acute \
hepatic failure on one side vs. isolated transaminases increased \
on the other — both hepatic, so not decisive, but the severity \
and character gap is large enough that calling it merely moderate \
understates how much the mismatch weakens same-case \
interpretation. Shared mechanism prevents decisive; it does not \
by itself justify moderate when the specific clinical pictures \
are substantially different.
- Treat one-sided additional AE content (one report's AEs are largely \
a subset of the other's) more leniently than bidirectional \
non-trivial divergence: sparser coding or partial capture is a \
plausible benign explanation. This leniency is intended for cases \
where sparse coding could plausibly reflect incomplete capture of \
the same underlying event; it does not apply when each report \
contains a clear, well-defined AE that is clinically unrelated to \
anything in the other report, nor when the reports' AEs differ \
materially in severity or organ-system reach even within a shared \
mechanism family.

Anti-overcounting principles
----------------------------
- Count distinct, clinically non-redundant findings, not PTs one by one.
- Group closely related unmatched AEs into a single finding where \
appropriate (e.g. urticaria + pruritus + rash → one cutaneous-syndrome \
finding, not three).
- In long AE lists with broad overlap, a few extra unmatched AEs \
should usually produce no finding or only a minor finding, especially \
when they appear peripheral or look like background/history coding.
- Do not stack a cluster-level finding and a broader summary finding \
that reflect the same underlying issue.
- If you emit a decisive global picture mismatch, do not also emit \
subordinate lower-severity findings describing the same contradiction \
— a downstream step will suppress them automatically, but emitting \
them clutters review.

Output
------
Return a structured JSON object matching the AEClinicalPictureVerdict \
schema. Return an empty findings list when the two AE pictures are \
clinically compatible.\
"""

_USER_PROMPT_TEMPLATE = """\
## Report 1 — adverse events (MedDRA Preferred Terms)
{ae_block_1}

## Report 2 — adverse events (MedDRA Preferred Terms)
{ae_block_2}

## Report 1 — narrative
{narrative_block_1}

## Report 2 — narrative
{narrative_block_2}

Assess AE clinical-picture inconsistency between the two reports per \
the system instructions. Ignore chronology and dates. Return a \
structured JSON list of distinct, clinically non-redundant findings.\
"""

_MISSING = "(none reported)"
_MISSING_NARRATIVE = "(no narrative available)"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _normalize_pt(pt_name: str) -> str:
    """Case-fold + whitespace-trim a PT for the trivially-matched check.

    Intentionally minimal — no MedDRA hierarchy, no synonym lookup.
    Mirrors :func:`llm_dup_dev.review_html.normalize.normalize_ae_key`.
    """
    return pt_name.strip().lower()


def _ae_pt_set(report: Report) -> frozenset[str]:
    return frozenset(_normalize_pt(ae.pt_name) for ae in report.adverse_events)


def _has_text(value: str | None) -> bool:
    return bool(value and value.strip())


def _format_ae_block(report: Report) -> str:
    if not report.adverse_events:
        return _MISSING
    return "\n".join(f"- {ae.pt_name}" for ae in report.adverse_events)


def _format_narrative_block(narrative: str | None) -> str:
    if not _has_text(narrative):
        return _MISSING_NARRATIVE
    return narrative  # type: ignore[return-value]


# ------------------------------------------------------------------
# Build LLM request
# ------------------------------------------------------------------


def build_ae_clinical_picture_request(
    r1: Report,
    r2: Report,
    *,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> LLMRequest:
    """Build an :class:`LLMRequest` for AE clinical-picture assessment.

    Pass-through: AE lists are not truncated. If a token guardrail
    later proves necessary, target the narratives, not the AE lists.
    """
    user_prompt = _USER_PROMPT_TEMPLATE.format(
        ae_block_1=_format_ae_block(r1),
        ae_block_2=_format_ae_block(r2),
        narrative_block_1=_format_narrative_block(r1.narrative),
        narrative_block_2=_format_narrative_block(r2.narrative),
    )
    return LLMRequest(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        response_model=AEClinicalPictureVerdict,
        temperature=temperature,
        max_tokens=max_tokens,
    )


# ------------------------------------------------------------------
# Dedup / suppression
# ------------------------------------------------------------------


def collapse_subordinate_findings(
    findings: list[AEInconsistencyFindingLLM],
) -> tuple[list[AEInconsistencyFindingLLM], list[AEInconsistencyFindingLLM]]:
    """Suppress findings subordinate to a decisive global picture mismatch.

    Rule
    ----
    If any finding has ``kind == GLOBAL_PICTURE_MISMATCH`` and
    ``severity == DECISIVE``, *all other findings* are suppressed from
    the kept set: the first such decisive global mismatch is kept, and
    every other finding (including additional decisive globals) is
    moved to the suppressed set.

    Suppression affects which findings count toward the severity
    buckets; the suppressed entries are still preserved in the master
    inconsistency list (with ``suppressed=True``) so human review can
    see the LLM's full reasoning.

    Returns
    -------
    tuple[list, list]
        (kept_for_counts, suppressed)
    """
    decisive_global_indices = [
        i
        for i, f in enumerate(findings)
        if f.kind == AEInconsistencyKind.GLOBAL_PICTURE_MISMATCH
        and f.severity == InconsistencySeverity.DECISIVE
    ]

    if not decisive_global_indices:
        return list(findings), []

    keep_index = decisive_global_indices[0]
    kept = [findings[keep_index]]
    suppressed = [f for i, f in enumerate(findings) if i != keep_index]
    return kept, suppressed


# ------------------------------------------------------------------
# Assessment orchestration
# ------------------------------------------------------------------


def _verdict_from_response(
    pair_id: str, response: LLMResponse
) -> AEClinicalPictureVerdict:
    """Coerce the parsed LLM response into an AEClinicalPictureVerdict."""
    if response.parsed is None:
        raise ValueError(
            f"AE clinical-picture assessment for pair {pair_id} failed: "
            f"LLM did not return structured output. Raw text: "
            f"{response.raw_text!r}"
        )
    if isinstance(response.parsed, AEClinicalPictureVerdict):
        return response.parsed
    if isinstance(response.parsed, dict):
        return AEClinicalPictureVerdict.model_validate(response.parsed)
    raise ValueError(
        f"AE clinical-picture assessment for pair {pair_id}: unexpected "
        f"parsed type {type(response.parsed).__name__}. Expected "
        f"AEClinicalPictureVerdict."
    )


def _llm_finding_to_inconsistency(
    finding: AEInconsistencyFindingLLM,
    *,
    suppressed: bool = False,
    suppressed_by: str = "",
) -> Inconsistency:
    """Translate an LLM finding into a unified :class:`Inconsistency` entry."""
    feature = (
        f"ae_clinical_picture:{finding.kind.value}"
        + (f":{finding.cluster_label}" if finding.cluster_label else "")
    )
    return Inconsistency(
        feature=feature,
        severity=finding.severity,
        description=finding.rationale or None,
        source=SOURCE_LABEL,
        kind=finding.kind.value,
        rationale=finding.rationale,
        terms=list(finding.ae_terms),
        cluster_label=finding.cluster_label,
        suppressed=suppressed,
        suppressed_by=suppressed_by,
    )


def _bucket_counts(
    findings: list[AEInconsistencyFindingLLM],
) -> AEClinicalPictureCounts:
    """Bucket *kept* (un-suppressed) findings by severity."""
    counts = AEClinicalPictureCounts()
    for f in findings:
        if f.severity == InconsistencySeverity.MINOR:
            counts.n_minor += 1
        elif f.severity == InconsistencySeverity.MODERATE:
            counts.n_moderate += 1
        elif f.severity == InconsistencySeverity.STRONG:
            counts.n_strong += 1
        elif f.severity == InconsistencySeverity.DECISIVE:
            counts.n_decisive += 1
    return counts


def _trivially_matched(r1: Report, r2: Report) -> bool:
    """True when AE PT sets are equal AND both narratives are empty.

    In that case the LLM has no information beyond what already matches
    perfectly, so calling it cannot produce a finding.
    """
    if _has_text(r1.narrative) or _has_text(r2.narrative):
        return False
    return _ae_pt_set(r1) == _ae_pt_set(r2)


def _build_result_from_verdict(
    verdict: AEClinicalPictureVerdict,
) -> AEClinicalPictureResult:
    """Apply dedup, build counts, and translate to unified findings."""
    kept, suppressed = collapse_subordinate_findings(verdict.findings)
    counts = _bucket_counts(kept)

    findings: list[Inconsistency] = []
    findings.extend(_llm_finding_to_inconsistency(f) for f in kept)
    findings.extend(
        _llm_finding_to_inconsistency(
            f,
            suppressed=True,
            suppressed_by=SUPPRESSED_BY_DECISIVE_GLOBAL,
        )
        for f in suppressed
    )

    return AEClinicalPictureResult(
        status=AEClinicalPictureStatus.ASSESSED,
        reason="",
        counts=counts,
        findings=findings,
        reasoning=verdict.reasoning,
    )


def assess_ae_clinical_picture(
    llm_client: LLMClient,
    pair_id: str,
    r1: Report,
    r2: Report,
    *,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> AEClinicalPictureResult:
    """Assess AE clinical-picture inconsistency between two reports.

    Returns
    -------
    AEClinicalPictureResult
        ``status=ASSESSED`` with bucketed counts and unified
        :class:`Inconsistency` findings (suppressed entries included)
        when the LLM was called and produced a parseable verdict.
        ``status=NOT_ASSESSED`` with reason ``"trivially_matched"``
        when the AE PT sets are equal and both narratives are empty
        (no LLM call is made in that case).

    Raises
    ------
    ValueError
        If the LLM returns no parseable structured output. Callers
        (typically :class:`PipelineRunner`) are expected to catch this
        and downgrade to ``NOT_ASSESSED(reason="llm_error")``.
    """
    if _trivially_matched(r1, r2):
        return AEClinicalPictureResult(
            status=AEClinicalPictureStatus.NOT_ASSESSED,
            reason="trivially_matched",
        )

    request = build_ae_clinical_picture_request(
        r1, r2, temperature=temperature, max_tokens=max_tokens
    )
    response = llm_client.generate(request)
    verdict = _verdict_from_response(pair_id, response)
    return _build_result_from_verdict(verdict)
