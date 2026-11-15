"""Tests for evidence attribution: what turns a cell green, red, or neither.

Every test here is a statement about the one rule the format exists to
enforce — a colour follows the stored assessment, never a comparison of the
two displayed values. The anti-requirements matter most: a drug only one
report carries is *not* red, and two identical report dates are *not* green.
"""

from __future__ import annotations

from datetime import date, timedelta

from llm_dup_dev.models.classification import (
    ClassificationResult,
    InconsistencyPenalty,
    PenaltyTerm,
)
from llm_dup_dev.models.enums import (
    AnchorType,
    DrugRole,
    IndependenceAssessment,
    IndependenceReason,
    InconsistencySeverity,
    RelationshipAssessment,
    ReportingContextLevel,
)
from llm_dup_dev.models.pair_features import (
    AnchorPopulation,
    ClinicalDateFindingSummary,
    FeatureContribution,
    Inconsistency,
    InconsistencyCounts,
    PairFeatures,
    ProbabilityEstimate,
    ReportingContext,
)
from llm_dup_dev.models.report import (
    AdverseEvent,
    AgeValue,
    DateValue,
    Drug,
    PartialDate,
    Report,
    SourcedValue,
)
from llm_dup_dev.review_html.evidence.build import build_evidence_case
from llm_dup_dev.review_html.evidence.models import EvidenceRole


def _report(
    report_id: str,
    *,
    drugs: list[Drug] | None = None,
    aes: list[AdverseEvent] | None = None,
    sex: str | None = None,
    age: float | None = None,
    country: str = "US",
    report_date: str = "2020-06-12",
) -> Report:
    return Report(
        report_id=report_id,
        patient_sex_values=(
            [SourcedValue(value=sex, sources=["structured"])] if sex else []
        ),
        patient_age_values=(
            [AgeValue(value=age, unit="years", sources=["structured"])]
            if age is not None
            else []
        ),
        country_of_origin_values=[
            SourcedValue(value=country, sources=["structured"])
        ],
        report_date_values=[
            DateValue(value=report_date, resolution="day", sources=["structured"])
        ],
        drugs=drugs or [],
        adverse_events=aes or [],
    )


def _features(**overrides) -> PairFeatures:
    base = {
        "pair_id": "R1__R2",
        "reporting_context": ReportingContext(
            level=ReportingContextLevel.COUNTRY_YEAR, country="US", year=2020
        ),
        "matching_drugs": [],
        "matching_AEs": [],
        "anchor": AnchorPopulation(
            N_drugs=10,
            N_AEs=10,
            N_drugs_AEs=5,
            type=AnchorType.DRUGS_AES,
            N_anchor=5,
        ),
        "probability": ProbabilityEstimate(
            p_joint=1e-3, p_match=1e-3, feature_contributions=[]
        ),
        "L_match": 3.0,
        "L_patient": 1.0,
    }
    base.update(overrides)
    return PairFeatures(**base)


def _classification(**overrides) -> ClassificationResult:
    base = {
        "pair_id": "R1__R2",
        "independence_assessment": IndependenceAssessment.NON_INDEPENDENT,
        "relationship_assessment": RelationshipAssessment.SAME_CASE,
        "independence_reason": IndependenceReason.RARITY_COMPELLING,
        "penalty": InconsistencyPenalty(w_tot=0.0, threshold=4.0),
    }
    base.update(overrides)
    return ClassificationResult(**base)


def _case(report_1: Report, report_2: Report, features: PairFeatures, **kw):
    return build_evidence_case(
        report_1=report_1,
        report_2=report_2,
        pair_id="R1__R2",
        features=features,
        classification=kw.pop("classification", _classification()),
        **kw,
    )


def _drug_row(case, substance: str):
    return next(r for r in case.drugs if r.name.text == substance)


def _ae_row(case, term: str):
    return next(r for r in case.adverse_events if r.term.text == term)


def _char(case, label: str):
    return next(r for r in case.characteristics if r.label == label)


class TestOneSidedEntriesAreNotAutomaticallyRed:
    """The headline anti-requirement of the whole format."""

    def test_drug_in_one_report_only_is_neutral_without_a_finding(self):
        r1 = _report("R1", drugs=[Drug(substance="aspirin", sources=["s"])])
        r2 = _report("R2", drugs=[])
        case = _case(r1, r2, _features())
        row = _drug_row(case, "aspirin")
        assert row.name.role is EvidenceRole.NEUTRAL
        assert row.row_role is EvidenceRole.NEUTRAL
        assert row.presence.label == "A only"
        assert row.presence.role is EvidenceRole.NEUTRAL

    def test_ae_in_one_report_only_is_neutral_without_a_finding(self):
        r1 = _report("R1", aes=[AdverseEvent(pt_name="rash", sources=["s"])])
        r2 = _report("R2", aes=[])
        case = _case(r1, r2, _features())
        row = _ae_row(case, "rash")
        assert row.term.role is EvidenceRole.NEUTRAL
        assert row.presence.label == "A only"

    def test_compatible_but_differently_coded_terms_stay_neutral(self):
        """Two pregnancy-exposure terms vigiTwin did not flag are neutral."""
        r1 = _report(
            "R1",
            aes=[
                AdverseEvent(
                    pt_name="Foetal exposure during pregnancy", sources=["s"]
                )
            ],
        )
        r2 = _report(
            "R2",
            aes=[
                AdverseEvent(
                    pt_name="Maternal exposure during pregnancy", sources=["s"]
                )
            ],
        )
        case = _case(r1, r2, _features())
        assert all(
            row.term.role is EvidenceRole.NEUTRAL
            for row in case.adverse_events
        )


