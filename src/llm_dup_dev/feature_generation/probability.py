"""Probability estimation for the collision framework.

Computes feature-level probabilities, composite matching probability,
expected collisions within the anchor population, and L_match.

The core idea: if the expected number of random report pairs sharing
the observed feature pattern is extremely small, the reports likely
describe the same individual.

See: docs/same_individual_assessment_spec.md § Probability estimation
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field as dc_field

from pydantic import BaseModel

from llm_dup_dev.classifier.config import DomainSizeConfig
from llm_dup_dev.feature_generation.matching import (
    ClinicalDateMatch,
    DemographicMatch,
    MatchResult,
)
from llm_dup_dev.models.enums import AnchorType, ReportingContextLevel
from llm_dup_dev.models.pair_features import (
    AnchorPopulation,
    FeatureContribution,
    ProbabilityEstimate,
)


# ---------------------------------------------------------------------------
# Internal result types
# ---------------------------------------------------------------------------


@dataclass
class FeatureProbability:
    """Probability contribution from a single matched feature."""

    feature: str
    p_f: float
    method: str  # "empirical" or "domain_size"
    domain_size: float | None = None
    description: str = ""


@dataclass
class ProbabilityResult:
    """Complete probability computation result."""

    feature_probabilities: list[FeatureProbability] = dc_field(default_factory=list)
    p_joint: float = 1.0
    expected_collisions: float = 0.0
    L_match: float = 0.0

    @property
    def estimate(self) -> ProbabilityEstimate:
        """Convert to the PairFeatures-level model.

        Carries through per-feature probability contributions for
        debugging and method development.
        """
        return ProbabilityEstimate(
            p_joint=self.p_joint,
            expected_collisions=self.expected_collisions,
            feature_contributions=[
                FeatureContribution(
                    feature=fp.feature,
                    p_f=fp.p_f,
                    method=fp.method,
                    domain_size=fp.domain_size,
                    description=fp.description,
                )
                for fp in self.feature_probabilities
            ],
        )


# ---------------------------------------------------------------------------
# Frequency statistics contract
# ---------------------------------------------------------------------------


class FrequencyStats(BaseModel):
    """Pre-computed database statistics for a report pair's shared clinical context.

    These must be provided by the secure repo's data extraction layer.
    All counts are within the determined reporting context.
    """

    N_total: int  # total reports in the reporting context
    N_drugs: int  # reports containing all matching drugs
    N_AEs: int  # reports containing all matching AEs
    N_drugs_AEs: int  # reports containing both


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Pseudo-count for Bayesian smoothing of anchor-conditional probabilities.
#: Controls how strongly the estimate is pulled toward the prior p0 when
#: the leave-pair-out sample is small. Configurable via
#: ``AnchorConfig.smoothing_pseudocount`` in ``PipelineConfig``.
SMOOTHING_PSEUDOCOUNT: int = 10


# ---------------------------------------------------------------------------
# Main computation
# ---------------------------------------------------------------------------


def compute_probability(
    match_result: MatchResult,
    anchor: AnchorPopulation,
    stats: FrequencyStats,
    config: DomainSizeConfig,
    reporting_context_level: ReportingContextLevel = ReportingContextLevel.GLOBAL,
    epsilon: float = 1e-15,
    smoothing_pseudocount: int = SMOOTHING_PSEUDOCOUNT,
) -> ProbabilityResult:
    """Compute the composite matching probability and L_match.

    Steps:
        1. Compute p_f for each matched feature NOT in the anchor definition.
        2. Multiply to get p_joint.
        3. Compute E_joint = N_anchor*(N_anchor-1)/2 * p_joint.
        4. Compute L_match = max(0, -log10(E_joint + epsilon)).

    Drug/AE set frequencies are excluded when they form part of the
    anchor (to avoid double-counting). When included, they are estimated
    using leave-pair-out Bayesian smoothing (see ``leave_pair_out_probability``).

    Report date is excluded from the probability computation — it is
    matched for evidence display purposes only.

    Parameters
    ----------
    match_result : MatchResult
        Feature matches between the two reports.
    anchor : AnchorPopulation
        Selected anchor population.
    stats : FrequencyStats
        Pre-computed database statistics.
    config : DomainSizeConfig
        Domain size configuration.
    reporting_context_level : ReportingContextLevel
        Reporting context level, used to select the default date domain
        size (365 days for country-year, 3650 days otherwise).
    epsilon : float
        Small constant for numerical stability.
    smoothing_pseudocount : int
        Bayesian pseudo-count m for leave-pair-out smoothing. Defaults to
        ``SMOOTHING_PSEUDOCOUNT`` (10); pass ``config.anchor.smoothing_pseudocount``
        from ``PipelineConfig`` to use the configured value.

    Returns
    -------
    ProbabilityResult
        Full computation result including per-feature breakdown.
    """
    feature_probs: list[FeatureProbability] = []
    is_country_year = reporting_context_level == ReportingContextLevel.COUNTRY_YEAR
    date_span_days = _compute_date_span_days(
        match_result.demographic_matches, match_result.matched_clinical_dates
    )

    # Drug set probability — excluded if anchor uses drugs.
    # When the anchor is AEs, the drug set is an additional feature whose
    # probability is the marginal prevalence in the context with
    # leave-pair-out: (N_drugs - 2) / (N_total - 2).
    if (
        anchor.type not in (AnchorType.DRUGS, AnchorType.DRUGS_AES)
        and match_result.matching_drugs
    ):
        p_drugs = _leave_pair_out_marginal(stats.N_drugs, stats.N_total)
        if p_drugs is not None and p_drugs > 0:
            feature_probs.append(
                FeatureProbability(
                    feature="drug_set",
                    p_f=p_drugs,
                    method="leave_pair_out",
                    description=(
                        f"Drug set: ({stats.N_drugs}-2)/({stats.N_total}-2)"
                    ),
                )
            )

    # AE set probability — excluded if anchor uses AEs.
    # When the anchor is drugs, the AE set is an additional feature whose
    # probability is the marginal prevalence in the context with
    # leave-pair-out: (N_AEs - 2) / (N_total - 2).
    if (
        anchor.type not in (AnchorType.AES, AnchorType.DRUGS_AES)
        and match_result.matching_AEs
    ):
        p_aes = _leave_pair_out_marginal(stats.N_AEs, stats.N_total)
        if p_aes is not None and p_aes > 0:
            feature_probs.append(
                FeatureProbability(
                    feature="ae_set",
                    p_f=p_aes,
                    method="leave_pair_out",
                    description=(
                        f"AE set: ({stats.N_AEs}-2)/({stats.N_total}-2)"
                    ),
                )
            )

    # Demographic feature probabilities (domain-size based).
    # Report date is excluded — it is matched for evidence display only.
    for dm in match_result.demographic_matches:
        if not dm.matched or dm.feature == "report_date":
            continue

        domain_size = _get_domain_size(
            dm, config, is_country_year=is_country_year, span_days=date_span_days
        )
        if domain_size is not None and domain_size > 0:
            p_f = 1.0 / domain_size
            feature_probs.append(
                FeatureProbability(
                    feature=dm.feature,
                    p_f=p_f,
                    method="domain_size",
                    domain_size=domain_size,
                    description=f"{dm.feature}: D_f={domain_size}",
                )
            )

    # Clinical date probabilities (drug start/end, AE onset).
    # Each unique matched date contributes p_f = 1/D_f based on its
    # resolution. Uses explicit date-field coincidence only — NOT
    # interval overlap.
    for cd in match_result.matched_clinical_dates:
        domain_size = _get_clinical_date_domain_size(
            cd, config, is_country_year=is_country_year, span_days=date_span_days
        )
        if domain_size is not None and domain_size > 0:
            p_f = 1.0 / domain_size
            feature_probs.append(
                FeatureProbability(
                    feature="clinical_date",
                    p_f=p_f,
                    method="domain_size",
                    domain_size=domain_size,
                    description=(
                        f"clinical_date {cd.matched_value} "
                        f"({cd.resolution}): D_f={domain_size}"
                    ),
                )
            )

    # Composite probability
    p_joint = 1.0
    for fp in feature_probs:
        p_joint *= fp.p_f

    # Expected collisions (birthday-problem approximation)
    n = anchor.N_anchor
    if n >= 2:
        expected_collisions = (n * (n - 1) / 2) * p_joint
    else:
        expected_collisions = 0.0

    # L_match
    L_match = max(0.0, -math.log10(expected_collisions + epsilon))

    return ProbabilityResult(
        feature_probabilities=feature_probs,
        p_joint=p_joint,
        expected_collisions=expected_collisions,
        L_match=L_match,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _leave_pair_out_marginal(
    n_feature_in_context: int,
    n_total_in_context: int,
) -> float | None:
    """Leave-pair-out marginal prevalence of a feature in the context.

    Computes (N_feature - 2) / (N_total - 2), removing the two focal
    reports from both numerator and denominator.

    Returns None if n_total_in_context <= 2 (cannot compute a meaningful
    frequency).
    """
    denom = n_total_in_context - 2
    if denom <= 0:
        return None
    numer = max(0, n_feature_in_context - 2)
    return numer / denom


def leave_pair_out_probability(
    n_anchor: int,
    k_anchor_and_feature: int,
    n_feature_in_context: int,
    n_total_in_context: int,
    smoothing_pseudocount: int = SMOOTHING_PSEUDOCOUNT,
) -> float | None:
    """Bayesian-smoothed, leave-pair-out conditional probability.

    Estimates P(feature | anchor, context) for a candidate pair, removing
    the two focal reports from the anchor counts to avoid self-inclusion
    bias, then applying Bayesian smoothing toward a prior.

    Formula::

        n_other  = max(0, n_anchor - 2)
        k_other  = max(0, k_anchor_and_feature - 2)
        p0       = n_feature_in_context / n_total_in_context
        p_hat    = (k_other + m * p0) / (n_other + m)

    where m = smoothing_pseudocount (default SMOOTHING_PSEUDOCOUNT = 10).

    Parameters
    ----------
    n_anchor : int
        Number of reports in the relevant context that match the anchor
        (e.g. reports with the matching drug set when anchor is DRUGS).
    k_anchor_and_feature : int
        Number of those anchor reports that *also* have the additional
        feature (e.g. reports with both matching drugs AND matching AEs).
    n_feature_in_context : int
        Number of reports in the relevant context with the additional
        feature, **without** conditioning on the anchor.  Used to compute
        the prior p0.
    n_total_in_context : int
        Total reports in the relevant context.  Used to compute the
        prior p0.
    smoothing_pseudocount : int
        Bayesian pseudo-count m. Defaults to ``SMOOTHING_PSEUDOCOUNT`` (10).
        Configurable via ``AnchorConfig.smoothing_pseudocount`` in
        ``PipelineConfig``.

    Returns
    -------
    float or None
        The smoothed probability estimate, or None if ``n_total_in_context``
        is non-positive (cannot compute a meaningful prior).
    """
    if n_total_in_context <= 0:
        return None

    m = smoothing_pseudocount

    # Leave-pair-out: remove the two focal reports from anchor counts
    n_other = max(0, n_anchor - 2)
    k_other = max(0, k_anchor_and_feature - 2)

    # Prior: marginal prevalence of the feature in the relevant context,
    # NOT conditioned on the anchor.
    p0 = n_feature_in_context / n_total_in_context

    # Bayesian-smoothed estimate
    p_hat = (k_other + m * p0) / (n_other + m)

    return p_hat


def _empirical_frequency(count: int, total: int) -> float | None:
    """General utility: empirical relative frequency, or None if inputs are invalid.

    Not used for drug/AE set probabilities (which use ``leave_pair_out_probability``
    instead), but kept as a utility for any caller that needs a simple ratio.
    """
    if total <= 0 or count < 0:
        return None
    return count / total


def _parse_date_string(val: str) -> "date | None":
    """Parse an ISO date string, padding partial dates to day resolution."""
    from datetime import date as date_type

    try:
        parts = val.split("-")
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 else 1
        return date_type(year, month, day)
    except (ValueError, IndexError):
        return None


def _compute_date_span_days(
    demographic_matches: list[DemographicMatch],
    clinical_dates: list[ClinicalDateMatch] | None = None,
) -> int:
    """Compute the span in days across all matched date values.

    Collects dates from matched demographic date features AND matched
    clinical dates (drug start/end, AE onset), parses them, and returns
    the span from earliest to latest.
    Returns 0 if fewer than two distinct dates are found.
    """
    from datetime import date as date_type

    all_dates: list[date_type] = []

    # Dates from demographic matches (e.g. report_date)
    for dm in demographic_matches:
        if dm.date_resolution is None or not dm.matched:
            continue
        for val in list(dm.values_r1) + list(dm.values_r2):
            if not isinstance(val, str):
                continue
            parsed = _parse_date_string(val)
            if parsed is not None:
                all_dates.append(parsed)

    # Dates from clinical date matches (drug start/end, AE onset)
    if clinical_dates:
        for cd in clinical_dates:
            parsed = _parse_date_string(cd.matched_value)
            if parsed is not None:
                all_dates.append(parsed)

    if len(all_dates) < 2:
        return 0

    return (max(all_dates) - min(all_dates)).days


def _get_domain_size(
    dm: DemographicMatch,
    config: DomainSizeConfig,
    *,
    is_country_year: bool = False,
    span_days: int = 0,
) -> float | None:
    """Look up the domain size D_f for a matched feature.

    For date features, uses the matched resolution and reporting context
    to select the appropriate domain size. The domain may be extended
    if the span of matching dates exceeds the default.
    """
    if dm.date_resolution is not None:
        resolution = dm.date_resolution
        if resolution == "day":
            return config.date_exact(is_country_year, span_days)
        elif resolution == "month":
            return config.date_month(is_country_year, span_days)
        elif resolution == "year":
            return config.date_year(is_country_year, span_days)
        return config.date_exact(is_country_year, span_days)

    domain_map: dict[str, float] = {
        "patient_sex": config.sex_categories,
        "patient_age": config.age_years_range,
        "patient_height": config.height_cm_range,
        "patient_weight": config.weight_kg_range,
    }
    return domain_map.get(dm.feature)


def _get_clinical_date_domain_size(
    cd: ClinicalDateMatch,
    config: DomainSizeConfig,
    *,
    is_country_year: bool = False,
    span_days: int = 0,
) -> float | None:
    """Look up the domain size D_f for a matched clinical date.

    Uses the same context-dependent and span-extended logic as demographic
    date features.
    """
    resolution = cd.resolution
    if resolution == "day":
        return config.date_exact(is_country_year, span_days)
    elif resolution == "month":
        return config.date_month(is_country_year, span_days)
    elif resolution == "year":
        return config.date_year(is_country_year, span_days)
    return config.date_exact(is_country_year, span_days)
