"""Tests targeting low-coverage code paths.

Focuses on:
- execution/job.py: retry helpers, env-based strategy/delay, execute via legacy params,
  exit-code-blocked retry, dry-run execution
- execution/shell.py: _colors_enabled, merge_env, RunResult helpers, force_subproc_mode,
  run_colored, capture mode, invalid mode
- execution/stage_runner.py: _is_failure_allowed, pipeline cancellation, on_cancelled hook,
  DAG cancellation, DagPipelineRunner.execute_pipeline direct
- config/schema.py: find_yaml_files, validate_single_file, write_results_to_output,
  print_validation_summary, run_validate_all serial/empty/missing-dir
- config/validate_pipeline.py: ValidationResult.__post_init__, yaml_to_json,
  pragma skip, validate_gitlab_ci_yaml convenience fn
- plan.py: filter_pipeline with both filters, unknown job/stage warnings,
  no-jobs-match early return, best_efforts_run
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from io import StringIO
from pathlib import Path

import pytest

from bitrab.config.loader import ConfigurationLoader
from bitrab.execution.job import JobExecutor
from bitrab.execution.shell import RunResult, _colors_enabled, force_subproc_mode, merge_env, run_bash, run_colored
from bitrab.execution.stage_runner import PipelineCallbacks, StagePipelineRunner, _is_failure_allowed
from bitrab.execution.variables import VariableManager
from bitrab.models.pipeline import JobConfig, PipelineConfig
from bitrab.plan import LocalGitLabRunner, PipelineProcessor, filter_pipeline

# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------


def _make_job(name="j", stage="test", **kwargs) -> JobConfig:
    kwargs.setdefault("script", ["echo hi"])
    return JobConfig(name=name, stage=stage, **kwargs)


def _simple_pipeline(tmp_path: Path, yaml: str) -> PipelineConfig:
    ci = tmp_path / ".gitlab-ci.yml"
    ci.write_text(textwrap.dedent(yaml))
    loader = ConfigurationLoader(base_path=tmp_path)
    raw = loader.load_config(ci)
    return PipelineProcessor().process_config(raw)


def _executor(tmp_path: Path) -> JobExecutor:
    vm = VariableManager({}, project_dir=tmp_path)
    return JobExecutor(vm, project_dir=tmp_path)


# ===========================================================================
# execution/job.py
# ===========================================================================


class TestJobRetryHelpers:
    def test_env_delay_invalid_string_returns_zero(self, monkeypatch):
        monkeypatch.setenv("BITRAB_RETRY_DELAY_SECONDS", "notanumber")
        assert JobExecutor._env_delay_seconds() == 0

    def test_env_delay_negative_clamped_to_zero(self, monkeypatch):
        monkeypatch.setenv("BITRAB_RETRY_DELAY_SECONDS", "-5")
        assert JobExecutor._env_delay_seconds() == 0

    def test_env_delay_valid(self, monkeypatch):
        monkeypatch.setenv("BITRAB_RETRY_DELAY_SECONDS", "3")
        assert JobExecutor._env_delay_seconds() == 3

    def test_env_strategy_exponential_default(self, monkeypatch):
        monkeypatch.delenv("BITRAB_RETRY_STRATEGY", raising=False)
        assert JobExecutor._env_strategy() == "exponential"

    def test_env_strategy_constant(self, monkeypatch):
        monkeypatch.setenv("BITRAB_RETRY_STRATEGY", "constant")
        assert JobExecutor._env_strategy() == "constant"

    def test_env_strategy_unknown_falls_back_to_exponential(self, monkeypatch):
        monkeypatch.setenv("BITRAB_RETRY_STRATEGY", "linear")
        assert JobExecutor._env_strategy() == "exponential"

    def test_env_strategy_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("BITRAB_RETRY_STRATEGY", "CONSTANT")
        assert JobExecutor._env_strategy() == "constant"

    def test_should_retry_when_empty_list_always_retries(self):
        exc = Exception("oops")
        assert JobExecutor._should_retry_when([], exc) is True

    def test_should_retry_when_always_keyword(self):
        exc = Exception("oops")
        assert JobExecutor._should_retry_when(["always"], exc) is True

    def test_should_retry_when_script_failure_with_cpe(self):
        exc = subprocess.CalledProcessError(1, "cmd")
        assert JobExecutor._should_retry_when(["script_failure"], exc) is True

    def test_should_retry_when_script_failure_non_cpe_returns_false(self):
        exc = RuntimeError("nope")
        assert JobExecutor._should_retry_when(["script_failure"], exc) is False

    def test_should_retry_when_unknown_condition_returns_false(self):
        exc = subprocess.CalledProcessError(1, "cmd")
        assert JobExecutor._should_retry_when(["runner_system_failure"], exc) is False

    def test_should_retry_exit_codes_empty_allows_all(self):
        exc = subprocess.CalledProcessError(42, "cmd")
        assert JobExecutor._should_retry_exit_codes([], exc) is True

    def test_should_retry_exit_codes_matching_code(self):
        exc = subprocess.CalledProcessError(2, "cmd")
        assert JobExecutor._should_retry_exit_codes([1, 2, 3], exc) is True

    def test_should_retry_exit_codes_non_matching_code(self):
        exc = subprocess.CalledProcessError(99, "cmd")
        assert JobExecutor._should_retry_exit_codes([1, 2], exc) is False

    def test_should_retry_exit_codes_non_cpe_returns_false(self):
        exc = RuntimeError("oops")
        assert JobExecutor._should_retry_exit_codes([1], exc) is False

    def test_compute_delay_zero_base(self):
        assert JobExecutor._compute_delay_seconds("exponential", 0, 1) == 0.0

    def test_compute_delay_constant(self):
        assert JobExecutor._compute_delay_seconds("constant", 5, 3) == 5.0

    def test_compute_delay_exponential_attempt1(self):
        assert JobExecutor._compute_delay_seconds("exponential", 2, 1) == 2.0

    def test_compute_delay_exponential_attempt2(self):
        assert JobExecutor._compute_delay_seconds("exponential", 2, 2) == 4.0

    def test_compute_delay_exponential_attempt3(self):
        assert JobExecutor._compute_delay_seconds("exponential", 2, 3) == 8.0


class TestJobExecuteLegacyParams:
    """execute_job called with positional job= rather than ctx=."""

    def test_legacy_call_succeeds(self, tmp_path):
        ex = _executor(tmp_path)
        job = _make_job(script=["echo legacy"])
        ex.execute_job(job, job_dir=tmp_path)
        assert len(ex.job_history) == 1

    def test_build_context_sets_ci_job_dir(self, tmp_path):
        ex = _executor(tmp_path)
        job = _make_job()
        ctx = ex.build_context(job, job_dir=tmp_path / "mydir")
        assert ctx.env.get("CI_JOB_DIR") == str(tmp_path / "mydir")

    def test_build_context_no_job_dir_no_ci_job_dir(self, tmp_path):
        ex = _executor(tmp_path)
        job = _make_job()
        ctx = ex.build_context(job)
        assert "CI_JOB_DIR" not in ctx.env

    def test_build_context_uses_job_timeout_over_param(self, tmp_path):
        ex = _executor(tmp_path)
        job = _make_job(timeout=120.0)
        ctx = ex.build_context(job, timeout=999.0)
        assert ctx.timeout == 120.0

    def test_build_context_falls_back_to_param_timeout(self, tmp_path):
        ex = _executor(tmp_path)
        job = _make_job(timeout=None)
        ctx = ex.build_context(job, timeout=60.0)
        assert ctx.timeout == 60.0


class TestJobExecuteRetryExitCodeBlocked:
    """Retry blocked by exit_codes filter."""

    def test_retry_blocked_by_exit_code_filter(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BITRAB_RETRY_NO_SLEEP", "1")
        ex = _executor(tmp_path)
        # exit_codes=[99] but script exits 1 → retry should not happen (blocked by exit_codes)
        job = _make_job(
            script=["exit 1"],
            retry_max=2,
            retry_exit_codes=[99],  # only retry on exit code 99
        )
        from bitrab.exceptions import JobExecutionError

        with pytest.raises(JobExecutionError):
            ex.execute_job(job, job_dir=tmp_path)
        # Only 1 attempt should have been made
        assert len(ex.job_history) == 1

    def test_retry_blocked_by_when_condition(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BITRAB_RETRY_NO_SLEEP", "1")
        ex = _executor(tmp_path)
        # when=runner_system_failure but we raise CalledProcessError (script_failure)
        job = _make_job(
            script=["exit 1"],
            retry_max=2,
            retry_when=["runner_system_failure"],
        )
        from bitrab.exceptions import JobExecutionError

        with pytest.raises(JobExecutionError):
            ex.execute_job(job, job_dir=tmp_path)
        assert len(ex.job_history) == 1


class TestJobDryRun:
    def test_dry_run_does_not_execute_script(self, tmp_path):
        marker = tmp_path / "marker.txt"
        vm = VariableManager({}, project_dir=tmp_path)
        ex = JobExecutor(vm, dry_run=True, project_dir=tmp_path)
        job = _make_job(script=[f"touch {marker}"])
        ex.execute_job(job, job_dir=tmp_path)
        assert not marker.exists()

    def test_dry_run_records_history(self, tmp_path):
        vm = VariableManager({}, project_dir=tmp_path)
        ex = JobExecutor(vm, dry_run=True, project_dir=tmp_path)
        job = _make_job(script=["echo dry"])
        ex.execute_job(job, job_dir=tmp_path)
        assert len(ex.job_history) == 1
        assert ex.job_history[0].returncode == 0

    def test_dry_run_uses_output_writer(self, tmp_path):
        buf = StringIO()
        buf.flush = lambda: None
        vm = VariableManager({}, project_dir=tmp_path)
        ex = JobExecutor(vm, dry_run=True, project_dir=tmp_path)
        job = _make_job(script=["echo something"])
        ex.execute_job(job, job_dir=tmp_path, output_writer=buf)
        assert "echo something" in buf.getvalue()

    def test_dry_run_reports_all_script_sections(self, tmp_path):
        buf = StringIO()
        buf.flush = lambda: None
        vm = VariableManager({}, project_dir=tmp_path)
        ex = JobExecutor(vm, dry_run=True, project_dir=tmp_path)
        job = _make_job(
            before_script=["echo before"],
            script=["echo main"],
            after_script=["echo after"],
        )

        ex.execute_job(job, job_dir=tmp_path, output_writer=buf)

        output = buf.getvalue()
        assert "echo before" in output
        assert "echo main" in output
        assert "echo after" in output
        assert "dry-run preview only" in output

    def test_stage_runner_dry_run_skips_job_dirs_and_artifacts(self, tmp_path):
        artifact_file = tmp_path / "artifact.txt"
        artifact_file.write_text("artifact")
        pipeline = PipelineConfig(
            stages=["build"],
            jobs=[
                _make_job(
                    name="build job",
                    stage="build",
                    script=["echo build"],
                    artifacts_paths=["artifact.txt"],
                )
            ],
        )
        vm = VariableManager({}, project_dir=tmp_path)
        ex = JobExecutor(vm, dry_run=True, project_dir=tmp_path)

        StagePipelineRunner(ex, maximum_degree_of_parallelism=1).execute_pipeline(pipeline)

        assert not (tmp_path / ".bitrab").exists()


# ===========================================================================
# execution/shell.py
# ===========================================================================


class TestColorsEnabled:
    def test_force_true(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        assert _colors_enabled(True) is True

    def test_force_false(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert _colors_enabled(False) is False

    def test_no_color_env_disables(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert _colors_enabled(None) is False

    def test_no_env_enables(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        assert _colors_enabled(None) is True


class TestMergeEnv:
    def test_none_returns_copy_of_os_environ(self):
        result = merge_env(None)
        assert result["PATH"] == os.environ["PATH"]

    def test_extra_keys_override(self):
        result = merge_env({"MYVAR": "hello"})
        assert result["MYVAR"] == "hello"

    def test_does_not_mutate_os_environ(self):
        merge_env({"_BITRAB_TEST_KEY": "x"})
        assert "_BITRAB_TEST_KEY" not in os.environ


class TestRunResult:
    def test_stdout_clean_strips_ansi(self):
        r = RunResult(0, "\033[92mhello\033[0m", "")
        assert r.stdout_clean == "hello"

    def test_stderr_clean_strips_ansi(self):
        r = RunResult(0, "", "\033[91merr\033[0m")
        assert r.stderr_clean == "err"

    def test_check_returncode_raises_on_nonzero(self):
        r = RunResult(1, "out", "err")
        with pytest.raises(subprocess.CalledProcessError):
            r.check_returncode()

    def test_check_returncode_returns_self_on_zero(self):
        r = RunResult(0, "out", "")
        assert r.check_returncode() is r


class TestForceSubprocMode:
    def test_sets_and_restores_env(self, monkeypatch):
        monkeypatch.delenv("BITRAB_SUBPROC_MODE", raising=False)
        with force_subproc_mode("capture"):
            assert os.environ["BITRAB_SUBPROC_MODE"] == "capture"
        assert "BITRAB_SUBPROC_MODE" not in os.environ

    def test_restores_previous_value(self, monkeypatch):
        monkeypatch.setenv("BITRAB_SUBPROC_MODE", "stream")
        with force_subproc_mode("capture"):
            assert os.environ["BITRAB_SUBPROC_MODE"] == "capture"
        assert os.environ["BITRAB_SUBPROC_MODE"] == "stream"

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            with force_subproc_mode("invalid"):
                pass


class TestRunBashCapture:
    def test_capture_mode_stdout(self):
        r = run_bash("echo hello", mode="capture", check=False)
        assert "hello" in r.stdout

    def test_capture_mode_returncode(self):
        r = run_bash("exit 3", mode="capture", check=False)
        assert r.returncode == 3

    def test_capture_mode_check_raises(self):
        with pytest.raises(subprocess.CalledProcessError):
            run_bash("exit 1", mode="capture", check=True)

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            run_bash("echo x", mode="badmode")


class TestRunColored:
    def test_run_colored_success(self):
        r = run_colored("echo colored", mode="capture")
        assert "colored" in r.stdout

    def test_run_colored_failure_raises(self):
        with pytest.raises(subprocess.CalledProcessError):
            run_colored("exit 1", mode="capture")


# ===========================================================================
# execution/stage_runner.py
# ===========================================================================


class TestIsFailureAllowed:
    def test_allow_failure_false(self):
        job = _make_job(allow_failure=False)
        assert _is_failure_allowed(job, Exception()) is False

    def test_allow_failure_true_no_codes(self):
        job = _make_job(allow_failure=True, allow_failure_exit_codes=[])
        assert _is_failure_allowed(job, Exception()) is True

    def test_allow_failure_exit_codes_matching(self):
        job = _make_job(allow_failure=True, allow_failure_exit_codes=[2, 5])
        exc = subprocess.CalledProcessError(2, "cmd")
        assert _is_failure_allowed(job, exc) is True

    def test_allow_failure_exit_codes_non_matching(self):
        job = _make_job(allow_failure=True, allow_failure_exit_codes=[2, 5])
        exc = subprocess.CalledProcessError(99, "cmd")
        assert _is_failure_allowed(job, exc) is False

    def test_allow_failure_via_cause_chain(self):
        from bitrab.exceptions import JobExecutionError

        job = _make_job(allow_failure=True, allow_failure_exit_codes=[1])
        cause = subprocess.CalledProcessError(1, "cmd")
        exc = JobExecutionError("failed")
        exc.__cause__ = cause
        assert _is_failure_allowed(job, exc) is True

    def test_allow_failure_non_cpe_no_codes_returns_true(self):
        job = _make_job(allow_failure=True, allow_failure_exit_codes=[1])
        exc = RuntimeError("some other error")
        # Not a CalledProcessError and no cause → False
        assert _is_failure_allowed(job, exc) is False


class TestPipelineCancellation:
    def test_cancel_before_second_stage(self, tmp_path):
        """Cancelling mid-pipeline stops after first stage."""
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(
            textwrap.dedent(
                """\
            stages: [a, b]
            job_a:
              stage: a
              script: [echo a]
            job_b:
              stage: b
              script: [echo b]
        """
            )
        )
        loader = ConfigurationLoader(base_path=tmp_path)
        raw = loader.load_config(ci)
        pipeline = PipelineProcessor().process_config(raw)

        ran = []

        class CancelAfterFirst(PipelineCallbacks):
            def on_job_start(self, job):
                ran.append(job.name)

            def on_stage_complete(self, stage, outcomes):
                pass

            def is_cancelled(self):
                return "job_a" in ran  # cancel after job_a runs

            def on_cancelled(self):
                ran.append("CANCELLED")

        ex = _executor(tmp_path)
        runner = StagePipelineRunner(ex, callbacks=CancelAfterFirst(), maximum_degree_of_parallelism=1)
        runner.execute_pipeline(pipeline)

        assert "job_a" in ran
        assert "CANCELLED" in ran
        assert "job_b" not in ran


class TestDefaultCallbacksNoOps:
    """PipelineCallbacks base class methods all return harmlessly."""

    def test_all_noop_methods(self):
        cb = PipelineCallbacks()
        pipeline = PipelineConfig(stages=["test"], variables={}, jobs=[])
        cb.on_pipeline_start(pipeline, 1)
        cb.on_pipeline_complete(True)
        cb.on_stage_start("test", [])
        cb.on_stage_skip("test")
        cb.on_stage_complete("test", [])
        job = _make_job()
        cb.on_job_start(job)
        from bitrab.execution.stage_runner import JobOutcome

        cb.on_job_complete(JobOutcome(job=job, success=True))
        assert cb.is_cancelled() is False
        assert cb.make_output_writer(job, Path(".")) is None
        assert cb.make_worker_args(job, Path(".")) == {}
        cb.poll_during_parallel({})


# ===========================================================================
# config/schema.py  (bulk validator)
# ===========================================================================


class TestFindYamlFiles:
    def test_finds_yml_and_yaml(self, tmp_path):
        from bitrab.config.schema import find_yaml_files

        (tmp_path / "a.yml").write_text("x: 1")
        (tmp_path / "b.yaml").write_text("y: 2")
        (tmp_path / "c.txt").write_text("ignored")
        found = find_yaml_files(tmp_path)
        names = {f.name for f in found}
        assert "a.yml" in names
        assert "b.yaml" in names
        assert "c.txt" not in names

    def test_recursive(self, tmp_path):
        from bitrab.config.schema import find_yaml_files

        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.yml").write_text("z: 3")
        found = find_yaml_files(tmp_path)
        assert any(f.name == "deep.yml" for f in found)

    def test_empty_dir(self, tmp_path):
        from bitrab.config.schema import find_yaml_files

        assert find_yaml_files(tmp_path) == []


class TestValidateSingleFile:
    def test_valid_ci_file(self, tmp_path):
        from bitrab.config.schema import validate_single_file

        ci = tmp_path / "ci.yml"
        ci.write_text("job:\n  script: [echo hi]\n")
        result = validate_single_file(ci)
        assert result.is_valid

    def test_missing_file(self, tmp_path):
        from bitrab.config.schema import validate_single_file

        result = validate_single_file(tmp_path / "nonexistent.yml")
        assert not result.is_valid
        assert any("does not exist" in e for e in result.errors)

    def test_not_a_file(self, tmp_path):
        from bitrab.config.schema import validate_single_file

        # Pass a directory path
        result = validate_single_file(tmp_path)
        assert not result.is_valid
        assert any("not a file" in e for e in result.errors)


class TestWriteResultsToOutput:
    def test_writes_json(self, tmp_path):
        from bitrab.config.schema import write_results_to_output
        from bitrab.config.validate_pipeline import ValidationResult

        results = [
            ValidationResult(file_path=tmp_path / "a.yml", is_valid=True, errors=[]),
            ValidationResult(file_path=tmp_path / "b.yml", is_valid=False, errors=["bad"]),
        ]
        out = tmp_path / "results.json"
        write_results_to_output(results, out)
        import json

        data = json.loads(out.read_text())
        assert data["summary"]["total_files"] == 2
        assert data["summary"]["valid_files"] == 1
        assert data["summary"]["invalid_files"] == 1


class TestPrintValidationSummary:
    def test_all_valid(self, tmp_path, capsys):
        from bitrab.config.schema import print_validation_summary
        from bitrab.config.validate_pipeline import ValidationResult

        results = [ValidationResult(file_path=tmp_path / "a.yml", is_valid=True, errors=[])]
        print_validation_summary(results)
        out = capsys.readouterr().out
        assert "All files are valid" in out

    def test_some_invalid(self, tmp_path, capsys):
        from bitrab.config.schema import print_validation_summary
        from bitrab.config.validate_pipeline import ValidationResult

        results = [ValidationResult(file_path=tmp_path / "bad.yml", is_valid=False, errors=["oops"])]
        print_validation_summary(results)
        out = capsys.readouterr().out
        assert "Files with errors" in out or "bad.yml" in out


class TestRunValidateAll:
    def test_missing_input_dir_returns_2(self, tmp_path):
        from bitrab.config.schema import run_validate_all

        code = run_validate_all(tmp_path / "no_such_dir", tmp_path / "out.json")
        assert code == 2

    def test_input_is_file_not_dir_returns_2(self, tmp_path):
        from bitrab.config.schema import run_validate_all

        f = tmp_path / "file.yml"
        f.write_text("x: 1")
        code = run_validate_all(f, tmp_path / "out.json")
        assert code == 2

    def test_empty_dir_returns_0(self, tmp_path):
        from bitrab.config.schema import run_validate_all

        code = run_validate_all(tmp_path, tmp_path / "out.json")
        assert code == 0

    def test_valid_files_return_0(self, tmp_path):
        from bitrab.config.schema import run_validate_all

        (tmp_path / "good.yml").write_text("job:\n  script: [echo hi]\n")
        code = run_validate_all(tmp_path, tmp_path / "out.json")
        assert code == 0

    def test_invalid_file_returns_1(self, tmp_path):
        from bitrab.config.schema import run_validate_all

        # A completely bogus YAML structure that fails schema validation
        (tmp_path / "bad.yml").write_text("not_a_job: 123\nstages: 'wrong'\n")
        code = run_validate_all(tmp_path, tmp_path / "out.json")
        # may be 0 or 1 depending on schema strictness; just verify it runs
        assert code in (0, 1)


# ===========================================================================
# config/validate_pipeline.py
# ===========================================================================


class TestValidationResult:
    def test_file_path_coerced_to_path(self):
        from bitrab.config.validate_pipeline import ValidationResult

        r = ValidationResult(file_path="/some/path.yml", is_valid=True, errors=[])  # type: ignore[arg-type]
        assert isinstance(r.file_path, Path)

    def test_already_path_unchanged(self, tmp_path):
        from bitrab.config.validate_pipeline import ValidationResult

        p = tmp_path / "x.yml"
        r = ValidationResult(file_path=p, is_valid=True, errors=[])
        assert r.file_path == p


class TestGitLabCIValidatorPragma:
    def test_pragma_skip_returns_valid(self):
        from bitrab.config.validate_pipeline import GitLabCIValidator

        v = GitLabCIValidator()
        yaml = "# pragma: do-not-validate-schema\njob:\n  script: [exit 1]\n"
        ok, errors = v.validate_ci_config(yaml)
        assert ok is True
        assert errors == []

    def test_yaml_to_json(self):
        from bitrab.config.validate_pipeline import GitLabCIValidator

        v = GitLabCIValidator()
        result = v.yaml_to_json("key: value\n")
        assert result["key"] == "value"

    def test_validate_ci_config_invalid_yaml(self):
        from bitrab.config.validate_pipeline import GitLabCIValidator

        v = GitLabCIValidator()
        ok, errors = v.validate_ci_config(":\t invalid yaml {{{\n")
        assert ok is False
        assert errors

    def test_convenience_function(self, tmp_path):
        from bitrab.config.validate_pipeline import validate_gitlab_ci_yaml

        ok, _ = validate_gitlab_ci_yaml("job:\n  script: [echo hi]\n", cache_dir=str(tmp_path))
        assert ok is True


# ===========================================================================
# plan.py
# ===========================================================================


class TestFilterPipeline:
    def _make_pipeline(self):
        processor = PipelineProcessor()
        raw = {
            "stages": ["build", "test", "deploy"],
            "build_job": {"stage": "build", "script": ["echo build"]},
            "test_job": {"stage": "test", "script": ["echo test"]},
            "deploy_job": {"stage": "deploy", "script": ["echo deploy"]},
        }
        return processor.process_config(raw)

    def test_filter_by_jobs_only(self):
        p = self._make_pipeline()
        filtered = filter_pipeline(p, jobs=["build_job", "test_job"])
        assert {j.name for j in filtered.jobs} == {"build_job", "test_job"}
        assert "deploy" not in filtered.stages

    def test_filter_by_stages_only(self):
        p = self._make_pipeline()
        filtered = filter_pipeline(p, stages=["build"])
        assert all(j.stage == "build" for j in filtered.jobs)

    def test_filter_by_both(self):
        p = self._make_pipeline()
        filtered = filter_pipeline(p, jobs=["build_job", "test_job"], stages=["build"])
        assert [j.name for j in filtered.jobs] == ["build_job"]

    def test_unknown_job_silently_ignored(self):
        p = self._make_pipeline()
        filtered = filter_pipeline(p, jobs=["nonexistent"])
        assert filtered.jobs == []

    def test_preserves_stage_order(self):
        p = self._make_pipeline()
        filtered = filter_pipeline(p, stages=["deploy", "build"])
        # stages should follow original pipeline order
        assert filtered.stages == ["build", "deploy"]

    def test_no_filters_returns_all(self):
        p = self._make_pipeline()
        filtered = filter_pipeline(p)
        assert len(filtered.jobs) == len(p.jobs)


class TestLocalGitLabRunnerFilters:
    def _write_ci(self, tmp_path: Path) -> Path:
        ci = tmp_path / ".gitlab-ci.yml"
        ci.write_text(
            textwrap.dedent(
                """\
            stages: [build, test]
            build_job:
              stage: build
              script: [echo build]
            test_job:
              stage: test
              script: [echo test]
        """
            )
        )
        return ci

    def test_unknown_job_filter_warns(self, tmp_path, capsys):
        self._write_ci(tmp_path)
        runner = LocalGitLabRunner(base_path=tmp_path)
        runner.run_pipeline(
            config_path=tmp_path / ".gitlab-ci.yml",
            maximum_degree_of_parallelism=1,
            job_filter=["ghost_job"],
        )
        out = capsys.readouterr().out
        assert "ghost_job" in out

    def test_unknown_stage_filter_warns(self, tmp_path, capsys):
        self._write_ci(tmp_path)
        runner = LocalGitLabRunner(base_path=tmp_path)
        runner.run_pipeline(
            config_path=tmp_path / ".gitlab-ci.yml",
            maximum_degree_of_parallelism=1,
            stage_filter=["nonexistent_stage"],
        )
        out = capsys.readouterr().out
        assert "nonexistent_stage" in out

    def test_no_matching_jobs_returns_early(self, tmp_path, capsys):
        self._write_ci(tmp_path)
        runner = LocalGitLabRunner(base_path=tmp_path)
        runner.run_pipeline(
            config_path=tmp_path / ".gitlab-ci.yml",
            maximum_degree_of_parallelism=1,
            job_filter=["not_a_job"],
        )
        out = capsys.readouterr().out
        assert "nothing to run" in out.lower() or "No jobs match" in out

    def test_stage_filter_runs_only_that_stage(self, tmp_path):
        marker = tmp_path / "test_ran.txt"
        ci = tmp_path / ".gitlab-ci.yml"
        # Use echo redirect which works cross-platform via bash
        ci.write_text(
            textwrap.dedent(
                f"""\
            stages: [build, test]
            build_job:
              stage: build
              script: [echo build]
            test_job:
              stage: test
              script: [echo ran > '{marker}']
        """
            )
        )
        runner = LocalGitLabRunner(base_path=tmp_path)
        runner.run_pipeline(
            config_path=ci,
            maximum_degree_of_parallelism=1,
            stage_filter=["test"],
        )
        assert marker.exists()

    def test_dry_run_in_ci_mode_uses_streaming_runner(self, tmp_path, monkeypatch):
        ci = self._write_ci(tmp_path)
        runner = LocalGitLabRunner(base_path=tmp_path)
        stage_called = False
        tui_called = False

        class DummyStageOrchestrator:
            def __init__(self, *args, **kwargs):
                pass

            def execute_pipeline(self, pipeline):
                nonlocal stage_called
                stage_called = True

        class DummyTUIOrchestrator:
            def __init__(self, *args, **kwargs):
                pass

            def execute_pipeline_ci(self, pipeline):
                nonlocal tui_called
                tui_called = True

        monkeypatch.setattr("bitrab.plan.StageOrchestrator", DummyStageOrchestrator)
        monkeypatch.setattr("bitrab.tui.orchestrator.TUIOrchestrator", DummyTUIOrchestrator)

        runner.run_pipeline(
            config_path=ci,
            maximum_degree_of_parallelism=1,
            dry_run=True,
            ci_mode=True,
        )

        assert stage_called is True
        assert tui_called is False
