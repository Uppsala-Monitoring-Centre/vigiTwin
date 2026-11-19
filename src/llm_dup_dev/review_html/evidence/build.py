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
import re
from dataclasses import dataclass, field as dc_field
from enum import Enum

from llm_dup_dev.data.offline import FrozenPairBundle, FrozenScopeStats
from llm_dup_dev.models.classification import (
    ClassificationResult,
    InconsistencyPenalty,
)
from llm_dup_dev.models.enums import (
    AnchorType,
    IndependenceAssessment,
    IndependenceReason,
    RelationshipAssessment,
    ReportingContextLevel,
)
from llm_dup_dev.models.explicit_link import ExplicitLinkVerdict
from llm_dup_dev.models.narrative_demographics import NarrativeDemographics
from llm_dup_dev.models.narrative_source_case import (
    SourceCaseAssessment,
    SourceCaseVerdict,
    SourceIdentifierType,
)
from llm_dup_dev.models.pair_features import (
    ClinicalDateFindingSummary,
    FeatureContribution,
    Inconsistency,
    PairFeatures,
    ReportingContext,
)
from llm_dup_dev.feature_generation.clinical_date_inconsistencies import (
    effective_overall_ae_onset_date,
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
from .legacy import (
    recovered_clinical_date_gap,
    recovered_drug_set_gap,
    resolved_clinical_date,
    resolved_drug_set_terms,
)
from .models import (
    AEEvidenceRow,
    AssessmentRowVM,
    Attribution,
    AttributionKind,
    CaseSummaryVM,
    CharacteristicRow,
    DecisionPathway,
    DecisionStage,
    DecisionTileVM,
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
    SourceContextVM,
    TileState,
    ValueKind,
    pathway_label,
    stronger_role,
)
from .settings import EvidenceRunSettings
from .summary import (
    build_assessment_summary,
    context_phrase,
    format_score,
    independence_clause,
    same_case_clause,
    separating_decimals,
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

#: The case-level adverse-event onset. Named as the findings table names it,
#: so a reader matching a finding against the table sees one phrase.
AE_ONSET_LABEL = "Overall adverse event onset"

#: Said beside a derived value, because the column is headed "Report A" and a
#: derived date is not something report A stated. The rule itself is the
#: pipeline's, and is documented where it lives.
_DERIVED_ONSET_NOTE = "derived from the reported event and drug dates"


def _overall_ae_onset_row(r1: Report, r2: Report) -> CharacteristicRow | None:
    """The onset date the clinical-date checks actually compared, per report.

    Absent from this table until now, which left a whole class of finding —
    an overall-onset inconsistency — with nothing in the report comparison a
    reader could check it against. The finding named two dates that appeared
    nowhere else on the page.

    The value is the pipeline's own
    :func:`~llm_dup_dev.feature_generation.clinical_date_inconsistencies.effective_overall_ae_onset_date`,
    not a second reading of the reports: a row showing a different date from
    the one the assessment compared would be worse than no row. Where that
    value was derived rather than carried on the report, the cell says so —
    see :attr:`CharacteristicRow.left_note`.

    ``None`` when neither report yields one, where the row would be two
    blanks.
    """
    cells: list[EvidenceCell] = []
    notes: list[str] = []
    for report in (r1, r2):
        value = effective_overall_ae_onset_date(report)
        cells.append(
            _cell(value.value if value is not None else "", ValueKind.AE_ONSET_DATE)
        )
        notes.append(
            _DERIVED_ONSET_NOTE
            if value is not None and report.reported_overall_ae_onset_date is None
            else ""
        )
    if cells[0].missing and cells[1].missing:
        return None
    return CharacteristicRow(
        label=AE_ONSET_LABEL,
        left=cells[0],
        right=cells[1],
        left_note=notes[0],
        right_note=notes[1],
    )


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
    # After the report date, with the other date on the page, and before the
    # optional measurements.
    onset_row = _overall_ae_onset_row(r1, r2)
    if onset_row is not None:
        rows.append(onset_row)
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
    only inside its description, and is recovered from there (see
    :mod:`.legacy`) so those matches are highlighted too. The recovery is
    footnoted, and a contribution whose description does not yield a date is
    footnoted as unattributed rather than silently left un-highlighted.
    """
    date_contributions = [
        fc
        for fc in features.probability.feature_contributions
        if fc.feature == "clinical_date"
    ]
    if not date_contributions:
        return

    resolved = [(fc, resolved_clinical_date(fc)) for fc in date_contributions]
    n_recovered = sum(1 for _, found in resolved if found is not None and found[2])
    unattributed = [fc for fc, found in resolved if found is None]
    if n_recovered:
        gaps.append(recovered_clinical_date_gap(n_recovered))
    if unattributed:
        gaps.append(
            EvidenceGap(
                code=EvidenceGapCode.CLINICAL_DATE_VALUE_MISSING,
                message=(
                    f"{len(unattributed)} matched clinical "
                    f"date{'s' if len(unattributed) != 1 else ''} could not be "
                    "attributed to a specific date, because this run recorded "
                    "neither the matched value nor a description it could be "
                    "read from. The dates still count towards the matching "
                    "score shown below; they are simply not highlighted in the "
                    "tables."
                ),
            )
        )

    for fc, found in resolved:
        if found is None:
            continue
        matched_value, value_resolution, _ = found
        decades = _contribution_decades(fc)
        note = "matched clinical date" + (
            f" (+{decades:.1f})" if decades is not None else ""
        )
        for cell in _date_cells(drugs, aes):
            if _truncate(cell.text, value_resolution) == matched_value:
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

        # A source/case verdict has a presentation of its own — the Source
        # context block names the identifier and what became of the case
        # labels — so recording it as evidence nothing could carry is no
        # longer true, and a limitations list earns its space by naming what
        # is absent.
        if inc.feature.startswith("source_case_"):
            continue

        # Nothing in the tables corresponds to this finding. It is still
        # listed in the assessment section; the gap records that no cell
        # could carry it.
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
    substances only inside the description, and they are recovered from there
    (see :mod:`.legacy`) so the highlighting still happens; the recovery is
    footnoted. Only a finding that yields no substances from either source is
    left un-highlighted, and it still appears in the assessment section.
    """
    found = resolved_drug_set_terms(inc)
    if found is None:
        gaps.append(
            EvidenceGap(
                code=EvidenceGapCode.DRUG_SET_TERMS_MISSING,
                message=(
                    "A drug-set inconsistency is reported in the assessment "
                    "section without per-substance highlighting: this run "
                    "recorded neither the offending substances as structured "
                    "terms nor a description they could be read from."
                ),
            )
        )
        return
    terms, _kind, recovered = found
    if recovered:
        gaps.append(recovered_drug_set_gap(1))

    wanted = {normalize_drug_key(t) for t in terms}
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
    characteristics: list[CharacteristicRow],
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
        if entity.kind == "report_date":
            # Case-level and not about the patient; the report-date row is
            # deliberately neutral and the finding is stated in section 3.
            continue
        if entity.kind == "overall_ae_onset":
            # It has a row of its own now, so the finding can colour the two
            # dates it is about rather than naming dates printed nowhere.
            values = {finding.source_date, finding.matched_date}
            for char_row in characteristics:
                if char_row.label != AE_ONSET_LABEL:
                    continue
                for onset_cell in (char_row.left, char_row.right):
                    if not onset_cell.missing and onset_cell.text in values:
                        _mark(
                            onset_cell,
                            EvidenceRole.INCONSISTENT,
                            _date_finding_attribution(finding).model_copy(),
                        )
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


def _row_role(entity: EvidenceCell, cells: list[EvidenceCell]) -> EvidenceRole:
    """A row's tint: what the assessment made of the *entity* the row is about.

    The row is about a substance or an adverse-event term, and its tint
    answers "what did vigiTwin make of this one?". Where the entity carries a
    verdict of its own — matched as part of the shared pattern, or flagged as
    an inconsistency — that verdict is the row's.

    This differs from taking the strongest role among the row's cells, which
    is what it used to do. A drug reported by both reports, matched as part of
    the shared pattern, with a date inconsistency on one of its records, came
    out **red**: the strongest cell won, and the row read as though the
    substance itself were the problem. It is not. The substance matched; a
    date attached to it did not, and that date is red in the cell where it is
    shown. Tinting the whole row red for it overstated a date difference into
    a finding about the medication.

    Where the entity carries no verdict — neither report's shared pattern
    includes it and nothing flagged it — the strongest role among the row's
    cells still decides, so an inconsistency on a date is never left with no
    tint at all.
    """
    if entity.role is not EvidenceRole.NEUTRAL:
        return entity.role
    return _combine(cells)


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

    Evidence is carried by colour rather than by position. The row's tint is
    the substance's own verdict (see :func:`_row_role`); a date inconsistency
    on a matched substance is red in its own cell and does not repaint the
    row.
    """
    for row in rows:
        cells = [row.name]
        for entry in [*row.left_entries, *row.right_entries]:
            cells.extend(entry.cells)
        row.row_role = _row_role(row.name, cells)
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
        row.row_role = _row_role(row.term, cells)
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

#: What a scope is called when the artifact recorded no country to name it
#: by. Named by level, which is all that is left to say.
_SCOPE_LABEL = {
    "global_stats": "Global",
    "country_stats": "Country (all years)",
    "country_year_stats": "Country-year",
}

#: Which reporting level each scope row is, so the row the rarity was judged
#: in can be marked rather than described.
_SCOPE_LEVEL = {
    "country_stats": ReportingContextLevel.COUNTRY,
    "country_year_stats": ReportingContextLevel.COUNTRY_YEAR,
    "global_stats": ReportingContextLevel.GLOBAL,
}

#: Which count of the selected row became the anchor population.
_ANCHOR_COLUMN = {
    AnchorType.DRUGS: "drugs",
    AnchorType.AES: "aes",
    AnchorType.DRUGS_AES: "both",
}


def _scope_label(attr: str, context: ReportingContext) -> str:
    """A scope's name, as a reader would cite it: ``Canada, 2022``.

    The country and the year are what distinguish one case's context from
    another's, and a column headed "Country-year" told a reader only how the
    population was chosen. The country is whatever the run recorded — an ISO
    code in most artifacts — and is shown verbatim rather than expanded,
    because inventing a country name from a code is a mapping this module has
    no business owning. Where no country was recorded there is nothing to
    name, and the level stands in for it.
    """
    country = (context.country or "").strip()
    if attr == "global_stats" or not country:
        return _SCOPE_LABEL[attr]
    if attr == "country_year_stats":
        # Without a year there is nothing to narrow the country by, and
        # naming this row for the country alone gave the table two different
        # rows called "PL". The level is what is left to say.
        return f"{country}, {context.year}" if context.year else _SCOPE_LABEL[attr]
    return country


def _format_count(value: int) -> str:
    return f"{value:,}"


def _build_freq_rows(
    bundle: FrozenPairBundle | None, features: PairFeatures | None = None
) -> list[FreqRowVM]:
    """Co-occurrence counts per scope, narrowest scope last.

    Column headings name the *sets* rather than repeating their members, and
    the counts are for co-occurrence of every term in a set — not for the
    individual terms, which is the reading a reader will otherwise assume.

    Two facts that used to be stated in paragraphs above this table are now
    marked on it: which scope the rarity was judged in, and which of that
    row's counts became the anchor population. Both were already visible in
    the table as numbers; what a reader lacked was a way to tell which ones
    the assessment reached for, and matching a sentence against a row was
    the work those paragraphs left them.

    A column whose set is empty is left **blank**. "How many reports carry
    every term in this set" has no answer for a set with no terms — the
    stored count is the whole scope, which is not a co-occurrence of
    anything — and printing it invited a reader to compare a real count
    against a population size. *Both sets* goes blank as soon as either side
    does, since it is then not the intersection of two sets but a restatement
    of the one that is left. With neither set shared there is nothing to
    count at all, and the table does not appear; the panel's Context row
    still names the population.
    """
    if bundle is None:
        return []
    context = (
        features.reporting_context
        if features is not None
        else ReportingContext(level=ReportingContextLevel.GLOBAL)
    )
    anchor_column = (
        _ANCHOR_COLUMN.get(features.anchor.type, "") if features is not None else ""
    )
    has_drugs = features is None or bool(features.matching_drugs)
    has_aes = features is None or bool(features.matching_AEs)
    if not has_drugs and not has_aes:
        return []
    shown = {"drugs": has_drugs, "aes": has_aes, "both": has_drugs and has_aes}
    # Never box a cell that has no count in it. The anchor type and the
    # shared sets agree in real artifacts, but a mismatch would otherwise
    # draw the reader's eye to an empty box.
    if not shown.get(anchor_column, False):
        anchor_column = ""
    rows: list[FreqRowVM] = []
    for attr in ("global_stats", "country_stats", "country_year_stats"):
        stats: FrozenScopeStats | None = getattr(bundle, attr, None)
        if stats is None:
            continue
        selected = features is not None and _SCOPE_LEVEL[attr] is context.level
        rows.append(
            FreqRowVM(
                scope_label=_scope_label(attr, context),
                n_drugs=_format_count(stats.N_drugs) if has_drugs else "",
                n_aes=_format_count(stats.N_AEs) if has_aes else "",
                n_both=(
                    _format_count(stats.N_drugs_AEs)
                    if has_drugs and has_aes
                    else ""
                ),
                is_selected=selected,
                anchor_column=anchor_column if selected else "",
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


#: The anchor's name and the verb that agrees with it. The two-set anchor is
#: plural, so it takes "together define" — "the shared drug and adverse event
#: sets defines" was the reading before the verb was carried alongside.
_ANCHOR_SET_NAME = {
    AnchorType.DRUGS: ("shared drug set", "defines"),
    AnchorType.AES: ("shared adverse event set", "defines"),
    AnchorType.DRUGS_AES: (
        "shared drug and adverse event sets",
        "together define",
    ),
}


def _anchor_note(features: PairFeatures) -> str:
    """One sentence naming the anchor, its size and its context."""
    anchor = features.anchor
    name, verb = _ANCHOR_SET_NAME.get(
        anchor.type, ("shared clinical pattern", "defines")
    )
    reports = "report" if anchor.N_anchor == 1 else "reports"
    return (
        f"The {name} {verb} an anchor population of "
        f"{_format_count(anchor.N_anchor)} {reports} in "
        f"{context_phrase(features.reporting_context)}. The matching "
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

#: What kind of value a matching-feature row's label actually holds.
#:
#: ``_matching_feature_label`` below writes the *matched value* into the cell
#: where there is one — an age read off the characteristics table, a shared
#: drug set, a matched clinical date — so the cell's ``kind`` has to say which
#: of those it is. It drives no colour and no rendering; it is the dispatch
#: key the publication transform uses, and a row stamped ``FREE_TEXT`` would
#: carry a reported age onto a published page untouched.
_MATCHING_FEATURE_KIND = {
    "patient_sex": ValueKind.SEX,
    "patient_age": ValueKind.AGE,
    "patient_height": ValueKind.HEIGHT,
    "patient_weight": ValueKind.WEIGHT,
    "clinical_date": ValueKind.DRUG_START_DATE,
    "drug_set": ValueKind.DRUG_SUBSTANCE,
    "ae_set": ValueKind.AE_TERM,
}


def _demographic_match_values(
    characteristics: list[CharacteristicRow],
) -> dict[str, str]:
    """Matched demographic values, keyed by contribution feature name.

    Read off the characteristics table rather than out of the reports, so a
    matching-features row quotes exactly the string the reader sees in the
    table above it, formatting and units included.

    Where the two reports hold *different* values for a feature the assessment
    nonetheless scored as matched — an age within tolerance, say — both are
    shown. Picking one as "the" matched value would be a choice this module
    has no basis to make, and the pair of values is the more informative
    answer anyway.
    """
    by_label = {row.label: row for row in characteristics}
    values: dict[str, str] = {}
    for feature, label in FEATURE_TO_DEMO_LABEL.items():
        row = by_label.get(label)
        if row is None:
            continue
        left = "" if row.left.missing else row.left.text.strip()
        right = "" if row.right.missing else row.right.text.strip()
        if left and right:
            values[feature] = left if left == right else f"{left} / {right}"
        elif left or right:
            values[feature] = left or right
    return values


def _matching_feature_label(
    fc: FeatureContribution,
    features: PairFeatures,
    demographic_values: dict[str, str] | None = None,
) -> str:
    """A reader-facing label for one contribution.

    Every row names the value that earned the contribution, not the kind of
    value: the drug and adverse-event sets by their members, a clinical date
    by the date itself, a demographic feature by the matched value. A reader
    looking at a contribution of +6.7 wants to know which drugs earned it,
    and "Sex" alone does not say whether the shared value was the common one
    or the rare one.

    The value stands on its own — ``Female``, ``34 years``, ``2002-01-04`` —
    rather than as ``Sex (female)``. A date never carried its kind, and the
    demographic values do not need theirs either: the column is headed
    "Additional matching feature", and a value that cannot be recognised
    without a label is a value worth showing differently. Where no value was
    recorded the kind is all there is, and it is shown alone.
    """
    if fc.feature == "clinical_date":
        found = resolved_clinical_date(fc)
        if found is not None:
            return found[0]
    if fc.feature == "drug_set":
        return "; ".join(features.matching_drugs) or "Shared drug set"
    if fc.feature == "ae_set":
        return "; ".join(features.matching_AEs) or "Shared adverse event set"
    label = _MATCHING_FEATURE_LABEL.get(fc.feature, _readable_feature(fc.feature))
    value = (fc.matched_value or "").strip() or (demographic_values or {}).get(
        fc.feature, ""
    )
    if not value:
        return label
    return value[0].upper() + value[1:]


def _build_matching_features(
    features: PairFeatures,
    demographic_values: dict[str, str] | None = None,
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
        kind = _MATCHING_FEATURE_KIND.get(fc.feature, ValueKind.FREE_TEXT)
        rows.append(
            MatchingFeatureRow(
                label=EvidenceCell(
                    text=_matching_feature_label(fc, features, demographic_values),
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


def _expected_score(total: float, n_anchor: int, epsilon: float | None) -> float | None:
    """What ``L_match`` should be, given the contributions and the anchor.

    The page's two figures add to ``-log10(p_joint x M)``; the stored score is
    ``max(0, -log10(P_match + epsilon))`` with ``P_match = 1 - (1 - p_joint) ** M``.
    Rather than asking whether the stored score sits *at* a bound, this
    reproduces the pipeline's own arithmetic and compares the result, which
    covers the bounds and everything between them in one expression.

    That distinction is not academic. ``epsilon`` does not act only at the
    ceiling: it inflates the denominator continuously as ``P_match``
    approaches it, so a score of 14.1 against a stabiliser of 1e-15 is already
    0.05 below the additive figure — enough to trip a tolerance built for
    rounding, while sitting nowhere near the 15.0 ceiling a bound test looks
    for. Such pairs were reported as unreconciled, in a sentence quoting two
    figures that print identically.

    ``None`` when ``epsilon`` is unknown, where the stored score cannot be
    predicted; the caller then bounds it from above instead.
    """
    p_joint = 10.0 ** -total
    pairs = n_anchor * (n_anchor - 1) // 2
    if pairs <= 0 or p_joint >= 1.0:
        # No comparison to make, or a "probability" at or above one: either
        # way a chance match is not unlikely and the score is at its floor.
        p_match = 1.0
    else:
        # ``1 - (1 - p) ** M`` loses every significant digit for p below
        # about 1e-16, which is exactly the range a score of 15 lives in.
        p_match = -math.expm1(pairs * math.log1p(-p_joint))
    denominator = p_match + (epsilon or 0.0)
    if epsilon is None:
        if p_match <= 0.0:
            return None
        return max(0.0, -math.log10(p_match))
    if denominator <= 0.0:
        return None
    return max(0.0, -math.log10(denominator))


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

    The page states the total the contributions and the anchor adjustment
    come to, and leaves it at that: the bounds below move no classification,
    and a paragraph of arithmetic on every case cost more attention than it
    returned. What survives here is the part that is not presentational —
    deciding whether a difference between that total and the stored score is
    explained by a bound or is a genuine disagreement worth recording as a
    gap.

    The stored score is

        L_match = max(0, -log10(P_match + epsilon)),
        P_match  = 1 - (1 - p_joint) ** M,

    while the two figures shown add to ``-log10(p_joint x M)``. That sum
    tracks the stored score only while ``p_joint x M`` is small, and the score
    is bounded at **both** ends:

    * **Ceiling.** ``+ epsilon`` holds the score at ``-log10(epsilon)`` once
      the contributions imply a chance-match probability far below it. Named
      only when the run's epsilon is known.
    * **Floor.** ``max(0, ...)`` holds the score at zero once the additive
      figure turns negative. This is not merely a clamp: as ``p_joint x M``
      approaches one, ``P_match`` saturates towards one — a chance match is
      not unlikely at all — and the additive figure stops describing anything.
      It needs no run settings to state, because neither bound's arithmetic
      depends on epsilon here.

    Those two flags stay, because a reader and a caller both want to know
    which bound a score is sitting on. What they no longer do is decide
    whether a difference is a disagreement. Where epsilon is known the stored
    score is simply **recomputed** (:func:`_expected_score`) and compared: a
    bound test asks a yes/no question of a stabiliser whose effect is
    gradual, and reported perfectly ordinary pairs — a score of 14.1 is
    already 0.05 below its additive figure — as unreconciled. Where epsilon
    is unknown the stored score can only be bounded from above, and a score
    *below* that bound is unverifiable rather than wrong.
    """
    total = sum(r.contribution for r in matching_features)
    adjustment = _anchor_adjustment(features.anchor.N_anchor)
    stored = features.L_match
    ceiling = run_settings.score_ceiling if run_settings is not None else None

    epsilon = run_settings.epsilon if run_settings is not None else None

    additive = total + (adjustment or 0.0)
    at_ceiling = (
        ceiling is not None and abs(stored - ceiling) <= _RECONCILE_TOLERANCE
    )
    # The floor explains the difference only when the score is *at* zero and
    # the additive figure is below it. A negative additive figure with a
    # non-zero score is a genuine disagreement, not a bound.
    at_floor = (
        not at_ceiling
        and stored <= _RECONCILE_TOLERANCE
        and additive < -_RECONCILE_TOLERANCE
    )

    # With epsilon known, ``expected`` is what the pipeline would have stored.
    # Without it, ``expected`` is the score at epsilon = 0, which is an upper
    # bound: sitting below it is what an unrecorded stabiliser looks like, and
    # the second message below says so rather than claiming a disagreement.
    expected = _expected_score(total, features.anchor.N_anchor, epsilon)
    unexplained = (
        abs(additive - stored) > _RECONCILE_TOLERANCE
        if expected is None
        else abs(stored - expected) > _RECONCILE_TOLERANCE
    )

    if unexplained:
        # Enough decimals that the two figures read as different numbers. A
        # note saying "totals +14.1, while the run stored 14.1" contradicts
        # itself, and the difference it is reporting is exactly the part a
        # single decimal hides.
        places = separating_decimals(additive, stored)
        # The page states the additive figure and never the stored score, so
        # a disagreement between them is otherwise invisible to a reader. It
        # has to be recorded whether or not epsilon is known: not knowing it
        # leaves a ceiling unverifiable, but knowing it and still failing to
        # reconcile is the more serious of the two and used to pass silently.
        gaps.append(
            EvidenceGap(
                code=EvidenceGapCode.SCORE_UNRECONCILED,
                message=(
                    "The matching evidence listed here totals "
                    f"{additive:+.{places}f}, while the run stored a matching "
                    f"score of {stored:.{places}f}. This page shows the "
                    "total, and the score's own arithmetic does not account "
                    "for the difference."
                    if ceiling is not None
                    else (
                        "The matching evidence listed here totals "
                        f"{additive:+.{places}f}, while the run stored a "
                        f"matching score of {stored:.{places}f}. This page "
                        "shows the total. The run's numerical stabiliser was "
                        "not recorded, so the stored score cannot be "
                        "reproduced; supply the run manifest to resolve this."
                    )
                ),
            )
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
        additive_total=additive,
        additive_total_text=f"{additive:+.1f}",
        stored_score=stored,
        stored_score_text=f"{stored:.1f}",
        score_ceiling=ceiling,
        ceiling_reached=at_ceiling,
        floor_reached=at_floor,
        reconciled=not unexplained,
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
    reader. Where a run recorded neither, the substances are recovered from
    that description (see :mod:`.legacy`) and the sentence reads the same, so
    the term leads whatever the run's vintage.

    The substance leads, because that is what a reader is looking for when
    scanning a list of findings; the direction follows it.

    The wording says the drug is *absent* from the other report, not that it is
    "a key drug in report A only": the latter reads as though both reports list
    it and only one calls it key, which is a different finding and not the one
    the check makes.
    """
    found = resolved_drug_set_terms(inc)
    if found is None:
        return inc.description or "A key drug is reported in only one report."
    terms, kind, _recovered = found

    # The stored terms are lowercased substance names. Where the drug table
    # shows a report's own spelling, use that here too, so a reader matching
    # the finding against the table sees the same word in both places.
    lookup = reported_names or {}
    names = "; ".join(
        lookup.get(normalize_drug_key(term), term) for term in terms
    )
    plural = "key drugs" if len(terms) > 1 else "key drug"
    if kind == DRUG_SET_KIND_MISSING_IN_R2:
        source, other = "A", "B"
    elif kind == DRUG_SET_KIND_MISSING_IN_R1:
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


def _drug_set_names(
    inc: Inconsistency, reported_names: dict[str, str] | None = None
) -> str:
    """Just the substances a drug-set finding is about: ``vitamins nos``.

    The direction — which report carried the drug — is what the sentence in
    the findings table says. In a chip there is room for the substance and
    nothing else, and the substance is what a reader is scanning for.
    """
    found = resolved_drug_set_terms(inc)
    if found is None:
        return ""
    terms, _kind, _recovered = found
    lookup = reported_names or {}
    return "; ".join(
        lookup.get(normalize_drug_key(term), term) for term in terms
    )


def _short_inconsistency_text(
    inc: Inconsistency, reported_names: dict[str, str] | None = None
) -> str:
    """One inconsistency reduced to the values behind it, or empty.

    Empty where there is nothing shorter to say than the sentence itself: the
    chip then falls back to that sentence rather than inventing a stub.
    """
    if inc.feature == DRUG_SET_FEATURE:
        return _drug_set_names(inc, reported_names)
    if inc.terms:
        return "; ".join(inc.terms)
    return ""


def _short_date_finding_text(finding: ClinicalDateFindingSummary) -> str:
    """A clinical-date finding as the two dates: ``2002-10-28 vs 2005``.

    Which date of which entity, and how far apart they are, is the findings
    table's sentence. What a reader checks at a glance is the pair.
    """
    return f"{finding.source_date} vs {finding.matched_date}"


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


def _date_finding_lead(finding: ClinicalDateFindingSummary) -> str:
    """What a clinical-date finding is about: the entity, named."""
    entity = parse_entity_label(finding.entity_label)
    label = _ENTITY_READABLE.get(entity.kind, "Clinical date")
    if entity.key:
        label = f"{label} for {entity.key}"
    return label


def _date_finding_tail(finding: ClinicalDateFindingSummary) -> str:
    """What a clinical-date finding adds after its two dates."""
    tail = ""
    if finding.diff_days is not None:
        days = "day" if abs(finding.diff_days) == 1 else "days"
        tail += f", at least {abs(finding.diff_days)} {days} apart"
    if finding.downgrades:
        tail += "; severity reduced because " + "; ".join(finding.downgrades)
    return f"{tail}."


def _date_finding_cells(
    finding: ClinicalDateFindingSummary,
) -> tuple[EvidenceCell, EvidenceCell]:
    """The two dates a clinical-date finding names, as red cells.

    Red because that is what the finding is: a retained inconsistency naming
    these two values. They were previously inside a prose string, where the
    page's one colour rule could not reach them, so an inconsistency about
    two dates showed neither of them in red.

    New cells rather than the ones in the drug and adverse-event tables.
    Those are per record and per report — a drug with two regimens has two
    start-date cells — and there is no single pair to borrow. A publication
    transform reaches these by kind like any other date.
    """
    return (
        EvidenceCell(
            text=finding.source_date,
            kind=ValueKind.AE_ONSET_DATE,
            role=EvidenceRole.INCONSISTENT,
        ),
        EvidenceCell(
            text=finding.matched_date,
            kind=ValueKind.AE_ONSET_DATE,
            role=EvidenceRole.INCONSISTENT,
        ),
    )


def _date_finding_text(finding: ClinicalDateFindingSummary) -> str:
    """A reader-facing sentence for one clinical-date finding."""
    lead = _date_finding_lead(finding)
    return (
        f"{lead}: {finding.source_date} versus {finding.matched_date}"
        f"{_date_finding_tail(finding)}"
    )


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


def _demographic_cells(
    characteristics: list[CharacteristicRow],
) -> dict[str, tuple[EvidenceCell, EvidenceCell]]:
    """The two cells behind each demographic feature, keyed by feature name.

    The cells themselves, not their text: a finding that quotes them is then
    quoting the very objects the characteristics table renders, so the two
    cannot drift and one publication rewrite serves both.
    """
    by_label = {row.label: row for row in characteristics}
    cells: dict[str, tuple[EvidenceCell, EvidenceCell]] = {}
    for feature, label in FEATURE_TO_DEMO_LABEL.items():
        row = by_label.get(label)
        if row is None or row.left.missing or row.right.missing:
            continue
        cells[feature] = (row.left, row.right)
    return cells


def _discrepancy_column(feature: str) -> str:
    """The value for the findings table's own column, in the reader's terms."""
    if feature in _DISCREPANCY_COLUMN_LABEL:
        return _DISCREPANCY_COLUMN_LABEL[feature]
    if feature.startswith("ae_clinical_picture"):
        return "Adverse events"
    if feature.startswith("source_case_"):
        return "Source and case identifiers"
    return _readable_feature(feature)


def _severity_weights(
    penalty: InconsistencyPenalty | None,
) -> dict[str, float]:
    """Severity name -> the weight this run applied to it.

    Read from the stored penalty, which records the weight *actually used*
    rather than the configured default, so a run with custom weights is
    described with its own numbers. Empty when no classification was saved,
    in which case no weight is shown at all: the defaults are not a safe
    substitute for what a given run did.
    """
    if penalty is None:
        return {}
    return {term.severity.value: term.weight for term in penalty.terms}


def _build_discrepancies(
    features: PairFeatures,
    reported_names: dict[str, str] | None = None,
    penalty: InconsistencyPenalty | None = None,
    demographic_cells: dict[str, tuple[EvidenceCell, EvidenceCell]] | None = None,
) -> list[DiscrepancyVM]:
    """Every finding the assessment recorded, retained ones first.

    Retained and suppressed findings are both listed, and marked as such. A
    suppressed finding contributed nothing to the penalty, so presenting it as
    operative would be wrong; omitting it entirely would hide an assessment the
    assessor actually made.

    Each row carries what it added to the penalty, taken from the run's own
    stored weights. A suppressed row carries ``0.0``, which is what it
    contributed; a downgraded clinical-date finding carries the weight of its
    *final* severity, which is the one that was counted.

    Ordered by severity descending, then by text, so the order is
    deterministic and the most serious finding is read first.
    """
    weights = _severity_weights(penalty)

    def weighed(severity: str, *, suppressed: bool) -> tuple[float | None, str]:
        if suppressed:
            return 0.0, "+0.0"
        weight = weights.get(severity)
        return weight, "" if weight is None else f"+{weight:.1f}"

    sides = demographic_cells or {}
    items: list[DiscrepancyVM] = []
    for inc in features.inconsistencies:
        weight, weight_text = weighed(
            inc.severity.value, suppressed=inc.suppressed
        )
        pair = sides.get(inc.feature)
        items.append(
            DiscrepancyVM(
                feature_label=_discrepancy_column(inc.feature),
                severity=inc.severity.value,
                text=_inconsistency_text(inc, reported_names),
                short_text=_short_inconsistency_text(inc, reported_names),
                left=pair[0] if pair else None,
                right=pair[1] if pair else None,
                retained=not inc.suppressed,
                suppressed=inc.suppressed,
                suppressed_by=inc.suppressed_by,
                weight=weight,
                weight_text=weight_text,
                source_context=inc.feature.startswith("source_case_"),
            )
        )
    for finding in features.clinical_date_findings:
        weight, weight_text = weighed(finding.severity.value, suppressed=False)
        left_date, right_date = _date_finding_cells(finding)
        items.append(
            DiscrepancyVM(
                feature_label="Clinical dates",
                severity=finding.severity.value,
                text=_date_finding_text(finding),
                short_text=_short_date_finding_text(finding),
                left=left_date,
                right=right_date,
                lead=_date_finding_lead(finding),
                tail=_date_finding_tail(finding),
                retained=True,
                downgrades=list(finding.downgrades),
                weight=weight,
                weight_text=weight_text,
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


#: The study's binary reference vocabulary. The only values a mapping may
#: produce, and the only labels this review will show as a human verdict.
REFERENCE_VOCABULARY = frozenset({"duplicate", "non_duplicate", "abstain"})

#: The reference vocabulary an annotation may carry, mapped to the study's
#: binary reference labels. The pipeline's own four labels are deliberately
#: absent: an annotation carrying one of those is a *prediction*, not a
#: reference verdict, and treating it as one is how the two vocabularies get
#: conflated.
BUILTIN_REFERENCE_TERMS: dict[str, str] = {
    "duplicate": "duplicate",
    "non_duplicate": "non_duplicate",
    "abstain": "abstain",
}


def normalise_reference_term(value: str) -> str:
    """Fold an annotation value, or a mapping key, to one comparable form.

    ``Otherwise related``, ``otherwise_related`` and ``Otherwise-Related`` are
    one term written three ways, and a reference set is free to use any of
    them. Case, surrounding space, and runs of spaces, underscores or hyphens
    are all folded, so a mapping written the obvious way matches the data.
    """
    return re.sub(r"[\s_-]+", "_", value.strip().lower()).strip("_")


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
    #: Why no screening verdict could be read, when one was looked for and
    #: rejected. Rendered as a gap, so a malformed verdict is visible rather
    #: than indistinguishable from an absent one. See :mod:`.screening`.
    screening_note: str = ""
    #: Why no reference label could be read, when the annotation carried a
    #: value this review does not know. Rendered as a gap for the same reason
    #: as ``screening_note``: an unread label and an absent one both show
    #: "Not supplied", and only the note tells them apart.
    reference_note: str = ""
    scle: SCLEAssessment = dc_field(default_factory=SCLEAssessment)
    #: Name of the positive-class rule used to compare the reference label
    #: against the vigiTwin label.
    positive_class: str = "primary"


@dataclass(frozen=True)
class ExamplePresentation:
    """Curated, human-authored framing for one example."""

    example_id: str = ""
    #: What the heading calls this example and what surrounding text cites it
    #: as. Defaults to ``example_id``, which is the right label whenever a
    #: curator chose one; a collection sets it explicitly where it had to
    #: invent an id, so the page shows "Example 3" rather than the slug
    #: ``example-3``.
    example_label: str = ""
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
            label="SCLE of reference label", value=value, state=state
        )
    )
    return tiles


# ---------------------------------------------------------------------------
# Assessment section
# ---------------------------------------------------------------------------

#: What a context names when the artifact recorded no country to name it by.
_CONTEXT_LEVEL_FALLBACK = {
    ReportingContextLevel.COUNTRY_YEAR: "Country-year",
    ReportingContextLevel.COUNTRY: "Country",
}


def _context_cell(features: PairFeatures) -> EvidenceCell:
    """The population the rarity was judged against, named.

    ``SE 2003`` rather than ``Country-year``: the level alone says how the
    population was chosen but not which population it is, and a reader
    comparing two cases wants the second. The country is whatever the run
    recorded — an ISO code in most artifacts — and is shown verbatim rather
    than expanded, because inventing a country name from a code is a mapping
    this module has no business owning.

    A cell, not a string: a country is a report value, so the later
    publication phase masks or substitutes it here exactly as it does in the
    characteristics table. Neutral, because country agreement defines the
    comparison population and is never counted as matching evidence.
    """
    context = features.reporting_context
    level = context.level
    country = (context.country or "").strip()
    if level is ReportingContextLevel.GLOBAL or not country:
        text = (
            "Global"
            if level is ReportingContextLevel.GLOBAL
            else _CONTEXT_LEVEL_FALLBACK.get(level, "Global")
        )
    elif level is ReportingContextLevel.COUNTRY_YEAR and context.year:
        text = f"{country} {context.year}"
    else:
        text = country
    return EvidenceCell(text=text, kind=ValueKind.COUNTRY)


def _scored_penalty_total(
    penalty: InconsistencyPenalty,
    discrepancies: list[DiscrepancyVM],
    *,
    source_context_decided: bool,
) -> float:
    """The penalty of the findings the panel lists directly above the figure.

    ``w_tot`` is the stored total over every retained finding, the
    source-derived one included. Where the source context decided, that
    finding is shown as source-context evidence and not as a weighed
    inconsistency — so the panel read "Inconsistencies: None" with a penalty
    of 100.0 underneath, which is the arithmetic of a row it had just said
    was not there.

    The figure is therefore the sum of the chips above it. With nothing
    listed that is exactly zero, which is the common case: a pinned verdict
    usually produces the only finding there is. A pair with a source verdict
    *and* an ordinary finding shows the ordinary one's weight.

    Falls back to the stored total where a listed finding's weight is not
    known — no classification recorded the weights, so there is no sum to
    take and quoting a default this run may not have used would be worse than
    quoting the figure it stored. The stored ``w_tot`` is untouched either
    way, in the artifacts and in the audit record.
    """
    if not source_context_decided:
        return penalty.w_tot
    scored = [d for d in discrepancies if d.retained and not d.source_context]
    if any(d.weight is None for d in scored):
        return penalty.w_tot
    return sum(d.weight or 0.0 for d in scored)


def _penalty_total(penalty: InconsistencyPenalty, total: float) -> str:
    """The penalty, as the one figure it is: ``1.2``.

    The arithmetic that produced it — ``3 x 0.4 = 1.2`` — sat here until the
    chips above began carrying each finding's own weight. With the terms
    listed a row above, restating them as a multiplication was the same sum
    twice, and the row a reader comes to this panel for is the total.

    Where the run made no comparison the figure is flagged as unweighed, in
    the page's own word for it — ``findings_weighed`` governs the same fact a
    few sections below. What the comparison *was*, where one happened, is left
    to the clause beneath this row rather than said twice.
    """
    shown = f"{total:.1f}"
    if penalty.threshold is None:
        return f"{shown} (not weighed)"
    return shown


def _as_clause(sentence: str | None) -> str:
    """A clause to sit under a figure: lower-cased, with no closing period."""
    if not sentence:
        return ""
    text = sentence[0].lower() + sentence[1:]
    return text.rstrip(".")


def shown_score(
    features: PairFeatures, reconciliation: ReconciliationVM | None
) -> float:
    """The matching score as this page states it: the sum it also shows.

    The stored ``L_match`` is bounded — held at ``-log10(epsilon)`` above and
    at zero below — and the page does not describe those bounds, because
    neither moves a classification. Printing the bounded figure beside a
    column that adds to a different one would leave a reader to reconcile
    two numbers for no gain, so the page states the sum throughout and the
    clause beside it quotes the same number.

    Except where the two genuinely disagree. The sum is worth quoting because
    it describes the same decision the stored score does; once it provably
    does not, quoting it would let the page say a thing like "the matching
    score of -3.2 ... exceeding the independence threshold of 1.0". So an
    unreconciled case falls back to the figure the decision was actually made
    on, and the gap recorded alongside it names the total the contributions
    came to. A bound is not a disagreement: those still show the sum.
    """
    if reconciliation is None or not reconciliation.reconciled:
        return features.L_match
    return reconciliation.additive_total


def _build_summary_panel(
    features: PairFeatures,
    classification: ClassificationResult | None,
    run_settings: EvidenceRunSettings | None,
    shared_sets: list[SharedSetVM],
    reconciliation: ReconciliationVM | None,
    discrepancies: list[DiscrepancyVM] | None = None,
    *,
    source_context_decided: bool = False,
) -> CaseSummaryVM:
    """The assessment chain in figures, for the panel above the narrative.

    Assembled from what the sections below already hold, not recomputed from
    the artifacts: the anchor sets and the adjustment are the objects section
    2 renders, and the two clauses come from the same builders that write the
    narrative paragraph. A panel derived twice could disagree with the page it
    sits on top of; this one cannot.
    """
    adjustment: EvidenceCell | None = None
    if reconciliation is not None and reconciliation.anchor_adjustment:
        adjustment = EvidenceCell(
            text=reconciliation.anchor_adjustment_text,
            kind=ValueKind.SCORE,
            # Red: a larger anchor population makes a chance match likelier,
            # so this term pushes the score down towards independence. It is
            # not a finding about a reported value, but it told against the
            # pair, which is what red means here.
            role=EvidenceRole.INCONSISTENT,
            attributions=[
                Attribution(
                    kind=AttributionKind.ANCHOR_MULTIPLICITY,
                    source=EvidenceSource.ANCHOR,
                    feature="anchor",
                    contribution=reconciliation.anchor_adjustment,
                    note=(
                        "the number of report pairs the anchor population "
                        "could yield, which the score is corrected for"
                    ),
                )
            ],
        )

    penalty = classification.penalty if classification is not None else None
    score = shown_score(features, reconciliation)
    return CaseSummaryVM(
        context=_context_cell(features),
        anchor_sets=[s for s in shared_sets if s.is_anchor],
        anchor_n_text=(
            f"{features.anchor.N_anchor:,} "
            f"{'report' if features.anchor.N_anchor == 1 else 'reports'}"
        ),
        anchor_adjustment=adjustment,
        score_text=format_score(score),
        independence_note=(
            ""
            if classification is None
            else _as_clause(
                independence_clause(
                    features, classification, run_settings, score=score
                )
            )
        ),
        penalty_text=(
            ""
            if penalty is None
            else _penalty_total(
                penalty,
                _scored_penalty_total(
                    penalty,
                    discrepancies or [],
                    source_context_decided=source_context_decided,
                ),
            )
        ),
        same_case_note=(
            ""
            if classification is None
            else _as_clause(same_case_clause(classification))
        ),
    )


_INDEPENDENCE_LABEL = {
    "Non-independent": "Non-independent",
    "Independent": "Independence not rejected",
}


def _build_assessment_rows(
    features: PairFeatures,
    classification: ClassificationResult,
    run_settings: EvidenceRunSettings | None,
    *,
    score: float | None = None,
) -> list[AssessmentRowVM]:
    """The two decisions and the final label, each stated separately.

    The independence and same-case assessments answer different questions and
    are shown as different rows, with the reason vigiTwin recorded for each.
    Where no same-case comparison was made, the row says so rather than being
    omitted — an absent row reads as an oversight.
    """
    independence = independence_clause(
        features, classification, run_settings, score=score
    )
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
        verdict = relationship_label(relationship)
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


def _explicit_link_note(
    features: PairFeatures, run_settings: EvidenceRunSettings | None = None
) -> str:
    """What the case-identifier fields showed — including "nothing to check".

    An absent link and an unchecked field are different facts, and the stored
    verdict distinguishes them. Reporting ``not_assessed`` as "no link" would
    turn missing data into evidence of absence.

    A third fact sits above both: a run configured not to use explicit links
    at all. Its artifacts may still carry an ``explicit_link`` field — an
    empty or defaulted one — and the page then read that as "neither report
    carried case-identifier data", blaming the data for a check nobody ran.
    ``explicit_link_enabled`` is recorded on the run manifest, or declared
    per dataset in a curated collection when the manifest predates it.
    """
    if run_settings is not None and run_settings.explicit_link_enabled is False:
        return (
            "This run did not use explicit inter-report case links, so the "
            "case-identifier fields were not examined."
        )
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


def _explicit_link_found(
    features: PairFeatures, run_settings: EvidenceRunSettings | None = None
) -> bool:
    """Whether the case-identifier fields produced a link, as opposed to not.

    The same two inputs :func:`_explicit_link_note` reads, so the flag and
    the sentence cannot come to disagree: a run that did not use explicit
    links found none whatever its artifacts carry in the field, and both
    ``NOT_ASSESSED`` and ``NO_LINK`` are absences — of the check and of a
    result — rather than a link.
    """
    if run_settings is not None and run_settings.explicit_link_enabled is False:
        return False
    link = features.explicit_link
    return link is not None and link.verdict is ExplicitLinkVerdict.EXPLICIT_LINK


#: What each source-case verdict says, in the reader's terms.
#:
#: The stored values are field names — ``same_source_different_case`` — and
#: the page used to print them with their underscores swapped for spaces. A
#: reader met "Same source different case", which is three nouns and no
#: sentence, and could not tell whether the *reports* differed or the *cases*
#: did. These say which.
_SOURCE_VERDICT_LABEL: dict[SourceCaseVerdict, str] = {
    SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT: (
        "Shared source, describing a single patient"
    ),
    SourceCaseVerdict.SAME_SOURCE_SAME_CASE: (
        "Shared source, and the same case within it"
    ),
    SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE: (
        "Shared source, but different cases within it"
    ),
    SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE: (
        "Shared source, cases within it not comparable"
    ),
    SourceCaseVerdict.MULTI_PATIENT_CONTEXT_NO_LINK: (
        "A source describing several patients, with no shared identifier"
    ),
    SourceCaseVerdict.NO_DECISIVE_SIGNAL: "No decisive source or case signal",
    SourceCaseVerdict.NOT_ASSESSED: "Source context not assessed",
}

#: What kind of source an identifier names, for the label beside its value.
#: Never the enum member: ``article_reference`` is a field name, and a
#: parenthetical field name in a reader-facing line is the clearest sign a
#: page is showing its internals.
_SOURCE_IDENTIFIER_LABEL: dict[SourceIdentifierType, str] = {
    SourceIdentifierType.DOI: "Publication (DOI)",
    SourceIdentifierType.PMID: "Publication (PubMed ID)",
    SourceIdentifierType.TRIAL_REGISTRATION: "Trial registration",
    SourceIdentifierType.LINKED_CASE: "Linked-case reference",
    SourceIdentifierType.PROTOCOL_NUMBER: "Protocol number",
    SourceIdentifierType.STUDY_NAME: "Study name",
    # The two catch-alls. ``article_reference`` is documented as "a free-text
    # citation when no more specific type fits", so labelling it a literature
    # citation claimed something specific about a source that need not be a
    # publication at all — a registry entry, a programme, a company's own
    # case series. Source-neutral, and the same words for both, because both
    # mean "shared, and we cannot say more than that".
    SourceIdentifierType.ARTICLE_REFERENCE: "Shared external source",
    SourceIdentifierType.UNKNOWN: "Shared external source",
}

#: Verdicts that establish a shared source outright.
_SHARED_SOURCE_VERDICTS = frozenset(
    {
        SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT,
        SourceCaseVerdict.SAME_SOURCE_SAME_CASE,
        SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE,
        SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
    }
)

#: Verdicts that settle the relationship on their own, whatever the penalty
#: comes to. Mirrors the pinned branches of
#: :func:`llm_dup_dev.classifier.decision_logic._relationship`; the builder
#: describes that decision and never remakes it.
_RELATIONSHIP_PINNING_VERDICTS = frozenset(
    {SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE}
)


def _source_case_note(features: PairFeatures) -> str:
    """The source and case verdict in prose, naming no identifier.

    The identifier lives in :class:`SourceContextVM.identifier`, a cell, for
    the reason ``context_phrase`` gives about the reporting context: keeping
    the identity out of the sentences means a publication export needs no
    different ones, and the one place it appears is a cell the transform
    reaches by kind.
    """
    assessment = features.source_case_assessment
    if assessment is None:
        return ""
    if assessment.verdict in (
        SourceCaseVerdict.NOT_ASSESSED,
        SourceCaseVerdict.NO_DECISIVE_SIGNAL,
    ):
        return ""
    label = _SOURCE_VERDICT_LABEL.get(assessment.verdict, "")
    if not label:  # pragma: no cover - the table covers the enum
        return ""
    return f"{label}."


def _case_comparison_text(assessment: SourceCaseAssessment) -> str:
    """What became of the cases *within* the shared source.

    The distinction the verdict turns on, said rather than left to be
    inferred from the verdict's name: two reports can share a source and
    describe one case, two cases, or two labels nobody can line up.
    """
    if not assessment.case_identifiers_compared:
        return (
            "Case labels within the source were not available on both sides, "
            "so they were not compared."
        )
    if not assessment.case_identifiers_comparable:
        return (
            "Case labels within the source were present on both sides but "
            "not of a kind that can be compared, so their difference is not "
            "evidence of different patients."
        )
    if assessment.verdict is SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE:
        return (
            "Case labels within the source were compared and name different "
            "cases."
        )
    return "Case labels within the source were compared and agree."


def _source_context(
    features: PairFeatures,
    classification: ClassificationResult | None,
) -> SourceContextVM | None:
    """The source-context evidence, as structure.

    ``None`` where the comparison reached nothing worth a reader's time: not
    assessed, or assessed and decisive about neither the source nor the case.
    """
    assessment = features.source_case_assessment
    if assessment is None:
        return None
    if assessment.verdict in (
        SourceCaseVerdict.NOT_ASSESSED,
        SourceCaseVerdict.NO_DECISIVE_SIGNAL,
    ):
        return None

    shared = assessment.verdict in _SHARED_SOURCE_VERDICTS
    identifier: EvidenceCell | None = None
    identifier_label = ""
    if assessment.shared_source_identifier:
        identifier = EvidenceCell(
            text=assessment.shared_source_identifier,
            kind=ValueKind.SOURCE_IDENTIFIER,
            # Green: a shared source is evidence counted *for* the pair, in
            # the same sense the matching sets are. It is not a term in the
            # matching score, which is why it carries no contribution.
            role=EvidenceRole.MATCHING if shared else EvidenceRole.NEUTRAL,
            attributions=[
                Attribution(
                    kind=AttributionKind.MATCHING_FEATURE,
                    source=EvidenceSource.SOURCE_CASE,
                    feature="shared_source_identifier",
                    note="Both narratives name this source",
                )
            ]
            if shared
            else [],
        )
        identifier_label = _SOURCE_IDENTIFIER_LABEL.get(
            assessment.shared_source_identifier_type
            or SourceIdentifierType.UNKNOWN,
            "Source reference",
        )

    decided = assessment.verdict in _RELATIONSHIP_PINNING_VERDICTS and (
        classification is not None
        and classification.relationship_assessment is not None
    )
    return SourceContextVM(
        verdict_label=_SOURCE_VERDICT_LABEL.get(assessment.verdict, ""),
        identifier=identifier,
        identifier_label=identifier_label,
        case_comparison=_case_comparison_text(assessment),
        decided_relationship=decided,
        role=EvidenceRole.MATCHING if shared else EvidenceRole.NEUTRAL,
    )


#: Which pathway each recorded independence reason belongs to.
_INDEPENDENCE_PATHWAY: dict[IndependenceReason, DecisionPathway] = {
    IndependenceReason.SHARED_SOURCE: DecisionPathway.SHARED_SOURCE,
    IndependenceReason.EXPLICIT_LINK: DecisionPathway.EXPLICIT_LINK,
    IndependenceReason.RARITY_COMPELLING: DecisionPathway.PROBABILISTIC_MATCHING,
    IndependenceReason.RARITY_INTERMEDIATE: (
        DecisionPathway.PROBABILISTIC_MATCHING
    ),
    IndependenceReason.RARITY_LIMITED: DecisionPathway.PROBABILISTIC_MATCHING,
    IndependenceReason.RARITY_INSUFFICIENT: (
        DecisionPathway.PROBABILISTIC_MATCHING
    ),
}

_EXPLICIT_LINK_BASIS = "One report references the other"


def _as_sentence(clause: str) -> str:
    """A clause from the summary's builders, as a sentence on its own.

    The clauses are written to sit mid-paragraph, so some arrive lower-cased
    ("the total inconsistency penalty of ...") and some already capitalised
    ("Independence was rejected ..."). Jinja's ``capitalize`` would have done
    this, and it lower-cases the rest of the string: one clause begins with
    "vigiTwin", which came out as "Vigitwin". So a word that already carries a
    capital is left alone — it is a name, and capitalising its first letter
    renames it.
    """
    if not clause:
        return ""
    first = clause.split(" ", 1)[0]
    if not any(character.isupper() for character in first):
        clause = clause[0].upper() + clause[1:]
    return clause if clause.endswith(".") else f"{clause}."


def _build_decision_tiles(
    features: PairFeatures,
    classification: ClassificationResult | None,
    source_context: SourceContextVM | None,
    run_settings: EvidenceRunSettings | None,
    *,
    score: float | None,
) -> list[DecisionTileVM]:
    """The two decision stages, described from what the run recorded.

    Described, never re-derived: every branch below reads
    ``independence_reason``, ``relationship_assessment``, the penalty's
    ``threshold`` and the source verdict, all of them stored, and maps them
    onto a pathway. Nothing here can reach a different answer from the
    classifier's, because nothing here computes one.
    """
    if classification is None:
        absent = DecisionTileVM(
            stage=DecisionStage.INDEPENDENCE,
            stage_label="Independence assessment",
            outcome="Not recorded",
            state=TileState.NOT_ENTERED,
        )
        return [
            absent,
            absent.model_copy(
                update={
                    "stage": DecisionStage.RELATIONSHIP,
                    "stage_label": "Relationship assessment",
                }
            ),
        ]
    return [
        _independence_tile(
            features, classification, source_context, run_settings, score
        ),
        _relationship_tile(features, classification, source_context),
    ]


#: Which way each outcome points, for the tile's colour. The same two
#: colours the tables use: green counts towards the reports being one case,
#: red counts against, and an abstention points neither way.
_INDEPENDENCE_ROLE: dict[IndependenceAssessment, EvidenceRole] = {
    IndependenceAssessment.NON_INDEPENDENT: EvidenceRole.MATCHING,
    IndependenceAssessment.INDEPENDENT: EvidenceRole.INCONSISTENT,
}

#: What each relationship outcome is called on the page.
#:
#: The stored values are ``Same_case`` and ``Different_case``: field names,
#: and the page printed them as they stood. The plural matters — two reports
#: found not to be one case describe *different cases* — and an underscore in
#: a decision tile is the same tell as a parenthetical field name anywhere
#: else. Used for the tile and for the assessment table, so the page calls
#: one outcome one thing.
_RELATIONSHIP_LABEL: dict[RelationshipAssessment, str] = {
    RelationshipAssessment.SAME_CASE: "Same case",
    RelationshipAssessment.DIFFERENT_CASE: "Different cases",
    RelationshipAssessment.ABSTAIN: "Abstain",
}


def relationship_label(relationship: RelationshipAssessment) -> str:
    """The reader-facing name for a relationship outcome."""
    return _RELATIONSHIP_LABEL.get(
        relationship, relationship.value.replace("_", " ").capitalize()
    )


_RELATIONSHIP_ROLE: dict[RelationshipAssessment, EvidenceRole] = {
    RelationshipAssessment.SAME_CASE: EvidenceRole.MATCHING,
    RelationshipAssessment.DIFFERENT_CASE: EvidenceRole.INCONSISTENT,
    RelationshipAssessment.ABSTAIN: EvidenceRole.NEUTRAL,
}

#: What the case labels within a shared source came to, as the relationship
#: tile's basis. The source made the comparison possible; these are what it
#: turned on, which is the half a reader was missing.
_INDIVIDUAL_IDENTIFIER_BASIS: dict[SourceCaseVerdict, str] = {
    SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE: (
        "Differing comparable individual identifiers"
    ),
    SourceCaseVerdict.SAME_SOURCE_SAME_CASE: (
        "Matching comparable individual identifiers"
    ),
}


def _independence_tile(
    features: PairFeatures,
    classification: ClassificationResult,
    source_context: SourceContextVM | None,
    run_settings: EvidenceRunSettings | None,
    score: float | None,
) -> DecisionTileVM:
    reason = classification.independence_reason
    pathway = (
        _INDEPENDENCE_PATHWAY.get(reason, DecisionPathway.UNRECORDED)
        if reason is not None
        else DecisionPathway.UNRECORDED
    )
    shown = features.L_match if score is None else score

    if pathway is DecisionPathway.SHARED_SOURCE:
        basis = "Shared source"
    elif pathway is DecisionPathway.EXPLICIT_LINK:
        basis = _EXPLICIT_LINK_BASIS
    else:
        basis = f"Matching score {format_score(shown)}"
    # One wording for the tile and the prose, from the clause builder the
    # summary uses, so the two cannot drift apart.
    note = _as_sentence(
        independence_clause(features, classification, run_settings, score=score)
    )

    return DecisionTileVM(
        stage=DecisionStage.INDEPENDENCE,
        stage_label="Independence assessment",
        outcome=classification.independence_assessment.value,
        role=_INDEPENDENCE_ROLE.get(
            classification.independence_assessment, EvidenceRole.NEUTRAL
        ),
        pathway=pathway,
        # Nothing is gained by repeating the badge directly under it.
        basis="" if basis == pathway_label(pathway) else basis,
        note=note,
    )


def _relationship_tile(
    features: PairFeatures,
    classification: ClassificationResult,
    source_context: SourceContextVM | None,
) -> DecisionTileVM:
    relationship = classification.relationship_assessment
    penalty = classification.penalty
    assessment = features.source_case_assessment
    verdict = assessment.verdict if assessment is not None else None
    reason = classification.independence_reason

    if relationship is None:
        return DecisionTileVM(
            stage=DecisionStage.RELATIONSHIP,
            stage_label="Relationship assessment",
            outcome="Not reached",
            state=TileState.NOT_ASSESSED,
            pathway=DecisionPathway.NOT_REACHED,
            note=_as_sentence(same_case_clause(classification) or ""),
        )

    note = _as_sentence(same_case_clause(classification) or "")

    if verdict in _INDIVIDUAL_IDENTIFIER_BASIS:
        # The source made the comparison possible; the individual identifiers
        # are what it turned on, and a badge saying only "Shared source" left
        # a reader to work out which of the two had decided.
        pathway = DecisionPathway.INDIVIDUAL_IDENTIFIERS
        basis = _INDIVIDUAL_IDENTIFIER_BASIS[verdict]
    elif verdict is SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT:
        pathway = DecisionPathway.SHARED_SOURCE
        basis = "Source describes a single patient"
    elif reason is IndependenceReason.EXPLICIT_LINK:
        pathway = DecisionPathway.EXPLICIT_LINK
        basis = _EXPLICIT_LINK_BASIS
    elif penalty is not None and penalty.threshold is not None:
        pathway = DecisionPathway.INCONSISTENCY_PENALTY
        basis = f"Inconsistency penalty {penalty.w_tot:.1f}"
    else:
        pathway = DecisionPathway.UNRECORDED
        basis = ""

    if pathway is DecisionPathway.UNRECORDED and not note:
        # A saved run from before the penalty carried its threshold, or one
        # that recorded no penalty at all. Say that rather than leave a tile
        # a reader has to interpret by its emptiness.
        note = (
            "The run recorded no evidence pathway for this decision, so the "
            "page can state the outcome and not what reached it."
        )

    return DecisionTileVM(
        stage=DecisionStage.RELATIONSHIP,
        stage_label="Relationship assessment",
        outcome=relationship_label(relationship),
        role=_RELATIONSHIP_ROLE.get(relationship, EvidenceRole.NEUTRAL),
        pathway=pathway,
        basis="" if basis == pathway_label(pathway) else basis,
        note=note,
    )


def _narrative_note(r1: Report, r2: Report) -> str:
    """Any literature reference the two reports carry.

    It used to open by saying whether each report had a narrative at all —
    "Both reports carry a narrative", or "Only report A carries a narrative".
    Neither sentence was evidence about the pair: the first said that the
    ordinary case held, and the second named an absence a reader can do
    nothing with. What remains is the reference itself, which is a value and
    is cited.
    """
    refs = _dedupe(
        [r.literature_reference for r in (r1, r2) if r.literature_reference]
    )
    return " ".join(f"Literature reference: {ref}" for ref in refs)


def _build_narrative_blocks(
    features: PairFeatures,
    classification: ClassificationResult | None,
    presentation: ExamplePresentation,
    run_settings: EvidenceRunSettings | None,
    gaps: list[EvidenceGap],
    score: float | None = None,
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
        features, classification, run_settings=run_settings, score=score
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
        # Named for its author, not its scope: the block opposite is headed
        # "vigiTwin assessment summary", and what a reader needs to know is
        # which of the two a sentence came from. "Case-level" answered a
        # question nobody was asking.
        heading="Human interpretation",
        paragraphs=[
            p.strip() for p in human_text.strip().split("\n\n") if p.strip()
        ],
        is_human=True,
    )
    if presentation.summary_mode == "both":
        # vigiTwin's own account first, directly under the panel that states
        # the same assessment in figures: the two belong together, and a
        # human reading is a response to them rather than a preface.
        return [generated, human]
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
    if reference is not None and reference.screening_note:
        gaps.append(
            EvidenceGap(
                code=EvidenceGapCode.SCREENING_VERDICT_UNREADABLE,
                message=reference.screening_note,
            )
        )
    if reference is not None and reference.reference_note:
        gaps.append(
            EvidenceGap(
                code=EvidenceGapCode.REFERENCE_LABEL_UNREADABLE,
                message=reference.reference_note,
            )
        )

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
    summary_panel: CaseSummaryVM | None = None
    discrepancies: list[DiscrepancyVM] = []
    assessment_rows: list[AssessmentRowVM] = []
    anchor_note = ""
    context_note = ""
    explicit_link_note = ""
    explicit_link_found = False
    source_case_note = ""
    source_context: SourceContextVM | None = None
    narrative_blocks: list[EvidenceTextVM] = []

    if features is not None:
        _attach_matching_sets(drugs, aes, features)
        _attach_demographic_contributions(characteristics, features)
        _attach_clinical_date_matches(drugs, aes, features, gaps)
        _attach_inconsistencies(characteristics, drugs, aes, features, gaps)
        _attach_clinical_date_findings(
            characteristics, drugs, aes, features, gaps
        )

        shared_sets = _build_shared_sets(features)
        freq_rows = _build_freq_rows(frozen_bundle, features)
        # Keyed on the bundle rather than on the rows. A pair sharing no drug
        # or adverse-event terms produces no rows either, but nothing is
        # missing there — there is simply no co-occurrence to count, and
        # calling that a gap would send a reader looking for data that was
        # never relevant.
        if frozen_bundle is None:
            gaps.append(
                EvidenceGap(
                    code=EvidenceGapCode.FREQUENCY_STATS_MISSING,
                    message=(
                        "Contextual frequency counts are not shown: no frozen "
                        "frequency statistics were supplied for this pair."
                    ),
                )
            )
        matching_features = _build_matching_features(
            features, _demographic_match_values(characteristics)
        )
        reconciliation = _build_reconciliation(
            features, matching_features, run_settings, gaps
        )
        discrepancies = _build_discrepancies(
            features,
            {row.sort_key: row.name.text for row in drugs},
            penalty=classification.penalty if classification else None,
            demographic_cells=_demographic_cells(characteristics),
        )
        source_case_note = _source_case_note(features)
        source_context = _source_context(features, classification)
        summary_panel = _build_summary_panel(
            features,
            classification,
            run_settings,
            shared_sets,
            reconciliation,
            discrepancies,
            source_context_decided=(
                source_context is not None
                and source_context.decided_relationship
            ),
        )
        anchor_note = _anchor_note(features)
        context_note = _context_note(report_1, report_2, features)
        explicit_link_note = _explicit_link_note(features, run_settings)
        explicit_link_found = _explicit_link_found(features, run_settings)

        if classification is not None:
            assessment_rows = _build_assessment_rows(
                features,
                classification,
                run_settings,
                score=shown_score(features, reconciliation),
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
            features,
            classification,
            presentation,
            run_settings,
            gaps,
            score=shown_score(features, reconciliation),
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
        # Not ``or pair_id``: an unframed single-pair review has nothing to
        # cite, and a pair id in the heading would be an internal identifier
        # dressed up as a label.
        example_label=presentation.example_label or presentation.example_id,
        kicker=presentation.kicker,
        title=presentation.title,
        lede=presentation.lede,
        outcomes=_build_outcome_tiles(reference, final_label),
        decisions=_build_decision_tiles(
            features,
            classification,
            source_context,
            run_settings,
            score=shown_score(features, reconciliation),
        )
        if features is not None
        else [],
        reference_verdict=_build_reference_verdict(reference, final_label),
        summary_panel=(
            None
            if summary_panel is None or summary_panel.is_empty
            else summary_panel
        ),
        narrative_blocks=narrative_blocks,
        characteristics=characteristics,
        context_note=context_note,
        drugs=drugs,
        adverse_events=aes,
        findings_weighed=_findings_were_weighed(classification),
        shared_sets=shared_sets,
        anchor_note=anchor_note,
        freq_rows=freq_rows,
        matching_features=matching_features,
        reconciliation=reconciliation,
        explicit_link_note=explicit_link_note,
        explicit_link_found=explicit_link_found,
        source_case_note=source_case_note,
        source_context=source_context,
        narrative_note=_narrative_note(report_1, report_2),
        assessment_rows=assessment_rows,
        discrepancies=discrepancies,
        gaps=_dedupe_gaps(gaps),
    )


def _findings_were_weighed(classification: ClassificationResult | None) -> bool:
    """Whether the inconsistency penalty was compared against a tolerance.

    ``InconsistencyPenalty.threshold`` is the bar the penalty was tested
    against, and is ``None`` in exactly the cases where no test happened:
    independence was not rejected, so the relationship question was never
    reached, or the relationship was pinned on source context regardless of
    the penalty. The findings still carry the weights the run assigns their
    severities; what did not happen is the weighing.
    """
    if classification is None or classification.penalty is None:
        return False
    return classification.penalty.threshold is not None


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
    "AE_ONSET_LABEL",
    "BUILTIN_REFERENCE_TERMS",
    "REFERENCE_VOCABULARY",
    "ExamplePresentation",
    "ReferenceInfo",
    "SCLEAssessment",
    "SCLEState",
    "build_evidence_case",
    "context_phrase",
    "normalise_reference_term",
]
