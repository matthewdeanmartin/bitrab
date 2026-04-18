# AGENTS.md — bitrab developer guide for AI agents

## Read the bitrab skill first

For repo-local bitrab usage patterns, read `.github/skills/bitrab/SKILL.md` before inventing new workflows.
It captures the shared quality-gate command, safe parallelism defaults, and when to prefer serial execution.

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

## Build workflows

Both `Makefile` and `Justfile` are supported and should stay feature-parity.

Discover targets with:

```bash
make help
just help
```

Preferred workflows:

```bash
make quality-gate
just quality-gate
make check-human
just check-human
```

- `quality-gate` validates and runs the shared `.bitrab-ci.yml` read-only gate through bitrab
- `fix` is the mutating phase
- `verify` is the read-only verification phase
- `check-ci` is non-mutating and CI-safe
- `check-llm` is compact and token-efficient
- `fast-verify` runs read-only checks in parallel with grouped logs
- `bugs` is the bug-finding oriented workflow
- `repro` runs serial verification for easier debugging
