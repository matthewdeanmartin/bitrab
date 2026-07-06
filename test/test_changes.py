"""Tests for rules:changes baselines and changed-job selection."""

from __future__ import annotations

import logging
import subprocess  # nosec
from pathlib import Path

import pytest

from bitrab.changes import ChangeResolver, ChangeSet, changes_match, discover_changes, path_matches, select_changed_jobs
from bitrab.cli import cmd_install_hook, cmd_run, create_parser
from bitrab.config.rules import rule_matches
from bitrab.models.pipeline import JobConfig, PipelineConfig, RuleConfig
from bitrab.plan import LocalGitLabRunner, PipelineProcessor


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)  # nosec
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.email", "bitrab@example.test")
    git(tmp_path, "config", "user.name", "Bitrab Tests")
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('base')\n", encoding="utf-8")
    (tmp_path / "docs" / "index.md").write_text("base\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "base")
    return tmp_path


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("*.md", "README.md", True),
        ("*.md", "docs/README.md", False),
        ("docs/**/*", "docs/index.md", True),
        ("docs/**/*", "docs/guides/start.md", True),
        ("docs/**/*", "src/docs/index.md", False),
        ("docs/", "docs/index.md", False),
        ("docs/", "docs/guides/start.md", False),
        ("src/*.py", "src/app.py", True),
        ("src/*.py", "src/pkg/app.py", False),
        ("src/*.{rb,py,sh}", "src/app.py", True),
    ],
)
def test_gitlab_style_glob_semantics(pattern: str, path: str, expected: bool):
    assert path_matches(pattern, path) is expected


def test_discover_changes_unions_committed_staged_unstaged_and_untracked(repo: Path):
    git(repo, "switch", "-c", "feature")
    (repo / "committed.txt").write_text("committed\n", encoding="utf-8")
    git(repo, "add", "committed.txt")
    git(repo, "commit", "-m", "feature")
    (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
    git(repo, "add", "staged.txt")
    (repo / "src" / "app.py").write_text("print('dirty')\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    (repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (repo / "ignored.txt").write_text("ignored\n", encoding="utf-8")

    result = discover_changes(repo)

    assert result.evaluable is True
    assert {"committed.txt", "staged.txt", "src/app.py", "untracked.txt", ".gitignore"} <= result.files
    assert "ignored.txt" not in result.files


def test_explicit_baseline_is_honored(repo: Path):
    original = git(repo, "rev-parse", "HEAD")
    (repo / "later.txt").write_text("later\n", encoding="utf-8")
    git(repo, "add", "later.txt")
    git(repo, "commit", "-m", "later")

    result = discover_changes(repo, original)

    assert result.evaluable is True
    assert "later.txt" in result.files
    assert result.baseline == original


def test_changes_match_is_conservative_when_git_is_unevaluable(tmp_path: Path, caplog):
    resolver = ChangeResolver(tmp_path)
    with caplog.at_level(logging.WARNING):
        change_set = resolver.resolve()

    assert changes_match(["src/**/*"], change_set) is True
    assert "will match so jobs run safely" in caplog.text


def test_rule_compare_to_uses_override(repo: Path):
    baseline = git(repo, "rev-parse", "HEAD")
    (repo / "src" / "app.py").write_text("print('changed')\n", encoding="utf-8")
    rule = RuleConfig(changes=["src/**/*"], compare_to=baseline)

    assert rule_matches(rule, {}, project_dir=repo, change_resolver=ChangeResolver(repo)) is True


def test_processor_parses_bare_and_mapping_changes_forms():
    pipeline = PipelineProcessor().process_config(
        {
            "bare": {"script": "echo bare", "rules": [{"changes": ["src/**/*"]}]},
            "mapping": {
                "script": "echo mapping",
                "rules": [{"changes": {"paths": ["docs/**/*"], "compare_to": "main"}}],
            },
        }
    )

    assert pipeline.jobs[0].rules[0].changes == ["src/**/*"]
    assert pipeline.jobs[1].rules[0].changes == ["docs/**/*"]
    assert pipeline.jobs[1].rules[0].compare_to == "main"


def test_cli_parses_changed_baseline_and_hook_commands():
    run_args = create_parser().parse_args(["run", "--changed", "--changes-base", "origin/develop"])
    assert run_args.func is cmd_run
    assert run_args.changed is True
    assert run_args.changes_base == "origin/develop"

    hook_args = create_parser().parse_args(["install-hook", "--uninstall"])
    assert hook_args.func is cmd_install_hook
    assert hook_args.uninstall is True


def test_select_changed_jobs_includes_unknown_inputs_and_transitive_dependents():
    pipeline = PipelineConfig(
        jobs=[
            JobConfig("source", rules=[RuleConfig(changes=["src/**/*"])]),
            JobConfig("consumer", variables={"BITRAB_FINGERPRINT_PATHS": "docs/**/*"}, needs=["source"]),
            JobConfig("final", variables={"BITRAB_FINGERPRINT_PATHS": "other/**/*"}, needs=["consumer"]),
            JobConfig("unknown"),
            JobConfig("unrelated", rules=[RuleConfig(changes=["tests/**/*"])]),
        ]
    )
    change_set = ChangeSet(frozenset({"src/app.py"}), "main", True)

    assert select_changed_jobs(pipeline, change_set) == {"source", "consumer", "final", "unknown"}


def test_run_changed_executes_matching_job_and_needs_dependents(repo: Path):
    (repo / ".gitlab-ci.yml").write_text(
        """
stages: [test]
source:
  rules:
    - changes: [src/**/*]
  script: echo source > source.out
consumer:
  needs: [source]
  variables:
    BITRAB_FINGERPRINT_PATHS: docs/**/*
  script: echo consumer > consumer.out
unrelated:
  rules:
    - changes: [docs/**/*]
  script: echo unrelated > unrelated.out
""".lstrip(),
        encoding="utf-8",
    )
    git(repo, "add", ".gitlab-ci.yml")
    git(repo, "commit", "-m", "pipeline")
    (repo / "src" / "app.py").write_text("print('changed')\n", encoding="utf-8")

    LocalGitLabRunner(repo).run_pipeline(changed=True, serial=True, use_worktrees=False)

    assert (repo / "source.out").exists()
    assert (repo / "consumer.out").exists()
    assert not (repo / "unrelated.out").exists()
