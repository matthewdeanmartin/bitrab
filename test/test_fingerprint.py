"""Tests for Sprint 02: job fingerprint memoization (``--incremental``)."""

from __future__ import annotations

import json
import subprocess  # nosec
import threading
from pathlib import Path

import pytest

from bitrab.exceptions import JobExecutionError
from bitrab.execution.fingerprint import (
    NO_GIT_MARKER,
    FingerprintManager,
    fingerprint_root,
    git_tree_digest,
    hash_listed_files,
    hash_path_globs,
    read_record,
    record_path,
    write_record,
)
from bitrab.models.pipeline import CacheConfig, JobConfig, PipelineConfig
from bitrab.plan import LocalGitLabRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_job(name: str = "myjob", **kwargs) -> JobConfig:
    kwargs.setdefault("stage", "test")
    kwargs.setdefault("script", ["echo hi"])
    return JobConfig(name=name, **kwargs)


def make_manager(
    tmp_path: Path, jobs: list[JobConfig], stages: list[str] | None = None, **kwargs
) -> FingerprintManager:
    manager = FingerprintManager(tmp_path, **kwargs)
    manager.prepare(PipelineConfig(stages=stages or ["test"], jobs=jobs))
    return manager


def run_lines(tmp_path: Path, name: str) -> int:
    """Return how many times a job's run-marker script appended a line."""
    marker = tmp_path / name
    if not marker.exists():
        return 0
    return len(marker.read_text().strip().splitlines())


def git(tmp_path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)  # nosec


def init_git_repo(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")


# ---------------------------------------------------------------------------
# Store: read / write / corruption / concurrency
# ---------------------------------------------------------------------------


class TestStore:
    def test_write_and_read_roundtrip(self, tmp_path):
        root = fingerprint_root(tmp_path)
        assert write_record(root, "job one", "abc123")
        record = read_record(root, "job one")
        assert record is not None
        assert record["fingerprint"] == "abc123"
        assert record["status"] == "success"
        assert record["bitrab"]
        assert record["completed_at"]

    def test_missing_record_is_none(self, tmp_path):
        assert read_record(fingerprint_root(tmp_path), "never-ran") is None

    def test_read_does_not_create_store(self, tmp_path):
        read_record(fingerprint_root(tmp_path), "never-ran")
        assert not fingerprint_root(tmp_path).exists()

    def test_corrupt_record_is_a_miss_and_rewritable(self, tmp_path):
        root = fingerprint_root(tmp_path)
        write_record(root, "j", "abc")
        record_path(root, "j").write_text("{not json", encoding="utf-8")
        assert read_record(root, "j") is None
        assert write_record(root, "j", "def")
        assert read_record(root, "j")["fingerprint"] == "def"

    def test_non_dict_json_is_a_miss(self, tmp_path):
        root = fingerprint_root(tmp_path)
        root.mkdir(parents=True)
        record_path(root, "j").write_text('["a list"]', encoding="utf-8")
        assert read_record(root, "j") is None

    def test_concurrent_writers_leave_valid_record(self, tmp_path):
        root = fingerprint_root(tmp_path)
        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def write(fp: str) -> None:
            try:
                barrier.wait(timeout=10)
                for _ in range(25):
                    assert write_record(root, "shared", fp)
            except BaseException as exc:  # noqa: BLE001 - collected for the assert below
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(fp,)) for fp in ("aaaa", "bbbb")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors
        data = json.loads(record_path(root, "shared").read_text(encoding="utf-8"))
        assert data["fingerprint"] in {"aaaa", "bbbb"}
        assert data["status"] == "success"


# ---------------------------------------------------------------------------
# Input-file digests
# ---------------------------------------------------------------------------


