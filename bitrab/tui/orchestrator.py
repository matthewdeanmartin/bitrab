"""TUI-aware pipeline orchestrator.

Runs jobs in parallel via ProcessPoolExecutor, routing output through a
multiprocessing.Manager().Queue() so the Textual app can display per-job logs.

For CI mode (no TUI), jobs write to per-job log files and the files are printed
to stdout after each stage completes.

Both modes are thin callback wrappers around :class:`StagePipelineRunner`.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import signal
import subprocess  # nosec
import sys
import threading
from pathlib import Path
from queue import Empty
from typing import TYPE_CHECKING, Any

from bitrab.execution.events import EventCollector
from bitrab.execution.job import JobExecutor, RunResult
from bitrab.execution.shell import TextWriter
from bitrab.execution.stage_runner import JobOutcome, PipelineCallbacks, StagePipelineRunner, sanitize_job_name
from bitrab.models.pipeline import JobConfig, PipelineConfig

if TYPE_CHECKING:
    from bitrab.tui.app import PipelineApp


# ---------------------------------------------------------------------------
# Queue-based writer (must be module-level for pickling)
# ---------------------------------------------------------------------------


class QueueWriter:
    """File-like object that puts text into a multiprocessing Manager Queue.

    Must be at module level (not nested) to be picklable under spawn context.
    """

    def __init__(self, queue: Any, job_name: str) -> None:
        self._queue = queue
        self._job_name = job_name

    def write(self, s: str) -> None:
        """Write text to the queue."""
        if s:
            self._queue.put((self._job_name, s))

    def flush(self) -> None:
        """No-op flush to satisfy IO protocol."""


# ---------------------------------------------------------------------------
# Picklable worker functions (module-level)
# ---------------------------------------------------------------------------


def _run_single_job_queued(
    job: JobConfig,
    executor: JobExecutor,
    job_dir: Path,
    output_queue: Any = None,
    worker_pids: Any = None,
) -> list[RunResult]:
    """Worker function: runs one job, writes all output to the shared queue.

    Sends (job_name, text) tuples while running, then (job_name, None) as sentinel.
    """
    if worker_pids is not None:
        worker_pids[job.name] = os.getpid()
    writer = QueueWriter(output_queue, job.name)
    try:
        ctx = executor.build_context(job, job_dir=job_dir, output_writer=writer)
        executor.execute_job(ctx=ctx)
    finally:
        output_queue.put((job.name, None))  # sentinel: job done
    return executor.job_history


def _run_single_job_file(
    job: JobConfig,
    executor: JobExecutor,
    job_dir: Path,
    log_path: Any = None,
) -> list[RunResult]:
    """Worker function for CI mode: writes output to a file instead of a queue."""
    job_dir.mkdir(parents=True, exist_ok=True)
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as fh:
        ctx = executor.build_context(job, job_dir=job_dir, output_writer=fh)
        executor.execute_job(ctx=ctx)
    return executor.job_history


# ---------------------------------------------------------------------------
# TUI callbacks
# ---------------------------------------------------------------------------


class _TUICallbacks(PipelineCallbacks):
    """Callbacks that route status updates to the Textual app."""

    def __init__(
        self,
        app: PipelineApp,
        output_queue: Any,
        worker_pids: Any,
        cancel_event: threading.Event,
    ) -> None:
        self._app = app
        self._output_queue = output_queue
        self._worker_pids = worker_pids
        self._cancel_event = cancel_event
        self._active_jobs: set[str] = set()

    def on_stage_start(self, stage: str, jobs: list[JobConfig]) -> None:
        self._app.call_from_thread(self._app.update_stage_status, stage, len(jobs))

    def on_job_start(self, job: JobConfig) -> None:
        from bitrab.tui.app import JobStatusChanged

        self._app.call_from_thread(self._app.post_message, JobStatusChanged(job.name, "running"))
        self._active_jobs.add(job.name)

    def on_job_complete(self, outcome: JobOutcome) -> None:
        from bitrab.tui.app import JobStatusChanged

        if outcome.allowed_failure:
            status = "warned"
        elif outcome.success:
            status = "success"
        else:
            status = "failed"
        self._app.call_from_thread(self._app.post_message, JobStatusChanged(outcome.job.name, status))

    def on_pipeline_awaiting_manual(self) -> None:
        self._app.call_from_thread(self._app.on_pipeline_awaiting_manual)

    def on_pipeline_complete(self, success: bool) -> None:
        if self._cancel_event.is_set():
            self._app.call_from_thread(self._app.on_pipeline_cancelled)
        else:
            self._app.call_from_thread(self._app.on_pipeline_complete, success)

    def on_cancelled(self) -> None:
        self._app.call_from_thread(self._app.on_pipeline_cancelled)

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def make_output_writer(self, job: JobConfig, _job_dir: Path) -> TextWriter | None:
        return QueueWriter(self._output_queue, job.name)

    def get_worker_func(self):
        return _run_single_job_queued

    def make_worker_args(self, _job: JobConfig, _job_dir: Path) -> dict[str, Any]:
        return {"output_queue": self._output_queue, "worker_pids": self._worker_pids}

    def on_stage_complete(self, stage: str, outcomes: list[JobOutcome]) -> None:
        # Drain any remaining queue items after serial execution
        self._drain_remaining()

    def poll_during_parallel(self, futures: dict) -> None:
        """Drain the output queue while parallel futures are running."""
        from bitrab.tui.app import JobOutput

        try:
            while True:
                job_name, text = self._output_queue.get(timeout=0.02)
                if text is None:
                    self._active_jobs.discard(job_name)
                else:
                    self._app.call_from_thread(self._app.post_message, JobOutput(job_name, text))
        except Empty:
            pass

    def _drain_remaining(self) -> None:
        """Drain any remaining items from the output queue."""
        from bitrab.tui.app import JobOutput

        while True:
            try:
                job_name, text = self._output_queue.get_nowait()
                if text is not None:
                    self._app.call_from_thread(self._app.post_message, JobOutput(job_name, text))
            except Empty:
                break


# ---------------------------------------------------------------------------
# CI-file callbacks
# ---------------------------------------------------------------------------


class _CIFileCallbacks(PipelineCallbacks):
    """Callbacks that write job output to files and print after each stage."""

    def __init__(self) -> None:
        self._log_paths: dict[str, Path] = {}
        self._current_stage_jobs: list[JobConfig] = []

    def on_pipeline_start(self, pipeline: PipelineConfig, max_workers: int) -> None:
        print("🚀 Starting pipeline (CI mode - parallel jobs, sequential stages)")
        print(f"📋 Stages: {', '.join(pipeline.stages)}")

    def on_pipeline_complete(self, success: bool) -> None:
        if success:
            print("\n🎉 Pipeline completed successfully!")

    def on_stage_start(self, stage: str, jobs: list[JobConfig]) -> None:
        self._current_stage_jobs = jobs
        self._log_paths = {}
        print(f"\n🎯 Stage: {stage} ({len(jobs)} job(s) running in parallel)")

    def on_stage_skip(self, stage: str) -> None:
        print(f"⏭️  Skipping empty stage: {stage}")

    def on_job_start(self, job: JobConfig) -> None:
        print(f"  ▶ {job.name}")

    def on_stage_complete(self, stage: str, outcomes: list[JobOutcome]) -> None:
        failures = {o.job.name for o in outcomes if not o.success}
        for job in self._current_stage_jobs:
            log_path = self._log_paths.get(job.name)
            status = "❌" if job.name in failures else "✅"
            print(f"\n{'=' * 60}")
            print(f"{status} Job: {job.name} (stage: {stage})")
            print(f"{'=' * 60}")
            if log_path and log_path.exists():
                print(log_path.read_text(encoding="utf-8"))
            else:
                print("(no output)")

        if failures:
            print(f"\n🛑 Stage {stage} failed. Stopping pipeline.")

    def make_output_writer(self, job: JobConfig, job_dir: Path) -> TextWriter | None:
        log_path = job_dir / "output.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_paths[job.name] = log_path
        return open(log_path, "w", encoding="utf-8")  # noqa: SIM115

    def get_worker_func(self):
        return _run_single_job_file

    def make_worker_args(self, job: JobConfig, job_dir: Path) -> dict[str, Any]:
        log_path = job_dir / "output.log"
        self._log_paths[job.name] = log_path
        return {"log_path": str(log_path)}


# ---------------------------------------------------------------------------
# TUI Orchestrator (preserves original public API)
# ---------------------------------------------------------------------------


class TUIOrchestrator:
    """Pipeline orchestrator that feeds output to the Textual TUI or CI log files.

    Attributes:
        job_executor: The JobExecutor used to run individual jobs.
        maximum_degree_of_parallelism: Max parallel jobs per stage.
    """

    def __init__(
        self,
        job_executor: JobExecutor,
        maximum_degree_of_parallelism: int | None = None,
        mp_ctx: Any = None,
    ) -> None:
        self.job_executor = job_executor
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

        # Cancel/control state
        self._cancel_event: threading.Event = threading.Event()
        # job_name -> worker OS PID (populated via Manager().dict() in parallel path)
        self._worker_pids: dict[str, int] = {}
        # Structured event collector (populated per execution)
        self._event_collector: EventCollector | None = None

    @property
    def event_collector(self) -> EventCollector | None:
        """Access the structured event collector from the last execution."""
        return self._event_collector

    def is_running(self) -> bool:
        """Return True if the pipeline is actively running (not cancelled, not done)."""
        return not self._cancel_event.is_set()

    def reset(self) -> None:
        """Reset orchestrator state for a fresh pipeline run."""
        self._cancel_event.clear()
        self._worker_pids = {}

    def cancel_pipeline(self) -> None:
        """Signal the pipeline to stop after the current stage completes."""
        self._cancel_event.set()

    def cancel_job(self, job_name: str) -> None:
        """Best-effort: send SIGTERM/taskkill to the worker process running job_name."""
        pid = self._worker_pids.get(job_name)
        if pid is None:
            return
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], check=False, capture_output=True)  # nosec
            else:
                os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def run_job_inline(self, job: JobConfig, app: PipelineApp) -> None:
        """Run a single job inline in the calling thread (for restart-job)."""
        from bitrab.tui.app import JobOutput, JobStatusChanged

        mgr = self._mp_ctx.Manager()
        output_queue = mgr.Queue()
        job_dir = self.job_executor.project_dir / ".bitrab" / sanitize_job_name(job.name)
        job_dir.mkdir(parents=True, exist_ok=True)
        app.call_from_thread(app.post_message, JobStatusChanged(job.name, "running"))
        writer = QueueWriter(output_queue, job.name)
        try:
            ctx = self.job_executor.build_context(job, job_dir=job_dir, output_writer=writer)
            self.job_executor.execute_job(ctx=ctx)
            app.call_from_thread(app.post_message, JobStatusChanged(job.name, "success"))
        except Exception:
            app.call_from_thread(app.post_message, JobStatusChanged(job.name, "failed"))
        finally:
            self._drain_queue_sync(output_queue, app, JobOutput)
            mgr.shutdown()

    def execute_pipeline_tui(self, pipeline: PipelineConfig, app: PipelineApp) -> None:
        """Execute pipeline with live output routed to Textual TUI."""
        mgr = self._mp_ctx.Manager()
        output_queue = mgr.Queue()
        worker_pids = mgr.dict()
        self._worker_pids = worker_pids  # type: ignore[assignment]

        tui_callbacks = _TUICallbacks(app, output_queue, worker_pids, self._cancel_event)
        self._event_collector = EventCollector(inner=tui_callbacks)

        try:
            runner = StagePipelineRunner(
                job_executor=self.job_executor,
                callbacks=self._event_collector,
                maximum_degree_of_parallelism=self.maximum_degree_of_parallelism,
                mp_ctx=self._mp_ctx,
            )
            runner.execute_pipeline(pipeline)
        finally:
            mgr.shutdown()

    def execute_pipeline_ci(self, pipeline: PipelineConfig) -> None:
        """Execute pipeline in CI mode: jobs write to files, printed when done."""
        ci_callbacks = _CIFileCallbacks()
        self._event_collector = EventCollector(inner=ci_callbacks)
        runner = StagePipelineRunner(
            job_executor=self.job_executor,
            callbacks=self._event_collector,
            maximum_degree_of_parallelism=self.maximum_degree_of_parallelism,
            mp_ctx=self._mp_ctx,
        )
        runner.execute_pipeline(pipeline)
        summary = self._event_collector.summary()
        print(summary.format_text())

    def _drain_queue_sync(self, queue: Any, app: PipelineApp, job_output_cls: Any) -> None:
        """Drain remaining items from queue after single-job inline execution."""
        while True:
            try:
                job_name, text = queue.get_nowait()
                if text is not None:
                    app.call_from_thread(app.post_message, job_output_cls(job_name, text))
            except Empty:
                break
