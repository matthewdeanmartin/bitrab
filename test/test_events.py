"""Tests for ARCH-4: Structured pipeline execution events."""

from __future__ import annotations

from bitrab.execution.events import EventCollector, EventType
from bitrab.execution.stage_runner import JobOutcome, PipelineCallbacks
from bitrab.models.pipeline import JobConfig, PipelineConfig


def make_pipeline(jobs: list[JobConfig] | None = None) -> PipelineConfig:
    if jobs is None:
        jobs = [
            JobConfig(name="lint", stage="test", script=["echo lint"]),
            JobConfig(name="build", stage="build", script=["echo build"]),
        ]
    return PipelineConfig(stages=["test", "build"], jobs=jobs)


class TestEventCollector:
    """EventCollector captures events and delegates to inner callbacks."""

    def test_captures_pipeline_lifecycle(self):
        collector = EventCollector()
        pipeline = make_pipeline()

        collector.on_pipeline_start(pipeline, max_workers=2)
        collector.on_pipeline_complete(success=True)

        events = collector.events
        assert len(events) == 2
        assert events[0].event_type == EventType.PIPELINE_START
        assert events[0].data["stages"] == ["test", "build"]
        assert events[0].data["job_count"] == 2
        assert events[0].data["max_workers"] == 2
        assert events[1].event_type == EventType.PIPELINE_COMPLETE
        assert events[1].data["success"] is True

    def test_captures_stage_events(self):
        collector = EventCollector()
        jobs = [JobConfig(name="lint", stage="test", script=["echo lint"])]

        collector.on_stage_start("test", jobs)
        collector.on_stage_skip("deploy")
        collector.on_stage_complete("test", [JobOutcome(job=jobs[0], success=True)])

        events = collector.events
        assert len(events) == 3
        assert events[0].event_type == EventType.STAGE_START
        assert events[0].stage == "test"
        assert events[0].data["job_names"] == ["lint"]
        assert events[1].event_type == EventType.STAGE_SKIP
        assert events[1].stage == "deploy"
        assert events[2].event_type == EventType.STAGE_COMPLETE
        assert events[2].stage == "test"

    def test_captures_job_events(self):
        collector = EventCollector()
        job = JobConfig(name="lint", stage="test", script=["echo lint"])

        collector.on_job_start(job)
        outcome = JobOutcome(job=job, success=True)
        collector.on_job_complete(outcome)

        events = collector.events
        assert len(events) == 2
        assert events[0].event_type == EventType.JOB_START
        assert events[0].job == "lint"
        assert events[0].stage == "test"
        assert events[1].event_type == EventType.JOB_COMPLETE
        assert events[1].data["status"] == "success"

    def test_captures_failed_job(self):
        collector = EventCollector()
        job = JobConfig(name="build", stage="build", script=["exit 1"])

        collector.on_job_start(job)
        outcome = JobOutcome(job=job, success=False, error=RuntimeError("exit 1"))
        collector.on_job_complete(outcome)

        events = collector.events
        assert events[1].data["status"] == "failed"
        assert events[1].data["success"] is False
        assert "RuntimeError" in events[1].data["error"]

    def test_captures_allowed_failure(self):
        collector = EventCollector()
        job = JobConfig(name="flaky", stage="test", script=["exit 1"], allow_failure=True)

        collector.on_job_start(job)
        outcome = JobOutcome(job=job, success=True, error=RuntimeError("exit 1"), allowed_failure=True)
        collector.on_job_complete(outcome)

        events = collector.events
        assert events[1].data["status"] == "allowed_failure"

    def test_captures_cancellation(self):
        collector = EventCollector()
        collector.on_cancelled()
        assert collector.events[0].event_type == EventType.PIPELINE_CANCELLED

    def test_captures_awaiting_manual(self):
        collector = EventCollector()
        collector.on_pipeline_awaiting_manual()
        assert collector.events[0].event_type == EventType.PIPELINE_AWAITING_MANUAL

    def test_delegates_to_inner(self):
        """EventCollector forwards all calls to the wrapped inner callbacks."""
        calls: list[str] = []

        class Tracker(PipelineCallbacks):
            def on_pipeline_start(self, pipeline, max_workers):
                calls.append("pipeline_start")

            def on_pipeline_complete(self, success):
                calls.append("pipeline_complete")

            def on_stage_start(self, stage, jobs):
                calls.append("stage_start")

            def on_job_start(self, job):
                calls.append("job_start")

            def on_job_complete(self, outcome):
                calls.append("job_complete")

        collector = EventCollector(inner=Tracker())
        pipeline = make_pipeline()
        job = pipeline.jobs[0]

        collector.on_pipeline_start(pipeline, max_workers=1)
        collector.on_stage_start("test", [job])
        collector.on_job_start(job)
        collector.on_job_complete(JobOutcome(job=job, success=True))
        collector.on_pipeline_complete(success=True)

        assert calls == ["pipeline_start", "stage_start", "job_start", "job_complete", "pipeline_complete"]
        assert len(collector.events) == 5

    def test_events_are_chronologically_ordered(self):
        collector = EventCollector()
        pipeline = make_pipeline()
        job = pipeline.jobs[0]

        collector.on_pipeline_start(pipeline, max_workers=1)
        collector.on_stage_start("test", [job])
        collector.on_job_start(job)
        collector.on_job_complete(JobOutcome(job=job, success=True))
        collector.on_stage_complete("test", [JobOutcome(job=job, success=True)])
        collector.on_pipeline_complete(success=True)

        timestamps = [e.timestamp for e in collector.events]
        assert timestamps == sorted(timestamps)

    def test_events_list_is_a_copy(self):
        collector = EventCollector()
        collector.on_cancelled()
        events = collector.events
        events.clear()
        assert len(collector.events) == 1  # original unmodified

    def test_is_cancelled_delegates(self):
        class AlwaysCancelled(PipelineCallbacks):
            def is_cancelled(self):
                return True

        collector = EventCollector(inner=AlwaysCancelled())
        assert collector.is_cancelled() is True


