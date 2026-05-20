"""Pydantic models for the per-run manifest.

A ``RunManifest`` records the *effective* configuration and metadata of a
single pipeline run, so that a results folder is self-describing and an old
evaluation can be re-run with the same knobs. The dev repo defines the
contract; secure-side scripts populate ``secure_context`` with
deployment-specific metadata (dataset snapshot, secure repo commit, notebook
name, …).

Secrets are redacted automatically. See ``_redaction.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from llm_dup_dev.run_manifest._redaction import redact_secrets

SCHEMA_VERSION = "1.1"


class DevPackageInfo(BaseModel):
    """Identity of the dev-side package that produced the manifest."""

    name: str = "llm-dup-dev"
    version: str | None = None


class DatasetRef(BaseModel):
    """Pointer from a run manifest to its parent dataset manifest.

    ``dataset_id`` is the authoritative join key (matches
    ``DatasetManifest.dataset_id``). The database / MedDRA fields are
    duplicated here so that a copied or archived run folder remains
    self-describing even when the parent dataset manifest is unavailable;
    ``relative_path`` is advisory only — the dev repo does not enforce a
    folder layout.
    """

    dataset_id: str
    relative_path: str | None = None
    database_name: str | None = None
    database_snapshot: str | None = None
    meddra_version: str | None = None


class RunManifest(BaseModel):
    """Record of the effective configuration and metadata for one pipeline run.

    Callers should populate this via :func:`build_run_manifest` rather than
    instantiating directly — the builder fills in defaults like ``created_at``
    and pulls effective config off a ``PipelineRunner``.
    """

    schema_version: str = SCHEMA_VERSION
    created_at: datetime
    run_id: str
    run_name: str | None = None

    dev_package: DevPackageInfo = Field(default_factory=DevPackageInfo)
    dev_git_commit: str | None = None

    input_paths: dict[str, Any] = Field(default_factory=dict)
    pipeline_config: dict[str, Any] = Field(default_factory=dict)
    decision_thresholds: dict[str, Any] = Field(default_factory=dict)
    feature_flags: dict[str, Any] = Field(default_factory=dict)
    llm_config: dict[str, Any] | None = None
    output_paths: dict[str, Any] = Field(default_factory=dict)

    secure_context: dict[str, Any] | None = None
    dataset_ref: DatasetRef | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _redact_secret_values(self) -> "RunManifest":
        # Mutate in place — Pydantic v2 model_validator(mode="after") is allowed
        # to set attributes on self.
        self.llm_config = redact_secrets(self.llm_config, field_name="llm_config")
        self.secure_context = redact_secrets(
            self.secure_context, field_name="secure_context"
        )
        return self
