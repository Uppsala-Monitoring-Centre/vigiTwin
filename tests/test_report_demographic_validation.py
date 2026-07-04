"""Tests for Report-layer demographic value validation.

The ``Report`` model normalises structured categorical demographics at
construction time (see ``docs/data_contracts.md`` § Patient Sex):

* ``patient_sex_values`` keeps only the E2B-aligned binary vocabulary
  ``male``/``female`` (case-insensitive), dropping everything else as missing.
* ``country_of_origin_values`` drops uncertainty sentinels while leaving real
  values untouched.

Offending entries are dropped, not rejected, so legacy frozen bundles remain
loadable.
"""

import pytest

from llm_dup_dev.feature_generation.inconsistencies import detect_inconsistencies
from llm_dup_dev.classifier.config import InconsistencyThresholds
from llm_dup_dev.feature_generation.narrative_demographics import (
    merge_narrative_demographics,
)
from llm_dup_dev.models.narrative_demographics import (
    NarrativeDemographics,
    NarrativeSex,
    NarrativeSexValue,
)
from llm_dup_dev.models.report import Report, SourcedValue


def _sex(r: Report) -> list[tuple[str, list[str]]]:
    return [(v.value, v.sources) for v in r.patient_sex_values]


def _country(r: Report) -> list[str]:
    return [v.value for v in r.country_of_origin_values]


class TestSexNormalization:

    @pytest.mark.parametrize(
        "raw",
        ["not known", "unknown", "other", "", "n/a", "not reported"],
    )
    def test_uncertainty_and_other_dropped(self, raw: str):
        r = Report(
            report_id="R001",
            patient_sex_values=[SourcedValue(value=raw, sources=["structured"])],
        )
        assert r.patient_sex_values == []

    def test_male_female_kept(self):
        r = Report(
            report_id="R001",
            patient_sex_values=[
                SourcedValue(value="male", sources=["structured"]),
                SourcedValue(value="female", sources=["db"]),
            ],
        )
        assert _sex(r) == [("male", ["structured"]), ("female", ["db"])]

    def test_case_canonicalised_sources_preserved(self):
        r = Report(
            report_id="R001",
            patient_sex_values=[
                SourcedValue(value="Male", sources=["structured"]),
                SourcedValue(value="FEMALE", sources=["structured"]),
            ],
        )
        assert _sex(r) == [
            ("male", ["structured"]),
            ("female", ["structured"]),
        ]

    def test_mixed_list_keeps_only_valid(self):
        r = Report(
            report_id="R001",
            patient_sex_values=[
                SourcedValue(value="male", sources=["structured"]),
                SourcedValue(value="not known", sources=["structured"]),
            ],
        )
        assert _sex(r) == [("male", ["structured"])]


class TestCountryNormalization:

    def test_uncertainty_dropped(self):
        r = Report(
            report_id="R001",
            country_of_origin_values=[
                SourcedValue(value="not known", sources=["structured"]),
                SourcedValue(value="unknown", sources=["structured"]),
            ],
        )
        assert _country(r) == []

    def test_real_values_preserved_unchanged(self):
        r = Report(
            report_id="R001",
            country_of_origin_values=[
                SourcedValue(value="US", sources=["structured"]),
                SourcedValue(value="Sweden", sources=["structured"]),
            ],
        )
        assert _country(r) == ["US", "Sweden"]


class TestFrozenBundleRoundTrip:

    def test_model_validate_drops_legacy_unknown(self):
        """Loading a legacy bundle with 'unknown' sex must not raise."""
        r = Report.model_validate(
            {
                "report_id": "R001",
                "patient_sex_values": [
                    {"value": "unknown", "sources": ["structured"]},
                    {"value": "female", "sources": ["structured"]},
                ],
                "country_of_origin_values": [
                    {"value": "unknown", "sources": ["structured"]},
                ],
            }
        )
        assert _sex(r) == [("female", ["structured"])]
        assert _country(r) == []


class TestNarrativeMergeUnaffected:

    def test_narrative_other_survives_merge(self):
        """model_copy in the merge bypasses the validator, so 'other' remains."""
        r = merge_narrative_demographics(
            Report(report_id="R001"),
            NarrativeDemographics(
                sexes=[NarrativeSexValue(value=NarrativeSex.OTHER, evidence="…")],
            ),
        )
        assert _sex(r) == [("other", ["narrative_llm"])]


class TestOffendingPairNoLongerPenalised:

    def test_male_vs_not_known_produces_no_sex_inconsistency(self):
        r1 = Report(
            report_id="R001",
            patient_sex_values=[SourcedValue(value="male", sources=["structured"])],
        )
        r2 = Report(
            report_id="R002",
            patient_sex_values=[
                SourcedValue(value="not known", sources=["structured"]),
            ],
        )
        incs = detect_inconsistencies(r1, r2, InconsistencyThresholds())
        assert [i for i in incs if i.feature == "patient_sex"] == []
