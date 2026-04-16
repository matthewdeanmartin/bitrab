# Getting started for developers

This is the contributor workflow for working on bitrab itself, not the end-user quick start.

## Environment setup

Bitrab is a Python project managed through `uv`. Runtime and development dependencies live in `pyproject.toml`.

Typical first-time setup:

```bash
uv sync --all-extras
```

The project also exposes the CLI directly through `pyproject.toml`:

```text
[project.scripts]
bitrab = "bitrab.cli:main"
```

So most local workflows use `uv run bitrab ...`.

## Recommended local workflows

Both `Makefile` and `Justfile` are first-class. The contributor docs and build files keep them in feature parity.

```bash
make help
just help
make check-human
just check-human
```

Useful workflows:

| Goal | Command |
| --- | --- |
| Run mutating fixers then verification | `make check-human` / `just check-human` |
| Read-only verification | `make verify` / `just verify` |
| Faster parallel verification | `make fast-verify` / `just fast-verify` |
| Reproduction-friendly serial checks | `make repro` / `just repro` |
| Docs site build | `uv run mkdocs build --strict` |

## Running bitrab against this repo

This project dogfoods bitrab against its own CI pipeline:

```bash
uv run bitrab run --no-tui --parallel 1
```

That is the best way to understand real runtime behavior from the outside in.

## Running tests

The main test suite lives under `test/`.

Common commands:

```bash
uv run pytest
uv run pytest test/test_cli.py
uv run pytest test/test_dag_execution.py
uv run pytest test/test_textual_app.py
```

Representative test areas:

- `test/test_cli.py` for command parsing and CLI behavior
- `test/test_validate_pipeline.py` and `test/test_schema.py` for validation
- `test/test_rules.py`, `test/test_extends.py`, `test/test_matrix.py` for config semantics
- `test/test_dag_execution.py`, `test/test_scenarios.py`, `test/test_scenario_dags.py` for runtime behavior
- `test/test_shell.py`, `test/test_timeout.py`, `test/test_artifacts.py` for execution details
- `test/test_events.py`, `test/test_folder.py`, `test/test_watch.py` for support subsystems

## Debugging basics

Good starting points:

1. `uv run bitrab validate` to check schema, capability diagnostics, and structural parsing.
2. `uv run bitrab list` to inspect the parsed pipeline without running jobs.
3. `uv run bitrab graph --format text` to inspect stage and `needs:` relationships.
4. `uv run bitrab debug` for config path and parsed pipeline counts.

For code-level debugging, start at:

- `bitrab/cli.py` for command dispatch
- `bitrab/plan.py` for loading, normalization, and runner selection
- `bitrab/execution/stage_runner.py` for scheduling
- `bitrab/execution/job.py` for retries, timeouts, and shell execution

## Documentation workflow

The published docs use MkDocs with the Read the Docs theme. The nav is configured in `mkdocs.yml`, and the site source lives under `docs/`.
