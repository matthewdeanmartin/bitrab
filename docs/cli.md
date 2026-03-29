# CLI reference

## Global options

| Flag | Description |
|---|---|
| `-c, --config PATH` | Path to config file |
| `-v, --verbose` | Debug logging |
| `-q, --quiet` | Errors only |
| `--version` | Print version |
| `--license` | Print license text |

## `bitrab run`

Execute the pipeline.

```bash
bitrab run [options]
```

| Flag | Description |
|---|---|
| `--dry-run` | Show what would execute |
| `--parallel N`, `-j N` | Maximum local concurrency |
| `--jobs JOB...` | Run only named jobs |
| `--stage STAGE...` | Run only selected stages |
| `--no-tui` | Disable the Textual interface |

Running plain `bitrab` is equivalent to `bitrab run`.[^cli]

## `bitrab watch`

Re-run the pipeline when the root config or local include files change.

```bash
bitrab watch
bitrab watch --dry-run
```

Supports the same `--parallel`, `--jobs`, and `--stage` filters as `run`.[^watch]

## `bitrab list`

List jobs grouped by stage.

```bash
bitrab list
```

## `bitrab validate`

Validate the configuration.

```bash
bitrab validate
bitrab validate --json
```

This performs schema validation, capability checks, and semantic checks.[^cli][^validate]

## `bitrab graph`

Render the pipeline structure.

```bash
bitrab graph
bitrab graph --format dot
```

`text` is the default. `dot` emits Graphviz DOT output.[^graph]

## `bitrab debug`

Show quick environment and pipeline facts.

```bash
bitrab debug
```

## `bitrab clean`

Clean `.bitrab/` workspace data.

```bash
bitrab clean
bitrab clean --dry-run
bitrab clean --what artifacts
```

## `bitrab logs`

Manage persisted run logs.

```bash
bitrab logs
bitrab logs show
bitrab logs rm --keep 5
```

## `bitrab folder`

Inspect or clean the `.bitrab/` folder with a size breakdown.

```bash
bitrab folder
bitrab folder clean --dry-run
```

## `bitrab lint`

Reserved for GitLab server-side linting. The command exists, but today it only reports that server-side linting is not implemented yet.[^cli]

[^cli]: Source: [bitrab/cli.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/cli.py)
[^watch]: Source: [bitrab/watch.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/watch.py)
[^validate]: Source: [bitrab/config/validate_pipeline.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/config/validate_pipeline.py) and [bitrab/config/capabilities.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/config/capabilities.py)
[^graph]: Source: [bitrab/graph.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/graph.py) and [bitrab/folder.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/folder.py)
