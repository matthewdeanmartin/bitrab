# Bitrab Roadmap

## Purpose

This roadmap turns the architecture review into an implementation sequence that fits Bitrab's actual product direction:

- local-first
- host-native
- faster feedback over perfect isolation
- explicit partial GitLab compatibility

The goal is not to turn Bitrab into a full GitLab Runner clone. The goal is to make it a trustworthy local CI interpreter with predictable behavior and good developer ergonomics.

## Guiding principles

### 1. Prefer honesty over accidental compatibility

If Bitrab does not support a GitLab concept, it should say so clearly and early.

### 2. Make local behavior predictable

Users can accept imperfect isolation if they understand the rules and the tool helps them avoid foot-guns.

### 3. Optimize the default path for day-to-day use

The default experience should be the safest, least surprising, and easiest to debug.

### 4. Separate execution from presentation

The terminal view is not the execution model. Logging and UI should be layered on top of runtime events.

## Recommended roadmap

## Phase 0: sharpen the product contract

This is the prerequisite for almost everything else.

### Goals

- make Bitrab's supported surface explicit
- reduce user confusion about what "GitLab compatible" means here
- prevent unsupported features from looking silently accepted

### Deliverables

- define and document "Bitrab capability validation"
- distinguish:
  - schema-valid GitLab YAML
  - supported by Bitrab
  - accepted with warnings
  - rejected
- explicitly reject unsupported modern GitLab features:
  - `include:component`
  - component `inputs`
  - unsupported remote includes
  - `workflow` / `rules` semantics that Bitrab cannot honor

### Why this phase matters

Right now, users can get a false sense that Bitrab is closer to GitLab behavior than it really is. Fixing that mismatch will improve trust immediately.

## Phase 1: stabilize local execution semantics

This is the highest-value technical phase.

### Goals

- make workspace behavior explicit
- reduce parallel foot-guns
- make environment handling understandable

### Deliverables

- introduce explicit workspace modes:
  - `shared`
  - `isolated`
- introduce explicit concurrency modes:
  - `serial`
  - `parallel`
- make the default:
  - `shared + serial`
- allow `parallel` primarily with `isolated`, or with an explicit user opt-in
- fix built-in CI variables so they reflect actual runtime context
- define and document env precedence and host pass-through policy

### Suggested policy

Default behavior should be:

- jobs run against the real checkout
- jobs are serialized unless the user opts into parallelism
- parallelism is treated as a convenience feature, not as a safety guarantee

This matches the local-first compromise better than pretending process pools provide meaningful isolation.

## Phase 2: improve runtime structure

This phase reduces coupling and makes future features easier.

### Goals

- stop passing implicit state between layers
- make execution more testable
- create room for future planning and UI work

### Deliverables

- add a `JobRuntimeContext` or `ExecutionContext`
- move resolved runtime state into that object:
  - workspace path
  - env snapshot
  - job metadata
  - retry settings
  - output routing
- make config normalization more explicit and less mutation-heavy
- create a richer intermediate plan object between raw YAML and execution

### Why this phase matters

A lot of Bitrab's current complexity is not in large code volume; it is in hidden coupling. Making runtime state explicit will pay off everywhere.

## Phase 3: fix output architecture

This is the main UX phase.

### Goals

- make parallel output readable
- keep CLI useful while opening the door to better UIs

### Deliverables

- emit structured execution events
- separate:
  - execution
  - log collection
  - rendering
- change parallel CLI behavior to buffered-per-job output by default
- in CI-style single-log environments, capture per-job output and emit completed jobs one at a time
- preserve live streaming for serial runs
- add summaries at stage/job completion

### Longer-term options

- Textual TUI with:
  - first tab for pipeline flow / job diagram
  - one tab per job log
  - visible job status and failure markers
- local web UI with concurrent logs, filters, and timelines

### Why this phase matters

A regular terminal is not a good medium for multiplexed parallel logs. The runtime should stop assuming otherwise.

## Phase 4: deepen planning and compatibility handling

This phase is where Bitrab grows up without trying to become GitLab.

### Goals

- support more useful planning logic
- keep unsupported features explicit
- avoid contaminating the executor with compatibility rules

### Deliverables

- richer normalized pipeline representation
- capability diagnostics attached to the execution plan
- room for future support of:
  - `needs`
  - smarter job filtering
  - partial conditional behavior
