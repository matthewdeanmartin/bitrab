"""Shared stage-loop engine used by all pipeline execution modes.

The three execution modes (streaming, TUI, CI-file) differ only in:
  - How output is routed (stdout, queue, file)
  - How status is reported (print, Textual message, none)
  - Whether cancellation is supported

This module provides :class:`StagePipelineRunner` which implements the shared
stage-iteration, job-directory creation, serial/parallel dispatching, failure
propagation, and job-history merging.  Callers customise behaviour via a
:class:`PipelineCallbacks` protocol.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import re
import subprocess  # nosec
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any, Callable

from bitrab.execution.artifacts import collect_artifacts, inject_dependencies
from bitrab.execution.job import JobExecutor, JobRuntimeContext, RunResult
from bitrab.execution.shell import TextWriter
from bitrab.models.pipeline import JobConfig, PipelineConfig
from bitrab.mutation import MutationConfig, MutationSnapshot, ParallelBackendConfig

WorkerFunc = Callable[[JobConfig, JobExecutor, Path], list[RunResult]]

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class JobOutcome:
    """Result of running a single job."""

    job: JobConfig
    success: bool
    error: BaseException | None = None
    history: list[RunResult] = field(default_factory=list)
    allowed_failure: bool = False  # True if job failed but allow_failure was set


# ---------------------------------------------------------------------------
# Callbacks protocol
# ---------------------------------------------------------------------------


class PipelineCallbacks:
    """Override any of these hooks to customise pipeline behaviour.

    Every method has a no-op default so callers only need to override what they
    care about.
    """

    def on_pipeline_start(self, pipeline: PipelineConfig, max_workers: int) -> None:
        """Called once before any stages run."""

    def on_pipeline_complete(self, success: bool) -> None:
        """Called once after all stages finish (or after a failure stops the run)."""

    def on_stage_start(self, stage: str, jobs: list[JobConfig]) -> None:
        """Called at the beginning of each non-empty stage."""

    def on_stage_skip(self, stage: str) -> None:
        """Called when a stage has no jobs and is skipped."""

    def on_stage_complete(
        self,
        stage: str,
        outcomes: list[JobOutcome],
    ) -> None:
        """Called after all jobs in a stage have finished (or failed)."""

    def on_job_start(self, job: JobConfig) -> None:
        """Called just before a job begins executing."""

    def on_job_complete(self, outcome: JobOutcome) -> None:
        """Called when a single job finishes (success or failure)."""

    def is_cancelled(self) -> bool:
        """Return True to abort the pipeline before the next stage."""
        return False

    def make_output_writer(self, _job: JobConfig, _job_dir: Path) -> TextWriter | None:
        """Return an output writer for this job, or None for default (sys.stdout)."""
        return None

    def make_worker_args(self, _job: JobConfig, _job_dir: Path) -> dict[str, Any]:
        """Return extra kwargs passed to the parallel worker function.

        The returned dict is merged into the arguments of the module-level
        worker function.  Override this when you need to pass a queue, log
        path, or PID dict to the worker.
        """
        return {}

    def get_worker_func(self) -> WorkerFunc | None:
        """Return the module-level function to call in parallel workers.

        Must be picklable (module-level, not a closure).  Signature must be:
            func(job, executor, job_dir, **extra) -> list[RunResult]
        where **extra comes from :meth:`make_worker_args`.

        Return None to use the default worker.
        """
        return None

    def poll_during_parallel(self, futures: dict[Any, JobConfig]) -> None:
        """Called repeatedly while parallel futures are running.

        Use this to drain queues, update progress, etc.  The default
        implementation does nothing.  Implementations should be non-blocking
        or use a very short timeout.
        """

    def on_cancelled(self) -> None:
        """Called when the pipeline is aborted due to cancellation."""

    def on_pipeline_awaiting_manual(self) -> None:
        """Called when all automatically-runnable jobs have finished but one or
        more manual jobs remain.  The pipeline is considered successful at this
        point; the TUI should stay open in interactive use so the operator can
        trigger manual jobs, but tests may choose to close via
        ``close_on_completion``.
        """

    def enrich_context(self, ctx: JobRuntimeContext) -> JobRuntimeContext:
        """Optionally transform the context before it is passed to a worker.

        The default implementation returns *ctx* unchanged.  Override this to
        swap the output writer, inject extra env vars, etc.
        """
        return ctx


# ---------------------------------------------------------------------------
# Default (picklable) worker
# ---------------------------------------------------------------------------


def _is_failure_allowed(job: JobConfig, exc: BaseException) -> bool:
    """Check if the job's failure should be treated as a warning (not a hard failure)."""
    if not job.allow_failure:
        return False
    if not job.allow_failure_exit_codes:
        return True  # allow_failure: true with no exit_codes => all failures allowed
    # allow_failure with specific exit_codes: only allowed if the exit code matches
    if isinstance(exc, subprocess.CalledProcessError):
        return exc.returncode in job.allow_failure_exit_codes
    # For JobExecutionError, check the cause chain
    cause = exc.__cause__
    if isinstance(cause, subprocess.CalledProcessError):
        return cause.returncode in job.allow_failure_exit_codes
    return False


