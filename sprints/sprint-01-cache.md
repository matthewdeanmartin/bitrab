# Sprint 01 — Execute `cache:` locally

**Status:** complete (2026-07-06) — implemented by subagent, verified (914 passed, 3 skipped); changes in working tree awaiting human commit
**Delegable:** yes — well-bounded, clear spec, existing patterns to follow (artifacts).

## Goal

`cache:` is currently parsed by the schema and ignored by execution
(`docs/differences.md`: "Parsed by schema, not executed by bitrab"). Implement real
local cache semantics so repeated runs skip re-downloading/re-building dependency
trees, both on a workstation and when bitrab fans out inside a single CI container.

## GitLab semantics to implement

Subset, in scope:

- `cache: paths: [...]` — glob patterns relative to project dir, same style as artifacts.
- `cache: key: <string>` — literal key; variable expansion of `$CI_*` vars in the key
  (e.g. `key: "$CI_COMMIT_REF_SLUG"`).
- `cache: key: files: [...]` — key derived from SHA of the listed files' contents
  (GitLab allows max 2 files; enforce that). Missing file → treat as empty content.
- `cache: key: files: + prefix:` — prefix prepended to the computed hash.
- `cache: policy:` — `pull-push` (default), `pull` (restore only), `push` (save only).
- `cache: when:` — `on_success` (default), `on_failure`, `always`.
- Default key when none given: GitLab uses `default`; do the same.
- Job-level `cache:` overrides top-level/default `cache:` wholesale (no merge),
  matching GitLab. `cache: []` or `cache: {}` disables.
- A job may declare a list of up to 4 cache entries (GitLab limit); support a list.

Out of scope (document in differences.md):

- `untracked: true`, `unprotect`, `fallback_keys` — capability WARNING, ignored.
- Cross-runner distributed cache — meaningless locally.

## Design

New module `bitrab/execution/cache.py`, modeled on `bitrab/execution/artifacts.py`.

- **Storage:** `.bitrab/cache/<sanitized-key>/` under the *project root* (not the
  worktree), so parallel worktree jobs share caches. Key sanitized for filesystem
  safety (hash the key if it contains path separators or exceeds length limits).
- **Restore (pull):** before `before_script`, copy cached paths into the job's
  working directory. Missing key → silent cache miss (log at info).
- **Save (push):** after scripts, per `policy` and `when`, copy matched paths into
  the store.

### Multi-writer safety (required, not optional)

The filesystem is shared: parallel jobs in one run, and possibly concurrent bitrab
processes (watch mode + manual run), can hit the same key.

- All saves write to `.bitrab/cache/.tmp/<key>-<pid>-<rand>/` then atomically
  rename into place. On Windows, rename onto an existing directory fails — use a
  per-key generation scheme: `<key>/<generation>/` with a `latest` pointer file
  written atomically (write temp file + `os.replace`), or replace-with-retry.
- Per-key advisory lock file `.bitrab/cache/<key>.lock` held during save and during
  restore's directory read. Implement one small cross-platform lock helper
  (`msvcrt.locking` on Windows, `fcntl.flock` on POSIX) with a timeout; on lock
  timeout, log a warning and skip the cache step rather than fail the job.
- Readers must never observe a half-written cache: only the atomic
  pointer/rename publish step makes a save visible.

### Model and plumbing

- Add `CacheConfig` dataclass in `bitrab/models/pipeline.py`; add
  `cache: list[CacheConfig]` to `JobConfig` (default empty).
- Parse in `PipelineProcessor.process_job` (`bitrab/plan.py`), honoring top-level
  `cache:` as the default and job-level override semantics above.
- Wire restore/save into `JobExecutor.execute_with_context`
  (`bitrab/execution/job.py`), skipping entirely under `--dry-run`.
- Variable expansion of `$VAR` in keys uses the job's prepared environment.

### CLI / cleanup

- `bitrab clean --what cache` and `bitrab folder status` should know about the new
  directory (see `bitrab/folder.py`).
- `bitrab run --no-cache` flag to bypass restore and save for a run.

## Acceptance criteria

- E2E test: pipeline with two runs of a job whose script writes to a cached dir;
  second run sees the file restored before `before_script`.
- Test: `key: files:` changes when a listed file's content changes; stable otherwise.
- Test: `policy: pull` never writes; `policy: push` never restores.
- Test: `when: on_failure` saves only on failure; `always` saves regardless.
- Concurrency test: two threads saving the same key concurrently produce a valid,
  complete cache (no interleaved/partial state).
- `docs/differences.md` matrix row updated from "not executed" to supported subset;
  capability checker downgraded accordingly; CHANGELOG entry.
- Works on Windows (primary dev platform) and POSIX.
