import textwrap

from bitrab.config.rules import _evaluate_if, evaluate_rules
from bitrab.models.pipeline import RuleConfig
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


# ---------------------------------------------------------------------------
# RULES-1: rules: exists
# ---------------------------------------------------------------------------


class TestRulesExists:
    def test_exists_matches_when_file_present(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM alpine")
        from bitrab.config.rules import _evaluate_exists

        assert _evaluate_exists(["Dockerfile"], tmp_path) is True

    def test_exists_no_match_when_file_absent(self, tmp_path):
        from bitrab.config.rules import _evaluate_exists

        assert _evaluate_exists(["Dockerfile"], tmp_path) is False

    def test_exists_glob_pattern(self, tmp_path):
        (tmp_path / "setup.py").write_text("")
        from bitrab.config.rules import _evaluate_exists

        assert _evaluate_exists(["*.py"], tmp_path) is True

    def test_exists_multiple_patterns_any_match(self, tmp_path):
        (tmp_path / "Makefile").write_text("")
        from bitrab.config.rules import _evaluate_exists

        assert _evaluate_exists(["Dockerfile", "Makefile"], tmp_path) is True

    def test_rule_matches_when_exists_file_present(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM alpine")
        from bitrab.config.rules import _rule_matches

        rule = RuleConfig(exists=["Dockerfile"])
        assert _rule_matches(rule, {}, project_dir=tmp_path) is True

    def test_rule_does_not_match_when_exists_file_absent(self, tmp_path):
        from bitrab.config.rules import _rule_matches

        rule = RuleConfig(exists=["Dockerfile"])
        assert _rule_matches(rule, {}, project_dir=tmp_path) is False

    def test_rule_requires_both_if_and_exists(self, tmp_path):
        """Both if_expr and exists must pass."""
        (tmp_path / "Dockerfile").write_text("FROM alpine")
        from bitrab.config.rules import _rule_matches

        rule = RuleConfig(if_expr='$CI_COMMIT_BRANCH == "main"', exists=["Dockerfile"])
        # if_expr fails (var not set) => rule does not match
        assert _rule_matches(rule, {}, project_dir=tmp_path) is False
        # both pass
        assert _rule_matches(rule, {"CI_COMMIT_BRANCH": "main"}, project_dir=tmp_path) is True

    def test_exists_integration(self, tmp_path):
        """End-to-end: job only runs if Dockerfile exists."""
        (tmp_path / "Dockerfile").write_text("FROM alpine")
        ci = textwrap.dedent(
            """
            stages: [build]
            docker_job:
              stage: build
              rules:
                - exists: [Dockerfile]
              script:
                - echo ran > ran.txt
        """
        )
        (tmp_path / ".gitlab-ci.yml").write_text(ci)
        runner = LocalGitLabRunner(base_path=tmp_path)
        runner.run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "ran.txt").exists()

    def test_exists_integration_skips_when_absent(self, tmp_path):
        ci = textwrap.dedent(
            """
            stages: [build]
            docker_job:
              stage: build
              rules:
                - exists: [Dockerfile]
                - when: never
              script:
                - echo ran > ran.txt
        """
        )
        (tmp_path / ".gitlab-ci.yml").write_text(ci)
        runner = LocalGitLabRunner(base_path=tmp_path)
        runner.run_pipeline(maximum_degree_of_parallelism=1)
        assert not (tmp_path / "ran.txt").exists()


# ---------------------------------------------------------------------------
# RULES-2: && / || compound expressions
# ---------------------------------------------------------------------------


class TestCompoundExpressions:
    def test_and_both_true(self):
        env = {"A": "hello", "B": "world"}
        assert _evaluate_if("$A && $B", env) is True

    def test_and_one_false(self):
        env = {"A": "hello"}
        assert _evaluate_if("$A && $B", env) is False

    def test_or_first_true(self):
        env = {"A": "hello"}
        assert _evaluate_if("$A || $B", env) is True

    def test_or_second_true(self):
        env = {"B": "world"}
        assert _evaluate_if("$A || $B", env) is True

    def test_or_both_false(self):
        assert _evaluate_if("$A || $B", {}) is False

    def test_and_binds_tighter_than_or(self):
        # A || B && C  =>  A || (B && C)
        # A=set, B=unset, C=unset  =>  True || False  => True
        env = {"A": "1"}
        assert _evaluate_if("$A || $B && $C", env) is True

    def test_equality_in_compound(self):
        env = {"BRANCH": "main", "SOURCE": "push"}
        expr = '$BRANCH == "main" && $SOURCE == "push"'
        assert _evaluate_if(expr, env) is True

    def test_equality_compound_one_fails(self):
        env = {"BRANCH": "main", "SOURCE": "web"}
        expr = '$BRANCH == "main" && $SOURCE == "push"'
        assert _evaluate_if(expr, env) is False

    def test_or_with_equality(self):
        env = {"BRANCH": "develop"}
        expr = '$BRANCH == "main" || $BRANCH == "develop"'
        assert _evaluate_if(expr, env) is True

    def test_quoted_value_containing_double_ampersand_not_split(self):
        # The literal string "a&&b" inside quotes should not be treated as operator
        env = {"VAR": "a&&b"}
        assert _evaluate_if('$VAR == "a&&b"', env) is True

    def test_compound_with_regex(self):
        env = {"TAG": "v1.2.3", "BRANCH": "main"}
        expr = '$TAG =~ /^v/ && $BRANCH == "main"'
        assert _evaluate_if(expr, env) is True

    def test_three_way_and(self):
        env = {"A": "1", "B": "2", "C": "3"}
        assert _evaluate_if("$A && $B && $C", env) is True

    def test_three_way_and_one_missing(self):
        env = {"A": "1", "B": "2"}
        assert _evaluate_if("$A && $B && $C", env) is False
