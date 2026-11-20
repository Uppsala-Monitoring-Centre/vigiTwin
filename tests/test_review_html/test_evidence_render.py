"""Tests for evidence HTML rendering.

Assertions are on the HTML string, following the convention of
``test_render.py``. The print rules are asserted because the format's stated
purpose includes being turned into a PDF, and a stylesheet nobody checks is a
stylesheet that silently regresses.
"""

from __future__ import annotations

import pytest

from llm_dup_dev.review_html.evidence.build import (
    ExamplePresentation,
    build_evidence_case,
    ReferenceInfo,
    SCLEAssessment,
    SCLEState,
    build_evidence_case,
)
from llm_dup_dev.review_html.evidence.models import (
    EvidencePageVM,
    PresentationMode,
)
from llm_dup_dev.review_html.render import (
    EVIDENCE_DOCUMENT_FILENAME,
    evidence_filename,
    render_evidence_page,
    write_evidence_batch_html,
    write_evidence_html,
)
from llm_dup_dev.models.report import AdverseEvent, Drug
from llm_dup_dev.models.pair_features import Inconsistency, InconsistencyCounts
from llm_dup_dev.models.classification import InconsistencyPenalty, PenaltyTerm
from llm_dup_dev.models.enums import (
    IndependenceAssessment,
    IndependenceReason,
    InconsistencySeverity,
)

from tests.test_review_html.test_evidence_build import (
    _classification,
    _features,
    _report,
)


def _case(**kwargs):
    """One rendered case. Both reports are overridable, for the tests that
    need particular reported values rather than a matching pair."""
    r1 = kwargs.pop(
        "report_1",
        _report(
            "R001",
            drugs=[Drug(substance="topiramate", sources=["s"])],
            aes=[AdverseEvent(pt_name="cleft lip", sources=["s"])],
        ),
    )
    r2 = kwargs.pop(
        "report_2",
        _report(
            "R002",
            drugs=[Drug(substance="topiramate", sources=["s"])],
            aes=[AdverseEvent(pt_name="cleft lip", sources=["s"])],
        ),
    )
    return build_evidence_case(
        report_1=r1,
        report_2=r2,
        pair_id="R001__R002",
        features=kwargs.pop(
            "features",
            _features(matching_drugs=["topiramate"], matching_AEs=["cleft lip"]),
        ),
        classification=kwargs.pop("classification", _classification()),
        **kwargs,
    )


def _html(**kwargs) -> str:
    page = EvidencePageVM(title="Evidence review", cases=[_case(**kwargs)])
    return render_evidence_page(page)


def _print_block(html: str) -> str:
    """The whole ``@media print`` block, found by matching its brace.

    This was a fixed 1200-character window from ``@media print``, which the
    block has since outgrown — every rule added past the window weakened the
    assertion without failing it. Keyed on ``@media print{`` rather than
    ``@media print`` so a comment mentioning the block by name is not mistaken
    for the block itself.
    """
    start = html.index("@media print{")
    opened = html.index("{", start)
    depth = 0
    for index in range(opened, len(html)):
        if html[index] == "{":
            depth += 1
        elif html[index] == "}":
            depth -= 1
            if depth == 0:
                return html[opened : index + 1]
    raise AssertionError("the @media print block is never closed")


class TestEvidenceColouring:
    """The three colour classes reach the HTML."""

    def test_matching_values_are_green(self):
        assert "value-match" in _html()

    def test_inconsistency_classes_are_emitted(self):
        html = _html(
            features=_features(
                matching_drugs=["topiramate"],
                inconsistencies=[
                    Inconsistency(
                        feature="ae_clinical_picture:unmatched_burden:r1_to_r2",
                        severity=InconsistencySeverity.MINOR,
                        terms=["cleft lip"],
                        source="rule_ae_clinical_picture",
                    )
                ],
                inconsistency_counts=InconsistencyCounts(minor=1),
            )
        )
        assert "value-mismatch" in html
        assert "ae-mismatch" in html

    def test_neutral_class_is_emitted(self):
        assert "value-neutral" in _html()

    def test_colour_key_is_shown(self):
        html = _html()
        assert "Green: counted for the pair" in html
        assert "Red: counted against it" in html


