"""
Super limited local pipeline runner.
"""

from __future__ import annotations

import os
import re
import subprocess  # nosec
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union

from ruamel.yaml import YAML

from bitrab.exceptions import BitrabError

# Copy of the base environment variables
BASE_ENV = os.environ.copy()


def merge_env(env=None):
    """
    Merge os.environ and an env dict into a new dict.
    Values from env override os.environ on conflict.

    Args:
        env: Optional dict of environment variables.

    Returns:
        A merged dict suitable for subprocess calls.
    """
    if env:
        return {**BASE_ENV, **env}
    return BASE_ENV


# ANSI color codes for terminal output
GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

# Disable colors if NO_COLOR is set in the environment
if os.getenv("NO_COLOR"):
    GREEN = RED = RESET = ""


def run_colored(script: str, env=None, cwd=None) -> int:
    """
    Run a script in a subprocess with colored output for stdout and stderr.

    Args:
        script: The script to execute.
        env: Optional environment variables for the subprocess.
        cwd: Optional working directory for the subprocess.

    Returns:
        The return code of the subprocess.

    Raises:
        subprocess.CalledProcessError: If the subprocess exits with a non-zero code.
    """
    env = merge_env(env)

    # Disable colors if NO_COLOR is set
    if os.getenv("NO_COLOR"):
        g, r, reset = "", "", ""
    else:
        g, r, reset = GREEN, RED, RESET

    # Determine the bash executable based on the operating system
    if os.name == "nt":
        bash = [r"C:\Program Files\Git\bin\bash.exe"]
    else:
        bash = ["bash"]

    if os.environ.get("bitrab_RUN_LOAD_BASHRC"):
        bash.append("-l")
    # Start the subprocess
    with subprocess.Popen(  # nosec
        # , "-l"  # -l loads .bashrc and make it really, really slow.
        bash,  # bash reads script from stdin
        env=env,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,  # to prevent \r
        bufsize=1,  # line-buffered
    ) as process:

        def stream(pipe, color, target):
            """
            Stream output from a pipe to a target with optional color.

            Args:
                pipe: The pipe to read from.
                color: The color to apply to the output.
                target: The target to write the output to.
            """
            for line in iter(pipe.readline, ""):  # text mode here, so sentinel is ""
                if not line:
                    break
                target.write(f"{color}{line}{reset}")
                target.flush()
            pipe.close()

        # Start threads to stream stdout and stderr in parallel
        threads = [
            threading.Thread(target=stream, args=(process.stdout, g, sys.stdout)),
            threading.Thread(target=stream, args=(process.stderr, r, sys.stderr)),
        ]
        for t in threads:
            t.start()

        # Feed the script and close stdin

        if os.name == "nt":
            script = script.replace("\r\n", "\n")

        if process.stdin:
            # without this it will keep going on errors
            robust_script_content = f"set -eo pipefail\n{script}"
            process.stdin.write(robust_script_content)
            process.stdin.close()

        # Wait for process to finish
        for t in threads:
            t.join()

        process.wait()

        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, script)

        return process.returncode


@dataclass
class JobConfig:
    """
    Configuration for a single job.

    Attributes:
        name: The name of the job.
        stage: The stage the job belongs to.
        script: The main script to execute for the job.
        variables: Environment variables specific to the job.
        before_script: Scripts to run before the main script.
        after_script: Scripts to run after the main script.
    """

    name: str
    stage: str = "test"
    script: list[str] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)
    before_script: list[str] = field(default_factory=list)
    after_script: list[str] = field(default_factory=list)


@dataclass
class DefaultConfig:
    """
    Default configuration that can be inherited by jobs.

    Attributes:
        before_script: Default scripts to run before job scripts.
        after_script: Default scripts to run after job scripts.
        variables: Default environment variables for jobs.
    """

    before_script: list[str] = field(default_factory=list)
    after_script: list[str] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)


@dataclass
class PipelineConfig:
    """
    Complete pipeline configuration.

    Attributes:
        stages: List of pipeline stages.
        variables: Global environment variables for the pipeline.
        default: Default configuration for jobs.
        jobs: List of job configurations.
    """

    stages: list[str] = field(default_factory=lambda: ["test"])
    variables: dict[str, str] = field(default_factory=dict)
    default: DefaultConfig = field(default_factory=DefaultConfig)
    jobs: list[JobConfig] = field(default_factory=list)


class GitlabRunnerError(BitrabError):
    """Base exception for GitLab CI runner errors."""


class JobExecutionError(GitlabRunnerError):
    """Raised when a job fails to execute successfully."""


