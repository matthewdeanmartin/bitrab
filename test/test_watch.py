"""Tests for D4: watch mode."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bitrab.watch import _collect_watched_paths, _PipelineRerunHandler


class TestCollectWatchedPaths:
    def test_empty_config_returns_empty_set(self, tmp_path):
        """Config with no includes returns an empty set."""
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text("stages:\n  - test\njob:\n  script:\n    - echo\n", encoding="utf-8")
        paths = _collect_watched_paths(ci.resolve())
        assert paths == set()

    def test_local_include_collected(self, tmp_path):
        sub = tmp_path / "sub.yml"
        sub.write_text("job2:\n  script:\n    - echo\n", encoding="utf-8")
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text("include:\n  - local: sub.yml\n", encoding="utf-8")

        paths = _collect_watched_paths(ci.resolve())
        assert sub.resolve() in paths

    def test_transitive_includes_collected(self, tmp_path):
        c = tmp_path / "c.yml"
        c.write_text("job_c:\n  script:\n    - echo\n", encoding="utf-8")
        b = tmp_path / "b.yml"
        b.write_text("include:\n  - local: c.yml\n", encoding="utf-8")
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text("include:\n  - local: b.yml\n", encoding="utf-8")

        paths = _collect_watched_paths(ci.resolve())
        assert b.resolve() in paths
        assert c.resolve() in paths

    def test_remote_includes_not_collected(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text("include:\n  - remote: https://example.com/ci.yml\n", encoding="utf-8")

        paths = _collect_watched_paths(ci.resolve())
        assert paths == set()

    def test_circular_includes_safe(self, tmp_path):
        a = tmp_path / "a.yml"
        b = tmp_path / "b.yml"
        a.write_text("include:\n  - local: b.yml\n", encoding="utf-8")
        b.write_text("include:\n  - local: a.yml\n", encoding="utf-8")
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text("include:\n  - local: a.yml\n", encoding="utf-8")

        # Should not recurse infinitely
        paths = _collect_watched_paths(ci.resolve())
        assert a.resolve() in paths
        assert b.resolve() in paths


class TestPipelineRerunHandler:
    def _make_event(self, path: Path, is_directory: bool = False) -> MagicMock:
        event = MagicMock()
        event.src_path = str(path)
        event.is_directory = is_directory
        return event

    def test_debounce_prevents_double_trigger(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.touch()

        runner = MagicMock()
        handler = _PipelineRerunHandler(runner, {ci.resolve()})

        event = self._make_event(ci.resolve())
        handler.on_modified(event)
        handler.on_modified(event)  # within debounce window

        assert runner.call_count == 1

    def test_skips_unrelated_files(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.touch()
        other = tmp_path / "other.py"
        other.touch()

        runner = MagicMock()
        handler = _PipelineRerunHandler(runner, {ci.resolve()})

        handler.on_modified(self._make_event(other.resolve()))
        assert runner.call_count == 0

    def test_skips_directory_events(self, tmp_path):
        ci = tmp_path / ".gitlab-ci.yml"
        ci.touch()

        runner = MagicMock()
        handler = _PipelineRerunHandler(runner, {ci.resolve()})

        handler.on_modified(self._make_event(ci.resolve(), is_directory=True))
        assert runner.call_count == 0

    def test_triggers_after_debounce_window(self, tmp_path):
        """After the debounce window, a second event triggers the runner."""
        ci = tmp_path / ".gitlab-ci.yml"
        ci.touch()

        runner = MagicMock()
        handler = _PipelineRerunHandler(runner, {ci.resolve()})

        event = self._make_event(ci.resolve())
        handler.on_modified(event)
        # Force last_triggered into the past
        handler._last_triggered = time.monotonic() - 2.0
        handler.on_modified(event)

        assert runner.call_count == 2

    def test_runner_exception_does_not_crash_handler(self, tmp_path):
        """A failing pipeline run should not crash the watch loop."""
        ci = tmp_path / ".gitlab-ci.yml"
        ci.touch()

        runner = MagicMock(side_effect=RuntimeError("boom"))
        handler = _PipelineRerunHandler(runner, {ci.resolve()})

        # Should not raise
        handler.on_modified(self._make_event(ci.resolve()))


class TestCmdWatch:
    def test_cmd_watch_config_not_found(self, tmp_path):
        """cmd_watch exits with code 1 when config is missing."""
        import argparse

        from bitrab.cli import cmd_watch

        args = argparse.Namespace(
            config=str(tmp_path / "nonexistent.yml"),
            dry_run=False,
            parallel=None,
            jobs=None,
            stage=None,
        )

        with pytest.raises(SystemExit) as exc_info:
            cmd_watch(args)

        assert exc_info.value.code == 1
