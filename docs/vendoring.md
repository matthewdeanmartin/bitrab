# Vendoring and offline mode

Remote includes normally make a pipeline depend on a live server. `bitrab vendor` turns that dependency into an
explicit, hash-locked snapshot, so the same pipeline can be validated and run later without GitLab credentials or any
network access.

## Create or refresh a snapshot

```bash
bitrab vendor
```

Bitrab follows local includes and the complete recursive `remote:` / `url:` include graph. It stores payloads under
`.bitrab/vendor/<host>/<path>` and writes their URL, SHA-256 hash, path, and fetch time to `.bitrab/vendor.lock`.
Refreshes are idempotent: unchanged entries retain their timestamp. If upstream content changes, the command prints the
old and new hashes prominently because that is a supply-chain event worth reviewing.

Commit `.bitrab/vendor.lock`. The payload directory is ignored by this repository's default rules; teams that need the
payload in source control can force-add it or adjust their ignore policy.

## Check the snapshot in CI

```bash
bitrab vendor --check
```

The check is network-free. It fails if a payload is missing or does not match its locked hash, or if the root/local/
vendored include graph refers to a remote URL absent from the lockfile.

## Run without a network

```bash
bitrab validate --offline
bitrab run --offline --no-tui
```

Offline mode never fetches a remote include. An unlocked URL is a hard error that names the URL and suggests running
`bitrab vendor`. In normal mode, a valid locked snapshot is also preferred over the network, making the lockfile the
pipeline's reproducible source of truth. Run `bitrab vendor` explicitly when you want to refresh upstream content.

`include: template`, `include: project`, and `include: component` are not vendorable yet.
