"""Plain-streaming pipeline orchestrator.

Thin wrapper around :class:`StagePipelineRunner` that prints status to stdout.
"""

from __future__ import annotations

from bitrab.console import safe_print
from bitrab.execution.events import EventCollector
from bitrab.execution.job import JobExecutor
from bitrab.execution.stage_runner import JobOutcome, PipelineCallbacks, StagePipelineRunner
from bitrab.models.pipeline import JobConfig, PipelineConfig
from bitrab.mutation import MutationConfig, ParallelBackendConfig


class _StreamingCallbacks(PipelineCallbacks):
    """Callbacks that print status updates to stdout (original StageOrchestrator behaviour)."""

    def __init__(self, dry_run: bool = False) -> None:
        self._dry_run = dry_run

    def on_pipeline_start(self, pipeline: PipelineConfig, max_workers: int) -> None:
        if self._dry_run:
            safe_print("🚀 Starting GitLab CI pipeline dry run")
        else:
            safe_print("🚀 Starting GitLab CI pipeline execution")
        safe_print(f"📋 Stages: {', '.join(pipeline.stages)}")
        safe_print(f"🧠 Parallel workers per stage: {max_workers}")

    def on_pipeline_complete(self, success: bool) -> None:
        if success:
            safe_print("\n🎉 Pipeline completed successfully!")

    def on_stage_start(self, stage: str, jobs: list[JobConfig]) -> None:
        verb = "Previewing" if self._dry_run else "Executing"
        safe_print(f"\n🎯 {verb} stage in parallel: {stage} ({len(jobs)} job(s))")

    def on_stage_skip(self, stage: str) -> None:
        safe_print(f"⏭️  Skipping empty stage: {stage}")

    def on_job_complete(self, outcome: JobOutcome) -> None:
        if outcome.allowed_failure:
            safe_print(f"⚠️  Job warned (allow_failure): {outcome.job.name}")
        elif outcome.success:
            safe_print(f"✅ Job completed: {outcome.job.name}")
        else:
            safe_print(f"❌ Job failed: {outcome.job.name} -> {outcome.error!r}")

    def on_stage_complete(self, stage: str, outcomes: list[JobOutcome]) -> None:
        failures = [o for o in outcomes if not o.success]
        if failures:
            safe_print("\n🛑 Stopping pipeline due to failures in stage:", stage)


class StageOrchestrator:
    """Orchestrates job execution by stages, running jobs within a stage in parallel.

    This is a thin wrapper around :class:`StagePipelineRunner` with stdout-printing
    callbacks, preserving the original public API.
    """

    def __init__(
        self,
        job_executor: JobExecutor,
        maximum_degree_of_parallelism: int | None = None,
        dry_run: bool = False,
        mutation_config: MutationConfig | None = None,
        parallel_backend: ParallelBackendConfig | None = None,
    ) -> None:
        self.job_executor = job_executor
        self._event_collector = EventCollector(inner=_StreamingCallbacks(dry_run=dry_run))
        self._runner = StagePipelineRunner(
            job_executor=job_executor,
            callbacks=self._event_collector,
            maximum_degree_of_parallelism=maximum_degree_of_parallelism,
            mutation_config=mutation_config,
            parallel_backend=parallel_backend,
        )

    @property
    def event_collector(self) -> EventCollector:
        """Access the structured event collector for this orchestrator."""
        return self._event_collector

    def execute_pipeline(self, pipeline: PipelineConfig) -> None:
        """Execute all jobs in the pipeline, organized by stages."""
        self._runner.execute_pipeline(pipeline)
        summary = self._event_collector.summary()
        safe_print(summary.format_text())
