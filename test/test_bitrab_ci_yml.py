"""Tests for .bitrab-ci.yml preferential loading."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from bitrab.config.loader import ConfigurationLoader


class TestBitrabCiYmlPreference:
    def _make_loader(self, base: Path) -> ConfigurationLoader:
        return ConfigurationLoader(base_path=base)

    def test_uses_bitrab_ci_when_only_it_exists(self, tmp_path):
        (tmp_path / ".bitrab-ci.yml").write_text("stages:\n  - build\n")
        loader = self._make_loader(tmp_path)
        cfg = loader.load_config()
        assert cfg["stages"] == ["build"]

    def test_uses_gitlab_ci_when_only_it_exists(self, tmp_path):
        (tmp_path / ".gitlab-ci.yml").write_text("stages:\n  - test\n")
        loader = self._make_loader(tmp_path)
        cfg = loader.load_config()
        assert cfg["stages"] == ["test"]

    def test_prefers_bitrab_ci_over_gitlab_ci(self, tmp_path):
        (tmp_path / ".bitrab-ci.yml").write_text("stages:\n  - bitrab\n")
        (tmp_path / ".gitlab-ci.yml").write_text("stages:\n  - gitlab\n")
        loader = self._make_loader(tmp_path)
        cfg = loader.load_config()
        assert cfg["stages"] == ["bitrab"]

    def test_warns_when_both_exist(self, tmp_path):
        (tmp_path / ".bitrab-ci.yml").write_text("stages:\n  - bitrab\n")
        (tmp_path / ".gitlab-ci.yml").write_text("stages:\n  - gitlab\n")
        loader = self._make_loader(tmp_path)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            loader.load_config()
        messages = [str(w.message) for w in caught]
        assert any(".bitrab-ci.yml" in m and ".gitlab-ci.yml" in m for m in messages)

    def test_no_warning_when_only_bitrab_ci(self, tmp_path):
        (tmp_path / ".bitrab-ci.yml").write_text("stages:\n  - build\n")
        loader = self._make_loader(tmp_path)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            loader.load_config()
        assert not caught

    def test_explicit_config_path_not_redirected(self, tmp_path):
        (tmp_path / ".bitrab-ci.yml").write_text("stages:\n  - bitrab\n")
        (tmp_path / ".gitlab-ci.yml").write_text("stages:\n  - gitlab\n")
        loader = self._make_loader(tmp_path)
        # Explicitly passing the gitlab-ci.yml path bypasses the detection
        cfg = loader.load_config(tmp_path / ".gitlab-ci.yml")
        assert cfg["stages"] == ["gitlab"]

    def test_raises_when_neither_exists(self, tmp_path):
        from bitrab.exceptions import GitlabRunnerError

        loader = self._make_loader(tmp_path)
        with pytest.raises(GitlabRunnerError, match="not found"):
            loader.load_config()
