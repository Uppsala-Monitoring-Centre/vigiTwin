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
from pydantic import ValidationError

from vigitwin.review_html.evidence.models import FreqRowVM
from vigitwin.review_html.evidence.publication import (
    ApprovedText,
    DisclosureCode,
    DisclosureFinding,
    FindingResolution,
    PublicationApprovals,
    PublicationPolicy,
    build_key_record,
    digest_text,
    export_approvals_form,
    write_approvals_form,
)
from vigitwin.review_html.evidence.publication.approvals import (
    RENDERED_SCAN_PATH,
    merge_approvals_form,
    resolvable,
)
from tests.test_review_html.conftest import (
    case_with_human_text,
    publication_page,
    scramble,
    worked_example_case,
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

    def test_the_message_carries_the_digest_to_put_in_the_file(self):
        """The remedy is a sha256, and the only way to get one by hand is to
        reimplement digest_text and remember that a list and a string digest
        differently. So the finding names the value — in the *message*, which
        is what the CLI prints to stderr, and not only in the detail, which
        until now was the one place it appeared."""
        page = publication_page(case_with_human_text())
        approvals = _signed(export_approvals_form(page))
        edited = publication_page(case_with_human_text())
        edited.cases[0].title = "Two reports from the Uppsala clinic"
        result = scramble(edited, approvals=approvals)

        mismatch = next(
            f
            for f in result.report.findings
            if f.code is DisclosureCode.APPROVAL_DIGEST_MISMATCH
        )
        assert digest_text("Two reports from the Uppsala clinic") in (
            mismatch.message
        )
        assert "set text_sha256 to" in mismatch.message

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
    def test_a_second_run_merges_instead_of_refusing(self, tmp_path):
        """It used to raise. Findings change between runs, so needing a second
        form is the normal case rather than the mistake."""
        page = publication_page(case_with_human_text())
        out = tmp_path / "approvals.json"
        first = write_approvals_form(page, out)
        assert first.added
        second = write_approvals_form(page, out)
        assert second.added == 0
        assert second.refreshed == first.added

    def test_force_still_regenerates_from_scratch(self, tmp_path):
        page = publication_page(case_with_human_text())
        out = tmp_path / "approvals.json"
        write_approvals_form(page, out)
        written = write_approvals_form(page, out, force=True)
        assert written.refreshed == 0
        assert written.added

    def test_it_writes_json_the_model_shape_recognises(self, tmp_path):
        page = publication_page(case_with_human_text())
        written = write_approvals_form(page, tmp_path / "nested" / "form.json")
        data = json.loads(written.path.read_text(encoding="utf-8"))
        assert set(data) == {
            "approved_texts",
            "resolutions",
            "country_masking",
        }
        assert data["approved_texts"]


class TestTheFormAnswersFindingsThatFired:
    """``approved_texts`` is derivable from the page: every ``REVIEW`` field
    blocks categorically. A resolution answers a finding a check actually
    raised, so it needs the report — which is why the form was texts-only
    until it was given one."""

    @staticmethod
    def _small_count_page():
        case = worked_example_case()
        case.freq_rows = [
            FreqRowVM(
                scope_label="SE, 2024", n_drugs="40", n_aes="", n_both="2"
            )
        ]
        return publication_page(case)

    def test_without_a_report_it_offers_no_resolutions(self, tmp_path):
        """The pre-transform call, which is what lets a reviewer have the
        free-text form before the key store is opened."""
        page = publication_page(case_with_human_text())
        written = write_approvals_form(page, tmp_path / "form.json")
        data = json.loads(written.path.read_text(encoding="utf-8"))
        assert data["approved_texts"]
        assert data["resolutions"] == []

    def test_a_blocking_finding_gets_an_entry_pinned_to_its_detail(
        self, tmp_path
    ):
        page = self._small_count_page()
        result = scramble(page)
        written = write_approvals_form(
            page, tmp_path / "form.json", report=result.report
        )
        data = json.loads(written.path.read_text(encoding="utf-8"))
        entry = next(
            e
            for e in data["resolutions"]
            if e["code"] == "retained_country_small_count"
        )
        finding = next(
            f
            for f in result.report.findings
            if f.code is DisclosureCode.RETAINED_COUNTRY_SMALL_COUNT
        )
        assert entry["detail_sha256"] == digest_text(finding.detail)

    def test_signing_it_clears_the_run(self, tmp_path):
        """End to end: the generated entry is the one the gate consults."""
        page = self._small_count_page()
        written = write_approvals_form(
            page, tmp_path / "form.json", report=scramble(page).report
        )
        data = json.loads(written.path.read_text(encoding="utf-8"))
        for entry in data["resolutions"]:
            entry["approved_by"] = "reviewer"
            entry["approved_at"] = "2026-01-01"
        approvals = PublicationApprovals.model_validate(data)
        assert not scramble(page, approvals=approvals).blocked

    def test_the_entry_is_unsigned_and_so_does_not_validate(self, tmp_path):
        """A form that loaded as it came out of the generator would approve
        every finding in it."""
        page = self._small_count_page()
        written = write_approvals_form(
            page, tmp_path / "form.json", report=scramble(page).report
        )
        data = json.loads(written.path.read_text(encoding="utf-8"))
        with pytest.raises(ValidationError, match="unsigned entry is a form"):
            PublicationApprovals.model_validate(data)

    def test_it_names_both_remedies_for_a_retained_country(self, tmp_path):
        """Generating only the signature arm would quietly recommend
        publishing the country."""
        page = self._small_count_page()
        written = write_approvals_form(
            page, tmp_path / "form.json", report=scramble(page).report
        )
        data = json.loads(written.path.read_text(encoding="utf-8"))
        entry = next(
            e
            for e in data["resolutions"]
            if e["code"] == "retained_country_small_count"
        )
        assert "country_masking" in entry["rationale"]
        assert data["country_masking"] == []

    def test_a_free_text_finding_gets_no_second_remedy(self, tmp_path):
        """It already has an approved_texts entry, and a resolution would be
        a weaker way past a field that must be decided on its actual words."""
        page = publication_page(case_with_human_text())
        written = write_approvals_form(
            page, tmp_path / "form.json", report=scramble(page).report
        )
        data = json.loads(written.path.read_text(encoding="utf-8"))
        assert data["approved_texts"]
        assert data["resolutions"] == []

    def test_a_rendered_scan_finding_gets_no_entry(self):
        """Those are appended after partition has run, so a resolution
        written against one would never be consulted."""
        assert not resolvable(
            DisclosureFinding(
                code=DisclosureCode.RESIDUAL_ORIGINAL_VALUE,
                example_id="R1__R2",
                field_path=RENDERED_SCAN_PATH,
                message="an original value survived into the markup",
            )
        )


class TestMergingAFormThatIsAlreadySigned:
    """One rule: a signed entry is a record and is never touched; an unsigned
    entry is scaffolding and is regenerated."""

    def _fresh(self, digest="new"):
        return {
            "approved_texts": [
                {
                    "example_id": "S1",
                    "field_path": "title",
                    "approve_as_written": True,
                    "text_sha256": digest,
                    "approved_by": "",
                    "approved_at": "",
                    "rationale": "",
                }
            ],
            "resolutions": [],
            "country_masking": [],
        }

    def _signed(self, digest="old"):
        entry = dict(self._fresh(digest)["approved_texts"][0])
        entry["approved_by"] = "reviewer"
        entry["approved_at"] = "2026-01-01"
        return {
            "approved_texts": [entry],
            "resolutions": [],
            "country_masking": [],
        }

    def test_a_signed_entry_survives_verbatim(self):
        merged, stats = merge_approvals_form(
            self._signed(), self._fresh(), authoritative=["approved_texts"]
        )
        assert merged["approved_texts"] == self._signed()["approved_texts"]
        assert stats["kept_signed"] == 1
        assert stats["refreshed"] == 0

    def test_an_unsigned_entry_is_regenerated(self):
        """Keeping it would hand back a digest that no longer matches
        anything, which is a form guaranteed to fail on load."""
        stale = self._fresh("stale")
        merged, stats = merge_approvals_form(
            stale, self._fresh("current"), authoritative=["approved_texts"]
        )
        assert merged["approved_texts"][0]["text_sha256"] == "current"
        assert stats["refreshed"] == 1

    def test_a_signed_entry_the_run_no_longer_asks_for_is_kept(self):
        """It may be the only trace of a decision about a case that was
        removed from the manifest."""
        empty: dict[str, list[dict[str, object]]] = {
            "approved_texts": [],
            "resolutions": [],
            "country_masking": [],
        }
        merged, stats = merge_approvals_form(
            self._signed(), empty, authoritative=["approved_texts"]
        )
        assert len(merged["approved_texts"]) == 1
        assert stats["orphaned"] == 1

    def test_a_listing_the_form_never_generates_passes_through(self):
        """``country_masking`` is written by hand, because neither
        ``mask: true`` nor ``mask: false`` is a neutral entry to generate. So
        it is never compared, and never reported as orphaned."""
        held: dict[str, list[dict[str, object]]] = {
            "approved_texts": [],
            "resolutions": [],
            "country_masking": [
                {
                    "example_id": "S1",
                    "mask": True,
                    "decided_by": "reviewer",
                    "decided_at": "2026-01-01",
                    "rationale": "narrow population",
                }
            ],
        }
        merged, stats = merge_approvals_form(
            held,
            {"approved_texts": [], "resolutions": [], "country_masking": []},
            authoritative=["approved_texts"],
        )
        assert stats["orphaned"] == 0
        assert merged["country_masking"] == held["country_masking"]

    def test_a_signed_resolution_survives_the_pre_transform_write(
        self, tmp_path
    ):
        """That write has no report, so it generates no resolutions. Without
        the distinction between "raised nothing" and "did not look", it would
        report every signed resolution as orphaned on every run."""
        page = publication_page(case_with_human_text())
        out = tmp_path / "form.json"
        out.write_text(
            json.dumps(
                {
                    "approved_texts": [],
                    "resolutions": [
                        {
                            "code": "retained_country_small_count",
                            "example_id": "S1",
                            "field_path": "",
                            "detail_sha256": digest_text("SE, both count 2"),
                            "approved_by": "reviewer",
                            "approved_at": "2026-01-01",
                            "rationale": "wide enough",
                        }
                    ],
                    "country_masking": [],
                }
            ),
            encoding="utf-8",
        )
        written = write_approvals_form(page, out)
        assert written.orphaned == 0
        data = json.loads(out.read_text(encoding="utf-8"))
        assert len(data["resolutions"]) == 1

    def test_a_malformed_file_is_not_overwritten(self, tmp_path):
        """It may hold signatures; guessing at it is worse than stopping."""
        out = tmp_path / "form.json"
        out.write_text("{not json", encoding="utf-8")
        page = publication_page(case_with_human_text())
        with pytest.raises(ValueError, match="not readable as JSON"):
            write_approvals_form(page, out)
        assert out.read_text(encoding="utf-8") == "{not json"


class TestTheSignedRelease:
    """The one document-level decision, and the only thing that removes the
    candidate caveat from a published page.

    A command-line flag would have been cheaper to use and would have
    recorded nobody; set once in a script it would keep the candidate wording
    from ever appearing again. So it is a record like every other decision in
    this layer — a name, a date, a reason, and a pin on the document that was
    actually read.
    """

    @staticmethod
    def _clear_page():
        """A page with nothing outstanding, which is when a release is
        offered: the form holds the release back until it is the decision
        that remains."""
        return publication_page(worked_example_case())

    def _form(self, tmp_path, page=None):
        page = page or self._clear_page()
        result = scramble(page)
        written = write_approvals_form(
            page,
            tmp_path / "form.json",
            report=result.report,
            candidate_digest=result.candidate_digest,
        )
        return page, result, json.loads(
            written.path.read_text(encoding="utf-8")
        )

    def test_the_form_scaffolds_it_with_the_digest_filled_in(self, tmp_path):
        """The signer types a name and a date. They never type a sha256."""
        _, result, data = self._form(tmp_path)
        assert data["release"]["candidate_sha256"] == result.candidate_digest
        assert data["release"]["released_by"] == ""

    def test_it_is_not_offered_while_anything_is_outstanding(self, tmp_path):
        """Signing a release for a candidate that is about to change would
        pin a document nobody will publish."""
        page = publication_page(case_with_human_text())
        result = scramble(page)
        written = write_approvals_form(
            page,
            tmp_path / "form.json",
            report=result.report,
            candidate_digest=result.candidate_digest,
        )
        data = json.loads(written.path.read_text(encoding="utf-8"))
        assert "release" not in data
        assert data["approved_texts"]

    def test_unsigned_it_loads_as_no_release_at_all(self, tmp_path):
        """Not as an error. A candidate is the ordinary state, so a form
        offering a release has to stay loadable while nobody has signed it —
        otherwise the sign-then-re-run loop stops the moment it is offered."""
        _, _, data = self._form(tmp_path)
        approvals = PublicationApprovals.model_validate(data)
        assert approvals.release is None

    def test_a_half_made_decision_still_raises(self):
        """A name with no date is not a candidate state, it is a mistake."""
        with pytest.raises(ValidationError, match="unsigned entry is a form"):
            PublicationApprovals.model_validate(
                {
                    "release": {
                        "released_by": "A. Reviewer",
                        "released_at": "",
                        "candidate_sha256": digest_text("x"),
                    }
                }
            )

    def test_signed_it_clears_the_candidate_caveat(self, tmp_path):
        page, _, data = self._form(tmp_path)
        data["release"]["released_by"] = "A. Reviewer"
        data["release"]["released_at"] = "2026-11-19"
        released = scramble(
            page, approvals=PublicationApprovals.model_validate(data)
        )
        assert released.page.approved_for_release
        assert not released.release_is_stale

    def test_a_stale_pin_does_not_release(self, tmp_path):
        """The signature was given for a document this no longer is."""
        page, _, data = self._form(tmp_path)
        data["release"].update(
            released_by="A. Reviewer",
            released_at="2026-11-19",
            candidate_sha256=digest_text("some other document"),
        )
        result = scramble(
            page, approvals=PublicationApprovals.model_validate(data)
        )
        assert not result.page.approved_for_release
        assert result.release_is_stale

    def test_a_signed_release_survives_a_form_regeneration(self, tmp_path):
        page, _, data = self._form(tmp_path)
        data["release"]["released_by"] = "A. Reviewer"
        data["release"]["released_at"] = "2026-11-19"
        out = tmp_path / "form.json"
        out.write_text(json.dumps(data), encoding="utf-8")
        result = scramble(
            page, approvals=PublicationApprovals.model_validate(data)
        )
        written = write_approvals_form(
            page,
            out,
            report=result.report,
            candidate_digest=result.candidate_digest,
        )
        assert written.kept_signed
        after = json.loads(out.read_text(encoding="utf-8"))
        assert after["release"]["released_by"] == "A. Reviewer"

    def test_an_unsigned_release_is_refreshed_with_the_current_digest(
        self, tmp_path
    ):
        """Keeping a stale digest on an unsigned entry would hand back a form
        guaranteed to fail the gate."""
        page, result, _ = self._form(tmp_path)
        out = tmp_path / "form.json"
        data = json.loads(out.read_text(encoding="utf-8"))
        data["release"]["candidate_sha256"] = "stale"
        out.write_text(json.dumps(data), encoding="utf-8")
        write_approvals_form(
            page,
            out,
            report=result.report,
            candidate_digest=result.candidate_digest,
        )
        after = json.loads(out.read_text(encoding="utf-8"))
        assert after["release"]["candidate_sha256"] == result.candidate_digest

    def test_the_pre_transform_write_leaves_a_signed_release_alone(
        self, tmp_path
    ):
        """It has no digest, so it did not look at the release."""
        page, _, data = self._form(tmp_path)
        data["release"]["released_by"] = "A. Reviewer"
        data["release"]["released_at"] = "2026-11-19"
        out = tmp_path / "form.json"
        out.write_text(json.dumps(data), encoding="utf-8")
        written = write_approvals_form(page, out)
        assert written.orphaned == 0
        after = json.loads(out.read_text(encoding="utf-8"))
        assert after["release"]["released_by"] == "A. Reviewer"


class TestAVoidedReleaseIsResetNotLeftToBeEdited:
    """The loop has to terminate.

    A signed release whose pin no longer matches was kept by the merge, as
    every signed entry is — so regenerating the form could not fix it, while
    the refusal told the reviewer to regenerate the form. Running the
    documented remedy produced the identical error, forever.
    """

    def _signed_release_for(self, page, digest):
        return {
            "approved_texts": [],
            "resolutions": [],
            "country_masking": [],
            "release": {
                "released_by": "A. Reviewer",
                "released_at": "2026-11-19",
                "rationale": "read in full",
                "candidate_sha256": digest,
            },
        }

    def _fresh(self, digest):
        return {
            "approved_texts": [],
            "resolutions": [],
            "country_masking": [],
            "release": {
                "released_by": "",
                "released_at": "",
                "rationale": "",
                "candidate_sha256": digest,
            },
        }

    def test_a_matching_release_is_kept(self):
        merged, stats = merge_approvals_form(
            self._signed_release_for(None, "same"),
            self._fresh("same"),
            authoritative=["release"],
        )
        assert merged["release"]["released_by"] == "A. Reviewer"
        assert stats["kept_signed"] == 1

    def test_a_voided_release_is_reset_to_an_unsigned_skeleton(self):
        merged, stats = merge_approvals_form(
            self._signed_release_for(None, "old"),
            self._fresh("new"),
            authoritative=["release"],
        )
        assert merged["release"]["candidate_sha256"] == "new"
        assert merged["release"]["released_by"] == ""
        assert stats["release_reset"] == 1

    def test_the_reset_forces_a_fresh_date_not_just_a_new_digest(self):
        """Editing the digest in place would leave a record saying the
        reviewer read the new document on the day they read the old one."""
        merged, _ = merge_approvals_form(
            self._signed_release_for(None, "old"),
            self._fresh("new"),
            authoritative=["release"],
        )
        assert merged["release"]["released_at"] == ""

    def test_a_call_that_generated_no_release_voids_nothing(self):
        """Not looking is not grounds for voiding somebody's signature — the
        pre-transform write has no digest to compare against."""
        held = self._signed_release_for(None, "old")
        merged, stats = merge_approvals_form(
            held,
            {"approved_texts": [], "resolutions": [], "country_masking": []},
            authoritative=["approved_texts"],
        )
        assert merged["release"] == held["release"]
        assert not stats.get("release_reset")

    def test_the_write_reports_the_reset(self, tmp_path):
        page = publication_page(worked_example_case())
        result = scramble(page)
        out = tmp_path / "form.json"
        out.write_text(
            json.dumps(self._signed_release_for(None, "a-different-document")),
            encoding="utf-8",
        )
        written = write_approvals_form(
            page,
            out,
            report=result.report,
            candidate_digest=result.candidate_digest,
        )
        assert written.release_reset
        after = json.loads(out.read_text(encoding="utf-8"))
        assert after["release"]["candidate_sha256"] == result.candidate_digest


class TestAWorkingResolutionIsNotReportedAsStray:
    """"Orphaned" has to mean obsolete, or it means nothing.

    A resolution that works moves its finding out of `report.findings` and
    into `report.resolved`. A fresh form built from `findings` alone
    therefore regenerated no entry for it, and the merge reported every
    *working* resolution as orphaned on the next run — which reads as "tidy
    me up", and deleting one re-blocks the run on the finding it was
    clearing.
    """

    @staticmethod
    def _page_with_a_small_count():
        case = worked_example_case()
        case.freq_rows = [
            FreqRowVM(
                scope_label="SE, 2024", n_drugs="40", n_aes="", n_both="2"
            )
        ]
        return publication_page(case)

    def _signed_for(self, page):
        finding = scramble(page).report.findings[0]
        return PublicationApprovals(
            resolutions=[
                FindingResolution(
                    code=finding.code.value,
                    example_id=finding.example_id,
                    field_path=finding.field_path,
                    detail_sha256=digest_text(finding.detail),
                    approved_by="A. Reviewer",
                    approved_at="2026-11-19",
                    rationale="the population is wide enough",
                )
            ]
        )

    def test_it_is_kept_not_orphaned(self, tmp_path):
        page = self._page_with_a_small_count()
        approvals = self._signed_for(page)
        result = scramble(page, approvals=approvals)
        assert result.report.resolved and not result.report.findings

        out = tmp_path / "form.json"
        out.write_text(approvals.model_dump_json(), encoding="utf-8")
        written = write_approvals_form(
            page,
            out,
            report=result.report,
            candidate_digest=result.candidate_digest,
        )
        assert written.kept_signed == 1
        assert written.orphaned == 0

    def test_the_entry_survives_the_regeneration(self, tmp_path):
        """Which is what makes the next run still clear."""
        page = self._page_with_a_small_count()
        approvals = self._signed_for(page)
        out = tmp_path / "form.json"
        out.write_text(approvals.model_dump_json(), encoding="utf-8")
        write_approvals_form(
            page,
            out,
            report=scramble(page, approvals=approvals).report,
            candidate_digest=scramble(page, approvals=approvals).candidate_digest,
        )
        reloaded = PublicationApprovals.model_validate_json(
            out.read_text(encoding="utf-8")
        )
        assert len(reloaded.resolutions) == 1
        assert not scramble(page, approvals=reloaded).blocked

    def test_an_unsigned_advisory_still_gets_no_entry(self, tmp_path):
        """Regenerating for resolved findings must not reintroduce the
        advisory opt-in by the back door: an advisory nobody signed for is
        still not offered one."""
        page = publication_page(worked_example_case())
        result = scramble(
            page,
            policy=PublicationPolicy(
                date_offset_min_days=0,
                date_offset_max_days=0,
                decouple_clinical_dates=False,
            ),
        )
        assert result.report.advisories
        form = export_approvals_form(page, report=result.report)
        assert form["resolutions"] == []
