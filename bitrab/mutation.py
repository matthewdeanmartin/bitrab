"""Filesystem mutation detection for job execution.

Detects files/directories modified by a job and warns or fails if unexpected
mutations are found.  Tools like mypy and pytest write to cache directories —
these are whitelisted by default and can be extended via ``pyproject.toml``.

Configuration (``[tool.bitrab]`` in ``pyproject.toml``)::

    [tool.bitrab]
    warn_on_mutation = true

    [tool.bitrab.mutation]
    # Additional glob patterns that are safe to ignore (on top of defaults).
    # These are relative to the project root.
    whitelist = [
        ".mypy_cache/**",
        ".pytest_cache/**",
        ".ruff_cache/**",
        "**/__pycache__/**",
        ".bitrab/**",
    ]
"""

from __future__ import annotations

import fnmatch
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bitrab._toml import load_file as load_toml_file

# Cache for parsed pyproject.toml: maps (path, mtime) -> parsed dict
_TOML_CACHE: dict[tuple[str, float], dict[str, Any]] = {}

# Patterns always considered safe regardless of user config.
# Relative to project root, using forward-slash glob syntax.
_BUILTIN_WHITELIST: list[str] = [
    ".mypy_cache/**",
    ".pytest_cache/**",
    ".ruff_cache/**",
    ".hypothesis/**",
    "**/__pycache__/**",
    ".bitrab/**",
    "*.pyc",
    "**/*.pyc",
    ".coverage",
    "coverage.xml",
    "htmlcov/**",
    ".tox/**",
    ".nox/**",
    "dist/**",
    "build/**",
    "*.egg-info/**",
]


@dataclass
class MutationConfig:
    """Resolved mutation-detection configuration."""

    enabled: bool = False
    whitelist: list[str] = field(default_factory=list)

    @property
    def effective_whitelist(self) -> list[str]:
        """Return builtin patterns plus user-supplied ones."""
        return _BUILTIN_WHITELIST + self.whitelist


@dataclass
class ParallelBackendConfig:
    """Configuration for the parallel execution backend.

    Attributes:
        backend: ``"process"`` (default) or ``"thread"``.
            - ``"process"``: uses ``ProcessPoolExecutor`` (full isolation, GIL-free).
            - ``"thread"``: uses ``ThreadPoolExecutor`` (lighter weight, shared memory,
              but subject to the GIL for CPU-bound work).
    """

    backend: str = "process"  # "process" | "thread"

    def __post_init__(self) -> None:
        if self.backend not in ("process", "thread"):
            self.backend = "process"


def _load_toml(file_path: Path) -> dict[str, Any]:
    """Load a TOML file, caching the result by path + mtime."""
    try:
        mtime = file_path.stat().st_mtime
    except OSError:
        return load_toml_file(file_path)
    key = (str(file_path), mtime)
    if key not in _TOML_CACHE:
        _TOML_CACHE[key] = load_toml_file(file_path)
    return _TOML_CACHE[key]


def load_parallel_config(project_dir: Path) -> ParallelBackendConfig:
    """Read ``[tool.bitrab]`` from ``pyproject.toml`` and return parallel config.

    Returns the default (process) config if ``pyproject.toml`` is missing or
    the section is absent.
    """
    pyproject = project_dir / "pyproject.toml"
    if not pyproject.exists():
        return ParallelBackendConfig()

    data = _load_toml(pyproject)
    bitrab_section: dict[str, Any] = data.get("tool", {}).get("bitrab", {})
    backend = str(bitrab_section.get("parallel_backend", "process")).lower()

    return ParallelBackendConfig(backend=backend)


def load_mutation_config(project_dir: Path) -> MutationConfig:
    """Read ``[tool.bitrab]`` from ``pyproject.toml`` and return a MutationConfig.

    Returns a disabled MutationConfig if ``pyproject.toml`` is missing or the
    section is absent.
    """
    pyproject = project_dir / "pyproject.toml"
    if not pyproject.exists():
        return MutationConfig()

    data = _load_toml(pyproject)
    bitrab_section: dict[str, Any] = data.get("tool", {}).get("bitrab", {})
    enabled: bool = bool(bitrab_section.get("warn_on_mutation", False))
    mutation_section: dict[str, Any] = bitrab_section.get("mutation", {})
    whitelist: list[str] = list(mutation_section.get("whitelist", []))

    return MutationConfig(enabled=enabled, whitelist=whitelist)


def _snapshot(project_dir: Path) -> dict[str, float]:
    """Walk *project_dir* and return a dict of ``{rel_path: mtime}``."""
    snapshot: dict[str, float] = {}
    root = str(project_dir)
    for dirpath, _dirs, files in os.walk(project_dir):
        for fname in files:
            full_str = os.path.join(dirpath, fname)
            try:
                snapshot[os.path.relpath(full_str, root)] = os.stat(full_str).st_mtime
            except OSError:
                pass
    return snapshot


def _is_whitelisted(rel_path: str, patterns: list[str]) -> bool:
    """Return True if *rel_path* matches any whitelist glob pattern."""
    # Normalise to forward-slash for consistent matching on all platforms
    norm = rel_path.replace(os.sep, "/")
    for pattern in patterns:
        if fnmatch.fnmatch(norm, pattern):
            return True
        # Also match the path as a prefix so "dir/**" catches "dir/sub/file"
        # by checking each component prefix.
        # fnmatch handles "**" via a simple hack: replace it with "*" for a
        # first-pass check, then fall through to an exact prefix test.
        #
        # For patterns like ".bitrab/**", we also check if the path starts
        # with the prefix before "/**".
        if "**" in pattern:
            prefix = pattern.split("/**")[0]
            if norm == prefix or norm.startswith(prefix + "/"):
                return True
    return False


@dataclass
class MutationSnapshot:
    """Holds a pre-job filesystem snapshot for later comparison."""

    project_dir: Path
    config: MutationConfig
    _before: dict[str, float] = field(default_factory=dict, init=False)
    # small grace period so timestamps written right at job-end aren't missed
    _taken_at: float = field(default_factory=time.monotonic, init=False)

    def take(self) -> None:
        """Capture the current state of the project directory."""
        self._before = _snapshot(self.project_dir)
        self._taken_at = time.monotonic()

    def mutations(self) -> list[str]:
        """Compare the filesystem against the snapshot.

        Returns a sorted list of relative paths that were created or modified
        after the snapshot was taken, excluding whitelisted paths.
        """
        after = _snapshot(self.project_dir)
        whitelist = self.config.effective_whitelist
        changed: list[str] = []

        for rel, mtime in after.items():
            prev = self._before.get(rel)
            if prev is None or mtime > prev:
                if not _is_whitelisted(rel, whitelist):
                    changed.append(rel)

        return sorted(changed)
