"""Moderate performance benchmarks to verify optimizations."""

import yaml

from bitrab.plan import LocalGitLabRunner


def create_config(stages=5, jobs_per_stage=2):
    config = {
        "stages": [f"stage_{i}" for i in range(stages)],
    }
    for i in range(stages):
        for j in range(jobs_per_stage):
            config[f"job_{i}_{j}"] = {"stage": f"stage_{i}", "script": ["echo 'hi'"]}
    return config


def test_benchmark_moderate_dry_run(benchmark, tmp_path):
    config = create_config(stages=5, jobs_per_stage=2)
    ci_file = tmp_path / ".gitlab-ci.yml"
    with open(ci_file, "w") as f:
        yaml.dump(config, f)

    runner = LocalGitLabRunner(base_path=tmp_path)

    def run():
        runner.run_pipeline(config_path=ci_file, dry_run=True, maximum_degree_of_parallelism=2)

    benchmark(run)
