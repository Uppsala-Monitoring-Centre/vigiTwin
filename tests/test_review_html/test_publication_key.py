"""The two records, and the line between them.

The secure key record holds the offsets and the full original → published
table, which is to say it holds real ages, dates, countries and identifiers.
The public manifest holds the policy and the offset *bounds*, and nothing
that varies with the values themselves. Neither holds the salt. The tests
that matter are the negative ones: nothing in the publication directory may
carry a seed, a salt, an offset or an original value.
"""

from __future__ import annotations

import json

from llm_dup_dev.review_html.render import EVIDENCE_DOCUMENT_FILENAME
from llm_dup_dev.review_html.evidence.publication import (
    MANIFEST_FILENAME,
    REVIEW_FILENAME,
    PublicationApprovals,
    ReleaseApproval,
    PublicationPolicy,
    build_key_record,
    build_publication_manifest,
    write_publication_evidence,
    write_scrambling_key,
    write_side_by_side,
)
from tests.test_review_html.conftest import (
    _TEST_SALT,
    case_with_human_text,
    publication_page,
    scramble,
    secrets,
    worked_example_case,
)


class TestTheSecureKeyRecord:
    def test_it_records_the_date_offset(self):
        result = scramble(publication_page(worked_example_case()))
        record = build_key_record(result)
        assert record.cases[0].date_offset_days == (
            result.transforms[0].date_offset_days
        )

    def test_it_records_the_numeric_offsets_in_words(self):
        result = scramble(publication_page(worked_example_case()))
        record = build_key_record(result)
        assert "age:years" in record.cases[0].numeric_offsets
        assert "years" in record.cases[0].numeric_offsets["age:years"]

    def test_it_distinguishes_transformed_from_unchanged(self):
        """Three outcomes, and a reviewer needs all three: moved, transformed
        and unchanged, and a transform that could not run at all."""
        result = scramble(
            publication_page(worked_example_case()),
            policy=PublicationPolicy(
                date_offset_min_days=0,
                date_offset_max_days=0,
                decouple_clinical_dates=False,
            ),
        )
        record = build_key_record(result)
        case = record.cases[0]
        dates = [s for s in case.substitutions if s.kind.startswith("date:")]
        assert dates and all(not s.changed for s in dates)
        assert case.date_pattern_unchanged
        assert case.dates_changed == 0
        assert case.dates_total == len(dates)

    def test_it_records_where_each_value_is_shown(self):
        """The substitution table is keyed by value; the cell records are
        keyed by place, and a reviewer needs both."""
        result = scramble(publication_page(worked_example_case()))
        case = build_key_record(result).cases[0]
        assert case.cells
        assert any(c.path.startswith("characteristics[") for c in case.cells)
        assert any(c.changed for c in case.cells)

    def test_it_records_the_whole_substitution_table(self):
        result = scramble(publication_page(worked_example_case()))
        record = build_key_record(result)
        originals = {s.original for s in record.cases[0].substitutions}
        assert {"2023-11-13", "SE", "81 years"} <= originals

    def test_it_maps_the_internal_identifier_to_the_published_one(self):
        """Which is the only way back from a published supplement to the
        internal review it came from."""
        result = scramble(publication_page(worked_example_case()))
        record = build_key_record(result)
        assert record.cases[0].example_id == "R1__R2"
        assert record.cases[0].published_example_id == "pair-01"

    def test_it_names_the_salt_without_holding_it(self):
        """A file holding both the salt and the mappings it produced could
        regenerate every past and future supplement."""
        result = scramble(publication_page(worked_example_case()))
        record = build_key_record(result)
        assert record.key_id == secrets().key_id
        assert _TEST_SALT not in record.model_dump_json()

    def test_it_records_the_findings_a_reviewer_was_asked_about(self):
        result = scramble(publication_page(worked_example_case()))
        record = build_key_record(result)
        assert len(record.findings) == len(result.report.findings)

    def test_it_writes_as_json(self, tmp_path):
        result = scramble(publication_page(worked_example_case()))
        path = write_scrambling_key(result, tmp_path / "secure" / "key.json")
        assert json.loads(path.read_text())["schema_version"] == "1.0"


