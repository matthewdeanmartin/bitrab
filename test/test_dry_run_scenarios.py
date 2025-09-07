from pathlib import Path

from bitrab.plan import LocalGitLabRunner


def test_run_scenarios():
    config_path = Path("test/scenarios/stress.yml")
    runner = LocalGitLabRunner()
    runner.run_pipeline(config_path, dry_run=True, maximum_degree_of_parallelism=1)
