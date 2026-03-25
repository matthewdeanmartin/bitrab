import os
from pathlib import Path

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

    # Isolated dirs: .bitrab/job/job.txt
    job_dir = tmp_path / ".bitrab" / "job"
    assert (job_dir / "job.txt").exists()
    assert not (job_dir / "global.txt").exists()


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

    output = (tmp_path / ".bitrab" / "job" / "output.txt").read_text().strip()
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
    config_content = """
stages:
  - test

job1:
  stage: test
  script:
    - echo "JOB1" > shared.txt
    - sleep 1
    - cat shared.txt > result.txt

job2:
  stage: test
  script:
    - echo "JOB2" > shared.txt
    - sleep 1
    - cat shared.txt > result.txt
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
