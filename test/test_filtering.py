"""Tests for FEATURE-8 (--jobs filtering) and FEATURE-9 (--stage filtering)."""
from __future__ import annotations


from bitrab.models.pipeline import PipelineConfig
from bitrab.plan import PipelineProcessor, filter_pipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pipeline() -> PipelineConfig:
    """Three stages, five jobs."""
    proc = PipelineProcessor()
    return proc.process_config(
        {
            "stages": ["build", "test", "deploy"],
            "build_job": {"stage": "build", "script": ["make"]},
            "unit_tests": {"stage": "test", "script": ["pytest"]},
            "lint": {"stage": "test", "script": ["ruff check ."]},
            "deploy_staging": {"stage": "deploy", "script": ["./deploy.sh staging"]},
            "deploy_prod": {"stage": "deploy", "script": ["./deploy.sh prod"]},
        }
    )


def _job_names(pipeline: PipelineConfig) -> list[str]:
    return [j.name for j in pipeline.jobs]


def _stage_list(pipeline: PipelineConfig) -> list[str]:
    return pipeline.stages


# ---------------------------------------------------------------------------
# filter_pipeline: job filter
# ---------------------------------------------------------------------------


def test_filter_jobs_keeps_named_jobs():
    p = _make_pipeline()
    result = filter_pipeline(p, jobs=["build_job", "lint"])
    assert set(_job_names(result)) == {"build_job", "lint"}


def test_filter_jobs_trims_stage_list():
    p = _make_pipeline()
    result = filter_pipeline(p, jobs=["unit_tests"])
    assert _stage_list(result) == ["test"]
    assert "build" not in result.stages
    assert "deploy" not in result.stages


def test_filter_jobs_preserves_stage_order():
    p = _make_pipeline()
    result = filter_pipeline(p, jobs=["build_job", "deploy_prod"])
    assert _stage_list(result) == ["build", "deploy"]


def test_filter_jobs_unknown_name_returns_empty():
    p = _make_pipeline()
    result = filter_pipeline(p, jobs=["nonexistent"])
    assert _job_names(result) == []
    assert _stage_list(result) == []


def test_filter_jobs_none_keeps_all():
    p = _make_pipeline()
    result = filter_pipeline(p, jobs=None)
    assert set(_job_names(result)) == {"build_job", "unit_tests", "lint", "deploy_staging", "deploy_prod"}


def test_filter_jobs_empty_list_returns_empty():
    p = _make_pipeline()
    result = filter_pipeline(p, jobs=[])
    assert _job_names(result) == []
    assert _stage_list(result) == []


# ---------------------------------------------------------------------------
# filter_pipeline: stage filter
# ---------------------------------------------------------------------------


def test_filter_stages_keeps_jobs_in_stage():
    p = _make_pipeline()
    result = filter_pipeline(p, stages=["test"])
    assert set(_job_names(result)) == {"unit_tests", "lint"}


def test_filter_stages_multiple_stages():
    p = _make_pipeline()
    result = filter_pipeline(p, stages=["build", "deploy"])
    assert set(_job_names(result)) == {"build_job", "deploy_staging", "deploy_prod"}
    assert _stage_list(result) == ["build", "deploy"]


def test_filter_stages_preserves_original_order():
    p = _make_pipeline()
    result = filter_pipeline(p, stages=["deploy", "build"])  # reversed input
    # Output order follows original pipeline.stages order
    assert _stage_list(result) == ["build", "deploy"]


def test_filter_stages_unknown_stage_returns_empty():
    p = _make_pipeline()
    result = filter_pipeline(p, stages=["nonexistent"])
    assert _job_names(result) == []
    assert _stage_list(result) == []


def test_filter_stages_none_keeps_all():
    p = _make_pipeline()
    result = filter_pipeline(p, stages=None)
    assert len(result.jobs) == 5
    assert result.stages == ["build", "test", "deploy"]


# ---------------------------------------------------------------------------
# filter_pipeline: combined job + stage filter
# ---------------------------------------------------------------------------


def test_filter_combined_jobs_and_stages():
    p = _make_pipeline()
    # Ask for deploy_prod (deploy stage) but also restrict to test stage
    # => deploy_prod is excluded by stage filter, nothing remains
    result = filter_pipeline(p, jobs=["deploy_prod"], stages=["test"])
    assert _job_names(result) == []


def test_filter_combined_narrowing():
    p = _make_pipeline()
    # Jobs filter keeps build+test jobs; stage filter also restricts to test
    result = filter_pipeline(p, jobs=["build_job", "unit_tests", "lint"], stages=["test"])
    assert set(_job_names(result)) == {"unit_tests", "lint"}
    assert _stage_list(result) == ["test"]


# ---------------------------------------------------------------------------
# filter_pipeline: original pipeline is not mutated
# ---------------------------------------------------------------------------


def test_filter_does_not_mutate_original():
    p = _make_pipeline()
    original_jobs = list(p.jobs)
    original_stages = list(p.stages)

    filter_pipeline(p, jobs=["lint"])

    assert p.jobs == original_jobs
    assert p.stages == original_stages


# ---------------------------------------------------------------------------
# PipelineProcessor: parsing is unaffected by filter_pipeline
# ---------------------------------------------------------------------------


def test_filter_pipeline_preserves_variables():
    proc = PipelineProcessor()
    raw = {
        "stages": ["test"],
        "variables": {"FOO": "bar"},
        "myjob": {"stage": "test", "script": ["echo $FOO"]},
    }
    pipeline = proc.process_config(raw)
    result = filter_pipeline(pipeline, jobs=["myjob"])
    assert result.variables == {"FOO": "bar"}
