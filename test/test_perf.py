"""Performance benchmarks for core parsing and validation."""

from bitrab.plan import PipelineProcessor

# A fairly complex but static gitlab-ci.yml equivalent
SAMPLE_CONFIG = {
    "stages": ["build", "test", "deploy"],
    "variables": {"GLOBAL_VAR": "global_value"},
    "build_job": {"stage": "build", "script": ["echo 'Building'"]},
    "test_job_1": {"stage": "test", "script": ["echo 'Testing 1'"], "needs": ["build_job"]},
    "test_job_2": {"stage": "test", "script": ["echo 'Testing 2'"], "needs": ["build_job"]},
    "deploy_job": {"stage": "deploy", "script": ["echo 'Deploying'"], "rules": [{"if": "$CI_COMMIT_BRANCH == 'main'"}]},
}


def test_benchmark_process_config(benchmark):
    """Benchmark the parsing, validation, and DAG creation."""
    processor = PipelineProcessor()

    def process():
        return processor.process_config(SAMPLE_CONFIG)

    # We run the process function repeatedly to measure performance.
    result = benchmark(process)

    # Just a small sanity check to make sure the work was actually done
    assert result is not None
    assert any(job.name == "build_job" for job in result.jobs)
