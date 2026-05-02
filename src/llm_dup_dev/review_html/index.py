"""Batch-index row formatting: PairViewModel -> BatchIndexItem.

Builds the compact per-pair entry shown on the review batch index
page. Consumes the already-built :class:`PairViewModel` (produced by
``compare.build_pair_view_model``) and emits HTML-safe summaries:
inline inconsistency items, anchor / context labels, the penalty
breakdown, and the matching-feature chip strip.

Shared scoring/display utilities (``_format_penalty_summary``,
``_contrib_pos_class``, ``_contrib_neg_class``,
``_FEATURE_TO_DEMO_LABEL``) live in :mod:`compare` because they are
also used by pair-building; this module imports them.
"""

from __future__ import annotations

import math
import re

from markupsafe import escape as _esc

from .compare import (
    _FEATURE_TO_DEMO_LABEL,
    _contrib_neg_class,
    _contrib_pos_class,
    _format_penalty_summary,
)
from .models import (
    BatchIndexItem,
    ClinicalDateFindingVM,
    IndexInconsistencyItem,
    InconsistencyVM,
    PairViewModel,
)


def build_batch_index_item(
    vm: PairViewModel,
    filename: str,
) -> BatchIndexItem:
    """Build an index entry from a rendered pair view model."""
    # Build per-item inconsistency list for colour-coded inline display.
    key_drugs = _key_drug_names(vm)
    inc_items = _build_index_inconsistency_items(vm, key_drugs)

    anchor_label, anchor_n = _format_anchor_label(vm, key_drugs)

    penalty_text = _format_penalty_text(vm)

    return BatchIndexItem(
        pair_id=vm.pair_id,
        report_1_id=vm.report_1_id,
        report_2_id=vm.report_2_id,
        filename=filename,
        final_label=(
            vm.classification_summary.final_label
            if vm.classification_summary
            else None
        ),
        l_match=(
            vm.features_summary.l_match
            if vm.features_summary
            else None
        ),
        duplicate_relationship=(
            vm.classification_summary.duplicate_relationship
            if vm.classification_summary
            else None
        ),
        inconsistency_items=inc_items,
        narrative_consistency=(
            vm.features_summary.narrative_consistency
            if vm.features_summary
            else None
        ),
        anchor_label=anchor_label,
        anchor_n=anchor_n,
        context_label=_format_context_label(vm),
        matching_features=_format_matching_features(vm, key_drugs),
        annotation_label=(
            vm.annotation.normalised_label if vm.annotation else None
        ),
        annotation_agrees=(
            vm.annotation.agrees_with_pipeline if vm.annotation else None
        ),
        penalty_text=penalty_text,
        anchor_penalty=(
            vm.features_summary.anchor_penalty
            if vm.features_summary
            else None
        ),
        anchor_penalty_class=(
            vm.features_summary.anchor_penalty_class
            if vm.features_summary
            else None
        ),
    )


# ===================================================================
# Index-row formatting helpers
# ===================================================================

_KEY_DISPLAY_ROLES = {"suspected", "interacting"}

# Regex for parsing drug_set inconsistency descriptions.
_DRUG_SET_RE_1 = re.compile(
    r"Key drug\(s\) in report 1 not found in report 2:\s*\[(.+)\]"
)
_DRUG_SET_RE_2 = re.compile(
    r"Key drug\(s\) in report 2 not found in report 1:\s*\[(.+)\]"
)


def _format_penalty_text(vm: PairViewModel) -> str | None:
    """Build the batch-index Penalty cell content for a pair."""
    if not vm.classification_summary:
        return None
    counts = (
        vm.features_summary.inconsistency_counts
        if vm.features_summary
        else None
    )
    return _format_penalty_summary(
        counts, vm.classification_summary.explanation
    )


def _parse_drug_list(raw: str) -> list[str]:
    """Parse a Python-style string list like ``"'placebo', 'blinded therapy'"``."""
    return [s.strip().strip("'\"") for s in raw.split(",")]


