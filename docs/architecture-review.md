# Bitrab Architecture Review

## Executive summary

Bitrab has a good core shape for a local-first tool: the codebase is small, easy to trace, and the main execution path
is understandable. The current architecture is already good enough for experimentation and local debugging, but it is
not yet a trustworthy long-term foundation for "GitLab-like" behavior.

The biggest weaknesses are not raw complexity; they are boundary problems:

- the product surface suggests more GitLab compatibility than the runtime can safely provide
- execution semantics are underspecified around workspace isolation, repo visibility, and parallel safety
- environment-variable behavior is inconsistent and partly unwired
- parallel terminal output is fighting the medium instead of being modeled explicitly
- unsupported features are often parsed or ignored instead of being surfaced as intentional non-goals

My recommendation is to lean into a clear product stance:

> Bitrab is a local-first GitLab CI interpreter, not a GitLab runner clone.

That means favoring fast local usability, predictable failure modes, and explicit capability boundaries over trying to
emulate all of GitLab.

## Current architecture map

### CLI and top-level orchestration

The CLI entrypoint is straightforward:

- `bitrab\__main__.py`
- `bitrab\cli.py`

`bitrab.cli` handles argument parsing, user-facing commands, and basic error presentation. `cmd_run()` creates a
`LocalGitLabRunner`, which then loads config, processes it, creates execution helpers, and runs the pipeline.

### Config loading and validation

Configuration responsibilities are split across:

- `bitrab\config\loader.py`
- `bitrab\config\validate_pipeline.py`
- `bitrab\config\schema.py`

`ConfigurationLoader` loads YAML and recursively merges local includes. `GitLabCIValidator` validates against GitLab's
JSON schema, using network fetch, temp cache, and packaged fallback schema logic.

This is useful, but "schema-valid" and "supported by Bitrab" are not the same thing, and the current architecture does
not strongly separate those concerns.

### Planning / normalization

Pipeline normalization currently lives in:

- `bitrab\plan.py`
- `bitrab\models\pipeline.py`

`PipelineProcessor` converts raw YAML into `PipelineConfig`, `JobConfig`, and `DefaultConfig`. This is the closest thing
to a planning layer, but today it is still fairly thin. It mostly copies fields into dataclasses and merges a few
variable/script defaults.

### Execution stack

Execution is split across:

- `bitrab\execution\variables.py`
- `bitrab\execution\job.py`
- `bitrab\execution\scheduler.py`
- `bitrab\execution\shell.py`

The flow is:

1. `VariableManager` prepares environment dictionaries.
2. `JobExecutor` combines scripts and invokes the shell runner.
3. `StageOrchestrator` groups jobs by stage and runs them serially or in parallel.
4. `run_bash()` handles streaming or captured subprocess execution.

This is the right rough layering, but the abstractions are still leaky.

## What is working well

### 1. The codebase is small and comprehensible

This is a real strength. A new contributor can understand the whole system quickly. That matters a lot for a tool whose
value is developer trust.

### 2. The execution path is explicit

There is no mystery framework here. It is easy to trace `cli -> runner -> processor -> executor -> shell`.

### 3. There is already a useful distinction between planning and execution

Even though it is incomplete, `PipelineProcessor` vs `JobExecutor` is the correct architectural seam.

### 4. Tests are exercising behavior, not just structure

The tests around shell behavior and runner behavior are already catching semantics that matter locally, especially
around retries, streaming, includes, and job history.

## Main weaknesses

## 1. Product boundary is blurry

This is the highest-level architectural issue.

The README and schema validation create an impression of broad GitLab compatibility, but the runtime only implements a
subset. In code, unsupported or partially supported concepts are often:

- reserved and ignored
- schema-validated but not executable
- described as "parsed but limited"

Examples:

- `bitrab\plan.py` reserves `image`, `services`, `cache`, and `artifacts`
- the README says `only`, `except`, and `rules` are parsed but not enforced
- remote includes are not supported, but schema validation may still make users think they are close

This leads to the worst kind of mismatch: a config can look accepted while behavior is materially different.

### Recommendation

Add a first-class "capability boundary" phase after schema validation and before execution. That phase should:

- detect unsupported GitLab constructs
- classify them as `error`, `warning`, or `ignored`
- explain why Bitrab is not executing them

For the features you explicitly said you are not ready for, especially GitLab components and inputs, Bitrab should fail
clearly and early rather than half-parsing or silently ignoring them.

## 2. Execution isolation model is implicit and internally inconsistent

This is the most important runtime weakness.

Today, jobs often run in per-job directories under `.bitrab\<job-name>` via `StageOrchestrator`, which is useful for
avoiding some file clobbering. But this is not a real isolation model:

- the process still inherits the host environment
- jobs can still reach outside their working directory
- no filesystem snapshot or checkout is created
- the repo working copy is not modeled explicitly as a workspace
- `CI_PROJECT_DIR` is currently based on `Path.cwd()` in `VariableManager`, not the actual execution directory or runner
  base path
