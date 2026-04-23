"""Merge narrative-extracted demographics into a :class:`Report`.

Deterministic downstream consumer of the per-report LLM task
:mod:`llm_dup_dev.llm.tasks.narrative_demographics`. Appends the
candidate age, sex, weight, and height values captured on a
:class:`~llm_dup_dev.models.narrative_demographics.NarrativeDemographics`
extraction to the corresponding ``patient_*_values`` lists on a
:class:`~llm_dup_dev.models.report.Report`, tagged with
``sources=["narrative_llm"]`` (configurable).

The resulting Report is consumed by the existing inconsistency logic in
:mod:`llm_dup_dev.feature_generation.inconsistencies` unchanged: min-
difference across all value pairs for numeric demographics and set-
intersection for sex produce the generous matching rule
``v_1 ∩ v_2 ≠ ∅``. Because of that rule, the moment a narrative value
lands in one of those lists it participates automatically — no further
wiring is required.

Evidence quotes on the extraction are **not** copied into the Report —
they live on the extraction artifact (which the secure repo can freeze
alongside ``NarrativeSourceCase``).

See: docs/data_contracts.md § Derived Narrative Signals
"""

from __future__ import annotations

from llm_dup_dev.models.narrative_demographics import NarrativeDemographics
from llm_dup_dev.models.report import (
    AgeValue,
    MeasurementValue,
    Report,
    SourcedValue,
)


def merge_narrative_demographics(
    report: Report,
    extraction: NarrativeDemographics,
    *,
    source_name: str = "narrative_llm",
) -> Report:
    """Return a copy of ``report`` with narrative values appended.

    Candidate demographics from ``extraction`` are appended to the
    corresponding ``patient_*_values`` lists on a *copy* of ``report``,
    each tagged with ``sources=[source_name]``. Existing structured
    entries are preserved in their original order; narrative entries
    follow them.

    No deduplication is performed: a narrative-extracted value that
    happens to equal an existing structured value produces a separate
    list entry. The generous min-difference matching means behavior is
    identical whether duplicates are collapsed or not, and defining
    equivalence for floats/units is a judgment call we avoid.

    Parameters
    ----------
    report : Report
        Structured report to enrich. Not mutated.
    extraction : NarrativeDemographics
        Per-report LLM extraction.
    source_name : str, optional
        Provenance tag for the appended entries. Defaults to
        ``"narrative_llm"``.

    Returns
    -------
    Report
        A new :class:`Report` instance with narrative values appended.
    """
    sources = [source_name]

    new_sex_values = [
        *report.patient_sex_values,
        *(
            SourcedValue(value=s.value.value, sources=sources)
            for s in extraction.sexes
        ),
    ]
    new_age_values = [
        *report.patient_age_values,
        *(
            AgeValue(value=a.value, unit=a.unit, sources=sources)
            for a in extraction.ages
        ),
    ]
    new_weight_values = [
        *report.patient_weight_values,
        *(
            MeasurementValue(value=w.value, unit=w.unit, sources=sources)
            for w in extraction.weights
        ),
    ]
    new_height_values = [
        *report.patient_height_values,
        *(
            MeasurementValue(value=h.value, unit=h.unit, sources=sources)
            for h in extraction.heights
        ),
    ]

    return report.model_copy(
        update={
            "patient_sex_values": new_sex_values,
            "patient_age_values": new_age_values,
            "patient_weight_values": new_weight_values,
            "patient_height_values": new_height_values,
        }
    )
