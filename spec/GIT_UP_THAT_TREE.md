# Git Worktree Support for Parallel Jobs

## Problem

When jobs run in parallel (via `ProcessPoolExecutor` or `ThreadPoolExecutor`), they all share the same
working directory — the project root. Jobs that mutate the filesystem (write files, install packages,
build outputs, etc.) stomp on each other. Conflict city.

In single-threaded mode (`maximum_degree_of_parallelism == 1`, or only one job in a stage) there is no
conflict — only one job touches the repo at a time — so worktrees are unnecessary overhead.

---

## Execution Modes & Worktree Applicability

| Mode                                   | When                                                  | Worktree needed?         |
|----------------------------------------|-------------------------------------------------------|--------------------------|
| Serial (`_run_stage_serial`)           | `max_parallelism == 1` **or** stage has exactly 1 job | **No** — mutate in place |
| Parallel stage (`_run_stage_parallel`) | Multiple jobs in stage, `max_parallelism > 1`         | **Yes**                  |
| DAG parallel (`DagPipelineRunner`)     | Any job declares `needs:`, ready set > 1              | **Yes**                  |

Single-threaded mode already exists: `stage_runner.py:338` short-circuits to `_run_stage_serial` when
`self.maximum_degree_of_parallelism == 1`. No new flag needed.

---

## Proposed Design

### 1. Worktree Lifecycle Helper — `bitrab/git_worktree.py`

New module, keeps all `git worktree` logic in one place.

```python
# bitrab/git_worktree.py

@dataclass
class WorktreeContext:
    worktree_path: Path  # absolute path of the worktree
    project_dir: Path  # original project root (bare repo location)


def is_git_repo(project_dir: Path) -> bool:
    """Return True if project_dir is inside a git repository."""
    ...


def create_worktree(project_dir: Path, name: str) -> WorktreeContext:
    """
    Run `git worktree add --detach <path>` and return context.
    name is a sanitized job name used as the directory suffix.
    Worktree is created at: project_dir / ".bitrab" / "worktrees" / name
    Uses --detach so we don't create a new branch.
    """
    ...


def remove_worktree(ctx: WorktreeContext) -> None:
    """
    Run `git worktree remove --force <path>` and delete the directory.
    Called in a finally block so cleanup happens even on failure.
    """
    ...


@contextmanager
def job_worktree(project_dir: Path, name: str) -> Iterator[Path]:
    """
    Context manager: creates worktree, yields its path, always removes it.
    Use this from worker functions so cleanup is guaranteed.
    """
    ctx = create_worktree(project_dir, name)
    try:
        yield ctx.worktree_path
    finally:
        remove_worktree(ctx)
```

Worktrees land at `.bitrab/worktrees/<sanitized_job_name>/` — already inside the `.gitignore`-able
`.bitrab/` directory, consistent with where job dirs and artifacts live.

### 2. Configuration — `pyproject.toml`

Add opt-in flag to `[tool.bitrab]`:

```toml
[tool.bitrab]
use_git_worktrees = true   # default: false until we're confident
```

Wire into `ParallelBackendConfig` (already in `mutation.py`) or create a new `WorktreeConfig` dataclass:

```python
@dataclass
class WorktreeConfig:
    enabled: bool = False  # must opt in
```

Read from `pyproject.toml` alongside existing `MutationConfig` / `ParallelBackendConfig`.

**Why opt-in?** Worktrees require git, require a clean-enough HEAD to check out, and add non-trivial
overhead (filesystem copy). Projects that don't need isolation shouldn't pay the cost.

### 3. Worker Function Integration — `stage_runner.py`

The key integration point is the worker functions submitted to the pool. Currently:

```python
# stage_runner.py:196 — _default_worker
def _default_worker(job, executor, job_dir, **extra) -> list[RunResult]:
    return executor.execute_job(job, job_dir)
```

`executor.execute_job` runs scripts with `project_dir` as the working directory
(`job.py:JobRuntimeContext.project_dir`). That's what needs to change per-job.

**Option A — wrap the worker function (preferred)**

In `_run_stage_parallel` (and the DAG equivalent), before submitting futures, check if worktrees are
enabled and we're in a git repo. If so, wrap `worker_func` with a worktree-aware shim:

```python
# In StagePipelineRunner._run_stage_parallel:
if self._worktree_config.enabled and is_git_repo(self.job_executor.project_dir):
    worker_func = _worktree_worker  # module-level, picklable


# Module-level worker (picklable for ProcessPoolExecutor):
def _worktree_worker(job, executor, job_dir, **extra) -> list[RunResult]:
    from bitrab.git_worktree import job_worktree
    with job_worktree(executor.project_dir, sanitize_job_name(job.name)) as wt_path:
        # Replace project_dir on executor with the worktree path for this job
        import dataclasses
        scoped_executor = dataclasses.replace(executor, project_dir=wt_path)
        return scoped_executor.execute_job(job, job_dir)
```

`JobExecutor` needs to be a dataclass (or have `__replace__` support) for this to work. Check current
structure in `job.py` and add `@dataclass` / `frozen=False` if not already there.

**Why Option A and not modifying `execute_job`?** The worker shim pattern is already established
in the codebase — the TUI uses `make_worker_args` / `get_worker_func` to swap in a different worker.
This slot is the designed extension point. We fit in cleanly.

