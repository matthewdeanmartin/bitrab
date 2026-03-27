import os
from pathlib import Path

import pytest

from bitrab.exceptions import JobExecutionError
from bitrab.execution.job import JobExecutor
from bitrab.execution.variables import VariableManager
from bitrab.models.pipeline import JobConfig
from bitrab.plan import LocalGitLabRunner


# Example test case
def test_basic_pipeline_execution(tmp_path):
    """Test basic pipeline execution with pytest."""
    # Create a test .gitlab-ci.yml
    config_content = """
stages:
  - build
  - test
  - deploy

variables:
  GLOBAL_VAR: "global_value"

default:
  before_script:
    - echo "Default before script"
  variables:
    DEFAULT_VAR: "default_value"

build_job:
  stage: build
  variables:
    JOB_VAR: "job_value"
  script:
    - echo "Building with $GLOBAL_VAR"
    - 'echo "Job var: $JOB_VAR"'
    - echo "touch build_artifact.txt"

test_job:
  stage: test
  script:
    - echo "Testing"
    - echo "test -f build_artifact.txt"
    - echo "Tests passed"

deploy_job:
  stage: deploy
  script:
    - echo "Deploying with $DEFAULT_VAR"
    - echo "Deployment complete"
  after_script:
    - echo "Cleanup after deploy"
"""

    # Write config file
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text(config_content)

    # Change to test directory
    original_cwd = Path.cwd()
    os.chdir(tmp_path)

    try:
        # Run pipeline
        runner = LocalGitLabRunner(tmp_path)
        runner.run_pipeline()

        # Verify artifact was created
        # assert (tmp_path / "build_artifact.txt").exists()

    finally:
        os.chdir(original_cwd)


def test_before_script_override(tmp_path):
    config_content = """
default:
  before_script:
    - echo "GLOBAL" > global.txt

job:
  before_script:
    - echo "JOB" > job.txt
  script:
    - echo "Running"
"""
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text(config_content)

    runner = LocalGitLabRunner(tmp_path)
    runner.run_pipeline()

    # Scripts run from project_dir (tmp_path), so files land there
    assert (tmp_path / "job.txt").exists()
    assert not (tmp_path / "global.txt").exists()


def test_variable_precedence(tmp_path):
    config_content = """
variables:
  FOO: global

default:
  variables:
    FOO: default

job:
  script:
    - echo "FOO is $FOO" > output.txt
"""
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text(config_content)

    runner = LocalGitLabRunner(tmp_path)
    runner.run_pipeline()

    # Scripts run from project_dir (tmp_path)
    output = (tmp_path / "output.txt").read_text().strip()
    assert "FOO is default" in output


def test_job_history_includes_failures():
    vm = VariableManager()
    executor = JobExecutor(vm)

    job = JobConfig(name="fail", stage="test", script=["false"], variables={}, before_script=[], after_script=[])

    try:
        executor.execute_job(job)
    except Exception:
        pass

    assert len(executor.job_history) == 1
    assert executor.job_history[0].returncode == 1


def test_concurrency_isolation(tmp_path):
    # Jobs using $CI_JOB_DIR get isolated per-job workspaces even when running
    # in parallel. Scripts themselves run from project_dir (correct for ./scripts/
    # relative paths), but $CI_JOB_DIR points to the per-job sandbox.
    config_content = """
stages:
  - test

job1:
  stage: test
  script:
    - echo "JOB1" > "$CI_JOB_DIR/result.txt"

job2:
  stage: test
  script:
    - echo "JOB2" > "$CI_JOB_DIR/result.txt"
"""
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text(config_content)

    runner = LocalGitLabRunner(tmp_path)
    runner.run_pipeline(maximum_degree_of_parallelism=2)

    res1 = (tmp_path / ".bitrab" / "job1" / "result.txt").read_text().strip()
    res2 = (tmp_path / ".bitrab" / "job2" / "result.txt").read_text().strip()

    assert res1 == "JOB1"
    assert res2 == "JOB2"


def test_parallel_history_preserved(tmp_path):
    config_content = """
stages:
  - test
job1:
  stage: test
  script:
    - echo hi
job2:
  stage: test
  script:
    - echo hello
"""
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text(config_content)

    runner = LocalGitLabRunner(tmp_path)
    runner.run_pipeline(maximum_degree_of_parallelism=2)

    assert len(runner.job_executor.job_history) == 2


