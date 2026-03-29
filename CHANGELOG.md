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

## [Unreleased]

### Added

- **RULES-1: `rules: exists` support.** Rules can now include an `exists:` list
  of file glob patterns. A rule matches only if at least one listed path exists
  under the project root. Both `if:` and `exists:` must pass when both are
  present (AND semantics), matching GitLab CI behavior.
- **RULES-2: `&&` / `||` compound `if` expressions.** `rules: if:` expressions
  now support `&&` and `||` at the top level. `&&` binds tighter than `||`
  (standard precedence). Quoted string values containing `&&`/`||` are not split.
  Covers the vast majority of real-world compound rules without parentheses.

### Fixed

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
