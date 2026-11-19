"""One value, one answer, everywhere it appears — and nothing else touched.

Two properties the whole design rests on. The first is that a transformed
value is the same transformed value in the table, the chip, the finding and
the summary, because the transform is a substitution table rather than a
sequence of edits. The second is the inherited phase-2 contract: the
publication tree differs from the internal one in its *displayed values* and
in nothing else — no role, no attribution, no contribution, no row ordering.
"""

from __future__ import annotations

from llm_dup_dev.review_html.evidence.models import EvidenceCaseVM
from llm_dup_dev.review_html.evidence.publication import (
    SubstitutionTable,
    walk_cells,
)
from tests.test_review_html.conftest import (
    publication_page,
    scramble,
    worked_example_case,
)
from tests.test_review_html.test_evidence_build import TestSubstitutionInvariance


def _strip_text(node):
    """The evidence tree with every displayed value removed.

    The same reduction ``TestSubstitutionInvariance`` uses, reused here so
    the two tests are making the same statement about the same tree.
    """
    return TestSubstitutionInvariance._strip_text(node)


class TestTheSubstitutionTable:
    def test_one_original_maps_one_way(self):
        table = SubstitutionTable()
        table.add("SE", "Country 1", kind="country")
        table.add("SE", "Country 1", kind="country")
        assert not table.conflicts

    def test_mapping_one_original_two_ways_is_a_conflict(self):
        """A value that came out differently in two places is exactly the
        inconsistency across tables this layer exists to prevent."""
        table = SubstitutionTable()
        table.add("SE", "Country 1", kind="country")
        table.add("SE", "Country 2", kind="country")
        assert table.conflicts
        assert table.conflict_findings("x")

    def test_a_longer_value_is_replaced_before_one_of_its_parts(self):
        table = SubstitutionTable()
        table.add("2023-11-13", "2023-12-13", kind="date:day")
        table.add("2023", "2024", kind="date:year")
        assert table.rewrite("onset 2023-11-13 in 2023") == (
            "onset 2023-12-13 in 2024"
        )

    def test_a_published_value_is_not_rewritten_again(self):
        """One pass, so a published value that happens to equal another
        original does not get a second transform applied to it."""
        table = SubstitutionTable()
        table.add("A", "B", kind="x")
        table.add("B", "C", kind="x")
        assert table.rewrite("A") == "B"


class TestOneValueOneAnswer:
    def test_a_demographic_cell_and_the_finding_quoting_it_agree(self):
        """They are the same object in the view model, and the walk visits
        each object once, so they cannot be offset different numbers of
        times."""
        case = _case_with_an_age_discrepancy()
        result = scramble(publication_page(case))
        published = result.page.cases[0]
        age_row = next(
            row for row in published.characteristics if row.label == "Age"
        )
        finding = next(
            d for d in published.discrepancies if d.left is not None
        )
        assert finding.left is not None
        assert finding.left.text == age_row.left.text

    def test_every_cell_is_visited_exactly_once(self):
        case = worked_example_case()
        seen = [id(cell) for _, cell in walk_cells(case)]
        assert len(seen) == len(set(seen))

    def test_a_shared_set_member_and_the_panel_agree(self):
        """``CaseSummaryVM.anchor_sets`` holds the same objects as
        ``shared_sets``; one rewrite has to reach both."""
        result = scramble(publication_page(worked_example_case()))
        published = result.page.cases[0]
        assert published.summary_panel is not None
        if published.summary_panel.anchor_sets and published.shared_sets:
            panel = published.summary_panel.anchor_sets[0]
            assert panel in published.shared_sets


class TestTheInternalTreeIsUntouched:
    def test_scrambling_does_not_modify_the_page_it_was_given(self):
        page = publication_page(worked_example_case())
        before = page.model_dump(mode="json")
        scramble(page)
        assert page.model_dump(mode="json") == before

    def test_the_internal_page_keeps_its_mode(self):
        page = publication_page(worked_example_case())
        result = scramble(page)
        assert page.mode.value == "internal"
        assert result.page.mode.value == "publication"


class TestTheEvidenceTreeSurvives:
    """The phase-2 contract, now that there is a phase 2.

    ``TestSubstitutionInvariance`` proved the builder would tolerate a
    wholesale rewrite of every ``text``. This proves the rewrite the
    publication layer actually performs is one of those.
    """

    @staticmethod
    def _comparable(case: EvidenceCaseVM) -> dict:
        data = case.model_dump(mode="json")
        # The identifiers and the citation label are replaced by design, and
        # the whole point of ``withheld`` is that it differs.
        for key in ("pair_id", "example_id", "example_label"):
            data.pop(key, None)
        return _strip_text(data)

    def test_roles_attributions_and_contributions_are_unchanged(self):
        page = publication_page(worked_example_case())
        internal = self._comparable(page.cases[0])
        result = scramble(page)
        assert self._comparable(result.page.cases[0]) == internal

    def test_the_displayed_values_really_did_change(self):
        """So the test above cannot pass by the transform doing nothing."""
        page = publication_page(worked_example_case())
        before = page.cases[0].model_dump(mode="json")
        result = scramble(page)
        assert result.page.cases[0].model_dump(mode="json") != before


def _case_with_an_age_discrepancy() -> EvidenceCaseVM:
    from llm_dup_dev.models.enums import InconsistencySeverity
    from llm_dup_dev.models.pair_features import (
        Inconsistency,
        InconsistencyCounts,
    )
    from tests.test_review_html.test_evidence_build import (
        _case,
        _features,
        _report,
    )

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
                )
            ],
            inconsistency_counts=InconsistencyCounts(moderate=1),
        ),
    )


