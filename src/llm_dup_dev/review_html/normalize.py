"""Display-formatting functions for report field values.

Converts raw multi-valued Report attributes into human-readable strings
suitable for the comparison view models. All functions are pure and
stateless.

Convention: missing / empty values are rendered as ``"--"``.

Provenance display
------------------
The four ``format_*_values`` helpers emit HTML-safe display strings.
Each segment is HTML-escaped; segments whose underlying contributors
all carry ``sources=["narrative_llm"]`` (i.e. came from the narrative
LLM extraction merged in by
:func:`llm_dup_dev.feature_generation.narrative_demographics.merge_narrative_demographics`)
are wrapped in ``<em>...</em>``. The Jinja template renders the result
with ``| safe`` so the italics survive auto-escaping. A segment with
any non-narrative contributor is treated as structured (no italics) —
this keeps the visual cue specific to "narrative-only" provenance.
"""

from __future__ import annotations

from markupsafe import Markup, escape

from llm_dup_dev.models.report import (
    AgeValue,
    DateValue,
    MeasurementValue,
    PartialDate,
    SourcedValue,
)

MISSING = "--"

_NARRATIVE_SOURCE = "narrative_llm"


def _is_narrative_only(sources: list[str]) -> bool:
    """True iff every entry in ``sources`` is the narrative-LLM tag.

    ``sources`` is guaranteed non-empty by the Pydantic ``min_length=1``
    constraint on the underlying value models.
    """
    return all(s == _NARRATIVE_SOURCE for s in sources)


def _join_segments(
    segments: list[tuple[str, bool]],
) -> str:
    """Render ``[(display, narrative_only), ...]`` as a single HTML string.

    Each segment is HTML-escaped. Narrative-only segments are wrapped
    in ``<em>...</em>``. Returns the bare ``MISSING`` sentinel when the
    list is empty.
    """
    if not segments:
        return MISSING
    parts: list[str] = []
    for display, narrative_only in segments:
        escaped = escape(display)
        if narrative_only:
            parts.append(f"<em>{escaped}</em>")
        else:
            parts.append(str(escaped))
    return Markup("; ".join(parts))


def _dedupe_with_provenance(
    pairs: list[tuple[str, bool]],
) -> list[tuple[str, bool]]:
    """First-wins dedupe by display string.

    Aggregates provenance across contributors with the same display:
    a segment is narrative-only iff *every* contributor of that
    display string is narrative-only.
    """
    seen: dict[str, bool] = {}
    order: list[str] = []
    for display, narrative_only in pairs:
        if display not in seen:
            seen[display] = narrative_only
            order.append(display)
        else:
            seen[display] = seen[display] and narrative_only
    return [(d, seen[d]) for d in order]


# ---------------------------------------------------------------------------
# Sourced categorical values (sex, country)
# ---------------------------------------------------------------------------


def format_sourced_values(values: list[SourcedValue]) -> str:
    """Format a list of sourced categorical values for display.

    Deduplicates across sources. Narrative-only entries are wrapped
    in ``<em>`` tags; the result is HTML-safe.
    """
    if not values:
        return MISSING
    pairs = [(v.value, _is_narrative_only(v.sources)) for v in values]
    return _join_segments(_dedupe_with_provenance(pairs))


# ---------------------------------------------------------------------------
# Age values
# ---------------------------------------------------------------------------


def format_age_values(values: list[AgeValue]) -> str:
    """Format patient age values with unit; HTML-safe with provenance markup.

    Deduplicates identical (value, unit) pairs.
    """
    if not values:
        return MISSING
    pairs: list[tuple[str, bool]] = []
    for v in values:
        num = int(v.value) if v.value == int(v.value) else v.value
        pairs.append((f"{num} {v.unit}", _is_narrative_only(v.sources)))
    return _join_segments(_dedupe_with_provenance(pairs))


# ---------------------------------------------------------------------------
# Measurement values (height, weight)
# ---------------------------------------------------------------------------


def format_measurement_values(values: list[MeasurementValue]) -> str:
    """Format physical measurement values; HTML-safe with provenance markup.

    Deduplicates identical (value, unit) pairs.
    """
    if not values:
        return MISSING
    pairs: list[tuple[str, bool]] = []
    for v in values:
        num = int(v.value) if v.value == int(v.value) else v.value
        pairs.append((f"{num} {v.unit}", _is_narrative_only(v.sources)))
    return _join_segments(_dedupe_with_provenance(pairs))


# ---------------------------------------------------------------------------
# Date values (report-level)
# ---------------------------------------------------------------------------


def format_date_values(values: list[DateValue]) -> str:
    """Format report date values; HTML-safe with provenance markup.

    Deduplicates identical date strings.
    """
    if not values:
        return MISSING
    pairs = [(v.value, _is_narrative_only(v.sources)) for v in values]
    return _join_segments(_dedupe_with_provenance(pairs))


# ---------------------------------------------------------------------------
# Partial dates (drug / AE level)
# ---------------------------------------------------------------------------


def format_partial_date(pd: PartialDate | None) -> str | None:
    """Format a partial date for inline display within drug/AE cells.

    Returns ``None`` (not ``"--"``) when absent so that the template
    can distinguish *field not applicable* from *field present but empty*.
    Omits resolution label.
    """
    if pd is None:
        return None
    return pd.value


# ---------------------------------------------------------------------------
# Drug-name normalisation key
# ---------------------------------------------------------------------------


def normalize_drug_key(substance: str) -> str:
    """Case-fold a drug substance name for use as a union key."""
    return substance.strip().lower()


# ---------------------------------------------------------------------------
# AE PT normalisation key
# ---------------------------------------------------------------------------


def normalize_ae_key(pt_name: str) -> str:
    """Case-fold an AE preferred term for use as a union key."""
    return pt_name.strip().lower()
