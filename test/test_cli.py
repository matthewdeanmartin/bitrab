import argparse
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bitrab.cli import (
    _ensure_config_dependencies,
    _ensure_runner_dependency,
    _ensure_validation_dependencies,
    _get_check_capabilities,
    _get_configuration_loader,
    _get_gitlab_ci_validator,
    _get_local_gitlab_runner,
    _get_pipeline_processor,
    cmd_clean,
    cmd_debug,
    cmd_folder,
    cmd_graph,
    cmd_lint,
    cmd_list,
    cmd_logs,
    cmd_run,
    cmd_validate,
    cmd_watch,
    create_parser,
    load_and_process_config,
    main,
    resolve_config_path,
    setup_logging,
)
from bitrab.exceptions import BitrabError, GitlabRunnerError


def test_setup_logging_quiet():
    with patch("logging.basicConfig") as mock_logging:
        setup_logging(verbose=False, quiet=True)
        mock_logging.assert_called_once_with(level=logging.ERROR, format="%(levelname)s: %(message)s")


def test_setup_logging_verbose():
    with patch("logging.basicConfig") as mock_logging:
        setup_logging(verbose=True, quiet=False)
        mock_logging.assert_called_once_with(level=logging.DEBUG, format="%(levelname)s: %(message)s")


def test_setup_logging_default():
    with patch("logging.basicConfig") as mock_logging:
        setup_logging(verbose=False, quiet=False)
        mock_logging.assert_called_once_with(level=logging.INFO, format="%(levelname)s: %(message)s")


def test_load_and_process_config_success(tmp_path):
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text("stages: [test]\njob: {script: [echo 1]}")

    raw, pipeline = load_and_process_config(config_file)
    assert "stages" in raw
    assert len(pipeline.jobs) == 1
    assert pipeline.jobs[0].name == "job"


def test_load_and_process_config_error(tmp_path):
    config_file = tmp_path / "bad.yml"
    config_file.write_text("invalid yaml: [")

    with pytest.raises(GitlabRunnerError):
        load_and_process_config(config_file)


def test_cmd_run_config_not_found(capsys):
    args = argparse.Namespace(config="nonexistent.yml", jobs=None)
    with pytest.raises(SystemExit) as e:
        cmd_run(args)
    assert e.value.code == 1
    captured = capsys.readouterr()
    assert "Configuration file not found" in captured.err


@patch("bitrab.cli.LocalGitLabRunner")
def test_cmd_run_success(mock_runner_class, tmp_path):
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text("stages: [test]")

    args = argparse.Namespace(
        config=str(config_file), jobs=None, stage=None, parallel=None, dry_run=False, verbose=False, quiet=False
    )

    mock_runner = mock_runner_class.return_value

    # Mock is_ci_mode and should_use_tui
    with patch("bitrab.tui.ci_mode.is_ci_mode", return_value=False):
        with patch("bitrab.tui.ci_mode.should_use_tui", return_value=False):
            cmd_run(args)
            assert mock_runner.run_pipeline.called


@patch("bitrab.cli.LocalGitLabRunner")
def test_cmd_run_dry_run_reports_preview_mode(mock_runner_class, tmp_path, capsys):
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text("stages: [test]")

    args = argparse.Namespace(
        config=str(config_file), jobs=None, stage=None, parallel=None, dry_run=True, verbose=False, quiet=False
    )

    mock_runner = mock_runner_class.return_value

    with patch("bitrab.tui.ci_mode.is_ci_mode", return_value=False):
        with patch("bitrab.tui.ci_mode.should_use_tui", return_value=False):
            cmd_run(args)

    captured = capsys.readouterr()
    assert "Dry-run mode enabled" in captured.out
    mock_runner.run_pipeline.assert_called_once()
    assert mock_runner.run_pipeline.call_args.kwargs["dry_run"] is True


def test_create_parser_parses_run_dry_run_flag():
    parser = create_parser()

    args = parser.parse_args(["run", "--dry-run"])

    assert args.command == "run"
    assert args.dry_run is True
    assert args.func is cmd_run


def test_create_parser_parses_graph_format_flag():
    parser = create_parser()

    args = parser.parse_args(["graph", "--format", "dot"])

    assert args.command == "graph"
    assert args.format == "dot"
    assert args.func is cmd_graph


