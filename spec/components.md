# Components, Inputs, and Config Preprocessing

## Purpose

Bitrab currently rejects `include:component` and `inputs:` because pretending to support them would produce misleading
local behavior. That was the right first boundary. The next step is to support the useful subset intentionally.

This spec lays out a phased implementation plan for GitLab CI/CD components, input definitions, input values, and the
config preprocessing layer needed to make them work together.

The important idea: components, inputs, interpolation, and multi-document YAML are not separate features in practice.
They form one config compilation pipeline:

1. Load YAML documents.
2. Read `spec:` metadata from the header document.
3. Resolve includes and component references.
4. Bind `include:inputs` values to `spec:inputs` definitions.
5. Interpolate `$[[ inputs.NAME ]]` expressions.
6. Merge the resulting YAML into the executable pipeline config.
7. Hand the normalized config to Bitrab's existing planner.

We should implement that pipeline in layers, with clear support boundaries at each phase.

## Product Stance

Bitrab should not try to become GitLab's component registry or a perfect remote runner. The goal is:

- support local development against configs that use components
- make reusable local CI building blocks work
- support deterministic component expansion before execution
- fail clearly for component features that require GitLab server context

The local contract should be:

- local components are first-class
- remote component fetching is opt-in and cacheable
- input resolution is deterministic
- unsupported interpolation or metadata fails before jobs run
- final execution still uses Bitrab's existing local runner semantics

## Current State

Known current behavior:

- `include:component` is rejected as an error.
- top-level and job-level `inputs:` are rejected as errors.
- `variables:` are supported and flow into job environments.
- local and remote includes exist, but there is no component-aware compilation phase.
- YAML loading assumes one executable document, not a header/body split.
- there is no `$[[ ... ]]` interpolation preprocessor.

This means Bitrab cannot currently run pipelines that use modern GitLab components even when all files are available
locally.

## Target Concepts

### `spec:inputs`

Component files can declare input definitions in a YAML header document:

```yaml
spec:
  inputs:
    stage:
      default: test
      description: Stage to run the job in.
    python-version:
      default: "3.12"
---
component-test:
  stage: $[[ inputs.stage ]]
  script:
    - python --version
```

Bitrab should parse the `spec:` header as metadata, not as executable pipeline config.

### `include:inputs`

Callers pass input values when including a component or component-like local file:

```yaml
include:
  - local: ci/python-test.yml
    inputs:
      stage: test
      python-version: "3.12"
```

Values are bound to the included file's `spec:inputs` before that file is interpolated and merged.

### `$[[ inputs.NAME ]]`

The interpolation layer replaces declared input references before normal pipeline processing. The first implementation
should support only `inputs.*` expressions, not a general expression language.

### `include:component`

Component includes identify reusable GitLab component packages:

```yaml
include:
  - component: $CI_SERVER_FQDN/group/project/component-name@1.0.0
    inputs:
      stage: test
```

Bitrab needs a resolver abstraction so local file resolution, cached remote resolution, and future registry-aware
resolution do not leak into the planner.

## Architecture Direction

Add a config compilation layer between `ConfigurationLoader` and `PipelineProcessor`.

Proposed flow:

```text
raw YAML source
  -> YAML document loader
  -> include/component resolver
  -> spec header parser
  -> input binder
  -> interpolation preprocessor
  -> GitLab-style config merger
  -> capability diagnostics
  -> PipelineProcessor
```

This layer should produce a normal raw config dict shaped like today's `ConfigurationLoader.load_config()` output. That
keeps execution changes small and lets the component work land without rewriting the runner.

## Phase 0: Define Boundaries and Fixtures

Goal: make the support target precise before changing loader behavior.

Deliverables:

- document supported component/input syntax in `docs/differences.md`
- collect fixture YAML files under `test/fixtures/components/`
- add failing tests marked against the future behavior
- decide exact local path conventions for component fixtures

Acceptance criteria:

- examples cover `spec:inputs`, `include:inputs`, `$[[ inputs.NAME ]]`, and multi-document YAML
- unsupported features are listed explicitly
- existing rejection behavior remains unchanged until Phase 1 starts

Non-goals:

- remote component fetching
- GitLab registry authentication
- full interpolation expression support

## Phase 1: Multi-Document YAML and `spec:` Header Parsing

Goal: teach the loader to understand "YAML header plus pipeline body" without changing execution.