def _build_index_inconsistency_items(
    vm: PairViewModel,
    key_drugs: set[str],
) -> list[IndexInconsistencyItem]:
    """Build succinct, colour-coded inconsistency items for the index row.

    Produces compact text like ``"male vs female"``,
    ``"75 years vs 68 years"``, ``"Sertraline: 2022-01 vs 2020"``,
    or ``"Placebo listed only on report 1"``.
    """
    if vm.features_summary is None:
        return []

    # Lookup from demographic label → (left_display, right_display).
    demo_values: dict[str, tuple[str, str]] = {
        fc.label: (fc.left_display, fc.right_display)
        for fc in vm.demographics
    }

    items: list[IndexInconsistencyItem] = []

    for inc in vm.features_summary.inconsistencies:
        # Skip suppressed AE entries — they exist for transparency in the
        # detailed view but should not inflate the index row.
        if inc.suppressed:
            continue
        if inc.source == "llm_ae_clinical_picture":
            text = _format_ae_clinical_picture_item(inc)
        else:
            text = _format_demographic_inconsistency(
                inc, demo_values, key_drugs,
            )
        items.append(IndexInconsistencyItem(text=text, severity=inc.severity))

    for cdf in vm.features_summary.clinical_date_findings:
        text = _format_clinical_date_item(cdf, key_drugs)
        items.append(IndexInconsistencyItem(
            text=text, severity=cdf.severity,
        ))

    return items


def _format_ae_clinical_picture_item(inc: InconsistencyVM) -> str:
    """Format an AE clinical-picture finding for the index row.

    Produces a compact label like ``"AE picture: cutaneous syndrome
    (rash, pruritus)"`` or ``"AE picture: global mismatch
    (rash, fracture)"``. Falls back to the kind label when no terms
    are available.
    """
    label = inc.cluster_label or inc.kind.replace("_", " ") or "AE picture"
    terms = ", ".join(inc.terms[:5])
    if len(inc.terms) > 5:
        terms += ", ..."
    body = f"{_esc(label)}"
    if terms:
        body += f" ({_esc(terms)})"
    return f"AE picture: {body}"


def _format_demographic_inconsistency(
    inc: InconsistencyVM,
    demo_values: dict[str, tuple[str, str]],
    key_drugs: set[str],
) -> str:
    """Format a single demographic/drug-set inconsistency for the index.

    Returns an HTML-safe string.
    """
    # --- patient_sex / sex: "male vs female" ---
    if inc.feature in ("patient_sex", "sex"):
        vals = demo_values.get("Sex")
        if vals:
            return f"{_esc(vals[0])} vs {_esc(vals[1])}"
        # Fallback: parse from description "Sex mismatch: ['male'] vs ['female']"
        return str(_esc(inc.description))

    # --- patient_age / patient_weight / patient_height: "75 years vs 68 years" ---
    demo_label = _FEATURE_TO_DEMO_LABEL.get(inc.feature)
    if demo_label:
        vals = demo_values.get(demo_label)
        if vals:
            return f"{_esc(vals[0])} vs {_esc(vals[1])}"

    # --- drug_set: "Placebo listed only on report 1" ---
    if inc.feature == "drug_set":
        return _format_drug_set_item(inc.description, key_drugs)

    # Fallback for any unknown feature
    return str(_esc(inc.description or inc.feature))


def _format_drug_set_item(description: str, key_drugs: set[str]) -> str:
    """Format a drug-set inconsistency as succinct HTML.

    Converts ``"Key drug(s) in report 1 not found in report 2: ['placebo']"``
    to ``"<strong>Placebo</strong> listed only on report 1"``.
    """
    m1 = _DRUG_SET_RE_1.match(description)
    if m1:
        drugs = _parse_drug_list(m1.group(1))
        names = ", ".join(_bold_drug(d.title(), key_drugs) for d in drugs)
        return f"{names} listed only on report 1"

    m2 = _DRUG_SET_RE_2.match(description)
    if m2:
        drugs = _parse_drug_list(m2.group(1))
        names = ", ".join(_bold_drug(d.title(), key_drugs) for d in drugs)
        return f"{names} listed only on report 2"

    # Fallback
    return _bold_drugs_in_text(description, key_drugs)


# Prefixes in clinical-date entity_labels that reference a drug or AE.
_ENTITY_LABEL_PREFIXES = ("drug_start:", "drug_end:", "ae_onset:")


def _format_clinical_date_item(
    cdf: ClinicalDateFindingVM,
    key_drugs: set[str],
) -> str:
    """Format a clinical-date inconsistency for the index.

    Converts ``entity_label="drug_start:sertraline"`` with dates
    to ``"<strong>Sertraline</strong>: 2022-01-28 vs 2020"``.
    """
    dates = f"{_esc(cdf.source_date)} vs {_esc(cdf.matched_date)}"

    for prefix in _ENTITY_LABEL_PREFIXES:
        if cdf.entity_label.startswith(prefix):
            name = cdf.entity_label[len(prefix):]
            if prefix.startswith("drug_"):
                label = _bold_drug(name, key_drugs)
            else:
                label = str(_esc(name))
            return f"{label}: {dates}"

    # Fallback for other entity labels (e.g. "overall_ae_onset")
    return f"{_esc(cdf.entity_label)}: {dates}"


