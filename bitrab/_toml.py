"""TOML helpers with a stdlib-first, fast-parser fallback chain."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def load_file(file_path: Path) -> dict[str, Any]:
    """Load a TOML file, preferring stdlib and then optional fast parsers."""
    try:
        try:
            import tomllib  # type: ignore[import]

            with open(file_path, "rb") as fh:
                return tomllib.load(fh)
        except ImportError:
            pass

        try:
            import rtoml  # type: ignore[import]

            return rtoml.load(file_path)
        except ImportError:
            pass

        try:
            import tomli  # type: ignore[import]

            with open(file_path, "rb") as fh:
                return tomli.load(fh)
        except ImportError:
            pass

        import toml  # type: ignore[import]

        with open(file_path, encoding="utf-8") as fh:
            return toml.load(fh)
    except Exception:
        return {}
