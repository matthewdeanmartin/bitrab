# Opus Says: Bitrab Strategic Technical Plan

## What this document is

An honest assessment of where bitrab stands, what's left to do, and in what
order. Updated after a comprehensive code review of every source file, test,
and spec document at version 0.2.1.

The companion documents (`architecture-review.md`, `phases.md`, `roadmap.md`)
provide strategic context. This document is tactical: it names specific issues,
specific GitLab features worth adding, and assigns work by difficulty.

---

## Part 1: What Got Done (removed from active tracking)

These items from the original plan have been implemented and verified:

- **BUG-1 (CI_PROJECT_DIR):** Fixed. `VariableManager` now receives
  `project_dir` from `LocalGitLabRunner.base_path` and uses it correctly.
- **BUG-2 (dead `substitute_variables()`):** Fixed. The method has been deleted
  from `VariableManager`.
- **BUG-3 / ARCH-1 (three orchestrators):** Fixed. `StagePipelineRunner` in
  `stage_runner.py` is the single shared engine. The three modes (streaming,
  TUI, CI) are thin wrappers via the `PipelineCallbacks` protocol.
- **BUG-5 (Windows bash path):** Fixed. `shell.py` now searches
  `BITRAB_BASH_PATH` env var, then PATH, then common candidate locations.
- **BUG-6 / FEATURE-8 (`--jobs` filtering):** Done. `filter_pipeline()` in
  `plan.py` handles it; `cmd_run` warns on unknown names.
- **FEATURE-1 (`allow_failure`):** Done. Parsed from YAML (bool and
  `exit_codes` dict), propagated through `JobConfig`, honored in
  `_is_failure_allowed()` in `stage_runner.py`.
- **FEATURE-2 (`when` keyword):** Done. Parsed in `PipelineProcessor`, filtered
  in `_filter_jobs_by_when()`. Supports `on_success`, `on_failure`, `always`,
  `manual`, `never`.
- **FEATURE-3 (`rules`, Phase 1):** Partially done -- see "Rules Scrutiny"
  below for what's missing.
- **FEATURE-4 (`needs` / DAG):** Done. `DagPipelineRunner` uses
  `graphlib.TopologicalSorter`. Mixed mode (stage-based fallback for jobs
  without `needs:`) works correctly. Auto-switches when any job has `needs:`.
- **FEATURE-5 (`timeout`):** Done. `parse_duration()` handles GitLab's format.
  Enforced in both capture and stream modes via deadline threading in `shell.py`.
- **FEATURE-6 (`artifacts`):** Done. `artifacts.py` implements
  `collect_artifacts()` and `inject_dependencies()` with proper `when`
  conditions and glob support.
- **FEATURE-9 (`--stage` filtering):** Done. Same `filter_pipeline()` function,
  wired through `cmd_run`.
- **ARCH-2 (`JobRuntimeContext`):** Done. Frozen dataclass in `job.py`, built
  once per job via `JobExecutor.build_context()`.
- **ARCH-3 (Capability validation):** Done. `capabilities.py` checks for
  unsupported features and emits structured `CapabilityDiagnostic` objects.
  Surfaced in `bitrab validate`.
- **Python 3.9 minimum:** Done. `requires-python = ">=3.9"` in pyproject.toml.
  `graphlib` used from stdlib.
- **A9 (end-of-run summary):** Handled via `PipelineCallbacks.on_pipeline_complete`.
- **ARCH-4 (Structured execution events):** Done. `EventCollector` in
  `execution/events.py` wraps any `PipelineCallbacks` and records typed
  `PipelineEvent` objects (9 event types) with monotonic timestamps. All three
  execution modes (streaming, TUI, CI) now wrap their callbacks with
  `EventCollector`. `PipelineSummary.from_events()` builds per-job/stage
  timing summaries; `format_text()` is printed at end of every run.
- **ARCH-5 (`graph` command):** Done. `bitrab graph` renders pipeline stages
  and jobs. `--format text` (default) gives an ASCII terminal tree with DAG
  `↳ needs:` annotations. `--format dot` emits Graphviz DOT with stage
  clusters, colored nodes, and stage/needs edges.

---

## Part 2: Rules Scrutiny

The `rules` implementation was started by another LLM. The core is solid but
has gaps that need attention.

### What works

