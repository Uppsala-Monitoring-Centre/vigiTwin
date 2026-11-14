"""`dev_git_commit` must name the dev package's own checkout, or nothing.

The failure these pin is silent: a study-eval stage is normally driven from the
consumer's repository, so reading git state from the working directory — or
from whatever repository happens to enclose an installed copy — yields a sha
that looks perfectly valid and names the wrong repository.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from llm_dup_dev.run_manifest import _git
from llm_dup_dev.run_manifest._git import (
    dev_repo_path,
    try_dev_git_commit,
    try_git_commit,
)
from llm_dup_dev.run_manifest.builder import build_run_manifest
from llm_dup_dev.study_eval.manifest import build_manifest


def _init_repo(path: Path) -> str:
    """Create a one-commit git repo at ``path``; return its short sha."""
    path.mkdir(parents=True, exist_ok=True)

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-c", "commit.gpgsign=false", *args],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )

    run("init", "-q")
    run("config", "user.email", "test@example.invalid")
    run("config", "user.name", "Test")
    # An ``__init__.py`` at the root, because that is the file
    # ``try_dev_git_commit`` asks git about.
    (path / "__init__.py").write_text("")
    run("add", "__init__.py")
    run("commit", "-qm", "initial")
    return run("rev-parse", "--short", "HEAD").stdout.strip()


def test_dev_repo_path_points_at_the_package_source() -> None:
    import llm_dup_dev

    assert dev_repo_path() == Path(llm_dup_dev.__file__).resolve().parent


def test_commit_is_read_from_the_repo_tracking_the_source(tmp_path, monkeypatch):
    """A tracked source directory reports the sha of the repo tracking it."""
    repo = tmp_path / "devrepo"
    sha = _init_repo(repo)
    monkeypatch.setattr(_git, "dev_repo_path", lambda: repo)

    assert try_dev_git_commit() == sha


def test_untracked_source_reports_nothing_not_the_enclosing_repo(
    tmp_path, monkeypatch
):
    """An installed copy inside a consumer's checkout must report ``None``.

    This is the wheel-in-a-venv case: ``site-packages`` sits under the
    consumer's repository, so git answers for *that* repository. Enclosure is
    not provenance, and a wrong sha is worse than an absent one.
    """
    consumer = tmp_path / "consumer"
    consumer_sha = _init_repo(consumer)
    installed = consumer / ".venv" / "lib" / "site-packages" / "llm_dup_dev"
    installed.mkdir(parents=True)
    (installed / "__init__.py").write_text("")
    monkeypatch.setattr(_git, "dev_repo_path", lambda: installed)

    # The low-level helper still answers for the enclosing repo — that is its
    # documented job, and exactly why provenance must not use it directly.
    assert try_git_commit(installed) == consumer_sha
    assert try_dev_git_commit() is None


def test_study_eval_manifest_ignores_the_callers_cwd(tmp_path, monkeypatch) -> None:
    """The reported defect: `build_manifest` defaulted to ``Path.cwd()``."""
    consumer = tmp_path / "consumer"
    consumer_sha = _init_repo(consumer)
    monkeypatch.chdir(consumer)

    manifest = build_manifest("finalize", "run-1")

    assert manifest.dev_git_commit != consumer_sha
    assert manifest.dev_git_commit == try_dev_git_commit()


def test_run_manifest_ignores_the_callers_cwd(tmp_path, monkeypatch) -> None:
    consumer = tmp_path / "consumer"
    consumer_sha = _init_repo(consumer)
    monkeypatch.chdir(consumer)

    assert build_run_manifest().dev_git_commit != consumer_sha


def test_explicit_repo_path_still_overrides(tmp_path, monkeypatch) -> None:
    """An explicitly supplied path is honoured: the caller knows what it means."""
    other = tmp_path / "other"
    other_sha = _init_repo(other)
    monkeypatch.chdir(tmp_path)

    manifest = build_manifest("finalize", "run-2", repo_path=other)

    assert manifest.dev_git_commit == other_sha
