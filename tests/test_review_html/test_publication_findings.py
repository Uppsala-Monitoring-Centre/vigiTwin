"""The fail-closed gate, and the two exhaustiveness checks behind it.

A finding blocks the publication export. There is no override argument: the
only ways past one are an approved replacement text or a
:class:`FindingResolution` naming a reviewer and a reason. The exhaustiveness
tests are what make that gate hold over time — a field or a value class added
to the evidence model with no policy fails here, rather than reaching a page
untransformed.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from llm_dup_dev.review_html.evidence.models import (
    EvidenceCaseVM,
    EvidenceCell,
    EvidencePageVM,
    FreqRowVM,
    ValueKind,
)
from llm_dup_dev.review_html.evidence.publication import (
    FREE_TEXT_FIELDS,
    KIND_POLICIES,
    DisclosureCode,
    FindingResolution,
    PublicationApprovals,
    PublicationBlocked,
    PublicationPolicy,
    PublicationSecrets,
    write_publication_evidence,
)
from llm_dup_dev.review_html.evidence.publication.substitute import (
    SubstitutionTable,
    _text_shape,
    scan_residual,
)
from tests.test_review_html.conftest import (
    case_with_human_text,
    publication_page,
    scramble,
    worked_example_case,
)


def _model_classes(root: type[BaseModel]) -> set[type[BaseModel]]:
    """Every model class reachable from *root* through its annotations.

    Static, so the check does not depend on a fixture happening to populate
    an optional field.
    """
    import types
    from typing import Union, get_args, get_origin

    found: set[type[BaseModel]] = set()

    def visit(annotation: object) -> None:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if annotation in found:
                return
            found.add(annotation)
            for info in annotation.model_fields.values():
                visit(info.annotation)
            return
        origin = get_origin(annotation)
        if origin in (Union, types.UnionType, list, tuple, dict, set):
            for arg in get_args(annotation):
                visit(arg)

    visit(root)
    return found


class TestEveryValueClassHasAPolicy:
    def test_no_value_kind_is_unhandled(self):
        """A new ``ValueKind`` with no entry would otherwise pass through to a
        published page carrying whatever it carries."""
        assert set(KIND_POLICIES) == set(ValueKind)

    def test_an_unhandled_kind_would_be_a_finding_not_a_pass_through(self):
        """Belt and braces: the dispatch records a finding rather than
        publishing a value it has no rule for."""
        assert (
            DisclosureCode.UNSUPPORTED_VALUE_KIND.value
            == "unsupported_value_kind"
        )


class TestEveryTextFieldHasAPolicy:
    def test_no_string_field_of_the_page_model_is_unhandled(self):
        keys = {
            f"{cls.__name__}.{name}"
            for cls in _model_classes(EvidencePageVM)
            for name, info in cls.model_fields.items()
            if _text_shape(info.annotation) is not None
        }
        assert keys - set(FREE_TEXT_FIELDS) == set()

    def test_the_inventory_names_no_field_that_does_not_exist(self):
        """So a renamed field shows up here instead of quietly losing its
        policy."""
        keys = {
            f"{cls.__name__}.{name}"
            for cls in _model_classes(EvidencePageVM)
            for name, info in cls.model_fields.items()
            if _text_shape(info.annotation) is not None
        }
        assert set(FREE_TEXT_FIELDS) - keys == set()


class TestTheResidualScan:
    def test_an_untransformed_original_left_on_the_page_is_caught(self):
        """The scan does not trust the transforms that ran before it."""
        result = scramble(
            publication_page(worked_example_case()),
            policy=PublicationPolicy(mask_countries=True),
        )
        # Plant an original back into the published tree, as a template or a
        # future field might, and look again.
        result.page.cases[0].context_note = "Reported from SE in 2024."
        findings = scan_residual(
            result.page.cases[0], result.transforms[0].table, example_id="x"
        )
        assert DisclosureCode.RESIDUAL_ORIGINAL_VALUE in {
            f.code for f in findings
        }

    def test_a_long_digit_run_looks_like_a_case_number(self):
        page = EvidencePageVM(title="x", note="Case 100123456 refers.")
        findings = scan_residual(page, SubstitutionTable(), example_id="x")
        assert DisclosureCode.RESIDUAL_DIGIT_RUN in {f.code for f in findings}

    def test_an_unchanged_year_is_not_found_inside_a_complete_date(self):
        """The false positive the scan existed to produce: ``2023`` sits
        inside ``2023-12-05``, so a substring search reported a leak in every
        complete date of that year."""
        table = SubstitutionTable()
        table.add("2023-11-13", "2023-12-05", kind="date:day")
        table.add("2019", "2019", kind="date:year")
        page = EvidencePageVM(title="x", note="Onset 2023-12-05.")
        findings = scan_residual(
            page, table, example_id="x", published_dates=["2023-12-05", "2019"]
        )
        assert findings == []

    def test_a_year_on_its_own_is_still_found(self):
        """The check is not weakened, only made exact."""
        table = SubstitutionTable()
        table.add("2023", "2024", kind="date:year")
        page = EvidencePageVM(title="x", note="Drug started in 2023.")
        findings = scan_residual(page, table, example_id="x")
        assert DisclosureCode.RESIDUAL_ORIGINAL_VALUE in {
            f.code for f in findings
        }

    def test_a_count_that_looks_like_a_year_is_not_a_date(self):
        """A co-occurrence count of 2019 reports is a count."""
        page = EvidencePageVM(
            title="x",
            cases=[
                EvidenceCaseVM(
                    pair_id="p",
                    example_id="e",
                    freq_rows=[
                        FreqRowVM(
                            scope_label="Country 1",
                            n_drugs="2019",
                            n_aes="",
                            n_both="",
                        )
                    ],
                )
            ],
        )
        findings = scan_residual(page, SubstitutionTable(), example_id="x")
        assert DisclosureCode.RESIDUAL_DATE_TOKEN not in {
            f.code for f in findings
        }

    def test_a_date_written_into_a_count_field_is_still_caught(self):
        """The exemption is from date-*shape* detection only; an original
        value in a numeric field is still a leak."""
        table = SubstitutionTable()
        table.add("2023-11-13", "2024-01-02", kind="date:day")
        page = EvidencePageVM(
            title="x",
            cases=[
                EvidenceCaseVM(
                    pair_id="p",
                    example_id="e",
                    freq_rows=[
                        FreqRowVM(
                            scope_label="Country 1",
                            n_drugs="2023-11-13",
                            n_aes="",
                            n_both="",
                        )
                    ],
                )
            ],
        )
        findings = scan_residual(page, table, example_id="x")
        assert DisclosureCode.RESIDUAL_ORIGINAL_VALUE in {
            f.code for f in findings
        }

    def test_a_four_digit_figure_outside_any_era_is_not_a_year(self):
        page = EvidencePageVM(title="x", note="A total of 4821 reports.")
        findings = scan_residual(page, SubstitutionTable(), example_id="x")
        assert DisclosureCode.RESIDUAL_DATE_TOKEN not in {
            f.code for f in findings
        }

    def test_a_render_timestamp_is_not_flagged(self):
        """``generated_at`` is a date by construction and nobody's date."""
        result = scramble(publication_page(worked_example_case()))
        paths = {f.field_path for f in result.report.findings}
        assert "generated_at" not in paths


