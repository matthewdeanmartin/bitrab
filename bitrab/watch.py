"""D4: Watch mode — re-run the pipeline when CI config files change."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from bitrab.config.loader import ConfigurationLoader
from bitrab.console import safe_print

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 1.0  # coalesce rapid saves within this window


def collect_watched_paths(config_path: Path) -> set[Path]:
    """Return the set of local files the config depends on (excluding itself).

    Args:
        config_path: Resolved absolute path to the root CI config file.

    Returns:
        Set of resolved absolute Paths for all transitively included local files.
    """
    loader = ConfigurationLoader(base_path=config_path.parent)
    return loader.collect_include_paths(config_path)


class PipelineRerunHandler(FileSystemEventHandler):
    """Watchdog handler that re-runs the pipeline on relevant file changes."""

    def __init__(self, runner_fn: Any, watched_paths: set[Path]) -> None:
        super().__init__()
        self.runner_fn = runner_fn
        self.watched = {str(p) for p in watched_paths}
        self.last_triggered = 0.0

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src_path = event.src_path
        if isinstance(src_path, bytes):
            src_path = src_path.decode("utf-8")
        if str(Path(src_path).resolve()) not in self.watched:
            return
        now = time.monotonic()
        if now - self.last_triggered < DEBOUNCE_SECONDS:
            return
        self.last_triggered = now
        safe_print("\n[watch] File changed — re-running pipeline...")
        try:
            self.runner_fn()
        except Exception as exc:  # pylint: disable=broad-except
            safe_print(f"[watch] Pipeline run failed: {exc}")


def run_watch(
    config_path: Path,
    runner_kwargs: dict[str, Any],
) -> None:
    """Entry point for watch mode.

    Runs the pipeline once immediately, then watches the CI config and any
    local include files for changes, re-running on each save.

    Args:
        config_path: Absolute path to the CI config file.
        runner_kwargs: Forwarded verbatim to ``LocalGitLabRunner.run_pipeline()``.
    """
    from bitrab.plan import LocalGitLabRunner

    base_path = config_path.parent
    runner = LocalGitLabRunner(base_path=base_path)

    def run() -> None:
        runner.run_pipeline(config_path=config_path, **runner_kwargs)

    # Initial run
    safe_print("[watch] Starting initial pipeline run...")
    try:
        run()
    except Exception as exc:  # pylint: disable=broad-except
        safe_print(f"[watch] Initial run failed: {exc}")

    # Determine which paths to watch
    watched = collect_watched_paths(config_path)
    watched.add(config_path.resolve())
    watch_dirs = {p.parent for p in watched}

    safe_print(f"[watch] Watching {len(watched)} file(s) for changes. Press Ctrl+C to stop.")
    for p in sorted(watched):
        safe_print(f"         {p}")

    handler = PipelineRerunHandler(run, watched)
    observer = Observer()
    for d in watch_dirs:
        observer.schedule(handler, str(d), recursive=False)

    observer.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        safe_print("\n[watch] Stopped.")
    finally:
        observer.stop()
        observer.join()