class TestThePublicManifest:
    def test_it_records_the_policy_version_and_the_key_id(self):
        result = scramble(publication_page(worked_example_case()))
        manifest = build_publication_manifest(result)
        assert manifest.policy_version == "2.0"
        assert manifest.key_id == secrets().key_id

    def test_it_records_bounds_and_not_offsets(self):
        """A recorded offset is an invitation to subtract it."""
        result = scramble(publication_page(worked_example_case()))
        manifest = build_publication_manifest(result)
        payload = manifest.model_dump_json()
        assert manifest.date_offset_bound_days == (30, 400)
        assert manifest.clinical_offset_bound_days == (30, 1826)
        offset = result.transforms[0].date_offset_days
        assert offset is not None
        assert f'"{offset}"' not in payload

    def test_the_key_record_carries_the_survivor_counts(self):
        """Secure-side, because the number says how much the salt was
        actually doing for this case — which is an audit question."""
        from llm_dup_dev.review_html.evidence.publication.key import (
            build_key_record,
        )

        thin = build_key_record(
            scramble(
                publication_page(worked_example_case()),
                policy=PublicationPolicy(
                    date_offset_min_days=0,
                    date_offset_max_days=0,
                    decouple_clinical_dates=False,
                ),
            )
        )
        assert thin.cases[0].report_offset_survivors == 1
        wide = build_key_record(scramble(publication_page(worked_example_case())))
        assert wide.cases[0].report_offset_survivors is None

    def test_it_carries_no_per_kind_value_profile(self):
        """The counts are gone, and the reasoning is worth keeping here.

        They were the one field that gave a reader of the *manifest alone*
        something the supplement did not already give them: anyone holding the
        pages can see every transformed value and very nearly count them. For
        a single-case supplement the run-level aggregate is that case's own
        profile, and ``date:day -> 16`` is close to a fingerprint.
        """
        result = scramble(publication_page(worked_example_case()))
        manifest = build_publication_manifest(result)
        assert not hasattr(manifest, "counts")
        payload = manifest.model_dump_json()
        assert "values_transformed" not in payload
        assert "date:day" not in payload

    def test_it_says_nothing_about_whether_a_value_moved(self):
        """A published value may equal its original, and the manifest must not
        be the thing that tells a reader which case that was."""
        unmoved = scramble(
            publication_page(worked_example_case()),
            policy=PublicationPolicy(
                date_offset_min_days=0,
                date_offset_max_days=0,
                decouple_clinical_dates=False,
            ),
        )
        assert unmoved.transforms[0].dates_changed == 0
        assert unmoved.transforms[0].date_pattern_unchanged
        payload = build_publication_manifest(unmoved).model_dump_json()
        # The advisory that says so is secure-side; the public manifest
        # reports only that the review cleared.
        assert "date_pattern_unchanged" not in payload
        assert "unchanged" not in payload

    def test_it_names_no_value(self):
        result = scramble(publication_page(worked_example_case()))
        manifest = build_publication_manifest(result)
        payload = manifest.model_dump_json()
        for original in ("SE", "2023-11-13", "81 years", "R1"):
            assert original not in payload

    def test_it_states_that_the_output_is_a_candidate(self):
        result = scramble(publication_page(worked_example_case()))
        manifest = build_publication_manifest(result)
        assert "candidate for disclosure review" in manifest.disclosure.statement
        assert "not a document certified" in manifest.disclosure.statement

    def test_it_says_whether_the_review_was_cleared(self):
        result = scramble(publication_page(worked_example_case()))
        assert build_publication_manifest(result).disclosure.cleared


