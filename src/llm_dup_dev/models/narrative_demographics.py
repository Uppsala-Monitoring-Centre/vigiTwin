"""Derived narrative signals for patient demographics.

Structured output of the per-report LLM task
:mod:`llm_dup_dev.llm.tasks.narrative_demographics`. Captures candidate
age, sex, weight, and height values extracted from one report's
narrative, together with the verbatim snippet that supported each
candidate.

This is a **derived artifact** in the sense of the data-contract
document: the pipeline does not consume ``NarrativeDemographics``
directly. Instead, the merge helper
:func:`llm_dup_dev.feature_generation.narrative_demographics.merge_narrative_demographics`
appends the extracted values to the corresponding ``patient_*_values``
lists on the :class:`~llm_dup_dev.models.report.Report`, tagged with
``sources=["narrative_llm"]``. The existing inconsistency checks in
:mod:`llm_dup_dev.feature_generation.inconsistencies` then operate over
the combined structured + narrative candidates using min-difference /
set-intersection — the generous matching rule ``v_1 ∩ v_2 ≠ ∅``.

Evidence quotes are preserved on ``NarrativeDemographics`` but are not
propagated into the Report. The extractor artifact is what the secure
repo freezes to JSON for offline evaluation.

See: docs/data_contracts.md § Derived Narrative Signals
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from llm_dup_dev.models.demographics_vocab import UNCERTAINTY_SENTINELS


# ------------------------------------------------------------------
# Controlled vocabulary
# ------------------------------------------------------------------


class NarrativeSex(str, Enum):
    """Controlled vocabulary for narrative-extracted patient sex.

    Lowercase to match the convention used by
    :func:`llm_dup_dev.feature_generation.inconsistencies._check_sex`,
    which lowercases all values before comparing.

    There is deliberately no ``UNKNOWN`` member: an explicit statement
    that sex is unknown/unreported is treated as *missing* (no candidate
    extracted), not as a categorical value. See ``_MISSING_SEX_SENTINELS``
    and the ``sexes`` validator on :class:`NarrativeDemographics`.
    """

    MALE = "male"
    FEMALE = "female"
    OTHER = "other"


# Sex "values" that actually signal absence of information. These are
# dropped rather than recorded, so a narrative saying "sex: unknown"
# yields no sex candidate — consistent with the precision-oriented,
# empty-list-means-missing contract of the extractor. The canonical set
# is shared with the structured-side Report validators (see
# :mod:`llm_dup_dev.models.demographics_vocab`).
_MISSING_SEX_SENTINELS = UNCERTAINTY_SENTINELS


# ------------------------------------------------------------------
# Per-value extractions
# ------------------------------------------------------------------


class NarrativeAgeValue(BaseModel):
    """A single candidate patient age extracted from the narrative."""

    value: float = Field(description="Numeric age value.")
    unit: Literal["years", "months", "days"] = "years"
    evidence: str = Field(
        description="Short verbatim snippet copied from the narrative.",
    )


class NarrativeSexValue(BaseModel):
    """A single candidate patient sex extracted from the narrative."""

    value: NarrativeSex
    evidence: str = Field(
        description="Short verbatim snippet copied from the narrative.",
    )


class NarrativeMeasurementValue(BaseModel):
    """A single candidate patient measurement (weight or height)."""

    value: float = Field(description="Numeric measurement value.")
    unit: Literal["kg", "cm"] = Field(
        description="Unit; 'kg' for weight, 'cm' for height.",
    )
    evidence: str = Field(
        description="Short verbatim snippet copied from the narrative.",
    )


# ------------------------------------------------------------------
# Per-report extraction
# ------------------------------------------------------------------


class NarrativeDemographics(BaseModel):
    """Structured demographic signals extracted from one report narrative.

    All lists are allowed to contain multiple candidates — the narrative
    may legitimately mention more than one age (on admission vs follow-
    up) or weight (on admission vs discharge). The extractor is
    precision-oriented: leave lists empty when the narrative does not
    clearly state a value *for the patient of interest*. Values
    describing family members, bystanders, or comparators must not be
    included.

    Consumed by :func:`merge_narrative_demographics`, which appends
    candidates to the Report's ``patient_*_values`` lists with
    ``sources=["narrative_llm"]``.
    """

    ages: list[NarrativeAgeValue] = Field(default_factory=list)
    sexes: list[NarrativeSexValue] = Field(default_factory=list)
    weights: list[NarrativeMeasurementValue] = Field(default_factory=list)
    heights: list[NarrativeMeasurementValue] = Field(default_factory=list)
    notes: str = Field(
        default="",
        description="Brief free-text explanation for human review.",
    )

    @field_validator("sexes", mode="before")
    @classmethod
    def _drop_missing_sex_sentinels(cls, value: object) -> object:
        """Drop sex candidates that merely say the sex is unknown/unreported.

        Runs before per-item validation so a value like ``"unknown"`` is
        removed entirely (yielding an empty list) rather than either being
        recorded as a categorical value or raising a ``ValidationError``.
        This also keeps legacy frozen bundles that still contain
        ``"unknown"`` loadable — the entry is silently dropped.
        """
        if not isinstance(value, list):
            return value
        kept = []
        for item in value:
            if isinstance(item, NarrativeSexValue):
                raw = item.value
            elif isinstance(item, dict):
                raw = item.get("value")
            else:
                raw = item
            if str(raw).strip().lower() in _MISSING_SEX_SENTINELS:
                continue
            kept.append(item)
        return kept
