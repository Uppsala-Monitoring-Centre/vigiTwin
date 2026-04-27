"""Best-effort git commit lookup. Always returns ``None`` rather than raising."""

from __future__ import annotations

import subprocess
from pathlib import Path


def try_git_commit(repo_path: str | Path) -> str | None:
    """Return short HEAD sha for the git repo containing ``repo_path``, or None.

    Tolerates: missing git binary, non-git directory, detached state,
    permission errors, and slow filesystems (via a 2s timeout).
    """
    try:
        path = Path(repo_path)
        if not path.exists():
            return None
        cwd = path if path.is_dir() else path.parent
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0:
            return None
        sha = result.stdout.strip()
        return sha or None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
