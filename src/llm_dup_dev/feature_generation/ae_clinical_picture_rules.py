"""Deterministic AE clinical-picture inconsistency assessment.

A non-LLM fallback for organisations/settings where LLM use is not
possible. Mimics the spirit of the LLM AE clinical-picture prompt but
uses the primary MedDRA hierarchy (PT / primary HLT / primary HLGT /
primary SOC) alone, so the result is deterministic and auditable.

The assessor produces the same :class:`AEClinicalPictureResult` shape
as the LLM task so it is a drop-in alternative for the pipeline. Step
1 asks whether the two reports share any clinical core (most-granular
hierarchy overlap, including Investigations PTs). Step 2 asks how much
additional non-overlapping AE burden each report carries against the
other (counted bidirectionally so two-sided burden naturally outweighs
a simple subset relation). Investigations SOC PTs are excluded from
Step 2 to avoid penalising routine lab-finding coding differences.

When MedDRA hierarchy is missing on every PT in both reports the
assessor returns ``status=NOT_ASSESSED`` with reason
``"missing_hierarchy"`` rather than fabricating a verdict.
"""

from __future__ import annotations

from llm_dup_dev.models.enums import (
    AEClinicalPictureStatus,
    InconsistencySeverity,
)
from llm_dup_dev.models.pair_features import (
    AEClinicalPictureCounts,
    AEClinicalPictureResult,
    GlobalOverlapLevel,
    Inconsistency,
    RuleBasedAEClinicalPictureDetails,
)
from llm_dup_dev.models.report import AdverseEvent, Report

# Provenance string for every Inconsistency emitted by this assessor.
# Mirrors the LLM source label so downstream filters can distinguish
# the two assessors.
RULE_SOURCE_LABEL = "rule_ae_clinical_picture"

# Canonical SOC name treated as "Investigations". Compared case-folded.
INVESTIGATIONS_SOC_NAME = "investigations"


def _norm(value: str | None) -> str | None:
    """Case-fold a hierarchy/PT string, treating empty as missing."""
    if value is None:
        return None
    folded = value.casefold().strip()
    return folded or None


def _is_investigations(ae: AdverseEvent) -> bool:
    soc = _norm(ae.primary_soc_name)
    return soc == INVESTIGATIONS_SOC_NAME


def _has_any_hierarchy(ae: AdverseEvent) -> bool:
    return any(
        _norm(v) is not None
        for v in (ae.primary_hlt_name, ae.primary_hlgt_name, ae.primary_soc_name)
    )


def _step1_overlap(
    aes1: list[AdverseEvent],
    aes2: list[AdverseEvent],
) -> tuple[GlobalOverlapLevel, InconsistencySeverity | None]:
    """Find the most granular MedDRA-hierarchy overlap between two AE sets.

    Returns the overlap level and the corresponding inconsistency
    severity. ``severity is None`` when an exact PT match was found.
    Empty hierarchy values never match (they're treated as "no info" —
    not as a wildcard).
    """
    pts1 = {_norm(ae.pt_name) for ae in aes1 if _norm(ae.pt_name)}
    pts2 = {_norm(ae.pt_name) for ae in aes2 if _norm(ae.pt_name)}
    if pts1 & pts2:
        return GlobalOverlapLevel.PT, None

    hlt1 = {_norm(ae.primary_hlt_name) for ae in aes1 if _norm(ae.primary_hlt_name)}
    hlt2 = {_norm(ae.primary_hlt_name) for ae in aes2 if _norm(ae.primary_hlt_name)}
    if hlt1 & hlt2:
        return GlobalOverlapLevel.HLT, InconsistencySeverity.MINOR

    hlgt1 = {_norm(ae.primary_hlgt_name) for ae in aes1 if _norm(ae.primary_hlgt_name)}
    hlgt2 = {_norm(ae.primary_hlgt_name) for ae in aes2 if _norm(ae.primary_hlgt_name)}
    if hlgt1 & hlgt2:
        return GlobalOverlapLevel.HLGT, InconsistencySeverity.MODERATE

    soc1 = {_norm(ae.primary_soc_name) for ae in aes1 if _norm(ae.primary_soc_name)}
    soc2 = {_norm(ae.primary_soc_name) for ae in aes2 if _norm(ae.primary_soc_name)}
    if soc1 & soc2:
        return GlobalOverlapLevel.SOC, InconsistencySeverity.STRONG

    return GlobalOverlapLevel.NONE, InconsistencySeverity.DECISIVE


