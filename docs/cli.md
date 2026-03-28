# CLI reference

## Global options

These apply to every subcommand:

| Flag | Description |
|---|---|
| `-c, --config PATH` | Path to config file (default: `.gitlab-ci.yml`) |
| `-v, --verbose` | Enable debug-level logging |
| `-q, --quiet` | Suppress everything except errors |
| `--version` | Print version and exit |
| `--license` | Print license text and exit |

---

## `bitrab run`

Execute the pipeline.

```
bitrab run [options]
```

| Flag | Description |
|---|---|
| `--dry-run` | Print what would run without executing |
| `--parallel N`, `-j N` | Max concurrent jobs per stage |
| `--jobs JOB...` | Run only the named jobs |
| `--stage STAGE...` | Run only jobs in the named stages |
| `--no-tui` | Disable the Textual TUI; use plain output |

Running `bitrab` with no subcommand is equivalent to `bitrab run`.

---

## `bitrab list`

Show all jobs organized by stage.

```
bitrab list
bitrab list -c other-ci.yml
```

Output example:

```
📋 Pipeline Jobs:
   Stages: build, test, deploy

🎯 Stage: build
   • compile
   • lint (retry: 2)

🎯 Stage: test
   • unit_tests
   • integration_tests
```

---

## `bitrab validate`

Validate the configuration file.

```
bitrab validate
bitrab validate --json
```

Runs three checks in order:

1. **Schema validation** — checks against the official GitLab CI JSON schema.
2. **Capability check** — warns about features bitrab cannot execute locally (e.g. `image:`, `trigger:`, remote includes).
3. **Semantic check** — verifies stages exist, jobs have scripts, etc.

| Flag | Description |
|---|---|
| `--json` | Also print the parsed pipeline as JSON |

Exits with code 0 if valid, 1 if not.

---

## `bitrab debug`

Print debug information about the pipeline and environment.

```
bitrab debug
```

Shows:

- Config file path and whether it exists
- Number of jobs and stages found
- Global variable count

Useful as a quick sanity check before running.

---

## `bitrab lint`

*Not yet implemented.* Will validate against the GitLab server-side linter API. Use `bitrab validate` for local checks.

---

## `bitrab graph`

*Not yet implemented.* Will render a visual dependency graph of the pipeline DAG.

---

## `bitrab clean`

*Not yet implemented.* Will remove `.bitrab/` artifacts and cache directories.