- the advertised project directory and the job's actual working directory can therefore disagree

So the system is in an awkward middle ground: it is not GitLab-style isolated, but it is also not clearly "shared
workspace with safe serialization."

### Recommendation

Make execution policy an explicit architectural concept. I would recommend two supported workspace modes:

- `shared`: run against the real repo checkout; safest default is serialized execution
- `isolated`: materialize a per-job workspace for jobs that may run in parallel

For Bitrab's stated goals, I would optimize for:

- local-first experience
- fast feedback
- predictable results

That suggests this default policy:

- default to serialized execution in shared workspace mode
- allow parallel execution only when the user opts in, or when using isolated workspaces

That is a much better compromise than pretending local subprocesses can safely approximate container isolation.

## 3. Environment-variable handling is messy and partly disconnected

You called this out, and the code supports that concern.

Current state:

- `VariableManager.prepare_environment()` starts from `os.environ.copy()`
- then overlays built-in CI variables
- then overlays pipeline variables
- then job variables
- `substitute_variables()` exists, but does not appear to be wired into execution

This creates several problems:

- there is no single documented precedence model users can rely on
- host environment leakage is the default
- built-in CI vars are incomplete and sometimes misleading
- `CI_PROJECT_DIR` does not reflect workspace reality
- there is no explicit pass-through policy for host variables
- the existence of `substitute_variables()` implies one expansion model, while actual shell execution relies on bash
  runtime expansion

The best part is that the current behavior is simple; the risky part is that it is simple in an implicit way.

### Recommendation

Introduce an immutable `ExecutionContext` or `JobRuntimeContext` object that is built once per job and contains:

- resolved workspace path
- resolved environment snapshot
- built-in CI variables
- job metadata
- output/log routing metadata

Document a strict env precedence order, for example:

1. Bitrab built-ins
2. host pass-through env
3. pipeline variables
4. default variables
5. job variables
6. explicit CLI overrides

Then make host pass-through configurable:

- `inherit-all` for convenience
- `inherit-none` for reproducibility
- allowlist/blocklist options for practical middle ground

Also: do not try to pre-substitute shell syntax unless Bitrab is deliberately implementing a full templating layer. For
local execution, passing env to the shell is the safer model.

## 4. Parallel output is a rendering problem, not just a subprocess problem

This is another big architectural issue.

`run_bash()` has both `stream` and `capture` modes, and `StageOrchestrator` may run jobs in parallel. In practice, that
means concurrent job output competes for a single terminal. Even if the subprocess code is technically correct, the UX
is still bad:

- interleaved stdout/stderr is hard to read
- parallel jobs destroy log locality
- failures are harder to interpret
- terminal output becomes timing-dependent

The tests already show this tension: some tests need capture mode for determinism.

### Recommendation

Model logs as structured events, then render them differently depending on UI:

- terminal CLI: serialize displayed output by job, or show one active job at a time with buffered summaries for others
- TUI: prefer a Textual tabbed interface, with one tab per job log and a first tab showing the stage/job flow diagram
- local web UI: full concurrent stream view, job timelines, and filtered logs

In other words, separate:

- job execution
- log collection
- log rendering

Today those concerns are still mixed together.

For the plain terminal, I would strongly recommend defaulting to buffered-per-job output when jobs run in parallel.
Real-time multiplexed streams are almost never worth the readability cost.

For CI environments such as GitHub Actions, I would go even further: avoid live interleaving entirely. Capture each job's output, emit it only when that job is complete, and print jobs one at a time in a stable order, followed by a job summary at the end. That gives up some immediacy, but it makes the logs much more usable when everything is sharing one container log.

## 5. Process-based parallelism is carrying too much architectural meaning

`StageOrchestrator` uses `ProcessPoolExecutor` for parallel stage execution. That works, but it is currently doing
several jobs at once:

- providing concurrency
- approximating isolation
- determining failure semantics
- affecting logging behavior

That is too much weight for one mechanism.

It also introduces awkwardness:

- pickling concerns
- state handoff via `job_history`
- platform-specific multiprocessing behavior
- brittle interactions with test runners and child process environments

### Recommendation

Treat concurrency policy separately from isolation policy.

You likely want an execution engine that can support:

- serial execution
- local subprocess parallelism
- future isolated workspace parallelism

without forcing those concerns to be identical.

## 6. Planning is too thin for future GitLab feature growth

Right now, `PipelineProcessor` mostly reshapes config and merges defaults. That is fine for the current feature set, but
it will not scale well to:

- `needs`
- DAG planning
- conditional execution
- feature gating
- capability diagnostics
- future compatibility layers

GitLab components and inputs are the clearest example. Even if you do not support them, the architecture should have a
place where unsupported higher-level config features are recognized intentionally.

### Recommendation

Add a richer intermediate representation between raw YAML and execution. That layer should carry:

- normalized job definitions
- resolved includes
- capability warnings/errors
- execution plan metadata
- workspace/isolation requirements
- concurrency hints

This would let Bitrab become more honest and more flexible without becoming much larger.