def _step2_directional(
    source: list[AdverseEvent],
    target: list[AdverseEvent],
) -> tuple[int, int, list[str], InconsistencySeverity | None]:
    """Compute directional unmatched-burden counts.

    Investigations SOC PTs are filtered out of both source and target
    before counting (see module docstring). Returns
    ``(U, M, unmatched_pts, severity)`` where ``severity is None`` means
    no inconsistency.

    Counting rules — intentional:
    * exact PT match → ``M``
    * else, source HLT present in target → ``M`` (clinically compatible
      coding difference; do not over-count as unmatched)
    * else, source HLT non-empty and unmatched in target → ``U``
    * else (source HLT missing) → ``M`` (cannot claim "unmatched" without
      hierarchy info on the source PT — fail-safe to compatible)
    """
    src = [ae for ae in source if not _is_investigations(ae)]
    tgt = [ae for ae in target if not _is_investigations(ae)]

    target_pts = {_norm(ae.pt_name) for ae in tgt if _norm(ae.pt_name)}
    target_hlts = {_norm(ae.primary_hlt_name) for ae in tgt if _norm(ae.primary_hlt_name)}

    u = 0
    m = 0
    unmatched_pts: list[str] = []
    for ae in src:
        pt = _norm(ae.pt_name)
        if pt is None:
            continue
        if pt in target_pts:
            m += 1
            continue
        hlt = _norm(ae.primary_hlt_name)
        if hlt is not None and hlt not in target_hlts:
            u += 1
            unmatched_pts.append(ae.pt_name)
        else:
            m += 1

    if u == 0:
        return u, m, unmatched_pts, None
    if u == 1:
        return u, m, unmatched_pts, InconsistencySeverity.MINOR
    if (u + m) > 0 and (u / (u + m)) < 0.5:
        return u, m, unmatched_pts, InconsistencySeverity.MINOR
    return u, m, unmatched_pts, InconsistencySeverity.MODERATE


def _bucket(severity: InconsistencySeverity | None, counts: AEClinicalPictureCounts) -> None:
    if severity == InconsistencySeverity.MINOR:
        counts.n_minor += 1
    elif severity == InconsistencySeverity.MODERATE:
        counts.n_moderate += 1
    elif severity == InconsistencySeverity.STRONG:
        counts.n_strong += 1
    elif severity == InconsistencySeverity.DECISIVE:
        counts.n_decisive += 1


def _missing_hierarchy_warnings(report: Report) -> list[str]:
    return [
        f"{report.report_id}:{ae.pt_name}"
        for ae in report.adverse_events
        if not _has_any_hierarchy(ae)
    ]


