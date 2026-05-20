"""Configurable parameters for the duplicate adjudication pipeline.

Thresholds define the decision boundaries and probability estimation
parameters used by the report-pair classification logic. These are
meant to be tuned during method development using annotated report
pairs.

See: docs/duplicate_adjudication_spec.md
"""

from pydantic import BaseModel, Field, model_validator


class DomainSizeConfig(BaseModel):
    """Default domain sizes (D_f) for features without empirical frequencies.

    Used when computing p_f = 1/D_f for matched features.

    See: docs/duplicate_adjudication_spec.md § Determining Domain Size
    """

    # Age
    age_years_range: int = 80  # plausible ages 0–80 years (very high ages are rare)
    age_months_range: int = 100  # plausible ages in months (0–100; only recorded for young children)

    # Sex
    sex_categories: int = 2

    # Height — effective domain span conditional on age (cm, at 1 cm resolution)
    child_age_threshold_years: int = 15  # patients ≤ this age are treated as children
    height_cm_range_child: int = 20
    height_cm_range_adult: int = 40

    # Weight — effective domain span conditional on age (kg, at 1 kg resolution)
    weight_kg_range_child: int = 20
    weight_kg_range_adult: int = 40

    # Dates — default domain sizes depend on reporting context.
    # Country-year context: 1.5 years (most reports in a country-year are
    # recent; 1.5 years gives the month/year resolutions a bit of headroom
    # — 540 days → 18 months / 2 years — while still being narrow).
    # Broader contexts (country, global): 10 years (database spans ~50
    # years but exponential growth means most reports are from the last
    # decade).
    date_plausible_days_country_year: int = 540
    date_plausible_days_broad: int = 3650  # ~10 years

    def height_cm(self, is_child: bool) -> int:
        """Domain size for height, conditional on age group."""
        return self.height_cm_range_child if is_child else self.height_cm_range_adult

    def weight_kg(self, is_child: bool) -> int:
        """Domain size for weight, conditional on age group."""
        return self.weight_kg_range_child if is_child else self.weight_kg_range_adult

    def date_exact(self, is_country_year: bool = False) -> int:
        """Context-dependent default domain size for exact date (day resolution)."""
        return (
            self.date_plausible_days_country_year
            if is_country_year
            else self.date_plausible_days_broad
        )

    def date_month(self, is_country_year: bool = False) -> int:
        """Context-dependent default domain size for month-year resolution."""
        exact = self.date_exact(is_country_year)
        return max(1, -(-exact // 30))  # ceiling division

    def date_year(self, is_country_year: bool = False) -> int:
        """Context-dependent default domain size for year-only resolution."""
        exact = self.date_exact(is_country_year)
        return max(1, -(-exact // 365))  # ceiling division


class InconsistencyThresholds(BaseModel):
    """Thresholds for classifying feature differences as inconsistencies.

    These are starting values from the specification and may need
    refinement based on evaluation results.

    See: docs/duplicate_adjudication_spec.md § Structured Data Inconsistencies
    """

    # Age (years)
    age_moderate: float = 2.0
    age_strong: float = 5.0

    # Height (cm)
    height_moderate: float = 3.0
    height_strong: float = 6.0

    # Weight (kg)
    weight_moderate: float = 3.0
    weight_strong: float = 10.0

    # Sex: any mismatch is a strong inconsistency (no threshold needed)

    # Drug-date thresholds are no longer used here.  Clinical-date
    # inconsistencies (drug starts, AE onsets, report dates) are
    # handled by the dedicated rule-based module in
    # feature_generation/clinical_date_inconsistencies.py.


class AnchorConfig(BaseModel):
    """Parameters for anchor population selection.

    See: docs/duplicate_adjudication_spec.md § Anchor Selection
    """

    threshold: int = Field(
        default=50,
        description=(
            "T: if either N_drugs or N_AEs <= T, "
            "use the smaller of the two as anchor."
        ),
    )


class DecisionThresholds(BaseModel):
    """Thresholds for the decision logic (phase 8).

    The decision uses a weighted inconsistency score:

        w_tot = w_minor × minor + w_moderate × moderate
              + w_strong × strong + w_decisive × decisive

    Three L_match zones define progressively stricter tolerance for w_tot:

    - strong zone (L_match > lmatch_strong): w_tot < 4 → Same
    - medium zone (L_match > lmatch_med):    w_tot < 3 → Same
    - weak zone   (L_match > lmatch_weak):   w_tot < 1 → Same
    - below weak: always Different individuals / Unrelated

    In all three non-trivial zones, matching evidence is treated as
    sufficient to reject independence; w_tot only decides the
    duplicate-vs-otherwise-related sub-label. Only the below-weak
    branch yields Unrelated.

    ``w_decisive`` is set far above any zone threshold so that a single
    decisive inconsistency always forces the failing-zone branch, which
    in combination with the zone logic yields ``Otherwise_related``
    whenever ``L_match > lmatch_weak`` and ``Unrelated`` otherwise. This
    is enforced by an invariant validator.

    See: docs/duplicate_adjudication_spec.md § Decision logic
    """

    # L_match zone boundaries
    lmatch_strong: float = 3.0
    lmatch_med: float = 2.0
    lmatch_weak: float = 1.0

    # Inconsistency weights
    w_minor: float = 0.4
    w_moderate: float = 1.0
    w_strong: float = 3.0
    w_decisive: float = 100.0

    # Per-zone w_tot thresholds (Same if w_tot < threshold)
    w_tot_strong_zone: float = 4.0
    w_tot_med_zone: float = 3.0
    w_tot_weak_zone: float = 1.0

    @model_validator(mode="after")
    def _w_decisive_exceeds_zone_thresholds(self) -> "DecisionThresholds":
        max_zone = max(
            self.w_tot_strong_zone,
            self.w_tot_med_zone,
            self.w_tot_weak_zone,
        )
        if self.w_decisive <= max_zone:
            raise ValueError(
                f"w_decisive ({self.w_decisive}) must exceed the largest "
                f"zone threshold ({max_zone}) so that a single decisive "
                f"inconsistency always fails every zone."
            )
        return self


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
