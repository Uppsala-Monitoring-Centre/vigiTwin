"""``--mode publication`` end to end, over the synthetic collection.

The flags are documented, which makes them a promise. What these check is the
shape of a real run: the secure outputs are written first and
unconditionally, because they are what a reviewer needs in order to clear a
block, and the supplement is written last and only if nothing is outstanding.
"""

from __future__ import annotations

import json

import pytest

from vigitwin.review_html.evidence.cli import main
from vigitwin.review_html.evidence.publication import (
    MANIFEST_FILENAME,
    REVIEW_FILENAME,
    ApprovedText,
    CountryMaskingOverride,
    PublicationApprovals,
    ReleaseApproval,
    PublicationPolicy,
)
from tests.test_review_html.test_evidence_cli import collection  # noqa: F401

SALT_VAR = "VIGITWIN_PUBLICATION_SALT"


@pytest.fixture
def salted(monkeypatch):
    monkeypatch.setenv(SALT_VAR, "cli-test-salt")


def _text(example_id: str, field_path: str, replacement: str) -> ApprovedText:
    return ApprovedText(
        example_id=example_id,
        field_path=field_path,
        replacement=replacement,
        approved_by="reviewer",
        approved_at="2026-01-01",
    )


@pytest.fixture
def approved(collection):  # noqa: F811 - the imported fixture
    """The curator's titles and ledes, signed off.

    Every publication run of this collection blocks on them until somebody
    does: a title naming a case is exactly where a place or a clinician ends
    up, and the policy has no way to tell this one apart from that one.
    """
    path = collection / "approvals.json"
    path.write_text(
        PublicationApprovals(
            approved_texts=[
                _text("S1", "title", "First example"),
                _text("S1", "lede", "Extensive matching evidence."),
                _text("S2", "title", "Second example"),
            ]
        ).model_dump_json()
    )
    return path


def _argv(collection, out, *extra, approvals=None):
    return [
        "--manifest",
        str(collection / "collection.yaml"),
        "--mode",
        "publication",
        "--output-dir",
        str(out),
        *(("--approvals", str(approvals)) if approvals else ()),
        *extra,
    ]


class TestTheSaltIsRequired:
    def test_without_it_the_command_fails_rather_than_inventing_one(
        self, collection, monkeypatch, tmp_path  # noqa: F811
    ):
        monkeypatch.delenv(SALT_VAR, raising=False)
        with pytest.raises(ValueError, match="no default salt"):
            main(_argv(collection, tmp_path / "out"))


class TestAPublicationRun:
    def test_it_writes_the_supplement_and_its_manifest(
        self, collection, salted, approved
    ):
        out = collection / "publication"
        main(_argv(collection, out, approvals=approved))
        assert (out / "pair-01.html").exists()
        assert (out / "pair-02.html").exists()
        assert (out / "index.html").exists()
        assert (out / MANIFEST_FILENAME).exists()

    def test_the_files_are_named_by_position_not_by_example_id(
        self, collection, salted, approved
    ):
        """The curator's own ids name the internal review; a published
        supplement cites its examples by position."""
        out = collection / "publication"
        main(_argv(collection, out, approvals=approved))
        assert not (out / "S1.html").exists()

    def test_the_published_pages_carry_no_original_identifier(
        self, collection, salted, approved
    ):
        out = collection / "publication"
        main(_argv(collection, out, approvals=approved))
        written = "\n".join(
            path.read_text(encoding="utf-8")
            for path in out.rglob("*.html")
        )
        for original in ("R001", "R002", "R003", "R001__R002"):
            assert original not in written, original

    def test_the_manifest_says_the_output_is_a_candidate(
        self, collection, salted, approved
    ):
        out = collection / "publication"
        main(_argv(collection, out, approvals=approved))
        manifest = json.loads((out / MANIFEST_FILENAME).read_text())
        assert manifest["disclosure"]["cleared"] is True
        assert "candidate for disclosure review" in (
            manifest["disclosure"]["statement"]
        )

    def test_the_same_salt_reproduces_the_run(
        self, collection, salted, approved
    ):
        first = collection / "one"
        second = collection / "two"
        main(_argv(collection, first, approvals=approved))
        main(_argv(collection, second, approvals=approved))
        assert _values(first) == _values(second)

    def test_a_different_salt_moves_the_values(
        self, collection, salted, approved, monkeypatch
    ):
        first = collection / "one"
        main(_argv(collection, first, approvals=approved))
        monkeypatch.setenv(SALT_VAR, "a-different-salt")
        second = collection / "two"
        main(_argv(collection, second, approvals=approved))
        assert _values(first) != _values(second)


