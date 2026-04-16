# Sonnet Says: Go Fast

## Implemented

Items 1–5 (deepcopy, regex cache, mutation snapshot, `_toml.py` import chain,
`rglob` → `os.walk`) and the weak-ref cache fix were implemented and all 649
tests pass.  The remaining items (6–10) are documented below for future work.

---

## Scope

Performance audit of the current codebase, focusing on changes that would
actually move the needle in real workloads. Each item is tagged:

- `EASY` — small, contained change, low risk
- `HARD` — works across multiple files or requires design changes
- `HUGE` — valid idea but the refactoring surface is large enough to warrant its own milestone

Already-fixed items from earlier passes (CODEX_SAYS_GO_FAST.md) are not
repeated here. This doc covers only what remains.

---

## 1. `copy.deepcopy` in parallel job expansion — **EASY**

**File:** `bitrab/plan.py:458, 496`

`_expand_parallel_jobs()` calls `copy.deepcopy(job)` for every parallel
instance. For `parallel: N` that is N copies; for `parallel: matrix` it is
the full Cartesian product. The deep copy walks every nested object
recursively even though only a handful of fields are actually modified after
the copy (`name`, `parallel_index`, `parallel_total`, `variables`).

`JobConfig` is a dataclass. `dataclasses.replace()` gives a shallow copy with
explicit field overrides. The only field that needs independent isolation is
`variables` (a plain `dict`) — everything else is either overwritten
immediately or safely shared between copies (immutable strings, lists that are
never mutated after planning).

Replacement pattern:

```python
# instead of:
clone = copy.deepcopy(job)
clone.name = f"{job.name} {idx}/{n}"
clone.variables["CI_NODE_INDEX"] = str(idx)

# use:
clone = dataclasses.replace(
    job,
    name=f"{job.name} {idx}/{n}",
    parallel_total=n,
    parallel_index=idx,
    variables={**job.variables, "CI_NODE_INDEX": str(idx), "CI_NODE_TOTAL": str(n)},
)
```

Expected impact: 5–10× faster parallel expansion for matrix pipelines; also
lower GC pressure because no temporary deep-object graph is created.

---

## 2. `copy.deepcopy` of entire config in include processing — **HARD**

**File:** `bitrab/config/loader.py:138`

`_process_includes()` deep-copies the entire config dict at the top of every
recursive call, including for the common case where no rewrite is needed. On a
pipeline with several `include:` files this means the full merged YAML tree is
copied multiple times before `_process_job()` ever sees it.

The right fix is copy-on-write: only copy the sub-tree being mutated (the job
or anchor being extended/overridden) instead of the root dict. This touches
the include-resolution and `extends:` merge logic, which is why it is `HARD`,
but the payoff for large configs with many includes is real.

---

## 3. `_toml.py` re-runs the import chain on every call — **EASY**

**File:** `bitrab/_toml.py:9–40`

`load_file()` tries `import rtoml`, then `import tomllib`, then `import tomli`,
then `import toml` — inside a nested try/except — on **every invocation**.
Python caches successful imports in `sys.modules`, so repeated successful
imports are cheap, but repeated `ImportError` walks are not free, and the
structure makes the selection invisible at a glance.

Fix: resolve the best available backend once at module load time (exactly the
same pattern used in `_json.py`), then call a stable function reference:

```python
# resolved once at import time
try:
    import rtoml as _toml_backend
    def load_file(path): return _toml_backend.load(path)
except ImportError:
    ...  # fallback chain, same idea
```

This also makes `load_file` a straight call with no branching hot path.

---

## 4. Uncompiled regex in `rules.py` for `=~` / `!~` — **EASY**

**File:** `bitrab/config/rules.py:136, 145`

The regex pattern extracted from a rule expression (`$VAR =~ /pattern/`) is
passed directly to `re.search()` each time the rule is evaluated. Python's
`re` module caches a small number of recently-compiled patterns, but this
cache is bounded (~512 entries) and shared across all threads. In a pipeline
with many rules re-evaluated across parallel jobs, cache eviction is likely.

Fix: compile the pattern once when the `RuleConfig` is constructed and store
the compiled object. At evaluation time, call `.search()` on the pre-compiled
object. Risk is low because the pattern string comes from the CI YAML and is
fixed at parse time.

---

## 5. `_snapshot` in `mutation.py` creates one `Path` object per file — **EASY**

**File:** `bitrab/mutation.py:142–148`

```python
for dirpath, _dirs, files in os.walk(project_dir):
    for fname in files:
        full = Path(dirpath) / fname          # Path allocation
        snapshot[str(full.relative_to(project_dir))] = full.stat().st_mtime
```

Two allocations per file: the `Path` for `full`, and an implicit one inside
`relative_to`. For large trees (thousands of files) this is measurable.

Using `os.path` string operations avoids all intermediate `Path` objects:

```python
root = str(project_dir)
for dirpath, _dirs, files in os.walk(project_dir):
    for fname in files:
        full_str = os.path.join(dirpath, fname)
        rel = os.path.relpath(full_str, root)
        try:
            snapshot[rel] = os.stat(full_str).st_mtime
        except OSError:
            pass
```

This is called twice per job execution (before + after), so the saving
compounds with parallel job counts.

---

