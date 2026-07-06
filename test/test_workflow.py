"""Tests for pipeline-level workflow:rules."""

from __future__ import annotations

import pytest

from bitrab.cli import cmd_run, create_parser, load_and_process_config
from bitrab.plan import LocalGitLabRunner


def test_workflow_when_never_skips_pipeline_with_distinct_result(tmp_path, capsys):
    config = tmp_path / ".gitlab-ci.yml"
    config.write_text(
        """
workflow:
  rules:
    - when: never
job:
  script: echo ran > ran.txt
""".lstrip(),
        encoding="utf-8",
    )

    completed = LocalGitLabRunner(tmp_path).run_pipeline(config_path=config, serial=True)

    assert completed is False
    assert not (tmp_path / "ran.txt").exists()
    assert "workflow:rules" in capsys.readouterr().out

    args = create_parser().parse_args(["-c", str(config), "run", "--no-tui", "--serial"])
    with pytest.raises(SystemExit) as exit_info:
        cmd_run(args)
    assert exit_info.value.code == 3


def test_workflow_variables_merge_into_pipeline_and_jobs(tmp_path):
    config = tmp_path / ".gitlab-ci.yml"
    config.write_text(
        """
variables:
  ORIGINAL: original
workflow:
  rules:
    - if: '$ENABLE == "yes"'
      variables:
        WORKFLOW_VALUE: merged
job:
  script: echo "$ORIGINAL-$WORKFLOW_VALUE" > value.txt
""".lstrip(),
        encoding="utf-8",
    )

    _raw, pipeline = load_and_process_config(config)
    assert pipeline.workflow_skipped is True

    config.write_text(
        config.read_text(encoding="utf-8").replace("ORIGINAL: original", "ENABLE: yes\n  ORIGINAL: original"),
        encoding="utf-8",
    )
    raw, pipeline = load_and_process_config(config)
    assert pipeline.workflow_skipped is False
    assert raw["variables"]["WORKFLOW_VALUE"] == "merged"
    assert pipeline.jobs[0].variables["WORKFLOW_VALUE"] == "merged"

    assert LocalGitLabRunner(tmp_path).run_pipeline(config_path=config, serial=True) is True
    assert (tmp_path / "value.txt").read_text(encoding="utf-8").strip() == "original-merged"


def test_workflow_first_match_wins(tmp_path):
    config = tmp_path / ".gitlab-ci.yml"
    config.write_text(
        """
workflow:
  rules:
    - if: '$MODE == "skip"'
      when: never
    - when: always
job:
  script: echo ran
""".lstrip(),
        encoding="utf-8",
    )
    _raw, pipeline = load_and_process_config(config)
    assert pipeline.workflow_skipped is False
