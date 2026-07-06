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

## Incremental runs (`--incremental`)

```bash
bitrab run --incremental
```

With `--incremental`, bitrab skips any job whose *fingerprint* matches its last successful run — a
Turborepo-style memoization for pipelines, with no containers. Skipped jobs are reported with a distinct
`cached` status and counted separately in the summary. Memoized jobs still satisfy `needs:` and their
previously collected artifacts under `.bitrab/artifacts/<job>/` are still injected into downstream jobs; if
that artifact directory is gone, the job runs again. This is always opt-in — without the flag, nothing
changes.[^fingerprint]

### What the fingerprint sees

A job's fingerprint is a SHA-256 digest over:

1. The resolved `before_script`, `script`, and `after_script`.
2. The job's `variables:` (resolved values).
3. The values of environment variables you declare in `pyproject.toml`:

   ```toml
   [tool.bitrab]
   fingerprint_env = ["CC", "TOOLCHAIN_HOME"]
   ```

   This is an explicit salt for shared-environment inputs (toolchain paths, compiler versions) that scripts
   read but bitrab cannot infer.
4. A content digest of the job's input files. Precedence:
   - `variables: BITRAB_FINGERPRINT_PATHS: "src/**,pyproject.toml"` (comma-separated globs) — explicit wins;
   - `cache: key: files:` entries;
   - fallback: all **git-tracked** files, using git's own blob hashes (`git ls-files -s`) plus a hash of
     `git diff` for dirty working-tree state — nothing is re-hashed. Outside a git repository the fallback is
     inert: only scripts, variables, and explicitly declared paths are fingerprinted.
5. The fingerprints of every job this one `needs:`/depends on — an upstream change transitively re-runs
   downstream jobs.
6. The bitrab version and a schema version, so upgrades invalidate cleanly.

### What the fingerprint does NOT see

The outside world. Network resources, system package upgrades, tool installs, database state — none of these
change the fingerprint, so a "cached" job may be stale if its behaviour depends on them. Escape hatches:

- `bitrab run --incremental --refresh` — run everything, record fresh fingerprints;
- `bitrab clean --what fingerprints` — drop the store entirely.

Untracked files are also invisible to the git fallback; declare them via `BITRAB_FINGERPRINT_PATHS` if a job
depends on them.

Only successful jobs record a fingerprint; failed jobs (and jobs flagged by mutation detection) always re-run.
`bitrab run --dry-run --incremental` reports which jobs *would* be memoized without touching the store. The
store lives at `.bitrab/fingerprints/` under the project root and is safe against concurrent runs.

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
[^fingerprint]:
Source: [bitrab/execution/fingerprint.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/execution/fingerprint.py)
[^graph]: Source: [bitrab/graph.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/graph.py)
and [bitrab/folder.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/folder.py)
