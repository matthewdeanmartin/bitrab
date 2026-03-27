# AGENTS.md — bitrab developer guide for AI agents

## Running the pipeline locally (dogfooding)

To run the project's own `.gitlab-ci.yml` locally:

```bash
uv run bitrab run --no-tui --parallel 1
```

- `--no-tui` disables the Textual TUI and prints streaming output instead (LLM-friendly)
- `--parallel N` controls parallelism per stage (default: CPU count)
- `--dry-run` prints what would run without executing

Full help: `uv run bitrab run --help`

## Running tests

```bash
uv run pytest
```

## Linting / type-checking

```bash
uv run ruff check .
uv run mypy bitrab/
```
