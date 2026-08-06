"""Serialisation: formats, markdown legibility, and the no-suppression rule."""

from __future__ import annotations

import csv
import json

import pytest

from llm_dup_dev.characterization import (
    CSV_FILENAME,
    JSON_FILENAME,
    MARKDOWN_FILENAME,
    CharacterizationReport,
    build_characterization_report,
    characterization_to_markdown,
    write_characterization_report,
)
from llm_dup_dev.characterization.writer import CSV_HEADER

from .conftest import drug, make_reference_set, make_report, sourced


@pytest.fixture
def report(simple_reports):
    return build_characterization_report(
        [make_reference_set([("R001", "R002"), ("R003", "R004")])],
        simple_reports,
        dataset_id="ds-1",
    )


class TestFormats:
    def test_defaults_write_json_and_markdown(self, tmp_path, report):
        written = write_characterization_report(tmp_path, report)

        assert set(written) == {"json", "md"}
        assert written["json"] == tmp_path / JSON_FILENAME
        assert written["md"] == tmp_path / MARKDOWN_FILENAME
        assert all(path.exists() for path in written.values())

    def test_csv_is_opt_in(self, tmp_path, report):
        written = write_characterization_report(
            tmp_path, report, formats=("json", "md", "csv")
        )

        assert written["csv"] == tmp_path / CSV_FILENAME

    def test_missing_parents_are_created(self, tmp_path, report):
        target = tmp_path / "nested" / "deeper"
        write_characterization_report(target, report)

        assert (target / JSON_FILENAME).exists()

    def test_unknown_format_raises(self, tmp_path, report):
        with pytest.raises(ValueError, match="Unsupported"):
            write_characterization_report(tmp_path, report, formats=("json", "xlsx"))

    def test_empty_format_list_raises(self, tmp_path, report):
        with pytest.raises(ValueError, match="At least one format"):
            write_characterization_report(tmp_path, report, formats=())

    def test_json_round_trips_through_the_model(self, tmp_path, report):
        written = write_characterization_report(tmp_path, report)
        reloaded = CharacterizationReport.model_validate_json(
            written["json"].read_text()
        )

        assert reloaded.model_dump_json() == report.model_dump_json()


class TestMarkdown:
    def test_names_the_set_and_states_both_report_counts(self, report):
        text = characterization_to_markdown(report)

        assert "set_1" in text
        assert "4 unique reports" in text
        assert "4 report-instances across 2 pairs" in text

    def test_dispositions_are_printed_beside_every_distribution(self, report):
        text = characterization_to_markdown(report)

        assert text.count("Denominator 4 reports") >= 5
        assert "missing" in text
        assert "conflicting" in text

    def test_the_resolution_policy_is_stated(self, report):
        text = characterization_to_markdown(report)

        assert "structured-sourced values are preferred" in text
        assert "completed years" in text

    def test_an_all_missing_set_says_so_rather_than_printing_zeroes(
        self, report_empty, report_empty_2
    ):
        empty = build_characterization_report(
            [make_reference_set([("R_EMPTY", "R_EMPTY2")])],
            {"R_EMPTY": report_empty, "R_EMPTY2": report_empty_2},
        )
        text = characterization_to_markdown(empty)

        assert "2 with a value" not in text
        assert "0 with a value, 2 missing" in text
        # A median that could not be computed prints as an em dash, not 0.
        assert "Median — completed_years" in text

    def test_an_unavailable_table_explains_itself(self):
        reports = {
            "R1": make_report("R1", drugs=[drug("pregabalin", None)]),
            "R2": make_report("R2", drugs=[drug("metformin", None)]),
        }
        built = build_characterization_report(
            [make_reference_set([("R1", "R2")])], reports
        )
        text = characterization_to_markdown(built)

        assert "missing_drug_role" in text
        assert "missing_hierarchy" in text

    def test_truncation_and_ties_are_disclosed(self):
        substances = [f"drug_{i:02d}" for i in range(12)]
        reports = {
            f"R{i}": make_report(f"R{i}", drugs=[drug(s)])
            for i, s in enumerate(substances)
        }
        reports["RX"] = make_report("RX")
        built = build_characterization_report(
            [make_reference_set([(f"R{i}", "RX") for i in range(12)])], reports
        )
        text = characterization_to_markdown(built)

        assert "Showing the top 10 of 12 distinct values" in text
        assert "The cut fell inside a tie" in text


class TestNoSmallCellSuppression:
    """Exact counts by decision — see docs/reference_set_characteristics.md.

    Suppression is a plausible-sounding future change that would silently alter
    published numbers, so the absence of it is pinned rather than assumed.
    """

    @pytest.fixture
    def singleton_report(self):
        reports = {
            "R1": make_report("R1", countries=[sourced("Liechtenstein")]),
            "R2": make_report("R2", countries=[sourced("US")]),
        }
        return build_characterization_report(
            [make_reference_set([("R1", "R2")])], reports
        )

    def test_a_count_of_one_survives_into_the_json(self, tmp_path, singleton_report):
        written = write_characterization_report(tmp_path, singleton_report)
        payload = json.loads(written["json"].read_text())
        countries = payload["sets"][0]["reports"]["country_of_origin"]["categories"]

        assert {row["label"]: row["count"] for row in countries} == {
            "Liechtenstein": 1,
            "US": 1,
        }

    def test_a_count_of_one_survives_into_the_markdown(self, singleton_report):
        text = characterization_to_markdown(singleton_report)

        assert "| Liechtenstein | 1 |" in text

    def test_no_cell_is_replaced_by_a_threshold_placeholder(self, singleton_report):
        text = characterization_to_markdown(singleton_report)

        table_rows = [line for line in text.splitlines() if line.startswith("|")]
        for placeholder in ("<5", "&lt;5", "[redacted]", "suppressed", "n/a"):
            assert not any(placeholder in row for row in table_rows), placeholder

    def test_the_document_states_that_counts_are_unsuppressed(self, singleton_report):
        """Stated once at the top — a fact about the artifact, not a per-cell warning."""
        text = characterization_to_markdown(singleton_report)

        assert "Counts are exact and unsuppressed." in text
        assert text.count("unsuppressed") == 1


class TestCsv:
    def test_long_format_with_the_declared_header(self, tmp_path, report):
        written = write_characterization_report(
            tmp_path, report, formats=("csv",)
        )
        with open(written["csv"], newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))

        assert rows[0] == CSV_HEADER
        assert len(rows) > 1
        assert all(len(row) == len(CSV_HEADER) for row in rows)

    def test_every_row_names_its_set_and_section(self, tmp_path, report):
        written = write_characterization_report(tmp_path, report, formats=("csv",))
        with open(written["csv"], newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

        assert {row["set_id"] for row in rows} == {"set_1"}
        assert {row["section"] for row in rows} == {"reports", "pairs"}
