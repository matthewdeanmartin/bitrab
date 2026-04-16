# Core concepts

These are the domain concepts bitrab uses internally. Keeping them straight matters because the same GitLab keyword can affect multiple layers.

## Pipeline

Internally, a pipeline is a `PipelineConfig` dataclass from `bitrab/models/pipeline.py`.

It contains:

- ordered `stages`
- global `variables`
- a `default` block
- concrete `jobs`

By the time a `PipelineConfig` exists, includes have already been merged and `extends:` has already been resolved.

## Job

A job is a `JobConfig` dataclass. A top-level YAML mapping becomes a job if:

- its key is not in `PipelineProcessor.RESERVED_KEYWORDS`
- it is not a hidden template name like `.base`
- its value is a mapping

The execution-facing job model includes:

- shell command lists: `before_script`, `script`, `after_script`
- scheduling metadata: `stage`, `when`, `needs`
- failure policy: `retry_*`, `allow_failure*`, `timeout`
- filesystem behavior: `artifacts_*`, `dependencies`
- expansion metadata for `parallel:` and matrix fan-out

## Stages

Stages are still first-class even though bitrab also supports DAG execution.

- In pure stage mode, stages are the primary ordering mechanism.
- In DAG mode, stages still matter because `_build_dag()` uses them to synthesize dependencies for jobs that do not declare `needs:`.

This means stage order is not just display metadata.

## `needs:` and DAG scheduling

If any job has `needs:`, `StagePipelineRunner.execute_pipeline()` delegates to `DagPipelineRunner`.

Important consequence: bitrab does **not** run two separate execution engines. The stage runner is the front door, and it switches to DAG mode when needed.

## Rules

Rules are represented as `RuleConfig` dataclasses and applied by `config.rules.evaluate_rules()`.

Current supported rule keys:

- `if`
- `exists`
- `when`
- `allow_failure`
- `variables`
- `needs`

Rules are evaluated in order; the first match wins. If no rule matches, the job is rewritten to `when: never`.

## Variable handling

There are several variable layers:

1. host `os.environ`
2. synthesized CI-style built-ins from `VariableManager`
3. local dotenv files (`.env`, `.bitrab.env`)
4. pipeline/global variables
5. default/job variables
6. upstream dotenv report variables for dependencies

The exact merge points matter, especially because `JobExecutor.build_context()` re-applies job variables after loading upstream dotenv report values so job-level YAML remains authoritative.

## Executors

Bitrab does not currently have a plugin executor system. In practice, the executor is:

- **native bash** via `execution.shell.run_bash()`

The "executor" abstraction today is mostly embodied by `JobExecutor` plus the `PipelineCallbacks` output hooks, not by interchangeable shell/docker backends.

## Workspace model

Jobs run from the project directory, not from an isolated copied checkout. `JobExecutor._execute_with_context()` explicitly uses `ctx.project_dir` as `cwd`.

Per-job directories still exist under `.bitrab/<job-name>/`, but they are exposed to scripts as `CI_JOB_DIR`, not used as the main working directory.

That is one of the most important behavioral differences from containerized GitLab Runner execution.
