"""Job fingerprint memoization for ``bitrab run --incremental``.

A job's *fingerprint* is a SHA-256 digest over a canonical JSON encoding of
everything bitrab can see that feeds the job:

1. The resolved ``before_script`` + ``script`` + ``after_script``.
2. The job's ``variables:`` (resolved values as they will be exported).
3. The *values* of environment variables the user declared in
   ``[tool.bitrab] fingerprint_env`` in ``pyproject.toml`` — an explicit salt
   for shared-environment inputs (toolchain paths, compiler versions, ...).
4. A content digest of the job's input files.  Sources, in order of preference:
   - ``variables: BITRAB_FINGERPRINT_PATHS`` (comma-separated globs);
   - ``cache: key: files:`` entries;
   - fallback: all git-tracked files via ``git ls-files -s`` (git's own blob
     hashes — no re-hashing of file contents) plus a hash of ``git diff`` to
     capture dirty working-tree state.  Outside a git repo the file input is a
     constant marker — only scripts/variables/declared paths are fingerprinted.
5. The fingerprints of all jobs this job ``needs:``/depends on, so an upstream
   change transitively invalidates downstream jobs.
6. The bitrab version plus :data:`FINGERPRINT_SCHEMA_VERSION` so format changes
   invalidate cleanly.

**What the fingerprint cannot see:** the outside world.  Network resources,
system packages, and tool upgrades outside the repository do not change the
fingerprint.  ``--refresh`` and ``bitrab clean --what fingerprints`` are the
escape hatches; ``--incremental`` is always opt-in.

Storage layout (shared filesystem, multiple writers — see sprints/README.md):

    <project>/.bitrab/fingerprints/
        <sanitized-job-name>.json   {"fingerprint", "status", "completed_at", "bitrab"}
        <sanitized-job-name>.lock   per-job advisory lock file

Only *successful* completions are recorded.  Writes go to a temp file and are
published atomically via ``os.replace`` under the per-job lock.  A missing or
corrupt record is a miss, never an error.  The store lives under the *project
root* (never a worktree) so parallel worktree jobs share one store.
"""

from __future__ import annotations

import datetime
import glob
import hashlib
import json
import logging
import os
import subprocess  # nosec
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from bitrab.__about__ import __version__
from bitrab.execution.artifacts import DOTENV_STORE, artifact_dir
from bitrab.models.pipeline import JobConfig, PipelineConfig
from bitrab.utils import sanitize_job_name
from bitrab.utils.filelock import FileLock, FileLockTimeout

logger = logging.getLogger(__name__)

# Bumping this invalidates every recorded fingerprint (rule 6).
FINGERPRINT_SCHEMA_VERSION = 1

# Job variable holding comma-separated glob overrides for input files (rule 4).
FINGERPRINT_PATHS_VARIABLE = "BITRAB_FINGERPRINT_PATHS"

# Seconds to wait for the per-job lock before treating the step as a miss/skip.
LOCK_TIMEOUT_SECONDS = 30.0

# File-input marker used when the project is not a git repository and no
# explicit input paths are declared.
NO_GIT_MARKER = "no-git"


def fingerprint_root(project_dir: Path) -> Path:
    """Return the fingerprint store directory for *project_dir*."""
    return project_dir / ".bitrab" / "fingerprints"


def record_path(root: Path, job_name: str) -> Path:
    """Return the JSON record path for a job."""
    return root / f"{sanitize_job_name(job_name)}.json"


def lock_path(root: Path, job_name: str) -> Path:
    """Return the advisory lock file path for a job."""
    return root / f"{sanitize_job_name(job_name)}.lock"


