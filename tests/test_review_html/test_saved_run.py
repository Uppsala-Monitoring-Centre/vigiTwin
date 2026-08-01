"""Tests for llm_dup_dev.review_html.saved_run.

All reports and artifacts use synthetic data only.
"""

import json

import pytest

from llm_dup_dev.data.offline import (
    FrozenNarrativeDemographics,
    FrozenPairBundle,
    FrozenScopeStats,
)
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
    SavedRunSkip,
    SavedRunSummary,
    SkipReason,
    load_saved_run,
    render_saved_run,
)

from tests.test_review_html.test_render import _make_pair_result

# Re-export shared synthetic report fixtures (report_a = R001, report_b = R002).
from tests.test_review_html.conftest import report_a, report_b  # noqa: F401

PAIR_ID = "R001__R002"


# ---------------------------------------------------------------------------
# Artifact writers (both list- and mapping-shaped where supported)
# ---------------------------------------------------------------------------


def _write_reports(tmp_path, *reports):
    """Mapping-shaped reports.json (review-CLI shape)."""
    path = tmp_path / "reports.json"
    path.write_text(
        json.dumps({r.report_id: r.model_dump(mode="json") for r in reports})
    )
    return path


def _write_reports_list(tmp_path, *reports):
    """List-shaped reports.json (secure-runner shape)."""
    path = tmp_path / "reports_list.json"
    path.write_text(json.dumps([r.model_dump(mode="json") for r in reports]))
    return path


def _write_pairs(tmp_path, *pairs):
    path = tmp_path / "pairs.json"
    path.write_text(json.dumps([p.model_dump(mode="json") for p in pairs]))
    return path


def _pair(pair_id=PAIR_ID, r1="R001", r2="R002"):
    return ReportPair(pair_id=pair_id, report_1_id=r1, report_2_id=r2)


def _bundle(pair_id=PAIR_ID):
    return FrozenPairBundle(
        pair_id=pair_id,
        matching_drugs=["pregabalin"],
        matching_AEs=["somnolence"],
        global_stats=FrozenScopeStats(
            N_total=1000, N_drugs=100, N_AEs=100, N_drugs_AEs=50
        ),
    )


def _demographics():
    return NarrativeDemographics(
        ages=[NarrativeAgeValue(value=54, evidence="54-year-old")],
        notes="r1",
    )


def _write_pair_results(tmp_path, *results):
    path = tmp_path / "pair_results.json"
    write_pair_results(list(results), path)
    return path


def _write_split(tmp_path, *results):
    features = tmp_path / "features.json"
    classifications = tmp_path / "classifications.json"
    write_pair_features(list(results), features)
    write_classifications(list(results), classifications)
    return features, classifications


# ---------------------------------------------------------------------------
# Source selection & precedence
# ---------------------------------------------------------------------------


class TestSourceSelection:
    def test_prefers_pair_results(self, tmp_path, report_a, report_b):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = _write_pair_results(tmp_path, _make_pair_result(PAIR_ID))

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
        features, classifications = _write_split(
            tmp_path, _make_pair_result(PAIR_ID)
        )

        demographics = _demographics()
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
        assert (
            loaded.source_case_assessment
            is loaded.features.source_case_assessment
        )
        assert loaded.narrative_demographics_1 == demographics
        assert loaded.narrative_demographics_2 is None

    def test_pair_results_wins_when_both_present(
        self, tmp_path, report_a, report_b
    ):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        result = _make_pair_result(PAIR_ID)
        pr_path = _write_pair_results(tmp_path, result)
        features, classifications = _write_split(tmp_path, result)

        _, summary = load_saved_run(
            reports_path=reports,
            pairs_path=pairs,
            pair_results_path=pr_path,
            features_path=features,
            classifications_path=classifications,
        )
        assert summary.source == "pair_results"

    def test_missing_pair_results_file_raises(
        self, tmp_path, report_a, report_b
    ):
        """An explicitly supplied pair_results_path must not silently fall back."""
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        features, classifications = _write_split(
            tmp_path, _make_pair_result(PAIR_ID)
        )

        with pytest.raises(FileNotFoundError):
            load_saved_run(
                reports_path=reports,
                pairs_path=pairs,
                pair_results_path=tmp_path / "does_not_exist.json",
                features_path=features,
                classifications_path=classifications,
            )

    def test_malformed_pair_results_propagates(
        self, tmp_path, report_a, report_b
    ):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = tmp_path / "pair_results.json"
        pr_path.write_text(json.dumps([1, 2, 3]))  # top-level not an object

        with pytest.raises(ValueError, match="top-level JSON must be an object"):
            load_saved_run(
                reports_path=reports,
                pairs_path=pairs,
                pair_results_path=pr_path,
            )

    def test_split_requires_both_files(self, tmp_path, report_a, report_b):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        features, _ = _write_split(tmp_path, _make_pair_result(PAIR_ID))

        with pytest.raises(ValueError, match="both features_path"):
            load_saved_run(
                reports_path=reports,
                pairs_path=pairs,
                features_path=features,
            )


