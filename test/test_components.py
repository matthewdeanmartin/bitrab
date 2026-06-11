from __future__ import annotations

import pytest

from bitrab.config.loader import ConfigurationLoader
from bitrab.exceptions import GitlabRunnerError
from bitrab.plan import PipelineProcessor


def load_config(tmp_path, text: str, **kwargs):
    config = tmp_path / ".gitlab-ci.yml"
    config.write_text(text, encoding="utf-8")
    return ConfigurationLoader(base_path=tmp_path).load_config_with_inputs(config, **kwargs)


def test_multidoc_spec_header_is_stripped_from_root_config(tmp_path):
    config = load_config(
        tmp_path,
        """
spec:
  inputs:
    stage:
      default: test
---
stages: [test]
job:
  stage: test
  script:
    - echo hi
""",
    )

    assert "spec" not in config
    assert config["stages"] == ["test"]
    assert config["job"]["script"] == ["echo hi"]


def test_single_document_config_stays_unchanged(tmp_path):
    config = load_config(
        tmp_path,
        """
spec:
  inputs:
    stage:
      default: test
job:
  script:
    - echo hi
""",
    )

    assert "spec" in config
    assert "job" in config


def test_local_include_with_input_default_loads(tmp_path):
    component = tmp_path / "component.yml"
    component.write_text(
        """
spec:
  inputs:
    stage:
      default: test
---
component_job:
  stage: build
  script:
    - echo component
""",
        encoding="utf-8",
    )

    config = load_config(
        tmp_path,
        """
include:
  - local: component.yml
stages: [build]
""",
    )

    assert "spec" not in config
    assert config["component_job"]["script"] == ["echo component"]


def test_local_include_with_required_input_value_loads(tmp_path):
    component = tmp_path / "component.yml"
    component.write_text(
        """
spec:
  inputs:
    job-name:
      description: Name to use later during interpolation.
---
component_job:
  script:
    - echo component
""",
        encoding="utf-8",
    )

    config = load_config(
        tmp_path,
        """
include:
  - local: component.yml
    inputs:
      job-name: test-python
""",
    )

    assert "component_job" in config


def test_local_include_missing_required_input_fails(tmp_path):
    component = tmp_path / "component.yml"
    component.write_text(
        """
spec:
  inputs:
    job-name:
      description: Required input.
---
component_job:
  script:
    - echo component
""",
        encoding="utf-8",
    )

    with pytest.raises(GitlabRunnerError, match="missing required input 'job-name'"):
        load_config(
            tmp_path,
            """
include:
  - local: component.yml
""",
        )


def test_local_include_unknown_input_fails(tmp_path):
    component = tmp_path / "component.yml"
    component.write_text(
        """
spec:
  inputs:
    known:
      default: ok
---
component_job:
  script:
    - echo component
""",
        encoding="utf-8",
    )

    with pytest.raises(GitlabRunnerError, match="unknown input"):
        load_config(
            tmp_path,
            """
include:
  - local: component.yml
    inputs:
      typo: nope
""",
        )


def test_local_include_input_options_are_validated(tmp_path):
    component = tmp_path / "component.yml"
    component.write_text(
        """
spec:
  inputs:
    environment:
      default: dev
      options: [dev, staging]
---
component_job:
  script:
    - echo component
""",
        encoding="utf-8",
    )

    with pytest.raises(GitlabRunnerError, match="not one of"):
        load_config(
            tmp_path,
            """
include:
  - local: component.yml
    inputs:
      environment: prod
""",
        )


def test_input_defaults_and_values_must_be_scalars(tmp_path):
    component = tmp_path / "component.yml"
    component.write_text(
        """
spec:
  inputs:
    stage:
      default: [test]
---
component_job:
  script:
    - echo component
""",
        encoding="utf-8",
    )

    with pytest.raises(GitlabRunnerError, match="must be a scalar"):
        load_config(
            tmp_path,
            """
include:
  - local: component.yml
""",
        )


def test_unsupported_input_type_fails(tmp_path):
    component = tmp_path / "component.yml"
    component.write_text(
        """
spec:
  inputs:
    targets:
      type: array
---
component_job:
  script:
    - echo component
""",
        encoding="utf-8",
    )

    with pytest.raises(GitlabRunnerError, match="unsupported type"):
        load_config(
            tmp_path,
            """
include:
  - local: component.yml
    inputs:
      targets: test
""",
        )


def test_input_interpolation_replaces_nested_values(tmp_path):
    component = tmp_path / "component.yml"
    component.write_text(
        """
spec:
  inputs:
    stage:
      default: test
    message:
      default: hello
---
component_job:
  stage: $[[ inputs.stage ]]
  variables:
    MESSAGE: $[[ inputs.message ]]
  script:
    - echo $[[ inputs.message ]]
""",
        encoding="utf-8",
    )

    config = load_config(
        tmp_path,
        """
include:
  - local: component.yml
    inputs:
      stage: build
      message: hi
stages: [build]
""",
    )

    assert config["component_job"]["stage"] == "build"
    assert config["component_job"]["variables"]["MESSAGE"] == "hi"
    assert config["component_job"]["script"] == ["echo hi"]


