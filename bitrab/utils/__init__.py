"""Shared bitrab utilities."""

from __future__ import annotations

import re

INVALID_PATH_CHARS_RE = re.compile(r'[\\/:*?"<>|]')
INVALID_NAME_CHARS_RE = re.compile(r'[\\/:*?"<>|\s]+')


def sanitize_job_name(name: str, *, for_worktree: bool = False) -> str:
    """Replace filesystem-hostile characters in a job name with underscores.

    Two flavours, kept behaviour-equivalent to the previous duplicates:

    - Default (``for_worktree=False``): strip ``\\/:*?"<>|``. Used by
      ``.bitrab/<job>/`` and ``.bitrab/artifacts/<job>/`` where whitespace
      is tolerated.
    - Worktree (``for_worktree=True``): also collapse whitespace and trim
      leading/trailing underscores; falls back to ``"job"`` if the result
      is empty. Worktree directories are passed to ``git worktree add`` and
      the stricter form avoids quoting headaches.
    """
    if for_worktree:
        cleaned = INVALID_NAME_CHARS_RE.sub("_", name).strip("_")
        return cleaned or "job"
    return INVALID_PATH_CHARS_RE.sub("_", name)
