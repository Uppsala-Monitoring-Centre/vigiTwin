"""The at-a-glance panel above the written summary.

The panel exists because three figures a reader wants first — the matching
score, the inconsistency penalty and the tolerance it was put to — used to
appear only inside sentences. It is also the page's only statement of the two
verdicts now that the section-3 decision table is gone, which is why several
of these tests are about what it must *not* omit.
"""

from __future__ import annotations

from llm_dup_dev.models.classification import InconsistencyPenalty, PenaltyTerm
from llm_dup_dev.models.enums import (
    AnchorType,
    IndependenceAssessment,
    IndependenceReason,
    InconsistencySeverity,
    RelationshipAssessment,
    ReportingContextLevel,
)
from llm_dup_dev.models.pair_features import (
    AnchorPopulation,
    FeatureContribution,
    Inconsistency,
    ProbabilityEstimate,
    ReportingContext,
)
from llm_dup_dev.review_html.evidence.models import EvidenceRole, ValueKind
from llm_dup_dev.review_html.evidence.build import shown_score
from llm_dup_dev.review_html.evidence.settings import EvidenceRunSettings
from llm_dup_dev.review_html.evidence.summary import (
    format_score,
    independence_clause,
    same_case_clause,
)

from tests.test_review_html.test_evidence_build import (
    _classification,
    _features,
    _report,
)
from tests.test_review_html.test_evidence_render import _case, _html


def _flat(**kwargs) -> str:
    return " ".join(_html(**kwargs).split())


def _with_contribution(**overrides):
    """Features carrying one contribution, so the panel has a figure to show.

    ``L_match`` is set to what the contributions and the anchor adjustment
    actually come to — -log10(0.002) - log10(C(5,2)) = 1.7 — so these cases
    reconcile. A fixture whose stored score contradicts its own contributions
    exercises the fallback path instead, which is tested on its own.
    """
    base = dict(
        matching_drugs=["topiramate"],
        matching_AEs=["cleft lip"],
        L_match=1.7,
        probability=ProbabilityEstimate(
            p_joint=1e-3,
            p_match=1e-3,
            feature_contributions=[
                FeatureContribution(
                    feature="clinical_date",
                    p_f=0.002,
                    method="domain_size",
                    matched_value="2002-01-04",
                    value_resolution="day",
                )
            ],
        ),
    )
    base.update(overrides)
    return _features(**base)


def _at_the_ceiling():
    """Contributions implying far more than the stored score can express."""
    return _features(
        matching_drugs=["topiramate"],
        anchor=AnchorPopulation(
            N_drugs=10,
            N_AEs=10,
            N_drugs_AEs=5,
            type=AnchorType.DRUGS_AES,
            N_anchor=2,
        ),
        L_match=15.0,
        probability=ProbabilityEstimate(
            p_joint=10**-17.2,
            p_match=1e-15,
            feature_contributions=[
                FeatureContribution(
                    feature="clinical_date",
                    p_f=10**-17.2,
                    method="domain_size",
                    matched_value="2002-01-04",
                    value_resolution="day",
                )
            ],
        ),
    )


class TestThePanelIsBuilt:
    def test_a_case_with_an_assessment_has_one(self):
        assert _case().summary_panel is not None

    def test_a_case_with_no_features_has_none(self):
        """No assessment to summarise, so no summary of one."""
        assert _case(features=None, classification=None).summary_panel is None


class TestWhereItSits:
    def test_between_the_verdict_note_and_the_written_summary(self):
        html = _html()
        assert html.index("Assessment at a glance") < html.index(
            "vigiTwin assessment summary"
        )

    def test_above_the_first_numbered_section(self):
        html = _html()
        assert html.index("Assessment at a glance") < html.index(
            "<h3>1. Report comparison</h3>"
        )