## 6. `validate_pipeline.py` — `WeakKeyDictionary` keyed on `self` was immediately GC'd — **EASY** ✓ DONE

**File:** `bitrab/config/validate_pipeline.py:21–22`

Both `_SCHEMA_CACHE` and `_VALIDATOR_CACHE` were `WeakKeyDictionary` instances
keyed on the `GitLabCIValidator` instance (`self`). A weak-key dictionary
drops an entry as soon as nothing else holds a strong reference to the key.

In practice, `GitLabCIValidator()` is constructed, used (e.g. in
`schema.py:158`), and then goes out of scope immediately. The cache entry is
evicted before the next call, making the cache entirely ineffective — every
`validate_ci_config()` call rebuilt the `Draft7Validator` from scratch, which
resolves all `$ref` chains in the 10 000-line GitLab schema.

Fix: replace both `WeakKeyDictionary`s with plain `dict`s keyed on
`self.cache_file` (a `Path`, stable and hashable across all instances that
point at the same schema location). The schema and validator now survive for
the process lifetime, as intended.

---

## 7. `rglob("*")` in artifact injection — **EASY**

**File:** `bitrab/execution/artifacts.py:113`

`artifact_src.rglob("*")` traverses the full tree and returns every entry
(files *and* directories) as `Path` objects. The loop then separately
`item.is_dir()` checks each one. `os.walk()` gives the same traversal but
pre-separates files and directories, eliminating the per-entry `is_dir()`
syscall and the `Path` allocation per item:

```python
for dirpath, dirnames, filenames in os.walk(artifact_src):
    for fname in filenames:
        src = Path(dirpath) / fname
        rel = src.relative_to(artifact_src)
        dest = project_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
```

Low risk, small but real gain for artifact-heavy jobs.

---

## 8. `_is_whitelisted` rescans patterns for every file — **EASY**

**File:** `bitrab/mutation.py:152–170`

`_is_whitelisted(rel_path, patterns)` is called once per changed file, and
for each file it iterates the full `patterns` list (which includes the builtin
list of ~15 entries plus any user list). For the `"**"` case it also splits
the pattern string inside the loop.

The pattern list is fixed for the lifetime of a job execution. Pre-processing
it into a compiled form once (split prefixes, compile `fnmatch` patterns to
regexes via `re.compile(fnmatch.translate(...))`) would make the per-file test
much faster. This matters most when mutation detection fires on a large tree.

---

## 9. `parallel: process` has per-job process spawn overhead — **HARD**

**File:** `bitrab/execution/stage_runner.py` (default backend)

On Windows especially, `multiprocessing` with `spawn` context incurs a
noticeable process-launch penalty for every job (importing Python, re-loading
modules, initializing the environment). For short-lived jobs (linting, fast
unit tests), the spawn overhead can dominate the actual job time.

The thread backend avoids this but is constrained by the GIL for CPU-bound
work. For I/O-bound jobs (shell script execution, most CI jobs) threads are
faster and the GIL is not a bottleneck.

Options:
- Default to `thread` for jobs whose script is shell-executed (they spend
  nearly all time in a subprocess, so GIL is irrelevant).
- Default to `process` only for `python:` executor or explicit CPU-bound work.
- Or: benchmark `thread` vs `process` for the standard dry-run fixture and let
  data drive the default.

This is `HARD` because changing the default has correctness implications
(shared memory between threads vs processes) and the "right" answer depends on
workload, not just theory.

---

## 10. `prepare_environment` copies `_shared_base_env` for every job — EASY (marginal)

**File:** `bitrab/execution/variables.py:234`

`_shared_base_env` is already pre-computed (good). `prepare_environment` then
calls `.copy()` and updates the result with job variables. The copy is
necessary for isolation — this is correct. The marginal optimization would be
to avoid the copy for jobs that define zero additional variables (copy-on-write
semantics using `types.MappingProxyType` + lazy materialization), but this is
likely only meaningful when `prepare_environment` is in a tight loop. Leave it
unless profiling shows it in the hot path.

---

## Summary

| # | Location | Difficulty | Impact | Status |
|---|----------|------------|--------|--------|
| 1 | `plan.py:458,496` — `deepcopy` in parallel expansion | EASY | High | **DONE** |
| 2 | `loader.py:138` — `deepcopy` in include processing | HARD | Medium–High | open |
| 3 | `_toml.py` — import-chain on every call | EASY | Low–Medium | **DONE** |
| 4 | `rules.py:136,145` — uncompiled regex in `=~`/`!~` | EASY | Medium | **DONE** |
| 5 | `mutation.py:142` — `Path` allocation per file in snapshot | EASY | Medium | **DONE** |
| 6 | `validate_pipeline.py` — WeakKeyDict keyed on `self` (GC'd immediately) | EASY | Medium | **DONE** |
| 7 | `artifacts.py:113` — `rglob("*")` vs `os.walk` | EASY | Low | **DONE** |
| 8 | `mutation.py:152` — repeated pattern scanning in whitelist | EASY | Low | open |
| 9 | `stage_runner.py` — process-spawn overhead on Windows | HARD | High (Windows) | open |
| 10 | `variables.py:234` — `prepare_environment` copy | EASY | Marginal | open |

Start with items 1, 4, and 5 — they are all `EASY`, independently testable,
and cover the three most exercised hot paths: planning, rule evaluation, and
mutation detection.
