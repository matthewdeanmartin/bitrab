"""Scenario-based integration tests for realistic CI pipeline use-cases.

Covers four broad scenarios:
  - Quality gates (lint, type-check, coverage thresholds)
  - Native code build chains (C/C++ style: configure → compile → link → test)
  - Convenience task runner (monorepo helper scripts, code generation)
  - JavaScript/frontend pipeline (install → build → test → bundle)

Each scenario exercises real bitrab behaviours: DAGs, parallel stages,
allow_failure, artifacts, when conditions, variable precedence.
"""

from __future__ import annotations

import subprocess  # nosec
import textwrap
from pathlib import Path

import pytest

from bitrab.config.loader import ConfigurationLoader
from bitrab.exceptions import JobExecutionError
from bitrab.execution.artifacts import artifact_dir, collect_artifacts, inject_dependencies
from bitrab.execution.stage_runner import (
    build_dag,
    filter_jobs_by_when,
    has_dag_jobs,
    organize_jobs_by_stage,
    sanitize_job_name,
)
from bitrab.execution.variables import VariableManager
from bitrab.models.pipeline import JobConfig, PipelineConfig
from bitrab.plan import LocalGitLabRunner, PipelineProcessor, filter_pipeline, parse_duration

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_ci(tmp_path: Path, content: str) -> Path:
    p = tmp_path / ".gitlab-ci.yml"
    p.write_text(textwrap.dedent(content))
    return p


def runner(tmp_path: Path) -> LocalGitLabRunner:
    return LocalGitLabRunner(base_path=tmp_path)


def proc(raw: dict) -> PipelineConfig:
    return PipelineProcessor().process_config(raw)


# ===========================================================================
# parse_duration — pure function, easy to test exhaustively
# ===========================================================================


class TestParseDuration:
    def test_none_returns_none(self):
        assert parse_duration(None) is None

    def test_zero_string_returns_none(self):
        # "0" has no unit groups, total==0 → falls through to float("0") == 0.0
        assert parse_duration("0") == 0.0

    def test_plain_int(self):
        assert parse_duration(42) == 42.0

    def test_plain_float(self):
        assert parse_duration(1.5) == 1.5

    def test_seconds_suffix(self):
        assert parse_duration("30s") == 30.0
        assert parse_duration("30sec") == 30.0
        assert parse_duration("30seconds") == 30.0

    def test_minutes_suffix(self):
        assert parse_duration("5m") == 300.0
        assert parse_duration("5min") == 300.0
        assert parse_duration("5minutes") == 300.0

    def test_hours_suffix(self):
        assert parse_duration("2h") == 7200.0
        assert parse_duration("2hours") == 7200.0

    def test_days_suffix(self):
        assert parse_duration("1d") == 86400.0
        assert parse_duration("1day") == 86400.0

    def test_weeks_suffix(self):
        assert parse_duration("1w") == 7 * 86400.0
        assert parse_duration("1week") == 7 * 86400.0

    def test_composite_1h30m(self):
        assert parse_duration("1h 30m") == 5400.0

    def test_composite_2h_30m_15s(self):
        assert parse_duration("2h 30m 15s") == pytest.approx(9015.0)

    def test_composite_1d_6h(self):
        assert parse_duration("1d 6h") == pytest.approx(108000.0)

    def test_numeric_string(self):
        assert parse_duration("3600") == 3600.0

    def test_empty_string_returns_none(self):
        assert parse_duration("") is None

    def test_garbage_string_returns_none(self):
        assert parse_duration("notaduration") is None

    def test_whitespace_only_returns_none(self):
        assert parse_duration("   ") is None


# ===========================================================================
# sanitize_job_name
# ===========================================================================


class TestSanitizeJobName:
    def test_clean_name_unchanged(self):
        assert sanitize_job_name("build_job") == "build_job"

    def test_colon_replaced(self):
        assert sanitize_job_name("stage:job") == "stage_job"

    def test_slash_replaced(self):
        assert sanitize_job_name("a/b") == "a_b"

    def test_backslash_replaced(self):
        assert sanitize_job_name("a\\b") == "a_b"

    def test_multiple_invalid_chars(self):
        # : / * ? " < > | are all replaced → 8 replacements
        result = sanitize_job_name('a:b/c*d?"<>|e')
        assert result == "a_b_c_d_____e"

    def test_empty_string(self):
        assert sanitize_job_name("") == ""


# ===========================================================================
# VariableManager
# ===========================================================================


