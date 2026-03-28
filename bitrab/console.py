from __future__ import annotations

import sys
from typing import Any


def configure_stdio() -> None:
    """Best-effort UTF-8 stdio configuration for Windows consoles."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (AttributeError, OSError, TypeError, ValueError):
                continue


def safe_print(
    *values: object,
    sep: str = " ",
    end: str = "\n",
    file: Any = None,
    flush: bool = False,
) -> None:
    """Print text, degrading safely when the target stream cannot encode Unicode."""
    target = sys.stdout if file is None else file
    text = sep.join(str(value) for value in values) + end

    try:
        target.write(text)
    except UnicodeEncodeError:
        encoding = getattr(target, "encoding", None) or "utf-8"
        safe_text = text.encode(encoding, errors="backslashreplace").decode(encoding)
        target.write(safe_text)

    if flush:
        target.flush()
