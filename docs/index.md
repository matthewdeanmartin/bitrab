# Bitrab

**Run GitLab CI pipelines locally — no Docker, no admin rights, no GitLab required.**

Bitrab reads your `.gitlab-ci.yml` and runs jobs directly in your shell, on your machine, using your existing tools. Think of it as `make` or `just`, but with GitLab CI syntax.

It works on any host with Python: your laptop, GitHub Actions, AWS CodeBuild, or any other build runner.

---

## What it is

Bitrab is a lightweight Python tool that interprets GitLab CI YAML and executes jobs as native shell processes. It respects stage ordering, DAG `needs:` dependencies, retry logic, `when:` conditions, variable substitution, and artifact passing — without spinning up containers or talking to a GitLab server.

## What it is not

Bitrab is **not** a drop-in replacement for the official GitLab Runner. It does not:

- Run jobs inside Docker containers
- Pull images
- Talk to the GitLab API
- Handle secrets from GitLab's vault
- Enforce `rules:` branch/tag conditions

If you need full GitLab Runner behavior with container isolation, use the [official GitLab Runner](https://docs.gitlab.com/runner/). Bitrab trades isolation for simplicity and speed.

## Why use it?

| Situation | Bitrab helps |
|---|---|
| Debugging a CI config without pushing commits | Yes |
| Running a build pipeline on a plain host without Docker | Yes |
| Fast iteration on pipeline logic | Yes |
| Reproducible container-isolated builds | No — use GitLab Runner |
| Accessing GitLab-specific secrets or environments | No |

## Quick start

```bash
pipx install bitrab

cd your-project
bitrab
```

That's it. Bitrab picks up `.gitlab-ci.yml` from the current directory and runs it.

---

## Documentation

- [Installation](installation.md) — how to install and verify
- [Running pipelines](running.md) — `bitrab run` and its options
- [CLI reference](cli.md) — all commands and flags
- [Key concepts](concepts.md) — stages, DAGs, variables, artifacts, `when:`
- [Local vs GitLab differences](differences.md) — what works, what's skipped, what's blocked

GITLAB is a trademark of GitLab Inc. Bitrab is not affiliated with, endorsed by, or approved by GitLab Inc.
