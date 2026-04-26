"""Tests for bitrab.tui.orchestrator — non-TUI portions only."""

from __future__ import annotations

import sys
from pathlib import Path
from queue import SimpleQueue
from unittest.mock import MagicMock, patch

import pytest

from bitrab.execution.job import JobExecutor
from bitrab.execution.stage_runner import JobOutcome
from bitrab.execution.variables import VariableManager
from bitrab.models.pipeline import JobConfig, PipelineConfig
from bitrab.tui.orchestrator import (
    QueueWriter,
    TUIOrchestrator,
    _CIFileCallbacks,
    _run_single_job_file,
    _run_single_job_queued,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_executor(tmp_path: Path) -> JobExecutor:
    vm = VariableManager(project_dir=tmp_path)
    return JobExecutor(variable_manager=vm, project_dir=tmp_path)


def _make_job(name: str = "my-job", stage: str = "test") -> JobConfig:
    return JobConfig(name=name, stage=stage)


def _make_orchestrator(tmp_path: Path, parallelism: int = 1) -> TUIOrchestrator:
    executor = _make_executor(tmp_path)
    return TUIOrchestrator(job_executor=executor, maximum_degree_of_parallelism=parallelism)


# ---------------------------------------------------------------------------
# QueueWriter
# ---------------------------------------------------------------------------


class TestQueueWriter:
    def test_write_puts_tuple_on_queue(self):
        q = SimpleQueue()
        writer = QueueWriter(q, "my-job")
        writer.write("hello")
        assert q.get_nowait() == ("my-job", "hello")

    def test_write_skips_empty_string(self):
        q = SimpleQueue()
        writer = QueueWriter(q, "my-job")
        writer.write("")
        assert q.empty()

    def test_flush_is_noop(self):
        q = SimpleQueue()
        writer = QueueWriter(q, "my-job")
        writer.flush()  # must not raise
        assert q.empty()

    def test_multiple_writes(self):
        q = SimpleQueue()
        writer = QueueWriter(q, "job-a")
        writer.write("line1")
        writer.write("line2")
        assert q.get_nowait() == ("job-a", "line1")
        assert q.get_nowait() == ("job-a", "line2")


# ---------------------------------------------------------------------------
# _run_single_job_queued
# ---------------------------------------------------------------------------


class TestRunSingleJobQueued:
    def test_puts_sentinel_on_queue(self, tmp_path):
        q = SimpleQueue()
        executor = _make_executor(tmp_path)
        executor.build_context = MagicMock(return_value=MagicMock())
        executor.execute_job = MagicMock()
        executor.job_history = []

        job = _make_job()
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        _run_single_job_queued(job, executor, job_dir, output_queue=q)

        # Sentinel must be the last item
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert items[-1] == ("my-job", None)

    def test_records_worker_pid(self, tmp_path):
        q = SimpleQueue()
        executor = _make_executor(tmp_path)
        executor.build_context = MagicMock(return_value=MagicMock())
        executor.execute_job = MagicMock()
        executor.job_history = []

        worker_pids: dict = {}
        job = _make_job()
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        _run_single_job_queued(job, executor, job_dir, output_queue=q, worker_pids=worker_pids)

        import os

        assert worker_pids["my-job"] == os.getpid()

    def test_sentinel_sent_even_on_execute_error(self, tmp_path):
        q = SimpleQueue()
        executor = _make_executor(tmp_path)
        executor.build_context = MagicMock(return_value=MagicMock())
        executor.execute_job = MagicMock(side_effect=RuntimeError("boom"))
        executor.job_history = []

        job = _make_job()
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        with pytest.raises(RuntimeError):
            _run_single_job_queued(job, executor, job_dir, output_queue=q)

        items = []
        while not q.empty():
            items.append(q.get_nowait())
        assert items[-1] == ("my-job", None)

    def test_returns_job_history(self, tmp_path):
        q = SimpleQueue()
        executor = _make_executor(tmp_path)
        executor.build_context = MagicMock(return_value=MagicMock())
        executor.execute_job = MagicMock()
        sentinel = object()
        executor.job_history = [sentinel]

        job = _make_job()
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        result = _run_single_job_queued(job, executor, job_dir, output_queue=q)
        assert result is executor.job_history


# ---------------------------------------------------------------------------
# _run_single_job_file
# ---------------------------------------------------------------------------


class TestRunSingleJobFile:
    def test_creates_log_file(self, tmp_path):
        executor = _make_executor(tmp_path)
        executor.build_context = MagicMock(return_value=MagicMock())
        executor.execute_job = MagicMock()
        executor.job_history = []

        job = _make_job()
        job_dir = tmp_path / "job"
        log_path = job_dir / "output.log"

        _run_single_job_file(job, executor, job_dir, log_path=str(log_path))

        assert log_path.exists()

    def test_returns_job_history(self, tmp_path):
        executor = _make_executor(tmp_path)
        executor.build_context = MagicMock(return_value=MagicMock())
        executor.execute_job = MagicMock()
        sentinel = object()
        executor.job_history = [sentinel]

        job = _make_job()
        job_dir = tmp_path / "job"
        log_path = job_dir / "output.log"

        result = _run_single_job_file(job, executor, job_dir, log_path=str(log_path))
        assert result is executor.job_history

    def test_creates_nested_job_dir(self, tmp_path):
        executor = _make_executor(tmp_path)
        executor.build_context = MagicMock(return_value=MagicMock())
        executor.execute_job = MagicMock()
        executor.job_history = []

        job = _make_job()
        job_dir = tmp_path / "deep" / "nested" / "job"
        log_path = job_dir / "output.log"

        _run_single_job_file(job, executor, job_dir, log_path=str(log_path))
        assert job_dir.exists()


# ---------------------------------------------------------------------------
# _CIFileCallbacks
# ---------------------------------------------------------------------------


class TestCIFileCallbacks:
    def _make_pipeline(self) -> PipelineConfig:
        return PipelineConfig(stages=["build", "test"])

    def _make_outcome(self, name: str, success: bool, stage: str = "test") -> JobOutcome:
        return JobOutcome(job=_make_job(name=name, stage=stage), success=success)

    def test_on_pipeline_start_prints_stages(self, capsys):
        cb = _CIFileCallbacks()
        cb.on_pipeline_start(self._make_pipeline(), max_workers=2)
        out = capsys.readouterr().out
        assert "build" in out
        assert "test" in out

    def test_on_pipeline_complete_success_prints_message(self, capsys):
        cb = _CIFileCallbacks()
        cb.on_pipeline_complete(success=True)
        assert "successfully" in capsys.readouterr().out.lower()

    def test_on_pipeline_complete_failure_prints_nothing(self, capsys):
        cb = _CIFileCallbacks()
        cb.on_pipeline_complete(success=False)
        assert capsys.readouterr().out == ""

    def test_on_stage_start_prints_stage_name(self, capsys):
        cb = _CIFileCallbacks()
        jobs = [_make_job("j1"), _make_job("j2")]
        cb.on_stage_start("build", jobs)
        out = capsys.readouterr().out
        assert "build" in out
        assert "2" in out

    def test_on_stage_start_resets_log_paths(self, tmp_path):
        cb = _CIFileCallbacks()
        cb._log_paths = {"stale": tmp_path / "stale.log"}
        cb.on_stage_start("build", [])
        assert cb._log_paths == {}

    def test_on_stage_skip_prints_stage_name(self, capsys):
        cb = _CIFileCallbacks()
        cb.on_stage_skip("deploy")
        assert "deploy" in capsys.readouterr().out

    def test_on_job_start_prints_job_name(self, capsys):
        cb = _CIFileCallbacks()
        cb.on_job_start(_make_job("my-job"))
        assert "my-job" in capsys.readouterr().out

    def test_on_stage_complete_prints_success_marker(self, capsys, tmp_path):
        cb = _CIFileCallbacks()
        job = _make_job("j1")
        cb._current_stage_jobs = [job]
        log = tmp_path / "j1.log"
        log.write_text("some output", encoding="utf-8")
        cb._log_paths = {"j1": log}

        cb.on_stage_complete("test", [self._make_outcome("j1", success=True)])
        out = capsys.readouterr().out
        assert "j1" in out
        assert "some output" in out

    def test_on_stage_complete_prints_failure_marker(self, capsys, tmp_path):
        cb = _CIFileCallbacks()
        job = _make_job("j1")
        cb._current_stage_jobs = [job]
        log = tmp_path / "j1.log"
        log.write_text("error here", encoding="utf-8")
        cb._log_paths = {"j1": log}

        cb.on_stage_complete("test", [self._make_outcome("j1", success=False)])
        out = capsys.readouterr().out
        assert "j1" in out
        assert "Stopping pipeline" in out

    def test_on_stage_complete_no_log_file(self, capsys):
        cb = _CIFileCallbacks()
        cb._current_stage_jobs = [_make_job("j1")]
        cb._log_paths = {}

        cb.on_stage_complete("test", [self._make_outcome("j1", success=True)])
        assert "no output" in capsys.readouterr().out

    def test_make_output_writer_creates_file(self, tmp_path):
        cb = _CIFileCallbacks()
        job = _make_job("j1")
        job_dir = tmp_path / "j1"
        job_dir.mkdir()

        fh = cb.make_output_writer(job, job_dir)
        fh.write("hello")
        fh.close()

        assert (job_dir / "output.log").read_text() == "hello"

    def test_make_output_writer_registers_log_path(self, tmp_path):
        cb = _CIFileCallbacks()
        job = _make_job("j1")
        job_dir = tmp_path / "j1"
        job_dir.mkdir()

        fh = cb.make_output_writer(job, job_dir)
        fh.close()

        assert "j1" in cb._log_paths
        assert cb._log_paths["j1"] == job_dir / "output.log"

    def test_get_worker_func_returns_file_worker(self):
        cb = _CIFileCallbacks()
        assert cb.get_worker_func() is _run_single_job_file

    def test_make_worker_args_returns_log_path(self, tmp_path):
        cb = _CIFileCallbacks()
        job = _make_job("j1")
        job_dir = tmp_path / "j1"

        args = cb.make_worker_args(job, job_dir)
        assert "log_path" in args
        assert args["log_path"] == str(job_dir / "output.log")

    def test_make_worker_args_registers_log_path(self, tmp_path):
        cb = _CIFileCallbacks()
        job = _make_job("j1")
        job_dir = tmp_path / "j1"

        cb.make_worker_args(job, job_dir)
        assert cb._log_paths["j1"] == job_dir / "output.log"


# ---------------------------------------------------------------------------
# TUIOrchestrator — state / control methods (no TUI needed)
# ---------------------------------------------------------------------------


class TestTUIOrchestratorControl:
    def test_is_running_true_initially(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        assert orch.is_running() is True

    def test_cancel_pipeline_stops_is_running(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        orch.cancel_pipeline()
        assert orch.is_running() is False

    def test_reset_clears_cancel(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        orch.cancel_pipeline()
        orch.reset()
        assert orch.is_running() is True

    def test_reset_clears_worker_pids(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        orch._worker_pids = {"job-a": 12345}
        orch.reset()
        assert orch._worker_pids == {}

    def test_event_collector_none_before_run(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        assert orch.event_collector is None

    def test_maximum_parallelism_defaults_to_cpu_count(self, tmp_path):
        import os as _os

        executor = _make_executor(tmp_path)
        orch = TUIOrchestrator(job_executor=executor)  # no parallelism kwarg
        assert orch.maximum_degree_of_parallelism == (_os.cpu_count() or 1)

    def test_maximum_parallelism_clamped_to_one(self, tmp_path):
        executor = _make_executor(tmp_path)
        orch = TUIOrchestrator(job_executor=executor, maximum_degree_of_parallelism=0)
        assert orch.maximum_degree_of_parallelism == 1

    def test_maximum_parallelism_negative_clamped_to_one(self, tmp_path):
        executor = _make_executor(tmp_path)
        orch = TUIOrchestrator(job_executor=executor, maximum_degree_of_parallelism=-5)
        assert orch.maximum_degree_of_parallelism == 1

    def test_cancel_job_noop_when_pid_unknown(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        orch.cancel_job("nonexistent-job")  # must not raise

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific kill path")
    def test_cancel_job_calls_taskkill_on_windows(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        orch._worker_pids = {"j1": 99999}
        with patch("subprocess.run") as mock_run:
            orch.cancel_job("j1")
        args = mock_run.call_args[0][0]
        assert "taskkill" in args
        assert "99999" in args

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix-specific kill path")
    def test_cancel_job_sends_sigterm_on_unix(self, tmp_path):
        import signal as _signal

        orch = _make_orchestrator(tmp_path)
        orch._worker_pids = {"j1": 99999}
        with patch("os.kill") as mock_kill:
            orch.cancel_job("j1")
        mock_kill.assert_called_once_with(99999, _signal.SIGTERM)

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix kill path")
    def test_cancel_job_ignores_process_lookup_error(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        orch._worker_pids = {"j1": 99999}
        with patch("os.kill", side_effect=ProcessLookupError):
            orch.cancel_job("j1")  # must not raise

    @pytest.mark.skipif(sys.platform == "win32", reason="Unix kill path")
    def test_cancel_job_ignores_permission_error(self, tmp_path):
        orch = _make_orchestrator(tmp_path)
        orch._worker_pids = {"j1": 99999}
        with patch("os.kill", side_effect=PermissionError):
            orch.cancel_job("j1")  # must not raise
