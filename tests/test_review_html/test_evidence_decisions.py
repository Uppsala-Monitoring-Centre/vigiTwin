"""The two decision stages, and which evidence actually drove each.

The page used to let a reader infer the route from which figures were filled
in. That worked for the rarity route and not at all for the others: a pair
decided on direct evidence of a shared source showed a matching score, an
inconsistency penalty and no indication that neither had settled anything.

Two synthetic fixtures carry these tests. ``shared_source_case`` shares one
trial registration and names different cases within it — definitive source
context, with a score and a penalty computed and neither decisive.
``probabilistic_case`` has no source context at all and runs both stages on
the figures. Every test below asks the same thing of both: can a reader tell
what was decided, and on what.
"""

from __future__ import annotations

from llm_dup_dev.review_html.evidence.models import (
    DecisionPathway,
    DecisionStage,
    EvidenceRole,
    TileState,
)
from llm_dup_dev.review_html.render import render_evidence_page
from tests.test_review_html.conftest import (
    probabilistic_case,
    publication_page,
    shared_source_case,
)


def _tiles(case):
    return {t.stage: t for t in case.decisions}


def _html(case) -> str:
    return " ".join(render_evidence_page(publication_page(case)).split())


class TestBothStagesAreAlwaysReported:
    """Same two tiles, same place, whichever route each took."""

    def test_both_cases_carry_two_tiles(self):
        for case in (shared_source_case(), probabilistic_case()):
            assert [t.stage for t in case.decisions] == [
                DecisionStage.INDEPENDENCE,
                DecisionStage.RELATIONSHIP,
            ]

    def test_both_stages_are_labelled_the_same_way_in_both_cases(self):
        labels = {
            tuple(t.stage_label for t in case.decisions)
            for case in (shared_source_case(), probabilistic_case())
        }
        assert labels == {
            ("Independence assessment", "Relationship assessment")
        }

    def test_a_case_with_no_classification_still_carries_both(self):
        from tests.test_review_html.test_evidence_build import (
            _case,
            _features,
            _report,
        )

        case = _case(
            _report("R1"), _report("R2"), _features(), classification=None
        )
        assert len(case.decisions) == 2
        assert all(t.state is TileState.NOT_ENTERED for t in case.decisions)

    def test_both_render_with_the_same_markup(self):
        for case in (shared_source_case(), probabilistic_case()):
            html = _html(case)
            assert html.count('class="decision ') == 2
            assert "Independence assessment" in html
            assert "Relationship assessment" in html


class TestAThinArtifactStillReadsAsATile:
    """A saved run from before the penalty carried its threshold.

    The outcome is recorded and the route is not, which is a state the page
    has to be able to state rather than leave a reader to interpret from an
    empty tile.
    """

    @staticmethod
    def _case():
        from llm_dup_dev.models.classification import ClassificationResult
        from llm_dup_dev.models.enums import (
            IndependenceAssessment,
            RelationshipAssessment,
        )
        from tests.test_review_html.test_evidence_build import (
            _case,
            _features,
            _report,
        )

        return _case(
            _report("R1"),
            _report("R2"),
            _features(),
            classification=ClassificationResult(
                pair_id="R1__R2",
                independence_assessment=(
                    IndependenceAssessment.NON_INDEPENDENT
                ),
                relationship_assessment=RelationshipAssessment.SAME_CASE,
            ),
        )

    def test_the_pathway_says_it_was_not_recorded(self):
        tiles = _tiles(self._case())
        assert all(
            t.pathway is DecisionPathway.UNRECORDED for t in tiles.values()
        )
        assert all(t.pathway_label == "Not recorded" for t in tiles.values())

    def test_the_outcome_is_still_reported(self):
        tiles = _tiles(self._case())
        assert tiles[DecisionStage.RELATIONSHIP].outcome == "Same case"
        assert tiles[DecisionStage.RELATIONSHIP].state is TileState.VALUE

    def test_the_tile_says_why_it_is_thin(self):
        note = _tiles(self._case())[DecisionStage.RELATIONSHIP].note
        assert "recorded no evidence pathway" in note


