from __future__ import annotations

from pathlib import Path
from typing import Any, Union

from bitrab.config.loader import ConfigurationLoader
from bitrab.execution.job import JobExecutor
from bitrab.execution.scheduler import StageOrchestrator
from bitrab.execution.variables import VariableManager
from bitrab.models.pipeline import DefaultConfig, JobConfig, PipelineConfig


class PipelineProcessor:
    """
    Processes raw configuration into structured pipeline configuration.

    Attributes:
        RESERVED_KEYWORDS: Reserved keywords in GitLab CI configuration.
    """

    RESERVED_KEYWORDS = {
        "stages",
        "variables",
        "default",
        "include",
        "image",
        "services",
        "before_script",
        "after_script",
        "cache",
        "artifacts",
    }

    def process_config(self, raw_config: dict[str, Any]) -> PipelineConfig:
        """
        Process raw configuration into structured pipeline config.

        Args:
            raw_config: The raw configuration dictionary.

        Returns:
            A structured PipelineConfig object.
        """
        # Extract global configuration
        stages = raw_config.get("stages", ["test"])
        global_variables = raw_config.get("variables", {})
        default_config = self._process_default_config(raw_config.get("default", {}))

        # Process jobs
        jobs = []
        for name, job_data in raw_config.items():
            if name not in self.RESERVED_KEYWORDS and isinstance(job_data, dict):
                job = self._process_job(name, job_data, default_config, global_variables)
                jobs.append(job)

        return PipelineConfig(stages=stages, variables=global_variables, default=default_config, jobs=jobs)

    def _process_default_config(self, default_data: dict[str, Any]) -> DefaultConfig:
        """
        Process default configuration block.

        Args:
            default_data: The default configuration dictionary.

        Returns:
            A DefaultConfig object.
        """
        return DefaultConfig(
            before_script=self._ensure_list(default_data.get("before_script", [])),
            after_script=self._ensure_list(default_data.get("after_script", [])),
            variables=default_data.get("variables", {}),
        )

    def _process_job(self, name: str, job_data: dict[str, Any], default: DefaultConfig, global_vars: dict[str, str]) -> JobConfig:
        """
        Process a single job configuration.

        Args:
            name: The name of the job.
            job_data: The job configuration dictionary.
            default: The default configuration.
            global_vars: Global environment variables.

        Returns:
            A JobConfig object.
        """
        # Merge variables with precedence: job > global > default
        variables = {}
        variables.update(default.variables)
        variables.update(global_vars)
        variables.update(job_data.get("variables", {}))

        # Merge scripts with default
        before_script = default.before_script + self._ensure_list(job_data.get("before_script", []))
        after_script = self._ensure_list(job_data.get("after_script", [])) + default.after_script

        return JobConfig(
            name=name,
            stage=job_data.get("stage", "test"),
            script=self._ensure_list(job_data.get("script", [])),
            variables=variables,
            before_script=before_script,
            after_script=after_script,
        )

    def _ensure_list(self, value: Union[str, list[str]]) -> list[str]:
        """
        Ensure a value is a list of strings.

        Args:
            value: The value to ensure.

        Returns:
            A list of strings.
        """
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return value
        return []


class LocalGitLabRunner:
    """
    Main runner class that orchestrates the entire pipeline execution.

    Attributes:
        base_path: The base path for resolving configuration files.
        loader: The ConfigurationLoader instance for loading configurations.
        processor: The PipelineProcessor instance for processing configurations.
    """

    def __init__(self, base_path: Path | None = None):
        if not base_path:
            self.base_path = Path.cwd()
        else:
            self.base_path = base_path
        self.loader = ConfigurationLoader(base_path)
        self.processor = PipelineProcessor()

    def run_pipeline(self, config_path: Path | None = None) -> None:
        """
        Run the complete pipeline.

        Args:
            config_path: Path to the pipeline configuration file.

        Returns:
            The exit code of the pipeline execution.

        Raises:
            GitLabCIError: If there is an error in the pipeline configuration.
            Exception: For unexpected errors.
        """
        # Load and process configuration
        raw_config = self.loader.load_config(config_path)
        pipeline = self.processor.process_config(raw_config)

        # Set up execution components
        variable_manager = VariableManager(pipeline.variables)
        job_executor = JobExecutor(variable_manager)
        orchestrator = StageOrchestrator(job_executor)

        # Execute pipeline
        orchestrator.execute_pipeline(pipeline)


def best_efforts_run(config_path: Path) -> None:
    """Main entry point for the best-efforts-run command."""
    runner = LocalGitLabRunner()
    runner.run_pipeline(config_path)