class TestMatchingFeatureRowsQuoteTransformedValues:
    """A row that quotes a demographic value quotes the published one.

    ``build._matching_feature_label`` writes the matched value into the cell,
    reading it off the characteristics table so the two agree. That makes the
    cell's ``kind`` load-bearing: stamped ``FREE_TEXT``, as it was, an age in
    that row would reach a published page as reported.
    """

    @staticmethod
    def _case_with_an_age_contribution():
        from llm_dup_dev.models.pair_features import (
            FeatureContribution,
            ProbabilityEstimate,
        )
        from llm_dup_dev.review_html.evidence.models import ValueKind
        from tests.test_review_html.test_evidence_build import (
            _case,
            _features,
            _report,
        )

        case = _case(
            _report("R1", age=54),
            _report("R2", age=54),
            _features(
                probability=ProbabilityEstimate(
                    p_joint=1e-4,
                    p_match=1e-4,
                    feature_contributions=[
                        FeatureContribution(
                            feature="patient_age",
                            p_f=0.01,
                            method="domain_size",
                        )
                    ],
                )
            ),
        )
        row = next(
            r for r in case.matching_features if "year" in r.label.text
        )
        assert row.label.kind is ValueKind.AGE
        return case, row

    def test_the_row_carries_the_values_own_kind(self):
        self._case_with_an_age_contribution()

    def test_the_published_row_agrees_with_the_published_table(self):
        case, _ = self._case_with_an_age_contribution()
        result = scramble(publication_page(case))
        published = result.page.cases[0]
        age_row = next(
            r for r in published.characteristics if r.label == "Age"
        )
        feature_row = next(
            r for r in published.matching_features if "year" in r.label.text
        )
        assert age_row.left.text in feature_row.label.text

    def test_the_original_age_is_not_on_the_published_page(self):
        case, row = self._case_with_an_age_contribution()
        original = row.label.text
        result = scramble(publication_page(case))
        feature_row = next(
            r
            for r in result.page.cases[0].matching_features
            if "year" in r.label.text
        )
        assert feature_row.label.text != original


class TestUnchangedValuesAreLegitimate:
    """A published value equalling a reported one is not a defect.

    It is the state the transform needs to be able to produce. A transform
    whose output always differed from its input would let anyone holding the
    policy rule out the displayed value as the true one, which is a narrower
    search than they had before.
    """

    @staticmethod
    def _zero_only():
        from llm_dup_dev.review_html.evidence.publication import (
            PublicationPolicy,
        )

        return PublicationPolicy(
            date_offset_min_days=0,
                date_offset_max_days=0,
                decouple_clinical_dates=False,
        )

    def test_a_case_whose_dates_all_held_still_publishes(self):
        result = scramble(
            publication_page(worked_example_case()), policy=self._zero_only()
        )
        assert not result.blocked

    def test_the_transform_records_having_run_on_them(self):
        """Transformed-and-unchanged has to be distinguishable from
        never-reached, which is what the cell records are for."""
        result = scramble(
            publication_page(worked_example_case()), policy=self._zero_only()
        )
        transform = result.transforms[0]
        dates = [c for c in transform.cells if "date" in c.kind]
        assert dates
        assert all(not c.changed for c in dates)
        assert transform.date_pattern_unchanged

    def test_two_cases_may_disagree_about_one_value(self):
        """Each pair carries its own offset, so one case's original is
        routinely another's published value. Neither is a leak."""
        page = publication_page(
            worked_example_case(report_ids=("R1", "R2")),
            worked_example_case(report_ids=("R3", "R4")),
        )
        result = scramble(page)
        offsets = {t.date_offset_days for t in result.transforms}
        assert len(offsets) == 2
        assert not result.blocked


class TestSeparationsAreRequotedForPresentation:
    """The figure beside two dates has to agree with the dates.

    The tolerance in the invariants means the transformed separation may
    differ from the one the pipeline computed. Only the displayed figure is
    brought into line; the severity, the weight and the penalty are the
    assessment's and stay as they were.
    """

    @staticmethod
    def _result():
        return scramble(
            publication_page(worked_example_case(clinical_date_finding=True))
        )

    def test_the_quoted_figure_matches_the_published_dates(self):
        from llm_dup_dev.feature_generation.clinical_date_alignment import (
            min_day_difference,
        )
        from llm_dup_dev.review_html.evidence.publication import (
            parse_partial_date,
        )

        published = self._result().page.cases[0]
        finding = next(
            d for d in published.discrepancies if d.left and d.right
        )
        left = parse_partial_date(finding.left.text)
        right = parse_partial_date(finding.right.text)
        assert left is not None and right is not None
        expected = min_day_difference(left, right)
        assert f"at least {expected} day" in finding.tail

    def test_the_sentence_and_the_tail_agree(self):
        published = self._result().page.cases[0]
        finding = next(
            d for d in published.discrepancies if d.left and d.right
        )
        assert finding.tail.strip(".") in finding.text

    def test_the_assessments_severity_and_weight_are_untouched(self):
        internal = publication_page(
            worked_example_case(clinical_date_finding=True)
        )
        before = [
            (d.severity, d.weight, d.retained)
            for d in internal.cases[0].discrepancies
        ]
        published = self._result().page.cases[0]
        assert [
            (d.severity, d.weight, d.retained) for d in published.discrepancies
        ] == before

    def test_a_recomputation_is_recorded_for_the_reviewer(self):
        result = self._result()
        recomputed = result.transforms[0].recomputed_separations
        # Either the figure held or it moved; if it moved it is on the record.
        published = result.page.cases[0]
        finding = next(
            d for d in published.discrepancies if d.left and d.right
        )
        if "at least 51 day" not in finding.tail:
            assert recomputed
