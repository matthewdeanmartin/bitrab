#! /bin/bash
set -eou pipefail
# Smoke test  all the tests that don't necessarily change anything
# exercises the arg parser mostly.
set -eou pipefail
echo "help..."
bitrab --help
echo "compile help..."
bitrab run --help
echo "version..."
bitrab --version
echo "dry run run"
bitrab run --dry-run
echo "done"

