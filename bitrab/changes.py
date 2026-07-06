"""Git-aware changed-file discovery and GitLab-style path matching."""

from __future__ import annotations

import logging
import re
import subprocess  # nosec
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from bitrab.mutation import load_bitrab_section

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from bitrab.models.pipeline import PipelineConfig


@dataclass(frozen=True)
class ChangeSet:
    """Changed paths and the baseline used to discover them."""

    files: frozenset[str]
    baseline: str | None
    evaluable: bool


def _git(project_dir: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # nosec
        ["git", "-C", str(project_dir), *args],
        capture_output=True,
        check=False,
    )


def _successful(project_dir: Path, *args: str) -> bytes | None:
    try:
        result = _git(project_dir, *args)
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def _paths(output: bytes) -> set[str]:
    return {raw.decode("utf-8", errors="surrogateescape").replace("\\", "/") for raw in output.split(b"\0") if raw}


def configured_changes_base(project_dir: Path) -> str | None:
    """Read ``[tool.bitrab] changes_base`` when configured."""
    section = load_bitrab_section(project_dir)
    if not section:
        return None
    value = section.get("changes_base")
    return str(value).strip() if value is not None and str(value).strip() else None


def detect_default_branch(project_dir: Path) -> str | None:
    """Detect the default branch using the documented local fallback order."""
    symbolic = _successful(project_dir, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    candidates: list[str] = []
    if symbolic:
        candidates.append(symbolic.decode("utf-8", errors="replace").strip())
    candidates.extend(["origin/main", "origin/master", "main"])
    for candidate in candidates:
        if candidate and _successful(project_dir, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"):
            return candidate
    return None


def resolve_default_baseline(project_dir: Path) -> str | None:
    """Return the merge-base of HEAD and the detected default branch."""
    branch = detect_default_branch(project_dir)
    if branch is None:
        return None
    output = _successful(project_dir, "merge-base", "HEAD", branch)
    if not output:
        return None
    return output.decode("ascii", errors="replace").strip() or None


def discover_changes(project_dir: Path, baseline: str | None = None) -> ChangeSet:
    """Collect committed, staged, unstaged, and untracked non-ignored paths."""
    inside = _successful(project_dir, "rev-parse", "--is-inside-work-tree")
    if inside is None or inside.strip() != b"true":
        return ChangeSet(frozenset(), None, False)

    resolved = baseline or resolve_default_baseline(project_dir)
    if resolved is None:
        return ChangeSet(frozenset(), None, False)
    if _successful(project_dir, "rev-parse", "--verify", "--quiet", f"{resolved}^{{commit}}") is None:
        return ChangeSet(frozenset(), resolved, False)

    commands = [
        ("diff", "--name-only", "-z", resolved, "HEAD"),
        ("diff", "--cached", "--name-only", "-z"),
        ("diff", "--name-only", "-z"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    ]
    files: set[str] = set()
    for command in commands:
        output = _successful(project_dir, *command)
        if output is None:
            return ChangeSet(frozenset(), resolved, False)
        files.update(_paths(output))
    return ChangeSet(frozenset(files), resolved, True)


class ChangeResolver:
    """Cache changed-file sets for the run's default and rule overrides."""

    def __init__(self, project_dir: Path, changes_base: str | None = None):
        self.project_dir = project_dir.resolve()
        self.changes_base = changes_base or configured_changes_base(self.project_dir)
        self._cache: dict[str | None, ChangeSet] = {}
        self._warned: set[str | None] = set()

    def resolve(self, compare_to: str | None = None) -> ChangeSet:
        """Resolve one rule, with ``compare_to`` overriding the run baseline."""
        baseline = compare_to or self.changes_base
        if baseline not in self._cache:
            self._cache[baseline] = discover_changes(self.project_dir, baseline)
        result = self._cache[baseline]
        if not result.evaluable and baseline not in self._warned:
            detail = f" baseline {baseline!r}" if baseline else " a default-branch baseline"
            logger.warning(
                "Could not resolve%s in %s; changes conditions will match so jobs run safely.",
                detail,
                self.project_dir,
            )
            self._warned.add(baseline)
        return result


@lru_cache(maxsize=1024)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    normalized = pattern.replace("\\", "/").removeprefix("./").removeprefix("/")
    pieces = ["^"]
    index = 0
    while index < len(normalized):
        char = normalized[index]
        if char == "*":
            if index + 1 < len(normalized) and normalized[index + 1] == "*":
                index += 2
                if index < len(normalized) and normalized[index] == "/":
                    pieces.append("(?:.*/)?")
                    index += 1
                else:
                    pieces.append(".*")
                continue
            pieces.append("[^/]*")
        elif char == "?":
            pieces.append("[^/]")
        elif char == "[":
            end = normalized.find("]", index + 1)
            if end == -1:
                pieces.append(r"\[")
            else:
                content = normalized[index + 1 : end]
                if content.startswith("!"):
                    content = "^" + content[1:]
                pieces.append("[" + content.replace("\\", r"\\") + "]")
                index = end
        else:
            pieces.append(re.escape(char))
        index += 1
    pieces.append("$")
    return re.compile("".join(pieces))


def _expand_braces(pattern: str) -> list[str]:
    start = pattern.find("{")
    end = pattern.find("}", start + 1)
    if start == -1 or end == -1:
        return [pattern]
    choices = pattern[start + 1 : end].split(",")
    if len(choices) < 2:
        return [pattern]
    return [
        expanded for choice in choices for expanded in _expand_braces(pattern[:start] + choice + pattern[end + 1 :])
    ]


def path_matches(pattern: str, path: str) -> bool:
    """Match one project-relative path with GitLab's slash-aware glob rules."""
    normalized_path = path.replace("\\", "/").removeprefix("./")
    return any(_glob_regex(expanded).match(normalized_path) for expanded in _expand_braces(pattern))


def changes_match(patterns: list[str], change_set: ChangeSet) -> bool:
    """Return true on any intersection, or conservatively when unevaluable."""
    if not change_set.evaluable:
        return True
    return any(path_matches(pattern, path) for pattern in patterns for path in change_set.files)


def select_changed_jobs(pipeline: PipelineConfig, change_set: ChangeSet) -> set[str]:
    """Select jobs affected by changes, including transitive ``needs`` dependents."""
    from bitrab.execution.fingerprint import selection_input_patterns

    selected: set[str] = set()
    for job in pipeline.jobs:
        patterns = selection_input_patterns(job)
        if not patterns or changes_match(patterns, change_set):
            selected.add(job.name)

    dependents: dict[str, set[str]] = {}
    for job in pipeline.jobs:
        for needed in job.needs:
            dependents.setdefault(needed, set()).add(job.name)

    pending = list(selected)
    while pending:
        upstream = pending.pop()
        for dependent in dependents.get(upstream, set()):
            if dependent not in selected:
                selected.add(dependent)
                pending.append(dependent)
    return selected
