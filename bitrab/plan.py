from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any, Union

import itertools

from bitrab.config.loader import ConfigurationLoader
from bitrab.config.rules import evaluate_rules
from bitrab.console import safe_print
from bitrab.exceptions import GitlabRunnerError
from bitrab.execution.job import JobExecutor
from bitrab.execution.scheduler import StageOrchestrator
from bitrab.execution.variables import VariableManager
from bitrab.models.pipeline import DefaultConfig, JobConfig, PipelineConfig, RuleConfig

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
        "extends",
    }

    def process_config(self, raw_config: dict[str, Any]) -> PipelineConfig:
        """
        Process raw configuration into structured pipeline config.

        Args:
            raw_config: The raw configuration dictionary.

        Returns:
            A structured PipelineConfig object.
        """
        # Deep-copy to avoid mutating the caller's dict (BUG-4)
        raw_config = copy.deepcopy(raw_config)

        # Resolve extends: directives before building job objects
        raw_config = self._resolve_extends(raw_config)

        # Extract global configuration
        stages = raw_config.get("stages", ["test"])
        global_variables = raw_config.get("variables", {})
        default_config = self._process_default_config(raw_config.get("default", {}))

        # Process jobs — skip reserved keywords and hidden templates (`.name`)
        jobs = []
        for name, job_data in raw_config.items():
            if name not in self.RESERVED_KEYWORDS and not name.startswith(".") and isinstance(job_data, dict):
                job = self._process_job(name, job_data, default_config, global_variables)
                jobs.append(job)

        # Expand parallel: N and parallel: matrix: directives
        jobs = self._expand_parallel_jobs(jobs, raw_config)

        # Resolve needs references to expanded matrix/parallel jobs
        jobs = self._resolve_expanded_needs(jobs)

        return PipelineConfig(stages=stages, variables=global_variables, default=default_config, jobs=jobs)

    def _resolve_extends(self, raw_config: dict[str, Any]) -> dict[str, Any]:
        """Resolve all ``extends:`` directives in *raw_config*.

        GitLab semantics:
        - ``extends:`` may be a string (single parent) or list (multiple
          parents; later entries take precedence, child overrides all).
        - Hidden jobs (keys starting with ``.``) are valid base templates.
        - Circular references raise ``GitlabRunnerError``.
        - ``extends:`` is removed from each job after resolution.

        Args:
            raw_config: The deep-copied configuration dictionary (will be
                mutated in-place and returned).

        Returns:
            The same dict with all ``extends:`` chains resolved.
        """
        # Collect all job-like blocks (real jobs and hidden templates)
        all_jobs: dict[str, dict[str, Any]] = {
            name: data
            for name, data in raw_config.items()
            if isinstance(data, dict) and name not in self.RESERVED_KEYWORDS
        }

        resolved: dict[str, dict[str, Any]] = {}

        def _resolve_one(name: str, chain: list[str]) -> dict[str, Any]:
            if name in resolved:
                return resolved[name]
            if name in chain:
                raise GitlabRunnerError(
                    f"`extends:` circular reference detected: {' -> '.join(chain + [name])}"
                )
            if name not in all_jobs:
                raise GitlabRunnerError(
                    f"`extends:` references unknown job or template: {name!r}"
                )

            job_data = dict(all_jobs[name])
            parents_raw = job_data.pop("extends", None)
            if parents_raw is None:
                resolved[name] = job_data
                return job_data

            parents: list[str] = [parents_raw] if isinstance(parents_raw, str) else list(parents_raw)

            merged: dict[str, Any] = {}
            for parent in parents:
                parent_resolved = _resolve_one(parent, chain + [name])
                merged = self._deep_merge(merged, parent_resolved)

            resolved[name] = self._deep_merge(merged, job_data)
            return resolved[name]

        for name in list(all_jobs):
            _resolve_one(name, [])

        for name, data in resolved.items():
            raw_config[name] = data

        return raw_config

    @staticmethod
    def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
        """Deep-merge *overlay* on top of *base*.

        Dicts are merged recursively. All other types (lists, scalars) are
        fully replaced by the overlay value, matching GitLab ``extends:``
        semantics where a child list completely replaces the parent list.

        Args:
            base: The base dictionary.
            overlay: The overlay dictionary whose values take precedence.

        Returns:
            A new merged dictionary.
        """
        result = base.copy()
        for key, value in overlay.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = PipelineProcessor._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

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

        # rules: conditional execution
        rules_raw = job_data.get("rules", [])
        rules: list[RuleConfig] = []
        if isinstance(rules_raw, list):
            for r in rules_raw:
                if isinstance(r, dict):
                    rule_needs: list[str] | None = None
                    if "needs" in r:
                        rule_needs = []
                        _rn = r["needs"]
                        if isinstance(_rn, list):
                            for item in _rn:
                                if isinstance(item, str):
                                    rule_needs.append(item)
                                elif isinstance(item, dict) and "job" in item:
                                    rule_needs.append(str(item["job"]))

                    rule_exists: list[str] | None = None
                    if "exists" in r:
                        _ex = r["exists"]
                        if isinstance(_ex, list):
                            rule_exists = [str(p) for p in _ex]
                        elif isinstance(_ex, str):
                            rule_exists = [_ex]

                    rules.append(
                        RuleConfig(
                            if_expr=r.get("if"),
                            when=r.get("when"),
                            allow_failure=r.get("allow_failure"),
                            variables=r.get("variables", {}),
                            needs=rule_needs,
                            exists=rule_exists,
                        )
                    )

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
            rules=rules,
            needs=needs,
            timeout=timeout,
            artifacts_paths=artifacts_paths,
            artifacts_when=artifacts_when,
            dependencies=dependencies,
        )

    def _expand_parallel_jobs(
        self,
        jobs: list[JobConfig],
        raw_config: dict[str, Any],
    ) -> list[JobConfig]:
        """Expand jobs that use ``parallel: N`` or ``parallel: matrix:`` into multiple jobs.

        GitLab CI semantics:
        - ``parallel: N`` creates N copies named ``job_name N/N`` with
          ``CI_NODE_INDEX`` (1-based) and ``CI_NODE_TOTAL`` set.
        - ``parallel: matrix: [...]`` computes the Cartesian product of the
          variable lists and creates one job per combination, named
          ``job_name: [VAR1=val1, VAR2=val2]``.
        """
        expanded: list[JobConfig] = []

        for job in jobs:
            raw_job = raw_config.get(job.name, {})
            parallel_raw = raw_job.get("parallel") if isinstance(raw_job, dict) else None

            if parallel_raw is None:
                expanded.append(job)
                continue

            if isinstance(parallel_raw, int):
                # parallel: N — create N copies
                n = max(1, min(parallel_raw, 200))
                for idx in range(1, n + 1):
                    clone = copy.deepcopy(job)
                    clone.name = f"{job.name} {idx}/{n}"
                    clone.parallel_total = n
                    clone.parallel_index = idx
                    clone.variables["CI_NODE_INDEX"] = str(idx)
                    clone.variables["CI_NODE_TOTAL"] = str(n)
                    expanded.append(clone)

            elif isinstance(parallel_raw, dict) and "matrix" in parallel_raw:
                matrix_entries = parallel_raw["matrix"]
                if not isinstance(matrix_entries, list):
                    expanded.append(job)
                    continue

                # Each matrix entry is a dict where values can be scalars or lists.
                # We compute the Cartesian product within each entry, then concatenate
                # all entries to get the full set of combinations.
                all_combos: list[dict[str, str]] = []
                for entry in matrix_entries:
                    if not isinstance(entry, dict):
                        continue
                    keys = sorted(entry.keys())
                    value_lists: list[list[str]] = []
                    for key in keys:
                        val = entry[key]
                        if isinstance(val, list):
                            value_lists.append([str(v) for v in val])
                        else:
                            value_lists.append([str(val)])
                    for combo in itertools.product(*value_lists):
                        all_combos.append(dict(zip(keys, combo)))

                if not all_combos:
                    expanded.append(job)
                    continue

                total = len(all_combos)
                for idx, combo in enumerate(all_combos, 1):
                    clone = copy.deepcopy(job)
                    # GitLab names matrix jobs as: "job_name: [K1=V1, K2=V2]"
                    label = ", ".join(f"{k}={v}" for k, v in sorted(combo.items()))
                    clone.name = f"{job.name}: [{label}]"
                    clone.parallel_total = total
                    clone.parallel_index = idx
                    clone.variables.update(combo)
                    clone.variables["CI_NODE_INDEX"] = str(idx)
                    clone.variables["CI_NODE_TOTAL"] = str(total)
                    expanded.append(clone)
            else:
                expanded.append(job)

        return expanded

    @staticmethod
    def _resolve_expanded_needs(jobs: list[JobConfig]) -> list[JobConfig]:
        """Rewrite ``needs`` entries that reference a job that was expanded by ``parallel:``.

        If job A has ``needs: [B]`` but B was expanded into ``B 1/3``, ``B 2/3``,
        ``B 3/3``, then A's needs list is rewritten to depend on all three
        expanded instances.  References that match an existing job name are left
        unchanged.
        """
        existing_names = {j.name for j in jobs}

        # Build a map: original_name -> list of expanded names
        # Expanded jobs have parallel_total > 0 and their name differs from
        # the original.  We derive the original name by stripping the suffix.
        expanded_map: dict[str, list[str]] = {}
        for job in jobs:
            if job.parallel_total > 0:
                # Parallel: N pattern: "original N/N"
                # Matrix pattern: "original: [KEY=VAL]"
                # We need the original name. We can infer it:
                #   - For "name K/N": everything before " K/N"
                #   - For "name: [...]": everything before ": ["
                if ": [" in job.name:
                    orig = job.name.split(": [")[0]
                elif "/" in job.name:
                    # "original 1/3" -> strip " 1/3"
                    parts = job.name.rsplit(" ", 1)
                    orig = parts[0] if len(parts) == 2 and "/" in parts[1] else job.name
                else:
                    continue
                expanded_map.setdefault(orig, []).append(job.name)

        if not expanded_map:
            return jobs

        for job in jobs:
            # Resolve needs
            if job.needs:
                new_needs: list[str] = []
                for dep in job.needs:
                    if dep in existing_names:
                        new_needs.append(dep)
                    elif dep in expanded_map:
                        new_needs.extend(expanded_map[dep])
                    else:
                        new_needs.append(dep)
                job.needs = new_needs

            # Resolve dependencies (artifact injection)
            if job.dependencies is not None:
                new_deps: list[str] = []
                for dep in job.dependencies:
                    if dep in existing_names:
                        new_deps.append(dep)
                    elif dep in expanded_map:
                        new_deps.extend(expanded_map[dep])
                    else:
                        new_deps.append(dep)
                job.dependencies = new_deps

        return jobs

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
                        safe_print(f"⚠️  Unknown job: '{name}' (not found in pipeline)")
            if stage_filter is not None:
                known_stages = set(pipeline.stages)
                for name in stage_filter:
                    if name not in known_stages:
                        safe_print(f"⚠️  Unknown stage: '{name}' (not found in pipeline)")
            pipeline = filter_pipeline(pipeline, jobs=job_filter, stages=stage_filter)
            if not pipeline.jobs:
                safe_print("⚠️  No jobs match the given filter — nothing to run.")
                return

        # Set up execution components
        variable_manager = VariableManager(pipeline.variables, project_dir=self.base_path)

        # Evaluate rules for each job
        # We use a base environment (os + global + builtin) for rule evaluation
        base_env = os.environ.copy()
        base_env.update(variable_manager.gitlab_ci_vars)
        base_env.update(variable_manager.base_variables)

        for job in pipeline.jobs:
            evaluate_rules(job, base_env, project_dir=self.base_path)

        self.job_executor = JobExecutor(variable_manager, dry_run=dry_run, project_dir=self.base_path)

        from bitrab.mutation import load_mutation_config, load_parallel_config

        mutation_config = load_mutation_config(self.base_path)
        parallel_config = load_parallel_config(self.base_path)

        event_collector = None
        started_at = __import__("time").time()

        if use_tui or (ci_mode and not dry_run):
            from bitrab.tui.orchestrator import TUIOrchestrator

            tui_orchestrator = TUIOrchestrator(
                self.job_executor,
                maximum_degree_of_parallelism=maximum_degree_of_parallelism,
                mutation_config=mutation_config,
                parallel_backend=parallel_config,
            )
            if use_tui:
                from bitrab.tui.app import PipelineApp

                app = PipelineApp(pipeline, tui_orchestrator)
                exit_code = app.run()
                if exit_code:
                    raise RuntimeError("Pipeline failed — see TUI output for details")
            else:
                tui_orchestrator.execute_pipeline_ci(pipeline)
            event_collector = tui_orchestrator.event_collector
        else:
            self.orchestrator = StageOrchestrator(
                self.job_executor,
                maximum_degree_of_parallelism=maximum_degree_of_parallelism,
                dry_run=dry_run,
                mutation_config=mutation_config,
                parallel_backend=parallel_config,
            )
            self.orchestrator.execute_pipeline(pipeline)
            event_collector = getattr(self.orchestrator, "event_collector", None)

        if not dry_run and event_collector is not None:
            _persist_run_log(self.base_path, event_collector, started_at, pipeline)


def _persist_run_log(
    project_dir: Path,
    event_collector: Any,
    started_at: float,
    pipeline: Any,
) -> None:
    """Write a run log to .bitrab/logs/<run_id>/ — silently ignore errors."""
    import logging

    try:
        from bitrab.folder import maybe_warn_size, write_run_log

        summary = event_collector.summary()
        events = event_collector.events

        events_json = [
            {
                "event_type": e.event_type.value,
                "timestamp": e.timestamp,
                "wall_time": e.wall_time,
                "stage": e.stage,
                "job": e.job,
                "data": e.data,
            }
            for e in events
        ]

        meta = {
            "started_at": started_at,
            "success": summary.success,
            "total_duration_s": summary.total_duration_s,
            "job_count": len(pipeline.jobs),
        }

        write_run_log(project_dir, events_json, summary.format_text(), meta)

        warn = maybe_warn_size(project_dir)
        if warn:
            from bitrab.console import safe_print

            safe_print(warn)
    except Exception as exc:  # pylint: disable=broad-except
        logging.debug("Failed to persist run log: %s", exc)


def best_efforts_run(config_path: Path) -> None:
    """Main entry point for the best-efforts-run command."""
    runner = LocalGitLabRunner()
    runner.run_pipeline(config_path)
