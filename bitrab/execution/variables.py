from __future__ import annotations

import os
import re
from pathlib import Path

from bitrab.models.pipeline import JobConfig


class VariableManager:
    """
    Manages variable substitution and environment preparation.

    Attributes:
        base_variables: Base environment variables.
        gitlab_ci_vars: Simulated GitLab CI built-in variables.
    """

    def __init__(self, base_variables: dict[str, str] | None = None):
        self.base_variables = base_variables or {}
        self.gitlab_ci_vars = self._get_gitlab_ci_variables()

    def _get_gitlab_ci_variables(self) -> dict[str, str]:
        """
        Get GitLab CI built-in variables that we can simulate.

        Returns:
            A dictionary of simulated GitLab CI variables.
        """
        return {
            "CI": "true",
            "CI_PROJECT_DIR": str(Path.cwd()),
            "CI_PROJECT_NAME": Path.cwd().name,
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
        env = os.environ.copy()

        # Apply variables in order: built-in -> base -> job
        env.update(self.gitlab_ci_vars)
        env.update(self.base_variables)
        env.update(job.variables)

        # Set job-specific variables
        env["CI_JOB_STAGE"] = job.stage
        env["CI_JOB_NAME"] = job.name

        return env

    def substitute_variables(self, text: str, variables: dict[str, str]) -> str:
        """
        Perform basic variable substitution in text.

        Args:
            text: The text to substitute variables in.
            variables: The variables to use for substitution.

        Returns:
            The text with variables substituted.
        """
        # Simple substitution - replace $VAR and ${VAR} patterns

        def replace_var(match):
            var_name = match.group(1) or match.group(2)
            return variables.get(var_name, match.group(0))

        # Match $VAR or ${VAR}
        pattern = r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)"
        # Fails on echo "FOO"BAR"
        return re.sub(pattern, replace_var, text)