class TestSections:
    """The document carries the sections the format promises."""

    def test_the_sections_name_the_two_questions(self):
        """Three sections: the evidence, then each question in order.

        The headings match how vigiTwin is described elsewhere — an
        independence assessment, then a relationship assessment — rather than
        naming the material each section happens to contain.
        """
        html = _html()
        assert "1. Report comparison" in html
        assert "2. Independence assessment" in html
        assert "3. Relationship assessment" in html
        assert html.index("2. Independence assessment") < html.index(
            "3. Relationship assessment"
        )

    def test_the_verdicts_come_before_the_evidence(self):
        """The panel states both verdicts above section 1, not after it.

        A reader of a supplement meets the conclusion first and reads the
        evidence to check it, rather than assembling the conclusion from
        three sections of tables. The decision table that used to close
        section 3 is gone; this is what replaced it.
        """
        html = _html()
        assert html.index("Assessment at a glance") < html.index(
            "<h3>1. Report comparison</h3>"
        )
        assert "<h4>vigiTwin classification</h4>" not in html
        assert '<table class="decision">' not in html

    def test_four_outcome_tiles(self):
        html = _html(
            reference=ReferenceInfo(
                reference_label="duplicate",
                set_label="FDA Gold",
                screening_retrieved=True,
            )
        )
        assert "FDA Gold label" in html
        assert "vigiMatch screening" in html
        assert "vigiTwin classification" in html
        assert "SCLE of reference label" in html

    def test_the_agreement_line_sits_with_the_tiles_it_compares(self):
        """One level up from where it was: under the row, not under the
        decision tiles.

        It compares two of the four verdicts the tile row states, so a reader
        meets it while that row is still what they are looking at. Behind the
        decision tiles it arrived a stage later than the thing it describes.
        """
        html = " ".join(
            _html(
                reference=ReferenceInfo(
                    reference_label="non_duplicate", set_label="FDA Gold"
                )
            ).split()
        )
        assert "vigiTwin disagrees with the reference label." in html
        assert html.index("SCLE of reference label") < html.index(
            "vigiTwin disagrees with the reference label."
        )
        assert html.index("vigiTwin disagrees with the reference label.") < (
            html.index('<div class="decisions">')
        )

    def test_no_sentence_where_no_matching_feature_contributed(self):
        """An absence the panel already states as a value.

        "Additional matching features: None" is in the panel, in the
        countable form the rest of the page uses. The paragraph under the
        table said the same thing as a claim about what was not found.
        """
        html = " ".join(_html().split())
        assert "No additional matching features" not in html
        assert "<dt>Additional matching features</dt>" in html

    def test_frequency_headings_name_the_sets(self):
        from llm_dup_dev.data.offline import FrozenPairBundle, FrozenScopeStats

        html = _html(
            frozen_bundle=FrozenPairBundle(
                pair_id="R001__R002",
                matching_drugs=["topiramate"],
                matching_AEs=["cleft lip"],
                global_stats=FrozenScopeStats(
                    N_total=1000, N_drugs=5, N_AEs=2, N_drugs_AEs=2
                ),
            )
        )
        assert "Shared drug set" in html
        assert "Shared AE set" in html
        assert "Both sets" in html
        assert "Counts include the two reports under review" in html

    def test_uses_vigitwin_not_generic_terms(self):
        html = _html()
        assert "vigiTwin" in html
        assert "the pipeline" not in html
        assert "the adjudicator" not in html

    def test_no_debug_or_raw_json_sections(self):
        html = _html()
        for forbidden in (
            "debug-section",
            "Raw Data",
            "PairFeatures",
            "ClassificationResult",
        ):
            assert forbidden not in html


class TestSCLEStates:
    """Four states, four distinguishable renderings."""

    def test_not_entered(self):
        assert "To be completed" in _html()

    def test_not_assessed(self):
        html = _html(
            reference=ReferenceInfo(
                scle=SCLEAssessment(state=SCLEState.NOT_ASSESSED)
            )
        )
        assert "Not assessed" in html

    def test_assessed(self):
        html = _html(
            reference=ReferenceInfo(
                scle=SCLEAssessment(
                    state=SCLEState.ASSESSED, issue="possibly_incorrect"
                )
            )
        )
        assert "Possibly incorrect" in html

    def test_inconclusive(self):
        html = _html(
            reference=ReferenceInfo(
                scle=SCLEAssessment(
                    state=SCLEState.INCONCLUSIVE, issue="genuinely_ambiguous"
                )
            )
        )
        assert "Genuinely ambiguous" in html


