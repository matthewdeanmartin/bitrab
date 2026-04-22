# Configuration system

Bitrab accepts GitLab CI YAML and then layers its own local capability model on top.

## Source formats

The runtime config is YAML, normally:

- `.gitlab-ci.yml`
- `.bitrab-ci.yml`

The config path rule is centralized in `bitrab.cli.resolve_config_path()`, so `run`, `validate`, `list`, `graph`, `debug`, and `watch` all choose the same file.

## Loader responsibilities

`config.loader.ConfigurationLoader` is responsible for:

- YAML parsing with `ruamel.yaml`
- include resolution
- recursive include deduplication
- raw dict merging

It is deliberately not responsible for semantic job interpretation.

## Validation layers

`bitrab validate` combines three distinct checks:

1. **GitLab schema validation** via `config.validate_pipeline.GitLabCIValidator`
2. **Capability diagnostics** via `config.capabilities.check_capabilities()`
3. **Basic semantic checks** in `cli.cmd_validate()`

That layering explains a lot of code review questions:

- something can be valid GitLab YAML but still unsupported locally
- something can be warned-and-skipped instead of rejected
- some unsupported constructs fail in the loader before validation gets very far

## Capability model

`config.capabilities.py` defines a two-level model:

- `DiagnosticLevel.ERROR`
- `DiagnosticLevel.WARNING`

Practical meaning:

- **ERROR** means bitrab cannot safely emulate the feature locally and should stop
- **WARNING** means the feature is ignored or only partially meaningful locally, but keeping one shared `.gitlab-ci.yml` is still useful

Examples:

| Feature | Current behavior |
| --- | --- |
| `include: component` | error |
| `trigger:` | error |
| `inputs:` | error |
| `image:` / `services:` | warning, ignored |
| `workflow:` | warning, ignored locally |
| `resource_group:` | warning |
| `environment:` | warning |
| `rules: changes` | warning |

## Defaults and overrides

`PipelineProcessor._process_default_config()` normalizes the top-level `default:` block into `DefaultConfig`.

Then `_process_job()` applies precedence roughly like this:

```text
global variables
  -> default variables
    -> job variables
```

For scripts:

- job `before_script` overrides default `before_script`
- job `after_script` overrides default `after_script`

This is intentionally closer to GitLab's inheritance model than simple concatenation.

## `extends:`

`PipelineProcessor._resolve_extends()` resolves inheritance before job dataclasses are created.

Behavior:

- accepts a string or list of parents
- hidden jobs like `.base` are valid templates
- parent dictionaries deep-merge
- lists and scalars are replaced, not merged
- circular references raise `GitlabRunnerError`

## Includes

Loader include behavior:

- string include -> local file path
- `local:` -> local file path
- `remote:` / `url:` -> HTTP fetch
- `template:` / `project:` -> skipped
- `component:` -> hard error

The merge policy is recursive dict merge in `_merge_configs()`, with the current file overriding included values.

## Rule expressions

`config.rules._evaluate_if()` supports a practical subset:

- `$VAR`
- `$VAR == "value"`
- `$VAR != "value"`
- `$VAR =~ /regex/`
- `$VAR !~ /regex/`
- top-level `&&` and `||`

`rules: exists` is implemented with filesystem glob checks. `rules: changes` is not implemented locally.

## Configuration outside YAML

Bitrab also reads `pyproject.toml` for local execution behavior in `mutation.py`:

- `parallel_backend`
- `use_git_worktrees`
- `worktree_root`
- `serial`
- `warn_on_mutation`
- mutation whitelist patterns

That means runtime behavior is split between **pipeline config** and **local project config**.
