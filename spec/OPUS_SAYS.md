# Opus Says: Bitrab Strategic Technical Plan

## What this document is

An honest assessment of where bitrab stands, what's broken, what's missing, what's
achievable, and what order to do it in. Written after reading every source file,
every test, and the existing spec documents.

The existing `architecture-review.md`, `phases.md`, and `roadmap.md` are good
strategic documents. This document is more tactical: it names specific bugs,
specific GitLab features worth supporting, specific libraries to use, and assigns
work by difficulty so the right model handles the right task.

---

## Part 1: Bugs and Correctness Issues

These are things that are broken today and should be fixed before new features.

### BUG-1: `CI_PROJECT_DIR` lies

`VariableManager.prepare_environment()` sets `CI_PROJECT_DIR = str(Path.cwd())`.
This is the Python process's cwd at startup, not necessarily the project root.
When bitrab is invoked from a subdirectory, or when `--config` points elsewhere,
this is wrong.

**Fix:** Use `LocalGitLabRunner.base_path` (which is already resolved) and thread
it through to `VariableManager`. The variable should reflect the actual project
root, not the shell's cwd.

**Assign:** Any LLM (Sonnet, Haiku). Straightforward plumbing.

### BUG-2: `substitute_variables()` exists but is dead code

`VariableManager.substitute_variables()` implements `$VAR` / `${VAR}` expansion
but is never called. Bash does its own expansion at runtime, so this method is
misleading. Either wire it in for non-shell contexts (e.g., expanding variables
in `rules:if` expressions later) or delete it.

**Fix:** Delete it now. Resurrect a better version when `rules` support lands.

**Assign:** Any LLM. One-line deletion plus test cleanup.

### BUG-3: Three orchestrators doing overlapping work

There are now three execution paths:
- `StageOrchestrator` (execution/scheduler.py) -- plain streaming
- `TUIOrchestrator.execute_pipeline_tui()` -- Textual mode
- `TUIOrchestrator.execute_pipeline_ci()` -- CI file mode

They duplicate stage-loop logic, job directory creation, and failure semantics.
A bug fixed in one path may not be fixed in the others.

**Fix:** Extract a common `_execute_stages()` core that accepts a strategy/callback
for output routing. The three modes become thin wrappers around shared stage
iteration, cancellation, and failure-propagation logic.

**Assign:** Opus. This is an architectural refactor that requires holding the full
picture of all three paths, their subtle differences, and the multiprocessing
constraints.

### BUG-4: Config loader mutates its input

`ConfigurationLoader._process_includes()` and `_merge_configs()` mutate the
config dictionary in place. This means the same raw config dict can't be safely
reused (e.g., for dry-run preview then real execution).

**Fix:** Deep-copy before mutation, or switch to an immutable merge pattern.

**Assign:** Sonnet. Moderate refactor, well-scoped.

### BUG-5: Windows bash path is hardcoded

`shell.py` line 128: `_DEF_BASH_WINDOWS = r"C:\Program Files\Git\bin\bash.exe"`.
This works if Git for Windows is installed in the default location. It fails for
WSL users, scoop/chocolatey installs, or custom paths.

**Fix:** Search PATH first, then check common locations, then allow override via
`BITRAB_BASH_PATH` environment variable.

**Assign:** Any LLM. Simple search-order implementation.

### BUG-6: `--jobs` flag is parsed but does nothing useful

`cli.py` accepts `--jobs` but it's not wired into execution filtering. The flag
creates false user expectations.

**Fix:** See FEATURE-8 below.

---

## Part 2: Missing GitLab CI Features Worth Supporting

These are ordered by how much user value they deliver for a local-first tool.
Features that only make sense in a server context (e.g., `trigger`, `environment`,
`resource_group`) are deliberately excluded.

### FEATURE-1: `allow_failure` (high value, low effort)

**What GitLab does:** A job with `allow_failure: true` (or `allow_failure: exit_codes: [1, 2]`)
doesn't cause the pipeline to fail when it fails.

**Why it matters locally:** Many CI configs use this for optional checks (security
scans, style linters). Without it, bitrab fails the whole pipeline on the first
optional job.