def _zero_only(**kw) -> PublicationPolicy:
    """A policy whose only candidate offset is zero.

    The cheapest way to reach the "every date is published as reported" state
    deliberately, which is otherwise a one-in-eight-hundred draw.
    """
    return PublicationPolicy(
        date_offset_min_days=0,
        date_offset_max_days=0,
        decouple_clinical_dates=False,
        **kw,
    )


class TestTheExportGate:
    def test_a_blocked_candidate_is_not_written(self, tmp_path):
        result = scramble(publication_page(case_with_human_text()))
        out = tmp_path / "publication"
        with pytest.raises(PublicationBlocked):
            write_publication_evidence(result, out)
        assert not out.exists()

    def test_the_exception_says_what_is_outstanding(self, tmp_path):
        result = scramble(publication_page(case_with_human_text()))
        with pytest.raises(PublicationBlocked) as caught:
            write_publication_evidence(result, tmp_path / "publication")
        assert "human_text_review_required" in str(caught.value)
        assert caught.value.report.blocked

    def test_a_clear_candidate_is_written(self, tmp_path):
        result = scramble(publication_page(worked_example_case()))
        out = write_publication_evidence(result, tmp_path / "publication")
        assert (out / "pair-01.html").exists()
        assert (out / "publication_manifest.json").exists()

    def test_an_advisory_alone_does_not_block(self, tmp_path):
        """A case whose dates all came out as reported is an allowed outcome
        with a re-identification question attached, not a defect."""
        result = scramble(
            publication_page(worked_example_case()), policy=_zero_only()
        )
        assert [f.code for f in result.report.advisories] == [
            DisclosureCode.DATE_PATTERN_UNCHANGED
        ]
        assert not result.blocked
        write_publication_evidence(result, tmp_path / "publication")

    def test_advisories_can_be_made_to_block(self):
        """For a run that wants them treated as blockers."""
        result = scramble(
            publication_page(worked_example_case()),
            policy=_zero_only(advisories_block=True),
        )
        assert result.blocked

    def test_the_unchanged_advisory_says_how_many_dates_held(self):
        result = scramble(
            publication_page(worked_example_case()), policy=_zero_only()
        )
        advisory = result.report.advisories[0]
        assert "published as reported" in advisory.message
        assert "date value(s)" in advisory.detail

    def test_a_reviewer_resolution_clears_a_finding(self):
        page = publication_page(case_with_human_text())
        blocked = scramble(page)
        approvals = PublicationApprovals(
            resolutions=[
                FindingResolution(
                    code=f.code.value,
                    example_id=f.example_id,
                    field_path=f.field_path,
                    approved_by="reviewer",
                    approved_at="2026-01-01",
                    rationale="checked against the disclosure checklist",
                )
                for f in blocked.report.findings
            ]
        )
        cleared = scramble(page, approvals=approvals)
        assert not cleared.blocked
        assert cleared.report.resolved

    def test_there_is_no_force_argument(self):
        """Clearing a finding is a decision with a signature on it, not a
        flag."""
        import inspect

        signature = inspect.signature(write_publication_evidence)
        assert "force" not in signature.parameters


