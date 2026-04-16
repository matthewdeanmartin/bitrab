# CLI internals, compatibility, and contribution guidance

## CLI internals

`bitrab/cli.py` owns the public command surface.

Important commands and their handlers:

| Command | Handler |
| --- | --- |
| `run` | `cmd_run()` |
| `list` | `cmd_list()` |
| `validate` | `cmd_validate()` |
| `watch` | `cmd_watch()` |
| `graph` | `cmd_graph()` |
| `debug` | `cmd_debug()` |
| `clean` | `cmd_clean()` |
| `logs` | `cmd_logs()` |
| `folder` | `cmd_folder()` |

Notable implementation details:

- imports are lazy so help output stays cheap
- `main()` defaults to `run` when no subcommand is given
- `resolve_config_path()` is centralized so commands cannot accidentally operate on different config files

## Adding a new command

The normal path is:

1. add a `cmd_*` function in `cli.py`
2. register a subparser in `create_parser()`
3. add or update tests in `test/test_cli.py`
4. document the command in user docs if it is externally visible

If the command needs pipeline execution behavior, prefer reusing `LocalGitLabRunner`, `PipelineProcessor`, `StagePipelineRunner`, or `EventCollector` instead of creating a new execution path.

## Versioning and compatibility

The project follows semantic versioning in `CHANGELOG.md`, but the runtime compatibility contract is intentionally narrower than "full GitLab Runner compatibility".

A useful way to think about it:

- GitLab schema compatibility is broad
- local execution compatibility is selective and explicit
- unsupported features should warn or fail clearly rather than act silently

That design direction also shows up in:

- `docs/differences.md`
- `config/capabilities.py`
- loader hard-fail behavior for some include and trigger features

## Contribution guidance

When changing behavior, identify which layer you are touching:

| If you are changing... | Start here |
| --- | --- |
| config file shape or interpretation | `config/loader.py`, `plan.py`, `models/pipeline.py` |
| scheduling or failure semantics | `execution/stage_runner.py`, `execution/job.py` |
| environment variables | `execution/variables.py` |
| artifacts or dotenv passing | `execution/artifacts.py` |
| output/UI behavior | `execution/scheduler.py`, `tui/orchestrator.py`, `tui/app.py`, `execution/events.py` |
| persisted run state | `folder.py` |

Good habits for safe changes:

1. update the smallest layer that owns the behavior
2. preserve the split between normalization and execution when possible
3. add or update tests near the affected behavior
4. document user-visible changes in `CHANGELOG.md`

## Common pitfalls

- forgetting that `plan.py` currently owns both planning and top-level running
- changing rule behavior without considering pre-execution evaluation timing
- assuming `CI_JOB_DIR` is the process working directory
- assuming parallel jobs have isolation
- adding presentation-specific logic directly into core scheduling code

## Current roadmap and limitations

For the longer product direction, see `spec/roadmap.md`. The recurring themes are:

- honesty about partial GitLab compatibility
- predictable local behavior over accidental similarity
- separation of execution from presentation
- host-native execution rather than container emulation

That roadmap is best used as design context, not as an exact implementation checklist.