**Implementation:**
- Add `allow_failure: bool` and `allow_failure_exit_codes: list[int]` to `JobConfig`
- Parse in `PipelineProcessor._process_job()`
- In orchestrator stage loop: catch job failure, check allow_failure, mark as
  "warning" instead of "failure" if allowed
- TUI: add a "warned" status icon (e.g., `"warned": "⚠️"`)

**Assign:** Sonnet. Clear spec, touches 4 files, no architectural risk.

### FEATURE-2: `when` (job execution condition) (high value, moderate effort)

**What GitLab does:** `when: manual`, `when: on_success` (default), `when: on_failure`,
`when: always`, `when: delayed`, `when: never`.

**What bitrab should support locally:**
- `on_success` (default) -- run if all prior jobs in stage succeeded
- `on_failure` -- run only if a prior job failed (useful for cleanup jobs)
- `always` -- always run regardless
- `manual` -- skip unless explicitly selected via `--jobs`
- `never` -- skip entirely (used with `rules:` to conditionally disable)
- `delayed` -- could be supported with a sleep, low priority

**Implementation:**
- Add `when: str = "on_success"` to `JobConfig`
- Parse in `PipelineProcessor._process_job()`
- In stage execution: filter jobs by `when` condition and prior stage outcome
- `manual` jobs appear in `bitrab list` with a `[manual]` tag but don't run
  unless `--jobs` selects them

**Assign:** Sonnet. The logic is branchy but well-specified.

### FEATURE-3: `rules` (conditional job inclusion) (high value, high effort)

**What GitLab does:** `rules:` is a list of conditions. Each rule has:
- `if:` -- a CI/CD variable expression (e.g., `$CI_COMMIT_BRANCH == "main"`)
- `changes:` -- file glob patterns (job runs if matching files changed)
- `exists:` -- file existence check
- `when:` -- what to do if the rule matches
- `allow_failure:` -- override per rule
- `variables:` -- override per rule

**What bitrab should support (Phase 1):**
- `rules: - when: always` / `when: never` / `when: manual` (static rules)
- `rules: - if: $VARIABLE == "value"` (simple variable comparison)
- `rules: - exists: ["path/to/file"]` (file existence)
- `rules: - changes: ["src/**"]` -- requires git diff, defer to Phase 2

**What to defer:** `changes:` with compare-to refs, complex boolean expressions,
`$CI_PIPELINE_SOURCE` matching.

**Implementation:**
- Add `rules: list[RuleConfig]` to `JobConfig` (new dataclass)
- Expression evaluator for simple `$VAR == "value"` and `$VAR != "value"`
- File existence check against project root
- Rules are evaluated in order; first match wins; no match = job excluded

**Assign:** Opus. Expression evaluation needs careful design. The rules engine
is the most complex single feature bitrab could add, and getting the semantics
wrong would be worse than not having it.

### FEATURE-4: `needs` and DAG execution (high value, high effort)

**What GitLab does:** `needs:` declares explicit job dependencies. Jobs run as
soon as their dependencies complete, ignoring stage boundaries.

**Why it matters:** Many real CI configs use `needs:` to speed up pipelines by
running independent jobs as early as possible.

**Implementation (using `graphlib.TopologicalSorter`):**

Python 3.9+ ships `graphlib.TopologicalSorter` which has a `get_ready()`/`done()`
API designed exactly for parallel scheduling:

```python
from graphlib import TopologicalSorter

ts = TopologicalSorter()
for job in pipeline.jobs:
    if job.needs:
        for dep in job.needs:
            ts.add(job.name, dep)
    else:
        ts.add(job.name)  # no deps = stage-only ordering

ts.prepare()
while ts.is_active():
    ready = ts.get_ready()     # jobs whose deps are all done
    # submit ready jobs to executor
    for completed_job in as_completed(futures):
        ts.done(completed_job)
```

**Model changes:**
- Add `needs: list[str] = field(default_factory=list)` to `JobConfig`
- Parse in `PipelineProcessor._process_job()`
- New `DagScheduler` class that uses `TopologicalSorter` instead of stage iteration
- When no jobs have `needs:`, fall back to current stage-based execution
- Cycle detection is free (TopologicalSorter raises `CycleError`)

