"""Tests for bitrab.folder — .bitrab/ workspace management."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from bitrab.folder import (
    human_size,
    is_run_id,
    make_run_id,
    bitrab_dir,
    clean_all,
    clean_artifacts,
    clean_job_dirs,
    clean_logs,
    list_runs,
    maybe_warn_size,
    prune_runs,
    scan_folder,
    write_run_log,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_file(path: Path, size: int = 16) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


# ---------------------------------------------------------------------------
# Unit: human_size
# ---------------------------------------------------------------------------


def test_human_size_bytes():
    assert human_size(512) == "512.0 B"


def test_human_size_kb():
    assert human_size(2048) == "2.0 KB"


def test_human_size_mb():
    assert human_size(3 * 1024 * 1024) == "3.0 MB"


# ---------------------------------------------------------------------------
# Unit: make_run_id / is_run_id
# ---------------------------------------------------------------------------


def test_make_run_id_format():
    run_id = make_run_id()
    assert is_run_id(run_id)


def test_is_run_id_rejects_short():
    assert not is_run_id("20240101_120000")


def test_is_run_id_rejects_wrong_length():
    assert not is_run_id("20240101_120000_xxxxxxxx_extra")


def test_is_run_id_rejects_non_hex():
    assert not is_run_id("20240101_120000_ZZZZZZZZ")


# ---------------------------------------------------------------------------
# Unit: bitrab_dir
# ---------------------------------------------------------------------------


def test_bitrab_dir(tmp_path):
    assert bitrab_dir(tmp_path) == tmp_path / ".bitrab"


# ---------------------------------------------------------------------------
# scan_folder — non-existent directory
# ---------------------------------------------------------------------------


def test_scan_folder_nonexistent(tmp_path):
    summary = scan_folder(tmp_path)
    assert not summary.exists
    assert summary.total_size_bytes == 0
    assert summary.run_count == 0
    assert not summary.is_large


# ---------------------------------------------------------------------------
# scan_folder — with content
# ---------------------------------------------------------------------------


def test_scan_folder_counts_job_dirs(tmp_path):
    bd = tmp_path / ".bitrab"
    make_file(bd / "myjob" / "output.log", size=1024)
    summary = scan_folder(tmp_path)
    assert summary.exists
    assert summary.job_dirs_size_bytes >= 1024
    assert "myjob" in summary.subdirs


def test_scan_folder_counts_artifacts(tmp_path):
    bd = tmp_path / ".bitrab"
    make_file(bd / "artifacts" / "build" / "app.zip", size=4096)
    summary = scan_folder(tmp_path)
    assert summary.artifacts_size_bytes >= 4096
    assert summary.job_dirs_size_bytes == 0


def test_scan_folder_counts_logs(tmp_path):
    run_id = make_run_id()
    log_dir = tmp_path / ".bitrab" / "logs" / run_id
    make_file(log_dir / "summary.txt", size=512)
    summary = scan_folder(tmp_path)
    assert summary.logs_size_bytes >= 512
    assert summary.run_count == 1


def test_scan_folder_size_warning(tmp_path):
    bd = tmp_path / ".bitrab"
    make_file(bd / "myjob" / "big.bin", size=1024)
    summary = scan_folder(tmp_path, warn_threshold_bytes=100)
    assert summary.is_large


def test_scan_folder_no_warning_under_threshold(tmp_path):
    bd = tmp_path / ".bitrab"
    make_file(bd / "myjob" / "small.txt", size=10)
    summary = scan_folder(tmp_path, warn_threshold_bytes=10 * 1024 * 1024)
    assert not summary.is_large


# ---------------------------------------------------------------------------
# write_run_log / list_runs
# ---------------------------------------------------------------------------


def sample_events():
    return [
        {
            "event_type": "pipeline_start",
            "timestamp": 1.0,
            "wall_time": time.time(),
            "stage": None,
            "job": None,
            "data": {},
        },
        {
            "event_type": "pipeline_complete",
            "timestamp": 2.5,
            "wall_time": time.time(),
            "stage": None,
            "job": None,
            "data": {"success": True},
        },
    ]


def test_write_run_log_creates_files(tmp_path):
    run_dir = write_run_log(
        tmp_path,
        events_json=sample_events(),
        summary_text="Pipeline succeeded\n",
        meta={"started_at": time.time(), "success": True, "total_duration_s": 1.5, "job_count": 3},
    )
    assert (run_dir / "meta.json").exists()
    assert (run_dir / "events.jsonl").exists()
    assert (run_dir / "summary.txt").exists()


def test_write_run_log_meta_contains_size(tmp_path):
    run_dir = write_run_log(
        tmp_path,
        events_json=sample_events(),
        summary_text="Pipeline succeeded\n",
        meta={"started_at": time.time(), "success": True, "total_duration_s": 1.5, "job_count": 2},
    )
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["size_bytes"] > 0
    assert meta["run_id"] is not None
    assert is_run_id(meta["run_id"])


def test_list_runs_empty(tmp_path):
    assert list_runs(tmp_path) == []


def test_list_runs_returns_records(tmp_path):
    for _ in range(3):
        write_run_log(
            tmp_path,
            events_json=sample_events(),
            summary_text="ok",
            meta={"started_at": time.time(), "success": True, "total_duration_s": 1.0, "job_count": 1},
        )
        time.sleep(0.01)  # ensure distinct timestamps for ordering

    runs = list_runs(tmp_path)
    assert len(runs) == 3
    # newest first
    assert runs[0].run_id >= runs[1].run_id >= runs[2].run_id


def test_list_runs_reads_meta_fields(tmp_path):
    write_run_log(
        tmp_path,
        events_json=sample_events(),
        summary_text="Pipeline failed\n",
        meta={"started_at": 1700000000.0, "success": False, "total_duration_s": 42.0, "job_count": 5},
    )
    runs = list_runs(tmp_path)
    assert len(runs) == 1
    r = runs[0]
    assert r.success is False
    assert r.total_duration_s == pytest.approx(42.0)
    assert r.job_count == 5
    assert r.size_bytes > 0


def test_list_runs_skips_non_run_dirs(tmp_path):
    logs_dir = tmp_path / ".bitrab" / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "not_a_run").mkdir()
    runs = list_runs(tmp_path)
    assert runs == []


# ---------------------------------------------------------------------------
# prune_runs
# ---------------------------------------------------------------------------


def test_prune_runs_keeps_n_most_recent(tmp_path):
    for _ in range(5):
        write_run_log(
            tmp_path,
            events_json=sample_events(),
            summary_text="ok",
            meta={"started_at": time.time(), "success": True, "total_duration_s": 1.0, "job_count": 1},
        )
        time.sleep(0.01)

    deleted = prune_runs(tmp_path, keep=3)
    assert len(deleted) == 2
    assert len(list_runs(tmp_path)) == 3


def test_prune_runs_keep_more_than_exist(tmp_path):
    write_run_log(
        tmp_path,
        events_json=[],
        summary_text="ok",
        meta={"started_at": time.time(), "success": True, "total_duration_s": 0.0, "job_count": 0},
    )
    deleted = prune_runs(tmp_path, keep=10)
    assert deleted == []
    assert len(list_runs(tmp_path)) == 1


# ---------------------------------------------------------------------------
# clean_* functions
# ---------------------------------------------------------------------------


def test_clean_artifacts(tmp_path):
    make_file(tmp_path / ".bitrab" / "artifacts" / "build" / "x.zip", size=256)
    freed = clean_artifacts(tmp_path)
    assert freed >= 256
    assert not (tmp_path / ".bitrab" / "artifacts").exists()


def test_clean_artifacts_nonexistent(tmp_path):
    assert clean_artifacts(tmp_path) == 0


def test_clean_job_dirs(tmp_path):
    make_file(tmp_path / ".bitrab" / "job_a" / "output.log", size=128)
    make_file(tmp_path / ".bitrab" / "job_b" / "output.log", size=64)
    freed = clean_job_dirs(tmp_path)
    assert freed >= 192
    assert not (tmp_path / ".bitrab" / "job_a").exists()
    assert not (tmp_path / ".bitrab" / "job_b").exists()


def test_clean_job_dirs_preserves_artifacts_and_logs(tmp_path):
    make_file(tmp_path / ".bitrab" / "myjob" / "out.log", size=32)
    make_file(tmp_path / ".bitrab" / "artifacts" / "build" / "x", size=32)
    write_run_log(
        tmp_path,
        events_json=[],
        summary_text="ok",
        meta={"started_at": time.time(), "success": True, "total_duration_s": 0.0, "job_count": 0},
    )
    clean_job_dirs(tmp_path)
    # artifacts and logs must survive
    assert (tmp_path / ".bitrab" / "artifacts").exists()
    assert (tmp_path / ".bitrab" / "logs").exists()
    assert not (tmp_path / ".bitrab" / "myjob").exists()


def test_clean_logs(tmp_path):
    write_run_log(
        tmp_path,
        events_json=sample_events(),
        summary_text="ok",
        meta={"started_at": time.time(), "success": True, "total_duration_s": 1.0, "job_count": 1},
    )
    freed = clean_logs(tmp_path)
    assert freed > 0
    assert not (tmp_path / ".bitrab" / "logs").exists()


def test_clean_all(tmp_path):
    make_file(tmp_path / ".bitrab" / "myjob" / "out.log", size=64)
    make_file(tmp_path / ".bitrab" / "artifacts" / "x.zip", size=64)
    write_run_log(
        tmp_path,
        events_json=[],
        summary_text="ok",
        meta={"started_at": time.time(), "success": True, "total_duration_s": 0.0, "job_count": 0},
    )
    freed = clean_all(tmp_path)
    assert freed > 0
    assert not (tmp_path / ".bitrab").exists()


def test_clean_all_nonexistent(tmp_path):
    assert clean_all(tmp_path) == 0


# ---------------------------------------------------------------------------
# maybe_warn_size
# ---------------------------------------------------------------------------


def test_maybe_warn_size_no_warning(tmp_path):
    make_file(tmp_path / ".bitrab" / "job" / "out.log", size=10)
    assert maybe_warn_size(tmp_path, warn_threshold_bytes=10 * 1024 * 1024) is None


def test_maybe_warn_size_triggers(tmp_path):
    make_file(tmp_path / ".bitrab" / "job" / "out.log", size=1024)
    msg = maybe_warn_size(tmp_path, warn_threshold_bytes=100)
    assert msg is not None
    assert "bitrab folder clean" in msg


def test_maybe_warn_size_no_bitrab_dir(tmp_path):
    assert maybe_warn_size(tmp_path) is None


# ---------------------------------------------------------------------------
# FolderSummary.format_text
# ---------------------------------------------------------------------------


def test_folder_summary_format_nonexistent(tmp_path):
    summary = scan_folder(tmp_path)
    text = summary.format_text()
    assert "does not exist" in text


def test_folder_summary_format_exists(tmp_path):
    make_file(tmp_path / ".bitrab" / "myjob" / "out.log", size=128)
    summary = scan_folder(tmp_path)
    text = summary.format_text()
    assert ".bitrab" in text
    assert "Total" in text