class TestThePathwayIsStated:
    def test_the_shared_source_case_names_both_halves_of_its_route(self):
        """Independence rested on there being one source; the relationship
        rested on the individual identifiers within it."""
        tiles = _tiles(shared_source_case())
        assert [t.pathway for t in tiles.values()] == [
            DecisionPathway.SHARED_SOURCE,
            DecisionPathway.INDIVIDUAL_IDENTIFIERS,
        ]
        assert [t.pathway_label for t in tiles.values()] == [
            "Shared source",
            "Individual ids",
        ]

    def test_the_probabilistic_case_names_the_two_figure_routes(self):
        tiles = _tiles(probabilistic_case())
        assert (
            tiles[DecisionStage.INDEPENDENCE].pathway
            is DecisionPathway.PROBABILISTIC_MATCHING
        )
        assert (
            tiles[DecisionStage.RELATIONSHIP].pathway
            is DecisionPathway.INCONSISTENCY_PENALTY
        )

    def test_the_pathway_label_reaches_the_page(self):
        assert "Shared source" in _html(shared_source_case())
        assert "Probabilistic matching" in _html(probabilistic_case())

    def test_the_badge_text_distinguishes_the_routes(self):
        """The *text* carries the route; the colour carries the direction."""
        labels = {
            _tiles(case)[DecisionStage.INDEPENDENCE].pathway_label
            for case in (shared_source_case(), probabilistic_case())
        }
        assert labels == {"Shared source", "Probabilistic matching"}


class TestTheDecisionDrivingEvidenceIsNamed:
    def test_the_source_case_names_the_source_not_the_score(self):
        """The independence badge says "Shared source", so the basis line
        would only repeat it and is dropped."""
        tiles = _tiles(shared_source_case())
        tile = tiles[DecisionStage.INDEPENDENCE]
        assert tile.pathway_label == "Shared source"
        assert tile.basis == ""
        assert "score" not in tile.note.lower()

    def test_the_relationship_names_the_individual_identifiers(self):
        """The source made the comparison possible; the individual
        identifiers are what it turned on, and a badge saying only "Shared
        source" left a reader to work out which of the two had decided."""
        tile = _tiles(shared_source_case())[DecisionStage.RELATIONSHIP]
        assert tile.pathway is DecisionPathway.INDIVIDUAL_IDENTIFIERS
        assert tile.pathway_label == "Individual ids"
        assert tile.basis == "Differing comparable individual identifiers"

    def test_the_probabilistic_case_names_the_score(self):
        tiles = _tiles(probabilistic_case())
        assert "Matching score" in tiles[DecisionStage.INDEPENDENCE].basis
        assert (
            "Inconsistency penalty"
            in tiles[DecisionStage.RELATIONSHIP].basis
        )

    def test_the_note_comes_from_the_same_clause_the_summary_uses(self):
        """So a tile and the generated prose cannot drift apart.

        The tile reads its clause as a sentence of its own, so the first
        character is capitalised; the rest is the summary's own words.
        """
        case = probabilistic_case()
        note = _tiles(case)[DecisionStage.RELATIONSHIP].note
        summary = " ".join(
            p for b in case.narrative_blocks for p in b.paragraphs
        )
        assert note.endswith(".")
        assert note[1:].split(",")[0] in summary

    def test_a_clause_that_begins_with_a_name_keeps_it(self):
        """Jinja's ``capitalize`` lower-cases the rest of the string, which
        turned "vigiTwin assessed ..." into "Vigitwin assessed ..."."""
        from llm_dup_dev.review_html.evidence.build import _as_sentence

        assert _as_sentence("vigiTwin assessed the reports") == (
            "vigiTwin assessed the reports."
        )
        assert _as_sentence("the penalty was below") == (
            "The penalty was below."
        )