class TestNarrativeBlocks:
    """A human interpretation replaces the generated summary by default."""

    def test_generated_summary_by_default(self):
        html = _html()
        assert "vigiTwin assessment summary" in html

    def test_interpretation_replaces_the_summary(self):
        html = _html(
            presentation=ExamplePresentation(
                case_interpretation="A reviewer reading of the case."
            )
        )
        assert "Human interpretation" in html
        assert "A reviewer reading of the case." in html
        assert "vigiTwin assessment summary" not in html

    def test_both_mode_shows_each_separately(self):
        html = _html(
            presentation=ExamplePresentation(
                case_interpretation="A reviewer reading.",
                summary_mode="both",
            )
        )
        assert "Human interpretation" in html
        assert "vigiTwin assessment summary" in html

    def test_vigitwins_own_account_comes_first(self):
        """It belongs with the panel above it, which says the same in figures.

        A human reading is a response to that assessment, so it follows it
        rather than standing between it and the panel it restates.
        """
        html = _html(
            presentation=ExamplePresentation(
                case_interpretation="A reviewer reading.",
                summary_mode="both",
            )
        )
        assert html.index("Assessment at a glance") < html.index(
            "vigiTwin assessment summary"
        ) < html.index("Human interpretation")

    def test_human_text_is_escaped_not_injected(self):
        html = _html(
            presentation=ExamplePresentation(
                case_interpretation='<script>alert("x")</script> & more'
            )
        )
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestPrintStylesheet:
    """The format is meant to become a PDF."""

    def test_a4_page_rule(self):
        """The bottom margin is deeper than the rest: it is the band the
        repeating per-sheet footer sits in."""
        assert "@page{size:A4;margin:16mm 16mm 28mm}" in _html()

    def test_table_headings_repeat_across_pages(self):
        assert "thead{display:table-header-group}" in _html()

    def test_rows_do_not_split(self):
        assert "tr{break-inside:avoid}" in _html()

    def test_each_case_starts_a_page(self):
        """Every example, the first included, so a collection's front matter
        gets a sheet of its own."""
        assert ".case:not(:only-of-type){break-before:page}" in _html()

    def test_a_lone_case_does_not_open_a_sheet(self):
        """A per-pair page holds exactly one case, and breaking before it
        would print a near-empty first sheet. The old rule is asserted absent
        so it cannot creep back under a passing suite."""
        assert ".case+.case{break-before:page}" not in _html()

    def test_long_terms_wrap(self):
        assert "overflow-wrap:anywhere" in _html()

    def test_narratives_are_not_clipped(self):
        assert ".callout p,.narrative-text{overflow:visible" in _html()

    def test_the_running_footer_is_hidden_by_a_rule_outside_the_print_block(
        self,
    ):
        """It must be, because no rule inside the print block may hide
        content — the assertion that holds that line cannot distinguish one
        display:none from another."""
        html = _html()
        assert ".page-footer{display:none}" in html
        assert html.index(".page-footer{display:none}") < html.index(
            "@media print{"
        )

    def test_the_running_footer_is_fixed_so_it_repeats(self):
        """Chrome paints a fixed element once per sheet. That is the only
        mechanism available here: @page margin boxes are out of reach."""
        block = _print_block(_html())
        assert "position:fixed" in block
        assert "bottom:-14mm" in block

    def test_a_case_opening_a_sheet_drops_its_top_rule(self):
        assert (
            ".case:not(:only-of-type){margin-top:0;border-top:none;"
            "padding-top:0}" in _html()
        )


class TestTheOnlySectionsInMainAreCases:
    """What ``.case:not(:only-of-type)`` rests on.

    The selector is scoped to the element type, not to the class, so it
    behaves as intended only while every ``<section>`` inside ``<main>`` is a
    case and none is nested in another. That is true today and nothing in the
    templates says it must stay true, so it is asserted rather than assumed:
    add a ``<section class="front-matter">`` to the shell and a per-pair page
    silently regains the near-empty first sheet this rule exists to avoid.
    """

    @staticmethod
    def _two_case_html() -> str:
        return render_evidence_page(
            EvidencePageVM(
                title="Evidence review",
                cases=[_case(), _case()],
            )
        )

    def test_every_section_is_a_case(self):
        import re

        html = self._two_case_html()
        opens = re.findall(r"<section\b[^>]*>", html)
        assert len(opens) == 2
        assert all('class="case"' in tag for tag in opens)

    def test_no_section_is_nested_in_another(self):
        import re

        html = self._two_case_html()
        depth = 0
        for tag in re.findall(r"<section\b[^>]*>|</section>", html):
            if tag == "</section>":
                depth -= 1
            else:
                assert depth == 0, "a <section> opened inside another"
                depth += 1
        assert depth == 0


