from __future__ import annotations

import subprocess  # nosec
from pathlib import Path

from bitrab.exceptions import BitrabError, JobExecutionError
from bitrab.execution.shell import run_colored
from bitrab.execution.variables import VariableManager
from bitrab.models.pipeline import JobConfig


class JobExecutor:
    """
    Executes individual jobs.

    Attributes:
        variable_manager: The VariableManager instance for managing variables.
    """

    def __init__(self, variable_manager: VariableManager):
        self.variable_manager = variable_manager

    def execute_job(self, job: JobConfig) -> None:
        """
        Execute a single job.

        Args:
            job: The job configuration.

        Raises:
            JobExecutionError: If the job fails to execute successfully.
        """
        print(f"🔧 Running job: {job.name} (stage: {job.stage})")

        env = self.variable_manager.prepare_environment(job)

        try:
            # Don't have a way for variable declared in before to exist in middle or after.
            # Execute before_script
            if job.before_script:
                print("  📋 Running before_script...")
                self._execute_scripts(job.before_script, env)

            # Execute main script
            if job.script:
                print("  🚀 Running script...")
                self._execute_scripts(job.script, env)

        except subprocess.CalledProcessError as e:
            raise JobExecutionError(f"Job {job.name} failed with exit code {e.returncode}") from e
        finally:
            # Execute after_script
            if job.after_script:
                print("  📋 Running after_script...")
                self._execute_scripts(job.after_script, env)

        print(f"✅ Job {job.name} completed successfully")

    def _execute_scripts(self, scripts: list[str], env: dict[str, str]) -> None:
        """
        Execute a list of script commands.

        Args:
            scripts: The list of scripts to execute.
            env: The environment variables for the scripts.

        Raises:
            subprocess.CalledProcessError: If a script exits with a non-zero code.
        """
        lines = []
        for script in scripts:
            if not isinstance(script, str):
                raise BitrabError(f"{script} is not a string")
            if not script.strip():
                continue

            # Substitute variables in the script
            script = self.variable_manager.substitute_variables(script, env)
            lines.append(script)

        full_script = "\n".join(lines)
        print(f"    $ {full_script}")

        returncode = run_colored(
            full_script,
            env=env,
            cwd=Path.cwd(),
        )

        if returncode != 0:
            raise subprocess.CalledProcessError(returncode, full_script)