class TestTheRows:
    def test_the_seven_rows_in_order(self):
        html = _flat()
        labels = [
            "<dt>Context</dt>",
            "<dt>Anchor</dt>",
            "<dt>Anchor N</dt>",
            "<dt>Additional matching features</dt>",
            "<dt>Matching score</dt>",
            "<dt>Inconsistencies</dt>",
            "<dt>Penalty</dt>",
        ]
        positions = [html.index(label) for label in labels]
        assert positions == sorted(positions)

    def test_no_classification_row(self):
        """The outcome tile says it a dozen lines above; twice is enough."""
        assert "<dt>Classification</dt>" not in _flat()

    def test_the_final_label_is_still_on_the_page(self):
        """Dropping the row must not drop the verdict."""
        html = _flat()
        assert "vigiTwin classification" in html
        assert "Duplicates" in html

    def test_the_score_is_the_figure_the_clause_quotes(self):
        """One number, formatted once — that is why format_score is public."""
        case = _case(features=_with_contribution())
        panel = case.summary_panel
        assert panel is not None
        assert case.reconciliation is not None
        assert case.reconciliation.reconciled
        shown = format_score(case.reconciliation.additive_total)
        assert panel.score_text == shown
        assert shown in panel.independence_note

    def test_the_ceiling_does_not_hold_the_figure_down(self):
        """The page shows what the evidence came to, not where the stored
        score was held."""
        case = _case(
            features=_at_the_ceiling(),
            run_settings=EvidenceRunSettings(epsilon=1e-15),
        )
        panel = case.summary_panel
        assert panel is not None
        assert case.reconciliation is not None
        assert case.reconciliation.ceiling_reached
        assert case.reconciliation.stored_score == 15.0
        assert panel.score_text == "17.2"

    def test_an_unreconciled_case_falls_back_to_the_stored_score(self):
        """Quoting a sum that provably does not describe the decision would
        let the page contradict itself a line later."""
        features = _with_contribution(L_match=9.0)
        case = _case(features=features)
        panel = case.summary_panel
        assert panel is not None
        assert case.reconciliation is not None
        assert not case.reconciliation.reconciled
        assert panel.score_text == "9.0"
        assert "9.0" in panel.independence_note
        assert "score_unreconciled" in {g.code.value for g in case.gaps}

    def test_the_anchor_n_is_grouped(self):
        features = _features(
            anchor=AnchorPopulation(
                N_drugs=10,
                N_AEs=10,
                N_drugs_AEs=5,
                type=AnchorType.DRUGS_AES,
                N_anchor=89967,
            )
        )
        panel = _case(features=features).summary_panel
        assert panel is not None
        assert panel.anchor_n_text == "89,967 reports"

    def test_a_single_report_anchor_reads_as_one_report(self):
        features = _features(
            anchor=AnchorPopulation(
                N_drugs=1,
                N_AEs=1,
                N_drugs_AEs=1,
                type=AnchorType.DRUGS_AES,
                N_anchor=1,
            )
        )
        panel = _case(features=features).summary_panel
        assert panel is not None
        assert panel.anchor_n_text == "1 report"


class TestTheContextRowNamesThePopulation:
    """Which population, not merely how it was chosen."""

    def test_country_year(self):
        panel = _case().summary_panel
        assert panel is not None
        assert panel.context is not None
        assert panel.context.text == "US 2020"

    def test_country(self):
        features = _features(
            reporting_context=ReportingContext(
                level=ReportingContextLevel.COUNTRY, country="US"
            )
        )
        panel = _case(features=features).summary_panel
        assert panel is not None
        assert panel.context is not None
        assert panel.context.text == "US"

    def test_global(self):
        features = _features(
            reporting_context=ReportingContext(
                level=ReportingContextLevel.GLOBAL
            )
        )
        panel = _case(features=features).summary_panel
        assert panel is not None
        assert panel.context is not None
        assert panel.context.text == "Global"

    def test_a_country_level_run_that_recorded_no_country(self):
        """Nothing to name it by, so the level is all that can be said."""
        features = _features(
            reporting_context=ReportingContext(
                level=ReportingContextLevel.COUNTRY_YEAR
            )
        )
        panel = _case(features=features).summary_panel
        assert panel is not None
        assert panel.context is not None
        assert panel.context.text == "Country-year"

    def test_it_is_a_cell_so_the_publication_phase_can_mask_it(self):
        panel = _case().summary_panel
        assert panel is not None
        assert panel.context is not None
        assert panel.context.kind is ValueKind.COUNTRY
        assert panel.context.role is EvidenceRole.NEUTRAL


