# Codebase structure

The repository is small enough to navigate quickly once you know where responsibilities live.

## Top-level layout

| Path | Responsibility |
| --- | --- |
| `bitrab/` | Application code |
| `docs/` | User and developer documentation |
| `test/` | Test suite |
| `scripts/` | Helper scripts used by build and verification workflows |
| `spec/` | Design notes, roadmap ideas, and architecture review artifacts |
| `Makefile`, `Justfile` | Contributor task runners |
| `pyproject.toml` | Packaging, dependencies, tool configuration |
| `mkdocs.yml` | Docs navigation and theme |

## Application package layout

```text
bitrab/
  cli.py                  CLI parser and command handlers
  plan.py                 Pipeline normalization + top-level runner
  models/pipeline.py      Core dataclasses
  config/
    loader.py             YAML loading, include resolution, merge logic
    rules.py              rules: evaluation
    capabilities.py       supported vs ignored vs rejected GitLab features
    validate_pipeline.py  GitLab schema validation
  execution/
    shell.py              bash subprocess wrapper
    job.py                per-job execution, retries, timeouts
    stage_runner.py       stage-mode and DAG-mode scheduling
    scheduler.py          plain streaming orchestrator
    artifacts.py          local artifacts + dotenv report passing
    variables.py          CI variable synthesis and env assembly
    events.py             runtime event model and summaries
  tui/
    app.py                Textual UI
    orchestrator.py       TUI and CI log routing callbacks
    ci_mode.py            TTY/CI detection
  folder.py               .bitrab workspace scanning and cleanup
  graph.py                ASCII and DOT pipeline graph rendering
  watch.py                file watching and automatic re-runs
  mutation.py             mutation detection and parallel backend config
```

## Where core logic lives

### CLI surface

`bitrab/cli.py` owns:

- argparse setup in `create_parser()`
- command entry points like `cmd_run()`, `cmd_validate()`, `cmd_graph()`
- lazy imports so `--help` stays cheap
- config path selection through `resolve_config_path()`

### Planning and model construction

`bitrab/plan.py` owns:

- duration parsing via `parse_duration()`
- pipeline filtering via `filter_pipeline()`
- raw config normalization in `PipelineProcessor`
- top-level execution orchestration in `LocalGitLabRunner`

### Runtime

Execution is spread across a few focused modules:

- `execution/stage_runner.py`: scheduling and job batch execution
- `execution/job.py`: retries, timeout budgeting, script bundling
- `execution/shell.py`: actual subprocess management
- `execution/artifacts.py`: copy in / copy out filesystem behavior
- `execution/variables.py`: environment synthesis

### Support systems

- `execution/events.py` for structured observability
- `folder.py` for persisted run logs and cleanup
- `watch.py` for watch mode
- `graph.py` for graph output
- `mutation.py` for mutation detection and backend selection

## Helpers vs core modules

Core behavior changes usually involve one of these files:

- `bitrab/plan.py`
- `bitrab/models/pipeline.py`
- `bitrab/config/loader.py`
- `bitrab/config/rules.py`
- `bitrab/execution/stage_runner.py`
- `bitrab/execution/job.py`
- `bitrab/execution/shell.py`

Helper-style modules are more isolated:

- `bitrab/console.py`
- `bitrab/_json.py`
- `bitrab/_toml.py`
- `bitrab/utils/terminal_colors.py`

When reviewing a change, start by asking whether it changes the **normalized model**, the **scheduler**, or only the **presentation/persistence layer**.
