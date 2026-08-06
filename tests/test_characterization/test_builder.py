"""The reports seam, missing-report handling, and per-set block assembly."""

from __future__ import annotations

import re
from datetime import datetime

import pytest

from llm_dup_dev.characterization.builder import (
    UNLABELLED_KEY,
    build_characterization_report,
    build_reference_set_characteristics,
)

from .conftest import DictReportLoader, make_reference_set, make_report


class TestInputSeam:
    def test_mapping_and_loader_give_identical_results(self, simple_reports):
        reference_set = make_reference_set([("R001", "R002"), ("R003", "R004")])

        from_mapping = build_reference_set_characteristics(
            reference_set, simple_reports
        )
        from_loader = build_reference_set_characteristics(
            reference_set, DictReportLoader(dict(simple_reports))
        )

        assert from_mapping.model_dump_json() == from_loader.model_dump_json()

    def test_each_report_is_fetched_once_however_many_pairs_name_it(self):
        reports = {rid: make_report(rid) for rid in ("R1", "R2", "R3")}
        loader = DictReportLoader(reports)

        build_reference_set_characteristics(
            make_reference_set([("R1", "R2"), ("R1", "R3"), ("R2", "R3")]), loader
        )

        assert sorted(loader.calls) == ["R1", "R2", "R3"]

    def test_reports_shared_across_sets_are_fetched_once_for_the_run(self):
        reports = {rid: make_report(rid) for rid in ("R1", "R2", "R3")}
        loader = DictReportLoader(reports)

        build_characterization_report(
            [
                make_reference_set([("R1", "R2")], set_id="a"),
                make_reference_set([("R1", "R3")], set_id="b"),
            ],
            loader,
        )

        assert sorted(loader.calls) == ["R1", "R2", "R3"]


class TestMissingReports:
    def test_raises_once_listing_every_missing_id(self):
        reports = {"R1": make_report("R1")}
        reference_set = make_reference_set(
            [("R1", "R2"), ("R3", "R4"), ("R5", "R6")]
        )

        with pytest.raises(KeyError) as excinfo:
            build_reference_set_characteristics(reference_set, reports)

        message = str(excinfo.value)
        assert "5 referenced report id(s)" in message
        for report_id in ("R2", "R3", "R4", "R5"):
            assert report_id in message

    def test_long_missing_lists_are_truncated(self):
        reference_set = make_reference_set(
            [(f"R{i}", f"R{i + 100}") for i in range(10)]
        )

        with pytest.raises(KeyError) as excinfo:
            build_reference_set_characteristics(reference_set, {})

        assert "..." in str(excinfo.value)

    def test_skip_records_the_gap_and_shrinks_the_denominators(self):
        reports = {
            "R1": make_report("R1"),
            "R2": make_report("R2"),
            "R3": make_report("R3"),
        }
        reference_set = make_reference_set([("R1", "R2"), ("R3", "R4")])

        result = build_reference_set_characteristics(
            reference_set, reports, on_missing_report="skip"
        )

        assert result.reports.n_reports_unavailable == 1
        assert result.reports.n_unique_reports == 3
        assert result.reports.report_year.denominator == 3
        # The pair touching the missing report is dropped from the pair section.
        assert result.pairs.n_pairs == 2
        assert result.pairs.n_pairs_evaluable == 1

    def test_unknown_mode_is_rejected(self):
        with pytest.raises(ValueError, match="on_missing_report"):
            build_reference_set_characteristics(
                make_reference_set([("R1", "R2")]),
                {},
                on_missing_report="ignore",  # type: ignore[arg-type]
            )


class TestSetBlocks:
    def test_one_block_per_set_in_input_order(self, simple_reports):
        report = build_characterization_report(
            [
                make_reference_set([("R001", "R002")], set_id="second"),
                make_reference_set([("R003", "R004")], set_id="first"),
            ],
            simple_reports,
        )

        assert [block.set_id for block in report.sets] == ["second", "first"]

    def test_no_pooled_block_is_emitted(self, simple_reports):
        report = build_characterization_report(
            [
                make_reference_set([("R001", "R002")], set_id="a"),
                make_reference_set([("R003", "R004")], set_id="b"),
            ],
            simple_reports,
        )

        assert len(report.sets) == 2

    def test_set_metadata_travels_with_its_block(self, simple_reports):
        reference_set = make_reference_set(
            [("R001", "R002")],
            set_id="precision_primary",
            label="Screening candidates",
            provenance="manual_annotation",
        )
        report = build_characterization_report([reference_set], simple_reports)
        block = report.sets[0]

        assert block.set_id == "precision_primary"
        assert block.role == "precision"
        assert block.label == "Screening candidates"
        assert block.provenance == "manual_annotation"
        assert block.screening_mode == "observed"

    def test_reference_label_counts_for_a_precision_set(self, simple_reports):
        reference_set = make_reference_set(
            [("R001", "R002"), ("R003", "R004")], reference_label="non_duplicate"
        )
        result = build_reference_set_characteristics(reference_set, simple_reports)

        assert result.reference_label_counts == {"non_duplicate": 2}

    def test_recall_set_pairs_count_as_unlabelled(self, simple_reports):
        reference_set = make_reference_set(
            [("R001", "R002")], set_id="recall_gold", role="recall"
        )
        result = build_reference_set_characteristics(reference_set, simple_reports)

        assert result.reference_label_counts == {UNLABELLED_KEY: 1}

    def test_distributions_are_not_split_by_reference_label(self, simple_reports):
        """A single table per field, over every report in the set."""
        reference_set = make_reference_set([("R001", "R002"), ("R003", "R004")])
        result = build_reference_set_characteristics(reference_set, simple_reports)

        assert result.reports.country_of_origin.denominator == 4


class TestReportEnvelope:
    def test_provenance_fields_are_populated(self, simple_reports):
        report = build_characterization_report(
            [make_reference_set([("R001", "R002")])], simple_reports
        )

        assert report.schema_version == "1.0"
        assert isinstance(report.created_at, datetime)
        assert report.created_at.tzinfo is not None
        assert re.fullmatch(r"[0-9a-f]{12}", report.characterization_id)
        assert report.dev_package.name == "llm-dup-dev"

    def test_explicit_values_win_over_the_defaults(self, simple_reports):
        moment = datetime.fromisoformat("2026-03-01T12:00:00+00:00")
        report = build_characterization_report(
            [make_reference_set([("R001", "R002")])],
            simple_reports,
            created_at=moment,
            characterization_id="fixed12345678",
            dataset_id="ds-1",
            notes="post-merge reports",
        )

        assert report.created_at == moment
        assert report.characterization_id == "fixed12345678"
        assert report.dataset_id == "ds-1"
        assert report.notes == "post-merge reports"

    def test_the_policy_record_travels_with_the_numbers(self, simple_reports):
        report = build_characterization_report(
            [make_reference_set([("R001", "R002")])], simple_reports, top_n=5
        )

        assert report.policy.source_preference == ["structured"]
        assert report.policy.top_n == 5
        assert report.policy.drug_roles_counted == ["suspected", "interacting"]
        assert report.policy.age_band_width_years == 10

    def test_an_empty_bundle_is_rejected(self, simple_reports):
        """A run that described nothing must not look like one that succeeded."""
        with pytest.raises(ValueError, match="at least one reference set"):
            build_characterization_report([], simple_reports)
