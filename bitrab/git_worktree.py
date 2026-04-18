"""Git worktree lifecycle for per-job filesystem isolation.

When jobs run in parallel they share the project root.  Any job that mutates the
filesystem (writes files, installs packages, generates build outputs) will stomp
on sibling jobs running concurrently.  ``git worktree`` gives us a cheap way out:
each worker gets its own checkout of the same commit, sharing the object store
with the main repo, so the cost is roughly "create a directory and write a few
metadata files" rather than a full clone.

The worktrees live under ``.bitrab/worktrees/<sanitized_job_name>/`` — the
``.bitrab/`` directory is already gitignored, which is fine for a worktree:
git tracks worktree location via ``.git/worktrees/`` metadata, not via the
working-tree files, so ignored paths and worktrees coexist without problems.

Public API:

* :func:`is_git_available` — is the ``git`` binary callable at all?
* :func:`is_git_repo` — is *project_dir* inside a git working copy?
* :func:`can_use_worktrees` — both of the above are True.
* :func:`create_worktree` / :func:`remove_worktree` — low-level lifecycle.
* :func:`job_worktree` — context manager; always removes the worktree.
* :func:`prune_worktrees` — housekeeping for abandoned worktrees.

Everything here is best-effort on the *remove* side: a killed process can leave
an orphan directory under ``.bitrab/worktrees/`` and an orphan entry in
``.git/worktrees/``.  :func:`prune_worktrees` plus ``git worktree prune`` is how
we recover.
"""

from __future__ import annotations

import re
import shutil
import subprocess  # nosec
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_WORKTREES_SUBDIR = ".bitrab/worktrees"
_INVALID_NAME_CHARS_RE = re.compile(r'[\\/:*?"<>|\s]+')


@dataclass(frozen=True)
class WorktreeContext:
    """Paths describing a live worktree checkout."""

    worktree_path: Path
    project_dir: Path


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run ``git`` with *args* in *cwd*, capturing output as text."""
    return subprocess.run(  # nosec
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def is_git_available() -> bool:
    """Return True if the ``git`` executable is on PATH."""
    return shutil.which("git") is not None


def is_git_repo(project_dir: Path) -> bool:
    """Return True if *project_dir* lives inside a git working copy."""
    if not is_git_available():
        return False
    try:
        result = _run_git(["rev-parse", "--is-inside-work-tree"], cwd=project_dir)
    except (OSError, FileNotFoundError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def can_use_worktrees(project_dir: Path) -> bool:
    """Return True iff git is available and *project_dir* is a git repo."""
    return is_git_available() and is_git_repo(project_dir)


def _sanitize_name(name: str) -> str:
    """Replace filesystem-hostile characters with underscores.

    Worktree directories are named after job names, which can contain matrix
    labels like ``build: [OS=linux, PY=3.11]`` or slashes like ``test 1/3``.
    """
    cleaned = _INVALID_NAME_CHARS_RE.sub("_", name).strip("_")
    return cleaned or "job"


def worktree_root(project_dir: Path) -> Path:
    """Directory that holds all per-job worktrees."""
    return project_dir / _WORKTREES_SUBDIR


def worktree_path_for(project_dir: Path, name: str) -> Path:
    """Compute the worktree directory for a job name (without creating it)."""
    return worktree_root(project_dir) / _sanitize_name(name)


def create_worktree(project_dir: Path, name: str) -> WorktreeContext:
    """Create a detached-HEAD worktree for *project_dir* at the configured path.

    The worktree is created with ``--detach`` so we do not pollute the branch
    namespace.  If a worktree already exists at the target path (left over from
    a previous crashed run) it is removed first.
    """
    target = worktree_path_for(project_dir, name)
    target.parent.mkdir(parents=True, exist_ok=True)

    # If something is already there, tear it down — a stale entry would make
    # `git worktree add` fail.  We try git first (so the metadata is cleaned),
    # then fall back to a plain directory removal.
    if target.exists():
        _run_git(["worktree", "remove", "--force", str(target)], cwd=project_dir)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
    # Prune dangling metadata in case a previous run left orphans behind.
    _run_git(["worktree", "prune"], cwd=project_dir)

    result = _run_git(
        ["worktree", "add", "--detach", str(target)],
        cwd=project_dir,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git worktree add failed for {target}: {result.stderr.strip() or result.stdout.strip()}")
    return WorktreeContext(worktree_path=target, project_dir=project_dir)


def remove_worktree(ctx: WorktreeContext) -> None:
    """Tear down a worktree, ignoring the usual 'already gone' errors.

    We run ``git worktree remove --force`` first so the metadata under
    ``.git/worktrees`` is cleaned; then we ``shutil.rmtree`` as a belt-and-
    braces step in case git left artifacts behind (happens occasionally on
    Windows when a subprocess still holds a handle).
    """
    _run_git(
        ["worktree", "remove", "--force", str(ctx.worktree_path)],
        cwd=ctx.project_dir,
    )
    if ctx.worktree_path.exists():
        shutil.rmtree(ctx.worktree_path, ignore_errors=True)


@contextmanager
def job_worktree(project_dir: Path, name: str) -> Iterator[Path]:
    """Context manager: create a worktree, yield its path, always remove it."""
    ctx = create_worktree(project_dir, name)
    try:
        yield ctx.worktree_path
    finally:
        remove_worktree(ctx)


def prune_worktrees(project_dir: Path) -> None:
    """Best-effort cleanup: run ``git worktree prune`` and remove the root dir.

    Called by ``bitrab folder clean``.  Safe to run when no worktrees exist.
    """
    if is_git_repo(project_dir):
        _run_git(["worktree", "prune"], cwd=project_dir)
    root = worktree_root(project_dir)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