class TestNothingSensitiveReachesThePublicationDirectory:
    def test_no_salt_seed_offset_or_original_value_is_written(self, tmp_path):
        result = scramble(publication_page(worked_example_case()))
        out = write_publication_evidence(result, tmp_path / "publication")
        written = "\n".join(
            path.read_text() for path in out.rglob("*") if path.is_file()
        )
        offset = result.transforms[0].date_offset_days
        assert _TEST_SALT not in written
        assert secrets().seed("1.0", "dates", "R1__R2") not in written
        for original in ("2023-11-13", "2024-02-20", "81 years", "R1__R2"):
            assert original not in written, original
        assert f"offset_days: {offset}" not in written

    def test_the_key_is_written_by_a_separate_call_to_a_separate_path(
        self, tmp_path
    ):
        """The one way to be sure the mappings never land beside the
        supplement is for the code that writes it never to hold them."""
        result = scramble(publication_page(worked_example_case()))
        out = write_publication_evidence(result, tmp_path / "publication")
        assert not list(out.glob("*key*"))
        write_scrambling_key(result, tmp_path / "secure" / "key.json")
        assert (tmp_path / "secure" / "key.json").exists()

    def test_the_manifest_and_the_document_are_the_only_extra_files(
        self, tmp_path
    ):
        """A closed inventory, which is the point of it — not a count.

        The combined document joined the directory when it became a default;
        nothing else may. The invariant that actually matters is the test
        above, which reads every file written and refuses to find anything
        the key record holds, and it extends to new files on its own.
        """
        result = scramble(publication_page(worked_example_case()))
        out = write_publication_evidence(result, tmp_path / "publication")
        names = sorted(p.name for p in out.iterdir())
        assert names == sorted(
            [
                MANIFEST_FILENAME,
                EVIDENCE_DOCUMENT_FILENAME,
                "index.html",
                "pair-01.html",
            ]
        )

    def test_the_manifest_names_the_document_beside_it(self, tmp_path):
        """Written by the same call, so the two cannot drift."""
        result = scramble(publication_page(worked_example_case()))
        out = write_publication_evidence(result, tmp_path / "publication")
        manifest = json.loads((out / MANIFEST_FILENAME).read_text())
        assert manifest["combined_document"] == EVIDENCE_DOCUMENT_FILENAME
        assert (out / manifest["combined_document"]).exists()

    def test_the_manifest_names_no_path_outside_its_directory(self, tmp_path):
        """A run may also write a copy anywhere on disk, including the secure
        side. The manifest describes the directory it sits in."""
        result = scramble(publication_page(worked_example_case()))
        elsewhere = tmp_path / "secure" / "supplement.html"
        out = write_publication_evidence(
            result, tmp_path / "publication", single_document=elsewhere
        )
        payload = (out / MANIFEST_FILENAME).read_text()
        assert elsewhere.exists()
        assert "secure" not in payload
        assert str(elsewhere) not in payload


