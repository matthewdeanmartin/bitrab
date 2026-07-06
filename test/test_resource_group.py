"""Tests for local resource_group serialization."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from bitrab.exceptions import JobExecutionError
from bitrab.execution.job import JobExecutor
from bitrab.execution.variables import VariableManager
from bitrab.models.pipeline import JobConfig
from bitrab.plan import LocalGitLabRunner
from bitrab.utils.filelock import FileLock


def executor(tmp_path: Path) -> JobExecutor:
    return JobExecutor(VariableManager({}, project_dir=tmp_path), project_dir=tmp_path)


def test_same_resource_group_serializes_jobs(tmp_path, monkeypatch):
    active = 0
    maximum = 0

    def fake_execute(_ctx):
        nonlocal active, maximum
        import time

        active += 1
        maximum = max(maximum, active)
        time.sleep(0.08)
        active -= 1

    first = executor(tmp_path)
    second = executor(tmp_path)
    monkeypatch.setattr(first, "_execute_with_context_unlocked", fake_execute)
    monkeypatch.setattr(second, "_execute_with_context_unlocked", fake_execute)
    job_a = JobConfig("a", resource_group="production")
    job_b = JobConfig("b", resource_group="production")

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(first.execute_job, ctx=first.build_context(job_a)),
            pool.submit(second.execute_job, ctx=second.build_context(job_b)),
        ]
        for future in futures:
            future.result()

    assert maximum == 1
    assert (tmp_path / ".bitrab" / "locks" / "production.lock").exists()


def test_different_resource_groups_do_not_serialize(tmp_path, monkeypatch):
    import threading
    import time

    barrier = threading.Barrier(2)
    reached = []

    def fake_execute(ctx):
        reached.append(ctx.job.name)
        barrier.wait(timeout=1)
        time.sleep(0.02)

    first = executor(tmp_path)
    second = executor(tmp_path)
    monkeypatch.setattr(first, "_execute_with_context_unlocked", fake_execute)
    monkeypatch.setattr(second, "_execute_with_context_unlocked", fake_execute)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(first.execute_job, ctx=first.build_context(JobConfig("a", resource_group="one")))
        two = pool.submit(second.execute_job, ctx=second.build_context(JobConfig("b", resource_group="two")))
        one.result()
        two.result()
    assert set(reached) == {"a", "b"}


def test_resource_group_wait_uses_job_timeout(tmp_path):
    worker = executor(tmp_path)
    job = JobConfig("blocked", resource_group="production", timeout=0.02)
    lock_path = tmp_path / ".bitrab" / "locks" / "production.lock"
    with FileLock(lock_path):
        with pytest.raises(JobExecutionError, match="timed out waiting"):
            worker.execute_job(ctx=worker.build_context(job))


def test_resource_group_serializes_real_parallel_pipeline(tmp_path):
    config = tmp_path / ".gitlab-ci.yml"
    config.write_text(
        """
stages: [deploy]
first:
  stage: deploy
  resource_group: production
  script:
    - echo first-start >> trace.txt
    - sleep 0.08
    - echo first-end >> trace.txt
second:
  stage: deploy
  resource_group: production
  script:
    - echo second-start >> trace.txt
    - sleep 0.08
    - echo second-end >> trace.txt
""".lstrip(),
        encoding="utf-8",
    )

    LocalGitLabRunner(tmp_path).run_pipeline(
        config_path=config,
        maximum_degree_of_parallelism=2,
        parallel_backend="thread",
        use_worktrees=False,
    )

    lines = (tmp_path / "trace.txt").read_text(encoding="utf-8").splitlines()
    assert lines in (
        ["first-start", "first-end", "second-start", "second-end"],
        ["second-start", "second-end", "first-start", "first-end"],
    )
