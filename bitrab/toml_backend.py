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
    import rtoml as rtoml_backend  # type: ignore[import]

    def load(file_path: Path) -> dict[str, Any]:
        return rtoml_backend.load(file_path)

except ImportError:
    rtoml_backend = None  # type: ignore[assignment]

    try:
        import tomllib as tomllib_backend  # type: ignore[import]

        def load(file_path: Path) -> dict[str, Any]:  # type: ignore[misc]
            with open(file_path, "rb") as fh:
                return tomllib_backend.load(fh)

    except ImportError:
        tomllib_backend = None  # type: ignore[assignment]

        try:
            import tomli as tomli_backend  # type: ignore[import]

            def load(file_path: Path) -> dict[str, Any]:  # type: ignore[misc]
                with open(file_path, "rb") as fh:
                    return tomli_backend.load(fh)

        except ImportError:
            import toml as toml_lib  # type: ignore[import]

            def load(file_path: Path) -> dict[str, Any]:  # type: ignore[misc]
                with open(file_path, encoding="utf-8") as fh:
                    return toml_lib.load(fh)


def load_file(file_path: Path) -> dict[str, Any]:
    """Load a TOML file, preferring rtoml (fast extras) then stdlib/tomli/toml."""
    try:
        return load(file_path)
    except Exception:
        return {}
