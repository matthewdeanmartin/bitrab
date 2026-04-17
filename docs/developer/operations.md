# State, errors, observability, and testing

This page covers the support systems around execution rather than the scheduler itself.

## Persistent state in `.bitrab/`

`bitrab/folder.py` is the authority for the workspace folder.

Important APIs:

- `scan_folder()`
- `list_runs()`
- `prune_runs()`
- `clean_artifacts()`
- `clean_job_dirs()`
- `clean_logs()`
- `write_run_log()`

The design intent is simple:

- run logs should be cheap to list later
- size reporting should not require a full re-walk every time
- cleanup commands should map directly to workspace subtrees

## Structured events

`execution/events.py` adds a typed event layer around `PipelineCallbacks`.

Main types:

- `EventType`
- `PipelineEvent`
- `EventCollector`
- `PipelineSummary`

This layer is important because it decouples:

- execution
- summaries
- log persistence
- future UI/reporting work

If you are adding a new lifecycle hook, update the callback path and then decide whether it should also become a structured event.

## Error handling and recovery

Main exception types live in `bitrab/exceptions.py`:

- `BitrabError`
- `GitlabRunnerError`
- `JobExecutionError`
- `JobTimeoutError`

The error boundaries are roughly:

| Layer | Typical errors |
| --- | --- |
| config loading | `GitlabRunnerError` |
| job shell execution | `subprocess.CalledProcessError`, wrapped as `JobExecutionError` |
| timeout | `JobTimeoutError` |
| CLI surface | caught and reported in `cli.py` command handlers |

Recovery features today are narrow but useful:

- job-level retry support
- `allow_failure`
- persisted run summaries in `.bitrab/logs/`
- watch mode re-runs on config changes

There is no broad "resume partially completed pipeline" system.

## Logging and observability

Bitrab does not have a heavy centralized logging framework. Observability is mostly a mix of:

- user-facing console output via `safe_print()`
- structured runtime events
- persisted run summaries and event logs
- per-job log files in CI mode

This keeps the runtime understandable, but it also means feature work often has to decide between:

- human output
- structured event output
- both

## Testing strategy

The test suite in `test/` is broad and mostly organized by behavior area rather than package path.

Broad buckets:

| Area | Example tests |
| --- | --- |
| CLI behavior | `test/test_cli.py` |
| YAML/schema/validation | `test/test_schema.py`, `test/test_validate_pipeline.py`, `test/test_capabilities.py` |
| Config semantics | `test/test_rules.py`, `test/test_extends.py`, `test/test_matrix.py` |
| Runtime scheduling | `test/test_dag_execution.py`, `test/test_scenarios.py`, `test/test_scenario_dags.py` |
| Output and UI | `test/test_textual_app.py`, `test/test_tui_mode.py` |
| Support systems | `test/test_artifacts.py`, `test/test_events.py`, `test/test_folder.py`, `test/test_watch.py` |

The runtime also uses scenario-style tests under `test/scenarios/` to exercise more realistic pipeline shapes.

## Performance and concurrency notes

Parallelism is configurable, but the main bottlenecks are still:

- host CPU and process startup cost
- shared checkout filesystem contention
- log routing overhead in parallel/TUI modes

For performance-sensitive or correctness-sensitive changes, start with:

- `execution/stage_runner.py`
- `execution/shell.py`
- `execution/artifacts.py`
- `tui/orchestrator.py`

## Mutation detection

`mutation.py` is a bitrab-specific safety feature.

It snapshots the project tree before a job, compares it after the job, and warns on unexpected writes outside the builtin and configured whitelist. This is especially useful for keeping verification-style jobs honest, especially when they run serially in the real checkout or when worktree isolation is unavailable.
