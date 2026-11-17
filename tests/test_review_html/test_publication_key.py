"""The two records, and the line between them.

The secure key record holds the offsets and the full original → published
table, which is to say it holds real ages, dates, countries and identifiers.
The public manifest holds the policy, the offset *bounds* and per-class
counts. Neither holds the salt. The tests that matter are the negative ones:
nothing in the publication directory may carry a seed, a salt, an offset or
an original value.
"""

from __future__ import annotations

import json

from llm_dup_dev.review_html.evidence.publication import (
    MANIFEST_FILENAME,
    PublicationPolicy,
    build_key_record,
    build_publication_manifest,
    write_publication_evidence,
    write_scrambling_key,
)
from tests.test_review_html.conftest import (
    _TEST_SALT,
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
                date_offset_min_days=0, date_offset_max_days=0
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
        assert manifest.policy_version == "1.0"
        assert manifest.key_id == secrets().key_id

    def test_it_records_bounds_and_not_offsets(self):
        """A recorded offset is an invitation to subtract it."""
        result = scramble(publication_page(worked_example_case()))
        manifest = build_publication_manifest(result)
        payload = manifest.model_dump_json()
        assert manifest.date_offset_bound_days == (0, 400)
        offset = result.transforms[0].date_offset_days
        assert offset is not None
        assert f'"{offset}"' not in payload

    def test_it_counts_values_transformed_and_not_values_moved(self):
        """A count of what *moved* would say, in a case where nothing did,
        that every date on the page is the real one."""
        unmoved = scramble(
            publication_page(worked_example_case()),
            policy=PublicationPolicy(
                date_offset_min_days=0, date_offset_max_days=0
            ),
        )
        moved = scramble(publication_page(worked_example_case()))
        assert unmoved.transforms[0].dates_changed == 0
        dates = lambda m: next(  # noqa: E731 - a one-line reader
            c.values_transformed for c in m.counts if c.kind == "date:day"
        )
        assert dates(build_publication_manifest(unmoved)) == dates(
            build_publication_manifest(moved)
        )

    def test_it_names_no_value(self):
        result = scramble(publication_page(worked_example_case()))
        manifest = build_publication_manifest(result)
        assert {c.kind for c in manifest.counts} >= {"country", "age"}
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

    def test_the_manifest_is_the_only_extra_file(self, tmp_path):
        result = scramble(publication_page(worked_example_case()))
        out = write_publication_evidence(result, tmp_path / "publication")
        names = sorted(p.name for p in out.iterdir())
        assert names == sorted(
            [MANIFEST_FILENAME, "index.html", "pair-01.html"]
        )