class TestTheRepeatingFooterText:
    """Three states, because a sheet on its own must say which it is."""

    @staticmethod
    def _footer(**kwargs) -> str:
        html = render_evidence_page(
            EvidencePageVM(title="A curated collection", cases=[_case()], **kwargs)
        )
        start = html.index('<div class="page-footer">')
        return html[start : html.index("</div>", start)]

    def test_an_internal_page_says_it_is_not_publication_safe(self):
        assert "Not publication-safe" in self._footer()

    def test_a_candidate_says_it_is_not_certified(self):
        footer = self._footer(mode=PresentationMode.PUBLICATION)
        assert "Candidate for disclosure review" in footer
        assert "not" in footer and "certified as anonymised" in footer

    def test_a_released_document_carries_its_title_instead(self):
        footer = self._footer(
            mode=PresentationMode.PUBLICATION, approved_for_release=True
        )
        assert "A curated collection" in footer
        assert "Candidate for disclosure review" not in footer

    def test_a_released_sheet_still_says_the_values_moved(self):
        """Being cleared for publication settles the disclosure question and
        leaves the interpretive one. A loose sheet of a released supplement
        has only this to stop a reader taking a shifted date for a real one."""
        footer = self._footer(
            mode=PresentationMode.PUBLICATION, approved_for_release=True
        )
        assert "displayed values transformed" in footer


class TestDocumentAssembly:
    """One file per case now; the same template makes the supplement later."""

    def test_two_cases_render_as_two_sections(self):
        page = EvidencePageVM(
            title="Illustrative report pairs",
            cases=[
                _case(presentation=ExamplePresentation(example_id="S1")),
                _case(presentation=ExamplePresentation(example_id="S2")),
            ],
        )
        html = render_evidence_page(page)
        assert html.count('<section class="case"') == 2
        assert 'id="S1"' in html and 'id="S2"' in html

    def test_single_titled_case_does_not_repeat_its_title(self):
        page = EvidencePageVM(
            title="Duplicates with extensive shared information",
            cases=[
                _case(
                    presentation=ExamplePresentation(
                        title="Duplicates with extensive shared information"
                    )
                )
            ],
        )
        html = render_evidence_page(page)
        assert html.count("Duplicates with extensive shared information") == 2

    def test_page_states_it_is_not_publication_safe(self):
        assert "not a" in _html() and "publication-safe" in _html()


class TestFilesAndIndex:
    """Filenames come from the example id; the index links only what was written."""

    def test_filename_uses_the_example_id(self):
        case = _case(presentation=ExamplePresentation(example_id="S1"))
        assert evidence_filename(case) == "S1.html"

    def test_filename_falls_back_to_the_pair_id(self):
        assert evidence_filename(_case()) == "R001__R002.html"

    def test_write_single_file(self, tmp_path):
        path = write_evidence_html(_case(), tmp_path)
        assert path.exists()
        assert path.name == "R001__R002.html"

    def test_batch_writes_files_and_an_index(self, tmp_path):
        cases = [
            _case(presentation=ExamplePresentation(example_id="S1")),
            _case(presentation=ExamplePresentation(example_id="S2")),
        ]
        write_evidence_batch_html(cases, tmp_path)
        names = sorted(p.name for p in tmp_path.glob("*.html"))
        assert names == ["S1.html", "S2.html", "index.html"]

    def test_index_links_only_the_selected_files(self, tmp_path):
        write_evidence_batch_html(
            [_case(presentation=ExamplePresentation(example_id="S1"))], tmp_path
        )
        index = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert 'href="S1.html"' in index
        assert "S2.html" not in index

    def test_index_can_be_skipped(self, tmp_path):
        write_evidence_batch_html(
            [_case()], tmp_path, write_index=False
        )
        assert not (tmp_path / "index.html").exists()

    def test_the_combined_document_is_off_unless_asked_for(self, tmp_path):
        """A saved run's directory is unchanged. The combined document is the
        shape a curated collection wants, not the shape of a directory
        someone navigates pair by pair."""
        write_evidence_batch_html([_case()], tmp_path)
        assert not (tmp_path / EVIDENCE_DOCUMENT_FILENAME).exists()

    def test_a_batch_can_write_the_combined_document_too(self, tmp_path):
        cases = [
            _case(presentation=ExamplePresentation(example_id="S1")),
            _case(presentation=ExamplePresentation(example_id="S2")),
        ]
        write_evidence_batch_html(
            cases, tmp_path, document_filename=EVIDENCE_DOCUMENT_FILENAME
        )
        combined = (tmp_path / EVIDENCE_DOCUMENT_FILENAME).read_text(
            encoding="utf-8"
        )
        assert combined.count('<section class="case"') == 2
        index = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert f'href="{EVIDENCE_DOCUMENT_FILENAME}"' in index

    def test_an_example_id_that_would_clobber_the_document_is_refused(
        self, tmp_path
    ):
        """Silently overwriting the combined document with a case page is
        worse than not building."""
        with pytest.raises(ValueError, match="combined.html"):
            write_evidence_batch_html(
                [_case(presentation=ExamplePresentation(example_id="combined"))],
                tmp_path,
                document_filename=EVIDENCE_DOCUMENT_FILENAME,
            )

    def test_an_example_id_that_would_clobber_the_index_is_refused(
        self, tmp_path
    ):
        """The same hole, and it predates the combined document: the index is
        written last, so a case called `index` was already being lost."""
        with pytest.raises(ValueError, match="index.html"):
            write_evidence_batch_html(
                [_case(presentation=ExamplePresentation(example_id="index"))],
                tmp_path,
                document_filename=EVIDENCE_DOCUMENT_FILENAME,
            )


