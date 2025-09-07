# tests/test_runner.py
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bitrab.execution.shell import run_bash, run_colored


def _bash_available() -> bool:
    """
    Rough availability check for bash used by the runner.
    - On POSIX: look for 'bash' on PATH.
    - On Windows: check the common Git-Bash path (same as your runner's default).
    """
    if os.name != "nt":
        return shutil.which("bash") is not None
    # Common Git-Bash location used by the runner
    default_git_bash = r"C:\Program Files\Git\bin\bash.exe"
    return os.path.exists(default_git_bash)


pytestmark = pytest.mark.skipif(not _bash_available(), reason="Bash not available for subprocess tests")


# ---------------- run_bash tests ----------------


def test_capture_mode_returns_stdout_and_stderr():
    res = run_bash(
        "echo out_line; echo err_line 1>&2",
        mode="capture",
        check=True,
    )
    assert "out_line" in res.stdout
    assert "err_line" in res.stderr
    assert res.returncode == 0


def test_stream_mode_is_captured_by_capsys(monkeypatch, capsys):
    # Disable ANSI to make assertions deterministic
    monkeypatch.setenv("NO_COLOR", "1")
    run_bash("echo hello; echo oops 1>&2", mode="stream", check=True)
    captured = capsys.readouterr()
    assert "hello" in captured.out
    assert "oops" in captured.err


def test_nonzero_exit_raises_calledprocesserror():
    with pytest.raises(subprocess.CalledProcessError) as ei:
        run_bash("echo hi; exit 7", mode="capture", check=True)
    assert ei.value.returncode == 7


def test_no_check_does_not_raise_and_captures_rc():
    res = run_bash("exit 5", mode="capture", check=False)
    assert res.returncode == 5


def test_env_and_cwd(tmp_path: Path):
    (tmp_path / "probe.sh").write_text("echo $FOO; pwd\n", encoding="utf-8")
    # Use capture for deterministic assertions
    res = run_bash(
        "bash ./probe.sh",
        env={"FOO": "barbaz"},
        cwd=str(tmp_path),
        mode="capture",
        check=True,
    )
    # stdout should contain FOO and working directory path
    lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
    assert "barbaz" in lines[0]
    assert str(tmp_path.name) in lines[1]


@pytest.mark.skipif(sys.platform != "win32", reason="Only works on windows!")
@pytest.mark.parametrize(
    "line_endings",
    ["unix", "windows"],
)
def test_crlf_normalization(line_endings: str):
    script = "printf 'X'\n"
    if line_endings == "windows":
        script = script.replace("\n", "\r\n")
    res = run_bash(script, mode="capture", check=True)
    assert res.stdout == "X"


def test_pipefail_is_prepended_and_causes_failure():
    # The first command in a pipeline fails; with pipefail the whole pipeline should fail.
    script = "false | cat\necho should_not_run\n"
    with pytest.raises(subprocess.CalledProcessError):
        run_bash(script, mode="capture", check=True)


# ---------------- shim (run_colored) tests ----------------


def test_run_colored_capture_produces_no_live_output(monkeypatch, capsys):
    # Disable colors for deterministic matching
    monkeypatch.setenv("NO_COLOR", "1")
    rc = run_colored("echo captured; echo err 1>&2", mode="capture")
    assert rc.returncode == 0
    captured = capsys.readouterr()
    # In capture mode, nothing should be *written* to live std streams
    assert "captured" not in captured.out
    assert "err" not in captured.err


def test_run_colored_stream_writes_to_std_streams(monkeypatch, capsys):
    monkeypatch.setenv("NO_COLOR", "1")
    rc = run_colored("echo streamed; echo errd 1>&2", mode="stream")
    assert rc.returncode == 0
    captured = capsys.readouterr()
    assert "streamed" in captured.out
    assert "errd" in captured.err


def test_run_colored_raises_on_nonzero():
    with pytest.raises(subprocess.CalledProcessError):
        run_colored("exit 9", mode="capture")
