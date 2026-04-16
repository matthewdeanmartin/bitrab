# Developer documentation

This section explains how bitrab is built: how the CLI loads a pipeline, how raw GitLab-style YAML becomes a normalized execution model, how jobs are scheduled, and how run state is persisted under `.bitrab/`.

Start here if you are reviewing code, changing runtime behavior, or adding a new CLI surface.

## Suggested reading order

1. [Architecture](architecture.md) for the top-level execution flow.
2. [Getting started](getting-started.md) for the contributor workflow.
3. [Codebase structure](codebase.md) to find the relevant modules quickly.
4. [Core concepts](core-concepts.md) and [pipeline execution model](execution-model.md) before changing scheduling or semantics.
5. [Configuration system](configuration.md) and [execution engine](execution-engine.md) before touching parsing, jobs, artifacts, variables, or output.

## What these docs optimize for

- **Concrete source references.** File, class, and function names are called out directly.
- **Current behavior, not aspirational behavior.** Where bitrab intentionally differs from GitLab, the docs say so.
- **Reviewability.** The goal is to make it easy to answer "where does this behavior live?" during code review.

## Architecture at a glance

```text
CLI command
  bitrab.cli
      |
      v
resolve config path
  resolve_config_path()
      |
      v
load raw YAML + includes
  config.loader.ConfigurationLoader
      |
      v
normalize raw config into dataclasses
  plan.PipelineProcessor -> PipelineConfig / JobConfig
      |
      v
evaluate rules + build variables
  config.rules.evaluate_rules()
  execution.variables.VariableManager
      |
      v
pick execution mode
  execution.scheduler.StageOrchestrator
  tui.orchestrator.TUIOrchestrator
      |
      v
run jobs
  execution.stage_runner.StagePipelineRunner
  execution.stage_runner.DagPipelineRunner
      |
      v
execute shell scripts
  execution.job.JobExecutor
  execution.shell.run_bash()
      |
      v
persist artifacts + run logs
  execution.artifacts
  execution.events.EventCollector
  folder.write_run_log()
```

The docs use plain-text diagrams like this on purpose so they render with the existing MkDocs configuration.
