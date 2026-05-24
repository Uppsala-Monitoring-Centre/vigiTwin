"""Tests for llm_dup_dev.review_html.render."""

from pathlib import Path

from llm_dup_dev.data.offline import FrozenPairBundle, FrozenScopeStats
from llm_dup_dev.models.classification import ClassificationResult
from llm_dup_dev.models.enums import (
    AnchorType,
    DuplicateRelationship,
    IndependenceAssessment,
    InconsistencySeverity,
    NarrativeConsistency,
    ReportingContextLevel,
)
from llm_dup_dev.models.pair_features import (
    AnchorPopulation,
    FeatureContribution,
    InconsistencyCounts,
    Inconsistency,
    PairFeatures,
    ProbabilityEstimate,
    ReportingContext,
)
from llm_dup_dev.models.report import Report
from llm_dup_dev.pipeline.runner import PairResult
from llm_dup_dev.review_html import (
    render_pair_html,
    render_result_html,
    render_results_html,
    ResultRenderInput,
)
from llm_dup_dev.review_html.compare import build_pair_view_model
from llm_dup_dev.review_html.models import (
    BatchIndexItem,
    ComparisonStatus,
    DecisionSummaryVM,
    FieldComparison,
    FreqScopeVM,
    FreqStatsVM,
    InconsistencyCountsVM,
    NarrativeComparison,
    PairViewModel,
)
from llm_dup_dev.review_html.render import (
    _safe_filename,
    render_index,
    render_pair,
    write_batch_html,
    write_pair_html,
)


# ---------------------------------------------------------------------------
# _safe_filename
# ---------------------------------------------------------------------------


class TestSafeFilename:
    def test_simple_id(self):
        assert _safe_filename("R001__R002") == "R001__R002"

    def test_special_chars(self):
        assert _safe_filename("pair/1:2") == "pair_1_2"


# ---------------------------------------------------------------------------
# render_pair
# ---------------------------------------------------------------------------


