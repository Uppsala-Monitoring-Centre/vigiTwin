"""Age, height and weight offsets, and the imperial-origin special case.

One offset per pair and per unit, so the *difference* between the two reports
survives — which is what a discrepancy finding quotes, and what a reader
checks. The transform is held to four conditions, all of them things the page
or the assessment actually says:

#. **Written precision** — ``70`` stays a whole number and ``47.63`` keeps two
   decimals. The pipeline matches numeric demographics on a grid derived from
   the decimals as written
   (:func:`~llm_dup_dev.feature_generation.matching._decimal_resolution`), so
   a shift that adds a decimal place changes what counts as a match.
#. **Pairwise differences** — preserved exactly, computed from the published
   values rather than before rounding.
#. **Match grid** — for every pair of values, whether they fall on the same
   grid point at the coarser of their two resolutions. Checked over all pairs
   rather than across the two reports, which is the stronger statement and
   needs no knowledge of which report a value came from.
#. **Age band** — nobody crosses into another band. An offset that turns a
   17-year-old into a 19-year-old has changed the clinical reading of the
   case, which is the one thing this layer must never do.

**Nothing here requires a value to move.** A zero offset preserves every
condition and is a legitimate outcome; so is an offset that publishes report
A's age as the age report B reported. Forbidding either would let anyone
holding the policy rule out the displayed value as the true one, which is the
inference these offsets exist to prevent.

**The imperial-origin rule.** A weight recorded as ``47.63 kg`` was almost
certainly ``105 lb`` before somebody converted it; shifting it by a whole or
half kilogram produces a figure that no scale and no clerk would ever have
produced, and the oddity draws the eye. Where every value of a kind in the
pair is consistent with a whole number of pounds (or inches), the shift is
applied in **pounds** and converted back at the original number of decimals.
The decision is made per pair and per kind, never per value: shifting one
value in pounds and its counterpart in kilograms would break the difference
between them.
"""

from __future__ import annotations

import random
import re
from collections.abc import Sequence
from dataclasses import dataclass

# Imported privately, as `review_html.compare` already does: these are the
# pipeline's own numeric matching rules.
from llm_dup_dev.feature_generation.matching import (
    _decimal_resolution,
    _quantize,
    _quantize_floor,
)

from .invariants import InvariantFailure, InvariantReport
from .policy import PublicationPolicy

#: Pounds per kilogram and centimetres per inch, to the precision a
#: pharmacovigilance database's own conversion would have used.
POUNDS_PER_KG = 2.2046226218
CM_PER_INCH = 2.54

#: How far from a whole pound (or inch) a converted value may sit and still
#: count as imperial in origin. 105 lb lands on 47.627 kg, which a database
#: stores as 47.63 — a 0.006 lb discrepancy once converted back.
_IMPERIAL_TOLERANCE = 0.02

#: ``"81 years"``, ``"47.63 kg"``. The unit is whatever follows the figure.
_QUANTITY_RE = re.compile(r"^(-?\d+(?:\.\d+)?)\s+(\S+(?:\s+\S+)*)$")

#: Days and months per year, for putting an age into a band.
_DAYS_PER_YEAR = 365.25
_MONTHS_PER_YEAR = 12.0


@dataclass(frozen=True)
class Quantity:
    """A figure with its unit and the precision it was *written* at.

    ``decimals`` is read off the source text rather than derived from the
    float: ``47.60`` and ``47.6`` are the same number and different
    statements about how precisely it was known.
    """

    value: float
    unit: str
    decimals: int

    @property
    def resolution(self) -> float:
        """The grid step the pipeline would match this value on."""
        return 10.0**-self.decimals


@dataclass(frozen=True)
class NumericOffset:
    """What was applied to one group of values, for the secure key record."""

    unit: str
    amount: float
    #: ``"lb"`` or ``"in"`` when the shift was applied in imperial units and
    #: converted back; empty when it was applied in the unit as stored.
    imperial_unit: str = ""

    def describe(self) -> str:
        if self.imperial_unit:
            return f"{self.amount:+g} {self.imperial_unit} (stored as {self.unit})"
        return f"{self.amount:+g} {self.unit}"


def parse_quantity(token: str) -> Quantity | None:
    """``"47.63 kg"`` as a :class:`Quantity`, or ``None`` if it is not one."""
    match = _QUANTITY_RE.match(token.strip())
    if match is None:
        return None
    figure, unit = match.groups()
    decimals = len(figure.split(".")[1]) if "." in figure else 0
    return Quantity(value=float(figure), unit=unit, decimals=decimals)


