# Architecture

Bitrab is a local interpreter for a practical subset of GitLab CI. The core design is:

- **parse GitLab-style YAML**
- **normalize it into Python dataclasses**
- **run jobs as native shell processes on the host**
- **layer output, events, and persistence around the runtime**

The central modules are:

| Area | Main code |
| --- | --- |
| CLI entry point | `bitrab/cli.py` |
| Config loading and includes | `bitrab/config/loader.py` |
| Pipeline normalization | `bitrab/plan.py` (`PipelineProcessor`) |
| Pipeline dataclasses | `bitrab/models/pipeline.py` |
| Rule evaluation | `bitrab/config/rules.py` |
| Variable preparation | `bitrab/execution/variables.py` |
| Scheduling and orchestration | `bitrab/execution/stage_runner.py`, `bitrab/execution/scheduler.py`, `bitrab/tui/orchestrator.py` |
| Job execution | `bitrab/execution/job.py`, `bitrab/execution/shell.py` |
| Artifacts and dotenv passing | `bitrab/execution/artifacts.py` |
| Events and summaries | `bitrab/execution/events.py` |
| Persistent `.bitrab/` state | `bitrab/folder.py` |

## System architecture

There are three layers worth keeping separate when you read the code:

1. **Configuration layer.** `ConfigurationLoader` reads YAML, resolves includes, and merges raw config dictionaries.
2. **Planning layer.** `PipelineProcessor` converts raw dictionaries into `PipelineConfig`, `JobConfig`, `DefaultConfig`, and `RuleConfig`.
3. **Execution layer.** The stage or DAG runner schedules `JobConfig` objects, then `JobExecutor` turns scripts into shell subprocesses.

This separation is not perfect, but it is much clearer than "CLI directly runs YAML".

## Execution lifecycle

```text
argparse command
  -> resolve_config_path()
  -> ConfigurationLoader.load_config()
  -> PipelineProcessor.process_config()
  -> VariableManager(...)
  -> evaluate_rules(job, env, project_dir)
  -> LocalGitLabRunner.run_pipeline()
  -> StageOrchestrator or TUIOrchestrator
  -> StagePipelineRunner or DagPipelineRunner
  -> JobExecutor.execute_job()
  -> run_bash()
  -> collect_artifacts() / collect_dotenv_report()
  -> EventCollector.summary()
  -> folder.write_run_log()
```

`bitrab/plan.py` owns most of the bridge between configuration and execution. Even though the file name is historical, it currently contains both the planner (`PipelineProcessor`) and the high-level runtime façade (`LocalGitLabRunner`).

## Key abstractions

### `PipelineConfig`

Defined in `bitrab/models/pipeline.py`. This is the normalized pipeline model consumed by the runners:

- `stages`
- `variables`
- `default`
- `jobs`

### `JobConfig`

Also in `bitrab/models/pipeline.py`. This is the runtime-facing job model. Important fields include:

- stage and scripts
- merged variables
- retry settings
- `when`
- `allow_failure`
- `rules`
- `needs`
- `timeout`
- artifact and dependency metadata
- parallel expansion metadata (`parallel_total`, `parallel_index`)

### `JobRuntimeContext`

Defined in `bitrab/execution/job.py`. This is the frozen per-job execution bundle built by `JobExecutor.build_context()`. It carries the resolved environment, project directory, job directory, output writer, and timeout.

### `PipelineCallbacks`

Defined in `bitrab/execution/stage_runner.py`. This is the protocol that lets the same execution engine support:

- plain streaming CLI output
- Textual TUI output
- CI-mode buffered file output
- event capture

That callback protocol is the main "presentation layer" seam in the current architecture.

## Scheduling modes

Bitrab has two execution models in `bitrab/execution/stage_runner.py`:

1. **Stage mode** via `StagePipelineRunner`: jobs are grouped by stage, then jobs inside a stage can run serially or in parallel.
2. **DAG mode** via `DagPipelineRunner`: if any job has `needs:`, bitrab builds a `graphlib.TopologicalSorter` and executes ready jobs as dependencies complete.

Mixed behavior is implemented by `_build_dag()`: jobs without `needs:` get synthetic dependencies on all earlier-stage jobs, while jobs with `needs:` depend only on their explicit requirements.

## Architectural constraints

Some important constraints show up repeatedly in the code:

- **No container boundary.** Jobs run in the host shell, not in fresh runner containers.
- **Filesystem isolation is conditional.** Parallel jobs use per-job git worktrees by default when available; otherwise they can still interfere through the filesystem.
- **Partial GitLab compatibility.** Some features are supported, some warn, and some hard-fail.
- **Windows support matters.** The execution layer explicitly handles bash discovery and uses spawn-based multiprocessing.