class TestVariableManager:
    def test_ci_variable_set(self, tmp_path):
        vm = VariableManager(project_dir=tmp_path)
        job = JobConfig(name="myjob", stage="build", variables={})
        env = vm.prepare_environment(job)
        assert env["CI"] == "true"
        assert env["CI_JOB_STAGE"] == "build"
        assert env["CI_JOB_NAME"] == "myjob"
        assert env["CI_PROJECT_DIR"] == str(tmp_path)

    def test_base_variables_override_gitlab_ci_vars(self, tmp_path):
        # Users can override even CI-named vars via base variables
        vm = VariableManager(base_variables={"MY_VAR": "hello"}, project_dir=tmp_path)
        job = JobConfig(name="j", stage="test", variables={})
        env = vm.prepare_environment(job)
        assert env["MY_VAR"] == "hello"

    def test_job_variables_take_highest_precedence(self, tmp_path):
        vm = VariableManager(base_variables={"FOO": "base"}, project_dir=tmp_path)
        job = JobConfig(name="j", stage="test", variables={"FOO": "job"})
        env = vm.prepare_environment(job)
        assert env["FOO"] == "job"

    def test_ci_project_name_derived_from_dir(self, tmp_path):
        project_dir = tmp_path / "my_project"
        project_dir.mkdir()
        vm = VariableManager(project_dir=project_dir)
        job = JobConfig(name="j", stage="test", variables={})
        env = vm.prepare_environment(job)
        assert env["CI_PROJECT_NAME"] == "my_project"

    def test_git_variables_batched_for_repo_metadata(self, tmp_path, monkeypatch):
        commands: list[tuple[str, ...]] = []
        real_run = subprocess.run

        def counting_run(*args, **kwargs):
            cmd = tuple(args[0])
            commands.append(cmd)
            return real_run(*args, **kwargs)

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # nosec
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], check=True)  # nosec
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)  # nosec
        subprocess.run(
            ["git", "-C", str(tmp_path), "remote", "add", "origin", "git@github.com:octo-org/sample-repo.git"],
            check=True,
        )  # nosec
        (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(tmp_path), "add", "README.md"], check=True)  # nosec
        subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"], check=True)  # nosec

        monkeypatch.setattr("bitrab.execution.variables.subprocess.run", counting_run)

        vm = VariableManager(project_dir=tmp_path)
        env = vm.prepare_environment(JobConfig(name="j", stage="test", variables={}))

        assert env["CI_COMMIT_SHA"]
        assert env["CI_COMMIT_TITLE"] == "init"
        assert env["CI_PROJECT_NAMESPACE"] == "octo-org"
        assert env["CI_PROJECT_PATH"] == "octo-org/sample-repo"
        assert env["CI_PROJECT_URL"] == "https://github.com/octo-org/sample-repo"
        assert len(commands) == 4
        assert commands[0][1:3] == ("log", "-1")
        assert commands[1][1:] == ("branch", "--show-current")
        assert commands[2][1:4] == ("describe", "--tags", "--exact-match")
        assert commands[3][1:4] == ("remote", "get-url", "origin")


# ===========================================================================
# ConfigurationLoader — local includes
# ===========================================================================


class TestConfigurationLoader:
    def test_load_simple_config(self, tmp_path):
        write_ci(
            tmp_path,
            """
            stages: [build]
            build_job:
              stage: build
              script: [make]
            """,
        )
        loader = ConfigurationLoader(base_path=tmp_path)
        raw = loader.load_config(tmp_path / ".gitlab-ci.yml")
        assert "build_job" in raw
        assert raw["stages"] == ["build"]

    def test_local_include_merged(self, tmp_path):
        shared = tmp_path / "shared.yml"
        shared.write_text("shared_job:\n  script: [echo shared]\n")
        write_ci(
            tmp_path,
            """
            include:
              - local: shared.yml
            stages: [test]
            """,
        )
        loader = ConfigurationLoader(base_path=tmp_path)
        raw = loader.load_config(tmp_path / ".gitlab-ci.yml")
        assert "shared_job" in raw

    def test_include_overrides_base(self, tmp_path):
        """Main config keys override included keys."""
        shared = tmp_path / "defaults.yml"
        shared.write_text("variables:\n  COLOR: red\n")
        write_ci(
            tmp_path,
            """
            include:
              - local: defaults.yml
            variables:
              COLOR: blue
            stages: [test]
            job:
              script: [echo hi]
            """,
        )
        loader = ConfigurationLoader(base_path=tmp_path)
        raw = loader.load_config(tmp_path / ".gitlab-ci.yml")
        assert raw["variables"]["COLOR"] == "blue"

    def test_recursive_include_prevention(self, tmp_path):
        """A file that includes itself should not loop forever."""
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text("include:\n  - local: .gitlab-ci.yml\nstages: [test]\njob:\n  script: [echo hi]\n")
        loader = ConfigurationLoader(base_path=tmp_path)
        raw = loader.load_config(ci)
        assert "job" in raw  # loaded successfully, recursion was skipped

    def test_missing_file_raises(self, tmp_path):
        from bitrab.exceptions import GitlabRunnerError

        loader = ConfigurationLoader(base_path=tmp_path)
        with pytest.raises(GitlabRunnerError, match="not found"):
            loader.load_config(tmp_path / "nonexistent.yml")


# ===========================================================================
# PipelineProcessor — realistic raw config parsing
# ===========================================================================