class TestGapsAreRecordedAndNotPrinted:
    """The limitations list is gone; the gaps behind it are not.

    Every entry on that list named something the stored artifacts could not
    support, which is what the page no longer spends a reader's attention on.
    The record is unchanged: a gap keeps its code and its message on the
    case, where a caller, a test and the publication gate read it.
    """

    def test_a_missing_input_is_recorded_on_the_case(self):
        case = _case()
        codes = {gap.code.value for gap in case.gaps}
        assert "frequency_stats_missing" in codes

    def test_it_is_not_printed(self):
        html = _html()
        assert "Limitations of this review" not in html
        assert "no frozen frequency statistics" not in html


class TestAbsentValueRendering:
    """How an absence is shown, and why the two cases differ."""

    def test_an_absent_drug_column_is_blank(self):
        """The presence column already says which report carries the drug."""
        from llm_dup_dev.models.report import Drug

        r1 = _report("R001", drugs=[Drug(substance="aspirin", sources=["s"])])
        case = build_evidence_case(
            report_1=r1,
            report_2=_report("R002"),
            pair_id="R001__R002",
            features=_features(),
            classification=_classification(),
        )
        html = render_evidence_page(
            EvidencePageVM(title="t", cases=[case])
        )
        assert "Not listed" not in html
        assert "A only" in html

    def test_an_absent_ae_column_is_blank(self):
        from llm_dup_dev.models.report import AdverseEvent

        r1 = _report(
            "R001", aes=[AdverseEvent(pt_name="rash", sources=["s"])]
        )
        case = build_evidence_case(
            report_1=r1,
            report_2=_report("R002"),
            pair_id="R001__R002",
            features=_features(),
            classification=_classification(),
        )
        html = render_evidence_page(EvidencePageVM(title="t", cases=[case]))
        assert "Not listed" not in html

    def test_an_unreported_characteristic_is_blank(self):
        """An unpopulated field is absence of evidence, so it asserts nothing."""
        html = _html()
        assert "Not reported" not in html

    def test_blanks_are_explained(self):
        """The one glyph key the compact legend keeps: a blank is an absence."""
        text = " ".join(_html().split())
        assert "blank cells, which mean the information was not reported" in text

    def test_a_date_range_boundary_keeps_its_dash(self):
        """Inside "X to Y" the slot must hold something, or the phrase truncates."""
        from llm_dup_dev.models.report import Drug, PartialDate

        r1 = _report(
            "R001",
            drugs=[
                Drug(
                    substance="aspirin",
                    sources=["s"],
                    start_date=PartialDate(value="2020-01-01", resolution="day"),
                )
            ],
        )
        case = build_evidence_case(
            report_1=r1,
            report_2=_report("R002"),
            pair_id="R001__R002",
            features=_features(),
            classification=_classification(),
        )
        html = render_evidence_page(EvidencePageVM(title="t", cases=[case]))
        assert "2020-01-01" in html
        assert "to <span class=\"absent\">\u2014</span>" in html

    def test_missing_cells_stay_structurally_marked(self):
        """The display text changed; the fact a consumer reads did not."""
        case = build_evidence_case(
            report_1=_report("R001"),
            report_2=_report("R002"),
            pair_id="R001__R002",
            features=_features(),
            classification=_classification(),
        )
        age = next(r for r in case.characteristics if r.label == "Age")
        assert age.left.missing is True
        assert age.left.text == ""