def test_input_interpolation_replaces_mapping_keys(tmp_path):
    component = tmp_path / "component.yml"
    component.write_text(
        """
spec:
  inputs:
    job-name:
      default: component_job
---
$[[ inputs.job-name ]]:
  script:
    - echo component
""",
        encoding="utf-8",
    )

    config = load_config(
        tmp_path,
        """
include:
  - local: component.yml
    inputs:
      job-name: test-python
""",
    )

    assert "test-python" in config
    assert "component_job" not in config


def test_unknown_interpolation_input_reference_fails(tmp_path):
    component = tmp_path / "component.yml"
    component.write_text(
        """
spec:
  inputs:
    known:
      default: ok
---
component_job:
  script:
    - echo $[[ inputs.typo ]]
""",
        encoding="utf-8",
    )

    with pytest.raises(GitlabRunnerError, match="unknown input reference 'typo'"):
        load_config(
            tmp_path,
            """
include:
  - local: component.yml
""",
        )


def test_unsupported_interpolation_expression_fails(tmp_path):
    component = tmp_path / "component.yml"
    component.write_text(
        """
spec:
  inputs:
    known:
      default: ok
---
component_job:
  script:
    - echo $[[ variables.FOO ]]
""",
        encoding="utf-8",
    )

    with pytest.raises(GitlabRunnerError, match="unsupported interpolation expression"):
        load_config(
            tmp_path,
            """
include:
  - local: component.yml
""",
        )


def test_same_local_component_can_be_included_with_different_inputs(tmp_path):
    component = tmp_path / "component.yml"
    component.write_text(
        """
spec:
  inputs:
    job-name:
      description: Generated job name.
    message:
      default: component
---
$[[ inputs.job-name ]]:
  script:
    - echo $[[ inputs.message ]]
""",
        encoding="utf-8",
    )

    config = load_config(
        tmp_path,
        """
include:
  - local: component.yml
    inputs:
      job-name: first
      message: one
  - local: component.yml
    inputs:
      job-name: second
      message: two
""",
    )

    assert config["first"]["script"] == ["echo one"]
    assert config["second"]["script"] == ["echo two"]


def test_local_component_include_processes_into_pipeline_jobs(tmp_path):
    component = tmp_path / "component.yml"
    component.write_text(
        """
spec:
  inputs:
    job-name:
      default: generated
    stage:
      default: test
---
$[[ inputs.job-name ]]:
  stage: $[[ inputs.stage ]]
  script:
    - echo generated
""",
        encoding="utf-8",
    )

    raw = load_config(
        tmp_path,
        """
include:
  - local: component.yml
    inputs:
      job-name: lint
      stage: verify
stages: [verify]
""",
    )
    pipeline = PipelineProcessor().process_config(raw)

    assert [job.name for job in pipeline.jobs] == ["lint"]
    assert pipeline.jobs[0].stage == "verify"


def test_root_pipeline_input_default_is_interpolated(tmp_path):
    config = load_config(
        tmp_path,
        """
spec:
  inputs:
    job-name:
      default: generated
---
$[[ inputs.job-name ]]:
  script:
    - echo root
""",
    )

    assert "generated" in config
    assert config["generated"]["script"] == ["echo root"]


def test_root_pipeline_input_value_overrides_default(tmp_path):
    config = load_config(
        tmp_path,
        """
spec:
  inputs:
    job-name:
      default: generated
---
$[[ inputs.job-name ]]:
  script:
    - echo root
""",
        input_values={"job-name": "custom"},
    )

    assert "custom" in config
    assert "generated" not in config


def test_missing_required_root_pipeline_input_fails(tmp_path):
    with pytest.raises(GitlabRunnerError, match="missing required input 'job-name'"):
        load_config(
            tmp_path,
            """
spec:
  inputs:
    job-name:
      description: Required generated job name.
---
$[[ inputs.job-name ]]:
  script:
    - echo root
""",
        )


def test_root_pipeline_input_prompt_supplies_missing_value(tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "prompted")

    config = load_config(
        tmp_path,
        """
spec:
  inputs:
    job-name:
      description: Required generated job name.
---
$[[ inputs.job-name ]]:
  script:
    - echo root
""",
        prompt_missing_inputs=True,
    )

    assert "prompted" in config


def test_root_pipeline_input_can_feed_include_inputs(tmp_path):
    component = tmp_path / "component.yml"
    component.write_text(
        """
spec:
  inputs:
    job-name:
      description: Generated job name.
---
$[[ inputs.job-name ]]:
  script:
    - echo component
""",
        encoding="utf-8",
    )

    config = load_config(
        tmp_path,
        """
spec:
  inputs:
    job-name:
      default: from-root
---
include:
  - local: component.yml
    inputs:
      job-name: $[[ inputs.job-name ]]
""",
    )

    assert "from-root" in config
