"""Artifact collection and injection for FEATURE-6.

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
