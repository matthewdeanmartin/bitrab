import textwrap

from bitrab.plan import LocalGitLabRunner


def test_rules_skips_when_no_match(tmp_path):
    ci = """
    stages:
      - build
      - release

    build_job:
      stage: build
      script:
        - echo "building" > built.txt

    publish_job:
      stage: release
      rules:
        - if: "$CI_COMMIT_TAG"
          when: on_success
        - when: never
      script:
        - echo "publishing" > published.txt
    """
    p = tmp_path / ".gitlab-ci.yml"
    p.write_text(textwrap.dedent(ci))

    runner = LocalGitLabRunner(base_path=tmp_path)
    runner.run_pipeline(maximum_degree_of_parallelism=1)

    assert (tmp_path / "built.txt").exists()
    assert not (tmp_path / "published.txt").exists(), "publish_job should have been skipped by rules: never"


def test_rules_matches_global_variable(tmp_path):
    ci = """
    stages:
      - build
      - release

    variables:
      CI_COMMIT_TAG: "v1.0.0"

    build_job:
      stage: build
      script:
        - echo "building" > built.txt

    publish_job:
      stage: release
      rules:
        - if: "$CI_COMMIT_TAG"
          when: on_success
        - when: never
      script:
        - echo "publishing" > published.txt
    """
    p = tmp_path / ".gitlab-ci.yml"
    p.write_text(textwrap.dedent(ci))

    runner = LocalGitLabRunner(base_path=tmp_path)
    runner.run_pipeline(maximum_degree_of_parallelism=1)

    assert (tmp_path / "built.txt").exists()
    assert (tmp_path / "published.txt").exists(), "publish_job should have run because CI_COMMIT_TAG is set"


def test_rules_variables_override(tmp_path):
    ci = """
    stages: [test]
    variables:
      CI_COMMIT_TAG: "v1.0.0"
    
    test_job:
      stage: test
      variables:
        VAR: "original"
      rules:
        - if: "$CI_COMMIT_TAG"
          variables:
            VAR: "overridden"
      script:
        - echo $VAR > var.txt
    """
    p = tmp_path / ".gitlab-ci.yml"
    p.write_text(textwrap.dedent(ci))

    runner = LocalGitLabRunner(base_path=tmp_path)
    runner.run_pipeline(maximum_degree_of_parallelism=1)

    assert (tmp_path / "var.txt").read_text().strip() == "overridden"


def test_rules_needs_override(tmp_path):
    ci = """
    stages: [build, test]
    variables:
      CI_COMMIT_TAG: "v1.0.0"
    
    build_a:
      stage: build
      script: [echo build_a]
    
    build_b:
      stage: build
      script: [echo build_b]
      
    test_job:
      stage: test
      needs: [build_a]
      rules:
        - if: "$CI_COMMIT_TAG"
          needs: [build_b]
      script:
        - echo hi
    """
    p = tmp_path / ".gitlab-ci.yml"
    p.write_text(textwrap.dedent(ci))

    from bitrab.config.loader import ConfigurationLoader
    from bitrab.config.rules import evaluate_rules
    from bitrab.execution.variables import VariableManager
    from bitrab.plan import PipelineProcessor

    loader = ConfigurationLoader(base_path=tmp_path)
    raw = loader.load_config(p)
    processor = PipelineProcessor()
    pipeline = processor.process_config(raw)

    vm = VariableManager(pipeline.variables, project_dir=tmp_path)
    env = vm._get_gitlab_ci_variables()
    env.update(vm.base_variables)

    test_job = next(j for j in pipeline.jobs if j.name == "test_job")
    assert test_job.needs == ["build_a"]

    evaluate_rules(test_job, env)
    assert test_job.needs == ["build_b"], "needs should have been overridden by rules"
