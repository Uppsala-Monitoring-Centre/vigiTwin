"""Shared test fixtures for the same-individual assessment pipeline.

All reports use synthetic data only — no real pharmacovigilance data.
"""

import pytest

from llm_dup_dev.models.report import (
    AdverseEvent,
    AgeValue,
    DateValue,
    Drug,
    MeasurementValue,
    PartialDate,
    Report,
    SourcedValue,
)


@pytest.fixture
def report_a() -> Report:
    """A synthetic report: 54yo female, US, pregabalin + sertraline."""
    return Report(
        report_id="R001",
        patient_sex_values=[
            SourcedValue(value="female", sources=["structured"]),
        ],
        patient_age_values=[
            AgeValue(value=54, unit="years", sources=["structured"]),
        ],
        patient_height_values=[
            MeasurementValue(value=165.0, unit="cm", sources=["structured"]),
        ],
        patient_weight_values=[
            MeasurementValue(value=68.0, unit="kg", sources=["structured"]),
        ],
        country_of_origin_values=[
            SourcedValue(value="US", sources=["structured"]),
        ],
        report_date_values=[
            DateValue(
                value="2020-06-12", resolution="day", sources=["structured"]
            ),
        ],
        drugs=[
            Drug(
                substance="pregabalin",
                sources=["structured"],
                start_date=PartialDate(value="2020-05-01", resolution="day"),
            ),
            Drug(
                substance="sertraline",
                sources=["structured"],
                start_date=PartialDate(value="2020-03-15", resolution="day"),
            ),
        ],
        adverse_events=[
            AdverseEvent(pt_name="somnolence", sources=["structured"]),
            AdverseEvent(pt_name="dizziness", sources=["structured"]),
        ],
        narrative=(
            "Patient experienced somnolence and dizziness "
            "after starting pregabalin."
        ),
    )


@pytest.fixture
def report_b() -> Report:
    """A near-duplicate of report_a with minor differences.

    Differences from report_a:
    - age has two candidate values (54 structured, 55 narrative)
    - weight differs slightly (70 vs 68)
    - one extra drug (omeprazole)
    - AE set differs (somnolence shared, nausea vs dizziness)
    """
    return Report(
        report_id="R002",
        patient_sex_values=[
            SourcedValue(value="female", sources=["structured"]),
        ],
        patient_age_values=[
            AgeValue(value=54, unit="years", sources=["structured"]),
            AgeValue(value=55, unit="years", sources=["narrative_llm"]),
        ],
        patient_height_values=[
            MeasurementValue(value=165.0, unit="cm", sources=["structured"]),
        ],
        patient_weight_values=[
            MeasurementValue(value=70.0, unit="kg", sources=["structured"]),
        ],
        country_of_origin_values=[
            SourcedValue(value="US", sources=["structured"]),
        ],
        report_date_values=[
            DateValue(
                value="2020-06-12", resolution="day", sources=["structured"]
            ),
        ],
        drugs=[
            Drug(
                substance="pregabalin",
                sources=["structured"],
                start_date=PartialDate(value="2020-05-01", resolution="day"),
            ),
            Drug(substance="sertraline", sources=["structured"]),
            Drug(substance="omeprazole", sources=["structured"]),
        ],
        adverse_events=[
            AdverseEvent(pt_name="somnolence", sources=["structured"]),
            AdverseEvent(pt_name="nausea", sources=["structured"]),
        ],
        narrative=(
            "Female patient, 55yo, reports drowsiness following "
            "pregabalin treatment."
        ),
    )


@pytest.fixture
def report_c() -> Report:
    """A clearly different report: 28yo male, Germany, different drugs."""
    return Report(
        report_id="R003",
        patient_sex_values=[
            SourcedValue(value="male", sources=["structured"]),
        ],
        patient_age_values=[
            AgeValue(value=28, unit="years", sources=["structured"]),
        ],
        patient_height_values=[
            MeasurementValue(value=180.0, unit="cm", sources=["structured"]),
        ],
        patient_weight_values=[
            MeasurementValue(value=85.0, unit="kg", sources=["structured"]),
        ],
        country_of_origin_values=[
            SourcedValue(value="DE", sources=["structured"]),
        ],
        report_date_values=[
            DateValue(
                value="2022-01-10", resolution="day", sources=["structured"]
            ),
        ],
        drugs=[
            Drug(substance="metformin", sources=["structured"]),
            Drug(substance="lisinopril", sources=["structured"]),
        ],
        adverse_events=[
            AdverseEvent(pt_name="headache", sources=["structured"]),
        ],
        narrative="28-year-old male developed headache after starting metformin.",
    )


@pytest.fixture
def report_empty() -> Report:
    """A minimal report with no demographic or clinical information."""
    return Report(report_id="R_EMPTY")


@pytest.fixture
def report_empty_2() -> Report:
    """A second minimal report, so an "empty pair" is not a self-pair.

    A report is not a candidate duplicate of itself, so pair identity rejects
    the same id twice (see ``llm_dup_dev.models.pair_identity``).
    """
    return Report(report_id="R_EMPTY2")
