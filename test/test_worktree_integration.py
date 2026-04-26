"""End-to-end tests: parallel jobs under worktree isolation do not stomp on each other.

These tests launch a tiny pipeline with two concurrent jobs that each write to the
same filename, and assert both jobs succeed, their artifacts are collected
correctly, and the real project root is unchanged.
"""

from __future__ import annotations

import subprocess  # nosec
import textwrap
from pathlib import Path

import pytest

from bitrab.git_worktree import is_git_available
from bitrab.plan import LocalGitLabRunner

pytestmark = pytest.mark.skipif(not is_git_available(), reason="git binary not available")


def init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)  # nosec
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)  # nosec
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)  # nosec
    (path / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "seed.txt"], check=True)  # nosec
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "seed"], check=True)  # nosec


def write_ci(tmp_path: Path, content: str) -> Path:
    p = tmp_path / ".gitlab-ci.yml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def test_parallel_mutating_jobs_do_not_conflict(tmp_path: Path) -> None:
    """Two jobs writing to the same filename in parallel should both succeed.

    Without worktrees, one job would overwrite the other's file.  With worktrees
    each job gets its own checkout, so the writes are isolated and each job
    collects its own artifact.
    """
    init_repo(tmp_path)
    write_ci(
        tmp_path,
        """
        stages: [build]

        build_a:
          stage: build
          script:
            - echo "A" > shared.txt
          artifacts:
            paths: [shared.txt]

        build_b:
          stage: build
          script:
            - echo "B" > shared.txt
          artifacts:
            paths: [shared.txt]
        """,
    )

    runner = LocalGitLabRunner(base_path=tmp_path)
    runner.run_pipeline(
        config_path=tmp_path / ".gitlab-ci.yml",
        maximum_degree_of_parallelism=2,
    )

    # Both artifact copies should be present, each with the correct content.
    art_a = tmp_path / ".bitrab" / "artifacts" / "build_a" / "shared.txt"
    art_b = tmp_path / ".bitrab" / "artifacts" / "build_b" / "shared.txt"
    assert art_a.exists(), "build_a artifact missing — worktree isolation didn't run"
    assert art_b.exists(), "build_b artifact missing — worktree isolation didn't run"
    assert art_a.read_text(encoding="utf-8").strip() == "A"
    assert art_b.read_text(encoding="utf-8").strip() == "B"

    # The real project root should not have shared.txt — it only existed in the
    # ephemeral worktrees.
    assert not (tmp_path / "shared.txt").exists()


def test_worktree_root_cleaned_up_after_run(tmp_path: Path) -> None:
    """After the pipeline finishes, no worktree directories should remain."""
    init_repo(tmp_path)
    write_ci(
        tmp_path,
        """
        stages: [build]

        one:
          stage: build
          script: ["echo one"]
        two:
          stage: build
          script: ["echo two"]
        """,
    )

    runner = LocalGitLabRunner(base_path=tmp_path)
    runner.run_pipeline(
        config_path=tmp_path / ".gitlab-ci.yml",
        maximum_degree_of_parallelism=2,
    )

    wt_root = tmp_path / ".bitrab" / "worktrees"
    # Either the directory doesn't exist, or it's empty. Both are fine.
    if wt_root.exists():
        assert not any(wt_root.iterdir()), f"worktree root not empty: {list(wt_root.iterdir())}"


def test_serial_mode_skips_worktrees_and_mutates_project(tmp_path: Path) -> None:
    """In --serial mode, jobs run in the real project dir so mutations persist.

    This is the intended behaviour for formatters / autofixers that need to
    modify the actual working copy.
    """
    init_repo(tmp_path)
    write_ci(
        tmp_path,
        """
        stages: [format]

        formatter:
          stage: format
          script:
            - echo "formatted" > output.txt
        """,
    )

    runner = LocalGitLabRunner(base_path=tmp_path)
    runner.run_pipeline(
        config_path=tmp_path / ".gitlab-ci.yml",
        serial=True,
    )

    # Serial mode writes directly into the project root — that's the whole point.
    assert (tmp_path / "output.txt").exists()
    assert (tmp_path / "output.txt").read_text(encoding="utf-8").strip() == "formatted"


def test_no_worktrees_flag_disables_isolation(tmp_path: Path) -> None:
    """With --no-worktrees, parallel jobs run in the real project dir."""
    init_repo(tmp_path)
    write_ci(
        tmp_path,
        """
        stages: [build]

        solo:
          stage: build
          script:
            - echo "no-isolation" > out.txt
        """,
    )

    runner = LocalGitLabRunner(base_path=tmp_path)
    runner.run_pipeline(
        config_path=tmp_path / ".gitlab-ci.yml",
        use_worktrees=False,
    )

    # Without worktrees the file lands in the project dir.
    assert (tmp_path / "out.txt").exists()


def test_worktree_mode_falls_back_gracefully_outside_git(tmp_path: Path) -> None:
    """Project not a git repo → worktree flag is ignored, job runs in project dir."""
    # No init_repo call — this path is not a git repo.
    write_ci(
        tmp_path,
        """
        stages: [build]

        solo:
          stage: build
          script:
            - echo "plain" > plain.txt
        """,
    )

    runner = LocalGitLabRunner(base_path=tmp_path)
    runner.run_pipeline(
        config_path=tmp_path / ".gitlab-ci.yml",
    )

    # Falls back to plain execution: file exists in the project dir.
    assert (tmp_path / "plain.txt").exists()


def test_configured_external_worktree_root_keeps_repo_clean(tmp_path: Path) -> None:
    init_repo(tmp_path)
    external_root = tmp_path.parent / f"{tmp_path.name}-worktrees"
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.bitrab]\nworktree_root = "{external_root.as_posix()}"\n',
        encoding="utf-8",
    )
    write_ci(
        tmp_path,
        """
        stages: [build]

        one:
          stage: build
          script: ["echo one"]
        two:
          stage: build
          script: ["echo two"]
        """,
    )

    runner = LocalGitLabRunner(base_path=tmp_path)
    runner.run_pipeline(
        config_path=tmp_path / ".gitlab-ci.yml",
        maximum_degree_of_parallelism=2,
    )

    assert not (tmp_path / ".bitrab" / "worktrees").exists()
    if external_root.exists():
        assert not any(external_root.iterdir()), f"external worktree root not empty: {list(external_root.iterdir())}"
