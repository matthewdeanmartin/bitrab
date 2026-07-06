# Local vs GitLab differences

Bitrab is useful because it reuses GitLab CI syntax. It is trustworthy because it does not pretend to implement all of
GitLab.

## Biggest difference: no container boundary

GitLab Runner commonly gives each job a fresh container or VM context. Bitrab runs jobs directly in your shell on your
machine instead.[^stage][^job]

That changes the economics and the tradeoffs:

- startup is cheaper
- local debugging is easier
- queueing disappears when you run on your workstation
- isolation is weaker
- parallel jobs can still interfere if you disable worktrees or run outside a git checkout

## Support matrix

| Feature                                              | GitLab CI                     | Bitrab today                             |
|------------------------------------------------------|-------------------------------|------------------------------------------|
| `stages`                                             | Ordered execution groups      | Supported                                |
| `script`, `before_script`, `after_script`            | Run in runner environment     | Supported in your shell                  |
| `variables`                                          | Runner env injection          | Supported                                |
| `needs:`                                             | DAG scheduling                | Supported                                |
| `rules: if`                                          | Conditional evaluation        | Supported                                |
| `rules: exists`                                      | File existence rules          | Supported                                |
| `rules: when`, `allow_failure`, `variables`, `needs` | Rule-side overrides           | Supported                                |
| `rules: changes`                                     | Git-diff based evaluation     | Not implemented locally                  |
| `when:`                                              | Scheduling behavior           | Supported for local scheduling           |
| `allow_failure:`                                     | Non-blocking failures         | Supported                                |
| `retry:`                                             | Retry policy                  | Supported                                |
| `timeout:`                                           | Job timeout                   | Supported                                |
| `artifacts:`                                         | Persist and publish artifacts | Supported locally only                   |
| `dependencies:`                                      | Artifact download selection   | Supported locally only                   |
| `parallel:`                                          | Fan-out jobs                  | Supported                                |
| `parallel: matrix:`                                  | Matrix expansion              | Supported                                |
| `extends:`                                           | Template inheritance          | Supported                                |
| `include: local`                                     | Merge local config            | Supported                                |
| `include: remote` / `include: url`                   | Fetch remote config           | Supported                                |
| `include: template`                                  | GitLab template catalog       | Warned and skipped                       |
| `include: project`                                   | Cross-project config reuse    | Warned and skipped                       |
| `include: component`                                 | CI component includes         | Error                                    |
| `image:`                                             | Pull and run container image  | Ignored                                  |
| `services:`                                          | Sidecar containers            | Ignored                                  |
| `cache:`                                             | Shared cache semantics        | Supported locally (subset; see Cache)    |
| `workflow:`                                          | Pipeline-level creation rules | Ignored                                  |
| `trigger:`                                           | Child or downstream pipelines | Error                                    |
| `resource_group:`                                    | Cross-run mutex               | Ignored                                  |
| `environment:`                                       | Deployment metadata           | Ignored                                  |
| `release:`                                           | GitLab release creation       | Ignored                                  |
| `pages` job                                          | GitLab Pages deployment       | Script runs, no deployment               |
| `inputs:`                                            | Pipeline/component inputs     | Error                                    |
| `only:` / `except:`                                  | Legacy ref filters            | Not enforced locally                     |

## Includes

This is one place where "GitLab-like" and "GitLab-identical" are different:

- bitrab supports local includes
- bitrab can also fetch remote URL includes
- GitLab-managed include types such as `template`, `project`, and `component` are not available in the same way
  locally[^loader][^capabilities]

## Rules and branch context

Bitrab can evaluate local `rules:` expressions and `exists:` checks, but it does not have GitLab's full pipeline
context. That means GitLab-only variables or diff-driven logic can be absent or meaningless on your
machine.[^rules][^vars]

`only:` and `except:` are not enforced, so do not depend on them to protect a local run from deployment-style
jobs.[^plan]

## Cache