class ConfigurationLoader:
    """
    Loads and processes GitLab CI configuration files.

    Attributes:
        base_path: The base path for resolving configuration files.
        yaml: YAML parser instance.
    """

    def __init__(self, base_path: Path | None = None):
        if not base_path:
            self.base_path = Path.cwd()
        else:
            self.base_path = base_path
        self.yaml = YAML(typ="safe")

    def load_config(self, config_path: Path | None = None) -> dict[str, Any]:
        """
        Load the main configuration file and process includes.

        Args:
            config_path: Path to the configuration file.

        Returns:
            The loaded and processed configuration.

        Raises:
            GitLabCIError: If the configuration file is not found or fails to load.
        """
        if config_path is None:
            config_path = self.base_path / ".gitlab-ci.yml"

        if not config_path.exists():
            raise GitlabRunnerError(f"Configuration file not found: {config_path}")

        config = self._load_yaml_file(config_path)
        config = self._process_includes(config, config_path.parent)

        return config

    def _load_yaml_file(self, file_path: Path) -> dict[str, Any]:
        """
        Load a single YAML file.

        Args:
            file_path: Path to the YAML file.

        Returns:
            The loaded YAML content.

        Raises:
            GitLabCIError: If the file fails to load.
        """
        try:
            with open(file_path, encoding="utf-8") as f:
                return self.yaml.load(f) or {}
        except Exception as e:
            raise GitlabRunnerError(f"Failed to load YAML file {file_path}: {e}") from e

    def _process_includes(self, config: dict[str, Any], base_dir: Path, seen_files: set[Path] | None = None) -> dict[str, Any]:
        """
        Recursively process 'include' directives from a GitLab-style YAML config.

        Args:
            config: The configuration dictionary to process.
            base_dir: The base path to resolve relative includes.
            seen_files: Tracks already-included files to avoid infinite recursion.

        Returns:
            The merged configuration.
        """
        seen_files = seen_files or set()

        includes = config.pop("include", [])
        if isinstance(includes, (str, dict)):
            includes = [includes]

        merged_config: dict[str, Any] = {}

        for include in includes:
            if isinstance(include, str):
                include_path = (base_dir / include).resolve()
            elif isinstance(include, dict) and "local" in include:
                include_path = (base_dir / include["local"]).resolve()
            else:
                continue  # Unsupported include type

            if include_path in seen_files:
                continue  # Skip already processed files to prevent recursion

            seen_files.add(include_path)
            included_config = self._load_yaml_file(include_path)
            included_config = self._process_includes(included_config, include_path.parent, seen_files)
            merged_config = self._merge_configs(merged_config, included_config)

        # The current config overrides any previously merged includes
        merged_config = self._merge_configs(merged_config, config)
        return merged_config

    def _merge_configs(self, base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
        """
        Merge two configuration dictionaries.

        Args:
            base: The base configuration.
            overlay: The overlay configuration.

        Returns:
            The merged configuration.
        """
        result = base.copy()
        for key, value in overlay.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_configs(result[key], value)
            else:
                result[key] = value
        return result


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


class StageOrchestrator:
    """
    Orchestrates job execution by stages.

    Attributes:
        job_executor: The JobExecutor instance for executing jobs.
    """

    def __init__(self, job_executor: JobExecutor):
        self.job_executor = job_executor

    def execute_pipeline(self, pipeline: PipelineConfig) -> None:
        """
        Execute all jobs in the pipeline, organized by stages.

        Args:
            pipeline: The pipeline configuration.
        """
        print("🚀 Starting GitLab CI pipeline execution")
        print(f"📋 Stages: {', '.join(pipeline.stages)}")

        jobs_by_stage = self._organize_jobs_by_stage(pipeline)

        for stage in pipeline.stages:
            stage_jobs = jobs_by_stage.get(stage, [])
            if not stage_jobs:
                print(f"⏭️  Skipping empty stage: {stage}")
                continue

            print(f"\n🎯 Executing stage: {stage}")

            for job in stage_jobs:
                self.job_executor.execute_job(job)

        print("\n🎉 Pipeline completed successfully!")

    def _organize_jobs_by_stage(self, pipeline: PipelineConfig) -> dict[str, list[JobConfig]]:
        """
        Organize jobs by their stages.

        Args:
            pipeline: The pipeline configuration.

        Returns:
            A dictionary mapping stages to lists of jobs.
        """
        jobs_by_stage: dict[str, Any] = {}

        for job in pipeline.jobs:
            stage = job.stage
            if stage not in jobs_by_stage:
                jobs_by_stage[stage] = []
            jobs_by_stage[stage].append(job)

        return jobs_by_stage


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


if __name__ == "__main__":

    def run() -> None:
        print(sys.argv)
        config = str(sys.argv[-1:][0])
        print(f"Running {config} ...")
        best_efforts_run(Path(config))

    run()
