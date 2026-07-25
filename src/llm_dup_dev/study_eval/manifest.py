"""Provenance manifest for one study-eval stage.

Follows the repo's ``run_manifest`` pattern (git sha, package/python versions,
redacted secure context) and adds the content-hash chain the freeze needs: each
stage records its ``inputs`` and ``outputs`` as :class:`ArtifactRef` (path +
sha256 + size), so the final result tables can be traced by hash back through
consensus, arbitration, sampling, to the canonical inputs.

Reuses ``run_manifest._git`` and ``run_manifest._redaction`` rather than
re-implementing them.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from llm_dup_dev.run_manifest._git import try_git_commit
from llm_dup_dev.run_manifest._redaction import redact_secrets
from llm_dup_dev.study_eval.checksums import sha256_canonical_json, sha256_file

SCHEMA_VERSION = "1.0"

Stage = Literal[
    "analysis",
    "workbooks",
    "arbitration",
    "finalize",
    "packets",
]


class ArtifactRef(BaseModel):
    """A content-addressed reference to one file on disk."""

    path: str
    sha256: str
    n_bytes: int


class DevPackageInfo(BaseModel):
    """Identity of the dev-side package producing the manifest."""

    name: str = "llm-dup-dev"
    version: str | None = None


class EvaluationRunManifest(BaseModel):
    """Record of one study-eval stage's provenance."""

    schema_version: str = SCHEMA_VERSION
    created_at: datetime
    run_id: str
    stage: Stage

    dev_package: DevPackageInfo = Field(default_factory=DevPackageInfo)
    dev_git_commit: str | None = None
    python_version: str | None = None

    inputs: list[ArtifactRef] = Field(default_factory=list)
    outputs: list[ArtifactRef] = Field(default_factory=list)

    analysis_variant: str | None = None
    ci_method: str | None = None

    secure_context: dict[str, Any] | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _redact(self) -> "EvaluationRunManifest":
        self.secure_context = redact_secrets(
            self.secure_context, field_name="secure_context"
        )
        return self


def artifact_ref(path: str | Path) -> ArtifactRef:
    """Build an :class:`ArtifactRef` for an existing file."""
    p = Path(path)
    return ArtifactRef(
        path=str(p),
        sha256=sha256_file(p),
        n_bytes=p.stat().st_size,
    )


def _package_version() -> str | None:
    try:
        return metadata.version("llm-dup-dev")
    except metadata.PackageNotFoundError:
        return None


def build_manifest(
    stage: Stage,
    run_id: str,
    *,
    inputs: Sequence[str | Path] | None = None,
    outputs: Sequence[str | Path] | None = None,
    analysis_variant: str | None = None,
    ci_method: str | None = None,
    secure_context: dict[str, Any] | None = None,
    repo_path: str | Path | None = None,
    notes: str | None = None,
) -> EvaluationRunManifest:
    """Assemble a manifest, hashing the input/output files and reading git state."""
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    return EvaluationRunManifest(
        created_at=datetime.now(timezone.utc),
        run_id=run_id,
        stage=stage,
        dev_package=DevPackageInfo(version=_package_version()),
        dev_git_commit=try_git_commit(repo_path or Path.cwd()),
        python_version=py,
        inputs=[artifact_ref(p) for p in (inputs or [])],
        outputs=[artifact_ref(p) for p in (outputs or [])],
        analysis_variant=analysis_variant,
        ci_method=ci_method,
        secure_context=secure_context,
        notes=notes,
    )


def write_manifest(
    output_dir: str | Path,
    manifest: EvaluationRunManifest,
    *,
    filename: str = "evaluation_run_manifest.json",
) -> Path:
    """Write ``manifest`` to ``output_dir`` as indented JSON."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / filename
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return path


__all__ = [
    "SCHEMA_VERSION",
    "Stage",
    "ArtifactRef",
    "DevPackageInfo",
    "EvaluationRunManifest",
    "artifact_ref",
    "build_manifest",
    "write_manifest",
    "sha256_canonical_json",
    "sha256_file",
]
