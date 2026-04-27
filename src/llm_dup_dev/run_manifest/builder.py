"""Build a :class:`RunManifest` from a runner and/or explicit overrides."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, Any

from llm_dup_dev.classifier.config import DecisionThresholds, PipelineConfig
from llm_dup_dev.run_manifest._git import try_git_commit
from llm_dup_dev.run_manifest.models import DevPackageInfo, RunManifest

if TYPE_CHECKING:
    from llm_dup_dev.pipeline.runner import PipelineRunner


_PACKAGE_NAME = "llm-dup-dev"


def _detect_package_version() -> str | None:
    try:
        return version(_PACKAGE_NAME)
    except PackageNotFoundError:
        return None


def _default_dev_repo_path() -> Path:
    # Use the package directory as a stable starting point for git lookup.
    # In editable installs this resolves to the source tree.
    import llm_dup_dev

    return Path(llm_dup_dev.__file__).resolve().parent


def _runner_feature_flags(runner: "PipelineRunner") -> dict[str, Any]:
    return {
        "ae_clinical_picture_mode": getattr(
            runner, "_ae_clinical_picture_mode", None
        ),
        "has_llm_client": getattr(runner, "_llm_client", None) is not None,
        "has_frozen_source_case": getattr(runner, "_frozen_source_case", None)
        is not None,
        "has_frozen_narrative_demographics": getattr(
            runner, "_frozen_narrative_demographics", None
        )
        is not None,
    }


def build_run_manifest(
    *,
    runner: "PipelineRunner | None" = None,
    pipeline_config: PipelineConfig | None = None,
    decision_thresholds: DecisionThresholds | None = None,
    feature_flags: dict[str, Any] | None = None,
    llm_config: dict[str, Any] | None = None,
    input_paths: dict[str, Any] | None = None,
    output_paths: dict[str, Any] | None = None,
    secure_context: dict[str, Any] | None = None,
    run_id: str | None = None,
    run_name: str | None = None,
    notes: str | None = None,
    dev_repo_path: str | Path | None = None,
    created_at: datetime | None = None,
) -> RunManifest:
    """Assemble a :class:`RunManifest` capturing effective runtime state.

    Resolution order for config and feature flags:

    1. Explicit keyword arguments (``pipeline_config``, ``decision_thresholds``,
       ``feature_flags``) win.
    2. If a ``runner`` is supplied, its resolved attributes are used.
    3. Otherwise defaults (``PipelineConfig()``, ``DecisionThresholds()``) are
       used so the manifest still represents what *would* run.

    Auto-populated when not provided:

    - ``run_id``: 12-char hex from ``uuid4``.
    - ``created_at``: timezone-aware ``datetime.now(UTC)``.
    - ``dev_package.version``: from ``importlib.metadata`` if installed,
      else ``None``.
    - ``dev_git_commit``: from ``git rev-parse --short HEAD`` rooted at
      ``dev_repo_path`` (defaults to the package source directory); ``None``
      if git metadata is unavailable.

    Secrets in ``llm_config`` and ``secure_context`` are redacted by the
    model validator — see ``_redaction.SECRET_KEY_PATTERNS``.
    """
    resolved_pipeline_config = pipeline_config
    resolved_decision_thresholds = decision_thresholds
    resolved_feature_flags: dict[str, Any] = {}

    if runner is not None:
        if resolved_pipeline_config is None:
            resolved_pipeline_config = getattr(runner, "_pipeline_config", None)
        if resolved_decision_thresholds is None:
            resolved_decision_thresholds = getattr(
                runner, "_decision_thresholds", None
            )
        resolved_feature_flags.update(_runner_feature_flags(runner))

    if feature_flags:
        resolved_feature_flags.update(feature_flags)

    if resolved_pipeline_config is None:
        resolved_pipeline_config = PipelineConfig()
    if resolved_decision_thresholds is None:
        resolved_decision_thresholds = DecisionThresholds()

    repo_path = Path(dev_repo_path) if dev_repo_path else _default_dev_repo_path()

    return RunManifest(
        created_at=created_at or datetime.now(timezone.utc),
        run_id=run_id or uuid.uuid4().hex[:12],
        run_name=run_name,
        dev_package=DevPackageInfo(
            name=_PACKAGE_NAME, version=_detect_package_version()
        ),
        dev_git_commit=try_git_commit(repo_path),
        input_paths=dict(input_paths or {}),
        pipeline_config=resolved_pipeline_config.model_dump(mode="json"),
        decision_thresholds=resolved_decision_thresholds.model_dump(mode="json"),
        feature_flags=resolved_feature_flags,
        llm_config=dict(llm_config) if llm_config is not None else None,
        output_paths=dict(output_paths or {}),
        secure_context=dict(secure_context) if secure_context is not None else None,
        notes=notes,
    )
