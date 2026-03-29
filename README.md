# Bitrab 🐰

Bitrab runs your `.gitlab-ci.yml` on a plain machine so you can find problems before you push, wait, and repeat.

It is built for the expensive part of CI: feedback latency and duplicated setup. Instead of burning build minutes on
every small YAML tweak, you can validate and run the same pipeline definition locally, then reuse that same definition
inside CI when it makes sense.

## Why you might want to use it

- Run GitLab-style pipelines locally and catch failures before pushing.
- Shorten the edit-test-debug loop for CI changes.
- Reuse `.gitlab-ci.yml` outside GitLab on any Python-capable host.
- Run multiple jobs in parallel inside one host or container instead of paying full per-job startup overhead every time.
- Keep one build script closer to reality instead of maintaining separate local and CI scripts.

The theme is simple: save developer time, save CI minutes, and stop paying for "push, wait, discover typo, push again".

## Where the savings come from

GitLab CI is great at orchestrating remote jobs, but remote feedback is naturally slower:

- every iteration costs a push or MR update
- every job pays queue and startup overhead
- every "just checking if the YAML works" run consumes minutes
- local and remote scripts can drift when they are maintained separately

Bitrab helps by moving more of that loop earlier:

- validate and dry-run the pipeline locally
- execute jobs directly on your workstation when container isolation is unnecessary
- use the same pipeline file in CI with `bitrab run --no-tui --parallel N` to fan jobs out inside one container
- optionally keep one source of truth for your build steps

## Quick start

```bash
pipx install bitrab
bitrab validate
bitrab run --no-tui --parallel 1
```

For this repo's own dogfooding flow:

```bash
uv run bitrab run --no-tui --parallel 1
```

## What Bitrab is

Bitrab is a local runner for a practical subset of GitLab CI. It executes jobs as native shell processes, supports stage
execution, DAG `needs:`, job filtering, retries, local and remote includes, artifacts, watch mode, graph output, and
optional mutation warnings.

## What Bitrab is not

Bitrab is not a drop-in replacement for GitLab Runner.

- It does not provide container isolation.
- `image:` and `services:` are not executed locally.
- Some GitLab features are ignored, partially supported, or intentionally blocked.
- GitLab-specific server features still need GitLab.

The docs call these differences out explicitly instead of pretending full compatibility.

## Docs

- [Quick start](docs/quickstart.md)
- [Installation](docs/installation.md)
- [Running pipelines](docs/running.md)
- [Key concepts](docs/concepts.md)
- [Local vs GitLab differences](docs/differences.md)
- [CLI reference](docs/cli.md)

GITLAB is a trademark of GitLab Inc. Bitrab is not affiliated with, endorsed by, or approved by GitLab Inc.
