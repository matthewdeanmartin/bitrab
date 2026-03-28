"""ARCH-4: Structured pipeline execution events.

Provides a typed event model for every lifecycle point in pipeline execution.
Events are emitted by :class:`EventCollector` (a :class:`PipelineCallbacks`
wrapper) and can be consumed for log persistence, timing analysis, summaries,
and future web UI integration.

Usage::

    from bitrab.execution.events import EventCollector, EventType

    collector = EventCollector(inner=my_callbacks)
    runner = StagePipelineRunner(job_executor=executor, callbacks=collector)
    runner.execute_pipeline(pipeline)

    for event in collector.events:
        print(event)

    print(collector.summary())
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from bitrab.execution.job import JobRuntimeContext
from bitrab.execution.shell import TextWriter
from bitrab.execution.stage_runner import JobOutcome, PipelineCallbacks, WorkerFunc
from bitrab.models.pipeline import JobConfig, PipelineConfig


class EventType(str, Enum):
    """All lifecycle events emitted during pipeline execution."""

    PIPELINE_START = "pipeline_start"
    PIPELINE_COMPLETE = "pipeline_complete"
    STAGE_START = "stage_start"
    STAGE_SKIP = "stage_skip"
    STAGE_COMPLETE = "stage_complete"
    JOB_START = "job_start"
    JOB_COMPLETE = "job_complete"
    PIPELINE_CANCELLED = "pipeline_cancelled"
    PIPELINE_AWAITING_MANUAL = "pipeline_awaiting_manual"


@dataclass(frozen=True)
class PipelineEvent:
    """A single structured event from pipeline execution.

    Attributes:
        event_type: The kind of lifecycle event.
        timestamp: Monotonic time (``time.monotonic()``) when the event occurred.
        wall_time: Wall-clock time (``time.time()``) for display and persistence.
        stage: Stage name, if the event is stage- or job-scoped.
        job: Job name, if the event is job-scoped.
        data: Arbitrary payload — contents depend on ``event_type``.
    """

    event_type: EventType
    timestamp: float
    wall_time: float
    stage: str | None = None
    job: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# EventCollector — composable PipelineCallbacks wrapper
# ---------------------------------------------------------------------------


class EventCollector(PipelineCallbacks):
    """Captures structured events while delegating to an inner callbacks instance.

    This is a *decorator* over any existing :class:`PipelineCallbacks`.  Every
    lifecycle hook records a :class:`PipelineEvent` and then forwards the call
    to the wrapped ``inner`` callbacks, so existing behaviour (printing, TUI
    updates, file writing) is completely preserved.

    Access captured events via :attr:`events` after execution completes.
    """

    def __init__(self, inner: PipelineCallbacks | None = None) -> None:
        self._inner = inner or PipelineCallbacks()
        self._events: list[PipelineEvent] = []

    @property
    def events(self) -> list[PipelineEvent]:
        """All events captured during execution, in chronological order."""
        return list(self._events)

    def _emit(
        self,
        event_type: EventType,
        *,
        stage: str | None = None,
        job: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> PipelineEvent:
        """Create, record, and return a new event."""
        event = PipelineEvent(
            event_type=event_type,
            timestamp=time.monotonic(),
            wall_time=time.time(),
            stage=stage,
            job=job,
            data=data or {},
        )
        self._events.append(event)
        return event

    # -- Pipeline lifecycle --------------------------------------------------

    def on_pipeline_start(self, pipeline: PipelineConfig, max_workers: int) -> None:
        self._emit(
            EventType.PIPELINE_START,
            data={
                "stages": list(pipeline.stages),
                "job_count": len(pipeline.jobs),
                "max_workers": max_workers,
            },
        )
        self._inner.on_pipeline_start(pipeline, max_workers)

    def on_pipeline_complete(self, success: bool) -> None:
        self._emit(EventType.PIPELINE_COMPLETE, data={"success": success})
        self._inner.on_pipeline_complete(success)

    def on_cancelled(self) -> None:
        self._emit(EventType.PIPELINE_CANCELLED)
        self._inner.on_cancelled()

    def on_pipeline_awaiting_manual(self) -> None:
        self._emit(EventType.PIPELINE_AWAITING_MANUAL)
        self._inner.on_pipeline_awaiting_manual()

    # -- Stage lifecycle -----------------------------------------------------

    def on_stage_start(self, stage: str, jobs: list[JobConfig]) -> None:
        self._emit(
            EventType.STAGE_START,
            stage=stage,
            data={"job_names": [j.name for j in jobs]},
        )
        self._inner.on_stage_start(stage, jobs)

    def on_stage_skip(self, stage: str) -> None:
        self._emit(EventType.STAGE_SKIP, stage=stage)
        self._inner.on_stage_skip(stage)

    def on_stage_complete(self, stage: str, outcomes: list[JobOutcome]) -> None:
        self._emit(
            EventType.STAGE_COMPLETE,
            stage=stage,
            data={
                "outcomes": [
                    {
                        "job": o.job.name,
                        "success": o.success,
                        "allowed_failure": o.allowed_failure,
                    }
                    for o in outcomes
                ],
            },
        )
        self._inner.on_stage_complete(stage, outcomes)

    # -- Job lifecycle -------------------------------------------------------

    def on_job_start(self, job: JobConfig) -> None:
        self._emit(EventType.JOB_START, stage=job.stage, job=job.name)
        self._inner.on_job_start(job)

    def on_job_complete(self, outcome: JobOutcome) -> None:
        status = "allowed_failure" if outcome.allowed_failure else ("success" if outcome.success else "failed")
        self._emit(
            EventType.JOB_COMPLETE,
            stage=outcome.job.stage,
            job=outcome.job.name,
            data={
                "success": outcome.success,
                "allowed_failure": outcome.allowed_failure,
                "status": status,
                "error": repr(outcome.error) if outcome.error else None,
            },
        )
        self._inner.on_job_complete(outcome)

    # -- Passthrough hooks (no events needed) --------------------------------

    def is_cancelled(self) -> bool:
        return self._inner.is_cancelled()

    def make_output_writer(self, job: JobConfig, job_dir: Path) -> TextWriter | None:
        return self._inner.make_output_writer(job, job_dir)

    def make_worker_args(self, job: JobConfig, job_dir: Path) -> dict[str, Any]:
        return self._inner.make_worker_args(job, job_dir)

    def get_worker_func(self) -> WorkerFunc | None:
        return self._inner.get_worker_func()

    def poll_during_parallel(self, futures: dict[Any, JobConfig]) -> None:
        self._inner.poll_during_parallel(futures)

    def enrich_context(self, ctx: JobRuntimeContext) -> JobRuntimeContext:
        return self._inner.enrich_context(ctx)

    # -- Summary generation --------------------------------------------------

    def summary(self) -> PipelineSummary:
        """Build a :class:`PipelineSummary` from captured events."""
        return PipelineSummary.from_events(self._events)


# ---------------------------------------------------------------------------
# Pipeline summary
# ---------------------------------------------------------------------------


@dataclass
class JobTiming:
    """Timing and status for a single job."""

    name: str
    stage: str
    status: str  # "success" | "failed" | "allowed_failure"
    duration_s: float  # seconds between JOB_START and JOB_COMPLETE
    error: str | None = None


@dataclass
class StageTiming:
    """Timing and status for a single stage."""

    name: str
    duration_s: float  # seconds between STAGE_START and STAGE_COMPLETE
    job_count: int
    skipped: bool = False


@dataclass
class PipelineSummary:
    """Structured summary of a pipeline execution, built from events."""

    success: bool
    total_duration_s: float
    stages: list[StageTiming]
    jobs: list[JobTiming]
    cancelled: bool = False
    awaiting_manual: bool = False

    @classmethod
    def from_events(cls, events: list[PipelineEvent]) -> PipelineSummary:
        """Construct a summary from a chronological list of events."""
        success = True
        total_duration = 0.0
        cancelled = False
        awaiting_manual = False

        # Timestamps for pipeline
        pipeline_start_ts: float | None = None
        pipeline_end_ts: float | None = None

        # Track stage start times
        stage_starts: dict[str, float] = {}
        stage_job_counts: dict[str, int] = {}
        stages: list[StageTiming] = []

        # Track job start times
        job_starts: dict[str, float] = {}
        job_stages: dict[str, str] = {}
        jobs: list[JobTiming] = []

        for event in events:
            if event.event_type == EventType.PIPELINE_START:
                pipeline_start_ts = event.timestamp
            elif event.event_type == EventType.PIPELINE_COMPLETE:
                pipeline_end_ts = event.timestamp
                success = event.data.get("success", True)
            elif event.event_type == EventType.PIPELINE_CANCELLED:
                cancelled = True
            elif event.event_type == EventType.PIPELINE_AWAITING_MANUAL:
                awaiting_manual = True
            elif event.event_type == EventType.STAGE_START:
                stage = event.stage or ""
                stage_starts[stage] = event.timestamp
                stage_job_counts[stage] = len(event.data.get("job_names", []))
            elif event.event_type == EventType.STAGE_SKIP:
                stage = event.stage or ""
                stages.append(StageTiming(name=stage, duration_s=0.0, job_count=0, skipped=True))
            elif event.event_type == EventType.STAGE_COMPLETE:
                stage = event.stage or ""
                start = stage_starts.get(stage)
                duration = (event.timestamp - start) if start is not None else 0.0
                stages.append(
                    StageTiming(
                        name=stage,
                        duration_s=duration,
                        job_count=stage_job_counts.get(stage, 0),
                    )
                )
            elif event.event_type == EventType.JOB_START:
                job = event.job or ""
                job_starts[job] = event.timestamp
                job_stages[job] = event.stage or ""
            elif event.event_type == EventType.JOB_COMPLETE:
                job = event.job or ""
                start = job_starts.get(job)
                duration = (event.timestamp - start) if start is not None else 0.0
                jobs.append(
                    JobTiming(
                        name=job,
                        stage=job_stages.get(job, event.stage or ""),
                        status=event.data.get("status", "unknown"),
                        duration_s=duration,
                        error=event.data.get("error"),
                    )
                )

        if pipeline_start_ts is not None and pipeline_end_ts is not None:
            total_duration = pipeline_end_ts - pipeline_start_ts

        return cls(
            success=success,
            total_duration_s=total_duration,
            stages=stages,
            jobs=jobs,
            cancelled=cancelled,
            awaiting_manual=awaiting_manual,
        )

    def format_text(self) -> str:
        """Render the summary as a human-readable text block."""
        lines: list[str] = []
        lines.append("")
        icon = "Pipeline succeeded" if self.success else "Pipeline failed"
        if self.cancelled:
            icon = "Pipeline cancelled"
        lines.append(f"{'=' * 50}")
        lines.append(f"  {icon}  ({self.total_duration_s:.1f}s)")
        lines.append(f"{'=' * 50}")

        if self.stages:
            lines.append("")
            lines.append("  Stages:")
            for st in self.stages:
                if st.skipped:
                    lines.append(f"    - {st.name} (skipped)")
                else:
                    lines.append(f"    - {st.name} ({st.job_count} jobs, {st.duration_s:.1f}s)")

        if self.jobs:
            lines.append("")
            lines.append("  Jobs:")
            for jt in self.jobs:
                if jt.status == "success":
                    mark = "pass"
                elif jt.status == "allowed_failure":
                    mark = "warn"
                else:
                    mark = "FAIL"
                lines.append(f"    [{mark:>4}] {jt.name} ({jt.duration_s:.1f}s)")
                if jt.error:
                    lines.append(f"           {jt.error}")

        if self.awaiting_manual:
            lines.append("")
            lines.append("  Manual jobs are awaiting trigger.")

        lines.append("")
        return "\n".join(lines)
