"""Tests for the run-manifest utility."""

from __future__ import annotations

import json
import warnings
from datetime import datetime, timezone

import pytest
import yaml

from llm_dup_dev.classifier.config import (
    AnchorConfig,
    DecisionThresholds,
    PipelineConfig,
)
from llm_dup_dev.data.synthetic import (
    SyntheticReportLoader,
    SyntheticStatsProvider,
)
from llm_dup_dev.pipeline.runner import PipelineRunner
from llm_dup_dev.run_manifest import (
    JSON_FILENAME,
    SCHEMA_VERSION,
    YAML_FILENAME,
    DatasetRef,
    RunManifest,
    build_run_manifest,
    write_run_manifest,
)
from llm_dup_dev.run_manifest._redaction import REDACTED


def test_build_run_manifest_minimal_inputs() -> None:
    manifest = build_run_manifest()

    assert manifest.schema_version == SCHEMA_VERSION
    assert isinstance(manifest.created_at, datetime)
    assert manifest.created_at.tzinfo is not None
    assert manifest.run_id and len(manifest.run_id) >= 8
    # Defaults filled in.
    assert manifest.pipeline_config == PipelineConfig().model_dump(mode="json")
    assert manifest.decision_thresholds == DecisionThresholds().model_dump(mode="json")
    # Open dicts default to empty.
    assert manifest.input_paths == {}
    assert manifest.output_paths == {}
    assert manifest.feature_flags == {}
    assert manifest.llm_config is None
    assert manifest.secure_context is None


def test_build_run_manifest_from_runner() -> None:
    custom_config = PipelineConfig(anchor=AnchorConfig(threshold=99))
    custom_thresholds = DecisionThresholds(lmatch_compelling=4.2)
    runner = PipelineRunner(
        report_loader=SyntheticReportLoader({}),
        stats_provider=SyntheticStatsProvider(),
        pipeline_config=custom_config,
        decision_thresholds=custom_thresholds,
        ae_clinical_picture_mode="rule_based",
    )

    manifest = build_run_manifest(runner=runner)

    # Effective config — non-default override survives.
    assert manifest.pipeline_config["anchor"]["threshold"] == 99
    assert manifest.decision_thresholds["lmatch_compelling"] == 4.2
    # Defaults still present alongside overrides.
    assert "domain_sizes" in manifest.pipeline_config
    # Feature flags reflect runner state.
    assert manifest.feature_flags["ae_clinical_picture_mode"] == "rule_based"
    assert manifest.feature_flags["has_llm_client"] is False
    assert manifest.feature_flags["has_frozen_source_case"] is False


def test_explicit_args_override_runner() -> None:
    runner = PipelineRunner(
        report_loader=SyntheticReportLoader({}),
        stats_provider=SyntheticStatsProvider(),
        pipeline_config=PipelineConfig(anchor=AnchorConfig(threshold=10)),
    )
    explicit = PipelineConfig(anchor=AnchorConfig(threshold=77))
    manifest = build_run_manifest(runner=runner, pipeline_config=explicit)
    assert manifest.pipeline_config["anchor"]["threshold"] == 77


def test_write_yaml_and_json(tmp_path) -> None:
    manifest = build_run_manifest(run_name="t1")
    paths = write_run_manifest(tmp_path, manifest, formats=("yaml", "json"))

    assert paths["yaml"] == tmp_path / YAML_FILENAME
    assert paths["json"] == tmp_path / JSON_FILENAME
    assert paths["yaml"].is_file()
    assert paths["json"].is_file()

    yaml_payload = yaml.safe_load(paths["yaml"].read_text())
    assert yaml_payload["run_name"] == "t1"
    assert yaml_payload["schema_version"] == SCHEMA_VERSION
    # datetime serialised as a string in JSON-mode dump.
    assert isinstance(yaml_payload["created_at"], str)

    json_payload = json.loads(paths["json"].read_text())
    assert json_payload["run_name"] == "t1"

    # Pydantic round-trip from the JSON file.
    restored = RunManifest.model_validate_json(paths["json"].read_text())
    assert restored.run_id == manifest.run_id


