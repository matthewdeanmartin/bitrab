"""Tests for Sprint 01: local execution of ``cache:``."""

from __future__ import annotations

import threading
from pathlib import Path

from bitrab.config.capabilities import DiagnosticLevel, check_capabilities
from bitrab.execution.cache import (
    DEFAULT_CACHE_KEY,
    expand_variables,
    read_latest_generation,
    resolve_cache_key,
    restore_cache_entry,
    restore_caches,
    sanitize_cache_key,
    save_cache_entry,
    save_caches,
)
from bitrab.models.pipeline import CacheConfig, JobConfig
from bitrab.plan import LocalGitLabRunner, PipelineProcessor
from bitrab.utils.filelock import FileLock, FileLockTimeout

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_job(name: str = "myjob", cache: list[CacheConfig] | None = None) -> JobConfig:
    return JobConfig(name=name, stage="test", script=["echo hi"], cache=cache or [])


def make_store(tmp_path: Path) -> Path:
    return tmp_path / ".bitrab" / "cache"


def seed_cache(tmp_path: Path, key: str, rel_path: str, content: str) -> None:
    """Save one file into the store under *key* via the real save path."""
    src = tmp_path / "seed_src"
    dest = src / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content)
    cache = CacheConfig(paths=[rel_path.split("/")[0]])
    assert save_cache_entry(cache, key, make_store(tmp_path), src)


# ---------------------------------------------------------------------------
# Parsing: PipelineProcessor.parse_cache_entries / process_config
# ---------------------------------------------------------------------------


class TestCacheParsing:
    def test_single_dict_entry(self):
        proc = PipelineProcessor()
        raw = {"myjob": {"script": ["echo hi"], "cache": {"key": "k1", "paths": ["dist/"]}}}
        pipeline = proc.process_config(raw)
        assert pipeline.jobs[0].cache == [CacheConfig(paths=["dist/"], key="k1")]

    def test_top_level_cache_is_default_for_jobs(self):
        proc = PipelineProcessor()
        raw = {
            "cache": {"paths": ["node_modules/"]},
            "myjob": {"script": ["echo hi"]},
        }
        pipeline = proc.process_config(raw)
        assert pipeline.jobs[0].cache == [CacheConfig(paths=["node_modules/"])]

    def test_default_cache_wins_over_top_level(self):
        proc = PipelineProcessor()
        raw = {
            "cache": {"paths": ["top/"]},
            "default": {"cache": {"paths": ["def/"]}},
            "myjob": {"script": ["echo hi"]},
        }
        pipeline = proc.process_config(raw)
        assert pipeline.jobs[0].cache[0].paths == ["def/"]

    def test_job_cache_overrides_top_level_wholesale(self):
        proc = PipelineProcessor()
        raw = {
            "cache": {"key": "global", "paths": ["node_modules/"]},
            "myjob": {"script": ["echo hi"], "cache": {"key": "mine", "paths": ["dist/"]}},
        }
        pipeline = proc.process_config(raw)
        assert pipeline.jobs[0].cache == [CacheConfig(paths=["dist/"], key="mine")]

    def test_empty_list_disables_cache(self):
        proc = PipelineProcessor()
        raw = {
            "cache": {"paths": ["node_modules/"]},
            "myjob": {"script": ["echo hi"], "cache": []},
        }
        pipeline = proc.process_config(raw)
        assert pipeline.jobs[0].cache == []

    def test_empty_dict_disables_cache(self):
        proc = PipelineProcessor()
        raw = {
            "cache": {"paths": ["node_modules/"]},
            "myjob": {"script": ["echo hi"], "cache": {}},
        }
        pipeline = proc.process_config(raw)
        assert pipeline.jobs[0].cache == []

    def test_list_of_entries_truncated_at_four(self):
        entries = [{"key": f"k{i}", "paths": [f"p{i}/"]} for i in range(6)]
        parsed = PipelineProcessor.parse_cache_entries(entries)
        assert len(parsed) == 4
        assert [c.key for c in parsed] == ["k0", "k1", "k2", "k3"]

    def test_key_files_and_prefix_parsed(self):
        parsed = PipelineProcessor.parse_cache_entries(
            {"key": {"files": ["poetry.lock", "pyproject.toml"], "prefix": "py"}, "paths": [".venv/"]}
        )
        assert parsed[0].key_files == ["poetry.lock", "pyproject.toml"]
        assert parsed[0].key_prefix == "py"
        assert parsed[0].key is None

    def test_key_files_truncated_at_two(self):
        parsed = PipelineProcessor.parse_cache_entries({"key": {"files": ["a", "b", "c"]}, "paths": ["out/"]})
        assert parsed[0].key_files == ["a", "b"]

    def test_invalid_policy_and_when_fall_back_to_defaults(self):
        parsed = PipelineProcessor.parse_cache_entries({"paths": ["x/"], "policy": "bogus", "when": "bogus"})
        assert parsed[0].policy == "pull-push"
        assert parsed[0].when == "on_success"

    def test_entry_without_paths_is_skipped(self):
        assert PipelineProcessor.parse_cache_entries({"key": "k"}) == []

    def test_non_dict_raw_yields_empty(self):
        assert PipelineProcessor.parse_cache_entries("nonsense") == []
        assert PipelineProcessor.parse_cache_entries(None) == []


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------


