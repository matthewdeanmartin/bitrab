# Bitrab Phased Backlog

## Purpose

This document breaks the roadmap into issue-sized tasks that can be implemented and reviewed incrementally.

The priorities assume Bitrab stays:

- local-first
- host-native
- explicitly partial in GitLab compatibility
- focused on predictable behavior over perfect isolation

## How to use this document

Each task is intended to be small enough to become a standalone issue or PR.

The rough pattern is:

- one behavior change
- one focused surface area
- clear acceptance criteria

## Phase 0: product truth and capability boundaries

Goal: make it obvious what Bitrab supports, warns on, and rejects.

### P0-1: Add Bitrab capability validation layer

Create a validation phase after schema validation that checks whether a config is meaningfully supported by Bitrab.

Acceptance criteria:

- `validate` reports capability results separately from schema validity
- unsupported features can be classified as warning or error
- capability validation is reusable by both `validate` and `run`

### P0-2: Reject `include:component`

Add explicit detection and rejection for GitLab component includes.

Acceptance criteria:

- configs using `include:component` fail clearly
- the error explains that Bitrab does not support components yet
- tests cover the rejection path

### P0-3: Reject component `inputs`

Add explicit detection and rejection for component inputs.

Acceptance criteria:

- configs using component inputs fail clearly
- the message explains that Bitrab is not ready for inputs
- tests cover the rejection path

### P0-4: Reject unsupported remote include forms

Make remote include handling explicit instead of silent or accidental.

Acceptance criteria:

- unsupported remote include forms are surfaced as capability errors
- local includes continue to work
- docs reflect the boundary

### P0-5: Surface capability warnings in `validate`

Show users actionable warnings before runtime.

Acceptance criteria:

- `bitrab validate` shows capability warnings distinctly
- the output explains what Bitrab will ignore or not enforce
- existing validation still works

### P0-6: Tighten supported/unsupported docs

Update user-facing docs so compatibility claims match runtime behavior.

Acceptance criteria:

- README/docs distinguish supported, limited, and unsupported features
- docs mention components and inputs are unsupported
- docs stop implying silent near-compatibility

## Phase 1: stabilize local execution semantics

Goal: make local behavior predictable and safer by default.

### P1-1: Make shared workspace + serial execution the default

Change default execution policy to the safest local mode.

Acceptance criteria:

- default run mode is shared workspace and serial execution
- existing CLI flags still allow parallel runs
- output clearly states the selected execution mode

### P1-2: Add explicit concurrency mode setting

Introduce a first-class concurrency mode instead of inferring behavior indirectly.

Acceptance criteria:

- serial and parallel modes are explicit
- mode is visible in user output
- invalid combinations fail clearly

### P1-3: Add explicit workspace mode setting

Make shared vs isolated workspaces a declared runtime policy.

Acceptance criteria:

- workspace mode can be chosen explicitly
- current behavior is mapped intentionally to one of the modes
- docs explain tradeoffs

### P1-4: Fix `CI_PROJECT_DIR` and related built-ins

Make built-in CI variables reflect the actual runtime context.

Acceptance criteria:

- `CI_PROJECT_DIR` matches the actual job workspace
- `CI_JOB_NAME` and `CI_JOB_STAGE` remain correct
- tests cover shared and isolated workspace expectations

### P1-5: Document and enforce env precedence

Turn current env behavior into a stable contract.

Acceptance criteria:

- env precedence is documented
- code follows the documented order
- tests cover precedence behavior

### P1-6: Add env inheritance modes

Support explicit host-env policies.

Acceptance criteria:

- support at least `inherit-all` and `inherit-none`
- chosen mode is visible in output/debug info
- behavior is tested

### P1-7: Add env allowlist/blocklist support

Add a practical middle ground between full inheritance and full isolation.

Acceptance criteria:

- users can include or exclude selected env vars
- results are visible in debug output
- tests cover allowlist/blocklist behavior

### P1-8: Add execution-context debug output

Make local behavior easier to inspect.

Acceptance criteria:

- a debug command or flag shows resolved cwd, workspace mode, env mode, and job script bundle
- sensitive values can be masked if needed
- docs describe how to use it

### P1-9: Add basic per-job timeout support

Prevent local runs from hanging forever.

Acceptance criteria:

- jobs can be given a timeout
- timed-out jobs fail clearly
- tests cover timeout behavior

## Phase 2: improve runtime structure

Goal: reduce hidden coupling and make future work easier.

### P2-1: Introduce `JobRuntimeContext`

Create a dedicated object for resolved per-job runtime state.

Acceptance criteria:

- runtime state is no longer spread implicitly across multiple helpers
- job execution consumes a context object
- tests still pass with the refactor

### P2-2: Move resolved env into runtime context

Stop recomputing env ad hoc inside multiple layers.

Acceptance criteria:

- resolved env is built once per job
- shell execution reads env from runtime context
- behavior remains unchanged except where intentionally improved

### P2-3: Move workspace resolution into runtime context

Unify cwd/workspace handling.

Acceptance criteria:

- job workspace resolution is centralized
- built-in CI vars use the same resolved workspace
- tests cover the resolved workspace contract

### P2-4: Make config normalization immutable

Reduce mutation-heavy behavior in loading and planning.

Acceptance criteria:

- config loading avoids unexpected mutation of source structures
- normalized config objects are clearer to reason about
- tests cover include processing behavior

### P2-5: Add richer normalized execution plan object

Create a stronger intermediate layer between YAML and execution.

Acceptance criteria:

- the planner produces an execution-plan-like structure
- capability diagnostics can attach to the plan
- execution consumes the plan instead of raw-ish config

### P2-6: Separate execution policy from multiprocessing details

Decouple concurrency decisions from process-pool implementation.