def test_write_default_yaml_only(tmp_path) -> None:
    manifest = build_run_manifest()
    paths = write_run_manifest(tmp_path, manifest)
    assert "yaml" in paths
    assert "json" not in paths
    assert not (tmp_path / JSON_FILENAME).exists()


def test_write_creates_missing_dirs(tmp_path) -> None:
    nested = tmp_path / "nested" / "deep" / "run_42"
    manifest = build_run_manifest()
    paths = write_run_manifest(nested, manifest)
    assert paths["yaml"].is_file()
    assert nested.is_dir()


def test_secure_context_preserved(tmp_path) -> None:
    secure = {
        "secure_repo_commit": "abc1234",
        "dataset_snapshot": "vigibase_2026Q1",
        "notebook": "eval_run.ipynb",
        "nested": {"sql_loader_version": "2.3.1", "unicode": "länder"},
    }
    manifest = build_run_manifest(secure_context=secure)
    paths = write_run_manifest(tmp_path, manifest, formats=("yaml", "json"))

    yaml_payload = yaml.safe_load(paths["yaml"].read_text())
    assert yaml_payload["secure_context"] == secure
    json_payload = json.loads(paths["json"].read_text())
    assert json_payload["secure_context"] == secure


def test_missing_git_metadata_does_not_fail(tmp_path) -> None:
    manifest = build_run_manifest(dev_repo_path=tmp_path)
    assert manifest.dev_git_commit is None


def test_missing_package_metadata_does_not_fail(monkeypatch) -> None:
    from importlib.metadata import PackageNotFoundError

    from llm_dup_dev.run_manifest import builder

    def _raise(_name: str) -> str:
        raise PackageNotFoundError("not installed")

    monkeypatch.setattr(builder, "version", _raise)
    manifest = build_run_manifest()
    assert manifest.dev_package.name == "llm-dup-dev"
    assert manifest.dev_package.version is None


def test_llm_config_auto_pulled_from_runner_client() -> None:
    """When ``llm_config=`` is omitted, the builder duck-types
    ``runner._llm_client.info()`` to populate the field."""
    from llm_dup_dev.llm.fake import FakeLLMClient

    runner = PipelineRunner(
        report_loader=SyntheticReportLoader({}),
        stats_provider=SyntheticStatsProvider(),
        llm_client=FakeLLMClient(model_name="auto-pulled-model"),
    )

    manifest = build_run_manifest(runner=runner)

    assert manifest.llm_config is not None
    assert manifest.llm_config["provider"] == "fake"
    assert manifest.llm_config["model"] == "auto-pulled-model"
    assert manifest.llm_config["default_temperature"] == 0.0
    # has_llm_client flag still set alongside the auto-pulled config.
    assert manifest.feature_flags["has_llm_client"] is True


def test_explicit_llm_config_overrides_runner_client_info() -> None:
    """An explicit ``llm_config=`` supersedes whatever ``info()`` would return."""
    from llm_dup_dev.llm.fake import FakeLLMClient

    runner = PipelineRunner(
        report_loader=SyntheticReportLoader({}),
        stats_provider=SyntheticStatsProvider(),
        llm_client=FakeLLMClient(model_name="auto-pulled-model"),
    )
    manifest = build_run_manifest(
        runner=runner,
        llm_config={"provider": "manual", "model": "explicit-override"},
    )

    assert manifest.llm_config == {
        "provider": "manual",
        "model": "explicit-override",
    }


def test_llm_config_falls_back_to_none_when_client_lacks_info() -> None:
    """A client missing ``info()`` is silently tolerated — manifest stays empty."""

    class MinimalClient:
        def generate(self, request):
            raise NotImplementedError

    runner = PipelineRunner(
        report_loader=SyntheticReportLoader({}),
        stats_provider=SyntheticStatsProvider(),
        llm_client=MinimalClient(),
    )

    manifest = build_run_manifest(runner=runner)
    assert manifest.llm_config is None
    assert manifest.feature_flags["has_llm_client"] is True