class TestReportDateAndCountryAreNeverEvidence:
    """Agreement on reporting metadata is not patient-level evidence."""

    def test_identical_report_dates_are_neutral(self):
        r1 = _report("R1", report_date="2010-03-01")
        r2 = _report("R2", report_date="2010-03-01")
        case = _case(r1, r2, _features())
        row = _char(case, "Report date")
        assert row.left.role is EvidenceRole.NEUTRAL
        assert row.right.role is EvidenceRole.NEUTRAL
        assert row.left.attributions == []

    def test_matching_country_is_neutral_and_explained_as_context(self):
        case = _case(_report("R1"), _report("R2"), _features())
        row = _char(case, "Country")
        assert row.left.role is EvidenceRole.NEUTRAL
        assert "defines that context" in case.context_note


class TestMatchingEvidenceIsGreen:
    """Green comes from the stored sets and contributions."""

    def test_shared_drug_is_green_and_marked_as_anchor_when_it_is_one(self):
        drug = Drug(substance="aspirin", sources=["s"])
        case = _case(
            _report("R1", drugs=[drug]),
            _report("R2", drugs=[Drug(substance="aspirin", sources=["s"])]),
            _features(
                matching_drugs=["aspirin"],
                anchor=AnchorPopulation(
                    N_drugs=4,
                    N_AEs=99,
                    N_drugs_AEs=4,
                    type=AnchorType.DRUGS,
                    N_anchor=4,
                ),
            ),
        )
        row = _drug_row(case, "aspirin")
        assert row.name.role is EvidenceRole.ANCHOR
        assert row.presence.label == "Both"
        assert row.presence.role is EvidenceRole.ANCHOR

    def test_shared_drug_is_matching_when_the_aes_define_the_anchor(self):
        case = _case(
            _report("R1", drugs=[Drug(substance="aspirin", sources=["s"])]),
            _report("R2", drugs=[Drug(substance="aspirin", sources=["s"])]),
            _features(
                matching_drugs=["aspirin"],
                anchor=AnchorPopulation(
                    N_drugs=90,
                    N_AEs=3,
                    N_drugs_AEs=3,
                    type=AnchorType.AES,
                    N_anchor=3,
                ),
            ),
        )
        assert _drug_row(case, "aspirin").name.role is EvidenceRole.MATCHING

    def test_shared_anchor_term_is_green_even_without_extra_evidence(self):
        """An anchor member is green although it added nothing to the score."""
        case = _case(
            _report("R1", aes=[AdverseEvent(pt_name="rash", sources=["s"])]),
            _report("R2", aes=[AdverseEvent(pt_name="rash", sources=["s"])]),
            _features(
                matching_AEs=["rash"],
                anchor=AnchorPopulation(
                    N_drugs=0,
                    N_AEs=2,
                    N_drugs_AEs=2,
                    type=AnchorType.AES,
                    N_anchor=2,
                ),
                probability=ProbabilityEstimate(
                    p_joint=1.0, p_match=1.0, feature_contributions=[]
                ),
            ),
        )
        assert _ae_row(case, "rash").term.role is EvidenceRole.ANCHOR

    def test_demographic_contribution_greens_both_sides(self):
        case = _case(
            _report("R1", sex="male"),
            _report("R2", sex="male"),
            _features(
                probability=ProbabilityEstimate(
                    p_joint=0.5,
                    p_match=0.5,
                    feature_contributions=[
                        FeatureContribution(
                            feature="patient_sex", p_f=0.5, method="domain_size"
                        )
                    ],
                )
            ),
        )
        row = _char(case, "Sex")
        assert row.left.role is EvidenceRole.MATCHING
        assert row.right.role is EvidenceRole.MATCHING


class TestClinicalDateAttribution:
    """A matched date greens the period it belongs to, not the whole drug."""

    def _two_period_pair(self):
        r1 = _report(
            "R1",
            drugs=[
                Drug(
                    substance="topiramate",
                    sources=["s"],
                    start_date=PartialDate(value="2002-01-04", resolution="day"),
                    end_date=PartialDate(value="2002-03-22", resolution="day"),
                ),
                Drug(
                    substance="topiramate",
                    sources=["s"],
                    start_date=PartialDate(value="2002-05-02", resolution="day"),
                ),
            ],
        )
        r2 = _report(
            "R2",
            drugs=[
                Drug(substance="topiramate", sources=["s"]),
                Drug(
                    substance="topiramate",
                    sources=["s"],
                    start_date=PartialDate(value="2002-01-04", resolution="day"),
                    end_date=PartialDate(value="2002-03-22", resolution="day"),
                ),
            ],
        )
        features = _features(
            matching_drugs=["topiramate"],
            probability=ProbabilityEstimate(
                p_joint=1e-5,
                p_match=1e-5,
                feature_contributions=[
                    FeatureContribution(
                        feature="clinical_date",
                        p_f=0.002,
                        method="domain_size",
                        matched_value="2002-01-04",
                        value_resolution="day",
                    ),
                    FeatureContribution(
                        feature="clinical_date",
                        p_f=0.002,
                        method="domain_size",
                        matched_value="2002-03-22",
                        value_resolution="day",
                    ),
                ],
            ),
        )
        return r1, r2, features

    def test_matched_dates_are_green_on_both_sides(self):
        r1, r2, features = self._two_period_pair()
        row = _drug_row(_case(r1, r2, features), "topiramate")
        left_first = row.left_entries[0]
        assert left_first.start_date is not None
        assert left_first.start_date.role is EvidenceRole.MATCHING
        assert left_first.end_date is not None
        assert left_first.end_date.role is EvidenceRole.MATCHING
        right_matched = row.right_entries[1]
        assert right_matched.start_date is not None
        assert right_matched.start_date.role is EvidenceRole.MATCHING

    def test_unmatched_period_stays_neutral(self):
        """The second regimen's date was not scored, so it is not green."""
        r1, r2, features = self._two_period_pair()
        row = _drug_row(_case(r1, r2, features), "topiramate")
        second = row.left_entries[1]
        assert second.start_date is not None
        assert second.start_date.text == "2002-05-02"
        assert second.start_date.role is EvidenceRole.NEUTRAL

    def _legacy_date_features(self, description: str):
        """A clinical-date contribution with no ``matched_value``."""
        return _features(
            matching_drugs=["topiramate"],
            probability=ProbabilityEstimate(
                p_joint=1e-3,
                p_match=1e-3,
                feature_contributions=[
                    FeatureContribution(
                        feature="clinical_date",
                        p_f=0.002,
                        method="domain_size",
                        description=description,
                    )
                ],
            ),
        )

    def test_matched_value_is_recovered_from_the_description(self):
        """A run saved before the field existed still highlights its dates.

        The date is read from the description the run itself wrote, so the
        highlighting is the run's own finding. The period whose dates did not
        match stays neutral, exactly as for a run with the structured field.
        """
        r1, r2, _ = self._two_period_pair()
        case = _case(
            r1,
            r2,
            self._legacy_date_features("clinical_date 2002-01-04 (day): D_f=540"),
        )
        assert "clinical_date_value_recovered" in [
            g.code.value for g in case.gaps
        ]
        assert "clinical_date_value_missing" not in [
            g.code.value for g in case.gaps
        ]
        row = _drug_row(case, "topiramate")
        first = row.left_entries[0]
        assert first.start_date is not None
        assert first.start_date.role is EvidenceRole.MATCHING
        second = row.left_entries[1]
        assert second.start_date is not None
        assert second.start_date.text == "2002-05-02"
        assert second.start_date.role is EvidenceRole.NEUTRAL

    def test_an_unparseable_description_colours_nothing(self):
        """No value and no recoverable one is reported, never guessed."""
        r1, r2, _ = self._two_period_pair()
        case = _case(
            r1, r2, self._legacy_date_features("a clinical date matched")
        )
        assert "clinical_date_value_missing" in [
            g.code.value for g in case.gaps
        ]
        row = _drug_row(case, "topiramate")
        for entry in row.left_entries:
            if entry.start_date is not None:
                assert entry.start_date.role is EvidenceRole.NEUTRAL

    def test_the_recovered_date_labels_its_matching_feature_row(self):
        """The row names the date, not the generic feature name."""
        r1, r2, _ = self._two_period_pair()
        case = _case(
            r1,
            r2,
            self._legacy_date_features("clinical_date 2002-01-04 (day): D_f=540"),
        )
        labels = [row.label.text for row in case.matching_features]
        assert "2002-01-04" in labels
        assert "Clinical date" not in labels


