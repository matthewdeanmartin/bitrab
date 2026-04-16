# Bitrab Code Review & Roadmap

This report provides a comprehensive code review of the **Bitrab** project, identifying missing features, potential
bugs, areas for improvement, and a recommended roadmap for future development.

---

## 1. Comprehensive Code Review

### 1.1 Architectural Strengths

* **Decoupled Orchestration**: The use of `PipelineCallbacks` is excellent. It separates the core execution logic from
  the UI (TUI, CI-mode, or plain CLI). This makes it easy to add new output modes in the future.
* **Parallelism & Isolation**: The project correctly uses `multiprocessing` with the `spawn` context (especially
  critical for Windows) to run jobs in parallel. The use of `Manager().Queue()` for TUI communication is robust.
* **Lazy Imports**: `cli.py` is well-optimized for startup time, using lazy imports to keep `--help` and basic commands
  fast.
* **GitLab CI Alignment**: The project handles complex GitLab CI features like `extends` (with circular dependency
  checks), `parallel: matrix`, and DAG `needs:` remarkably well.

### 1.2 Missing GitLab CI Features

Bitrab implements a "practical subset" of GitLab CI, but several key features are still missing:

* **Git Integration**: ~~Many standard variables (e.g., `CI_COMMIT_SHA`, `CI_COMMIT_BRANCH`, `CI_COMMIT_TAG`) are not
  automatically populated.~~ **FIXED** — `VariableManager` now runs `git` commands at startup to populate all standard
  `CI_COMMIT_*`, `CI_PROJECT_*`, `CI_SERVER`, `GITLAB_CI`, `CI_JOB_ID`, and `CI_PIPELINE_ID` variables.  Values fall
  back to empty string when git is unavailable, which matches GitLab semantics (e.g. `$CI_COMMIT_TAG` is empty on
  untagged commits).
* **Advanced Includes**: Support for `include: template`, `include: project`, and `include: component` is missing. This
  limits the ability to use standard GitLab templates.
* **Cache Persistence**: While `cache` is mentioned, there is no logic to persist and restore cached files between
  different runs of the same job or different pipelines.
* **Downstream Pipelines**: The `trigger` keyword for triggering other pipelines is not supported.
* **Secret Management**: ~~There is no dedicated support for masked variables or loading secrets from external sources ( `.env` files).~~  **FIXED (local .env loading + dotenv reports)** — `VariableManager` now loads `.env` and `.bitrab.env` from the project root (simulating GitLab CI/CD Settings > Variables).  `artifacts: reports: dotenv:` is now parsed and collected; downstream jobs receive those variables automatically.  Masked variables (server-side redaction in GitLab UI logs) remain out of scope for a local runner.
* **Environment & Deployment**: The `environment` and `deployment` keywords are largely ignored.
* **Rules `changes`**: The `rules` engine only supports `if` and `exists`. It lacks the ability to trigger based on file
  changes (`changes`).

### 1.3 Potential Bugs & Edge Cases

* **Shell Pipe Blocking**: In `shell.py`, `proc.stdin.write(robust_script_content)` is called after starting the
  stdout/stderr threads but before closing stdin. If a script is exceptionally large, it might fill the pipe buffer and
  block before the process can consume it.
* **DAG `needs: []` Behavior**: In GitLab CI, `needs: []` means "run immediately, ignoring stages." Bitrab currently
  treats empty `needs` the same as no `needs`, which falls back to stage-based dependencies.
* **Windows Path Normalization**: While there are many `os.name == "nt"` checks, some globbing and path normalization in
  `mutation.py` and `rules.py` may have subtle issues with casing or drive letters on Windows.
* **Variable Expansion in Rules**: Rules are evaluated once at the start. If a job defines variables that should be used
  in its own rules, or if rules should depend on output from previous stages (via artifacts), it might not behave
  exactly like GitLab CI.

### 1.4 Ergonomics & UX

* **No `init` Command**: There's no interactive way to scaffold a `.bitrab-ci.yml` or `.bitrab-mutation.yml`.
* **Large Pipeline "Noise"**: Running `bitrab run` on a large pipeline can be overwhelming. Better interactive job
  selection or fuzzy matching would help.
* **Local Overrides**: Users currently have to modify the CI YAML to change variables for a local run. A local override
  file (e.g., `.bitrab.env`) would be cleaner.

---

## 2. Recommended Roadmap

### Phase 1: Foundations & Git Integration (Immediate)

Focus on making Bitrab feel more "automatic" in a standard development environment.

* **Implement Git Detection (EASY)**: ~~Automatically detect if Bitrab is running in a Git repo and populate `CI_COMMIT_*`
  variables using `git` commands.~~ **DONE.**
* **Local Variable Overrides (EASY)**: Support loading variables from a `.bitrab.env` file or similar local-only
  configuration.
* **Fix `needs: []` in DAG (MEDIUM)**: Update the DAG builder to correctly handle empty `needs` by making the job depend
  on no other jobs.
* **Enhanced YAML Errors (MEDIUM)**: Improve `validate` output to include line and column numbers for schema violations.

### Phase 2: Core GitLab Compatibility (Near-term)

Fill the most significant gaps in the GitLab CI feature set.

* **Cache Implementation (MEDIUM)**: Implement local zip/unzip logic for `cache: paths`. Store caches in
  `.bitrab/cache/<job_name>/`.
* **Template Includes (MEDIUM)**: Support `include: template` by fetching standard templates from GitLab's official
  repository (similar to how the schema is fetched).
* **Rules `changes` Support (HARD)**: Implement `rules: changes` by comparing the current state against a Git
  reference (e.g., `HEAD` or a base branch).
* **Masked Variable Support (EASY)**: Allow marking variables as sensitive so they are masked in the TUI and logs.

### Phase 3: Performance & Scalability (Mid-term)

Optimize for large projects and complex workflows.

* **Optimized Mutation Detection (MEDIUM)**: Instead of a full tree walk before/after every job, use a more efficient
  file system watcher or only snapshot relevant directories.
* **Resource Groups (MEDIUM)**: Implement `resource_group` to limit concurrency for jobs that share a limited resource (
  e.g., a database or hardware).
* **Interruptible Jobs (EASY)**: Support the `interruptible` keyword to cancel older runs when a new pipeline is
  started (relevant for `watch` mode).
* **Advanced Includes (HARD)**: Support `include: project` and `include: component`, which requires authenticating with
  GitLab APIs.

### Phase 4: TUI & UX Polishing (Long-term)

Make Bitrab a "best-in-class" local CI experience.

* **Interactive TUI DAG (HARD)**: Add a visual DAG representation to the TUI to show job dependencies and progress.
* **Interactive Job Picker (MEDIUM)**: Add a CLI subcommand or TUI mode to interactively select which jobs/stages to
  run.
* **Bitrab `init` (EASY)**: Add an interactive scaffolding command to help users set up Bitrab for their projects.
* **Resource Usage Tracking (MEDIUM)**: Show CPU and memory usage per job in the TUI.

---

## 3. Conclusion

Bitrab is a well-engineered tool that solves a real pain point in the GitLab CI ecosystem. Its architecture is clean and
extensible. By focusing on Git integration, caching, and a few more GitLab-native keywords, it can significantly bridge
the gap between local development and CI execution.