class TestPipelineProcessor:
    def test_default_stage_is_test(self):
        raw = {"job": {"script": ["echo hi"]}}
        pipeline = proc(raw)
        assert pipeline.jobs[0].stage == "test"

    def test_global_variables_in_job(self):
        raw = {
            "variables": {"REGISTRY": "registry.example.com"},
            "push": {"stage": "test", "script": ["docker push $REGISTRY/image"]},
        }
        pipeline = proc(raw)
        assert pipeline.jobs[0].variables["REGISTRY"] == "registry.example.com"

    def test_default_before_script_inherited(self):
        raw = {
            "default": {"before_script": ["source venv/bin/activate"]},
            "test": {"script": ["pytest"]},
        }
        pipeline = proc(raw)
        assert pipeline.jobs[0].before_script == ["source venv/bin/activate"]

    def test_job_before_script_overrides_default(self):
        raw = {
            "default": {"before_script": ["global setup"]},
            "test": {"before_script": ["local setup"], "script": ["pytest"]},
        }
        pipeline = proc(raw)
        assert pipeline.jobs[0].before_script == ["local setup"]

    def test_retry_int(self):
        raw = {"job": {"script": ["flaky"], "retry": 2}}
        pipeline = proc(raw)
        assert pipeline.jobs[0].retry_max == 2

    def test_retry_dict(self):
        raw = {
            "job": {
                "script": ["flaky"],
                "retry": {"max": 3, "when": "script_failure"},
            }
        }
        pipeline = proc(raw)
        job = pipeline.jobs[0]
        assert job.retry_max == 3
        assert job.retry_when == ["script_failure"]

    def test_allow_failure_bool(self):
        raw = {"job": {"script": ["exit 1"], "allow_failure": True}}
        pipeline = proc(raw)
        assert pipeline.jobs[0].allow_failure is True

    def test_allow_failure_exit_codes(self):
        raw = {
            "job": {
                "script": ["exit 42"],
                "allow_failure": {"exit_codes": [42, 100]},
            }
        }
        pipeline = proc(raw)
        job = pipeline.jobs[0]
        assert job.allow_failure is True
        assert job.allow_failure_exit_codes == [42, 100]

    def test_needs_string_list(self):
        raw = {
            "stages": ["build", "test"],
            "build": {"stage": "build", "script": ["make"]},
            "test": {"stage": "test", "script": ["pytest"], "needs": ["build"]},
        }
        pipeline = proc(raw)
        test_job = next(j for j in pipeline.jobs if j.name == "test")
        assert test_job.needs == ["build"]

    def test_needs_dict_form(self):
        raw = {
            "stages": ["build", "test"],
            "build": {"stage": "build", "script": ["make"]},
            "test": {
                "stage": "test",
                "script": ["pytest"],
                "needs": [{"job": "build"}],
            },
        }
        pipeline = proc(raw)
        test_job = next(j for j in pipeline.jobs if j.name == "test")
        assert test_job.needs == ["build"]

    def test_timeout_parsed(self):
        raw = {"job": {"script": ["echo hi"], "timeout": "30m"}}
        pipeline = proc(raw)
        assert pipeline.jobs[0].timeout == 1800.0

    def test_artifacts_parsed(self):
        raw = {
            "job": {
                "script": ["make"],
                "artifacts": {"paths": ["dist/", "*.so"], "when": "always"},
            }
        }
        pipeline = proc(raw)
        job = pipeline.jobs[0]
        assert "dist/" in job.artifacts_paths
        assert "*.so" in job.artifacts_paths
        assert job.artifacts_when == "always"

    def test_when_keyword(self):
        raw = {"job": {"script": ["echo hi"], "when": "on_failure"}}
        pipeline = proc(raw)
        assert pipeline.jobs[0].when == "on_failure"

    def test_invalid_when_defaults_to_on_success(self):
        raw = {"job": {"script": ["echo hi"], "when": "bogus"}}
        pipeline = proc(raw)
        assert pipeline.jobs[0].when == "on_success"

    def test_dependencies_empty_list(self):
        raw = {"job": {"script": ["echo hi"], "dependencies": []}}
        pipeline = proc(raw)
        assert pipeline.jobs[0].dependencies == []

    def test_dependencies_named_jobs(self):
        raw = {"job": {"script": ["echo hi"], "dependencies": ["build", "lint"]}}
        pipeline = proc(raw)
        assert pipeline.jobs[0].dependencies == ["build", "lint"]

    def test_dependencies_none_when_omitted(self):
        raw = {"job": {"script": ["echo hi"]}}
        pipeline = proc(raw)
        assert pipeline.jobs[0].dependencies is None

    def test_reserved_keywords_not_treated_as_jobs(self):
        raw = {
            "stages": ["test"],
            "variables": {"A": "1"},
            "default": {"before_script": ["echo hi"]},
            "image": "python:3.12",
            "services": ["postgres:14"],
            "before_script": ["global bs"],
            "after_script": ["global as"],
            "cache": {"key": "default", "paths": [".cache/"]},
            "artifacts": {"paths": ["dist/"]},
            "myjob": {"script": ["pytest"]},
        }
        pipeline = proc(raw)
        assert len(pipeline.jobs) == 1
        assert pipeline.jobs[0].name == "myjob"


# ===========================================================================
# filter_jobs_by_when (stage_runner helper)
# ===========================================================================


