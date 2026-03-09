"""Tests that validate generated JSON schemas and examples.

Ensures that:
1. Schema files are valid JSON and match current Pydantic model schemas.
2. Example files are valid JSON and can be deserialized into their models.
"""

import json
from pathlib import Path

import pytest

from llm_dup_dev.models.report import Report
from llm_dup_dev.models.report_pair import ReportPair
from llm_dup_dev.models.pair_features import PairFeatures
from llm_dup_dev.models.classification import ClassificationResult
from llm_dup_dev.feature_generation.probability import FrequencyStats


# Resolve paths relative to repo root
REPO_ROOT = Path(__file__).parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"
EXAMPLES_DIR = REPO_ROOT / "examples"

MODEL_MAP = {
    "report": Report,
    "report_pair": ReportPair,
    "pair_features": PairFeatures,
    "classification_result": ClassificationResult,
    "frequency_stats": FrequencyStats,
}


# ===================================================================
# Schema validation
# ===================================================================

class TestSchemas:

    @pytest.mark.parametrize("name,model", list(MODEL_MAP.items()))
    def test_schema_file_exists(self, name, model):
        path = SCHEMAS_DIR / f"{name}.schema.json"
        assert path.exists(), f"Missing schema file: {path}"

    @pytest.mark.parametrize("name,model", list(MODEL_MAP.items()))
    def test_schema_is_valid_json(self, name, model):
        path = SCHEMAS_DIR / f"{name}.schema.json"
        with open(path) as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert "properties" in data or "$defs" in data or "title" in data

    @pytest.mark.parametrize("name,model", list(MODEL_MAP.items()))
    def test_schema_matches_model(self, name, model):
        """Schema on disk should match what the Pydantic model generates."""
        path = SCHEMAS_DIR / f"{name}.schema.json"
        with open(path) as f:
            on_disk = json.load(f)
        from_model = model.model_json_schema()
        assert on_disk == from_model, (
            f"Schema for {name} is stale — regenerate with "
            f"model.model_json_schema()"
        )


# ===================================================================
# Example validation
# ===================================================================

class TestExamples:

    @pytest.mark.parametrize("name,model", list(MODEL_MAP.items()))
    def test_example_file_exists(self, name, model):
        path = EXAMPLES_DIR / f"{name}_example.json"
        assert path.exists(), f"Missing example file: {path}"

    @pytest.mark.parametrize("name,model", list(MODEL_MAP.items()))
    def test_example_is_valid_json(self, name, model):
        path = EXAMPLES_DIR / f"{name}_example.json"
        with open(path) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    @pytest.mark.parametrize("name,model", list(MODEL_MAP.items()))
    def test_example_deserializes_into_model(self, name, model):
        """Example JSON should successfully parse into its Pydantic model."""
        path = EXAMPLES_DIR / f"{name}_example.json"
        with open(path) as f:
            data = json.load(f)
        instance = model.model_validate(data)
        assert instance is not None

    @pytest.mark.parametrize("name,model", list(MODEL_MAP.items()))
    def test_example_roundtrips(self, name, model):
        """Deserialize → re-serialize should produce identical JSON."""
        path = EXAMPLES_DIR / f"{name}_example.json"
        with open(path) as f:
            original = json.load(f)
        instance = model.model_validate(original)
        reserialized = json.loads(instance.model_dump_json())
        assert reserialized == original
