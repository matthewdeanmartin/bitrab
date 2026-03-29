import argparse
import logging
import sys
from unittest.mock import patch

import pytest

from bitrab.cli import cmd_clean, cmd_graph, cmd_run, create_parser, load_and_process_config, main, setup_logging
from bitrab.exceptions import GitlabRunnerError


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
    args = argparse.Namespace(dry_run=True)

    cmd_clean(args)

    captured = capsys.readouterr()
    assert "Dry-run mode enabled" in captured.out
    assert "would remove build artifacts" in captured.out
