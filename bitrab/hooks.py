"""Install and remove the managed bitrab pre-push hook block."""

from __future__ import annotations

import os
import re
import stat
import subprocess  # nosec
import uuid
from dataclasses import dataclass
from pathlib import Path

from bitrab.exceptions import GitlabRunnerError

START_MARKER = "# >>> bitrab pre-push >>>"
END_MARKER = "# <<< bitrab pre-push <<<"


@dataclass(frozen=True)
class HookResult:
    """Result of a hook installation or removal."""

    path: Path
    action: str


def _git_path(project_dir: Path, *args: str) -> str:
    try:
        result = subprocess.run(  # nosec
            ["git", "-C", str(project_dir), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise GitlabRunnerError("git is required to install the pre-push hook") from exc
    if result.returncode != 0:
        raise GitlabRunnerError(
            f"Not a git repository: {project_dir} ({result.stderr.strip() or result.stdout.strip()})"
        )
    return result.stdout.strip()


def pre_push_path(project_dir: Path) -> Path:
    """Resolve the effective pre-push path, including ``core.hooksPath``."""
    raw = _git_path(project_dir, "rev-parse", "--git-path", "hooks/pre-push")
    path = Path(raw)
    return path if path.is_absolute() else (project_dir / path).resolve()


def _managed_block(created_file: bool) -> str:
    created = "yes" if created_file else "no"
    return f"""{START_MARKER}
# bitrab-created-hook: {created}
# Runs affected jobs before objects leave this machine.
# Skip once with: git push --no-verify
# Skip via environment with: BITRAB_SKIP_HOOK=1 git push
if [ "${{BITRAB_SKIP_HOOK:-0}}" != "1" ]; then
    bitrab run --changed --incremental --no-tui || exit $?
fi
{END_MARKER}"""


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _is_shell_hook(content: str) -> bool:
    first_line = content.splitlines()[0] if content.splitlines() else ""
    return first_line.startswith("#!") and bool(re.search(r"(?:^|[/\s])(?:ba|da|k|z)?sh(?:\s|$)", first_line.lower()))


def install_pre_push_hook(project_dir: Path) -> HookResult:
    """Create the hook or append an idempotent managed block to a shell hook."""
    path = pre_push_path(project_dir)
    if path.exists():
        content = path.read_text(encoding="utf-8")
        if START_MARKER in content and END_MARKER in content:
            return HookResult(path, "unchanged")
        if START_MARKER in content or END_MARKER in content:
            raise GitlabRunnerError(f"Refusing to edit malformed bitrab markers in {path}")
        if not _is_shell_hook(content):
            raise GitlabRunnerError(
                f"Refusing to modify non-shell pre-push hook {path}. Chain bitrab manually with: "
                "bitrab run --changed --incremental --no-tui"
            )
        updated = content.rstrip("\r\n") + "\n\n" + _managed_block(False) + "\n"
        action = "chained"
    else:
        updated = "#!/bin/sh\n\n" + _managed_block(True) + "\n"
        action = "installed"
    _atomic_write(path, updated)
    try:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError as exc:
        raise GitlabRunnerError(f"Installed {path}, but could not make it executable: {exc}") from exc
    return HookResult(path, action)


def uninstall_pre_push_hook(project_dir: Path) -> HookResult:
    """Remove exactly the managed block, deleting files created by bitrab."""
    path = pre_push_path(project_dir)
    if not path.exists():
        return HookResult(path, "absent")
    content = path.read_text(encoding="utf-8")
    start = content.find(START_MARKER)
    end = content.find(END_MARKER)
    if start == -1 and end == -1:
        return HookResult(path, "absent")
    if start == -1 or end == -1 or end < start:
        raise GitlabRunnerError(f"Refusing to edit malformed bitrab markers in {path}")
    end += len(END_MARKER)
    managed = content[start:end]
    outside = (content[:start] + content[end:]).strip()
    if "# bitrab-created-hook: yes" in managed and outside == "#!/bin/sh":
        path.unlink()
        return HookResult(path, "removed")
    updated = content[:start].rstrip("\r\n") + content[end:]
    if updated:
        updated = updated.rstrip("\r\n") + "\n"
    _atomic_write(path, updated)
    return HookResult(path, "unchained")