class TestFindingWeightsAreRendered:
    """The severity column carries what the finding added to the penalty."""

    @staticmethod
    def _features_with_finding(*, suppressed: bool = False):
        return _features(
            matching_drugs=["topiramate"],
            inconsistencies=[
                Inconsistency(
                    feature="patient_age",
                    severity=InconsistencySeverity.MODERATE,
                    difference=7.0,
                    units="years",
                    suppressed=suppressed,
                    suppressed_by="llm_ae_clinical_picture" if suppressed else "",
                )
            ],
            inconsistency_counts=InconsistencyCounts(moderate=1),
        )

    @staticmethod
    def _classification_with_weight(weight: float):
        return _classification(
            penalty=InconsistencyPenalty(
                w_tot=weight,
                threshold=4.0,
                terms=[
                    PenaltyTerm(
                        severity=InconsistencySeverity.MODERATE,
                        count=1,
                        weight=weight,
                    )
                ],
            )
        )

    def test_a_retained_finding_shows_severity_and_weight(self):
        html = _html(
            features=self._features_with_finding(),
            classification=self._classification_with_weight(1.0),
        )
        assert "Moderate (+1.0)" in html

    def test_a_superseded_finding_shows_zero(self):
        html = _html(
            features=self._features_with_finding(suppressed=True),
            classification=self._classification_with_weight(1.0),
        )
        assert "Moderate (+0.0)" in html

    def test_severity_stands_alone_when_the_weight_is_unknown(self):
        html = _html(features=self._features_with_finding())
        assert "Moderate" in html
        assert "Moderate (+" not in html


class TestFindingRowsAreRed:
    """Section 3 tints its rows with the same classes the tables above use."""

    @staticmethod
    def _features_with(*, suppressed: bool):
        return _features(
            matching_drugs=["topiramate"],
            inconsistencies=[
                Inconsistency(
                    feature="patient_age",
                    severity=InconsistencySeverity.MODERATE,
                    difference=7.0,
                    units="years",
                    suppressed=suppressed,
                    suppressed_by="llm_ae_clinical_picture" if suppressed else "",
                )
            ],
            inconsistency_counts=InconsistencyCounts(moderate=1),
        )

    def test_a_retained_finding_row_is_red(self):
        html = _html(features=self._features_with(suppressed=False))
        assert '<tr class="row-mismatch">' in html

    def test_a_superseded_finding_row_is_not(self):
        """It contributed nothing; red would say the assessment weighed it."""
        html = _html(features=self._features_with(suppressed=True))
        assert '<tr class="row-mismatch">' not in html
        assert '<tr class="row-neutral">' in html


class TestTheLegendStaysCompact:
    """Guards against the explanatory paragraphs creeping back in."""

    def test_the_colour_key_is_still_there(self):
        html = _html()
        assert "Green: counted for the pair" in html
        assert "Red: counted against it" in html

    def test_the_blank_cell_key_is_kept(self):
        """The one glyph a reader cannot infer from the colours."""
        text = " ".join(_html().split())
        assert "blank cells, which mean the information was not reported" in text

    def test_the_colour_caveats_are_gone(self):
        """Documented in docs/evidence_format.md, not on every case."""
        text = " ".join(_html().split())
        assert "not how severe it was" not in text
        assert "member of a shared pattern" not in text

    def test_the_table_prose_is_gone(self):
        text = " ".join(_html().split())
        assert "Rows are ordered by presence" not in text
        assert "MedDRA preferred terms as each report coded them" not in text
        assert "A dash inside a treatment period" not in text


