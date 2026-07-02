"""Build a :class:`DatasetManifest` from explicit fields and defaults."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from llm_dup_dev.dataset_manifest.models import (
    DatasetManifest,
    DatasetSnapshot,
)
from llm_dup_dev.run_manifest._git import try_git_commit
from llm_dup_dev.run_manifest.builder import _llm_info_to_dict
from llm_dup_dev.run_manifest.models import DevPackageInfo

_PACKAGE_NAME = "llm-dup-dev"


def _detect_package_version() -> str | None:
    try:
        return version(_PACKAGE_NAME)
    except PackageNotFoundError:
        return None


def _default_dev_repo_path() -> Path:
    import llm_dup_dev

    return Path(llm_dup_dev.__file__).resolve().parent


def build_dataset_manifest(
    *,
    dataset_snapshot: DatasetSnapshot,
    dataset_name: str | None = None,
    dataset_id: str | None = None,
    llm_config: Any = None,
    secure_context: dict[str, Any] | None = None,
    notes: str | None = None,
    dev_repo_path: str | Path | None = None,
    created_at: datetime | None = None,
) -> DatasetManifest:
    """Assemble a :class:`DatasetManifest` for a validation set.

    ``llm_config`` records the **extraction-time** LLM that produced the
    dataset's frozen LLM artifacts (secure-side scripts supply it). It may
    be an ``LLMInfo`` dataclass, a Pydantic model, or a plain dict — all
    normalised to a dict. Secret-looking keys are redacted by the model
    validator.

    Auto-populated when not provided:

    - ``dataset_id``: 12-char hex from ``uuid4``. Every run under the
      validation set will reference this id via ``RunManifest.dataset_ref``.
    - ``created_at``: timezone-aware ``datetime.now(UTC)``.
    - ``dev_package.version``: from ``importlib.metadata`` if installed,
      else ``None``.
    - ``dev_git_commit``: from ``git rev-parse --short HEAD`` rooted at
      ``dev_repo_path`` (defaults to the package source directory);
      ``None`` if git metadata is unavailable.

    Secrets in ``llm_config`` and ``secure_context`` are redacted by the
    model validator — see ``run_manifest._redaction.SECRET_KEY_PATTERNS``.
    """
    repo_path = Path(dev_repo_path) if dev_repo_path else _default_dev_repo_path()

    return DatasetManifest(
        created_at=created_at or datetime.now(timezone.utc),
        dataset_id=dataset_id or uuid.uuid4().hex[:12],
        dataset_name=dataset_name,
        dataset_snapshot=dataset_snapshot,
        dev_package=DevPackageInfo(
            name=_PACKAGE_NAME, version=_detect_package_version()
        ),
        dev_git_commit=try_git_commit(repo_path),
        llm_config=_llm_info_to_dict(llm_config),
        secure_context=dict(secure_context) if secure_context is not None else None,
        notes=notes,
    )
