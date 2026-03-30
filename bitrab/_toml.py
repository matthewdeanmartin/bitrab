"""TOML helpers with rtoml-first, stdlib fallback chain.

The best available backend is resolved once at import time (same pattern as
``_json.py``), so ``load_file`` is a direct call with no per-invocation
import-chain overhead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Backend resolution — happens once at module import time
# ---------------------------------------------------------------------------

try:
    import rtoml as _rtoml  # type: ignore[import]

    def _load(file_path: Path) -> dict[str, Any]:
        return _rtoml.load(file_path)

except ImportError:
    _rtoml = None  # type: ignore[assignment]

    try:
        import tomllib as _tomllib  # type: ignore[import]

        def _load(file_path: Path) -> dict[str, Any]:  # type: ignore[misc]
            with open(file_path, "rb") as fh:
                return _tomllib.load(fh)

    except ImportError:
        _tomllib = None  # type: ignore[assignment]

        try:
            import tomli as _tomli  # type: ignore[import]

            def _load(file_path: Path) -> dict[str, Any]:  # type: ignore[misc]
                with open(file_path, "rb") as fh:
                    return _tomli.load(fh)

        except ImportError:
            import toml as _toml  # type: ignore[import]

            def _load(file_path: Path) -> dict[str, Any]:  # type: ignore[misc]
                with open(file_path, encoding="utf-8") as fh:
                    return _toml.load(fh)


def load_file(file_path: Path) -> dict[str, Any]:
    """Load a TOML file, preferring rtoml (fast extras) then stdlib/tomli/toml."""
    try:
        return _load(file_path)
    except Exception:
        return {}
