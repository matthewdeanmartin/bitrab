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
        mode="capture",               # or "stream"
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
        res = run_bash("printf '%s' foo", mode='capture')
        assert res.stdout == "foo"

"""

from __future__ import annotations

import os
import subprocess  # nosec
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import IO

from bitrab.exceptions import BitrabError

# ---------- Color helpers ----------
GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"


def _colors_enabled(force: bool | None) -> bool:
    if force is True:
        return True
    if force is False:
        return False
    # default: respect NO_COLOR
    return not bool(os.getenv("NO_COLOR"))


# ---------- Result container ----------
@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str

    def check_returncode(self) -> RunResult:
        if self.returncode != 0:
            raise subprocess.CalledProcessError(self.returncode, "<bash stdin>", self.stdout, self.stderr)
        return self


# ---------- Env merge ----------
_BASE_ENV = os.environ.copy()


def merge_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Return a merged environment where `env` overrides current process env."""
    if env:
        merged = {**_BASE_ENV, **env}
    else:
        merged = _BASE_ENV.copy()
    return merged


# ---------- Core runner ----------
class _Buffer:
    """A very small helper to collect text while also acting like a file-like object."""

    def __init__(self, target: IO[str] | None = None) -> None:
        self._buf: list[str] = []
        self._target = target

    def write(self, s: str) -> None:  # type: ignore[override]
        self._buf.append(s)
        if self._target is not None:
            self._target.write(s)

    def flush(self) -> None:  # type: ignore[override]
        if self._target is not None:
            self._target.flush()

    def getvalue(self) -> str:
        return "".join(self._buf)


_DEF_BASH_WINDOWS = r"C:\\Program Files\\Git\\bin\\bash.exe"


def _pick_bash(login_shell: bool) -> list[str]:
    if os.name == "nt":
        cmd = [_DEF_BASH_WINDOWS]
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
    mode: str = "stream",  # "stream" | "capture"
    check: bool = True,
    login_shell: bool = False,
    force_color: bool | None = None,
    stdout_target: IO[str] | None = None,
    stderr_target: IO[str] | None = None,
) -> RunResult:
    """Run a bash *script string* via stdin.

    Parameters
    ----------
    script : str
        Bash script content to execute. `set -eo pipefail` will be prepended.
    env : dict[str, str] | None
        Environment vars to merge over process env.
    cwd : str | os.PathLike[str] | None
        Working directory for the subprocess.
    mode : {"stream", "capture"}
        - "stream": real-time tee to `stdout_target`/`stderr_target` (default sys.std*),
          while also capturing and returning the full text.
        - "capture": do not tee; only return captured text.
    check : bool
        If True, raise `CalledProcessError` on non-zero exit.
    login_shell : bool
        If True, add `-l` to bash (reads profile/bashrc). Off by default because it's slow.
    force_color : bool | None
        Force-enable/disable ANSI color regardless of NO_COLOR. If None, respect NO_COLOR.
    stdout_target / stderr_target : IO[str] | None
        Streams to receive live output in streaming mode. Defaults to sys.stdout/sys.stderr.

    Returns:
    -------
    RunResult
        Includes return code and full stdout/stderr text.
    """
    env_merged = merge_env(env)

    # Normalize line endings for Git-Bash on Windows when feeding via stdin
    if os.name == "nt":
        script = script.replace("\r\n", "\n")

    colors = _colors_enabled(force_color)
    g, r, reset = (GREEN, RED, RESET) if colors else ("", "", "")

    bash = _pick_bash(login_shell or bool(os.environ.get("bitrab_RUN_LOAD_BASHRC")))

    # Always prepend robust flags
    robust_script_content = f"set -eo pipefail\n{script}"

    if mode not in {"stream", "capture"}:
        raise ValueError("mode must be 'stream' or 'capture'")

    if mode == "capture":
        # No threads, simpler deterministic behavior for tests/CI
        with subprocess.Popen(  # nosec
            bash,
            env=env_merged,
            cwd=str(cwd) if cwd is not None else None,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as proc:
            out, err = proc.communicate(robust_script_content)
            rc = proc.returncode
        result = RunResult(rc, out, err)
        if check:
            result.check_returncode()
        return result

    # Streaming mode: threads + tee to targets while capturing
    out_buf = _Buffer(stdout_target or sys.stdout)
    err_buf = _Buffer(stderr_target or sys.stderr)

    def _stream(pipe: IO[str], color: str, buf: _Buffer) -> None:
        try:
            for line in iter(pipe.readline, ""):
                if not line:
                    break
                buf.write(f"{color}{line}{reset}")
                buf.flush()
        finally:
            try:
                pipe.close()
            except Exception:  # nosec: clean up
                pass

    with subprocess.Popen(  # nosec
        bash,
        env=env_merged,
        cwd=str(cwd) if cwd is not None else None,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,  # line buffered
    ) as proc:
        if not (proc.stdout is not None and proc.stderr is not None and proc.stdin is not None):
            raise BitrabError("proc properties are None")

        t_out = threading.Thread(target=_stream, args=(proc.stdout, g, out_buf), daemon=True)
        t_err = threading.Thread(target=_stream, args=(proc.stderr, r, err_buf), daemon=True)
        t_out.start()
        t_err.start()

        proc.stdin.write(robust_script_content)
        proc.stdin.close()

        t_out.join()
        t_err.join()
        rc = proc.wait()

    result = RunResult(rc, out_buf.getvalue(), err_buf.getvalue())
    if check:
        result.check_returncode()
    return result


# Optional: env var knob (document for CI/pytest usage)
_ENV_MODE = "BITRAB_SUBPROC_MODE"  # "stream" or "capture"


def _auto_mode() -> str:
    mode = os.getenv(_ENV_MODE)
    if mode in {"stream", "capture"}:
        return mode
    # Auto: be nice to tests/CI
    if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("CI"):
        return "capture"
    return "stream"


def run_colored(script: str, env=None, cwd=None, mode: str | None = None) -> RunResult:
    """
    Backward-compatible wrapper. Keeps streaming default in dev,
    but auto-switches to capture in pytest/CI, unless overridden via BITRAB_SUBPROC_MODE.
    Returns the int returncode and raises on non-zero (as before).
    """
    if mode is None:
        mode = _auto_mode()  # "stream" or "capture"
    # Respect NO_COLOR automatically; users can force with force_color if desired
    result = run_bash(
        script,
        env=env,
        cwd=cwd,
        mode=mode,
        check=True,  # old behavior: raise on non-zero
        force_color=None,  # respect NO_COLOR by default
        # You can pass stdout_target/stderr_target here if you want custom tee targets
    )
    return result


# Optional helpers (useful in tests or specific blocks)
@contextmanager
def force_subproc_mode(mode: str):
    """
    Temporarily force 'stream' or 'capture' without changing call sites.
    Example:
        with force_subproc_mode("capture"):
            run_colored("echo hi")
    """
    if mode not in {"stream", "capture"}:
        raise ValueError("mode must be 'stream' or 'capture'")
    prev = os.getenv(_ENV_MODE)
    os.environ[_ENV_MODE] = mode
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop(_ENV_MODE, None)
        else:
            os.environ[_ENV_MODE] = prev
