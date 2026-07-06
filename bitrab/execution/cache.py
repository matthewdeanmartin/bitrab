"""Local execution of GitLab ``cache:`` semantics.

Before a job's ``before_script`` runs:
  - Each cache entry with policy ``pull-push`` or ``pull`` is restored from
    the store into the job's working directory.  A missing key is a silent
    cache miss (logged at INFO).

After a job's scripts finish:
  - Each entry with policy ``pull-push`` or ``push`` is saved, subject to
    ``when:`` (on_success / on_failure / always) vs. whether the job
    succeeded.

Storage layout (shared filesystem, multiple writers — see sprints/README.md):

    <project>/.bitrab/cache/
        .tmp/<key>-<pid>-<token>/   staging area for in-flight saves
        <key>.lock                  per-key advisory lock file
        <key>/latest                pointer file naming the live generation
        <key>/<generation>/         one complete, immutable snapshot

Saves stage into ``.tmp/`` then atomically rename the staged directory to a
fresh generation and atomically publish it by rewriting the ``latest``
pointer (write temp file + ``os.replace``).  Readers resolve ``latest`` and
copy from that generation while holding the per-key lock, so they can never
observe a half-written cache.  On lock timeout the cache step is skipped
with a warning rather than failing the job.

The store lives under the *project root* (never a worktree) so parallel
worktree jobs share caches.
"""

from __future__ import annotations

import glob
import hashlib
import logging
import os
import re
import shutil
import time
import uuid
from collections.abc import Mapping
from pathlib import Path

from bitrab.models.pipeline import CacheConfig, JobConfig
from bitrab.utils.filelock import FileLock, FileLockTimeout

logger = logging.getLogger(__name__)

# GitLab's default cache key when none is given.
DEFAULT_CACHE_KEY = "default"

# Name of the pointer file that marks the live generation for a key.
LATEST_POINTER = "latest"

# Seconds to wait for the per-key lock before skipping the cache step.
LOCK_TIMEOUT_SECONDS = 30.0

# $VAR or ${VAR} — the two forms GitLab expands inside cache keys.
VARIABLE_RE = re.compile(r"\$(\w+)|\$\{(\w+)\}")

# Characters allowed verbatim in an on-disk cache key directory name.
UNSAFE_KEY_CHARS_RE = re.compile(r"[^A-Za-z0-9_.-]")

# Longest key we store verbatim before switching to the hashed form.
MAX_KEY_LENGTH = 80


def cache_root(project_dir: Path) -> Path:
    """Return the cache store directory for *project_dir*."""
    return project_dir / ".bitrab" / "cache"


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------


def expand_variables(text: str, env: Mapping[str, str]) -> str:
    """Expand ``$VAR`` and ``${VAR}`` references in *text* using *env*.

    Unknown variables expand to the empty string, matching shell behaviour.
    """

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        return env.get(name, "")

    return VARIABLE_RE.sub(replace, text)


def resolve_cache_key(cache: CacheConfig, env: Mapping[str, str], files_dir: Path) -> str:
    """Return the logical cache key for *cache*.

    - ``key: files:`` → SHA-256 over the listed files' contents (missing file
      → empty content), optionally prefixed by ``key: prefix:``.
    - ``key: <string>`` → the string with ``$VAR`` references expanded
      against *env*.
    - No key → GitLab's literal ``default``.
    """
    if cache.key_files:
        hasher = hashlib.sha256()
        for rel in cache.key_files[:2]:
            path = files_dir / rel
            try:
                data = path.read_bytes()
            except OSError:
                data = b""  # missing file → empty content, per GitLab
            hasher.update(data)
            hasher.update(b"\x00")  # separator so (a+b, "") != (a, b)
        digest = hasher.hexdigest()[:16]
        prefix = expand_variables(cache.key_prefix, env) if cache.key_prefix else ""
        return f"{prefix}-{digest}" if prefix else digest

    if cache.key:
        expanded = expand_variables(cache.key, env).strip()
        return expanded or DEFAULT_CACHE_KEY

    return DEFAULT_CACHE_KEY


