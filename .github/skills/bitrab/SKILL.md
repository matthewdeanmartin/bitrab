# bitrab

Use this skill when you need to design, validate, or run GitLab-style local CI workflows with bitrab, especially in this repository.

## When to use

- You need a shared quality gate that works from `make`, `just`, or bitrab directly.
- You are changing `.gitlab-ci.yml` or `.bitrab-ci.yml`.
- You want fast local feedback for CI logic without pushing to remote CI.
- You need to decide between serial and parallel execution safely.

## Repository defaults

- Canonical local quality gate: `.bitrab-ci.yml`
- Wrapper commands:
  - `make quality-gate`
  - `just quality-gate`
- Direct command:
  - `uv run bitrab -c .bitrab-ci.yml validate`
  - `uv run bitrab -c .bitrab-ci.yml run --no-tui --parallel 4 --parallel-backend thread --no-worktrees`

## Rules of thumb

1. Validate before running: use `bitrab validate` on the same config file you will execute.
2. Prefer `.bitrab-ci.yml` for repo-local experimentation; bitrab auto-prefers it when both CI files exist.
3. Keep read-only quality gates parallel. Use one stage with independent jobs unless you need DAG ordering.
4. Use `--serial` for mutating jobs such as formatters, autofixers, or code generation that should write into the real checkout.
5. For this repo, avoid nested `uv run` inside bitrab job scripts. Run bitrab itself under `uv run`, then use plain `python -m ...` or shell commands inside jobs so parallel runs reuse the active environment cleanly.
6. Prefer `--parallel-backend thread --no-worktrees` for this repository's quality gate. The jobs are shell-heavy and already spawn subprocesses, so thread orchestration avoids extra process-pool overhead, and `--no-worktrees` avoids recursive lint/test traversal into `.bitrab/worktrees/`.

## Shared quality-gate job set

The canonical read-only gate in this repo runs these jobs in parallel:

- `format_check`
- `ruff`
- `mypy`
- `pylint`
- `bandit`
- `smoke`
- `pytest`

These jobs should stay non-mutating and safe under bitrab worktree parallelism.

## Mutation safety

- `pyproject.toml` enables `warn_on_mutation = true` for bitrab.
- `junit.xml` is explicitly whitelisted because pytest writes it as part of the gate.
- If a read-only job starts modifying tracked files, treat that as a bug in the gate.

## Fast path

Use this when you want the quickest full local gate for this repository:

```bash
make quality-gate
```

or

```bash
just quality-gate
```

## Slow but safest path

Use this when debugging mutating jobs or worktree-related issues:

```bash
make quality-gate-serial
```

or

```bash
just quality-gate-serial
```