class TestFileDigests:
    def test_hash_path_globs_stable_and_content_sensitive(self, tmp_path):
        (tmp_path / "inputs").mkdir()
        (tmp_path / "inputs" / "a.txt").write_text("v1")
        d1 = hash_path_globs(tmp_path, ["inputs/*.txt"])
        assert d1 == hash_path_globs(tmp_path, ["inputs/*.txt"])
        (tmp_path / "inputs" / "a.txt").write_text("v2")
        assert d1 != hash_path_globs(tmp_path, ["inputs/*.txt"])

    def test_hash_path_globs_ignores_unmatched_files(self, tmp_path):
        (tmp_path / "inputs").mkdir()
        (tmp_path / "inputs" / "a.txt").write_text("v1")
        d1 = hash_path_globs(tmp_path, ["inputs/*.txt"])
        (tmp_path / "unrelated.md").write_text("noise")
        assert d1 == hash_path_globs(tmp_path, ["inputs/*.txt"])

    def test_hash_path_globs_directory_matches_recursively(self, tmp_path):
        (tmp_path / "src" / "pkg").mkdir(parents=True)
        (tmp_path / "src" / "pkg" / "m.py").write_text("x = 1")
        d1 = hash_path_globs(tmp_path, ["src"])
        (tmp_path / "src" / "pkg" / "m.py").write_text("x = 2")
        assert d1 != hash_path_globs(tmp_path, ["src"])

    def test_hash_listed_files_missing_is_deterministic(self, tmp_path):
        assert hash_listed_files(tmp_path, ["nope.lock"]) == hash_listed_files(tmp_path, ["nope.lock"])

    def test_git_digest_outside_repo_is_marker(self, tmp_path):
        assert git_tree_digest(tmp_path) == NO_GIT_MARKER

    def test_git_digest_tracks_dirty_state(self, tmp_path):
        init_git_repo(tmp_path)
        (tmp_path / "input.txt").write_text("v1")
        git(tmp_path, "add", "-A")
        d1 = git_tree_digest(tmp_path)
        assert d1 != NO_GIT_MARKER
        # Untracked files are invisible to the fallback.
        (tmp_path / "untracked.txt").write_text("noise")
        assert git_tree_digest(tmp_path) == d1
        # Dirty tracked file changes the digest without re-adding.
        (tmp_path / "input.txt").write_text("v2")
        assert git_tree_digest(tmp_path) != d1


# ---------------------------------------------------------------------------
# Fingerprint composition
# ---------------------------------------------------------------------------


class TestComposition:
    def test_same_inputs_same_fingerprint(self, tmp_path):
        job = make_job()
        m1 = make_manager(tmp_path, [job])
        m2 = make_manager(tmp_path, [job])
        assert m1.fingerprint_for(job.name) == m2.fingerprint_for(job.name)

    def test_script_change_invalidates(self, tmp_path):
        m1 = make_manager(tmp_path, [make_job(script=["echo a"])])
        m2 = make_manager(tmp_path, [make_job(script=["echo b"])])
        assert m1.fingerprint_for("myjob") != m2.fingerprint_for("myjob")

    def test_before_and_after_script_change_invalidates(self, tmp_path):
        base = make_manager(tmp_path, [make_job()])
        with_before = make_manager(tmp_path, [make_job(before_script=["setup"])])
        with_after = make_manager(tmp_path, [make_job(after_script=["teardown"])])
        fps = {m.fingerprint_for("myjob") for m in (base, with_before, with_after)}
        assert len(fps) == 3

    def test_variable_change_invalidates(self, tmp_path):
        m1 = make_manager(tmp_path, [make_job(variables={"FOO": "1"})])
        m2 = make_manager(tmp_path, [make_job(variables={"FOO": "2"})])
        assert m1.fingerprint_for("myjob") != m2.fingerprint_for("myjob")

    def test_upstream_change_transitively_invalidates_needs(self, tmp_path):
        def pipeline(a_script: str) -> list[JobConfig]:
            a = make_job("a", script=[a_script])
            b = make_job("b", needs=["a"])
            c = make_job("c", needs=["b"])
            return [a, b, c]

        m1 = make_manager(tmp_path, pipeline("echo v1"))
        m2 = make_manager(tmp_path, pipeline("echo v2"))
        assert m1.fingerprint_for("b") != m2.fingerprint_for("b")
        assert m1.fingerprint_for("c") != m2.fingerprint_for("c")

    def test_prior_stage_jobs_are_implicit_upstream(self, tmp_path):
        def pipeline(build_script: str) -> list[JobConfig]:
            build = make_job("build", stage="build", script=[build_script])
            test = make_job("test", stage="test")
            return [build, test]

        stages = ["build", "test"]
        m1 = make_manager(tmp_path, pipeline("echo v1"), stages=stages)
        m2 = make_manager(tmp_path, pipeline("echo v2"), stages=stages)
        assert m1.fingerprint_for("test") != m2.fingerprint_for("test")

    def test_explicit_empty_dependencies_are_isolated(self, tmp_path):
        def pipeline(build_script: str) -> list[JobConfig]:
            build = make_job("build", stage="build", script=[build_script])
            test = make_job("test", stage="test", dependencies=[])
            return [build, test]

        stages = ["build", "test"]
        m1 = make_manager(tmp_path, pipeline("echo v1"), stages=stages)
        m2 = make_manager(tmp_path, pipeline("echo v2"), stages=stages)
        assert m1.fingerprint_for("test") == m2.fingerprint_for("test")

    def test_fingerprint_paths_variable_overrides_git(self, tmp_path):
        (tmp_path / "inputs").mkdir()
        (tmp_path / "inputs" / "a.txt").write_text("v1")
        job = make_job(variables={"BITRAB_FINGERPRINT_PATHS": "inputs/*.txt"})
        m1 = make_manager(tmp_path, [job])
        fp1 = m1.fingerprint_for("myjob")
        (tmp_path / "inputs" / "a.txt").write_text("v2")
        m2 = make_manager(tmp_path, [job])
        assert m2.fingerprint_for("myjob") != fp1

    def test_cache_key_files_used_when_no_override(self, tmp_path):
        (tmp_path / "poetry.lock").write_text("v1")
        job = make_job(cache=[CacheConfig(paths=[".venv/"], key_files=["poetry.lock"])])
        fp1 = make_manager(tmp_path, [job]).fingerprint_for("myjob")
        (tmp_path / "poetry.lock").write_text("v2")
        assert make_manager(tmp_path, [job]).fingerprint_for("myjob") != fp1

    def test_fingerprint_env_salt(self, tmp_path, monkeypatch):
        (tmp_path / "pyproject.toml").write_text('[tool.bitrab]\nfingerprint_env = ["MY_TOOLCHAIN"]\n')
        job = make_job()
        monkeypatch.setenv("MY_TOOLCHAIN", "/opt/gcc-13")
        fp1 = make_manager(tmp_path, [job]).fingerprint_for("myjob")
        monkeypatch.setenv("MY_TOOLCHAIN", "/opt/gcc-14")
        fp2 = make_manager(tmp_path, [job]).fingerprint_for("myjob")
        assert fp1 != fp2

    def test_unknown_and_cyclic_needs_do_not_raise(self, tmp_path):
        a = make_job("a", needs=["b", "ghost"])
        b = make_job("b", needs=["a"])
        manager = make_manager(tmp_path, [a, b])
        assert manager.fingerprint_for("a")
        assert manager.fingerprint_for("b")


