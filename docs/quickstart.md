# Quick start

This guide gets you from install to a useful local CI loop quickly.

## 1. Install bitrab

```bash
pipx install bitrab
```

If you want the native speedups:

```bash
pipx install 'bitrab[fast]'
```

Or for this repository while developing:

```bash
uv sync --all-extras
uv run bitrab --version
```

## 2. Validate your pipeline

```bash
bitrab validate
```

This checks the YAML against GitLab's schema, then reports features that bitrab handles differently locally.[^validate]

## 3. Run the pipeline locally

```bash
bitrab run --no-tui --parallel 1
```

That is the safest starting point: plain output, one job at a time, same workspace.[^cli][^stage]

## 4. Speed it up when jobs are independent

```bash
bitrab run --no-tui --parallel 4
```

Jobs in the same stage can run concurrently, and DAG pipelines can also release work as dependencies complete. Remember that these jobs share one working tree unless your scripts isolate themselves.[^stage]

## 5. Run only part of the pipeline

```bash
bitrab run --jobs lint test
bitrab run --stage build test
```

This is useful for short local feedback loops while keeping `.gitlab-ci.yml` as the source of truth.[^cli]

## 6. Enable mutation warnings for read-only jobs

Add this to `pyproject.toml`:

```toml
[tool.bitrab]
warn_on_mutation = true

[tool.bitrab.mutation]
whitelist = ["docs/**"]
```

When enabled, bitrab snapshots the project tree before a job and warns if the job creates or modifies files outside the built-in and custom whitelist.[^mutation]

## 7. Try the dogfooding command

In this repository, the local CI loop is:

```bash
uv run bitrab run --no-tui --parallel 1
```

## 8. Useful next commands

```bash
bitrab watch
bitrab graph
bitrab logs
bitrab clean --dry-run
```

[^validate]: Source: [bitrab/cli.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/cli.py) and [bitrab/config/validate_pipeline.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/config/validate_pipeline.py)
[^cli]: Source: [bitrab/cli.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/cli.py)
[^stage]: Source: [bitrab/execution/stage_runner.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/execution/stage_runner.py)
[^mutation]: Source: [bitrab/mutation.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/mutation.py) and [bitrab/execution/stage_runner.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/execution/stage_runner.py)
