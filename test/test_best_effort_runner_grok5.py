from pathlib import Path

import pytest
from ruamel.yaml import YAML

from bitrab.exceptions import JobExecutionError
from bitrab.plan import LocalGitLabRunner

yaml = YAML()


def write_yaml(path: Path, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)


# Simple passing test for basic functionality
def test_basic_job_execution(tmp_path: Path, capsys):
    config_path = tmp_path / ".gitlab-ci.yml"
    write_yaml(config_path, {"job": {"script": ["echo 'Hello, world!'"]}})

    runner = LocalGitLabRunner(base_path=tmp_path)
    runner.run_pipeline(config_path)

    captured = capsys.readouterr()
    assert "Hello, world!" in captured.out


# Bug 1: Scripts are executed line-by-line separately instead of as a single bash script.
# This breaks shell variable persistence across lines.
# Failing test: Expects no exception (as in real GitLab), but raises due to bug.
def test_multi_line_script_variable_persistence(tmp_path: Path):
    config_path = tmp_path / ".gitlab-ci.yml"
    write_yaml(config_path, {"job": {"script": ["foo=1", "test $foo = 1"]}})

    runner = LocalGitLabRunner(base_path=tmp_path)
    runner.run_pipeline(config_path)  # Should succeed if fixed, but fails with CalledProcessError


# Bug 2: Variable pre-substitution breaks script syntax if variable value contains special characters like ".
# Failing test: Expects no exception, but raises due to bash syntax error.
@pytest.mark.skip("Requires real parsing of bash,  I think.")
def test_variable_substitution_with_quotes(tmp_path: Path):
    config_path = tmp_path / ".gitlab-ci.yml"
    write_yaml(config_path, {"job": {"variables": {"VAR": 'hello"world'}, "script": ['echo "$VAR"']}})

    runner = LocalGitLabRunner(base_path=tmp_path)
    runner.run_pipeline(config_path)  # Should succeed if fixed, but fails with CalledProcessError


# Bug 3: Variable pre-substitution causes incorrect expansion when value contains $.
# Failing test: Expects '$UNIQUE_VAR_123' in output, but due to bug, it's expanded to empty string.
def test_variable_substitution_with_dollar_sign(tmp_path: Path, capsys):
    config_path = tmp_path / ".gitlab-ci.yml"
    write_yaml(config_path, {"job": {"variables": {"VAR": "$UNIQUE_VAR_123"}, "script": ['echo "$VAR"']}})

    runner = LocalGitLabRunner(base_path=tmp_path)
    runner.run_pipeline(config_path)

    captured = capsys.readouterr().out
    assert "$UNIQUE_VAR_123" in captured  # Fails due to bug, output is empty instead


# Bug 4: after_script does not run if main script fails.
# Failing test: Expects 'after' in output despite failure, but due to bug, it's not executed.
def test_after_script_runs_on_failure(tmp_path: Path, capsys):
    config_path = tmp_path / ".gitlab-ci.yml"
    write_yaml(config_path, {"job": {"script": ["false"], "after_script": ["echo after"]}})

    runner = LocalGitLabRunner(base_path=tmp_path)
    with pytest.raises(JobExecutionError):
        runner.run_pipeline(config_path)

    captured = capsys.readouterr().out
    assert "after" in captured  # Fails due to bug, after_script not run


# Bug 5: Includes are not processed recursively.
# Failing test: Expects job from nested include to run and output 'hello', but due to bug, no jobs are loaded.
def test_recursive_includes(tmp_path: Path, capsys):
    main_path = tmp_path / ".gitlab-ci.yml"
    local1_path = tmp_path / "local1.yml"
    local2_path = tmp_path / "local2.yml"

    write_yaml(main_path, {"include": "local1.yml"})
    write_yaml(local1_path, {"include": "local2.yml"})
    write_yaml(local2_path, {"job": {"script": ["echo hello"]}})

    runner = LocalGitLabRunner(base_path=tmp_path)
    runner.run_pipeline(main_path)

    captured = capsys.readouterr().out
    assert "hello" in captured  # Fails due to bug, job not loaded
