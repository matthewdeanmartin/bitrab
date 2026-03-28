"""Tests for FEATURE-5: job timeout support."""

from __future__ import annotations

import os
import shutil

import pytest

from bitrab.exceptions import JobTimeoutError
from bitrab.execution.shell import run_bash
from bitrab.plan import PipelineProcessor, parse_duration

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bash_available() -> bool:
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


bash_required = pytest.mark.skipif(not _bash_available(), reason="Bash not available")


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


def _make_processor():
    return PipelineProcessor()


def test_timeout_parsed_as_seconds():
    proc = _make_processor()
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
    proc = _make_processor()
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
    proc = _make_processor()
    raw = {
        "stages": ["test"],
        "myjob": {"stage": "test", "script": ["echo hi"]},
    }
    pipeline = proc.process_config(raw)
    assert pipeline.jobs[0].timeout is None


# ---------------------------------------------------------------------------
# run_bash timeout – capture mode
# ---------------------------------------------------------------------------


@bash_required
def test_capture_mode_timeout_raises_job_timeout_error():
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


@bash_required
def test_stream_mode_timeout_raises_job_timeout_error():
    with pytest.raises(JobTimeoutError):
        run_bash("sleep 10", mode="stream", check=False, timeout=0.5)


@bash_required
def test_stream_mode_no_timeout_completes():
    res = run_bash("echo done", mode="stream", check=False, timeout=10.0)
    assert res.returncode == 0
    assert "done" in res.stdout