class TestItReusesWhatTheSectionsHold:
    """A panel derived twice could disagree with the page under it."""

    def test_the_anchor_sets_are_the_shared_set_objects(self):
        case = _case()
        panel = case.summary_panel
        assert panel is not None
        anchor_sets = [s for s in case.shared_sets if s.is_anchor]
        assert anchor_sets
        pairs = zip(panel.anchor_sets, anchor_sets, strict=True)
        for panel_set, section_set in pairs:
            assert panel_set is section_set

    def test_a_member_rewrite_reaches_both(self):
        """What the later publication phase does, done by hand."""
        case = _case()
        panel = case.summary_panel
        assert panel is not None
        panel.anchor_sets[0].members[0].text = "WITHHELD"
        anchor = next(s for s in case.shared_sets if s.is_anchor)
        assert anchor.members[0].text == "WITHHELD"

    def test_the_contributions_come_from_the_case(self):
        """No copy on the panel: the template reads matching_features."""
        case = _case(features=_with_contribution())
        assert case.summary_panel is not None
        assert not hasattr(case.summary_panel, "matching_features")
        assert case.matching_features
        html = _flat(features=_with_contribution())
        assert html.count("2002-01-04") >= 2  # the panel and section 2

    def test_a_finding_names_its_feature(self):
        """"Difference of 1 year." says nothing without saying what differed."""
        features = _features(
            matching_drugs=["topiramate"],
            inconsistencies=[
                Inconsistency(
                    feature="patient_age",
                    severity=InconsistencySeverity.MINOR,
                    difference=7.0,
                    units="years",
                )
            ],
        )
        html = _flat(features=features)
        panel = html[html.index("Assessment at a glance") :]
        panel = panel[: panel.index("<h3>1. Report comparison</h3>")]
        assert "Age: Difference of 7 years." in panel

    def test_a_suppressed_finding_is_not_in_the_panel(self):
        """It contributed nothing, so it is not evidence against the pair."""
        features = _features(
            matching_drugs=["topiramate"],
            inconsistencies=[
                Inconsistency(
                    feature="ae_clinical_picture:unmatched_burden:r1_to_r2",
                    severity=InconsistencySeverity.MINOR,
                    terms=["cleft lip"],
                    source="llm_ae_clinical_picture",
                    suppressed=True,
                    suppressed_by="clinical picture",
                )
            ],
        )
        case = _case(features=features)
        assert case.suppressed_discrepancies
        assert not case.retained_discrepancies


class TestFindingsQuoteTheirValues:
    """"Difference of 1 years." says what changed; the values say what was said."""

    @staticmethod
    def _age_finding():
        return _features(
            matching_drugs=["topiramate"],
            inconsistencies=[
                Inconsistency(
                    feature="patient_age",
                    severity=InconsistencySeverity.MINOR,
                    difference=1.0,
                    units="years",
                )
            ],
        )

    def _case_with_ages(self):
        return _case(
            report_1=_report("R001", age=31.0),
            report_2=_report("R002", age=30.0),
            features=self._age_finding(),
        )

    def test_both_values_are_carried(self):
        (finding,) = self._case_with_ages().retained_discrepancies
        assert finding.left is not None and finding.right is not None
        assert finding.left.text == "31 years"
        assert finding.right.text == "30 years"

    def test_they_are_the_characteristics_table_cells(self):
        """Same objects, so one publication rewrite serves both."""
        case = self._case_with_ages()
        (finding,) = case.retained_discrepancies
        row = next(r for r in case.characteristics if r.label == "Age")
        assert finding.left is row.left
        assert finding.right is row.right

    def test_the_panel_and_the_findings_table_both_show_them(self):
        html = " ".join(
            _html(
                report_1=_report("R001", age=31.0),
                report_2=_report("R002", age=30.0),
                features=self._age_finding(),
            ).split()
        )
        assert html.count("31 years") >= 2
        assert "vs" in html
        assert "Difference of 1 years." not in html

    def test_a_finding_with_no_pair_of_values_keeps_its_prose(self):
        """A drug-set finding is not about two values side by side."""
        features = _features(
            matching_drugs=["topiramate"],
            inconsistencies=[
                Inconsistency(
                    feature="ae_clinical_picture:unmatched_burden:r1_to_r2",
                    severity=InconsistencySeverity.MINOR,
                    terms=["cleft lip"],
                    source="rule_ae_clinical_picture",
                )
            ],
        )
        (finding,) = _case(features=features).retained_discrepancies
        assert finding.left is None and finding.right is None
        assert finding.text


class TestTheChips:
    """Flat, never shaded: colour says which way, the figure says how much."""

    def test_a_contribution_is_a_green_chip(self):
        html = _flat(features=_with_contribution())
        assert '<span class="chip chip-for">' in html

    def test_a_finding_is_a_red_chip_carrying_its_weight(self):
        features = _features(
            matching_drugs=["topiramate"],
            inconsistencies=[
                Inconsistency(
                    feature="patient_age",
                    severity=InconsistencySeverity.MINOR,
                    difference=1.0,
                    units="years",
                )
            ],
        )
        html = _flat(
            features=features,
            classification=_classification(
                penalty=InconsistencyPenalty(
                    w_tot=0.4,
                    threshold=4.0,
                    terms=[
                        PenaltyTerm(
                            severity=InconsistencySeverity.MINOR,
                            count=1,
                            weight=0.4,
                        )
                    ],
                )
            ),
        )
        panel = html[html.index("Assessment at a glance") :]
        panel = panel[: panel.index("<h3>1. Report comparison</h3>")]
        assert '<span class="chip chip-against">' in panel
        assert "<b>+0.4</b>" in panel

    def test_no_chip_is_shaded_by_magnitude(self):
        """Two classes only; a bigger figure gets a bigger number, not a
        darker box."""
        html = _flat(features=_with_contribution())
        chips = {
            part.split('"')[0]
            for part in html.split('<span class="chip ')[1:]
        }
        assert chips <= {"chip-for", "chip-against"}