# ---------------------------------------------------------------------------
# Flexible input shapes with hard validation
# ---------------------------------------------------------------------------


class TestInputShapes:
    def test_reports_accepted_as_list(self, tmp_path, report_a, report_b):
        reports = _write_reports_list(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = _write_pair_results(tmp_path, _make_pair_result(PAIR_ID))

        _, summary = load_saved_run(
            reports_path=reports, pairs_path=pairs, pair_results_path=pr_path
        )
        assert summary.n_loaded == 1

    def test_reports_duplicate_id_raises(self, tmp_path, report_a):
        path = tmp_path / "reports.json"
        dup = report_a.model_dump(mode="json")
        path.write_text(json.dumps([dup, dup]))
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = _write_pair_results(tmp_path, _make_pair_result(PAIR_ID))

        with pytest.raises(ValueError, match="duplicate report_id"):
            load_saved_run(
                reports_path=path, pairs_path=pairs, pair_results_path=pr_path
            )

    def test_reports_mapping_key_conflict_raises(self, tmp_path, report_a):
        path = tmp_path / "reports.json"
        # key "WRONG" != model.report_id "R001"
        path.write_text(json.dumps({"WRONG": report_a.model_dump(mode="json")}))
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = _write_pair_results(tmp_path, _make_pair_result(PAIR_ID))

        with pytest.raises(ValueError, match="does not match report_id"):
            load_saved_run(
                reports_path=path, pairs_path=pairs, pair_results_path=pr_path
            )

    def test_reports_bad_top_level_raises(self, tmp_path):
        path = tmp_path / "reports.json"
        path.write_text(json.dumps(123))
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = _write_pair_results(tmp_path, _make_pair_result(PAIR_ID))

        with pytest.raises(ValueError, match="must be a list or an object"):
            load_saved_run(
                reports_path=path, pairs_path=pairs, pair_results_path=pr_path
            )

    def test_bundles_accepted_as_list_and_mapping(
        self, tmp_path, report_a, report_b
    ):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = _write_pair_results(tmp_path, _make_pair_result(PAIR_ID))
        bundle = _bundle()

        list_path = tmp_path / "bundles_list.json"
        list_path.write_text(json.dumps([bundle.model_dump(mode="json")]))
        map_path = tmp_path / "bundles_map.json"
        map_path.write_text(
            json.dumps({PAIR_ID: bundle.model_dump(mode="json")})
        )

        for bundles_path in (list_path, map_path):
            items, _ = load_saved_run(
                reports_path=reports,
                pairs_path=pairs,
                pair_results_path=pr_path,
                bundles_path=bundles_path,
            )
            assert items[0].frozen_bundle is not None
            assert items[0].frozen_bundle.pair_id == PAIR_ID

    def test_bundles_duplicate_id_raises(self, tmp_path, report_a, report_b):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = _write_pair_results(tmp_path, _make_pair_result(PAIR_ID))
        dup = _bundle().model_dump(mode="json")
        bundles_path = tmp_path / "bundles.json"
        bundles_path.write_text(json.dumps([dup, dup]))

        with pytest.raises(ValueError, match="duplicate pair_id"):
            load_saved_run(
                reports_path=reports,
                pairs_path=pairs,
                pair_results_path=pr_path,
                bundles_path=bundles_path,
            )

    @pytest.mark.parametrize("wrapper", [True, False])
    def test_demographics_wrapper_and_plain(
        self, tmp_path, report_a, report_b, wrapper
    ):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        features, classifications = _write_split(
            tmp_path, _make_pair_result(PAIR_ID)
        )
        demo = _demographics()
        demo_path = tmp_path / "demographics.json"
        if wrapper:
            demo_path.write_text(
                FrozenNarrativeDemographics(
                    extractions={"R001": demo}
                ).model_dump_json()
            )
        else:
            demo_path.write_text(
                json.dumps({"R001": demo.model_dump(mode="json")})
            )

        items, _ = load_saved_run(
            reports_path=reports,
            pairs_path=pairs,
            features_path=features,
            classifications_path=classifications,
            narrative_demographics_path=demo_path,
        )
        assert items[0].result.narrative_demographics_1 == demo

    def test_annotations_list_and_mapping(
        self, tmp_path, report_a, report_b
    ):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = _write_pair_results(tmp_path, _make_pair_result(PAIR_ID))

        list_path = tmp_path / "ann_list.json"
        list_path.write_text(
            json.dumps([{"pair_id": PAIR_ID, "label": "dup"}])
        )
        map_path = tmp_path / "ann_map.json"
        map_path.write_text(json.dumps({PAIR_ID: {"label": "dup"}}))

        for ann_path in (list_path, map_path):
            items, _ = load_saved_run(
                reports_path=reports,
                pairs_path=pairs,
                pair_results_path=pr_path,
                annotations_path=ann_path,
            )
            assert items[0].annotation is not None
            assert items[0].annotation["label"] == "dup"

    def test_annotations_list_item_missing_pair_id_raises(
        self, tmp_path, report_a, report_b
    ):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = _write_pair_results(tmp_path, _make_pair_result(PAIR_ID))
        ann_path = tmp_path / "ann.json"
        ann_path.write_text(json.dumps([{"label": "dup"}]))

        with pytest.raises(ValueError, match="missing 'pair_id'"):
            load_saved_run(
                reports_path=reports,
                pairs_path=pairs,
                pair_results_path=pr_path,
                annotations_path=ann_path,
            )

    def test_annotations_duplicate_raises(
        self, tmp_path, report_a, report_b
    ):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = _write_pair_results(tmp_path, _make_pair_result(PAIR_ID))
        ann_path = tmp_path / "ann.json"
        ann_path.write_text(
            json.dumps([{"pair_id": PAIR_ID}, {"pair_id": PAIR_ID}])
        )

        with pytest.raises(ValueError, match="duplicate pair_id"):
            load_saved_run(
                reports_path=reports,
                pairs_path=pairs,
                pair_results_path=pr_path,
                annotations_path=ann_path,
            )

    def test_annotations_key_embedded_mismatch_raises(
        self, tmp_path, report_a, report_b
    ):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = _write_pair_results(tmp_path, _make_pair_result(PAIR_ID))
        ann_path = tmp_path / "ann.json"
        ann_path.write_text(json.dumps({PAIR_ID: {"pair_id": "OTHER"}}))

        with pytest.raises(ValueError, match="does not match embedded pair_id"):
            load_saved_run(
                reports_path=reports,
                pairs_path=pairs,
                pair_results_path=pr_path,
                annotations_path=ann_path,
            )


# ---------------------------------------------------------------------------
# Skip records & strict default
# ---------------------------------------------------------------------------


class TestSkipAndStrict:
    def test_missing_report_skipped_non_strict(self, tmp_path, report_a):
        # report_b (R002) is intentionally absent.
        reports = _write_reports(tmp_path, report_a)
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = _write_pair_results(tmp_path, _make_pair_result(PAIR_ID))

        items, summary = load_saved_run(
            reports_path=reports,
            pairs_path=pairs,
            pair_results_path=pr_path,
            strict=False,
        )
        assert items == []
        assert summary.n_loaded == 0
        assert summary.skipped == [
            SavedRunSkip(
                pair_id=PAIR_ID,
                reason=SkipReason.MISSING_REPORT_2,
                detail="missing report R002",
            )
        ]

    def test_strict_is_the_default(self, tmp_path, report_a):
        reports = _write_reports(tmp_path, report_a)
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = _write_pair_results(tmp_path, _make_pair_result(PAIR_ID))

        # No strict kwarg -> library default is strict=True -> raises.
        with pytest.raises(RuntimeError, match="missing_report_2"):
            load_saved_run(
                reports_path=reports,
                pairs_path=pairs,
                pair_results_path=pr_path,
            )

    def test_missing_split_entry_skipped(self, tmp_path, report_a, report_b):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        # features/classifications for a *different* pair id -> lookup misses.
        features, classifications = _write_split(
            tmp_path, _make_pair_result("X__Y")
        )

        _, summary = load_saved_run(
            reports_path=reports,
            pairs_path=pairs,
            features_path=features,
            classifications_path=classifications,
            strict=False,
        )
        assert summary.n_loaded == 0
        assert len(summary.skipped) == 1
        assert summary.skipped[0].reason is SkipReason.MISSING_FEATURES

    def test_missing_pair_results_entry_skipped(
        self, tmp_path, report_a, report_b
    ):
        reports = _write_reports(tmp_path, report_a, report_b)
        # pair not present in the (empty) pair_results artifact.
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = _write_pair_results(tmp_path)  # no results

        _, summary = load_saved_run(
            reports_path=reports,
            pairs_path=pairs,
            pair_results_path=pr_path,
            strict=False,
        )
        assert summary.skipped[0].reason is SkipReason.MISSING_RESULT


# ---------------------------------------------------------------------------
# External URL handling & validation
# ---------------------------------------------------------------------------


class TestExternalUrl:
    def test_external_url_builder_is_applied(
        self, tmp_path, report_a, report_b
    ):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = _write_pair_results(tmp_path, _make_pair_result(PAIR_ID))

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
        pr_path = _write_pair_results(tmp_path, _make_pair_result(PAIR_ID))

        items, _ = load_saved_run(
            reports_path=reports,
            pairs_path=pairs,
            pair_results_path=pr_path,
        )
        assert items[0].external_url is None

    @pytest.mark.parametrize("strict", [True, False])
    def test_builder_failure_renders_link_less(
        self, tmp_path, report_a, report_b, strict
    ):
        """A builder that raises never drops the pair; it renders link-less."""
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = _write_pair_results(tmp_path, _make_pair_result(PAIR_ID))

        def boom(r1_id, r2_id):
            raise RuntimeError("url service down")

        items, summary = load_saved_run(
            reports_path=reports,
            pairs_path=pairs,
            pair_results_path=pr_path,
            external_url_builder=boom,
            strict=strict,
        )
        assert summary.n_loaded == 1
        assert summary.skipped == []
        assert items[0].external_url is None

    def test_no_source_raises_value_error(self, tmp_path, report_a, report_b):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        with pytest.raises(ValueError, match="pair_results_path"):
            load_saved_run(reports_path=reports, pairs_path=pairs)


# ---------------------------------------------------------------------------
# render_saved_run
# ---------------------------------------------------------------------------


class TestRenderSavedRun:
    def test_renders_and_reports_summary(self, tmp_path, report_a, report_b):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = _write_pair_results(tmp_path, _make_pair_result(PAIR_ID))
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


# ---------------------------------------------------------------------------
# Canonical pair identity — legacy artifacts
# ---------------------------------------------------------------------------


def _write_legacy_pairs(tmp_path):
    """A pairs.json written before pair ids were canonicalised."""
    path = tmp_path / "legacy_pairs.json"
    path.write_text(
        json.dumps(
            [
                {
                    "pair_id": "R002__R001",
                    "report_1_id": "R002",
                    "report_2_id": "R001",
                }
            ]
        )
    )
    return path


class TestCanonicalPairIdentity:
    """A pre-canonicalisation run must fail loudly, or be opted into.

    Rewriting a stale pair_id in place would break its join against the
    annotations and predictions still keyed on the old id, so the loader
    refuses by default and the escape hatch renders the run as written.
    """

    def test_legacy_pairs_artifact_is_rejected_by_default(
        self, tmp_path, report_a, report_b
    ):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_legacy_pairs(tmp_path)
        pr_path = _write_pair_results(tmp_path, _make_pair_result("R002__R001"))
        with pytest.raises(ValueError, match="not in canonical order"):
            load_saved_run(
                reports_path=reports,
                pairs_path=pairs,
                pair_results_path=pr_path,
            )

    def test_escape_hatch_renders_a_legacy_run(
        self, tmp_path, report_a, report_b
    ):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_legacy_pairs(tmp_path)
        pr_path = _write_pair_results(tmp_path, _make_pair_result("R002__R001"))
        items, summary = load_saved_run(
            reports_path=reports,
            pairs_path=pairs,
            pair_results_path=pr_path,
            allow_noncanonical_pairs=True,
        )
        assert summary.n_loaded == 1
        assert items[0].result.pair_id == "R002__R001"

    def test_legacy_keyed_results_artifact_is_rejected(
        self, tmp_path, report_a, report_b
    ):
        """The pairs are canonical but the results file predates them.

        PairResult carries no report ids, so a stale key would silently fail to
        join and the pair would just be reported missing.
        """
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = _write_pair_results(tmp_path, _make_pair_result("R002__R001"))
        with pytest.raises(ValueError, match="predates canonical pair identity"):
            load_saved_run(
                reports_path=reports,
                pairs_path=pairs,
                pair_results_path=pr_path,
            )

    def test_legacy_keyed_split_artifacts_are_rejected(
        self, tmp_path, report_a, report_b
    ):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        features, classifications = _write_split(
            tmp_path, _make_pair_result("R002__R001")
        )
        with pytest.raises(ValueError, match="predates canonical pair identity"):
            load_saved_run(
                reports_path=reports,
                pairs_path=pairs,
                features_path=features,
                classifications_path=classifications,
            )

    def test_escape_hatch_skips_the_key_check_too(
        self, tmp_path, report_a, report_b
    ):
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_legacy_pairs(tmp_path)
        pr_path = _write_pair_results(tmp_path, _make_pair_result("R002__R001"))
        _, summary = load_saved_run(
            reports_path=reports,
            pairs_path=pairs,
            pair_results_path=pr_path,
            allow_noncanonical_pairs=True,
        )
        assert summary.n_loaded == 1

    def test_free_form_artifact_keys_are_left_alone(
        self, tmp_path, report_a, report_b
    ):
        """Only the canonical form carries the ordering promise.

        A key that is not two report ids joined by the separator says nothing
        about orientation, so the key check must not object to it. pair_id is
        free-form on PairResult by design — only ReportPair is constrained.
        """
        reports = _write_reports(tmp_path, report_a, report_b)
        pairs = _write_pairs(tmp_path, _pair())
        pr_path = _write_pair_results(
            tmp_path,
            _make_pair_result(PAIR_ID),
            _make_pair_result("8047905-7215592"),
        )
        _, summary = load_saved_run(
            reports_path=reports,
            pairs_path=pairs,
            pair_results_path=pr_path,
        )
        assert summary.n_loaded == 1
