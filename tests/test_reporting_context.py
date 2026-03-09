"""Tests for reporting context determination."""

from llm_dup_dev.models.enums import ReportingContextLevel
from llm_dup_dev.models.report import DateValue, Report, SourcedValue
from llm_dup_dev.feature_generation.reporting_context import (
    determine_reporting_context,
)


class TestReportingContext:
    def test_same_country_and_year(self):
        r1 = Report(
            report_id="1",
            country_of_origin_values=[
                SourcedValue(value="US", sources=["structured"])
            ],
            report_date_values=[
                DateValue(
                    value="2020-06-12",
                    resolution="day",
                    sources=["structured"],
                )
            ],
        )
        r2 = Report(
            report_id="2",
            country_of_origin_values=[
                SourcedValue(value="US", sources=["structured"])
            ],
            report_date_values=[
                DateValue(
                    value="2020-09-01",
                    resolution="day",
                    sources=["structured"],
                )
            ],
        )
        ctx = determine_reporting_context(r1, r2)
        assert ctx.level == ReportingContextLevel.COUNTRY_YEAR
        assert ctx.country == "US"
        assert ctx.year == 2020

    def test_same_country_different_year(self):
        r1 = Report(
            report_id="1",
            country_of_origin_values=[
                SourcedValue(value="US", sources=["structured"])
            ],
            report_date_values=[
                DateValue(
                    value="2020-06-12",
                    resolution="day",
                    sources=["structured"],
                )
            ],
        )
        r2 = Report(
            report_id="2",
            country_of_origin_values=[
                SourcedValue(value="US", sources=["structured"])
            ],
            report_date_values=[
                DateValue(
                    value="2021-09-01",
                    resolution="day",
                    sources=["structured"],
                )
            ],
        )
        ctx = determine_reporting_context(r1, r2)
        assert ctx.level == ReportingContextLevel.COUNTRY
        assert ctx.country == "US"
        assert ctx.year is None

    def test_different_countries(self):
        r1 = Report(
            report_id="1",
            country_of_origin_values=[
                SourcedValue(value="US", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            country_of_origin_values=[
                SourcedValue(value="DE", sources=["structured"])
            ],
        )
        ctx = determine_reporting_context(r1, r2)
        assert ctx.level == ReportingContextLevel.GLOBAL

    def test_missing_country_one_side(self):
        r1 = Report(
            report_id="1",
            country_of_origin_values=[
                SourcedValue(value="US", sources=["structured"])
            ],
        )
        r2 = Report(report_id="2")
        ctx = determine_reporting_context(r1, r2)
        assert ctx.level == ReportingContextLevel.GLOBAL

    def test_both_missing(self):
        r1 = Report(report_id="1")
        r2 = Report(report_id="2")
        ctx = determine_reporting_context(r1, r2)
        assert ctx.level == ReportingContextLevel.GLOBAL

    def test_year_only_resolution(self):
        """Year-only dates should still extract the year correctly."""
        r1 = Report(
            report_id="1",
            country_of_origin_values=[
                SourcedValue(value="JP", sources=["structured"])
            ],
            report_date_values=[
                DateValue(
                    value="2019", resolution="year", sources=["structured"]
                )
            ],
        )
        r2 = Report(
            report_id="2",
            country_of_origin_values=[
                SourcedValue(value="JP", sources=["structured"])
            ],
            report_date_values=[
                DateValue(
                    value="2019-03", resolution="month", sources=["structured"]
                )
            ],
        )
        ctx = determine_reporting_context(r1, r2)
        assert ctx.level == ReportingContextLevel.COUNTRY_YEAR
        assert ctx.country == "JP"
        assert ctx.year == 2019

    def test_same_country_no_dates(self):
        """Shared country but no dates → country-level context."""
        r1 = Report(
            report_id="1",
            country_of_origin_values=[
                SourcedValue(value="FR", sources=["structured"])
            ],
        )
        r2 = Report(
            report_id="2",
            country_of_origin_values=[
                SourcedValue(value="FR", sources=["structured"])
            ],
        )
        ctx = determine_reporting_context(r1, r2)
        assert ctx.level == ReportingContextLevel.COUNTRY
        assert ctx.country == "FR"
