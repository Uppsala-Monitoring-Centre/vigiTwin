"""Determine the reporting context for a report pair.

The reporting context defines the population within which
probabilities and frequencies are evaluated. The most specific
shared context is preferred.

See: docs/duplicate_adjudication_spec.md § Reporting context
"""

from __future__ import annotations

from llm_dup_dev.models.enums import ReportingContextLevel
from llm_dup_dev.models.pair_features import ReportingContext
from llm_dup_dev.models.report import Report


def determine_reporting_context(r1: Report, r2: Report) -> ReportingContext:
    """Determine the most specific shared reporting context.

    Priority:
        1. country + year  (if both reports share country and year)
        2. country         (if both reports share country but not year)
        3. global          (fallback)

    When multiple countries or years overlap, the lexicographically
    first value is chosen for determinism.
    """
    countries_1 = {v.value for v in r1.country_of_origin_values}
    countries_2 = {v.value for v in r2.country_of_origin_values}
    shared_countries = countries_1 & countries_2

    if not shared_countries:
        return ReportingContext(level=ReportingContextLevel.GLOBAL)

    country = sorted(shared_countries)[0]

    years_1 = _extract_years(r1)
    years_2 = _extract_years(r2)
    shared_years = years_1 & years_2

    if shared_years:
        year = sorted(shared_years)[0]
        return ReportingContext(
            level=ReportingContextLevel.COUNTRY_YEAR,
            country=country,
            year=year,
        )

    return ReportingContext(
        level=ReportingContextLevel.COUNTRY,
        country=country,
    )


def _extract_years(report: Report) -> set[int]:
    """Extract year values from a report's date attributes."""
    years: set[int] = set()
    for dv in report.report_date_values:
        try:
            year = int(dv.value[:4])
            years.add(year)
        except (ValueError, IndexError):
            continue
    return years
