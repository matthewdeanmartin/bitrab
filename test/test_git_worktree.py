"""Tests for bitrab.git_worktree — per-job git worktree isolation."""

from __future__ import annotations

import subprocess  # nosec
from pathlib import Path

import pytest

from bitrab.git_worktree import (
    can_use_worktrees,
    create_worktree,
    is_git_available,
    is_git_repo,
    is_repo_dirty,
    job_worktree,
    prune_worktrees,
    remove_worktree,
    worktree_path_for,
    worktree_root,
)

# Skip the whole module when git isn't installed — bitrab falls back gracefully,
# but the tests have nothing to assert against.
pytestmark = pytest.mark.skipif(not is_git_available(), reason="git binary not available")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def init_repo(path: Path) -> None:
    """Create a minimal git repo with one commit so worktree add can detach from HEAD."""
    subprocess.run(["git", "init", "-q", str(path)], check=True)  # nosec
    # Configure identity locally so the commit succeeds regardless of global config.
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)  # nosec
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)  # nosec
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)  # nosec
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)  # nosec


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_is_git_repo_true(tmp_path: Path) -> None:
    init_repo(tmp_path)
    assert is_git_repo(tmp_path)
    assert can_use_worktrees(tmp_path)


def test_is_git_repo_false(tmp_path: Path) -> None:
    assert not is_git_repo(tmp_path)
    assert not can_use_worktrees(tmp_path)


# ---------------------------------------------------------------------------
# Path computation
# ---------------------------------------------------------------------------


def test_worktree_path_sanitizes_name(tmp_path: Path) -> None:
    path = worktree_path_for(tmp_path, "build: [OS=linux, PY=3.11]")
    # Must not contain characters that break on Windows filesystems.
    for ch in '\\/:*?"<>|':
        assert ch not in path.name
    assert path.parent == worktree_root(tmp_path)


def test_worktree_path_slash_and_space(tmp_path: Path) -> None:
    path = worktree_path_for(tmp_path, "test 1/3")
    assert "/" not in path.name
    assert " " not in path.name


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_create_and_remove_worktree(tmp_path: Path) -> None:
    init_repo(tmp_path)
    ctx = create_worktree(tmp_path, "myjob")
    try:
        assert ctx.worktree_path.exists()
        # Worktree should have the same file content as the source repo.
        assert (ctx.worktree_path / "README.md").read_text(encoding="utf-8") == "hello\n"
    finally:
        remove_worktree(ctx)
    assert not ctx.worktree_path.exists()


def test_job_worktree_context_manager_cleans_up(tmp_path: Path) -> None:
    init_repo(tmp_path)
    captured: Path | None = None
    with job_worktree(tmp_path, "build") as wt:
        captured = wt
        assert wt.exists()
        (wt / "scratch.txt").write_text("mutation", encoding="utf-8")
    assert captured is not None
    assert not captured.exists()


def test_job_worktree_cleans_on_exception(tmp_path: Path) -> None:
    init_repo(tmp_path)
    captured: Path | None = None
    with pytest.raises(RuntimeError, match="boom"):
        with job_worktree(tmp_path, "flaky") as wt:
            captured = wt
            raise RuntimeError("boom")
    assert captured is not None
    assert not captured.exists()


def test_two_worktrees_are_isolated(tmp_path: Path) -> None:
    """Two concurrent worktrees must not see each other's writes — that's the whole point."""
    init_repo(tmp_path)
    ctx_a = create_worktree(tmp_path, "jobA")
    ctx_b = create_worktree(tmp_path, "jobB")
    try:
        (ctx_a.worktree_path / "a.txt").write_text("A", encoding="utf-8")
        (ctx_b.worktree_path / "b.txt").write_text("B", encoding="utf-8")

        assert (ctx_a.worktree_path / "a.txt").exists()
        assert not (ctx_a.worktree_path / "b.txt").exists()

        assert (ctx_b.worktree_path / "b.txt").exists()
        assert not (ctx_b.worktree_path / "a.txt").exists()

        # The real project root should also be untouched.
        assert not (tmp_path / "a.txt").exists()
        assert not (tmp_path / "b.txt").exists()
    finally:
        remove_worktree(ctx_a)
        remove_worktree(ctx_b)


def test_create_worktree_overwrites_stale_dir(tmp_path: Path) -> None:
    """If a previous crashed run left a directory behind, create_worktree recovers."""
    init_repo(tmp_path)
    target = worktree_path_for(tmp_path, "myjob")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir()
    (target / "stale.txt").write_text("old", encoding="utf-8")

    ctx = create_worktree(tmp_path, "myjob")
    try:
        # Stale file is gone; real checkout is in place.
        assert not (ctx.worktree_path / "stale.txt").exists()
        assert (ctx.worktree_path / "README.md").exists()
    finally:
        remove_worktree(ctx)


def test_prune_worktrees_is_safe_on_empty(tmp_path: Path) -> None:
    init_repo(tmp_path)
    # No worktrees yet — must not raise.
    prune_worktrees(tmp_path)
    assert not worktree_root(tmp_path).exists()


def test_prune_worktrees_removes_root(tmp_path: Path) -> None:
    init_repo(tmp_path)
    root = worktree_root(tmp_path)
    root.mkdir(parents=True)
    (root / "orphan").mkdir()
    prune_worktrees(tmp_path)
    assert not root.exists()


# ---------------------------------------------------------------------------
# is_repo_dirty
# ---------------------------------------------------------------------------


def test_is_repo_dirty_clean(tmp_path: Path) -> None:
    init_repo(tmp_path)
    assert not is_repo_dirty(tmp_path)


def test_is_repo_dirty_uncommitted_change(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "README.md").write_text("changed\n", encoding="utf-8")
    assert is_repo_dirty(tmp_path)


def test_is_repo_dirty_untracked_file(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "new_file.txt").write_text("untracked\n", encoding="utf-8")
    assert is_repo_dirty(tmp_path)


def test_is_repo_dirty_non_repo(tmp_path: Path) -> None:
    # Returns False (not True) outside a git repo — no reason to block the run.
    assert not is_repo_dirty(tmp_path)


# ---------------------------------------------------------------------------
# Non-git-repo fallback
# ---------------------------------------------------------------------------


def test_create_worktree_errors_outside_git_repo(tmp_path: Path) -> None:
    # create_worktree should fail loudly if called outside a git repo — callers
    # are expected to guard with can_use_worktrees() first.
    with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
        create_worktree(tmp_path, "nope")
