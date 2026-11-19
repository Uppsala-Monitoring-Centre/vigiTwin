"""Approving the manifest's own text, instead of restating it.

Every ``REVIEW`` field blocks categorically, because the policy cannot tell a
safe curator title from one naming a clinician. What that gate must not do is
make a curator type the same words twice: the fields that block are the ones
they just wrote in the manifest, and copying them into the approvals file
costs the words again and publishes them *verbatim*, outside the substitution
table, which is worse than not copying them at all.

So an approval can instead point at the text already there, pinned to a
digest of it. These check the three properties that makes it safe to prefer:
the pin holds the approval to the text it was given for, the text still goes
through the substitution table, and an unsigned form is not an approval.
"""

from __future__ import annotations

import json

import pytest

from llm_dup_dev.review_html.evidence.publication import (
    ApprovedText,
    DisclosureCode,
    PublicationApprovals,
    build_key_record,
    digest_text,
    export_approvals_form,
    write_approvals_form,
)
from tests.test_review_html.conftest import (
    case_with_human_text,
    publication_page,
    scramble,
)

#: The four fields ``case_with_human_text`` leaves for a reviewer.
BLOCKING_PATHS = (
    "kicker",
    "title",
    "lede",
    "narrative_blocks[1].paragraphs",
)


def _signed(form: dict[str, object], **kw: object) -> PublicationApprovals:
    """The form as a reviewer hands it back."""
    texts = []
    for entry in form["approved_texts"]:  # type: ignore[union-attr]
        signed = dict(entry)
        signed.update(
            {
                "approved_by": "reviewer",
                "approved_at": "2026-01-01",
                "rationale": "Checked against the side-by-side review.",
            }
        )
        signed.update(kw)
        texts.append(signed)
    return PublicationApprovals.model_validate({"approved_texts": texts})


class TestTheFormNamesWhatBlocks:
    def test_one_entry_per_field_a_reviewer_owes_a_decision_on(self):
        page = publication_page(case_with_human_text())
        form = export_approvals_form(page)
        paths = {e["field_path"] for e in form["approved_texts"]}
        assert paths == set(BLOCKING_PATHS) | {
            p for p in paths if p.startswith("characteristics[")
        }
        assert set(BLOCKING_PATHS) <= paths

    def test_the_generated_summary_is_not_in_it(self):
        """vigiTwin's own account holds no human's words."""
        page = publication_page(case_with_human_text())
        paths = {
            e["field_path"] for e in export_approvals_form(page)["approved_texts"]
        }
        assert "narrative_blocks[0].paragraphs" not in paths

    def test_every_entry_approves_as_written_and_carries_a_digest(self):
        page = publication_page(case_with_human_text())
        for entry in export_approvals_form(page)["approved_texts"]:
            assert entry["approve_as_written"] is True
            assert len(str(entry["text_sha256"])) == 64
            assert "replacement" not in entry  # the form carries no text

    def test_the_form_is_unsigned_so_it_is_not_yet_an_approval(self):
        """A form that validated as generated would make the signature a
        formality."""
        page = publication_page(case_with_human_text())
        form = export_approvals_form(page)
        with pytest.raises(ValueError, match="unsigned entry is a form"):
            PublicationApprovals.model_validate(form)

    def test_signed_it_clears_the_block(self):
        page = publication_page(case_with_human_text())
        form = export_approvals_form(page)
        result = scramble(page, approvals=_signed(form))
        assert not result.blocked, result.report.summary()


class TestThePinHoldsTheApprovalToTheText:
    def test_an_edited_manifest_blocks_again(self):
        page = publication_page(case_with_human_text())
        approvals = _signed(export_approvals_form(page))

        edited = publication_page(case_with_human_text())
        edited.cases[0].title = "Two reports from the Uppsala clinic"
        result = scramble(edited, approvals=approvals)

        assert result.blocked
        codes = {f.code for f in result.report.findings}
        assert DisclosureCode.APPROVAL_DIGEST_MISMATCH in codes
        mismatch = next(
            f
            for f in result.report.findings
            if f.code is DisclosureCode.APPROVAL_DIGEST_MISMATCH
        )
        assert mismatch.field_path == "title"
        assert "digests to" in mismatch.detail

    def test_the_edited_text_is_not_published_in_the_meantime(self):
        """The old approval does not carry, and neither does the old text."""
        page = publication_page(case_with_human_text())
        approvals = _signed(export_approvals_form(page))
        edited = publication_page(case_with_human_text())
        edited.cases[0].title = "Two reports from the Uppsala clinic"
        result = scramble(edited, approvals=approvals)
        assert result.page.cases[0].title.endswith("Uppsala clinic")

    def test_a_digest_distinguishes_a_string_from_a_one_item_list(self):
        assert digest_text("a") != digest_text(["a"])

    def test_the_digest_is_stable_across_calls(self):
        assert digest_text(["a", "b"]) == digest_text(["a", "b"])


