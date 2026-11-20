"""Recovering structured evidence from a saved run's own descriptions.

Two fields a reader-facing display needs were added after runs had already been
saved: :attr:`~vigitwin.models.pair_features.FeatureContribution.matched_value`
(*which* date matched a clinical-date contribution) and
:attr:`~vigitwin.models.pair_features.Inconsistency.terms` / ``.kind``
(*which* substances a drug-set finding named, and in which direction). A run
saved before those fields existed carries neither, and the evidence review
could then neither highlight the matched dates nor name the offending drug.

Both values are nevertheless *present* in those records — inside the
``description`` string each producer wrote for its audit trail. This module
recovers them from there.

Parsing a description is not how evidence should be read, and nothing here runs
for a run that recorded the structured fields: each function is a fallback,
tried only when the structured field is absent, and returns ``None`` rather
than a guess whenever the string is not exactly the shape its producer writes.
The alternative — recomputing the finding from the two reports — was rejected:
it would be a comparison performed by the renderer, which is the one thing this
format's colour rule forbids, and it can disagree with what the run actually
flagged (the drug-set check's combination-product rule, or any change to the
check since the run was saved). Recovering the producer's own words cannot
disagree with the producer.

The caller records every recovery as a gap, so a reader is told the attribution
came from a description rather than from a structured field.
"""

from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING

from .models import EvidenceGap, EvidenceGapCode

if TYPE_CHECKING:
    from vigitwin.models.pair_features import FeatureContribution, Inconsistency

__all__ = [
    "recover_clinical_date",
    "recover_drug_set_terms",
    "recovered_clinical_date_gap",
    "recovered_drug_set_gap",
    "resolved_clinical_date",
    "resolved_drug_set_terms",
]


#: ``_check_drug_set``'s own wording, both directions. "report 1 not found in
#: report 2" means the terms are key drugs of report 1 — that is
#: ``missing_in_r2``, matching the ``kind`` the check writes today.
_DRUG_SET_RE = re.compile(
    r"^Key drug\(s\) in report (?P<source>[12]) not found in report "
    r"(?P<target>[12]): (?P<terms>\[.*\])$"
)

#: ``compute_probability``'s own wording for one matched clinical date, e.g.
#: ``clinical_date 2022-10-11 (day): D_f=1200``.
_CLINICAL_DATE_RE = re.compile(
    r"^clinical_date (?P<value>[^\s(]+) \((?P<resolution>[a-z]+)\):"
)

#: The resolutions ``ClinicalDateMatch`` can report. A string outside this set
#: means the description is not the one this parser was written for.
_RESOLUTIONS = frozenset({"day", "month", "year"})


def recover_drug_set_terms(description: str | None) -> tuple[list[str], str] | None:
    """Recover ``(terms, kind)`` from a drug-set finding's description.

    ``None`` when the description is absent, is not the shape the drug-set
    check writes, or does not hold a list of substance names — in which case
    the caller keeps today's behaviour of naming the finding without
    per-substance highlighting.

    The names are returned exactly as the check stored them (lowercased
    substance names), so a consumer joining them onto a drug table applies its
    own normalisation to both sides, as it does for the structured field.
    """
    if not description:
        return None
    match = _DRUG_SET_RE.match(description.strip())
    if match is None:
        return None
    source, target = match["source"], match["target"]
    if source == target:
        return None
    try:
        terms = ast.literal_eval(match["terms"])
    except (ValueError, SyntaxError):
        return None
    if not isinstance(terms, list) or not terms:
        return None
    if not all(isinstance(term, str) and term for term in terms):
        return None
    kind = "missing_in_r2" if source == "1" else "missing_in_r1"
    return list(terms), kind


def recover_clinical_date(description: str | None) -> tuple[str, str] | None:
    """Recover ``(matched_value, resolution)`` from a contribution's description.

    ``None`` when the description is absent, is not the shape the probability
    module writes, or names a resolution that module does not produce. The
    value is returned as the pipeline compared it — already truncated to the
    matched resolution — which is what makes it a join key for the date cells
    rather than a label.
    """
    if not description:
        return None
    match = _CLINICAL_DATE_RE.match(description.strip())
    if match is None:
        return None
    resolution = match["resolution"]
    if resolution not in _RESOLUTIONS:
        return None
    value = match["value"]
    return value, resolution


def resolved_drug_set_terms(
    inc: "Inconsistency",
) -> tuple[list[str], str | None, bool] | None:
    """The substances and direction for one drug-set finding.

    Returns ``(terms, kind, recovered)``, where ``recovered`` says the values
    came from the description rather than the structured fields, or ``None``
    when neither source yields any. The structured fields always win: this
    never second-guesses a run that recorded them.
    """
    if inc.terms:
        return list(inc.terms), inc.kind, False
    found = recover_drug_set_terms(inc.description)
    if found is None:
        return None
    terms, kind = found
    return terms, kind, True


def resolved_clinical_date(
    fc: "FeatureContribution",
) -> tuple[str, str | None, bool] | None:
    """The matched date and resolution for one clinical-date contribution.

    Returns ``(matched_value, resolution, recovered)``, where ``recovered``
    says the values came from the description rather than the structured
    fields, or ``None`` when neither source yields a date. The structured
    fields always win.
    """
    if fc.matched_value is not None:
        return fc.matched_value, fc.value_resolution, False
    found = recover_clinical_date(fc.description)
    if found is None:
        return None
    value, resolution = found
    return value, resolution, True


def recovered_drug_set_gap(n_findings: int) -> EvidenceGap:
    """The footnote for drug-set terms recovered from a description."""
    plural = "s" if n_findings != 1 else ""
    verb = "were" if n_findings != 1 else "was"
    return EvidenceGap(
        code=EvidenceGapCode.DRUG_SET_TERMS_RECOVERED,
        message=(
            f"The substance{plural} named by {n_findings} drug-set "
            f"inconsistenc{'ies' if n_findings != 1 else 'y'} {verb} read "
            "from the finding's own stored description, because this run was "
            "saved before they were recorded as structured terms. The "
            "highlighting is the run's own finding, not a recomputed "
            "comparison."
        ),
    )


def recovered_clinical_date_gap(n_dates: int) -> EvidenceGap:
    """The footnote for clinical dates recovered from a description."""
    plural = "s" if n_dates != 1 else ""
    verb = "were" if n_dates != 1 else "was"
    return EvidenceGap(
        code=EvidenceGapCode.CLINICAL_DATE_VALUE_RECOVERED,
        message=(
            f"{n_dates} matched clinical date{plural} {verb} read from the "
            "contribution's own stored description, because this run was "
            "saved before the matched value was recorded alongside it. The "
            "dates are highlighted wherever they appear, but the run did not "
            "record which date field each match came from, so a report "
            "carrying the same date in two fields shows both as matched."
        ),
    )