class TestTheSecureOutputs:
    def test_the_key_and_the_review_go_where_they_are_asked(
        self, collection, salted, approved
    ):
        out = collection / "publication"
        main(
            _argv(
                collection,
                out,
                "--key-out",
                str(collection / "secure" / "key.json"),
                "--side-by-side-dir",
                str(collection / "secure" / "review"),
                approvals=approved,
            )
        )
        key = collection / "secure" / "key.json"
        review = collection / "secure" / "review"
        assert key.exists()
        assert (review / REVIEW_FILENAME).exists()
        assert (review / "internal" / "S1.html").exists()
        assert (review / "publication" / "pair-01.html").exists()
        # And nothing of the key beside the supplement.
        assert not list(out.glob("*key*"))

    def test_the_key_records_the_mappings(
        self, collection, salted, approved
    ):
        key = collection / "secure" / "key.json"
        main(
            _argv(
                collection,
                collection / "publication",
                "--key-out",
                str(key),
                approvals=approved,
            )
        )
        record = json.loads(key.read_text())
        # The case-level mapping is the way back from a published supplement
        # to the internal review it came from.
        assert [c["pair_id"] for c in record["cases"]] == [
            "R001__R002",
            "R001__R003",
        ]
        assert [c["published_example_id"] for c in record["cases"]] == [
            "pair-01",
            "pair-02",
        ]
        # And the value-level mapping is the way back from a published value.
        originals = {
            entry["original"]
            for case in record["cases"]
            for entry in case["substitutions"]
        }
        assert any(o.endswith("years") for o in originals)
        assert "cli-test-salt" not in key.read_text()

    def test_they_are_written_even_when_the_candidate_is_blocked(
        self, collection, salted, tmp_path
    ):
        """They are how a reviewer clears the block, so they cannot wait for
        it to be cleared."""
        blocked = _with_an_interpretation(collection)
        review = collection / "secure" / "review"
        with pytest.raises(SystemExit) as exit_code:
            main(
                _argv(
                    blocked,
                    collection / "publication",
                    "--side-by-side-dir",
                    str(review),
                )
            )
        assert exit_code.value.code == 2
        assert (review / REVIEW_FILENAME).exists()
        assert not (collection / "publication").exists()


class TestApprovalsFromAFile:
    def test_an_approvals_file_clears_the_block(
        self, collection, salted, approved
    ):
        blocked = _with_an_interpretation(collection)
        approved.write_text(
            PublicationApprovals(
                approved_texts=[
                    _text("S1", "title", "First example"),
                    _text("S1", "lede", "Extensive matching evidence."),
                    _text("S2", "title", "Second example"),
                    ApprovedText(
                        example_id="S1",
                        field_path="narrative_blocks[1].paragraphs",
                        withhold=True,
                        approved_by="reviewer",
                        approved_at="2026-01-01",
                    ),
                ]
            ).model_dump_json()
        )
        out = collection / "publication"
        main(_argv(blocked, out, approvals=approved))
        page = (out / "pair-01.html").read_text(encoding="utf-8")
        assert "First example" in page
        assert "telephoned" not in page
        assert PublicationPolicy().withheld_text_notice in page

    def test_a_policy_file_is_honoured(
        self, collection, salted, approved
    ):
        policy = collection / "policy.json"
        policy.write_text(
            PublicationPolicy(
                policy_version="2.0",
                mask_countries=True,
                country_alias_template="Region {n}",
            ).model_dump_json()
        )
        out = collection / "publication"
        main(
            _argv(collection, out, "--policy", str(policy), approvals=approved)
        )
        manifest = json.loads((out / MANIFEST_FILENAME).read_text())
        assert manifest["policy_version"] == "2.0"
        assert "Region 1" in (out / "pair-01.html").read_text(encoding="utf-8")


def _values(directory):
    """Every published figure and date, as a fingerprint of one run.

    Ages alone are too coarse now that a zero offset is a legitimate draw:
    two runs can agree on an age and differ on every date.
    """
    import re

    html = (directory / "pair-01.html").read_text(encoding="utf-8")
    return re.findall(r"\d{4}-\d{2}-\d{2}|\d+(?:\.\d+)? (?:years|kg|cm)", html)


