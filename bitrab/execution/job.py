from __future__ import annotations

import os
import subprocess  # nosec
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bitrab.console import safe_print
from bitrab.exceptions import BitrabError, JobExecutionError, JobTimeoutError
from bitrab.execution.shell import RunResult, TextWriter, run_bash
from bitrab.execution.variables import VariableManager
from bitrab.models.pipeline import JobConfig

FAIL_FAST = False


@dataclass(frozen=True)
class JobRuntimeContext:
    """Frozen per-job context built once before execution begins.

    Replaces the pattern where :meth:`JobExecutor.execute_job` receives
    scattered parameters and rebuilds the environment on every call.

    Attributes:
        job: The job configuration.
        env: Fully resolved environment dict (os.environ + CI vars + job vars).
        job_dir: Per-job workspace directory (e.g. ``.bitrab/<job>/``).
        project_dir: The project root directory (used as cwd for scripts).
        output_writer: File-like sink for job output; ``None`` → sys.stdout.
        timeout: Maximum seconds the job may run; ``None`` → no limit.
    """

    job: JobConfig
    env: dict[str, str] = field(default_factory=dict)
    job_dir: Path = field(default_factory=Path)
    project_dir: Path = field(default_factory=Path)
    output_writer: Any | None = None
    timeout: float | None = None


