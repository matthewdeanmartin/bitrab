# Pipeline execution model

This is the most important runtime flow in the project: how bitrab turns YAML into a sequence of local shell jobs.

## Phase 1: raw config loading

`ConfigurationLoader.load_config()` in `bitrab/config/loader.py`:

1. picks `.bitrab-ci.yml` over `.gitlab-ci.yml` when both exist
2. reads the root YAML file
3. recursively resolves `include:`
4. deep-merges included config into one raw dictionary

Important details:

- local includes are resolved relative to the current file
- remote `include: remote:` and `include: url:` are fetched with `urllib3`
- `include: component` hard-fails
- GitLab-managed include types like `template` and `project` are skipped, with diagnostics handled elsewhere

## Phase 2: normalization

`PipelineProcessor.process_config()` in `bitrab/plan.py` converts the raw dictionary into dataclasses.

Sub-steps:

1. deep-copy the raw config to avoid mutating the caller's dictionary
2. resolve `extends:` chains with `_resolve_extends()`
3. extract top-level `stages`, `variables`, and `default`
4. convert job mappings into `JobConfig` objects with `_process_job()`
5. expand `parallel: N` and `parallel: matrix:` with `_expand_parallel_jobs()`
6. rewrite `needs:` and `dependencies:` to point at expanded job names via `_resolve_expanded_needs()`

## Phase 3: filtering

`LocalGitLabRunner.run_pipeline()` optionally applies CLI filters:

- `--jobs`
- `--stage`

Filtering happens on the normalized `PipelineConfig`, not on raw YAML.

## Phase 4: rule evaluation

Before scheduling starts, `LocalGitLabRunner.run_pipeline()` creates a `VariableManager`, assembles a base environment, and calls:

```python
evaluate_rules(job, base_env, project_dir=self.base_path)
```

This means rule evaluation happens **after** normalization but **before** execution. Rule-side changes to `when`, `allow_failure`, `variables`, and `needs` affect the runtime model directly.

## Phase 5: scheduler selection

The execution path then forks:

```text
LocalGitLabRunner.run_pipeline()
  -> use_tui?            -> TUIOrchestrator.execute_pipeline_tui()
  -> ci_mode && !dry_run -> TUIOrchestrator.execute_pipeline_ci()
  -> otherwise           -> StageOrchestrator.execute_pipeline()
```

Inside `StagePipelineRunner.execute_pipeline()`:

- if any job has `needs:`, switch to `DagPipelineRunner`
- otherwise execute stage-by-stage

## Stage scheduling

`StagePipelineRunner`:

- groups jobs with `organize_jobs_by_stage()`
- filters jobs in each stage with `_filter_jobs_by_when()`
- runs a stage serially or in parallel depending on `maximum_degree_of_parallelism`
- stops stage-mode execution on the first hard failure

Manual jobs are skipped by normal scheduling and surfaced through `on_pipeline_awaiting_manual()`.

## DAG scheduling

`DagPipelineRunner` uses `graphlib.TopologicalSorter`.

Key behavior:

- jobs with explicit `needs:` depend only on those jobs
- jobs without `needs:` depend on all prior-stage jobs
- ready jobs are executed in batches
- `when:` is applied when jobs become ready
- failed dependencies prevent `on_success` jobs from running

Cycle errors from `TopologicalSorter` are allowed to propagate.

## Parallelism model

Parallel execution is local host parallelism, not isolated runner parallelism.

The pool backend is chosen in `StagePipelineRunner._make_pool()` and `DagPipelineRunner._make_pool()`:

- `ProcessPoolExecutor` by default
- `ThreadPoolExecutor` when configured with `parallel_backend = "thread"`

Config comes from `pyproject.toml` via `mutation.load_parallel_config()` and can be overridden with `--parallel-backend`.

## Failure handling

The core failure policy lives in the runners and `JobExecutor`:

- first hard failure stops stage-mode progression
- `allow_failure` converts some failures into warnings
- allowed failures still count as failures for `on_failure` logic
- retries are handled inside `JobExecutor`, not by the scheduler
- timeouts are enforced per job through `run_bash(..., timeout=...)`

This split is useful to remember: the scheduler decides **what to run next**, while `JobExecutor` decides **whether a single job attempt succeeded**.