class TestRenderPair:
    def test_minimal_vm(self):
        """A minimal view model renders valid HTML."""
        vm = PairViewModel(
            pair_id="TEST",
            report_1_id="A",
            report_2_id="B",
            narratives=NarrativeComparison(),
        )
        html = render_pair(vm)
        assert "<!DOCTYPE html>" in html
        assert "TEST" in html
        assert "Report 1" in html

    def test_contains_status_classes(self, report_a, report_b):
        """Generated HTML includes status badge labels for comparison outcomes."""
        vm = build_pair_view_model(report_a, report_b, pair_id="R001__R002")
        html = render_pair(vm)
        assert "match" in html
        # omeprazole is only in report_b → R2 only badge
        assert "R2 only" in html

    def test_demographics_rendered(self, report_a, report_b):
        vm = build_pair_view_model(report_a, report_b, pair_id="R001__R002")
        html = render_pair(vm)
        assert "Demographics" in html
        assert "Sex" in html
        assert "female" in html

    def test_drugs_rendered(self, report_a, report_b):
        vm = build_pair_view_model(report_a, report_b, pair_id="R001__R002")
        html = render_pair(vm)
        assert "pregabalin" in html
        assert "omeprazole" in html

    def test_narratives_rendered(self, report_a, report_b):
        vm = build_pair_view_model(report_a, report_b, pair_id="R001__R002")
        html = render_pair(vm)
        assert "Narratives" in html
        assert "somnolence" in html

    def test_empty_reports_no_demographics_section(self, report_empty):
        """Empty reports should omit the demographics section entirely."""
        r2 = Report(report_id="R_EMPTY2")
        vm = build_pair_view_model(report_empty, r2, pair_id="EMPTY")
        html = render_pair(vm)
        assert "Demographics" not in html

    def test_report_date_uses_neutral_status(self, report_a, report_b):
        """Report date row should carry neutral status (no background colour)."""
        vm = build_pair_view_model(report_a, report_b, pair_id="R001__R002")
        date_field = next(f for f in vm.demographics if f.label == "Report date")
        assert date_field.status == ComparisonStatus.NEUTRAL
        # Neutral badge renders with empty text
        html = render_pair(vm)
        assert 'status-label-neutral' in html

    def test_external_url_rendered_in_header(self, report_a, report_b):
        """External URL and label should appear as a link in the header."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            external_url="https://example.com/pair/123",
            external_url_label="Open in VigiLyze",
        )
        html = render_pair(vm)
        assert "https://example.com/pair/123" in html
        assert "Open in VigiLyze" in html

    def test_no_external_url_no_link(self, report_a, report_b):
        """When external_url is omitted the external-link block is absent."""
        vm = build_pair_view_model(report_a, report_b, pair_id="R001__R002")
        html = render_pair(vm)
        assert '<div class="external-link">' not in html


# ---------------------------------------------------------------------------
# render_index
# ---------------------------------------------------------------------------


class TestRenderIndex:
    def test_basic_index(self):
        items = [
            BatchIndexItem(
                pair_id="P1",
                report_1_id="A",
                report_2_id="B",
                filename="P1.html",
            ),
            BatchIndexItem(
                pair_id="P2",
                report_1_id="C",
                report_2_id="D",
                filename="P2.html",
                final_label="Duplicates",
                l_match=4.5,
            ),
        ]
        html = render_index(items)
        assert "P1.html" in html
        assert "Duplicates" in html
        assert "4.50" in html


# ---------------------------------------------------------------------------
# File writing
# ---------------------------------------------------------------------------


class TestWritePairHtml:
    def test_writes_file(self, tmp_path, report_a, report_b):
        vm = build_pair_view_model(report_a, report_b, pair_id="R001__R002")
        path = write_pair_html(vm, tmp_path)
        assert path.exists()
        assert path.name == "R001__R002.html"
        content = path.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content


# ---------------------------------------------------------------------------
# Frequency statistics section
# ---------------------------------------------------------------------------


_GLOBAL_STATS = FrozenScopeStats(N_total=5000, N_drugs=200, N_AEs=300, N_drugs_AEs=40)
_COUNTRY_STATS = FrozenScopeStats(N_total=1200, N_drugs=50, N_AEs=80, N_drugs_AEs=12)
_COUNTRY_YEAR_STATS = FrozenScopeStats(N_total=300, N_drugs=15, N_AEs=22, N_drugs_AEs=4)


class TestFreqStatsRendering:
    def _bundle(
        self,
        *,
        country: bool = True,
        country_year: bool = True,
    ) -> FrozenPairBundle:
        return FrozenPairBundle(
            pair_id="R001__R002",
            matching_drugs=["pregabalin"],
            matching_AEs=["somnolence"],
            global_stats=_GLOBAL_STATS,
            country_stats=_COUNTRY_STATS if country else None,
            country_year_stats=_COUNTRY_YEAR_STATS if country_year else None,
        )

    def test_fallback_when_no_bundle(self, report_a, report_b):
        """When no frozen_bundle is passed the fallback message is rendered."""
        vm = build_pair_view_model(report_a, report_b, pair_id="R001__R002")
        html = render_pair(vm)
        assert "Frequency statistics not available" in html

    def test_section_heading_always_present(self, report_a, report_b):
        """The Frequency Statistics heading appears regardless of bundle presence."""
        vm_no_bundle = build_pair_view_model(report_a, report_b, pair_id="R001__R002")
        vm_with_bundle = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            frozen_bundle=self._bundle(),
        )
        for vm in (vm_no_bundle, vm_with_bundle):
            assert "Frequency Statistics" in render_pair(vm)

    def test_all_three_scopes_rendered(self, report_a, report_b):
        """All three scope rows appear when the full bundle is provided.

        report_a/b share country "US" and report year 2020, so the rendered
        labels are "US" and "US, 2020" rather than the generic fallbacks.
        """
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            frozen_bundle=self._bundle(),
        )
        html = render_pair(vm)
        assert "Global" in html
        assert "US" in html          # country scope label
        assert "US, 2020" in html    # country-year scope label

    def test_counts_rendered(self, report_a, report_b):
        """N_total and N_drugs_AEs values appear in the output."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            frozen_bundle=self._bundle(),
        )
        html = render_pair(vm)
        assert "5000" in html   # global N_total
        assert "40" in html     # global N_drugs_AEs

    def test_partial_scopes_only_global(self, report_a, report_b):
        """When only global stats are in the bundle, country rows are absent."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            frozen_bundle=self._bundle(country=False, country_year=False),
        )
        html = render_pair(vm)
        assert "Global" in html
        assert "US, 2020" not in html
        assert vm.freq_stats is not None
        assert len(vm.freq_stats.scopes) == 1

    def test_global_and_country_only(self, report_a, report_b):
        """country_year absent → two scopes in freq_stats, country label is 'US'."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            frozen_bundle=self._bundle(country_year=False),
        )
        assert vm.freq_stats is not None
        assert len(vm.freq_stats.scopes) == 2
        assert vm.freq_stats.scopes[0].label == "Global"
        assert vm.freq_stats.scopes[1].label == "US"

    def test_scope_label_falls_back_when_no_country(self):
        """Generic 'Country' / 'Country-year' fallbacks used when reports lack country."""
        r1 = Report(report_id="X1")  # no country_of_origin_values
        r2 = Report(report_id="X2")
        bundle = FrozenPairBundle(
            pair_id="X1__X2",
            matching_drugs=["metformin"],
            matching_AEs=["rash"],
            global_stats=_GLOBAL_STATS,
            country_stats=_COUNTRY_STATS,
            country_year_stats=_COUNTRY_YEAR_STATS,
        )
        vm = build_pair_view_model(r1, r2, pair_id="X1__X2", frozen_bundle=bundle)
        assert vm.freq_stats is not None
        assert vm.freq_stats.scopes[1].label == "Country"
        assert vm.freq_stats.scopes[2].label == "Country-year"

    def test_column_headers_contain_drug_names(self, report_a, report_b):
        """Column headers reference the matching drug names from the bundle."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            frozen_bundle=self._bundle(),
        )
        html = render_pair(vm)
        # The bundle fixture uses matching_drugs=["pregabalin"]
        assert "# reports on pregabalin" in html

    def test_column_headers_contain_ae_names(self, report_a, report_b):
        """Column headers reference the matching AE names from the bundle."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            frozen_bundle=self._bundle(),
        )
        html = render_pair(vm)
        # The bundle fixture uses matching_AEs=["somnolence"]
        assert "# reports with somnolence" in html
        assert "# reports on pregabalin with somnolence" in html

    def test_column_header_multi_item_list(self, report_a, report_b):
        """Two drugs are joined as 'A and B'; three use Oxford-comma form."""
        bundle_two = FrozenPairBundle(
            pair_id="R001__R002",
            matching_drugs=["pregabalin", "sertraline"],
            matching_AEs=["somnolence", "nausea", "dizziness"],
            global_stats=_GLOBAL_STATS,
        )
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            frozen_bundle=bundle_two,
        )
        html = render_pair(vm)
        assert "pregabalin and sertraline" in html
        assert "somnolence, nausea, and dizziness" in html

    def test_matching_lists_stored_on_vm(self, report_a, report_b):
        """matching_drugs and matching_aes are accessible on the view model."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            frozen_bundle=self._bundle(),
        )
        assert vm.freq_stats is not None
        assert vm.freq_stats.matching_drugs == ["pregabalin"]
        assert vm.freq_stats.matching_aes == ["somnolence"]

    def test_no_fallback_message_when_bundle_present(self, report_a, report_b):
        """Fallback message must not appear when valid stats are provided."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            frozen_bundle=self._bundle(),
        )
        html = render_pair(vm)
        assert "Frequency statistics not available" not in html

    def test_freq_stats_vm_direct_injection(self):
        """FreqStatsVM can be set directly on PairViewModel (no bundle needed)."""
        vm = PairViewModel(
            pair_id="X",
            report_1_id="A",
            report_2_id="B",
            freq_stats=FreqStatsVM(
                scopes=[FreqScopeVM(label="Global", N_total=100, N_drugs=10, N_AEs=20, N_drugs_AEs=5)]
            ),
        )
        html = render_pair(vm)
        assert "Global" in html
        assert "100" in html


