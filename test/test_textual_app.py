from __future__ import annotations

import asyncio

from textual.widgets import RichLog, Static, TabbedContent

from bitrab.models.pipeline import JobConfig, PipelineConfig
from bitrab.tui.app import JobOutput, JobStatusChanged, PipelineApp, _extract_richlog_text


class DummyOrchestrator:
    def __init__(self) -> None:
        self.execute_calls = 0
        self.cancel_pipeline_calls = 0
        self.cancel_job_calls: list[str] = []
        self.reset_calls = 0
        self.inline_job_runs: list[str] = []
        self.running = True

    def execute_pipeline_tui(self, pipeline: PipelineConfig, app: PipelineApp) -> None:
        self.execute_calls += 1

    def cancel_pipeline(self) -> None:
        self.cancel_pipeline_calls += 1
        self.running = False

    def cancel_job(self, job_name: str) -> None:
        self.cancel_job_calls.append(job_name)

    def reset(self) -> None:
        self.reset_calls += 1
        self.running = True

    def is_running(self) -> bool:
        return self.running

    def run_job_inline(self, job: JobConfig, app: PipelineApp) -> None:
        self.inline_job_runs.append(job.name)


class CompletingOrchestrator(DummyOrchestrator):
    def __init__(self, success: bool = True) -> None:
        super().__init__()
        self.success = success

    def execute_pipeline_tui(self, pipeline: PipelineConfig, app: PipelineApp) -> None:
        super().execute_pipeline_tui(pipeline, app)
        app.call_from_thread(app.on_pipeline_complete, self.success)


def _pipeline() -> PipelineConfig:
    return PipelineConfig(
        stages=["build", "test"],
        jobs=[
            JobConfig(name="build-job", stage="build", script=["echo build"]),
            JobConfig(name="test-job", stage="test", script=["echo test"]),
        ],
    )


def _static_text(widget: Static) -> str:
    content = widget.content
    return content.plain if hasattr(content, "plain") else str(content)


def test_pipeline_app_renders_initial_state() -> None:
    async def scenario() -> None:
        orchestrator = DummyOrchestrator()
        app = PipelineApp(_pipeline(), orchestrator)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            assert orchestrator.execute_calls == 1

            summary = app.query_one("#summary", Static)
            assert "Initializing pipeline" in _static_text(summary)

            tabbed = app.query_one(TabbedContent)
            build_tab = tabbed.get_tab(app._job_tab_ids["build-job"])
            test_tab = tabbed.get_tab(app._job_tab_ids["test-job"])
            assert build_tab is not None
            assert test_tab is not None
            assert "🔲 build/build-job" in str(build_tab.label)
            assert "🔲 test/test-job" in str(test_tab.label)

    asyncio.run(scenario())


def test_pipeline_app_routes_status_and_output_messages() -> None:
    async def scenario() -> None:
        app = PipelineApp(_pipeline(), DummyOrchestrator())

        async with app.run_test(size=(120, 40)) as pilot:
            app.post_message(JobStatusChanged("build-job", "running"))
            app.post_message(JobOutput("build-job", "line one\nline two\n"))
            await pilot.pause()

            tabbed = app.query_one(TabbedContent)
            build_tab_id = app._job_tab_ids["build-job"]
            build_tab = tabbed.get_tab(build_tab_id)
            assert tabbed.active == build_tab_id
            assert build_tab is not None
            assert "⏳ build/build-job" in str(build_tab.label)

            rich_log = app.query_one(f"#{build_tab_id} RichLog", RichLog)
            assert _extract_richlog_text(rich_log) == "line one\nline two"

            app.post_message(JobStatusChanged("build-job", "success"))
            await pilot.pause()
            assert "✅ build/build-job" in str(tabbed.get_tab(build_tab_id).label)

    asyncio.run(scenario())


def test_pipeline_app_cancel_pipeline_button_updates_summary() -> None:
    async def scenario() -> None:
        orchestrator = DummyOrchestrator()
        app = PipelineApp(_pipeline(), orchestrator)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.click("#cancel-pipeline-btn")
            await pilot.pause()

            assert orchestrator.cancel_pipeline_calls == 1
            summary = app.query_one("#summary", Static)
            assert "Cancelling" in _static_text(summary)

    asyncio.run(scenario())


def test_pipeline_app_copy_button_reports_success(monkeypatch) -> None:
    monkeypatch.setattr("bitrab.tui.app._copy_to_clipboard", lambda text: True)

    async def scenario() -> None:
        app = PipelineApp(_pipeline(), DummyOrchestrator())

        async with app.run_test(size=(120, 40)) as pilot:
            app.post_message(JobStatusChanged("build-job", "running"))
            app.post_message(JobOutput("build-job", "copied line\n"))
            await pilot.pause()

            await pilot.click("#copy-btn")
            await pilot.pause()

            status = app.query_one("#copy-status", Static)
            assert "Copied 1 lines from [build-job]" in _static_text(status)

    asyncio.run(scenario())


def test_pipeline_app_can_close_on_completion() -> None:
    async def scenario() -> None:
        orchestrator = CompletingOrchestrator(success=True)
        app = PipelineApp(_pipeline(), orchestrator, close_on_completion=True)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.pause()

        assert app.return_code == 0
        assert orchestrator.execute_calls == 1

    asyncio.run(scenario())
