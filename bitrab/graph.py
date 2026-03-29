"""ARCH-5: Pipeline dependency graph rendering.

Renders a pipeline's stage/job structure as either:
- ASCII terminal output (default): stages as columns, jobs as rows, DAG arrows
- DOT format (--format dot): Graphviz .dot for external rendering

Usage::

    from bitrab.graph import render_pipeline_graph
    print(render_pipeline_graph(pipeline))
    print(render_pipeline_graph(pipeline, fmt="dot"))
"""

from __future__ import annotations

from bitrab.execution.stage_runner import has_dag_jobs, organize_jobs_by_stage
from bitrab.models.pipeline import JobConfig, PipelineConfig


def render_pipeline_graph(pipeline: PipelineConfig, fmt: str = "text") -> str:
    """Render the pipeline as a dependency graph.

    Args:
        pipeline: The pipeline configuration to render.
        fmt: Output format — ``"text"`` (default) or ``"dot"`` (Graphviz).

    Returns:
        A string representation of the pipeline graph.
    """
    if fmt == "dot":
        return _render_dot(pipeline)
    return _render_text(pipeline)


# ---------------------------------------------------------------------------
# Text renderer
# ---------------------------------------------------------------------------


def _render_text(pipeline: PipelineConfig) -> str:
    """Render a human-readable stage/job tree with DAG dependency arrows."""
    lines: list[str] = []
    jobs_by_stage = organize_jobs_by_stage(pipeline)
    is_dag = has_dag_jobs(pipeline)
    job_index: dict[str, JobConfig] = {j.name: j for j in pipeline.jobs}

    lines.append("")
    if is_dag:
        lines.append("  Pipeline graph (DAG mode — jobs with needs: run out of stage order)")
    else:
        lines.append("  Pipeline graph (stage mode)")
    lines.append("")

    for i, stage in enumerate(pipeline.stages):
        jobs = jobs_by_stage.get(stage, [])

        # Stage header
        prefix = "  " if i == 0 else "  "
        lines.append(f"{prefix}Stage: {stage}")
        lines.append(f"  {'─' * (len(stage) + 7)}")

        if not jobs:
            lines.append("    (empty — no jobs)")
        else:
            for job in jobs:
                attrs = _job_attrs(job)
                attr_str = f"  [{', '.join(attrs)}]" if attrs else ""
                lines.append(f"    • {job.name}{attr_str}")
                if job.needs:
                    for dep in job.needs:
                        dep_job = job_index.get(dep)
                        dep_stage = f" (stage: {dep_job.stage})" if dep_job and dep_job.stage != stage else ""
                        lines.append(f"        ↳ needs: {dep}{dep_stage}")

        if i < len(pipeline.stages) - 1:
            lines.append("        ↓")
        lines.append("")

    _append_legend(lines, is_dag, pipeline)
    return "\n".join(lines)


def _job_attrs(job: JobConfig) -> list[str]:
    """Return a list of notable attribute labels for a job."""
    attrs: list[str] = []
    if getattr(job, "when", None) and job.when not in ("on_success", None):  # type: ignore[union-attr]
        attrs.append(f"when:{job.when}")  # type: ignore[union-attr]
    if getattr(job, "allow_failure", False):
        attrs.append("allow_failure")
    return attrs


def _append_legend(lines: list[str], is_dag: bool, pipeline: PipelineConfig) -> None:
    """Append a summary line with job/stage counts."""
    total_jobs = len(pipeline.jobs)
    total_stages = len(pipeline.stages)
    dag_jobs = sum(1 for j in pipeline.jobs if j.needs)

    lines.append(
        f"  {total_stages} stage(s), {total_jobs} job(s)",
    )
    if is_dag:
        lines.append(f"  {dag_jobs} job(s) have explicit needs: dependencies")
    lines.append("")


# ---------------------------------------------------------------------------
# DOT renderer
# ---------------------------------------------------------------------------


def _render_dot(pipeline: PipelineConfig) -> str:
    """Render a Graphviz DOT representation of the pipeline."""
    lines: list[str] = []
    jobs_by_stage = organize_jobs_by_stage(pipeline)

    lines.append("digraph pipeline {")
    lines.append("  rankdir=LR;")
    lines.append("  node [shape=box, style=filled, fillcolor=lightblue];")
    lines.append("")

    # Render stages as subgraphs (clusters)
    for _i, stage in enumerate(pipeline.stages):
        jobs = jobs_by_stage.get(stage, [])
        safe_stage = _dot_id(stage)
        lines.append(f"  subgraph cluster_{safe_stage} {{")
        lines.append(f'    label="{stage}";')
        lines.append("    style=rounded;")
        lines.append("    color=gray;")
        for job in jobs:
            job_id = _dot_id(job.name)
            attrs = _dot_job_attrs(job)
            lines.append(f'    {job_id} [label="{job.name}"{attrs}];')
        lines.append("  }")
        lines.append("")

    # Render edges
    has_dag = has_dag_jobs(pipeline)
    if has_dag:
        # Explicit needs: edges
        for job in pipeline.jobs:
            if job.needs:
                job_id = _dot_id(job.name)
                for dep in job.needs:
                    dep_id = _dot_id(dep)
                    lines.append(f"  {dep_id} -> {job_id};")
    else:
        # Stage ordering edges: each job in stage N → each job in stage N+1
        stage_list = pipeline.stages
        for i in range(len(stage_list) - 1):
            cur_jobs = jobs_by_stage.get(stage_list[i], [])
            nxt_jobs = jobs_by_stage.get(stage_list[i + 1], [])
            for cur in cur_jobs:
                for nxt in nxt_jobs:
                    lines.append(f"  {_dot_id(cur.name)} -> {_dot_id(nxt.name)};")

    lines.append("}")
    return "\n".join(lines)


def _dot_id(name: str) -> str:
    """Convert a job/stage name to a valid DOT identifier."""
    return '"' + name.replace('"', '\\"') + '"'


def _dot_job_attrs(job: JobConfig) -> str:
    """Return extra DOT attributes for a job node."""
    parts: list[str] = []
    if getattr(job, "when", None) == "manual":
        parts.append("fillcolor=lightyellow")
    elif getattr(job, "allow_failure", False):
        parts.append("fillcolor=lightsalmon")
    if parts:
        return ", " + ", ".join(parts)
    return ""
