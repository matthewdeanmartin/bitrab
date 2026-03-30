# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

- Added for new features.
- Changed for changes in existing functionality.
- Deprecated for soon-to-be removed features.
- Removed for now removed features.
- Fixed for any bug fixes.
- Security in case of vulnerabilities.

## [0.3.0] - 2026-03-29

### Added

- **D3: Remote include support.** `include: remote:` and `include: url:` entries
  are now fetched over HTTP/HTTPS using `urllib3` + `certifi` (both already
  runtime dependencies). Fetched YAML is parsed in memory, merged exactly like
  a local include, and can itself contain further includes. Duplicate URLs
  within the same load are de-duplicated. `bitrab validate` no longer warns on
  `remote:` includes; `template:` includes still warn (unsupported).
- **D4: Watch mode (`bitrab watch`).** New subcommand that runs the pipeline
  once immediately, then watches `.gitlab-ci.yml` and all transitively included
  local files for changes, re-running automatically on each save. Uses the
  `watchdog` library (now a runtime dependency). Features a 1-second debounce
  to coalesce rapid saves. TUI mode is disabled in watch mode (incompatible
  event loops). Stop with Ctrl+C.
- **D5: `extends:` keyword (job inheritance).** Jobs can now inherit from other
  jobs or hidden template jobs (keys starting with `.`) via `extends:`. Supports
  a single string or a list of parents (later parents take precedence; child
  overrides all). Merges are deep on dicts; lists and scalars are fully replaced
  by the child — matching GitLab CI semantics. Multi-level chains are resolved
  in the correct order. Circular references and references to unknown templates
  raise a clear error. Hidden template jobs (`.name`) are excluded from the
  final pipeline job list.

### Fixed

- **`.env` file loading (GitLab CI/CD Settings simulation).** `VariableManager` now loads `.env`
  and `.bitrab.env` from the project root at startup, simulating GitLab's CI/CD project-level
  variables. Variables from these files are available to all jobs without putting secrets in
  `.gitlab-ci.yml`. Resolution order: `os.environ` → built-in CI vars → `.env` → `.bitrab.env`
  → pipeline `variables:` (pipeline variables always win). A `parse_dotenv()` helper handles
  comments, blank lines, `export KEY=VAL` prefix, and single/double-quoted values.
- **`artifacts: reports: dotenv:` (pipeline variable passing).** Jobs that write a dotenv file
  via `artifacts: reports: dotenv: deploy.env` now have those variables collected and injected
  into downstream jobs that depend on them (via `dependencies:` or the default "inherit all"
  behaviour). This is GitLab's mechanism for passing dynamic values between jobs (e.g. a build
  job writing a computed version tag that deploy jobs then read). Works in both stage-mode and
  DAG-mode, serial and parallel.
- **Git-derived CI variables now auto-populated.** `CI_COMMIT_SHA`, `CI_COMMIT_BRANCH`, `CI_COMMIT_TAG`,
  `CI_COMMIT_REF_NAME`, `CI_COMMIT_REF_SLUG`, `CI_COMMIT_SHORT_SHA`, `CI_COMMIT_TITLE`,
  `CI_COMMIT_MESSAGE`, `CI_COMMIT_AUTHOR`, `CI_COMMIT_TIMESTAMP`, `CI_PROJECT_NAMESPACE`,
  `CI_PROJECT_PATH`, `CI_PROJECT_PATH_SLUG`, `CI_PROJECT_URL`, `CI_PIPELINE_ID`, `CI_PIPELINE_SOURCE`,
  `CI_JOB_ID`, `GITLAB_CI`, and `CI_SERVER` are now populated automatically by running `git` commands
  against the project directory. All values fall back to empty string when git is unavailable or the
  directory is not a repo — matching GitLab semantics (`$CI_COMMIT_TAG` is empty on untagged commits,
  etc.). Rules like `$CI_COMMIT_BRANCH == "main"` now evaluate correctly locally without any manual
  variable overrides.
- **DAG dry-run side effects.** `--dry-run` with a DAG pipeline (jobs that use `needs:`) no longer
  creates `.bitrab/` job directories, injects artifact dependencies, takes mutation snapshots, or
  collects artifacts. DAG-mode and stage-mode dry-run now have identical side-effect policies.
- **Config path inconsistency across commands.** All commands (`run`, `validate`, `list`, `graph`,
  `debug`, `watch`) now go through a single `resolve_config_path()` helper that prefers
  `.bitrab-ci.yml` over `.gitlab-ci.yml` when both exist. Previously only `run` applied this
  preference, so a user could validate one file and run another without realising it.
- **`include: component` now hard-fails at load time.** Previously the loader silently skipped
  component includes (a `continue` that made entire job sets disappear without warning). The loader
  now raises `GitlabRunnerError` immediately, matching the ERROR-level intent already declared by the
  capability checker.  `include: template` and `include: project` keep their existing warn-and-skip
  behaviour.
- **`validate --json` now emits pure JSON on stdout.** All human-readable progress text is redirected
  to stderr when `--json` is active, so `bitrab validate --json | jq .` works without filtering.
