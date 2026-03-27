"""Tests for FEATURE-6: artifacts support."""
from __future__ import annotations

from pathlib import Path

import pytest

from bitrab.execution.artifacts import collect_artifacts, inject_dependencies, _artifact_dir
from bitrab.models.pipeline import JobConfig
from bitrab.plan import PipelineProcessor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _job(
    name: str = "myjob",
    artifacts_paths: list[str] | None = None,
    artifacts_when: str = "on_success",
    dependencies: list[str] | None = None,
) -> JobConfig:
    return JobConfig(
        name=name,
        stage="test",
        script=["echo hi"],
        artifacts_paths=artifacts_paths or [],
        artifacts_when=artifacts_when,
        dependencies=dependencies,
    )


# ---------------------------------------------------------------------------
# PipelineProcessor: parsing artifacts and dependencies
# ---------------------------------------------------------------------------


def test_artifacts_paths_parsed():
    proc = PipelineProcessor()
    raw = {
        "stages": ["test"],
        "myjob": {
            "stage": "test",
            "script": ["echo hi"],
            "artifacts": {"paths": ["dist/", "coverage.xml"]},
        },
    }
    pipeline = proc.process_config(raw)
    assert pipeline.jobs[0].artifacts_paths == ["dist/", "coverage.xml"]


def test_artifacts_when_parsed():
    proc = PipelineProcessor()
    raw = {
        "stages": ["test"],
        "myjob": {
            "stage": "test",
            "script": ["echo hi"],
            "artifacts": {"paths": ["out/"], "when": "always"},
        },
    }
    pipeline = proc.process_config(raw)
    assert pipeline.jobs[0].artifacts_when == "always"


def test_artifacts_when_defaults_to_on_success():
    proc = PipelineProcessor()
    raw = {
        "stages": ["test"],
        "myjob": {
            "stage": "test",
            "script": ["echo hi"],
            "artifacts": {"paths": ["out/"]},
        },
    }
    pipeline = proc.process_config(raw)
    assert pipeline.jobs[0].artifacts_when == "on_success"


def test_artifacts_when_invalid_falls_back_to_on_success():
    proc = PipelineProcessor()
    raw = {
        "stages": ["test"],
        "myjob": {
            "stage": "test",
            "script": ["echo hi"],
            "artifacts": {"paths": ["out/"], "when": "bogus"},
        },
    }
    pipeline = proc.process_config(raw)
    assert pipeline.jobs[0].artifacts_when == "on_success"


def test_artifacts_not_configured_defaults():
    proc = PipelineProcessor()
    raw = {
        "stages": ["test"],
        "myjob": {"stage": "test", "script": ["echo hi"]},
    }
    pipeline = proc.process_config(raw)
    assert pipeline.jobs[0].artifacts_paths == []
    assert pipeline.jobs[0].artifacts_when == "on_success"


def test_dependencies_parsed():
    proc = PipelineProcessor()
    raw = {
        "stages": ["build", "test"],
        "build_job": {"stage": "build", "script": ["make"]},
        "test_job": {
            "stage": "test",
            "script": ["make test"],
            "dependencies": ["build_job"],
        },
    }
    pipeline = proc.process_config(raw)
    test_job = next(j for j in pipeline.jobs if j.name == "test_job")
    assert test_job.dependencies == ["build_job"]


def test_dependencies_empty_list():
    proc = PipelineProcessor()
    raw = {
        "stages": ["test"],
        "myjob": {"stage": "test", "script": ["echo hi"], "dependencies": []},
    }
    pipeline = proc.process_config(raw)
    assert pipeline.jobs[0].dependencies == []


def test_dependencies_none_when_not_configured():
    proc = PipelineProcessor()
    raw = {
        "stages": ["test"],
        "myjob": {"stage": "test", "script": ["echo hi"]},
    }
    pipeline = proc.process_config(raw)
    assert pipeline.jobs[0].dependencies is None


# ---------------------------------------------------------------------------
# collect_artifacts unit tests
# ---------------------------------------------------------------------------


def test_collect_artifacts_on_success_copies_files(tmp_path):
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "app.whl").write_text("wheel")
    job = _job(artifacts_paths=["dist/app.whl"], artifacts_when="on_success")

    collect_artifacts(job, tmp_path, succeeded=True)

    dest = _artifact_dir(tmp_path, "myjob") / "dist" / "app.whl"
    assert dest.exists()
    assert dest.read_text() == "wheel"


def test_collect_artifacts_on_success_skips_when_failed(tmp_path):
    (tmp_path / "out.txt").write_text("data")
    job = _job(artifacts_paths=["out.txt"], artifacts_when="on_success")

    collect_artifacts(job, tmp_path, succeeded=False)

    dest = _artifact_dir(tmp_path, "myjob") / "out.txt"
    assert not dest.exists()