def _key_drug_names(vm: PairViewModel) -> set[str]:
    """Return casefolded substance names for drugs with key roles.

    A drug is "key" if any DrugDetail (left or right) has role
    ``"suspected"`` or ``"interacting"``.  Drugs with ``role=None``
    or ``"concomitant"`` are excluded, matching the convention in
    ``feature_generation.inconsistencies._KEY_ROLES``.
    """
    key: set[str] = set()
    for row in vm.drugs:
        for detail in (*row.left_details, *row.right_details):
            if detail.role and detail.role.lower() in _KEY_DISPLAY_ROLES:
                key.add(row.substance.casefold())
                break
    return key


def _bold_drug(name: str, key_drugs: set[str]) -> str:
    """Return HTML for a drug name, bolded if it's a key (suspected/interacting) drug."""
    escaped = _esc(name)
    if name.casefold() in key_drugs:
        return f"<strong>{escaped}</strong>"
    return str(escaped)


def _bold_drugs_in_text(text: str, key_drugs: set[str]) -> str:
    """Bold any key drug names found in a free-text inconsistency description.

    Scans for each key drug name (case-insensitive) and wraps matches
    in ``<strong>`` tags.  The rest of the text is HTML-escaped.

    For descriptions like ``"Key drug(s) in report 1 not found in report 2:
    ['aspirin', 'ibuprofen']"``, this will bold the drug names inside.
    """
    if not key_drugs:
        return str(_esc(text))

    # Build a regex that matches any key drug name (case-insensitive, word
    # boundaries).  We sort by length descending so longer names match first.
    sorted_drugs = sorted(key_drugs, key=len, reverse=True)
    pattern = re.compile(
        "|".join(re.escape(d) for d in sorted_drugs),
        re.IGNORECASE,
    )

    parts: list[str] = []
    last_end = 0
    for m in pattern.finditer(text):
        # Escape the text before this match
        parts.append(str(_esc(text[last_end:m.start()])))
        parts.append(f"<strong>{_esc(m.group())}</strong>")
        last_end = m.end()
    parts.append(str(_esc(text[last_end:])))
    return "".join(parts)


def _format_anchor_label(
    vm: PairViewModel,
    key_drugs: set[str],
) -> tuple[str | None, int | None]:
    """Format the anchor descriptor and population count for the index row.

    Returns ``(descriptor_html, anchor_n)`` where *descriptor_html* contains
    drug/AE names (key drugs bolded) and *anchor_n* is the raw population
    count for its own column.

    Examples (descriptor only):
        DRUGS_AES anchor: ``"<strong>ranitidine</strong> + rash"``
        DRUGS anchor:     ``"<strong>ranitidine</strong>, aspirin"``
        AES anchor:       ``"rash, fever"``
    """
    fs = vm.features_summary
    if fs is None:
        return None, None

    drugs = ", ".join(_bold_drug(d, key_drugs) for d in fs.matching_drugs)
    aes = ", ".join(str(_esc(a)) for a in fs.matching_aes)

    if fs.anchor_type == "drugs_AEs" and drugs and aes:
        body = f"{drugs} + {aes}"
    elif fs.anchor_type == "drugs" and drugs:
        body = drugs
    elif fs.anchor_type == "AEs" and aes:
        body = aes
    else:
        # Fallback: include whatever is present
        body = " + ".join(p for p in (drugs, aes) if p) or str(_esc(fs.anchor_type))

    return body, fs.anchor_n


def _format_context_label(vm: PairViewModel) -> str | None:
    """Format a short reporting-context label.

    Examples: "Global", "SE", "SE 2020".
    """
    fs = vm.features_summary
    if fs is None:
        return None
    # FeaturesSummaryVM.reporting_context is built as e.g.
    # "global", "country (SE)", or "country (SE, 2022)".
    raw = fs.reporting_context
    if raw.startswith("global"):
        return "Global"
    # Extract the country / year from the parenthesised tail.
    if "(" in raw and raw.endswith(")"):
        inner = raw[raw.index("(") + 1 : -1]
        parts = [p.strip() for p in inner.split(",")]
        return " ".join(parts)
    return raw


