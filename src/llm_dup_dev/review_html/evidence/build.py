"""Build an :class:`EvidenceCaseVM` from a report pair and its stored evidence.

The builder performs no comparison of its own. It lays out both reports'
values as neutral cells, then runs a series of *attribution passes*, each of
which reads one field of the stored assessment and colours the elements that
field names. A cell is green because ``matching_drugs`` or a
``FeatureContribution`` named it; red because an ``Inconsistency`` or a
``ClinicalDateFindingSummary`` named it; neutral because nothing did.

That is the difference from the detailed format, which colours by comparing
displayed values. Here a value the two reports share is neutral unless the
assessment scored it, and a value only one report carries is neutral unless
the assessment flagged it — which is what the reviewer needs to see.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field as dc_field
from enum import Enum

from llm_dup_dev.data.offline import FrozenPairBundle, FrozenScopeStats
from llm_dup_dev.models.classification import ClassificationResult
from llm_dup_dev.models.enums import AnchorType, ReportingContextLevel
from llm_dup_dev.models.explicit_link import ExplicitLinkVerdict
from llm_dup_dev.models.narrative_demographics import NarrativeDemographics
from llm_dup_dev.models.narrative_source_case import SourceCaseVerdict
from llm_dup_dev.models.pair_features import (
    ClinicalDateFindingSummary,
    FeatureContribution,
    Inconsistency,
    PairFeatures,
)
from llm_dup_dev.models.report import (
    AdverseEvent,
    AgeValue,
    DateValue,
    Drug,
    MeasurementValue,
    PartialDate,
    Report,
    SourcedValue,
)

from ..attribution_maps import (
    AE_FEATURE_TO_SOURCE_REPORT,
    AE_LLM_SOURCE,
    COUNTRY_LABEL,
    DRUG_SET_FEATURE,
    DRUG_SET_KIND_MISSING_IN_R1,
    DRUG_SET_KIND_MISSING_IN_R2,
    FEATURE_TO_DEMO_LABEL,
    REPORT_DATE_LABEL,
    SEVERITY_ORDER,
    parse_entity_label,
)
from ..grouping import grouped_adverse_events, grouped_drugs
from ..normalize import normalize_ae_key, normalize_drug_key
from .models import (
    AEEvidenceRow,
    AssessmentRowVM,
    Attribution,
    AttributionKind,
    CharacteristicRow,
    DiscrepancyVM,
    DrugEntryVM,
    DrugEvidenceRow,
    EvidenceCaseVM,
    EvidenceCell,
    EvidenceGap,
    EvidenceGapCode,
    EvidenceRole,
    EvidenceSource,
    EvidenceTextVM,
    FreqRowVM,
    MatchingFeatureRow,
    OutcomeTileVM,
    PresenceVM,
    ReconciliationVM,
    ReferenceVerdictVM,
    SharedSetVM,
    TileState,
    ValueKind,
    stronger_role,
)
from .settings import EvidenceRunSettings
from .summary import (
    build_assessment_summary,
    independence_clause,
    same_case_clause,
)

#: Shown where a report supplied no value: nothing at all. An unpopulated
#: field is absence of evidence rather than evidence of absence, so naming it
#: would assert more than the data supports.
_MISSING_TEXT = ""


# ---------------------------------------------------------------------------
# Plain-text value formatting
#
# The evidence format escapes everything it renders, so these return plain
# text rather than the markup the detailed format's formatters produce.
# Multiple reported values are joined, never dropped: a report carrying two
# candidate ages has two candidate ages, and collapsing them would hide a
# discrepancy the assessment may well have scored.
# ---------------------------------------------------------------------------


def _number_text(value: float) -> str:
    """Drop a trailing ``.0`` so ``54.0`` reads as ``54``."""
    return str(int(value)) if value == int(value) else str(value)


def _dedupe(items: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return list(seen)


def _sourced_text(values: list[SourcedValue]) -> str:
    return "; ".join(_dedupe([v.value for v in values]))


def _age_text(values: list[AgeValue]) -> str:
    return "; ".join(
        _dedupe([f"{_number_text(v.value)} {v.unit}" for v in values])
    )


def _measurement_text(values: list[MeasurementValue]) -> str:
    return "; ".join(
        _dedupe([f"{_number_text(v.value)} {v.unit}" for v in values])
    )


def _date_text(values: list[DateValue]) -> str:
    return "; ".join(_dedupe([v.value for v in values]))


def _cell(text: str, kind: ValueKind) -> EvidenceCell:
    """A neutral cell, or a missing one when there is no text."""
    if not text:
        return EvidenceCell(text=_MISSING_TEXT, kind=kind, missing=True)
    return EvidenceCell(text=text, kind=kind)


def _date_cell(date: PartialDate | None, kind: ValueKind) -> EvidenceCell | None:
    """A cell for a partial date, or ``None`` when the field is absent.

    ``None`` rather than a missing cell: for a drug's end date, "not reported"
    and "no end date field here" are the same thing to a reader, and the
    prototype renders the absence as a dash inside the date range.
    """
    if date is None:
        return None
    return EvidenceCell(text=date.value, kind=kind)


# ---------------------------------------------------------------------------
# Pass 1 — characteristics
# ---------------------------------------------------------------------------


def _narrative_snippets(
    extraction: NarrativeDemographics | None, attr: str
) -> list[str]:
    if extraction is None:
        return []
    return [c.evidence for c in getattr(extraction, attr) if c.evidence]


def _build_characteristic_rows(
    r1: Report,
    r2: Report,
    nd1: NarrativeDemographics | None,
    nd2: NarrativeDemographics | None,
) -> list[CharacteristicRow]:
    """Lay out the demographic and report-metadata rows, all neutral.

    ``Report date`` is included and stays neutral for the rest of the build:
    the probability computation excludes it deliberately, because it reflects
    when a report was filed rather than anything about the patient. Two
    reports filed the same day have not thereby matched. It can still turn red
    via the decisive report-date-vs-onset impossibility rule, which is
    attributed at section level.

    ``Country`` likewise never carries a contribution — it defines the
    reporting context within which rarity is judged — but it *can* be an
    inconsistency, so pass 6 may colour it.
    """
    rows = [
        CharacteristicRow(
            label="Sex",
            left=_cell(_sourced_text(r1.patient_sex_values), ValueKind.SEX),
            right=_cell(_sourced_text(r2.patient_sex_values), ValueKind.SEX),
            left_evidence=_narrative_snippets(nd1, "sexes"),
            right_evidence=_narrative_snippets(nd2, "sexes"),
        ),
        CharacteristicRow(
            label="Age",
            left=_cell(_age_text(r1.patient_age_values), ValueKind.AGE),
            right=_cell(_age_text(r2.patient_age_values), ValueKind.AGE),
            left_evidence=_narrative_snippets(nd1, "ages"),
            right_evidence=_narrative_snippets(nd2, "ages"),
        ),
        CharacteristicRow(
            label=COUNTRY_LABEL,
            left=_cell(
                _sourced_text(r1.country_of_origin_values), ValueKind.COUNTRY
            ),
            right=_cell(
                _sourced_text(r2.country_of_origin_values), ValueKind.COUNTRY
            ),
        ),
        CharacteristicRow(
            label=REPORT_DATE_LABEL,
            left=_cell(_date_text(r1.report_date_values), ValueKind.REPORT_DATE),
            right=_cell(_date_text(r2.report_date_values), ValueKind.REPORT_DATE),
        ),
    ]
    if r1.patient_height_values or r2.patient_height_values:
        rows.append(
            CharacteristicRow(
                label="Height",
                left=_cell(
                    _measurement_text(r1.patient_height_values), ValueKind.HEIGHT
                ),
                right=_cell(
                    _measurement_text(r2.patient_height_values), ValueKind.HEIGHT
                ),
                left_evidence=_narrative_snippets(nd1, "heights"),
                right_evidence=_narrative_snippets(nd2, "heights"),
            )
        )
    if r1.patient_weight_values or r2.patient_weight_values:
        rows.append(
            CharacteristicRow(
                label="Weight",
                left=_cell(
                    _measurement_text(r1.patient_weight_values), ValueKind.WEIGHT
                ),
                right=_cell(
                    _measurement_text(r2.patient_weight_values), ValueKind.WEIGHT
                ),
                left_evidence=_narrative_snippets(nd1, "weights"),
                right_evidence=_narrative_snippets(nd2, "weights"),
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Pass 2 — drug and adverse-event rows, uncoloured
# ---------------------------------------------------------------------------

_DRUG_ROLE_TEXT = {
    "suspected": "Suspected",
    "concomitant": "Concomitant",
    "interacting": "Interacting",
}


def _drug_entry(drug: Drug) -> DrugEntryVM:
    """One reported drug record, with every detail it carries.

    Role, form and strength are kept because they are reported information a
    reviewer wants, and neutral because they are not evidence unless the
    assessment says so. Both date boundaries are kept even when one is absent.
    """
    role_text = (
        _DRUG_ROLE_TEXT.get(drug.role.value, drug.role.value)
        if drug.role is not None
        else ""
    )
    return DrugEntryVM(
        drug_role=(
            EvidenceCell(text=role_text, kind=ValueKind.DRUG_ROLE)
            if role_text
            else None
        ),
        form=(
            EvidenceCell(text=drug.form, kind=ValueKind.DRUG_FORM)
            if drug.form
            else None
        ),
        strength=(
            EvidenceCell(text=drug.strength, kind=ValueKind.DRUG_STRENGTH)
            if drug.strength
            else None
        ),
        start_date=_date_cell(drug.start_date, ValueKind.DRUG_START_DATE),
        end_date=_date_cell(drug.end_date, ValueKind.DRUG_END_DATE),
    )


def _presence(has_left: bool, has_right: bool) -> PresenceVM:
    if has_left and has_right:
        return PresenceVM(label="Both")
    return PresenceVM(label="A only" if has_left else "B only")


#: Roles that make a drug a *key* drug — the ones the drug-set inconsistency
#: check considers. A concomitant medication listed by one report only is
#: ordinary; a suspected drug listed by one report only is worth reading first.
_KEY_DRUG_ROLES = {"suspected", "interacting"}


def _is_key_drug(drugs: list[Drug]) -> bool:
    return any(
        d.role is not None and d.role.value in _KEY_DRUG_ROLES for d in drugs
    )


def _build_drug_rows(r1: Report, r2: Report) -> list[DrugEvidenceRow]:
    """Union-group both reports' drugs; every cell starts neutral.

    Each side's name cell carries that report's own spelling of the substance.
    They are separate cells so the reported text is preserved on both sides,
    and they share the row's attributions because the assessment matched the
    substance rather than either report's rendering of it.
    """
    rows: list[DrugEvidenceRow] = []
    for key, left, right in grouped_drugs(r1.drugs, r2.drugs):
        display = left[0].substance if left else right[0].substance
        rows.append(
            DrugEvidenceRow(
                name=EvidenceCell(text=display, kind=ValueKind.DRUG_SUBSTANCE),
                left_name=(
                    EvidenceCell(
                        text=left[0].substance, kind=ValueKind.DRUG_SUBSTANCE
                    )
                    if left
                    else None
                ),
                right_name=(
                    EvidenceCell(
                        text=right[0].substance, kind=ValueKind.DRUG_SUBSTANCE
                    )
                    if right
                    else None
                ),
                left_entries=[_drug_entry(d) for d in left],
                right_entries=[_drug_entry(d) for d in right],
                presence=_presence(bool(left), bool(right)),
                sort_key=key,
                presence_rank=0 if (left and right) else (1 if left else 2),
                key_rank=0 if _is_key_drug([*left, *right]) else 1,
            )
        )
    return rows


def _ae_onset_cells(events: list[AdverseEvent]) -> list[EvidenceCell]:
    cells = [
        _date_cell(ae.start_date, ValueKind.AE_ONSET_DATE) for ae in events
    ]
    return [c for c in cells if c is not None]


def _build_ae_rows(r1: Report, r2: Report) -> list[AEEvidenceRow]:
    """Union-group both reports' adverse events; every cell starts neutral."""
    rows: list[AEEvidenceRow] = []
    for key, left, right in grouped_adverse_events(
        r1.adverse_events, r2.adverse_events
    ):
        display = left[0].pt_name if left else right[0].pt_name
        rows.append(
            AEEvidenceRow(
                term=EvidenceCell(text=display, kind=ValueKind.AE_TERM),
                left_term=(
                    EvidenceCell(text=left[0].pt_name, kind=ValueKind.AE_TERM)
                    if left
                    else None
                ),
                right_term=(
                    EvidenceCell(text=right[0].pt_name, kind=ValueKind.AE_TERM)
                    if right
                    else None
                ),
                presence=_presence(bool(left), bool(right)),
                presence_rank=0 if (left and right) else (1 if left else 2),
                left_onsets=_ae_onset_cells(left),
                right_onsets=_ae_onset_cells(right),
                sort_key=key,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Pass 3 — shared sets
# ---------------------------------------------------------------------------


def _anchor_covers_drugs(anchor_type: AnchorType) -> bool:
    return anchor_type in (AnchorType.DRUGS, AnchorType.DRUGS_AES)


def _anchor_covers_aes(anchor_type: AnchorType) -> bool:
    return anchor_type in (AnchorType.AES, AnchorType.DRUGS_AES)


def _attach_matching_sets(
    drugs: list[DrugEvidenceRow],
    aes: list[AEEvidenceRow],
    features: PairFeatures,
) -> None:
    """Green the members of the shared drug and adverse-event sets.

    A set is either part of the anchor definition or an additional matching
    feature — never both, because ``compute_probability`` excludes an
    anchor-defining set from the contributions so it is not counted twice.
    The role records which, so the page and the summary can say that the
    shared adverse events *defined* the anchor rather than adding to the
    score.

    Both roles render green. A shared term is positive evidence either way;
    what differs is whether it narrowed the comparison population or raised
    the score.
    """
    drug_role = (
        EvidenceRole.ANCHOR
        if _anchor_covers_drugs(features.anchor.type)
        else EvidenceRole.MATCHING
    )
    ae_role = (
        EvidenceRole.ANCHOR
        if _anchor_covers_aes(features.anchor.type)
        else EvidenceRole.MATCHING
    )

    drug_keys = {normalize_drug_key(d) for d in features.matching_drugs}
    ae_keys = {normalize_ae_key(a) for a in features.matching_AEs}

    for row in drugs:
        if normalize_drug_key(row.name.text) in drug_keys:
            _mark(
                row.name,
                drug_role,
                Attribution(
                    kind=(
                        AttributionKind.ANCHOR_MEMBER
                        if drug_role is EvidenceRole.ANCHOR
                        else AttributionKind.SHARED_SET_MEMBER
                    ),
                    source=(
                        EvidenceSource.ANCHOR
                        if drug_role is EvidenceRole.ANCHOR
                        else EvidenceSource.MATCHING_SET
                    ),
                    feature="drug_set",
                    note=(
                        "part of the shared drug set defining the anchor"
                        if drug_role is EvidenceRole.ANCHOR
                        else "part of the shared drug set"
                    ),
                ),
            )

    for ae_row in aes:
        if normalize_ae_key(ae_row.term.text) in ae_keys:
            _mark(
                ae_row.term,
                ae_role,
                Attribution(
                    kind=(
                        AttributionKind.ANCHOR_MEMBER
                        if ae_role is EvidenceRole.ANCHOR
                        else AttributionKind.SHARED_SET_MEMBER
                    ),
                    source=(
                        EvidenceSource.ANCHOR
                        if ae_role is EvidenceRole.ANCHOR
                        else EvidenceSource.MATCHING_SET
                    ),
                    feature="ae_set",
                    note=(
                        "part of the shared adverse event set defining the anchor"
                        if ae_role is EvidenceRole.ANCHOR
                        else "part of the shared adverse event set"
                    ),
                ),
            )


def _mark(
    cell: EvidenceCell, role: EvidenceRole, attribution: Attribution
) -> None:
    """Attach an attribution and promote the cell's role if that is stronger.

    Every attribution is kept even when it does not change the colour, so a
    tooltip can show that a cell carried both a match and an inconsistency.
    """
    cell.attributions.append(attribution)
    if cell.missing:
        # An absence is not evidence. The attribution is kept for the record;
        # the colour is not.
        return
    cell.role = stronger_role(cell.role, role)


# ---------------------------------------------------------------------------
# Pass 4 — demographic contributions
# ---------------------------------------------------------------------------


def _contribution_decades(fc: FeatureContribution) -> float | None:
    """``-log10(p_f)``, or ``None`` for a probability that cannot be logged."""
    if fc.p_f <= 0:
        return None
    return -math.log10(fc.p_f)


def _attach_demographic_contributions(
    rows: list[CharacteristicRow], features: PairFeatures
) -> None:
    """Green the demographic rows that contributed to the matching score.

    The join is on the feature name, so no value comparison is involved: a
    row is green because the assessment recorded a contribution for that
    feature, full stop. ``report_date`` never appears among the contributions
    and ``patient_country`` has no domain size, so neither is reachable here.
    """
    by_label = {r.label: r for r in rows}
    for fc in features.probability.feature_contributions:
        label = FEATURE_TO_DEMO_LABEL.get(fc.feature)
        if label is None:
            continue
        row = by_label.get(label)
        if row is None:
            continue
        decades = _contribution_decades(fc)
        attribution = Attribution(
            kind=AttributionKind.MATCHING_FEATURE,
            source=EvidenceSource.FEATURE_CONTRIBUTION,
            feature=fc.feature,
            contribution=decades,
            note="matching evidence"
            + (f" (+{decades:.1f})" if decades is not None else ""),
        )
        _mark(row.left, EvidenceRole.MATCHING, attribution)
        _mark(row.right, EvidenceRole.MATCHING, attribution.model_copy())


# ---------------------------------------------------------------------------
# Pass 5 — matched clinical dates
# ---------------------------------------------------------------------------


def _truncate(value: str, resolution: str | None) -> str:
    """Truncate an ISO date string to ``day`` / ``month`` / ``year``."""
    if resolution == "year":
        return value[:4]
    if resolution == "month":
        return value[:7]
    return value


def _attach_clinical_date_matches(
    drugs: list[DrugEvidenceRow],
    aes: list[AEEvidenceRow],
    features: PairFeatures,
    gaps: list[EvidenceGap],
) -> None:
    """Green every date cell whose value the assessment scored as a match.

    Matched clinical dates are recorded per *value*, not per drug or event —
    one contribution for ``2002-01-04``, whichever fields it came from — so
    every cell holding that value is greened on both sides. For a drug with
    two treatment periods that is the correct outcome: the period whose dates
    matched is marked and the period whose dates did not is left neutral.

    A contribution written before ``matched_value`` existed carries the date
    only inside its description. Rather than parse that string, this records a
    gap: the page then says per-date highlighting is unavailable for the run,
    which is honest, where a silent absence of green would not be.
    """
    date_contributions = [
        fc
        for fc in features.probability.feature_contributions
        if fc.feature == "clinical_date"
    ]
    if not date_contributions:
        return

    unattributed = [fc for fc in date_contributions if fc.matched_value is None]
    if unattributed:
        gaps.append(
            EvidenceGap(
                code=EvidenceGapCode.CLINICAL_DATE_VALUE_MISSING,
                message=(
                    f"{len(unattributed)} matched clinical "
                    f"date{'s' if len(unattributed) != 1 else ''} could not be "
                    "attributed to a specific date field, because this run was "
                    "saved before the matched value was recorded alongside its "
                    "contribution. The dates still count towards the matching "
                    "score shown below; they are simply not highlighted in the "
                    "tables."
                ),
            )
        )

    for fc in date_contributions:
        if fc.matched_value is None:
            continue
        decades = _contribution_decades(fc)
        note = "matched clinical date" + (
            f" (+{decades:.1f})" if decades is not None else ""
        )
        for cell in _date_cells(drugs, aes):
            if _truncate(cell.text, fc.value_resolution) == fc.matched_value:
                _mark(
                    cell,
                    EvidenceRole.MATCHING,
                    Attribution(
                        kind=AttributionKind.MATCHING_FEATURE,
                        source=EvidenceSource.FEATURE_CONTRIBUTION,
                        feature="clinical_date",
                        contribution=decades,
                        note=note,
                    ),
                )


def _date_cells(
    drugs: list[DrugEvidenceRow], aes: list[AEEvidenceRow]
) -> list[EvidenceCell]:
    """Every date cell in the drug and adverse-event tables, both sides."""
    cells: list[EvidenceCell] = []
    for row in drugs:
        for entry in [*row.left_entries, *row.right_entries]:
            for cell in (entry.start_date, entry.end_date):
                if cell is not None:
                    cells.append(cell)
    for ae_row in aes:
        cells.extend(ae_row.left_onsets)
        cells.extend(ae_row.right_onsets)
    return cells


# ---------------------------------------------------------------------------
# Pass 6 — inconsistencies
# ---------------------------------------------------------------------------

#: Readable names for the inconsistency features a reader should not have to
#: decode. Anything absent falls back to a tidied form of the feature string.
_FEATURE_READABLE: dict[str, str] = {
    "patient_age": "Age",
    "patient_sex": "Sex",
    "patient_height": "Height",
    "patient_weight": "Weight",
    "patient_country": "Country of origin",
    "age": "Age",
    "sex": "Sex",
    "height": "Height",
    "weight": "Weight",
    "drug_set": "Drugs",
}


def _readable_feature(feature: str) -> str:
    """A reader-facing name for an inconsistency's feature."""
    if feature in _FEATURE_READABLE:
        return _FEATURE_READABLE[feature]
    if feature.startswith("ae_clinical_picture"):
        return "Adverse event picture"
    if feature.startswith("source_case_"):
        return "Source and case identifiers"
    return feature.replace("_", " ").capitalize()


def _attach_inconsistencies(
    characteristics: list[CharacteristicRow],
    drugs: list[DrugEvidenceRow],
    aes: list[AEEvidenceRow],
    features: PairFeatures,
    gaps: list[EvidenceGap],
) -> None:
    """Red the elements that a retained inconsistency names.

    Suppressed findings are deliberately *not* coloured. A suppressed finding
    contributed nothing to the penalty — the decision was made as if it were
    absent — so showing it as conflicting evidence would misrepresent the
    assessment. It is still recorded as an attribution and listed as a
    suppressed discrepancy, because a finding the assessor made and then set
    aside is something a reviewer should be able to see.
    """
    by_label = {r.label: r for r in characteristics}
    worst_demo: dict[str, Inconsistency] = {}

    for inc in features.inconsistencies:
        if inc.suppressed:
            _attach_suppressed(inc, drugs, aes)
            continue

        label = FEATURE_TO_DEMO_LABEL.get(inc.feature)
        if label is not None:
            current = worst_demo.get(label)
            if current is None or _is_worse(inc, current):
                worst_demo[label] = inc
            continue

        if inc.feature == DRUG_SET_FEATURE:
            _attach_drug_set(inc, drugs, gaps)
            continue

        if inc.feature in AE_FEATURE_TO_SOURCE_REPORT or inc.source == AE_LLM_SOURCE:
            _attach_ae_terms(inc, aes)
            continue

        # Nothing in the tables corresponds to this finding (a source/case
        # verdict, for instance). It is still listed in the assessment
        # section; the gap records that no cell could carry it.
        gaps.append(
            EvidenceGap(
                code=EvidenceGapCode.UNMAPPED_INCONSISTENCY,
                message=(
                    f"The {_readable_feature(inc.feature)} inconsistency is "
                    "reported in the assessment section only; it does not name "
                    "a value shown in the comparison tables."
                ),
            )
        )

    for label, inc in worst_demo.items():
        row = by_label.get(label)
        if row is None:
            continue
        attribution = _inconsistency_attribution(inc)
        _mark(row.left, EvidenceRole.INCONSISTENT, attribution)
        _mark(row.right, EvidenceRole.INCONSISTENT, attribution.model_copy())


def _is_worse(candidate: Inconsistency, current: Inconsistency) -> bool:
    return SEVERITY_ORDER.get(candidate.severity.value, -1) > SEVERITY_ORDER.get(
        current.severity.value, -1
    )


def _inconsistency_attribution(
    inc: Inconsistency, *, suppressed: bool = False
) -> Attribution:
    return Attribution(
        kind=(
            AttributionKind.SUPPRESSED_FINDING
            if suppressed
            else AttributionKind.RETAINED_INCONSISTENCY
        ),
        source=EvidenceSource.INCONSISTENCY,
        feature=inc.feature,
        severity=inc.severity.value,
        note=(
            f"superseded {inc.severity.value} finding"
            if suppressed
            else f"{inc.severity.value} inconsistency"
        ),
    )


def _attach_drug_set(
    inc: Inconsistency,
    drugs: list[DrugEvidenceRow],
    gaps: list[EvidenceGap],
) -> None:
    """Red the substances a drug-set finding names.

    The names come from ``terms``; ``kind`` says which report they are key
    drugs of. A run saved before those fields were populated carries the
    substances only inside the description, which this does not parse — it
    records a gap instead, and the finding still appears in the assessment
    section.
    """
    if not inc.terms:
        gaps.append(
            EvidenceGap(
                code=EvidenceGapCode.DRUG_SET_TERMS_MISSING,
                message=(
                    "A drug-set inconsistency is reported in the assessment "
                    "section without per-substance highlighting: this run was "
                    "saved before the offending substances were recorded as "
                    "structured terms."
                ),
            )
        )
        return

    wanted = {normalize_drug_key(t) for t in inc.terms}
    attribution = _inconsistency_attribution(inc)
    for row in drugs:
        if normalize_drug_key(row.name.text) in wanted:
            _mark(row.name, EvidenceRole.INCONSISTENT, attribution.model_copy())


def _attach_ae_terms(inc: Inconsistency, aes: list[AEEvidenceRow]) -> None:
    """Red the preferred terms an adverse-event finding names.

    The rule-based assessor's directional findings carry the source report in
    the feature name; the LLM assessor's do not. Either way the term cell is
    what turns red — the row names one preferred term, so there is no
    per-side cell to colour.
    """
    if not inc.terms:
        return
    wanted = {normalize_ae_key(t) for t in inc.terms}
    attribution = _inconsistency_attribution(inc)
    for row in aes:
        if normalize_ae_key(row.term.text) in wanted:
            _mark(row.term, EvidenceRole.INCONSISTENT, attribution.model_copy())


def _attach_suppressed(
    inc: Inconsistency,
    drugs: list[DrugEvidenceRow],
    aes: list[AEEvidenceRow],
) -> None:
    """Record a suppressed finding on its elements without colouring them."""
    attribution = _inconsistency_attribution(inc, suppressed=True)
    if not inc.terms:
        return
    wanted_aes = {normalize_ae_key(t) for t in inc.terms}
    for row in aes:
        if normalize_ae_key(row.term.text) in wanted_aes:
            row.term.attributions.append(attribution.model_copy())
    wanted_drugs = {normalize_drug_key(t) for t in inc.terms}
    for drug_row in drugs:
        if normalize_drug_key(drug_row.name.text) in wanted_drugs:
            drug_row.name.attributions.append(attribution.model_copy())


# ---------------------------------------------------------------------------
# Pass 7 — clinical-date findings
# ---------------------------------------------------------------------------


def _attach_clinical_date_findings(
    drugs: list[DrugEvidenceRow],
    aes: list[AEEvidenceRow],
    features: PairFeatures,
    gaps: list[EvidenceGap],
) -> None:
    """Red the date cells a retained clinical-date finding names.

    The severity used is the finding's *final* severity — the one that was
    counted. A finding downgraded from strong to moderate is still a retained
    inconsistency and is still red; only its severity moved. A finding
    downgraded all the way out of the assessment is absent from
    ``clinical_date_findings`` entirely, so nothing here can colour it.

    The stored summary does not say which report a finding's source date came
    from, so both sides' cells holding either of the two dates are marked.
    That is deliberately conservative: it never claims a side the record does
    not support.
    """
    for finding in features.clinical_date_findings:
        entity = parse_entity_label(finding.entity_label)
        if entity.kind == "unknown":
            gaps.append(
                EvidenceGap(
                    code=EvidenceGapCode.UNKNOWN_ENTITY_LABEL,
                    message=(
                        "A clinical-date inconsistency is reported in the "
                        "assessment section only: its entity label "
                        f"({finding.entity_label!r}) is not one this review "
                        "knows how to locate in the tables."
                    ),
                )
            )
            continue
        if entity.kind in ("overall_ae_onset", "report_date"):
            # Case-level, not tied to one drug or event; shown in the
            # assessment section.
            continue

        attribution = _date_finding_attribution(finding)
        values = {finding.source_date, finding.matched_date}
        if entity.kind == "drug":
            for row in drugs:
                if row.sort_key != entity.key:
                    continue
                for e in [*row.left_entries, *row.right_entries]:
                    for cell in (e.start_date, e.end_date):
                        if cell is not None and cell.text in values:
                            _mark(
                                cell,
                                EvidenceRole.INCONSISTENT,
                                attribution.model_copy(),
                            )
        else:
            for ae_row in aes:
                if ae_row.sort_key != entity.key:
                    continue
                for cell in [*ae_row.left_onsets, *ae_row.right_onsets]:
                    if cell.text in values:
                        _mark(
                            cell,
                            EvidenceRole.INCONSISTENT,
                            attribution.model_copy(),
                        )


def _date_finding_attribution(
    finding: ClinicalDateFindingSummary,
) -> Attribution:
    downgraded = bool(finding.downgrades)
    return Attribution(
        kind=(
            AttributionKind.DOWNGRADED_RETAINED
            if downgraded
            else AttributionKind.RETAINED_INCONSISTENCY
        ),
        source=EvidenceSource.CLINICAL_DATE_FINDING,
        feature=finding.entity_label,
        severity=finding.severity.value,
        note=(
            f"{finding.severity.value} date inconsistency"
            + (" (reduced severity)" if downgraded else "")
        ),
    )


# ---------------------------------------------------------------------------
# Pass 8 — row roles and ordering
# ---------------------------------------------------------------------------


def _combine(cells: list[EvidenceCell]) -> EvidenceRole:
    """The role of a row, from the strongest role among its cells."""
    role = EvidenceRole.NEUTRAL
    for cell in cells:
        if cell.missing:
            continue
        role = stronger_role(role, cell.role)
    return role


def _mirror(source: EvidenceCell, target: EvidenceCell | None) -> None:
    """Give a per-side name cell the row's role and attributions.

    The two side cells show each report's own spelling of the same substance
    or term. The assessment matched the entity, not a spelling, so both sides
    carry the same verdict.
    """
    if target is None:
        return
    target.role = source.role
    target.attributions = [a.model_copy() for a in source.attributions]


def _finalise_drug_rows(rows: list[DrugEvidenceRow]) -> list[DrugEvidenceRow]:
    """Set each row's role and presence colour, then order the table.

    Rows are ordered by *presence* — both reports, then report A only, then
    report B only — because that is the first question a reader asks of a drug
    table. Within the one-sided groups, key drugs (suspected or interacting)
    come first: a suspected drug listed by one report only is worth reading
    before a concomitant one. Rows present in both keep alphabetical order, so
    the two reports' shared medication list reads as a list.

    Evidence is carried by colour rather than by position. A row's role is
    still its strongest negative evidence, so a row carrying both a matched
    date and a flagged substance shows red on the substance and green on the
    date — the inconsistency is never hidden because the substance matched.
    """
    for row in rows:
        cells = [row.name]
        for entry in [*row.left_entries, *row.right_entries]:
            cells.extend(entry.cells)
        row.row_role = _combine(cells)
        row.presence.role = row.row_role
        _mirror(row.name, row.left_name)
        _mirror(row.name, row.right_name)
    return sorted(
        rows,
        key=lambda r: (
            r.presence_rank,
            # Key-drug precedence applies only where a drug is one-sided;
            # a shared medication list stays alphabetical.
            r.key_rank if r.presence_rank else 0,
            r.sort_key,
        ),
    )


def _ae_evidence_rank(row: AEEvidenceRow) -> int:
    """Where a one-sided adverse event sits within its presence group.

    The two one-sided groups are ordered in opposite directions — report A's
    flagged terms first, report B's flagged terms last — so that the terms
    neither report flagged meet in the middle:

        Both reports          (shared pattern)
        A only, flagged
        A only, not flagged   ]  one unbroken band of terms
        B only, not flagged   ]  vigiTwin did not treat as inconsistent
        B only, flagged

    A reader scanning the table then sees the retained inconsistencies at the
    two edges of the one-sided region rather than scattered through it, and
    the merely-unmatched terms as a single block rather than twice over.
    """
    if row.presence_rank == 1:  # report A only: flagged first
        return 0 if row.row_role is EvidenceRole.INCONSISTENT else 1
    if row.presence_rank == 2:  # report B only: flagged last
        return 1 if row.row_role is EvidenceRole.INCONSISTENT else 0
    return 0


def _finalise_ae_rows(rows: list[AEEvidenceRow]) -> list[AEEvidenceRow]:
    """Set each row's role and presence colour, then order the table.

    Primary order is presence — both reports, then report A only, then report
    B only. Within the one-sided groups the evidence order mirrors, so the
    unflagged terms form a single central band; see :func:`_ae_evidence_rank`.
    """
    for row in rows:
        cells = [row.term, *row.left_onsets, *row.right_onsets]
        row.row_role = _combine(cells)
        row.presence.role = row.row_role
        _mirror(row.term, row.left_term)
        _mirror(row.term, row.right_term)
    return sorted(
        rows,
        key=lambda r: (r.presence_rank, _ae_evidence_rank(r), r.sort_key),
    )


# ---------------------------------------------------------------------------
# Context and matching evidence
# ---------------------------------------------------------------------------

_CONTEXT_PHRASE = {
    ReportingContextLevel.COUNTRY_YEAR: "the selected country-year context",
    ReportingContextLevel.COUNTRY: "the selected country context",
    ReportingContextLevel.GLOBAL: "the global context",
}

_SCOPE_LABEL = {
    "global_stats": "Global",
    "country_stats": "Country (all years)",
    "country_year_stats": "Country-year",
}


def context_phrase(level: ReportingContextLevel) -> str:
    """A reader-facing name for a reporting context.

    Names the *level*, never the country or the year. The level is what makes
    a count interpretable; the identity of the country is reporting metadata
    that an internal review shows in its own row and a publication export
    masks. Keeping it out of the prose means neither version needs different
    sentences.
    """
    return _CONTEXT_PHRASE.get(level, "the selected context")


def _format_count(value: int) -> str:
    return f"{value:,}"


def _build_freq_rows(bundle: FrozenPairBundle | None) -> list[FreqRowVM]:
    """Co-occurrence counts per scope, narrowest scope last.

    Column headings name the *sets* rather than repeating their members, and
    the counts are for co-occurrence of every term in a set — not for the
    individual terms, which is the reading a reader will otherwise assume.
    """
    if bundle is None:
        return []
    rows: list[FreqRowVM] = []
    for attr in ("global_stats", "country_stats", "country_year_stats"):
        stats: FrozenScopeStats | None = getattr(bundle, attr, None)
        if stats is None:
            continue
        rows.append(
            FreqRowVM(
                scope_label=_SCOPE_LABEL[attr],
                n_drugs=_format_count(stats.N_drugs),
                n_aes=_format_count(stats.N_AEs),
                n_both=_format_count(stats.N_drugs_AEs),
            )
        )
    return rows


def _build_shared_sets(features: PairFeatures) -> list[SharedSetVM]:
    """The shared drug and adverse-event sets, members named explicitly."""
    sets: list[SharedSetVM] = []
    if features.matching_drugs:
        sets.append(
            SharedSetVM(
                label="Shared drug set",
                members=[
                    EvidenceCell(
                        text=name,
                        kind=ValueKind.DRUG_SUBSTANCE,
                        role=EvidenceRole.ANCHOR
                        if _anchor_covers_drugs(features.anchor.type)
                        else EvidenceRole.MATCHING,
                    )
                    for name in features.matching_drugs
                ],
                is_anchor=_anchor_covers_drugs(features.anchor.type),
            )
        )
    if features.matching_AEs:
        sets.append(
            SharedSetVM(
                label="Shared adverse event set",
                members=[
                    EvidenceCell(
                        text=name,
                        kind=ValueKind.AE_TERM,
                        role=EvidenceRole.ANCHOR
                        if _anchor_covers_aes(features.anchor.type)
                        else EvidenceRole.MATCHING,
                    )
                    for name in features.matching_AEs
                ],
                is_anchor=_anchor_covers_aes(features.anchor.type),
            )
        )
    return sets


_ANCHOR_SET_NAME = {
    AnchorType.DRUGS: "shared drug set",
    AnchorType.AES: "shared adverse event set",
    AnchorType.DRUGS_AES: "shared drug and adverse event sets",
}


def _anchor_note(features: PairFeatures) -> str:
    """One sentence naming the anchor, its size and its context."""
    anchor = features.anchor
    name = _ANCHOR_SET_NAME.get(anchor.type, "shared clinical pattern")
    reports = "report" if anchor.N_anchor == 1 else "reports"
    return (
        f"The {name} defines an anchor population of "
        f"{_format_count(anchor.N_anchor)} {reports} in "
        f"{context_phrase(features.reporting_context.level)}. The matching "
        "score measures how surprising the observed matches would be within "
        "that population, so a larger anchor weakens the evidence from an "
        "identical match."
    )


_MATCHING_FEATURE_LABEL = {
    "patient_sex": "Sex",
    "patient_age": "Age",
    "patient_height": "Height",
    "patient_weight": "Weight",
}


def _matching_feature_label(
    fc: FeatureContribution, features: PairFeatures
) -> str:
    """A reader-facing label for one contribution.

    The drug and adverse-event sets are named by their members rather than as
    "shared drug set": a reader looking at a contribution of +6.7 wants to know
    which drugs earned it, and the set name alone sends them hunting for the
    list elsewhere on the page.
    """
    if fc.feature == "clinical_date" and fc.matched_value:
        return fc.matched_value
    if fc.feature == "drug_set":
        return "; ".join(features.matching_drugs) or "Shared drug set"
    if fc.feature == "ae_set":
        return "; ".join(features.matching_AEs) or "Shared adverse event set"
    return _MATCHING_FEATURE_LABEL.get(fc.feature, _readable_feature(fc.feature))


def _build_matching_features(
    features: PairFeatures,
) -> list[MatchingFeatureRow]:
    """Every contribution to the matching score, strongest first.

    Ordered by contribution descending, then by label, so the reader meets
    the evidence that mattered most first and the order is deterministic.
    """
    rows: list[MatchingFeatureRow] = []
    for fc in features.probability.feature_contributions:
        decades = _contribution_decades(fc)
        if decades is None:
            continue
        kind = (
            ValueKind.DRUG_START_DATE
            if fc.feature == "clinical_date"
            else ValueKind.FREE_TEXT
        )
        rows.append(
            MatchingFeatureRow(
                label=EvidenceCell(
                    text=_matching_feature_label(fc, features),
                    kind=kind,
                    role=EvidenceRole.MATCHING,
                ),
                contribution=decades,
                contribution_text=f"+{decades:.1f}",
            )
        )
    return sorted(rows, key=lambda r: (-r.contribution, r.label.text))


# ---------------------------------------------------------------------------
# Score reconciliation
# ---------------------------------------------------------------------------

#: How far the contributions may sit from the stored score before the
#: difference needs explaining. Contributions are shown to one decimal, so a
#: few hundredths of accumulated rounding is expected.
_RECONCILE_TOLERANCE = 0.05


def _anchor_adjustment(n_anchor: int) -> float | None:
    """``-log10(M)`` for ``M = C(N_anchor, 2)`` pairwise opportunities.

    ``None`` when the anchor is too small to furnish a comparison at all
    (``M = 0``), where there is no multiplicity to adjust for.
    """
    pairs = n_anchor * (n_anchor - 1) // 2
    if pairs <= 0:
        return None
    return -math.log10(pairs)


def _build_reconciliation(
    features: PairFeatures,
    matching_features: list[MatchingFeatureRow],
    run_settings: EvidenceRunSettings | None,
    gaps: list[EvidenceGap],
) -> ReconciliationVM:
    """Relate the listed contributions to the stored matching score.

    The three numbers do not simply add up, and a reader who tries to add them
    deserves an explanation rather than the impression that the table
    contradicts its own score. Two things intervene: the anchor-multiplicity
    adjustment, and the ceiling. The score is
    ``max(0, -log10(p_match + epsilon))``, so once the contributions imply a
    chance-match probability well below ``epsilon`` the score stops rising and
    sits at ``-log10(epsilon)``.

    The ceiling can only be named when the run's epsilon is known. Where it is
    not and the numbers disagree, this records a gap saying so rather than
    asserting a ceiling it cannot verify.
    """
    total = sum(r.contribution for r in matching_features)
    adjustment = _anchor_adjustment(features.anchor.N_anchor)
    stored = features.L_match
    ceiling = run_settings.score_ceiling if run_settings is not None else None

    expected = max(0.0, total + (adjustment or 0.0))
    unexplained = abs(expected - stored) > _RECONCILE_TOLERANCE
    at_ceiling = (
        ceiling is not None and abs(stored - ceiling) <= _RECONCILE_TOLERANCE
    )

    if at_ceiling and ceiling is not None:
        note = (
            "The contributions imply more evidence than the score can express. "
            f"The score is capped at {ceiling:.1f} by the numerical stabiliser "
            "this run used, so it reads as that ceiling rather than the larger "
            "total. The individual contributions are the evidence; the score is "
            "a bounded summary of it."
        )
    elif unexplained and ceiling is None:
        note = (
            "The contributions and the stored score differ. The score is "
            "computed from a multiplicity-adjusted probability and is capped by "
            "a numerical stabiliser, so the two need not agree exactly; this "
            "run's stabiliser was not recorded, so the cap cannot be stated "
            "here."
        )
        gaps.append(
            EvidenceGap(
                code=EvidenceGapCode.SCORE_UNRECONCILED,
                message=(
                    "The listed contributions do not account for the stored "
                    "matching score, and this run's numerical stabiliser was "
                    "not recorded, so the score ceiling cannot be stated. "
                    "Supply the run manifest to resolve this."
                ),
            )
        )
    elif unexplained:
        note = (
            "The contributions and the stored score differ slightly: the score "
            "is computed from a multiplicity-adjusted probability rather than "
            "by summing the rounded contributions shown here."
        )
    else:
        note = (
            "The contributions, the anchor adjustment and the score agree to "
            "the precision shown."
        )

    return ReconciliationVM(
        contributions_total=total,
        contributions_total_text=f"+{total:.1f}",
        anchor_adjustment=adjustment,
        anchor_adjustment_text=(
            "not applicable"
            if adjustment is None
            # An anchor of two reports offers one comparison, so the
            # adjustment is -log10(1) = -0.0, which would print as "-0.0".
            else ("0.0" if adjustment == 0 else f"{adjustment:+.1f}")
        ),
        stored_score=stored,
        stored_score_text=f"{stored:.1f}",
        score_ceiling=ceiling,
        ceiling_reached=at_ceiling,
        note=note,
    )


# ---------------------------------------------------------------------------
# Discrepancies
# ---------------------------------------------------------------------------

_ENTITY_READABLE = {
    "drug": "Drug start date",
    "ae": "Adverse event onset date",
    "overall_ae_onset": "Overall adverse event onset date",
    "report_date": "Report date precedes adverse event onset",
}


def _drug_set_text(
    inc: Inconsistency, reported_names: dict[str, str] | None = None
) -> str:
    """A readable sentence for a drug-set finding.

    Built from ``terms`` and ``kind`` rather than from the stored description,
    which embeds a Python list repr — fine for an audit trail, not for a
    reader.

    The substance leads, because that is what a reader is looking for when
    scanning a list of findings; the direction follows it.

    The wording says the drug is *absent* from the other report, not that it is
    "a key drug in report A only": the latter reads as though both reports list
    it and only one calls it key, which is a different finding and not the one
    the check makes.
    """
    if not inc.terms:
        return inc.description or "A key drug is reported in only one report."

    # The stored terms are lowercased substance names. Where the drug table
    # shows a report's own spelling, use that here too, so a reader matching
    # the finding against the table sees the same word in both places.
    lookup = reported_names or {}
    names = "; ".join(
        lookup.get(normalize_drug_key(term), term) for term in inc.terms
    )
    plural = "key drugs" if len(inc.terms) > 1 else "key drug"
    if inc.kind == DRUG_SET_KIND_MISSING_IN_R2:
        source, other = "A", "B"
    elif inc.kind == DRUG_SET_KIND_MISSING_IN_R1:
        source, other = "B", "A"
    else:
        return f"{names} — reported as a {plural} in only one report."
    return f"{names} — {plural} on report {source} absent from report {other}."


def _ae_burden_text(inc: Inconsistency) -> str | None:
    """A readable sentence for a directional adverse-event finding.

    The feature name says which report carried the unmatched terms, so the
    sentence can name it. Preferred over the stored description, which is
    written for an audit trail and speaks of "non-Investigations PT(s)".
    """
    source = AE_FEATURE_TO_SOURCE_REPORT.get(inc.feature)
    if source is None or not inc.terms:
        return None
    side = "A" if source == 1 else "B"
    other = "B" if source == 1 else "A"
    names = "; ".join(inc.terms)
    # The rule-based assessor matches at MedDRA HLT level, so naming that
    # level is what tells a reader why two clinically adjacent terms did or
    # did not count as a difference.
    return (
        f"{names} listed on report {side} without a matching HLT on "
        f"report {other}."
    )


def _inconsistency_text(
    inc: Inconsistency, reported_names: dict[str, str] | None = None
) -> str:
    """A reader-facing sentence for one inconsistency."""
    ae_text = _ae_burden_text(inc)
    if inc.feature == DRUG_SET_FEATURE:
        body = _drug_set_text(inc, reported_names)
    elif ae_text is not None:
        body = ae_text
    elif inc.description:
        body = inc.description
    elif inc.rationale:
        body = inc.rationale
    elif inc.terms:
        body = ", ".join(inc.terms) + "."
    elif inc.difference is not None:
        units = f" {inc.units}" if inc.units else ""
        body = f"Difference of {_number_text(inc.difference)}{units}."
    else:
        body = "Reported values are inconsistent."
    return body


def _date_finding_text(finding: ClinicalDateFindingSummary) -> str:
    """A reader-facing sentence for one clinical-date finding."""
    entity = parse_entity_label(finding.entity_label)
    label = _ENTITY_READABLE.get(entity.kind, "Clinical date")
    if entity.key:
        label = f"{label} for {entity.key}"
    body = f"{label}: {finding.source_date} versus {finding.matched_date}"
    if finding.diff_days is not None:
        days = "day" if abs(finding.diff_days) == 1 else "days"
        body += f", at least {abs(finding.diff_days)} {days} apart"
    if finding.downgrades:
        body += (
            "; severity reduced because "
            + "; ".join(finding.downgrades)
        )
    return f"{body}."


_DISCREPANCY_COLUMN_LABEL: dict[str, str] = {
    "drug_set": "Drugs",
    "patient_age": "Age",
    "patient_sex": "Sex",
    "patient_height": "Height",
    "patient_weight": "Weight",
    "patient_country": "Country of origin",
    "age": "Age",
    "sex": "Sex",
    "height": "Height",
    "weight": "Weight",
}


def _discrepancy_column(feature: str) -> str:
    """The value for the findings table's own column, in the reader's terms."""
    if feature in _DISCREPANCY_COLUMN_LABEL:
        return _DISCREPANCY_COLUMN_LABEL[feature]
    if feature.startswith("ae_clinical_picture"):
        return "Adverse events"
    if feature.startswith("source_case_"):
        return "Source and case identifiers"
    return _readable_feature(feature)


def _build_discrepancies(
    features: PairFeatures, reported_names: dict[str, str] | None = None
) -> list[DiscrepancyVM]:
    """Every finding the assessment recorded, retained ones first.

    Retained and suppressed findings are both listed, and marked as such. A
    suppressed finding contributed nothing to the penalty, so presenting it as
    operative would be wrong; omitting it entirely would hide an assessment the
    assessor actually made.

    Ordered by severity descending, then by text, so the order is
    deterministic and the most serious finding is read first.
    """
    items: list[DiscrepancyVM] = []
    for inc in features.inconsistencies:
        items.append(
            DiscrepancyVM(
                feature_label=_discrepancy_column(inc.feature),
                severity=inc.severity.value,
                text=_inconsistency_text(inc, reported_names),
                retained=not inc.suppressed,
                suppressed=inc.suppressed,
                suppressed_by=inc.suppressed_by,
            )
        )
    for finding in features.clinical_date_findings:
        items.append(
            DiscrepancyVM(
                feature_label="Clinical dates",
                severity=finding.severity.value,
                text=_date_finding_text(finding),
                retained=True,
                downgrades=list(finding.downgrades),
            )
        )
    return sorted(
        items,
        key=lambda d: (
            not d.retained,
            -SEVERITY_ORDER.get(d.severity, -1),
            d.feature_label,
            d.text,
        ),
    )


# ---------------------------------------------------------------------------
# Header: classifications from four different sources, kept apart
# ---------------------------------------------------------------------------


class SCLEState(str, Enum):
    """Whether an SCLE assessment of the reference label exists, and what kind.

    Four states, because three different absences mean different things and a
    reader must not have to guess which applies: nobody has entered an
    assessment yet; this pair was deliberately not assessed; an assessment was
    made; an assessment was made and found the label genuinely ambiguous.
    """

    NOT_ENTERED = "not_entered"
    NOT_ASSESSED = "not_assessed"
    ASSESSED = "assessed"
    INCONCLUSIVE = "inconclusive"


_SCLE_ISSUE_TEXT = {
    "no_apparent_issue": "No apparent issue",
    "possibly_incorrect": "Possibly incorrect",
    "genuinely_ambiguous": "Genuinely ambiguous",
}

_SCLE_STATE_TEXT = {
    SCLEState.NOT_ENTERED: "To be completed",
    SCLEState.NOT_ASSESSED: "Not assessed",
}


@dataclass(frozen=True)
class SCLEAssessment:
    """An SCLE verdict on the reference label, or the stated absence of one."""

    state: SCLEState = SCLEState.NOT_ENTERED
    #: A ``ReferenceLabelIssue`` value when ``state`` is ASSESSED or
    #: INCONCLUSIVE; ``None`` otherwise.
    issue: str | None = None
    note: str = ""


@dataclass(frozen=True)
class ReferenceInfo:
    """What the reference set says about this pair, and what screening did.

    Deliberately separate from the vigiTwin result. A reference label is a
    human verdict and a screening outcome is another method's; neither is ever
    inferred from what vigiTwin decided.
    """

    #: The study reference vocabulary: ``duplicate`` / ``non_duplicate`` /
    #: ``abstain``. Not the pipeline's four-label vocabulary.
    reference_label: str | None = None
    set_label: str | None = None
    #: Whether the screening algorithm flagged the pair. ``None`` means no
    #: screening verdict was supplied — never "not flagged".
    screening_retrieved: bool | None = None
    screening_algorithm: str = "vigiMatch"
    scle: SCLEAssessment = dc_field(default_factory=SCLEAssessment)
    #: Name of the positive-class rule used to compare the reference label
    #: against the vigiTwin label.
    positive_class: str = "primary"


@dataclass(frozen=True)
class ExamplePresentation:
    """Curated, human-authored framing for one example."""

    example_id: str = ""
    kicker: str = ""
    title: str = ""
    lede: str = ""
    #: Human-authored interpretation. Never generated, never overwritten.
    case_interpretation: str | None = None
    #: ``"deterministic"`` | ``"interpretation"`` | ``"both"``.
    summary_mode: str = "deterministic"


_REFERENCE_DISPLAY = {
    "duplicate": "Duplicate",
    "non_duplicate": "Non-duplicate",
    "abstain": "Abstain",
}

#: Which vigiTwin labels count as positive under each named rule. Mirrors
#: ``study_eval.aggregate``'s prespecified ``PositiveClass`` values, which are
#: a study-protocol choice rather than a property of the method — so the rule
#: is named on the page whenever it is not the primary one.
_POSITIVE_LABELS: dict[str, frozenset[str]] = {
    "primary": frozenset({"Duplicates"}),
    "abstain_positive": frozenset({"Duplicates", "Abstain"}),
    "otherwise_related_positive": frozenset({"Duplicates", "Otherwise_related"}),
}


def _build_reference_verdict(
    reference: ReferenceInfo, final_label: str | None
) -> ReferenceVerdictVM | None:
    """The reference label, and whether vigiTwin agreed — in binary space.

    The reference vocabulary is binary (``duplicate`` / ``non_duplicate``);
    vigiTwin's is four-valued. Comparing the two as strings reports a
    disagreement whenever they merely differ in vocabulary — ``"duplicate"``
    against ``"Duplicates"`` is not a disagreement, and under a rule that
    treats ``Otherwise_related`` as positive, neither is ``"duplicate"``
    against ``Otherwise_related``. So the comparison is made by collapsing the
    vigiTwin label to positive/negative under a named rule, which is the same
    operation the study's own analysis performs.

    ``abstain`` on either side yields no verdict: an abstention is a refusal to
    decide, not a label that can agree or disagree.
    """
    label = reference.reference_label
    if label is None:
        return None

    reference_positive = {"duplicate": True, "non_duplicate": False}.get(label)
    agrees: bool | None = None
    note = ""
    rule = reference.positive_class
    positive_labels = _POSITIVE_LABELS.get(rule)

    if reference_positive is None:
        note = (
            "The reference annotation abstained, so no agreement is defined."
        )
    elif final_label is None or final_label == "Abstain":
        note = "vigiTwin abstained, so no agreement is defined."
    elif positive_labels is None:
        note = (
            f"The positive-class rule {rule!r} is not one this review knows, "
            "so no agreement is shown."
        )
    else:
        agrees = (final_label in positive_labels) == reference_positive
        if rule != "primary":
            note = (
                f"Agreement is assessed under the {rule.replace('_', ' ')} "
                "rule, which counts "
                + " and ".join(sorted(positive_labels))
                + " as duplicates."
            )

    return ReferenceVerdictVM(
        reference_label=label,
        display=_REFERENCE_DISPLAY.get(label, label),
        set_label=reference.set_label,
        agrees_with_vigitwin=agrees,
        positive_class_name=rule,
        comparison_note=note,
    )


def _build_outcome_tiles(
    reference: ReferenceInfo, final_label: str | None
) -> list[OutcomeTileVM]:
    """The four header tiles, each naming its own source.

    They are kept visually and semantically distinct because they come from
    four different places — a human annotator, a screening algorithm,
    vigiTwin, and a case-level reviewer — and conflating any two of them is
    the error this header exists to prevent.
    """
    tiles: list[OutcomeTileVM] = []

    ref_label = reference.set_label or "Reference"
    if reference.reference_label is None:
        tiles.append(
            OutcomeTileVM(
                label=f"{ref_label} label",
                value="Not supplied",
                state=TileState.NOT_ENTERED,
            )
        )
    else:
        tiles.append(
            OutcomeTileVM(
                label=f"{ref_label} label",
                value=_REFERENCE_DISPLAY.get(
                    reference.reference_label, reference.reference_label
                ),
            )
        )

    if reference.screening_retrieved is None:
        tiles.append(
            OutcomeTileVM(
                label=f"{reference.screening_algorithm} screening",
                value="Not evaluated",
                state=TileState.NOT_ASSESSED,
            )
        )
    else:
        tiles.append(
            OutcomeTileVM(
                label=f"{reference.screening_algorithm} screening",
                value="Flagged" if reference.screening_retrieved else "Not flagged",
            )
        )

    tiles.append(
        OutcomeTileVM(
            label="vigiTwin classification",
            value=(final_label or "Not classified").replace("_", " "),
            state=TileState.VALUE if final_label else TileState.NOT_ENTERED,
        )
    )

    scle = reference.scle
    if scle.state in (SCLEState.ASSESSED, SCLEState.INCONCLUSIVE):
        value = _SCLE_ISSUE_TEXT.get(scle.issue or "", scle.issue or "Assessed")
        state = (
            TileState.INCONCLUSIVE
            if scle.state is SCLEState.INCONCLUSIVE
            else TileState.VALUE
        )
    else:
        value = _SCLE_STATE_TEXT[scle.state]
        state = (
            TileState.NOT_ASSESSED
            if scle.state is SCLEState.NOT_ASSESSED
            else TileState.NOT_ENTERED
        )
    tiles.append(
        OutcomeTileVM(
            label="SCLE assessment of reference label", value=value, state=state
        )
    )
    return tiles


# ---------------------------------------------------------------------------
# Assessment section
# ---------------------------------------------------------------------------

_INDEPENDENCE_LABEL = {
    "Non-independent": "Non-independent",
    "Independent": "Independence not rejected",
}


def _build_assessment_rows(
    features: PairFeatures,
    classification: ClassificationResult,
    run_settings: EvidenceRunSettings | None,
) -> list[AssessmentRowVM]:
    """The two decisions and the final label, each stated separately.

    The independence and same-case assessments answer different questions and
    are shown as different rows, with the reason vigiTwin recorded for each.
    Where no same-case comparison was made, the row says so rather than being
    omitted — an absent row reads as an oversight.
    """
    independence = independence_clause(features, classification, run_settings)
    same_case = same_case_clause(classification)
    relationship = classification.relationship_assessment

    rows = [
        AssessmentRowVM(
            label="Independence",
            text=(
                f"{_INDEPENDENCE_LABEL.get(classification.independence_assessment.value, classification.independence_assessment.value)} — "
                f"{independence[0].lower() + independence[1:]}."
            ),
        )
    ]

    if relationship is None:
        same_case_text = (
            "Not assessed: the relationship question is only reached once "
            "independence has been rejected."
        )
    else:
        verdict = relationship.value.replace("_", " ").capitalize()
        same_case_text = (
            f"{verdict}" + (f" — {same_case}." if same_case else ".")
        )
    rows.append(AssessmentRowVM(label="Same case", text=same_case_text))

    rows.append(
        AssessmentRowVM(
            label="Final classification",
            text=classification.final_label.replace("_", " "),
        )
    )
    return rows


# ---------------------------------------------------------------------------
# Narrative, link and source notes
# ---------------------------------------------------------------------------


def _explicit_link_note(features: PairFeatures) -> str:
    """What the case-identifier fields showed — including "nothing to check".

    An absent link and an unchecked field are different facts, and the stored
    verdict distinguishes them. Reporting ``not_assessed`` as "no link" would
    turn missing data into evidence of absence.
    """
    link = features.explicit_link
    if link is None:
        return (
            "Explicit inter-report case links were not evaluated for this run."
        )
    if link.verdict == ExplicitLinkVerdict.NOT_ASSESSED:
        return (
            "Neither report carried case-identifier data, so no explicit "
            "inter-report link could be checked. This is an absence of "
            "information, not an assessed absence of a link."
        )
    if link.verdict == ExplicitLinkVerdict.NO_LINK:
        return (
            "No explicit inter-report case link was found in the available "
            "case-identifier data."
        )
    matched = len(link.matches)
    return (
        f"One report explicitly references the other in its case-identifier "
        f"fields ({matched} matching "
        f"{'reference' if matched == 1 else 'references'}). The sending "
        "organisation has declared the two reports to describe the same case."
    )


def _source_case_note(features: PairFeatures) -> str:
    """The narrative-derived source and case verdict, when one was reached."""
    assessment = features.source_case_assessment
    if assessment is None:
        return ""
    if assessment.verdict in (
        SourceCaseVerdict.NOT_ASSESSED,
        SourceCaseVerdict.NO_DECISIVE_SIGNAL,
    ):
        return ""
    parts = [assessment.verdict.value.replace("_", " ").capitalize() + "."]
    if assessment.shared_source_identifier:
        kind = (
            assessment.shared_source_identifier_type.value
            if assessment.shared_source_identifier_type is not None
            else "identifier"
        )
        parts.append(
            f"Shared source {kind}: {assessment.shared_source_identifier}."
        )
    if assessment.reasoning:
        parts.append(assessment.reasoning)
    return " ".join(parts)


def _narrative_note(r1: Report, r2: Report) -> str:
    """Whether narratives were available, and any literature references."""
    refs = [
        r.literature_reference
        for r in (r1, r2)
        if r.literature_reference
    ]
    have = [bool(r1.narrative), bool(r2.narrative)]
    if not any(have) and not refs:
        return ""
    parts: list[str] = []
    if all(have):
        parts.append("Both reports carry a narrative.")
    elif any(have):
        side = "A" if have[0] else "B"
        parts.append(f"Only report {side} carries a narrative.")
    for ref in _dedupe(refs):
        parts.append(f"Literature reference: {ref}")
    return " ".join(parts)


def _build_narrative_blocks(
    features: PairFeatures,
    classification: ClassificationResult | None,
    presentation: ExamplePresentation,
    run_settings: EvidenceRunSettings | None,
    gaps: list[EvidenceGap],
) -> list[EvidenceTextVM]:
    """The prose blocks: a human's reading, the generated summary, or both.

    A supplied interpretation *replaces* the generated summary by default,
    rather than sitting beside it: the two would otherwise say the same thing
    twice, and a reader cannot tell which carries authority. ``both`` is
    available for when a reviewer wants the generated account on the record
    alongside their own.

    Human text is only ever read from the presentation. Nothing here writes
    to it, so regeneration cannot overwrite it.
    """
    summary = build_assessment_summary(
        features, classification, run_settings=run_settings
    )
    gaps.extend(summary.gaps)

    generated = EvidenceTextVM(
        heading="vigiTwin assessment summary",
        paragraphs=summary.paragraphs(),
    )
    human_text = presentation.case_interpretation
    if not human_text:
        return [generated]

    human = EvidenceTextVM(
        heading="Case-level interpretation",
        paragraphs=[
            p.strip() for p in human_text.strip().split("\n\n") if p.strip()
        ],
        is_human=True,
    )
    if presentation.summary_mode == "both":
        return [human, generated]
    return [human]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_evidence_case(
    *,
    report_1: Report,
    report_2: Report,
    pair_id: str,
    features: PairFeatures | None = None,
    classification: ClassificationResult | None = None,
    frozen_bundle: FrozenPairBundle | None = None,
    narrative_demographics_1: NarrativeDemographics | None = None,
    narrative_demographics_2: NarrativeDemographics | None = None,
    reference: ReferenceInfo | None = None,
    presentation: ExamplePresentation | None = None,
    run_settings: EvidenceRunSettings | None = None,
) -> EvidenceCaseVM:
    """Build one evidence-review case from a pair and its stored assessment.

    ``features`` and ``classification`` are optional so that a pair whose
    assessment was not saved still renders as a report comparison — with every
    element neutral and a gap recorded, rather than with colours invented from
    a comparison this format does not perform.
    """
    reference = reference or ReferenceInfo()
    presentation = presentation or ExamplePresentation()
    gaps: list[EvidenceGap] = []

    characteristics = _build_characteristic_rows(
        report_1,
        report_2,
        narrative_demographics_1,
        narrative_demographics_2,
    )
    drugs = _build_drug_rows(report_1, report_2)
    aes = _build_ae_rows(report_1, report_2)

    shared_sets: list[SharedSetVM] = []
    freq_rows: list[FreqRowVM] = []
    matching_features: list[MatchingFeatureRow] = []
    reconciliation: ReconciliationVM | None = None
    discrepancies: list[DiscrepancyVM] = []
    assessment_rows: list[AssessmentRowVM] = []
    anchor_note = ""
    context_note = ""
    explicit_link_note = ""
    source_case_note = ""
    narrative_blocks: list[EvidenceTextVM] = []

    if features is not None:
        _attach_matching_sets(drugs, aes, features)
        _attach_demographic_contributions(characteristics, features)
        _attach_clinical_date_matches(drugs, aes, features, gaps)
        _attach_inconsistencies(characteristics, drugs, aes, features, gaps)
        _attach_clinical_date_findings(drugs, aes, features, gaps)

        shared_sets = _build_shared_sets(features)
        freq_rows = _build_freq_rows(frozen_bundle)
        if not freq_rows:
            gaps.append(
                EvidenceGap(
                    code=EvidenceGapCode.FREQUENCY_STATS_MISSING,
                    message=(
                        "Contextual frequency counts are not shown: no frozen "
                        "frequency statistics were supplied for this pair."
                    ),
                )
            )
        matching_features = _build_matching_features(features)
        reconciliation = _build_reconciliation(
            features, matching_features, run_settings, gaps
        )
        discrepancies = _build_discrepancies(
            features,
            {row.sort_key: row.name.text for row in drugs},
        )
        anchor_note = _anchor_note(features)
        context_note = _context_note(report_1, report_2, features)
        explicit_link_note = _explicit_link_note(features)
        source_case_note = _source_case_note(features)

        if classification is not None:
            assessment_rows = _build_assessment_rows(
                features, classification, run_settings
            )
        else:
            gaps.append(
                EvidenceGap(
                    code=EvidenceGapCode.NO_CLASSIFICATION,
                    message=(
                        "No classification was supplied for this pair, so no "
                        "vigiTwin assessment is shown."
                    ),
                )
            )
        narrative_blocks = _build_narrative_blocks(
            features, classification, presentation, run_settings, gaps
        )
    else:
        gaps.append(
            EvidenceGap(
                code=EvidenceGapCode.NO_FEATURES,
                message=(
                    "No stored evidence was supplied for this pair. The "
                    "reports are shown side by side with no evidence "
                    "highlighting: this review colours values from the "
                    "assessment, and there is no assessment to read."
                ),
            )
        )
        if presentation.case_interpretation:
            narrative_blocks = _build_narrative_blocks(
                PairFeatures.model_construct(), None, presentation, None, []
            )[:1]

    drugs = _finalise_drug_rows(drugs)
    aes = _finalise_ae_rows(aes)

    final_label = (
        classification.final_label if classification is not None else None
    )

    return EvidenceCaseVM(
        pair_id=pair_id,
        example_id=presentation.example_id or pair_id,
        kicker=presentation.kicker,
        title=presentation.title,
        lede=presentation.lede,
        outcomes=_build_outcome_tiles(reference, final_label),
        reference_verdict=_build_reference_verdict(reference, final_label),
        narrative_blocks=narrative_blocks,
        characteristics=characteristics,
        context_note=context_note,
        drugs=drugs,
        adverse_events=aes,
        shared_sets=shared_sets,
        anchor_note=anchor_note,
        freq_rows=freq_rows,
        matching_features=matching_features,
        reconciliation=reconciliation,
        explicit_link_note=explicit_link_note,
        source_case_note=source_case_note,
        narrative_note=_narrative_note(report_1, report_2),
        assessment_rows=assessment_rows,
        discrepancies=discrepancies,
        gaps=_dedupe_gaps(gaps),
    )


def _context_note(r1: Report, r2: Report, features: PairFeatures) -> str:
    """Why this reporting context applies, in the reader's terms."""
    level = features.reporting_context.level
    if level is ReportingContextLevel.COUNTRY_YEAR:
        return (
            "Rarity is judged within the country-year context, because both "
            "reports originate from the same country and the same year. "
            "Country agreement defines that context; it is not counted as "
            "additional matching evidence."
        )
    if level is ReportingContextLevel.COUNTRY:
        return (
            "Rarity is judged within the country context, because the reports "
            "share a country but not a reporting year. Country agreement "
            "defines that context; it is not counted as additional matching "
            "evidence."
        )
    return (
        "Rarity is judged against the whole database, because the reports do "
        "not share a reporting country."
    )


def _dedupe_gaps(gaps: list[EvidenceGap]) -> list[EvidenceGap]:
    """One footnote per gap kind, in first-seen order."""
    seen: dict[EvidenceGapCode, EvidenceGap] = {}
    for gap in gaps:
        seen.setdefault(gap.code, gap)
    return list(seen.values())


__all__ = [
    "ExamplePresentation",
    "ReferenceInfo",
    "SCLEAssessment",
    "SCLEState",
    "build_evidence_case",
    "context_phrase",
]
