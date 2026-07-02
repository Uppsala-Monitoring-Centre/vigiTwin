"""Tests for the dataset-manifest utility."""

from __future__ import annotations

import json
import re
import warnings
from datetime import datetime, timezone

import pytest
import yaml

from llm_dup_dev.dataset_manifest import (
    JSON_FILENAME,
    SCHEMA_VERSION,
    YAML_FILENAME,
    DatasetManifest,
    DatasetSnapshot,
    build_dataset_manifest,
    write_dataset_manifest,
)
from llm_dup_dev.run_manifest._redaction import REDACTED


def _snapshot() -> DatasetSnapshot:
    return DatasetSnapshot(
        database_name="VigiBase",
        database_snapshot="2026Q1",
        meddra_version="27.0",
        extracted_at=datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc),
    )


def test_build_dataset_manifest_minimal_inputs() -> None:
    manifest = build_dataset_manifest(dataset_snapshot=_snapshot())

    assert manifest.schema_version == SCHEMA_VERSION
    assert isinstance(manifest.created_at, datetime)
    assert manifest.created_at.tzinfo is not None
    assert manifest.dataset_id and re.fullmatch(r"[0-9a-f]{12}", manifest.dataset_id)
    assert manifest.dataset_name is None
    assert manifest.dataset_snapshot.database_name == "VigiBase"
    assert manifest.dataset_snapshot.database_snapshot == "2026Q1"
    assert manifest.dataset_snapshot.meddra_version == "27.0"
    assert manifest.secure_context is None
    assert manifest.notes is None


def test_dataset_id_unique_across_calls() -> None:
    a = build_dataset_manifest(dataset_snapshot=_snapshot())
    b = build_dataset_manifest(dataset_snapshot=_snapshot())
    assert a.dataset_id != b.dataset_id


def test_explicit_dataset_id_and_created_at() -> None:
    when = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    manifest = build_dataset_manifest(
        dataset_snapshot=_snapshot(),
        dataset_id="custom-1",
        created_at=when,
        dataset_name="quarterly_validation_2026Q1",
    )
    assert manifest.dataset_id == "custom-1"
    assert manifest.created_at == when
    assert manifest.dataset_name == "quarterly_validation_2026Q1"


def test_dataset_snapshot_required_fields() -> None:
    # All four fields are required; missing any raises ValidationError.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DatasetSnapshot(  # type: ignore[call-arg]
            database_name="VigiBase",
            database_snapshot="2026Q1",
            meddra_version="27.0",
            # extracted_at missing
        )


def test_write_yaml_and_json(tmp_path) -> None:
    manifest = build_dataset_manifest(
        dataset_snapshot=_snapshot(),
        dataset_name="vs1",
    )
    paths = write_dataset_manifest(tmp_path, manifest, formats=("yaml", "json"))

    assert paths["yaml"] == tmp_path / YAML_FILENAME
    assert paths["json"] == tmp_path / JSON_FILENAME
    assert paths["yaml"].is_file()
    assert paths["json"].is_file()

    yaml_payload = yaml.safe_load(paths["yaml"].read_text())
    assert yaml_payload["dataset_name"] == "vs1"
    assert yaml_payload["schema_version"] == SCHEMA_VERSION
    assert yaml_payload["dataset_snapshot"]["database_name"] == "VigiBase"
    assert yaml_payload["dataset_snapshot"]["meddra_version"] == "27.0"
    # datetime serialised as a string in JSON-mode dump.
    assert isinstance(yaml_payload["created_at"], str)
    assert isinstance(yaml_payload["dataset_snapshot"]["extracted_at"], str)

    json_payload = json.loads(paths["json"].read_text())
    assert json_payload["dataset_name"] == "vs1"

    restored = DatasetManifest.model_validate_json(paths["json"].read_text())
    assert restored.dataset_id == manifest.dataset_id
    assert restored.dataset_snapshot.meddra_version == "27.0"


def test_write_default_yaml_only(tmp_path) -> None:
    manifest = build_dataset_manifest(dataset_snapshot=_snapshot())
    paths = write_dataset_manifest(tmp_path, manifest)
    assert "yaml" in paths
    assert "json" not in paths
    assert not (tmp_path / JSON_FILENAME).exists()


