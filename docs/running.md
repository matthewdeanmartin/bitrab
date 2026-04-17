# Running pipelines

## Basic usage

Run from the directory that contains your `.gitlab-ci.yml` or `.bitrab-ci.yml`:

```bash
bitrab
```

or explicitly:

```bash
bitrab run
```

If both files exist, bitrab prefers `.bitrab-ci.yml` unless you pass `-c` explicitly.[^loader][^cli]

## Pick a config file

```bash
bitrab run -c path/to/my-ci.yml
```

## Dry run

```bash
bitrab run --dry-run
```

Dry run shows what would execute without launching job scripts.[^cli]

## Parallel execution

```bash
bitrab run --parallel 4
```

In stage mode, jobs in the same stage can run concurrently. In DAG mode, jobs are released as soon as their `needs:`
dependencies are satisfied. For real runs in a Git checkout, parallel jobs use per-job git worktrees by default when
they can. Use `--serial` when jobs need to mutate the real working tree instead of an isolated checkout.[^stage][^cli]

## Filters

Run only named jobs:

```bash
bitrab run --jobs lint test
```

Run only selected stages:

```bash
bitrab run --stage build test
```

You can combine both. Unknown names generate warnings instead of crashing.[^plan][^cli]

## CI-friendly output

```bash
bitrab run --no-tui
```

Bitrab uses a Textual TUI in interactive mode, but plain output is usually the better choice for CI logs, shell
transcripts, and LLM-driven sessions. CI mode also disables the TUI automatically when `CI=true` is present.[^cli][^tui]

## Run bitrab inside CI

One of bitrab's more interesting uses is running it inside a single CI job:

```yaml
local_ci:
  script:
    - pipx install bitrab
    - bitrab run --no-tui --parallel 4
```

That lets one container or VM execute several pipeline jobs in parallel, which can reduce queueing and repeated
environment setup compared with splitting every small task into a separate remote CI job. It is not identical to native
GitLab fan-out, but it can save build minutes when isolation is unnecessary.[^stage][^plan]

## Watch mode

```bash
bitrab watch
```

Watch mode reruns the pipeline when the root config file or any local include file changes.[^watch]

## Logs, graph, and cleanup

```bash
bitrab logs
bitrab graph
bitrab clean --dry-run
```

Run logs are stored under `.bitrab/logs/`, graph output can be rendered as text or Graphviz DOT, and cleanup commands
remove artifacts, job directories, and logs under `.bitrab/`.[^cli][^graph]

[^loader]:
Source: [bitrab/config/loader.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/config/loader.py)
[^cli]: Source: [bitrab/cli.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/cli.py)
[^plan]: Source: [bitrab/plan.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/plan.py)
[^stage]:
Source: [bitrab/execution/stage_runner.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/execution/stage_runner.py)
[^tui]:
Source: [bitrab/tui/orchestrator.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/tui/orchestrator.py)
and [bitrab/tui/ci_mode.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/tui/ci_mode.py)
[^watch]: Source: [bitrab/watch.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/watch.py)
[^graph]: Source: [bitrab/graph.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/graph.py)
and [bitrab/folder.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/folder.py)