# ---------------------------------------------------------------------------
# Manager check / record semantics
# ---------------------------------------------------------------------------


class TestCheckAndRecord:
    def test_no_record_is_a_miss(self, tmp_path):
        job = make_job()
        manager = make_manager(tmp_path, [job])
        decision = manager.check(job)
        assert not decision.hit
        assert decision.reason == "no-record"

    def test_record_then_hit(self, tmp_path):
        job = make_job()
        manager = make_manager(tmp_path, [job])
        assert manager.record(job)
        assert manager.check(job).hit

    def test_refresh_reports_miss_but_records(self, tmp_path):
        job = make_job()
        manager = make_manager(tmp_path, [job], refresh=True)
        manager.record(job)
        assert manager.check(job).reason == "refresh"
        # A non-refresh manager sees the recorded success.
        assert make_manager(tmp_path, [job]).check(job).hit

    def test_missing_artifact_dir_is_a_miss(self, tmp_path):
        job = make_job(artifacts_paths=["out.txt"])
        manager = make_manager(tmp_path, [job])
        manager.record(job)
        assert manager.check(job).reason == "artifacts-missing"
        (tmp_path / ".bitrab" / "artifacts" / "myjob").mkdir(parents=True)
        assert manager.check(job).hit

    def test_missing_dotenv_store_is_a_miss(self, tmp_path):
        job = make_job(artifacts_dotenv="build.env")
        manager = make_manager(tmp_path, [job])
        manager.record(job)
        assert manager.check(job).reason == "dotenv-missing"

    def test_mutation_detection_blocks_record(self, tmp_path):
        from bitrab.execution.job import JobExecutor
        from bitrab.execution.stage_runner import StagePipelineRunner
        from bitrab.execution.variables import VariableManager

        job = make_job()
        manager = make_manager(tmp_path, [job])
        executor = JobExecutor(VariableManager({}, project_dir=tmp_path), project_dir=tmp_path)
        runner = StagePipelineRunner(job_executor=executor, fingerprints=manager)

        runner.record_fingerprint(job, succeeded=True, mutations=["src/rewritten.py"])
        assert read_record(manager.root, job.name) is None

        runner.record_fingerprint(job, succeeded=False)
        assert read_record(manager.root, job.name) is None

        runner.record_fingerprint(job, succeeded=True)
        assert read_record(manager.root, job.name) is not None


