# Running pipelines

## Basic usage

Run from the directory that contains your `.gitlab-ci.yml`:

```bash
bitrab
```

or explicitly:

```bash
bitrab run
```

Bitrab loads the config, resolves local `include:` files, runs stages in order, and streams output to your terminal.

## Specifying a config file

```bash
bitrab run -c path/to/my-ci.yml
```

## Dry run — preview without executing

```bash
bitrab run --dry-run
```

Prints what would run — stages, jobs, scripts — without executing any shell commands.

## Parallel jobs

By default bitrab runs jobs within a stage serially (one at a time). To run them in parallel:

```bash
bitrab run --parallel 4
```

This sets the maximum number of concurrent jobs per stage. Be aware that parallel jobs share the same working directory, so race conditions are possible if jobs write to the same files.

## Filtering what runs

Run only specific jobs:

```bash
bitrab run --jobs build test
```

Run only jobs in specific stages:

```bash
bitrab run --stage build test
```

You can combine both flags. Jobs that are not selected are skipped entirely, including their `needs:` dependency chain checks.

## Disabling the TUI

By default bitrab shows a Textual terminal UI when running interactively. To disable it and use plain streaming output:

```bash
bitrab run --no-tui
```

The TUI is automatically disabled in CI environments (when `CI=true` is set).

## Verbose and quiet modes

```bash
bitrab run --verbose     # debug-level logging
bitrab run --quiet       # errors only
```

## Environment variables that control behavior

| Variable | Effect |
|---|---|
| `BITRAB_RETRY_DELAY_SECONDS` | Base delay between retries (default: 1) |
| `BITRAB_RETRY_STRATEGY` | `exponential` (default) or `constant` |
| `BITRAB_RETRY_NO_SLEEP` | Set to `1` to skip retry sleep delays |
| `NO_COLOR` | Set to any value to disable colored output |
| `CI` | Set to `true` to activate CI output mode (no TUI) |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | All jobs passed |
| 1 | One or more jobs failed |
| 130 | Interrupted by Ctrl-C |
