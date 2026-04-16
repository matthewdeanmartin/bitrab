# Execution engine

This page covers what makes bitrab distinct: jobs are run as native shell processes on the current machine.

## Command execution path

The stack for a single job is:

```text
StagePipelineRunner / DagPipelineRunner
  -> JobExecutor.build_context()
  -> JobExecutor.execute_job()
  -> JobExecutor._execute_with_context()
  -> JobExecutor._execute_scripts()
  -> execution.shell.run_bash()
```

## Process management

`execution.shell.run_bash()` is the real subprocess wrapper.

It supports:

- bash invocation by feeding a script over stdin
- `set -eo pipefail`
- streaming mode with threads
- capture mode with `communicate()`
- per-job timeout
- stdout/stderr capture even in streaming mode

On Windows, `_find_bash_windows()` searches:

1. `BITRAB_BASH_PATH`
2. `PATH`
3. common Git Bash / MSYS locations

## Output modes

There are three user-visible execution presentations:

| Mode | Main code | Behavior |
| --- | --- | --- |
| Plain streaming CLI | `execution/scheduler.py` | prints progress and job output to stdout |
| Textual TUI | `tui/app.py`, `tui/orchestrator.py` | one tab per job, live streamed output |
| CI file mode | `tui/orchestrator.py` | write each job to a file, print completed job logs after each stage |

All three reuse `PipelineCallbacks` plus `StagePipelineRunner`.

## Retries

Retries are implemented inside `JobExecutor`, not at the scheduler level.

Relevant functions:

- `_should_retry_when()`
- `_should_retry_exit_codes()`
- `_compute_delay_seconds()`

Environment variables influence retry timing:

- `BITRAB_RETRY_DELAY_SECONDS`
- `BITRAB_RETRY_STRATEGY`
- `BITRAB_RETRY_NO_SLEEP`

## Timeout handling

Timeouts originate in YAML, are parsed by `parse_duration()` in `plan.py`, stored on `JobConfig.timeout`, and enforced in `run_bash()`.

The timeout budget is treated as a whole-job deadline, not as a separate timeout per script line. `JobExecutor` converts the job timeout into a monotonic deadline and passes the remaining time to each script bundle.

## Environment handling

`VariableManager` in `execution/variables.py` synthesizes the environment for each job:

- GitLab-style built-ins like `CI_COMMIT_SHA`, `CI_COMMIT_BRANCH`, `CI_PROJECT_DIR`
- variables from `.env` and `.bitrab.env`
- pipeline variables
- job variables

`prepare_environment()` also sets per-job values such as:

- `CI_JOB_ID`
- `CI_JOB_STAGE`
- `CI_JOB_NAME`

`JobExecutor.build_context()` adds `CI_JOB_DIR` and can inject extra environment values from upstream dotenv reports.

## Artifacts and dependencies

Artifacts are implemented as local filesystem copies in `execution/artifacts.py`.

After a job:

- `collect_artifacts()` copies configured paths into `.bitrab/artifacts/<job>/`
- `collect_dotenv_report()` stores dotenv reports in a stable internal location

Before a job:

- `inject_dependencies()` copies artifacts from prior jobs back into the project tree
- `load_dotenv_reports()` loads dependency-produced environment variables

This is intentionally local persistence, not remote artifact publishing.

## State and persistence

The runtime writes state under `.bitrab/`:

- `.bitrab/<job>/` for per-job directories
- `.bitrab/artifacts/<job>/` for local artifacts
- `.bitrab/logs/<run_id>/` for persisted run records

`folder.write_run_log()` writes:

- `events.jsonl`
- `summary.txt`
- `meta.json`

Those files are built from `EventCollector` output.

## Security considerations

Bitrab is intentionally **not** a sandbox:

- scripts run on the host
- jobs share the checkout
- local environment variables can flow into job processes
- parallel jobs can race on shared files

A few safety measures do exist:

- unsupported GitLab features are surfaced explicitly
- timeout support prevents some runaway jobs
- mutation detection can warn when "read-only" jobs write unexpected files

But the trust model is still: **run pipelines you trust**.
