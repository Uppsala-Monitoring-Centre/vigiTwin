"""Pydantic models for the per-validation-set dataset manifest.

A ``DatasetManifest`` records the provenance of a validation set — the
source database (name + snapshot/version) and the MedDRA version used to
extract reports and frequency statistics. It lives once at the root of the
validation set folder; each ``RunManifest`` under that folder carries a
``DatasetRef`` pointing back to it via ``dataset_id``.

The dev repo defines the contract; secure-side scripts populate the actual
values when extracting a validation set from the source database. Secrets
in ``secure_context`` are redacted automatically by the same helper used
for run manifests (see :mod:`llm_dup_dev.run_manifest._redaction`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from llm_dup_dev.run_manifest._redaction import redact_secrets
from llm_dup_dev.run_manifest.models import DevPackageInfo

SCHEMA_VERSION = "1.0"


class DatasetSnapshot(BaseModel):
    """Identity of the data sources used to build a validation set.

    All four fields are required: a dataset manifest exists *because* this
    provenance needs to be pinned. Values are opaque to the dev repo —
    callers (secure-side) decide the format.
    """

    database_name: str
    database_snapshot: str
    meddra_version: str
    extracted_at: datetime


class DatasetManifest(BaseModel):
    """Record of a validation set's data provenance.

    Callers should populate this via :func:`build_dataset_manifest` rather
    than instantiating directly — the builder fills in defaults like
    ``created_at``, ``dataset_id``, ``dev_package``, and ``dev_git_commit``.
    """

    schema_version: str = SCHEMA_VERSION
    created_at: datetime
    dataset_id: str
    dataset_name: str | None = None

    dataset_snapshot: DatasetSnapshot

    dev_package: DevPackageInfo = Field(default_factory=DevPackageInfo)
    dev_git_commit: str | None = None

    secure_context: dict[str, Any] | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _redact_secret_values(self) -> "DatasetManifest":
        self.secure_context = redact_secrets(
            self.secure_context, field_name="secure_context"
        )
        return self
