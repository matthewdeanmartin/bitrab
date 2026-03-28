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

## [0.2.1] - 2026-03-28

### Added

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

### Removed

- Dropped support for Python 3.8. Minimum required version is now 3.9.


## [0.2.0] - 2026-03-27

### Added

- Supports more gitlab syntax, validation command to tell you what syntax will be ignored.


## [0.1.0] - 2025-09-07

### Added

- Initial runner