- `rules:` list is parsed from YAML into `RuleConfig` objects
- First-match semantics are correct (matches GitLab behavior)
- No-match correctly sets `when: never`
- `if:` expressions support: `$VAR` (existence), `==`, `!=`, `=~`, `!~`
- Rule match correctly overrides `when`, `allow_failure`, `variables`, `needs`
- Default `when: on_success` when rule matches without explicit `when` (correct)
- Tests cover the main paths: skip on no match, match on global variable,
  variable override, needs override

### Issues found

~~**RULES-1: `rules: exists` is claimed but not implemented**~~

Done. `RuleConfig` has an `exists` field; `plan.py` parses it; `_rule_matches()`
checks file existence via glob against project root. `capabilities.py` moved
`exists` to `_SUPPORTED_RULES_KEYS`. Both `if:` and `exists:` must pass (AND
semantics). 9 new tests in `test_rules.py::TestRulesExists`.

~~**RULES-2: `&&` / `||` compound expressions don't work**~~

Done (option 1). `_evaluate_if()` now splits on `&&`/`||` at the top level
(respecting quoted strings), with `&&` binding tighter than `||`. Covers the
vast majority of real-world compound rules. 13 new tests in
`test_rules.py::TestCompoundExpressions`.

~~**RULES-3: The docstring lies about `&&`/`||` support**~~

Done. Docstring updated before this session started.

~~**RULES-4: Fallback behavior on unrecognized expressions is silent**~~

Done. `logger.warning(...)` added before this session started.

---

## Part 3: Validation Is Opt-In

Schema validation (`bitrab validate`) runs the GitLab CI JSON schema checker.
This is valuable for catching typos, but most users will share the same
`.gitlab-ci.yml` between GitLab and local bitrab runs. These files will
commonly contain keys that bitrab ignores but are valid GitLab CI (like
`image:`, `services:`, `workflow:`).

**Current behavior:** `bitrab validate` runs schema validation followed by
capability checks. `bitrab run` does NOT run schema validation -- it just
loads and processes the YAML. This is the correct default.

**What users should know:** The following GitLab CI features are parsed without
error but have no effect when running locally:

| Feature                            | What happens locally                                |
|------------------------------------|-----------------------------------------------------|
| `image:`                           | Ignored. Jobs run on the host, not in containers.   |
| `services:`                        | Ignored. No sidecar containers are started.         |
| `workflow:rules`                   | Ignored. No pipeline-source context exists locally. |
| `environment:`                     | Ignored. No deployment tracking.                    |
| `resource_group:`                  | Ignored. No mutual exclusion between pipelines.     |
| `pages`                            | Script runs but no GitLab Pages deployment occurs.  |
| `release:`                         | Ignored. No GitLab release API locally.             |
| `cache:`                           | Not yet implemented.                                |
| `include: remote/template/project` | Skipped. Only `include: local` works.               |
| `rules: changes`                   | Not evaluated. Rule is treated as non-matching.     |
| `only` / `except`                  | Parsed but ignored.                                 |

The following features will cause errors if you try to run them:

| Feature              | Why it fails                                          |
|----------------------|-------------------------------------------------------|
| `include: component` | Cannot resolve component references locally.          |
| `inputs:`            | Pipeline/job inputs not supported.                    |
| `trigger:`           | Cannot trigger child/multi-project pipelines locally. |

`bitrab validate` surfaces all of these via the capability checker. Run it
when you want to understand what will and won't work locally.

---

## Part 4: Remaining Work

### Still TODO from the original plan

