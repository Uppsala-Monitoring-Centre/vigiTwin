"""Build a :class:`RunManifest` from a runner and/or explicit overrides."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, Any

from llm_dup_dev.classifier.config import DecisionThresholds, PipelineConfig
from llm_dup_dev.run_manifest._git import try_git_commit
from llm_dup_dev.run_manifest.models import DatasetRef, DevPackageInfo, RunManifest

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


def _llm_info_to_dict(info: Any) -> dict[str, Any] | None:
    """Normalise LLM identity metadata to a plain dict.

    Accepts an ``LLMInfo`` (frozen dataclass), any Pydantic model, or a
    plain dict — matched by duck-typing so this module stays decoupled
    from the ``llm`` package. Returns ``None`` for ``None`` or an
    unrecognised shape.
    """
    if info is None:
        return None
    if dataclasses.is_dataclass(info) and not isinstance(info, type):
        return dataclasses.asdict(info)
    if hasattr(info, "model_dump"):
        return info.model_dump(mode="json")
    if isinstance(info, dict):
        return dict(info)
    return None


def _llm_config_from_client(client: Any) -> dict[str, Any] | None:
    """Best-effort extraction of LLM identity from an ``LLMClient`` instance.

    Calls the client's optional ``info()`` method (see the ``LLMClient``
    protocol docstring) and converts the result to a plain dict via
    :func:`_llm_info_to_dict`. Returns ``None`` when the client is
    missing, lacks ``info()``, or returns an unrecognised shape — the
    manifest then falls back to whatever the caller passed explicitly (or
    leaves ``llm_config`` empty).
    """
    if client is None:
        return None
    info_method = getattr(client, "info", None)
    if not callable(info_method):
        return None
    return _llm_info_to_dict(info_method())


def _runner_feature_flags(runner: "PipelineRunner") -> dict[str, Any]:
    has_llm_client = getattr(runner, "_llm_client", None) is not None
    has_frozen_source_case = (
        getattr(runner, "_frozen_source_case", None) is not None
    )
    has_frozen_narrative_demographics = (
        getattr(runner, "_frozen_narrative_demographics", None) is not None
    )
    return {
        "ae_clinical_picture_mode": getattr(
            runner, "_ae_clinical_picture_mode", None
        ),
        "has_llm_client": has_llm_client,
        "has_frozen_source_case": has_frozen_source_case,
        "has_frozen_narrative_demographics": has_frozen_narrative_demographics,
        # Derived: an LLM-derived feature is *enabled* when either a live
        # client is configured (so per-report extraction can happen at
        # inference) or a frozen sidecar covers the relevant reports. With
        # neither, the feature silently does nothing — and the runner emits
        # a WARNING at ``run_batch`` start. Surfacing the disabled state
        # here makes it auditable in saved manifests.
        "source_case_override_enabled": has_llm_client
        or has_frozen_source_case,
        "narrative_demographics_extraction_enabled": has_llm_client
        or has_frozen_narrative_demographics,
    }


def build_run_manifest(
    *,
    runner: "PipelineRunner | None" = None,
    pipeline_config: PipelineConfig | None = None,
    decision_thresholds: DecisionThresholds | None = None,
    feature_flags: dict[str, Any] | None = None,
    llm_config: Any = None,
    input_paths: dict[str, Any] | None = None,
    output_paths: dict[str, Any] | None = None,
    secure_context: dict[str, Any] | None = None,
    dataset_ref: DatasetRef | None = None,
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
    - ``llm_config``: the run's *live inference-time* model. When not
      supplied, extracted via duck-typing from ``runner._llm_client.info()``
      (see the ``LLMClient`` protocol docstring). Clients that don't expose
      ``info()`` simply leave the field unset; supplying ``llm_config=``
      explicitly always wins. An explicit value may be an ``LLMInfo``
      dataclass, a Pydantic model, or a plain dict. It is left ``None`` for
      a fully-offline run (no live client) and never inherits from
      ``dataset_ref`` — the *extraction-time* model lives in its own slot,
      ``dataset_ref.llm_config`` (see :class:`DatasetRef`).

    Secrets in ``llm_config``, ``dataset_ref.llm_config`` and
    ``secure_context`` are redacted by the model validator — see
    ``_redaction.SECRET_KEY_PATTERNS``.
    """
    resolved_pipeline_config = pipeline_config
    resolved_decision_thresholds = decision_thresholds
    resolved_feature_flags: dict[str, Any] = {}

    # The top-level ``llm_config`` records the run's *live inference-time*
    # model. Resolution: explicit arg > client ``info()``. It deliberately
    # does NOT fall back to ``dataset_ref.llm_config`` — that slot records
    # the distinct *extraction-time* model and the two are never merged.
    resolved_llm_config: dict[str, Any] | None = _llm_info_to_dict(llm_config)

    if runner is not None:
        if resolved_pipeline_config is None:
            resolved_pipeline_config = getattr(runner, "_pipeline_config", None)
        if resolved_decision_thresholds is None:
            resolved_decision_thresholds = getattr(
                runner, "_decision_thresholds", None
            )
        resolved_feature_flags.update(_runner_feature_flags(runner))
        if resolved_llm_config is None:
            resolved_llm_config = _llm_config_from_client(
                getattr(runner, "_llm_client", None)
            )

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
        llm_config=resolved_llm_config,
        output_paths=dict(output_paths or {}),
        secure_context=dict(secure_context) if secure_context is not None else None,
        dataset_ref=dataset_ref,
        notes=notes,
    )