class TestTheColourSaysWhichWayTheDecisionWent:
    """One colour rule on the page, not two.

    Green counts towards the reports being one case and red counts against,
    exactly as in the tables. A badge coloured per *pathway* said a
    shared-source decision and a rarity decision were different kinds of
    thing, which is not the distinction a reader needs from a colour.
    """

    def test_a_non_independent_outcome_is_green(self):
        for case in (shared_source_case(), probabilistic_case()):
            tile = _tiles(case)[DecisionStage.INDEPENDENCE]
            assert tile.role is EvidenceRole.MATCHING
            assert tile.pathway_css_class.endswith("value-match")

    def test_an_independent_outcome_is_red(self):
        from llm_dup_dev.models.classification import ClassificationResult
        from llm_dup_dev.models.enums import (
            IndependenceAssessment,
            IndependenceReason,
        )
        from tests.test_review_html.test_evidence_build import (
            _case,
            _features,
            _report,
        )

        case = _case(
            _report("R1"),
            _report("R2"),
            _features(),
            classification=ClassificationResult(
                pair_id="R1__R2",
                independence_assessment=IndependenceAssessment.INDEPENDENT,
                independence_reason=IndependenceReason.RARITY_INSUFFICIENT,
            ),
        )
        tile = _tiles(case)[DecisionStage.INDEPENDENCE]
        assert tile.role is EvidenceRole.INCONSISTENT
        assert tile.pathway_css_class.endswith("value-mismatch")

    def test_same_case_is_green_and_different_case_is_red(self):
        assert (
            _tiles(probabilistic_case())[DecisionStage.RELATIONSHIP].role
            is EvidenceRole.MATCHING
        )
        assert (
            _tiles(shared_source_case())[DecisionStage.RELATIONSHIP].role
            is EvidenceRole.INCONSISTENT
        )

    def test_the_tile_itself_carries_the_same_class(self):
        tile = _tiles(shared_source_case())[DecisionStage.RELATIONSHIP]
        assert tile.css_class == "decision-value value-mismatch"

    def test_both_classes_reach_the_page(self):
        html = _html(shared_source_case())
        assert 'class="decision decision-value value-match"' in html
        assert 'class="decision decision-value value-mismatch"' in html

    def test_an_abstention_points_neither_way(self):
        from llm_dup_dev.models.classification import (
            ClassificationResult,
            InconsistencyPenalty,
        )
        from llm_dup_dev.models.enums import (
            IndependenceAssessment,
            IndependenceReason,
            RelationshipAssessment,
        )
        from tests.test_review_html.test_evidence_build import (
            _case,
            _features,
            _report,
        )

        case = _case(
            _report("R1"),
            _report("R2"),
            _features(),
            classification=ClassificationResult(
                pair_id="R1__R2",
                independence_assessment=(
                    IndependenceAssessment.NON_INDEPENDENT
                ),
                independence_reason=IndependenceReason.RARITY_COMPELLING,
                relationship_assessment=RelationshipAssessment.ABSTAIN,
                penalty=InconsistencyPenalty(w_tot=5.0, threshold=4.0),
            ),
        )
        assert (
            _tiles(case)[DecisionStage.RELATIONSHIP].role
            is EvidenceRole.NEUTRAL
        )


class TestNoCommentaryOnEvidenceThatDidNotDecide:
    """The badge says what the decision rested on; a paragraph saying what it
    did *not* rest on answers a question the tile never raised."""

    def test_the_tile_has_no_unused_field(self):
        tile = _tiles(shared_source_case())[DecisionStage.INDEPENDENCE]
        assert not hasattr(tile, "unused")

    def test_the_page_does_not_discuss_the_undecisive_score(self):
        html = _html(shared_source_case())
        assert "did not bear on this decision" not in html
        assert "whatever the penalty came to" not in html

    def test_nor_does_the_note_qualify_itself(self):
        tiles = _tiles(shared_source_case())
        assert "not on the matching score" not in (
            tiles[DecisionStage.INDEPENDENCE].note
        )
        assert "no inconsistency-penalty comparison" not in (
            tiles[DecisionStage.RELATIONSHIP].note
        )


class TestSourceFindingsLeaveTheScoredTrace:
    """A pinned verdict's finding is not a term in a comparison that happened."""

    def test_the_finding_is_still_in_the_audit_view(self):
        case = shared_source_case()
        assert len(case.retained_discrepancies) == 1
        assert case.retained_discrepancies[0].source_context is True

    def test_it_keeps_its_severity_and_weight_in_the_data(self):
        """Presentation-only: the stored finding is untouched."""
        case = shared_source_case()
        finding = case.retained_discrepancies[0]
        assert finding.severity == "decisive"
        assert finding.retained is True

    def test_it_is_not_in_the_scored_trace(self):
        case = shared_source_case()
        assert case.scored_discrepancies == []
        assert len(case.deferred_discrepancies) == 1

    def test_the_page_shows_it_under_its_own_heading(self):
        html = _html(shared_source_case())
        assert "Decided on source context" in html

    def test_the_page_does_not_call_it_a_weighed_inconsistency(self):
        html = _html(shared_source_case())
        assert "retained and weighed" not in html

    def test_the_panel_chips_do_not_carry_it_either(self):
        """The chips are the decision trace in miniature."""
        case = shared_source_case()
        assert case.summary_panel is not None
        html = _html(case)
        # The panel's Inconsistencies row says None, because none was weighed.
        assert "<dt>Inconsistencies</dt> <dd><span" in html

    def test_an_ordinary_finding_stays_in_the_scored_trace(self):
        """The anti-requirement: nothing is deferred where the source context
        did not decide."""
        from llm_dup_dev.models.enums import InconsistencySeverity
        from llm_dup_dev.models.pair_features import (
            Inconsistency,
            InconsistencyCounts,
        )
        from tests.test_review_html.test_evidence_build import (
            _case,
            _features,
            _report,
        )

        case = _case(
            _report("R1", age=54),
            _report("R2", age=61),
            _features(
                inconsistencies=[
                    Inconsistency(
                        feature="patient_age",
                        severity=InconsistencySeverity.MODERATE,
                        difference=7.0,
                        units="years",
                    )
                ],
                inconsistency_counts=InconsistencyCounts(moderate=1),
            ),
        )
        assert not case.source_context_decided
        assert len(case.scored_discrepancies) == 1
        assert case.deferred_discrepancies == []