def _filter_jobs_by_when(jobs: list[JobConfig], prior_had_failure: bool) -> list[JobConfig]:
    """Filter jobs based on their ``when`` condition and prior pipeline state.

    - ``on_success`` (default): run if no prior hard failure
    - ``on_failure``: run only if a prior stage had a failure
    - ``always``: always run
    - ``manual``: skip (requires explicit ``--jobs`` selection — not yet wired)
    - ``never``: skip entirely
    """
    result = []
    for job in jobs:
        when = job.when
        if when in {"never", "manual"}:
            continue
        if when == "always":
            result.append(job)
        elif when == "on_failure":
            if prior_had_failure:
                result.append(job)
        else:  # on_success (default)
            if not prior_had_failure:
                result.append(job)
    return result


def _default_worker(
    job: JobConfig,
    executor: JobExecutor,
    job_dir: Path,
) -> list[RunResult]:
    """Module-level worker: run one job, return its history."""
    ctx = executor.build_context(job, job_dir=job_dir)
    executor.execute_job(ctx=ctx)
    return executor.job_history


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def sanitize_job_name(name: str) -> str:
    """Replace characters that are invalid in filenames with underscores."""
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def organize_jobs_by_stage(pipeline: PipelineConfig) -> dict[str, list[JobConfig]]:
    """Group pipeline jobs by their stage."""
    jobs_by_stage: dict[str, list[JobConfig]] = {}
    for job in pipeline.jobs:
        jobs_by_stage.setdefault(job.stage, []).append(job)
    return jobs_by_stage


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------


