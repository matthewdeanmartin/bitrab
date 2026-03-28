from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Union

from bitrab.config.loader import ConfigurationLoader
from bitrab.execution.job import JobExecutor
from bitrab.execution.scheduler import StageOrchestrator
from bitrab.execution.variables import VariableManager
from bitrab.models.pipeline import DefaultConfig, JobConfig, PipelineConfig

_DURATION_RE = re.compile(
    r"""
    (?:(\d+)\s*w(?:eeks?)?)?\s*
    (?:(\d+)\s*d(?:ays?)?)?\s*
    (?:(\d+)\s*h(?:ours?)?)?\s*
    (?:(\d+)\s*m(?:in(?:utes?)?)?)?\s*
    (?:(\d+)\s*s(?:ec(?:onds?)?)?)?
    """,
    re.IGNORECASE | re.VERBOSE,
)


def parse_duration(value: Any) -> float | None:
    """Parse a GitLab CI timeout string or numeric seconds to float seconds.

    Supports formats like "30m", "1h 30m", "2h", "3600", 3600.
    Returns None if value is None or empty.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    m = _DURATION_RE.fullmatch(s)
    if m:
        weeks, days, hours, minutes, seconds = (int(g or 0) for g in m.groups())
        total = weeks * 7 * 86400 + days * 86400 + hours * 3600 + minutes * 60 + seconds
        if total > 0:
            return float(total)
    # fall back: try plain number string
    try:
        return float(s)
    except ValueError:
        return None


def filter_pipeline(
    pipeline: PipelineConfig,
    jobs: list[str] | None = None,
    stages: list[str] | None = None,
) -> PipelineConfig:
    """Return a copy of *pipeline* with jobs and stages filtered.

    Args:
        pipeline: The original pipeline configuration.
        jobs: If given, only keep jobs whose name is in this list.
              Unknown names are silently ignored (callers should warn first).
        stages: If given, only keep jobs whose stage is in this list.
                Both filters are applied when both are provided.

    Returns:
        A new PipelineConfig with filtered jobs and trimmed stage list.
    """
    filtered = list(pipeline.jobs)

    if jobs is not None:
        job_set = set(jobs)
        filtered = [j for j in filtered if j.name in job_set]

    if stages is not None:
        stage_set = set(stages)
        filtered = [j for j in filtered if j.stage in stage_set]

    # Keep only stages that still have jobs, preserving original order
    active_stages = {j.stage for j in filtered}
    trimmed_stages = [s for s in pipeline.stages if s in active_stages]

    return PipelineConfig(
        stages=trimmed_stages,
        variables=pipeline.variables,
        default=pipeline.default,
        jobs=filtered,
    )


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

    def _process_job(
        self,
        name: str,
        job_data: dict[str, Any],
        default: DefaultConfig,
        global_vars: dict[str, str],
    ) -> JobConfig:
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
        # Merge variables with precedence: job > default > global
        variables: dict[str, str] = {}
        variables.update(global_vars)
        variables.update(default.variables)
        variables.update(job_data.get("variables", {}))

        # Scripts: job overrides default
        before_script = self._ensure_list(job_data.get("before_script", []))
        if not before_script:
            before_script = default.before_script

        after_script = self._ensure_list(job_data.get("after_script", []))
        if not after_script:
            after_script = default.after_script

        # GitLab-aligned retry parsing
        retry_cfg = job_data.get("retry", 0)
        retry_max = 0
        retry_when: list[str] = []
        retry_exit_codes: list[int] = []

        if isinstance(retry_cfg, int):
            retry_max = max(0, int(retry_cfg))
        elif isinstance(retry_cfg, dict):
            # GitLab uses "max"
            retry_max = int(retry_cfg.get("max", 0) or 0)
            _when = retry_cfg.get("when", [])
            if isinstance(_when, str):
                retry_when = [_when]
            elif isinstance(_when, list):
                retry_when = [str(x) for x in _when if isinstance(x, (str, int))]

            _codes = retry_cfg.get("exit_codes", [])
            if isinstance(_codes, int):
                retry_exit_codes = [int(_codes)]
            elif isinstance(_codes, list):
                retry_exit_codes = [int(c) for c in _codes if isinstance(c, (int, str)) and str(c).isdigit()]

        # GitLab-aligned allow_failure parsing
        af_cfg = job_data.get("allow_failure", False)
        allow_failure = False
        allow_failure_exit_codes: list[int] = []

        if isinstance(af_cfg, bool):
            allow_failure = af_cfg
        elif isinstance(af_cfg, dict):
            allow_failure = True
            _codes = af_cfg.get("exit_codes", [])
            if isinstance(_codes, int):
                allow_failure_exit_codes = [int(_codes)]
            elif isinstance(_codes, list):
                allow_failure_exit_codes = [int(c) for c in _codes if isinstance(c, (int, str)) and str(c).isdigit()]

        # needs: DAG dependencies
        needs_raw = job_data.get("needs", [])
        needs: list[str] = []
        if isinstance(needs_raw, list):
            for item in needs_raw:
                if isinstance(item, str):
                    needs.append(item)
                elif isinstance(item, dict) and "job" in item:
                    needs.append(str(item["job"]))

        # timeout: maximum seconds the job may run
        timeout = parse_duration(job_data.get("timeout"))

        # artifacts
        artifacts_paths: list[str] = []
        artifacts_when = "on_success"
        artifacts_raw = job_data.get("artifacts", {})
        if isinstance(artifacts_raw, dict):
            _paths = artifacts_raw.get("paths", [])
            if isinstance(_paths, list):
                artifacts_paths = [str(p) for p in _paths if isinstance(p, str)]
            _when = artifacts_raw.get("when", "on_success")
            if _when in {"on_success", "on_failure", "always"}:
                artifacts_when = _when

        # dependencies: None means "inherit all" (GitLab default)
        dependencies: list[str] | None = None
        deps_raw = job_data.get("dependencies")
        if deps_raw is not None:
            if isinstance(deps_raw, list):
                dependencies = [str(d) for d in deps_raw if isinstance(d, (str, int))]
            else:
                dependencies = []

        # when keyword
        when = job_data.get("when", "on_success")
        if when not in {"on_success", "on_failure", "always", "manual", "never", "delayed"}:
            when = "on_success"

        return JobConfig(
            name=name,
            stage=job_data.get("stage", "test"),
            script=self._ensure_list(job_data.get("script", [])),
            variables=variables,
            before_script=before_script,
            after_script=after_script,
            retry_max=retry_max,
            retry_when=retry_when,
            retry_exit_codes=retry_exit_codes,
            allow_failure=allow_failure,
            allow_failure_exit_codes=allow_failure_exit_codes,
            when=when,
            needs=needs,
            timeout=timeout,
            artifacts_paths=artifacts_paths,
            artifacts_when=artifacts_when,
            dependencies=dependencies,
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
        self.job_executor: JobExecutor | None = None
        self.orchestrator: StageOrchestrator | None = None

    def run_pipeline(
        self,
        config_path: Path | None = None,
        maximum_degree_of_parallelism: int | None = None,
        dry_run: bool = False,
        use_tui: bool = False,
        ci_mode: bool = False,
        job_filter: list[str] | None = None,
        stage_filter: list[str] | None = None,
    ) -> None:
        """
        Run the complete pipeline.

        Args:
            config_path: Path to the pipeline configuration file.
            maximum_degree_of_parallelism: How many jobs can run at same time.
            dry_run: Do we really run jobs.
            use_tui: Use Textual TUI with per-job tabs.
            ci_mode: CI mode - jobs write to files, output printed after each stage.
            job_filter: If given, only run jobs whose names are in this list.
            stage_filter: If given, only run jobs whose stages are in this list.

        Raises:
            GitLabCIError: If there is an error in the pipeline configuration.
            Exception: For unexpected errors.
        """
        # Load and process configuration
        raw_config = self.loader.load_config(config_path)
        pipeline = self.processor.process_config(raw_config)

        # Apply job/stage filters with warnings for unknown names
        if job_filter is not None or stage_filter is not None:
            if job_filter is not None:
                known_jobs = {j.name for j in pipeline.jobs}
                for name in job_filter:
                    if name not in known_jobs:
                        print(f"⚠️  Unknown job: '{name}' (not found in pipeline)")
            if stage_filter is not None:
                known_stages = set(pipeline.stages)
                for name in stage_filter:
                    if name not in known_stages:
                        print(f"⚠️  Unknown stage: '{name}' (not found in pipeline)")
            pipeline = filter_pipeline(pipeline, jobs=job_filter, stages=stage_filter)
            if not pipeline.jobs:
                print("⚠️  No jobs match the given filter — nothing to run.")
                return

        # Set up execution components
        variable_manager = VariableManager(pipeline.variables, project_dir=self.base_path)
        self.job_executor = JobExecutor(variable_manager, dry_run=dry_run, project_dir=self.base_path)

        if use_tui or (ci_mode and not dry_run):
            from bitrab.tui.orchestrator import TUIOrchestrator

            tui_orchestrator = TUIOrchestrator(
                self.job_executor,
                maximum_degree_of_parallelism=maximum_degree_of_parallelism,
            )
            if use_tui:
                from bitrab.tui.app import PipelineApp

                app = PipelineApp(pipeline, tui_orchestrator)
                exit_code = app.run()
                if exit_code:
                    raise RuntimeError("Pipeline failed — see TUI output for details")
            else:
                tui_orchestrator.execute_pipeline_ci(pipeline)
        else:
            self.orchestrator = StageOrchestrator(self.job_executor, maximum_degree_of_parallelism=maximum_degree_of_parallelism, dry_run=dry_run)
            self.orchestrator.execute_pipeline(pipeline)


def best_efforts_run(config_path: Path) -> None:
    """Main entry point for the best-efforts-run command."""
    runner = LocalGitLabRunner()
    runner.run_pipeline(config_path)