def format_quantity(quantity: Quantity) -> str:
    """Write a quantity the way the evidence builder writes one.

    Mirrors ``build._number_text``: a value that is a whole number drops its
    decimal part, so ``54.0`` reads as ``54``.
    """
    rounded = round(quantity.value, quantity.decimals)
    if rounded == int(rounded):
        return f"{int(rounded)} {quantity.unit}"
    return f"{rounded:.{quantity.decimals}f} {quantity.unit}"


def imperial_amount(quantity: Quantity) -> tuple[str, int] | None:
    """``("lb", 105)`` when *quantity* is a converted imperial figure.

    Only metric values carrying a decimal place qualify: a weight written as
    a whole number of kilograms is a kilogram measurement, however neatly it
    happens to divide into pounds.
    """
    if quantity.decimals < 1 or quantity.value <= 0:
        return None
    if quantity.unit == "kg":
        imperial, label = quantity.value * POUNDS_PER_KG, "lb"
    elif quantity.unit == "cm":
        imperial, label = quantity.value / CM_PER_INCH, "in"
    else:
        return None
    whole = round(imperial)
    if whole < 1 or abs(imperial - whole) > _IMPERIAL_TOLERANCE:
        return None
    return label, int(whole)


def _years(quantity: Quantity) -> float | None:
    """An age in years, or ``None`` for a unit that is not an age unit."""
    if quantity.unit.startswith("year"):
        return quantity.value
    if quantity.unit.startswith("month"):
        return quantity.value / _MONTHS_PER_YEAR
    if quantity.unit.startswith("day") or quantity.unit.startswith("week"):
        factor = 7.0 if quantity.unit.startswith("week") else 1.0
        return quantity.value * factor / _DAYS_PER_YEAR
    return None


def _grid_fingerprint(
    values: Sequence[Quantity], *, is_age: bool
) -> tuple[tuple[float, bool], ...]:
    """Whether each pair of values falls on one grid point, and at what step.

    Ages use floor binning and measurements half-up, exactly as
    ``matching._match_age`` and ``matching._match_measurement`` do.
    """
    quantize = _quantize_floor if is_age else _quantize
    out: list[tuple[float, bool]] = []
    for i in range(len(values)):
        for j in range(i + 1, len(values)):
            a, b = values[i].value, values[j].value
            step = max(_decimal_resolution(a), _decimal_resolution(b))
            out.append((step, quantize(a, step) == quantize(b, step)))
    return tuple(out)


def apply_offset(
    quantity: Quantity, offset: NumericOffset
) -> Quantity:
    """*quantity* moved by *offset*, written at its original precision."""
    if offset.imperial_unit:
        imperial = imperial_amount(quantity)
        if imperial is None:  # pragma: no cover - solver guarantees this
            raise ValueError(
                f"{quantity!r} is not an imperial-origin value"
            )
        shifted = imperial[1] + offset.amount
        per_unit = POUNDS_PER_KG if offset.imperial_unit == "lb" else 1 / CM_PER_INCH
        value = shifted / per_unit
    else:
        value = quantity.value + offset.amount
    return Quantity(
        value=round(value, quantity.decimals),
        unit=quantity.unit,
        decimals=quantity.decimals,
    )


def check_numeric_offset(
    originals: Sequence[Quantity],
    shifted: Sequence[Quantity],
    policy: PublicationPolicy,
    *,
    is_age: bool,
) -> InvariantReport:
    """Whether a shift kept everything the page and the assessment say."""
    failures: list[InvariantFailure] = []

    for before, after in zip(originals, shifted, strict=True):
        if after.decimals != before.decimals:  # pragma: no cover - rule guard
            failures.append(
                InvariantFailure(
                    "written_precision",
                    f"{format_quantity(before)} became "
                    f"{format_quantity(after)}, at a different precision",
                )
            )
        if after.value <= 0:
            failures.append(
                InvariantFailure(
                    "positive",
                    f"{format_quantity(before)} became "
                    f"{format_quantity(after)}, which is not a measurement",
                )
            )
        if is_age:
            was, now = _years(before), _years(after)
            if was is not None and now is not None:
                if policy.age_band(was) != policy.age_band(now):
                    failures.append(
                        InvariantFailure(
                            "age_band",
                            f"{format_quantity(before)} became "
                            f"{format_quantity(after)}, moving from the "
                            f"{policy.age_band(was)} band to the "
                            f"{policy.age_band(now)} band",
                        )
                    )

    for i in range(len(originals)):
        for j in range(i + 1, len(originals)):
            places = max(originals[i].decimals, originals[j].decimals)
            was = round(originals[i].value - originals[j].value, places)
            now = round(shifted[i].value - shifted[j].value, places)
            if was != now:
                failures.append(
                    InvariantFailure(
                        "difference",
                        f"{format_quantity(originals[i])} vs "
                        f"{format_quantity(originals[j])}: difference moved "
                        f"from {was:g} to {now:g}",
                    )
                )

    if not policy.allow_unchanged_values and all(
        format_quantity(a) == format_quantity(b)
        for a, b in zip(originals, shifted, strict=True)
    ):
        failures.append(
            InvariantFailure(
                "effectiveness",
                "the offset left every value as reported, which this policy "
                "does not allow",
            )
        )

    if _grid_fingerprint(originals, is_age=is_age) != _grid_fingerprint(
        shifted, is_age=is_age
    ):
        failures.append(
            InvariantFailure(
                "match_grid",
                "two values that matched no longer do, or two that did not "
                "now do",
            )
        )

    return InvariantReport(tuple(failures))


