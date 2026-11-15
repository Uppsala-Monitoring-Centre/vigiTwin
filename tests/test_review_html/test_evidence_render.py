"""Tests for evidence HTML rendering.

Assertions are on the HTML string, following the convention of
``test_render.py``. The print rules are asserted because the format's stated
purpose includes being turned into a PDF, and a stylesheet nobody checks is a
stylesheet that silently regresses.
"""

from __future__ import annotations

from llm_dup_dev.review_html.evidence.build import (
    ExamplePresentation,
    build_evidence_case,
    ReferenceInfo,
    SCLEAssessment,
    SCLEState,
    build_evidence_case,
)
from llm_dup_dev.review_html.evidence.models import EvidencePageVM
from llm_dup_dev.review_html.render import (
    evidence_filename,
    render_evidence_page,
    write_evidence_batch_html,
    write_evidence_html,
)
from llm_dup_dev.models.report import AdverseEvent, Drug
from llm_dup_dev.models.pair_features import Inconsistency, InconsistencyCounts
from llm_dup_dev.models.classification import InconsistencyPenalty, PenaltyTerm
from llm_dup_dev.models.enums import InconsistencySeverity

from tests.test_review_html.test_evidence_build import (
    _classification,
    _features,
    _report,
)


def _case(**kwargs):
    r1 = _report(
        "R001",
        drugs=[Drug(substance="topiramate", sources=["s"])],
        aes=[AdverseEvent(pt_name="cleft lip", sources=["s"])],
    )
    r2 = _report(
        "R002",
        drugs=[Drug(substance="topiramate", sources=["s"])],
        aes=[AdverseEvent(pt_name="cleft lip", sources=["s"])],
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
        assert "Green: matching evidence or anchor pattern" in html
        assert "Red: retained inconsistency" in html


class TestSections:
    """The document carries the sections the format promises."""

    def test_four_numbered_sections(self):
        """Inconsistencies are their own section, before the assessment.

        They feed the same-case decision, so a reader following the reasoning
        meets the findings before the conclusion drawn from them.
        """
        html = _html()
        assert "1. Report comparison" in html
        assert "2. Context and matching evidence" in html
        assert "3. Inconsistencies" in html
        assert "4. vigiTwin assessment" in html
        assert html.index("3. Inconsistencies") < html.index(
            "4. vigiTwin assessment"
        )

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
        assert "SCLE assessment of reference label" in html

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
        assert "co-occurrence of the whole set" in html

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
        assert "Case-level interpretation" in html
        assert "A reviewer reading of the case." in html
        assert "vigiTwin assessment summary" not in html

    def test_both_mode_shows_each_separately(self):
        html = _html(
            presentation=ExamplePresentation(
                case_interpretation="A reviewer reading.",
                summary_mode="both",
            )
        )
        assert "Case-level interpretation" in html
        assert "vigiTwin assessment summary" in html

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
        assert "@page{size:A4;margin:16mm}" in _html()

    def test_table_headings_repeat_across_pages(self):
        assert "thead{display:table-header-group}" in _html()

    def test_rows_do_not_split(self):
        assert "tr{break-inside:avoid}" in _html()

    def test_each_case_starts_a_page(self):
        assert ".case+.case{break-before:page}" in _html()

    def test_long_terms_wrap(self):
        assert "overflow-wrap:anywhere" in _html()

    def test_narratives_are_not_clipped(self):
        assert ".callout p,.narrative-text{overflow:visible" in _html()


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


class TestGapsAreVisible:
    """A limitation is a footnote, not a silent omission."""

    def test_missing_frequency_statistics_is_stated(self):
        html = _html()
        assert "Limitations of this review" in html
        assert "no frozen frequency statistics" in html


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
        assert "Green: matching evidence or anchor pattern" in html
        assert "Red: retained inconsistency" in html

    def test_the_two_warnings_that_stop_a_misreading_are_kept(self):
        text = " ".join(_html().split())
        assert "not how severe it was" in text
        assert "member of a shared pattern" in text

    def test_the_table_prose_is_gone(self):
        text = " ".join(_html().split())
        assert "Rows are ordered by presence" not in text
        assert "MedDRA preferred terms as each report coded them" not in text
        assert "A dash inside a treatment period" not in text


class TestLimitationsListsAbsencesOnly:
    """A provenance note is recorded but not printed."""

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
        # The fixture carries absence gaps of its own, so the section is
        # still rendered; what must not appear is this gap's sentence.
        assert "Limitations of this review" in html

    def test_an_absence_gap_is_printed(self):
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
        html = render_evidence_page(EvidencePageVM(title="t", cases=[case]))
        assert "Limitations of this review" in html
        assert "A date could not be attributed." in html