class TestFilterJobsByWhen:
    def jobs(self, *specs):
        return [JobConfig(name=n, stage="test", when=w) for n, w in specs]

    def test_on_success_runs_when_no_failure(self):
        jobs = self.jobs(("j", "on_success"))
        result = filter_jobs_by_when(jobs, prior_had_failure=False)
        assert len(result) == 1

    def test_on_success_skipped_after_failure(self):
        jobs = self.jobs(("j", "on_success"))
        result = filter_jobs_by_when(jobs, prior_had_failure=True)
        assert result == []

    def test_on_failure_runs_after_failure(self):
        jobs = self.jobs(("j", "on_failure"))
        result = filter_jobs_by_when(jobs, prior_had_failure=True)
        assert len(result) == 1

    def test_on_failure_skipped_when_no_failure(self):
        jobs = self.jobs(("j", "on_failure"))
        result = filter_jobs_by_when(jobs, prior_had_failure=False)
        assert result == []

    def test_always_runs_regardless(self):
        jobs = self.jobs(("j", "always"))
        assert len(filter_jobs_by_when(jobs, False)) == 1
        assert len(filter_jobs_by_when(jobs, True)) == 1

    def test_never_always_skipped(self):
        jobs = self.jobs(("j", "never"))
        assert filter_jobs_by_when(jobs, False) == []
        assert filter_jobs_by_when(jobs, True) == []

    def test_manual_always_skipped(self):
        jobs = self.jobs(("j", "manual"))
        assert filter_jobs_by_when(jobs, False) == []

    def test_mixed_jobs(self):
        jobs = self.jobs(
            ("success_job", "on_success"),
            ("failure_job", "on_failure"),
            ("always_job", "always"),
            ("never_job", "never"),
        )
        result = filter_jobs_by_when(jobs, prior_had_failure=True)
        names = {j.name for j in result}
        assert "failure_job" in names
        assert "always_job" in names
        assert "success_job" not in names
        assert "never_job" not in names


# ===========================================================================
# has_dag_jobs / organize_jobs_by_stage
# ===========================================================================


class TestStageRunnerHelpers:
    def test_no_dag_when_no_needs(self):
        pipeline = proc({"stages": ["test"], "job": {"script": ["echo hi"]}})
        assert not has_dag_jobs(pipeline)

    def test_has_dag_when_needs_present(self):
        raw = {
            "stages": ["build", "test"],
            "build": {"stage": "build", "script": ["make"]},
            "test": {"stage": "test", "script": ["pytest"], "needs": ["build"]},
        }
        pipeline = proc(raw)
        assert has_dag_jobs(pipeline)

    def test_organize_by_stage(self):
        raw = {
            "stages": ["build", "test"],
            "build_job": {"stage": "build", "script": ["make"]},
            "test_a": {"stage": "test", "script": ["pytest"]},
            "test_b": {"stage": "test", "script": ["ruff"]},
        }
        pipeline = proc(raw)
        by_stage = organize_jobs_by_stage(pipeline)
        assert len(by_stage["build"]) == 1
        assert len(by_stage["test"]) == 2

    def test_build_dag_no_cycles(self):
        raw = {
            "stages": ["build", "test"],
            "build": {"stage": "build", "script": ["make"]},
            "test": {"stage": "test", "script": ["pytest"], "needs": ["build"]},
        }
        pipeline = proc(raw)
        ts = build_dag(pipeline)
        ts.prepare()
        # Should not raise CycleError

    def test_build_dag_cycle_detected(self):
        from graphlib import CycleError

        raw = {
            "stages": ["build", "test"],
            "a": {"stage": "build", "script": ["x"], "needs": ["b"]},
            "b": {"stage": "test", "script": ["y"], "needs": ["a"]},
        }
        pipeline = proc(raw)
        ts = build_dag(pipeline)
        with pytest.raises(CycleError):
            ts.prepare()


# ===========================================================================
# Artifact helpers (unit)
# ===========================================================================


