import sys
# emoji support
sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
__all__ =[
    "run"
]

from bitrab.cli import run