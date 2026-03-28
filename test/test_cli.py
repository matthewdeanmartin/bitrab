import argparse
import logging
from unittest.mock import patch

import pytest

from bitrab.cli import cmd_run, load_and_process_config, setup_logging


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

    with pytest.raises(Exception):  # ruamel.yaml.YAMLError or BitrabError
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
