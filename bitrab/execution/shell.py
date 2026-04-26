"""
A subprocess/bash runner that supports **streaming** (threads for real-time output)
*and* a **captured** mode that plays perfectly with pytest and CI.

- Streaming mode: still returns captured stdout/stderr, while also teeing to
  provided targets (default: sys.stdout/sys.stderr). Pytest's `capsys` can capture
  these writes; but if you want absolute determinism, use captured mode.
- Captured mode: no threads; uses `Popen.communicate()` and returns stdout/stderr
  as strings without writing to live streams. Ideal for unit tests and CI logs.
- Color handling respects NO_COLOR; can be forced on/off.
- Windows CRLF normalization for scripts fed via stdin.
- Optional `check` raises on non-zero return code.

Usage:

    result = run_bash(
        "echo hello && echo oops >&2",
        mode="capture",  # or "stream"
        check=False,
    )
    assert result.stdout.strip() == "hello"
    assert "oops" in result.stderr

In pytest with capsys (streaming):

    def test_streaming(capsys):
        run_bash("echo hi", mode="stream")
        captured = capsys.readouterr()
        assert "hi" in captured.out

Deterministic (captured) tests:

    def test_captured():
        res = run_bash("printf '%s' foo", mode="capture")
        assert res.stdout == "foo"
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess  # nosec
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import IO, Any, Protocol, runtime_checkable

from bitrab.exceptions import BitrabError, JobTimeoutError


@runtime_checkable
class TextWriter(Protocol):
    """Minimal write/flush interface for job output targets."""

    def write(self, s: str) -> Any: ...
    def flush(self) -> Any: ...


# ---------- Color helpers ----------
GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def colors_enabled(force: bool | None) -> bool:
    if force is True:
        return True
    if force is False:
        return False
    return not bool(os.getenv("NO_COLOR"))


# ---------- Result container ----------
@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def stdout_clean(self) -> str:
        return ANSI_ESCAPE_RE.sub("", self.stdout)

    @property
    def stderr_clean(self) -> str:
        return ANSI_ESCAPE_RE.sub("", self.stderr)

    def check_returncode(self) -> RunResult:
        if self.returncode != 0:
            print(self.stderr)
            print(self.stdout)
            raise subprocess.CalledProcessError(self.returncode, "<bash stdin>", self.stdout, self.stderr)
        return self


# ---------- Env merge ----------
def merge_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Return a merged environment where `env` overrides current process env."""
    current_env = os.environ.copy()
    if env:
        return {**current_env, **env}
    return current_env


# ---------- Core runner ----------
class Buffer:
    """A very small helper to collect text while also acting like a file-like object."""

    def __init__(self, target: Any = None) -> None:
        self.buf: list[str] = []
        self.target = target

    def write(self, s: str) -> None:  # type: ignore[override]
        self.buf.append(s)
        if self.target is not None:
            self.target.write(s)

    def flush(self) -> None:  # type: ignore[override]
        if self.target is not None:
            self.target.flush()

    def getvalue(self) -> str:
        return "".join(self.buf)


BASH_WINDOWS_CANDIDATES = [
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
    r"C:\msys64\usr\bin\bash.exe",
    r"C:\msys\usr\bin\bash.exe",
]


def windows_bash_candidates() -> list[str]:
    """Return Windows bash candidate paths, including %PROGRAMFILES% expansions.

    Hardcoded ``C:\\Program Files\\...`` paths miss installations on other
    drives (e.g. Git for Windows installed to D:).  We probe the environment
    so the actual ProgramFiles location is honoured first.
    """
    candidates: list[str] = []
    for env_key in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(env_key)
        if base:
            candidates.append(os.path.join(base, "Git", "bin", "bash.exe"))
    candidates.extend(BASH_WINDOWS_CANDIDATES)
    # de-duplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        norm = os.path.normcase(c)
        if norm not in seen:
            seen.add(norm)
            unique.append(c)
    return unique


CACHED_BASH_PATH: str | None = None


def is_wsl_bash(path: str) -> bool:
    # System32\bash.exe and the Store alias (WindowsApps\bash.exe) are WSL shims.
    # They can't see Windows-style paths like C:\Users\...\tmp\pytest-of-...
    # so jobs driven by bitrab (which pass Windows cwd/env) break inside WSL.
    norm = path.replace("/", "\\").lower()
    return "\\system32\\bash.exe" in norm or "\\windowsapps\\bash.exe" in norm


def find_bash_windows() -> str:
    """Find bash on Windows: env override → PATH → common locations.

    Skips WSL's bash.exe shim and raises if nothing usable exists, so callers
    get a clear error instead of a cryptic Popen failure later.
    """
    global CACHED_BASH_PATH
    if CACHED_BASH_PATH:
        return CACHED_BASH_PATH

    override = os.environ.get("BITRAB_BASH_PATH")
    if override:
        CACHED_BASH_PATH = override
        return override
    on_path = shutil.which("bash")
    if on_path and not is_wsl_bash(on_path):
        CACHED_BASH_PATH = on_path
        return on_path
    for candidate in windows_bash_candidates():
        if os.path.isfile(candidate):
            CACHED_BASH_PATH = candidate
            return candidate

    raise BitrabError(
        "Could not locate a usable bash.exe on Windows. Install Git for Windows (provides C:\\Program Files\\Git\\bin\\bash.exe) or set BITRAB_BASH_PATH to a Git Bash / MSYS bash executable. WSL's bash.exe is not supported because it cannot see Windows-style paths."
    )


