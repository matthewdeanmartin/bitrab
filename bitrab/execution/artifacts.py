"""Artifact collection, injection, and dotenv report handling.

After a job completes:
  - If the job defines ``artifacts: paths:``, matching files are copied from
    the project directory to ``.bitrab/artifacts/<job_name>/``.
  - The copy is conditional on ``artifacts: when:`` (on_success / on_failure /
    always) vs. whether the job succeeded.

Before a job starts:
  - If the job defines ``dependencies: [job_a, job_b]``, artifacts from those
    jobs are copied into the project directory (preserving relative paths).
  - ``dependencies: []`` means "no artifacts" — nothing is copied.
  - Omitting ``dependencies`` (None) means "copy artifacts from all prior jobs
    that produced them" (GitLab default behaviour).
"""

from __future__ import annotations

import glob
import os
import shutil
from pathlib import Path

from bitrab.execution.variables import parse_dotenv
from bitrab.models.pipeline import JobConfig
from bitrab.utils import sanitize_job_name as sanitize_name


def artifact_dir(project_dir: Path, job_name: str) -> Path:
    """Return the artifact storage directory for a job."""
    return project_dir / ".bitrab" / "artifacts" / sanitize_name(job_name)


def collect_artifacts(
    job: JobConfig,
    project_dir: Path,
    succeeded: bool,
    effective_dir: Path | None = None,
) -> None:
    """Copy artifact paths to ``.bitrab/artifacts/<job_name>/`` after job execution.

    Files are *read from* ``effective_dir`` (which may be a worktree) and
    *stored under* ``project_dir/.bitrab/artifacts/<job>/`` so downstream jobs
    can find them regardless of where they ran.  When *effective_dir* is None
    it defaults to *project_dir* (non-worktree execution).

    Respects ``artifacts_when``:
    - ``on_success``: collect only if ``succeeded`` is True
    - ``on_failure``: collect only if ``succeeded`` is False
    - ``always``: collect regardless

    If no ``artifacts_paths`` are configured, does nothing.
    """
    if not job.artifacts_paths:
        return

    when = job.artifacts_when
    if when == "on_success" and not succeeded:
        return
    if when == "on_failure" and succeeded:
        return
    # "always" falls through

    source_dir = effective_dir if effective_dir is not None else project_dir
    dest_root = artifact_dir(project_dir, job.name)
    dest_root.mkdir(parents=True, exist_ok=True)

    for pattern in job.artifacts_paths:
        # For Python 3.9 compatibility (root_dir= was added in 3.10)
        import os

        full_pattern = os.path.join(str(source_dir), pattern)
        for abs_path in glob.glob(full_pattern, recursive=True):
            rel_path = os.path.relpath(abs_path, str(source_dir))
            src = Path(abs_path)
            if not src.exists():
                continue
            dest = dest_root / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)
            else:
                shutil.copy2(src, dest)


def inject_dependencies(
    job: JobConfig,
    project_dir: Path,
    completed_jobs: list[str],
    effective_dir: Path | None = None,
) -> None:
    """Copy artifacts from dependency jobs into the job's working tree.

    Artifacts are always *read from* the stable artifact store under
    ``project_dir/.bitrab/artifacts/`` but *written into* ``effective_dir``
    (the worktree for this job, or ``project_dir`` if worktrees are off).

    - ``dependencies: None`` (omitted) → copy artifacts from all ``completed_jobs``
      that have an artifact directory.
    - ``dependencies: []`` → copy nothing.
    - ``dependencies: [a, b]`` → copy only from jobs a and b.
    """
    if job.dependencies is not None and len(job.dependencies) == 0:
        return  # explicit empty list = no artifacts

    if job.dependencies is None:
        sources = completed_jobs
    else:
        sources = job.dependencies

    target_dir = effective_dir if effective_dir is not None else project_dir

    for dep_name in sources:
        artifact_src = artifact_dir(project_dir, dep_name)
        if not artifact_src.exists():
            continue
        # Copy each file from the artifact directory to the target tree,
        # preserving relative paths.  os.walk pre-separates files and
        # directories, avoiding a per-entry is_dir() syscall.
        for dirpath, dirnames, filenames in os.walk(artifact_src):
            for dname in dirnames:
                dest_dir = target_dir / Path(dirpath).relative_to(artifact_src) / dname
                dest_dir.mkdir(parents=True, exist_ok=True)
            for fname in filenames:
                src = Path(dirpath) / fname
                dest = target_dir / src.relative_to(artifact_src)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)


# ---------------------------------------------------------------------------
# Dotenv report: artifacts: reports: dotenv:
# ---------------------------------------------------------------------------

DOTENV_STORE = ".bitrab/artifacts/{job_name}/.dotenv_report"


def collect_dotenv_report(
    job: JobConfig,
    project_dir: Path,
    succeeded: bool,
    effective_dir: Path | None = None,
) -> None:
    """Store the dotenv report file produced by *job* in the artifact store.

    Called immediately after a job completes (same lifecycle as
    :func:`collect_artifacts`).  Only runs when the job defines
    ``artifacts: reports: dotenv:`` *and* the dotenv file actually exists in
    ``effective_dir`` (the worktree, or ``project_dir`` for non-worktree runs).
    The file is copied to a stable path inside ``project_dir/.bitrab/`` so
    downstream jobs can read it via :func:`load_dotenv_reports`.

    Respects ``artifacts: when:`` — if the job failed and ``when`` is
    ``on_success`` (the default), the dotenv is not stored.
    """
    if not job.artifacts_dotenv:
        return

    when = job.artifacts_when
    if when == "on_success" and not succeeded:
        return
    if when == "on_failure" and succeeded:
        return

    source_dir = effective_dir if effective_dir is not None else project_dir
    src = source_dir / job.artifacts_dotenv
    if not src.is_file():
        return

    dest = project_dir / DOTENV_STORE.format(job_name=sanitize_name(job.name))
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def load_dotenv_reports(
    job: JobConfig,
    project_dir: Path,
    completed_jobs: list[str],
) -> dict[str, str]:
    """Return variables from dotenv reports produced by *job*'s dependencies.

    This simulates GitLab's pipeline variable passing via
    ``artifacts: reports: dotenv:``.  When job A writes a dotenv file and job B
    lists A in its ``dependencies:`` (or inherits all, which is the default),
    the variables from A's dotenv report are available as environment variables
    in B.

    Resolution follows the same ``dependencies:`` logic as
    :func:`inject_dependencies`:
    - ``dependencies: None`` (omitted) → variables from all completed jobs
    - ``dependencies: []``             → no variables
    - ``dependencies: [a, b]``         → variables from a and b only

    Variables from later jobs in the list override earlier ones.  Job-level
    ``variables:`` set in ``.gitlab-ci.yml`` take precedence over these (that
    layering happens in :meth:`VariableManager.prepare_environment`).
    """
    if job.dependencies is not None and len(job.dependencies) == 0:
        return {}

    sources = completed_jobs if job.dependencies is None else job.dependencies

    merged: dict[str, str] = {}
    for dep_name in sources:
        store = project_dir / DOTENV_STORE.format(job_name=sanitize_name(dep_name))
        if store.is_file():
            try:
                merged.update(parse_dotenv(store.read_text(encoding="utf-8")))
            except OSError:
                pass
    return merged
