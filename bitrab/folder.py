"""Manage the .bitrab/ workspace folder.

The ``.bitrab/`` directory accumulates several kinds of content:

* **Job directories** – ``.bitrab/<job_name>/`` – per-job working directories
  created by the stage runner for each pipeline execution.
* **Artifact directories** – ``.bitrab/artifacts/<job_name>/`` – files
  collected from job output by the artifacts subsystem.
* **Log directories** – ``.bitrab/logs/<run_id>/`` – one directory per
  pipeline run containing a JSON event log and a text summary.  The
  ``run_id`` is a ``YYYYMMDD_HHMMSS_<short-uuid>`` string.

Public API
----------
- :func:`bitrab_dir` – canonical path for the workspace
- :func:`scan_folder` – return a :class:`FolderSummary` describing current state
- :func:`list_runs` – return :class:`RunRecord` objects for every persisted run
- :func:`prune_runs` – delete the oldest runs, keeping *keep* most recent
- :func:`clean_artifacts` – delete all artifact directories
- :func:`clean_job_dirs` – delete all job working directories (not logs/artifacts)
- :func:`clean_all` – delete everything under ``.bitrab/``

Size tracking
-------------
When a run log is written the directory size is recorded inside the log
metadata file (``meta.json``), so :func:`list_runs` never has to re-walk the
tree — it just reads the cheap JSON file.  Folder-level totals are calculated
on demand by :func:`scan_folder`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LOGS_DIR = "logs"
_ARTIFACTS_DIR = "artifacts"
_SIZE_WARN_BYTES_DEFAULT = 500 * 1024 * 1024  # 500 MB

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def bitrab_dir(project_dir: Path) -> Path:
    """Return the ``.bitrab/`` directory for *project_dir*."""
    return project_dir / ".bitrab"


def _dir_size_bytes(path: Path) -> int:
    """Return total byte size of all files under *path* (fast os.walk version)."""
    total = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(path):
            for fname in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, fname))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _human_size(num_bytes: int) -> str:
    """Return a human-readable size string (e.g. ``"12.3 MB"``)."""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024.0:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PB"


def _make_run_id() -> str:
    """Return a sortable, unique run identifier: ``YYYYMMDD_HHMMSS_<8hex>``."""
    ts = time.strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"{ts}_{short}"


_RUN_ID_RE = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{8}$")


def _is_run_id(name: str) -> bool:
    return bool(_RUN_ID_RE.match(name))


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class RunRecord:
    """Metadata about a single persisted pipeline run."""

    run_id: str
    run_dir: Path
    started_at: float  # wall-clock time.time() from meta.json
    success: bool
    total_duration_s: float
    job_count: int
    size_bytes: int  # pre-recorded in meta.json; 0 if missing

    @property
    def human_size(self) -> str:
        """Human-readable size string."""
        return _human_size(self.size_bytes)

    @property
    def started_at_iso(self) -> str:
        """ISO-8601 representation of *started_at*."""
        import datetime

        return datetime.datetime.fromtimestamp(self.started_at).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class FolderSummary:
    """Snapshot of the ``.bitrab/`` directory state."""

    bitrab_path: Path
    exists: bool
    total_size_bytes: int
    logs_size_bytes: int
    artifacts_size_bytes: int
    job_dirs_size_bytes: int
    run_count: int
    warn_threshold_bytes: int = _SIZE_WARN_BYTES_DEFAULT
    subdirs: list[str] = field(default_factory=list)

    @property
    def total_human(self) -> str:
        return _human_size(self.total_size_bytes)

    @property
    def logs_human(self) -> str:
        return _human_size(self.logs_size_bytes)

    @property
    def artifacts_human(self) -> str:
        return _human_size(self.artifacts_size_bytes)

    @property
    def job_dirs_human(self) -> str:
        return _human_size(self.job_dirs_size_bytes)

    @property
    def is_large(self) -> bool:
        """True when the folder exceeds the warning threshold."""
        return self.total_size_bytes >= self.warn_threshold_bytes

    def format_text(self) -> str:
        """Render a human-readable status report."""
        lines: list[str] = []
        if not self.exists:
            lines.append("  .bitrab/ does not exist yet (no runs recorded).")
            return "\n".join(lines)

        lines.append(f"  Location : {self.bitrab_path}")
        lines.append(f"  Total    : {self.total_human}")
        lines.append(f"  Logs     : {self.logs_human}  ({self.run_count} run(s))")
        lines.append(f"  Artifacts: {self.artifacts_human}")
        lines.append(f"  Job dirs : {self.job_dirs_human}")
        if self.subdirs:
            lines.append(f"  Contents : {', '.join(self.subdirs)}")
        if self.is_large:
            lines.append("")
            lines.append(
                f"  ⚠️  Folder is large ({self.total_human} ≥ {_human_size(self.warn_threshold_bytes)})."
                "  Consider running: bitrab folder clean"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------


def scan_folder(
    project_dir: Path,
    warn_threshold_bytes: int = _SIZE_WARN_BYTES_DEFAULT,
) -> FolderSummary:
    """Walk ``.bitrab/`` and return a :class:`FolderSummary`.

    This is intentionally fast: it uses ``os.walk`` (single syscall-chain)
    rather than ``Path.rglob`` which allocates a lot of objects.
    """
    bd = bitrab_dir(project_dir)
    if not bd.exists():
        return FolderSummary(
            bitrab_path=bd,
            exists=False,
            total_size_bytes=0,
            logs_size_bytes=0,
            artifacts_size_bytes=0,
            job_dirs_size_bytes=0,
            run_count=0,
            warn_threshold_bytes=warn_threshold_bytes,
        )

    logs_path = bd / _LOGS_DIR
    artifacts_path = bd / _ARTIFACTS_DIR

    logs_bytes = _dir_size_bytes(logs_path) if logs_path.exists() else 0
    artifact_bytes = _dir_size_bytes(artifacts_path) if artifacts_path.exists() else 0

    # Job dirs = everything that isn't logs/ or artifacts/
    job_bytes = 0
    subdirs: list[str] = []
    try:
        for entry in os.scandir(bd):
            if entry.is_dir() and entry.name not in (_LOGS_DIR, _ARTIFACTS_DIR):
                job_bytes += _dir_size_bytes(Path(entry.path))
                subdirs.append(entry.name)
    except OSError:
        pass

    total = logs_bytes + artifact_bytes + job_bytes

    # Count run dirs
    run_count = 0
    if logs_path.exists():
        try:
            run_count = sum(1 for e in os.scandir(logs_path) if e.is_dir() and _is_run_id(e.name))
        except OSError:
            pass

    return FolderSummary(
        bitrab_path=bd,
        exists=True,
        total_size_bytes=total,
        logs_size_bytes=logs_bytes,
        artifacts_size_bytes=artifact_bytes,
        job_dirs_size_bytes=job_bytes,
        run_count=run_count,
        warn_threshold_bytes=warn_threshold_bytes,
        subdirs=sorted(subdirs),
    )


def list_runs(project_dir: Path) -> list[RunRecord]:
    """Return :class:`RunRecord` objects for every persisted run, newest first.

    Size data comes from the pre-recorded ``meta.json`` — no directory walk needed.
    """
    logs_path = bitrab_dir(project_dir) / _LOGS_DIR
    if not logs_path.exists():
        return []

    records: list[RunRecord] = []
    try:
        for entry in os.scandir(logs_path):
            if not (entry.is_dir() and _is_run_id(entry.name)):
                continue
            run_dir = Path(entry.path)
            meta_file = run_dir / "meta.json"
            try:
                meta: dict[str, Any] = json.loads(meta_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}

            records.append(
                RunRecord(
                    run_id=entry.name,
                    run_dir=run_dir,
                    started_at=float(meta.get("started_at", 0.0)),
                    success=bool(meta.get("success", False)),
                    total_duration_s=float(meta.get("total_duration_s", 0.0)),
                    job_count=int(meta.get("job_count", 0)),
                    size_bytes=int(meta.get("size_bytes", 0)),
                )
            )
    except OSError:
        pass

    records.sort(key=lambda r: r.run_id, reverse=True)
    return records


def prune_runs(project_dir: Path, keep: int) -> list[str]:
    """Delete the oldest run directories, keeping the *keep* most recent.

    Returns list of deleted run IDs.
    """
    runs = list_runs(project_dir)  # newest first
    to_delete = runs[keep:]
    deleted: list[str] = []
    for rec in to_delete:
        try:
            shutil.rmtree(rec.run_dir)
            deleted.append(rec.run_id)
        except OSError:
            pass
    return deleted


def clean_artifacts(project_dir: Path) -> int:
    """Delete ``.bitrab/artifacts/``. Returns bytes freed."""
    artifacts_path = bitrab_dir(project_dir) / _ARTIFACTS_DIR
    if not artifacts_path.exists():
        return 0
    freed = _dir_size_bytes(artifacts_path)
    shutil.rmtree(artifacts_path)
    return freed


def clean_job_dirs(project_dir: Path) -> int:
    """Delete all job working directories (not logs or artifacts). Returns bytes freed."""
    bd = bitrab_dir(project_dir)
    if not bd.exists():
        return 0
    freed = 0
    try:
        for entry in os.scandir(bd):
            if entry.is_dir() and entry.name not in (_LOGS_DIR, _ARTIFACTS_DIR):
                freed += _dir_size_bytes(Path(entry.path))
                shutil.rmtree(entry.path)
    except OSError:
        pass
    return freed


def clean_logs(project_dir: Path) -> int:
    """Delete ``.bitrab/logs/``. Returns bytes freed."""
    logs_path = bitrab_dir(project_dir) / _LOGS_DIR
    if not logs_path.exists():
        return 0
    freed = _dir_size_bytes(logs_path)
    shutil.rmtree(logs_path)
    return freed


def clean_all(project_dir: Path) -> int:
    """Delete everything under ``.bitrab/``. Returns bytes freed."""
    bd = bitrab_dir(project_dir)
    if not bd.exists():
        return 0
    freed = _dir_size_bytes(bd)
    shutil.rmtree(bd)
    return freed


# ---------------------------------------------------------------------------
# Log writing
# ---------------------------------------------------------------------------


def write_run_log(
    project_dir: Path,
    events_json: list[dict[str, Any]],
    summary_text: str,
    meta: dict[str, Any],
) -> Path:
    """Persist a run log to ``.bitrab/logs/<run_id>/``.

    Creates three files:

    * ``meta.json`` – lightweight metadata (success, duration, job count,
      started_at, size_bytes).  Size is computed *after* writing the other
      two files and then patched in.
    * ``events.jsonl`` – one JSON object per line, one event per line.
    * ``summary.txt`` – the human-readable text summary.

    Returns the run directory path.
    """
    run_id = _make_run_id()
    run_dir = bitrab_dir(project_dir) / _LOGS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Write events log (JSONL)
    events_path = run_dir / "events.jsonl"
    with events_path.open("w", encoding="utf-8") as fh:
        for event in events_json:
            fh.write(json.dumps(event, default=str) + "\n")

    # Write text summary
    summary_path = run_dir / "summary.txt"
    summary_path.write_text(summary_text, encoding="utf-8")

    # Compute size and write meta (size_bytes includes events + summary)
    size_bytes = _dir_size_bytes(run_dir)
    meta["size_bytes"] = size_bytes
    meta["run_id"] = run_id
    meta_path = run_dir / "meta.json"
    meta_path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")

    return run_dir


def maybe_warn_size(
    project_dir: Path,
    warn_threshold_bytes: int = _SIZE_WARN_BYTES_DEFAULT,
) -> str | None:
    """Return a warning string if ``.bitrab/`` exceeds *warn_threshold_bytes*, else None."""
    summary = scan_folder(project_dir, warn_threshold_bytes=warn_threshold_bytes)
    if summary.is_large:
        return (
            f"⚠️  .bitrab/ is {summary.total_human} "
            f"(threshold: {_human_size(warn_threshold_bytes)}). "
            "Run 'bitrab folder clean' to free space."
        )
    return None