class TestThePenaltyRow:
    def test_an_unweighed_penalty_says_so(self):
        panel = _case(
            classification=_classification(
                penalty=InconsistencyPenalty(
                    w_tot=1.2,
                    threshold=None,
                    terms=[
                        PenaltyTerm(
                            severity=InconsistencySeverity.MINOR,
                            count=3,
                            weight=0.4,
                        )
                    ],
                )
            )
        ).summary_panel
        assert panel is not None
        assert panel.penalty_text == "3 × 0.4 = 1.2 (not weighed)"

    def test_a_weighed_penalty_leaves_the_comparison_to_the_clause(self):
        """Saying "< 4.0" here and "below the threshold of 4.0" below is twice."""
        panel = _case(
            classification=_classification(
                penalty=InconsistencyPenalty(
                    w_tot=1.2,
                    threshold=4.0,
                    terms=[
                        PenaltyTerm(
                            severity=InconsistencySeverity.MINOR,
                            count=3,
                            weight=0.4,
                        )
                    ],
                )
            )
        ).summary_panel
        assert panel is not None
        assert panel.penalty_text == "3 × 0.4 = 1.2"
        assert "below the same-case threshold of 4.0" in panel.same_case_note

    def test_no_terms_still_gives_a_total(self):
        panel = _case(
            classification=_classification(
                penalty=InconsistencyPenalty(w_tot=0.0, threshold=4.0)
            )
        ).summary_panel
        assert panel is not None
        assert panel.penalty_text == "0.0"


class TestTheClausesAreTheSummarysOwn:
    """The panel must not paraphrase; it renders what the generator wrote."""

    CASES = [
        (IndependenceReason.RARITY_COMPELLING, RelationshipAssessment.SAME_CASE),
        (
            IndependenceReason.RARITY_INTERMEDIATE,
            RelationshipAssessment.DIFFERENT_CASE,
        ),
        (IndependenceReason.RARITY_LIMITED, RelationshipAssessment.ABSTAIN),
        (IndependenceReason.SHARED_SOURCE, RelationshipAssessment.SAME_CASE),
        (IndependenceReason.EXPLICIT_LINK, RelationshipAssessment.SAME_CASE),
    ]

    def test_every_pathway(self):
        features = _features()
        for reason, relationship in self.CASES:
            classification = _classification(
                independence_reason=reason,
                relationship_assessment=relationship,
            )
            case = _case(features=features, classification=classification)
            panel = case.summary_panel
            assert panel is not None
            assert case.reconciliation is not None
            expected = independence_clause(
                features,
                classification,
                None,
                score=shown_score(features, case.reconciliation),
            )
            assert panel.independence_note == expected[0].lower() + expected[1:]
            same_case = same_case_clause(classification)
            assert same_case is not None
            assert panel.same_case_note == same_case.rstrip(".")

    def test_an_independent_pair(self):
        classification = _classification(
            independence_assessment=IndependenceAssessment.INDEPENDENT,
            relationship_assessment=None,
            independence_reason=IndependenceReason.RARITY_INSUFFICIENT,
            penalty=InconsistencyPenalty(w_tot=0.0),
        )
        panel = _case(classification=classification).summary_panel
        assert panel is not None
        assert "independence was not rejected" in panel.same_case_note

    def test_no_threshold_number_without_run_settings(self):
        """The format never quotes a bar the run manifest did not record."""
        panel = _case().summary_panel
        assert panel is not None
        assert "threshold of" not in panel.independence_note

    def test_the_threshold_is_quoted_when_the_run_recorded_it(self):
        panel = _case(
            run_settings=EvidenceRunSettings(lmatch_limited=1.0)
        ).summary_panel
        assert panel is not None
        assert "independence threshold of 1.0" in panel.independence_note


class TestDegradedInputs:
    def test_no_classification_leaves_the_decision_rows_empty(self):
        case = _case(classification=None)
        panel = case.summary_panel
        assert panel is not None
        assert panel.penalty_text == ""
        assert panel.independence_note == ""
        assert panel.same_case_note == ""
        html = " ".join(
            _html(classification=None).split()
        )
        assert "<dt>Penalty</dt>" not in html

    def test_the_evidence_rows_survive_without_a_classification(self):
        """The arithmetic is in the features; only the verdicts are missing."""
        panel = _case(classification=None).summary_panel
        assert panel is not None
        assert panel.context is not None
        assert panel.anchor_n_text
