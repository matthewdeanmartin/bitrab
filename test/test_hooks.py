"""Tests for the managed git pre-push hook."""

from __future__ import annotations

import os
import subprocess  # nosec
from pathlib import Path

import pytest

from bitrab.exceptions import GitlabRunnerError
from bitrab.hooks import END_MARKER, START_MARKER, install_pre_push_hook, pre_push_path, uninstall_pre_push_hook


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)  # nosec


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init")
    return tmp_path


def test_hook_installs_fires_skips_and_uninstalls(repo: Path, monkeypatch):
    bin_dir = repo / "bin"
    bin_dir.mkdir()
    log = repo / "hook.log"
    fake = bin_dir / "bitrab"
    fake.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{log.as_posix()}'\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))

    result = install_pre_push_hook(repo)
    content = result.path.read_text(encoding="utf-8")
    assert result.action == "installed"
    assert START_MARKER in content and END_MARKER in content

    git(repo, "hook", "run", "pre-push")
    assert log.read_text(encoding="utf-8").strip() == "run --changed --incremental --no-tui"

    monkeypatch.setenv("BITRAB_SKIP_HOOK", "1")
    git(repo, "hook", "run", "pre-push")
    assert log.read_text(encoding="utf-8").count("\n") == 1

    removed = uninstall_pre_push_hook(repo)
    assert removed.action == "removed"
    assert not result.path.exists()


def test_hook_chains_and_uninstall_preserves_foreign_shell_hook(repo: Path):
    path = pre_push_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    original = "#!/bin/sh\necho foreign\n"
    path.write_text(original, encoding="utf-8")

    installed = install_pre_push_hook(repo)
    assert installed.action == "chained"
    assert path.read_text(encoding="utf-8").startswith(original.rstrip())

    removed = uninstall_pre_push_hook(repo)
    assert removed.action == "unchained"
    assert path.read_text(encoding="utf-8") == original


def test_uninstall_preserves_code_added_after_bitrab_created_the_hook(repo: Path):
    path = install_pre_push_hook(repo).path
    path.write_text(path.read_text(encoding="utf-8") + "echo retained\n", encoding="utf-8")

    result = uninstall_pre_push_hook(repo)

    assert result.action == "unchained"
    assert path.read_text(encoding="utf-8") == "#!/bin/sh\necho retained\n"


def test_env_sh_shebang_is_recognized_as_shell(repo: Path):
    path = pre_push_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env sh\necho existing\n", encoding="utf-8")
    assert install_pre_push_hook(repo).action == "chained"


def test_hook_install_is_idempotent(repo: Path):
    first = install_pre_push_hook(repo)
    second = install_pre_push_hook(repo)
    assert first.path == second.path
    assert second.action == "unchanged"
    assert first.path.read_text(encoding="utf-8").count(START_MARKER) == 1


def test_hook_refuses_to_clobber_non_shell_hook(repo: Path):
    path = pre_push_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("print('foreign')\n", encoding="utf-8")

    with pytest.raises(GitlabRunnerError, match="non-shell"):
        install_pre_push_hook(repo)
