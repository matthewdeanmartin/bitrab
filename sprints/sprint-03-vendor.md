# Sprint 03 — `bitrab vendor` and `--offline`

**Status:** delegated to subagent (2026-07-06)
**Delegable:** yes, once the lockfile format below is agreed — mechanical after that.

## Goal

One command that makes a pipeline self-contained: fetch every remote include,
snapshot it locally with provenance, and let the loader resolve from the snapshot.
This is the "eject button" from GitLab — a vendored pipeline runs with no network
and no GitLab account, forever.

## Commands

- `bitrab vendor` — resolve the include graph, download every `remote:`/`url:`
  include (recursively — vendored files may themselves include), store under
  `.bitrab/vendor/`, write/update the lockfile. Idempotent; re-running refreshes
  content and reports changed hashes loudly (a changed upstream include is a
  supply-chain event the user should see).
- `bitrab vendor --check` — verify every vendored file still matches its lockfile
  hash and that no include in the config is un-vendored. Exit non-zero on drift.
  (CI-friendly.)
- `bitrab vendor --rewrite` — optional, destructive-ish: rewrite the YAML to replace
  `remote:` includes with `local:` paths into a committed `vendor/ci/` directory.
  For users who want the vendored files in git and zero bitrab-specific resolution.
- `bitrab run --offline` / `bitrab validate --offline` — loader resolves remote
  includes **only** from the vendor store; any un-vendored remote include is a hard
  error naming the URL and suggesting `bitrab vendor`.

## Lockfile

`.bitrab/vendor.lock` (committed to git; the vendor payload dir may or may not be):

```toml
schema = 1

[[include]]
url = "https://example.com/ci/python.yml"
sha256 = "…"
file = "vendor/example.com/ci/python.yml"   # relative to .bitrab/
fetched_at = "2026-07-06T00:00:00Z"
```

- File layout mirrors host/path of the URL for human readability.
- **Multi-writer safety:** same temp-write + `os.replace` + lock-helper discipline
  as sprints 01/02 (vendor is less contended, but watch mode + manual vendor can race).

## Loader integration

`ConfigurationLoader._process_includes` / `_fetch_remote_yaml`
(`bitrab/config/loader.py`):

- Default mode: network fetch as today, but if the URL exists in the lockfile,
  prefer the vendored copy and log when upstream would have differed (cheap: only
  compare when `--offline` is off and a `--verify-vendor` flag asks for it — do not
  add a network call to every load).
- `--offline`: vendored copy or hard error. No sockets opened, period (also skips
  any other network path — audit for stragglers).

## Later extensions (not this sprint, note in code)

- `include: template` vendoring by fetching from the public
  `gitlab-org/gitlab` raw URLs.
- `include: project` vendoring via authenticated GitLab raw-file API (`GITLAB_TOKEN`).
  Both then flow through the same lockfile — vendoring is the *only* planned path to
  supporting these include types, which keeps the support matrix honest.

## Acceptance criteria

- E2E: config with a remote include (serve via `http.server` fixture on localhost);
  `bitrab vendor` then `bitrab run --offline --no-tui` succeeds with network access
  monkeypatched to raise.
- Tampered vendored file → `vendor --check` exits non-zero and names the file.
- Un-vendored remote include under `--offline` → clear error with the URL.
- Recursive remote includes (remote including remote) fully vendored in one pass.
- Docs page "Vendoring and offline mode" framed around GitLab independence;
  CHANGELOG entry; `differences.md` note that `--offline` changes include semantics.
