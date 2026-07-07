# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-07-07
### Added
- GitLab `!reference` support resolved against the merged include graph before `extends`, including nested references, list splicing, scalar lookup, missing-target diagnostics, depth limits, and circular-reference detection.
- `workflow: rules` pipeline gating using the shared rule evaluator. Matching variables merge into pipeline/job variables; `when: never` skips validation cleanly and gives `run` the distinct exit code 3.
- Local `resource_group:` enforcement through cross-process locks under `.bitrab/locks/`, shared across threads, processes, worktrees, and concurrent bitrab runs. Lock waits use the configured job timeout.
- Remote include hygiene: urllib3 retry/backoff for connection, read, and 5xx failures; a 5 MiB response limit; and a ten-minute atomic TTL cache under `.bitrab/include-cache/`, bypassed with `--no-include-cache` and kept deliberately distinct from vendor snapshots.
- `rules: changes` evaluation with slash-aware GitLab-style globs, bare-list and `paths:` forms, and `compare_to:` overrides. Local baselines use an explicit ref when configured, otherwise the merge-base with the detected default branch, and always include committed, staged, unstaged, and untracked non-ignored files; unevaluable repositories conservatively run the job with a warning.
- `bitrab run --changed` / `--changes-base REF` for selecting jobs whose fingerprint or `changes:` inputs intersect the local change set, plus unknown-input jobs and transitive `needs:` dependents. `rules:changes` patterns now also participate in fingerprint input precedence.
- `bitrab install-hook` and `--uninstall` for an idempotent, marker-managed pre-push shell hook running `bitrab run --changed --incremental --no-tui`. Existing shell hooks are chained and preserved; foreign non-shell hooks are refused.
- `bitrab vendor` and `bitrab vendor --check` for recursively snapshotting remote includes under `.bitrab/vendor/` with URL provenance and SHA-256 hashes in `.bitrab/vendor.lock`. Writes are atomic and guarded by the shared cross-platform file lock; unchanged refreshes preserve timestamps, while upstream hash changes are reported prominently.
- `bitrab run --offline` and `bitrab validate --offline`. Remote includes resolve only from hash-verified vendor snapshots, unlocked URLs fail with an actionable error, and validation does not fall back to downloading a schema. Normal loads prefer locked snapshots over the network.
- Local execution of `cache:`. Cached paths are restored before `before_script` and saved after scripts into `.bitrab/cache/<key>/` under the project root (shared across parallel worktree jobs). Supports `paths:`, `key:` (with `$VAR` expansion), `key: files:` (max 2) + `prefix:`, `policy:` (`pull-push`/`pull`/`push`), `when:` (`on_success`/`on_failure`/`always`), lists of up to 4 cache entries, and job-level wholesale override of the top-level/default `cache:` (`cache: []` disables). Saves stage to a temp directory and publish atomically via a generation directory plus a `latest` pointer rewritten with `os.replace`, guarded by per-key advisory file locks (`msvcrt` on Windows, `fcntl` on POSIX) so readers never see a partially written cache; on lock timeout the cache step is skipped with a warning instead of failing the job. Unsupported sub-keys (`untracked:`, `unprotect:`, `fallback_keys:`) are ignored with a capability warning; the blanket "cache is not executed" warning is gone.
- `bitrab run --no-cache` to bypass cache restore and save for a run.
- `bitrab clean --what cache` / `bitrab folder clean --what cache`, and cache size reporting in `bitrab folder status`.
- Cross-platform advisory file lock helper `bitrab.utils.filelock.FileLock` with timeout (reused by upcoming fingerprint/vendor stores).
- Job fingerprint memoization via `bitrab run --incremental`. Before each job, bitrab computes a SHA-256 fingerprint over the resolved scripts, job variables, the values of user-declared `[tool.bitrab] fingerprint_env` names from `pyproject.toml`, a content digest of input files (`BITRAB_FINGERPRINT_PATHS` globs > `cache: key: files:` > all git-tracked files via git's own blob hashes plus a dirty-diff hash), the fingerprints of all `needs:`/dependency jobs (transitive invalidation), and the bitrab/schema version. Jobs whose fingerprint matches a recorded success are skipped with a distinct `cached` status (counted separately in the summary), still satisfy `needs:`, and still inject their previously collected artifacts downstream; a missing artifact directory is a miss. Only successful jobs record fingerprints — failures, dry runs, and jobs flagged by mutation detection never do. Works in stage mode, DAG mode, serial/parallel, and the TUI/CI output paths. The fingerprint cannot see outside-world changes (network, system packages, tool upgrades); this is documented and the feature is strictly opt-in. Store: `.bitrab/fingerprints/<job>.json` under the project root, written atomically (temp file + `os.replace`) under per-job advisory locks; corrupt or stale records are treated as misses, never errors.
- `bitrab run --refresh` (with `--incremental`) to force every job to run while still recording fresh fingerprints, and `bitrab run --dry-run --incremental` to report which jobs *would* be memoized.
- `bitrab clean --what fingerprints` / `bitrab folder clean --what fingerprints`, and fingerprint store size reporting in `bitrab folder status`.

## [0.4.0] - 2026-04-26
### Added
- Improved documentation

### Changed
- Naming convention improvements

### Fixed
- Performance improvements

## [0.3.0] - 2026-03-30
### Added
- Remote include support for HTTP/HTTPS-fetched YAML via `include: remote:` and `include: url:` entries using `urllib3` and `certifi`. Fetched YAML is parsed in memory, merged like a local include, and can itself contain further includes. Duplicate URLs within the same load are de-duplicated. `bitrab validate` no longer warns on `remote:` includes; `template:` includes still warn.
- Watch mode via `bitrab watch` subcommand. Runs the pipeline once immediately, then watches `.gitlab-ci.yml` and all transitively included local files for changes, re-running automatically on each save. Uses the `watchdog` library. Features a 1-second debounce to coalesce rapid saves. TUI mode is disabled in watch mode. Stop with Ctrl+C.
- Extends keyword for job inheritance. Jobs can inherit from other jobs or hidden template jobs (keys starting with `.`) via `extends:`. Supports a single string or a list of parents (later parents take precedence; child overrides all). Merges are deep on dicts; lists and scalars are fully replaced by the child, matching GitLab CI semantics. Multi-level chains are resolved in the correct order. Circular references and references to unknown templates raise a clear error. Hidden template jobs are excluded from the final pipeline job list.
- Structured event system for execution lifecycle via `EventCollector`, which wraps any `PipelineCallbacks` instance and records a typed `PipelineEvent` for every lifecycle hook. Events carry monotonic timestamps, wall-clock times, and a typed data payload.

### Fixed
- Dotenv file loading for GitLab CI/CD variable simulation. `VariableManager` now loads `.env` and `.bitrab.env` from the project root at startup. Variables from these files are available to all jobs without putting secrets in `.gitlab-ci.yml`. Resolution order: `os.environ` → built-in CI vars → `.env` → `.bitrab.env` → pipeline `variables:` (pipeline variables always win). A `parse_dotenv()` helper handles comments, blank lines, `export KEY=VAL` prefix, and single/double-quoted values.
- Artifacts reports dotenv integration. Jobs that write a dotenv file via `artifacts: reports: dotenv:` now have those variables collected and injected into downstream jobs that depend on them via `dependencies:` or the default inherit-all behaviour. Works in both stage-mode and DAG-mode, serial and parallel.
- Git-derived CI variables now auto-populated. `CI_COMMIT_SHA`, `CI_COMMIT_BRANCH`, `CI_COMMIT_TAG`, `CI_COMMIT_REF_NAME`, `CI_COMMIT_REF_SLUG`, `CI_COMMIT_SHORT_SHA`, `CI_COMMIT_TITLE`, `CI_COMMIT_MESSAGE`, `CI_COMMIT_AUTHOR`, `CI_COMMIT_TIMESTAMP`, `CI_PROJECT_NAMESPACE`, `CI_PROJECT_PATH`, `CI_PROJECT_PATH_SLUG`, `CI_PROJECT_URL`, `CI_PIPELINE_ID`, `CI_PIPELINE_SOURCE`, `CI_JOB_ID`, `GITLAB_CI`, and `CI_SERVER` are now populated automatically by running `git` commands against the project directory. All values fall back to empty string when git is unavailable or the directory is not a repo. Rules like `$CI_COMMIT_BRANCH == "main"` now evaluate correctly locally without manual variable overrides.
- DAG dry-run no longer creates `.bitrab/` job directories, injects artifact dependencies, takes mutation snapshots, or collects artifacts when `--dry-run` is active. DAG-mode and stage-mode dry-run now have identical side-effect policies.
- Config path consistency across all commands. All commands (`run`, `validate`, `list`, `graph`, `debug`, `watch`) now go through a single `resolve_config_path()` helper that prefers `.bitrab-ci.yml` over `.gitlab-ci.yml` when both exist.
- Component includes now hard-fail at load time instead of silently skipping. The loader now raises `GitlabRunnerError` immediately, matching the ERROR-level intent already declared by the capability checker. `include: template` and `include: project` keep their existing warn-and-skip behaviour.
- `validate --json` now emits pure JSON on stdout. All human-readable progress text is redirected to stderr when `--json` is active, so `bitrab validate --json | jq .` works without filtering.
- Float division in `_human_size()` size formatter. The formatter used integer floor division, causing values like 1.9 KB to display as 1.0 KB. It now converts to float first.
- Added detailed block comments to `DiagnosticLevel` in `capabilities.py` explaining the two-tier ERROR vs WARNING design.
- Added matching comment in `cmd_validate()` explaining why capability diagnostics are informational-only notes rather than validation failures.

### Changed
- Package description updated in `pyproject.toml` from the stale "Compile bash to gitlab pipeline yaml" to "Run GitLab CI pipelines locally".

## [0.2.0] - 2026-03-29
### Added
- Rules `exists` support for file glob patterns. Rules can now include an `exists:` list of file glob patterns. A rule matches only if at least one listed path exists under the project root. Both `if:` and `exists:` must pass when both are present (AND semantics), matching GitLab CI behavior.
- Compound `if` expressions with `&&` and `||` at the top level of `rules: if:`. `&&` binds tighter than `||` (standard precedence). Quoted string values containing `&&`/`||` are not split. Covers the vast majority of real-world compound rules without parentheses.
- Allow failure support with `exit_codes` dict. Jobs with `allow_failure: true` no longer fail the pipeline.
- When keyword support (`on_success`, `on_failure`, `always`, `manual`, `never`). Jobs are filtered by condition and prior stage outcome.
- Rules engine with `if` expressions using `$VAR`, `==`, `!=`, `=~`, `!~`; first-match semantics; overrides for `when`, `allow_failure`, `variables`, `needs`.
- Needs and DAG execution via `graphlib.TopologicalSorter`. Jobs run as soon as their dependencies complete, ignoring stage boundaries. Mixed mode (stage-based fallback for jobs without `needs:`) supported.
- Timeout support with GitLab-compatible duration parsing (`30m`, `1h 30m`). Enforced in both capture and streaming modes.
- Artifacts paths and `when` support. Files matching glob patterns are collected to `.bitrab/artifacts/<job>/` after job execution.
- Dependencies support (`None` = all, `[]` = none, `[list]` = specific). Artifacts from dependency jobs are injected before job execution.
- Job filtering with `--jobs` and `--stage` options for `bitrab run`. Warns on unknown names.
- `JobRuntimeContext` frozen dataclass: pre-computed environment built once per job, replacing scattered parameters.
- Capability validation layer via `bitrab validate`: structured diagnostics for unsupported GitLab CI features (errors vs. warnings).
- Unified pipeline execution engine (`StagePipelineRunner`) with pluggable `PipelineCallbacks` protocol. Streaming, TUI, and CI modes are thin wrappers.
- `bitrab graph` command renders a visual representation of the pipeline's stages and jobs. `--format text` (default) outputs an ASCII terminal tree with stage headers, job bullets, separators, `needs:` annotations, and attribute labels. `--format dot` outputs Graphviz DOT with stages as labeled clusters, color-coded nodes for manual/allow_failure jobs, and edges following `needs:` or stage ordering.
- Windows bash discovery: searches `BITRAB_BASH_PATH` env, then PATH, then common install locations (Git for Windows, MSYS2).
- Log management.
- Validation command to report which GitLab CI syntax will be ignored.

### Fixed
- `CI_PROJECT_DIR` now uses the resolved project root (`base_path`) instead of the Python process's working directory.
- Config loader no longer mutates the caller's dict. `PipelineProcessor.process_config()` now deep-copies `raw_config` before processing, so calling code that holds a reference to the original dict is not affected.
- Removed dead `substitute_variables()` method from `VariableManager`.

### Removed
- Dropped support for Python 3.8. Minimum required version is now 3.9.

## [0.1.0] - 2025-09-07
### Added
- Initial runner implementation
- Retry mechanism
- Unit test friendly stdout

[0.4.0]: https://github.com/matthewdeanmartin/bitrab/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/matthewdeanmartin/bitrab/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/matthewdeanmartin/bitrab/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/matthewdeanmartin/bitrab/releases/tag/v0.1.0