class StagePipelineRunner:
    """Execute a pipeline stage-by-stage with pluggable callbacks.

    This class contains all the logic that was previously duplicated across
    ``StageOrchestrator``, ``TUIOrchestrator.execute_pipeline_tui()``, and
    ``TUIOrchestrator.execute_pipeline_ci()``.
    """

    def __init__(
        self,
        job_executor: JobExecutor,
        callbacks: PipelineCallbacks | None = None,
        maximum_degree_of_parallelism: int | None = None,
        mp_ctx: Any = None,
        mutation_config: MutationConfig | None = None,
        parallel_backend: ParallelBackendConfig | None = None,
    ) -> None:
        self.job_executor = job_executor
        self.callbacks = callbacks or PipelineCallbacks()
        cpu_cnt = os.cpu_count() or 1
        self.maximum_degree_of_parallelism = (
            cpu_cnt if maximum_degree_of_parallelism is None else max(1, maximum_degree_of_parallelism)
        )
        if mp_ctx is None:
            if sys.platform == "win32":
                mp_ctx = mp.get_context("spawn")
            else:
                mp_ctx = mp.get_context("spawn")
        self._mp_ctx = mp_ctx
        self._mutation_config = mutation_config or MutationConfig()
        self._parallel_backend = parallel_backend or ParallelBackendConfig()
        # Tracks names of all jobs that have completed (for artifact injection)
        self._completed_jobs: list[str] = []

    def execute_pipeline(self, pipeline: PipelineConfig) -> None:
        """Run all stages sequentially; jobs within a stage run in parallel.

        If any job declares ``needs:``, automatically switches to DAG execution
        via :class:`DagPipelineRunner`.
        """
        if has_dag_jobs(pipeline):
            dag_runner = DagPipelineRunner(
                job_executor=self.job_executor,
                callbacks=self.callbacks,
                maximum_degree_of_parallelism=self.maximum_degree_of_parallelism,
                mp_ctx=self._mp_ctx,
                mutation_config=self._mutation_config,
                parallel_backend=self._parallel_backend,
            )
            dag_runner.execute_pipeline(pipeline)
            return

        cb = self.callbacks
        cb.on_pipeline_start(pipeline, self.maximum_degree_of_parallelism)

        jobs_by_stage = organize_jobs_by_stage(pipeline)
        success = True
        prior_had_failure = False
        first_error: BaseException | None = None
        has_manual_skipped = False

        try:
            for stage in pipeline.stages:
                if cb.is_cancelled():
                    cb.on_cancelled()
                    success = False
                    return

                all_stage_jobs = jobs_by_stage.get(stage, [])
                stage_jobs = _filter_jobs_by_when(all_stage_jobs, prior_had_failure)

                if not stage_jobs:
                    if any(j.when == "manual" for j in all_stage_jobs):
                        has_manual_skipped = True
                    cb.on_stage_skip(stage)
                    continue

                cb.on_stage_start(stage, stage_jobs)
                outcomes = self._run_stage(stage_jobs)
                cb.on_stage_complete(stage, outcomes)

                hard_failures = [o for o in outcomes if not o.success]
                if hard_failures:
                    prior_had_failure = True
                    success = False
                    if first_error is None:
                        first_error = hard_failures[0].error

                # Allowed failures count as "failure" for on_failure job filtering
                if any(o.allowed_failure for o in outcomes):
                    prior_had_failure = True

            if cb.is_cancelled():
                cb.on_cancelled()
                success = False

            if first_error is not None:
                raise first_error
        except BaseException:
            success = False
            raise
        finally:
            if has_manual_skipped and success:
                cb.on_pipeline_awaiting_manual()
            cb.on_pipeline_complete(success)

    def _run_stage(self, stage_jobs: list[JobConfig]) -> list[JobOutcome]:
        """Execute all jobs in a single stage, serial or parallel."""
        if self.maximum_degree_of_parallelism == 1 or len(stage_jobs) == 1:
            return self._run_stage_serial(stage_jobs)
        return self._run_stage_parallel(stage_jobs)

    def _run_stage_serial(self, stage_jobs: list[JobConfig]) -> list[JobOutcome]:
        """Run jobs one at a time in the calling process."""
        cb = self.callbacks
        outcomes: list[JobOutcome] = []

        for job in stage_jobs:
            job_dir = self._make_job_dir(job)
            cb.on_job_start(job)
            if not self.job_executor.dry_run:
                inject_dependencies(job, self.job_executor.project_dir, self._completed_jobs)
            writer = cb.make_output_writer(job, job_dir)
            ctx = self.job_executor.build_context(job, job_dir=job_dir, output_writer=writer)
            ctx = cb.enrich_context(ctx)
            succeeded = True

            snap: MutationSnapshot | None = None
            if self._mutation_config.enabled and not self.job_executor.dry_run:
                snap = MutationSnapshot(
                    project_dir=self.job_executor.project_dir,
                    config=self._mutation_config,
                )
                snap.take()

            try:
                self.job_executor.execute_job(ctx=ctx)
                outcome = JobOutcome(job=job, success=True, history=list(self.job_executor.job_history))
            except BaseException as exc:
                succeeded = False
                allowed = _is_failure_allowed(job, exc)
                outcome = JobOutcome(
                    job=job,
                    success=allowed,
                    error=exc,
                    history=list(self.job_executor.job_history),
                    allowed_failure=allowed,
                )
            finally:
                if not self.job_executor.dry_run:
                    collect_artifacts(job, self.job_executor.project_dir, succeeded)
                self._completed_jobs.append(job.name)

            if snap is not None:
                _report_mutations(job.name, snap, writer)

            outcomes.append(outcome)
            cb.on_job_complete(outcome)

            if not outcome.success:
                break  # stop stage on first hard failure

        return outcomes

    def _make_pool(self, max_workers: int):
        """Create the appropriate executor pool based on backend config."""
        if self._parallel_backend.backend == "thread":
            return ThreadPoolExecutor(max_workers=max_workers)
        return ProcessPoolExecutor(
            max_workers=max_workers,
            mp_context=self._mp_ctx,
        )

    def _run_stage_parallel(self, stage_jobs: list[JobConfig]) -> list[JobOutcome]:
        """Run jobs across processes/threads using the configured pool executor."""
        cb = self.callbacks
        outcomes: list[JobOutcome] = []

        _wf = cb.get_worker_func()
        worker_func: WorkerFunc = _wf if _wf is not None else _default_worker

        with self._make_pool(self.maximum_degree_of_parallelism) as pool:
            futures = {}
            for job in stage_jobs:
                job_dir = self._make_job_dir(job)
                cb.on_job_start(job)
                if not self.job_executor.dry_run:
                    inject_dependencies(job, self.job_executor.project_dir, self._completed_jobs)
                extra = cb.make_worker_args(job, job_dir)
                fut = pool.submit(worker_func, job, self.job_executor, job_dir, **extra)
                futures[fut] = job

            # Poll while futures are running (allows TUI queue draining etc.)
            pending = set(futures.keys())
            while pending:
                cb.poll_during_parallel(futures)
                done = {f for f in pending if f.done()}
                if not done:
                    # Brief sleep to avoid busy-waiting; poll_during_parallel
                    # may also use a short timeout internally.
                    time.sleep(0.05)
                    continue
                for fut in done:
                    pending.discard(fut)
                    job = futures[fut]
                    succeeded = True
                    try:
                        history = fut.result()
                        self.job_executor.job_history.extend(history)
                        outcome = JobOutcome(job=job, success=True, history=history)
                    except BaseException as exc:
                        succeeded = False
                        allowed = _is_failure_allowed(job, exc)
                        outcome = JobOutcome(
                            job=job,
                            success=allowed,
                            error=exc,
                            allowed_failure=allowed,
                        )
                    if not self.job_executor.dry_run:
                        collect_artifacts(job, self.job_executor.project_dir, succeeded)
                    self._completed_jobs.append(job.name)

                    outcomes.append(outcome)
                    cb.on_job_complete(outcome)

        return outcomes

    def _make_job_dir(self, job: JobConfig) -> Path:
        """Create and return the per-job directory."""
        job_dir = self.job_executor.project_dir / ".bitrab" / sanitize_job_name(job.name)
        if not self.job_executor.dry_run:
            job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir


