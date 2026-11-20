"""What a row's background colour is a claim about.

A row in the drug or adverse-event table is about one entity — a substance,
a term — and its tint says what vigiTwin made of *that*. It used to be the
strongest role among the row's cells, which meant a substance both reports
carried, matched as part of the shared pattern, came out red because a date
attached to one of its records did not line up. That overstated a date
difference into a finding about the medication.

The inconsistency is not hidden by this: it is red in the cell where it is
shown, which is the cell it is about.
"""

from __future__ import annotations

from vigitwin.models.enums import InconsistencySeverity
from vigitwin.models.pair_features import (
    ClinicalDateFindingSummary,
    InconsistencyCounts,
)
from vigitwin.models.report import AdverseEvent, Drug, PartialDate

from tests.test_review_html.test_evidence_build import (
    _ae_row,
    _case,
    _drug_row,
    _features,
    _report,
)
from vigitwin.review_html.evidence.models import EvidenceRole


def _drug(substance: str, start: str | None = None) -> Drug:
    return Drug(
        substance=substance,
        sources=["s"],
        start_date=(
            PartialDate(value=start, resolution="day") if start else None
        ),
    )


def _date_finding(entity: str, a: str, b: str) -> ClinicalDateFindingSummary:
    return ClinicalDateFindingSummary(
        entity_label=entity,
        severity=InconsistencySeverity.MINOR,
        source_date=a,
        matched_date=b,
        diff_days=91,
    )


class TestAMatchedDrugWithDifferingDates:
    def _case(self):
        return _case(
            _report("R1", drugs=[_drug("aspirin", "2020-01-01")]),
            _report("R2", drugs=[_drug("aspirin", "2020-04-01")]),
            _features(
                matching_drugs=["aspirin"],
                clinical_date_findings=[
                    _date_finding("drug_start:aspirin", "2020-01-01", "2020-04-01")
                ],
                inconsistency_counts=InconsistencyCounts(minor=1),
            ),
        )

    def test_the_row_is_green(self):
        """The substance matched; that is what the row is about."""
        row = _drug_row(self._case(), "aspirin")
        assert row.row_role is not EvidenceRole.INCONSISTENT
        assert row.row_css_class == "row-match"

    def test_the_presence_cell_follows_the_row(self):
        row = _drug_row(self._case(), "aspirin")
        assert row.presence.css_class == EvidenceRole.ANCHOR.css_class

    def test_the_date_itself_is_still_red(self):
        """The finding is not hidden — it is shown where it belongs."""
        row = _drug_row(self._case(), "aspirin")
        start = row.left_entries[0].start_date
        assert start is not None
        assert start.role is EvidenceRole.INCONSISTENT

    def test_the_finding_is_still_in_the_findings_table(self):
        case = self._case()
        assert [d.feature_label for d in case.retained_discrepancies] == [
            "Clinical dates"
        ]


class TestAMatchedAdverseEventWithDifferingOnsets:
    def _case(self):
        return _case(
            _report(
                "R1",
                aes=[
                    AdverseEvent(
                        pt_name="rash",
                        sources=["s"],
                        onset_date=PartialDate(
                            value="2020-01-01", resolution="day"
                        ),
                    )
                ],
            ),
            _report(
                "R2",
                aes=[
                    AdverseEvent(
                        pt_name="rash",
                        sources=["s"],
                        onset_date=PartialDate(
                            value="2020-04-01", resolution="day"
                        ),
                    )
                ],
            ),
            _features(
                matching_AEs=["rash"],
                clinical_date_findings=[
                    _date_finding("ae:rash", "2020-01-01", "2020-04-01")
                ],
                inconsistency_counts=InconsistencyCounts(minor=1),
            ),
        )

    def test_the_row_is_green(self):
        row = _ae_row(self._case(), "rash")
        assert row.row_role is not EvidenceRole.INCONSISTENT
        assert row.row_css_class == "ae-match"


