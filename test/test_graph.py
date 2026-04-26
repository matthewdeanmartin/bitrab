"""Tests for ARCH-5: Pipeline dependency graph rendering."""

from __future__ import annotations

from bitrab.graph import render_pipeline_graph
from bitrab.models.pipeline import JobConfig, PipelineConfig


def pipeline(*jobs: JobConfig, stages: list[str] | None = None) -> PipelineConfig:
    if stages is None:
        unique_stages: list[str] = []
        seen: set[str] = set()
        for j in jobs:
            if j.stage not in seen:
                unique_stages.append(j.stage)
                seen.add(j.stage)
        stages = unique_stages
    return PipelineConfig(stages=stages, jobs=list(jobs))


class TestTextRenderer:
    def test_single_stage_single_job(self):
        pipe = pipeline(JobConfig(name="lint", stage="test", script=["echo hi"]))
        out = render_pipeline_graph(pipe)
        assert "Stage: test" in out
        assert "lint" in out

    def test_two_stages_shown_in_order(self):
        pipe = pipeline(
            JobConfig(name="lint", stage="test", script=[]),
            JobConfig(name="build", stage="build", script=[]),
            stages=["test", "build"],
        )
        out = render_pipeline_graph(pipe)
        assert out.index("Stage: test") < out.index("Stage: build")

    def test_stage_mode_label(self):
        pipe = pipeline(JobConfig(name="lint", stage="test", script=[]))
        out = render_pipeline_graph(pipe)
        assert "stage mode" in out

    def test_dag_mode_label(self):
        pipe = pipeline(
            JobConfig(name="lint", stage="test", script=[]),
            JobConfig(name="build", stage="build", script=[], needs=["lint"]),
            stages=["test", "build"],
        )
        out = render_pipeline_graph(pipe)
        assert "DAG mode" in out

    def test_needs_shown_with_arrow(self):
        pipe = pipeline(
            JobConfig(name="lint", stage="test", script=[]),
            JobConfig(name="build", stage="build", script=[], needs=["lint"]),
            stages=["test", "build"],
        )
        out = render_pipeline_graph(pipe)
        assert "needs: lint" in out

    def test_needs_shows_cross_stage_label(self):
        pipe = pipeline(
            JobConfig(name="lint", stage="test", script=[]),
            JobConfig(name="build", stage="build", script=[], needs=["lint"]),
            stages=["test", "build"],
        )
        out = render_pipeline_graph(pipe)
        assert "stage: test" in out  # cross-stage annotation

    def test_allow_failure_attr(self):
        pipe = pipeline(JobConfig(name="flaky", stage="test", script=[], allow_failure=True))
        out = render_pipeline_graph(pipe)
        assert "allow_failure" in out

    def test_when_attr_shown(self):
        pipe = pipeline(JobConfig(name="deploy", stage="deploy", script=[], when="manual"))
        out = render_pipeline_graph(pipe)
        assert "when:manual" in out

    def test_on_success_when_not_shown(self):
        pipe = pipeline(JobConfig(name="build", stage="build", script=[], when="on_success"))
        out = render_pipeline_graph(pipe)
        assert "when:" not in out

    def test_empty_stage_shown(self):
        pipe = PipelineConfig(
            stages=["test", "deploy"],
            jobs=[JobConfig(name="lint", stage="test", script=[])],
        )
        out = render_pipeline_graph(pipe)
        assert "Stage: deploy" in out
        assert "empty" in out

    def test_separator_between_stages(self):
        pipe = pipeline(
            JobConfig(name="lint", stage="test", script=[]),
            JobConfig(name="build", stage="build", script=[]),
            stages=["test", "build"],
        )
        out = render_pipeline_graph(pipe)
        assert "↓" in out

    def test_summary_line_counts(self):
        pipe = pipeline(
            JobConfig(name="a", stage="test", script=[]),
            JobConfig(name="b", stage="test", script=[]),
            JobConfig(name="c", stage="build", script=[]),
            stages=["test", "build"],
        )
        out = render_pipeline_graph(pipe)
        assert "2 stage(s), 3 job(s)" in out

    def test_dag_summary_counts_needs_jobs(self):
        pipe = pipeline(
            JobConfig(name="lint", stage="test", script=[]),
            JobConfig(name="build", stage="build", script=[], needs=["lint"]),
            stages=["test", "build"],
        )
        out = render_pipeline_graph(pipe)
        assert "1 job(s) have explicit needs:" in out

    def test_multiple_jobs_in_same_stage(self):
        pipe = pipeline(
            JobConfig(name="unit", stage="test", script=[]),
            JobConfig(name="lint", stage="test", script=[]),
        )
        out = render_pipeline_graph(pipe)
        assert "unit" in out
        assert "lint" in out

    def test_fmt_text_explicit(self):
        pipe = pipeline(JobConfig(name="lint", stage="test", script=[]))
        out = render_pipeline_graph(pipe, fmt="text")
        assert "Stage: test" in out


class TestDotRenderer:
    def test_returns_dot_header(self):
        pipe = pipeline(JobConfig(name="lint", stage="test", script=[]))
        out = render_pipeline_graph(pipe, fmt="dot")
        assert "digraph pipeline" in out

    def test_stage_cluster_present(self):
        pipe = pipeline(JobConfig(name="lint", stage="test", script=[]))
        out = render_pipeline_graph(pipe, fmt="dot")
        assert "cluster_" in out
        assert 'label="test"' in out

    def test_job_node_present(self):
        pipe = pipeline(JobConfig(name="lint", stage="test", script=[]))
        out = render_pipeline_graph(pipe, fmt="dot")
        assert '"lint"' in out

    def test_stage_edges_without_dag(self):
        pipe = pipeline(
            JobConfig(name="lint", stage="test", script=[]),
            JobConfig(name="build", stage="build", script=[]),
            stages=["test", "build"],
        )
        out = render_pipeline_graph(pipe, fmt="dot")
        assert '"lint" -> "build"' in out

    def test_dag_edges_use_needs(self):
        pipe = pipeline(
            JobConfig(name="lint", stage="test", script=[]),
            JobConfig(name="build", stage="build", script=[], needs=["lint"]),
            stages=["test", "build"],
        )
        out = render_pipeline_graph(pipe, fmt="dot")
        assert '"lint" -> "build"' in out

    def test_manual_job_gets_yellow_fill(self):
        pipe = pipeline(JobConfig(name="deploy", stage="deploy", script=[], when="manual"))
        out = render_pipeline_graph(pipe, fmt="dot")
        assert "lightyellow" in out

    def test_allow_failure_gets_salmon_fill(self):
        pipe = pipeline(JobConfig(name="flaky", stage="test", script=[], allow_failure=True))
        out = render_pipeline_graph(pipe, fmt="dot")
        assert "lightsalmon" in out

    def test_dot_closing_brace(self):
        pipe = pipeline(JobConfig(name="lint", stage="test", script=[]))
        out = render_pipeline_graph(pipe, fmt="dot")
        assert out.rstrip().endswith("}")

    def test_special_chars_in_name_escaped(self):
        # job names with quotes should not break DOT syntax
        pipe = pipeline(JobConfig(name='my "job"', stage="test", script=[]))
        out = render_pipeline_graph(pipe, fmt="dot")
        assert '\\"' in out
