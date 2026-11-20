"""Text a human wrote, and text that merely quotes values.

The line between them is the one the specification draws: anything a person
authored, and anything quoted verbatim from a narrative, is treated as
potentially identifying and needs an explicit decision. Automatic redaction
is never accepted as sufficient. Derived prose — the deterministic summary, a
finding's sentence — is rewritten through the case's own substitution table,
so it cannot come to quote a value the table above it no longer shows.
"""

from __future__ import annotations

from vigitwin.review_html.evidence.publication import (
    ApprovedText,
    DisclosureCode,
    PublicationApprovals,
    PublicationPolicy,
)
from tests.test_review_html.conftest import (
    case_with_human_text,
    masking_policy,
    publication_page,
    scramble,
    worked_example_case,
)


def _approval(path: str, **kw) -> ApprovedText:
    base = {
        "example_id": "se-twins",
        "field_path": path,
        "approved_by": "reviewer",
        "approved_at": "2026-01-01",
    }
    base.update(kw)
    return ApprovedText(**base)


def _all_approved() -> PublicationApprovals:
    return PublicationApprovals(
        approved_texts=[
            _approval("title", replacement="A worked example"),
            _approval("lede", replacement="Two reports, one patient."),
            _approval("kicker", replacement="Example"),
            _approval("narrative_blocks[1].paragraphs", withhold=True),
            _approval("characteristics[1].left_evidence", withhold=True),
            _approval("characteristics[1].right_evidence", withhold=True),
        ]
    )


class TestHumanTextBlocksByDefault:
    def test_a_written_interpretation_is_not_published_unasked(self):
        result = scramble(publication_page(case_with_human_text()))
        codes = {f.code for f in result.report.findings}
        assert DisclosureCode.HUMAN_TEXT_REVIEW_REQUIRED in codes
        assert result.blocked

    def test_verbatim_narrative_snippets_are_not_published_unasked(self):
        result = scramble(publication_page(case_with_human_text()))
        codes = {f.code for f in result.report.findings}
        assert DisclosureCode.NARRATIVE_SNIPPET_REVIEW_REQUIRED in codes

    def test_a_curators_title_and_lede_need_a_decision_too(self):
        """A title naming a case is exactly where a place or a clinician
        ends up."""
        result = scramble(publication_page(case_with_human_text()))
        paths = {
            f.field_path
            for f in result.report.findings
            if f.code is DisclosureCode.CURATOR_TEXT_REVIEW_REQUIRED
        }
        assert paths == {"title", "lede", "kicker"}

    def test_the_finding_names_the_path_an_approval_is_written_against(self):
        result = scramble(publication_page(case_with_human_text()))
        finding = next(
            f
            for f in result.report.findings
            if f.code is DisclosureCode.HUMAN_TEXT_REVIEW_REQUIRED
        )
        assert finding.field_path == "narrative_blocks[1].paragraphs"

    def test_the_finding_quotes_the_text_so_a_reviewer_can_judge_it(self):
        result = scramble(publication_page(case_with_human_text()))
        finding = next(
            f
            for f in result.report.findings
            if f.code is DisclosureCode.HUMAN_TEXT_REVIEW_REQUIRED
        )
        assert "Uppsala" in finding.detail


class TestApprovedReplacements:
    def test_an_approved_text_clears_the_finding(self):
        result = scramble(
            publication_page(case_with_human_text()), approvals=_all_approved()
        )
        assert not result.blocked

    def test_the_replacement_is_what_is_published(self):
        result = scramble(
            publication_page(case_with_human_text()), approvals=_all_approved()
        )
        assert result.page.cases[0].title == "A worked example"

    def test_the_original_is_gone(self):
        result = scramble(
            publication_page(case_with_human_text()), approvals=_all_approved()
        )
        page = result.page.model_dump_json()
        assert "Uppsala" not in page

    def test_a_withheld_block_says_so_rather_than_vanishing(self):
        """A reader must be able to tell "nobody wrote this" from "this was
        not published"; a silently empty section says the first."""
        result = scramble(
            publication_page(case_with_human_text()), approvals=_all_approved()
        )
        headings = {b.heading for b in result.page.cases[0].narrative_blocks}
        assert "Human interpretation" in headings
        block = next(
            b
            for b in result.page.cases[0].narrative_blocks
            if b.heading == "Human interpretation"
        )
        assert block.paragraphs == [PublicationPolicy().withheld_text_notice]

    def test_a_withhold_with_replacement_text_is_rejected_outright(self):
        """Asking for both is an unresolved decision, not a preference."""
        try:
            _approval("title", replacement="x", withhold=True)
        except ValueError as exc:
            assert "exactly one decision" in str(exc)
        else:  # pragma: no cover - the validator must fire
            raise AssertionError("expected a validation error")

    def test_an_entry_deciding_nothing_is_rejected(self):
        """Neither replacement, withhold, nor approve_as_written."""
        try:
            _approval("title", replacement="  ")
        except ValueError as exc:
            assert "exactly one decision" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("expected a validation error")


class TestDerivedProseIsRewritten:
    def test_the_deterministic_summary_needs_no_approval(self):
        """It is generated from the assessment and holds no human's words."""
        result = scramble(publication_page(worked_example_case()))
        assert not result.blocked
        summary = next(
            b for b in result.page.cases[0].narrative_blocks if not b.is_human
        )
        assert summary.paragraphs

    @staticmethod
    def _summary_text(result) -> str:
        return " ".join(
            p
            for b in result.page.cases[0].narrative_blocks
            for p in b.paragraphs
        )

    def test_a_summary_naming_a_country_names_the_published_one(self):
        """The sentence names the anchor population, so a masked run has to
        rewrite it there as well as in the cell beside it."""
        result = scramble(
            publication_page(worked_example_case()), policy=masking_policy()
        )
        text = self._summary_text(result)
        assert "SE" not in text.split()
        assert "Country 1" in text

    def test_and_the_reported_one_where_countries_are_retained(self):
        """The default. The sentence and the context cell agree either way."""
        text = self._summary_text(
            scramble(publication_page(worked_example_case()))
        )
        assert "SE" in text.split()