class TestWriteBatchHtml:
    def test_writes_all_files_and_index(self, tmp_path, report_a, report_b, report_c):
        vm1 = build_pair_view_model(report_a, report_b, pair_id="R001__R002")
        vm2 = build_pair_view_model(report_a, report_c, pair_id="R001__R003")
        out = write_batch_html([vm1, vm2], tmp_path)
        assert (out / "R001__R002.html").exists()
        assert (out / "R001__R003.html").exists()
        assert (out / "index.html").exists()

        index_content = (out / "index.html").read_text(encoding="utf-8")
        assert "R001__R002.html" in index_content
        assert "R001__R003.html" in index_content

    def test_no_index_flag(self, tmp_path, report_a, report_b):
        vm = build_pair_view_model(report_a, report_b, pair_id="R001__R002")
        out = write_batch_html([vm], tmp_path, write_index=False)
        assert (out / "R001__R002.html").exists()
        assert not (out / "index.html").exists()


# ---------------------------------------------------------------------------
# Shared fixtures for prediction/evaluation tests
# ---------------------------------------------------------------------------


def _make_features(pair_id: str = "R001__R002") -> PairFeatures:
    """Build a minimal PairFeatures for testing."""
    return PairFeatures(
        pair_id=pair_id,
        reporting_context=ReportingContext(
            level=ReportingContextLevel.COUNTRY, country="US", year=2020
        ),
        matching_drugs=["pregabalin"],
        matching_AEs=["somnolence"],
        anchor=AnchorPopulation(
            N_drugs=200, N_AEs=300, N_drugs_AEs=40,
            type=AnchorType.DRUGS_AES, N_anchor=40,
        ),
        probability=ProbabilityEstimate(
            p_joint=1.5e-5, expected_collisions=0.075,
            feature_contributions=[
                FeatureContribution(
                    feature="patient_sex", p_f=0.5, method="domain_size",
                    domain_size=2, description="patient_sex: D_f=2",
                ),
                FeatureContribution(
                    feature="drug_set", p_f=0.01, method="leave_pair_out",
                    description="Drug set: (5-2)/(200-2)",
                ),
            ],
        ),
        L_match=3.8,
        inconsistencies=[
            Inconsistency(
                feature="weight",
                severity=InconsistencySeverity.MODERATE,
                difference=2.0, units="kg",
                description="Weight differs by 2.0 kg",
            ),
        ],
        inconsistency_counts=InconsistencyCounts(moderate=1, strong=0, decisive=0),
        narrative_consistency=NarrativeConsistency.CONSISTENT,
    )


def _make_classification(pair_id: str = "R001__R002") -> ClassificationResult:
    """Build a minimal ClassificationResult for testing."""
    return ClassificationResult(
        pair_id=pair_id,
        independence_assessment=IndependenceAssessment.NON_INDEPENDENT,
        duplicate_relationship=DuplicateRelationship.DUPLICATES,
        explanation="High L_match with minor inconsistencies.",
    )


def _make_pair_result(pair_id: str = "R001__R002") -> PairResult:
    """Build a PairResult for testing."""
    return PairResult(
        pair_id=pair_id,
        features=_make_features(pair_id),
        classification=_make_classification(pair_id),
    )


# ---------------------------------------------------------------------------
# Comparison-only rendering (no prediction data)
# ---------------------------------------------------------------------------


class TestComparisonOnlyRendering:
    """Tests for rendering created report pairs without pipeline data."""

    def test_renders_without_features_or_classification(self, report_a, report_b):
        """Comparison-only mode renders valid HTML without prediction sections."""
        vm = build_pair_view_model(report_a, report_b, pair_id="R001__R002")
        html = render_pair(vm)
        assert "<!DOCTYPE html>" in html
        assert "R001__R002" in html
        assert "Demographics" in html
        assert "Drugs" in html
        # Should NOT contain prediction-specific sections
        assert "Decision Summary" not in html
        assert "Debug" not in html

    def test_comparison_only_file_write(self, tmp_path, report_a, report_b):
        """render_pair_html works without features/classification."""
        path = render_pair_html(
            report_a, report_b, pair_id="R001__R002",
            output_dir=tmp_path,
        )
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "Demographics" in content
        assert "Decision Summary" not in content

    def test_comparison_only_batch_index(self, tmp_path, report_a, report_b):
        """Batch index renders cleanly with comparison-only pairs."""
        vm1 = build_pair_view_model(report_a, report_b, pair_id="R001__R002")
        vm2 = build_pair_view_model(report_a, report_b, pair_id="R001__R003")
        out = write_batch_html([vm1, vm2], tmp_path)
        index_html = (out / "index.html").read_text(encoding="utf-8")
        assert "R001__R002" in index_html
        assert "R001__R003" in index_html
        # No label/L_match columns when all are None
        assert "Label" not in index_html


