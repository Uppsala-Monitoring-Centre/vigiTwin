"""Configurable parameters for the same-individual assessment pipeline.

Thresholds define the decision boundaries and probability estimation
parameters. These are meant to be tuned during method development
using annotated report pairs.

See: docs/same_individual_assessment_spec.md
"""

from pydantic import BaseModel, Field


class DomainSizeConfig(BaseModel):
    """Default domain sizes (D_f) for features without empirical frequencies.

    Used when computing p_f = 1/D_f for matched features.

    See: docs/same_individual_assessment_spec.md § Determining Domain Size
    """

    # Age
    age_years_range: int = 101  # 0–100 years
    age_months_range: int = 24  # plausible infant months

    # Sex
    sex_categories: int = 2

    # Height (adult defaults, cm)
    height_cm_range: int = 50  # ~150–200 cm

    # Weight (adult defaults, kg)
    weight_kg_range: int = 100  # ~40–140 kg

    # Dates — default domain sizes depend on reporting context.
    # Country-year context: 1 year (most reports in a country-year are recent).
    # Broader contexts (country, global): 10 years (database spans ~50 years
    # but exponential growth means most reports are from the last decade).
    date_plausible_days_country_year: int = 365
    date_plausible_days_broad: int = 3650  # ~10 years

    def date_exact(self, is_country_year: bool = False, span_days: int = 0) -> int:
        """Domain size for exact date (day resolution).

        Parameters
        ----------
        is_country_year : bool
            Whether the reporting context is country-year.
        span_days : int
            Span in days of all matching dates across features. If this
            exceeds the default, the domain is extended to this value.
        """
        default = (
            self.date_plausible_days_country_year
            if is_country_year
            else self.date_plausible_days_broad
        )
        return max(default, span_days)

    def date_month(self, is_country_year: bool = False, span_days: int = 0) -> int:
        """Domain size for month-year resolution."""
        exact = self.date_exact(is_country_year, span_days)
        # Convert days to months (integer ceiling)
        return max(1, -(-exact // 30))  # ceiling division

    def date_year(self, is_country_year: bool = False, span_days: int = 0) -> int:
        """Domain size for year-only resolution."""
        exact = self.date_exact(is_country_year, span_days)
        return max(1, -(-exact // 365))  # ceiling division


class InconsistencyThresholds(BaseModel):
    """Thresholds for classifying feature differences as inconsistencies.

    These are starting values from the specification and may need
    refinement based on evaluation results.

    See: docs/same_individual_assessment_spec.md § Structured Data Inconsistencies
    """

    # Age (years)
    age_moderate: float = 3.0
    age_strong: float = 10.0

    # Height (cm)
    height_moderate: float = 5.0
    height_strong: float = 10.0

    # Weight (kg)
    weight_moderate: float = 5.0
    weight_strong: float = 15.0

    # Sex: any mismatch is a strong inconsistency (no threshold needed)

    # Drug dates (days)
    drug_date_small_days: float = 7.0
    drug_date_moderate_days: float = 30.0
    drug_date_small_weight: float = 0.5
    drug_date_moderate_weight: float = 1.0
    drug_date_large_weight: float = 3.0
    drug_date_concomitant_factor: float = 0.5
    drug_date_cap_per_drug: float = 3.0
    drug_date_score_moderate: float = 1.0
    drug_date_score_strong: float = 3.0


class AnchorConfig(BaseModel):
    """Parameters for anchor population selection.

    See: docs/same_individual_assessment_spec.md § Anchor Selection
    """

    threshold: int = Field(
        default=50,
        description=(
            "T: if either N_drugs or N_AEs <= T, "
            "use the smaller of the two as anchor."
        ),
    )
    smoothing_pseudocount: int = Field(
        default=10,
        description=(
            "m: Bayesian pseudo-count for leave-pair-out probability smoothing. "
            "Controls how strongly the estimate is pulled toward the prior p0 "
            "when the leave-pair-out sample is small."
        ),
    )


class DecisionThresholds(BaseModel):
    """Thresholds for the decision logic (phase 8).

    The decision uses a weighted inconsistency score:

        w_tot = w_moderate × moderate + w_strong × strong

    Three L_match zones define progressively stricter tolerance for w_tot:

    - strong zone (L_match > lmatch_strong): w_tot < 4 → Same
    - medium zone (L_match > lmatch_med):    w_tot < 3 → Same
    - weak zone   (L_match > lmatch_weak):   w_tot < 1 → Same
    - below weak: always Different individuals / Unrelated

    Decisive inconsistencies override all zones → Different.

    See: docs/same_individual_assessment_spec.md § Decision logic
    """

    # L_match zone boundaries
    lmatch_strong: float = 3.0
    lmatch_med: float = 2.0
    lmatch_weak: float = 1.0

    # Inconsistency weights
    w_moderate: float = 1.0
    w_strong: float = 3.0

    # Per-zone w_tot thresholds (Same if w_tot < threshold)
    w_tot_strong_zone: float = 4.0
    w_tot_med_zone: float = 3.0
    w_tot_weak_zone: float = 1.0


class PipelineConfig(BaseModel):
    """Configuration for the feature generation pipeline (phases 1–7).

    Does NOT include decision logic thresholds — use DecisionThresholds
    separately for the classifier.
    """

    domain_sizes: DomainSizeConfig = Field(default_factory=DomainSizeConfig)
    inconsistency_thresholds: InconsistencyThresholds = Field(
        default_factory=InconsistencyThresholds
    )
    anchor: AnchorConfig = Field(default_factory=AnchorConfig)

    epsilon: float = Field(
        default=1e-15,
        description="Small constant for numerical stability in log computation.",
    )
    feature_version: str = "1.0"
