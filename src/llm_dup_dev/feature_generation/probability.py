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
        """Convert to the PairFeatures-level model."""
        return ProbabilityEstimate(
            p_joint=self.p_joint,
            expected_collisions=self.expected_collisions,
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
# Main computation
# ---------------------------------------------------------------------------


def compute_probability(
    match_result: MatchResult,
    anchor: AnchorPopulation,
    stats: FrequencyStats,
    config: DomainSizeConfig,
    epsilon: float = 1e-15,
) -> ProbabilityResult:
    """Compute the composite matching probability and L_match.

    Steps:
        1. Compute p_f for each matched feature NOT in the anchor definition.
        2. Multiply to get p_joint.
        3. Compute E_joint = N_anchor*(N_anchor-1)/2 * p_joint.
        4. Compute L_match = max(0, -log10(E_joint + epsilon)).

    Drug/AE set frequencies are excluded when they form part of the
    anchor (to avoid double-counting).

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

    Returns
    -------
    ProbabilityResult
        Full computation result including per-feature breakdown.
    """
    feature_probs: list[FeatureProbability] = []

    # Drug set probability — excluded if anchor uses drugs
    if (
        anchor.type not in (AnchorType.DRUGS, AnchorType.DRUGS_AES)
        and match_result.matching_drugs
    ):
        p_drugs = _empirical_frequency(stats.N_drugs, stats.N_total)
        if p_drugs is not None and p_drugs > 0:
            feature_probs.append(
                FeatureProbability(
                    feature="drug_set",
                    p_f=p_drugs,
                    method="empirical",
                    description=f"Drug set: {stats.N_drugs}/{stats.N_total}",
                )
            )

    # AE set probability — excluded if anchor uses AEs
    if (
        anchor.type not in (AnchorType.AES, AnchorType.DRUGS_AES)
        and match_result.matching_AEs
    ):
        p_aes = _empirical_frequency(stats.N_AEs, stats.N_total)
        if p_aes is not None and p_aes > 0:
            feature_probs.append(
                FeatureProbability(
                    feature="ae_set",
                    p_f=p_aes,
                    method="empirical",
                    description=f"AE set: {stats.N_AEs}/{stats.N_total}",
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


def _empirical_frequency(count: int, total: int) -> float | None:
    """Compute empirical relative frequency, or None if invalid."""
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