# ---------------------------------------------------------------------------
# E2E: LocalGitLabRunner with --incremental
# ---------------------------------------------------------------------------


INCREMENTAL_PIPELINE = """
stages:
  - build
  - test

build:
  stage: build
  script:
    - echo built > out.txt
    - echo run >> build_runs.txt
  artifacts:
    paths:
      - out.txt

test:
  stage: test
  script:
    - cat out.txt
    - echo run >> test_runs.txt
"""


def write_ci(tmp_path: Path, content: str = INCREMENTAL_PIPELINE) -> None:
    (tmp_path / ".gitlab-ci.yml").write_text(content)


def run(tmp_path: Path, **kwargs) -> None:
    kwargs.setdefault("maximum_degree_of_parallelism", 1)
    LocalGitLabRunner(tmp_path).run_pipeline(**kwargs)


def test_e2e_second_incremental_run_executes_zero_jobs(tmp_path):
    write_ci(tmp_path)
    run(tmp_path, incremental=True)
    assert run_lines(tmp_path, "build_runs.txt") == 1
    assert run_lines(tmp_path, "test_runs.txt") == 1

    run(tmp_path, incremental=True)
    assert run_lines(tmp_path, "build_runs.txt") == 1
    assert run_lines(tmp_path, "test_runs.txt") == 1


def test_e2e_memoized_upstream_still_injects_artifacts(tmp_path):
    write_ci(tmp_path)
    run(tmp_path, incremental=True)

    # Invalidate only the downstream job and delete its injected input file:
    # the memoized upstream must still provide out.txt from the artifact store.
    record_path(fingerprint_root(tmp_path), "test").unlink()
    (tmp_path / "out.txt").unlink()

    run(tmp_path, incremental=True)
    assert run_lines(tmp_path, "build_runs.txt") == 1  # memoized
    assert run_lines(tmp_path, "test_runs.txt") == 2  # ran, `cat out.txt` succeeded


def test_e2e_script_change_invalidates_job_and_dependents(tmp_path):
    write_ci(tmp_path)
    run(tmp_path, incremental=True)

    write_ci(tmp_path, INCREMENTAL_PIPELINE.replace("echo built > out.txt", "echo rebuilt > out.txt"))
    run(tmp_path, incremental=True)
    assert run_lines(tmp_path, "build_runs.txt") == 2
    # test's fingerprint embeds build's, so it re-runs too.
    assert run_lines(tmp_path, "test_runs.txt") == 2


def test_e2e_variable_change_invalidates(tmp_path):
    ci = INCREMENTAL_PIPELINE + "  variables:\n    MODE: fast\n"
    write_ci(tmp_path, ci)
    run(tmp_path, incremental=True)
    write_ci(tmp_path, ci.replace("MODE: fast", "MODE: slow"))
    run(tmp_path, incremental=True)
    assert run_lines(tmp_path, "test_runs.txt") == 2


def test_e2e_tracked_file_change_invalidates(tmp_path):
    init_git_repo(tmp_path)
    write_ci(tmp_path)
    (tmp_path / "input.txt").write_text("v1")
    git(tmp_path, "add", "-A")

    run(tmp_path, incremental=True)
    run(tmp_path, incremental=True)
    assert run_lines(tmp_path, "build_runs.txt") == 1

    (tmp_path / "input.txt").write_text("v2")
    run(tmp_path, incremental=True)
    assert run_lines(tmp_path, "build_runs.txt") == 2
    assert run_lines(tmp_path, "test_runs.txt") == 2


def test_e2e_failed_job_never_memoizes(tmp_path):
    write_ci(
        tmp_path,
        """
flaky:
  script:
    - echo run >> flaky_runs.txt
    - exit 3
""",
    )
    with pytest.raises(JobExecutionError):
        run(tmp_path, incremental=True)
    assert read_record(fingerprint_root(tmp_path), "flaky") is None

    with pytest.raises(JobExecutionError):
        run(tmp_path, incremental=True)
    assert run_lines(tmp_path, "flaky_runs.txt") == 2