## 7. Operational hazards exist outside the main happy path

There are a few implementation details that are not the deepest architectural issues, but they will keep causing
friction:

- `StageOrchestrator` accepts `dry_run` but does not meaningfully use it as an orchestration policy
- there is no first-class timeout / cancellation model for hanging jobs
- the bash path on Windows is hardcoded to a common Git Bash location
- config loading mutates the config dictionary during include processing

Each of these makes the runtime a little less predictable than it should be for a local developer tool.

### Recommendation

Pull these into explicit runtime policy as the architecture matures:

- timeout support
- cancellation semantics
- shell discovery / override rules
- immutable config normalization after load

## 8. CLI command layer is growing into a god module

`bitrab\cli.py` currently owns:

- argument parsing
- command dispatch
- validation presentation
- JSON output shaping
- debug output
- error handling

It is still manageable, but it is on track to become the dumping ground for every feature.

### Recommendation

Split command handlers into dedicated modules once the next major feature wave starts. The current size is still
acceptable, but the trend is clear.

## 9. Validation architecture mixes external truth with local truth

Schema validation against GitLab's official schema is useful, but Bitrab's real contract is not "valid GitLab YAML"; it
is "GitLab-like YAML that Bitrab can interpret locally."

Right now the validation story is:

- first ask GitLab's schema
- then do a small amount of semantic checking

That means validation authority is tilted toward GitLab, while runtime authority is Bitrab.

### Recommendation

Make validation explicitly two-tier:

- `schema validation`: is this recognizable GitLab CI YAML?
- `Bitrab capability validation`: is this safe and meaningful for Bitrab to run?

Those should be separate, named concepts in both code and UX.

## Architectural direction I recommend

## 1. Adopt an explicit product stance

Bitrab should say, in code and docs:

- local-first
- host-native
- not container-accurate
- intentionally partial GitLab compatibility

That clarity will improve both the code and the UX.

## 2. Add first-class execution policy

Introduce explicit runtime choices such as:

- workspace mode: `shared`, `isolated`
- concurrency mode: `serial`, `parallel`
- logging mode: `live`, `buffered`, `ui`
- env mode: `inherit-all`, `inherit-none`, `allowlist`

Even if only some combinations are initially supported, this is the right architecture.

## 3. Fail clearly on unsupported modern GitLab features

For now, reject with actionable messages:

- `include:component`
- component `inputs`
- remote includes you do not support
- rules/workflow semantics you cannot honor

That is much better than letting schema validation imply support.

## 4. Move to structured events for execution

Have execution emit events like:

- job started
- script started
- stdout chunk
- stderr chunk
- retry scheduled
- job finished

Then let the terminal, TUI, or local web UI decide how to present them.

This is the cleanest path toward fixing the parallel output problem.

In particular, it opens the door to a strong local UI path with Textual:

- first tab: a pipeline diagram / stage-and-job flow view
- one tab per running or completed job
- stable per-job log streams without interleaving
- summaries and failure markers that stay visible after completion

## 5. Unify runtime state into a per-job context object

A per-job runtime object should own:

- resolved workspace
- resolved env
- job metadata
- retry settings
- output sink

That would reduce hidden coupling between `VariableManager`, `JobExecutor`, `StageOrchestrator`, and `shell.py`.

## Suggested near-term roadmap

### Phase 1: clarify truth and boundaries

- add Bitrab capability validation
- explicitly reject components and inputs
- document supported and unsupported semantics more strictly

### Phase 2: stabilize local execution

- introduce explicit workspace modes
- default shared workspaces to serialized execution
- fix `CI_PROJECT_DIR` and related built-ins to reflect actual runtime context
- define env precedence and pass-through rules

### Phase 3: fix output architecture

- collect structured log events
- buffer per-job logs for parallel CLI mode
- add a TUI or local web renderer when ready

### Phase 4: deepen planning

- add richer normalized pipeline/execution plan objects
- make room for future support of more advanced GitLab concepts without contaminating the executor

## Concrete decisions I would make now

If I were choosing defaults today, I would pick:

- shared workspace + serial execution as the default "safe local" mode
- isolated workspace + optional parallel execution as the power-user mode
- shell env injection, not string pre-substitution
- clear errors for unsupported GitLab features, especially components and inputs
- buffered job logs for parallel terminal mode
- TUI or local web UI as the long-term answer for concurrent output

That would match the compromise you described: local-first developer experience over perfect isolation, while still
giving users tools to avoid parallel foot-guns.

## Key files reviewed

- `bitrab\cli.py`
- `bitrab\plan.py`
- `bitrab\models\pipeline.py`
- `bitrab\config\loader.py`
- `bitrab\config\validate_pipeline.py`
- `bitrab\config\schema.py`
- `bitrab\execution\variables.py`
- `bitrab\execution\job.py`
- `bitrab\execution\scheduler.py`
- `bitrab\execution\shell.py`
- `test\test_best_effort_runner.py`
- `test\test_best_effort_runner_grok5.py`
- `test\test_shell.py`
