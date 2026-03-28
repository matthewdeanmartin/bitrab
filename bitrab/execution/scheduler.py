"""Plain-streaming pipeline orchestrator.

Thin wrapper around :class:`StagePipelineRunner` that prints status to stdout.
"""

from __future__ import annotations

from bitrab.execution.job import JobExecutor
from bitrab.execution.stage_runner import JobOutcome, PipelineCallbacks, StagePipelineRunner
from bitrab.models.pipeline import JobConfig, PipelineConfig


class _StreamingCallbacks(PipelineCallbacks):
    """Callbacks that print status updates to stdout (original StageOrchestrator behaviour)."""

    def on_pipeline_start(self, pipeline: PipelineConfig, max_workers: int) -> None:
        print("🚀 Starting GitLab CI pipeline execution")
        print(f"📋 Stages: {', '.join(pipeline.stages)}")
        print(f"🧠 Parallel workers per stage: {max_workers}")

    def on_pipeline_complete(self, success: bool) -> None:
        if success:
            print("\n🎉 Pipeline completed successfully!")

    def on_stage_start(self, stage: str, jobs: list[JobConfig]) -> None:
        print(f"\n🎯 Executing stage in parallel: {stage} ({len(jobs)} job(s))")

    def on_stage_skip(self, stage: str) -> None:
        print(f"⏭️  Skipping empty stage: {stage}")

    def on_job_complete(self, outcome: JobOutcome) -> None:
        if outcome.allowed_failure:
            print(f"⚠️  Job warned (allow_failure): {outcome.job.name}")
        elif outcome.success:
            print(f"✅ Job completed: {outcome.job.name}")
        else:
            print(f"❌ Job failed: {outcome.job.name} -> {outcome.error!r}")

    def on_stage_complete(self, stage: str, outcomes: list[JobOutcome]) -> None:
        failures = [o for o in outcomes if not o.success]
        if failures:
            print("\n🛑 Stopping pipeline due to failures in stage:", stage)


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
    ) -> None:
        self.job_executor = job_executor
        self._runner = StagePipelineRunner(
            job_executor=job_executor,
            callbacks=_StreamingCallbacks(),
            maximum_degree_of_parallelism=maximum_degree_of_parallelism,
        )

    def execute_pipeline(self, pipeline: PipelineConfig) -> None:
        """Execute all jobs in the pipeline, organized by stages."""
        self._runner.execute_pipeline(pipeline)
