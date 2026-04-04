"""Tests for llm_dup_dev.review_html.render."""

from pathlib import Path

from llm_dup_dev.models.report import Report
from llm_dup_dev.review_html.compare import build_pair_view_model
from llm_dup_dev.review_html.models import (
    BatchIndexItem,
    ComparisonStatus,
    FieldComparison,
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
