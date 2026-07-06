# Bitrab Sprints

Implementation sequence agreed 2026-07-06. Each sprint is a self-contained deliverable;
later sprints build on earlier ones but nothing blocks hard on a prior sprint shipping.

## Order and rationale

| Sprint | Deliverable | Primary goal served |
|--------|-------------|---------------------|
| [01](sprint-01-cache.md) | Execute `cache:` locally (with multi-writer safety) | Speed locally, minutes in CI |
| [02](sprint-02-fingerprint.md) | Job fingerprint memoization (`--incremental`) | Instant re-runs for compute-constrained users |
| [03](sprint-03-vendor.md) | `bitrab vendor` — snapshot remote includes, `--offline` mode | Independence from GitLab |
| [04](sprint-04-changes-prepush.md) | `rules: changes` + `--changed` + `bitrab install-hook` | Pre-push story, monorepos |
| [05](sprint-05-parity-hardening.md) | `workflow: rules`, `resource_group` locks, `!reference`, remote-include hygiene | Parity + robustness |

Spec-only (not scheduled, hard to test from a GitHub-hosted repo):
[`spec/gitlab_lint_crosscheck.md`](../spec/gitlab_lint_crosscheck.md) — `bitrab validate --against-gitlab`
diffing bitrab's merged config against GitLab's `/ci/lint` API output.

## Cross-cutting constraints

- **Shared filesystem, multiple writers.** `.bitrab/` is shared across parallel jobs
  (worktrees do not isolate it) and potentially across concurrent `bitrab` processes.
  Every new persistent store (cache, fingerprints, vendor) must use atomic
  write-to-temp-then-rename plus a per-key lock file. No store may assume it is the
  only writer.
- **Honesty over accidental compatibility.** Any divergence from GitLab semantics gets
  a row in `docs/differences.md` and, where relevant, a capability diagnostic.
- **No containers, ever.** That is the product position, not a gap.
- **Windows is a first-class platform.** Locking, path handling, and process cleanup
  must work on Windows (msvcrt) and POSIX (fcntl).

## Working agreements

- No branches, no commits — the human handles all git operations.
- `uv run pytest` for tests; new features need unit tests plus at least one
  end-to-end test driving a real `.gitlab-ci.yml` through `bitrab run --no-tui`.
- Update `docs/differences.md` support matrix and `CHANGELOG.md` (Unreleased) with
  each feature.
