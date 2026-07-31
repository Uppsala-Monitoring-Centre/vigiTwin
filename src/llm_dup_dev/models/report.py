"""Data models for the Report layer.

The Report layer represents structured information extracted from a single
pharmacovigilance report. It combines structured database fields and
LLM-extracted narrative attributes.

Multi-valued attributes preserve all candidate values with provenance,
using empty lists to represent missing or uncertain information.

See: docs/data_contracts.md § Report Layer
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from llm_dup_dev.models.demographics_vocab import (
    drop_uncertainty_values,
    normalize_structured_sex,
)
from llm_dup_dev.models.enums import DrugRole


# ---------------------------------------------------------------------------
# Value types with provenance tracking
# ---------------------------------------------------------------------------


class SourcedValue(BaseModel):
    """A categorical value with provenance tracking.

    Used for demographic attributes (sex, country) where the same field may
    have multiple candidate values from different sources.
    """

    value: str
    sources: list[str] = Field(min_length=1)


class AgeValue(BaseModel):
    """Patient age with unit and provenance."""

    value: float
    unit: str = "years"  # "years", "months", "days"
    sources: list[str] = Field(min_length=1)


class MeasurementValue(BaseModel):
    """Physical measurement (height or weight) with unit and provenance."""

    value: float
    unit: str  # "cm" for height, "kg" for weight
    sources: list[str] = Field(min_length=1)


class PartialDate(BaseModel):
    """A date with potentially reduced resolution.

    Used for drug start/end dates and AE start dates within
    drug and adverse_event objects (no separate provenance).
    """

    value: str  # ISO 8601: "2020-06-12", "2020-06", or "2020"
    resolution: Literal["day", "month", "year"]


class DateValue(BaseModel):
    """A dated value with resolution and provenance.

    Used for report-level date attributes (report_date_values).
    Resolution affects domain size in probability calculations.
    """

    value: str  # ISO 8601 format
    resolution: Literal["day", "month", "year"]
    sources: list[str] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Case identifiers (E2B-style inter-report references)
# ---------------------------------------------------------------------------


class CaseIdentifierKind(str, Enum):
    """Which of a report's own case-number fields a value was read from.

    Reports carry their own case numbers under three headings, and the
    same identifier may be filed under different headings by different
    senders — any of the three can hold a value that is in fact a
    worldwide-unique identifier. ``kind`` is therefore **provenance, not
    a namespace**: it records where the value came from for audit and
    display, never restricting what it may match. See
    :mod:`llm_dup_dev.feature_generation.explicit_link_comparison`.
    """

    SAFETY_REPORT_ID = "safety_report_id"
    COMPANY_NUMBER = "company_number"
    AUTHORITY_NUMBER = "authority_number"


class CaseIdentifierRef(BaseModel):
    """One of a report's *own* case numbers, tagged with its source field.

    Used by ``Report.case_identifiers`` only. The references a report
    makes to *other* reports (``Report.other_case_identifiers``) are bare
    strings — the ICH E2B(R3) C.1.9 field carries no such qualifier.
    """

    kind: CaseIdentifierKind
    value: str


# ---------------------------------------------------------------------------
# Clinical information
# ---------------------------------------------------------------------------


class Drug(BaseModel):
    """A drug reported in a pharmacovigilance report.

    Identified by WHODrug active substance name.
    Drugs are treated as sets when comparing reports.

    See: docs/data_contracts.md § Drug Information
    """

    substance: str
    sources: list[str] = Field(min_length=1)
    role: DrugRole | None = None
    form: str | None = None
    strength: str | None = None
    start_date: PartialDate | None = None
    end_date: PartialDate | None = None


class AdverseEvent(BaseModel):
    """An adverse event reported as a MedDRA Preferred Term (PT).

    Adverse events are treated as sets when comparing reports.

    The optional ``primary_hlt_name`` / ``primary_hlgt_name`` /
    ``primary_soc_name`` carry the primary MedDRA hierarchy path for
    the PT (i.e. the HLT/HLGT/SOC where ``PRIMARY_SOC_FG = 'Y'``). They
    are populated by the secure-side report loader when MedDRA hierarchy
    info is available; older frozen data and synthetic fixtures leave
    them as ``None``. The deterministic AE clinical-picture assessor
    (:mod:`llm_dup_dev.feature_generation.ae_clinical_picture_rules`)
    degrades gracefully when these are absent.

    See: docs/data_contracts.md § Adverse Events
    """

    pt_name: str
    sources: list[str] = Field(min_length=1)
    start_date: PartialDate | None = None
    primary_hlt_name: str | None = None
    primary_hlgt_name: str | None = None
    primary_soc_name: str | None = None


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


class Report(BaseModel):
    """Structured representation of a single pharmacovigilance report.

    Combines structured database fields and LLM-extracted narrative attributes.
    Multi-valued attributes preserve all candidate values with provenance.
    Missing/uncertain values are represented as empty lists.

    See: docs/data_contracts.md § Report Layer
    """

    report_id: str

    # Demographic attributes (multi-valued with provenance)
    patient_sex_values: list[SourcedValue] = Field(default_factory=list)
    patient_age_values: list[AgeValue] = Field(default_factory=list)
    patient_height_values: list[MeasurementValue] = Field(default_factory=list)
    patient_weight_values: list[MeasurementValue] = Field(default_factory=list)
    country_of_origin_values: list[SourcedValue] = Field(default_factory=list)
    report_date_values: list[DateValue] = Field(default_factory=list)

    # Clinical information (set semantics)
    drugs: list[Drug] = Field(default_factory=list)
    adverse_events: list[AdverseEvent] = Field(default_factory=list)

    # Explicit case-level adverse-event onset date as reported on the source
    # report (e.g. extracted from a "CASE EVENT DATE: YYYYMMDD" narrative
    # pattern on US reports). When present this takes precedence over the
    # dev-side derived overall AE onset date — see
    # ``feature_generation.clinical_date_inconsistencies
    # .effective_overall_ae_onset_date``.
    reported_overall_ae_onset_date: PartialDate | None = None

    # Narrative text (used for LLM-based consistency assessment)
    narrative: str | None = None

    # Optional structured citation / source descriptor for literature reports
    # (e.g. "Smith J et al. NEJM 2023;389:1234. PMID 12345678"). Populated by
    # the secure-side report loader when the source DB carries a distinct
    # literature-reference field; absent in older frozen data and synthetic
    # fixtures, in which case the narrative-source-case task falls back to
    # its pre-existing narrative-only behavior.
    literature_reference: str | None = None

    # This report's own case numbers, as carried by the source system
    # (safety report id / company number / authority number). Needed to
    # resolve the ``other_case_identifiers`` pointers *on the other report* of
    # a pair — ``report_id`` is the pipeline's own key and lives in a
    # different namespace from these. Empty when the loader does not supply
    # them (older frozen data, synthetic fixtures).
    case_identifiers: list[CaseIdentifierRef] = Field(default_factory=list)

    # References this report makes to *other* reports — the ICH E2B(R3)
    # C.1.9 "Other case identifiers in previous transmissions" one-to-many
    # field. When one report of a candidate pair names the other here, the
    # sender has declared the two to be the same case; see
    # ``feature_generation.explicit_link_comparison.compare_explicit_links``.
    #
    # Bare values, unlike ``case_identifiers``: the source field records
    # only the identifier, with no qualifier saying whether it is a safety
    # report id, a company number, or an authority number. A pointer
    # therefore resolves against *any* identifier the other report holds,
    # whichever of its fields that came from.
    other_case_identifiers: list[str] = Field(default_factory=list)

    @field_validator("patient_sex_values", mode="before")
    @classmethod
    def _validate_sex_values(cls, value: object) -> object:
        """Restrict structured patient sex to the E2B binary vocabulary.

        Keeps only ``male``/``female`` (canonicalised to lowercase) and drops
        every other value — ``other``, ``"unknown"``, ``"not known"``, blanks —
        as missing, per ``docs/data_contracts.md`` § Patient Sex. Offending
        entries are dropped rather than rejected so legacy frozen bundles remain
        loadable. Narrative candidates appended later via ``model_copy`` are
        already vocabulary-constrained upstream and bypass this validator.
        """
        return normalize_structured_sex(value)

    @field_validator("country_of_origin_values", mode="before")
    @classmethod
    def _validate_country_values(cls, value: object) -> object:
        """Drop uncertainty sentinels from country of origin.

        The country domain cannot be enumerated, so real values pass through
        unchanged; only values that signal absence of information (``"unknown"``,
        ``"not known"``, …) are dropped, per the empty-list-means-missing
        contract.
        """
        return drop_uncertainty_values(value)
