from __future__ import annotations

from dataclasses import dataclass, field


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
