"""Builders for characterization tests.

The root ``tests/conftest.py`` fixtures (``report_a`` … ``report_empty_2``) are
visible here and are used wherever they fit. These helpers cover what those
cannot: reports built field by field for the edge cases, and reference sets
assembled from bare report-id pairs.

All data is synthetic — no real pharmacovigilance data.
"""

from __future__ import annotations

from typing import Sequence

import pytest

from llm_dup_dev.models.enums import DrugRole
from llm_dup_dev.models.pair_identity import canonical_pair_id, canonical_report_ids
from llm_dup_dev.models.report import (
    AdverseEvent,
    AgeValue,
    DateValue,
    Drug,
    Report,
    SourcedValue,
)
from llm_dup_dev.study_eval.schemas import ReferencePair, ReferenceSet


def sourced(value: str, source: str = "structured") -> SourcedValue:
    return SourcedValue(value=value, sources=[source])


def age(value: float, unit: str = "years", source: str = "structured") -> AgeValue:
    return AgeValue(value=value, unit=unit, sources=[source])


def date(value: str, resolution: str = "day", source: str = "structured") -> DateValue:
    return DateValue(value=value, resolution=resolution, sources=[source])


def drug(substance: str, role: DrugRole | None = DrugRole.SUSPECTED) -> Drug:
    return Drug(substance=substance, sources=["structured"], role=role)


def event(pt_name: str, hlt: str | None = None) -> AdverseEvent:
    return AdverseEvent(
        pt_name=pt_name, sources=["structured"], primary_hlt_name=hlt
    )


def make_report(
    report_id: str,
    *,
    ages: Sequence[AgeValue] = (),
    sexes: Sequence[SourcedValue] = (),
    countries: Sequence[SourcedValue] = (),
    dates: Sequence[DateValue] = (),
    drugs: Sequence[Drug] = (),
    events: Sequence[AdverseEvent] = (),
    narrative: str | None = None,
) -> Report:
    """A ``Report`` with only the fields a test cares about populated."""
    return Report(
        report_id=report_id,
        patient_age_values=list(ages),
        patient_sex_values=list(sexes),
        country_of_origin_values=list(countries),
        report_date_values=list(dates),
        drugs=list(drugs),
        adverse_events=list(events),
        narrative=narrative,
    )


def make_reference_set(
    pair_specs: Sequence[tuple[str, str]],
    *,
    set_id: str = "set_1",
    role: str = "precision",
    label: str | None = None,
    provenance: str | None = None,
    reference_label: str | None = "duplicate",
) -> ReferenceSet:
    """A reference set over ``(report_id1, report_id2)`` tuples.

    Pair ids are derived with ``canonical_pair_id`` rather than formatted by
    hand, because the ordering rule is numeric and the model rejects a
    noncanonical pair rather than reordering it.
    """
    pairs = []
    for left, right in pair_specs:
        lower, higher = canonical_report_ids(left, right)
        pairs.append(
            ReferencePair(
                pair_id=canonical_pair_id(lower, higher),
                report_id1=lower,
                report_id2=higher,
                reference_label=reference_label if role == "precision" else None,
            )
        )
    return ReferenceSet(
        set_id=set_id, role=role, label=label, provenance=provenance, pairs=pairs
    )


def by_id(*reports: Report) -> dict[str, Report]:
    return {report.report_id: report for report in reports}


class DictReportLoader:
    """A minimal ``ReportLoader`` over a dict, counting its lookups.

    Exists to prove the two input seams agree and that each report is fetched
    once however many pairs name it.
    """

    def __init__(self, reports: dict[str, Report]) -> None:
        self._reports = reports
        self.calls: list[str] = []

    def load_report(self, report_id: str) -> Report:
        self.calls.append(report_id)
        return self._reports[report_id]

    def load_pair(self, pair):  # pragma: no cover - unused by characterization
        return self.load_report(pair.report_1_id), self.load_report(pair.report_2_id)

    def load_pairs(self, pairs):  # pragma: no cover - unused by characterization
        for pair in pairs:
            left, right = self.load_pair(pair)
            yield pair, left, right


@pytest.fixture
def simple_reports() -> dict[str, Report]:
    """Four reports covering a shared drug, a missing country, and an empty report."""
    return by_id(
        make_report(
            "R001",
            ages=[age(54)],
            sexes=[sourced("female")],
            countries=[sourced("US")],
            dates=[date("2020-06-12")],
            drugs=[drug("pregabalin"), drug("sertraline")],
            events=[event("somnolence", "disturbances in consciousness nec")],
            narrative="Somnolence after pregabalin.",
        ),
        make_report(
            "R002",
            ages=[age(54), age(55, source="narrative_llm")],
            sexes=[sourced("female")],
            countries=[sourced("US")],
            dates=[date("2020", resolution="year")],
            drugs=[drug("pregabalin")],
            events=[event("somnolence", "disturbances in consciousness nec")],
        ),
        make_report(
            "R003",
            ages=[age(28)],
            sexes=[sourced("male")],
            countries=[sourced("DE")],
            dates=[date("2022-01-10")],
            drugs=[drug("metformin")],
            events=[event("headache", "headaches nec")],
        ),
        make_report("R004"),
    )
