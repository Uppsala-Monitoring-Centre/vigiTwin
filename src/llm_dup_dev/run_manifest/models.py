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

# 1.0 → 1.1: added ``dataset_ref``.
# 1.1 → 1.2: added ``dataset_ref.llm_config`` (extraction-time model slot).
SCHEMA_VERSION = "1.2"


class DevPackageInfo(BaseModel):
    """Identity of the dev-side package that produced the manifest."""

    name: str = "llm-dup-dev"
    version: str | None = None


class DatasetRef(BaseModel):
    """Pointer from a run manifest to its parent dataset manifest.

    ``dataset_id`` is the authoritative join key (matches
    ``DatasetManifest.dataset_id``). The database / MedDRA fields — and
    ``llm_config`` — are duplicated here so that a copied or archived run
    folder remains self-describing even when the parent dataset manifest is
    unavailable; ``relative_path`` is advisory only — the dev repo does not
    enforce a folder layout.

    ``llm_config`` records the dataset's **extraction-time** LLM (the model
    that produced the frozen narrative-demographics / source-case
    artifacts). It is a distinct slot from ``RunManifest.llm_config``, which
    records the run's *live inference-time* model; a single run may use both.
    Same ``LLMInfo``-shaped dict as elsewhere; secret-looking keys are
    redacted by ``RunManifest``'s validator.
    """

    dataset_id: str
    relative_path: str | None = None
    database_name: str | None = None
    database_snapshot: str | None = None
    meddra_version: str | None = None
    llm_config: dict[str, Any] | None = None

    @classmethod
    def from_manifest(
        cls, manifest: Any, *, relative_path: str | None = None
    ) -> "DatasetRef":
        """Build a self-describing ref from a ``DatasetManifest``.

        Copies the join key plus the duplicated provenance fields
        (database identity, MedDRA version, and the extraction-time
        ``llm_config``) so an offline run that references a dataset records
        which model produced its frozen artifacts. Typed as ``Any`` to
        avoid a circular import with :mod:`llm_dup_dev.dataset_manifest`.
        """
        snapshot = manifest.dataset_snapshot
        return cls(
            dataset_id=manifest.dataset_id,
            relative_path=relative_path,
            database_name=snapshot.database_name,
            database_snapshot=snapshot.database_snapshot,
            meddra_version=snapshot.meddra_version,
            llm_config=manifest.llm_config,
        )


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
    # The run's *live inference-time* model (e.g. the AE clinical-picture
    # LLM). ``None`` for a fully-offline run. The dataset's *extraction-time*
    # model is recorded separately in ``dataset_ref.llm_config``.
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
        if self.dataset_ref is not None:
            self.dataset_ref.llm_config = redact_secrets(
                self.dataset_ref.llm_config, field_name="dataset_ref.llm_config"
            )
        return self
