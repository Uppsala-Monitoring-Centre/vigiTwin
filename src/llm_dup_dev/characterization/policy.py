"""How a multi-valued report field is reduced to one value for tabulation.

A ``Report`` deliberately preserves *every* candidate value per demographic
(``docs/data_contracts.md`` § Explicit representation of uncertainty): a report
may carry age 54 from the structured database and 55 from narrative extraction,
and the pipeline's matching logic compares the two reports' whole candidate sets
(``v1 ∩ v2 ≠ ∅``). Descriptive tabulation cannot do that — a Table 1 needs one
row per report — so the reduction happens here, in one place, and the rule is
recorded in the artifact it produces.

The rule
--------
1. **Empty list is missing.** Uncertainty sentinels (``"unknown"``, ``"not
   known"``, …) were already dropped by the ``Report`` validators; this module
   inherits that contract rather than re-implementing it, so there is exactly
   one definition of "value that means absence of information".
2. **Structured wins.** If any candidate is tagged ``"structured"``, only the
   structured candidates are considered; otherwise all candidates are. The
   resulting Table 1 therefore describes the source database, falling back to
   narrative extraction only where the database said nothing.
3. **Canonicalise, then compare.** Each surviving candidate is mapped to its
   *tabulation key* and the distinct keys are counted. One key resolves; more
   than one is :data:`CONFLICTING`; none parseable is :data:`UNRECOGNISED`.

Step 3 is what makes ``conflicting`` mean something. The comparison happens at
the granularity the value will be *tabulated* at, not on the raw value, so
``54.0 years`` and ``648 months`` both key to completed year ``54`` and agree,
as do a day-resolution ``2020-06-12`` and a year-resolution ``2020``. Only a
disagreement that would have changed a table cell is called a conflict.

Nothing here silently drops an observation: every disposition is counted, and
the counts partition the denominator in the artifact.

See: docs/reference_set_characteristics.md
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from llm_dup_dev.models.enums import DrugRole
from llm_dup_dev.models.report import (
    AdverseEvent,
    AgeValue,
    DateValue,
    Drug,
    Report,
    SourcedValue,
)

# --- Dispositions ----------------------------------------------------------

#: Exactly one tabulation key survived — the report contributes a category.
RESOLVED = "resolved"
#: The field was empty. Per the data contract this is the *only* way a report
#: says "no information"; there are no sentinel values to test for here.
MISSING = "missing"
#: Candidates survived but disagreed at tabulation granularity.
CONFLICTING = "conflicting"
#: Candidates were present but none could be interpreted (e.g. an age in an
#: unrecognised unit). Distinct from ``missing``: something *was* recorded.
UNRECOGNISED = "unrecognised"

#: The provenance tag that wins when both kinds of candidate exist.
STRUCTURED_SOURCE = "structured"

# --- Tabulation parameters -------------------------------------------------

#: Age units this module can convert to years, and their factors.
AGE_UNIT_TO_YEARS: dict[str, float] = {
    "years": 1.0,
    "months": 1.0 / 12.0,
    "days": 1.0 / 365.25,
}

#: Ages outside this range are recorded as out-of-range rather than used. They
#: are excluded from the median, the IQR *and* the bands — a 200-year-old must
#: not quietly shift a median, and must not appear as a "100+" patient either.
AGE_PLAUSIBLE_RANGE: tuple[float, float] = (0.0, 130.0)

#: Width of the reported age bands, in completed years.
AGE_BAND_WIDTH_YEARS = 10

#: Drug roles counted in the top-drugs table. Concomitant medication and drugs
#: with no recorded role are excluded, so the table shows clinically central
#: exposures. ``DataAvailability`` carries the coverage counts that make an
#: empty table readable when the loader never populated ``role``.
COUNTED_DRUG_ROLES: tuple[DrugRole, ...] = (DrugRole.SUSPECTED, DrugRole.INTERACTING)

#: Ordering rule for every distribution and top-N list.
TIE_BREAK = "descending count, then tabulation key ascending"

#: Free-text categorical fields are keyed case-insensitively after stripping;
#: no code system is assumed or imposed. See the module note in
#: docs/reference_set_characteristics.md on why country is left verbatim.
COUNTRY_KEY_NORMALISATION = "strip and lowercase; values otherwise verbatim"

CONFLICT_HANDLING = (
    "candidates are compared at tabulation granularity; two that would land in "
    "the same table cell agree, and only a genuine disagreement is counted as "
    "conflicting rather than resolved"
)


@dataclass(frozen=True)
class Resolution:
    """The outcome of reducing one report's candidates for one field.

    ``key`` and ``label`` are populated only when ``status`` is
    :data:`RESOLVED`; ``label`` is the original spelling behind the key, which
    the caller uses to give free-text categories a readable display form.
    """

    status: str
    key: str | None = None
    label: str | None = None

    @property
    def resolved(self) -> bool:
        return self.status == RESOLVED


_UNRESOLVED_MISSING = Resolution(MISSING)
_UNRESOLVED_CONFLICTING = Resolution(CONFLICTING)
_UNRESOLVED_UNRECOGNISED = Resolution(UNRECOGNISED)


# ---------------------------------------------------------------------------
# The shared reduction
# ---------------------------------------------------------------------------


def _prefer_structured(values: Sequence[object]) -> list[object]:
    """Keep only structured candidates when any exist, else keep everything."""
    structured = [
        v for v in values if STRUCTURED_SOURCE in (getattr(v, "sources", None) or ())
    ]
    return structured if structured else list(values)


def _reduce(
    values: Sequence[Any],
    key_of: Callable[[Any], tuple[str, str] | None],
) -> Resolution:
    """Apply the three-step rule to one report's candidates for one field.

    ``key_of`` maps a candidate to ``(key, label)``, or to ``None`` when the
    candidate cannot be interpreted at all.
    """
    if not values:
        return _UNRESOLVED_MISSING

    keyed: list[tuple[str, str]] = []
    for candidate in _prefer_structured(values):
        keyed_value = key_of(candidate)
        if keyed_value is not None:
            keyed.append(keyed_value)

    if not keyed:
        return _UNRESOLVED_UNRECOGNISED

    distinct = {key for key, _ in keyed}
    if len(distinct) > 1:
        return _UNRESOLVED_CONFLICTING

    key = next(iter(distinct))
    return Resolution(RESOLVED, key=key, label=_dominant_label(keyed))


def _dominant_label(keyed: Iterable[tuple[str, str]]) -> str:
    """Pick the display label for a resolved key: most frequent, ties by text.

    A field keyed case-insensitively can reach here as ``"US"`` and ``"us"``.
    Picking deterministically keeps two runs over the same data byte-identical.
    """
    counts = Counter(label for _, label in keyed)
    return min(counts, key=lambda label: (-counts[label], label))


# ---------------------------------------------------------------------------
# Per-field resolvers
# ---------------------------------------------------------------------------


def _country_key(value: SourcedValue) -> tuple[str, str] | None:
    text = value.value.strip()
    if not text:
        return None
    return text.lower(), text


def resolve_country(report: Report) -> Resolution:
    """Country of origin, keyed case-insensitively and otherwise verbatim.

    No code system is assumed: the data contract declares none, so mapping
    ``"US"`` onto ``"United States"`` here would be this package inventing a
    standard. The consequence is visible rather than hidden — the country
    distribution is emitted in full, so a mixed-vocabulary extract shows up as
    two categories that a reader can recognise as one country.
    """
    return _reduce(report.country_of_origin_values, _country_key)


def _sex_key(value: SourcedValue) -> tuple[str, str] | None:
    text = value.value.strip().lower()
    if not text:
        return None
    return text, text


def resolve_sex(report: Report) -> Resolution:
    """Patient sex. Already constrained to ``male``/``female`` by the model."""
    return _reduce(report.patient_sex_values, _sex_key)


def _year_key(value: DateValue) -> tuple[str, str] | None:
    try:
        year = int(value.value[:4])
    except (ValueError, IndexError):
        return None
    return str(year), str(year)


def resolve_report_year(report: Report) -> Resolution:
    """Reporting year, from the first four characters of the stored date.

    ``resolution`` is never a gate. Every stored value is ISO-8601, so a
    year-resolution date yields a year exactly as a day-resolution one does, and
    treating the coarse value as missing would discard information the report
    genuinely carries. This mirrors
    ``feature_generation.reporting_context._extract_years``.
    """
    return _reduce(report.report_date_values, _year_key)


def age_in_completed_years(value: AgeValue) -> float | None:
    """Convert one age candidate to completed (floored) years, or ``None``.

    ``AgeValue.unit`` is a bare ``str``, not a constrained ``Literal``, so an
    unexpected unit is a real possibility and must never be assumed to be years
    — reading ``"months"`` as years would age a patient twelvefold.

    Completed years match how age is reported and how the pipeline compares it
    (``matching._quantize_floor``): someone is 74 until their 75th birthday, so
    74.6 years floors to 74.
    """
    factor = AGE_UNIT_TO_YEARS.get(value.unit.strip().lower())
    if factor is None:
        return None
    years = value.value * factor
    # Absorb binary-float noise just below a grid point, so a stored 75.0 that
    # evaluates to 74.9999999 still floors to 75.
    return float(math.floor(years + 1e-9))


def _age_key(value: AgeValue) -> tuple[str, str] | None:
    years = age_in_completed_years(value)
    if years is None:
        return None
    return str(int(years)), str(int(years))


def resolve_age_years(report: Report) -> Resolution:
    """Patient age as completed years.

    The tabulation key *is* the number used for the median, so there is no
    second question of which raw float was taken: two candidates that agree on
    completed years agree, full stop.
    """
    return _reduce(report.patient_age_values, _age_key)


def age_band(years: float) -> str:
    """Label the 10-year band a completed age falls in (``"40-49"``, ``"100+"``).

    Bands above the top break collapse into one open band; splitting them would
    produce a long tail of near-empty rows on any real dataset.
    """
    top = 100
    if years >= top:
        return f"{top}+"
    low = int(years // AGE_BAND_WIDTH_YEARS) * AGE_BAND_WIDTH_YEARS
    return f"{low}-{low + AGE_BAND_WIDTH_YEARS - 1}"


def age_is_plausible(years: float) -> bool:
    """Whether a completed age falls inside :data:`AGE_PLAUSIBLE_RANGE`."""
    low, high = AGE_PLAUSIBLE_RANGE
    return low <= years <= high


# ---------------------------------------------------------------------------
# Clinical feature keys
# ---------------------------------------------------------------------------


def drug_keys(report: Report) -> set[str]:
    """Countable drug substances in one report, deduplicated.

    Only :data:`COUNTED_DRUG_ROLES` are counted. Deduplication is per report so
    a substance recorded twice (a legitimate occurrence — the same drug under
    two roles, or split across structured and narrative entries) contributes
    once to a "reports mentioning X" count.
    """
    return {
        drug.substance.strip().lower()
        for drug in report.drugs
        if drug.role in COUNTED_DRUG_ROLES and drug.substance.strip()
    }


def drug_labels(report: Report) -> dict[str, str]:
    """Display spellings for :func:`drug_keys`, keyed the same way."""
    return {
        drug.substance.strip().lower(): drug.substance.strip()
        for drug in report.drugs
        if drug.role in COUNTED_DRUG_ROLES and drug.substance.strip()
    }


def has_any_drug_role(report: Report) -> bool:
    """Whether any drug in the report records a role at all."""
    return any(drug.role is not None for drug in report.drugs)


def _text_key(text: str | None) -> tuple[str, str] | None:
    if text is None:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    return stripped.lower(), stripped


def pt_keys(report: Report) -> dict[str, str]:
    """Adverse-event Preferred Terms in one report, keyed and deduplicated."""
    return _keyed(report.adverse_events, lambda ae: ae.pt_name)


def hlt_keys(report: Report) -> dict[str, str]:
    """Primary MedDRA HLTs in one report, keyed and deduplicated.

    A PT whose ``primary_hlt_name`` is ``None`` contributes nothing — older
    frozen data and synthetic fixtures leave the hierarchy unpopulated. The
    caller distinguishes "no report carried an HLT" from "no adverse events at
    all" via :class:`~llm_dup_dev.characterization.models.DataAvailability`.
    """
    return _keyed(report.adverse_events, lambda ae: ae.primary_hlt_name)


def _keyed(
    events: Sequence[AdverseEvent],
    text_of: Callable[[AdverseEvent], str | None],
) -> dict[str, str]:
    keyed: dict[str, str] = {}
    for event in events:
        result = _text_key(text_of(event))
        if result is not None:
            key, label = result
            keyed.setdefault(key, label)
    return keyed


def counted_drugs(report: Report) -> list[Drug]:
    """The drug entries the top-drugs table counts, for coverage reporting."""
    return [drug for drug in report.drugs if drug.role in COUNTED_DRUG_ROLES]


__all__ = [
    "RESOLVED",
    "MISSING",
    "CONFLICTING",
    "UNRECOGNISED",
    "STRUCTURED_SOURCE",
    "AGE_UNIT_TO_YEARS",
    "AGE_PLAUSIBLE_RANGE",
    "AGE_BAND_WIDTH_YEARS",
    "COUNTED_DRUG_ROLES",
    "TIE_BREAK",
    "COUNTRY_KEY_NORMALISATION",
    "CONFLICT_HANDLING",
    "Resolution",
    "resolve_country",
    "resolve_sex",
    "resolve_report_year",
    "resolve_age_years",
    "age_in_completed_years",
    "age_band",
    "age_is_plausible",
    "drug_keys",
    "drug_labels",
    "has_any_drug_role",
    "pt_keys",
    "hlt_keys",
    "counted_drugs",
]