def test_create_parser_graph_default_format():
    parser = create_parser()

    args = parser.parse_args(["graph"])

    assert args.format == "text"


def test_create_parser_parses_clean_dry_run_flag():
    parser = create_parser()

    args = parser.parse_args(["clean", "--dry-run"])

    assert args.command == "clean"
    assert args.dry_run is True
    assert args.func is cmd_clean


@patch("bitrab.cli.LocalGitLabRunner")
@patch("bitrab.cli.setup_logging")
def test_main_run_dry_run_dispatches_flag(mock_setup_logging, mock_runner_class, tmp_path, monkeypatch, capsys):
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text("stages: [test]")
    mock_runner = mock_runner_class.return_value

    monkeypatch.setattr(sys, "argv", ["bitrab", "-c", str(config_file), "run", "--dry-run"])

    with patch("bitrab.tui.ci_mode.is_ci_mode", return_value=False):
        with patch("bitrab.tui.ci_mode.should_use_tui", return_value=False):
            main()

    captured = capsys.readouterr()
    assert "Dry-run mode enabled" in captured.out
    mock_setup_logging.assert_called_once_with(False, False)
    mock_runner.run_pipeline.assert_called_once()
    assert mock_runner.run_pipeline.call_args.kwargs["dry_run"] is True


def test_cmd_graph_renders_text(capsys, tmp_path):
    ci_file = tmp_path / ".gitlab-ci.yml"
    ci_file.write_text("stages:\n  - test\njob1:\n  stage: test\n  script:\n    - echo hi\n")
    args = argparse.Namespace(config=str(ci_file), format="text")

    cmd_graph(args)

    captured = capsys.readouterr()
    assert "Stage: test" in captured.out
    assert "job1" in captured.out


def test_cmd_graph_renders_dot(capsys, tmp_path):
    ci_file = tmp_path / ".gitlab-ci.yml"
    ci_file.write_text("stages:\n  - test\njob1:\n  stage: test\n  script:\n    - echo hi\n")
    args = argparse.Namespace(config=str(ci_file), format="dot")

    cmd_graph(args)

    captured = capsys.readouterr()
    assert "digraph pipeline" in captured.out


def test_cmd_clean_dry_run_reports_preview(capsys):
    args = argparse.Namespace(dry_run=True, config=None, what="all")

    cmd_clean(args)

    captured = capsys.readouterr()
    # No .bitrab/ exists in cwd during test, so it just reports nothing to clean
    assert "nothing to clean" in captured.out or "Dry-run" in captured.out or "does not exist" in captured.out


# ---------------------------------------------------------------------------
# resolve_config_path
# ---------------------------------------------------------------------------


def test_resolve_config_path_explicit():
    path = resolve_config_path("my.yml")
    assert path == Path("my.yml")


def test_resolve_config_path_prefers_bitrab_ci(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".bitrab-ci.yml").touch()
    path = resolve_config_path(None)
    assert path == Path(".bitrab-ci.yml")


def test_resolve_config_path_warns_when_both_exist(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".bitrab-ci.yml").touch()
    (tmp_path / ".gitlab-ci.yml").touch()
    path = resolve_config_path(None)
    assert path == Path(".bitrab-ci.yml")
    assert ".bitrab-ci.yml" in capsys.readouterr().out