class TestWhereTheReleaseIsRecorded:
    """Who released the document, and when — in the audit artifact.

    The public manifest carries the fact and the date. It does not carry the
    reviewer's name or their rationale: the rationale may name real cases,
    exactly as a finding resolution's does, and publishing an individual's
    name in an automated artifact is a disclosure decision of its own that
    belongs in the manuscript rather than here.
    """

    def _released(self):
        page = publication_page(worked_example_case())
        candidate = scramble(page)
        return scramble(
            page,
            approvals=PublicationApprovals(
                release=ReleaseApproval(
                    released_by="A. Reviewer",
                    released_at="2026-11-19",
                    rationale="read in full against the disclosure checklist",
                    candidate_sha256=candidate.candidate_digest,
                )
            ),
        )

    def test_the_key_record_holds_the_whole_entry(self):
        record = build_key_record(self._released())
        assert record.release is not None
        assert record.release.released_by == "A. Reviewer"
        assert "checklist" in record.release.rationale

    def test_the_public_manifest_says_cleared_and_when(self):
        manifest = build_publication_manifest(self._released())
        assert manifest.disclosure.approved_for_release
        assert manifest.disclosure.released_at == "2026-11-19"

    def test_the_public_manifest_names_nobody(self):
        payload = build_publication_manifest(self._released()).model_dump_json()
        assert "A. Reviewer" not in payload
        assert "checklist" not in payload

    def test_the_statement_stops_calling_it_a_candidate(self):
        statement = build_publication_manifest(
            self._released()
        ).disclosure.statement
        assert "cleared for public sharing" in statement
        assert "candidate for disclosure review" not in statement

    def test_a_candidate_still_says_it_is_one(self):
        statement = build_publication_manifest(
            scramble(publication_page(worked_example_case()))
        ).disclosure.statement
        assert "candidate for disclosure review" in statement

    def test_cleared_and_released_are_different_claims(self):
        """``cleared`` means the automated gate left nothing unresolved. A
        candidate is routinely cleared and not released; that is every
        document awaiting review."""
        candidate = build_publication_manifest(
            scramble(publication_page(worked_example_case()))
        ).disclosure
        assert candidate.cleared
        assert not candidate.approved_for_release


class TestThePageAndTheManifestNeverDisagree:
    """Two prose sites say what the document is. A reader may see either, so
    an edit to one that is not made to the other is the failure this holds
    shut."""

    @staticmethod
    def _rendered(result) -> str:
        from llm_dup_dev.review_html.render import render_evidence_page

        return render_evidence_page(result.page)

    def _released(self):
        page = publication_page(worked_example_case())
        candidate = scramble(page)
        return scramble(
            page,
            approvals=PublicationApprovals(
                release=ReleaseApproval(
                    released_by="A. Reviewer",
                    released_at="2026-11-19",
                    candidate_sha256=candidate.candidate_digest,
                )
            ),
        )

    def test_a_candidate_says_candidate_in_both(self):
        result = scramble(publication_page(worked_example_case()))
        page = self._rendered(result)
        statement = build_publication_manifest(result).disclosure.statement
        assert "candidate for disclosure review" in page
        assert "candidate for disclosure review" in statement

    def test_a_released_document_says_cleared_in_both(self):
        result = self._released()
        page = self._rendered(result)
        statement = build_publication_manifest(result).disclosure.statement
        assert "Cleared for public sharing" in page
        assert "cleared for public sharing" in statement
        assert "candidate for disclosure review" not in page