# ---------------------------------------------------------------------------
# DAG helpers
# ---------------------------------------------------------------------------


def has_dag_jobs(pipeline: PipelineConfig) -> bool:
    """Return True if any job in the pipeline declares ``needs:``."""
    return any(job.needs for job in pipeline.jobs)


def _build_dag(pipeline: PipelineConfig) -> TopologicalSorter:
    """Build a TopologicalSorter from pipeline jobs.

    Mixed mode: jobs without ``needs:`` get synthetic dependencies on every job
    in all prior stages, preserving stage ordering.  Jobs with ``needs:`` only
    depend on the explicitly listed jobs (ignoring stages).
    """
    jobs_by_stage = organize_jobs_by_stage(pipeline)
    # Build a set of jobs in each stage, ordered by pipeline.stages
    prior_stage_jobs: list[str] = []

    ts: TopologicalSorter = TopologicalSorter()
    for stage in pipeline.stages:
        stage_jobs = jobs_by_stage.get(stage, [])
        for job in stage_jobs:
            if job.needs:
                # Explicit DAG dependencies — ignore stage ordering
                ts.add(job.name, *job.needs)
            else:
                # Stage-based ordering: depend on all jobs from prior stages
                if prior_stage_jobs:
                    ts.add(job.name, *prior_stage_jobs)
                else:
                    ts.add(job.name)
        # Accumulate prior stage jobs for next iteration
        prior_stage_jobs.extend(j.name for j in stage_jobs)

    return ts


# ---------------------------------------------------------------------------
# DAG Pipeline Runner
# ---------------------------------------------------------------------------


def _report_mutations(job_name: str, snap: MutationSnapshot, writer: Any) -> None:
    """Emit a warning for each unexpected mutation detected after a job ran."""
    from bitrab.console import safe_print

    changed = snap.mutations()
    if changed:
        _print = (lambda msg: safe_print(msg, file=writer)) if writer else safe_print
        _print(f"⚠️  [mutation] Job '{job_name}' modified {len(changed)} unexpected file(s):")
        for path in changed:
            _print(f"   • {path}")
        _print(
            "   If these are intentional, add the pattern(s) to "
            "[tool.bitrab.mutation] whitelist in pyproject.toml"
        )