**Key decision:** `graphlib` is stdlib (3.9+), zero-dependency, and purpose-built.
There is no reason to use networkx here. For Python 3.8 support, a backport
(`graphlib_backport`) exists on PyPI, but since bitrab's tooling already targets
py39 (`target-version = "py39"` in ruff/black configs), consider bumping the
minimum Python version to 3.9.

**Assign:** Opus. The scheduler is the heart of the execution engine. Getting the
interaction between DAG mode, cancellation, parallel output routing, and TUI
updates right requires holding the full system in context.

### FEATURE-5: `timeout` (moderate value, low effort)

**What GitLab does:** `timeout: 30m` or `timeout: 1h 30m` at job or pipeline level.

**Implementation:**
- Add `timeout: float | None = None` to `JobConfig` (in seconds)
- Parse duration strings (e.g., "30m" -> 1800, "1h 30m" -> 5400)
- In `shell.py` `run_bash()`: pass timeout to `Popen.communicate()` (capture mode)
  or set a timer thread that kills the process (stream mode)
- Raise `JobTimeoutError` on expiry

**Assign:** Sonnet. Well-defined, touches shell.py and job.py.

### FEATURE-6: `artifacts` (moderate value, moderate effort)

**What GitLab does:** `artifacts: paths:` defines files to preserve after a job.
`artifacts: reports:` feeds test/coverage reports into the GitLab UI.
`dependencies:` controls which artifacts are downloaded from previous jobs.

**What bitrab should support:**
- `artifacts: paths:` -- copy matching files to `.bitrab/artifacts/<job>/`
- `artifacts: when:` -- on_success, on_failure, always
- `dependencies:` -- copy artifacts from named jobs into the current job's workspace
- Skip `reports:` (no GitLab UI to feed into)

**Implementation:**
- Add `artifacts_paths: list[str]` and `artifacts_when: str` to `JobConfig`
- After job execution, glob matching files from project dir and copy to artifacts dir
- Before job execution, if `dependencies:` is set, copy artifacts from named jobs

**Assign:** Sonnet. File copying with globs, no deep architectural decisions.

### FEATURE-7: `cache` (low-to-moderate value, moderate effort)

**What GitLab does:** `cache: key:`, `cache: paths:` -- persists directories between
pipeline runs. Used for package manager caches (node_modules, .venv, etc.).

**What bitrab should support:**
- `cache: paths:` -- symlink or copy cache dirs to/from `.bitrab/cache/<key>/`
- `cache: key:` -- cache key (default: `default`)
- `cache: policy:` -- pull, push, pull-push

**Assign:** Sonnet. Straightforward file operations.

### FEATURE-8: `--jobs` filtering (high value, low effort)

**What it should do:** `bitrab run --jobs lint mypy` runs only the named jobs,
respecting their stage ordering.

**Implementation:**
- After `PipelineProcessor.process_config()`, filter `pipeline.jobs` to only
  those whose names match the `--jobs` list
- Warn if a requested job name doesn't exist
- Adjust stage list to only include stages that still have jobs

**Assign:** Any LLM. Straightforward list filtering.

### FEATURE-9: `--stage` filtering (moderate value, low effort)

`bitrab run --stage test` runs only jobs in the named stage(s).

**Assign:** Any LLM. Same pattern as --jobs.

---

## Part 3: Architectural Improvements

### ARCH-1: Unify the three orchestrators

See BUG-3 above. The core stage-iteration logic should be shared. The three modes
(streaming, TUI, CI-file) differ only in:
- How output is routed (stdout, queue, file)
- How status is reported (print, Textual message, none)
- Whether a TUI event loop is running

A strategy pattern or callback-based design would eliminate the duplication.

**Assign:** Opus.

### ARCH-2: Introduce `JobRuntimeContext`

A frozen object built once per job containing:
- Resolved environment dict
- Resolved workspace path
- Job metadata (name, stage, timeout, allow_failure)
- Output sink reference

This replaces the current pattern where `JobExecutor.execute_job()` receives
scattered parameters and rebuilds environment on every call.

**Assign:** Opus. Requires tracing all call sites and ensuring nothing breaks.

### ARCH-3: Capability validation layer