class TestApprovedTextStillGoesThroughTheTable:
    """The reason to prefer this over copying the same words into
    ``replacement``: a value the curator quoted comes out transformed."""

    @staticmethod
    def _dated_page():
        page = publication_page(case_with_human_text())
        page.cases[0].title = "Both reports reached the centre on 2024-02-20"
        return page

    def test_a_date_the_curator_quoted_is_shifted(self):
        page = self._dated_page()
        form = export_approvals_form(page)
        result = scramble(page, approvals=_signed(form))
        published = result.page.cases[0].title
        if result.transforms[0].date_offset_days == 0:
            pytest.skip("this salt left the dates where they were")
        assert "2024-02-20" not in published
        assert published.startswith("Both reports reached the centre on ")

    def test_the_same_words_as_a_replacement_publish_the_real_date(self):
        """Not a defect: a replacement is the reviewer's own words, and
        rewriting them would publish something nobody approved. It is the
        reason copying the manifest's text across is the worse habit."""
        page = self._dated_page()
        form = export_approvals_form(page)
        copied = _signed(
            form,
            approve_as_written=False,
            text_sha256="",
            replacement="Both reports reached the centre on 2024-02-20",
        )
        result = scramble(page, approvals=copied)
        assert "2024-02-20" in result.page.cases[0].title


class TestItIsRecordedAsItsOwnDecision:
    def test_the_key_record_separates_approved_from_replaced(self):
        page = publication_page(case_with_human_text())
        result = scramble(page, approvals=_signed(export_approvals_form(page)))
        record = build_key_record(result)
        case = record.cases[0]
        assert set(BLOCKING_PATHS) <= set(case.approved_as_written_paths)
        assert "title" not in case.replaced_paths


class TestTheValidatorKeepsTheDecisionExplicit:
    def _entry(self, **kw):
        base = {
            "example_id": "se-twins",
            "field_path": "title",
            "approved_by": "reviewer",
            "approved_at": "2026-01-01",
        }
        base.update(kw)
        return ApprovedText(**base)

    def test_approve_as_written_without_a_digest_is_rejected(self):
        with pytest.raises(ValueError, match="outlive an edit"):
            self._entry(approve_as_written=True)

    def test_a_digest_without_approve_as_written_is_rejected(self):
        with pytest.raises(ValueError, match="says nothing about"):
            self._entry(replacement="x", text_sha256="a" * 64)

    def test_approving_as_written_and_replacing_at_once_is_rejected(self):
        with pytest.raises(ValueError, match="exactly one decision"):
            self._entry(
                approve_as_written=True,
                text_sha256="a" * 64,
                replacement="x",
            )

    def test_an_unsigned_entry_is_rejected(self):
        with pytest.raises(ValueError, match="unsigned entry is a form"):
            ApprovedText(
                example_id="se-twins",
                field_path="title",
                replacement="x",
                approved_by="  ",
                approved_at="2026-01-01",
            )


class TestWritingTheForm:
    def test_it_refuses_to_overwrite_signatures(self, tmp_path):
        page = publication_page(case_with_human_text())
        out = tmp_path / "approvals.json"
        write_approvals_form(page, out)
        with pytest.raises(FileExistsError, match="signed approvals"):
            write_approvals_form(page, out)
        write_approvals_form(page, out, force=True)

    def test_it_writes_json_the_model_shape_recognises(self, tmp_path):
        page = publication_page(case_with_human_text())
        out = write_approvals_form(page, tmp_path / "nested" / "form.json")
        data = json.loads(out.read_text(encoding="utf-8"))
        assert set(data) == {
            "approved_texts",
            "resolutions",
            "country_masking",
        }
        assert data["approved_texts"]
