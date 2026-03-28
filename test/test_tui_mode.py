import argparse
import os
from unittest.mock import patch

from bitrab.tui.ci_mode import is_ci_mode, is_tty, should_use_tui


def test_is_ci_mode():
    with patch.dict(os.environ, {"CI": "true"}):
        assert is_ci_mode() is True

    with patch.dict(os.environ, {"CI": "false"}):
        assert is_ci_mode() is False

    with patch.dict(os.environ, {"CI": "true", "BITRAB_TUI_FORCE": "1"}):
        assert is_ci_mode() is False


def test_is_tty():
    with patch("sys.stdout.isatty", return_value=True):
        assert is_tty() is True

    with patch("sys.stdout.isatty", return_value=False):
        assert is_tty() is False


def test_should_use_tui():
    args = argparse.Namespace(no_tui=False)

    # Normal interactive terminal
    with patch("bitrab.tui.ci_mode.is_ci_mode", return_value=False):
        with patch("bitrab.tui.ci_mode.is_tty", return_value=True):
            assert should_use_tui(args) is True

    # --no-tui flag
    args.no_tui = True
    assert should_use_tui(args) is False
    args.no_tui = False

    # CI mode
    with patch("bitrab.tui.ci_mode.is_ci_mode", return_value=True):
        assert should_use_tui(args) is False

    # Not a TTY
    with patch("bitrab.tui.ci_mode.is_ci_mode", return_value=False):
        with patch("bitrab.tui.ci_mode.is_tty", return_value=False):
            assert should_use_tui(args) is False