class TestWhatDidNotChange:
    def test_a_flagged_substance_still_reddens_its_row(self):
        """The entity itself carries the finding, so the row is about it."""
        from vigitwin.models.pair_features import Inconsistency

        case = _case(
            _report("R1", drugs=[_drug("aspirin")]),
            _report("R2", drugs=[_drug("ibuprofen")]),
            _features(
                inconsistencies=[
                    Inconsistency(
                        feature="drug_set",
                        severity=InconsistencySeverity.MODERATE,
                        terms=["aspirin"],
                        kind="missing_in_r2",
                        source="rule_drug_set",
                    )
                ],
                inconsistency_counts=InconsistencyCounts(moderate=1),
            ),
        )
        assert _drug_row(case, "aspirin").row_css_class == "row-mismatch"

    def test_an_unmatched_substance_still_takes_its_cells_evidence(self):
        """With no verdict on the entity, the strongest cell still decides,
        so a date inconsistency is never left with no tint at all."""
        case = _case(
            _report("R1", drugs=[_drug("aspirin", "2020-01-01")]),
            _report("R2", drugs=[_drug("aspirin", "2020-04-01")]),
            _features(
                # No matching_drugs: the substance is in neither report's
                # shared pattern, so its name cell carries no role.
                clinical_date_findings=[
                    _date_finding("drug_start:aspirin", "2020-01-01", "2020-04-01")
                ],
                inconsistency_counts=InconsistencyCounts(minor=1),
            ),
        )
        row = _drug_row(case, "aspirin")
        assert row.name.role is EvidenceRole.NEUTRAL
        assert row.row_css_class == "row-mismatch"

    def test_a_plain_matched_drug_is_still_green(self):
        case = _case(
            _report("R1", drugs=[_drug("aspirin")]),
            _report("R2", drugs=[_drug("aspirin")]),
            _features(matching_drugs=["aspirin"]),
        )
        assert _drug_row(case, "aspirin").row_css_class == "row-match"


class TestAClinicalDateFindingShowsItsDatesInRed:
    """The page's one colour rule could not reach inside a prose string.

    "Overall adverse event onset date: 2021-04-09 versus 2021-04-08, at least
    1 day apart." is an inconsistency about two dates that showed neither of
    them in red — on a page whose entire visual vocabulary exists to mark
    exactly that.
    """

    def _case(self):
        return _case(
            _report("R1", drugs=[_drug("aspirin", "2020-01-01")]),
            _report("R2", drugs=[_drug("aspirin", "2020-04-01")]),
            _features(
                matching_drugs=["aspirin"],
                clinical_date_findings=[
                    _date_finding(
                        "drug_start:aspirin", "2020-01-01", "2020-04-01"
                    )
                ],
                inconsistency_counts=InconsistencyCounts(minor=1),
            ),
        )

    def test_the_two_dates_are_cells(self):
        (finding,) = self._case().retained_discrepancies
        assert finding.left is not None and finding.right is not None
        assert finding.left.role is EvidenceRole.INCONSISTENT
        assert finding.right.role is EvidenceRole.INCONSISTENT

    def test_the_sentence_around_them_is_kept(self):
        (finding,) = self._case().retained_discrepancies
        assert finding.lead == "Drug start date for aspirin"
        assert finding.tail == ", at least 91 days apart."

    def test_the_full_prose_is_still_on_the_model(self):
        (finding,) = self._case().retained_discrepancies
        assert finding.text == (
            "Drug start date for aspirin: 2020-01-01 versus 2020-04-01, "
            "at least 91 days apart."
        )

    def test_the_findings_table_colours_them(self):
        from vigitwin.review_html.evidence.models import EvidencePageVM
        from vigitwin.review_html.render import render_evidence_page

        html = " ".join(
            render_evidence_page(
                EvidencePageVM(title="t", cases=[self._case()])
            ).split()
        )
        findings = html[html.index("3. Relationship") :]
        assert 'class="value-mismatch">2020-01-01</span>' in findings
        assert 'class="value-mismatch">2020-04-01</span>' in findings
        assert "Drug start date for aspirin:" in findings
        assert "at least 91 days apart." in findings

    def test_the_panel_chip_still_shows_the_bare_pair(self):
        from vigitwin.review_html.evidence.models import EvidencePageVM
        from vigitwin.review_html.render import render_evidence_page

        html = " ".join(
            render_evidence_page(
                EvidencePageVM(title="t", cases=[self._case()])
            ).split()
        )
        panel = html[html.index("Assessment at a glance") :]
        panel = panel[: panel.index("<h3>1. Report comparison</h3>")]
        assert "Drug start date" not in panel
        assert "days apart" not in panel
        (finding,) = self._case().retained_discrepancies
        assert finding.chip_text == "2020-01-01 vs 2020-04-01"

    def test_a_downgrade_reason_stays_in_the_tail(self):
        from vigitwin.models.pair_features import ClinicalDateFindingSummary

        case = _case(
            _report("R1", drugs=[_drug("aspirin", "2020-01-01")]),
            _report("R2", drugs=[_drug("aspirin", "2020-04-01")]),
            _features(
                matching_drugs=["aspirin"],
                clinical_date_findings=[
                    ClinicalDateFindingSummary(
                        entity_label="drug_start:aspirin",
                        severity=InconsistencySeverity.MINOR,
                        source_date="2020-01-01",
                        matched_date="2020-04-01",
                        diff_days=91,
                        downgrades=["concomitant drug downgrade (-1 step)"],
                    )
                ],
                inconsistency_counts=InconsistencyCounts(minor=1),
            ),
        )
        (finding,) = case.retained_discrepancies
        assert "severity reduced because" in finding.tail
