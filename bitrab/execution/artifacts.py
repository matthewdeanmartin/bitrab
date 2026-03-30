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
import re
import shutil
from pathlib import Path

from bitrab.execution.variables import parse_dotenv
from bitrab.models.pipeline import JobConfig

_INVALID_PATH_CHARS_RE = re.compile(r'[\\/:*?"<>|]')


def _sanitize(name: str) -> str:
    """Replace filesystem-invalid characters with underscores."""
    return _INVALID_PATH_CHARS_RE.sub("_", name)


def _artifact_dir(project_dir: Path, job_name: str) -> Path:
    """Return the artifact storage directory for a job."""
    return project_dir / ".bitrab" / "artifacts" / _sanitize(job_name)


def collect_artifacts(
    job: JobConfig,
    project_dir: Path,
    succeeded: bool,
) -> None:
    """Copy artifact paths to ``.bitrab/artifacts/<job_name>/`` after job execution.

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

    dest_root = _artifact_dir(project_dir, job.name)
    dest_root.mkdir(parents=True, exist_ok=True)

    for pattern in job.artifacts_paths:
        # glob.glob with recursive=True supports ** patterns
        matches = glob.glob(pattern, root_dir=str(project_dir), recursive=True)
        for rel_path in matches:
            src = project_dir / rel_path
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
) -> None:
    """Copy artifacts from dependency jobs into the project directory.

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

    for dep_name in sources:
        artifact_src = _artifact_dir(project_dir, dep_name)
        if not artifact_src.exists():
            continue
        # Copy each file/dir from the artifact directory to the project directory,
        # preserving relative paths.
        for item in artifact_src.rglob("*"):
            if not item.exists():
                continue
            rel = item.relative_to(artifact_src)
            dest = project_dir / rel
            if item.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dest)


# ---------------------------------------------------------------------------
# Dotenv report: artifacts: reports: dotenv:
# ---------------------------------------------------------------------------

_DOTENV_STORE = ".bitrab/artifacts/{job_name}/.dotenv_report"


def collect_dotenv_report(job: JobConfig, project_dir: Path, succeeded: bool) -> None:
    """Store the dotenv report file produced by *job* in the artifact store.

    Called immediately after a job completes (same lifecycle as
    :func:`collect_artifacts`).  Only runs when the job defines
    ``artifacts: reports: dotenv:`` *and* the dotenv file actually exists in
    the project directory.  The file is copied to a stable path inside
    ``.bitrab/`` so downstream jobs can read it via
    :func:`inject_dotenv_variables`.

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

    src = project_dir / job.artifacts_dotenv
    if not src.is_file():
        return

    dest = project_dir / _DOTENV_STORE.format(job_name=_sanitize(job.name))
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
        store = project_dir / _DOTENV_STORE.format(job_name=_sanitize(dep_name))
        if store.is_file():
            try:
                merged.update(parse_dotenv(store.read_text(encoding="utf-8")))
            except OSError:
                pass
    return merged
