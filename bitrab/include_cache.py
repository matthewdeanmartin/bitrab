"""Short-lived, multi-writer-safe cache for remote include responses."""

from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from pathlib import Path

from bitrab.utils.filelock import FileLock, FileLockTimeout

DEFAULT_TTL_SECONDS = 600.0
logger = logging.getLogger(__name__)


def cache_root(project_dir: Path) -> Path:
    """Return the transparent remote-include cache directory."""
    return project_dir.resolve() / ".bitrab" / "include-cache"


def cache_key(url: str) -> str:
    """Return the stable URL cache key."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def payload_path(project_dir: Path, url: str) -> Path:
    """Return the cached payload path for *url*."""
    return cache_root(project_dir) / f"{cache_key(url)}.yml"


def lock_path(project_dir: Path, url: str) -> Path:
    """Return the per-URL cache lock path."""
    return cache_root(project_dir) / f"{cache_key(url)}.lock"


def read_cached(project_dir: Path, url: str, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> bytes | None:
    """Return a fresh cached response, or ``None`` on miss/expiry/read failure."""
    path = payload_path(project_dir, url)
    if not path.is_file():
        return None
    try:
        with FileLock(lock_path(project_dir, url)):
            if time.time() - path.stat().st_mtime > ttl_seconds:
                return None
            return path.read_bytes()
    except (OSError, FileLockTimeout):
        return None


def write_cached(project_dir: Path, url: str, data: bytes) -> None:
    """Publish a cached response atomically under its per-URL lock."""
    path = payload_path(project_dir, url)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        try:
            with FileLock(lock_path(project_dir, url)):
                temporary.write_bytes(data)
                os.replace(temporary, path)
        except (OSError, FileLockTimeout) as exc:
            logger.warning("Could not update remote include cache for %s: %s", url, exc)
    finally:
        temporary.unlink(missing_ok=True)


def discard_cached(project_dir: Path, url: str) -> None:
    """Remove a corrupt cache entry under its lock."""
    path = payload_path(project_dir, url)
    try:
        with FileLock(lock_path(project_dir, url)):
            path.unlink(missing_ok=True)
    except (OSError, FileLockTimeout):
        pass
