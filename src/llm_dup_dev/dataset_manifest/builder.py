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
from llm_dup_dev.run_manifest._git import try_dev_git_commit, try_git_commit
from llm_dup_dev.run_manifest.builder import _llm_info_to_dict
from llm_dup_dev.run_manifest.models import DevPackageInfo

_PACKAGE_NAME = "llm-dup-dev"


def _detect_package_version() -> str | None:
    try:
        return version(_PACKAGE_NAME)
    except PackageNotFoundError:
        return None


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
    - ``dev_git_commit``: short HEAD sha of the checkout that *tracks* this
      package's source. ``None`` when the running source is not a working copy
      — an installed wheel has no commit to report, and the repository merely
      enclosing it (a venv inside the consumer's checkout) is not provenance.
      ``dev_repo_path`` overrides the lookup for callers who mean a specific
      checkout.

    Secrets in ``llm_config`` and ``secure_context`` are redacted by the
    model validator — see ``run_manifest._redaction.SECRET_KEY_PATTERNS``.
    """

    return DatasetManifest(
        created_at=created_at or datetime.now(timezone.utc),
        dataset_id=dataset_id or uuid.uuid4().hex[:12],
        dataset_name=dataset_name,
        dataset_snapshot=dataset_snapshot,
        dev_package=DevPackageInfo(
            name=_PACKAGE_NAME, version=_detect_package_version()
        ),
        dev_git_commit=(
            try_git_commit(dev_repo_path)
            if dev_repo_path is not None
            else try_dev_git_commit()
        ),
        llm_config=_llm_info_to_dict(llm_config),
        secure_context=dict(secure_context) if secure_context is not None else None,
        notes=notes,
    )
