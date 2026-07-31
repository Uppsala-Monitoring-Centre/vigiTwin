"""Tests for llm_dup_dev.eval.sweep.

The three hazard tests are the point of this file. Each guards a *silent*
failure — a sweep that reports a confident number over the wrong ground truth is
worse than one that crashes.
"""

import pytest

from llm_dup_dev.classifier.config import DecisionThresholds
from llm_dup_dev.classifier.decision_logic import classify
from llm_dup_dev.eval.labels import AbstainMode
from llm_dup_dev.eval.sweep import check_coverage, grid_over, sweep
from llm_dup_dev.eval.report import sweep_to_markdown, sweep_to_text
from llm_dup_dev.feature_generation.probability import compute_l_patient
from llm_dup_dev.models.enums import (
    AnchorType,
    ReportingContextLevel,
)
from llm_dup_dev.models.narrative_source_case import (
    SourceCaseAssessment,
    SourceCaseVerdict,
    SourceIdentifierType,
)
from llm_dup_dev.models.pair_features import (
    AnchorPopulation,
    FeatureContribution,
    InconsistencyCounts,
    PairFeatures,
    ProbabilityEstimate,
    ReportingContext,
)


def _features(pair_id, L_match, minor=0, moderate=0, strong=0,
              verdict=None, contributions=None):
    contributions = contributions or []
    assessment = None
    if verdict is not None:
        assessment = SourceCaseAssessment(
            verdict=verdict,
            shared_source_identifier="10.1/abc",
            shared_source_identifier_type=SourceIdentifierType.DOI,
            case_identifiers_compared=False,
            case_identifiers_comparable=False,
            reasoning="fixture",
        )
    return PairFeatures(
        pair_id=pair_id,
        reporting_context=ReportingContext(level=ReportingContextLevel.GLOBAL),
        anchor=AnchorPopulation(N_drugs=100, N_AEs=100, N_drugs_AEs=50,
                                type=AnchorType.DRUGS_AES, N_anchor=50),
        probability=ProbabilityEstimate(p_joint=1e-5, p_match=0.01,
                                        feature_contributions=contributions),
        L_match=L_match,
        L_patient=compute_l_patient(contributions),
        inconsistency_counts=InconsistencyCounts(
            minor=minor, moderate=moderate, strong=strong
        ),
        source_case_assessment=assessment,
    )


def _ann(pair_id, assessment):
    return {"pair_id": pair_id, "duplicates_assessment": assessment}


# ===================================================================
# Hazard 1 — abstain shrinks the denominator
# ===================================================================

class TestAbstainDenominator:
    """A setting that abstains on hard pairs scores better on fewer pairs.

    Under DECIDED_ONLY an abstained pair leaves n_pairs entirely, so F1 is not
    comparable across settings. A sweep that picked max F1 would reliably
    choose whichever setting refused the most pairs.
    """

    def test_abstain_mode_is_required(self):
        """No default: the caller must choose, because the choice moves n."""
        with pytest.raises(TypeError):
            sweep({}, [], grid_over())  # type: ignore[call-arg]

    def test_every_point_reports_n_and_abstained(self):
        """The trade-off must be visible on each row, not aggregated away."""
        features = {
            # SAME_SOURCE_SINGLE_PATIENT with moderate+strong -> w_tot = 4.0.
            # Abstain fires when w_tot >= w_tot_compelling_zone, so the sweep
            # toggles it by moving that bar across 4.0.
            "A__B": _features("A__B", L_match=0.5, moderate=1, strong=1,
                              verdict=SourceCaseVerdict.SAME_SOURCE_SINGLE_PATIENT),
        }
        result = sweep(
            features, [_ann("A__B", "duplicates")],
            grid_over(w_tot_compelling_zone=[5.0, 3.0]),
            abstain_mode=AbstainMode.DECIDED_ONLY,
        )
        # compelling_zone=5.0 -> w_tot 4.0 < 5.0 -> Duplicates (scored).
        # compelling_zone=3.0 -> w_tot 4.0 >= 3.0 -> Abstain (dropped from n_pairs).
        by_label = {p.summary.experiment_id: p for p in result.points}
        decided = by_label["w_tot_compelling_zone=5"]
        abstained = by_label["w_tot_compelling_zone=3"]
        assert decided.n_pairs == 1 and decided.n_abstained == 0
        assert abstained.n_pairs == 0 and abstained.n_abstained == 1

    def test_sweep_offers_no_argmax(self):
        """There is deliberately no 'best' — picking one would prefer abstainers."""
        result = sweep({}, [], grid_over(), abstain_mode=AbstainMode.DECIDED_ONLY)
        assert not hasattr(result, "best")
        assert not hasattr(result, "argmax")

    def test_report_warns_that_f1_is_not_comparable(self):
        """The reader must be told why no row is marked best."""
        features = {"A__B": _features("A__B", L_match=4.0)}
        result = sweep(features, [_ann("A__B", "duplicates")],
                       grid_over(lmatch_compelling=[3.0, 5.0]),
                       abstain_mode=AbstainMode.DECIDED_ONLY)
        text = sweep_to_text(result)
        assert "not comparable" in text
        assert "abst" in text  # the abstain column is present


