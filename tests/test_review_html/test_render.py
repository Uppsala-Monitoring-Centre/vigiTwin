"""Tests for llm_dup_dev.review_html.render."""

from pathlib import Path

from llm_dup_dev.data.offline import FrozenPairBundle, FrozenScopeStats
from llm_dup_dev.models.report import Report
from llm_dup_dev.review_html.compare import build_pair_view_model
from llm_dup_dev.review_html.models import (
    BatchIndexItem,
    ComparisonStatus,
    FieldComparison,
    FreqScopeVM,
    FreqStatsVM,
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
