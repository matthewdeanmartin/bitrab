"""Textual TUI application for bitrab pipeline visualization.

One tab per job, showing live streamed output. Tab colors indicate status:
  yellow = running, green = success, red = failed.

Keybindings:
  q       quit
  c       copy active tab's log to clipboard
  X       cancel pipeline (stops after current stage)
  r       restart entire pipeline
  R       run/restart selected job (use for manual jobs)
"""

from __future__ import annotations

import re
import subprocess  # nosec
import sys
import threading
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


def copy_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard. Returns True on success."""
    try:
        if sys.platform == "win32":
            proc = subprocess.run(  # nosec
                ["clip"],
                input=text,
                text=True,
                capture_output=True,
                check=False,
            )
            return proc.returncode == 0
        if sys.platform == "darwin":
            proc = subprocess.run(  # nosec
                ["pbcopy"],
                input=text,
                text=True,
                capture_output=True,
                check=False,
            )
            return proc.returncode == 0
        # Linux: try xclip, then xsel, then wl-copy
        for cmd in [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"], ["wl-copy"]]:
            try:
                proc = subprocess.run(cmd, input=text, text=True, capture_output=True, check=False)  # nosec
                if proc.returncode == 0:
                    return True
            except FileNotFoundError:
                continue
    except Exception:  # nosec B110
        pass
    return False


def extract_richlog_text(rich_log: RichLog) -> str:
    """Extract plain text from a RichLog widget's stored lines."""
    lines = []
    for line in rich_log.lines:
        if isinstance(line, Text):
            lines.append(line.plain)
        elif hasattr(line, "text"):
            lines.append(line.text)
        else:
            lines.append(str(line))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------

STATUS_ICONS = {
    "running": "⏳",
    "success": "✅",
    "failed": "❌",
    "warned": "⚠️",
    "pending": "🔲",
    "cancelled": "🚫",
}

