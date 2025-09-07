import sys

from bitrab.cli import main

__all__ = ["main"]

# emoji support
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