# ---------------------------------------------------------------------------
# Prediction/evaluation rendering
# ---------------------------------------------------------------------------


class TestPredictionRendering:
    """Tests for rendering evaluated pipeline outputs (PairResult)."""

    def test_decision_summary_rendered(self, report_a, report_b):
        """Decision summary panel appears when classification is present."""
        features = _make_features()
        classification = _make_classification()
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=features, classification=classification,
        )
        html = render_pair(vm)
        assert "Decision Summary" in html
        assert "Duplicates" in html
        assert "High L_match" in html
        assert "1 moderate" in html

    def test_decision_summary_absent_without_classification(self, report_a, report_b):
        """Decision summary is absent when only features are provided."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=_make_features(),
        )
        assert vm.decision_summary is None
        html = render_pair(vm)
        assert "Decision Summary" not in html

    def test_anchor_details_rendered(self, report_a, report_b):
        """Extended anchor details appear in the evidence section."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=_make_features(),
        )
        html = render_pair(vm)
        assert "Anchor Details" in html
        assert "200" in html   # N_drugs
        assert "300" in html   # N_AEs

    def test_probability_details_rendered(self, report_a, report_b):
        """p_joint and probability details appear."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=_make_features(),
        )
        html = render_pair(vm)
        assert "Probability Details" in html
        assert "1.5000e-05" in html  # p_joint

    def test_inconsistency_counts_rendered(self, report_a, report_b):
        """Inconsistency counts summary appears."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=_make_features(),
        )
        html = render_pair(vm)
        assert "1 moderate" in html
        assert "0 strong" in html
        assert "1 total" in html

    def test_render_result_html(self, tmp_path, report_a, report_b):
        """render_result_html creates a file with prediction sections."""
        result = _make_pair_result()
        path = render_result_html(
            result, report_a, report_b,
            output_dir=tmp_path,
        )
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "Decision Summary" in content
        assert "Evidence Summary" in content
        assert "Duplicates" in content

    def test_render_result_html_with_debug(self, tmp_path, report_a, report_b):
        """Debug sections appear when include_debug=True."""
        result = _make_pair_result()
        path = render_result_html(
            result, report_a, report_b,
            include_debug=True,
            output_dir=tmp_path,
        )
        content = path.read_text(encoding="utf-8")
        assert "Debug" in content
        assert "PairFeatures" in content
        assert "ClassificationResult" in content

    def test_render_result_html_no_debug_by_default(self, tmp_path, report_a, report_b):
        """Debug sections are absent by default."""
        result = _make_pair_result()
        path = render_result_html(
            result, report_a, report_b,
            output_dir=tmp_path,
        )
        content = path.read_text(encoding="utf-8")
        assert "Debug / Raw Data" not in content

    def test_render_results_html_batch(self, tmp_path, report_a, report_b):
        """render_results_html writes files and an index."""
        items = [
            ResultRenderInput(
                result=_make_pair_result("R001__R002"),
                report_1=report_a, report_2=report_b,
            ),
            ResultRenderInput(
                result=_make_pair_result("R001__R003"),
                report_1=report_a, report_2=report_b,
            ),
        ]
        out = render_results_html(items, output_dir=tmp_path)
        assert (out / "R001__R002.html").exists()
        assert (out / "R001__R003.html").exists()
        assert (out / "index.html").exists()

    def test_batch_index_shows_evaluation_columns(self, report_a, report_b):
        """Batch index includes label, L_match, inconsistencies, anchor, context, features."""
        features = _make_features()
        classification = _make_classification()
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=features, classification=classification,
        )
        from llm_dup_dev.review_html.compare import build_batch_index_item
        item = build_batch_index_item(vm, "R001__R002.html")
        assert item.final_label == "Duplicates"
        assert item.l_match == 3.8
        # Inconsistency items carry succinct text with severity
        assert len(item.inconsistency_items) == 1
        assert "68 kg vs 70 kg" in item.inconsistency_items[0].text
        assert item.inconsistency_items[0].severity == "moderate"
        assert item.narrative_consistency == "consistent"
        # Anchor: drugs_AEs anchor type combines matching drugs and AEs
        # (descriptor only — count is in anchor_n)
        assert item.anchor_label == "pregabalin + somnolence"
        assert item.anchor_n == 40
        # Context: country + year extracted from the reporting context
        assert item.context_label == "US 2020"
        # Matching features shows the actual values behind each contribution
        assert item.matching_features is not None
        assert "female" in item.matching_features
        assert "pregabalin" in item.matching_features

    def test_batch_index_renders_evaluation_columns(self, report_a, report_b):
        """Rendered index HTML includes the new column set."""
        features = _make_features()
        classification = _make_classification()
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=features, classification=classification,
        )
        from llm_dup_dev.review_html.compare import build_batch_index_item
        item = build_batch_index_item(vm, "R001__R002.html")
        html = render_index([item])
        assert "Duplicates" in html
        assert "3.80" in html
        # Inconsistency items rendered with colour-coded severity
        assert "68 kg vs 70 kg" in html
        assert 'class="inc-moderate"' in html
        # New columns are present
        assert "Anchor" in html
        assert "Context" in html
        assert "Additional matching features" in html
        assert "pregabalin + somnolence" in html
        assert "Anchor N" in html
        # Anchor N cell now shows "<count> reports" plus a red chip with the
        # anchor's negative log-rarity contribution to L_match.
        assert "40 reports" in html
        # -log10(40 * 39 / 2) ≈ -2.89, rendered as "(-2.9)" with the
        # contrib-neg-s class.
        assert 'class="contrib contrib-neg-s">(-2.9)' in html
        # Removed columns are no longer rendered
        assert "<th>Report 1</th>" not in html
        assert "<th>Report 2</th>" not in html
        assert "<th>Relationship</th>" not in html
        assert "<th>Narrative</th>" not in html

    def test_key_drugs_bolded_in_index(self, report_a, report_b):
        """Suspected/interacting drugs are bolded; concomitant and role=None are not."""
        from llm_dup_dev.models.enums import DrugRole
        from llm_dup_dev.models.report import Drug

        # Give report_a drugs with explicit roles
        r_a = report_a.model_copy(deep=True)
        r_a.drugs = [
            Drug(substance="pregabalin", sources=["s"], role=DrugRole.SUSPECTED),
            Drug(substance="sertraline", sources=["s"], role=DrugRole.CONCOMITANT),
        ]
        r_b = report_b.model_copy(deep=True)
        r_b.drugs = [
            Drug(substance="pregabalin", sources=["s"], role=DrugRole.SUSPECTED),
            Drug(substance="omeprazole", sources=["s"]),  # role=None
        ]
        features = _make_features()
        classification = _make_classification()
        vm = build_pair_view_model(
            r_a, r_b, pair_id="R001__R002",
            features=features, classification=classification,
        )
        from llm_dup_dev.review_html.compare import build_batch_index_item
        item = build_batch_index_item(vm, "R001__R002.html")

        # Anchor label: pregabalin should be bolded (suspected), somnolence is an AE (plain)
        assert "<strong>pregabalin</strong>" in item.anchor_label
        assert "<strong>somnolence" not in item.anchor_label

        # Matching features: pregabalin bolded
        assert "<strong>pregabalin</strong>" in item.matching_features

        # Rendered HTML: bold tags survive rendering
        html = render_index([item])
        assert "<strong>pregabalin</strong>" in html

    def test_role_none_drugs_not_bolded(self, report_a, report_b):
        """Drugs with role=None are not bolded (matching inconsistency logic)."""
        features = _make_features()
        classification = _make_classification()
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=features, classification=classification,
        )
        from llm_dup_dev.review_html.compare import build_batch_index_item
        item = build_batch_index_item(vm, "R001__R002.html")
        # No drugs have explicit suspected/interacting role → no bolding
        assert "<strong>" not in (item.anchor_label or "")
        assert "<strong>" not in (item.matching_features or "")

    def test_relationship_label_in_decision_summary(self, report_a, report_b):
        """Relationship label appears when classification is Otherwise_related."""
        classification = ClassificationResult(
            pair_id="R001__R002",
            independence_assessment=IndependenceAssessment.NON_INDEPENDENT,
            duplicate_relationship=DuplicateRelationship.OTHERWISE_RELATED,
            explanation="Related but not the same case.",
        )
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=_make_features(), classification=classification,
        )
        html = render_pair(vm)
        assert "Otherwise_related" in html