def canonical_json(payload: dict) -> str:
    """Return a canonical (sorted-keys, compact) JSON encoding of *payload*."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


# ---------------------------------------------------------------------------
# Store read / write
# ---------------------------------------------------------------------------


def read_record(root: Path, job_name: str, lock_timeout: float = LOCK_TIMEOUT_SECONDS) -> dict | None:
    """Read the recorded fingerprint for a job, or None on any kind of miss.

    A missing file, corrupt JSON, or lock timeout all return None — a miss is
    never an error.  The existence check happens before locking so read-only
    paths (dry runs, first runs) never create the store directory.
    """
    path = record_path(root, job_name)
    if not path.is_file():
        return None
    try:
        with FileLock(lock_path(root, job_name), timeout=lock_timeout):
            data = json.loads(path.read_text(encoding="utf-8"))
    except FileLockTimeout:
        logger.warning("Timed out waiting for fingerprint lock on job %r — treating as a miss.", job_name)
        return None
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_record(
    root: Path,
    job_name: str,
    fingerprint: str,
    lock_timeout: float = LOCK_TIMEOUT_SECONDS,
) -> bool:
    """Atomically record a successful completion for a job.

    Writes a temp file then publishes it via ``os.replace`` while holding the
    per-job lock.  Returns True if the record was published; a lock timeout
    logs a warning and skips the write rather than failing the job.
    """
    record = {
        "fingerprint": fingerprint,
        "status": "success",
        "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "bitrab": __version__,
    }
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f"{sanitize_job_name(job_name)}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with FileLock(lock_path(root, job_name), timeout=lock_timeout):
            tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
            os.replace(tmp, record_path(root, job_name))
            return True
    except FileLockTimeout:
        logger.warning("Timed out waiting for fingerprint lock on job %r — skipping record.", job_name)
        return False
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Input-file digests (rule 4)
# ---------------------------------------------------------------------------


def hash_path_globs(project_dir: Path, patterns: list[str]) -> str:
    """Digest the contents of every file matched by *patterns* under *project_dir*.

    Directories match recursively.  Files are hashed in sorted relative-path
    order so the result is deterministic; an unreadable file hashes as empty.
    """
    files: set[Path] = set()
    for pattern in patterns:
        full_pattern = os.path.join(str(project_dir), pattern)
        for abs_path in glob.glob(full_pattern, recursive=True):
            path = Path(abs_path)
            if path.is_dir():
                for dirpath, _dirnames, filenames in os.walk(path):
                    files.update(Path(dirpath) / fname for fname in filenames)
            elif path.is_file():
                files.add(path)

    hasher = hashlib.sha256()
    for path in sorted(files, key=lambda p: os.path.relpath(p, project_dir).replace(os.sep, "/")):
        rel = os.path.relpath(path, project_dir).replace(os.sep, "/")
        try:
            data = path.read_bytes()
        except OSError:
            data = b""
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(hashlib.sha256(data).digest())
        hasher.update(b"\x00")
    return hasher.hexdigest()


def hash_listed_files(project_dir: Path, rel_files: list[str]) -> str:
    """Digest the contents of the listed files (missing file → empty content)."""
    hasher = hashlib.sha256()
    for rel in rel_files:
        try:
            data = (project_dir / rel).read_bytes()
        except OSError:
            data = b""
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(hashlib.sha256(data).digest())
        hasher.update(b"\x00")
    return hasher.hexdigest()


def git_tree_digest(project_dir: Path) -> str:
    """Digest all git-tracked files plus dirty working-tree state.

    Uses ``git ls-files -s`` (mode + blob hash + path per entry — git already
    stores content hashes, so nothing is re-hashed) and ``git diff`` (index vs.
    working tree) to capture unstaged edits.  Returns :data:`NO_GIT_MARKER`
    when git is unavailable or the directory is not a repository.
    """
    try:
        ls_files = subprocess.run(  # nosec
            ["git", "-C", str(project_dir), "ls-files", "-s", "-z"],
            capture_output=True,
            check=True,
        )
        diff = subprocess.run(  # nosec
            ["git", "-C", str(project_dir), "diff"],
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        logger.warning(
            "Project %s is not a git repository — file inputs are excluded from job "
            "fingerprints (declare BITRAB_FINGERPRINT_PATHS to include them).",
            project_dir,
        )
        return NO_GIT_MARKER
    hasher = hashlib.sha256()
    hasher.update(ls_files.stdout)
    hasher.update(b"\x00")
    hasher.update(diff.stdout)
    return hasher.hexdigest()


def load_fingerprint_env_names(project_dir: Path) -> list[str]:
    """Return the ``[tool.bitrab] fingerprint_env`` names from ``pyproject.toml``."""
    from bitrab.mutation import load_bitrab_section

    bitrab_section = load_bitrab_section(project_dir)
    if bitrab_section is None:
        return []
    raw = bitrab_section.get("fingerprint_env", [])
    if not isinstance(raw, list):
        return []
    return [str(name) for name in raw]


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


@dataclass
class FingerprintDecision:
    """Outcome of a fingerprint check for one job."""

    fingerprint: str
    hit: bool
    reason: str


@dataclass
class FingerprintManager:
    """Computes, checks, and records job fingerprints for one pipeline run.

    Call :meth:`prepare` with the pipeline before any job runs so the
    dependency map (rule 5) can be built deterministically, then :meth:`check`
    before each job and :meth:`record` after each *successful* one.  All
    fingerprints are computed from the pre-run state of the project and
    memoized per job name, so transitive dependency hashing is cheap and
    order-independent.

    Attributes:
        project_dir: The original project root (never a worktree).
        refresh: When True every check reports a miss (jobs run) but new
            fingerprints are still recorded.
        lock_timeout: Seconds to wait for per-job store locks.
    """

    project_dir: Path
    refresh: bool = False
    lock_timeout: float = LOCK_TIMEOUT_SECONDS
    jobs_by_name: dict[str, JobConfig] = field(default_factory=dict, init=False)
    upstream: dict[str, list[str]] = field(default_factory=dict, init=False)
    computed: dict[str, str] = field(default_factory=dict, init=False)
    env_names: list[str] | None = field(default=None, init=False)
    git_digest: str | None = field(default=None, init=False)

    @property
    def root(self) -> Path:
        """The fingerprint store directory."""
        return fingerprint_root(self.project_dir)

    def prepare(self, pipeline: PipelineConfig) -> None:
        """Index jobs and build the upstream-dependency map for *pipeline*.

        Upstream jobs are the explicit ``needs:`` plus ``dependencies:`` — or,
        when ``dependencies:`` is omitted (GitLab's inherit-all default), every
        job in a strictly earlier stage.  The map is derived purely from the
        configuration so it is identical across serial, parallel, stage, and
        DAG execution.
        """
        self.jobs_by_name = {job.name: job for job in pipeline.jobs}
        self.upstream = {}
        self.computed = {}
        stage_index = {stage: i for i, stage in enumerate(pipeline.stages)}
        for job in pipeline.jobs:
            names: set[str] = set(job.needs)
            if job.dependencies is not None:
                names.update(job.dependencies)
            else:
                own_index = stage_index.get(job.stage, len(pipeline.stages))
                names.update(
                    other.name
                    for other in pipeline.jobs
                    if stage_index.get(other.stage, len(pipeline.stages)) < own_index
                )
            names.discard(job.name)
            self.upstream[job.name] = sorted(names)

    def files_digest(self, job: JobConfig) -> str:
        """Return the input-file digest for *job* (rule 4 precedence)."""
        paths_var = job.variables.get(FINGERPRINT_PATHS_VARIABLE, "")
        patterns = [p.strip() for p in paths_var.split(",") if p.strip()]
        if patterns:
            return hash_path_globs(self.project_dir, patterns)

        key_files = [rel for cache in job.cache for rel in cache.key_files]
        if key_files:
            return hash_listed_files(self.project_dir, key_files)

        if self.git_digest is None:
            self.git_digest = git_tree_digest(self.project_dir)
        return self.git_digest

    def fingerprint_env_values(self) -> dict[str, str]:
        """Return the declared ``fingerprint_env`` names mapped to their values."""
        if self.env_names is None:
            self.env_names = load_fingerprint_env_names(self.project_dir)
        return {name: os.environ.get(name, "") for name in self.env_names}

    def fingerprint_for(self, job_name: str, chain: frozenset[str] = frozenset()) -> str:
        """Return the memoized fingerprint for *job_name*, computing it if needed.

        Unknown job names (e.g. a ``needs:`` target removed by ``--jobs``
        filtering) and dependency cycles contribute deterministic markers
        instead of raising, so a fingerprint can always be computed.
        """
        if job_name in self.computed:
            return self.computed[job_name]
        if job_name in chain:
            return f"cycle:{job_name}"
        job = self.jobs_by_name.get(job_name)
        if job is None:
            return f"missing:{job_name}"

        chain = chain | {job_name}
        payload = {
            "schema": FINGERPRINT_SCHEMA_VERSION,
            "bitrab": __version__,
            "scripts": {
                "before_script": list(job.before_script),
                "script": list(job.script),
                "after_script": list(job.after_script),
            },
            "variables": dict(job.variables),
            "fingerprint_env": self.fingerprint_env_values(),
            "files": self.files_digest(job),
            "needs": {dep: self.fingerprint_for(dep, chain) for dep in self.upstream.get(job_name, [])},
        }
        digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        self.computed[job_name] = digest
        return digest

    def check(self, job: JobConfig) -> FingerprintDecision:
        """Decide whether *job* can be skipped as memoized.

        A hit requires a recorded ``success`` with an identical fingerprint
        *and* the job's previously collected outputs to still exist in the
        artifact store (so downstream injection keeps working).  ``--refresh``
        always reports a miss so the job runs, but the computed fingerprint is
        cached for the post-run :meth:`record`.
        """
        fingerprint = self.fingerprint_for(job.name)
        if self.refresh:
            return FingerprintDecision(fingerprint=fingerprint, hit=False, reason="refresh")

        record = read_record(self.root, job.name, lock_timeout=self.lock_timeout)
        if record is None:
            return FingerprintDecision(fingerprint=fingerprint, hit=False, reason="no-record")
        if record.get("status") != "success" or record.get("fingerprint") != fingerprint:
            return FingerprintDecision(fingerprint=fingerprint, hit=False, reason="changed")
        if job.artifacts_paths and not artifact_dir(self.project_dir, job.name).is_dir():
            return FingerprintDecision(fingerprint=fingerprint, hit=False, reason="artifacts-missing")
        if job.artifacts_dotenv:
            dotenv_store = self.project_dir / DOTENV_STORE.format(job_name=sanitize_job_name(job.name))
            if not dotenv_store.is_file():
                return FingerprintDecision(fingerprint=fingerprint, hit=False, reason="dotenv-missing")
        return FingerprintDecision(fingerprint=fingerprint, hit=True, reason="match")

    def record(self, job: JobConfig) -> bool:
        """Record a successful completion of *job* under its pre-run fingerprint."""
        fingerprint = self.fingerprint_for(job.name)
        return write_record(self.root, job.name, fingerprint, lock_timeout=self.lock_timeout)
