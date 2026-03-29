# Matrix Feature — Deferred Tasks for Sonnet

Tasks identified during matrix/parallel keyword implementation that can be
handled independently.

---

## 1. `needs: parallel: matrix` support

GitLab CI allows `needs` entries to reference specific matrix combinations:

```yaml
deploy:
  needs:
    - job: test
      parallel:
        matrix:
          - DB: postgres
```

Currently bitrab resolves `needs: [test]` to all expanded matrix instances.
The refined syntax (`needs: parallel: matrix:`) that targets a specific subset
is not yet implemented.

**Files**: `plan.py` (`_resolve_expanded_needs`), `models/pipeline.py` (RuleConfig.needs)

---

## 2. Graph rendering for matrix-expanded jobs

`graph.py` renders the pipeline DAG.  Matrix-expanded jobs will appear as
individual nodes (e.g. `test: [DB=pg]`, `test: [DB=mysql]`).  Consider
grouping them visually (e.g. a cluster/subgraph in DOT output) so the graph
stays readable for large matrices.

**Files**: `graph.py`

---

## 3. TUI tab management for large matrix expansions

The Textual TUI creates one tab per job.  A `parallel: 50` or a large matrix
could create an unwieldy number of tabs.  Consider:
- Collapsible groups for matrix jobs
- A summary view that only expands on click

**Files**: `tui/app.py`

---

## 4. Capability diagnostic for `parallel:` keyword

Add a check in `config/capabilities.py` to emit an informational diagnostic
when `parallel:` is used, noting that bitrab expands the jobs locally but
does not replicate GitLab's runner-level parallelism (e.g. no separate
machines).

**Files**: `config/capabilities.py`

---

## 5. Schema validation for `parallel:` in bitrab's own validation

The GitLab CI JSON schema already includes `parallel:` definitions, but
bitrab's semantic validation (`cmd_validate` in `cli.py`) doesn't check for
invalid matrix entries (e.g. non-dict items in the matrix array, empty
variable names).  Add semantic checks.

**Files**: `cli.py` (`cmd_validate`), possibly `config/validate_pipeline.py`

---

## 6. Thread backend: output routing in TUI/CI modes

The thread backend (`ThreadPoolExecutor`) shares memory with the main thread,
so the `QueueWriter` and file-based output paths used in TUI/CI mode should
work, but haven't been integration-tested for race conditions.  The
`_run_single_job_queued` worker relies on `os.getpid()` for worker PID
tracking which would return the same PID for all threads — needs a
thread-aware alternative (e.g. `threading.get_ident()`).

**Files**: `tui/orchestrator.py` (`_run_single_job_queued`), `stage_runner.py`

---

## 7. `parallel:` keyword in `cmd_list` output

The `list` command shows jobs but doesn't indicate when a job uses `parallel:`
or how many instances it would expand to.  Show this in the listing.

**Files**: `cli.py` (`cmd_list`)

---

## 8. Run-log metadata for matrix jobs

The run log (`_persist_run_log`) records `job_count` but doesn't capture
matrix metadata (which variable combinations ran, which instance failed).
Enrich the log with matrix context.

**Files**: `plan.py` (`_persist_run_log`), `folder.py`

---

## 9. `--parallel-backend` CLI flag

Currently the backend is only configurable via `pyproject.toml`.  Add a
`--parallel-backend thread|process` flag to the `run` and `watch` commands
for quick overriding without editing config.

**Files**: `cli.py` (argparse setup), `plan.py` (`run_pipeline`)