class TestTheReviewerCanReadWhatTheyAreSigningFor:
    """A release is a decision about the whole compiled document.

    The published copy of that document is written only by a run that has
    already cleared, so before the decision is made — which is every run that
    matters here — it does not exist. The review directory is written
    unconditionally, so this is where it has to be.
    """

    @staticmethod
    def _collection():
        page = publication_page(
            case_with_human_text(), worked_example_case()
        )
        page.intro = "Ten report pairs adjudicated for the 2026 study."
        page.note = "Displayed values are transformed."
        return page

    def test_it_is_written_even_when_the_run_is_blocked(self, tmp_path):
        page = self._collection()
        result = scramble(page)
        assert result.blocked
        out = write_side_by_side(page, result, tmp_path / "review")
        assert (out / "publication" / EVIDENCE_DOCUMENT_FILENAME).exists()
        assert (out / "internal" / EVIDENCE_DOCUMENT_FILENAME).exists()

    def test_it_holds_every_example(self, tmp_path):
        page = self._collection()
        out = write_side_by_side(page, scramble(page), tmp_path / "review")
        html = (out / "publication" / EVIDENCE_DOCUMENT_FILENAME).read_text(
            encoding="utf-8"
        )
        assert html.count('<section class="case"') == 2

    def test_it_carries_the_collections_introduction(self, tmp_path):
        """Which is what a reviewer is signing for as much as the cases, and
        which the review subtree used not to render at all."""
        page = self._collection()
        out = write_side_by_side(page, scramble(page), tmp_path / "review")
        html = (out / "publication" / EVIDENCE_DOCUMENT_FILENAME).read_text(
            encoding="utf-8"
        )
        assert "Ten report pairs adjudicated" in html
        assert "Displayed values are transformed" in html

    def test_the_internal_copy_shows_the_untransformed_introduction(
        self, tmp_path
    ):
        """The point of a side-by-side: the publication copy's intro has been
        through the substitution table and the internal one has not."""
        page = self._collection()
        page.intro = "Both describe the 2023-11-13 case."
        result = scramble(page)
        out = write_side_by_side(page, result, tmp_path / "review")
        internal = (out / "internal" / EVIDENCE_DOCUMENT_FILENAME).read_text(
            encoding="utf-8"
        )
        published = (
            out / "publication" / EVIDENCE_DOCUMENT_FILENAME
        ).read_text(encoding="utf-8")
        assert "2023-11-13" in internal
        assert "2023-11-13" not in published

    def test_the_review_page_links_it_and_names_the_pin(self, tmp_path):
        page = self._collection()
        result = scramble(page)
        out = write_side_by_side(page, result, tmp_path / "review")
        review = (out / REVIEW_FILENAME).read_text(encoding="utf-8")
        assert f"publication/{EVIDENCE_DOCUMENT_FILENAME}" in review
        assert result.candidate_digest in review

    def test_a_stale_release_says_so_on_the_review_page(self, tmp_path):
        page = self._collection()
        result = scramble(
            page,
            approvals=PublicationApprovals(
                release=ReleaseApproval(
                    released_by="A. Reviewer",
                    released_at="2026-11-19",
                    candidate_sha256="a different document",
                )
            ),
        )
        out = write_side_by_side(page, result, tmp_path / "review")
        review = (out / REVIEW_FILENAME).read_text(encoding="utf-8")
        assert "no longer matches this document" in review
        assert result.candidate_digest in review


class TestTheReviewCopiesSayWhatIsTrue:
    """Two artifacts on disk must not contradict each other about one
    document, and the review copies did.

    The publication subtree showed the candidate footer whatever the run's
    state, so after a release the review directory called the document a
    candidate while the supplement beside it called it cleared.
    """

    def _page(self):
        return publication_page(worked_example_case(), worked_example_case())

    def _released(self, page):
        candidate = scramble(page)
        return scramble(
            page,
            approvals=PublicationApprovals(
                release=ReleaseApproval(
                    released_by="A. Reviewer",
                    released_at="2026-11-20",
                    candidate_sha256=candidate.candidate_digest,
                )
            ),
        )

    def test_a_candidate_run_calls_them_candidates(self, tmp_path):
        page = self._page()
        out = write_side_by_side(page, scramble(page), tmp_path / "review")
        for name in (EVIDENCE_DOCUMENT_FILENAME, "index.html", "pair-01.html"):
            text = (out / "publication" / name).read_text(encoding="utf-8")
            assert "candidate for disclosure review" in text, name

    def test_a_released_run_calls_them_cleared(self, tmp_path):
        page = self._page()
        out = write_side_by_side(page, self._released(page), tmp_path / "review")
        for name in (EVIDENCE_DOCUMENT_FILENAME, "index.html", "pair-01.html"):
            text = (out / "publication" / name).read_text(encoding="utf-8")
            assert "leared for public sharing" in text, name
            assert "candidate for disclosure review" not in text, name

    def test_the_internal_copies_are_never_either(self, tmp_path):
        """They are an internal review whatever the publication side says."""
        page = self._page()
        out = write_side_by_side(page, self._released(page), tmp_path / "review")
        text = " ".join(
            (out / "internal" / EVIDENCE_DOCUMENT_FILENAME)
            .read_text(encoding="utf-8")
            .split()
        )
        assert "not a publication-safe document" in text
        assert "leared for public sharing" not in text
