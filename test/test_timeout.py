"""Tests for FEATURE-5: job timeout support."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import threading

import pytest

from bitrab.exceptions import JobTimeoutError
from bitrab.execution.shell import run_bash
from bitrab.plan import PipelineProcessor, parse_duration

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def bash_available() -> bool:
    if os.name != "nt":
        return shutil.which("bash") is not None
    override = os.environ.get("BITRAB_BASH_PATH")
    if override:
        return os.path.isfile(override)
    if shutil.which("bash") is not None:
        return True
    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        r"C:\msys64\usr\bin\bash.exe",
        r"C:\msys\usr\bin\bash.exe",
    ]
    return any(os.path.isfile(c) for c in candidates)


bash_required = pytest.mark.skipif(not bash_available(), reason="Bash not available")


class _FakeCapturePopen:
    def __init__(self, *args, **kwargs):
        self.returncode = None
        self.killed = False
        self.communicate_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def communicate(self, *_args, timeout=None):
        self.communicate_calls += 1
        if self.communicate_calls == 1:
            raise subprocess.TimeoutExpired(cmd="bash", timeout=timeout)
        self.returncode = -9
        return ("", "")

    def kill(self):
        self.killed = True
        self.returncode = -9


class _FakeStreamPopen:
    def __init__(self, *args, **kwargs):
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")
        self.stdin = io.StringIO()
        self.returncode = None
        self.killed = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def wait(self):
        self.killed.wait(timeout=1)
        self.returncode = -9
        return self.returncode

    def kill(self):
        self.returncode = -9
        self.killed.set()


# ---------------------------------------------------------------------------
# parse_duration tests (no bash needed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        (None, None),
        ("", None),
        (0, 0.0),
        (3600, 3600.0),
        (3600.5, 3600.5),
        ("30m", 1800.0),
        ("1h", 3600.0),
        ("1h 30m", 5400.0),
        ("2h 30m 15s", 9015.0),
        ("90s", 90.0),
        ("1d", 86400.0),
        ("1w", 604800.0),
        ("3600", 3600.0),
        ("30M", 1800.0),  # case-insensitive
        ("1H 30M", 5400.0),
    ],
)
def test_parse_duration(value, expected):
    result = parse_duration(value)
    assert result == expected


def test_parse_duration_invalid_returns_none():
    # Completely unrecognisable strings yield None
    result = parse_duration("not-a-duration")
    assert result is None


# ---------------------------------------------------------------------------
# JobConfig.timeout is set by PipelineProcessor
# ---------------------------------------------------------------------------


def make_processor():
    return PipelineProcessor()


def test_timeout_parsed_as_seconds():
    proc = make_processor()
    raw = {
        "stages": ["test"],
        "myjob": {
            "stage": "test",
            "script": ["echo hi"],
            "timeout": "30m",
        },
    }
    pipeline = proc.process_config(raw)
    assert pipeline.jobs[0].timeout == 1800.0


def test_timeout_parsed_as_integer_string():
    proc = make_processor()
    raw = {
        "stages": ["test"],
        "myjob": {
            "stage": "test",
            "script": ["echo hi"],
            "timeout": 120,
        },
    }
    pipeline = proc.process_config(raw)
    assert pipeline.jobs[0].timeout == 120.0


def test_timeout_defaults_to_none():
    proc = make_processor()
    raw = {
        "stages": ["test"],
        "myjob": {"stage": "test", "script": ["echo hi"]},
    }
    pipeline = proc.process_config(raw)
    assert pipeline.jobs[0].timeout is None


# ---------------------------------------------------------------------------
# run_bash timeout – capture mode
# ---------------------------------------------------------------------------


def test_capture_mode_timeout_raises_job_timeout_error(monkeypatch):
    monkeypatch.setattr("bitrab.execution.shell.subprocess.Popen", _FakeCapturePopen)

    with pytest.raises(JobTimeoutError):
        run_bash("sleep 10", mode="capture", check=False, timeout=0.5)


@bash_required
def test_capture_mode_no_timeout_completes():
    res = run_bash("echo done", mode="capture", check=False, timeout=10.0)
    assert res.returncode == 0
    assert "done" in res.stdout


# ---------------------------------------------------------------------------
# run_bash timeout – stream mode
# ---------------------------------------------------------------------------


def test_stream_mode_timeout_raises_job_timeout_error(monkeypatch):
    monkeypatch.setattr("bitrab.execution.shell.subprocess.Popen", _FakeStreamPopen)

    with pytest.raises(JobTimeoutError):
        run_bash("sleep 10", mode="stream", check=False, timeout=0.01)


@bash_required
def test_stream_mode_no_timeout_completes():
    res = run_bash("echo done", mode="stream", check=False, timeout=10.0)
    assert res.returncode == 0
    assert "done" in res.stdout
