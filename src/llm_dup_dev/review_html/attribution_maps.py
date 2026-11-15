"""Vocabularies joining stored evidence back to the report elements it names.

The evidence review colours a table cell only when the stored assessment
names the thing in that cell. That requires knowing, exactly, the identifier
vocabularies the feature-generation modules emit — which is what this module
declares, in one place, with the producing module named for each.

Nothing here parses *display* text. Every vocabulary below is a machine
identifier written by a known producer, and each is pinned by a test so a
change on the producing side fails loudly rather than silently dropping a
highlight.
"""

from __future__ import annotations

from typing import NamedTuple

# ---------------------------------------------------------------------------
# Demographic features
# ---------------------------------------------------------------------------

#: Probability / inconsistency feature name -> demographic row label.
#:
#: Both the long form (``"patient_weight"``, emitted by
#: ``feature_generation.matching`` and ``probability``) and the short form
#: (``"weight"``, used by some inconsistency feature names) are accepted.
#: Promoted from ``compare._FEATURE_TO_DEMO_LABEL``, which still reads it.
FEATURE_TO_DEMO_LABEL: dict[str, str] = {
    "patient_sex": "Sex",
    "patient_age": "Age",
    "patient_country": "Country",
    "patient_height": "Height",
    "patient_weight": "Weight",
    # Short forms (used in some inconsistency feature names)
    "sex": "Sex",
    "age": "Age",
    "height": "Height",
    "weight": "Weight",
}

#: Row order for the evidence review's characteristics table. Fixed, never
#: reordered by evidence: a reader scanning several cases needs the same rows
#: in the same places. ``Report date`` sits among them but is permanently
#: neutral — see ``REPORT_DATE_LABEL``.
DEMOGRAPHIC_FIELD_ORDER: tuple[str, ...] = (
    "Sex",
    "Age",
    "Country",
    "Report date",
    "Height",
    "Weight",
)

#: The one row that is never evidence. ``probability.compute_probability``
#: skips ``report_date`` explicitly — it is matched for display only, because
#: it reflects administrative timing rather than patient-identifying
#: information — so an identical report date is agreement, not evidence.
REPORT_DATE_LABEL = "Report date"

#: ``Country`` likewise never carries a contribution: it has no entry in
#: ``probability._get_domain_size``'s domain map, so no ``FeatureContribution``
#: is ever emitted for it. Country agreement defines the reporting *context*.
#: Country may still appear as an *inconsistency* (``patient_country``).
COUNTRY_LABEL = "Country"

#: Severity ranking, for picking the most severe of several findings that land
#: on one element. Mirrors ``compare._SEVERITY_ORDER``.
SEVERITY_ORDER: dict[str, int] = {
    "minor": 0,
    "moderate": 1,
    "strong": 2,
    "decisive": 3,
}


# ---------------------------------------------------------------------------
# Drug-set inconsistencies (feature_generation.inconsistencies._check_drug_set)
# ---------------------------------------------------------------------------

DRUG_SET_FEATURE = "drug_set"

#: ``Inconsistency.kind`` for a drug-set finding, naming the *direction*: the
#: substances in ``terms`` are key drugs of the named report that the other
#: report does not carry.
DRUG_SET_KIND_MISSING_IN_R2 = "missing_in_r2"
DRUG_SET_KIND_MISSING_IN_R1 = "missing_in_r1"


# ---------------------------------------------------------------------------
# AE clinical-picture inconsistencies
# ---------------------------------------------------------------------------

#: ``Inconsistency.source`` values for the two interchangeable assessors.
AE_RULE_SOURCE = "rule_ae_clinical_picture"
AE_LLM_SOURCE = "llm_ae_clinical_picture"

#: ``Inconsistency.suppressed_by`` value the LLM assessor writes when a
#: decisive global mismatch collapses every subordinate finding.
SUPPRESSED_BY_DECISIVE_GLOBAL = "decisive_global_mismatch"

