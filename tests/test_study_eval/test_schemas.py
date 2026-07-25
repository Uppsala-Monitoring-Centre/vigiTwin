"""Schema-drift and example round-trip tests for the study_eval models.

The committed schemas/study_*.schema.json must equal ``model.model_json_schema()``
and the examples/study_*_example.json must round-trip through the model.
Regenerate both with scripts/generate_study_eval_schemas.py after changing a
model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_dup_dev.study_eval.manifest import EvaluationRunManifest
from llm_dup_dev.study_eval.records import SamplingRecord
from llm_dup_dev.study_eval.sampling import SCLESample
from llm_dup_dev.study_eval.schemas import PredictionSet, ReferenceSet
from llm_dup_dev.study_eval.scle.agreement import AgreementReport
from llm_dup_dev.study_eval.scle.consensus import SCLEConsensusResult
from llm_dup_dev.study_eval.scle.form import ReviewerSubmission

REPO_ROOT = Path(__file__).parent.parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"
EXAMPLES_DIR = REPO_ROOT / "examples"

MODEL_MAP = {
    "study_reference_set": ReferenceSet,
    "study_prediction_set": PredictionSet,
    "study_sampling_record": SamplingRecord,
    "study_scle_sample": SCLESample,
    "study_reviewer_submission": ReviewerSubmission,
    "study_agreement_report": AgreementReport,
    "study_scle_consensus_result": SCLEConsensusResult,
    "study_evaluation_run_manifest": EvaluationRunManifest,
}


@pytest.mark.parametrize("name,model", list(MODEL_MAP.items()))
def test_schema_matches_model(name, model):
    path = SCHEMAS_DIR / f"{name}.schema.json"
    assert path.exists(), f"Missing schema file: {path}"
    on_disk = json.loads(path.read_text())
    assert on_disk == model.model_json_schema(), (
        f"Schema for {name} is stale — regenerate with "
        f"scripts/generate_study_eval_schemas.py"
    )


@pytest.mark.parametrize("name,model", list(MODEL_MAP.items()))
def test_example_exists_and_roundtrips(name, model):
    path = EXAMPLES_DIR / f"{name}_example.json"
    assert path.exists(), f"Missing example file: {path}"
    original = json.loads(path.read_text())
    instance = model.model_validate(original)
    reserialized = json.loads(instance.model_dump_json())
    assert reserialized == original