class DagPipelineRunner:
    """Execute a pipeline using DAG scheduling based on ``needs:`` dependencies.

    Jobs become ready as soon as their dependencies complete, potentially
    ignoring stage boundaries.  Uses :class:`PipelineCallbacks` for the same
    customisation hooks as :class:`StagePipelineRunner`.
    """

    def __init__(
        self,
        job_executor: JobExecutor,
        callbacks: PipelineCallbacks | None = None,
        maximum_degree_of_parallelism: int | None = None,
        mp_ctx: Any = None,
        mutation_config: MutationConfig | None = None,
        parallel_backend: ParallelBackendConfig | None = None,
    ) -> None:
        self.job_executor = job_executor
        self.callbacks = callbacks or PipelineCallbacks()
        cpu_cnt = os.cpu_count() or 1
        self.maximum_degree_of_parallelism = (
            cpu_cnt if maximum_degree_of_parallelism is None else max(1, maximum_degree_of_parallelism)
        )
        if mp_ctx is None:
            mp_ctx = mp.get_context("spawn")
        self._mp_ctx = mp_ctx
        self._mutation_config = mutation_config or MutationConfig()
        self._parallel_backend = parallel_backend or ParallelBackendConfig()
        self._completed_jobs: list[str] = []

    def execute_pipeline(self, pipeline: PipelineConfig) -> None:
        """Run all jobs respecting DAG dependencies."""
        cb = self.callbacks
        cb.on_pipeline_start(pipeline, self.maximum_degree_of_parallelism)

        # Build the DAG (raises CycleError if cyclic)
        ts = _build_dag(pipeline)
        ts.prepare()

        # Index jobs by name for fast lookup
        job_map: dict[str, JobConfig] = {j.name: j for j in pipeline.jobs}
        all_outcomes: list[JobOutcome] = []
        success = True
        first_error: BaseException | None = None
        failed_jobs: set[str] = set()  # jobs that hard-failed

        try:
            while ts.is_active():
                if cb.is_cancelled():
                    cb.on_cancelled()
                    success = False
                    return

                ready_names = ts.get_ready()
                if not ready_names:
                    break

                # Filter by when-condition
                ready_jobs = []
                for name in ready_names:
                    job = job_map.get(name)
                    if job is None:
                        # Dependency named a job that doesn't exist — mark done and skip
                        ts.done(name)
                        continue
                    when = job.when
                    skip = False
                    if when in {"never", "manual"}:
                        skip = True
                    elif when == "on_failure":
                        if not failed_jobs:
                            skip = True
                    elif when == "always":
                        pass  # always runs
                    else:  # on_success
                        # Check if any dependency failed
                        if job.needs:
                            if any(dep in failed_jobs for dep in job.needs):
                                skip = True
                        elif failed_jobs:
                            skip = True

                    if skip:
                        ts.done(name)
                        continue
                    ready_jobs.append(job)

                if not ready_jobs:
                    continue

                # Notify stage start (use first job's stage as label)
                stages_in_batch = sorted({j.stage for j in ready_jobs})
                for stage in stages_in_batch:
                    stage_jobs = [j for j in ready_jobs if j.stage == stage]
                    cb.on_stage_start(stage, stage_jobs)

                # Execute ready jobs
                outcomes = self._run_batch(ready_jobs)
                all_outcomes.extend(outcomes)

                # Notify stage completion
                for stage in stages_in_batch:
                    stage_outcomes = [o for o in outcomes if o.job.stage == stage]
                    cb.on_stage_complete(stage, stage_outcomes)

                # Process outcomes
                for outcome in outcomes:
                    ts.done(outcome.job.name)
                    if not outcome.success:
                        failed_jobs.add(outcome.job.name)
                        success = False
                        if first_error is None:
                            first_error = outcome.error
                    if outcome.allowed_failure:
                        failed_jobs.add(outcome.job.name)

            if cb.is_cancelled():
                cb.on_cancelled()
                success = False

            if first_error is not None:
                raise first_error
        except CycleError:
            success = False
            raise
        except BaseException:
            success = False
            raise
        finally:
            cb.on_pipeline_complete(success)

    def _run_batch(self, jobs: list[JobConfig]) -> list[JobOutcome]:
        """Execute a batch of ready jobs, serial or parallel."""
        if self.maximum_degree_of_parallelism == 1 or len(jobs) == 1:
            return self._run_batch_serial(jobs)
        return self._run_batch_parallel(jobs)

    def _run_batch_serial(self, jobs: list[JobConfig]) -> list[JobOutcome]:
        """Run jobs one at a time."""
        cb = self.callbacks
        outcomes: list[JobOutcome] = []

        for job in jobs:
            job_dir = self._make_job_dir(job)
            cb.on_job_start(job)
            inject_dependencies(job, self.job_executor.project_dir, self._completed_jobs)
            writer = cb.make_output_writer(job, job_dir)
            ctx = self.job_executor.build_context(job, job_dir=job_dir, output_writer=writer)
            ctx = cb.enrich_context(ctx)
            succeeded = True

            snap: MutationSnapshot | None = None
            if self._mutation_config.enabled:
                snap = MutationSnapshot(
                    project_dir=self.job_executor.project_dir,
                    config=self._mutation_config,
                )
                snap.take()

            try:
                self.job_executor.execute_job(ctx=ctx)
                outcome = JobOutcome(job=job, success=True, history=list(self.job_executor.job_history))
            except BaseException as exc:
                succeeded = False
                allowed = _is_failure_allowed(job, exc)
                outcome = JobOutcome(
                    job=job,
                    success=allowed,
                    error=exc,
                    history=list(self.job_executor.job_history),
                    allowed_failure=allowed,
                )
            finally:
                collect_artifacts(job, self.job_executor.project_dir, succeeded)
                self._completed_jobs.append(job.name)

            if snap is not None:
                _report_mutations(job.name, snap, writer)

            outcomes.append(outcome)
            cb.on_job_complete(outcome)
        return outcomes

    def _make_pool(self, max_workers: int):
        """Create the appropriate executor pool based on backend config."""
        if self._parallel_backend.backend == "thread":
            return ThreadPoolExecutor(max_workers=max_workers)
        return ProcessPoolExecutor(
            max_workers=max_workers,
            mp_context=self._mp_ctx,
        )

    def _run_batch_parallel(self, jobs: list[JobConfig]) -> list[JobOutcome]:
        """Run jobs across processes/threads using the configured pool executor."""
        cb = self.callbacks
        outcomes: list[JobOutcome] = []

        _wf = cb.get_worker_func()
        worker_func: WorkerFunc = _wf if _wf is not None else _default_worker

        with self._make_pool(min(self.maximum_degree_of_parallelism, len(jobs))) as pool:
            futures = {}
            for job in jobs:
                job_dir = self._make_job_dir(job)
                cb.on_job_start(job)
                inject_dependencies(job, self.job_executor.project_dir, self._completed_jobs)
                extra = cb.make_worker_args(job, job_dir)
                fut = pool.submit(worker_func, job, self.job_executor, job_dir, **extra)
                futures[fut] = job

            pending = set(futures.keys())
            while pending:
                cb.poll_during_parallel(futures)
                done = {f for f in pending if f.done()}
                if not done:
                    time.sleep(0.05)
                    continue
                for fut in done:
                    pending.discard(fut)
                    job = futures[fut]
                    succeeded = True
                    try:
                        history = fut.result()
                        self.job_executor.job_history.extend(history)
                        outcome = JobOutcome(job=job, success=True, history=history)
                    except BaseException as exc:
                        succeeded = False
                        allowed = _is_failure_allowed(job, exc)
                        outcome = JobOutcome(
                            job=job,
                            success=allowed,
                            error=exc,
                            allowed_failure=allowed,
                        )
                    collect_artifacts(job, self.job_executor.project_dir, succeeded)
                    self._completed_jobs.append(job.name)
                    outcomes.append(outcome)
                    cb.on_job_complete(outcome)

        return outcomes

    def _make_job_dir(self, job: JobConfig) -> Path:
        """Create and return the per-job directory."""
        job_dir = self.job_executor.project_dir / ".bitrab" / sanitize_job_name(job.name)
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir
