"""Display mode detection for bitrab.

Determines whether to use TUI, plain streaming, or CI file-based output.
"""

from __future__ import annotations

import argparse
import os
import sys


def is_ci_mode() -> bool:
    """True when running inside a real CI system (not bitrab's own CI injection).

    Checks the host process CI env var (not the child process injection in variables.py).
    The BITRAB_TUI_FORCE env var overrides this for testing/debugging.
    """
    return os.getenv("CI") == "true" and not os.getenv("BITRAB_TUI_FORCE")


def is_tty() -> bool:
    """True when stdout is an interactive terminal."""
    return sys.stdout.isatty()


def should_use_tui(args: argparse.Namespace) -> bool:
    """Return True if the Textual TUI should be used for this run.

    Priority (first match wins):
    1. --no-tui flag → plain streaming
    2. CI=true env var → CI file mode (no TUI)
    3. stdout not a tty → plain streaming
    4. Otherwise → TUI
    """
    if getattr(args, "no_tui", False):
        return False
    if is_ci_mode():
        return False
    if not is_tty():
        return False
    return True
