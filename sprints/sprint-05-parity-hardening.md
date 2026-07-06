# Sprint 05 — Parity and hardening grab-bag

**Status:** implemented (2026-07-06)
**Delegable:** yes — each item is independent and well-bounded; good candidates for
parallel subagents since they touch disjoint files.

Four independent items; ship in any order.

## 1. `!reference` tag support

The single most common load-failure for real-world configs. Ruamel custom
constructor in `ConfigurationLoader` (`bitrab/config/loader.py`) producing a
placeholder node; resolution pass **after** includes merge and **before** extends
resolution (GitLab resolves references against the merged config; references may
nest — resolve iteratively with a depth limit and a clear circular-reference error).
`!reference [.tmpl, script]` splices a list; `!reference [.tmpl, variables, KEY]`
yields a scalar. Table-test against GitLab's documented examples.

## 2. `workflow: rules`

Pipeline-level gate reusing the existing rule evaluator (`bitrab/config/rules.py`).
Evaluated once before planning: `when: never` match → pipeline does not run (clear
message, exit 0 for `validate`, distinct exit for `run`); `workflow: rules:
variables:` merge into pipeline variables. Matrix row flips from "Ignored".

## 3. `resource_group:` as a local mutex

Currently ignored — but under `--parallel` it is exactly where users get bitten.
Implement as a named lock via the shared lock helper (sprint 01):
`.bitrab/locks/<resource_group>.lock`. Jobs in the same group serialize; different
groups and ungrouped jobs are unaffected. Works across concurrent bitrab processes
for free because the lock is a file. Timeout = job timeout; log while waiting.

## 4. Remote-include hygiene

`ConfigurationLoader._fetch_remote_yaml` (`bitrab/config/loader.py`) gets:

- retry with backoff (urllib3 `Retry`, 3 attempts, connect/read errors + 5xx);
- response size cap (e.g. 5 MB) and a sanity check that the body parses as YAML
  mapping (already enforced downstream — keep the error message pointing at the URL);
- a TTL'd on-disk cache under `.bitrab/include-cache/` keyed by URL hash
  (default TTL ~10 min; `--no-include-cache` to bypass), using the same
  temp-write + `os.replace` + lock discipline as the other stores — watch mode
  refetching every remote include on every save is today's silent cost;
- distinct from sprint 03's vendor store: the cache is a transparent freshness
  optimization, the vendor store is an explicit, locked, provenance-tracked snapshot.

## Acceptance criteria

Per item: unit tests + one E2E pipeline exercising the feature via
`bitrab run --no-tui` or `validate`; `docs/differences.md` matrix and capability
diagnostics updated where a row changes; CHANGELOG entries.
