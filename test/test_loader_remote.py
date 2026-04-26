"""Tests for D3: remote include support (HTTP fetch)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bitrab.config.loader import ConfigurationLoader
from bitrab.exceptions import GitlabRunnerError


def make_loader(tmp_path: Path) -> ConfigurationLoader:
    return ConfigurationLoader(base_path=tmp_path)


def yaml_bytes(content: str) -> bytes:
    return content.encode("utf-8")


def mock_response(status: int, data: bytes) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.data = data
    return resp


class TestRemoteIncludeFetch:
    def test_remote_include_fetched_and_merged(self, tmp_path):
        """A `remote:` include is fetched and its jobs appear in the config."""
        remote_yaml = yaml_bytes("remote_job:\n  script:\n    - echo remote\n  stage: test\n")

        main_ci = tmp_path / ".gitlab-ci.yml"
        main_ci.write_text(
            "stages:\n  - test\n"
            "include:\n  - remote: https://example.com/ci.yml\n"
            "local_job:\n  script:\n    - echo local\n  stage: test\n",
            encoding="utf-8",
        )

        with patch("urllib3.PoolManager") as mock_pm:
            mock_pm.return_value.request.return_value = mock_response(200, remote_yaml)
            loader_inst = make_loader(tmp_path)
            config = loader_inst.load_config(main_ci)

        assert "remote_job" in config
        assert "local_job" in config

    def test_url_key_alias_works(self, tmp_path):
        """A `url:` include is treated identically to `remote:`."""
        remote_yaml = yaml_bytes("url_job:\n  script:\n    - echo url\n  stage: test\n")

        main_ci = tmp_path / ".gitlab-ci.yml"
        main_ci.write_text(
            "stages:\n  - test\n" "include:\n  - url: https://example.com/ci.yml\n",
            encoding="utf-8",
        )

        with patch("urllib3.PoolManager") as mock_pm:
            mock_pm.return_value.request.return_value = mock_response(200, remote_yaml)
            loader_inst = make_loader(tmp_path)
            config = loader_inst.load_config(main_ci)

        assert "url_job" in config

    def test_remote_include_http_error_raises(self, tmp_path):
        """A network error raises GitlabRunnerError."""
        import urllib3

        main_ci = tmp_path / ".gitlab-ci.yml"
        main_ci.write_text(
            "stages:\n  - test\n" "include:\n  - remote: https://example.com/ci.yml\n",
            encoding="utf-8",
        )

        with patch("urllib3.PoolManager") as mock_pm:
            mock_pm.return_value.request.side_effect = urllib3.exceptions.HTTPError("timeout")
            loader_inst = make_loader(tmp_path)
            with pytest.raises(GitlabRunnerError, match="Failed to fetch remote include"):
                loader_inst.load_config(main_ci)

    def test_remote_include_non_200_raises(self, tmp_path):
        """A non-200 HTTP response raises GitlabRunnerError."""
        main_ci = tmp_path / ".gitlab-ci.yml"
        main_ci.write_text(
            "stages:\n  - test\n" "include:\n  - remote: https://example.com/ci.yml\n",
            encoding="utf-8",
        )

        with patch("urllib3.PoolManager") as mock_pm:
            mock_pm.return_value.request.return_value = mock_response(404, b"Not Found")
            loader_inst = make_loader(tmp_path)
            with pytest.raises(GitlabRunnerError, match="HTTP 404"):
                loader_inst.load_config(main_ci)

    def test_remote_include_invalid_yaml_raises(self, tmp_path):
        """Invalid YAML in a remote include raises GitlabRunnerError."""
        main_ci = tmp_path / ".gitlab-ci.yml"
        main_ci.write_text(
            "stages:\n  - test\n" "include:\n  - remote: https://example.com/ci.yml\n",
            encoding="utf-8",
        )

        with patch("urllib3.PoolManager") as mock_pm:
            mock_pm.return_value.request.return_value = mock_response(200, b"invalid: [yaml: bad")
            loader_inst = make_loader(tmp_path)
            with pytest.raises(GitlabRunnerError, match="Failed to parse YAML"):
                loader_inst.load_config(main_ci)

    def test_remote_include_deduplicated(self, tmp_path):
        """The same remote URL listed twice is only fetched once."""
        remote_yaml = yaml_bytes("dedup_job:\n  script:\n    - echo\n  stage: test\n")

        main_ci = tmp_path / ".gitlab-ci.yml"
        main_ci.write_text(
            "stages:\n  - test\n"
            "include:\n"
            "  - remote: https://example.com/ci.yml\n"
            "  - remote: https://example.com/ci.yml\n",
            encoding="utf-8",
        )

        with patch("urllib3.PoolManager") as mock_pm:
            mock_pm.return_value.request.return_value = mock_response(200, remote_yaml)
            loader_inst = make_loader(tmp_path)
            loader_inst.load_config(main_ci)

        assert mock_pm.return_value.request.call_count == 1

    def test_local_and_remote_include_coexist(self, tmp_path):
        """Local and remote includes both contribute jobs to the merged config."""
        local_file = tmp_path / "local.yml"
        local_file.write_text("local_job:\n  script:\n    - echo local\n  stage: test\n", encoding="utf-8")

        remote_yaml = yaml_bytes("remote_job:\n  script:\n    - echo remote\n  stage: test\n")

        main_ci = tmp_path / ".gitlab-ci.yml"
        main_ci.write_text(
            "stages:\n  - test\n" "include:\n" "  - local: local.yml\n" "  - remote: https://example.com/ci.yml\n",
            encoding="utf-8",
        )

        with patch("urllib3.PoolManager") as mock_pm:
            mock_pm.return_value.request.return_value = mock_response(200, remote_yaml)
            loader_inst = make_loader(tmp_path)
            config = loader_inst.load_config(main_ci)

        assert "local_job" in config
        assert "remote_job" in config


class TestCollectIncludePaths:
    def test_collect_include_paths_empty(self, tmp_path):
        """Config with no includes returns empty set."""
        main_ci = tmp_path / ".gitlab-ci.yml"
        main_ci.write_text("stages:\n  - test\njob:\n  script:\n    - echo\n", encoding="utf-8")

        loader_inst = make_loader(tmp_path)
        paths = loader_inst.collect_include_paths(main_ci)
        assert paths == set()

    def test_collect_include_paths_local(self, tmp_path):
        """Config with a local include returns that file's path."""
        sub = tmp_path / "sub.yml"
        sub.write_text("job2:\n  script:\n    - echo\n", encoding="utf-8")

        main_ci = tmp_path / ".gitlab-ci.yml"
        main_ci.write_text(
            "include:\n  - local: sub.yml\n",
            encoding="utf-8",
        )

        loader_inst = make_loader(tmp_path)
        paths = loader_inst.collect_include_paths(main_ci)
        assert sub.resolve() in paths

    def test_collect_include_paths_transitive(self, tmp_path):
        """Three-level include chain — all intermediate paths collected."""
        c = tmp_path / "c.yml"
        c.write_text("job_c:\n  script:\n    - echo\n", encoding="utf-8")

        b = tmp_path / "b.yml"
        b.write_text("include:\n  - local: c.yml\njob_b:\n  script:\n    - echo\n", encoding="utf-8")

        a = tmp_path / ".gitlab-ci.yml"
        a.write_text("include:\n  - local: b.yml\n", encoding="utf-8")

        loader_inst = make_loader(tmp_path)
        paths = loader_inst.collect_include_paths(a)
        assert b.resolve() in paths
        assert c.resolve() in paths

    def test_collect_include_paths_ignores_remote(self, tmp_path):
        """Remote includes are not collected (no local path)."""
        main_ci = tmp_path / ".gitlab-ci.yml"
        main_ci.write_text(
            "include:\n  - remote: https://example.com/ci.yml\n",
            encoding="utf-8",
        )

        loader_inst = make_loader(tmp_path)
        paths = loader_inst.collect_include_paths(main_ci)
        assert paths == set()

    def test_collect_include_paths_cycle_safe(self, tmp_path):
        """Circular local includes don't cause infinite recursion."""
        a = tmp_path / "a.yml"
        b = tmp_path / "b.yml"
        a.write_text("include:\n  - local: b.yml\n", encoding="utf-8")
        b.write_text("include:\n  - local: a.yml\n", encoding="utf-8")

        main_ci = tmp_path / ".gitlab-ci.yml"
        main_ci.write_text("include:\n  - local: a.yml\n", encoding="utf-8")

        loader_inst = make_loader(tmp_path)
        paths = loader_inst.collect_include_paths(main_ci)
        assert a.resolve() in paths
        assert b.resolve() in paths