class TestNoGapReachesThePage:
    """Neither kind of gap is printed now — and both are still on the case.

    The distinction between a provenance note and an absence is still drawn
    on the model, because a consumer may act on it; it simply no longer
    decides what a reader sees, since no gap reaches the page at all.
    """

    @staticmethod
    def _case_with_gaps(*gaps):
        case = _case()
        case.gaps.extend(gaps)
        return case

    def test_a_provenance_gap_is_recorded_but_not_printed(self):
        from llm_dup_dev.review_html.evidence.models import (
            EvidenceGap,
            EvidenceGapCode,
        )

        gap = EvidenceGap(
            code=EvidenceGapCode.CLINICAL_DATE_VALUE_RECOVERED,
            message="Read from the contribution's own stored description.",
        )
        case = self._case_with_gaps(gap)
        assert gap in case.gaps
        assert gap.is_provenance
        html = render_evidence_page(EvidencePageVM(title="t", cases=[case]))
        assert "own stored description" not in html

    def test_an_absence_gap_is_recorded_but_not_printed_either(self):
        from llm_dup_dev.review_html.evidence.models import (
            EvidenceGap,
            EvidenceGapCode,
        )

        gap = EvidenceGap(
            code=EvidenceGapCode.CLINICAL_DATE_VALUE_MISSING,
            message="A date could not be attributed.",
        )
        case = self._case_with_gaps(gap)
        html = render_evidence_page(EvidencePageVM(title="t", cases=[case]))
        assert gap in case.gaps
        assert not gap.is_provenance
        assert "A date could not be attributed." not in html
        assert "Limitations of this review" not in html

    def test_the_page_still_collects_every_case_gap(self):
        """``EvidencePageVM.gaps`` is the caller's way in, and is unchanged."""
        from llm_dup_dev.review_html.evidence.models import (
            EvidenceGap,
            EvidenceGapCode,
        )

        case = self._case_with_gaps(
            EvidenceGap(
                code=EvidenceGapCode.CLINICAL_DATE_VALUE_MISSING,
                message="A date could not be attributed.",
            )
        )
        page = EvidencePageVM(title="t", cases=[case])
        assert "A date could not be attributed." in {
            gap.message for gap in page.gaps
        }


class TestTheInconsistencyNoteFollowsTheDecision:
    """The findings table carries no explanatory note, in either case.

    The note told a reader how to read the table's own two columns, which is
    format documentation and lives in ``docs/evidence_format.md``. What the
    page still does, and these tests still hold it to, is say where the
    penalty went: the panel marks an uncompared figure, and the relationship
    tile says why no comparison happened.
    """

    @staticmethod
    def _features_with_finding():
        return _features(
            matching_drugs=["topiramate"],
            inconsistencies=[
                Inconsistency(
                    feature="patient_age",
                    severity=InconsistencySeverity.MODERATE,
                    difference=7.0,
                    units="years",
                )
            ],
            inconsistency_counts=InconsistencyCounts(moderate=1),
        )

    def _html_with(self, penalty):
        return " ".join(
            _html(
                features=self._features_with_finding(),
                classification=_classification(penalty=penalty),
            ).split()
        )

    def test_a_compared_penalty_carries_no_note(self):
        """Dropped where it was true, not only where it was not.

        ``findings_weighed`` is still built — a consumer reads it — and the
        page simply no longer prints a paragraph off it.
        """
        html = self._html_with(InconsistencyPenalty(w_tot=1.0, threshold=4.0))
        assert "retained and weighed" not in html
        assert "decided the same-case question" not in html

    def test_an_uncompared_penalty_carries_no_note(self):
        """The panel says it once; the findings table does not repeat it.

        What must never appear is the *weighed* note where nothing was
        weighed — that would claim the findings decided something.
        """
        html = self._html_with(InconsistencyPenalty(w_tot=1.0, threshold=None))
        assert "retained and weighed" not in html
        assert "weighed against a same-case tolerance" not in html

    def test_the_flag_is_still_on_the_view_model(self):
        """The page stopped printing it; the view model did not stop saying it."""
        weighed = _case(
            features=self._features_with_finding(),
            classification=_classification(
                penalty=InconsistencyPenalty(w_tot=1.0, threshold=4.0)
            ),
        )
        unweighed = _case(
            features=self._features_with_finding(),
            classification=_classification(
                penalty=InconsistencyPenalty(w_tot=1.0, threshold=None)
            ),
        )
        assert weighed.findings_weighed is True
        assert unweighed.findings_weighed is False

    def test_the_panel_marks_an_unweighed_penalty_as_such(self):
        """The figure itself says no comparison was made."""
        html = self._html_with(InconsistencyPenalty(w_tot=1.0, threshold=None))
        assert "1.0 (not weighed)" in html

    def test_the_page_says_it_for_a_pinned_relationship(self):
        """Removed from one place because it is stated in another.

        Now stated in the relationship decision tile, and positively: the
        reports were assessed on source context alone, which is what having
        no penalty comparison means.
        """
        html = self._html_with(InconsistencyPenalty(w_tot=1.0, threshold=None))
        assert "assessed on source context alone" in html

    def test_the_page_says_it_for_an_independent_pair(self):
        """The other way a penalty goes unweighed, covered the same way.

        This is the guarantee the dropped decision table used to provide. It
        now lives in the relationship decision tile, which also says in so
        many words that the question was not reached.
        """
        html = " ".join(
            _html(
                features=self._features_with_finding(),
                classification=_classification(
                    independence_assessment=IndependenceAssessment.INDEPENDENT,
                    relationship_assessment=None,
                    independence_reason=IndependenceReason.RARITY_INSUFFICIENT,
                    penalty=InconsistencyPenalty(w_tot=1.0),
                ),
            ).split()
        )
        assert (
            "he relationship question was not reached, because independence "
            "was not rejected"
        ) in html
        assert "Not reached" in html

    def test_the_weight_is_still_shown_when_nothing_was_weighed(self):
        """The run assigns the severity a weight either way; it just went unused."""
        html = " ".join(
            _html(
                features=self._features_with_finding(),
                classification=_classification(
                    penalty=InconsistencyPenalty(
                        w_tot=1.0,
                        threshold=None,
                        terms=[
                            PenaltyTerm(
                                severity=InconsistencySeverity.MODERATE,
                                count=1,
                                weight=1.0,
                            )
                        ],
                    )
                ),
            ).split()
        )
        assert "Moderate (+1.0)" in html


