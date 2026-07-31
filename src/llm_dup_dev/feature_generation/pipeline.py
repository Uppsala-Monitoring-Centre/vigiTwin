"""Feature generation pipeline — orchestrates all steps.

Combines matching, reporting context, anchor selection, probability
estimation, and inconsistency detection into a single PairFeatures
computation.

See: docs/data_contracts.md § PairFeatures Layer
"""

from __future__ import annotations

from llm_dup_dev.classifier.config import PipelineConfig
from llm_dup_dev.feature_generation.anchor import select_anchor
from llm_dup_dev.feature_generation.clinical_date_inconsistencies import (
    compute_clinical_date_inconsistencies,
)
from llm_dup_dev.feature_generation.inconsistencies import detect_inconsistencies
from llm_dup_dev.feature_generation.matching import MatchResult, compute_matches
from llm_dup_dev.feature_generation.probability import (
    FrequencyStats,
    compute_probability,
)
from llm_dup_dev.feature_generation.source_case_inconsistency import (
    source_case_findings,
)
from llm_dup_dev.feature_generation.reporting_context import (
    determine_reporting_context,
)
from llm_dup_dev.models.enums import (
    AEClinicalPictureStatus,
    InconsistencySeverity,
)
from llm_dup_dev.models.narrative_source_case import SourceCaseAssessment
from llm_dup_dev.models.pair_features import (
    AEClinicalPictureResult,
    ClinicalDateFindingSummary,
    ClinicalDateInconsistencyCounts,
    EvidenceSummary,
    InconsistencyCounts,
    PairFeatures,
)
from llm_dup_dev.models.report import Report


