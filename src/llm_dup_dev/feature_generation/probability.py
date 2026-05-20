"""Probability estimation for the collision framework.

Computes feature-level probabilities, composite matching probability,
expected collisions within the anchor population, and L_match.

The core idea: if the expected number of random report pairs sharing the
observed feature pattern is extremely small, that is strong evidence the
two reports describe the same individual — and therefore strong support
for the *Duplicates* hypothesis in the duplicate adjudication pipeline.
L_match is one of the main inputs to the report-pair decision logic.

See: docs/duplicate_adjudication_spec.md § Probability estimation
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
    The primary counts (``N_total``, ``N_drugs``, ``N_AEs``,
    ``N_drugs_AEs``) are within the determined reporting context.

    The optional broader-context fields (``N_*_country`` and
    ``N_*_global``) are used by ``compute_probability`` for hierarchical
    backoff smoothing of drug-set / AE-set marginal probabilities. When
    a feature appears only in the candidate pair, the leave-pair-out
    estimate in the primary context collapses to zero; the broader
    counts let the estimate fall back through country and global priors
    instead of being silently dropped. Implementations should populate
    these when available; missing levels are skipped and a small floor
    is used at the bottom of the ladder.
    """

    N_total: int  # total reports in the reporting context
    N_drugs: int  # reports containing all matching drugs
    N_AEs: int  # reports containing all matching AEs
    N_drugs_AEs: int  # reports containing both

    # Optional broader-context counts for hierarchical backoff smoothing.
    # Populate these when the data source has access to country and/or
    # global statistics; leave as None to fall back to the floor.
    N_total_country: int | None = None
    N_drugs_country: int | None = None
    N_AEs_country: int | None = None
    N_total_global: int | None = None
    N_drugs_global: int | None = None
    N_AEs_global: int | None = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Pseudo-count for Bayesian smoothing of anchor-conditional probabilities.
#: Controls how strongly the estimate is pulled toward the prior p0 when
#: the leave-pair-out sample is small. Configurable via
#: ``AnchorConfig.smoothing_pseudocount`` in ``PipelineConfig``.
SMOOTHING_PSEUDOCOUNT: int = 10