class TestTooltipsAreNotLoadBearing:
    """Still true, and still tested — the page just no longer says it.

    Every tooltip repeats evidence the tables name, so a printed copy loses
    nothing. The claim was removed from the legend because a reader of a
    printed page cannot act on it; the property it described has to hold
    regardless, which is what these assert.
    """

    def test_no_print_rule_hides_content(self):
        """A print stylesheet that hides a section would defeat the claim."""
        assert "display:none" not in _print_block(_html())


class TestTheInconclusiveTileIsAVerdict:
    """Somebody assessed the label and found it genuinely ambiguous.

    Styling that as an absence put it in the small grey type reserved for
    "Not assessed", which reads as though nobody had looked.
    """

    def _tile(self, state, issue=None):
        case = _case(
            reference=ReferenceInfo(
                scle=SCLEAssessment(state=state, issue=issue)
            )
        )
        return next(t for t in case.outcomes if "SCLE" in t.label)

    def test_an_inconclusive_verdict_is_styled_like_a_value(self):
        tile = self._tile(SCLEState.INCONCLUSIVE, "genuinely_ambiguous")
        assert tile.value == "Genuinely ambiguous"
        assert tile.css_class == "tile-value"

    def test_so_is_an_ordinary_one(self):
        tile = self._tile(SCLEState.ASSESSED, "no_apparent_issue")
        assert tile.value == "No apparent issue"
        assert tile.css_class == "tile-value"

    def test_an_absence_is_still_muted(self):
        assert self._tile(SCLEState.NOT_ASSESSED).css_class == "tile-absent"
        assert self._tile(SCLEState.NOT_ENTERED).css_class == "tile-absent"

    def test_the_two_render_the_same_way(self):
        ambiguous = " ".join(
            _html(
                reference=ReferenceInfo(
                    scle=SCLEAssessment(
                        state=SCLEState.INCONCLUSIVE,
                        issue="genuinely_ambiguous",
                    )
                )
            ).split()
        )
        assert '<strong class="tile-value">Genuinely ambiguous</strong>' in (
            ambiguous
        )


class TestTheSharedSetsSitSideBySide:
    def test_the_list_is_a_two_column_grid(self):
        """Drugs left, adverse events right, in the column order of the
        counts table beneath them."""
        html = _html()
        assert ".set-list{" in html
        assert "grid-template-columns:1fr 1fr" in html
        assert "grid-auto-flow:column" in html

    def test_it_stacks_on_a_narrow_screen(self):
        html = _html()
        assert ".set-list{grid-template-columns:1fr;grid-auto-flow:row" in html