def sanitize_cache_key(key: str) -> str:
    """Return a filesystem-safe directory name for *key*.

    Keys that contain unsafe characters (path separators, etc.) or exceed
    :data:`MAX_KEY_LENGTH` are turned into ``<cleaned-head>-<sha12>`` so that
    distinct logical keys never collide on disk.
    """
    cleaned = UNSAFE_KEY_CHARS_RE.sub("_", key)
    if cleaned == key and 0 < len(cleaned) <= MAX_KEY_LENGTH:
        return cleaned
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    head = cleaned[:48].strip("_") or "key"
    return f"{head}-{digest}"


# ---------------------------------------------------------------------------
# Store internals (call with the per-key lock held)
# ---------------------------------------------------------------------------


def key_dir(root: Path, sanitized_key: str) -> Path:
    """Return the directory that holds all generations for a key."""
    return root / sanitized_key


def lock_path(root: Path, sanitized_key: str) -> Path:
    """Return the advisory lock file path for a key."""
    return root / f"{sanitized_key}.lock"


def read_latest_generation(root: Path, sanitized_key: str) -> Path | None:
    """Resolve the live generation directory for a key, or None on cache miss."""
    pointer = key_dir(root, sanitized_key) / LATEST_POINTER
    try:
        generation = pointer.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not generation:
        return None
    gen_dir = key_dir(root, sanitized_key) / generation
    return gen_dir if gen_dir.is_dir() else None


def publish_generation(root: Path, sanitized_key: str, staged_dir: Path) -> Path:
    """Atomically publish *staged_dir* as the new live generation for a key.

    Must be called with the per-key lock held.  The staged directory is
    renamed to ``<key>/<generation>/`` (the target never pre-exists, so the
    rename is atomic on Windows too), then the ``latest`` pointer is
    rewritten via temp-file + ``os.replace``.  Superseded generations are
    removed best-effort — safe because both readers and writers hold the
    per-key lock.
    """
    kdir = key_dir(root, sanitized_key)
    kdir.mkdir(parents=True, exist_ok=True)

    generation = f"{time.time_ns():x}-{uuid.uuid4().hex[:8]}"
    gen_dir = kdir / generation
    os.replace(staged_dir, gen_dir)

    pointer_tmp = kdir / f"{LATEST_POINTER}.{uuid.uuid4().hex[:8]}.tmp"
    pointer_tmp.write_text(generation, encoding="utf-8")
    os.replace(pointer_tmp, kdir / LATEST_POINTER)

    # Garbage-collect superseded generations.
    try:
        for entry in os.scandir(kdir):
            if entry.is_dir() and entry.name != generation:
                shutil.rmtree(entry.path, ignore_errors=True)
    except OSError:
        pass

    return gen_dir


def copy_tree_into(src_root: Path, target_dir: Path) -> int:
    """Copy every file under *src_root* into *target_dir*, preserving relative paths.

    Returns the number of files copied.
    """
    copied = 0
    for dirpath, dirnames, filenames in os.walk(src_root):
        rel_dir = Path(dirpath).relative_to(src_root)
        for dname in dirnames:
            (target_dir / rel_dir / dname).mkdir(parents=True, exist_ok=True)
        for fname in filenames:
            src = Path(dirpath) / fname
            dest = target_dir / rel_dir / fname
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            copied += 1
    return copied


def stage_matched_paths(cache: CacheConfig, source_dir: Path, staged_dir: Path) -> int:
    """Copy paths matched by *cache.paths* from *source_dir* into *staged_dir*.

    Glob semantics mirror :func:`bitrab.execution.artifacts.collect_artifacts`.
    Returns the number of top-level matches staged.
    """
    matched = 0
    for pattern in cache.paths:
        full_pattern = os.path.join(str(source_dir), pattern)
        for abs_path in glob.glob(full_pattern, recursive=True):
            src = Path(abs_path)
            if not src.exists():
                continue
            rel_path = os.path.relpath(abs_path, str(source_dir))
            if rel_path.startswith(".."):
                logger.warning("Cache path %r escapes the project directory; skipped.", pattern)
                continue
            dest = staged_dir / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)
            else:
                shutil.copy2(src, dest)
            matched += 1
    return matched