def generate_pair_features(
    r1: Report,
    r2: Report,
    stats: FrequencyStats,
    config: PipelineConfig | None = None,
    pair_id: str | None = None,
    ae_clinical_picture: AEClinicalPictureResult | None = None,
    source_case_assessment: SourceCaseAssessment | None = None,
) -> PairFeatures:
    """Generate all features for a report pair.

    This is the main entry point for the feature generation pipeline.
    It orchestrates:
        1. Reporting context determination
        2. Feature matching (drugs, AEs, demographics)
        3. Anchor population selection
        4. Probability estimation (p_joint, P_match, L_match)
        5. Inconsistency detection
        6. Evidence summary construction

    Parameters
    ----------
    r1, r2 : Report
        The two reports to compare.
    stats : FrequencyStats
        Pre-computed database frequency statistics for the shared
        clinical context. Must be provided by the secure repo.
    config : PipelineConfig, optional
        Pipeline configuration. Uses defaults if not provided.
    pair_id : str, optional
        Explicit pair ID. If not provided, constructed as
        ``{r1.report_id}__{r2.report_id}``.
    ae_clinical_picture : AEClinicalPictureResult, optional
        Result of the LLM-based AE clinical-picture inconsistency
        assessment (computed externally by the orchestrator). When
        ``None``, defaults to a NOT_ASSESSED result with reason
        ``"no_llm_client"``: no findings are appended and the AE
        component contributes zero to the merged severity counts.
    source_case_assessment : SourceCaseAssessment, optional
        Pair-level deterministic verdict from two per-report narrative
        extractions (see
        :mod:`llm_dup_dev.feature_generation.source_case_comparison`).
        When provided, synthesized inconsistency rows are merged into
        the master list the same way AE findings are and the raw
        assessment is stored on ``PairFeatures.source_case_assessment``
        so the decision logic can branch on the verdict directly.
        ``None`` when no extraction/comparison was performed.

    Returns
    -------
    PairFeatures
        Complete feature set for the decision logic.
    """
    if config is None:
        config = PipelineConfig()

    if pair_id is None:
        pair_id = f"{r1.report_id}__{r2.report_id}"

    if ae_clinical_picture is None:
        ae_clinical_picture = AEClinicalPictureResult(
            status=AEClinicalPictureStatus.NOT_ASSESSED,
            reason="no_llm_client",
        )

    # Step 1: Determine reporting context
    reporting_context = determine_reporting_context(r1, r2)

    # Step 2: Compute feature matches
    match_result = compute_matches(r1, r2)

    # Step 3: Select anchor population
    anchor = select_anchor(
        N_drugs=stats.N_drugs,
        N_AEs=stats.N_AEs,
        N_drugs_AEs=stats.N_drugs_AEs,
        N_total=stats.N_total,
        threshold=config.anchor.threshold,
    )

    # Step 4: Compute probability estimates
    prob_result = compute_probability(
        match_result=match_result,
        anchor=anchor,
        stats=stats,
        config=config.domain_sizes,
        reporting_context_level=reporting_context.level,
        epsilon=config.epsilon,
    )

    # Step 5: Detect inconsistencies
    inconsistencies = detect_inconsistencies(
        r1, r2, config.inconsistency_thresholds
    )

    # Step 5b: Clinical-date inconsistencies (rule-based module)
    cdi_result = compute_clinical_date_inconsistencies(r1, r2)
    clinical_date_counts = ClinicalDateInconsistencyCounts(
        n_minor=cdi_result.n_minor,
        n_moderate=cdi_result.n_moderate,
        n_strong=cdi_result.n_strong,
        n_decisive=cdi_result.n_decisive,
    )

    # Append AE clinical-picture findings into the unified master list
    # (suppressed entries included for transparency; they are excluded
    # from counts via the ``not suppressed`` filter below).
    inconsistencies = [*inconsistencies, *ae_clinical_picture.findings]

    # Synthesize narrative source/case findings from the deterministic
    # verdict and merge into the master list before counts are tallied.
    inconsistencies = [
        *inconsistencies,
        *source_case_findings(source_case_assessment),
    ]

    inconsistency_counts = InconsistencyCounts(
        minor=sum(
            1
            for i in inconsistencies
            if i.severity == InconsistencySeverity.MINOR and not i.suppressed
        ) + clinical_date_counts.n_minor,
        moderate=sum(
            1
            for i in inconsistencies
            if i.severity == InconsistencySeverity.MODERATE and not i.suppressed
        ) + clinical_date_counts.n_moderate,
        strong=sum(
            1
            for i in inconsistencies
            if i.severity == InconsistencySeverity.STRONG and not i.suppressed
        ) + clinical_date_counts.n_strong,
        decisive=sum(
            1
            for i in inconsistencies
            if i.severity == InconsistencySeverity.DECISIVE and not i.suppressed
        ) + clinical_date_counts.n_decisive,
    )

    # Step 5c: Build serializable clinical-date finding summaries
    clinical_date_findings = [
        ClinicalDateFindingSummary(
            entity_label=f.entity_label,
            severity=f.final_severity.to_inconsistency_severity(),
            source_date=f.source_date.value,
            matched_date=f.matched_date.value if f.matched_date else "",
            diff_days=f.raw_diff_days,
            downgrades=f.downgrades,
        )
        for f in cdi_result.findings
        if f.selected and f.final_severity.to_inconsistency_severity() is not None
    ]

    # Step 6: Build evidence summary
    evidence_summary = _build_evidence_summary(match_result)

    return PairFeatures(
        pair_id=pair_id,
        feature_version=config.feature_version,
        reporting_context=reporting_context,
        matching_drugs=match_result.matching_drugs,
        matching_AEs=match_result.matching_AEs,
        anchor=anchor,
        probability=prob_result.estimate,
        L_match=prob_result.L_match,
        L_patient=prob_result.L_patient,
        inconsistencies=inconsistencies,
        inconsistency_counts=inconsistency_counts,
        clinical_date_inconsistency_counts=clinical_date_counts,
        clinical_date_findings=clinical_date_findings,
        ae_clinical_picture_status=ae_clinical_picture.status,
        ae_clinical_picture_reason=ae_clinical_picture.reason,
        ae_clinical_picture_method=ae_clinical_picture.method,
        ae_clinical_picture_counts=ae_clinical_picture.counts,
        ae_clinical_picture_reasoning=ae_clinical_picture.reasoning,
        ae_clinical_picture_rule_based_details=(
            ae_clinical_picture.rule_based_details
        ),
        source_case_assessment=source_case_assessment,
        evidence_summary=evidence_summary,
    )


def _build_evidence_summary(match_result: MatchResult) -> EvidenceSummary:
    """Build a diagnostic evidence summary from match results."""
    shared: dict = {
        "drugs": match_result.matching_drugs,
        "adverse_events": match_result.matching_AEs,
        "demographics": [
            {"feature": dm.feature, "matched": dm.matched}
            for dm in match_result.demographic_matches
        ],
    }
    report1_only: dict = {
        "drugs": match_result.drugs_only_r1,
        "adverse_events": match_result.aes_only_r1,
    }
    report2_only: dict = {
        "drugs": match_result.drugs_only_r2,
        "adverse_events": match_result.aes_only_r2,
    }
    return EvidenceSummary(
        shared=shared,
        report1_only=report1_only,
        report2_only=report2_only,
    )
