"""Where adverse-event onset dates appear, and why there.

Two problems with one cause: the report comparison showed a reader less than
the assessment used.

The **case-level** onset — the date the overall-onset check actually compares
— appeared nowhere at all, so a finding about it named two dates printed on no
table. The **event-level** onsets were in a second table keyed by term, so a
reader who wanted to know when an event started had to match it against
another row, which the drug table never asks of them.
"""

from __future__ import annotations

from llm_dup_dev.models.enums import InconsistencySeverity
from llm_dup_dev.models.pair_features import (
    ClinicalDateFindingSummary,
    InconsistencyCounts,
)
from llm_dup_dev.models.report import AdverseEvent, Drug, PartialDate
from llm_dup_dev.review_html.evidence.build import AE_ONSET_LABEL
from llm_dup_dev.review_html.evidence.models import EvidenceRole

from tests.test_review_html.test_evidence_build import _features, _report
from tests.test_review_html.test_evidence_render import _case, _html


def _ae(term: str, onset: str | None = None) -> AdverseEvent:
    return AdverseEvent(
        pt_name=term,
        sources=["s"],
        start_date=(
            PartialDate(value=onset, resolution="day") if onset else None
        ),
    )


def _char(case, label: str):
    return next((r for r in case.characteristics if r.label == label), None)


class TestTheCaseLevelOnsetHasARow:
    def test_it_is_shown_for_each_report(self):
        case = _case(
            report_1=_report("R1", aes=[_ae("rash", "2020-01-01")]),
            report_2=_report("R2", aes=[_ae("rash", "2020-04-01")]),
            features=_features(matching_AEs=["rash"]),
        )
        row = _char(case, AE_ONSET_LABEL)
        assert row is not None
        assert row.left.text == "2020-01-01"
        assert row.right.text == "2020-04-01"

    def test_it_sits_after_the_report_date(self):
        case = _case(
            report_1=_report("R1", aes=[_ae("rash", "2020-01-01")]),
            report_2=_report("R2", aes=[_ae("rash", "2020-04-01")]),
            features=_features(matching_AEs=["rash"]),
        )
        labels = [r.label for r in case.characteristics]
        assert labels.index(AE_ONSET_LABEL) == labels.index("Report date") + 1

    def test_no_row_when_neither_report_yields_one(self):
        """Two blanks under a heading is not information."""
        case = _case(
            report_1=_report("R1", aes=[_ae("rash")]),
            report_2=_report("R2", aes=[_ae("rash")]),
            features=_features(matching_AEs=["rash"]),
        )
        assert _char(case, AE_ONSET_LABEL) is None

    def test_a_derived_value_says_it_was_derived(self):
        """The column is headed "Report A"; a derived date is not what
        report A stated."""
        case = _case(
            report_1=_report("R1", aes=[_ae("rash", "2020-01-01")]),
            report_2=_report("R2", aes=[_ae("rash", "2020-04-01")]),
            features=_features(matching_AEs=["rash"]),
        )
        row = _char(case, AE_ONSET_LABEL)
        assert row is not None
        assert "derived" in row.left_note
        assert "derived" in row.right_note

    def test_a_reported_value_carries_no_such_note(self):
        r1 = _report("R1", aes=[_ae("rash", "2020-01-01")])
        r1 = r1.model_copy(
            update={
                "reported_overall_ae_onset_date": PartialDate(
                    value="2019-12-30", resolution="day"
                )
            }
        )
        case = _case(
            report_1=r1,
            report_2=_report("R2", aes=[_ae("rash", "2020-04-01")]),
            features=_features(matching_AEs=["rash"]),
        )
        row = _char(case, AE_ONSET_LABEL)
        assert row is not None
        assert row.left.text == "2019-12-30"
        assert row.left_note == ""
        assert row.right_note != ""

    def test_the_note_reaches_the_page(self):
        html = " ".join(
            _html(
                report_1=_report("R1", aes=[_ae("rash", "2020-01-01")]),
                report_2=_report("R2", aes=[_ae("rash", "2020-04-01")]),
                features=_features(matching_AEs=["rash"]),
            ).split()
        )
        assert AE_ONSET_LABEL in html
        assert "derived from the reported event and drug dates" in html