### 4. Serial Mode — No Change Needed

`_run_stage_serial` calls `executor.execute_job` directly in the calling process, one job at a time.
No parallelism, no conflicts. Leave it alone.

### 5. DAG Runner

`DagPipelineRunner` (line 541–803) uses the same `_make_pool` / `_run_stage_parallel` machinery via a
shared helper. The worktree check in `_run_stage_parallel` covers both stage-based and DAG execution
automatically if `StagePipelineRunner` is the shared base. If `DagPipelineRunner` duplicates the parallel
submission loop, apply the same wrapping there.

---

## Artifact Handling in Worktrees

Artifacts are collected after job completion by `collect_artifacts` in `artifacts.py:464`. Currently it
copies from `project_dir`. When worktrees are enabled, artifact collection must copy from the **worktree
path**, not the original `project_dir`.

**Change needed in `_run_stage_parallel`:**

```python
# After fut.result(), know the worktree_path used by that job
collect_artifacts(job, worktree_path_for_job, succeeded)
collect_dotenv_report(job, worktree_path_for_job, succeeded)
```

The future result could return the worktree path alongside `list[RunResult]`, or we pass it back via a
wrapper result type. A simple `WorkerResult = namedtuple("WorkerResult", ["history", "effective_dir"])`
would work without breaking existing pickling.

Alternatively, artifact collection can stay `project_dir`-relative if jobs write artifacts to paths that
exist in both the worktree and the repo root. But that defeats isolation. Collect from worktree.

---

## Dotenv Reports & Dependency Injection

`inject_dependencies` copies artifacts **into** the job's working directory before the job runs
(`stage_runner.py:422`). When using worktrees, inject into the **worktree** directory, not the project
root. This means `inject_dependencies` needs the effective project dir, which is the worktree path.

Same solution: pass worktree path to `inject_dependencies` (already takes `project_dir` as an arg, just
pass the worktree path instead).

---

## Mutation Detection

`MutationSnapshot` takes a snapshot of `project_dir` before the job and diffs after. With worktrees, pass
the worktree path as `project_dir` when constructing the snapshot inside the worker. Mutation detection
then correctly tracks per-job changes in isolation.

---

## Error Handling & Cleanup

Worktree creation can fail if:

- Not a git repo (check upfront with `is_git_repo`)
- `git worktree add` fails (detached HEAD conflicts, locked worktrees, disk space)
- The `.bitrab/worktrees/` directory doesn't exist yet

The `job_worktree` context manager must:

1. Create `.bitrab/worktrees/` if it doesn't exist
2. Run `git worktree add`; on failure, raise immediately (job fails, not silent)
3. In `finally`: run `git worktree remove --force` + `shutil.rmtree` as belt-and-suspenders

If cleanup fails (e.g., process killed mid-job), the dangling worktree is left under `.bitrab/worktrees/`.
The existing `bitrab folder clean` command should also run `git worktree prune` when it detects this
directory exists.

---

## Files to Create / Modify

| File                               | Change                                                                                                                                        |
|------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------|
| `bitrab/git_worktree.py`           | **New** — `is_git_repo`, `create_worktree`, `remove_worktree`, `job_worktree`                                                                 |
| `bitrab/mutation.py`               | Add `WorktreeConfig` dataclass, read `use_git_worktrees` from `[tool.bitrab]`                                                                 |
| `bitrab/execution/stage_runner.py` | Accept `WorktreeConfig`; wrap worker with `_worktree_worker` when enabled; pass worktree path to artifact/dotenv helpers                      |
| `bitrab/execution/artifacts.py`    | Accept `effective_dir: Path` arg on `collect_artifacts`, `collect_dotenv_report`, `inject_dependencies` instead of always using `project_dir` |
| `bitrab/execution/job.py`          | Verify `JobExecutor` supports `dataclasses.replace` (or add it); ensure `project_dir` field is replaceable                                    |
| `bitrab/folder.py`                 | Add `clean_worktrees(project_dir)` that runs `git worktree prune` + `shutil.rmtree(".bitrab/worktrees/")`                                     |
| `tests/test_git_worktree.py`       | **New** — unit tests for `is_git_repo`, create/remove lifecycle, `job_worktree` context manager                                               |

---

## Implementation Order

1. `bitrab/git_worktree.py` — implement and test in isolation (no pipeline changes yet)
2. `WorktreeConfig` in `mutation.py` + pyproject.toml wiring
3. `artifacts.py` — thread effective_dir through the three functions (pure refactor, no behaviour change
   when `effective_dir == project_dir`)
4. `job.py` — confirm `JobExecutor` is replaceable
5. `stage_runner.py` — wire in `_worktree_worker`, pass `WorktreeConfig`
6. `folder.py` — add `clean_worktrees`
7. Integration test: pipeline with 2+ parallel jobs that both write to the same filename — verify no
   conflict and artifacts collected correctly

---

## What We Are NOT Doing

- We are not creating worktrees for serial jobs. One job at a time = no conflict.
- We are not inventing a new concurrency model. ProcessPoolExecutor stays.
- We are not supporting worktrees when the project is not a git repo (falls back to current behaviour).
- We are not enabling worktrees by default. `use_git_worktrees = false` until the feature is proven.
- We are not copying the entire repo into the worktree — `git worktree add` shares the object store,
  so it's fast (seconds, not minutes, for large repos).
