# Sprint 04 — `rules: changes`, `--changed`, and the pre-push hook

**Status:** implemented (2026-07-06)
**Delegable:** the `changes:` evaluator and hook installer are bot-friendly;
baseline-selection semantics (below) need human sign-off first.

## Goal

Close the biggest parity gap (`rules: changes` is "not implemented locally") and
turn it into the pre-push feature: run only the jobs your edit affects, before the
code ever leaves the machine.

## `rules: changes` evaluation

Extend `RuleConfig` (`bitrab/models/pipeline.py`) and the evaluator
(`bitrab/config/rules.py`) with `changes: list[str]` (support both the bare list
form and `changes: paths: [...]`; `compare_to:` accepted and honored as an explicit
baseline override).

Matching: glob patterns per GitLab semantics (`*` does not cross `/`, `**` does),
matched against the changed-file list, paths relative to project root.

### Baseline selection (the honest-divergence part)

GitLab compares against different refs depending on pipeline type (push event vs
MR). Locally there is no event, so bitrab defines its own explicit ladder,
documented in `differences.md`:

1. `--changes-base <ref>` CLI flag / `[tool.bitrab] changes_base` — explicit wins.
2. Default: `git merge-base HEAD <default-branch>` where default branch is detected
   from `origin/HEAD`, falling back to `origin/main`, `origin/master`, local `main`.
3. Changed files = committed diff vs baseline **plus** uncommitted changes
   (staged + unstaged) **plus** untracked non-ignored files. Pre-push wants to see
   what you are about to push *and* what you forgot to commit.
4. Not a git repo / no baseline resolvable → `changes:` rules evaluate the way
   GitLab treats un-evaluable changes in some contexts: **match = true** (run the
   job), with a warning. Safer to over-run than to silently skip.

## `--changed` job selection

`bitrab run --changed`: independent of `rules:`, filter the run to jobs whose
*fingerprint input patterns* (sprint 02) or `changes:` patterns intersect the
changed-file set — plus their transitive `needs:` dependents. Jobs with no
declared patterns are included (unknown inputs → must run). Combines with
`--incremental` for the fast path.

## `bitrab install-hook`

- Writes `.git/hooks/pre-push` (or chains if one exists — detect and append a
  clearly-marked block; refuse with instructions if the existing hook is not a
  shell script). Remember: hook files need the executable bit in the git index
  story only for committed hook dirs; `.git/hooks` just needs `chmod +x` on POSIX.
- Hook body: `bitrab run --changed --incremental --no-tui` with a clear
  skip-instruction comment (`git push --no-verify` and `BITRAB_SKIP_HOOK=1`).
- `bitrab install-hook --uninstall` removes exactly the marked block/file.
- Windows: git runs hooks under its own sh; plain shell script works — test it.

## Acceptance criteria

- Unit: glob semantics table-tested against GitLab-documented examples
  (`*.md`, `docs/**/*`, trailing-slash dirs).
- E2E in a fixture repo: edit a file, only the matching job (plus dependents) runs.
- Uncommitted and untracked files trigger `changes:` matches.
- No-repo fallback runs the job and warns.
- Hook installs, fires on push (simulate by invoking the hook script), uninstalls
  cleanly, refuses to clobber a foreign hook.
- `differences.md` gets a "Baseline selection" subsection; matrix row for
  `rules: changes` flips to supported-with-documented-divergence; CHANGELOG entry.