APP_CSS = """
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
    CSS = APP_CSS
    BINDINGS = [
        Binding("q", "quit_app", "Quit", show=True),
        Binding("c", "copy_log", "Copy log", show=True),
        Binding("X", "cancel_pipeline", "Cancel pipeline", show=True),
        Binding("r", "restart_pipeline", "Restart pipeline", show=True),
        Binding("R", "restart_job", "Run/restart selected job", show=True),
    ]

    def __init__(
        self, pipeline: PipelineConfig, orchestrator: TUIOrchestrator, *, close_on_completion: bool = False
    ) -> None:
        super().__init__()
        self.pipeline = pipeline
        self.orchestrator = orchestrator
        self.close_on_completion = close_on_completion
        # Map job name → tab_id for fast lookup
        self.job_tab_ids: dict[str, str] = {}
        # Track pipeline success for exit code
        self.pipeline_success: bool | None = None
        # Set when on_pipeline_awaiting_manual has fired; prevents on_pipeline_complete
        # from overwriting the summary message.
        self.awaiting_manual: bool = False

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Build the initial widget tree."""
        yield Header()
        yield Static("Initializing pipeline…", id="summary")
        with Static(id="action-bar"):
            yield Button("🚫 Cancel  [X]", id="cancel-pipeline-btn", variant="warning")
            yield Button("↺ Restart pipeline  [r]", id="restart-pipeline-btn", variant="primary")
            yield Button("▶ Run/restart selected job  [R]", id="restart-job-btn", variant="default")
            yield Button("✕ Cancel job", id="cancel-job-btn", variant="error")
        with TabbedContent():
            for job in self.pipeline.jobs:
                tab_id = self.make_tab_id(job.name)
                self.job_tab_ids[job.name] = tab_id
                label = f"{STATUS_ICONS['pending']} {job.stage}/{job.name}"
                yield TabPane(label, RichLog(highlight=False, markup=False), id=tab_id)
        with Static(id="copy-bar"):
            yield Static("", id="copy-status")
            yield Button("📋 Copy log  [c]", id="copy-btn", variant="default")
        yield Footer()

    async def on_mount(self) -> None:
        """Start the pipeline worker once the UI is ready."""
        self.run_worker(self.run_pipeline_worker, thread=True, name="pipeline-runner")

    # ------------------------------------------------------------------
    # Worker (background thread)
    # ------------------------------------------------------------------

    def run_pipeline_worker(self) -> None:
        """Run inside a background thread. Calls orchestrator which uses ProcessPoolExecutor."""
        try:
            self.orchestrator.execute_pipeline_tui(self.pipeline, self)
        except Exception:  # nosec B110
            # on_pipeline_complete already called by orchestrator
            pass

    # ------------------------------------------------------------------
    # Thread-safe callbacks (called via call_from_thread)
    # ------------------------------------------------------------------

    def update_stage_status(self, stage: str, job_count: int, backend: str = "") -> None:
        """Update summary bar with current stage info."""
        summary = self.query_one("#summary", Static)
        backend_label = f"  [{backend}]" if backend else ""
        summary.update(f"Stage: [{stage}]  Running {job_count} job(s){backend_label}…")

    def on_pipeline_awaiting_manual(self) -> None:
        """Called when all auto-runnable jobs finished but manual jobs remain."""
        self.pipeline_success = True
        self.awaiting_manual = True
        summary = self.query_one("#summary", Static)
        summary.update("⏸️  Pipeline paused — manual jobs are ready to trigger.")
        if self.close_on_completion:
            self.exit(0)

    def on_pipeline_complete(self, success: bool) -> None:
        """Called by orchestrator when the whole pipeline finishes."""
        self.pipeline_success = success
        if not self.awaiting_manual:
            summary = self.query_one("#summary", Static)
            if success:
                summary.update("🎉 Pipeline completed successfully!")
            else:
                summary.update("❌ Pipeline failed.")
        if self.close_on_completion and not self.awaiting_manual:
            self.exit(0 if success else 1)

    def on_pipeline_cancelled(self) -> None:
        """Called by orchestrator when the pipeline is cancelled by user."""
        self.pipeline_success = False
        summary = self.query_one("#summary", Static)
        summary.update("🚫 Pipeline cancelled.")
        # Mark any still-running tabs as cancelled
        tabbed = self.query_one(TabbedContent)
        for job_name, tab_id in self.job_tab_ids.items():
            try:
                tab = tabbed.get_tab(tab_id)
                if tab and STATUS_ICONS["running"] in str(tab.label):
                    tab.label = self.job_label_for(job_name, "cancelled")  # type: ignore[assignment]
            except Exception:  # nosec B110
                pass
        if self.close_on_completion:
            self.exit(1)

    # ------------------------------------------------------------------
    # Message handlers (run on event loop thread)
    # ------------------------------------------------------------------

    def on_job_output(self, message: JobOutput) -> None:
        """Route job output text to the correct RichLog."""
        tab_id = self.job_tab_ids.get(message.job_name)
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
        tab_id = self.job_tab_ids.get(message.job_name)
        if not tab_id:
            return

        job_label = self.job_label_for(message.job_name, message.status)
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
        code = 0 if self.pipeline_success else 1
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
            text = extract_richlog_text(rich_log)
            if not text.strip():
                status_widget.update("Nothing to copy")
                return
            job_name = next((name for name, tid in self.job_tab_ids.items() if tid == active_id), active_id)
            line_count = len(text.splitlines())
            status_widget.update("⏳ Copying…")

            def do_copy() -> None:
                ok = copy_to_clipboard(text)
                if ok:
                    self.call_from_thread(status_widget.update, f"✅ Copied {line_count} lines from [{job_name}]")
                else:
                    self.call_from_thread(
                        status_widget.update, "⚠️  Clipboard unavailable — try selecting text with mouse"
                    )

            threading.Thread(target=do_copy, daemon=True).start()
        except Exception as exc:
            status_widget.update(f"❌ Copy failed: {exc}")

    def action_cancel_pipeline(self) -> None:
        """Signal the orchestrator to stop after the current stage."""
        self.orchestrator.cancel_pipeline()
        summary = self.query_one("#summary", Static)
        summary.update("🚫 Cancelling… (current stage will finish)")

    def action_cancel_job(self) -> None:
        """Send SIGTERM to the worker process running the active job."""
        try:
            tabbed = self.query_one(TabbedContent)
            active_id = tabbed.active
            if not active_id:
                return
            job_name = next((n for n, tid in self.job_tab_ids.items() if tid == active_id), None)
            if job_name:
                self.orchestrator.cancel_job(job_name)
        except Exception:  # nosec B110
            pass

    def action_restart_pipeline(self) -> None:
        """Reset all UI state and re-run the entire pipeline from scratch."""
        # Guard: don't restart while pipeline is actively running (not cancelled, not yet done)
        if self.pipeline_success is None and self.orchestrator.is_running():
            self.query_one("#copy-status", Static).update("Cancel the pipeline first before restarting.")
            return

        # Reset orchestrator state
        self.orchestrator.reset()
        self.pipeline_success = None

        # Reset all tabs: clear logs and reset labels to pending
        tabbed = self.query_one(TabbedContent)
        for job in self.pipeline.jobs:
            tab_id = self.job_tab_ids[job.name]
            try:
                tab = tabbed.get_tab(tab_id)
                if tab:
                    tab.label = self.job_label_for(job.name, "pending")  # type: ignore[assignment]
                rich_log = self.query_one(f"#{tab_id} RichLog", RichLog)
                rich_log.clear()
            except Exception:  # nosec B110
                pass

        summary = self.query_one("#summary", Static)
        summary.update("Restarting pipeline…")

        # Re-launch the pipeline worker
        self.run_worker(self.run_pipeline_worker, thread=True, name="pipeline-runner")

    def action_restart_job(self) -> None:
        """Re-run the job shown in the active tab."""
        try:
            tabbed = self.query_one(TabbedContent)
            active_id = tabbed.active
            if not active_id:
                return
            job_name = next((n for n, tid in self.job_tab_ids.items() if tid == active_id), None)
            if not job_name:
                return
            job = next((j for j in self.pipeline.jobs if j.name == job_name), None)
            if not job:
                return

            # Guard: don't restart a job that is still running
            tab = tabbed.get_tab(active_id)
            if tab and STATUS_ICONS["running"] in str(tab.label):
                self.query_one("#copy-status", Static).update("Job is still running.")
                return

            # Clear the log for this specific job
            rich_log = self.query_one(f"#{active_id} RichLog", RichLog)
            rich_log.clear()

            # Run the job in a new background thread
            self.run_worker(
                lambda: self.orchestrator.run_job_inline(job, self),
                thread=True,
                name=f"restart-{job_name}",
            )
        except Exception:  # nosec B110
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def make_tab_id(self, job_name: str) -> str:
        """Convert a job name to a valid CSS ID."""
        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", job_name)
        return f"job-{sanitized}"

    def job_label_for(self, job_name: str, status: str) -> str:
        """Build tab label with status icon, stage, and job name."""
        icon = STATUS_ICONS.get(status, "❓")
        stage = next((j.stage for j in self.pipeline.jobs if j.name == job_name), "?")
        return f"{icon} {stage}/{job_name}"