def test_secret_keys_redacted_in_llm_config() -> None:
    leaky = {
        "deployment": "gpt-4o-prod",
        "model": "gpt-4o",
        "temperature": 0.0,
        "api_key": "sk-LEAK",
        "Authorization": "Bearer abc",
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        manifest = build_run_manifest(llm_config=leaky)

    assert manifest.llm_config is not None
    assert manifest.llm_config["deployment"] == "gpt-4o-prod"
    assert manifest.llm_config["model"] == "gpt-4o"
    assert manifest.llm_config["temperature"] == 0.0
    assert manifest.llm_config["api_key"] == REDACTED
    assert manifest.llm_config["Authorization"] == REDACTED
    assert any("llm_config" in str(w.message) for w in caught)


def test_secret_keys_redacted_in_secure_context() -> None:
    leaky = {
        "dataset_snapshot": "vigibase_2026Q1",
        "connection_string": "Server=db;Password=hunter2;",
        "nested": {"client_secret": "shh", "ok": "fine"},
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        manifest = build_run_manifest(secure_context=leaky)

    assert manifest.secure_context is not None
    assert manifest.secure_context["dataset_snapshot"] == "vigibase_2026Q1"
    assert manifest.secure_context["connection_string"] == REDACTED
    assert manifest.secure_context["nested"]["client_secret"] == REDACTED
    assert manifest.secure_context["nested"]["ok"] == "fine"
    assert any("secure_context" in str(w.message) for w in caught)


def test_unknown_format_raises(tmp_path) -> None:
    manifest = build_run_manifest()
    with pytest.raises(ValueError, match="Unsupported"):
        write_run_manifest(tmp_path, manifest, formats=("xml",))


def test_empty_formats_raises(tmp_path) -> None:
    manifest = build_run_manifest()
    with pytest.raises(ValueError, match="At least one"):
        write_run_manifest(tmp_path, manifest, formats=())


def test_run_id_and_created_at_are_unique_across_calls() -> None:
    a = build_run_manifest()
    b = build_run_manifest()
    assert a.run_id != b.run_id


def test_explicit_created_at_and_run_id() -> None:
    when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    manifest = build_run_manifest(run_id="custom-1", created_at=when)
    assert manifest.run_id == "custom-1"
    assert manifest.created_at == when


def test_schema_version_is_1_1() -> None:
    """Adding ``dataset_ref`` is an additive change — schema version bumped to 1.1."""
    assert SCHEMA_VERSION == "1.1"


def test_dataset_ref_default_none() -> None:
    manifest = build_run_manifest()
    assert manifest.dataset_ref is None


def test_dataset_ref_round_trip(tmp_path) -> None:
    ref = DatasetRef(
        dataset_id="ds-abc123",
        relative_path="../dataset_manifest.yaml",
        database_name="VigiBase",
        database_snapshot="2026Q1",
        meddra_version="27.0",
    )
    manifest = build_run_manifest(dataset_ref=ref)

    assert manifest.dataset_ref is not None
    assert manifest.dataset_ref.dataset_id == "ds-abc123"
    assert manifest.dataset_ref.meddra_version == "27.0"

    paths = write_run_manifest(tmp_path, manifest, formats=("yaml", "json"))
    yaml_payload = yaml.safe_load(paths["yaml"].read_text())
    assert yaml_payload["dataset_ref"]["dataset_id"] == "ds-abc123"
    assert yaml_payload["dataset_ref"]["database_snapshot"] == "2026Q1"
    assert yaml_payload["dataset_ref"]["meddra_version"] == "27.0"

    restored = RunManifest.model_validate_json(paths["json"].read_text())
    assert restored.dataset_ref is not None
    assert restored.dataset_ref.dataset_id == "ds-abc123"
    assert restored.dataset_ref.database_name == "VigiBase"