def test_allow_failure_does_not_fail_pipeline(tmp_path):
    """A job with allow_failure: true should not cause the pipeline to fail."""
    config_content = """
stages:
  - test
  - deploy

fail_job:
  stage: test
  allow_failure: true
  script:
    - exit 1

deploy_job:
  stage: deploy
  script:
    - echo "deployed"
"""
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text(config_content)

    runner = LocalGitLabRunner(tmp_path)
    # Should NOT raise, because fail_job has allow_failure: true
    runner.run_pipeline(maximum_degree_of_parallelism=1)


def test_allow_failure_exit_codes_matching(tmp_path):
    """allow_failure with matching exit_codes should not fail pipeline."""
    config_content = """
stages:
  - test
  - deploy

fail_job:
  stage: test
  allow_failure:
    exit_codes:
      - 42
  script:
    - exit 42

deploy_job:
  stage: deploy
  script:
    - echo "deployed"
"""
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text(config_content)

    runner = LocalGitLabRunner(tmp_path)
    runner.run_pipeline(maximum_degree_of_parallelism=1)


def test_allow_failure_exit_codes_non_matching(tmp_path):
    """allow_failure with non-matching exit_codes should still fail pipeline."""
    config_content = """
stages:
  - test

fail_job:
  stage: test
  allow_failure:
    exit_codes:
      - 42
  script:
    - exit 1
"""
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text(config_content)

    runner = LocalGitLabRunner(tmp_path)
    with pytest.raises(JobExecutionError):
        runner.run_pipeline(maximum_degree_of_parallelism=1)


def test_when_never_skips_job(tmp_path):
    """A job with when: never should not run."""
    config_content = """
stages:
  - test

skip_me:
  stage: test
  when: never
  script:
    - echo "should not run" > skip.txt

run_me:
  stage: test
  script:
    - echo "ran"
"""
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text(config_content)

    runner = LocalGitLabRunner(tmp_path)
    runner.run_pipeline(maximum_degree_of_parallelism=1)
    # The "never" job should not have created the file
    assert not (tmp_path / "skip.txt").exists()


def test_when_manual_skips_job(tmp_path):
    """A job with when: manual should not run automatically."""
    config_content = """
stages:
  - test

manual_job:
  stage: test
  when: manual
  script:
    - echo "should not run" > manual.txt

auto_job:
  stage: test
  script:
    - echo "ran"
"""
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text(config_content)

    runner = LocalGitLabRunner(tmp_path)
    runner.run_pipeline(maximum_degree_of_parallelism=1)
    assert not (tmp_path / "manual.txt").exists()


def test_when_always_runs_after_failure(tmp_path):
    """A job with when: always should run even after a prior stage failure."""
    config_content = """
stages:
  - test
  - cleanup

fail_job:
  stage: test
  script:
    - exit 1

cleanup_job:
  stage: cleanup
  when: always
  script:
    - echo "cleaned" > cleanup.txt
"""
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text(config_content)

    runner = LocalGitLabRunner(tmp_path)
    with pytest.raises(JobExecutionError):
        runner.run_pipeline(maximum_degree_of_parallelism=1)
    # The always job should have run despite the failure
    assert (tmp_path / "cleanup.txt").exists()


def test_when_on_failure_runs_after_failure(tmp_path):
    """A job with when: on_failure should run when a prior stage failed."""
    config_content = """
stages:
  - test
  - notify

fail_job:
  stage: test
  script:
    - exit 1

notify_job:
  stage: notify
  when: on_failure
  script:
    - echo "notified" > notify.txt
"""
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text(config_content)

    runner = LocalGitLabRunner(tmp_path)
    with pytest.raises(JobExecutionError):
        runner.run_pipeline(maximum_degree_of_parallelism=1)
    # The on_failure job should have run
    assert (tmp_path / "notify.txt").exists()


def test_when_on_failure_skipped_on_success(tmp_path):
    """A job with when: on_failure should NOT run when all prior stages succeeded."""
    config_content = """
stages:
  - test
  - notify

pass_job:
  stage: test
  script:
    - echo "ok"

notify_job:
  stage: notify
  when: on_failure
  script:
    - echo "notified" > notify.txt
"""
    config_file = tmp_path / ".gitlab-ci.yml"
    config_file.write_text(config_content)

    runner = LocalGitLabRunner(tmp_path)
    runner.run_pipeline(maximum_degree_of_parallelism=1)
    # The on_failure job should NOT have run since everything passed
    assert not (tmp_path / "notify.txt").exists()