class TestInconsistenciesAreRed:
    """Red comes from the retained findings, named by their own fields."""

    def test_drug_set_terms_colour_the_named_substance(self):
        r1 = _report(
            "R1",
            drugs=[
                Drug(substance="aspirin", sources=["s"], role=DrugRole.SUSPECTED),
                Drug(substance="warfarin", sources=["s"]),
            ],
        )
        r2 = _report("R2", drugs=[Drug(substance="warfarin", sources=["s"])])
        case = _case(
            r1,
            r2,
            _features(
                matching_drugs=["warfarin"],
                inconsistencies=[
                    Inconsistency(
                        feature="drug_set",
                        severity=InconsistencySeverity.MODERATE,
                        terms=["aspirin"],
                        kind="missing_in_r2",
                    )
                ],
                inconsistency_counts=InconsistencyCounts(moderate=1),
            ),
        )
        assert _drug_row(case, "aspirin").name.role is EvidenceRole.INCONSISTENT
        assert _drug_row(case, "warfarin").name.role is not EvidenceRole.INCONSISTENT

    def _legacy_drug_set_case(self, description: str):
        """A drug-set finding with no structured ``terms`` or ``kind``."""
        r1 = _report("R1", drugs=[Drug(substance="aspirin", sources=["s"])])
        r2 = _report("R2", drugs=[])
        return _case(
            r1,
            r2,
            _features(
                inconsistencies=[
                    Inconsistency(
                        feature="drug_set",
                        severity=InconsistencySeverity.MODERATE,
                        description=description,
                    )
                ],
                inconsistency_counts=InconsistencyCounts(moderate=1),
            ),
        )

    def test_drug_set_terms_are_recovered_from_the_description(self):
        """The substance is highlighted, and the recovery is footnoted."""
        case = self._legacy_drug_set_case(
            "Key drug(s) in report 1 not found in report 2: ['aspirin']"
        )
        assert "drug_set_terms_recovered" in [g.code.value for g in case.gaps]
        assert "drug_set_terms_missing" not in [g.code.value for g in case.gaps]
        assert _drug_row(case, "aspirin").name.role is EvidenceRole.INCONSISTENT

    def test_a_recovered_drug_set_finding_leads_with_the_term(self):
        """Same sentence as a run that stored the terms, same direction."""
        case = self._legacy_drug_set_case(
            "Key drug(s) in report 1 not found in report 2: ['aspirin']"
        )
        text = [d.text for d in case.retained_discrepancies][0]
        assert text.startswith("aspirin")
        assert "report A absent from report B" in text

    def test_drug_set_without_terms_or_a_parseable_description(self):
        case = self._legacy_drug_set_case("the drug sets differ")
        assert "drug_set_terms_missing" in [g.code.value for g in case.gaps]
        assert _drug_row(case, "aspirin").name.role is EvidenceRole.NEUTRAL

    def test_demographic_inconsistency_colours_both_sides(self):
        case = _case(
            _report("R1", age=54),
            _report("R2", age=61),
            _features(
                inconsistencies=[
                    Inconsistency(
                        feature="patient_age",
                        severity=InconsistencySeverity.STRONG,
                        difference=7.0,
                        units="years",
                    )
                ],
                inconsistency_counts=InconsistencyCounts(strong=1),
            ),
        )
        row = _char(case, "Age")
        assert row.left.role is EvidenceRole.INCONSISTENT
        assert row.right.role is EvidenceRole.INCONSISTENT

    def test_ae_finding_colours_the_named_term(self):
        r1 = _report("R1", aes=[AdverseEvent(pt_name="rash", sources=["s"])])
        r2 = _report(
            "R2",
            aes=[AdverseEvent(pt_name="decreased appetite", sources=["s"])],
        )
        case = _case(
            r1,
            r2,
            _features(
                inconsistencies=[
                    Inconsistency(
                        feature="ae_clinical_picture:unmatched_burden:r2_to_r1",
                        severity=InconsistencySeverity.MINOR,
                        terms=["decreased appetite"],
                        source="rule_ae_clinical_picture",
                    )
                ],
                inconsistency_counts=InconsistencyCounts(minor=1),
            ),
        )
        assert (
            _ae_row(case, "decreased appetite").term.role
            is EvidenceRole.INCONSISTENT
        )
        assert _ae_row(case, "rash").term.role is EvidenceRole.NEUTRAL

    def test_red_survives_on_an_unrelated_pair(self):
        """No same-case decision was reached, but the finding is still real."""
        r1 = _report("R1", aes=[AdverseEvent(pt_name="cleft lip", sources=["s"])])
        r2 = _report("R2", aes=[])
        case = _case(
            r1,
            r2,
            _features(
                L_match=0.0,
                inconsistencies=[
                    Inconsistency(
                        feature="ae_clinical_picture:unmatched_burden:r1_to_r2",
                        severity=InconsistencySeverity.MINOR,
                        terms=["cleft lip"],
                        source="rule_ae_clinical_picture",
                    )
                ],
                inconsistency_counts=InconsistencyCounts(minor=1),
            ),
            classification=_classification(
                independence_assessment=IndependenceAssessment.INDEPENDENT,
                relationship_assessment=None,
                independence_reason=IndependenceReason.RARITY_INSUFFICIENT,
                penalty=InconsistencyPenalty(w_tot=0.4),
            ),
        )
        assert _ae_row(case, "cleft lip").term.role is EvidenceRole.INCONSISTENT


