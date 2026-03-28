# Build Improvements Plan

## Problem statement

The current build surface mixes mutating and non-mutating jobs, which makes safe parallel execution harder and forces developers to remember the "fix first, verify second" workflow themselves. Output is also optimized inconsistently across humans, CI, and LLM-driven tooling: some targets are verbose, some mutate state in contexts where they should not, and several bug-finding tools are either bundled loosely or missing from the main ergonomics story.

We want a build interface that treats `Makefile` and `Justfile` as first-class, feature-parity entrypoints while making the workflow:

- mutation-aware, so mutators run first and read-only checks can parallelize safely
- LLM-friendly, so output stays compact and token-efficient by default when requested
- CI-friendly, so execution is deterministic, non-mutating, machine-readable, and colorless
- human-friendly, so local developer commands have strong summaries, sensible sequencing, and high-signal output
- bug-hunting focused, so static analysis, security checks, runtime verification, and workflow methodology get expanded rather than treated as an afterthought

## Proposed approach

Split the build surface into explicit phases and profiles rather than one flat pile of targets.

### 1. Define execution classes

Create a shared conceptual model for every build target:

- `mutating`: formatters, autofixers, code generators, schema updates, lock refreshes, docs sync
- `read_only`: lint, type-check, tests, security scans, smoke checks, validation, benchmarks
- `networked`: tasks that fetch remote content or depend on external services
- `artifact_generating`: tasks that write reports/logs but do not mutate source inputs

This classification becomes the backbone for both `Makefile` and `Justfile`.

### 2. Introduce workflow profiles

Define a small set of user-facing orchestration targets with identical meaning in both files:

- `fix`: run all safe mutators in the right order
- `verify`: run all non-mutating checks only
- `check`: run `fix` first, then `verify`
- `check-ci`: non-mutating, no color, no network mutation, machine-readable artifacts
- `check-llm`: compact, failure-oriented, token-minimal output
- `check-human`: rich status output, grouped summaries, readable stage boundaries
- `bugs`: bug-finding suite focused on correctness and security
- `fast-verify`: parallel read-only subset for quick iteration
- `full-verify`: broader, slower suite for pre-merge confidence

### 3. Make mutators explicit and ordered

Mutating targets should not be hidden inside "lint" or "pylint" style verification jobs. Instead:

- move `ruff --fix`, `isort`, `black`, metadata sync, and `git2md` under explicit mutator targets
- enforce "mutators before read-only checks" in human-oriented umbrella commands
- keep CI and read-only modes mutation-free by design
- optionally add a guard target that fails if source changed during a supposedly read-only run

### 4. Parallelize only where safe

Once mutators are isolated, non-mutating tasks can run in parallel more aggressively:

- mypy
- pylint or ruff-readonly
- bandit
- pytest
- smoke/basic CLI checks
- docs or spelling verification if they are non-mutating

Parallel orchestration should preserve:

- per-tool log isolation
- ordered summaries at the end
- a clear aggregate failure code
- optional "fail fast" for humans and "collect all failures" for CI/LLM modes

### 5. Unify output contracts by audience

Each profile should intentionally shape output:

#### Human mode

- rich headings and phase banners
- clear "mutating now" vs "verifying now" messaging
- actionable summaries
- keep successful output readable, not silent

#### CI mode

- no color
- no mutation
- deterministic ordering
- junit/coverage/xml/json artifacts where supported
- concise logs with explicit exit reasons

#### LLM mode

- suppress noisy success chatter
- show only phase summaries plus failing excerpts
- cap log tails per tool
- disable color and spinners
- prefer one-line status plus short failure blocks

### 6. Expand the bug-finding toolbox

Treat bug detection as its own first-class suite rather than just "linting":

#### Static correctness and typing

- preserve `mypy`
- add read-only `ruff check` without `--fix`
- review whether additional Ruff rule families should be enabled for bug detection, especially correctness and simplification rules
- consider a stricter mypy profile for targeted modules if full strict mode is too noisy

#### Security

- preserve `bandit`
- add dependency or supply-chain scanning if an existing ecosystem tool is already available in the workflow
- separate "local quick security" from "deep security sweep"