def pick_bash(login_shell: bool) -> list[str]:
    if os.name == "nt":
        cmd = [find_bash_windows()]
    else:
        cmd = ["bash"]
    if login_shell:
        cmd.append("-l")
    return cmd


def run_bash(
    script: str,
    *,
    env: dict[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
    mode: str = "stream",
    check: bool = True,
    login_shell: bool = False,
    force_color: bool | None = None,
    stdout_target: TextWriter | None = None,
    stderr_target: TextWriter | None = None,
    timeout: float | None = None,
) -> RunResult:
    """Run a bash script via stdin."""
    env_merged = merge_env(env)

    if os.name == "nt":
        script = script.replace("\r\n", "\n")

    colors = colors_enabled(force_color)
    g, r, reset = (GREEN, RED, RESET) if colors else ("", "", "")

    bash = pick_bash(login_shell or bool(os.environ.get("BITRAB_RUN_LOAD_BASHRC")))
    robust_script_content = f"set -eo pipefail\n{script}"

    if mode not in {"stream", "capture"}:
        raise ValueError("mode must be 'stream' or 'capture'")

    if mode == "capture":
        with subprocess.Popen(  # nosec
            bash,
            env=env_merged,
            cwd=str(cwd) if cwd is not None else None,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as proc:
            try:
                out, err = proc.communicate(robust_script_content, timeout=timeout)
            except subprocess.TimeoutExpired as toe:
                proc.kill()
                proc.communicate()
                raise JobTimeoutError(f"Job timed out after {timeout}s") from toe
            rc = proc.returncode
        result = RunResult(rc, out, err)
        if check:
            result.check_returncode()
        return result

    out_buf = Buffer(stdout_target or sys.stdout)
    err_buf = Buffer(stderr_target or sys.stderr)

    def stream(pipe: IO[str], color: str, buf: Buffer) -> None:
        # Read in blocks for efficiency while still streaming lines.
        # Downstream consumers (e.g. QueueWriter → TUI) receive coherent lines.
        try:
            pending: list[str] = []
            while True:
                chunk = pipe.read(1024)
                if not chunk:
                    # EOF: flush any remaining partial line
                    if pending:
                        buf.write(f"{color}{''.join(pending)}{reset}")
                        buf.flush()
                    break

                # Split the chunk into lines, keeping track of any partial line at the end
                lines = chunk.splitlines(keepends=True)
                if not lines:
                    continue

                for i, line in enumerate(lines):
                    pending.append(line)
                    if line.endswith(("\n", "\r")):
                        buf.write(f"{color}{''.join(pending)}{reset}")
                        buf.flush()
                        pending = []
        finally:
            try:
                pipe.close()
            except Exception:  # nosec B110
                pass

    process_killed_by_timeout = False

    with subprocess.Popen(  # nosec
        bash,
        env=env_merged,
        cwd=str(cwd) if cwd is not None else None,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=0,
    ) as proc:
        if not (proc.stdout is not None and proc.stderr is not None and proc.stdin is not None):
            raise BitrabError("proc properties are None")

        t_out = threading.Thread(target=stream, args=(proc.stdout, g, out_buf), daemon=True)
        t_err = threading.Thread(target=stream, args=(proc.stderr, r, err_buf), daemon=True)
        t_out.start()
        t_err.start()

        proc.stdin.write(robust_script_content)
        proc.stdin.close()

        cancel_timer = threading.Event()
        t_kill: threading.Thread | None = None
        if timeout is not None:

            def kill_on_timeout() -> None:
                nonlocal process_killed_by_timeout
                if not cancel_timer.wait(timeout):
                    process_killed_by_timeout = True
                    try:
                        proc.kill()
                    except OSError:
                        pass

            t_kill = threading.Thread(target=kill_on_timeout, daemon=True)
            t_kill.start()

        t_out.join()
        t_err.join()
        rc = proc.wait()

        if timeout is not None:
            cancel_timer.set()
            if t_kill is not None:
                # Bound the join so a stuck killer thread can't hang the run;
                # daemon=True still lets the process exit if this overruns.
                t_kill.join(timeout=0.1)

    if process_killed_by_timeout:
        raise JobTimeoutError(f"Job timed out after {timeout}s")

    result = RunResult(rc, out_buf.getvalue(), err_buf.getvalue())
    if check:
        result.check_returncode()
    return result


ENV_MODE = "BITRAB_SUBPROC_MODE"


def auto_mode() -> str:
    mode = os.getenv(ENV_MODE)
    if mode in {"stream", "capture"}:
        return mode
    if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("CI"):
        return "capture"
    return "stream"


def run_colored(script: str, env=None, cwd=None, mode: str | None = None) -> RunResult:
    """
    Backward-compatible wrapper. Keeps streaming default in dev,
    but auto-switches to capture in pytest/CI, unless overridden via BITRAB_SUBPROC_MODE.
    """
    if mode is None:
        mode = auto_mode()
    return run_bash(
        script,
        env=env,
        cwd=cwd,
        mode=mode,
        check=True,
        force_color=None,
    )


@contextmanager
def force_subproc_mode(mode: str):
    """Temporarily force 'stream' or 'capture' without changing call sites."""
    if mode not in {"stream", "capture"}:
        raise ValueError("mode must be 'stream' or 'capture'")
    prev = os.getenv(ENV_MODE)
    os.environ[ENV_MODE] = mode
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop(ENV_MODE, None)
        else:
            os.environ[ENV_MODE] = prev