class TestSourceContextHasEquivalentStanding:
    def test_the_identifier_is_a_cell_like_any_other_value(self):
        """So it is coloured by the page's one colour rule, and a publication
        transform reaches it by kind."""
        from llm_dup_dev.review_html.evidence.models import (
            EvidenceRole,
            ValueKind,
        )

        case = shared_source_case()
        assert case.source_context is not None
        cell = case.source_context.identifier
        assert cell is not None
        assert cell.kind is ValueKind.SOURCE_IDENTIFIER
        assert cell.role is EvidenceRole.MATCHING

    def test_the_source_block_is_a_table_not_a_sentence_in_passing(self):
        html = _html(shared_source_case())
        assert "Source context" in html
        assert "Cases within the source" in html

    def test_it_states_what_became_of_the_cases_within_the_source(self):
        case = shared_source_case()
        assert case.source_context is not None
        assert (
            "name different cases" in case.source_context.case_comparison
        )

    def test_a_case_without_source_context_renders_no_block(self):
        assert "Source context" not in _html(probabilistic_case())


class TestReaderFacingTerminology:
    def test_no_stored_field_name_reaches_the_page(self):
        """``article_reference`` and its siblings are field names; a
        parenthetical field name is the clearest sign a page is showing its
        internals."""
        for case in (shared_source_case(), probabilistic_case()):
            html = _html(case)
            for internal in (
                "article_reference",
                "same_source_different_case",
                "source_case_",
                "trial_registration",
                "shared_source_identifier",
            ):
                assert internal not in html, internal

    def test_the_verdict_says_which_thing_differed(self):
        """"Same source different case" is three nouns and no sentence."""
        case = shared_source_case()
        assert case.source_context is not None
        assert (
            case.source_context.verdict_label
            == "Shared source, but different cases within it"
        )

    def test_the_identifier_kind_is_named_in_words(self):
        case = shared_source_case()
        assert case.source_context is not None
        assert case.source_context.identifier_label == "Trial registration"

    def test_the_prose_note_names_no_identifier(self):
        """The same division the reporting context keeps: values in cells,
        sentences the same in both presentations."""
        case = shared_source_case()
        assert "SYNTH-TRIAL-0001" not in case.source_case_note

    def test_the_generated_summary_does_not_credit_unused_evidence(self):
        """The score is in the summary as context, never as the reason."""
        case = shared_source_case()
        summary = " ".join(
            p for b in case.narrative_blocks for p in b.paragraphs
        )
        assert "shared source" in summary.lower()
        assert "4.2" not in summary or "not on the matching score" in summary