#: Directional feature names from the rule-based Step 2 assessor. The terms
#: these carry are verbatim preferred-term names of the *source* report.
AE_UNMATCHED_BURDEN_R1_TO_R2 = "ae_clinical_picture:unmatched_burden:r1_to_r2"
AE_UNMATCHED_BURDEN_R2_TO_R1 = "ae_clinical_picture:unmatched_burden:r2_to_r1"

#: Which report the terms of a directional AE finding belong to: 1 or 2.
AE_FEATURE_TO_SOURCE_REPORT: dict[str, int] = {
    AE_UNMATCHED_BURDEN_R1_TO_R2: 1,
    AE_UNMATCHED_BURDEN_R2_TO_R1: 2,
}


# ---------------------------------------------------------------------------
# Clinical-date finding entity labels
# (feature_generation.clinical_date_inconsistencies)
# ---------------------------------------------------------------------------

OVERALL_AE_ONSET_LABEL = "overall_ae_onset"
DRUG_START_PREFIX = "drug_start:"
AE_ONSET_PREFIX = "ae_onset:"

#: The decisive report-date impossibility label is free-form and embeds both
#: report ids: ``"report_date(<rid>) < overall_ae_onset(<rid>)"``. It names no
#: drug or adverse event, so it is attributed at section level only, never to
#: a table cell. Matched by this prefix rather than parsed.
REPORT_DATE_IMPOSSIBILITY_PREFIX = "report_date("


class EntityKind(NamedTuple):
    """What a clinical-date finding's ``entity_label`` refers to."""

    kind: str  # "drug" | "ae" | "overall_ae_onset" | "report_date" | "unknown"
    key: str   # normalised entity key; "" when the kind names no entity


def parse_entity_label(entity_label: str) -> EntityKind:
    """Classify a ``ClinicalDateFindingSummary.entity_label``.

    The producing module emits exactly four forms (see its *Entity labels*
    section): ``"overall_ae_onset"``, ``"drug_start:<substance_key>"``,
    ``"ae_onset:<pt_key>"``, and the decisive
    ``"report_date(<rid>) < overall_ae_onset(<rid>)"``. The keys in the two
    prefixed forms come from ``normalize_entity_key``, which is
    ``name.strip().lower()`` — identical to ``normalize_drug_key`` and
    ``normalize_ae_key``, so they join exactly onto the grouped rows.

    Note that ``drug_end:`` never occurs: drug end dates are downgrade-only
    evidence and are never a finding's source entity.

    An unrecognised label yields ``kind="unknown"`` so the caller can record
    a gap rather than guess.
    """
    if entity_label == OVERALL_AE_ONSET_LABEL:
        return EntityKind("overall_ae_onset", "")
    if entity_label.startswith(DRUG_START_PREFIX):
        return EntityKind("drug", entity_label[len(DRUG_START_PREFIX) :])
    if entity_label.startswith(AE_ONSET_PREFIX):
        return EntityKind("ae", entity_label[len(AE_ONSET_PREFIX) :])
    if entity_label.startswith(REPORT_DATE_IMPOSSIBILITY_PREFIX):
        return EntityKind("report_date", "")
    return EntityKind("unknown", "")


__all__ = [
    "AE_FEATURE_TO_SOURCE_REPORT",
    "AE_LLM_SOURCE",
    "AE_ONSET_PREFIX",
    "AE_RULE_SOURCE",
    "AE_UNMATCHED_BURDEN_R1_TO_R2",
    "AE_UNMATCHED_BURDEN_R2_TO_R1",
    "COUNTRY_LABEL",
    "DEMOGRAPHIC_FIELD_ORDER",
    "DRUG_SET_FEATURE",
    "DRUG_SET_KIND_MISSING_IN_R1",
    "DRUG_SET_KIND_MISSING_IN_R2",
    "DRUG_START_PREFIX",
    "EntityKind",
    "FEATURE_TO_DEMO_LABEL",
    "OVERALL_AE_ONSET_LABEL",
    "REPORT_DATE_IMPOSSIBILITY_PREFIX",
    "REPORT_DATE_LABEL",
    "SEVERITY_ORDER",
    "SUPPRESSED_BY_DECISIVE_GLOBAL",
    "parse_entity_label",
]