Deliverables:

- replace single-document loading where needed with a helper that can read all YAML documents
- add a `ConfigDocument` or similar internal object with:
  - `spec`
  - `body`
  - `source`
- parse `spec:inputs` definitions into typed metadata
- strip `spec:` header documents from executable config before merging

Acceptance criteria:

- a file with `spec: ... --- job: ...` loads as only the job body for current execution
- a file without `---` behaves exactly as it does today
- malformed multi-document files fail with a clear config error
- tests cover local includes that contain a `spec:` header

Implementation notes:

- keep the public return type from `load_config()` as `dict[str, Any]` for now
- put the new document helper behind `ConfigurationLoader` instead of adding ad hoc YAML reads elsewhere
- do not yet interpolate values in this phase

## Phase 2: Input Model and Binding

Goal: represent input definitions and resolve concrete input values deterministically.

Deliverables:

- add an internal `InputDefinition` model:
  - name
  - type
  - default
  - description
  - options
  - required
- add an `InputBinding` or plain resolved mapping for a single include site
- support `include:inputs` for local includes
- validate:
  - unknown input names
  - missing required inputs
  - invalid option values
  - unsupported input types

Acceptance criteria:

- included files can define inputs with defaults
- callers can override defaults via `include:inputs`
- missing required inputs fail before planning
- input values are available to the interpolation phase, but not automatically injected as environment variables

Initial type policy:

- support string-like scalar values first
- accept booleans and numbers only by converting to strings for interpolation
- warn or fail on arrays/maps until a real use case needs them

## Phase 3: `$[[ inputs.* ]]` Interpolation Preprocessor

Goal: apply input values to included component bodies before normal config merging.

Deliverables:

- add a small interpolation module, for example `bitrab/config/interpolate.py`
- recursively walk YAML structures:
  - strings
  - lists
  - dictionaries
- replace `$[[ inputs.NAME ]]` with the bound input value
- support whole-value replacement and embedded string replacement
- fail clearly for unknown input references
- fail clearly for unsupported `$[[ ... ]]` expressions

Acceptance criteria:

- `stage: $[[ inputs.stage ]]` becomes a scalar stage value
- `script: ["echo $[[ inputs.message ]]"]` becomes an interpolated string
- nested dictionaries and lists are handled
- interpolation happens before `PipelineProcessor.process_config()`
- existing shell `$VAR` expansion behavior is unchanged

Design rule:

- this is not shell substitution
- this is not Jinja
- this is only GitLab config interpolation for supported `$[[ inputs.* ]]` expressions

## Phase 4: Local Component Includes

Goal: make component-like reuse work locally before remote registry support.

Deliverables:

- add a resolver abstraction:
  - `LocalIncludeResolver`
  - future `ComponentResolver`
- support `include:local` with `inputs:` end to end
- optionally support a Bitrab-local component shorthand only if it does not conflict with GitLab syntax
- preserve current local include merge behavior after preprocessing

Acceptance criteria:

- a root pipeline can include a local component file with inputs
- the included file's `spec:` header is consumed
- the included body is interpolated
- the result merges with the root config
- jobs from the included component execute normally

Testing focus:

- defaults
- caller overrides
- nested local includes
- multiple includes of the same component with different input values
- collision behavior when included components produce the same job name

Collision policy:

- initially keep existing merge semantics
- document that later includes or root config can override earlier keys
- consider adding diagnostics for duplicate job names in a later phase

## Phase 5: Pipeline-Level Inputs and CLI Prompting

Goal: support user-provided pipeline inputs as a sibling to component inputs, using the same input model.

Deliverables:

- allow root pipeline `spec:inputs`
- add CLI `--input KEY=VALUE`
- add optional interactive prompting for missing required root inputs
- add non-interactive failure behavior for CI or `--no-tui`
- feed resolved root inputs into the same interpolation preprocessor

Acceptance criteria:

- root pipeline inputs can influence `rules`, job fields, and scripts through interpolation
- non-interactive runs fail on missing required inputs
- interactive local runs can prompt for missing required inputs
- `--input` values override defaults
- resolved inputs are visible in debug output with masking support for future secret-like values

Important distinction:

- inputs are compile-time config values
- variables are runtime environment values
- an input only becomes an env var if the YAML explicitly writes it into `variables:`

## Phase 6: `include:component` Resolver, Cache, and Offline Mode