Acceptance criteria:

- execution policy can choose serial vs parallel independently of how workers are implemented
- orchestration code becomes easier to reason about
- tests cover serial and parallel policy paths

## Phase 3: fix output architecture

Goal: stop interleaving from dominating the UX.

### P3-1: Define structured execution events

Introduce a common event format for runtime activity.

Acceptance criteria:

- execution emits job/script/log lifecycle events
- event types are documented
- existing behavior can be rendered from events

### P3-2: Add log collector abstraction

Separate subprocess output capture from final presentation.

Acceptance criteria:

- output collection is independent from terminal printing
- stdout/stderr chunks can be grouped by job
- tests cover collection behavior

### P3-3: Buffer per-job logs for parallel CLI runs

Make plain terminal output readable by default.

Acceptance criteria:

- parallel CLI mode does not interleave live job logs
- each job's output is emitted as a coherent block
- users still get stage/job progress indicators

### P3-4: Add CI-safe completed-job emission mode

Optimize for single shared logs, such as GitHub Actions.

Acceptance criteria:

- in CI-style mode, each job's output is emitted only after completion
- jobs are printed in a stable order
- an end-of-run summary is always shown

### P3-5: Add end-of-run job summary

Help users relocate failures quickly.

Acceptance criteria:

- final output summarizes status, retries, and failures per job
- failed jobs are easy to identify
- summary appears in both local CLI and CI-safe modes

### P3-6: Persist job logs under `.bitrab\logs\`

Make it easy to revisit results without scrolling terminal history.

Acceptance criteria:

- per-job logs are saved to disk
- file naming is predictable
- docs explain where logs live

### P3-7: Improve failure-first output formatting

Make long logs easier to debug.

Acceptance criteria:

- failures surface the most important info first
- summaries point to log files or sections
- formatting remains readable without a TUI

## Phase 4: local-first UI

Goal: give parallel output a better home than a single terminal stream.

### P4-1: Build pipeline flow model for UI

Create the data model for a visual stage/job overview.

Acceptance criteria:

- execution plan can be rendered as stage/job flow data
- statuses can update over time
- the model is reusable by TUI and web UI

### P4-2: Create Textual app skeleton

Add the basic Textual entrypoint and screen layout.

Acceptance criteria:

- app launches from a CLI flag or command
- screen layout is stable
- non-TUI CLI continues to work

### P4-3: Add flow tab as the first tab

Implement the default tab showing pipeline shape and state.

Acceptance criteria:

- first tab shows stage/job flow
- running, passed, and failed states are visible
- the view works before detailed logs are added

### P4-4: Add one log tab per job

Give each job its own non-interleaved output surface.

Acceptance criteria:

- each job has a dedicated tab
- logs remain isolated per tab
- tab titles show status

### P4-5: Add visible retries and failure markers

Keep important state visible without scrolling raw output.

Acceptance criteria:

- retries are visible in UI
- failures are clearly marked
- finished jobs remain easy to inspect

### P4-6: Add focus and filter controls

Make the TUI useful for larger pipelines.

Acceptance criteria:

- users can jump to failed jobs
- users can filter by stage/status
- navigation is documented

### P4-7: Evaluate whether local web UI is still needed

Decide after the Textual model exists.

Acceptance criteria:

- document what Textual solves well
- document what still pushes toward a web UI
- decide whether a web UI remains a roadmap item

## Phase 5: planning depth and focused local workflows

Goal: make Bitrab better for everyday iterative development.

### P5-1: Finish `--jobs`

Support targeted execution for local debugging.

Acceptance criteria:

- users can run selected jobs only
- selection behavior is documented
- tests cover filtered execution

### P5-2: Add `--stage`

Allow targeted execution by stage.

Acceptance criteria:

- users can run a selected stage
- output explains what was skipped
- tests cover stage filtering

### P5-3: Add rerun-last-failed flow

Speed up local iteration after a broken run.

Acceptance criteria:

- users can rerun previously failed jobs
- behavior is well-defined if there is no previous failure set
- docs explain how the feature works

### P5-4: Add clearer skip/run summaries

Make targeted runs easy to understand.

Acceptance criteria:

- output explains what ran, what was skipped, and why
- summaries are consistent across run modes
- tests cover summary behavior

### P5-5: Add initial `needs` planning support

Prepare for smarter execution without overcommitting to full GitLab behavior.

Acceptance criteria:

- planner can parse and represent `needs`
- unsupported semantics are called out clearly
- this does not silently change execution without documentation

## Suggested implementation order

If you want the most leverage earliest, start here:

1. `P1-1` Make shared + serial the default.
2. `P0-1` Add capability validation layer.
3. `P0-2` Reject `include:component`.
4. `P0-3` Reject component `inputs`.
5. `P1-4` Fix `CI_PROJECT_DIR` and related built-ins.
6. `P1-5` Document and enforce env precedence.
7. `P5-1` Finish `--jobs`.
8. `P3-3` Buffer per-job logs for parallel CLI runs.
9. `P3-4` Add CI-safe completed-job emission mode.
10. `P2-1` Introduce `JobRuntimeContext`.
11. `P4-2` Create Textual app skeleton.
12. `P4-3` Add flow tab.
13. `P4-4` Add one log tab per job.

## Best first issues

If you want the easiest high-value issues first, I would pick:

- `P1-1` Make shared + serial the default
- `P0-2` Reject `include:component`
- `P0-3` Reject component `inputs`
- `P1-4` Fix `CI_PROJECT_DIR` and related built-ins
- `P3-5` Add end-of-run job summary
- `P5-1` Finish `--jobs`
- `P1-8` Add execution-context debug output

Those should improve user trust and local experience quickly without requiring the full architectural refactor first.