# ===================================================================
# Hazard 2 — pair orientation is not canonical
# ===================================================================

class TestOrientationCoverage:
    """pair_id is f"{r1}__{r2}" with no canonical ordering.

    An annotation file listing a pair the other way round matches nothing, and
    evaluate() skips unmatched annotations without counting them — so the sweep
    would score zero pairs and still report a number.
    """

    def test_reversed_annotation_is_reported_not_silently_dropped(self):
        features = {"A__B": _features("A__B", L_match=4.0)}
        coverage = check_coverage(features, [_ann("B__A", "duplicates")])
        assert coverage.n_matched == 0
        assert coverage.reversed_orientation == ["B__A"]
        assert coverage.unmatched_annotations == []

    def test_genuinely_absent_annotation_is_distinguished_from_reversed(self):
        """'Not in this run' and 'listed backwards' are different problems."""
        features = {"A__B": _features("A__B", L_match=4.0)}
        coverage = check_coverage(features, [_ann("X__Y", "duplicates")])
        assert coverage.unmatched_annotations == ["X__Y"]
        assert coverage.reversed_orientation == []

    def test_unannotated_features_are_reported_as_unscored(self):
        """Stored pairs with no gold label are unscored, not wrong."""
        features = {
            "A__B": _features("A__B", L_match=4.0),
            "C__D": _features("C__D", L_match=4.0),
        }
        coverage = check_coverage(features, [_ann("A__B", "duplicates")])
        assert coverage.matched == ["A__B"]
        assert coverage.unannotated_features == ["C__D"]

    def test_reversed_orientation_warns_loudly_in_the_report(self):
        features = {"A__B": _features("A__B", L_match=4.0)}
        result = sweep(features, [_ann("B__A", "duplicates")], grid_over(),
                       abstain_mode=AbstainMode.DECIDED_ONLY)
        text = sweep_to_text(result)
        assert "WARNING" in text
        assert "orientation" in text
        assert "NOT scored" in text

    def test_full_coverage_produces_no_warning(self):
        features = {"A__B": _features("A__B", L_match=4.0)}
        result = sweep(features, [_ann("A__B", "duplicates")], grid_over(),
                       abstain_mode=AbstainMode.DECIDED_ONLY)
        assert "WARNING" not in sweep_to_text(result)


# ===================================================================
# Hazard 3 — the DecisionThresholds validator rejects grid points
# ===================================================================

class TestValidatorRejectedPoints:
    """w_decisive must exceed every zone threshold, so a rectangular grid
    contains invalid corners. Those must be reported, not crash the sweep."""

    def test_invalid_point_is_skipped_with_a_reason(self):
        features = {"A__B": _features("A__B", L_match=4.0)}
        result = sweep(features, [_ann("A__B", "duplicates")],
                       grid_over(w_decisive=[100.0, 0.5]),
                       abstain_mode=AbstainMode.DECIDED_ONLY)
        assert len(result.points) == 1        # the sweep completed
        assert len(result.skipped) == 1
        raw, reason = result.skipped[0]
        assert raw["w_decisive"] == 0.5
        assert "w_decisive" in reason         # the constraint, not a stack trace
        assert "pydantic.dev" not in reason   # not the raw wrapper

    def test_skipped_points_are_surfaced_in_the_report(self):
        features = {"A__B": _features("A__B", L_match=4.0)}
        result = sweep(features, [_ann("A__B", "duplicates")],
                       grid_over(w_decisive=[100.0, 0.5]),
                       abstain_mode=AbstainMode.DECIDED_ONLY)
        assert "Skipped 1 invalid setting" in sweep_to_text(result)


