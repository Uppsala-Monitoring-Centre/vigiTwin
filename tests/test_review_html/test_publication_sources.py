"""Shared source identifiers in publication mode.

Masked by default, and decided separately from the country: a country is a
population, while a named study or publication is a small and often public
set of patients, so naming one beside a drug, an event and two dates narrows
the field much further than a country does. There is no equivalent of "the
countries are known from the reference-set design" for a study name.

The synthetic fixture shares one trial registration between two reports and
names different cases within it, which is the shape that matters here: the
alias has to keep the *shared source* visible while saying nothing about
which source it is.
"""

from __future__ import annotations

import json

from vigitwin.review_html.evidence.models import ValueKind
from vigitwin.review_html.evidence.publication import (
    ApprovedText,
    DisclosureCode,
    PublicationApprovals,
    PublicationPolicy,
    build_key_record,
    build_publication_manifest,
    walk_cells,
    write_publication_evidence,
    write_scrambling_key,
)
from vigitwin.review_html.render import render_evidence_page
from tests.test_review_html.conftest import (
    probabilistic_case,
    publication_page,
    scramble,
    shared_source_case,
)

_IDENTIFIER = "SYNTH-TRIAL-0001"


def _page():
    return publication_page(shared_source_case())


def _source_cell(case):
    return next(
        cell
        for _, cell in walk_cells(case)
        if cell.kind is ValueKind.SOURCE_IDENTIFIER
    )


def _html(page) -> str:
    return " ".join(render_evidence_page(page).split())


class TestTheDefaultIsToMask:
    def test_the_policy_default_is_on(self):
        assert PublicationPolicy().mask_source_identifiers is True

    def test_it_is_decided_separately_from_the_country(self):
        """Both defaults at once: the source masked, the country not."""
        result = scramble(_page())
        transform = result.transforms[0]
        assert transform.sources_masked is True
        assert transform.countries_masked is False

    def test_the_identifier_becomes_an_alias_in_its_own_series(self):
        result = scramble(_page())
        assert _source_cell(result.page.cases[0]).text == "Source 1"

    def test_the_alias_series_is_not_the_report_identifier_series(self):
        """So a reader can tell a masked study from a masked report."""
        result = scramble(_page())
        assert "Identifier" not in _source_cell(result.page.cases[0]).text
        assert "Report" not in _source_cell(result.page.cases[0]).text

    def test_the_original_is_nowhere_on_the_page(self):
        result = scramble(_page())
        assert _IDENTIFIER not in _html(result.page)

    def test_the_shared_source_relationship_survives(self):
        """The alias is the point of masking; the *sharing* is the point of
        the evidence, and the verdict carries it in words that name no
        identifier."""
        result = scramble(_page())
        case = result.page.cases[0]
        assert case.source_context is not None
        assert (
            case.source_context.verdict_label
            == "Shared source, but different cases within it"
        )
        assert "different cases" in case.source_context.case_comparison

    def test_the_distinction_within_the_source_is_still_stated(self):
        html = _html(scramble(_page()).page)
        assert "Cases within the source" in html
        assert "name different cases" in html

    def test_the_decision_tiles_still_name_the_pathway(self):
        """Masking a value must not cost the reader the route."""
        html = _html(scramble(_page()).page)
        assert "Shared source" in html
        assert "Individual ids" in html


class TestItCanBeTurnedOff:
    def test_the_identifier_is_then_shown_as_reported(self):
        result = scramble(
            _page(), policy=PublicationPolicy(mask_source_identifiers=False)
        )
        assert _source_cell(result.page.cases[0]).text == _IDENTIFIER

    def test_and_that_is_not_a_finding(self):
        result = scramble(
            _page(), policy=PublicationPolicy(mask_source_identifiers=False)
        )
        assert not result.blocked

    def test_the_retention_is_recorded_as_a_decision(self):
        result = scramble(
            _page(), policy=PublicationPolicy(mask_source_identifiers=False)
        )
        entry = result.transforms[0].table.entries[_IDENTIFIER]
        assert not entry.changed
        assert "retained" in entry.note

    def test_masking_the_country_does_not_imply_masking_the_source(self):
        """The two policies are independent in both directions."""
        result = scramble(
            _page(),
            policy=PublicationPolicy(
                mask_countries=True, mask_source_identifiers=False
            ),
        )
        case = result.page.cases[0]
        assert _source_cell(case).text == _IDENTIFIER
        assert case.summary_panel is not None
        assert case.summary_panel.context is not None
        assert "Country 1" in case.summary_panel.context.text