# ---------------------------------------------------------------------------
# Public restore / save API
# ---------------------------------------------------------------------------


def restore_cache_entry(
    cache: CacheConfig,
    key: str,
    root: Path,
    target_dir: Path,
    lock_timeout: float = LOCK_TIMEOUT_SECONDS,
) -> bool:
    """Restore one cache entry into *target_dir*. Returns True if files landed.

    A missing key is a silent cache miss.  A lock timeout logs a warning and
    skips the restore rather than failing the job.
    """
    sanitized = sanitize_cache_key(key)
    try:
        with FileLock(lock_path(root, sanitized), timeout=lock_timeout):
            gen_dir = read_latest_generation(root, sanitized)
            if gen_dir is None:
                logger.info("Cache miss for key %r — nothing to restore.", key)
                return False
            copied = copy_tree_into(gen_dir, target_dir)
            logger.info("Restored cache key %r (%d file(s)).", key, copied)
            return copied > 0
    except FileLockTimeout:
        logger.warning("Timed out waiting for cache lock on key %r — skipping restore.", key)
        return False


def save_cache_entry(
    cache: CacheConfig,
    key: str,
    root: Path,
    source_dir: Path,
    lock_timeout: float = LOCK_TIMEOUT_SECONDS,
) -> bool:
    """Save one cache entry from *source_dir*. Returns True if a generation published.

    All content is staged under ``.tmp/`` first; only the atomic
    rename + pointer rewrite (under the per-key lock) makes it visible.
    """
    sanitized = sanitize_cache_key(key)
    staged_dir = root / ".tmp" / f"{sanitized}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    staged_dir.mkdir(parents=True, exist_ok=True)

    try:
        matched = stage_matched_paths(cache, source_dir, staged_dir)
        if matched == 0:
            logger.info("Cache key %r: no paths matched — nothing to save.", key)
            return False

        try:
            with FileLock(lock_path(root, sanitized), timeout=lock_timeout):
                publish_generation(root, sanitized, staged_dir)
                logger.info("Saved cache key %r (%d match(es)).", key, matched)
                return True
        except FileLockTimeout:
            logger.warning("Timed out waiting for cache lock on key %r — skipping save.", key)
            return False
    finally:
        if staged_dir.exists():
            shutil.rmtree(staged_dir, ignore_errors=True)


def restore_caches(
    job: JobConfig,
    root: Path,
    target_dir: Path,
    env: Mapping[str, str],
    lock_timeout: float = LOCK_TIMEOUT_SECONDS,
) -> None:
    """Restore every restorable cache entry of *job* into *target_dir*.

    Entries with ``policy: push`` are save-only and skipped here.
    """
    for cache in job.cache:
        if cache.policy == "push":
            continue
        key = resolve_cache_key(cache, env, target_dir)
        restore_cache_entry(cache, key, root, target_dir, lock_timeout=lock_timeout)


def save_caches(
    job: JobConfig,
    root: Path,
    source_dir: Path,
    env: Mapping[str, str],
    succeeded: bool,
    lock_timeout: float = LOCK_TIMEOUT_SECONDS,
) -> None:
    """Save every saveable cache entry of *job* from *source_dir*.

    Entries with ``policy: pull`` are restore-only and skipped.  ``when:``
    gates the save on the job outcome:
    - ``on_success``: save only if *succeeded*
    - ``on_failure``: save only if the job failed
    - ``always``: save regardless
    """
    for cache in job.cache:
        if cache.policy == "pull":
            continue
        if cache.when == "on_success" and not succeeded:
            continue
        if cache.when == "on_failure" and succeeded:
            continue
        key = resolve_cache_key(cache, env, source_dir)
        save_cache_entry(cache, key, root, source_dir, lock_timeout=lock_timeout)