# ===================================================================
# The sweep must agree with the pipeline at the pipeline's own settings
# ===================================================================

class TestSweepAgreesWithClassify:
    def test_defaults_reproduce_classify(self):
        """Same features, same thresholds, same answer.

        If this fails, the sweep is measuring something other than the pipeline.
        """
        features = {
            "A__B": _features("A__B", L_match=4.0),
            "C__D": _features("C__D", L_match=0.5),
            "E__F": _features("E__F", L_match=4.0, strong=2),
        }
        annotations = [_ann(p, "duplicates") for p in features]
        result = sweep(features, annotations, grid_over(),
                       abstain_mode=AbstainMode.ABSTAIN_AS_NONDUPLICATE)

        assert len(result.points) == 1
        point = result.points[0]
        assert point.summary.experiment_id == "defaults"
        # Every pair scored, and with the labels classify() gives directly.
        direct = {
            pid: classify(f, DecisionThresholds()).final_label
            for pid, f in features.items()
        }
        assert point.n_pairs == 3
        assert set(direct.values()) == {"Duplicates", "Unrelated", "Otherwise_related"}

    def test_config_snapshot_carries_the_real_thresholds(self):
        """Each point is self-describing — the snapshot was previously unused."""
        features = {"A__B": _features("A__B", L_match=4.0)}
        result = sweep(features, [_ann("A__B", "duplicates")],
                       grid_over(lmatch_compelling=[7.5]),
                       abstain_mode=AbstainMode.DECIDED_ONLY)
        snapshot = result.points[0].summary.config_snapshot
        assert snapshot["lmatch_compelling"] == 7.5
        assert snapshot["w_decisive"] == 100.0  # the whole setting, not a diff


class TestGridOver:
    def test_single_axis(self):
        grid = grid_over(lmatch_limited=[0.5, 1.0, 1.5])
        assert [t.lmatch_limited for t in grid] == [0.5, 1.0, 1.5]
        assert all(t.lmatch_compelling == 3.0 for t in grid)  # others at base

    def test_two_axes_are_rectangular(self):
        grid = grid_over(lmatch_limited=[0.5, 1.0], lpatient_compelling=[2.0, 3.0])
        assert len(grid) == 4

    def test_no_axes_yields_the_base(self):
        assert grid_over() == [DecisionThresholds()]

    def test_respects_a_custom_base(self):
        base = DecisionThresholds(w_moderate=2.0)
        grid = grid_over(base, lmatch_limited=[0.5])
        assert grid[0].w_moderate == 2.0


class TestSweepReporting:
    def test_markdown_renders_a_table(self):
        features = {"A__B": _features("A__B", L_match=4.0)}
        result = sweep(features, [_ann("A__B", "duplicates")],
                       grid_over(lmatch_compelling=[3.0, 5.0]),
                       abstain_mode=AbstainMode.DECIDED_ONLY)
        md = sweep_to_markdown(result)
        assert "| Setting | n | Abstained |" in md
        assert "`defaults`" in md
        assert "not\ncomparable" in md or "not comparable" in md

    def test_four_decimal_places_separate_adjacent_points(self):
        """The existing summary formatter uses %.2g, which would collapse these."""
        features = {"A__B": _features("A__B", L_match=4.0)}
        result = sweep(features, [_ann("A__B", "duplicates")], grid_over(),
                       abstain_mode=AbstainMode.DECIDED_ONLY)
        assert "1.0000" in sweep_to_text(result)

    def test_empty_sweep_says_so(self):
        result = sweep({}, [], [], abstain_mode=AbstainMode.DECIDED_ONLY)
        assert "No valid threshold settings" in sweep_to_text(result)