class TestProseAndHumanText:
    def test_the_generated_prose_never_held_the_identifier(self):
        """Values live in cells; sentences read the same in both
        presentations. Nothing to rewrite, so nothing to get wrong."""
        internal = shared_source_case()
        assert _IDENTIFIER not in internal.source_case_note
        summary = " ".join(
            p for b in internal.narrative_blocks for p in b.paragraphs
        )
        assert _IDENTIFIER not in summary

    def test_prose_that_does_quote_it_is_rewritten(self):
        """A note built elsewhere, or a future field, is covered by the
        substitution table like any other derived prose."""
        page = _page()
        page.cases[0].context_note = f"Both reports cite {_IDENTIFIER}."
        result = scramble(page)
        assert _IDENTIFIER not in result.page.cases[0].context_note
        assert "Source 1" in result.page.cases[0].context_note
        assert not result.blocked

    def test_human_text_quoting_it_blocks_rather_than_being_redacted(self):
        """Fail-closed, as for any human-authored text: automatic redaction
        is not treated as sufficient."""
        page = _page()
        page.cases[0].title = f"The {_IDENTIFIER} pair"
        result = scramble(page)
        assert result.blocked
        assert DisclosureCode.CURATOR_TEXT_REVIEW_REQUIRED in {
            f.code for f in result.report.findings
        }

    def test_an_approved_replacement_clears_it(self):
        page = _page()
        page.cases[0].title = f"The {_IDENTIFIER} pair"
        example_id = page.cases[0].example_id
        approvals = PublicationApprovals(
            approved_texts=[
                ApprovedText(
                    example_id=example_id,
                    field_path="title",
                    replacement="Two reports from one trial",
                    approved_by="reviewer",
                    approved_at="2026-01-01",
                )
            ]
        )
        result = scramble(page, approvals=approvals)
        assert not result.blocked
        assert _IDENTIFIER not in _html(result.page)

    def test_a_narrative_snippet_quoting_it_also_blocks(self):
        page = _page()
        page.cases[0].characteristics[0].left_evidence = [
            f"Reported under {_IDENTIFIER}."
        ]
        result = scramble(page)
        assert result.blocked
        assert DisclosureCode.NARRATIVE_SNIPPET_REVIEW_REQUIRED in {
            f.code for f in result.report.findings
        }


class TestTheSecureAndPublicRecords:
    def test_the_mapping_is_in_the_key_record_only(self, tmp_path):
        result = scramble(_page())
        key = write_scrambling_key(result, tmp_path / "secure" / "key.json")
        out = write_publication_evidence(result, tmp_path / "publication")
        assert _IDENTIFIER in key.read_text()
        written = "\n".join(
            path.read_text() for path in out.rglob("*") if path.is_file()
        )
        assert _IDENTIFIER not in written

    def test_the_key_record_names_the_kind_of_value(self):
        case = build_key_record(scramble(_page())).cases[0]
        entry = next(
            s for s in case.substitutions if s.kind == "source_identifier"
        )
        assert entry.original == _IDENTIFIER
        assert entry.published == "Source 1"

    def test_the_manifest_records_the_effective_policy(self):
        manifest = build_publication_manifest(scramble(_page()))
        assert manifest.source_masking.default_masked is True
        assert manifest.source_masking.cases_masked == 1
        assert "own series" in manifest.source_masking.statement

    def test_the_manifest_names_no_identifier(self):
        manifest = build_publication_manifest(scramble(_page()))
        assert _IDENTIFIER not in manifest.model_dump_json()

    def test_the_footer_says_source_identifiers_were_transformed(self):
        result = scramble(_page())
        assert (
            "shared source identifiers have been transformed"
            in _html(result.page)
        )

    def test_and_says_otherwise_when_they_were_not(self):
        result = scramble(
            _page(), policy=PublicationPolicy(mask_source_identifiers=False)
        )
        html = _html(result.page)
        assert "shared source identifiers and countries are shown" in html


class TestBothPathwaysSurvivePublication:
    """The two worked examples, through the transform and onto a page."""

    def test_both_publish_without_a_finding(self, tmp_path):
        page = publication_page(shared_source_case(), probabilistic_case())
        result = scramble(page)
        assert not result.blocked
        out = write_publication_evidence(result, tmp_path / "publication")
        manifest = json.loads(
            (out / "publication_manifest.json").read_text()
        )
        assert manifest["cases"] == 2

    def test_both_keep_their_two_decision_tiles(self):
        page = publication_page(shared_source_case(), probabilistic_case())
        result = scramble(page)
        for case in result.page.cases:
            assert len(case.decisions) == 2
            assert all(t.pathway_label for t in case.decisions)

    def test_the_pathways_are_still_distinguishable_after_masking(self):
        page = publication_page(shared_source_case(), probabilistic_case())
        result = scramble(page)
        pathways = [
            tuple(t.pathway.value for t in case.decisions)
            for case in result.page.cases
        ]
        assert pathways == [
            ("shared_source", "individual_identifiers"),
            ("probabilistic_matching", "inconsistency_penalty"),
        ]

    def test_the_deferred_finding_is_still_deferred_after_masking(self):
        page = publication_page(shared_source_case(), probabilistic_case())
        result = scramble(page)
        shared, probabilistic = result.page.cases
        assert shared.scored_discrepancies == []
        assert len(shared.deferred_discrepancies) == 1
        assert probabilistic.deferred_discrepancies == []