def _contrib_chip_html(
    inner_html: str, contribution: float, *, negative: bool = False
) -> str:
    """Wrap inner HTML in a colour-coded chip with a ``(+X.X)`` / ``(-X.X)`` annotation.

    ``inner_html`` must already be HTML-safe (escaped or known-safe markup
    such as a ``<strong>`` tag from ``_bold_drug``). ``contribution`` is
    the log-rarity value to display; for negative chips its sign is
    reversed so the formatted text reads ``(-X.X)``.
    """
    if negative:
        cls = _contrib_neg_class(contribution)
        sign = "-"
    else:
        cls = _contrib_pos_class(contribution)
        sign = "+"
    mag = abs(contribution)
    return (
        f'<span class="contrib {cls}">{inner_html}'
        f' ({sign}{mag:.1f})</span>'
    )


# Regex to extract the date value from a clinical_date description like
# "clinical_date 2024-01-15 (day): D_f=365".
_CLINICAL_DATE_RE = re.compile(r"^clinical_date\s+(\S+)\s")


def _format_matching_features(
    vm: PairViewModel,
    key_drugs: set[str],
) -> str | None:
    """Build a compact summary of the values behind each probability feature.

    Shows the actual matching values (e.g. ``"male; sertraline, paroxetine;
    2005-03-02"``) rather than technical descriptions, with each value
    annotated by its log-rarity contribution ``(+X.X)`` and shaded by
    contribution magnitude. Rows are sorted by contribution descending so
    the strongest evidence appears first.

    Drug-set and AE-set carry one shared contribution each. Clinical
    dates are kept on a single row (since they share the "date" semantic
    category) but each date carries its own chip; the row is placed in
    the outer order by its strongest contribution.

    Key drugs are still wrapped in ``<strong>`` tags inside the chip.
    """
    fs = vm.features_summary
    if fs is None or not fs.feature_contributions:
        return None

    # Demographic-label → display value
    demo_display: dict[str, str] = {
        fc.label: fc.left_display for fc in vm.demographics
    }

    rows: list[tuple[float, str]] = []  # (contribution, html)

    # Drug-set / AE-set p_f lookup.
    drug_set_p_f: float | None = None
    ae_set_p_f: float | None = None
    # Clinical dates: list of (date, p_f), preserving FC order, dedup by date.
    clinical: list[tuple[str, float]] = []
    seen_dates: set[str] = set()

    for fc in fs.feature_contributions:
        if fc.feature == "drug_set":
            drug_set_p_f = fc.p_f
            continue
        if fc.feature == "ae_set":
            ae_set_p_f = fc.p_f
            continue
        if fc.feature == "clinical_date":
            m = _CLINICAL_DATE_RE.match(fc.description)
            if m:
                date_val = m.group(1)
                if date_val not in seen_dates:
                    seen_dates.add(date_val)
                    clinical.append((date_val, fc.p_f))
            continue
        # Demographic features.
        demo_label = _FEATURE_TO_DEMO_LABEL.get(fc.feature)
        if demo_label is None:
            continue
        value = demo_display.get(demo_label)
        if not value or fc.p_f <= 0:
            continue
        contribution = -math.log10(fc.p_f)
        rows.append(
            (contribution, _contrib_chip_html(str(_esc(value)), contribution))
        )

    if drug_set_p_f is not None and drug_set_p_f > 0 and fs.matching_drugs:
        contribution = -math.log10(drug_set_p_f)
        joined = ", ".join(_bold_drug(d, key_drugs) for d in fs.matching_drugs)
        rows.append((contribution, _contrib_chip_html(joined, contribution)))

    if ae_set_p_f is not None and ae_set_p_f > 0 and fs.matching_aes:
        contribution = -math.log10(ae_set_p_f)
        joined = ", ".join(str(_esc(a)) for a in fs.matching_aes)
        rows.append((contribution, _contrib_chip_html(joined, contribution)))

    if clinical:
        # Sort dates within the row by descending contribution.
        date_chips: list[tuple[float, str]] = []
        for date_val, p_f in clinical:
            if p_f <= 0:
                continue
            c = -math.log10(p_f)
            date_chips.append(
                (c, _contrib_chip_html(str(_esc(date_val)), c))
            )
        if date_chips:
            date_chips.sort(key=lambda t: t[0], reverse=True)
            row_contribution = date_chips[0][0]
            row_html = " ".join(chip for _, chip in date_chips)
            rows.append((row_contribution, row_html))

    if not rows:
        return None

    rows.sort(key=lambda t: t[0], reverse=True)
    return "<br>".join(html for _, html in rows)