# ---------------------------------------------------------------------------
# Graceful handling of missing optional fields
# ---------------------------------------------------------------------------


class TestMissingOptionalFields:
    """Verify the renderer handles missing/None fields gracefully."""

    def test_empty_reports_render(self):
        """Two empty reports render without error."""
        r1 = Report(report_id="EMPTY1")
        r2 = Report(report_id="EMPTY2")
        vm = build_pair_view_model(r1, r2, pair_id="EMPTY")
        html = render_pair(vm)
        assert "<!DOCTYPE html>" in html
        assert "EMPTY" in html

    def test_features_without_classification(self, report_a, report_b):
        """Features alone render evidence but no classification/decision."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=_make_features(),
        )
        html = render_pair(vm)
        assert "Evidence Summary" in html
        assert "<h2>Classification</h2>" not in html
        assert "Decision Summary" not in html

    def test_classification_without_features(self, report_a, report_b):
        """Classification alone renders decision summary without evidence."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            classification=_make_classification(),
        )
        html = render_pair(vm)
        assert "Decision Summary" in html
        assert "Evidence Summary" not in html
        # Decision summary should show label but no L_match
        assert "Duplicates" in html

    def test_decision_summary_no_l_match_without_features(self, report_a, report_b):
        """Decision summary omits L_match when features are absent."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            classification=_make_classification(),
        )
        assert vm.decision_summary is not None
        assert vm.decision_summary.l_match is None

    def test_batch_index_mixed_modes(self, report_a, report_b):
        """Index handles a mix of comparison-only and evaluated pairs."""
        vm_plain = build_pair_view_model(
            report_a, report_b, pair_id="PLAIN",
        )
        vm_eval = build_pair_view_model(
            report_a, report_b, pair_id="EVAL",
            features=_make_features("EVAL"),
            classification=_make_classification("EVAL"),
        )
        from llm_dup_dev.review_html.compare import build_batch_index_item
        items = [
            build_batch_index_item(vm_plain, "PLAIN.html"),
            build_batch_index_item(vm_eval, "EVAL.html"),
        ]
        html = render_index(items)
        assert "PLAIN" in html
        assert "EVAL" in html
        assert "Duplicates" in html
        assert "--" in html  # plain pair's missing label

    def test_index_no_extra_columns_when_all_none(self, report_a, report_b):
        """Index omits evaluation columns when no pair has them."""
        vm = build_pair_view_model(report_a, report_b, pair_id="PLAIN")
        from llm_dup_dev.review_html.compare import build_batch_index_item
        items = [build_batch_index_item(vm, "PLAIN.html")]
        html = render_index(items)
        assert "Inconsistencies" not in html
        assert "Narrative" not in html


# ---------------------------------------------------------------------------
# Human annotation rendering
# ---------------------------------------------------------------------------


def _make_annotation(pair_id: str, assessment: str) -> dict:
    return {"pair_id": pair_id, "duplicates_assessment": assessment}


class TestAnnotationRendering:
    """Tests for human annotation display in pair pages and batch index."""

    # -- AnnotationVM construction --

    def test_annotation_vm_agrees(self, report_a, report_b):
        """Agreement flag is True when pipeline and annotation match."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            classification=_make_classification(),
            annotation=_make_annotation("R001__R002", "duplicates"),
        )
        assert vm.annotation is not None
        assert vm.annotation.normalised_label == "Duplicates"
        assert vm.annotation.agrees_with_pipeline is True

    def test_annotation_vm_disagrees(self, report_a, report_b):
        """Agreement flag is False when pipeline and annotation differ."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            classification=_make_classification(),  # Duplicates
            annotation=_make_annotation("R001__R002", "unrelated"),
        )
        assert vm.annotation is not None
        assert vm.annotation.normalised_label == "Unrelated"
        assert vm.annotation.agrees_with_pipeline is False

    def test_annotation_vm_no_pipeline(self, report_a, report_b):
        """Agreement flag is None when no pipeline classification exists."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            annotation=_make_annotation("R001__R002", "otherwise_related"),
        )
        assert vm.annotation is not None
        assert vm.annotation.normalised_label == "Otherwise_related"
        assert vm.annotation.agrees_with_pipeline is None

    def test_annotation_vm_abstain(self, report_a, report_b):
        """Abstain annotation is normalised to 'Abstain'; agreement is None."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            classification=_make_classification(),
            annotation=_make_annotation("R001__R002", "abstain"),
        )
        assert vm.annotation is not None
        assert vm.annotation.normalised_label == "Abstain"
        assert vm.annotation.agrees_with_pipeline is None

    def test_annotation_vm_agreement_none_when_pipeline_abstains(
        self, report_a, report_b
    ):
        """When the pipeline returns Abstain, no ✓/✗ chip is shown — the
        pipeline took no position, so the human verdict is neither a
        match nor a mismatch. (Regression: previously the asymmetric
        short-circuit only suppressed the chip when the annotation side
        was Abstain.)"""
        abstain_classification = ClassificationResult(
            pair_id="R001__R002",
            independence_assessment=IndependenceAssessment.NON_INDEPENDENT,
            duplicate_relationship=DuplicateRelationship.ABSTAIN,
            explanation="Shared-source override; identity indeterminate.",
        )
        for human_label in ("duplicates", "otherwise_related", "unrelated"):
            vm = build_pair_view_model(
                report_a, report_b, pair_id="R001__R002",
                classification=abstain_classification,
                annotation=_make_annotation("R001__R002", human_label),
            )
            assert vm.annotation is not None
            assert vm.annotation.agrees_with_pipeline is None, (
                f"Expected None for pipeline=Abstain + human={human_label}, "
                f"got {vm.annotation.agrees_with_pipeline}"
            )

    def test_no_annotation_vm_without_annotation(self, report_a, report_b):
        """annotation is None on the view model when no annotation supplied."""
        vm = build_pair_view_model(report_a, report_b, pair_id="R001__R002")
        assert vm.annotation is None

    # -- Pair HTML rendering --

    def test_annotation_shown_in_header_with_classification(self, report_a, report_b):
        """Annotation badge appears in the header when classification is present."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            classification=_make_classification(),
            annotation=_make_annotation("R001__R002", "duplicates"),
        )
        html = render_pair(vm)
        assert "Human:" in html
        assert "✓ agree" in html or "&#10003;" in html

    def test_annotation_shown_in_classification_section(self, report_a, report_b):
        """Annotation row appears inside the Classification section."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            classification=_make_classification(),
            annotation=_make_annotation("R001__R002", "duplicates"),
        )
        html = render_pair(vm)
        assert "Human annotation" in html

    def test_annotation_disagree_shows_cross(self, report_a, report_b):
        """Disagreement indicator is rendered when labels differ."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            classification=_make_classification(),   # Duplicates
            annotation=_make_annotation("R001__R002", "unrelated"),
        )
        html = render_pair(vm)
        assert "&#10007;" in html   # ✗ disagree mark

    def test_annotation_standalone_section_without_classification(self, report_a, report_b):
        """A standalone Human Annotation section is rendered when there is no classification."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            annotation=_make_annotation("R001__R002", "otherwise_related"),
        )
        html = render_pair(vm)
        assert "Human Annotation" in html
        assert "Otherwise_related" in html
        # No agreement indicator when there is no pipeline label
        assert "&#10003;" not in html   # no ✓ agree mark
        assert "&#10007;" not in html   # no ✗ disagree mark

    def test_no_annotation_section_when_absent(self, report_a, report_b):
        """No annotation markup is rendered when annotation is not supplied."""
        vm = build_pair_view_model(report_a, report_b, pair_id="R001__R002")
        html = render_pair(vm)
        assert "Human Annotation" not in html
        assert "Human annotation" not in html

    # -- Batch index --

    def test_batch_index_item_carries_annotation(self, report_a, report_b):
        """BatchIndexItem captures annotation label and agreement."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            classification=_make_classification(),
            annotation=_make_annotation("R001__R002", "duplicates"),
        )
        from llm_dup_dev.review_html.compare import build_batch_index_item
        item = build_batch_index_item(vm, "R001__R002.html")
        assert item.annotation_label == "Duplicates"
        assert item.annotation_agrees is True

    def test_batch_index_renders_annotation_column(self, report_a, report_b):
        """Rendered index includes the Human annotation column when data is present."""
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            classification=_make_classification(),
            annotation=_make_annotation("R001__R002", "duplicates"),
        )
        from llm_dup_dev.review_html.compare import build_batch_index_item
        item = build_batch_index_item(vm, "R001__R002.html")
        html = render_index([item])
        assert "Human annotation" in html
        assert "Duplicates" in html
        assert "&#10003;" in html   # agree checkmark

    def test_batch_index_no_annotation_column_when_absent(self, report_a, report_b):
        """Annotation column is omitted from the index when no items have one."""
        vm = build_pair_view_model(report_a, report_b, pair_id="R001__R002")
        from llm_dup_dev.review_html.compare import build_batch_index_item
        item = build_batch_index_item(vm, "R001__R002.html")
        html = render_index([item])
        assert "Human annotation" not in html

    def test_render_result_html_with_annotation(self, tmp_path, report_a, report_b):
        """render_result_html accepts and renders annotation."""
        result = _make_pair_result()
        path = render_result_html(
            result, report_a, report_b,
            annotation=_make_annotation("R001__R002", "duplicates"),
            output_dir=tmp_path,
        )
        content = path.read_text(encoding="utf-8")
        assert "Human annotation" in content
        assert "&#10003;" in content