class TestKeyResolution:
    def test_literal_key(self, tmp_path):
        cache = CacheConfig(paths=["x/"], key="mykey")
        assert resolve_cache_key(cache, {}, tmp_path) == "mykey"

    def test_variable_expansion(self, tmp_path):
        cache = CacheConfig(paths=["x/"], key="$CI_COMMIT_REF_SLUG-deps")
        env = {"CI_COMMIT_REF_SLUG": "main"}
        assert resolve_cache_key(cache, env, tmp_path) == "main-deps"

    def test_braced_variable_expansion(self, tmp_path):
        cache = CacheConfig(paths=["x/"], key="${STAGE}_cache")
        assert resolve_cache_key(cache, {"STAGE": "build"}, tmp_path) == "build_cache"

    def test_unknown_variable_expands_empty(self, tmp_path):
        cache = CacheConfig(paths=["x/"], key="$NOPE")
        assert resolve_cache_key(cache, {}, tmp_path) == DEFAULT_CACHE_KEY

    def test_no_key_defaults_to_default(self, tmp_path):
        cache = CacheConfig(paths=["x/"])
        assert resolve_cache_key(cache, {}, tmp_path) == DEFAULT_CACHE_KEY

    def test_key_files_stable_when_content_unchanged(self, tmp_path):
        (tmp_path / "poetry.lock").write_text("lockfile v1")
        cache = CacheConfig(paths=["x/"], key_files=["poetry.lock"])
        k1 = resolve_cache_key(cache, {}, tmp_path)
        k2 = resolve_cache_key(cache, {}, tmp_path)
        assert k1 == k2

    def test_key_files_changes_when_content_changes(self, tmp_path):
        (tmp_path / "poetry.lock").write_text("lockfile v1")
        cache = CacheConfig(paths=["x/"], key_files=["poetry.lock"])
        k1 = resolve_cache_key(cache, {}, tmp_path)
        (tmp_path / "poetry.lock").write_text("lockfile v2")
        k2 = resolve_cache_key(cache, {}, tmp_path)
        assert k1 != k2

    def test_key_files_missing_file_treated_as_empty(self, tmp_path):
        cache = CacheConfig(paths=["x/"], key_files=["missing.lock"])
        # Must not raise; deterministic result
        assert resolve_cache_key(cache, {}, tmp_path) == resolve_cache_key(cache, {}, tmp_path)

    def test_key_files_prefix_prepended(self, tmp_path):
        (tmp_path / "a.lock").write_text("data")
        cache = CacheConfig(paths=["x/"], key_files=["a.lock"], key_prefix="py311")
        key = resolve_cache_key(cache, {}, tmp_path)
        assert key.startswith("py311-")

    def test_expand_variables_mixed_forms(self):
        env = {"A": "1", "B": "2"}
        assert expand_variables("$A and ${B} and $C", env) == "1 and 2 and "