- **`_human_size()` float division.** The size formatter used integer floor division, causing values
  like 1.9 KB to display as 1.0 KB. It now converts to float first.
- Added detailed block comments to `DiagnosticLevel` in `capabilities.py` explaining the two-tier
  ERROR vs WARNING design: ERROR features are enforced by the loader (hard stop); WARNING features
  are intentionally skipped so one `.gitlab-ci.yml` works both locally and in GitLab.
- Added matching comment in `cmd_validate()` explaining why capability diagnostics are
  informational-only notes rather than validation failures.

### Changed

- **Package description updated.** `pyproject.toml` description changed from the stale
  "Compile bash to gitlab pipeline yaml" to "Run GitLab CI pipelines locally".

## [0.2.0] - 2026-03-28

### Added

- **RULES-1: `rules: exists` support.** Rules can now include an `exists:` list
  of file glob patterns. A rule matches only if at least one listed path exists
  under the project root. Both `if:` and `exists:` must pass when both are
  present (AND semantics), matching GitLab CI behavior.
- **RULES-2: `&&` / `||` compound `if` expressions.** `rules: if:` expressions
  now support `&&` and `||` at the top level. `&&` binds tighter than `||`
  (standard precedence). Quoted string values containing `&&`/`||` are not split.
  Covers the vast majority of real-world compound rules without parentheses.
- Log management
- Supports more gitlab syntax, validation command to tell you what syntax will be ignored.
- `allow_failure` support (bool and `exit_codes` dict). Jobs with
  `allow_failure: true` no longer fail the pipeline.
- `when` keyword support (`on_success`, `on_failure`, `always`, `manual`,
  `never`). Jobs are filtered by condition and prior stage outcome.
- `rules` engine (Phase 1): `if` expressions with `$VAR`, `==`, `!=`, `=~`,
  `!~`; first-match semantics; overrides for `when`, `allow_failure`,
  `variables`, `needs`.
- `needs` / DAG execution via `graphlib.TopologicalSorter`. Jobs run as soon
  as their dependencies complete, ignoring stage boundaries. Mixed mode
  (stage-based fallback for jobs without `needs:`) supported.
- `timeout` support with GitLab-compatible duration parsing (`30m`, `1h 30m`).
  Enforced in both capture and streaming modes.
- `artifacts: paths` and `artifacts: when` support. Files matching glob
  patterns are collected to `.bitrab/artifacts/<job>/` after job execution.
- `dependencies` support (`None` = all, `[]` = none, `[list]` = specific).
  Artifacts from dependency jobs are injected before job execution.
- `--jobs` and `--stage` filtering for `bitrab run`. Warns on unknown names.
- `JobRuntimeContext` frozen dataclass: pre-computed environment built once per
  job, replacing scattered parameters.
- Capability validation layer (`bitrab validate`): structured diagnostics for
  unsupported GitLab CI features (errors vs. warnings).
- Unified pipeline execution engine (`StagePipelineRunner`) with pluggable
  `PipelineCallbacks` protocol. Streaming, TUI, and CI modes are thin wrappers.
- Windows bash discovery: searches `BITRAB_BASH_PATH` env, then PATH, then
  common install locations (Git for Windows, MSYS2).

### Fixed

- `CI_PROJECT_DIR` now uses the resolved project root (`base_path`) instead of
  the Python process's working directory.
- Removed dead `substitute_variables()` method from `VariableManager`.
- **BUG-4: Config loader no longer mutates caller's dict.** `PipelineProcessor.process_config()`
  now deep-copies `raw_config` before processing, so calling code that holds a
  reference to the original dict is not affected.

- **ARCH-4: Structured execution events.** `EventCollector` wraps any
  `PipelineCallbacks` instance and records a typed `PipelineEvent` for every
  lifecycle hook (pipeline start/complete, stage start/skip/complete, job
  start/complete, cancellation, awaiting-manual). Events carry monotonic
  timestamps, wall-clock times, and a typed `data` payload.
- `PipelineSummary`: built from events via `from_events()`. Holds per-job and
  per-stage timing, status (`success`/`failed`/`allowed_failure`), and flags
  for cancellation and awaiting-manual state. `format_text()` renders a concise
  human-readable summary printed at the end of every execution mode.
- **ARCH-5: `bitrab graph` command.** Renders a visual representation of the
  pipeline's stages and jobs. Supports two output formats:
    - `--format text` (default): ASCII terminal tree with stage headers, job
      bullets, `↓` separators between stages, `↳ needs:` annotations for DAG
      dependencies, `allow_failure`/`when` attribute labels, and a summary line.
    - `--format dot`: Graphviz DOT output. Stages become labeled clusters; jobs
      become nodes with color coding (yellow for `when: manual`, salmon for
      `allow_failure`); edges follow `needs:` in DAG mode or stage ordering
      otherwise. Pipe output to `dot -Tpng` or paste into any Graphviz viewer.

### Removed

- Dropped support for Python 3.8. Minimum required version is now 3.9.

## [0.1.0] - 2025-09-07

### Added

- Initial runner