# ---------------------------------------------------------------------------
# AE clinical-picture rendering
# ---------------------------------------------------------------------------


class TestAEClinicalPictureRendering:
    """Suppressed AE findings render with the badge but don't inflate counts."""

    def _features_with_ae(self) -> PairFeatures:
        from llm_dup_dev.llm.tasks.ae_clinical_picture import (
            SOURCE_LABEL,
            SUPPRESSED_BY_DECISIVE_GLOBAL,
        )
        from llm_dup_dev.models.enums import AEClinicalPictureStatus
        from llm_dup_dev.models.pair_features import AEClinicalPictureCounts

        feats = _make_features()
        feats.inconsistencies = list(feats.inconsistencies) + [
            Inconsistency(
                feature="ae_clinical_picture:global_picture_mismatch",
                severity=InconsistencySeverity.DECISIVE,
                source=SOURCE_LABEL,
                kind="global_picture_mismatch",
                rationale="completely different pictures",
                terms=["headache", "myocardial infarction"],
            ),
            Inconsistency(
                feature="ae_clinical_picture:unmatched_cluster",
                severity=InconsistencySeverity.MODERATE,
                source=SOURCE_LABEL,
                kind="unmatched_cluster",
                rationale="extra cardiac cluster",
                terms=["myocardial infarction"],
                suppressed=True,
                suppressed_by=SUPPRESSED_BY_DECISIVE_GLOBAL,
            ),
        ]
        feats.ae_clinical_picture_status = AEClinicalPictureStatus.ASSESSED
        feats.ae_clinical_picture_counts = AEClinicalPictureCounts(n_decisive=1)
        feats.inconsistency_counts = InconsistencyCounts(
            moderate=1, decisive=1
        )
        return feats

    def test_assessed_status_panel_rendered(self, report_a, report_b):
        feats = self._features_with_ae()
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=feats,
            classification=_make_classification(),
        )
        html = render_pair(vm)
        assert "AE clinical-picture" in html
        assert "ae-cp-status-assessed" in html

    def test_suppressed_finding_rendered_with_badge(self, report_a, report_b):
        feats = self._features_with_ae()
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=feats,
            classification=_make_classification(),
        )
        html = render_pair(vm)
        # Suppressed row carries the de-emphasis class and the badge
        assert "inc-suppressed" in html
        assert "suppressed-badge" in html
        # Both rationales are visible
        assert "completely different pictures" in html
        assert "extra cardiac cluster" in html
        # Suppressed-by reason is human-readable
        assert "decisive global mismatch" in html

    def test_suppressed_excluded_from_index_inconsistency_items(
        self, report_a, report_b
    ):
        from llm_dup_dev.review_html.compare import build_batch_index_item

        feats = self._features_with_ae()
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=feats,
            classification=_make_classification(),
        )
        item = build_batch_index_item(vm, "R001__R002.html")
        # Index items list the kept decisive global, not the suppressed cluster
        ae_items = [
            ii
            for ii in item.inconsistency_items
            if ii.text.startswith("AE picture:")
        ]
        assert len(ae_items) == 1
        assert ae_items[0].severity == InconsistencySeverity.DECISIVE
        assert "myocardial infarction" in ae_items[0].text

    def test_not_assessed_status_panel_rendered(self, report_a, report_b):
        from llm_dup_dev.models.enums import AEClinicalPictureStatus

        feats = _make_features()
        feats.ae_clinical_picture_status = AEClinicalPictureStatus.NOT_ASSESSED
        feats.ae_clinical_picture_reason = "no_llm_client"
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=feats,
            classification=_make_classification(),
        )
        html = render_pair(vm)
        assert "ae-cp-status-not_assessed" in html
        assert "no_llm_client" in html


