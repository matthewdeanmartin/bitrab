from __future__ import annotations

import multiprocessing as mp
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any

from bitrab.execution.job import JobExecutor
from bitrab.models.pipeline import JobConfig, PipelineConfig


def _run_single_job(job: JobConfig, executor: JobExecutor) -> None:
    """
    Module-level helper so it's picklable by multiprocessing on all platforms.
    Executes a single job using the provided JobExecutor instance.
    """
    # If your JobExecutor can't be pickled, consider passing enough config to
    # rebuild it here instead of passing an instance.
    executor.execute_job(job)


class StageOrchestrator:
    """
    Orchestrates job execution by stages, running jobs within a stage in parallel.

    Args:
        job_executor: A picklable JobExecutor instance (or adapt _run_single_job to
                      reconstruct it in the worker if needed).
        maximum_degree_of_parallelism: Max processes to use per stage. Defaults to
                      os.cpu_count() (falls back to 1 if None).
    """

    def __init__(
        self, job_executor: JobExecutor, maximum_degree_of_parallelism: int | None = None, dry_run: bool = False
    ):
        self.job_executor = job_executor
        cpu_cnt = os.cpu_count() or 1
        self.maximum_degree_of_parallelism = (
            cpu_cnt if maximum_degree_of_parallelism is None else max(1, maximum_degree_of_parallelism)
        )

        # Choose a safe context for multiprocessing on Unix (Windows already uses spawn)
        # Use forkserver on Linux if you prefer; spawn is the most portable/safe.
        if sys.platform == "win32":
            self._mp_ctx = mp.get_context("spawn")
        else:

            if not os.getenv("BITRAB_USE_FORKSERVER"):
                # spawn avoids the fork-in-multithreaded-parent hazard triggering the warning
                self._mp_ctx = mp.get_context("spawn")
            else:
                self._mp_ctx = mp.get_context("forkserver")  # type: ignore[assignment]

    def execute_pipeline(self, pipeline: PipelineConfig) -> None:
        """
        Execute all jobs in the pipeline, organized by stages.
        Jobs within the same stage are executed in parallel across processes.
        """
        print("🚀 Starting GitLab CI pipeline execution")
        print(f"📋 Stages: {', '.join(pipeline.stages)}")
        print(f"🧠 Parallel workers per stage: {self.maximum_degree_of_parallelism}")

        jobs_by_stage = self._organize_jobs_by_stage(pipeline)

        for stage in pipeline.stages:
            stage_jobs = jobs_by_stage.get(stage, [])
            if not stage_jobs:
                print(f"⏭️  Skipping empty stage: {stage}")
                continue

            print(f"\n🎯 Executing stage in parallel: {stage} ({len(stage_jobs)} job(s))")

            # Use a process pool to run jobs in this stage concurrently.
            # Note: JobExecutor must be picklable to be sent to worker processes.
            # If it isn't, change _run_single_job to reconstruct the executor from config.
            failures: list[tuple[JobConfig, BaseException]] = []

            if self.maximum_degree_of_parallelism == 1:
                for job in stage_jobs:
                    self.job_executor.execute_job(job)
            else:
                with ProcessPoolExecutor(
                    max_workers=self.maximum_degree_of_parallelism,
                    mp_context=self._mp_ctx,
                ) as pool:
                    futures = {pool.submit(_run_single_job, job, self.job_executor): job for job in stage_jobs}

                    for fut in as_completed(futures):
                        job = futures[fut]
                        try:
                            fut.result()  # propagate errors
                            print(f"✅ Job completed: {job.name}")
                        except BaseException as exc:  # catch all to surface any worker failures
                            failures.append((job, exc))
                            print(f"❌ Job failed: {job.name} -> {exc!r}")

                if failures:
                    # If any job in the stage fails, stop the pipeline (common CI behavior).
                    print("\n🛑 Stopping pipeline due to failures in stage:", stage)
                    # Re-raise the first failure to signal error to caller/runner
                    raise failures[0][1]

        print("\n🎉 Pipeline completed successfully!")

    def _organize_jobs_by_stage(self, pipeline: PipelineConfig) -> dict[str, list[JobConfig]]:
        """
        Organize jobs by their stages.
        """
        jobs_by_stage: dict[str, Any] = {}
        for job in pipeline.jobs:
            jobs_by_stage.setdefault(job.stage, []).append(job)
        return jobs_by_stage