class JobExecutor:
    """
    Executes individual jobs.

    Attributes:
        variable_manager: The VariableManager instance for managing variables.
    """

    def __init__(self, variable_manager: VariableManager, dry_run: bool = False, project_dir: Path | None = None):
        self.variable_manager = variable_manager
        self.job_history: list[RunResult] = []
        self.dry_run = dry_run
        self.project_dir = project_dir or Path.cwd()

    # ---- retry helpers ----

    @staticmethod
    def _env_delay_seconds() -> int:
        try:
            return max(0, int(os.getenv("BITRAB_RETRY_DELAY_SECONDS", "0")))
        except Exception:
            return 0

    @staticmethod
    def _env_strategy() -> str:
        val = os.getenv("BITRAB_RETRY_STRATEGY", "exponential").lower().strip()
        return val if val in {"exponential", "constant"} else "exponential"

    @staticmethod
    def _should_retry_when(when: list[str] | None, exc: BaseException) -> bool:
        normalized = [str(w).strip().lower() for w in (when or []) if isinstance(w, (str, int))]
        if not normalized:
            return True  # default to retry on any failure if max>0 was requested
        if "always" in normalized:
            return True
        if "script_failure" in normalized and isinstance(exc, subprocess.CalledProcessError):
            return True
        return False

    @staticmethod
    def _should_retry_exit_codes(exit_codes: list[int], exc: BaseException) -> bool:
        if not exit_codes:
            return True  # no restriction by codes
        return isinstance(exc, subprocess.CalledProcessError) and exc.returncode in exit_codes

    @staticmethod
    def _compute_delay_seconds(strategy: str, base: int, attempt_index: int) -> float:
        if base <= 0:
            return 0.0
        if strategy == "constant":
            return float(base)
        # exponential (default)
        return float(base) * (2 ** (attempt_index - 1))

    def build_context(
        self,
        job: JobConfig,
        job_dir: Path | None = None,
        output_writer: Any | None = None,
        timeout: float | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> JobRuntimeContext:
        """Build a :class:`JobRuntimeContext` for *job*.

        This resolves the environment once via :pyattr:`variable_manager` and
        wires in ``CI_JOB_DIR``.  The returned context is frozen and can be
        passed to :meth:`execute_job`.

        Args:
            job: The job configuration.
            job_dir: Optional custom directory for job execution.
            output_writer: Optional writer for job output.
            timeout: Optional maximum duration for the job.
            extra_env: Additional variables injected *before* job-level
                ``variables:`` (so job variables still win).  Used by the
                stage runner to pass dotenv-report variables from upstream
                jobs — simulating GitLab's pipeline variable passing via
                ``artifacts: reports: dotenv:``.
        """
        env = self.variable_manager.prepare_environment(job)
        if extra_env:
            # extra_env sits between CI built-ins and job-level variables.
            # We merge it after prepare_environment so that job.variables
            # (already applied inside prepare_environment) take precedence.
            # Re-apply job.variables on top to ensure correct priority.
            env.update(extra_env)
            env.update(job.variables)
            env["CI_JOB_STAGE"] = job.stage
            env["CI_JOB_NAME"] = job.name
        if job_dir is not None:
            env["CI_JOB_DIR"] = str(job_dir)

        job_timeout = job.timeout if job.timeout is not None else timeout
        return JobRuntimeContext(
            job=job,
            env=env,
            job_dir=job_dir or self.project_dir,
            project_dir=self.project_dir,
            output_writer=output_writer,
            timeout=job_timeout,
        )

    def execute_job(
        self,
        job: JobConfig | None = None,
        job_dir: Path | None = None,
        output_writer: TextWriter | None = None,
        timeout: float | None = None,
        *,
        ctx: JobRuntimeContext | None = None,
    ) -> None:
        """Execute a single job.

        May be called in two ways:

        1. **Legacy (scattered params)**::

               executor.execute_job(job, job_dir=..., output_writer=...)

        2. **New (context)**::

               ctx = executor.build_context(job, ...)
               executor.execute_job(ctx=ctx)

        When *ctx* is provided, *job*, *job_dir*, *output_writer*, and
        *timeout* are ignored.

        Raises:
            JobExecutionError: If the job fails to execute successfully.
        """
        if ctx is None:
            if job is None:
                raise ValueError("Either 'job' or 'ctx' must be provided")
            ctx = self.build_context(job, job_dir=job_dir, output_writer=output_writer, timeout=timeout)

        self._execute_with_context(ctx)

    def _execute_with_context(self, ctx: JobRuntimeContext) -> None:
        """Core execution loop driven by a :class:`JobRuntimeContext`."""
        job = ctx.job
        output_writer = ctx.output_writer
        # Always run scripts from project_dir so relative paths (e.g. ./scripts/foo.sh)
        # resolve correctly. job_dir is exposed as CI_JOB_DIR for scripts that need
        # an isolated workspace, but it is NOT used as cwd.
        execution_dir = ctx.project_dir
        env = dict(ctx.env)  # mutable copy (frozen dataclass stores the original)

        _print = (lambda msg: safe_print(msg, file=output_writer)) if output_writer else safe_print
        _print(f"🔧 Running job: {job.name} (stage: {job.stage})")

        job_timeout = ctx.timeout
        deadline: float | None = (time.monotonic() + job_timeout) if job_timeout is not None else None

        max_attempts = 1 + max(0, int(job.retry_max))
        attempt = 0
        last_exc: BaseException | None = None

        # env-configured timing controls
        base_delay = self._env_delay_seconds()
        strategy = self._env_strategy()
        skip_sleep = os.getenv("BITRAB_RETRY_NO_SLEEP") == "1"

        while attempt < max_attempts:
            attempt += 1
            if max_attempts > 1:
                _print(f"  🔁 Attempt {attempt}/{max_attempts}")

            try:
                if job.before_script:
                    _print("  📋 Running before_script...")
                    self._execute_scripts(
                        job.before_script, env, execution_dir, output_writer=output_writer, deadline=deadline
                    )

                if job.script:
                    _print("  🚀 Running script...")
                    self._execute_scripts(
                        job.script, env, execution_dir, output_writer=output_writer, deadline=deadline
                    )

                _print(f"✅ Job {job.name} completed successfully")
                return

            except JobTimeoutError:
                _print(f"  ⏱️ Job {job.name} timed out after {job_timeout}s")
                raise
            except subprocess.CalledProcessError as e:
                last_exc = e
                _print(f"  ❗ Job step failed with exit code {e.returncode}")
                if FAIL_FAST:
                    raise
            except BaseException as e:
                last_exc = e
                _print(f"  ❗ Job step raised an exception: {e!r}")
            finally:
                if job.after_script:
                    _print("  📋 Running after_script...")
                    try:
                        self._execute_scripts(
                            job.after_script, env, execution_dir, output_writer=output_writer, deadline=deadline
                        )
                    except subprocess.CalledProcessError as e2:
                        last_exc = last_exc or e2
                        _print(f"  ❗ after_script failed with exit code {e2.returncode}")

            # failed attempt
            if attempt >= max_attempts:
                break

            # honor exit_codes restriction first; then when
            if not self._should_retry_exit_codes(job.retry_exit_codes, last_exc or Exception("unknown failure")):
                _print("  ↩️  Retry blocked by exit_codes; will not retry.")
                break
            if not self._should_retry_when(job.retry_when, last_exc or Exception("unknown failure")):
                _print("  ↩️  Retry conditions not met (when); will not retry.")
                break

            delay = self._compute_delay_seconds(strategy, base_delay, attempt)
            if delay > 0 and not skip_sleep:
                _print(f"  ⏳ Waiting {delay:.2f}s before retry...")
                time.sleep(delay)

            _print("  🔄 Retrying job...")

        # out of attempts
        if isinstance(last_exc, subprocess.CalledProcessError):
            raise JobExecutionError(
                f"Job {job.name} failed after {attempt} attempt(s) with exit code {last_exc.returncode}"
            ) from last_exc
        raise JobExecutionError(f"Job {job.name} failed after {attempt} attempt(s).") from last_exc

    def _execute_scripts(
        self,
        scripts: list[str],
        env: dict[str, str],
        cwd: Path | None = None,
        output_writer: TextWriter | None = None,
        deadline: float | None = None,
    ) -> None:
        """
        Execute a list of script commands.

        Args:
            scripts: The list of scripts to execute.
            env: The environment variables for the scripts.
            cwd: Optional working directory.
            output_writer: Optional file-like object to direct all output into.
            deadline: monotonic clock deadline; if set, remaining time is passed
                      as the timeout to run_bash.

        Raises:
            subprocess.CalledProcessError: If a script exits with a non-zero code.
            JobTimeoutError: If the deadline is reached before the script finishes.
        """
        _print = (lambda msg: safe_print(msg, file=output_writer)) if output_writer else safe_print

        lines = []
        for script in scripts:
            if not isinstance(script, str):
                raise BitrabError(f"{script} is not a string")
            if not script.strip():
                continue

            lines.append(script)

        full_script = "\n".join(lines)
        for line in lines:
            _print(f"    $ {line}")

        target_cwd = cwd or self.project_dir

        if self.dry_run:
            _print("    ↪ dry-run preview only")
            result = RunResult(0, "", "")
        else:
            remaining: float | None = None
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise JobTimeoutError("Job timed out before script could start")

            result = run_bash(
                full_script,
                env=env,
                cwd=target_cwd,
                check=False,
                stdout_target=output_writer,
                stderr_target=output_writer,
                timeout=remaining,
            )
        self.job_history.append(result)

        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, full_script)
