"""Textual TUI application for bitrab pipeline visualization.

One tab per job, showing live streamed output. Tab colors indicate status:
  yellow = running, green = success, red = failed.

Keybindings:
  q       quit
  c       copy active tab's log to clipboard
  X       cancel pipeline (stops after current stage)
"""

from __future__ import annotations

import re
import subprocess  # nosec
import sys
from typing import TYPE_CHECKING

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Button, Footer, Header, RichLog, Static, TabbedContent, TabPane

from bitrab.models.pipeline import PipelineConfig

if TYPE_CHECKING:
    from bitrab.tui.orchestrator import TUIOrchestrator


# ---------------------------------------------------------------------------
# Messages (posted from background thread → event loop)
# ---------------------------------------------------------------------------


class JobOutput(Message):
    """Carries a line of output for a specific job."""

    def __init__(self, job_name: str, text: str) -> None:
        super().__init__()
        self.job_name = job_name
        self.text = text


class JobStatusChanged(Message):
    """Signals a job status transition."""

    # status values: "running" | "success" | "failed" | "cancelled"
    def __init__(self, job_name: str, status: str) -> None:
        super().__init__()
        self.job_name = job_name
        self.status = status


# ---------------------------------------------------------------------------
# Clipboard helper
# ---------------------------------------------------------------------------


def _copy_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard. Returns True on success."""
    try:
        if sys.platform == "win32":
            proc = subprocess.run(  # nosec
                ["clip"],
                input=text,
                text=True,
                capture_output=True,
            )
            return proc.returncode == 0
        if sys.platform == "darwin":
            proc = subprocess.run(  # nosec
                ["pbcopy"],
                input=text,
                text=True,
                capture_output=True,
            )
            return proc.returncode == 0
        # Linux: try xclip, then xsel, then wl-copy
        for cmd in [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"], ["wl-copy"]]:
            try:
                proc = subprocess.run(cmd, input=text, text=True, capture_output=True)  # nosec
                if proc.returncode == 0:
                    return True
            except FileNotFoundError:
                continue
    except Exception:  # nosec B110
        pass
    return False


def _extract_richlog_text(rich_log: RichLog) -> str:
    """Extract plain text from a RichLog widget's stored lines."""
    lines = []
    for line in rich_log.lines:
        if isinstance(line, Text):
            lines.append(line.plain)
        else:
            lines.append(str(line))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------

_STATUS_ICONS = {
    "running": "⏳",
    "success": "✅",
    "failed": "❌",
    "warned": "⚠️",
    "pending": "🔲",
    "cancelled": "🚫",
}

_CSS = """
Screen {
    background: $surface;
}

#summary {
    height: 3;
    border: solid $accent;
    padding: 0 1;
    color: $text;
}

#action-bar {
    height: 3;
    align: left middle;
    padding: 0 1;
}

#action-bar Button {
    margin-right: 1;
    min-width: 18;
}

#copy-bar {
    height: 3;
    align: right middle;
    padding: 0 1;
}

#copy-btn {
    min-width: 22;
}

#copy-status {
    width: 1fr;
    content-align: left middle;
    padding: 0 1;
    color: $text-muted;
}

TabbedContent {
    height: 1fr;
}

RichLog {
    scrollbar-gutter: stable;
}
"""


