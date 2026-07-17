"""Tests for llm_dup_dev.pipeline.export."""

import json

from llm_dup_dev.classifier.decision_logic import classify
from llm_dup_dev.models.enums import (
    AnchorType,
    NarrativeConsistency,
    ReportingContextLevel,
)
from llm_dup_dev.models.pair_features import (
    AnchorPopulation,
    FeatureContribution,
    InconsistencyCounts,
    PairFeatures,
    ProbabilityEstimate,
    ReportingContext,
)
from llm_dup_dev.pipeline.export import (
    read_classifications,
    read_pair_features,
    write_classifications,
    write_pair_features,
)
from llm_dup_dev.pipeline.runner import PairResult


def _features(pair_id="R001__R002", moderate=1):
    contributions = [
        FeatureContribution(feature="patient_sex", p_f=0.5, method="domain_size"),
        FeatureContribution(feature="drug_set", p_f=0.01, method="leave_pair_out"),
    ]
    return PairFeatures(
        pair_id=pair_id,
        reporting_context=ReportingContext(
            level=ReportingContextLevel.COUNTRY, country="US", year=2020
        ),
        matching_drugs=["pregabalin"],
        matching_AEs=["somnolence"],
        anchor=AnchorPopulation(N_drugs=100, N_AEs=100, N_drugs_AEs=50,
                                type=AnchorType.DRUGS_AES, N_anchor=50),
        probability=ProbabilityEstimate(p_joint=1.5e-5, p_match=0.075,
                                        feature_contributions=contributions),
        L_match=4.0,
        L_patient=0.30103,
        inconsistency_counts=InconsistencyCounts(moderate=moderate),
        narrative_consistency=NarrativeConsistency.NEUTRAL,
    )


def _result(pair_id="R001__R002"):
    f = _features(pair_id)
    return PairResult(pair_id=pair_id, features=f, classification=classify(f))


# ===================================================================
# PairFeatures
# ===================================================================

class TestPairFeaturesRoundTrip:
    def test_write_then_read_is_lossless(self, tmp_path):
        """The whole point: a stored PairFeatures can be re-decided later."""
        original = _features()
        path = tmp_path / "features.json"
        write_pair_features([_result()], path)

        restored = read_pair_features(path)
        assert set(restored) == {"R001__R002"}
        assert restored["R001__R002"] == original

    def test_keyed_by_pair_id(self, tmp_path):
        """The format review_html/cli.py already reads."""
        path = tmp_path / "features.json"
        write_pair_features([_result("A__B"), _result("C__D")], path)
        raw = json.loads(path.read_text())
        assert set(raw) == {"A__B", "C__D"}
        assert raw["A__B"]["pair_id"] == "A__B"

    def test_creates_parent_directories(self, tmp_path):
        path = tmp_path / "nested" / "deeper" / "features.json"
        write_pair_features([_result()], path)
        assert path.exists()

    def test_accepts_a_string_path(self, tmp_path):
        path = tmp_path / "features.json"
        write_pair_features([_result()], str(path))
        assert read_pair_features(str(path))

    def test_empty_results_write_an_empty_mapping(self, tmp_path):
        path = tmp_path / "features.json"
        write_pair_features([], path)
        assert read_pair_features(path) == {}


# ===================================================================
# ClassificationResult
# ===================================================================

class TestClassificationRoundTrip:
    def test_write_then_read_is_lossless(self, tmp_path):
        result = _result()
        path = tmp_path / "classifications.json"
        write_classifications([result], path)
        restored = read_classifications(path)
        assert restored["R001__R002"] == result.classification

    def test_penalty_survives(self, tmp_path):
        """The nested InconsistencyPenalty must round-trip with its terms."""
        result = _result()
        assert result.classification.penalty is not None
        path = tmp_path / "classifications.json"
        write_classifications([result], path)

        penalty = read_classifications(path)["R001__R002"].penalty
        assert penalty.w_tot == result.classification.penalty.w_tot
        assert penalty.threshold == result.classification.penalty.threshold
        assert [t.count for t in penalty.terms] == [1]
        assert [t.weight for t in penalty.terms] == [1.0]

    def test_derived_properties_are_recovered_not_stored(self, tmp_path):
        """final_label and penalty.satisfied are properties, not fields.

        They are absent from the JSON by design and recomputed on read; a
        round-trip must still expose them.
        """
        result = _result()
        path = tmp_path / "classifications.json"
        write_classifications([result], path)

        raw = json.loads(path.read_text())["R001__R002"]
        assert "final_label" not in raw
        assert "satisfied" not in raw["penalty"]

        restored = read_classifications(path)["R001__R002"]
        assert restored.final_label == result.classification.final_label
        assert restored.penalty.satisfied is result.classification.penalty.satisfied


# ===================================================================
# Compatibility with the review CLI — the reason the writer exists
# ===================================================================

class TestReviewCliCompatibility:
    def test_written_files_load_through_the_cli_readers(self, tmp_path):
        """review_html/cli.py accepted --features long before anything wrote one.

        The CLI now shares these readers, so this asserts the round trip the
        asymmetry always implied.
        """
        from llm_dup_dev.review_html import cli

        results = [_result("A__B")]
        fpath = tmp_path / "features.json"
        cpath = tmp_path / "classifications.json"
        write_pair_features(results, fpath)
        write_classifications(results, cpath)

        assert cli.read_pair_features(fpath)["A__B"].L_match == 4.0
        assert cli.read_classifications(cpath)["A__B"].final_label == "Duplicates"


# ===================================================================
# The round trip preserves the decision — the calibration premise
# ===================================================================

class TestStoredFeaturesReDecideIdentically:
    def test_classify_agrees_before_and_after_a_round_trip(self, tmp_path):
        """Persisting must not perturb the decision.

        Offline calibration rests on this: a stored PairFeatures must yield
        exactly the classification the live one did.
        """
        results = [_result("A__B"), _result("C__D")]
        path = tmp_path / "features.json"
        write_pair_features(results, path)

        restored = read_pair_features(path)
        for result in results:
            assert (
                classify(restored[result.pair_id]).final_label
                == result.classification.final_label
            )
            assert (
                classify(restored[result.pair_id]).explanation
                == result.classification.explanation
            )