class TestArtifactHelpers:
    def test_collect_on_success_creates_dir(self, tmp_path):
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / "app").write_text("binary")
        job = JobConfig(
            name="build",
            stage="build",
            artifacts_paths=["dist/"],
            artifacts_when="on_success",
        )
        collect_artifacts(job, tmp_path, succeeded=True)
        dest = artifact_dir(tmp_path, "build")
        assert (dest / "dist" / "app").exists()

    def test_collect_skipped_on_failure_when_on_success(self, tmp_path):
        (tmp_path / "report.xml").write_text("<test/>")
        job = JobConfig(
            name="test",
            stage="test",
            artifacts_paths=["report.xml"],
            artifacts_when="on_success",
        )
        collect_artifacts(job, tmp_path, succeeded=False)
        dest = artifact_dir(tmp_path, "test")
        assert not dest.exists()

    def test_collect_on_failure_only_when_failed(self, tmp_path):
        (tmp_path / "error.log").write_text("oops")
        job = JobConfig(
            name="build",
            stage="build",
            artifacts_paths=["error.log"],
            artifacts_when="on_failure",
        )
        collect_artifacts(job, tmp_path, succeeded=False)
        dest = artifact_dir(tmp_path, "build")
        assert (dest / "error.log").exists()

    def test_collect_always_regardless_of_status(self, tmp_path):
        (tmp_path / "coverage.xml").write_text("<cov/>")
        job = JobConfig(
            name="test",
            stage="test",
            artifacts_paths=["coverage.xml"],
            artifacts_when="always",
        )
        collect_artifacts(job, tmp_path, succeeded=False)
        dest = artifact_dir(tmp_path, "test")
        assert (dest / "coverage.xml").exists()

    def test_collect_no_paths_is_noop(self, tmp_path):
        job = JobConfig(name="test", stage="test")
        collect_artifacts(job, tmp_path, succeeded=True)
        assert not (tmp_path / ".bitrab").exists()

    def test_inject_dependencies_none_copies_all(self, tmp_path):
        # Simulate build job artifacts
        art_dir = artifact_dir(tmp_path, "build")
        art_dir.mkdir(parents=True)
        (art_dir / "app.so").write_text("compiled")
        job = JobConfig(name="test", stage="test", dependencies=None)
        inject_dependencies(job, tmp_path, completed_jobs=["build"])
        assert (tmp_path / "app.so").exists()

    def test_inject_dependencies_empty_copies_nothing(self, tmp_path):
        art_dir = artifact_dir(tmp_path, "build")
        art_dir.mkdir(parents=True)
        (art_dir / "app.so").write_text("compiled")
        job = JobConfig(name="test", stage="test", dependencies=[])
        inject_dependencies(job, tmp_path, completed_jobs=["build"])
        assert not (tmp_path / "app.so").exists()

    def test_inject_dependencies_named(self, tmp_path):
        for dep in ["build", "codegen"]:
            d = artifact_dir(tmp_path, dep)
            d.mkdir(parents=True)
            (d / f"{dep}.out").write_text(dep)
        job = JobConfig(name="test", stage="test", dependencies=["build"])
        inject_dependencies(job, tmp_path, completed_jobs=["build", "codegen"])
        assert (tmp_path / "build.out").exists()
        assert not (tmp_path / "codegen.out").exists()

    def test_sanitize_job_name_in_artifact_dir(self, tmp_path):
        # Jobs with special chars in names should still get a valid artifact dir
        job = JobConfig(
            name="build:linux/x86_64",
            stage="build",
            artifacts_paths=["bin/"],
            artifacts_when="on_success",
        )
        (tmp_path / "bin").mkdir()
        (tmp_path / "bin" / "app").write_text("exe")
        collect_artifacts(job, tmp_path, succeeded=True)
        dest = artifact_dir(tmp_path, "build:linux/x86_64")
        assert dest.exists()


# ===========================================================================
# SCENARIO: Quality Gate Pipeline
# Simulates: lint → type-check → unit-test (parallel) → coverage gate
# Uses: parallel jobs, allow_failure on lint, on_failure notification job
# ===========================================================================


