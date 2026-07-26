"""Tests for llm_dup_dev.review_html.saved_run.

All reports and artifacts use synthetic data only.
"""

import json

import pytest

from llm_dup_dev.data.offline import FrozenNarrativeDemographics
from llm_dup_dev.models.narrative_demographics import (
    NarrativeAgeValue,
    NarrativeDemographics,
)
from llm_dup_dev.models.report_pair import ReportPair
from llm_dup_dev.pipeline.export import (
    write_classifications,
    write_pair_features,
    write_pair_results,
)
from llm_dup_dev.review_html import (
    SavedRunSummary,
    load_saved_run,
    render_saved_run,
)

from tests.test_review_html.test_render import _make_pair_result

# Re-export shared synthetic report fixtures (report_a = R001, report_b = R002).
from tests.test_review_html.conftest import report_a, report_b  # noqa: F401

PAIR_ID = "R001__R002"


def _write_reports(tmp_path, *reports):
    path = tmp_path / "reports.json"
    path.write_text(
        json.dumps({r.report_id: r.model_dump(mode="json") for r in reports})
    )
    return path


def _write_pairs(tmp_path, *pairs):
    path = tmp_path / "pairs.json"
    path.write_text(json.dumps([p.model_dump(mode="json") for p in pairs]))
    return path


def _pair(pair_id=PAIR_ID, r1="R001", r2="R002"):
    return ReportPair(pair_id=pair_id, report_1_id=r1, report_2_id=r2)


class TestSourceSelection:
    def test_prefers_pair_results(self, tmp_path, report_a, report_b):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = tmp_path / "pair_results.json"
        write_pair_results([_make_pair_result(PAIR_ID)], pr_path)

        items, summary = load_saved_run(
            reports_path=reports,
            pairs_path=pairs,
            pair_results_path=pr_path,
        )

        assert isinstance(summary, SavedRunSummary)
        assert summary.source == "pair_results"
        assert summary.n_pairs == 1
        assert summary.n_loaded == 1
        assert summary.skipped == []
        assert len(items) == 1
        assert items[0].result.pair_id == PAIR_ID
        assert items[0].report_1.report_id == "R001"
        assert items[0].report_2.report_id == "R002"

    def test_split_fallback(self, tmp_path, report_a, report_b):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        result = _make_pair_result(PAIR_ID)
        features = tmp_path / "features.json"
        classifications = tmp_path / "classifications.json"
        write_pair_features([result], features)
        write_classifications([result], classifications)

        demographics = NarrativeDemographics(
            ages=[NarrativeAgeValue(value=54, evidence="54-year-old")],
            notes="r1",
        )
        demo_path = tmp_path / "narrative_demographics.json"
        demo_path.write_text(
            FrozenNarrativeDemographics(
                extractions={"R001": demographics}
            ).model_dump_json()
        )

        items, summary = load_saved_run(
            reports_path=reports,
            pairs_path=pairs,
            features_path=features,
            classifications_path=classifications,
            narrative_demographics_path=demo_path,
        )

        assert summary.source == "split"
        assert summary.n_loaded == 1
        loaded = items[0].result
        # source_case_assessment comes from the features on the split path.
        assert (
            loaded.source_case_assessment
            is loaded.features.source_case_assessment
        )
        # narrative demographics come from the per-report bundle.
        assert loaded.narrative_demographics_1 == demographics
        assert loaded.narrative_demographics_2 is None

    def test_pair_results_wins_when_both_present(
        self, tmp_path, report_a, report_b
    ):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        result = _make_pair_result(PAIR_ID)
        pr_path = tmp_path / "pair_results.json"
        features = tmp_path / "features.json"
        classifications = tmp_path / "classifications.json"
        write_pair_results([result], pr_path)
        write_pair_features([result], features)
        write_classifications([result], classifications)

        _, summary = load_saved_run(
            reports_path=reports,
            pairs_path=pairs,
            pair_results_path=pr_path,
            features_path=features,
            classifications_path=classifications,
        )
        assert summary.source == "pair_results"

    def test_missing_pair_results_file_falls_back_to_split(
        self, tmp_path, report_a, report_b
    ):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        result = _make_pair_result(PAIR_ID)
        features = tmp_path / "features.json"
        classifications = tmp_path / "classifications.json"
        write_pair_features([result], features)
        write_classifications([result], classifications)

        _, summary = load_saved_run(
            reports_path=reports,
            pairs_path=pairs,
            pair_results_path=tmp_path / "does_not_exist.json",
            features_path=features,
            classifications_path=classifications,
        )
        assert summary.source == "split"
        assert summary.n_loaded == 1


