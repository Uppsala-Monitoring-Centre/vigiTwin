"""Serialise a :class:`CharacterizationReport` to JSON, markdown, and CSV.

Mirrors :func:`llm_dup_dev.dataset_manifest.write_dataset_manifest`: the caller
picks formats, unknown ones raise, and the written paths come back keyed by
format.

:func:`characterization_to_markdown` does no arithmetic — every number it prints
was computed in the builder. That split is deliberate and matches
``study_eval.results.summary_to_markdown``: a statistic that exists only in a
rendered document cannot be tested, re-read, or checked against the JSON.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from pathlib import Path

from llm_dup_dev.characterization.models import (
    CategoricalDistribution,
    CharacterizationReport,
    Concordance,
    NumericSummary,
    ReferenceSetCharacteristics,
)

JSON_FILENAME = "reference_set_characteristics.json"
MARKDOWN_FILENAME = "reference_set_characteristics.md"
CSV_FILENAME = "reference_set_characteristics.csv"

_SUPPORTED_FORMATS = frozenset({"json", "md", "csv"})

CSV_HEADER = [
    "set_id",
    "section",
    "field",
    "key",
    "label",
    "statistic",
    "value",
    "denominator",
]


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _num(value: float | None) -> str:
    """Format a statistic, keeping enough precision for a quartile of counts.

    Two decimals rather than one: a shared-drug Q1 of 0.25 rendered as ``0.2``
    is a different number, and count-valued quartiles land on quarters often.
    """
    if value is None:
        return "—"
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}"


def _distribution_lines(
    distribution: CategoricalDistribution,
    title: str,
    *,
    sort_by_key: bool = False,
) -> list[str]:
    """Render one categorical distribution, dispositions included.

    The disposition rows are not optional garnish: printing a table of resolved
    values without them invites reading a percentage as a share of all reports
    when it is a share of those that had the field.
    """
    lines = [f"**{title}**", ""]

    if distribution.unavailable_reason:
        lines += [
            f"Not available for this set (`{distribution.unavailable_reason}`) — "
            f"no report carried this field, so an empty table here does not mean "
            f"the underlying events are absent.",
            "",
        ]

    rows = distribution.categories
    if sort_by_key:
        rows = sorted(rows, key=lambda row: row.key)

    if rows:
        lines += ["| Value | n | % of reports |", "| --- | ---: | ---: |"]
        lines += [
            f"| {row.label} | {row.count} | {_pct(row.proportion)} |" for row in rows
        ]
        lines.append("")

    if distribution.truncated_to is not None:
        tie = (
            " The cut fell inside a tie, so excluded values were no rarer than "
            "the last one shown."
            if distribution.boundary_tie
            else ""
        )
        lines += [
            f"Showing the top {distribution.truncated_to} of "
            f"{distribution.n_distinct} distinct values.{tie}",
            "",
        ]

    lines += [
        f"Denominator {distribution.denominator} reports — "
        f"{distribution.n_resolved} with a value, "
        f"{distribution.n_missing} missing, "
        f"{distribution.n_conflicting} conflicting, "
        f"{distribution.n_unrecognised} uninterpretable, "
        f"{distribution.n_out_of_range} out of range.",
        "",
    ]
    return lines


def _numeric_lines(
    summary: NumericSummary, title: str, *, extra: Sequence[str] = ()
) -> list[str]:
    """Render a numeric summary, with ``extra`` bullets kept in the same list."""
    return [
        f"**{title}**",
        "",
        f"- Median {_num(summary.median)} {summary.unit} "
        f"(IQR {_num(summary.q1)}–{_num(summary.q3)}; "
        f"range {_num(summary.minimum)}–{_num(summary.maximum)})",
        f"- Computed over {summary.n_used}; excluded "
        f"{summary.n_missing} missing, {summary.n_conflicting} conflicting, "
        f"{summary.n_unrecognised_unit} uninterpretable unit, "
        f"{summary.n_out_of_range} out of range.",
        *extra,
        "",
    ]


def _concordance_lines(concordance: Concordance) -> list[str]:
    return [
        f"| {concordance.field} | {concordance.n_same} | "
        f"{concordance.n_different} | {concordance.n_not_comparable} | "
        f"{_pct(concordance.proportion_same_comparable)} | "
        f"{_pct(concordance.proportion_same_all)} |"
    ]


def _set_lines(block: ReferenceSetCharacteristics) -> list[str]:
    reports = block.reports
    pairs = block.pairs

    heading = block.label or block.set_id
    provenance = f", provenance `{block.provenance}`" if block.provenance else ""
    labels = ", ".join(
        f"{label}: {count}" for label, count in block.reference_label_counts.items()
    )

    lines = [
        f"## {heading}",
        "",
        f"`{block.set_id}` — {block.role} set{provenance}, "
        f"screening mode `{block.screening_mode}`.",
        "",
        f"{reports.n_unique_reports} unique reports "
        f"({reports.n_report_instances} report-instances across "
        f"{reports.n_pairs} pairs; {reports.n_reports_in_multiple_pairs} reports "
        f"appear in more than one pair).",
        "",
    ]
    if reports.n_reports_unavailable:
        lines += [
            f"**{reports.n_reports_unavailable} referenced reports were not "
            f"available** and are excluded from every denominator below.",
            "",
        ]
    if labels:
        lines += [f"Reference labels — {labels}.", ""]

    lines += ["### Report characteristics", ""]
    lines += _distribution_lines(reports.report_year, "Year of reporting", sort_by_key=True)
    lines += _distribution_lines(reports.country_of_origin, "Country of origin")
    lines += _distribution_lines(reports.patient_sex, "Patient sex")
    lines += _numeric_lines(reports.patient_age_years, "Patient age")
    lines += _distribution_lines(reports.patient_age_bands, "Patient age bands", sort_by_key=True)
    lines += _distribution_lines(reports.drug_roles, "Drug roles recorded")
    lines += _distribution_lines(
        reports.top_drugs, "Most common drugs (suspected or interacting)"
    )
    lines += _distribution_lines(
        reports.top_adverse_events_pt, "Most common adverse events (MedDRA PT)"
    )
    lines += _distribution_lines(
        reports.top_adverse_events_hlt, "Most common adverse events (MedDRA HLT)"
    )

    availability = reports.availability
    lines += [
        "**Field availability** (of "
        f"{reports.n_unique_reports} reports)",
        "",
        f"- Narrative: {availability.n_reports_with_narrative}",
        f"- Any drug: {availability.n_reports_with_any_drug} "
        f"(any role recorded: {availability.n_reports_with_any_drug_role_recorded}; "
        f"any suspected/interacting: "
        f"{availability.n_reports_with_any_suspect_or_interacting_drug})",
        f"- Any adverse event: {availability.n_reports_with_any_adverse_event} "
        f"(any with a primary HLT: {availability.n_reports_with_any_hlt})",
        f"- Case identifiers: {availability.n_reports_with_case_identifiers}",
        "",
        "### Pair characteristics",
        "",
        f"{pairs.n_pairs_evaluable} of {pairs.n_pairs} pairs had both reports "
        f"available.",
        "",
        "| Field | Same | Different | Not comparable | % same (comparable) | % same (all) |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines += _concordance_lines(pairs.country)
    lines += _concordance_lines(pairs.report_year)
    lines += _concordance_lines(pairs.patient_sex)
    lines.append("")

    lines += _numeric_lines(pairs.year_difference, "Difference in reporting year")
    lines += _numeric_lines(
        pairs.age_difference_years,
        "Difference in patient age",
        extra=[
            f"- Pairs with identical patient age: "
            f"{pairs.n_pairs_with_identical_age}"
        ],
    )
    lines += _numeric_lines(
        pairs.shared_drugs,
        "Shared drugs per pair",
        extra=[
            f"- Pairs sharing at least one drug: "
            f"{pairs.n_pairs_with_any_shared_drug}"
        ],
    )
    lines += _numeric_lines(
        pairs.shared_adverse_events_pt,
        "Shared adverse events (PT) per pair",
        extra=[
            f"- Pairs sharing at least one adverse event: "
            f"{pairs.n_pairs_with_any_shared_adverse_event}"
        ],
    )
    return lines


def characterization_to_markdown(report: CharacterizationReport) -> str:
    """Render a human-readable summary of a characterization report."""
    policy = report.policy
    lines = [
        "# Reference-set characteristics",
        "",
        f"Generated {report.created_at.isoformat()} "
        f"(`{report.characterization_id}`"
        + (f", dataset `{report.dataset_id}`" if report.dataset_id else "")
        + ").",
        "",
        "Descriptive statistics only — no performance estimate and no "
        "confidence interval appears here. Counts are exact and unsuppressed.",
        "",
    ]
    if report.notes:
        lines += [f"**Notes.** {report.notes}", ""]

    lines += [
        "**Resolution policy.** Where a report carries several candidate values "
        f"for a field, {'/'.join(policy.source_preference)}-sourced values are "
        "preferred and the rest used only as a fallback; "
        f"{policy.conflict_handling}. Age is reported in completed years, banded "
        f"every {policy.age_band_width_years} years, with values outside "
        f"{policy.age_plausible_range} excluded as out of range. Drugs are "
        f"counted only where the recorded role is one of "
        f"{', '.join(policy.drug_roles_counted)}. Country keys are normalised by "
        f"{policy.country_key_normalisation} — no code system is assumed. Ranked "
        f"lists show the top {policy.top_n}, ordered by {policy.tie_break}.",
        "",
    ]

    for block in report.sets:
        lines += _set_lines(block)

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def _distribution_rows(
    set_id: str, section: str, distribution: CategoricalDistribution
) -> list[list[object]]:
    rows: list[list[object]] = [
        [
            set_id,
            section,
            distribution.field,
            row.key,
            row.label,
            "count",
            row.count,
            distribution.denominator,
        ]
        for row in distribution.categories
    ]
    for statistic, value in (
        ("n_resolved", distribution.n_resolved),
        ("n_missing", distribution.n_missing),
        ("n_conflicting", distribution.n_conflicting),
        ("n_unrecognised", distribution.n_unrecognised),
        ("n_out_of_range", distribution.n_out_of_range),
        ("n_distinct", distribution.n_distinct),
    ):
        rows.append(
            [set_id, section, distribution.field, "", "", statistic, value,
             distribution.denominator]
        )
    return rows


def _numeric_rows(
    set_id: str, section: str, summary: NumericSummary
) -> list[list[object]]:
    return [
        [set_id, section, summary.field, "", summary.unit, statistic, value,
         summary.n_used]
        for statistic, value in (
            ("median", summary.median),
            ("q1", summary.q1),
            ("q3", summary.q3),
            ("minimum", summary.minimum),
            ("maximum", summary.maximum),
            ("n_used", summary.n_used),
            ("n_missing", summary.n_missing),
            ("n_conflicting", summary.n_conflicting),
            ("n_unrecognised_unit", summary.n_unrecognised_unit),
            ("n_out_of_range", summary.n_out_of_range),
        )
    ]


def _concordance_rows(
    set_id: str, concordance: Concordance
) -> list[list[object]]:
    return [
        [set_id, "pairs", concordance.field, "", "", statistic, value,
         concordance.n_pairs]
        for statistic, value in (
            ("n_same", concordance.n_same),
            ("n_different", concordance.n_different),
            ("n_not_comparable", concordance.n_not_comparable),
            ("proportion_same_comparable", concordance.proportion_same_comparable),
            ("proportion_same_all", concordance.proportion_same_all),
        )
    ]


def characterization_to_csv_rows(report: CharacterizationReport) -> list[list[object]]:
    """Flatten the report into long-format rows, header excluded.

    Long format for the same reason ``study_eval.results`` uses it: one file
    that a statistician can filter and pivot beats a wide table per section.
    """
    rows: list[list[object]] = []
    for block in report.sets:
        reports, pairs = block.reports, block.pairs
        for distribution in (
            reports.report_year,
            reports.country_of_origin,
            reports.patient_sex,
            reports.patient_age_bands,
            reports.drug_roles,
            reports.top_drugs,
            reports.top_adverse_events_pt,
            reports.top_adverse_events_hlt,
        ):
            rows += _distribution_rows(block.set_id, "reports", distribution)
        rows += _numeric_rows(block.set_id, "reports", reports.patient_age_years)

        for concordance in (pairs.country, pairs.report_year, pairs.patient_sex):
            rows += _concordance_rows(block.set_id, concordance)
        for summary in (
            pairs.year_difference,
            pairs.age_difference_years,
            pairs.shared_drugs,
            pairs.shared_adverse_events_pt,
        ):
            rows += _numeric_rows(block.set_id, "pairs", summary)
    return rows


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def write_characterization_report(
    output_dir: str | Path,
    report: CharacterizationReport,
    formats: Iterable[str] = ("json", "md"),
) -> dict[str, Path]:
    """Write ``report`` into ``output_dir`` in the requested formats.

    ``csv`` is supported but not a default: the JSON is already machine-readable,
    so the flat table earns its place only when someone is re-plotting elsewhere.

    Creates ``output_dir`` and any missing parents. Returns a mapping from
    format name to the path written; an unknown or empty format list raises.
    """
    requested = list(formats)
    unknown = [f for f in requested if f not in _SUPPORTED_FORMATS]
    if unknown:
        raise ValueError(
            f"Unsupported characterization format(s): {unknown}. "
            f"Supported: {sorted(_SUPPORTED_FORMATS)}."
        )
    if not requested:
        raise ValueError("At least one format must be requested.")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    if "json" in requested:
        json_path = out_dir / JSON_FILENAME
        json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        written["json"] = json_path

    if "md" in requested:
        md_path = out_dir / MARKDOWN_FILENAME
        md_path.write_text(characterization_to_markdown(report), encoding="utf-8")
        written["md"] = md_path

    if "csv" in requested:
        csv_path = out_dir / CSV_FILENAME
        with open(csv_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(CSV_HEADER)
            writer.writerows(characterization_to_csv_rows(report))
        written["csv"] = csv_path

    return written


__all__ = [
    "JSON_FILENAME",
    "MARKDOWN_FILENAME",
    "CSV_FILENAME",
    "CSV_HEADER",
    "characterization_to_markdown",
    "characterization_to_csv_rows",
    "write_characterization_report",
]
