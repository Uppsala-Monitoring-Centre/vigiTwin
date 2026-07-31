"""Tests for llm_dup_dev.pipeline.export."""

import json

import pytest

from llm_dup_dev.classifier.decision_logic import classify
from llm_dup_dev.models.enums import (
    AnchorType,
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
from llm_dup_dev.models.enums import AEClinicalPictureStatus
from llm_dup_dev.models.narrative_demographics import (
    NarrativeAgeValue,
    NarrativeDemographics,
)
from llm_dup_dev.models.narrative_source_case import (
    SourceCaseAssessment,
    SourceCaseVerdict,
)
from llm_dup_dev.models.pair_features import AEClinicalPictureResult
from llm_dup_dev.pipeline.export import (
    read_classifications,
    read_pair_features,
    read_pair_results,
    write_classifications,
    write_pair_features,
    write_pair_results,
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
    )


def _result(pair_id="R001__R002"):
    f = _features(pair_id)
    return PairResult(pair_id=pair_id, features=f, classification=classify(f))


def _full_result(pair_id="R001__R002"):
    """A PairResult with every optional field populated."""
    f = _features(pair_id)
    return PairResult(
        pair_id=pair_id,
        features=f,
        classification=classify(f),
        ae_clinical_picture=AEClinicalPictureResult(
            status=AEClinicalPictureStatus.ASSESSED,
            method="rule_based",
            reasoning="No decisive AE mismatch.",
        ),
        source_case_assessment=SourceCaseAssessment(
            verdict=SourceCaseVerdict.NO_DECISIVE_SIGNAL,
            reasoning="No shared source identifier.",
        ),
        narrative_demographics_1=NarrativeDemographics(
            ages=[NarrativeAgeValue(value=54, evidence="54-year-old")],
            notes="report 1",
        ),
        narrative_demographics_2=NarrativeDemographics(
            ages=[NarrativeAgeValue(value=55, evidence="55yo")],
            notes="report 2",
        ),
    )


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


# ===================================================================
# PairResult (whole pair_results.json artifact)
# ===================================================================

class TestPairResultsRoundTrip:
    def test_full_result_is_lossless(self, tmp_path):
        """Every field, including all optionals, survives a round trip."""
        original = _full_result()
        path = tmp_path / "pair_results.json"
        write_pair_results([original], path)

        restored = read_pair_results(path)
        assert set(restored) == {"R001__R002"}
        assert restored["R001__R002"] == original

    def test_minimal_result_keeps_optionals_none(self, tmp_path):
        """Absent optionals write as JSON null and read back as None."""
        path = tmp_path / "pair_results.json"
        write_pair_results([_result()], path)

        raw = json.loads(path.read_text())["R001__R002"]
        for key in (
            "ae_clinical_picture",
            "source_case_assessment",
            "narrative_demographics_1",
            "narrative_demographics_2",
        ):
            assert raw[key] is None

        restored = read_pair_results(path)["R001__R002"]
        assert restored.ae_clinical_picture is None
        assert restored.source_case_assessment is None
        assert restored.narrative_demographics_1 is None
        assert restored.narrative_demographics_2 is None

    def test_keyed_by_pair_id_with_documented_fields(self, tmp_path):
        path = tmp_path / "pair_results.json"
        write_pair_results([_full_result("A__B")], path)
        raw = json.loads(path.read_text())
        assert set(raw) == {"A__B"}
        assert set(raw["A__B"]) == {
            "final_label",
            "features",
            "classification",
            "ae_clinical_picture",
            "source_case_assessment",
            "narrative_demographics_1",
            "narrative_demographics_2",
        }

    def test_final_label_mismatch_raises(self, tmp_path):
        """final_label is validated on read, not silently recomputed.

        A tampered final_label disagrees with the classification-derived label,
        so the artifact is rejected as internally inconsistent.
        """
        result = _full_result()
        path = tmp_path / "pair_results.json"
        write_pair_results([result], path)

        raw = json.loads(path.read_text())
        assert raw["R001__R002"]["final_label"] == result.final_label
        raw["R001__R002"]["final_label"] = "Bogus"
        path.write_text(json.dumps(raw))

        with pytest.raises(
            ValueError,
            match=r"Invalid pair-results artifact .*pair_id=R001__R002",
        ):
            read_pair_results(path)

    def test_top_level_must_be_object(self, tmp_path):
        path = tmp_path / "pair_results.json"
        path.write_text(json.dumps([{"pair_id": "x"}]))
        with pytest.raises(ValueError, match="top-level JSON must be an object"):
            read_pair_results(path)

    def test_record_must_be_object(self, tmp_path):
        path = tmp_path / "pair_results.json"
        path.write_text(json.dumps({"R001__R002": 5}))
        with pytest.raises(ValueError, match="record must be an object"):
            read_pair_results(path)

    def test_source_case_disagreement_raises(self, tmp_path):
        """Top-level and feature-level source/case, both present, must agree."""
        sca_top = SourceCaseAssessment(
            verdict=SourceCaseVerdict.SAME_SOURCE_SAME_CASE, reasoning="top"
        )
        sca_feat = SourceCaseAssessment(
            verdict=SourceCaseVerdict.NO_DECISIVE_SIGNAL, reasoning="feat"
        )
        features = _features().model_copy(
            update={"source_case_assessment": sca_feat}
        )
        result = PairResult(
            pair_id="R001__R002",
            features=features,
            classification=classify(features),
            source_case_assessment=sca_top,
        )
        path = tmp_path / "pair_results.json"
        write_pair_results([result], path)

        with pytest.raises(
            ValueError, match="source_case_assessment disagrees"
        ):
            read_pair_results(path)

    def test_feature_level_source_case_used_when_top_absent(self, tmp_path):
        """Top-level null but feature-level present → feature-level is used."""
        sca_feat = SourceCaseAssessment(
            verdict=SourceCaseVerdict.NO_DECISIVE_SIGNAL, reasoning="feat"
        )
        features = _features().model_copy(
            update={"source_case_assessment": sca_feat}
        )
        # PairResult.source_case_assessment defaults to None -> top-level null.
        result = PairResult(
            pair_id="R001__R002",
            features=features,
            classification=classify(features),
        )
        path = tmp_path / "pair_results.json"
        write_pair_results([result], path)
        assert json.loads(path.read_text())["R001__R002"][
            "source_case_assessment"
        ] is None

        restored = read_pair_results(path)["R001__R002"]
        assert restored.source_case_assessment == sca_feat

    def test_empty_results_write_an_empty_mapping(self, tmp_path):
        path = tmp_path / "pair_results.json"
        write_pair_results([], path)
        assert read_pair_results(path) == {}

    def test_reads_secure_shaped_payload(self, tmp_path):
        """A hand-built payload mirroring the secure runner loads cleanly.

        Guards compatibility with the secure ``pair_results.json``: an object
        keyed by pair_id whose values carry the eight fields, optionals null.
        """
        result = _full_result("R001__R002")
        payload = {
            "R001__R002": {
                "final_label": result.final_label,
                "features": result.features.model_dump(mode="json"),
                "classification": result.classification.model_dump(mode="json"),
                "ae_clinical_picture": result.ae_clinical_picture.model_dump(
                    mode="json"
                ),
                "source_case_assessment": (
                    result.source_case_assessment.model_dump(mode="json")
                ),
                "narrative_demographics_1": None,
                "narrative_demographics_2": None,
            }
        }
        path = tmp_path / "pair_results.json"
        path.write_text(json.dumps(payload, indent=2))

        restored = read_pair_results(path)["R001__R002"]
        assert restored.pair_id == "R001__R002"
        assert restored.features == result.features
        assert restored.narrative_demographics_1 is None