class TestSkipAndStrict:
    def test_missing_report_is_skipped(self, tmp_path, report_a):
        # report_b (R002) is intentionally absent from reports.json.
        reports = _write_reports(tmp_path, report_a)
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = tmp_path / "pair_results.json"
        write_pair_results([_make_pair_result(PAIR_ID)], pr_path)

        items, summary = load_saved_run(
            reports_path=reports,
            pairs_path=pairs,
            pair_results_path=pr_path,
        )
        assert items == []
        assert summary.n_loaded == 0
        assert len(summary.skipped) == 1
        assert "R002" in summary.skipped[0]

    def test_strict_raises_on_missing_report(self, tmp_path, report_a):
        reports = _write_reports(tmp_path, report_a)
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = tmp_path / "pair_results.json"
        write_pair_results([_make_pair_result(PAIR_ID)], pr_path)

        with pytest.raises(RuntimeError):
            load_saved_run(
                reports_path=reports,
                pairs_path=pairs,
                pair_results_path=pr_path,
                strict=True,
            )

    def test_missing_split_entry_is_skipped(self, tmp_path, report_a, report_b):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        # features/classifications for a *different* pair id -> lookup misses.
        other = _make_pair_result("X__Y")
        features = tmp_path / "features.json"
        classifications = tmp_path / "classifications.json"
        write_pair_features([other], features)
        write_classifications([other], classifications)

        _, summary = load_saved_run(
            reports_path=reports,
            pairs_path=pairs,
            features_path=features,
            classifications_path=classifications,
        )
        assert summary.n_loaded == 0
        assert "missing features" in summary.skipped[0]


class TestExternalUrlAndValidation:
    def test_external_url_builder_is_applied(
        self, tmp_path, report_a, report_b
    ):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = tmp_path / "pair_results.json"
        write_pair_results([_make_pair_result(PAIR_ID)], pr_path)

        calls = []

        def builder(r1_id, r2_id):
            calls.append((r1_id, r2_id))
            return f"https://example.test/{r1_id}-{r2_id}"

        items, _ = load_saved_run(
            reports_path=reports,
            pairs_path=pairs,
            pair_results_path=pr_path,
            external_url_builder=builder,
            external_url_label="View externally",
        )
        assert calls == [("R001", "R002")]
        assert items[0].external_url == "https://example.test/R001-R002"
        assert items[0].external_url_label == "View externally"

    def test_no_builder_means_no_url(self, tmp_path, report_a, report_b):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = tmp_path / "pair_results.json"
        write_pair_results([_make_pair_result(PAIR_ID)], pr_path)

        items, _ = load_saved_run(
            reports_path=reports,
            pairs_path=pairs,
            pair_results_path=pr_path,
        )
        assert items[0].external_url is None

    def test_no_source_raises_value_error(self, tmp_path, report_a, report_b):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        with pytest.raises(ValueError):
            load_saved_run(reports_path=reports, pairs_path=pairs)


class TestRenderSavedRun:
    def test_renders_and_reports_summary(self, tmp_path, report_a, report_b):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = tmp_path / "pair_results.json"
        write_pair_results([_make_pair_result(PAIR_ID)], pr_path)
        out_dir = tmp_path / "html_review"

        summary = render_saved_run(
            output_dir=out_dir,
            reports_path=reports,
            pairs_path=pairs,
            pair_results_path=pr_path,
        )

        assert summary.source == "pair_results"
        assert summary.output_dir == out_dir
        assert summary.n_rendered == 1
        assert (out_dir / "index.html").exists()
        pair_pages = [
            p for p in out_dir.glob("*.html") if p.name != "index.html"
        ]
        assert len(pair_pages) == 1