def _with_an_interpretation(collection):
    """The same collection, with a human interpretation on one example."""
    manifest = collection / "collection.yaml"
    text = manifest.read_text()
    text = text.replace(
        """    title: First example
    lede: Extensive matching evidence.""",
        """    title: First example
    lede: Extensive matching evidence.
    summary_mode: both
    case_interpretation: >
      The reporting centre telephoned to confirm these are one patient.""",
    )
    manifest.write_text(text)
    return collection


class TestTheApprovalsForm:
    """``--export-approvals``: the blocked run hands back a form to sign.

    The point is that a curator does not retype the manifest's own text to
    clear a gate that blocks on all of it. The form carries paths and
    digests; the text is read in the side-by-side review.
    """

    def _sign(self, path):
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data["approved_texts"]:
            entry["approved_by"] = "reviewer"
            entry["approved_at"] = "2026-01-01"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_it_is_written_although_the_run_blocks(
        self, collection, salted  # noqa: F811
    ):
        form = collection / "form.json"
        with pytest.raises(SystemExit) as exit_code:
            main(
                _argv(
                    _with_an_interpretation(collection),
                    collection / "publication",
                    "--export-approvals",
                    str(form),
                )
            )
        assert exit_code.value.code == 2
        data = json.loads(form.read_text(encoding="utf-8"))
        paths = {(e["example_id"], e["field_path"]) for e in data["approved_texts"]}
        assert ("S1", "title") in paths
        assert ("S1", "narrative_blocks[1].paragraphs") in paths
        assert all(e["approve_as_written"] for e in data["approved_texts"])

    def test_it_does_not_need_the_salt(
        self, collection, monkeypatch  # noqa: F811
    ):
        """A reviewer can be handed the form before anyone opens the key
        store."""
        monkeypatch.delenv(SALT_VAR, raising=False)
        form = collection / "form.json"
        with pytest.raises(ValueError, match=SALT_VAR):
            main(
                _argv(
                    collection,
                    collection / "publication",
                    "--export-approvals",
                    str(form),
                )
            )
        assert form.exists()

    def test_signed_it_clears_the_run_without_restating_the_text(
        self, collection, salted  # noqa: F811
    ):
        blocked = _with_an_interpretation(collection)
        form = collection / "form.json"
        out = collection / "publication"
        with pytest.raises(SystemExit):
            main(_argv(blocked, out, "--export-approvals", str(form)))

        main(_argv(blocked, out, approvals=self._sign(form)))

        page = (out / "pair-01.html").read_text(encoding="utf-8")
        assert "First example" in page  # the manifest's own title, published
        assert "telephoned" in page  # and its interpretation, substituted

    def test_an_unsigned_form_is_refused_as_an_approvals_file(
        self, collection, salted  # noqa: F811
    ):
        blocked = _with_an_interpretation(collection)
        form = collection / "form.json"
        with pytest.raises(SystemExit):
            main(
                _argv(
                    blocked,
                    collection / "publication",
                    "--export-approvals",
                    str(form),
                )
            )
        with pytest.raises(ValueError, match="unsigned entry is a form"):
            main(_argv(blocked, collection / "publication", approvals=form))

    def test_a_second_run_keeps_the_signatures_in_it(
        self, collection, salted  # noqa: F811
    ):
        """The form used to refuse a second run. That made the one workflow
        it is for — sign some, re-run, see what is left — impossible."""
        form = collection / "form.json"
        argv = _argv(
            collection,
            collection / "publication",
            "--export-approvals",
            str(form),
        )
        with pytest.raises(SystemExit):
            main(argv)
        data = json.loads(form.read_text(encoding="utf-8"))
        data["approved_texts"][0]["approved_by"] = "reviewer"
        data["approved_texts"][0]["approved_at"] = "2026-01-01"
        signed = dict(data["approved_texts"][0])
        form.write_text(json.dumps(data), encoding="utf-8")

        with pytest.raises(SystemExit):
            main(argv)
        after = json.loads(form.read_text(encoding="utf-8"))
        assert signed in after["approved_texts"]

    def test_force_discards_them_instead(
        self, collection, salted  # noqa: F811
    ):
        """Still available, and now the only thing that drops a signature —
        which is why it says so rather than being the way to re-run."""
        form = collection / "form.json"
        argv = _argv(
            collection,
            collection / "publication",
            "--export-approvals",
            str(form),
        )
        with pytest.raises(SystemExit):
            main(argv)
        data = json.loads(form.read_text(encoding="utf-8"))
        data["approved_texts"][0]["approved_by"] = "reviewer"
        form.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(SystemExit):
            main([*argv, "--force"])
        after = json.loads(form.read_text(encoding="utf-8"))
        assert all(
            not e["approved_by"] for e in after["approved_texts"]
        )


