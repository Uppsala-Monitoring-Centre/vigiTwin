"""Tests for llm_dup_dev.review_html.normalize."""

from llm_dup_dev.models.report import (
    AgeValue,
    DateValue,
    MeasurementValue,
    PartialDate,
    SourcedValue,
)
from llm_dup_dev.review_html.normalize import (
    MISSING,
    format_age_values,
    format_date_values,
    format_measurement_values,
    format_partial_date,
    format_sourced_values,
    normalize_ae_key,
    normalize_drug_key,
)


# ---------------------------------------------------------------------------
# format_sourced_values
# ---------------------------------------------------------------------------


class TestFormatSourcedValues:
    def test_empty_list_returns_missing(self):
        assert format_sourced_values([]) == MISSING

    def test_single_value(self):
        result = format_sourced_values(
            [SourcedValue(value="female", sources=["structured"])]
        )
        assert result == "female"

    def test_multiple_distinct_values(self):
        result = format_sourced_values(
            [
                SourcedValue(value="US", sources=["structured"]),
                SourcedValue(value="CA", sources=["narrative_llm"]),
            ]
        )
        assert "US" in result
        assert "CA" in result
        assert ";" in result
        # provenance labels omitted
        assert "structured" not in result
        assert "narrative_llm" not in result

    def test_duplicate_values_deduplicated(self):
        result = format_sourced_values(
            [
                SourcedValue(value="female", sources=["structured"]),
                SourcedValue(value="female", sources=["narrative_llm"]),
            ]
        )
        assert result == "female"

    def test_multiple_sources_provenance_omitted(self):
        result = format_sourced_values(
            [SourcedValue(value="female", sources=["structured", "narrative_llm"])]
        )
        assert result == "female"

    def test_narrative_only_wrapped_in_em(self):
        result = format_sourced_values(
            [SourcedValue(value="female", sources=["narrative_llm"])]
        )
        assert result == "<em>female</em>"

    def test_structured_not_wrapped_in_em(self):
        result = format_sourced_values(
            [SourcedValue(value="female", sources=["structured"])]
        )
        assert "<em>" not in result

    def test_mixed_sources_not_wrapped_in_em(self):
        result = format_sourced_values(
            [SourcedValue(value="female", sources=["structured", "narrative_llm"])]
        )
        assert "<em>" not in result

    def test_dedupe_drops_italics_when_any_contributor_structured(self):
        """A value contributed by both structured and narrative is shown plain."""
        result = format_sourced_values(
            [
                SourcedValue(value="female", sources=["structured"]),
                SourcedValue(value="female", sources=["narrative_llm"]),
            ]
        )
        assert "<em>" not in result

    def test_html_special_chars_escaped(self):
        result = format_sourced_values(
            [SourcedValue(value="A&B<C", sources=["structured"])]
        )
        assert "&amp;" in result
        assert "&lt;" in result
        assert "<C" not in result


# ---------------------------------------------------------------------------
# format_age_values
# ---------------------------------------------------------------------------


class TestFormatAgeValues:
    def test_empty_returns_missing(self):
        assert format_age_values([]) == MISSING

    def test_whole_number_displayed_as_int(self):
        result = format_age_values(
            [AgeValue(value=54.0, unit="years", sources=["structured"])]
        )
        assert result == "54 years"

    def test_fractional_value_displayed(self):
        result = format_age_values(
            [AgeValue(value=2.5, unit="months", sources=["structured"])]
        )
        assert "2.5 months" in result

    def test_multiple_values(self):
        result = format_age_values(
            [
                AgeValue(value=54, unit="years", sources=["structured"]),
                AgeValue(value=55, unit="years", sources=["narrative_llm"]),
            ]
        )
        assert "54 years" in result
        assert "55 years" in result

    def test_narrative_only_wrapped_in_em(self):
        result = format_age_values(
            [AgeValue(value=42, unit="years", sources=["narrative_llm"])]
        )
        assert result == "<em>42 years</em>"

    def test_mixed_segments_only_narrative_italicised(self):
        result = format_age_values(
            [
                AgeValue(value=54, unit="years", sources=["structured"]),
                AgeValue(value=55, unit="years", sources=["narrative_llm"]),
            ]
        )
        assert "<em>55 years</em>" in result
        assert "<em>54" not in result


# ---------------------------------------------------------------------------
# format_measurement_values
# ---------------------------------------------------------------------------


class TestFormatMeasurementValues:
    def test_empty_returns_missing(self):
        assert format_measurement_values([]) == MISSING

    def test_single_value(self):
        result = format_measurement_values(
            [MeasurementValue(value=165.0, unit="cm", sources=["structured"])]
        )
        assert result == "165 cm"

    def test_narrative_only_wrapped_in_em(self):
        result = format_measurement_values(
            [MeasurementValue(value=70.0, unit="kg", sources=["narrative_llm"])]
        )
        assert result == "<em>70 kg</em>"

    def test_structured_not_wrapped_in_em(self):
        result = format_measurement_values(
            [MeasurementValue(value=165.0, unit="cm", sources=["structured"])]
        )
        assert "<em>" not in result


# ---------------------------------------------------------------------------
# format_date_values
# ---------------------------------------------------------------------------


class TestFormatDateValues:
    def test_empty_returns_missing(self):
        assert format_date_values([]) == MISSING

    def test_day_resolution(self):
        result = format_date_values(
            [DateValue(value="2020-06-12", resolution="day", sources=["structured"])]
        )
        assert result == "2020-06-12"

    def test_month_resolution(self):
        result = format_date_values(
            [DateValue(value="2020-06", resolution="month", sources=["structured"])]
        )
        assert result == "2020-06"

    def test_narrative_only_wrapped_in_em(self):
        result = format_date_values(
            [
                DateValue(
                    value="2020-06-12",
                    resolution="day",
                    sources=["narrative_llm"],
                )
            ]
        )
        assert result == "<em>2020-06-12</em>"


# ---------------------------------------------------------------------------
# format_partial_date
# ---------------------------------------------------------------------------


class TestFormatPartialDate:
    def test_none_returns_none(self):
        assert format_partial_date(None) is None

    def test_day_resolution(self):
        result = format_partial_date(
            PartialDate(value="2020-05-01", resolution="day")
        )
        assert result == "2020-05-01"

    def test_year_resolution(self):
        result = format_partial_date(
            PartialDate(value="2020", resolution="year")
        )
        assert result == "2020"


# ---------------------------------------------------------------------------
# Normalisation keys
# ---------------------------------------------------------------------------


class TestNormalizeKeys:
    def test_drug_key_lowercases(self):
        assert normalize_drug_key("Pregabalin") == "pregabalin"

    def test_drug_key_strips_whitespace(self):
        assert normalize_drug_key("  aspirin ") == "aspirin"

    def test_ae_key_lowercases(self):
        assert normalize_ae_key("Somnolence") == "somnolence"