class TestSuppressedAndDowngraded:
    """Two different mechanisms, two different outcomes."""

    def test_suppressed_finding_is_not_coloured(self):
        r1 = _report("R1", aes=[AdverseEvent(pt_name="rash", sources=["s"])])
        r2 = _report("R2", aes=[])
        case = _case(
            r1,
            r2,
            _features(
                inconsistencies=[
                    Inconsistency(
                        feature="ae_clinical_picture:unmatched_cluster",
                        severity=InconsistencySeverity.MODERATE,
                        terms=["rash"],
                        source="llm_ae_clinical_picture",
                        suppressed=True,
                        suppressed_by="decisive_global_mismatch",
                    )
                ],
            ),
        )
        row = _ae_row(case, "rash")
        assert row.term.role is EvidenceRole.NEUTRAL
        assert [a.kind.value for a in row.term.attributions] == [
            "suppressed_finding"
        ]

    def test_suppressed_finding_is_still_listed_separately(self):
        case = _case(
            _report("R1", aes=[AdverseEvent(pt_name="rash", sources=["s"])]),
            _report("R2", aes=[]),
            _features(
                inconsistencies=[
                    Inconsistency(
                        feature="ae_clinical_picture:unmatched_cluster",
                        severity=InconsistencySeverity.MODERATE,
                        terms=["rash"],
                        source="llm_ae_clinical_picture",
                        suppressed=True,
                        suppressed_by="decisive_global_mismatch",
                    )
                ],
            ),
        )
        assert case.retained_discrepancies == []
        assert len(case.suppressed_discrepancies) == 1

    def test_downgraded_finding_is_red_at_its_final_severity(self):
        r1 = _report(
            "R1",
            drugs=[
                Drug(
                    substance="aspirin",
                    sources=["s"],
                    start_date=PartialDate(value="2020-01-01", resolution="day"),
                )
            ],
        )
        r2 = _report(
            "R2",
            drugs=[
                Drug(
                    substance="aspirin",
                    sources=["s"],
                    start_date=PartialDate(value="2020-04-01", resolution="day"),
                )
            ],
        )
        case = _case(
            r1,
            r2,
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
        row = _drug_row(case, "aspirin")
        start = row.left_entries[0].start_date
        assert start is not None
        assert start.role is EvidenceRole.INCONSISTENT
        attribution = next(
            a for a in start.attributions if a.source.value == "clinical_date_finding"
        )
        assert attribution.kind.value == "downgraded_retained"
        assert attribution.severity == "minor"


class TestRowOrderingAndMixedEvidence:
    """Rows order by presence; evidence is carried by colour, not position."""

    def test_drug_rows_order_both_then_a_only_then_b_only(self):
        r1 = _report(
            "R1",
            drugs=[
                Drug(substance="a_only", sources=["s"]),
                Drug(substance="shared", sources=["s"]),
            ],
        )
        r2 = _report(
            "R2",
            drugs=[
                Drug(substance="b_only", sources=["s"]),
                Drug(substance="shared", sources=["s"]),
            ],
        )
        case = _case(r1, r2, _features(matching_drugs=["shared"]))
        assert [r.name.text for r in case.drugs] == [
            "shared",
            "a_only",
            "b_only",
        ]

    def test_key_drugs_come_first_within_a_one_sided_group(self):
        """A suspected drug one report lists alone outranks a concomitant one."""
        r1 = _report(
            "R1",
            drugs=[
                Drug(substance="zzz_concomitant", sources=["s"], role=DrugRole.CONCOMITANT),
                Drug(substance="aaa_concomitant", sources=["s"], role=DrugRole.CONCOMITANT),
                Drug(substance="mmm_suspected", sources=["s"], role=DrugRole.SUSPECTED),
            ],
        )
        case = _case(r1, _report("R2"), _features())
        assert [r.name.text for r in case.drugs] == [
            "mmm_suspected",
            "aaa_concomitant",
            "zzz_concomitant",
        ]

    def test_interacting_counts_as_a_key_role(self):
        r1 = _report(
            "R1",
            drugs=[
                Drug(substance="zzz_interacting", sources=["s"], role=DrugRole.INTERACTING),
                Drug(substance="aaa_concomitant", sources=["s"], role=DrugRole.CONCOMITANT),
            ],
        )
        case = _case(r1, _report("R2"), _features())
        assert [r.name.text for r in case.drugs][0] == "zzz_interacting"

    def test_shared_drugs_stay_alphabetical(self):
        """Key-drug precedence applies to one-sided rows, not the shared list."""
        drugs = [
            Drug(substance="zzz", sources=["s"], role=DrugRole.SUSPECTED),
            Drug(substance="aaa", sources=["s"], role=DrugRole.CONCOMITANT),
        ]
        case = _case(
            _report("R1", drugs=drugs),
            _report("R2", drugs=list(drugs)),
            _features(matching_drugs=["aaa", "zzz"]),
        )
        assert [r.name.text for r in case.drugs] == ["aaa", "zzz"]

    def test_ae_rows_order_both_then_a_only_then_b_only(self):
        r1 = _report(
            "R1",
            aes=[
                AdverseEvent(pt_name="a_only", sources=["s"]),
                AdverseEvent(pt_name="shared", sources=["s"]),
            ],
        )
        r2 = _report(
            "R2",
            aes=[
                AdverseEvent(pt_name="b_only", sources=["s"]),
                AdverseEvent(pt_name="shared", sources=["s"]),
            ],
        )
        case = _case(r1, r2, _features(matching_AEs=["shared"]))
        assert [r.term.text for r in case.adverse_events] == [
            "shared",
            "a_only",
            "b_only",
        ]

    def test_each_side_carries_its_own_spelling(self):
        case = _case(
            _report("R1", drugs=[Drug(substance="Topiramate", sources=["s"])]),
            _report("R2", drugs=[Drug(substance="topiramate", sources=["s"])]),
            _features(matching_drugs=["topiramate"]),
        )
        row = case.drugs[0]
        assert row.left_name is not None and row.left_name.text == "Topiramate"
        assert row.right_name is not None and row.right_name.text == "topiramate"

    def test_side_cells_share_the_rows_verdict(self):
        case = _case(
            _report("R1", drugs=[Drug(substance="aspirin", sources=["s"])]),
            _report("R2", drugs=[Drug(substance="aspirin", sources=["s"])]),
            _features(matching_drugs=["aspirin"]),
        )
        row = case.drugs[0]
        assert row.left_name is not None and row.right_name is not None
        assert row.left_name.role is row.name.role
        assert row.right_name.role is row.name.role

    def test_absent_side_has_no_name_cell(self):
        case = _case(
            _report("R1", drugs=[Drug(substance="aspirin", sources=["s"])]),
            _report("R2"),
            _features(),
        )
        row = case.drugs[0]
        assert row.left_name is not None
        assert row.right_name is None

    def test_mixed_row_is_red_but_keeps_its_green_cell(self):
        """A matching date does not excuse a flagged substance, or vice versa."""
        r1 = _report(
            "R1",
            drugs=[
                Drug(
                    substance="aspirin",
                    sources=["s"],
                    role=DrugRole.SUSPECTED,
                    start_date=PartialDate(value="2020-01-01", resolution="day"),
                )
            ],
        )
        r2 = _report(
            "R2",
            drugs=[
                Drug(
                    substance="aspirin",
                    sources=["s"],
                    start_date=PartialDate(value="2020-01-01", resolution="day"),
                )
            ],
        )
        case = _case(
            r1,
            r2,
            _features(
                matching_drugs=["aspirin"],
                probability=ProbabilityEstimate(
                    p_joint=1e-3,
                    p_match=1e-3,
                    feature_contributions=[
                        FeatureContribution(
                            feature="clinical_date",
                            p_f=0.002,
                            method="domain_size",
                            matched_value="2020-01-01",
                            value_resolution="day",
                        )
                    ],
                ),
                inconsistencies=[
                    Inconsistency(
                        feature="drug_set",
                        severity=InconsistencySeverity.MODERATE,
                        terms=["aspirin"],
                        kind="missing_in_r2",
                    )
                ],
                inconsistency_counts=InconsistencyCounts(moderate=1),
            ),
        )
        row = _drug_row(case, "aspirin")
        assert row.row_role is EvidenceRole.INCONSISTENT
        assert row.name.role is EvidenceRole.INCONSISTENT
        start = row.left_entries[0].start_date
        assert start is not None
        assert start.role is EvidenceRole.MATCHING

    def test_ordering_is_stable_under_reordered_input(self):
        drugs_forward = [
            Drug(substance="beta", sources=["s"]),
            Drug(substance="alpha", sources=["s"]),
        ]
        features = _features(matching_drugs=["alpha", "beta"])
        forwards = _case(
            _report("R1", drugs=drugs_forward),
            _report("R2", drugs=list(drugs_forward)),
            features,
        )
        backwards = _case(
            _report("R1", drugs=list(reversed(drugs_forward))),
            _report("R2", drugs=list(reversed(drugs_forward))),
            features,
        )
        assert [r.name.text for r in forwards.drugs] == [
            r.name.text for r in backwards.drugs
        ]


class TestSubstitutionInvariance:
    """The phase-2 contract, as a test.

    The later publication phase shifts every date in a pair by one shared
    offset. If evidence attribution survived only by accident — say, because
    something matched a literal date string against a value stored elsewhere —
    that transform would silently drop a colour, and the supplement would
    show a date as unremarkable when the assessment had scored it.

    So the test builds the same case twice with every date shifted, shifting
    the stored ``matched_value`` with it, and asserts the two evidence trees
    are identical once the displayed text is set aside. The roles, the
    attributions, the row grouping and the contributions must all be the same.
    """

    @staticmethod
    def _shift(value: str, days: int) -> str:
        return (date.fromisoformat(value) + timedelta(days=days)).isoformat()

    def _rich_case(self, offset: int = 0):
        def d(value: str) -> str:
            return self._shift(value, offset)

        r1 = _report(
            "R1",
            sex="male",
            age=54,
            report_date=d("2020-06-12"),
            drugs=[
                Drug(
                    substance="topiramate",
                    sources=["s"],
                    role=DrugRole.SUSPECTED,
                    form="tablet",
                    strength="100 mg",
                    start_date=PartialDate(value=d("2002-01-04"), resolution="day"),
                    end_date=PartialDate(value=d("2002-03-22"), resolution="day"),
                ),
                Drug(
                    substance="topiramate",
                    sources=["s"],
                    start_date=PartialDate(value=d("2002-05-02"), resolution="day"),
                ),
                Drug(
                    substance="vitamins nos",
                    sources=["s"],
                    role=DrugRole.SUSPECTED,
                ),
            ],
            aes=[
                AdverseEvent(
                    pt_name="cleft lip",
                    sources=["s"],
                    start_date=PartialDate(value=d("2002-02-01"), resolution="day"),
                )
            ],
        )
        r2 = _report(
            "R2",
            sex="male",
            age=54,
            report_date=d("2020-06-12"),
            drugs=[
                Drug(
                    substance="topiramate",
                    sources=["s"],
                    start_date=PartialDate(value=d("2002-01-04"), resolution="day"),
                    end_date=PartialDate(value=d("2002-03-22"), resolution="day"),
                )
            ],
            aes=[AdverseEvent(pt_name="cleft lip", sources=["s"])],
        )
        features = _features(
            matching_drugs=["topiramate"],
            matching_AEs=["cleft lip"],
            probability=ProbabilityEstimate(
                p_joint=1e-8,
                p_match=1e-8,
                feature_contributions=[
                    FeatureContribution(
                        feature="patient_sex", p_f=0.5, method="domain_size"
                    ),
                    FeatureContribution(
                        feature="clinical_date",
                        p_f=0.002,
                        method="domain_size",
                        matched_value=d("2002-01-04"),
                        value_resolution="day",
                    ),
                    FeatureContribution(
                        feature="clinical_date",
                        p_f=0.004,
                        method="domain_size",
                        matched_value=d("2002-03-22"),
                        value_resolution="day",
                    ),
                ],
            ),
            inconsistencies=[
                Inconsistency(
                    feature="drug_set",
                    severity=InconsistencySeverity.MODERATE,
                    terms=["vitamins nos"],
                    kind="missing_in_r2",
                )
            ],
            inconsistency_counts=InconsistencyCounts(moderate=1),
        )
        return _case(r1, r2, features)

    @staticmethod
    def _strip_text(node):
        """The evidence tree with every displayed value removed."""
        if isinstance(node, dict):
            return {
                key: TestSubstitutionInvariance._strip_text(value)
                for key, value in node.items()
                if not (key == "text" and "role" in node and "kind" in node)
            }
        if isinstance(node, list):
            return [TestSubstitutionInvariance._strip_text(i) for i in node]
        return node

    def test_a_shared_date_offset_preserves_the_evidence_tree(self):
        original = self._rich_case(0).model_dump(mode="json")
        shifted = self._rich_case(37).model_dump(mode="json")

        assert shifted != original, "the fixture must actually change the dates"
        assert self._strip_text(shifted) == self._strip_text(original)

    def test_the_matched_dates_are_green_in_both_versions(self):
        """Guards the test above: it would also pass if nothing were green."""
        for offset in (0, 37):
            row = _drug_row(self._rich_case(offset), "topiramate")
            first = row.left_entries[0]
            assert first.start_date is not None
            assert first.start_date.role is EvidenceRole.MATCHING
            assert first.end_date is not None
            assert first.end_date.role is EvidenceRole.MATCHING

    def test_a_shifted_display_value_keeps_its_original_contribution(self):
        """The contribution attached to a date does not change when it moves."""
        before = _drug_row(self._rich_case(0), "topiramate")
        after = _drug_row(self._rich_case(37), "topiramate")
        before_cell = before.left_entries[0].start_date
        after_cell = after.left_entries[0].start_date
        assert before_cell is not None and after_cell is not None
        assert before_cell.text != after_cell.text
        assert [a.model_dump() for a in before_cell.attributions] == [
            a.model_dump() for a in after_cell.attributions
        ]


class TestFindingWording:
    """Findings read as what the check actually establishes."""

    def test_drug_set_says_absent_from_the_other_report(self):
        """Not "a key drug in report A only", which implies B lists it too."""
        case = _case(
            _report(
                "R1",
                drugs=[
                    Drug(
                        substance="vitamins nos",
                        sources=["s"],
                        role=DrugRole.SUSPECTED,
                    )
                ],
            ),
            _report("R2"),
            _features(
                inconsistencies=[
                    Inconsistency(
                        feature="drug_set",
                        severity=InconsistencySeverity.MODERATE,
                        terms=["vitamins nos"],
                        kind="missing_in_r2",
                    )
                ],
                inconsistency_counts=InconsistencyCounts(moderate=1),
            ),
        )
        (finding,) = case.retained_discrepancies
        assert finding.feature_label == "Drugs"
        # The substance leads, in the spelling report A used — not the
        # lowercased key the inconsistency stores.
        assert finding.text == (
            "vitamins nos — key drug on report A absent from report B."
        )

    def test_drug_set_direction_is_read_from_kind(self):
        case = _case(
            _report("R1"),
            _report(
                "R2",
                drugs=[
                    Drug(substance="warfarin", sources=["s"], role=DrugRole.SUSPECTED)
                ],
            ),
            _features(
                inconsistencies=[
                    Inconsistency(
                        feature="drug_set",
                        severity=InconsistencySeverity.MODERATE,
                        terms=["warfarin"],
                        kind="missing_in_r1",
                    )
                ],
                inconsistency_counts=InconsistencyCounts(moderate=1),
            ),
        )
        (finding,) = case.retained_discrepancies
        assert finding.text == (
            "warfarin — key drug on report B absent from report A."
        )

    def test_ae_finding_names_the_hlt_level(self):
        """The rule-based assessor matches at HLT, so the sentence says so."""
        case = _case(
            _report("R1"),
            _report(
                "R2",
                aes=[AdverseEvent(pt_name="decreased appetite", sources=["s"])],
            ),
            _features(
                inconsistencies=[
                    Inconsistency(
                        feature="ae_clinical_picture:unmatched_burden:r2_to_r1",
                        severity=InconsistencySeverity.MINOR,
                        terms=["decreased appetite"],
                        source="rule_ae_clinical_picture",
                    )
                ],
                inconsistency_counts=InconsistencyCounts(minor=1),
            ),
        )
        (finding,) = case.retained_discrepancies
        assert finding.feature_label == "Adverse events"
        assert finding.text == (
            "decreased appetite listed on report B without a matching HLT on "
            "report A."
        )

    def test_the_feature_is_its_own_column_not_a_prefix(self):
        case = _case(
            _report("R1", age=54),
            _report("R2", age=61),
            _features(
                inconsistencies=[
                    Inconsistency(
                        feature="patient_age",
                        severity=InconsistencySeverity.STRONG,
                        difference=7.0,
                        units="years",
                    )
                ],
                inconsistency_counts=InconsistencyCounts(strong=1),
            ),
        )
        (finding,) = case.retained_discrepancies
        assert finding.feature_label == "Age"
        assert not finding.text.startswith("Age:")


class TestMatchingFeatureLabels:
    """A contribution names the terms that earned it."""

    def test_drug_set_contribution_lists_the_drugs(self):
        case = _case(
            _report("R1", drugs=[Drug(substance="aspirin", sources=["s"])]),
            _report("R2", drugs=[Drug(substance="aspirin", sources=["s"])]),
            _features(
                matching_drugs=["aspirin", "warfarin"],
                anchor=AnchorPopulation(
                    N_drugs=90,
                    N_AEs=3,
                    N_drugs_AEs=3,
                    type=AnchorType.AES,
                    N_anchor=3,
                ),
                probability=ProbabilityEstimate(
                    p_joint=1e-4,
                    p_match=1e-4,
                    feature_contributions=[
                        FeatureContribution(
                            feature="drug_set", p_f=0.001, method="leave_pair_out"
                        )
                    ],
                ),
            ),
        )
        (row,) = case.matching_features
        assert row.label.text == "aspirin; warfarin"

    def test_ae_set_contribution_lists_the_terms(self):
        case = _case(
            _report("R1"),
            _report("R2"),
            _features(
                matching_AEs=["cleft lip", "cleft palate"],
                anchor=AnchorPopulation(
                    N_drugs=4,
                    N_AEs=90,
                    N_drugs_AEs=4,
                    type=AnchorType.DRUGS,
                    N_anchor=4,
                ),
                probability=ProbabilityEstimate(
                    p_joint=1e-4,
                    p_match=1e-4,
                    feature_contributions=[
                        FeatureContribution(
                            feature="ae_set", p_f=0.001, method="leave_pair_out"
                        )
                    ],
                ),
            ),
        )
        (row,) = case.matching_features
        assert row.label.text == "cleft lip; cleft palate"


class TestFindingsUseReportedSpelling:
    """A finding names the drug as its report wrote it, not as a lookup key."""

    def test_reported_capitalisation_is_used(self):
        case = _case(
            _report(
                "R1",
                drugs=[
                    Drug(
                        substance="Vitamins NOS",
                        sources=["s"],
                        role=DrugRole.SUSPECTED,
                    )
                ],
            ),
            _report("R2"),
            _features(
                inconsistencies=[
                    Inconsistency(
                        feature="drug_set",
                        severity=InconsistencySeverity.MODERATE,
                        terms=["vitamins nos"],
                        kind="missing_in_r2",
                    )
                ],
                inconsistency_counts=InconsistencyCounts(moderate=1),
            ),
        )
        (finding,) = case.retained_discrepancies
        assert finding.text.startswith("Vitamins NOS — key drug")

    def test_a_term_absent_from_the_tables_falls_back_to_the_stored_key(self):
        """A finding is still reported when no row carries the substance."""
        case = _case(
            _report("R1"),
            _report("R2"),
            _features(
                inconsistencies=[
                    Inconsistency(
                        feature="drug_set",
                        severity=InconsistencySeverity.MODERATE,
                        terms=["ghost drug"],
                        kind="missing_in_r2",
                    )
                ],
                inconsistency_counts=InconsistencyCounts(moderate=1),
            ),
        )
        (finding,) = case.retained_discrepancies
        assert finding.text.startswith("ghost drug — key drug")


class TestAdverseEventOrderingBand:
    """One-sided groups mirror, so unflagged terms meet in the middle."""

    def _case_with_flags(self):
        r1 = _report(
            "R1",
            aes=[
                AdverseEvent(pt_name="shared", sources=["s"]),
                AdverseEvent(pt_name="a_neutral", sources=["s"]),
                AdverseEvent(pt_name="a_flagged", sources=["s"]),
            ],
        )
        r2 = _report(
            "R2",
            aes=[
                AdverseEvent(pt_name="shared", sources=["s"]),
                AdverseEvent(pt_name="b_neutral", sources=["s"]),
                AdverseEvent(pt_name="b_flagged", sources=["s"]),
            ],
        )
        return _case(
            r1,
            r2,
            _features(
                matching_AEs=["shared"],
                inconsistencies=[
                    Inconsistency(
                        feature="ae_clinical_picture:unmatched_burden:r1_to_r2",
                        severity=InconsistencySeverity.MINOR,
                        terms=["a_flagged"],
                        source="rule_ae_clinical_picture",
                    ),
                    Inconsistency(
                        feature="ae_clinical_picture:unmatched_burden:r2_to_r1",
                        severity=InconsistencySeverity.MINOR,
                        terms=["b_flagged"],
                        source="rule_ae_clinical_picture",
                    ),
                ],
                inconsistency_counts=InconsistencyCounts(minor=2),
            ),
        )

    def test_flagged_terms_sit_at_the_outer_edges(self):
        case = self._case_with_flags()
        assert [r.term.text for r in case.adverse_events] == [
            "shared",
            "a_flagged",
            "a_neutral",
            "b_neutral",
            "b_flagged",
        ]

    def test_the_unflagged_terms_are_contiguous(self):
        """The point of the mirrored order: one band, not two."""
        rows = self._case_with_flags().adverse_events
        neutral_positions = [
            i
            for i, r in enumerate(rows)
            if r.presence_rank and r.row_role is not EvidenceRole.INCONSISTENT
        ]
        assert neutral_positions == list(
            range(neutral_positions[0], neutral_positions[-1] + 1)
        )

    def test_shared_terms_still_come_first(self):
        rows = self._case_with_flags().adverse_events
        assert rows[0].presence.label == "Both"

    def test_ordering_is_stable_without_any_flags(self):
        """With nothing flagged the groups stay alphabetical."""
        r1 = _report(
            "R1",
            aes=[
                AdverseEvent(pt_name="zzz", sources=["s"]),
                AdverseEvent(pt_name="aaa", sources=["s"]),
            ],
        )
        case = _case(r1, _report("R2"), _features())
        assert [r.term.text for r in case.adverse_events] == ["aaa", "zzz"]


class TestMatchingFeatureRowsNameTheMatchedValue:
    """A row says *what* matched, not only which kind of thing matched."""

    @staticmethod
    def _demographic_features(*features: str):
        return _features(
            probability=ProbabilityEstimate(
                p_joint=1e-3,
                p_match=1e-3,
                feature_contributions=[
                    FeatureContribution(
                        feature=feature,
                        p_f=0.01,
                        method="domain_size",
                        domain_size=100.0,
                    )
                    for feature in features
                ],
            ),
        )

    def _labels(self, r1, r2, *features: str):
        case = _case(r1, r2, self._demographic_features(*features))
        return [row.label.text for row in case.matching_features]

    def test_an_agreed_demographic_names_its_value(self):
        labels = self._labels(
            _report("R1", sex="male"), _report("R2", sex="male"), "patient_sex"
        )
        assert labels == ["Sex (male)"]

    def test_the_value_is_quoted_as_the_table_shows_it(self):
        """Units included, so the row and the table above it read the same."""
        labels = self._labels(
            _report("R1", age=54), _report("R2", age=54), "patient_age"
        )
        assert labels == ["Age (54 years)"]

    def test_a_tolerance_match_shows_both_values(self):
        """Neither value is "the" matched one, so neither is claimed to be."""
        labels = self._labels(
            _report("R1", age=54), _report("R2", age=55), "patient_age"
        )
        assert labels == ["Age (54 years / 55 years)"]

    def test_a_feature_with_no_recorded_value_keeps_its_plain_label(self):
        """Height has no characteristics row in these fixtures."""
        labels = self._labels(
            _report("R1"), _report("R2"), "patient_height"
        )
        assert labels == ["Height"]

    def test_the_drug_and_ae_sets_still_name_their_members(self):
        features = _features(
            matching_drugs=["pregabalin", "topiramate"],
            matching_AEs=["somnolence"],
            probability=ProbabilityEstimate(
                p_joint=1e-6,
                p_match=1e-6,
                feature_contributions=[
                    FeatureContribution(
                        feature="drug_set", p_f=1e-4, method="empirical"
                    ),
                    FeatureContribution(
                        feature="ae_set", p_f=1e-2, method="empirical"
                    ),
                ],
            ),
        )
        case = _case(_report("R1"), _report("R2"), features)
        labels = [r.label.text for r in case.matching_features]
        assert "pregabalin; topiramate" in labels
        assert "somnolence" in labels


class TestFindingsCarryTheirWeight:
    """What each finding added to the penalty, from the run's own weights."""

    @staticmethod
    def _penalty(*terms, **kw):
        return InconsistencyPenalty(
            w_tot=kw.pop("w_tot", 1.0),
            threshold=kw.pop("threshold", 4.0),
            terms=list(terms),
        )

    def _case_with(self, penalty, *, suppressed=False):
        return _case(
            _report("R1", age=54),
            _report("R2", age=61),
            _features(
                inconsistencies=[
                    Inconsistency(
                        feature="patient_age",
                        severity=InconsistencySeverity.MODERATE,
                        difference=7.0,
                        units="years",
                        suppressed=suppressed,
                        suppressed_by="llm_ae_clinical_picture" if suppressed else "",
                    )
                ],
                inconsistency_counts=InconsistencyCounts(moderate=1),
            ),
            classification=_classification(penalty=penalty) if penalty else None,
        )

    def test_a_retained_finding_carries_its_severity_weight(self):
        case = self._case_with(
            self._penalty(
                PenaltyTerm(
                    severity=InconsistencySeverity.MODERATE, count=1, weight=1.0
                )
            )
        )
        row = case.retained_discrepancies[0]
        assert row.weight == 1.0
        assert row.weight_text == "+1.0"

    def test_the_run_s_own_weight_is_used_not_the_default(self):
        """A run with custom weights is described with its numbers."""
        case = self._case_with(
            self._penalty(
                PenaltyTerm(
                    severity=InconsistencySeverity.MODERATE, count=1, weight=2.5
                )
            )
        )
        assert case.retained_discrepancies[0].weight_text == "+2.5"

    def test_a_suppressed_finding_carries_zero(self):
        case = self._case_with(
            self._penalty(
                PenaltyTerm(
                    severity=InconsistencySeverity.MODERATE, count=1, weight=1.0
                )
            ),
            suppressed=True,
        )
        row = case.suppressed_discrepancies[0]
        assert row.weight == 0.0
        assert row.weight_text == "+0.0"

    def test_no_weight_is_shown_when_the_penalty_does_not_carry_it(self):
        case = self._case_with(self._penalty())
        row = case.retained_discrepancies[0]
        assert row.weight is None
        assert row.weight_text == ""

    def test_no_weight_is_shown_without_a_classification(self):
        case = self._case_with(None)
        row = case.retained_discrepancies[0]
        assert row.weight is None
        assert row.weight_text == ""