# ---------------------------------------------------------------------------
# Source/case verdict surface on the batch index
# ---------------------------------------------------------------------------


def _features_with_source_case(verdict, **kwargs):
    """Build features carrying a SourceCaseAssessment with the given verdict."""
    from llm_dup_dev.models.narrative_source_case import SourceCaseAssessment

    feats = _make_features()
    feats.source_case_assessment = SourceCaseAssessment(
        verdict=verdict, **kwargs,
    )
    return feats


class TestIndexSourceCaseSurface:
    def test_short_label_populated_for_same_source_same_case(
        self, report_a, report_b,
    ):
        from llm_dup_dev.models.narrative_source_case import (
            SourceCaseVerdict,
            SourceIdentifierType,
        )
        from llm_dup_dev.review_html.compare import build_batch_index_item

        feats = _features_with_source_case(
            SourceCaseVerdict.SAME_SOURCE_SAME_CASE,
            shared_source_identifier="10.1000/example",
            shared_source_identifier_type=SourceIdentifierType.DOI,
            reasoning="matching case ID",
        )
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=feats, classification=_make_classification(),
        )
        item = build_batch_index_item(vm, "R001__R002.html")

        assert item.source_case is not None
        assert item.source_case.verdict == "same_source_same_case"
        assert item.source_case.short_label == (
            "Multi-pt. source, same individual"
        )
        assert item.source_case.shared_source_identifier == "10.1000/example"
        assert item.source_case.shared_source_identifier_type == "doi"

    def test_no_decisive_signal_filtered_out_on_index(
        self, report_a, report_b,
    ):
        from llm_dup_dev.models.narrative_source_case import SourceCaseVerdict
        from llm_dup_dev.review_html.compare import build_batch_index_item

        feats = _features_with_source_case(SourceCaseVerdict.NO_DECISIVE_SIGNAL)
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=feats, classification=_make_classification(),
        )
        item = build_batch_index_item(vm, "R001__R002.html")

        assert item.source_case is None
        # The pair page still receives the verdict — the filter is only
        # applied on the index side.
        assert vm.decision_summary is not None
        assert vm.decision_summary.source_case is not None
        assert vm.decision_summary.source_case.verdict == "no_decisive_signal"

    def test_not_assessed_filtered_upstream(self, report_a, report_b):
        from llm_dup_dev.models.narrative_source_case import SourceCaseVerdict
        from llm_dup_dev.review_html.compare import build_batch_index_item

        feats = _features_with_source_case(SourceCaseVerdict.NOT_ASSESSED)
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=feats, classification=_make_classification(),
        )
        item = build_batch_index_item(vm, "R001__R002.html")

        assert item.source_case is None
        assert vm.decision_summary is not None
        assert vm.decision_summary.source_case is None

    def test_renamed_uncomparable_case_short_label(self, report_a, report_b):
        from llm_dup_dev.models.narrative_source_case import (
            SourceCaseVerdict,
            SourceIdentifierType,
        )
        from llm_dup_dev.review_html.compare import build_batch_index_item

        feats = _features_with_source_case(
            SourceCaseVerdict.SAME_SOURCE_UNCOMPARABLE_CASE,
            shared_source_identifier="NCT01234567",
            shared_source_identifier_type=SourceIdentifierType.TRIAL_REGISTRATION,
        )
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=feats, classification=_make_classification(),
        )
        item = build_batch_index_item(vm, "R001__R002.html")

        assert item.source_case is not None
        assert item.source_case.verdict == "same_source_uncomparable_case"
        assert item.source_case.short_label == "Multi-pt. source, individual ?"

    def test_render_index_surfaces_badge_and_identifier(
        self, report_a, report_b,
    ):
        from llm_dup_dev.models.narrative_source_case import (
            SourceCaseVerdict,
            SourceIdentifierType,
        )
        from llm_dup_dev.review_html.compare import build_batch_index_item

        feats = _features_with_source_case(
            SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT,
            shared_source_identifier="10.1000/single",
            shared_source_identifier_type=SourceIdentifierType.DOI,
        )
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=feats, classification=_make_classification(),
        )
        item = build_batch_index_item(vm, "R001__R002.html")
        html = render_index([item])

        assert 'class="verdict-badge"' in html
        assert "Same single-pt. source" in html
        # Identifier and its type appear inside a <code> tag.
        assert "doi=10.1000/single" in html
        # Raw verdict surfaces as a tooltip on the badge.
        assert 'title="same_source_single_patient"' in html

    def test_render_index_omits_badge_when_no_source_case(
        self, report_a, report_b,
    ):
        from llm_dup_dev.review_html.compare import build_batch_index_item

        # No source_case_assessment attached -> badge absent.
        vm = build_pair_view_model(
            report_a, report_b, pair_id="R001__R002",
            features=_make_features(),
            classification=_make_classification(),
        )
        item = build_batch_index_item(vm, "R001__R002.html")
        html = render_index([item])

        assert item.source_case is None
        assert 'class="verdict-badge"' not in html
