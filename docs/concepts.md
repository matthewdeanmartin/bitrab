# Key concepts

## Stages

Jobs are grouped into stages. All jobs in a stage run before the next stage starts. Stages are defined at the top level:

```yaml
stages:
  - build
  - test
  - deploy
```

If a job doesn't specify a stage it defaults to `test`.

If any job in a stage fails (and is not marked `allow_failure`), subsequent stages are skipped.

## Jobs

A job is any top-level key that isn't a reserved keyword (`stages`, `variables`, `default`, `include`, etc.).

```yaml
compile:
  stage: build
  script:
    - make build

unit_tests:
  stage: test
  script:
    - make test
```

Each job runs its `before_script`, then `script`, then `after_script` — in that order.

## DAG with `needs:`

By default jobs in the same stage run independently (or in parallel). `needs:` lets you express explicit dependencies that bypass stage ordering:

```yaml
build_a:
  stage: build
  script: make a

build_b:
  stage: build
  script: make b

test_combined:
  stage: test
  needs: [build_a, build_b]
  script: make test-combined
```

`test_combined` will wait for both `build_a` and `build_b` to finish, regardless of stage boundaries.

When any job in the pipeline has `needs:`, bitrab switches to DAG execution mode and uses topological ordering across all jobs.

## Variables

Variables are available as environment variables inside job scripts.

```yaml
variables:
  DEPLOY_ENV: staging

deploy:
  stage: deploy
  variables:
    DEPLOY_ENV: production   # overrides global
  script:
    - echo "Deploying to $DEPLOY_ENV"
```

**Precedence** (highest wins): job-level variables → global variables → built-in CI variables.

Built-in variables bitrab injects include `CI=true`, `CI_PROJECT_DIR`, `CI_JOB_NAME`, `CI_JOB_STAGE`, and others.

## `when:` conditions

Controls when a job runs relative to the success or failure of earlier jobs.

| Value | Runs when |
|---|---|
| `on_success` | All prior jobs (or direct `needs:`) succeeded (default) |
| `on_failure` | At least one prior job failed |
| `always` | Regardless of prior job outcomes |
| `manual` | Only when explicitly triggered (not run automatically) |
| `never` | Never (effectively disables the job) |

```yaml
notify_on_failure:
  stage: notify
  when: on_failure
  script:
    - send-slack-alert.sh

cleanup:
  stage: teardown
  when: always
  script:
    - rm -rf tmp/
```

## `allow_failure:`

Mark a job as non-blocking. If it fails, the pipeline continues. The job is still reported as failed, but it does not prevent later stages from running — unless you use `when: on_success` downstream, which will still be skipped because a failure occurred.

```yaml
lint:
  stage: test
  allow_failure: true
  script:
    - ruff check .

deploy:
  stage: deploy
  when: always    # use 'always' if you want this to run regardless
  script:
    - deploy.sh
```

`allow_failure` can also accept a list of exit codes:

```yaml
flaky_test:
  allow_failure:
    exit_codes: [2, 5]
```

## Retry

```yaml
flaky_job:
  retry:
    max: 3
    when: [script_failure]
```

Supported `when` values: `always`, `script_failure`, `runner_system_failure`, `stuck_or_timeout_failure`, `runner_unsupported`, `stale_schedule`, `job_execution_timeout`, `archived_failure`, `unmet_prerequisites`, `scheduler_failure`, `data_integrity_failure`.

You can also filter retries by exit code:

```yaml
retry:
  max: 2
  exit_codes: [1, 127]
```

Retries use exponential backoff by default. Set `BITRAB_RETRY_STRATEGY=constant` to use a fixed delay.

## Artifacts

Jobs can declare files to preserve after they finish:

```yaml
build:
  stage: build
  script:
    - make dist/app
  artifacts:
    paths:
      - dist/
    when: on_success   # on_success | on_failure | always
```

Artifacts are stored under `.bitrab/artifacts/<job_name>/`. Downstream jobs that list the producing job in `dependencies:` (or via `needs:`) will have those files copied into their working directory before they run.

```yaml
test:
  stage: test
  needs: [build]
  dependencies: [build]
  script:
    - ./dist/app --test
```

Set `dependencies: []` to explicitly receive no artifacts.

## `include:` files

Split large configs into smaller files:

```yaml
include:
  - local: ci/build.yml
  - local: ci/test.yml
```

Only local file includes are supported. Remote URLs, GitLab project includes, templates, and components are not fetched.

## Timeout

```yaml
long_job:
  timeout: 30m
  script:
    - run-long-process.sh
```

Supported units: `s`/`sec`/`second(s)`, `m`/`min`/`minute(s)`, `h`/`hr`/`hour(s)`, `d`/`day(s)`. Combinations like `1h 30m` also work.

## `default:` block

Set defaults that apply to every job:

```yaml
default:
  before_script:
    - source .env
  after_script:
    - cleanup.sh
```

Jobs can override individual fields.