class TestSanitizeKey:
    def test_safe_key_unchanged(self):
        assert sanitize_cache_key("main-deps_1.2") == "main-deps_1.2"

    def test_path_separators_hashed(self):
        s = sanitize_cache_key("feature/foo")
        assert "/" not in s and "\\" not in s

    def test_distinct_unsafe_keys_do_not_collide(self):
        assert sanitize_cache_key("feature/foo") != sanitize_cache_key("feature?foo")

    def test_long_key_shortened(self):
        s = sanitize_cache_key("x" * 300)
        assert len(s) <= 80

    def test_empty_key_still_valid_name(self):
        assert sanitize_cache_key("")


# ---------------------------------------------------------------------------
# Save / restore behaviour: policy and when
# ---------------------------------------------------------------------------


class TestPolicyAndWhen:
    def test_save_and_restore_roundtrip(self, tmp_path):
        store = make_store(tmp_path)
        src = tmp_path / "src"
        (src / "cached").mkdir(parents=True)
        (src / "cached" / "data.txt").write_text("payload")

        job = make_job(cache=[CacheConfig(paths=["cached/"])])
        save_caches(job, store, src, {}, succeeded=True)

        target = tmp_path / "target"
        target.mkdir()
        restore_caches(job, store, target, {})
        assert (target / "cached" / "data.txt").read_text() == "payload"

    def test_policy_pull_never_writes(self, tmp_path):
        store = make_store(tmp_path)
        src = tmp_path / "src"
        (src / "out").mkdir(parents=True)
        (src / "out" / "f.txt").write_text("x")

        job = make_job(cache=[CacheConfig(paths=["out/"], key="k", policy="pull")])
        save_caches(job, store, src, {}, succeeded=True)
        assert read_latest_generation(store, sanitize_cache_key("k")) is None

    def test_policy_push_never_restores(self, tmp_path):
        store = make_store(tmp_path)
        seed_cache(tmp_path, "k", "out/f.txt", "x")

        job = make_job(cache=[CacheConfig(paths=["out/"], key="k", policy="push")])
        target = tmp_path / "target"
        target.mkdir()
        restore_caches(job, store, target, {})
        assert not (target / "out").exists()

    def test_when_on_success_skips_save_on_failure(self, tmp_path):
        store = make_store(tmp_path)
        src = tmp_path / "src"
        (src / "out").mkdir(parents=True)
        (src / "out" / "f.txt").write_text("x")

        job = make_job(cache=[CacheConfig(paths=["out/"], key="k", when="on_success")])
        save_caches(job, store, src, {}, succeeded=False)
        assert read_latest_generation(store, sanitize_cache_key("k")) is None

    def test_when_on_failure_saves_only_on_failure(self, tmp_path):
        store = make_store(tmp_path)
        src = tmp_path / "src"
        (src / "out").mkdir(parents=True)
        (src / "out" / "f.txt").write_text("x")

        job = make_job(cache=[CacheConfig(paths=["out/"], key="k", when="on_failure")])
        save_caches(job, store, src, {}, succeeded=True)
        assert read_latest_generation(store, sanitize_cache_key("k")) is None
        save_caches(job, store, src, {}, succeeded=False)
        assert read_latest_generation(store, sanitize_cache_key("k")) is not None

    def test_when_always_saves_regardless(self, tmp_path):
        store = make_store(tmp_path)
        src = tmp_path / "src"
        (src / "out").mkdir(parents=True)
        (src / "out" / "f.txt").write_text("x")

        job = make_job(cache=[CacheConfig(paths=["out/"], key="k", when="always")])
        save_caches(job, store, src, {}, succeeded=False)
        assert read_latest_generation(store, sanitize_cache_key("k")) is not None

    def test_cache_miss_is_silent(self, tmp_path):
        store = make_store(tmp_path)
        target = tmp_path / "target"
        target.mkdir()
        cache = CacheConfig(paths=["out/"], key="never-saved")
        assert restore_cache_entry(cache, "never-saved", store, target) is False
        assert list(target.iterdir()) == []

    def test_no_matches_saves_nothing(self, tmp_path):
        store = make_store(tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        cache = CacheConfig(paths=["missing/**"], key="k")
        assert save_cache_entry(cache, "k", store, src) is False


# ---------------------------------------------------------------------------
# Lock behaviour
# ---------------------------------------------------------------------------


class TestLocking:
    def test_filelock_roundtrip(self, tmp_path):
        lock = FileLock(tmp_path / "a.lock", timeout=1.0)
        with lock:
            assert lock.fd is not None
        assert lock.fd is None

    def test_filelock_timeout_raises(self, tmp_path):
        path = tmp_path / "a.lock"
        holder = FileLock(path, timeout=1.0)
        holder.acquire()
        try:
            contender = FileLock(path, timeout=0.2)
            try:
                contender.acquire()
                raise AssertionError("expected FileLockTimeout")
            except FileLockTimeout:
                pass
        finally:
            holder.release()

    def test_restore_skipped_on_lock_timeout(self, tmp_path):
        store = make_store(tmp_path)
        seed_cache(tmp_path, "k", "out/f.txt", "x")
        skey = sanitize_cache_key("k")
        holder = FileLock(store / f"{skey}.lock", timeout=1.0)
        holder.acquire()
        try:
            target = tmp_path / "target"
            target.mkdir()
            cache = CacheConfig(paths=["out/"], key="k")
            # Skips with a warning instead of raising / failing the job.
            assert restore_cache_entry(cache, "k", store, target, lock_timeout=0.2) is False
            assert not (target / "out").exists()
        finally:
            holder.release()

    def test_save_skipped_on_lock_timeout(self, tmp_path):
        store = make_store(tmp_path)
        src = tmp_path / "src"
        (src / "out").mkdir(parents=True)
        (src / "out" / "f.txt").write_text("x")
        skey = sanitize_cache_key("k")
        holder = FileLock(store / f"{skey}.lock", timeout=1.0)
        holder.acquire()
        try:
            cache = CacheConfig(paths=["out/"], key="k")
            assert save_cache_entry(cache, "k", store, src, lock_timeout=0.2) is False
        finally:
            holder.release()


# ---------------------------------------------------------------------------
# Concurrency: two writers, one key, no partial state
# ---------------------------------------------------------------------------


def test_concurrent_saves_produce_complete_cache(tmp_path):
    store = make_store(tmp_path)
    n_files = 20
    writers = {}
    for tag in ("alpha", "beta"):
        src = tmp_path / f"src_{tag}"
        (src / "out").mkdir(parents=True)
        for i in range(n_files):
            (src / "out" / f"f{i}.txt").write_text(f"{tag}-{i}")
        writers[tag] = src

    cache = CacheConfig(paths=["out/"], key="shared")
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def save(tag: str) -> None:
        try:
            barrier.wait(timeout=10)
            assert save_cache_entry(cache, "shared", store, writers[tag], lock_timeout=30.0)
        except BaseException as exc:  # noqa: BLE001 - collected for the assert below
            errors.append(exc)

    threads = [threading.Thread(target=save, args=(tag,)) for tag in ("alpha", "beta")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors

    # The published cache must be exactly one writer's complete set.
    gen_dir = read_latest_generation(store, sanitize_cache_key("shared"))
    assert gen_dir is not None
    contents = {p.name: p.read_text() for p in (gen_dir / "out").iterdir()}
    assert len(contents) == n_files
    tags = {v.split("-")[0] for v in contents.values()}
    assert len(tags) == 1, f"interleaved cache content from writers: {tags}"


# ---------------------------------------------------------------------------
# Wiring: JobExecutor + pipeline E2E
# ---------------------------------------------------------------------------


CACHE_PIPELINE = """
cache_job:
  cache:
    key: e2e
    paths:
      - cached/
  script:
    - if [ -f cached/data.txt ]; then cp cached/data.txt restored_marker.txt; fi
    - mkdir -p cached
    - echo hello > cached/data.txt
"""


def write_ci(tmp_path: Path) -> None:
    (tmp_path / ".gitlab-ci.yml").write_text(CACHE_PIPELINE)


def test_e2e_second_run_sees_restored_files(tmp_path):
    """A pipeline run twice restores cached files before the script on run 2."""
    import shutil

    write_ci(tmp_path)
    runner = LocalGitLabRunner(tmp_path)

    runner.run_pipeline(maximum_degree_of_parallelism=1)
    assert not (tmp_path / "restored_marker.txt").exists()
    assert (make_store(tmp_path) / sanitize_cache_key("e2e")).exists()

    # Wipe the working copy of the cached dir; only the store survives.
    shutil.rmtree(tmp_path / "cached")

    runner.run_pipeline(maximum_degree_of_parallelism=1)
    assert (tmp_path / "restored_marker.txt").read_text().strip() == "hello"


def test_e2e_no_cache_flag_bypasses_restore(tmp_path):
    import shutil

    write_ci(tmp_path)
    runner = LocalGitLabRunner(tmp_path)

    runner.run_pipeline(maximum_degree_of_parallelism=1)
    shutil.rmtree(tmp_path / "cached")

    runner.run_pipeline(maximum_degree_of_parallelism=1, no_cache=True)
    assert not (tmp_path / "restored_marker.txt").exists()


def test_e2e_dry_run_touches_no_cache(tmp_path):
    write_ci(tmp_path)
    runner = LocalGitLabRunner(tmp_path)
    runner.run_pipeline(maximum_degree_of_parallelism=1, dry_run=True)
    assert not make_store(tmp_path).exists()


def test_cli_run_accepts_no_cache_flag():
    from bitrab.cli import create_parser

    args = create_parser().parse_args(["run", "--no-cache"])
    assert args.no_cache is True


def test_clean_what_cache(tmp_path):
    from bitrab.folder import clean_cache, scan_folder

    write_ci(tmp_path)
    runner = LocalGitLabRunner(tmp_path)
    runner.run_pipeline(maximum_degree_of_parallelism=1)

    summary = scan_folder(tmp_path)
    assert summary.cache_size_bytes > 0

    freed = clean_cache(tmp_path)
    assert freed > 0
    assert not make_store(tmp_path).exists()
    assert scan_folder(tmp_path).cache_size_bytes == 0


# ---------------------------------------------------------------------------
# Capability diagnostics
# ---------------------------------------------------------------------------


def test_supported_cache_produces_no_diagnostics():
    raw = {
        "cache": [{"key": "a", "paths": ["x/"]}],
        "job": {"script": ["echo hi"], "cache": {"key": {"files": ["a.lock"]}, "paths": ["y/"]}},
    }
    diags = check_capabilities(raw)
    assert not [d for d in diags if d.feature.startswith("cache")]


def test_unsupported_cache_subkeys_warn():
    raw = {
        "cache": {"paths": ["x/"], "untracked": True},
        "job": {
            "script": ["echo hi"],
            "cache": [{"paths": ["y/"], "fallback_keys": ["main"], "unprotect": True}],
        },
    }
    diags = check_capabilities(raw)
    features = {d.feature for d in diags if d.level == DiagnosticLevel.WARNING}
    assert {"cache:untracked", "cache:fallback_keys", "cache:unprotect"} <= features
