"""Best-effort git commit lookup. Always returns ``None`` rather than raising."""

from __future__ import annotations

import subprocess
from pathlib import Path

_TIMEOUT_S = 2


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str] | None:
    """Run ``git`` in ``cwd``; ``None`` if it could not be run at all."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def try_git_commit(repo_path: str | Path) -> str | None:
    """Return short HEAD sha for the git repo containing ``repo_path``, or None.

    Tolerates: missing git binary, non-git directory, detached state,
    permission errors, and slow filesystems (via a 2s timeout).

    This answers "which repository encloses this path", which is **not** the
    question provenance asks. ``git`` searches upwards, so a path inside an
    installed copy of this package resolves to whatever repository happens to
    sit above it — commonly the consumer's own checkout. Use
    :func:`try_dev_git_commit` to record which code ran.
    """
    try:
        path = Path(repo_path)
        if not path.exists():
            return None
        cwd = path if path.is_dir() else path.parent
    except OSError:
        return None
    result = _run_git(["rev-parse", "--short", "HEAD"], cwd=cwd)
    if result is None or result.returncode != 0:
        return None
    return result.stdout.strip() or None


def dev_repo_path() -> Path:
    """The directory holding this package's own source.

    In an editable install this is inside the development checkout; in a wheel
    install it is inside ``site-packages``.
    """
    import llm_dup_dev

    return Path(llm_dup_dev.__file__).resolve().parent


def try_dev_git_commit() -> str | None:
    """Short HEAD sha of the checkout this package's source is *tracked in*.

    ``None`` unless the running source really is a working copy. Enclosure is
    deliberately not enough: a virtualenv usually lives inside the consumer's
    own checkout, so an installed copy sits under a repository that has nothing
    to do with this package, and ``git rev-parse`` from there returns a sha that
    looks valid and names the wrong repository. Silently-wrong provenance is
    worse than absent provenance — for an installed wheel ``dev_package.version``
    is the identity, and ``None`` says so honestly.

    The test is whether git *tracks* the package's ``__init__.py``, not whether
    some repository encloses it.
    """
    source = dev_repo_path()
    tracked = _run_git(["ls-files", "--error-unmatch", "--", "__init__.py"], cwd=source)
    if tracked is None or tracked.returncode != 0:
        return None
    return try_git_commit(source)
