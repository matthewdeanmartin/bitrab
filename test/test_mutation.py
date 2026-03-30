"""Tests for filesystem mutation detection."""

from __future__ import annotations

import time


from bitrab.mutation import (
    MutationConfig,
    MutationSnapshot,
    _BUILTIN_WHITELIST,
    _is_whitelisted,
    load_mutation_config,
)


# ---------------------------------------------------------------------------
# _is_whitelisted
# ---------------------------------------------------------------------------


class TestIsWhitelisted:
    def test_exact_match(self):
        assert _is_whitelisted(".coverage", [".coverage"])

    def test_glob_star(self):
        assert _is_whitelisted("foo.pyc", ["*.pyc"])

    def test_glob_double_star_prefix(self):
        assert _is_whitelisted(".mypy_cache/sub/file.py", [".mypy_cache/**"])

    def test_glob_double_star_middle(self):
        assert _is_whitelisted("pkg/__pycache__/mod.cpython-311.pyc", ["**/__pycache__/**"])

    def test_no_match(self):
        assert not _is_whitelisted("src/main.py", [".mypy_cache/**", "*.pyc"])

    def test_builtin_pytest_cache(self):
        assert _is_whitelisted(".pytest_cache/v/cache/lastfailed", _BUILTIN_WHITELIST)

    def test_builtin_pycache(self):
        assert _is_whitelisted("bitrab/__pycache__/cli.cpython-311.pyc", _BUILTIN_WHITELIST)

    def test_builtin_bitrab_dir(self):
        assert _is_whitelisted(".bitrab/some_job/output.log", _BUILTIN_WHITELIST)

    def test_not_whitelisted_source_file(self):
        assert not _is_whitelisted("bitrab/cli.py", _BUILTIN_WHITELIST)


# ---------------------------------------------------------------------------
# MutationConfig.effective_whitelist
# ---------------------------------------------------------------------------


class TestMutationConfig:
    def test_effective_whitelist_includes_builtins(self):
        cfg = MutationConfig(enabled=True, whitelist=["custom/**"])
        wl = cfg.effective_whitelist
        assert ".mypy_cache/**" in wl
        assert "custom/**" in wl

    def test_disabled_by_default(self):
        cfg = MutationConfig()
        assert not cfg.enabled


# ---------------------------------------------------------------------------
# load_mutation_config
# ---------------------------------------------------------------------------


class TestLoadMutationConfig:
    def test_missing_pyproject(self, tmp_path):
        cfg = load_mutation_config(tmp_path)
        assert not cfg.enabled

    def test_defaults_when_section_absent(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'foo'\n")
        cfg = load_mutation_config(tmp_path)
        assert not cfg.enabled
        assert cfg.whitelist == []

    def test_enabled_flag(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[tool.bitrab]\nwarn_on_mutation = true\n"
        )
        cfg = load_mutation_config(tmp_path)
        assert cfg.enabled

    def test_custom_whitelist(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[tool.bitrab]\nwarn_on_mutation = true\n"
            '[tool.bitrab.mutation]\nwhitelist = ["docs/**", "*.generated.py"]\n'
        )
        cfg = load_mutation_config(tmp_path)
        assert cfg.enabled
        assert "docs/**" in cfg.whitelist
        assert "*.generated.py" in cfg.whitelist

    def test_invalid_toml_returns_disabled(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("not : valid : toml !!!")
        cfg = load_mutation_config(tmp_path)
        assert not cfg.enabled


# ---------------------------------------------------------------------------
# MutationSnapshot
# ---------------------------------------------------------------------------


class TestMutationSnapshot:
    def test_no_mutations_when_nothing_changes(self, tmp_path):
        (tmp_path / "existing.txt").write_text("hello")
        cfg = MutationConfig(enabled=True)
        snap = MutationSnapshot(project_dir=tmp_path, config=cfg)
        snap.take()
        assert snap.mutations() == []

    def test_detects_new_file(self, tmp_path):
        cfg = MutationConfig(enabled=True)
        snap = MutationSnapshot(project_dir=tmp_path, config=cfg)
        snap.take()
        time.sleep(0.01)
        (tmp_path / "new_file.txt").write_text("created after snapshot")
        mutations = snap.mutations()
        assert "new_file.txt" in mutations

    def test_detects_modified_file(self, tmp_path):
        f = tmp_path / "existing.txt"
        f.write_text("before")
        cfg = MutationConfig(enabled=True)
        snap = MutationSnapshot(project_dir=tmp_path, config=cfg)
        snap.take()
        time.sleep(0.01)
        f.write_text("after")
        mutations = snap.mutations()
        assert "existing.txt" in mutations

    def test_whitelisted_file_not_reported(self, tmp_path):
        cfg = MutationConfig(enabled=True)
        snap = MutationSnapshot(project_dir=tmp_path, config=cfg)
        snap.take()
        time.sleep(0.01)
        # Create a .pytest_cache directory with a file
        cache = tmp_path / ".pytest_cache"
        cache.mkdir()
        (cache / "README.md").write_text("cache")
        mutations = snap.mutations()
        # Should be whitelisted by builtin pattern ".pytest_cache/**"
        assert not any(".pytest_cache" in m for m in mutations)

    def test_custom_whitelist_respected(self, tmp_path):
        cfg = MutationConfig(enabled=True, whitelist=["generated/**"])
        snap = MutationSnapshot(project_dir=tmp_path, config=cfg)
        snap.take()
        time.sleep(0.01)
        gen = tmp_path / "generated"
        gen.mkdir()
        (gen / "output.txt").write_text("generated")
        mutations = snap.mutations()
        assert not any("generated" in m for m in mutations)

    def test_non_whitelisted_subdir_reported(self, tmp_path):
        cfg = MutationConfig(enabled=True)
        snap = MutationSnapshot(project_dir=tmp_path, config=cfg)
        snap.take()
        time.sleep(0.01)
        src = tmp_path / "src"
        src.mkdir()
        (src / "generated.py").write_text("# oops")
        mutations = snap.mutations()
        assert any("generated.py" in m for m in mutations)
