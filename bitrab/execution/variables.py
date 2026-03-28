from __future__ import annotations

import os
from pathlib import Path

from bitrab.models.pipeline import JobConfig


class VariableManager:
    """
    Manages environment preparation for job execution.

    Attributes:
        base_variables: Base environment variables.
        gitlab_ci_vars: Simulated GitLab CI built-in variables.
        project_dir: The project root directory.
    """

    def __init__(self, base_variables: dict[str, str] | None = None, project_dir: Path | None = None):
        self.base_variables = base_variables or {}
        self.project_dir = project_dir or Path.cwd()
        self.gitlab_ci_vars = self._get_gitlab_ci_variables()

        # Pre-compute the shared base environment (os.environ + built-ins + base vars)
        self._shared_base_env = os.environ.copy()
        self._shared_base_env.update(self.gitlab_ci_vars)
        self._shared_base_env.update(self.base_variables)

    def _get_gitlab_ci_variables(self) -> dict[str, str]:
        """
        Get GitLab CI built-in variables that we can simulate.

        Returns:
            A dictionary of simulated GitLab CI variables.
        """
        return {
            "CI": "true",
            "CI_PROJECT_DIR": str(self.project_dir),
            "CI_PROJECT_NAME": self.project_dir.name,
            "CI_JOB_STAGE": "",  # Will be set per job
        }

    def prepare_environment(self, job: JobConfig) -> dict[str, str]:
        """
        Prepare environment variables for job execution.

        Args:
            job: The job configuration.

        Returns:
            A dictionary of prepared environment variables.
        """
        # Start from the pre-computed base instead of os.environ.copy()
        env = self._shared_base_env.copy()

        # Apply job-specific variables
        env.update(job.variables)
        env["CI_JOB_STAGE"] = job.stage
        env["CI_JOB_NAME"] = job.name

        return env