class PipelineApp(App[int]):
    """Textual TUI for monitoring a bitrab pipeline run.

    Attributes:
        TITLE: The application title.
        CSS: Application-level CSS.
        BINDINGS: Key bindings.
    """

    TITLE = "bitrab – GitLab CI Runner"
    CSS = _CSS
    BINDINGS = [
        Binding("q", "quit_app", "Quit", show=True),
        Binding("c", "copy_log", "Copy log", show=True),
        Binding("X", "cancel_pipeline", "Cancel pipeline", show=True),
    ]

    def __init__(self, pipeline: PipelineConfig, orchestrator: TUIOrchestrator) -> None:
        super().__init__()
        self._pipeline = pipeline
        self._orchestrator = orchestrator
        # Map job name → tab_id for fast lookup
        self._job_tab_ids: dict[str, str] = {}
        # Track pipeline success for exit code
        self._pipeline_success: bool | None = None

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Build the initial widget tree."""
        yield Header()
        yield Static("Initializing pipeline…", id="summary")
        with Static(id="action-bar"):
            yield Button("🚫 Cancel  [X]", id="cancel-pipeline-btn", variant="warning")
            yield Button("↺ Restart pipeline", id="restart-pipeline-btn", variant="primary")
            yield Button("↩ Restart job", id="restart-job-btn", variant="default")
            yield Button("✕ Cancel job", id="cancel-job-btn", variant="error")
        with TabbedContent():
            for job in self._pipeline.jobs:
                tab_id = self._make_tab_id(job.name)
                self._job_tab_ids[job.name] = tab_id
                label = f"{_STATUS_ICONS['pending']} {job.stage}/{job.name}"
                yield TabPane(label, RichLog(highlight=False, markup=False), id=tab_id)
        with Static(id="copy-bar"):
            yield Static("", id="copy-status")
            yield Button("📋 Copy log  [c]", id="copy-btn", variant="default")
        yield Footer()

    async def on_mount(self) -> None:
        """Start the pipeline worker once the UI is ready."""
        self.run_worker(self._run_pipeline_worker, thread=True, name="pipeline-runner")

    # ------------------------------------------------------------------
    # Worker (background thread)
    # ------------------------------------------------------------------

    def _run_pipeline_worker(self) -> None:
        """Run inside a background thread. Calls orchestrator which uses ProcessPoolExecutor."""
        try:
            self._orchestrator.execute_pipeline_tui(self._pipeline, self)
        except Exception:  # nosec B110
            # on_pipeline_complete already called by orchestrator
            pass

    # ------------------------------------------------------------------
    # Thread-safe callbacks (called via call_from_thread)
    # ------------------------------------------------------------------

    def update_stage_status(self, stage: str, job_count: int) -> None:
        """Update summary bar with current stage info."""
        summary = self.query_one("#summary", Static)
        summary.update(f"Stage: [{stage}]  Running {job_count} job(s)…")

    def on_pipeline_complete(self, success: bool) -> None:
        """Called by orchestrator when the whole pipeline finishes."""
        self._pipeline_success = success
        summary = self.query_one("#summary", Static)
        if success:
            summary.update("🎉 Pipeline completed successfully!")
        else:
            summary.update("❌ Pipeline failed.")

    def on_pipeline_cancelled(self) -> None:
        """Called by orchestrator when the pipeline is cancelled by user."""
        self._pipeline_success = False
        summary = self.query_one("#summary", Static)
        summary.update("🚫 Pipeline cancelled.")
        # Mark any still-running tabs as cancelled
        tabbed = self.query_one(TabbedContent)
        for job_name, tab_id in self._job_tab_ids.items():
            try:
                tab = tabbed.get_tab(tab_id)
                if tab and _STATUS_ICONS["running"] in str(tab.label):
                    tab.label = self._job_label_for(job_name, "cancelled")  # type: ignore[assignment]
            except Exception:  # nosec B110
                pass

    # ------------------------------------------------------------------
    # Message handlers (run on event loop thread)
    # ------------------------------------------------------------------

    def on_job_output(self, message: JobOutput) -> None:
        """Route job output text to the correct RichLog."""
        tab_id = self._job_tab_ids.get(message.job_name)
        if not tab_id:
            return
        try:
            rich_log = self.query_one(f"#{tab_id} RichLog", RichLog)
            # text is a full line from shell.py line-based streaming.
            # RichLog.write() appends its own newline, so strip trailing one first.
            for line in message.text.splitlines():
                rich_log.write(line)
        except Exception:  # nosec B110
            pass

    def on_job_status_changed(self, message: JobStatusChanged) -> None:
        """Update the tab label with status icon and switch active tab to running jobs."""
        tab_id = self._job_tab_ids.get(message.job_name)
        if not tab_id:
            return

        job_label = self._job_label_for(message.job_name, message.status)
        try:
            tabbed = self.query_one(TabbedContent)
            tab = tabbed.get_tab(tab_id)
            if tab:
                tab.label = job_label  # type: ignore[assignment]
            if message.status == "running":
                tabbed.active = tab_id
        except Exception:  # nosec B110
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks."""
        bid = event.button.id
        if bid == "copy-btn":
            self.action_copy_log()
        elif bid == "cancel-pipeline-btn":
            self.action_cancel_pipeline()
        elif bid == "restart-pipeline-btn":
            self.action_restart_pipeline()
        elif bid == "restart-job-btn":
            self.action_restart_job()
        elif bid == "cancel-job-btn":
            self.action_cancel_job()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_quit_app(self) -> None:
        """Quit with appropriate exit code."""
        code = 0 if self._pipeline_success else 1
        self.exit(code)

    def action_copy_log(self) -> None:
        """Copy the active tab's log content to the system clipboard."""
        status_widget = self.query_one("#copy-status", Static)
        try:
            tabbed = self.query_one(TabbedContent)
            active_id = tabbed.active
            if not active_id:
                status_widget.update("No active tab")
                return
            rich_log = self.query_one(f"#{active_id} RichLog", RichLog)
            text = _extract_richlog_text(rich_log)
            if not text.strip():
                status_widget.update("Nothing to copy")
                return
            if _copy_to_clipboard(text):
                # Show which job was copied
                job_name = next((name for name, tid in self._job_tab_ids.items() if tid == active_id), active_id)
                status_widget.update(f"✅ Copied {len(text.splitlines())} lines from [{job_name}]")
            else:
                status_widget.update("⚠️  Clipboard unavailable — try selecting text with mouse")
        except Exception as exc:
            status_widget.update(f"❌ Copy failed: {exc}")

    def action_cancel_pipeline(self) -> None:
        """Signal the orchestrator to stop after the current stage."""
        self._orchestrator.cancel_pipeline()
        summary = self.query_one("#summary", Static)
        summary.update("🚫 Cancelling… (current stage will finish)")

    def action_cancel_job(self) -> None:
        """Send SIGTERM to the worker process running the active job."""
        try:
            tabbed = self.query_one(TabbedContent)
            active_id = tabbed.active
            if not active_id:
                return
            job_name = next((n for n, tid in self._job_tab_ids.items() if tid == active_id), None)
            if job_name:
                self._orchestrator.cancel_job(job_name)
        except Exception:  # nosec B110
            pass

    def action_restart_pipeline(self) -> None:
        """Reset all UI state and re-run the entire pipeline from scratch."""
        # Guard: don't restart while pipeline is actively running (no cancel set, not yet done)
        if self._pipeline_success is None and not self._orchestrator._cancel_event.is_set():
            self.query_one("#copy-status", Static).update("Cancel the pipeline first before restarting.")
            return

        # Reset orchestrator state
        self._orchestrator.reset()
        self._pipeline_success = None

        # Reset all tabs: clear logs and reset labels to pending
        tabbed = self.query_one(TabbedContent)
        for job in self._pipeline.jobs:
            tab_id = self._job_tab_ids[job.name]
            try:
                tab = tabbed.get_tab(tab_id)
                if tab:
                    tab.label = self._job_label_for(job.name, "pending")  # type: ignore[assignment]
                rich_log = self.query_one(f"#{tab_id} RichLog", RichLog)
                rich_log.clear()
            except Exception:  # nosec B110
                pass

        summary = self.query_one("#summary", Static)
        summary.update("Restarting pipeline…")

        # Re-launch the pipeline worker
        self.run_worker(self._run_pipeline_worker, thread=True, name="pipeline-runner")

    def action_restart_job(self) -> None:
        """Re-run the job shown in the active tab."""
        try:
            tabbed = self.query_one(TabbedContent)
            active_id = tabbed.active
            if not active_id:
                return
            job_name = next((n for n, tid in self._job_tab_ids.items() if tid == active_id), None)
            if not job_name:
                return
            job = next((j for j in self._pipeline.jobs if j.name == job_name), None)
            if not job:
                return

            # Guard: don't restart a job that is still running
            tab = tabbed.get_tab(active_id)
            if tab and _STATUS_ICONS["running"] in str(tab.label):
                self.query_one("#copy-status", Static).update("Job is still running.")
                return

            # Clear the log for this specific job
            rich_log = self.query_one(f"#{active_id} RichLog", RichLog)
            rich_log.clear()

            # Run the job in a new background thread
            self.run_worker(
                lambda: self._orchestrator.run_job_inline(job, self),
                thread=True,
                name=f"restart-{job_name}",
            )
        except Exception:  # nosec B110
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_tab_id(self, job_name: str) -> str:
        """Convert a job name to a valid CSS ID."""
        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", job_name)
        return f"job-{sanitized}"

    def _job_label_for(self, job_name: str, status: str) -> str:
        """Build tab label with status icon, stage, and job name."""
        icon = _STATUS_ICONS.get(status, "❓")
        stage = next((j.stage for j in self._pipeline.jobs if j.name == job_name), "?")
        return f"{icon} {stage}/{job_name}"