class TestThePanelPenaltyIsTheTotalOfWhatItLists:
    """The figure under a row saying "None" has to be zero.

    ``w_tot`` is the stored total over every retained finding, the
    source-derived one included. Where that finding is shown as source-context
    evidence instead, the panel listed "Inconsistencies: None" and a penalty
    of 100.0 underneath it — the arithmetic of a row it had just said was not
    there.
    """

    def test_a_pinned_pair_shows_a_penalty_of_zero(self):
        case = shared_source_case()
        assert case.scored_discrepancies == []
        assert case.summary_panel is not None
        assert case.summary_panel.penalty_text.startswith("0.0")

    def test_the_stored_total_is_untouched(self):
        """Presentation only: the deferred finding keeps its weight, and the
        classification keeps its ``w_tot``."""
        case = shared_source_case()
        assert case.deferred_discrepancies[0].weight == 100.0

    def test_an_ordinary_finding_beside_it_is_still_counted(self):
        """Not a blanket zero: the figure is the sum of what is listed."""
        from llm_dup_dev.models.classification import (
            ClassificationResult,
            InconsistencyPenalty,
            PenaltyTerm,
        )
        from llm_dup_dev.models.enums import (
            IndependenceAssessment,
            IndependenceReason,
            InconsistencySeverity,
            RelationshipAssessment,
        )
        from llm_dup_dev.models.narrative_source_case import (
            SourceCaseAssessment,
            SourceCaseVerdict,
            SourceIdentifierType,
        )
        from llm_dup_dev.feature_generation.source_case_inconsistency import (
            source_case_findings,
        )
        from llm_dup_dev.models.pair_features import (
            Inconsistency,
            InconsistencyCounts,
        )
        from tests.test_review_html.test_evidence_build import (
            _case,
            _features,
            _report,
        )

        assessment = SourceCaseAssessment(
            verdict=SourceCaseVerdict.SAME_SOURCE_DIFFERENT_CASE,
            shared_source_identifier="SYNTH-TRIAL-0002",
            shared_source_identifier_type=(
                SourceIdentifierType.TRIAL_REGISTRATION
            ),
            case_identifiers_compared=True,
            case_identifiers_comparable=True,
        )
        case = _case(
            _report("R1", age=54),
            _report("R2", age=61),
            _features(
                source_case_assessment=assessment,
                inconsistencies=[
                    *source_case_findings(assessment),
                    Inconsistency(
                        feature="patient_age",
                        severity=InconsistencySeverity.MODERATE,
                        difference=7.0,
                        units="years",
                    ),
                ],
                inconsistency_counts=InconsistencyCounts(
                    decisive=1, moderate=1
                ),
            ),
            classification=ClassificationResult(
                pair_id="R1__R2",
                independence_assessment=(
                    IndependenceAssessment.NON_INDEPENDENT
                ),
                independence_reason=IndependenceReason.SHARED_SOURCE,
                relationship_assessment=RelationshipAssessment.DIFFERENT_CASE,
                penalty=InconsistencyPenalty(
                    w_tot=100.4,
                    threshold=None,
                    terms=[
                        PenaltyTerm(
                            severity=InconsistencySeverity.DECISIVE,
                            count=1,
                            weight=100.0,
                        ),
                        PenaltyTerm(
                            severity=InconsistencySeverity.MODERATE,
                            count=1,
                            weight=0.4,
                        ),
                    ],
                ),
            ),
        )
        assert len(case.scored_discrepancies) == 1
        assert case.summary_panel is not None
        assert case.summary_panel.penalty_text.startswith("0.4")

    def test_an_unweighed_run_falls_back_to_the_stored_total(self):
        """With no weights recorded there is no sum to take, and quoting a
        default this run may not have used would be worse."""
        from llm_dup_dev.review_html.evidence.build import (
            _scored_penalty_total,
        )
        from llm_dup_dev.models.classification import InconsistencyPenalty
        from llm_dup_dev.review_html.evidence.models import DiscrepancyVM

        penalty = InconsistencyPenalty(w_tot=7.0, threshold=None)
        unknown = DiscrepancyVM(severity="moderate", text="x", weight=None)
        assert (
            _scored_penalty_total(
                penalty, [unknown], source_context_decided=True
            )
            == 7.0
        )


class TestTheLimitationsListDoesNotNameTheSourceFinding:
    """It earns its space by naming what is absent.

    The gap said the finding "does not name a value shown in the comparison
    tables", which was true when the only place for it was a prose line in
    the assessment section. The Source context block names the identifier and
    says what became of the case labels, so the sentence is no longer true.
    """

    def test_no_unmapped_inconsistency_gap_for_a_source_verdict(self):
        codes = {g.code.value for g in shared_source_case().gaps}
        assert "unmapped_inconsistency" not in codes

    def test_the_page_does_not_print_it(self):
        html = _html(shared_source_case())
        assert "does not name a value shown in the comparison tables" not in html

    def test_a_genuinely_unmapped_finding_still_records_one(self):
        """The anti-requirement: the gap is scoped to source verdicts, not
        removed."""
        from llm_dup_dev.models.enums import InconsistencySeverity
        from llm_dup_dev.models.pair_features import (
            Inconsistency,
            InconsistencyCounts,
        )
        from tests.test_review_html.test_evidence_build import (
            _case,
            _features,
            _report,
        )

        case = _case(
            _report("R1"),
            _report("R2"),
            _features(
                inconsistencies=[
                    Inconsistency(
                        feature="something_the_tables_do_not_show",
                        severity=InconsistencySeverity.MODERATE,
                    )
                ],
                inconsistency_counts=InconsistencyCounts(moderate=1),
            ),
        )
        assert "unmapped_inconsistency" in {g.code.value for g in case.gaps}