| ID        | Task                              | Effort | Assign                 | Notes                            |
|-----------|-----------------------------------|--------|------------------------|----------------------------------|
| ~~RULES-1~~ | ~~`rules: exists` implementation~~    | ~~S~~ | ~~Sonnet~~ | Done — see Part 2 |
| ~~RULES-2~~ | ~~`&&`/`\|\|` compound expressions~~ | ~~M-L~~ | ~~Sonnet~~ | Done — see Part 2 |
| ~~RULES-3~~ | ~~Fix misleading docstring~~          | ~~XS~~ | ~~Any LLM~~ | Done |
| ~~RULES-4~~ | ~~Warn on unrecognized expressions~~  | ~~XS~~ | ~~Any LLM~~ | Done |
| ~~BUG-4~~   | ~~Config loader mutates input~~       | ~~S~~ | ~~Sonnet~~ | Done — deep-copy in `process_config()` |
| FEATURE-7 | `cache:` support                  | M      | Sonnet                 | symlink/copy to `.bitrab/cache/` |
| ~~ARCH-4~~    | ~~Structured execution events~~ | ~~L~~ | ~~Opus~~           | Done — see Part 1                |
| ~~ARCH-5~~    | ~~`graph` command~~             | ~~M~~ | ~~Sonnet~~         | Done — see Part 1                |
| C6        | Log persistence (`.bitrab/logs/`) | S      | Any LLM                |                                  |
| C7        | TUI flow tab                      | M      | Sonnet                 | Pipeline diagram in TUI          |
| C8        | `rules: changes` with git diff    | L      | Opus                   | Phase 2 rules                    |

### Phase D (optional, user-demand driven)

| ID | Task                                   | Effort | Assign                   |
|----|----------------------------------------|--------|--------------------------|
| D1 | Isolated workspace mode (git worktree) | L      | Opus design, Sonnet impl |
| D2 | `parallel:` keyword (matrix jobs)      | L      | Opus                     |
| D3 | Remote include support (HTTP fetch)    | M      | Sonnet                   |
| D4 | Watch mode (re-run on file changes)    | M      | Sonnet                   |
| D5 | `extends:` keyword (job inheritance)   | M      | Sonnet                   |
| D6 | `!reference` tag support               | M      | Opus                     |

---

## Part 5: What NOT to Build

Unchanged from the original assessment. These remain net-negative for bitrab:

1. **Docker/container execution.** This is GitLab Runner's job.
2. **Full `workflow:rules`** at pipeline level. No local pipeline-source context.
3. **`trigger:` child pipelines.** Server-side concern.
4. **`services:` sidecar containers.** Requires container orchestration.
5. **Remote API linting.** Use `glab ci lint` instead.
6. **Sophisticated variable expansion** (nested `$$`, file variables). Bash
   already handles this.

---

## Part 6: Priority Stack Rank (updated)

What's left, in impact order:

1. **FEATURE-7: `cache:`** -- common in real configs
2. **C8: `rules: changes`** -- requires git integration
3. **C6: Log persistence** -- quality of life
4. **C7: TUI flow tab** -- pipeline diagram in TUI

---

## Appendix: GitLab CI Feature Support Matrix (updated)

| Feature                                              | Status            | Notes                            |
|------------------------------------------------------|-------------------|----------------------------------|
| stages                                               | Supported         |                                  |
| script / before_script / after_script                | Supported         |                                  |
| variables (global, default, job)                     | Supported         |                                  |
| retry (max, when, exit_codes)                        | Supported         |                                  |
| include: local                                       | Supported         |                                  |
| allow_failure (bool + exit_codes)                    | Supported         |                                  |
| when (on_success, on_failure, always, manual, never) | Supported         |                                  |
| rules: if (simple + `&&`/`\|\|` compound)            | Supported         |                                  |
| rules: when / allow_failure / variables / needs      | Supported         |                                  |
| rules: exists                                        | Supported         | Glob patterns relative to root   |
| rules: changes                                       | Not supported     | Deferred (needs git diff)        |
| needs (DAG)                                          | Supported         | Via `graphlib.TopologicalSorter` |
| timeout                                              | Supported         | Duration string parsing          |
| artifacts: paths + when                              | Supported         |                                  |
| dependencies                                         | Supported         | None / [] / [list]               |
| --jobs / --stage filtering                           | Supported         |                                  |
| cache                                                | **Not supported** | Planned                          |
| extends                                              | Not supported     | Phase D                          |
| !reference                                           | Not supported     | Phase D                          |
| parallel (matrix)                                    | Not supported     | Phase D                          |
| include: remote/template                             | Not supported     | Skipped with warning             |
| include: component                                   | Not planned       | Error                            |
| image / services                                     | Ignored           | Warning via validate             |
| trigger                                              | Not planned       | Error                            |
| environment                                          | Ignored           | Warning via validate             |
| resource_group                                       | Ignored           | Warning via validate             |
| workflow: rules                                      | Ignored           | Warning via validate             |
| only / except                                        | Ignored           | Parsed but not evaluated         |
