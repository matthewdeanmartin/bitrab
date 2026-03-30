"""Run pytest benchmarks with autosave and regression checks."""

from __future__ import annotations

import platform
import sys
from pathlib import Path

import pytest

DEFAULT_REGRESSION_LIMIT = "mean:15%"


def _benchmark_search_roots(root: Path) -> list[Path]:
    impl = platform.python_implementation()
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    matching = sorted(root.glob(f"*{impl}*{version}*"))
    return matching or [root]


def _latest_saved_benchmark_id(root: Path) -> str | None:
    benchmark_files: list[Path] = []
    for search_root in _benchmark_search_roots(root):
        benchmark_files.extend(search_root.rglob("*.json"))
    if not benchmark_files:
        return None

    latest = max(benchmark_files, key=lambda path: path.stat().st_mtime)
    prefix = latest.stem.split("_", 1)[0]
    return prefix if prefix.isdigit() else None


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    baseline = _latest_saved_benchmark_id(Path(".benchmarks"))
    benchmark_args = ["--benchmark-autosave"]

    if baseline is None:
        print("benchmark: no saved baseline found; recording one now")
    else:
        print(
            "benchmark: comparing against saved run "
            f"{baseline} and failing on regressions above {DEFAULT_REGRESSION_LIMIT}"
        )
        benchmark_args.extend(
            [
                f"--benchmark-compare={baseline}",
                f"--benchmark-compare-fail={DEFAULT_REGRESSION_LIMIT}",
            ]
        )

    return pytest.main([*args, *benchmark_args])


if __name__ == "__main__":
    raise SystemExit(main())