class TestQualityGatePipeline:
    CI = """\
        stages:
          - lint
          - test
          - report

        variables:
          MIN_COVERAGE: "80"

        lint:
          stage: lint
          allow_failure: true
          script:
            - echo "ruff check src/" > lint_result.txt

        type_check:
          stage: lint
          script:
            - echo "mypy src/" > typecheck_result.txt

        unit_tests:
          stage: test
          script:
            - echo "pytest --cov=src" > test_result.txt

        coverage_report:
          stage: report
          script:
            - 'echo "coverage 95 pct" > coverage.txt'

        notify_failure:
          stage: report
          when: on_failure
          script:
            - echo "FAILED" > failure_notify.txt
        """

    def test_quality_gate_runs_all_stages(self, tmp_path):
        write_ci(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "lint_result.txt").exists()
        assert (tmp_path / "typecheck_result.txt").exists()
        assert (tmp_path / "test_result.txt").exists()
        assert (tmp_path / "coverage.txt").exists()

    def test_quality_gate_no_failure_notification_on_success(self, tmp_path):
        write_ci(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert not (tmp_path / "failure_notify.txt").exists()

    def test_quality_gate_failure_triggers_notify(self, tmp_path):
        ci = """\
            stages: [lint, report]
            lint:
              stage: lint
              script:
                - exit 1
            notify_failure:
              stage: report
              when: on_failure
              script:
                - echo "FAILED" > failure_notify.txt
            """
        write_ci(tmp_path, ci)
        with pytest.raises(JobExecutionError):
            runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "failure_notify.txt").exists()

    def test_quality_gate_lint_allow_failure_continues_pipeline(self, tmp_path):
        """allow_failure jobs don't raise, but they do set prior_had_failure=True
        which affects on_success jobs in later stages.  Use when: always to
        verify the pipeline itself doesn't raise.
        """
        ci = """\
            stages: [lint, test]
            lint:
              stage: lint
              allow_failure: true
              script:
                - exit 1
            test:
              stage: test
              when: always
              script:
                - echo "tests passed" > test_result.txt
            """
        write_ci(tmp_path, ci)
        # Should not raise despite lint failing
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "test_result.txt").exists()

    def test_quality_gate_allow_failure_exit_code_gate(self, tmp_path):
        """Coverage below threshold (exit 1) is a known-good allow_failure code.
        The pipeline should not raise, and a when:always job still runs.
        """
        ci = """\
            stages: [check, report]
            coverage_gate:
              stage: check
              allow_failure:
                exit_codes: [1]
              script:
                - exit 1
            publish:
              stage: report
              when: always
              script:
                - echo "published" > published.txt
            """
        write_ci(tmp_path, ci)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "published.txt").exists()

    def test_filter_to_lint_stage_only(self, tmp_path):
        write_ci(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(
            maximum_degree_of_parallelism=1,
            stage_filter=["lint"],
        )
        assert (tmp_path / "lint_result.txt").exists()
        assert (tmp_path / "typecheck_result.txt").exists()
        assert not (tmp_path / "test_result.txt").exists()


# ===========================================================================
# SCENARIO: Native Build Chain (C/Rust-like)
# configure → [compile_core, compile_tests] (parallel) → link → run_tests
# Uses: DAG (needs), artifacts, parallel compile
# ===========================================================================


class TestNativeBuildChain:
    CI = """\
        stages:
          - configure
          - compile
          - link
          - test

        configure:
          stage: configure
          script:
            - echo "cmake -B build" > configure.log
            - mkdir -p build
            - echo "configured" > build/config.stamp

        compile_core:
          stage: compile
          needs: [configure]
          script:
            - echo "gcc -c core.c" > compile_core.log
            - echo "compiled" > build/core.o
          artifacts:
            paths:
              - build/core.o
              - compile_core.log
            when: on_success

        compile_tests:
          stage: compile
          needs: [configure]
          script:
            - echo "gcc -c test.c" > compile_tests.log
            - echo "compiled" > build/test.o
          artifacts:
            paths:
              - build/test.o
              - compile_tests.log
            when: on_success

        link:
          stage: link
          needs: [compile_core, compile_tests]
          script:
            - echo "gcc -o myapp build/core.o build/test.o" > link.log
            - echo "linked" > myapp

        run_tests:
          stage: test
          needs: [link]
          script:
            - echo "./myapp --test" > test_run.log
        """

    def test_native_chain_completes(self, tmp_path):
        write_ci(tmp_path, self.CI)
        (tmp_path / "build").mkdir()
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "configure.log").exists()
        assert (tmp_path / "compile_core.log").exists()
        assert (tmp_path / "compile_tests.log").exists()
        assert (tmp_path / "link.log").exists()
        assert (tmp_path / "test_run.log").exists()

    def test_native_chain_uses_dag(self, tmp_path):
        write_ci(tmp_path, self.CI)
        loader = ConfigurationLoader(base_path=tmp_path)
        raw = loader.load_config(tmp_path / ".gitlab-ci.yml")
        pipeline = PipelineProcessor().process_config(raw)
        assert has_dag_jobs(pipeline)

    def test_native_chain_filter_to_compile_only(self, tmp_path):
        write_ci(tmp_path, self.CI)
        # Only run the configure + compile_core jobs
        runner(tmp_path).run_pipeline(
            maximum_degree_of_parallelism=1,
            job_filter=["configure", "compile_core"],
        )
        assert (tmp_path / "configure.log").exists()
        assert (tmp_path / "compile_core.log").exists()
        assert not (tmp_path / "compile_tests.log").exists()
        assert not (tmp_path / "link.log").exists()

    def test_artifact_collection_on_success(self, tmp_path):
        ci = """\
            stages: [compile]
            compile:
              stage: compile
              script:
                - mkdir -p build
                - echo "obj data" > build/app.o
              artifacts:
                paths:
                  - build/app.o
                when: on_success
            """
        write_ci(tmp_path, ci)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        dest = artifact_dir(tmp_path, "compile")
        assert (dest / "build" / "app.o").exists()


# ===========================================================================
# SCENARIO: Convenience Task Runner (monorepo)
# generate_protos → [build_svc_a, build_svc_b, build_svc_c] → integration_test
# Uses: codegen-style job, parallel builds, needs, variables
# ===========================================================================