Add a phase between YAML loading and execution that checks:
- Are there `include: component:` entries? (reject)
- Are there `inputs:` blocks? (reject)
- Are there `rules:` with unsupported expressions? (warn)
- Are there `image:` or `services:` blocks? (warn: will be ignored)
- Are there `trigger:` jobs? (reject: can't trigger child pipelines locally)
- Are there `resource_group:` settings? (warn: no mutual exclusion locally)

Output: a list of `CapabilityDiagnostic(level, feature, message)` that can be
shown by `bitrab validate` and checked before `bitrab run`.

**Assign:** Sonnet. The logic is a series of dict key checks. The design (what
to warn vs. reject) is already specified in the existing spec documents.

### ARCH-4: Structured execution events

Currently, execution output goes to `TextWriter` (file-like objects). For the TUI,
this works through `QueueWriter`. But there's no structured representation of what
happened -- only raw text.

A structured event model would enable:
- Proper end-of-run summaries
- Log persistence
- Timing information
- Retry tracking
- Future web UI

Events:
```python
@dataclass
class PipelineEvent:
    timestamp: float
    event_type: str  # "stage_start", "job_start", "output", "job_end", "stage_end"
    stage: str | None
    job: str | None
    data: dict[str, Any]
```

This is a medium-term refactor, not urgent.

**Assign:** Opus. Requires redesigning the output flow across all execution paths.

### ARCH-5: Better `graph` command with real visualization

The `graph` command is stubbed. With DAG support (FEATURE-4), this becomes useful:

```
$ bitrab graph
lint ─────────┐
mypy ─────────┤
precommit ────┤
pytest ────────→ bandit ──→ build_package ──→ publish_pypi
              └─→ check_docs ──→ build_docs
```

Could use `rich.tree` or a simple box-drawing approach for terminal output.
Could optionally output DOT format for Graphviz.

**Assign:** Sonnet. After FEATURE-4 lands.

---

## Part 4: Isolation and Workspace Model

The architecture review correctly identifies this as a major gap. Here's my
concrete recommendation:

### Current state

Jobs run scripts from `project_dir` (the real repo checkout). Per-job directories
exist under `.bitrab/<job>/` but are only used for `CI_JOB_DIR`. There is no
filesystem isolation between jobs.

### Recommended model

**Default mode: `shared` (no change needed)**
- Jobs share the real working tree
- Serial execution prevents most conflicts
- This is the right default for "run my CI locally to see if it passes"

**Power-user mode: `isolated`**
- Each job gets a `git worktree` (or tarball copy) of the repo
- Requires explicit opt-in: `--workspace isolated`
- Enables safe parallel execution on file-mutating jobs
- Cleanup: `bitrab clean` removes worktrees

**Not recommended: Docker/container isolation**
- This is GitLab Runner territory, not bitrab's mission
- The complexity cost is enormous for marginal local-dev value
- If users need container isolation, they should use GitLab Runner directly

**Assign:** Opus for design, Sonnet for implementation of worktree-based isolation.

---

## Part 5: Phased Implementation Plan

### Phase A: Foundations (fix bugs, add low-hanging features)

| ID | Task | Effort | Assign |
|----|------|--------|--------|
| A1 | BUG-1: Fix CI_PROJECT_DIR | S | Any LLM |
| A2 | BUG-2: Delete dead substitute_variables() | XS | Any LLM |
| A3 | BUG-5: Windows bash path discovery | S | Any LLM |
| A4 | FEATURE-1: allow_failure | M | Sonnet |
| A5 | FEATURE-5: timeout support | M | Sonnet |
| A6 | FEATURE-8: --jobs filtering | S | Any LLM |
| A7 | FEATURE-9: --stage filtering | S | Any LLM |
| A8 | ARCH-3: Capability validation layer | M | Sonnet |
| A9 | End-of-run summary (job status table) | S | Any LLM |

**Estimated total: 1-2 focused sessions per task. All independent, parallelizable.**

### Phase B: Core GitLab features (when, rules, needs)

| ID | Task | Effort | Assign |
|----|------|--------|--------|
| B1 | FEATURE-2: `when` keyword support | M | Sonnet |
| B2 | FEATURE-3: `rules` engine (Phase 1: static, if, exists) | L | Opus |
| B3 | FEATURE-4: `needs` + DAG scheduling with graphlib | L | Opus |
| B4 | BUG-3/ARCH-1: Unify orchestrators | L | Opus |
| B5 | ARCH-2: JobRuntimeContext | M | Opus |

**Dependencies:**
- B1 should come before B2 (rules produce `when` values)
- B4 should come before B3 (cleaner base for DAG scheduler)
- B5 can happen any time but ideally before B3

### Phase C: Polish and ecosystem

| ID | Task | Effort | Assign |
|----|------|--------|--------|
| C1 | FEATURE-6: artifacts support | M | Sonnet |
| C2 | FEATURE-7: cache support | M | Sonnet |
| C3 | ARCH-4: Structured execution events | L | Opus |
| C4 | ARCH-5: graph command | M | Sonnet |
| C5 | BUG-4: Immutable config loading | S | Sonnet |
| C6 | Log persistence (.bitrab/logs/) | S | Any LLM |
| C7 | TUI flow tab (pipeline diagram) | M | Sonnet |
| C8 | rules Phase 2: changes: with git diff | L | Opus |

### Phase D: Advanced (optional, driven by user demand)

| ID | Task | Effort | Assign |
|----|------|--------|--------|
| D1 | Isolated workspace mode (git worktree) | L | Opus design, Sonnet impl |
| D2 | `parallel:` keyword (matrix jobs) | L | Opus |
| D3 | Remote include support (HTTP fetch) | M | Sonnet |
| D4 | Watch mode (re-run on file changes) | M | Sonnet |
| D5 | `extends:` keyword (job inheritance) | M | Sonnet |
| D6 | `!reference` tag support | M | Opus |

---

## Part 6: What NOT to Build

These are features that sound appealing but would be net-negative for bitrab:

1. **Docker/container execution.** This is GitLab Runner. The complexity would
   dwarf the rest of the codebase. Users who need containers should run the real
   GitLab Runner.

2. **Full `workflow:rules`** at pipeline level. The local execution model is
   fundamentally different (no pipeline source, no merge request context). Partial
   emulation would be misleading.

3. **`trigger:` child pipelines.** Multi-project/child pipeline orchestration is
   a server-side concern.

4. **`services:`** (sidecar containers like postgres, redis). Same as Docker --
   this requires container orchestration.

5. **Remote API linting** (`bitrab lint`). GitLab's lint API requires authentication
   and network access. Users can use `glab ci lint` instead. Don't duplicate.

6. **Sophisticated variable expansion** (nested expansion, `$$`, file variables).
   Bash already handles this. Don't re-implement a shell.

---

## Part 7: The DAG Question in Detail

> Can we do DAG? Could it be done with a nice library?

**Yes, and yes.** Here's the concrete design:

### Library: `graphlib.TopologicalSorter` (stdlib, Python 3.9+)

This is the right choice. It's:
- Zero dependency (stdlib since 3.9)
- Designed for parallel scheduling (get_ready/done pattern)
- Handles cycle detection automatically
- Small API surface, easy to test

### How it fits into bitrab

```
.gitlab-ci.yml            PipelineProcessor           DagScheduler
                          (adds needs to JobConfig)    (uses TopologicalSorter)
  lint:                        |                           |
    stage: test                |                     ts.add("lint")
                               |                     ts.add("build", "lint")
  build:                       |                     ts.add("deploy", "build")
    stage: build               |                           |
    needs: [lint]              |                     ts.prepare()
                               |                     while ts.is_active():
  deploy:                      |                       ready = ts.get_ready()
    stage: deploy              |                       # run ready jobs
    needs: [build]             |                       ts.done(completed)
```

### Fallback behavior

When no job declares `needs:`, the current stage-based sequential execution
applies. This is also what GitLab does -- `needs:` is opt-in DAG mode.

### Mixed mode

GitLab allows some jobs to have `needs:` and others not. Jobs without `needs:`
wait for their entire previous stage to complete. This is achievable with
`TopologicalSorter` by adding synthetic dependencies:

```python
# For a job without needs:, add dependency on all jobs in prior stages
for prior_stage in stages_before(job.stage):
    for prior_job in jobs_in_stage(prior_stage):
        ts.add(job.name, prior_job.name)
```

### Python version consideration

bitrab's `pyproject.toml` says `requires-python = ">=3.8"` but all tooling
configs target py39. `graphlib` is stdlib in 3.9. Options:
1. Bump minimum to 3.9 (recommended -- 3.8 is EOL since Oct 2024)
2. Use `graphlib_backport` package for 3.8 support

**Recommendation:** Bump to 3.9. Python 3.8 is dead.

---

## Part 8: Assignment Guide

### What Opus should handle

Opus is for tasks where getting the design wrong is expensive, where multiple
subsystems interact, or where the problem space is ambiguous:

- **Rules engine** (FEATURE-3) -- expression evaluation, first-match semantics,
  interaction with `when`, `allow_failure`, `variables` overrides
- **DAG scheduler** (FEATURE-4) -- TopologicalSorter integration, mixed-mode
  stage/needs execution, cancellation, output routing
- **Orchestrator unification** (ARCH-1/BUG-3) -- the three code paths have subtle
  differences; merging them requires understanding all edge cases
- **JobRuntimeContext** (ARCH-2) -- touches every layer of the execution stack
- **Structured events** (ARCH-4) -- redesigns the output flow
- **Workspace isolation design** (Part 4) -- architectural decision with
  long-term implications

### What Sonnet should handle

Sonnet is for tasks with clear specs, well-defined boundaries, and moderate
complexity:

- **allow_failure** (FEATURE-1)
- **when** (FEATURE-2)
- **timeout** (FEATURE-5)
- **artifacts** (FEATURE-6)
- **cache** (FEATURE-7)
- **Capability validation** (ARCH-3)
- **graph command** (ARCH-5)
- **Config immutability** (BUG-4)
- **TUI flow tab** (C7)
- **extends: keyword** (D5)

### What any experienced LLM (including Haiku) should handle

These are mechanical, well-scoped tasks with obvious acceptance criteria:

- **Fix CI_PROJECT_DIR** (BUG-1)
- **Delete dead code** (BUG-2)
- **Windows bash discovery** (BUG-5)
- **--jobs filtering** (FEATURE-8)
- **--stage filtering** (FEATURE-9)
- **End-of-run summary** (A9)
- **Log persistence** (C6)

---

## Part 9: Priority Stack Rank

If I had to pick the single most impactful order to attack this:

1. **A6: --jobs filtering** -- unlocks the most common local workflow ("just run this one job")
2. **A4: allow_failure** -- stops pipelines from failing on optional jobs
3. **A8: Capability validation** -- stops confusing silent failures
4. **A1: Fix CI_PROJECT_DIR** -- correctness matters
5. **B1: when keyword** -- manual jobs, failure handlers, always-run cleanup
6. **B4: Unify orchestrators** -- pay down tech debt before adding complexity
7. **B3: DAG with graphlib** -- real speedup for complex pipelines
8. **B2: rules engine** -- conditional job execution
9. **A5: timeout** -- safety net for hanging jobs
10. **C1: artifacts** -- inter-job data passing

Everything else follows from user demand.

---

## Appendix: GitLab CI Feature Support Matrix

| Feature | Status | Priority | Phase |
|---------|--------|----------|-------|
| stages | Supported | -- | -- |
| script / before_script / after_script | Supported | -- | -- |
| variables (global, default, job) | Supported | -- | -- |
| retry (max, when, exit_codes) | Supported | -- | -- |
| include: local | Supported | -- | -- |
| allow_failure | **Not supported** | High | A |
| when (on_success, manual, etc.) | **Not supported** | High | B |
| rules (if, exists) | **Not supported** | High | B |
| needs (DAG) | **Not supported** | High | B |
| timeout | **Not supported** | Medium | A |
| artifacts: paths | **Not supported** | Medium | C |
| cache | **Not supported** | Low-Med | C |
| dependencies | **Not supported** | Medium | C |
| extends | **Not supported** | Medium | D |
| !reference | **Not supported** | Medium | D |
| parallel (matrix) | **Not supported** | Low | D |
| include: remote | **Not supported** | Low | D |
| include: template | **Not supported** | Low | D |
| include: component | Not planned | -- | -- |
| image / services | Not planned | -- | -- |
| trigger | Not planned | -- | -- |
| environment | Not planned | -- | -- |
| resource_group | Not planned | -- | -- |
| workflow: rules | Not planned | -- | -- |
| pages | Not planned | -- | -- |
| release | Not planned | -- | -- |
