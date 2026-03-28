# Contributing

## Build entrypoints

This repository supports both `Makefile` and `Justfile` as first-class interfaces.

Use either:

```bash
make help
just help
```

These commands list the supported build jobs and what each one does.

## Recommended local workflow

For normal local development, run mutators first and verification second:

```bash
make check-human
just check-human
```

That flow:

- runs `fix` first so formatters and autofixers clean up the tree before slower checks
- runs `verify` second using read-only checks

If you only want the mutating phase:

```bash
make fix
just fix
```

If you only want read-only verification:

```bash
make verify
just verify
```

## CI-safe and LLM-friendly workflows

For CI-safe, non-mutating verification:

```bash
make check-ci
just check-ci
```

For compact, token-efficient output:

```bash
make check-llm
just check-llm
```

## Fast parallel verification

To run read-only checks in parallel with collated logs:

```bash
make fast-verify
just fast-verify
```

Use `triage` as an alias for the same workflow.

## Bug-hunting workflows

For a correctness- and security-focused pass:

```bash
make bugs
just bugs
```

For easier debugging with serial execution:

```bash
make repro
just repro
```

## Notes

- `fix-ci` is read-only and checks that formatter-driven changes are not needed.
- `refresh-schema` is intentionally opt-in because it is networked and mutating.
- `check-md` now uses `mdformat --check` so markdown verification stays read-only.