def test_e2e_corrupt_fingerprint_is_a_miss_and_rewritten(tmp_path):
    write_ci(tmp_path)
    run(tmp_path, incremental=True)

    path = record_path(fingerprint_root(tmp_path), "build")
    path.write_text("garbage{{{", encoding="utf-8")
    run(tmp_path, incremental=True)
    assert run_lines(tmp_path, "build_runs.txt") == 2
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "success"

    run(tmp_path, incremental=True)
    assert run_lines(tmp_path, "build_runs.txt") == 2


def test_e2e_missing_artifact_dir_is_a_miss(tmp_path):
    import shutil

    write_ci(tmp_path)
    run(tmp_path, incremental=True)
    shutil.rmtree(tmp_path / ".bitrab" / "artifacts")

    run(tmp_path, incremental=True)
    assert run_lines(tmp_path, "build_runs.txt") == 2
    assert (tmp_path / ".bitrab" / "artifacts" / "build" / "out.txt").exists()


def test_e2e_refresh_runs_but_still_records(tmp_path):
    write_ci(tmp_path)
    run(tmp_path, incremental=True)

    run(tmp_path, incremental=True, refresh=True)
    assert run_lines(tmp_path, "build_runs.txt") == 2

    run(tmp_path, incremental=True)
    assert run_lines(tmp_path, "build_runs.txt") == 2


def test_e2e_without_incremental_always_runs(tmp_path):
    write_ci(tmp_path)
    run(tmp_path)
    run(tmp_path)
    assert run_lines(tmp_path, "build_runs.txt") == 2
    assert not fingerprint_root(tmp_path).exists()


def test_e2e_dag_mode_memoizes(tmp_path):
    write_ci(
        tmp_path,
        """
a:
  script:
    - echo run >> a_runs.txt

b:
  needs: [a]
  script:
    - echo run >> b_runs.txt
""",
    )
    run(tmp_path, incremental=True)
    run(tmp_path, incremental=True)
    assert run_lines(tmp_path, "a_runs.txt") == 1
    assert run_lines(tmp_path, "b_runs.txt") == 1


def test_e2e_dry_run_reports_would_be_memoized(tmp_path, capsys):
    write_ci(tmp_path)
    run(tmp_path, incremental=True)
    capsys.readouterr()

    run(tmp_path, incremental=True, dry_run=True)
    out = capsys.readouterr().out
    assert "would be cached" in out
    # Nothing actually ran.
    assert run_lines(tmp_path, "build_runs.txt") == 1


def test_e2e_dry_run_records_no_fingerprints(tmp_path):
    write_ci(tmp_path)
    run(tmp_path, incremental=True, dry_run=True)
    assert not fingerprint_root(tmp_path).exists()


# ---------------------------------------------------------------------------
# Wiring: CLI, folder status, clean
# ---------------------------------------------------------------------------


def test_cli_run_accepts_incremental_flags():
    from bitrab.cli import create_parser

    args = create_parser().parse_args(["run", "--incremental", "--refresh"])
    assert args.incremental is True
    assert args.refresh is True


def test_cli_clean_accepts_fingerprints():
    from bitrab.cli import create_parser

    args = create_parser().parse_args(["clean", "--what", "fingerprints"])
    assert args.what == "fingerprints"


def test_clean_what_fingerprints(tmp_path):
    from bitrab.folder import clean_fingerprints, scan_folder

    write_ci(tmp_path)
    run(tmp_path, incremental=True)

    summary = scan_folder(tmp_path)
    assert summary.fingerprints_size_bytes > 0

    freed = clean_fingerprints(tmp_path)
    assert freed > 0
    assert not fingerprint_root(tmp_path).exists()
    assert scan_folder(tmp_path).fingerprints_size_bytes == 0


# ---------------------------------------------------------------------------
# Reporting: distinct "cached" status
# ---------------------------------------------------------------------------


def test_cached_status_in_events_and_summary(tmp_path, capsys):
    write_ci(tmp_path)
    run(tmp_path, incremental=True)
    capsys.readouterr()

    run(tmp_path, incremental=True)
    out = capsys.readouterr().out
    assert "cached" in out
    assert "[cach]" in out
    assert "2 job(s) skipped (cached" in out


def test_event_collector_marks_memoized_outcomes():
    from bitrab.execution.events import EventCollector
    from bitrab.execution.stage_runner import JobOutcome

    collector = EventCollector()
    outcome = JobOutcome(job=make_job(), success=True, memoized=True)
    collector.on_job_complete(outcome)
    event = collector.events[-1]
    assert event.data["status"] == "cached"
    assert event.data["memoized"] is True