class TestOutcomesAreNamedForTheReader:
    """Not the stored field names.

    ``Same_case`` and ``Different_case`` are what the classifier records. An
    underscore in a decision tile is the same tell as a parenthetical field
    name anywhere else on the page, and the plural matters: two reports found
    not to be one case describe *different cases*.
    """

    def test_the_tile_says_same_case(self):
        tile = _tiles(probabilistic_case())[DecisionStage.RELATIONSHIP]
        assert tile.outcome == "Same case"

    def test_the_tile_says_different_cases_in_the_plural(self):
        tile = _tiles(shared_source_case())[DecisionStage.RELATIONSHIP]
        assert tile.outcome == "Different cases"

    def test_the_assessment_table_says_the_same_thing(self):
        """One outcome, one name, wherever the page shows it."""
        case = shared_source_case()
        row = next(r for r in case.assessment_rows if r.label == "Same case")
        assert row.text.startswith("Different cases")

    def test_no_underscored_outcome_reaches_the_page(self):
        for case in (shared_source_case(), probabilistic_case()):
            html = _html(case)
            assert "Same_case" not in html
            assert "Different_case" not in html

    def test_an_abstention_keeps_its_own_word(self):
        from llm_dup_dev.models.enums import RelationshipAssessment
        from llm_dup_dev.review_html.evidence.build import relationship_label

        assert relationship_label(RelationshipAssessment.ABSTAIN) == "Abstain"


class TestTheSourceKindIsSourceNeutral:
    """A catch-all identifier kind must not claim to be a publication.

    ``article_reference`` is documented as "a free-text citation when no more
    specific type fits", so calling it a literature citation said something
    specific about a source that need not be a paper at all — a registry
    entry, a programme, a company's own case series.
    """

    @staticmethod
    def _case_with(kind):
        from llm_dup_dev.models.narrative_source_case import (
            SourceCaseAssessment,
            SourceCaseVerdict,
        )
        from tests.test_review_html.test_evidence_build import (
            _case,
            _features,
            _report,
        )

        return _case(
            _report("R1"),
            _report("R2"),
            _features(
                source_case_assessment=SourceCaseAssessment(
                    verdict=SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT,
                    shared_source_identifier="SYNTH-SOURCE-0003",
                    shared_source_identifier_type=kind,
                )
            ),
        )

    def test_the_catch_all_kinds_read_as_a_shared_external_source(self):
        from llm_dup_dev.models.narrative_source_case import (
            SourceIdentifierType,
        )

        for kind in (
            SourceIdentifierType.ARTICLE_REFERENCE,
            SourceIdentifierType.UNKNOWN,
        ):
            case = self._case_with(kind)
            assert case.source_context is not None
            assert (
                case.source_context.identifier_label
                == "Shared external source"
            )

    def test_the_prose_reads_the_same_way(self):
        from llm_dup_dev.models.narrative_source_case import (
            SourceIdentifierType,
        )

        case = self._case_with(SourceIdentifierType.ARTICLE_REFERENCE)
        summary = " ".join(
            p for b in case.narrative_blocks for p in b.paragraphs
        )
        assert "a shared external source" in summary

    def test_a_specific_kind_is_still_named_specifically(self):
        """The anti-requirement: source-neutral where the type is a
        catch-all, not everywhere."""
        from llm_dup_dev.models.narrative_source_case import (
            SourceIdentifierType,
        )

        case = self._case_with(SourceIdentifierType.TRIAL_REGISTRATION)
        assert case.source_context is not None
        assert case.source_context.identifier_label == "Trial registration"

    def test_no_literature_claim_reaches_the_page(self):
        from llm_dup_dev.models.narrative_source_case import (
            SourceIdentifierType,
        )

        html = _html(self._case_with(SourceIdentifierType.ARTICLE_REFERENCE))
        assert "iterature citation" not in html
        assert "Shared external source" in html
