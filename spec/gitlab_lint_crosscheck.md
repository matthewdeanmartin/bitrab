# Spec: `bitrab validate --against-gitlab` (lint API cross-check)

**Status:** spec only — not scheduled.
**Why spec-only:** this repo is hosted on GitHub, so there is no natural GitLab
project to integration-test against. The feature needs a token and a real GitLab
project; automated testing from this repo's CI is impractical. Captured here so the
design is ready when a test venue exists.

## Problem

Bitrab reimplements GitLab's config pipeline: includes, `extends:`, `!reference`,
defaults, variable declaration merging. Any drift between bitrab's merged view and
GitLab's produces the worst failure mode this tool can have: *"it passed locally
but GitLab did something different."* GitLab exposes its own interpretation via the
CI Lint API — we can diff against the source of truth instead of guessing.

## GitLab API surface

- `POST /projects/:id/ci/lint` with `{"content": "<yaml>", "include_merged_yaml": true}`
  → `{ "valid": bool, "errors": [...], "warnings": [...], "merged_yaml": "<yaml>" }`.
  Project-scoped so `include: project/template` resolve with real permissions.
- `GET /projects/:id/ci/lint?include_merged_yaml=true` lints the config as committed
  on a ref (`&ref=`), useful for post-push verification.
- Auth: `PRIVATE-TOKEN` (PAT with `api` scope) or `CI_JOB_TOKEN` when running inside
  a GitLab job.

## Proposed CLI

```
bitrab validate --against-gitlab [--gitlab-url URL] [--gitlab-project ID|PATH] [--ref REF]
```

- Token from `GITLAB_TOKEN` env (never a CLI flag; never logged). Inside GitLab CI,
  fall back to `CI_JOB_TOKEN` + `CI_API_V4_URL` + `CI_PROJECT_ID` automatically —
  zero-config when it matters most.
- URL/project also configurable via `[tool.bitrab]` in pyproject.toml.

## Behavior

1. Load the local config file *raw* (before bitrab's own include processing) and
   POST it to the lint endpoint with `include_merged_yaml=true`.
2. Report GitLab-side `errors`/`warnings` verbatim (namespaced, e.g. `gitlab: jobs
   config should contain at least one visible job`).
3. Parse `merged_yaml`; run bitrab's own loader+merger on the same input; produce a
   **normalized structural diff**:
   - normalize before diffing: sort keys, drop keys bitrab intentionally ignores
     (`image`, `services`, ...) via an explicit allowlist-of-divergence, expand
     scalar/list shorthand (`script: "x"` vs `["x"]`);
   - diff at the job/keyword level and report per-job, e.g.
     `job "test": script differs`, `job "deploy": present in GitLab merge only`.
4. Exit codes: 0 = GitLab-valid and no unexplained structural diff; 1 = GitLab
   errors; 2 = valid but merged views diverge.
5. Never send `.env` / `.bitrab.env` contents; only the YAML files themselves. Warn
   that config is being sent to the configured GitLab server (external service).

## Failure handling

- No token / network / 401 / 404 → actionable error, but distinguish "cannot check"
  (exit 3) from "checked and failed" so CI can choose to soft-fail.
- Lint API payload limits: reject configs > ~1 MB with a clear message.

## Second consumer: bitrab's own test suite

The same differ, pointed at recorded fixtures, gives regression tests without the
API: check in pairs of (input yaml, `merged_yaml` captured once from a real GitLab)
under `test/fixtures/lint_crosscheck/` and assert bitrab's merge matches. Capturing
fixtures is a manual, occasional step — documented, not automated. This is how the
feature stays testable from a GitHub-hosted repo.

## Manual test plan (until fixtures exist)

1. Create a throwaway project on gitlab.com; push a config using `include:local`,
   `extends`, `!reference`, and a `template:` include.
2. Run with PAT: expect exit 2 until known gaps (e.g. `!reference` pre-sprint-05)
   close, then 0.
3. Run inside a GitLab CI job with `CI_JOB_TOKEN` and confirm zero-config path.

## Open questions

- How much of `merged_yaml` normalization is stable across GitLab versions? Pin a
  minimum supported GitLab version for the differ and record the version the
  fixtures came from.
- Should `--against-gitlab` also push `rules:` evaluation context (simulate a ref)
  via the lint API's `dry_run`/`ref` parameters? Likely phase 2.
