from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RuleConfig:
    """
    Configuration for a single rule in a job.

    Attributes:
        if_expr: Expression to evaluate (e.g. '$CI_COMMIT_TAG').
        when: Condition to apply if rule matches.
        allow_failure: Override allow_failure if rule matches.
        variables: Variables to inject if rule matches.
        needs: Override needs if rule matches.
    """

    if_expr: str | None = None
    when: str | None = None
    allow_failure: bool | None = None
    variables: dict[str, str] = field(default_factory=dict)
    needs: list[str] | None = None


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

    # GitLab-aligned retry fields
    retry_max: int = 0
    retry_when: list[str] = field(default_factory=list)
    retry_exit_codes: list[int] = field(default_factory=list)  # empty => not used

    # allow_failure: job failure doesn't fail the pipeline
    allow_failure: bool = False
    allow_failure_exit_codes: list[int] = field(default_factory=list)

    # when: controls job execution condition
    when: str = "on_success"  # on_success | on_failure | always | manual | never

    # rules: list of conditional rules
    rules: list[RuleConfig] = field(default_factory=list)

    # DAG execution: explicit job dependencies (bypasses stage ordering)
    needs: list[str] = field(default_factory=list)

    # timeout: maximum seconds the job may run (None = no limit)
    timeout: float | None = None

    # artifacts: files to preserve after job completion
    artifacts_paths: list[str] = field(default_factory=list)
    artifacts_when: str = "on_success"  # on_success | on_failure | always

    # dependencies: named jobs whose artifacts to copy before this job runs
    # None = inherit all (GitLab default); [] = no artifacts
    dependencies: list[str] | None = None


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
