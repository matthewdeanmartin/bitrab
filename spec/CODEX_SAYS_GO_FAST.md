# CODEX Says: Go Fast

## What changed in this pass

- `tox` now uses `tox-uv`, which should cut most of the repeated virtualenv and dependency installation cost from the matrix.
- Native speedups are now optional:
  - `bitrab[fast]` installs `orjson` and `rtoml`
  - `bitrab[all]` currently includes the same fast-path set
- Runtime code now falls back cleanly when those extras are not installed.
- Benchmark runs now autosave results and compare against the latest saved baseline.
- Benchmark regressions now fail the run when mean runtime gets worse by more than `15%`.

## Why the old benchmark flow stayed quiet

`pytest-benchmark` does not complain about regressions unless you give it a saved baseline to compare against. The previous setup timed the benchmarks, but it never ran with:

- `--benchmark-autosave`
- `--benchmark-compare=...`
- `--benchmark-compare-fail=...`

That means "perf got worse" was never part of the contract.

## Obvious speedups already present or now enabled

- `orjson` is a good fit for:
  - schema cache read/write
  - validation result output
  - persisted run-log JSON and JSONL
- `rtoml` is a good fit for:
  - Python 3.9/3.10 TOML reads when stdlib `tomllib` is unavailable

## Additional perf opportunities worth doing next

### 1. Reduce `copy.deepcopy` in planning

`bitrab/plan.py` deep-copies the whole config before processing, and `bitrab/config/loader.py` deep-copies again while resolving includes. That is safe, but it is expensive on large CI files.

Best next move:

- move toward targeted copy-on-write for only the parts being rewritten
- avoid copying the full tree when no `extends:` or `include:` rewrite is needed

Expected impact:

- better config-processing time
- lower memory churn on large pipelines

### 2. Reuse compiled schema validator objects

`bitrab/config/validate_pipeline.py` caches the schema document, but still builds a fresh `jsonschema.Draft7Validator` per validation call.

Best next move:

- cache the `Draft7Validator` per validator instance or per schema identity

Expected impact:

- faster repeated `validate` operations
- faster bulk validation in `run_validate_all`

### 3. Make TOML parsing cheaper on every run

`bitrab/mutation.py` reads `pyproject.toml` for mutation and parallel settings during pipeline startup.

Best next move:

- cache parsed `pyproject.toml` by path + mtime
- share one parsed result between `load_mutation_config()` and `load_parallel_config()`

Expected impact:

- small but free startup savings
- less repeated filesystem work

### 4. Make dry-run stay on the lightest execution path

The dry-run benchmark is useful because it mostly measures planning/orchestration overhead. That also means any extra file scans, mutation setup, or log shaping done during dry-run shows up immediately.

Best next move:

- audit dry-run for avoidable work
- skip setup that cannot affect dry-run output

Expected impact:

- faster local feedback loop
- clearer benchmark signal for planner-only changes

### 5. Revisit the default parallel backend by workload

The default backend is still `process`, which is safer for CPU-bound work but carries startup and serialization overhead. For orchestration-heavy or dry-run workloads, threads may be cheaper.

Best next move:

- benchmark `thread` vs `process` for dry-run and small pipelines
- consider adaptive defaults or a documented "fast local" preset

Expected impact:

- lower overhead for lightweight jobs
- better UX on Windows, where process startup is especially expensive

### 6. Avoid full-tree mutation snapshots when warnings are disabled

Mutation detection already has a config gate, but this should stay aggressively cheap whenever disabled.

Best next move:

- confirm snapshotting never happens on disabled paths
- keep new code from accidentally touching the filesystem when mutation warnings are off

Expected impact:

- prevents stealth regressions in normal runs

### 7. Expand benchmarks to cover realistic hot paths

Current benchmarks are useful, but narrow.

Best next move:

- add benchmarks for:
  - include resolution
  - `extends:` resolution
  - schema validation with a warm cache
  - pipeline graph construction on larger job counts
  - watch-mode config reload

Expected impact:

- catches regressions in the parts users actually feel
- makes future optimization work measurable

## Suggested next order

1. Cache parsed `pyproject.toml` and validator instances.
2. Benchmark `thread` vs `process` for dry-run and small pipelines.
3. Reduce full-config deep copies.
4. Add broader benchmark coverage for include/extends/validation hot paths.
