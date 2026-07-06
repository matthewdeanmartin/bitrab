"""Tests for remote include retry, limits, and transparent caching."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest

from bitrab.config.loader import MAX_REMOTE_INCLUDE_BYTES, ConfigurationLoader
from bitrab.cli import create_parser
from bitrab.exceptions import GitlabRunnerError
from bitrab.include_cache import payload_path, write_cached


def response(data: bytes, status: int = 200) -> MagicMock:
    result = MagicMock()
    result.status = status
    result.headers = {"Content-Length": str(len(data))}
    result.read.return_value = data
    return result


def root_config(tmp_path):
    path = tmp_path / ".gitlab-ci.yml"
    path.write_text("include:\n  - remote: https://example.test/ci.yml\n", encoding="utf-8")
    return path


def test_remote_include_cache_avoids_second_network_request(tmp_path):
    config = root_config(tmp_path)
    payload = b"job:\n  script: echo cached\n"
    with patch("urllib3.PoolManager") as pool:
        pool.return_value.request.return_value = response(payload)
        ConfigurationLoader(tmp_path).load_config(config)
        ConfigurationLoader(tmp_path).load_config(config)
    assert pool.return_value.request.call_count == 1
    assert payload_path(tmp_path, "https://example.test/ci.yml").is_file()


def test_expired_cache_refetches(tmp_path):
    config = root_config(tmp_path)
    payload = b"job:\n  script: echo cached\n"
    with patch("urllib3.PoolManager") as pool:
        pool.return_value.request.return_value = response(payload)
        ConfigurationLoader(tmp_path).load_config(config)
        cached = payload_path(tmp_path, "https://example.test/ci.yml")
        os.utime(cached, (0, 0))
        ConfigurationLoader(tmp_path).load_config(config)
    assert pool.return_value.request.call_count == 2


def test_no_include_cache_bypasses_reads_and_writes(tmp_path):
    config = root_config(tmp_path)
    payload = b"job:\n  script: echo fresh\n"
    with patch("urllib3.PoolManager") as pool:
        pool.return_value.request.return_value = response(payload)
        loader = ConfigurationLoader(tmp_path, no_include_cache=True)
        loader.load_config(config)
        loader.load_config(config)
    assert pool.return_value.request.call_count == 2
    assert not payload_path(tmp_path, "https://example.test/ci.yml").exists()


def test_request_uses_retry_policy(tmp_path):
    config = root_config(tmp_path)
    with patch("urllib3.PoolManager") as pool:
        pool.return_value.request.return_value = response(b"job:\n  script: echo ok\n")
        ConfigurationLoader(tmp_path).load_config(config)
    retry = pool.return_value.request.call_args.kwargs["retries"]
    assert retry.total == 3
    assert 503 in retry.status_forcelist


def test_remote_include_size_limit_names_url(tmp_path):
    config = root_config(tmp_path)
    oversized = b"x" * (MAX_REMOTE_INCLUDE_BYTES + 1)
    with patch("urllib3.PoolManager") as pool:
        pool.return_value.request.return_value = response(oversized)
        with pytest.raises(GitlabRunnerError, match="example.test.*size limit"):
            ConfigurationLoader(tmp_path).load_config(config)


def test_no_include_cache_cli_flags():
    assert create_parser().parse_args(["run", "--no-include-cache"]).no_include_cache is True
    assert create_parser().parse_args(["validate", "--no-include-cache"]).no_include_cache is True
    assert create_parser().parse_args(["watch", "--no-include-cache"]).no_include_cache is True


def test_corrupt_cached_yaml_is_discarded_and_refetched(tmp_path):
    config = root_config(tmp_path)
    url = "https://example.test/ci.yml"
    write_cached(tmp_path, url, b"invalid: [yaml")
    with patch("urllib3.PoolManager") as pool:
        pool.return_value.request.return_value = response(b"job:\n  script: echo recovered\n")
        loaded = ConfigurationLoader(tmp_path).load_config(config)
    assert "job" in loaded
    assert pool.return_value.request.call_count == 1


def test_concurrent_cache_writes_publish_complete_payload(tmp_path):
    url = "https://example.test/ci.yml"
    payloads = [b"a" * 100_000, b"b" * 120_000]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda payload: write_cached(tmp_path, url, payload), payloads))
    assert payload_path(tmp_path, url).read_bytes() in payloads
