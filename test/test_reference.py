"""Tests for GitLab !reference resolution."""

from __future__ import annotations

import pytest

from bitrab.config.loader import ConfigurationLoader
from bitrab.exceptions import GitlabRunnerError
from bitrab.plan import LocalGitLabRunner, PipelineProcessor


def load(tmp_path, content: str):
    config = tmp_path / ".gitlab-ci.yml"
    config.write_text(content, encoding="utf-8")
    return ConfigurationLoader(tmp_path).load_config(config)


def test_reference_splices_script_list_and_yields_scalar(tmp_path):
    raw = load(
        tmp_path,
        """
.template:
  script: [echo setup, echo test]
  variables:
    IMPORTANT: yes
job:
  script:
    - !reference [.template, script]
    - echo done
  variables:
    COPIED: !reference [.template, variables, IMPORTANT]
""",
    )
    assert raw["job"]["script"] == ["echo setup", "echo test", "echo done"]
    assert raw["job"]["variables"]["COPIED"] == "yes"


def test_reference_resolves_after_includes_merge_and_before_extends(tmp_path):
    (tmp_path / "template.yml").write_text(".shared:\n  script: [echo included]\n", encoding="utf-8")
    raw = load(
        tmp_path,
        """
include: template.yml
job:
  extends: .shared
  before_script: !reference [.shared, script]
""",
    )
    pipeline = PipelineProcessor().process_config(raw)
    job = pipeline.jobs[0]
    assert job.script == ["echo included"]
    assert job.before_script == ["echo included"]


def test_nested_reference_resolves(tmp_path):
    raw = load(
        tmp_path,
        """
.first:
  script: [echo first]
.second:
  script: !reference [.first, script]
job:
  script: !reference [.second, script]
""",
    )
    assert raw["job"]["script"] == ["echo first"]


def test_circular_reference_has_clear_error(tmp_path):
    with pytest.raises(GitlabRunnerError, match="Circular !reference"):
        load(
            tmp_path,
            """
.a:
  script: !reference [.b, script]
.b:
  script: !reference [.a, script]
""",
        )


def test_missing_reference_has_clear_error(tmp_path):
    with pytest.raises(GitlabRunnerError, match="points to a missing value"):
        load(tmp_path, "job:\n  script: !reference [.missing, script]\n")


def test_reference_pipeline_runs_end_to_end(tmp_path):
    config = tmp_path / ".gitlab-ci.yml"
    config.write_text(
        ".template:\n  script: [echo referenced > referenced.txt]\njob:\n  script: !reference [.template, script]\n",
        encoding="utf-8",
    )
    assert LocalGitLabRunner(tmp_path).run_pipeline(config_path=config, serial=True) is True
    assert (tmp_path / "referenced.txt").exists()