class TestPipelineSummary:
    """PipelineSummary.from_events constructs correct summaries."""

    def run_simple_pipeline(self) -> EventCollector:
        """Simulate a simple 2-job pipeline through the collector."""
        collector = EventCollector()
        pipeline = make_pipeline()
        lint, build = pipeline.jobs

        collector.on_pipeline_start(pipeline, max_workers=1)

        collector.on_stage_start("test", [lint])
        collector.on_job_start(lint)
        collector.on_job_complete(JobOutcome(job=lint, success=True))
        collector.on_stage_complete("test", [JobOutcome(job=lint, success=True)])

        collector.on_stage_start("build", [build])
        collector.on_job_start(build)
        collector.on_job_complete(JobOutcome(job=build, success=True))
        collector.on_stage_complete("build", [JobOutcome(job=build, success=True)])

        collector.on_pipeline_complete(success=True)
        return collector

    def test_summary_success(self):
        collector = self.run_simple_pipeline()
        summary = collector.summary()

        assert summary.success is True
        assert summary.cancelled is False
        assert len(summary.stages) == 2
        assert len(summary.jobs) == 2
        assert summary.stages[0].name == "test"
        assert summary.stages[1].name == "build"
        assert summary.jobs[0].name == "lint"
        assert summary.jobs[0].status == "success"
        assert summary.jobs[1].name == "build"

    def test_summary_failure(self):
        collector = EventCollector()
        job = JobConfig(name="fail", stage="test", script=["exit 1"])
        pipeline = PipelineConfig(stages=["test"], jobs=[job])

        collector.on_pipeline_start(pipeline, max_workers=1)
        collector.on_stage_start("test", [job])
        collector.on_job_start(job)
        collector.on_job_complete(JobOutcome(job=job, success=False, error=RuntimeError("boom")))
        collector.on_stage_complete("test", [JobOutcome(job=job, success=False)])
        collector.on_pipeline_complete(success=False)

        summary = collector.summary()
        assert summary.success is False
        assert summary.jobs[0].status == "failed"
        assert summary.jobs[0].error is not None

    def test_summary_with_skipped_stage(self):
        collector = EventCollector()
        pipeline = make_pipeline()

        collector.on_pipeline_start(pipeline, max_workers=1)
        collector.on_stage_skip("deploy")
        collector.on_pipeline_complete(success=True)

        summary = collector.summary()
        assert len(summary.stages) == 1
        assert summary.stages[0].skipped is True
        assert summary.stages[0].name == "deploy"

    def test_summary_cancelled(self):
        collector = EventCollector()
        pipeline = make_pipeline()

        collector.on_pipeline_start(pipeline, max_workers=1)
        collector.on_cancelled()
        collector.on_pipeline_complete(success=False)

        summary = collector.summary()
        assert summary.cancelled is True

    def test_summary_awaiting_manual(self):
        collector = EventCollector()
        pipeline = make_pipeline()

        collector.on_pipeline_start(pipeline, max_workers=1)
        collector.on_pipeline_awaiting_manual()
        collector.on_pipeline_complete(success=True)

        summary = collector.summary()
        assert summary.awaiting_manual is True

    def test_format_text_contains_key_info(self):
        collector = self.run_simple_pipeline()
        text = collector.summary().format_text()

        assert "Pipeline succeeded" in text
        assert "test" in text
        assert "build" in text
        assert "lint" in text
        assert "pass" in text

    def test_format_text_failure(self):
        collector = EventCollector()
        job = JobConfig(name="broken", stage="test", script=["exit 1"])
        pipeline = PipelineConfig(stages=["test"], jobs=[job])

        collector.on_pipeline_start(pipeline, max_workers=1)
        collector.on_stage_start("test", [job])
        collector.on_job_start(job)
        collector.on_job_complete(JobOutcome(job=job, success=False, error=RuntimeError("boom")))
        collector.on_stage_complete("test", [JobOutcome(job=job, success=False)])
        collector.on_pipeline_complete(success=False)

        text = collector.summary().format_text()
        assert "Pipeline failed" in text
        assert "FAIL" in text
        assert "broken" in text

    def test_job_timing_is_non_negative(self):
        collector = self.run_simple_pipeline()
        summary = collector.summary()
        for jt in summary.jobs:
            assert jt.duration_s >= 0.0
        for st in summary.stages:
            assert st.duration_s >= 0.0
        assert summary.total_duration_s >= 0.0