- clear "not supported" handling for features that remain out of scope

## Easy wins

These are relatively small changes with a high user-visible payoff.

### 1. Make `shared + serial` the default

This immediately reduces accidental race conditions and confusing local state issues.

### 2. Fail early on components and inputs

This avoids wasted debugging time on configs Bitrab is not ready for.

### 3. Fix `CI_PROJECT_DIR` and related built-ins

These should reflect the actual workspace/runtime path, not just `Path.cwd()`.

### 4. Document env precedence clearly

Even before deeper refactoring, a documented precedence model will reduce confusion.

### 5. Add a "parallel output is buffered" mode for CLI

Even a simple first version would be a major usability improvement.

### 6. In CI, emit completed jobs one at a time

For environments like GitHub Actions where everything lands in one container log, avoid interleaving entirely and print each job's output only when ready.

### 7. Improve Windows shell discovery

Allow configuring bash path or perform better discovery instead of relying on one hardcoded path.

### 8. Add capability warnings during `validate`

This gives users feedback before they run into runtime surprises.

### 9. Surface execution mode in output

Print something explicit like:

- workspace mode
- concurrency mode
- output mode
- env inheritance mode

That alone would make Bitrab feel much more intentional.

### 10. Add timeout support

Even a basic per-job timeout would make local runs safer and less annoying.

### 11. Add a shell/env debug view

A command or flag that prints the resolved execution context for a job would be extremely useful for debugging local issues.

## Better local experience ideas

These are improvements specifically aimed at making Bitrab nicer for real daily use.

## Safer defaults

- default to serial execution in shared workspaces
- require an explicit opt-in for risky parallel shared-workspace runs
- make dry-run output more realistic and more visible

## Better debugging

- add `bitrab debug-job <job>` or equivalent focused runtime inspection
- show resolved cwd, env mode, inherited vars, and script bundle
- show why a job was rejected or warned by capability validation

## Better logs

- serialize output per job in terminal mode
- in CI, capture then print one completed job at a time with no interleaving
- add an end-of-run job summary so failures are easy to relocate even if the detailed log is long
- add collapsible/folded summaries conceptually, even in plain text
- make failures print the most useful context first
- persist logs under `.bitrab\logs\`

## Better local UI

- use Textual as the likely local-first answer to parallel output
- show a first tab with the pipeline flow / job diagram
- show one tab per job for clean, non-interleaved spew
- keep status, retries, and failures visible without forcing users to scroll through mixed logs

## Better workspace ergonomics

- make it obvious where each job ran
- optionally preserve isolated workspaces after failures
- add cleanup controls for `.bitrab\`
- let users choose whether isolated workspaces reuse files or start fresh

## Better environment ergonomics

- add explicit env inheritance modes
- add allowlist/blocklist support
- show which vars Bitrab injects
- make it easy to reproduce a job outside Bitrab

## Better focus for local development

- finish `--jobs`
- add `--stage`
- add rerun-last-failed or rerun-single-job flows
- add clearer summaries for what ran, what was skipped, and why

## Better feedback loops

- keep startup overhead low
- validate capabilities before execution
- avoid process-pool work when not needed
- make command output consistent between dry-run and real-run

## Suggested implementation order

If you want the highest value per unit of effort, I would do the work in this order:

1. Make default execution `shared + serial`.
2. Add capability validation and reject components / inputs.
3. Fix runtime CI variables and document env precedence.
4. Finish `--jobs` and improve focused local reruns.
5. Buffer parallel CLI logs per job and add CI-safe completed-job emission.
6. Introduce `JobRuntimeContext`.
7. Add workspace modes and explicit env inheritance modes.
8. Build a Textual TUI on top of structured events, with a flow tab plus one tab per job.
9. Add a local web UI if the Textual model proves too limiting.

## What not to prioritize yet

These may be useful later, but they should not come before the items above:

- trying to emulate full GitLab container semantics
- partial/fragile support for components and inputs
- sophisticated parallel terminal streaming
- broad compatibility claims in docs before runtime behavior is nailed down

## Definition of success

This roadmap is working if Bitrab becomes:

- more predictable locally
- more honest about what it supports
- easier to debug
- safer by default
- more readable when jobs run in parallel

That is the right success bar for Bitrab's current niche.