Goal: support real component includes without forcing network access into every run.

Deliverables:

- parse component references into:
  - host
  - project path
  - component name
  - version/ref
- add a component cache under `.bitrab/components/`
- support resolving component refs from cache
- add explicit network opt-in for fetching uncached components
- support an offline mode that fails if a component is not cached

Acceptance criteria:

- cached component includes work without network access
- uncached component includes fail with an actionable message unless fetch is enabled
- fetched components are stored deterministically
- include inputs bind to fetched component `spec:inputs`
- cache layout is documented

Open design questions:

- exact URL mapping from GitLab component reference to downloadable YAML
- authentication strategy for private projects
- whether to use GitLab's API, raw file URLs, or a user-provided component mirror

Recommendation:

- start cache-first
- add network fetch only after local and cached behavior is solid

## Phase 7: Capability Diagnostics and Debuggability

Goal: make the compiled config understandable.

Deliverables:

- add `bitrab debug-config` or extend existing debug output
- show:
  - include graph
  - component sources
  - input defaults and overrides
  - interpolation diagnostics
  - final compiled config path or JSON output
- update capability validation to distinguish:
  - supported local components
  - supported cached remote components
  - unsupported component features

Acceptance criteria:

- users can see why an input value was chosen
- users can inspect the final config that Bitrab will run
- capability errors point to source include/component context
- JSON validation output can include component/input diagnostics

## Phase 8: Hardening and Broader Compatibility

Goal: fill compatibility gaps after the core path is stable.

Possible deliverables:

- richer input type support
- array and map interpolation if GitLab-compatible behavior is needed
- duplicate job diagnostics
- component version update command
- lockfile for resolved component refs
- `include:rules` interaction with component includes
- better source maps from compiled config back to component files

Acceptance criteria:

- new compatibility is test-driven from real component examples
- unsupported advanced features continue to fail clearly
- docs explain differences from GitLab behavior

## Suggested Implementation Order

1. Multi-document YAML loader and `spec:` header parsing.
2. Input definition parsing and binding for local includes.
3. `$[[ inputs.* ]]` interpolation.
4. End-to-end local include with inputs.
5. Root pipeline inputs and CLI `--input`.
6. Interactive prompting for missing root inputs.
7. Component resolver abstraction with cache-only support.
8. Optional remote fetch support.
9. Debug compiled config output.
10. Compatibility hardening from real-world examples.

## Test Strategy

Add focused tests by layer:

- YAML document loading tests
- input definition parsing tests
- input binding validation tests
- interpolation walker tests
- local include compilation tests
- root input CLI tests
- component cache resolver tests
- full pipeline execution smoke tests

The most important end-to-end fixture should look like:

```yaml
include:
  - local: components/python-test.yml
    inputs:
      job-name: test-python
      stage: test
      python-version: "3.12"
```

and compile into an ordinary Bitrab pipeline with no remaining `spec:`, `inputs:`, or `$[[ ... ]]` expressions.

## Documentation Updates

Update these docs as phases land:

- `docs/differences.md`
- `docs/developer/configuration.md`
- `docs/developer/core-concepts.md`
- README support matrix if present

Docs should keep a clear table:

| Feature | Phase | Status |
| --- | --- | --- |
| multi-document `spec:` header | 1 | planned |
| local `include:inputs` | 2-4 | planned |
| `$[[ inputs.* ]]` interpolation | 3 | planned |
| root pipeline inputs | 5 | planned |
| interactive input prompting | 5 | planned |
| cached `include:component` | 6 | planned |
| remote component fetch | 6+ | planned, opt-in |

## Risks

- silently compiling a different config than GitLab would compile
- mixing compile-time inputs with runtime variables
- building a too-general templating engine
- adding network fetch before cache and debug behavior are reliable
- losing source context in error messages

Mitigations:

- keep interpolation intentionally narrow
- expose compiled config for debugging
- preserve clear capability diagnostics
- add source metadata internally even if execution still consumes plain dicts
- make remote fetching opt-in

## Definition of Done

This feature area is working when Bitrab can:

- load a root pipeline that includes local component files
- parse each component's `spec:inputs`
- bind caller-provided `include:inputs`
- interpolate `$[[ inputs.* ]]`
- produce a normal pipeline config
- run the resulting jobs locally
- explain the compiled result when users debug it
- clearly reject unsupported component features before execution