def test_collect_artifacts_on_failure_copies_when_failed(tmp_path):
    (tmp_path / "crash.log").write_text("error")
    job = _job(artifacts_paths=["crash.log"], artifacts_when="on_failure")

    collect_artifacts(job, tmp_path, succeeded=False)

    dest = _artifact_dir(tmp_path, "myjob") / "crash.log"
    assert dest.exists()


def test_collect_artifacts_on_failure_skips_when_succeeded(tmp_path):
    (tmp_path / "crash.log").write_text("error")
    job = _job(artifacts_paths=["crash.log"], artifacts_when="on_failure")

    collect_artifacts(job, tmp_path, succeeded=True)

    dest = _artifact_dir(tmp_path, "myjob") / "crash.log"
    assert not dest.exists()


def test_collect_artifacts_always_copies_on_success(tmp_path):
    (tmp_path / "report.xml").write_text("<report/>")
    job = _job(artifacts_paths=["report.xml"], artifacts_when="always")

    collect_artifacts(job, tmp_path, succeeded=True)

    dest = _artifact_dir(tmp_path, "myjob") / "report.xml"
    assert dest.exists()


def test_collect_artifacts_always_copies_on_failure(tmp_path):
    (tmp_path / "report.xml").write_text("<report/>")
    job = _job(artifacts_paths=["report.xml"], artifacts_when="always")

    collect_artifacts(job, tmp_path, succeeded=False)

    dest = _artifact_dir(tmp_path, "myjob") / "report.xml"
    assert dest.exists()


def test_collect_artifacts_glob_pattern(tmp_path):
    src_dir = tmp_path / "build"
    src_dir.mkdir()
    (src_dir / "a.so").write_text("a")
    (src_dir / "b.so").write_text("b")
    (src_dir / "readme.md").write_text("docs")

    job = _job(artifacts_paths=["build/*.so"], artifacts_when="on_success")
    collect_artifacts(job, tmp_path, succeeded=True)

    art_dir = _artifact_dir(tmp_path, "myjob")
    assert (art_dir / "build" / "a.so").exists()
    assert (art_dir / "build" / "b.so").exists()
    assert not (art_dir / "build" / "readme.md").exists()


def test_collect_artifacts_no_paths_does_nothing(tmp_path):
    job = _job(artifacts_paths=[])
    collect_artifacts(job, tmp_path, succeeded=True)
    assert not _artifact_dir(tmp_path, "myjob").exists()


def test_collect_artifacts_missing_file_is_silently_skipped(tmp_path):
    job = _job(artifacts_paths=["nonexistent.txt"], artifacts_when="on_success")
    # Should not raise
    collect_artifacts(job, tmp_path, succeeded=True)


# ---------------------------------------------------------------------------
# inject_dependencies unit tests
# ---------------------------------------------------------------------------


def _put_artifact(project_dir: Path, job_name: str, rel_path: str, content: str) -> None:
    """Helper: write a file into a job's artifact directory."""
    dest = _artifact_dir(project_dir, job_name) / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content)


def test_inject_copies_named_dependency_artifacts(tmp_path):
    _put_artifact(tmp_path, "build_job", "dist/app.whl", "wheel")
    job = _job(name="test_job", dependencies=["build_job"])

    inject_dependencies(job, tmp_path, completed_jobs=["build_job"])

    assert (tmp_path / "dist" / "app.whl").read_text() == "wheel"


def test_inject_empty_dependencies_copies_nothing(tmp_path):
    _put_artifact(tmp_path, "build_job", "dist/app.whl", "wheel")
    job = _job(name="test_job", dependencies=[])

    inject_dependencies(job, tmp_path, completed_jobs=["build_job"])

    assert not (tmp_path / "dist" / "app.whl").exists()


def test_inject_none_dependencies_copies_all_completed(tmp_path):
    _put_artifact(tmp_path, "job_a", "a.txt", "A")
    _put_artifact(tmp_path, "job_b", "b.txt", "B")
    job = _job(name="job_c", dependencies=None)

    inject_dependencies(job, tmp_path, completed_jobs=["job_a", "job_b"])

    assert (tmp_path / "a.txt").read_text() == "A"
    assert (tmp_path / "b.txt").read_text() == "B"


def test_inject_skips_job_with_no_artifact_dir(tmp_path):
    job = _job(name="test_job", dependencies=None)
    # job_a has no artifacts directory
    inject_dependencies(job, tmp_path, completed_jobs=["job_a"])
    # Should not raise and should produce no output files
    assert list(tmp_path.iterdir()) == []


def test_inject_respects_named_dependencies_only(tmp_path):
    _put_artifact(tmp_path, "job_a", "a.txt", "A")
    _put_artifact(tmp_path, "job_b", "b.txt", "B")
    job = _job(name="job_c", dependencies=["job_a"])

    inject_dependencies(job, tmp_path, completed_jobs=["job_a", "job_b"])

    assert (tmp_path / "a.txt").exists()
    assert not (tmp_path / "b.txt").exists()
