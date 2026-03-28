"""Tests for FEATURE-4: ``needs:`` keyword and DAG execution."""

from graphlib import CycleError

import pytest

from bitrab.exceptions import JobExecutionError
from bitrab.plan import LocalGitLabRunner


def test_basic_needs_ordering(tmp_path):
    """Jobs with needs: run after their dependencies, ignoring stage order."""
    config_content = """\
stages:
  - build
  - test

build_job:
  stage: build
  script:
    - echo "built" > built.txt

test_job:
  stage: test
  needs:
    - build_job
  script:
    - cat built.txt
    - echo "tested" > tested.txt
"""
    (tmp_path / ".gitlab-ci.yml").write_text(config_content)
    runner = LocalGitLabRunner(tmp_path)
    runner.run_pipeline(maximum_degree_of_parallelism=1)

    assert (tmp_path / "built.txt").exists()
    assert (tmp_path / "tested.txt").exists()


def test_needs_empty_list_falls_back_to_stages(tmp_path):
    """Jobs with needs: [] (empty) have no dependencies and can run immediately."""
    config_content = """\
stages:
  - build
  - test

build_job:
  stage: build
  needs: []
  script:
    - echo "built" > built.txt

test_job:
  stage: test
  script:
    - echo "tested" > tested.txt
"""
    (tmp_path / ".gitlab-ci.yml").write_text(config_content)
    runner = LocalGitLabRunner(tmp_path)
    runner.run_pipeline(maximum_degree_of_parallelism=1)

    assert (tmp_path / "built.txt").exists()
    assert (tmp_path / "tested.txt").exists()


def test_no_needs_uses_stage_execution(tmp_path):
    """When no job declares needs:, stage-based execution is used (unchanged)."""
    config_content = """\
stages:
  - build
  - test

build_job:
  stage: build
  script:
    - echo "built" > built.txt

test_job:
  stage: test
  script:
    - echo "tested" > tested.txt
"""
    (tmp_path / ".gitlab-ci.yml").write_text(config_content)
    runner = LocalGitLabRunner(tmp_path)
    runner.run_pipeline(maximum_degree_of_parallelism=1)

    assert (tmp_path / "built.txt").exists()
    assert (tmp_path / "tested.txt").exists()


def test_cycle_detection(tmp_path):
    """Cyclic needs: dependencies should raise CycleError."""
    config_content = """\
stages:
  - test

job_a:
  stage: test
  needs:
    - job_b
  script:
    - echo "a"

job_b:
  stage: test
  needs:
    - job_a
  script:
    - echo "b"
"""
    (tmp_path / ".gitlab-ci.yml").write_text(config_content)
    runner = LocalGitLabRunner(tmp_path)
    with pytest.raises(CycleError):
        runner.run_pipeline(maximum_degree_of_parallelism=1)


def test_mixed_mode_needs_and_stages(tmp_path):
    """Mix of jobs with and without needs: — needs jobs bypass stages, others follow stage order."""
    config_content = """\
stages:
  - build
  - test
  - deploy

compile:
  stage: build
  script:
    - echo "compiled" > compiled.txt

lint:
  stage: build
  script:
    - echo "linted" > linted.txt

unit_test:
  stage: test
  needs:
    - compile
  script:
    - cat compiled.txt
    - echo "unit tested" > unit.txt

integration_test:
  stage: test
  script:
    - echo "integration" > integration.txt

deploy:
  stage: deploy
  needs:
    - unit_test
    - integration_test
  script:
    - echo "deployed" > deployed.txt
"""
    (tmp_path / ".gitlab-ci.yml").write_text(config_content)
    runner = LocalGitLabRunner(tmp_path)
    runner.run_pipeline(maximum_degree_of_parallelism=1)

    assert (tmp_path / "compiled.txt").exists()
    assert (tmp_path / "linted.txt").exists()
    assert (tmp_path / "unit.txt").exists()
    assert (tmp_path / "integration.txt").exists()
    assert (tmp_path / "deployed.txt").exists()


def test_dag_parallel_execution(tmp_path):
    """Independent DAG jobs can run in parallel."""
    config_content = """\
stages:
  - build
  - test

build_a:
  stage: build
  script:
    - echo "A" > "$CI_JOB_DIR/result.txt"

build_b:
  stage: build
  script:
    - echo "B" > "$CI_JOB_DIR/result.txt"

test_all:
  stage: test
  needs:
    - build_a
    - build_b
  script:
    - echo "tested" > tested.txt
"""
    (tmp_path / ".gitlab-ci.yml").write_text(config_content)
    runner = LocalGitLabRunner(tmp_path)
    runner.run_pipeline(maximum_degree_of_parallelism=2)

    res_a = (tmp_path / ".bitrab" / "build_a" / "result.txt").read_text().strip()
    res_b = (tmp_path / ".bitrab" / "build_b" / "result.txt").read_text().strip()
    assert res_a == "A"
    assert res_b == "B"
    assert (tmp_path / "tested.txt").exists()