def _offset_pool(
    values: Sequence[Quantity], policy: PublicationPolicy, *, is_age: bool
) -> tuple[list[NumericOffset], str]:
    """Candidate offsets for one group, and why that shape was chosen.

    Whole imperial units where every value is imperial in origin; otherwise
    whole multiples of the *coarsest* grid step present, so a shift can never
    add a decimal place to the bluntest value in the group.
    """
    imperial = [imperial_amount(v) for v in values]
    if values and all(entry is not None for entry in imperial):
        label = imperial[0][0]  # type: ignore[index]
        if all(entry[0] == label for entry in imperial):  # type: ignore[index]
            whole_units = (
                policy.measurement_offset_max_lb
                if label == "lb"
                else policy.measurement_offset_max_in
            )
            zero = (
                [
                    NumericOffset(
                        unit=values[0].unit, amount=0.0, imperial_unit=label
                    )
                ]
                if policy.allow_unchanged_values
                else []
            )
            pool = zero + [
                NumericOffset(
                    unit=values[0].unit,
                    amount=float(step),
                    imperial_unit=label,
                )
                for magnitude in range(1, whole_units + 1)
                for step in (-magnitude, magnitude)
            ]
            return pool, f"whole {label}"

    unit = values[0].unit if values else ""
    step = 10.0 ** -min((v.decimals for v in values), default=0)
    limit: float
    if is_age:
        if unit.startswith("month"):
            span = policy.age_offset_max_months
        elif unit.startswith("day") or unit.startswith("week"):
            span = policy.age_offset_max_days
        else:
            span = policy.age_offset_max_years
        limit = float(span)

    elif unit == "kg":
        limit = policy.measurement_offset_max_kg
    elif unit == "cm":
        limit = policy.measurement_offset_max_cm
    else:
        limit = policy.measurement_offset_max_kg

    count = int(limit / step + 1e-9)
    zero = (
        [NumericOffset(unit=unit, amount=0.0)]
        if policy.allow_unchanged_values
        else []
    )
    pool = zero + [
        NumericOffset(unit=unit, amount=round(sign * index * step, 6))
        for index in range(1, count + 1)
        for sign in (-1, 1)
    ]
    return pool, f"multiples of {step:g} {unit}".strip()


@dataclass(frozen=True)
class NumericSolution:
    """The accepted offset for one group, or the reasons there was none."""

    offset: NumericOffset | None
    values: tuple[Quantity, ...]
    failures: tuple[InvariantFailure, ...] = ()
    pool_description: str = ""
    candidates_tried: int = 0


def solve_numeric_offset(
    values: Sequence[Quantity],
    policy: PublicationPolicy,
    seed: str,
    *,
    is_age: bool,
) -> NumericSolution:
    """The first candidate offset that preserves everything, or nothing.

    Deterministic from *seed*, with the pool built in sorted order before the
    shuffle — the house style from :mod:`llm_dup_dev.study_eval.sampling`.
    Returning no offset is a real outcome, not an error: the caller records a
    finding and the export is blocked.
    """
    if not values:
        return NumericSolution(offset=None, values=())
    pool, description = _offset_pool(values, policy, is_age=is_age)
    rng = random.Random(seed)
    rng.shuffle(pool)
    last: tuple[InvariantFailure, ...] = ()
    for tried, candidate in enumerate(pool, start=1):
        shifted = [apply_offset(v, candidate) for v in values]
        report = check_numeric_offset(values, shifted, policy, is_age=is_age)
        if report.accepted:
            return NumericSolution(
                offset=candidate,
                values=tuple(shifted),
                pool_description=description,
                candidates_tried=tried,
            )
        last = report.failures
    return NumericSolution(
        offset=None,
        values=tuple(values),
        failures=last,
        pool_description=description,
        candidates_tried=len(pool),
    )


__all__ = [
    "CM_PER_INCH",
    "POUNDS_PER_KG",
    "NumericOffset",
    "NumericSolution",
    "Quantity",
    "apply_offset",
    "check_numeric_offset",
    "format_quantity",
    "imperial_amount",
    "parse_quantity",
    "solve_numeric_offset",
]
