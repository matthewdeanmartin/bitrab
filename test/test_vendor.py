"""Tests for remote include vendoring and offline loading."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from bitrab.config.loader import ConfigurationLoader
from bitrab.exceptions import GitlabRunnerError
from bitrab.vendor import check_vendor, load_lock, vendor


def write_root(tmp_path: Path, url: str = "https://example.test/ci/root.yml") -> Path:
    path = tmp_path / ".gitlab-ci.yml"
    path.write_text(
        f"stages: [test]\ninclude:\n  - remote: {url}\nlocal:\n  stage: test\n  script: echo local\n",
        encoding="utf-8",
    )
    return path


def test_vendor_recursively_snapshots_remote_graph_and_offline_loader_uses_it(tmp_path):
    root_url = "https://example.test/ci/root.yml"
    child_url = "https://cdn.example.test/child.yml"
    config = write_root(tmp_path, root_url)
    payloads = {
        root_url: f"include:\n  - url: {child_url}\nremote:\n  stage: test\n  script: echo remote\n".encode(),
        child_url: b"child:\n  stage: test\n  script: echo child\n",
    }

    result = vendor(config, fetcher=payloads.__getitem__)

    assert {entry.url for entry in result.entries} == {root_url, child_url}
    assert set(load_lock(tmp_path)) == {root_url, child_url}
    assert check_vendor(config) == []
    with patch("urllib3.PoolManager", side_effect=AssertionError("network opened")):
        loaded = ConfigurationLoader(tmp_path, offline=True).load_config(config)
    assert {"local", "remote", "child"} <= set(loaded)


def test_vendor_check_names_tampered_file(tmp_path):
    url = "https://example.test/ci.yml"
    config = write_root(tmp_path, url)
    vendor(config, fetcher=lambda _url: b"remote:\n  script: echo ok\n")
    entry = load_lock(tmp_path)[url]
    payload = tmp_path / ".bitrab" / entry.file
    payload.write_text("tampered: true\n", encoding="utf-8")

    errors = check_vendor(config)

    assert any(str(payload) in error and "hash mismatch" in error for error in errors)


def test_offline_loader_rejects_unvendored_remote_without_network(tmp_path):
    url = "https://example.test/missing.yml"
    config = write_root(tmp_path, url)

    with patch("urllib3.PoolManager", side_effect=AssertionError("network opened")):
        with pytest.raises(GitlabRunnerError, match="not vendored.*offline mode"):
            ConfigurationLoader(tmp_path, offline=True).load_config(config)


def test_vendor_refresh_preserves_timestamp_when_content_is_unchanged(tmp_path):
    url = "https://example.test/ci.yml"
    config = write_root(tmp_path, url)
    payload = b"remote:\n  script: echo stable\n"
    first = vendor(config, fetcher=lambda _url: payload)
    second = vendor(config, fetcher=lambda _url: payload)

    assert first.entries[0].fetched_at == second.entries[0].fetched_at
    assert second.unchanged == (url,)
