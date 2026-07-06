# Sprint 02 — Job fingerprint memoization (`--incremental`)

**Status:** delegated to subagent (2026-07-06)
**Delegable:** partially — the hashing/store layer is bot-friendly; the "what goes
into the fingerprint" policy decisions should be reviewed by a human before wiring in.

## Goal

Skip jobs whose inputs have not changed since their last successful run. For
compute-constrained users this turns "re-run the whole pipeline" into seconds.
Turborepo-style memoization, no containers.

**Accepted limitation (by design):** the fingerprint cannot see outside-world
changes — network resources, system packages, tool upgrades outside the repo.
Document this prominently; provide `--refresh` (and `bitrab clean --what fingerprints`)
as the escape hatch. This is opt-in via `--incremental`, never default.

## Fingerprint composition

Hash (SHA-256) over a canonical JSON encoding of:

1. Resolved `before_script` + `script` + `after_script` (post-extends, post-!reference
   when that lands, post-variable-*declaration* — not expansion of runtime env).
2. Job `variables:` (resolved values as they will be exported).
3. The subset of the shared environment the job's scripts reference is NOT included
   (undecidable); instead include an explicit user-declared salt:
   `[tool.bitrab] fingerprint_env = ["PATH_TO_TOOLCHAIN", ...]` from pyproject.toml,
   whose *values* get hashed in.
4. Content hash of input files. Sources of input patterns, in order of preference:
   - job-level `bitrab`-specific override: `variables: BITRAB_FINGERPRINT_PATHS`
     (comma-separated globs) — explicit wins;
   - `rules: changes:` patterns when present (sprint 04 synergy);
   - `cache: key: files:` entries;
   - fallback: all git-tracked files (`git ls-files -z` + content hash via
     `git rev-parse HEAD:` where clean, plus hash of `git diff` for dirty state —
     cheap because git already stores blob hashes).
5. Fingerprints of all jobs this job `needs:`/depends on (transitive input capture:
   if an upstream job re-ran, downstream re-runs).
6. bitrab version + a schema-version integer so format changes invalidate cleanly.

## Store

- `.bitrab/fingerprints/<sanitized-job-name>.json`:
  `{"fingerprint": "...", "status": "success", "completed_at": "...", "bitrab": "x.y.z"}`
- Only *successful* completions are recorded. Failed/skipped jobs never memoize.
- **Multi-writer safety:** same rules as the cache store — write temp + `os.replace`,
  per-file lock via the shared lock helper from sprint 01. A stale or corrupt
  fingerprint file is treated as a miss, never an error.
- A job that fails mutation detection (when enabled) should not record a fingerprint —
  its outputs are untrustworthy.

## Runtime behavior

- `bitrab run --incremental`: before executing a job, compute its fingerprint; on
  match with a recorded success, mark the job **skipped-memoized** — distinct status
  in the TUI/CI output (e.g. `↷ cached`), counted separately in the summary line.
- Memoized jobs still satisfy `needs:` and still *inject artifacts* into downstream
  jobs: artifacts from the previous run remain in `.bitrab/artifacts/<job>/`, so
  dependency injection works unchanged. If the artifact dir is missing, treat as a
  fingerprint miss and run.
- `--refresh` (with `--incremental`) forces a run but still records new fingerprints.
- Dry-run reports which jobs *would* be memoized.

## Acceptance criteria

- E2E: run twice with `--incremental`; second run executes zero jobs, reports all
  as cached, exits 0, downstream artifact injection still works.
- Touching a tracked source file, changing a script line, or changing a job variable
  each invalidates exactly the affected jobs (plus transitive dependents via rule 5).
- Failed job never memoizes; next run retries it.
- Corrupt fingerprint file → job runs normally (miss), file is rewritten.
- Concurrent runs do not corrupt the store.
- Docs: new page section explaining what the fingerprint sees and does not see
  (outside-world caveat, `fingerprint_env` salt), CHANGELOG entry.