class TestTheSaltHasNoDefault:
    def test_an_unset_environment_variable_is_an_error(self, monkeypatch):
        """A default salt would make every offset in every supplement
        guessable from this source alone."""
        monkeypatch.delenv("VIGITWIN_PUBLICATION_SALT", raising=False)
        with pytest.raises(ValueError, match="no default salt"):
            PublicationSecrets.from_env()

    def test_a_blank_environment_variable_is_an_error(self, monkeypatch):
        monkeypatch.setenv("VIGITWIN_PUBLICATION_SALT", "   ")
        with pytest.raises(ValueError, match="no default salt"):
            PublicationSecrets.from_env()

    def test_a_set_variable_is_read(self, monkeypatch):
        monkeypatch.setenv("VIGITWIN_PUBLICATION_SALT", "abc")
        assert PublicationSecrets.from_env().salt.get_secret_value() == "abc"


class TestUnsupportedCellsFailClosed:
    def test_a_cell_whose_kind_has_no_policy_is_reported(self, monkeypatch):
        """With a kind removed from the dispatch table, the value is reported
        rather than published as it stands."""
        import llm_dup_dev.review_html.evidence.publication.scramble as module

        page = publication_page(worked_example_case())
        trimmed = {
            k: v for k, v in KIND_POLICIES.items() if k is not ValueKind.SEX
        }
        monkeypatch.setattr(module, "KIND_POLICIES", trimmed)
        result = scramble(page)
        assert DisclosureCode.UNSUPPORTED_VALUE_KIND in {
            f.code for f in result.report.findings
        }

    def test_a_cell_kind_is_never_inferred_from_its_text(self):
        """The dispatch is on ``kind``, so a date written into a drug-name
        cell is not shifted by accident."""
        cell = EvidenceCell(text="2023-11-13", kind=ValueKind.DRUG_SUBSTANCE)
        assert KIND_POLICIES[cell.kind].value == "keep"