#: Last-resort floor probability for the bottom of the marginal-backoff
#: ladder, used when no broader-context counts are available. Picks a
#: rate of ~one-in-a-million so a feature that exists only in the
#: candidate pair still contributes ~6 to L_match instead of being
#: silently dropped. When global counts are available, the floor is
#: derived as ``1 / max(N_total_global - 2, 1)`` instead.
MARGINAL_FLOOR: float = 1e-6


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
    using a hierarchical leave-pair-out Bayesian smoother (see ``_marginal_with_backoff``).

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
        size (540 days for country-year, 3650 days otherwise).
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

    # Drug set probability — excluded if anchor uses drugs.
    # When the anchor is AEs, the drug set is an additional feature whose
    # marginal probability is estimated with a hierarchical
    # leave-pair-out smoother (primary context, with country and global
    # as broader-context priors when available, floor at the bottom).
    if (
        anchor.type not in (AnchorType.DRUGS, AnchorType.DRUGS_AES)
        and match_result.matching_drugs
    ):
        p_drugs, trace = _marginal_with_backoff(
            stats.N_drugs,
            stats.N_total,
            stats.N_drugs_country,
            stats.N_total_country,
            stats.N_drugs_global,
            stats.N_total_global,
            alpha=smoothing_pseudocount,
        )
        feature_probs.append(
            FeatureProbability(
                feature="drug_set",
                p_f=p_drugs,
                method="leave_pair_out",
                description=(
                    f"Drug set: smoothed leave-pair-out "
                    f"({stats.N_drugs}/{stats.N_total} primary; {trace})"
                ),
            )
        )

    # AE set probability — excluded if anchor uses AEs.
    # When the anchor is drugs, the AE set is an additional feature whose
    # marginal probability is estimated the same way as the drug set
    # above (see ``_marginal_with_backoff``).
    if (
        anchor.type not in (AnchorType.AES, AnchorType.DRUGS_AES)
        and match_result.matching_AEs
    ):
        p_aes, trace = _marginal_with_backoff(
            stats.N_AEs,
            stats.N_total,
            stats.N_AEs_country,
            stats.N_total_country,
            stats.N_AEs_global,
            stats.N_total_global,
            alpha=smoothing_pseudocount,
        )
        feature_probs.append(
            FeatureProbability(
                feature="ae_set",
                p_f=p_aes,
                method="leave_pair_out",
                description=(
                    f"AE set: smoothed leave-pair-out "
                    f"({stats.N_AEs}/{stats.N_total} primary; {trace})"
                ),
            )
        )

    # Demographic feature probabilities (domain-size based).
    # Report date is excluded — it is matched for evidence display only.

    # Pre-extract matched age in years for height/weight child/adult selection.
    matched_age_years: float | None = None
    for dm in match_result.demographic_matches:
        if dm.feature == "patient_age" and dm.matched and dm.matched_value is not None:
            if dm.age_unit == "months":
                matched_age_years = float(dm.matched_value) / 12.0
            else:
                matched_age_years = float(dm.matched_value)
            break

    for dm in match_result.demographic_matches:
        if not dm.matched or dm.feature == "report_date":
            continue

        domain_size = _get_domain_size(
            dm,
            config,
            is_country_year=is_country_year,
            matched_age_years=matched_age_years,
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
    # Each unique matched date contributes p_f = 1/D_f. Domain sizing
    # follows the sequential local-gap rule (see
    # ``_clinical_date_domain_sizes``): the most recent matched date
    # uses the context-dependent default; each older date uses a domain
    # equal to 2 × the gap to the nearest more recent matched date, in
    # the unit of its own resolution.
    clinical_domain_sizes = _clinical_date_domain_sizes(
        match_result.matched_clinical_dates,
        config,
        is_country_year=is_country_year,
    )
    for cd, domain_size in zip(
        match_result.matched_clinical_dates, clinical_domain_sizes
    ):
        if domain_size is None or domain_size <= 0:
            continue
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


def _smoothed_marginal(
    n_feature: int,
    n_total: int,
    p_prior: float,
    alpha: int,
) -> float:
    """Single-level Bayesian-smoothed leave-pair-out marginal.

    Computes::

        n_feature_excl = max(0, n_feature - 2)
        n_total_excl   = max(0, n_total - 2)
        p              = (n_feature_excl + alpha * p_prior) / (n_total_excl + alpha)

    The leave-pair-out subtraction is applied at every nesting level
    because the candidate pair sits inside country-year ⊂ country ⊂
    global. If ``n_total_excl + alpha`` would be zero (only possible
    when ``alpha == 0`` and the context is empty after exclusion), the
    prior is returned unchanged.
    """
    n_feature_excl = max(0, n_feature - 2)
    n_total_excl = max(0, n_total - 2)
    denom = n_total_excl + alpha
    if denom <= 0:
        return p_prior
    return (n_feature_excl + alpha * p_prior) / denom


def _marginal_with_backoff(
    n_feature: int,
    n_total: int,
    n_feature_country: int | None,
    n_total_country: int | None,
    n_feature_global: int | None,
    n_total_global: int | None,
    *,
    alpha: int = SMOOTHING_PSEUDOCOUNT,
) -> tuple[float, str]:
    """Hierarchical leave-pair-out marginal with country → global backoff.

    Replaces the bare ``_leave_pair_out_marginal`` call site for drug-set
    and AE-set probabilities. Walks the ladder bottom-up — floor →
    global → country → primary — feeding each level's smoothed estimate
    as the prior for the next, so that a feature seen only in the
    candidate pair still gets a non-zero, principled probability via the
    broader contexts.

    Levels whose counts are ``None`` are skipped (the previous-prior is
    carried forward unchanged). The primary level is always applied.

    The floor is ``1 / max(n_total_global - 2, 1)`` when global counts
    are available, else ``MARGINAL_FLOOR``.

    Returns
    -------
    (p, trace)
        ``p`` is a strictly positive float. ``trace`` is a short
        diagnostic string describing which levels were applied (e.g.
        ``"floor=1e-6 → global → country → primary"``), suitable for
        embedding in a ``FeatureProbability.description``.
    """
    # Floor: prefer global-derived rate when available.
    if n_total_global is not None and n_total_global > 0:
        floor = 1.0 / max(n_total_global - 2, 1)
        floor_label = f"floor=1/{max(n_total_global - 2, 1)}"
    else:
        floor = MARGINAL_FLOOR
        floor_label = f"floor={MARGINAL_FLOOR:g}"

    p_prior = floor
    parts: list[str] = [floor_label]

    # Walk bottom-up through the broader contexts.
    if n_feature_global is not None and n_total_global is not None:
        p_prior = _smoothed_marginal(
            n_feature_global, n_total_global, p_prior, alpha
        )
        parts.append("global")

    if n_feature_country is not None and n_total_country is not None:
        p_prior = _smoothed_marginal(
            n_feature_country, n_total_country, p_prior, alpha
        )
        parts.append("country")

    p = _smoothed_marginal(n_feature, n_total, p_prior, alpha)
    parts.append("primary")

    return p, " → ".join(parts)


def _empirical_frequency(count: int, total: int) -> float | None:
    """General utility: empirical relative frequency, or None if inputs are invalid.

    Not used for drug/AE set probabilities (which use ``_marginal_with_backoff``
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


def _age_decimal_resolution(value: float) -> float:
    """Return the decimal resolution of an age value (e.g. 54.2 → 0.1, 54 → 1.0)."""
    s = str(value)
    if "." in s:
        decimal_places = len(s.split(".")[1].rstrip("0"))
        if decimal_places > 0:
            return 10.0 ** (-decimal_places)
    return 1.0


def _get_domain_size(
    dm: DemographicMatch,
    config: DomainSizeConfig,
    *,
    is_country_year: bool = False,
    matched_age_years: float | None = None,
) -> float | None:
    """Look up the domain size D_f for a matched feature.

    For date features, uses the matched resolution and reporting context
    to select the context-dependent default domain size.

    For patient_age, uses the recorded unit and decimal precision.
    For patient_height and patient_weight, uses matched_age_years to
    select the child (<= child_age_threshold_years) or adult domain size.
    """
    if dm.date_resolution is not None:
        return _context_default_for_resolution(
            dm.date_resolution, config, is_country_year
        )

    if dm.feature == "patient_age":
        if dm.age_unit == "months":
            return config.age_months_range
        # Years (integer or decimal)
        resolution = (
            _age_decimal_resolution(float(dm.matched_value))
            if dm.matched_value is not None
            else 1.0
        )
        return config.age_years_range / resolution

    if dm.feature in ("patient_height", "patient_weight"):
        is_child = (
            matched_age_years is not None
            and matched_age_years <= config.child_age_threshold_years
        )
        if dm.feature == "patient_height":
            return config.height_cm(is_child)
        return config.weight_kg(is_child)

    domain_map: dict[str, float] = {
        "patient_sex": config.sex_categories,
    }
    return domain_map.get(dm.feature)


def _context_default_for_resolution(
    resolution: str,
    config: DomainSizeConfig,
    is_country_year: bool,
) -> float:
    """Context-dependent default domain size for a given date resolution."""
    if resolution == "month":
        return config.date_month(is_country_year)
    if resolution == "year":
        return config.date_year(is_country_year)
    return config.date_exact(is_country_year)


def _gap_domain_size(resolution: str, gap_days: int) -> float:
    """Domain size for an additional matched date: 2 × gap, in the date's unit.

    Guards against accidental zero/negative values (uniqueness should make
    them impossible upstream, but the guard keeps the function total).
    """
    if resolution == "month":
        months = max(1, -(-gap_days // 30))  # ceil days → months
        return 2 * months
    if resolution == "year":
        years = max(1, -(-gap_days // 365))  # ceil days → years
        return 2 * years
    return max(2, 2 * gap_days)  # day resolution


def _clinical_date_domain_sizes(
    clinical_dates: list[ClinicalDateMatch],
    config: DomainSizeConfig,
    *,
    is_country_year: bool,
) -> list[float | None]:
    """Return per-date domain sizes using the sequential local-gap rule.

    The set of matched clinical dates is already unique upstream. Sorted
    most-recent-first, the first date uses the context-dependent default
    and each later date uses a domain of ``2 × (gap to the previous
    date)`` in the unit implied by the later date's resolution.

    The returned list is aligned with the input ``clinical_dates`` order
    (the first date in the input is not necessarily the one that receives
    the context default — the ranking is by calendar date, not input
    position).
    """
    from datetime import date as date_type

    parsed: list[tuple[date_type, int]] = []  # (date, original index)
    result: list[float | None] = [None] * len(clinical_dates)

    for idx, cd in enumerate(clinical_dates):
        d = _parse_date_string(cd.matched_value)
        if d is None:
            continue
        parsed.append((d, idx))

    # Sort by calendar date, most recent first. Stable sort on original index
    # keeps behaviour deterministic if two dates parse to the same day
    # (should not occur given upstream uniqueness + subsumption).
    parsed.sort(key=lambda item: (item[0], item[1]), reverse=True)

    prev_date: date_type | None = None
    for rank, (d, orig_idx) in enumerate(parsed):
        cd = clinical_dates[orig_idx]
        if rank == 0:
            size = _context_default_for_resolution(
                cd.resolution, config, is_country_year
            )
        else:
            assert prev_date is not None
            gap_days = max(1, (prev_date - d).days)
            size = _gap_domain_size(cd.resolution, gap_days)
        result[orig_idx] = size
        prev_date = d

    return result