Bitrab executes `cache:` locally: matched paths are restored into the job's working directory before
`before_script` and saved back after scripts, into `.bitrab/cache/<key>/` under the project root (shared by
parallel worktree jobs). Supported subset:

- `paths:` (glob patterns), `key:` (literal, with `$VAR` expansion), `key: files:` (max 2 files) with
  `prefix:`, `policy:` (`pull-push`/`pull`/`push`), `when:` (`on_success`/`on_failure`/`always`), a list of up
  to 4 cache entries, and job-level wholesale override of the top-level/default `cache:` (with `cache: []` /
  `cache: {}` disabling caching for a job).
- Saves are atomic (staged writes published via a generation pointer) and guarded by per-key advisory locks;
  a lock timeout skips the cache step with a warning instead of failing the job.
- `bitrab run --no-cache` bypasses restore and save; `bitrab clean --what cache` deletes the store.

Not supported (ignored with a validation warning): `untracked:`, `unprotect:`, `fallback_keys:`. A
cross-runner distributed cache is meaningless locally.

## Fingerprint memoization is a bitrab-only feature

GitLab always re-runs every job in a pipeline. `bitrab run --incremental` skips jobs whose inputs — resolved
scripts, variables, declared environment salt, input files, and upstream job fingerprints — have not changed
since their last successful local run, reporting them with a distinct `cached` status. The fingerprint cannot
see outside-world changes (network resources, system packages, tool upgrades); `--refresh` and
`bitrab clean --what fingerprints` are the escape hatches, and the feature is strictly opt-in. See
[Incremental runs](running.md#incremental-runs---incremental) for the full contract.

## Artifacts are local, not uploaded

Bitrab stores artifacts in `.bitrab/artifacts/<job_name>/` and copies them between local jobs. It does not upload them
to GitLab or attach them to a pipeline record.[^artifacts]

## Parallel execution is lighter-weight, not runner-isolated

Parallelism in bitrab is about faster execution on a single host, not strict runner isolation. For real runs in a Git
checkout, bitrab uses per-job git worktrees by default when it can. If you disable worktrees, run outside a git repo, or
choose `--serial`, jobs run in the project root instead.[^stage][^cli]

## Mutation detection is a bitrab-only feature

GitLab does not natively warn that a supposedly read-only job rewrote part of your repository. Bitrab can.

When `warn_on_mutation = true` is enabled, bitrab snapshots the filesystem before each job, compares it afterward, and
reports unexpected changes outside a whitelist. Built-in cache patterns such as `.pytest_cache/**`, `.mypy_cache/**`,
`__pycache__`, `.bitrab/**`, and common coverage outputs are ignored by default.[^mutation]

This is useful for keeping `verify`, `check`, and other non-mutating workflows honest.[^mutation]

## Validation behavior

`bitrab validate` combines three checks:

1. GitLab schema validation
2. local capability diagnostics
3. structural validation of the parsed pipeline

So the tool can tell you both "this YAML is malformed" and "this YAML is valid GitLab syntax but bitrab will ignore or
block part of it locally".[^cli][^validate]

[^stage]:
Source: [bitrab/execution/stage_runner.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/execution/stage_runner.py)
[^job]: Source: [bitrab/execution/job.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/execution/job.py)
[^loader]:
Source: [bitrab/config/loader.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/config/loader.py)
[^capabilities]:
Source: [bitrab/config/capabilities.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/config/capabilities.py)
[^rules]: Source: [bitrab/config/rules.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/config/rules.py)
[^vars]:
Source: [bitrab/execution/variables.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/execution/variables.py)
[^plan]: Source: [bitrab/plan.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/plan.py)
[^artifacts]:
Source: [bitrab/execution/artifacts.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/execution/artifacts.py)
[^mutation]: Source: [bitrab/mutation.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/mutation.py)
and [bitrab/execution/stage_runner.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/execution/stage_runner.py)
[^cli]: Source: [bitrab/cli.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/cli.py)
[^validate]:
Source: [bitrab/config/validate_pipeline.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/config/validate_pipeline.py)
