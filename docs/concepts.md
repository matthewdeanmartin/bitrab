# Key concepts

## Stages

Jobs are grouped into stages. In the default execution model, bitrab runs stages in order and stops later `on_success`
work after a hard failure.[^stage]

```yaml
stages:
  - build
  - test
  - deploy
```

## Jobs

A job is any top-level mapping that is not a reserved CI keyword.

```yaml
compile:
  stage: build
  script:
    - make build
```

Each job executes `before_script`, then `script`, then `after_script`.[^plan][^job]

## `needs:` and DAG execution

If any job declares `needs:`, bitrab switches from pure stage scheduling to DAG scheduling.

```yaml
build_a:
  stage: build
  script: make a

build_b:
  stage: build
  script: make b

test_combined:
  stage: test
  needs: [ build_a, build_b ]
  script: make test-combined
```

That means `test_combined` waits on the listed jobs, not the entire prior stage.[^stage]

## Variables

Bitrab builds each job environment from built-in CI-style variables plus pipeline variables plus job variables.

```yaml
variables:
  DEPLOY_ENV: staging

deploy:
  variables:
    DEPLOY_ENV: production
```

Job-level values override broader ones.[^vars]

## `rules:`

Bitrab supports a practical local subset of `rules:`:

- `if`
- `exists`
- `when`
- `allow_failure`
- `variables`
- `needs`

Rules are evaluated in order, and the first match wins. If no rule matches, the job becomes `when: never`.
`rules: changes` is not evaluated locally.[^rules][^capabilities]

## `when:` and `allow_failure`

Bitrab applies `when:` during scheduling:

- `on_success` runs when prior required work succeeded
- `on_failure` runs after a prior failure
- `always` always runs
- `manual` is not auto-run by normal local scheduling
- `never` is skipped

`allow_failure` keeps a failing job from hard-failing the pipeline, though it still affects `on_failure`
logic.[^stage][^plan]

## Retry

Bitrab parses GitLab-style retry config:

```yaml
flaky_job:
  retry:
    max: 3
    when: [ script_failure ]
```

It also supports `exit_codes` filters and configurable backoff timing through environment variables.[^plan][^job]

## Artifacts and dependencies

Bitrab supports local artifact collection and dependency injection:

```yaml
build:
  artifacts:
    paths:
      - dist/

test:
  dependencies: [ build ]
```

Artifacts are copied into `.bitrab/artifacts/<job_name>/`, then copied back into the workspace for downstream jobs that
request them, or for all prior artifact-producing jobs when `dependencies` is omitted.[^artifacts]

## `parallel:` and matrix expansion

Bitrab expands both forms of GitLab job fan-out:

- `parallel: 4`
- `parallel: { matrix: ... }`

Expanded jobs receive `CI_NODE_INDEX` and `CI_NODE_TOTAL`, and downstream `needs:` or `dependencies:` can be rewritten
to point at the expanded job names.[^plan]

## Includes

Bitrab merges includes before processing jobs. Local includes are supported, remote HTTP includes are fetched, and some
GitLab-specific include forms are intentionally not supported or are only warned about.[^loader][^capabilities]

## Mutation detection

Mutation detection is a bitrab-specific feature for policing "read-only" jobs.

When enabled in `pyproject.toml`, bitrab:

1. snapshots the project tree before a job starts
2. snapshots again after the job finishes
3. reports files created or modified outside the built-in and custom whitelist

This is especially useful for keeping `verify`-style jobs honest when tools secretly write caches or generated
files.[^mutation]

## Timeout

Bitrab parses GitLab-style timeout strings such as `30m` or `1h 30m` and passes the resolved limit into job
execution.[^plan]

[^stage]:
Source: [bitrab/execution/stage_runner.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/execution/stage_runner.py)
[^plan]: Source: [bitrab/plan.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/plan.py)
[^job]: Source: [bitrab/execution/job.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/execution/job.py)
[^vars]:
Source: [bitrab/execution/variables.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/execution/variables.py)
[^rules]: Source: [bitrab/config/rules.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/config/rules.py)
[^capabilities]:
Source: [bitrab/config/capabilities.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/config/capabilities.py)
[^artifacts]:
Source: [bitrab/execution/artifacts.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/execution/artifacts.py)
[^mutation]: Source: [bitrab/mutation.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/mutation.py)
and [bitrab/execution/stage_runner.py](https://github.com/matthewdeanmartin/bitrab/blob/main/bitrab/execution/stage_runner.py)