class TestConvenienceTaskRunner:
    CI = """\
        stages:
          - codegen
          - build
          - integration

        variables:
          PROTO_DIR: "proto"
          OUTPUT_DIR: "gen"

        generate_protos:
          stage: codegen
          script:
            - mkdir -p gen
            - echo "protoc --go_out=$OUTPUT_DIR $PROTO_DIR/api.proto" > gen/api.pb.go

        build_svc_a:
          stage: build
          needs: [generate_protos]
          variables:
            SERVICE: svc_a
          script:
            - echo "go build ./services/$SERVICE/..." > build_svc_a.log

        build_svc_b:
          stage: build
          needs: [generate_protos]
          variables:
            SERVICE: svc_b
          script:
            - echo "go build ./services/$SERVICE/..." > build_svc_b.log

        build_svc_c:
          stage: build
          needs: [generate_protos]
          variables:
            SERVICE: svc_c
          script:
            - echo "go build ./services/$SERVICE/..." > build_svc_c.log

        integration_test:
          stage: integration
          needs: [build_svc_a, build_svc_b, build_svc_c]
          script:
            - echo "go test ./integration/..." > integration.log
        """

    def test_monorepo_pipeline_completes(self, tmp_path):
        write_ci(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        for svc in ["svc_a", "svc_b", "svc_c"]:
            assert (tmp_path / f"build_{svc}.log").exists()
        assert (tmp_path / "integration.log").exists()

    def test_monorepo_global_variables_available(self, tmp_path):
        write_ci(tmp_path, self.CI)
        loader = ConfigurationLoader(base_path=tmp_path)
        raw = loader.load_config(tmp_path / ".gitlab-ci.yml")
        pipeline = PipelineProcessor().process_config(raw)
        gen_job = next(j for j in pipeline.jobs if j.name == "generate_protos")
        assert gen_job.variables.get("PROTO_DIR") == "proto"
        assert gen_job.variables.get("OUTPUT_DIR") == "gen"

    def test_per_service_variables(self, tmp_path):
        write_ci(tmp_path, self.CI)
        loader = ConfigurationLoader(base_path=tmp_path)
        raw = loader.load_config(tmp_path / ".gitlab-ci.yml")
        pipeline = PipelineProcessor().process_config(raw)
        svc_a = next(j for j in pipeline.jobs if j.name == "build_svc_a")
        assert svc_a.variables["SERVICE"] == "svc_a"

    def test_run_single_service_filter(self, tmp_path):
        write_ci(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(
            maximum_degree_of_parallelism=1,
            job_filter=["generate_protos", "build_svc_b"],
        )
        assert (tmp_path / "gen" / "api.pb.go").exists()
        assert (tmp_path / "build_svc_b.log").exists()
        assert not (tmp_path / "build_svc_a.log").exists()
        assert not (tmp_path / "integration.log").exists()

    def test_dag_needs_respected_in_monorepo(self, tmp_path):
        write_ci(tmp_path, self.CI)
        loader = ConfigurationLoader(base_path=tmp_path)
        raw = loader.load_config(tmp_path / ".gitlab-ci.yml")
        pipeline = PipelineProcessor().process_config(raw)
        svc_a = next(j for j in pipeline.jobs if j.name == "build_svc_a")
        integration = next(j for j in pipeline.jobs if j.name == "integration_test")
        assert "generate_protos" in svc_a.needs
        assert "build_svc_a" in integration.needs


# ===========================================================================
# SCENARIO: JavaScript/Frontend Pipeline
# install → [build, lint, typecheck] → [unit_test, e2e_test] → bundle → deploy
# Uses: multiple parallel stages, e2e allow_failure, deploy: manual
# ===========================================================================


class TestJavaScriptPipeline:
    CI = """\
        stages:
          - install
          - check
          - test
          - bundle
          - deploy

        variables:
          NODE_ENV: "test"
          BUNDLE_DIR: "dist"

        install:
          stage: install
          script:
            - echo "npm ci" > npm_ci.log
            - mkdir -p node_modules
            - echo "installed" > node_modules/.installed

        build:
          stage: check
          needs: [install]
          script:
            - echo "npm run build" > build.log
            - mkdir -p dist
            - echo "bundle" > dist/main.js

        eslint:
          stage: check
          needs: [install]
          allow_failure: true
          script:
            - echo "npx eslint src/" > eslint.log

        typescript_check:
          stage: check
          needs: [install]
          script:
            - echo "tsc --noEmit" > tsc.log

        unit_tests:
          stage: test
          needs: [build, typescript_check]
          script:
            - echo "jest --coverage" > jest.log

        e2e_tests:
          stage: test
          needs: [build]
          allow_failure: true
          script:
            - echo "playwright test" > e2e.log

        bundle:
          stage: bundle
          needs: [unit_tests]
          script:
            - echo "webpack --mode production" > webpack.log
            - echo "bundled" > dist/bundle.min.js
          artifacts:
            paths:
              - dist/bundle.min.js
            when: on_success

        deploy_staging:
          stage: deploy
          needs: [bundle]
          when: manual
          script:
            - echo "deploying to staging" > deploy_staging.log

        cleanup_on_fail:
          stage: deploy
          when: on_failure
          script:
            - echo "cleaning up" > cleanup.log
        """

    def test_js_pipeline_completes(self, tmp_path):
        write_ci(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "npm_ci.log").exists()
        assert (tmp_path / "build.log").exists()
        assert (tmp_path / "eslint.log").exists()
        assert (tmp_path / "tsc.log").exists()
        assert (tmp_path / "jest.log").exists()
        assert (tmp_path / "webpack.log").exists()

    def test_manual_deploy_not_triggered(self, tmp_path):
        write_ci(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert not (tmp_path / "deploy_staging.log").exists()

    def test_cleanup_not_triggered_on_success(self, tmp_path):
        write_ci(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert not (tmp_path / "cleanup.log").exists()

    def test_e2e_allow_failure_does_not_block(self, tmp_path):
        """Pipeline completes without raising; bundle runs with when:always."""
        ci = """\
            stages: [test, bundle]
            e2e_tests:
              stage: test
              allow_failure: true
              script:
                - exit 1
            bundle:
              stage: bundle
              when: always
              script:
                - echo "bundled" > dist_bundle.log
            """
        write_ci(tmp_path, ci)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        assert (tmp_path / "dist_bundle.log").exists()

    def test_node_env_variable_in_jobs(self, tmp_path):
        write_ci(tmp_path, self.CI)
        loader = ConfigurationLoader(base_path=tmp_path)
        raw = loader.load_config(tmp_path / ".gitlab-ci.yml")
        pipeline = PipelineProcessor().process_config(raw)
        jest_job = next(j for j in pipeline.jobs if j.name == "unit_tests")
        assert jest_job.variables.get("NODE_ENV") == "test"

    def test_dag_structure_in_js_pipeline(self, tmp_path):
        write_ci(tmp_path, self.CI)
        loader = ConfigurationLoader(base_path=tmp_path)
        raw = loader.load_config(tmp_path / ".gitlab-ci.yml")
        pipeline = PipelineProcessor().process_config(raw)
        assert has_dag_jobs(pipeline)
        unit_tests = next(j for j in pipeline.jobs if j.name == "unit_tests")
        assert set(unit_tests.needs) == {"build", "typescript_check"}

    def test_filter_to_check_stage_only(self, tmp_path):
        write_ci(tmp_path, self.CI)
        runner(tmp_path).run_pipeline(
            maximum_degree_of_parallelism=1,
            stage_filter=["install", "check"],
        )
        assert (tmp_path / "build.log").exists()
        assert (tmp_path / "tsc.log").exists()
        assert not (tmp_path / "jest.log").exists()

    def test_bundle_artifact_collected(self, tmp_path):
        ci = """\
            stages: [build, bundle]
            build:
              stage: build
              script:
                - mkdir -p dist
                - echo "bundle content" > dist/main.js

            bundle:
              stage: bundle
              script:
                - echo "minified" > dist/bundle.min.js
              artifacts:
                paths:
                  - dist/bundle.min.js
                when: on_success
            """
        write_ci(tmp_path, ci)
        runner(tmp_path).run_pipeline(maximum_degree_of_parallelism=1)
        dest = artifact_dir(tmp_path, "bundle")
        assert (dest / "dist" / "bundle.min.js").exists()


# ===========================================================================
# SCENARIO: Multi-include config (realistic monorepo with shared templates)
# ===========================================================================


class TestMultiIncludeConfig:
    def test_two_level_include(self, tmp_path):
        """Main → shared.yml → variables.yml."""
        variables_yml = tmp_path / "variables.yml"
        variables_yml.write_text("variables:\n  REGISTRY: registry.example.com\n")

        shared_yml = tmp_path / "shared.yml"
        shared_yml.write_text(
            "include:\n  - local: variables.yml\n" "shared_test:\n  stage: test\n  script:\n    - echo shared\n"
        )

        write_ci(
            tmp_path,
            """
            include:
              - local: shared.yml
            stages: [test]
            local_job:
              stage: test
              script:
                - echo local
            """,
        )
        loader = ConfigurationLoader(base_path=tmp_path)
        raw = loader.load_config(tmp_path / ".gitlab-ci.yml")
        assert "shared_test" in raw
        assert "local_job" in raw
        assert raw["variables"]["REGISTRY"] == "registry.example.com"

    def test_include_does_not_leave_include_key(self, tmp_path):
        shared = tmp_path / "shared.yml"
        shared.write_text("helper_job:\n  script: [echo hi]\n")
        write_ci(
            tmp_path,
            """
            include:
              - local: shared.yml
            stages: [test]
            """,
        )
        loader = ConfigurationLoader(base_path=tmp_path)
        raw = loader.load_config(tmp_path / ".gitlab-ci.yml")
        assert "include" not in raw


# ===========================================================================
# filter_pipeline — edge cases not covered elsewhere
# ===========================================================================


class TestFilterPipelineEdgeCases:
    def pipeline(self):
        return proc(
            {
                "stages": ["build", "test", "deploy"],
                "build": {"stage": "build", "script": ["make"]},
                "unit": {"stage": "test", "script": ["pytest"]},
                "lint": {"stage": "test", "script": ["ruff"]},
                "deploy_stg": {"stage": "deploy", "script": ["./deploy stg"]},
                "deploy_prod": {"stage": "deploy", "script": ["./deploy prod"]},
            }
        )

    def test_both_filters_applied_together(self):
        p = self.pipeline()
        result = filter_pipeline(p, jobs=["unit", "lint", "deploy_prod"], stages=["test"])
        assert {j.name for j in result.jobs} == {"unit", "lint"}
        assert result.stages == ["test"]

    def test_unknown_job_silently_ignored(self):
        p = self.pipeline()
        result = filter_pipeline(p, jobs=["build", "no_such_job"])
        assert {j.name for j in result.jobs} == {"build"}

    def test_variables_preserved_after_filter(self):
        raw = {
            "stages": ["test"],
            "variables": {"TOKEN": "secret"},
            "job": {"stage": "test", "script": ["echo hi"]},
        }
        p = proc(raw)
        result = filter_pipeline(p, jobs=["job"])
        assert result.variables["TOKEN"] == "secret"

    def test_empty_stages_filter_returns_empty(self):
        p = self.pipeline()
        result = filter_pipeline(p, stages=[])
        assert result.jobs == []
        assert result.stages == []

    def test_filter_preserves_stage_order(self):
        p = self.pipeline()
        # Request in reverse order — output must follow original pipeline order
        result = filter_pipeline(p, stages=["deploy", "build"])
        assert result.stages == ["build", "deploy"]
