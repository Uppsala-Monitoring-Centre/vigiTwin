"""Display-formatting functions for report field values.

Converts raw multi-valued Report attributes into human-readable strings
suitable for the comparison view models. All functions are pure and
stateless.

Convention: missing / empty values are rendered as ``"--"``.
"""

from __future__ import annotations

from llm_dup_dev.models.report import (
    AgeValue,
    DateValue,
    MeasurementValue,
    PartialDate,
    SourcedValue,
)

MISSING = "--"


# ---------------------------------------------------------------------------
# Sourced categorical values (sex, country)
# ---------------------------------------------------------------------------


def format_sourced_values(values: list[SourcedValue]) -> str:
    """Format a list of sourced categorical values for display.

    Deduplicates across sources; omits provenance labels.

    Examples
    --------
    >>> format_sourced_values([SourcedValue(value="female", sources=["structured"])])
    'female'
    >>> format_sourced_values([])
    '--'
    """
    if not values:
        return MISSING
    seen: list[str] = []
    for v in values:
        if v.value not in seen:
            seen.append(v.value)
    return "; ".join(seen)


# ---------------------------------------------------------------------------
# Age values
# ---------------------------------------------------------------------------


def format_age_values(values: list[AgeValue]) -> str:
    """Format patient age values with unit; omits provenance labels.

    Deduplicates identical (value, unit) pairs.

    Examples
    --------
    >>> format_age_values([AgeValue(value=54, unit="years", sources=["structured"])])
    '54 years'
    """
    if not values:
        return MISSING
    seen: list[str] = []
    for v in values:
        num = int(v.value) if v.value == int(v.value) else v.value
        entry = f"{num} {v.unit}"
        if entry not in seen:
            seen.append(entry)
    return "; ".join(seen)


# ---------------------------------------------------------------------------
# Measurement values (height, weight)
# ---------------------------------------------------------------------------


def format_measurement_values(values: list[MeasurementValue]) -> str:
    """Format physical measurement values with unit; omits provenance labels.

    Deduplicates identical (value, unit) pairs.

    Examples
    --------
    >>> format_measurement_values([MeasurementValue(value=165.0, unit="cm", sources=["structured"])])
    '165 cm'
    """
    if not values:
        return MISSING
    seen: list[str] = []
    for v in values:
        num = int(v.value) if v.value == int(v.value) else v.value
        entry = f"{num} {v.unit}"
        if entry not in seen:
            seen.append(entry)
    return "; ".join(seen)


# ---------------------------------------------------------------------------
# Date values (report-level)
# ---------------------------------------------------------------------------


def format_date_values(values: list[DateValue]) -> str:
    """Format report date values; omits resolution and provenance labels.

    Deduplicates identical date strings.

    Examples
    --------
    >>> format_date_values([DateValue(value="2020-06-12", resolution="day", sources=["structured"])])
    '2020-06-12'
    """
    if not values:
        return MISSING
    seen: list[str] = []
    for v in values:
        if v.value not in seen:
            seen.append(v.value)
    return "; ".join(seen)


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