def test_dag_needs_with_allow_failure(tmp_path):
    """A job that needs: an allow_failure job should still run when the dep fails."""
    config_content = """\
stages:
  - build
  - test

flaky:
  stage: build
  allow_failure: true
  script:
    - exit 1

test_job:
  stage: test
  needs:
    - flaky
  when: always
  script:
    - echo "tested" > tested.txt
"""
    (tmp_path / ".gitlab-ci.yml").write_text(config_content)
    runner = LocalGitLabRunner(tmp_path)
    # Should not raise — flaky is allow_failure, and test_job uses when: always
    runner.run_pipeline(maximum_degree_of_parallelism=1)
    assert (tmp_path / "tested.txt").exists()


def test_dag_needs_dependency_failure_blocks_downstream(tmp_path):
    """When a dependency hard-fails, downstream on_success jobs should not run."""
    config_content = """\
stages:
  - build
  - test

build_job:
  stage: build
  script:
    - exit 1

test_job:
  stage: test
  needs:
    - build_job
  script:
    - echo "should not run" > tested.txt
"""
    (tmp_path / ".gitlab-ci.yml").write_text(config_content)
    runner = LocalGitLabRunner(tmp_path)
    with pytest.raises(JobExecutionError):
        runner.run_pipeline(maximum_degree_of_parallelism=1)
    assert not (tmp_path / "tested.txt").exists()


def test_dag_when_never_skips(tmp_path):
    """A job with when: never should not run even in DAG mode."""
    config_content = """\
stages:
  - test

skip_me:
  stage: test
  needs: []
  when: never
  script:
    - echo "bad" > skip.txt

run_me:
  stage: test
  needs: []
  script:
    - echo "good" > run.txt
"""
    (tmp_path / ".gitlab-ci.yml").write_text(config_content)
    runner = LocalGitLabRunner(tmp_path)
    runner.run_pipeline(maximum_degree_of_parallelism=1)

    assert not (tmp_path / "skip.txt").exists()
    assert (tmp_path / "run.txt").exists()


def test_dag_when_on_failure_after_dep_fails(tmp_path):
    """A job with when: on_failure should run after a dependency fails."""
    config_content = """\
stages:
  - build
  - notify

build_job:
  stage: build
  script:
    - exit 1

notify_job:
  stage: notify
  needs:
    - build_job
  when: on_failure
  script:
    - echo "notified" > notify.txt
"""
    (tmp_path / ".gitlab-ci.yml").write_text(config_content)
    runner = LocalGitLabRunner(tmp_path)
    with pytest.raises(JobExecutionError):
        runner.run_pipeline(maximum_degree_of_parallelism=1)
    assert (tmp_path / "notify.txt").exists()


def test_dag_needs_dict_form(tmp_path):
    """needs: [{job: name}] dict form should be parsed correctly."""
    config_content = """\
stages:
  - build
  - test

build_job:
  stage: build
  script:
    - echo "built" > built.txt

test_job:
  stage: test
  needs:
    - job: build_job
  script:
    - cat built.txt
    - echo "tested" > tested.txt
"""
    (tmp_path / ".gitlab-ci.yml").write_text(config_content)
    runner = LocalGitLabRunner(tmp_path)
    runner.run_pipeline(maximum_degree_of_parallelism=1)

    assert (tmp_path / "built.txt").exists()
    assert (tmp_path / "tested.txt").exists()


def test_dag_job_history_preserved(tmp_path):
    """Job history should be preserved in DAG mode."""
    config_content = """\
stages:
  - build
  - test

build_job:
  stage: build
  script:
    - echo "built"

test_job:
  stage: test
  needs:
    - build_job
  script:
    - echo "tested"
"""
    (tmp_path / ".gitlab-ci.yml").write_text(config_content)
    runner = LocalGitLabRunner(tmp_path)
    runner.run_pipeline(maximum_degree_of_parallelism=1)

    assert len(runner.job_executor.job_history) >= 2


def test_dag_diamond_dependency(tmp_path):
    """Diamond pattern: A -> B, A -> C, B -> D, C -> D should work."""
    config_content = """\
stages:
  - build
  - middle
  - final

job_a:
  stage: build
  script:
    - echo "A" > a.txt

job_b:
  stage: middle
  needs:
    - job_a
  script:
    - echo "B" > b.txt

job_c:
  stage: middle
  needs:
    - job_a
  script:
    - echo "C" > c.txt

job_d:
  stage: final
  needs:
    - job_b
    - job_c
  script:
    - echo "D" > d.txt
"""
    (tmp_path / ".gitlab-ci.yml").write_text(config_content)
    runner = LocalGitLabRunner(tmp_path)
    runner.run_pipeline(maximum_degree_of_parallelism=2)

    assert (tmp_path / "a.txt").exists()
    assert (tmp_path / "b.txt").exists()
    assert (tmp_path / "c.txt").exists()
    assert (tmp_path / "d.txt").exists()
