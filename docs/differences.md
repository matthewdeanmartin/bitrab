# Local vs GitLab differences

Bitrab is intentionally honest about what it supports. This page documents where bitrab diverges from GitLab CI behavior.

## The big one: no containers

GitLab Runner runs each job inside a Docker (or other) container. Bitrab does not. Jobs run directly in your shell, in your current working directory.

This means:

- Jobs share the same filesystem — they can read and write each other's files unless you manage that explicitly.
- The environment your jobs see is your local environment, not a clean container image.
- Tools referenced in scripts must be installed on your machine. There is no `image: node:20` that pulls and runs a container.
- There is no isolation between jobs running in parallel.

If your pipeline relies on container isolation (e.g. running tests against a fresh database image), bitrab is not the right tool for that run. It is the right tool for running the parts of the pipeline that don't need a container.

## Feature support table

| Feature | GitLab behavior | Bitrab behavior |
|---|---|---|
| `stages` | Ordered groups | Fully supported |
| `script`, `before_script`, `after_script` | Runs in container | Runs in your shell |
| `variables` | Injected into container env | Injected into shell env |
| `needs:` | DAG ordering | Fully supported |
| `when:` | Job condition | Fully supported |
| `allow_failure:` | Non-blocking failure | Fully supported |
| `retry:` | Retry on failure | Fully supported |
| `artifacts:` | Uploaded to GitLab | Stored in `.bitrab/artifacts/` |
| `dependencies:` | Downloads artifacts | Copies from `.bitrab/artifacts/` |
| `timeout:` | Kills job after limit | Kills subprocess after limit |
| `include: local` | Merges local file | Fully supported |
| `image:` | Pulls Docker image | **Ignored** — no container execution |
| `services:` | Starts sidecar containers | **Ignored** |
| `cache:` | Caches paths between runs | **Ignored** |
| `include: remote` | Fetches from URL | **Skipped** (warning emitted) |
| `include: template` | Fetches from GitLab | **Skipped** (warning emitted) |
| `include: project` | Fetches from other repo | **Skipped** (warning emitted) |
| `include: component` | CI component | **Error** — not supported |
| `trigger:` | Starts child pipeline | **Error** — not supported |
| `workflow:` | Pipeline-level conditions | **Ignored** — no pipeline source context |
| `rules: changes:` | File-change conditions | **Ignored** — not evaluated |
| `only:` / `except:` | Branch/tag conditions | **Parsed but not enforced** |
| `environment:` | Deployment tracking | **Ignored** — no GitLab deployment API |
| `release:` | Creates a GitLab release | **Ignored** |
| `pages` job | Deploys to GitLab Pages | Script runs, no deployment |
| `resource_group:` | Mutual exclusion | **Ignored** — not enforced locally |
| `inputs:` | Pipeline/component inputs | **Error** — not supported |

## Variable differences

Bitrab injects a set of built-in CI variables (`CI`, `CI_PROJECT_DIR`, `CI_JOB_NAME`, etc.) but these reflect your local runtime, not a GitLab pipeline context. Variables that depend on GitLab infrastructure — like `CI_JOB_TOKEN`, `CI_REGISTRY`, `GITLAB_USER_LOGIN` — are not set.

Your local shell environment variables are inherited by jobs. Variables defined in `.gitlab-ci.yml` take precedence over inherited environment variables.

## `only:` and `except:` are not enforced

GitLab uses these to decide whether a job should run on a given branch or tag. Bitrab runs all jobs regardless. If you rely on `only: [main]` to gate deployment jobs, those jobs will run locally too.

Use `when: manual` or `--jobs` / `--stage` filters to explicitly skip jobs you don't want to run locally.

## Parallel jobs share a workspace

When you use `--parallel N`, multiple jobs in the same stage run concurrently in the same working directory. Unlike GitLab Runner with Docker, there is no isolation. Jobs that write to the same paths will interfere with each other.

Serial execution (the default) avoids this entirely.

## Artifacts are local only

Artifacts are stored in `.bitrab/artifacts/<job_name>/` and are not uploaded anywhere. They persist between runs until you clean them up (or run `bitrab clean`, once implemented).

## What `bitrab validate` checks

Running `bitrab validate` before `bitrab run` is a good habit. It will:

- Validate the YAML against the GitLab CI JSON schema.
- Warn you about features that will be silently ignored (like `image:` or `cache:`).
- Error on features that cannot work at all locally (like `trigger:` or `include: component`).

This catches configuration problems before you waste time on a failed run.