def assess_ae_clinical_picture_rule_based(
    r1: Report,
    r2: Report,
    *,
    fallback_from_llm: bool = False,
) -> AEClinicalPictureResult:
    """Deterministic AE clinical-picture inconsistency assessment.

    Parameters
    ----------
    r1, r2 : Report
        The two reports to compare.
    fallback_from_llm : bool, optional
        ``True`` when this assessor is being invoked as a fallback after
        an LLM attempt (no client / LLM error). Affects the ``reason``
        field on the returned result so downstream code and human review
        can tell the two paths apart.

    Returns
    -------
    AEClinicalPictureResult
        ``status=ASSESSED`` with severity-bucketed counts, unified
        :class:`Inconsistency` findings, and a populated
        ``rule_based_details`` audit block. ``status=NOT_ASSESSED`` with
        ``reason="missing_hierarchy"`` when no PT on either report has
        any MedDRA hierarchy info.
    """
    aes1 = list(r1.adverse_events)
    aes2 = list(r2.adverse_events)

    warnings = _missing_hierarchy_warnings(r1) + _missing_hierarchy_warnings(r2)

    any_hierarchy = any(_has_any_hierarchy(ae) for ae in aes1 + aes2)
    if not any_hierarchy:
        return AEClinicalPictureResult(
            status=AEClinicalPictureStatus.NOT_ASSESSED,
            reason="missing_hierarchy",
            method="rule_based",
            rule_based_details=RuleBasedAEClinicalPictureDetails(
                step1_overlap_level=GlobalOverlapLevel.NONE,
                step1_severity=None,
                missing_hierarchy_warnings=warnings,
            ),
        )

    # Step 1 — symmetric, includes Investigations PTs.
    step1_level, step1_severity = _step1_overlap(aes1, aes2)

    # Step 2 — directional, excludes Investigations PTs.
    u_12, m_12, unmatched_12, sev_12 = _step2_directional(aes1, aes2)
    u_21, m_21, unmatched_21, sev_21 = _step2_directional(aes2, aes1)

    counts = AEClinicalPictureCounts()
    findings: list[Inconsistency] = []

    if step1_severity is not None:
        if step1_level == GlobalOverlapLevel.NONE:
            feature = "ae_clinical_picture:global_picture_mismatch"
            description = "No shared primary SOC across reported AEs."
        else:
            feature = f"ae_clinical_picture:hierarchy_overlap:{step1_level.value}"
            description = (
                f"Most granular shared MedDRA level: {step1_level.value.upper()}."
            )
        findings.append(
            Inconsistency(
                feature=feature,
                severity=step1_severity,
                description=description,
                source=RULE_SOURCE_LABEL,
                kind="global_picture_overlap",
            )
        )
        _bucket(step1_severity, counts)

    if sev_12 is not None:
        findings.append(
            Inconsistency(
                feature="ae_clinical_picture:unmatched_burden:r1_to_r2",
                severity=sev_12,
                description=(
                    f"R1 carries {u_12} non-Investigations PT(s) without an "
                    f"HLT-level match in R2 (M={m_12})."
                ),
                source=RULE_SOURCE_LABEL,
                kind="unmatched_burden",
                terms=list(unmatched_12),
            )
        )
        _bucket(sev_12, counts)

    if sev_21 is not None:
        findings.append(
            Inconsistency(
                feature="ae_clinical_picture:unmatched_burden:r2_to_r1",
                severity=sev_21,
                description=(
                    f"R2 carries {u_21} non-Investigations PT(s) without an "
                    f"HLT-level match in R1 (M={m_21})."
                ),
                source=RULE_SOURCE_LABEL,
                kind="unmatched_burden",
                terms=list(unmatched_21),
            )
        )
        _bucket(sev_21, counts)

    details = RuleBasedAEClinicalPictureDetails(
        step1_overlap_level=step1_level,
        step1_severity=step1_severity,
        step2_r1_to_r2_u=u_12,
        step2_r1_to_r2_m=m_12,
        step2_r1_to_r2_severity=sev_12,
        step2_r2_to_r1_u=u_21,
        step2_r2_to_r1_m=m_21,
        step2_r2_to_r1_severity=sev_21,
        investigations_excluded_step2=True,
        missing_hierarchy_warnings=warnings,
    )

    return AEClinicalPictureResult(
        status=AEClinicalPictureStatus.ASSESSED,
        reason="llm_fallback_to_rule" if fallback_from_llm else "rule_based",
        method="rule_based",
        counts=counts,
        findings=findings,
        rule_based_details=details,
    )