def test_resolve_config_path_falls_back_to_gitlab_ci(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = resolve_config_path(None)
    assert path == Path(".gitlab-ci.yml")


# ---------------------------------------------------------------------------
# Lazy-import helpers
# ---------------------------------------------------------------------------


def test_ensure_config_dependencies_idempotent():
    import bitrab.cli as cli_module

    original_loader = cli_module.ConfigurationLoader
    original_processor = cli_module.PipelineProcessor
    _ensure_config_dependencies()
    assert cli_module.ConfigurationLoader is not None
    assert cli_module.PipelineProcessor is not None
    # Calling again is safe (branches already populated)
    _ensure_config_dependencies()


def test_ensure_runner_dependency():
    import bitrab.cli as cli_module

    _ensure_runner_dependency()
    assert cli_module.LocalGitLabRunner is not None


def test_ensure_validation_dependencies():
    import bitrab.cli as cli_module

    _ensure_validation_dependencies()
    assert cli_module.GitLabCIValidator is not None
    assert cli_module.check_capabilities is not None


def test_get_helpers_return_non_none():
    assert _get_configuration_loader() is not None
    assert _get_pipeline_processor() is not None
    assert _get_local_gitlab_runner() is not None
    assert _get_gitlab_ci_validator() is not None
    assert _get_check_capabilities() is not None


# ---------------------------------------------------------------------------
# load_and_process_config error paths
# ---------------------------------------------------------------------------


def test_load_and_process_config_bitrab_error(tmp_path):
    config_file = tmp_path / "ci.yml"
    config_file.write_text("stages: [test]\n")

    with patch("bitrab.cli._get_configuration_loader") as mock_get:
        mock_loader = MagicMock()
        mock_loader.return_value.load_config.side_effect = BitrabError("boom")
        mock_get.return_value = mock_loader
        with pytest.raises(BitrabError):
            load_and_process_config(config_file)


def test_load_and_process_config_unexpected_error(tmp_path):
    config_file = tmp_path / "ci.yml"
    config_file.write_text("stages: [test]\n")

    with patch("bitrab.cli._get_configuration_loader") as mock_get:
        mock_loader = MagicMock()
        mock_loader.return_value.load_config.side_effect = RuntimeError("oops")
        mock_get.return_value = mock_loader
        with pytest.raises(RuntimeError):
            load_and_process_config(config_file)


# ---------------------------------------------------------------------------
# cmd_run — additional paths
# ---------------------------------------------------------------------------


@patch("bitrab.cli.LocalGitLabRunner")
def test_cmd_run_keyboard_interrupt(mock_runner_class, tmp_path, capsys):
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text("stages: [test]")

    mock_runner = mock_runner_class.return_value
    mock_runner.run_pipeline.side_effect = KeyboardInterrupt()

    args = argparse.Namespace(
        config=str(config_file),
        jobs=None,
        stage=None,
        parallel=None,
        dry_run=False,
        serial=False,
        no_worktrees=False,
        parallel_backend=None,
        no_tui=False,
    )

    with patch("bitrab.tui.ci_mode.is_ci_mode", return_value=True):
        with patch("bitrab.tui.ci_mode.should_use_tui", return_value=False):
            with pytest.raises(SystemExit) as exc:
                cmd_run(args)
    assert exc.value.code == 130
    assert "interrupted" in capsys.readouterr().err


@patch("bitrab.cli.LocalGitLabRunner")
def test_cmd_run_bitrab_error_reraises(mock_runner_class, tmp_path):
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text("stages: [test]")

    mock_runner = mock_runner_class.return_value
    mock_runner.run_pipeline.side_effect = BitrabError("bad")

    args = argparse.Namespace(
        config=str(config_file),
        jobs=None,
        stage=None,
        parallel=None,
        dry_run=False,
        serial=False,
        no_worktrees=False,
        parallel_backend=None,
        no_tui=False,
    )

    with patch("bitrab.tui.ci_mode.is_ci_mode", return_value=True):
        with patch("bitrab.tui.ci_mode.should_use_tui", return_value=False):
            with pytest.raises(BitrabError):
                cmd_run(args)


@patch("bitrab.cli.LocalGitLabRunner")
def test_cmd_run_unexpected_error_reraises(mock_runner_class, tmp_path):
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text("stages: [test]")

    mock_runner = mock_runner_class.return_value
    mock_runner.run_pipeline.side_effect = RuntimeError("unexpected")

    args = argparse.Namespace(
        config=str(config_file),
        jobs=None,
        stage=None,
        parallel=None,
        dry_run=False,
        serial=False,
        no_worktrees=False,
        parallel_backend=None,
        no_tui=False,
    )

    with patch("bitrab.tui.ci_mode.is_ci_mode", return_value=True):
        with patch("bitrab.tui.ci_mode.should_use_tui", return_value=False):
            with pytest.raises(RuntimeError):
                cmd_run(args)


@patch("bitrab.cli.LocalGitLabRunner")
def test_cmd_run_dirty_repo_serial_answer(mock_runner_class, tmp_path):
    """User picks 's' (serial) at the dirty-repo prompt."""
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text("stages: [test]")

    mock_runner = mock_runner_class.return_value

    args = argparse.Namespace(
        config=str(config_file),
        jobs=None,
        stage=None,
        parallel=None,
        dry_run=False,
        serial=False,
        no_worktrees=False,
        parallel_backend=None,
        no_tui=False,
    )

    with patch("bitrab.tui.ci_mode.is_ci_mode", return_value=False):
        with patch("bitrab.tui.ci_mode.should_use_tui", return_value=False):
            with patch("bitrab.git_worktree.is_repo_dirty", return_value=True):
                with patch("builtins.input", return_value="s"):
                    cmd_run(args)

    call_kwargs = mock_runner.run_pipeline.call_args.kwargs
    assert call_kwargs["serial"] is True


@patch("bitrab.cli.LocalGitLabRunner")
def test_cmd_run_dirty_repo_quit_answer(mock_runner_class, tmp_path):
    """User picks 'q' at the dirty-repo prompt → sys.exit(0)."""
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text("stages: [test]")

    args = argparse.Namespace(
        config=str(config_file),
        jobs=None,
        stage=None,
        parallel=None,
        dry_run=False,
        serial=False,
        no_worktrees=False,
        parallel_backend=None,
        no_tui=False,
    )

    with patch("bitrab.tui.ci_mode.is_ci_mode", return_value=False):
        with patch("bitrab.tui.ci_mode.should_use_tui", return_value=False):
            with patch("bitrab.git_worktree.is_repo_dirty", return_value=True):
                with patch("builtins.input", return_value="q"):
                    with pytest.raises(SystemExit) as exc:
                        cmd_run(args)
    assert exc.value.code == 0


@patch("bitrab.cli.LocalGitLabRunner")
def test_cmd_run_dirty_repo_parallel_answer(mock_runner_class, tmp_path):
    """User picks 'p' at the dirty-repo prompt → runs in parallel."""
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text("stages: [test]")

    mock_runner = mock_runner_class.return_value

    args = argparse.Namespace(
        config=str(config_file),
        jobs=None,
        stage=None,
        parallel=None,
        dry_run=False,
        serial=False,
        no_worktrees=False,
        parallel_backend=None,
        no_tui=False,
    )

    with patch("bitrab.tui.ci_mode.is_ci_mode", return_value=False):
        with patch("bitrab.tui.ci_mode.should_use_tui", return_value=False):
            with patch("bitrab.git_worktree.is_repo_dirty", return_value=True):
                with patch("builtins.input", return_value="p"):
                    cmd_run(args)

    assert mock_runner.run_pipeline.called


# ---------------------------------------------------------------------------
# cmd_list
# ---------------------------------------------------------------------------

SIMPLE_CI = "stages:\n  - test\njob1:\n  stage: test\n  script:\n    - echo hi\n"


def test_cmd_list_basic(capsys, tmp_path):
    ci_file = tmp_path / ".gitlab-ci.yml"
    ci_file.write_text(SIMPLE_CI)
    args = argparse.Namespace(config=str(ci_file))

    cmd_list(args)

    out = capsys.readouterr().out
    assert "job1" in out
    assert "test" in out


def test_cmd_list_config_not_found(capsys):
    args = argparse.Namespace(config="nonexistent.yml")
    with pytest.raises(SystemExit) as exc:
        cmd_list(args)
    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_cmd_list_parallel_int(capsys, tmp_path):
    ci_file = tmp_path / ".gitlab-ci.yml"
    ci_file.write_text(
        "stages:\n  - test\nbuild:\n  stage: test\n  parallel: 3\n  script:\n    - echo hi\n"
    )
    args = argparse.Namespace(config=str(ci_file))
    cmd_list(args)
    out = capsys.readouterr().out
    assert "parallel" in out


def test_cmd_list_parallel_matrix(capsys, tmp_path):
    ci_file = tmp_path / ".gitlab-ci.yml"
    ci_file.write_text(
        "stages:\n  - test\nbuild:\n  stage: test\n"
        "  parallel:\n    matrix:\n      - X: [1, 2]\n        Y: [a, b]\n"
        "  script:\n    - echo hi\n"
    )
    args = argparse.Namespace(config=str(ci_file))
    cmd_list(args)
    out = capsys.readouterr().out
    assert "matrix" in out


# ---------------------------------------------------------------------------
# cmd_validate
# ---------------------------------------------------------------------------


def _make_validate_args(config_path, output_json=False):
    return argparse.Namespace(config=str(config_path), output_json=output_json)


def test_cmd_validate_config_not_found(capsys):
    args = _make_validate_args("nonexistent.yml")
    with pytest.raises(SystemExit) as exc:
        cmd_validate(args)
    assert exc.value.code == 1


def test_cmd_validate_success(capsys, tmp_path):
    ci_file = tmp_path / ".gitlab-ci.yml"
    ci_file.write_text(SIMPLE_CI)
    args = _make_validate_args(ci_file)

    cmd_validate(args)

    out = capsys.readouterr().out
    assert "valid" in out.lower()


def test_cmd_validate_json_output(capsys, tmp_path):
    import json

    ci_file = tmp_path / ".gitlab-ci.yml"
    ci_file.write_text(SIMPLE_CI)
    args = _make_validate_args(ci_file, output_json=True)

    cmd_validate(args)

    out = capsys.readouterr().out
    data = json.loads(out)
    assert "jobs" in data
    assert "stages" in data


def test_cmd_validate_schema_failure(capsys, tmp_path):
    ci_file = tmp_path / ".gitlab-ci.yml"
    ci_file.write_text(SIMPLE_CI)
    args = _make_validate_args(ci_file)

    with patch("bitrab.cli._get_gitlab_ci_validator") as mock_get:
        mock_validator = MagicMock()
        mock_validator.return_value.validate_ci_config.return_value = (False, ["schema error"])
        mock_get.return_value = mock_validator
        with pytest.raises(SystemExit) as exc:
            cmd_validate(args)
    assert exc.value.code == 1
    assert "schema error" in capsys.readouterr().err


def test_cmd_validate_empty_pipeline(capsys, tmp_path):
    ci_file = tmp_path / ".gitlab-ci.yml"
    # A file that passes schema validation but has no jobs after processing
    ci_file.write_text("stages:\n  - test\n")
    args = _make_validate_args(ci_file)

    with patch("bitrab.cli._get_gitlab_ci_validator") as mock_get:
        mock_validator = MagicMock()
        mock_validator.return_value.validate_ci_config.return_value = (True, [])
        mock_get.return_value = mock_validator
        with pytest.raises(SystemExit) as exc:
            cmd_validate(args)
    assert exc.value.code == 1
    assert "No jobs" in capsys.readouterr().err


def test_cmd_validate_unexpected_exception(capsys, tmp_path):
    ci_file = tmp_path / ".gitlab-ci.yml"
    ci_file.write_text(SIMPLE_CI)
    args = _make_validate_args(ci_file)

    with patch("bitrab.cli._get_gitlab_ci_validator") as mock_get:
        mock_get.side_effect = RuntimeError("bang")
        with pytest.raises(SystemExit) as exc:
            cmd_validate(args)
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# cmd_lint
# ---------------------------------------------------------------------------


def test_cmd_lint_exits_1(capsys):
    args = argparse.Namespace()
    with pytest.raises(SystemExit) as exc:
        cmd_lint(args)
    assert exc.value.code == 1
    assert "not yet implemented" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# cmd_watch
# ---------------------------------------------------------------------------


def test_cmd_watch_config_not_found(capsys):
    args = argparse.Namespace(
        config="nonexistent.yml",
        parallel=None,
        dry_run=False,
        jobs=None,
        stage=None,
        parallel_backend=None,
        serial=False,
        no_worktrees=False,
    )
    with pytest.raises(SystemExit) as exc:
        cmd_watch(args)
    assert exc.value.code == 1


def test_cmd_watch_calls_run_watch(tmp_path):
    ci_file = tmp_path / ".gitlab-ci.yml"
    ci_file.write_text(SIMPLE_CI)

    args = argparse.Namespace(
        config=str(ci_file),
        parallel=None,
        dry_run=False,
        jobs=None,
        stage=None,
        parallel_backend=None,
        serial=False,
        no_worktrees=False,
    )

    with patch("bitrab.watch.run_watch") as mock_watch:
        cmd_watch(args)
        assert mock_watch.called


# ---------------------------------------------------------------------------
# cmd_debug
# ---------------------------------------------------------------------------


def test_cmd_debug_config_exists(capsys, tmp_path):
    ci_file = tmp_path / ".gitlab-ci.yml"
    ci_file.write_text(SIMPLE_CI)
    args = argparse.Namespace(config=str(ci_file))

    cmd_debug(args)

    out = capsys.readouterr().out
    assert "Config file" in out
    assert "Jobs found" in out


def test_cmd_debug_config_missing(capsys, tmp_path):
    args = argparse.Namespace(config=str(tmp_path / "missing.yml"))
    cmd_debug(args)
    out = capsys.readouterr().out
    assert "Config exists: False" in out


# ---------------------------------------------------------------------------
# cmd_clean — actual cleaning path
# ---------------------------------------------------------------------------


def test_cmd_clean_not_dry_run_no_bitrab_dir(capsys, tmp_path):
    args = argparse.Namespace(dry_run=False, config=str(tmp_path / "ci.yml"), what="all")
    cmd_clean(args)
    # Nothing to clean — should print cleaned 0 bytes or similar
    out = capsys.readouterr().out
    assert "does not exist" in out or "Cleaned" in out or "nothing" in out.lower()


def test_cmd_clean_dry_run_artifacts_only(capsys, tmp_path):
    args = argparse.Namespace(dry_run=True, config=str(tmp_path / "ci.yml"), what="artifacts")
    cmd_clean(args)
    # No .bitrab/ dir — reports nothing to clean
    out = capsys.readouterr().out
    assert out  # just confirm it didn't crash


def test_cmd_clean_all_with_bitrab_dir(capsys, tmp_path):
    bitrab_dir = tmp_path / ".bitrab"
    bitrab_dir.mkdir()
    (bitrab_dir / "artifact.txt").write_text("data")
    args = argparse.Namespace(dry_run=False, config=str(tmp_path / "ci.yml"), what="all")
    cmd_clean(args)
    out = capsys.readouterr().out
    assert "Cleaned" in out or "does not exist" in out


# ---------------------------------------------------------------------------
# cmd_logs
# ---------------------------------------------------------------------------


def test_cmd_logs_list_no_runs(capsys, tmp_path):
    args = argparse.Namespace(config=str(tmp_path / "ci.yml"), logs_cmd="list")
    cmd_logs(args)
    out = capsys.readouterr().out
    assert "No runs" in out


def test_cmd_logs_show_no_runs(capsys, tmp_path):
    args = argparse.Namespace(config=str(tmp_path / "ci.yml"), logs_cmd="show", run_id=None)
    cmd_logs(args)
    out = capsys.readouterr().out
    assert "No runs" in out


def test_cmd_logs_rm_all(capsys, tmp_path):
    args = argparse.Namespace(config=str(tmp_path / "ci.yml"), logs_cmd="rm", keep=0)
    cmd_logs(args)
    out = capsys.readouterr().out
    assert "Removed" in out or "freed" in out.lower() or out.strip() == "" or "logs" in out.lower()


def test_cmd_logs_rm_keep_n(capsys, tmp_path):
    args = argparse.Namespace(config=str(tmp_path / "ci.yml"), logs_cmd="rm", keep=5)
    cmd_logs(args)
    out = capsys.readouterr().out
    assert "Nothing to remove" in out or "Removed" in out


def test_cmd_logs_show_run_not_found(capsys, tmp_path):
    fake_run = MagicMock()
    fake_run.run_id = "run-abc"

    args = argparse.Namespace(config=str(tmp_path / "ci.yml"), logs_cmd="show", run_id="nonexistent")
    with patch("bitrab.folder.list_runs", return_value=[fake_run]):
        with pytest.raises(SystemExit) as exc:
            cmd_logs(args)
    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# cmd_folder
# ---------------------------------------------------------------------------


def test_cmd_folder_status(capsys, tmp_path):
    args = argparse.Namespace(config=str(tmp_path / "ci.yml"), folder_cmd="status")
    cmd_folder(args)
    out = capsys.readouterr().out
    assert ".bitrab" in out or "folder" in out.lower()


def test_cmd_folder_clean_delegates(capsys, tmp_path):
    args = argparse.Namespace(
        config=str(tmp_path / "ci.yml"), folder_cmd="clean", dry_run=True, what="all"
    )
    cmd_folder(args)
    # Should not crash; output is from cmd_clean
    capsys.readouterr()


# ---------------------------------------------------------------------------
# Parser — remaining subcommands
# ---------------------------------------------------------------------------


def test_create_parser_list_command():
    parser = create_parser()
    args = parser.parse_args(["list"])
    assert args.command == "list"
    assert args.func is cmd_list


def test_create_parser_validate_command():
    parser = create_parser()
    args = parser.parse_args(["validate"])
    assert args.command == "validate"
    assert args.output_json is False


def test_create_parser_validate_json_flag():
    parser = create_parser()
    args = parser.parse_args(["validate", "--json"])
    assert args.output_json is True


def test_create_parser_lint_command():
    parser = create_parser()
    args = parser.parse_args(["lint"])
    assert args.command == "lint"


def test_create_parser_debug_command():
    parser = create_parser()
    args = parser.parse_args(["debug"])
    assert args.command == "debug"


def test_create_parser_watch_command():
    parser = create_parser()
    args = parser.parse_args(["watch", "--dry-run"])
    assert args.command == "watch"
    assert args.dry_run is True


def test_create_parser_run_serial_flag():
    parser = create_parser()
    args = parser.parse_args(["run", "--serial"])
    assert args.serial is True


def test_create_parser_run_no_worktrees_flag():
    parser = create_parser()
    args = parser.parse_args(["run", "--no-worktrees"])
    assert args.no_worktrees is True


def test_create_parser_clean_what_artifacts():
    parser = create_parser()
    args = parser.parse_args(["clean", "--what", "artifacts"])
    assert args.what == "artifacts"


def test_create_parser_logs_list():
    parser = create_parser()
    args = parser.parse_args(["logs", "list"])
    assert args.logs_cmd == "list"


def test_create_parser_logs_show():
    parser = create_parser()
    args = parser.parse_args(["logs", "show", "abc"])
    assert args.logs_cmd == "show"
    assert args.run_id == "abc"


def test_create_parser_logs_rm_keep():
    parser = create_parser()
    args = parser.parse_args(["logs", "rm", "--keep", "3"])
    assert args.keep == 3


def test_create_parser_folder_status():
    parser = create_parser()
    args = parser.parse_args(["folder", "status"])
    assert args.folder_cmd == "status"


def test_create_parser_folder_clean():
    parser = create_parser()
    args = parser.parse_args(["folder", "clean", "--dry-run"])
    assert args.folder_cmd == "clean"
    assert args.dry_run is True


# ---------------------------------------------------------------------------
# main() — additional paths
# ---------------------------------------------------------------------------


def test_main_license_flag(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["bitrab", "--license"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    assert "MIT" in capsys.readouterr().out


def test_main_no_command_defaults_to_run(monkeypatch, tmp_path):
    ci_file = tmp_path / ".gitlab-ci.yml"
    ci_file.write_text(SIMPLE_CI)
    monkeypatch.setattr(sys, "argv", ["bitrab", "-c", str(ci_file)])

    with patch("bitrab.cli.LocalGitLabRunner") as mock_runner_class:
        with patch("bitrab.tui.ci_mode.is_ci_mode", return_value=True):
            with patch("bitrab.tui.ci_mode.should_use_tui", return_value=False):
                main()
        assert mock_runner_class.return_value.run_pipeline.called


def test_main_quiet_flag(monkeypatch, tmp_path):
    ci_file = tmp_path / ".gitlab-ci.yml"
    ci_file.write_text(SIMPLE_CI)
    monkeypatch.setattr(sys, "argv", ["bitrab", "-q", "-c", str(ci_file), "run"])

    with patch("bitrab.cli.LocalGitLabRunner"):
        with patch("bitrab.cli.setup_logging") as mock_log:
            with patch("bitrab.tui.ci_mode.is_ci_mode", return_value=True):
                with patch("bitrab.tui.ci_mode.should_use_tui", return_value=False):
                    main()
        mock_log.assert_called_once_with(False, True)


def test_main_verbose_flag(monkeypatch, tmp_path):
    ci_file = tmp_path / ".gitlab-ci.yml"
    ci_file.write_text(SIMPLE_CI)
    monkeypatch.setattr(sys, "argv", ["bitrab", "-v", "-c", str(ci_file), "run"])

    with patch("bitrab.cli.LocalGitLabRunner"):
        with patch("bitrab.cli.setup_logging") as mock_log:
            with patch("bitrab.tui.ci_mode.is_ci_mode", return_value=True):
                with patch("bitrab.tui.ci_mode.should_use_tui", return_value=False):
                    main()
        mock_log.assert_called_once_with(True, False)
