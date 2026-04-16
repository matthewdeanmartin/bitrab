# CODEX Says: Code Review and Recommended Roadmap

## Scope

This review is based on reading the current README, key docs, and the main implementation paths in:

- `README.md`
- `docs/cli.md`
- `docs/concepts.md`
- `docs/differences.md`
- `bitrab/cli.py`
- `bitrab/plan.py`
- `bitrab/config/*.py`
- `bitrab/execution/*.py`
- `bitrab/folder.py`
- `bitrab/graph.py`
- `bitrab/watch.py`

## High-level take

Bitrab has a strong core shape. The project is readable, the execution flow is traceable, and the docs do a better-than-average job of explaining that this is a practical subset of GitLab CI rather than a full runner replacement.

The main weaknesses are not "the code is messy". They are mostly contract gaps:

- some commands behave inconsistently with each other
- some unsupported features are only diagnosed in `validate`, not enforced in `run`
- a few user-facing promises are undermined by edge-case behavior
- the ergonomics are improving, but there are still several surprise points around defaults and capability boundaries

## What is working well

- The codebase is still small enough to reason about end-to-end.
- The CLI, planner, loader, and executor are separated well enough to evolve independently.
- The docs are unusually honest about the local-vs-GitLab gap.
- DAG, artifacts, retries, graph output, watch mode, and log persistence make this feel like a real tool rather than a toy parser.
- The project has already started moving toward clearer architecture with capability diagnostics and shared stage-running logic.

## Findings


### 6. Shared-workspace parallelism is still a major foot-gun, and the product currently advertises it as a strength (NOT FIXED — product decision deferred)

*(Partially agree — the tension between speed and safety is real.  However, changing the default from CPU-count to 1 is a breaking behaviour change that needs its own milestone.  The docs already document the trade-off honestly.  Not fixing in this pass; flagged for Phase 3 of the roadmap.)*

- The README highlights parallel execution savings in `README.md:14-15` and `:32`.
- The docs clearly admit shared-tree race risk in `docs/differences.md:80-84`.
- The implementation still defaults `--parallel` to CPU count when not specified in `bitrab/execution/stage_runner.py:244-247` and `:541-544`.

Why this matters:

- The docs already explain the danger.
- The default behavior still optimizes for speed rather than predictable local semantics.

Recommendation:

- Reconsider the default toward safer serial execution, at least in shared-workspace mode.
- If parallel remains the default, print a stronger runtime warning when jobs share one checkout.


## Missing features worth prioritizing

- `cache:` support
- `rules: changes`
- explicit manual-job execution flow
- consistent run-time capability enforcement
- safer workspace/concurrency modes
- better machine-readable CLI output
- richer debug output for resolved execution context
- rerun-last-failed / focused re-execution ergonomics

## Areas of confusion or surprise

- `validate` and `run` do not currently provide the same truth model.
- `.bitrab-ci.yml` is special in some commands but not others.
- `--dry-run` is not equally trustworthy across execution modes.
- The docs are more honest than the runtime in a few edge cases.
- The project strongly communicates "practical subset", but parts of the loader still behave like unsupported syntax should just vanish quietly.

## Areas for improvement

- Make capability boundaries executable, not just documented.
- Reduce command-to-command inconsistency.
- Tighten the semantics of preview/dry-run.
- Make the default local execution mode safer.
- Improve automation ergonomics for JSON output and debug surfaces.
- Keep product metadata aligned with the current vision.

## Recommended roadmap

### Phase 1: Trust and Contract

- `HARD` Enforce `CapabilityDiagnostic.ERROR` in both `validate` and `run`.
- `MEDIUM` Refactor unsupported include handling so the loader does not silently skip hard-failure constructs.
- `MEDIUM` Centralize config-file resolution so every command uses the same `.bitrab-ci.yml` / `.gitlab-ci.yml` logic.
- `EASY` Update package metadata and CLI/help text to match the current product description.

### Phase 2: Correctness and Safety

- `HARD` Fix DAG-mode dry-run so it never creates job dirs, snapshots mutations, injects dependencies, or collects artifacts.
- `MEDIUM` Add regression tests for dry-run across both stage and DAG paths.
- `MEDIUM` Make execution mode explicit in startup output: config path, workspace mode, concurrency mode, and dry-run status.
- `EASY` Fix human-readable size formatting.

### Phase 3: Local-first Ergonomics

- `HARD` Revisit default concurrency policy for shared workspaces, with a bias toward safer serial behavior.
- `MEDIUM` Add a richer `debug` surface that shows resolved env, cwd, built-in CI vars, and why jobs were skipped.
- `MEDIUM` Make `validate --json` emit pure JSON.
- `MEDIUM` Add clearer "what will be ignored locally" output before a run starts.
- `EASY` Print the resolved config file in all commands.

### Phase 4: High-value GitLab Subset Expansion

- `HARD` Implement `rules: changes` using git-aware diff evaluation.
- `MEDIUM` Implement practical `cache:` support with a clearly local model.
- `MEDIUM` Add a manual-job execution path so `when: manual` is not only skipped.
- `MEDIUM` Add rerun-last-failed and single-job rerun workflows.

### Phase 5: Workspace and Execution Model Maturity

- `HARD` Introduce explicit workspace modes such as `shared` and `isolated`.
- `HARD` Introduce explicit env inheritance modes such as `inherit-all` and `inherit-none`.
- `MEDIUM` Preserve failed isolated workspaces for debugging.
- `MEDIUM` Improve parallel-run UX so users know when they are taking on shared-tree risk.

## Suggested order inside the roadmap

If the goal is maximum trust gain per unit of effort, I would do the work in this order:

1. Fix capability enforcement.
2. Fix DAG dry-run side effects.
3. Unify config resolution across commands.
4. Clean up `validate --json`.
5. Revisit the default parallel/shared-workspace policy.
6. Add `cache:` and `rules: changes`.
7. Build richer workspace/env modes after the contract is stable.

## Bottom line

Bitrab is already useful and much closer to "serious local CI tool" than many projects at this stage. The next big step is not adding a huge feature wave. It is making the contract completely trustworthy:

- what Bitrab supports
- what it refuses
- what it ignores
- what it may mutate
- and which file it is actually running

Once those edges are made consistent, the rest of the roadmap becomes much easier to execute with confidence.
