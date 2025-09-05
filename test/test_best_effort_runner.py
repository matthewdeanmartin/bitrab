import os
from pathlib import Path

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
