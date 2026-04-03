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
from llm_dup_dev.feature_generation.matching import MatchResult
from llm_dup_dev.models.enums import AnchorType
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

    # Drug set probability — excluded if anchor uses drugs.
    # When the anchor is AEs, the drug set is an additional feature scored
    # with leave-pair-out Bayesian smoothing:
    #   n_anchor = N_AEs, k_anchor_and_feature = N_drugs_AEs,
    #   p0 = N_drugs / N_total  (drug prevalence without AE conditioning)
    if (
        anchor.type not in (AnchorType.DRUGS, AnchorType.DRUGS_AES)
        and match_result.matching_drugs
    ):
        p_drugs = leave_pair_out_probability(
            n_anchor=anchor.N_anchor,
            k_anchor_and_feature=stats.N_drugs_AEs,
            n_feature_in_context=stats.N_drugs,
            n_total_in_context=stats.N_total,
            smoothing_pseudocount=smoothing_pseudocount,
        )
        if p_drugs is not None and p_drugs > 0:
            n_other = max(0, anchor.N_anchor - 2)
            k_other = max(0, stats.N_drugs_AEs - 2)
            feature_probs.append(
                FeatureProbability(
                    feature="drug_set",
                    p_f=p_drugs,
                    method="leave_pair_out",
                    description=(
                        f"Drug set: k_other={k_other}/{n_other} "
                        f"smoothed with p0={stats.N_drugs}/{stats.N_total}"
                    ),
                )
            )

    # AE set probability — excluded if anchor uses AEs.
    # When the anchor is drugs, the AE set is an additional feature scored
    # with leave-pair-out Bayesian smoothing:
    #   n_anchor = N_drugs, k_anchor_and_feature = N_drugs_AEs,
    #   p0 = N_AEs / N_total  (AE prevalence without drug conditioning)
    if (
        anchor.type not in (AnchorType.AES, AnchorType.DRUGS_AES)
        and match_result.matching_AEs
    ):
        p_aes = leave_pair_out_probability(
            n_anchor=anchor.N_anchor,
            k_anchor_and_feature=stats.N_drugs_AEs,
            n_feature_in_context=stats.N_AEs,
            n_total_in_context=stats.N_total,
            smoothing_pseudocount=smoothing_pseudocount,
        )
        if p_aes is not None and p_aes > 0:
            n_other = max(0, anchor.N_anchor - 2)
            k_other = max(0, stats.N_drugs_AEs - 2)
            feature_probs.append(
                FeatureProbability(
                    feature="ae_set",
                    p_f=p_aes,
                    method="leave_pair_out",
                    description=(
                        f"AE set: k_other={k_other}/{n_other} "
                        f"smoothed with p0={stats.N_AEs}/{stats.N_total}"
                    ),
                )
            )

    # Demographic feature probabilities (domain-size based)
    for dm in match_result.demographic_matches:
        if not dm.matched:
            continue

        domain_size = _get_domain_size(dm, config)
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


def _get_domain_size(
    dm: "MatchResult.__class__",  # DemographicMatch
    config: DomainSizeConfig,
) -> float | None:
    """Look up the domain size D_f for a matched feature.

    For date features, uses the matched resolution to select
    the appropriate domain size.
    """
    if dm.feature == "report_date":
        resolution = dm.date_resolution or "day"
        if resolution == "day":
            return config.date_exact
        elif resolution == "month":
            return config.date_month
        elif resolution == "year":
            return config.date_year
        return config.date_exact

    domain_map: dict[str, float] = {
        "patient_sex": config.sex_categories,
        "patient_age": config.age_years_range,
        "patient_height": config.height_cm_range,
        "patient_weight": config.weight_kg_range,
    }
    return domain_map.get(dm.feature)