class TestCountryMaskingThroughTheCLI:
    """The policy and the per-example override, as files on disk."""

    def test_countries_are_shown_as_reported_by_default(
        self, collection, salted, approved
    ):
        out = collection / "publication"
        main(_argv(collection, out, approvals=approved))
        page = (out / "pair-01.html").read_text(encoding="utf-8")
        assert "US" in page
        assert "Country 1" not in page

    def test_a_policy_file_turns_masking_on(
        self, collection, salted, approved
    ):
        policy = collection / "policy.json"
        policy.write_text(
            PublicationPolicy(mask_countries=True).model_dump_json()
        )
        out = collection / "publication"
        main(
            _argv(collection, out, "--policy", str(policy), approvals=approved)
        )
        page = (out / "pair-01.html").read_text(encoding="utf-8")
        assert "Country 1" in page

    def test_an_override_in_the_approvals_file_decides_one_example(
        self, collection, salted, approved
    ):
        approved.write_text(
            PublicationApprovals(
                approved_texts=[
                    _text("S1", "title", "First example"),
                    _text("S1", "lede", "Extensive matching evidence."),
                    _text("S2", "title", "Second example"),
                ],
                country_masking=[
                    CountryMaskingOverride(
                        example_id="S1",
                        mask=True,
                        decided_by="reviewer",
                        decided_at="2026-01-01",
                        rationale="synthetic test",
                    )
                ],
            ).model_dump_json()
        )
        out = collection / "publication"
        main(_argv(collection, out, approvals=approved))
        first = (out / "pair-01.html").read_text(encoding="utf-8")
        second = (out / "pair-02.html").read_text(encoding="utf-8")
        assert "Country 1" in first and "US" not in first
        assert "US" in second

    def test_the_manifest_records_the_effective_policy(
        self, collection, salted, approved
    ):
        out = collection / "publication"
        main(_argv(collection, out, approvals=approved))
        manifest = json.loads((out / MANIFEST_FILENAME).read_text())
        assert manifest["country_masking"]["default_masked"] is False
        assert manifest["country_masking"]["cases_retained"] == 2
        assert "shown as reported" in manifest["country_masking"]["statement"]

    def test_the_review_page_puts_the_retention_to_the_reviewer(
        self, collection, salted, approved
    ):
        review = collection / "secure" / "review"
        main(
            _argv(
                collection,
                collection / "publication",
                "--side-by-side-dir",
                str(review),
                approvals=approved,
            )
        )
        page = (review / REVIEW_FILENAME).read_text(encoding="utf-8")
        assert "shown as reported" in page
        assert "override the policy for this example" in page


class TestTheConsoleAgreesWithTheArtifacts:
    """The pages and the manifest are held to the same phrase by a test in
    `test_publication_key.py`. Standard output is a third place that says what
    the run produced, and it used to call every run a candidate — including
    one it had just released."""

    def test_a_candidate_run_says_candidate(
        self, collection, salted, approved, capsys  # noqa: F811
    ):
        main(_argv(collection, collection / "publication", approvals=approved))
        assert "candidate for disclosure review" in capsys.readouterr().out

    def test_a_released_run_says_cleared(
        self, collection, salted, approved, capsys  # noqa: F811
    ):
        from vigitwin.review_html.evidence.collection import build_collection
        from vigitwin.review_html.evidence.manifest import (
            load_curated_manifest,
        )
        from vigitwin.review_html.evidence.publication import (
            PublicationSecrets,
            scramble_page,
        )

        # The digest of the candidate this collection produces, which is what
        # --export-approvals would have put in the form.
        page = build_collection(load_curated_manifest(collection / "collection.yaml"))
        candidate = scramble_page(
            page,
            secrets=PublicationSecrets.from_env(),
            approvals=PublicationApprovals.model_validate_json(
                approved.read_text(encoding="utf-8")
            ),
        )
        signed = PublicationApprovals.model_validate_json(
            approved.read_text(encoding="utf-8")
        )
        approved.write_text(
            signed.model_copy(
                update={
                    "release": ReleaseApproval(
                        released_by="A. Reviewer",
                        released_at="2026-11-19",
                        candidate_sha256=candidate.candidate_digest,
                    )
                }
            ).model_dump_json()
        )
        main(_argv(collection, collection / "publication", approvals=approved))
        out = capsys.readouterr().out
        assert "cleared for public sharing" in out
        assert "candidate for disclosure review" not in out