class TestAnOverallOnsetFindingColoursThatRow:
    """The finding used to name two dates that appeared on no table."""

    def _case(self):
        return _case(
            report_1=_report("R1", aes=[_ae("rash", "2020-01-01")]),
            report_2=_report("R2", aes=[_ae("rash", "2020-04-01")]),
            features=_features(
                matching_AEs=["rash"],
                clinical_date_findings=[
                    ClinicalDateFindingSummary(
                        entity_label="overall_ae_onset",
                        severity=InconsistencySeverity.MINOR,
                        source_date="2020-01-01",
                        matched_date="2020-04-01",
                        diff_days=91,
                    )
                ],
                inconsistency_counts=InconsistencyCounts(minor=1),
            ),
        )

    def test_both_dates_are_red(self):
        row = _char(self._case(), AE_ONSET_LABEL)
        assert row is not None
        assert row.left.role is EvidenceRole.INCONSISTENT
        assert row.right.role is EvidenceRole.INCONSISTENT

    def test_the_colour_carries_an_attribution(self):
        row = _char(self._case(), AE_ONSET_LABEL)
        assert row is not None
        assert row.left.attributions
        assert (
            row.left.attributions[0].source.value == "clinical_date_finding"
        )

    def test_the_report_date_row_stays_neutral(self):
        """It reflects when a report was filed, not anything about the
        patient, and is deliberately uncoloured by this check."""
        row = _char(self._case(), "Report date")
        assert row is not None
        assert row.left.role is EvidenceRole.NEUTRAL


class TestEventOnsetsSitUnderTheirTerm:
    def _html(self) -> str:
        return " ".join(
            _html(
                report_1=_report("R1", aes=[_ae("rash", "2020-01-01")]),
                report_2=_report("R2", aes=[_ae("rash", "2020-04-01")]),
                features=_features(matching_AEs=["rash"]),
            ).split()
        )

    def test_the_separate_onset_table_is_gone(self):
        html = self._html()
        assert '<table class="onsets">' not in html
        assert "Reported adverse event onset" not in html
        assert "shown separately so the table above does not" not in html

    def test_the_onset_is_in_the_adverse_event_table(self):
        html = self._html()
        table = html[html.index('<table class="aes">') :]
        table = table[: table.index("</table>")]
        assert "2020-01-01" in table
        assert "2020-04-01" in table

    def test_it_reads_like_the_drug_column(self):
        """Same two classes the drug column uses for a name and its dates."""
        html = self._html()
        table = html[html.index('<table class="aes">') :]
        table = table[: table.index("</table>")]
        assert '<div class="entity-name">' in table
        assert '<div class="entry-dates">' in table

    def test_a_term_only_one_report_carries_leaves_the_other_blank(self):
        html = " ".join(
            _html(
                report_1=_report("R1", aes=[_ae("rash", "2020-01-01")]),
                report_2=_report("R2", aes=[_ae("nausea")]),
                features=_features(),
            ).split()
        )
        table = html[html.index('<table class="aes">') :]
        table = table[: table.index("</table>")]
        assert "<td></td>" in table
        assert "2020-01-01" in table

    def test_several_onsets_for_one_term_are_all_shown(self):
        html = " ".join(
            _html(
                report_1=_report(
                    "R1",
                    aes=[_ae("rash", "2020-01-01"), _ae("rash", "2020-02-01")],
                ),
                report_2=_report("R2", aes=[_ae("rash", "2020-04-01")]),
                features=_features(matching_AEs=["rash"]),
            ).split()
        )
        table = html[html.index('<table class="aes">') :]
        table = table[: table.index("</table>")]
        assert "2020-01-01" in table
        assert "2020-02-01" in table


class TestADrugOnlyPairStillGetsAnOnsetRow:
    def test_a_derived_onset_from_drug_dates_alone_is_absent(self):
        """With no adverse-event onset to pick, there is nothing to derive."""
        case = _case(
            report_1=_report(
                "R1",
                drugs=[
                    Drug(
                        substance="aspirin",
                        sources=["s"],
                        drug_role="suspected",
                        start_date=PartialDate(
                            value="2020-01-01", resolution="day"
                        ),
                    )
                ],
                aes=[_ae("rash")],
            ),
            report_2=_report("R2", aes=[_ae("rash")]),
            features=_features(),
        )
        assert _char(case, AE_ONSET_LABEL) is None
