"""Pydantic models for the reference-set characteristics ("Table 1") artifact.

A :class:`CharacterizationReport` describes the *composition* of one or more
reference sets — reporting years, countries, patient age and sex, the commonest
drugs and adverse events, and how often the two reports of a pair agree on
country, year and sex. It is descriptive, never inferential: nothing here
estimates a method's performance, and no proportion carries a confidence
interval, because these numbers describe the sets as constituted rather than a
sample drawn from a population.

Two properties of the artifact are load-bearing and easy to lose:

**Every denominator is stated, and missingness is a category.** A
:class:`CategoricalDistribution` always satisfies ``n_resolved + n_missing +
n_conflicting + n_unrecognised == denominator``, and a :class:`NumericSummary`
always states what it excluded. A reader can therefore never mistake "we had no
data" for "the value was zero" — the same distinction the ``StatsProvider``
contract insists on for frequency counts.

**The resolution policy travels with the numbers.** Reports carry *multiple*
candidate values per demographic (a structured age and a narrative-extracted
one), so producing one row per report requires a documented reduction rule.
:class:`ResolutionPolicySummary` records the rule that was actually applied, on
the same principle that makes ``EvaluationRunManifest`` record
``analysis_positive_labels`` beside the variant name: the name of a rule does
not tell a later reader what it did.

See: docs/reference_set_characteristics.md
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from llm_dup_dev.run_manifest.models import DevPackageInfo

#: Bumped when the artifact shape changes in a way consumers must notice.
SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Distribution primitives
# ---------------------------------------------------------------------------


class CategoryCount(BaseModel):
    """One row of a categorical distribution.

    ``key`` is the normalised tabulation key (what values were grouped by);
    ``label`` is the form shown to a reader, which for free-text fields such as
    country is the most frequent original spelling behind that key. Keeping both
    means a reader sees ``United States`` while the grouping stays deterministic.
    """

    key: str
    label: str
    count: int
    proportion: float | None = None


class CategoricalDistribution(BaseModel):
    """A tabulated categorical field, with its missingness fully accounted for.

    The disposition counts partition the denominator::

        n_resolved + n_missing + n_conflicting + n_unrecognised
            + n_out_of_range == denominator

    ``categories`` covers the ``n_resolved`` reports only, sorted by descending
    count then key. For a top-N list ``truncated_to`` is set and ``n_distinct``
    records how many distinct values were observed before truncation, so a
    reader can tell a complete table from a truncated one. ``n_distinct`` counts
    only keys actually observed; a category emitted with a count of zero (as sex
    does, so an all-female set is legible as one) does not inflate it.

    ``unavailable_reason`` distinguishes "this question could not be asked of
    this data" from "the answer is empty". An empty ``categories`` list with no
    reason means no report resolved a value; with a reason it means the input
    never carried the field at all (no MedDRA hierarchy, no drug roles).
    """

    field: str
    denominator: int
    n_resolved: int
    n_missing: int
    n_conflicting: int
    n_unrecognised: int = 0
    n_out_of_range: int = 0
    n_distinct: int
    categories: list[CategoryCount] = Field(default_factory=list)
    truncated_to: int | None = None
    boundary_tie: bool = False
    unavailable_reason: str | None = None


class NumericSummary(BaseModel):
    """Median / IQR / range for a numeric field, with its exclusions stated.

    ``n_used`` is the median's denominator. The remaining counts say why the
    other observations were left out, so a median over 6 of 50 reports cannot
    be mistaken for a median over 50.

    All three of ``median`` / ``q1`` / ``q3`` are ``None`` when ``n_used`` is 0.
    At ``n_used == 1`` they are all the single observed value: a one-observation
    IQR is degenerate rather than undefined, and raising would be wrong for a
    legitimately tiny reference set.
    """

    field: str
    unit: str
    n_used: int
    n_missing: int
    n_conflicting: int
    n_unrecognised_unit: int = 0
    n_out_of_range: int = 0
    median: float | None = None
    q1: float | None = None
    q3: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    quantile_method: str = "inclusive_linear"


class Concordance(BaseModel):
    """How often the two reports of a pair agree on one field.

    Deliberately three-way. ``not_comparable`` holds every pair where at least
    one side had no usable value, and it is reported rather than folded into
    ``different`` — two reports that are silent about country do not disagree
    about it.

    Both proportions are given because either alone can mislead:
    ``proportion_same_comparable`` is the figure a reader usually wants, and
    ``proportion_same_all`` keeps the not-comparable mass visible beside it.
    """

    field: str
    n_pairs: int
    n_same: int
    n_different: int
    n_not_comparable: int
    proportion_same_comparable: float | None = None
    proportion_same_all: float | None = None


# ---------------------------------------------------------------------------
# Per-set sections
# ---------------------------------------------------------------------------


class DataAvailability(BaseModel):
    """Coverage counts over unique reports, for reading the tables above.

    These answer "was the field there at all?", which is what turns an empty or
    thin distribution from a puzzle into a fact about the extract. The drug-role
    counts matter most: the top-drug table counts only suspected/interacting
    drugs, so a set whose loader leaves ``role`` unset yields an empty table for
    a reason that is invisible without them.
    """

    n_reports_with_narrative: int = 0
    n_reports_with_any_drug: int = 0
    n_reports_with_any_suspect_or_interacting_drug: int = 0
    n_reports_with_any_drug_role_recorded: int = 0
    n_reports_with_any_adverse_event: int = 0
    n_reports_with_any_hlt: int = 0
    n_reports_with_case_identifiers: int = 0


class ReportCharacteristics(BaseModel):
    """Report-level statistics over the **unique** reports of one set.

    A report appearing in two pairs is counted once. ``n_report_instances`` is
    stated beside ``n_unique_reports`` so the inflation factor is visible: it is
    the pair count doubled, and the gap between the two is exactly how much a
    naive per-pair tabulation would have over-counted.

    ``n_unique_reports`` is the denominator of every distribution below it.
    """

    n_pairs: int
    n_report_instances: int
    n_unique_reports: int
    n_reports_in_multiple_pairs: int
    n_reports_unavailable: int = 0

    availability: DataAvailability

    report_year: CategoricalDistribution
    country_of_origin: CategoricalDistribution
    patient_sex: CategoricalDistribution
    patient_age_years: NumericSummary
    patient_age_bands: CategoricalDistribution
    drug_roles: CategoricalDistribution
    top_drugs: CategoricalDistribution
    top_adverse_events_pt: CategoricalDistribution
    top_adverse_events_hlt: CategoricalDistribution


class PairCharacteristics(BaseModel):
    """Pair-level statistics for one set.

    Separate from :class:`ReportCharacteristics` because the unit of analysis is
    different: these describe *relationships between* the two reports of a pair,
    which no report-level table can express.

    ``n_pairs_evaluable`` is the denominator for every statistic here; it falls
    below ``n_pairs`` only when reports were missing and the caller asked for
    them to be skipped.
    """

    n_pairs: int
    n_pairs_evaluable: int

    country: Concordance
    report_year: Concordance
    patient_sex: Concordance

    year_difference: NumericSummary
    age_difference_years: NumericSummary
    n_pairs_with_identical_age: int = 0

    shared_drugs: NumericSummary
    shared_adverse_events_pt: NumericSummary
    n_pairs_with_any_shared_drug: int = 0
    n_pairs_with_any_shared_adverse_event: int = 0


class ResolutionPolicySummary(BaseModel):
    """The rules the numbers in this artifact were produced under.

    Embedded rather than documented elsewhere because a Table 1 whose reduction
    rule is unknown is not interpretable: "median age 54" means something
    different if narrative-extracted ages were preferred, or if conflicting
    reports were dropped instead of counted.
    """

    source_preference: list[str] = Field(default_factory=lambda: ["structured"])
    conflict_handling: str
    age_unit_conversions: dict[str, float] = Field(default_factory=dict)
    age_band_width_years: int
    age_plausible_range: list[float] = Field(default_factory=list)
    country_key_normalisation: str
    drug_roles_counted: list[str] = Field(default_factory=list)
    top_n: int
    tie_break: str


class ReferenceSetCharacteristics(BaseModel):
    """Everything this artifact says about one reference set.

    The set's own identity (``set_id`` / ``role`` / ``provenance`` /
    ``screening_mode``) is carried here so a block stays interpretable when
    lifted out of the report, and ``reference_label_counts`` gives the set's
    composition in the human-reference vocabulary without splitting any of the
    distributions by it.
    """

    set_id: str
    role: str
    label: str | None = None
    provenance: str | None = None
    screening_mode: str = "observed"
    reference_label_counts: dict[str, int] = Field(default_factory=dict)

    reports: ReportCharacteristics
    pairs: PairCharacteristics


class CharacterizationReport(BaseModel):
    """Descriptive characteristics of one or more reference sets.

    One block per set, in the order the sets were supplied. There is
    deliberately no pooled across-sets block: the sets differ in provenance and
    role, and a pooled table would invite exactly the comparison the per-set
    reporting exists to prevent — the same reason recall is reported per set and
    never pooled in ``study_eval``.

    ``dataset_id`` joins the artifact back to the ``DatasetManifest`` of the
    validation set the reports came from, which is what pins the database
    snapshot and MedDRA version these numbers describe.

    Real counts computed on the secure side describe real reports. The artifact
    applies no small-cell suppression by design: whether a given report may
    leave the secure environment is a human decision, made with the numbers in
    view rather than pre-empted by a threshold this package chose.
    """

    schema_version: str = SCHEMA_VERSION
    created_at: datetime
    characterization_id: str
    dataset_id: str | None = None

    dev_package: DevPackageInfo = Field(default_factory=DevPackageInfo)
    dev_git_commit: str | None = None

    policy: ResolutionPolicySummary
    sets: list[ReferenceSetCharacteristics] = Field(default_factory=list)
    notes: str | None = None


__all__ = [
    "SCHEMA_VERSION",
    "CategoryCount",
    "CategoricalDistribution",
    "NumericSummary",
    "Concordance",
    "DataAvailability",
    "ReportCharacteristics",
    "PairCharacteristics",
    "ResolutionPolicySummary",
    "ReferenceSetCharacteristics",
    "CharacterizationReport",
]