#### Test methodologies

- keep the main test suite
- add dedicated smoke, focused regression, and slow/full targets
- consider mutation-testing or property-driven test workflows only if they fit the existing ecosystem and cost profile
- document when to run serial tests versus parallel tests to catch order-dependent bugs

#### Workflow methodologies

- add a "changed-files verify" path for fast feedback
- add a "full repository verify" path for release confidence
- add a "repro mode" target that disables parallelism to simplify debugging
- add a "triage mode" target that collects all failures without mutating anything

## Planned workstreams

### Workstream A: inventory and classify existing targets

- catalog every current `Makefile` and `Justfile` target
- classify each as mutating, read-only, networked, or artifact-generating
- identify mismatches between `Makefile` and `Justfile`
- identify current hidden mutation inside verification targets

### Workstream B: define the shared target taxonomy

- establish canonical target names and semantics shared by both files
- decide which targets are local-only, CI-only, or multi-audience
- document the output contract for each profile

### Workstream C: refactor mutating targets

- extract all autofix and generation behavior into explicit mutator targets
- ensure ordering is deterministic
- remove mutation from verification targets and CI-oriented paths

### Workstream D: build parallel read-only orchestration

- create a parallel verification phase for safe jobs
- isolate logs per tool
- aggregate results cleanly
- preserve non-zero exit behavior when any constituent fails

### Workstream E: improve audience-specific output

- human-mode summaries and banners
- CI-mode no-color, no-mutation, machine-readable outputs
- LLM-mode compact summaries and tailed failure snippets

### Workstream F: strengthen bug-finding coverage

- review existing tools and enable stronger read-only correctness checks
- add dedicated bug-oriented umbrella targets
- document recommended usage patterns for quick local feedback versus deep investigation

### Workstream G: parity and documentation

- keep `Makefile` and `Justfile` behavior aligned
- document the target matrix in repo docs
- add tests or smoke checks for critical orchestration behavior where practical

## Candidate target matrix

| Target | Mutates source | Parallel-safe | Audience | Purpose |
| --- | --- | --- | --- | --- |
| `fix` | Yes | No | Human | Run all source mutators in canonical order |
| `fix-ci` | No | Yes | CI | Verify mutators would make no changes |
| `verify` | No | Yes | Human/CI/LLM | Run read-only checks |
| `fast-verify` | No | Yes | Human | Quick feedback loop |
| `full-verify` | No | Yes | Human/CI | Wider confidence suite |
| `check` | Yes | Mixed | Human | Run `fix` then `verify` |
| `check-ci` | No | Yes | CI | Deterministic non-mutating gate |
| `check-llm` | No | Yes | LLM | Compact token-efficient gate |
| `check-human` | Yes | Mixed | Human | Friendly local workflow with summaries |
| `bugs` | No | Yes | Human/CI | Concentrated bug-finding suite |
| `repro` | No | No | Human | Debug-friendly serial verification |
| `triage` | No | Yes | Human/CI/LLM | Collect all failures with grouped logs |

## Notes and considerations

- The current `Makefile` already hides mutation inside `pylint` via `ruff --fix`, which is exactly the kind of coupling this plan should remove.
- `update-schema` is networked and mutating; it should never run implicitly inside CI verification.
- `Justfile` already experiments with LLM-friendly and parallel flows; the plan should harvest that good structure and formalize it rather than treating it as a side path.
- "No color" should be enforced through environment or tool flags consistently, not left to individual command defaults.
- Parallel execution should optimize for readability as well as speed: per-tool logs plus a stable final summary beat interleaved console spam.
- If a tool does not support compact output directly, wrap it with filtering only when that filtering remains trustworthy and does not hide important failures.
- Any target named like a verifier should be read-only by contract.

## Initial todo list

1. Inventory and classify every existing build target and subcommand in `Makefile` and `Justfile`.
2. Define the canonical shared target taxonomy and audience profiles.
3. Refactor mutators out of read-only targets.
4. Create parallel read-only orchestration with log aggregation.
5. Design human, CI, and LLM output contracts and wire them into both files.
6. Expand the bug-finding suite and methodology targets.
7. Document the new build matrix and expected workflows.