def test_write_creates_missing_dirs(tmp_path) -> None:
    nested = tmp_path / "validation_sets" / "vs1"
    manifest = build_dataset_manifest(dataset_snapshot=_snapshot())
    paths = write_dataset_manifest(nested, manifest)
    assert paths["yaml"].is_file()
    assert nested.is_dir()


def test_secure_context_preserved(tmp_path) -> None:
    secure = {
        "secure_repo_commit": "abc1234",
        "notebook": "extract_vs1.ipynb",
        "nested": {"sql_loader_version": "2.3.1", "unicode": "länder"},
    }
    manifest = build_dataset_manifest(
        dataset_snapshot=_snapshot(),
        secure_context=secure,
    )
    paths = write_dataset_manifest(tmp_path, manifest, formats=("yaml", "json"))

    yaml_payload = yaml.safe_load(paths["yaml"].read_text())
    assert yaml_payload["secure_context"] == secure
    json_payload = json.loads(paths["json"].read_text())
    assert json_payload["secure_context"] == secure


def test_secret_keys_redacted_in_secure_context() -> None:
    leaky = {
        "secure_repo_commit": "abc1234",
        "connection_string": "Server=db;Password=hunter2;",
        "nested": {"client_secret": "shh", "ok": "fine"},
    }
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        manifest = build_dataset_manifest(
            dataset_snapshot=_snapshot(),
            secure_context=leaky,
        )

    assert manifest.secure_context is not None
    assert manifest.secure_context["secure_repo_commit"] == "abc1234"
    assert manifest.secure_context["connection_string"] == REDACTED
    assert manifest.secure_context["nested"]["client_secret"] == REDACTED
    assert manifest.secure_context["nested"]["ok"] == "fine"
    assert any("secure_context" in str(w.message) for w in caught)


def test_llm_config_defaults_none() -> None:
    manifest = build_dataset_manifest(dataset_snapshot=_snapshot())
    assert manifest.llm_config is None


def test_llm_config_passthrough_and_roundtrip(tmp_path) -> None:
    manifest = build_dataset_manifest(
        dataset_snapshot=_snapshot(),
        llm_config={"provider": "azure_openai", "model": "gpt-5.4"},
    )
    assert manifest.llm_config == {"provider": "azure_openai", "model": "gpt-5.4"}

    paths = write_dataset_manifest(tmp_path, manifest, formats=("yaml", "json"))
    assert yaml.safe_load(paths["yaml"].read_text())["llm_config"]["model"] == "gpt-5.4"
    restored = DatasetManifest.model_validate_json(paths["json"].read_text())
    assert restored.llm_config["model"] == "gpt-5.4"


def test_llm_config_accepts_llm_info() -> None:
    from llm_dup_dev.llm.models import LLMInfo

    manifest = build_dataset_manifest(
        dataset_snapshot=_snapshot(),
        llm_config=LLMInfo(provider="fake", model="gpt-5.4"),
    )
    assert manifest.llm_config["provider"] == "fake"
    assert manifest.llm_config["model"] == "gpt-5.4"


def test_llm_config_secrets_redacted() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        manifest = build_dataset_manifest(
            dataset_snapshot=_snapshot(),
            llm_config={"model": "gpt-5.4", "api_key": "sk-secret"},
        )
    assert manifest.llm_config["model"] == "gpt-5.4"
    assert manifest.llm_config["api_key"] == REDACTED


def test_unknown_format_raises(tmp_path) -> None:
    manifest = build_dataset_manifest(dataset_snapshot=_snapshot())
    with pytest.raises(ValueError, match="Unsupported"):
        write_dataset_manifest(tmp_path, manifest, formats=("xml",))


def test_empty_formats_raises(tmp_path) -> None:
    manifest = build_dataset_manifest(dataset_snapshot=_snapshot())
    with pytest.raises(ValueError, match="At least one"):
        write_dataset_manifest(tmp_path, manifest, formats=())


def test_missing_git_metadata_does_not_fail(tmp_path) -> None:
    manifest = build_dataset_manifest(
        dataset_snapshot=_snapshot(),
        dev_repo_path=tmp_path,
    )
    assert manifest.dev_git_commit is None
