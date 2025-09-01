#! /bin/bash
set -eou pipefail
# Smoke test  all the tests that don't necessarily change anything
# exercises the arg parser mostly.

IN=test/test_commands/scenario2/src
OUT=test/test_commands/scenario2/out
set -eou pipefail
echo "help..."
bitrab --help
echo "compile help..."
bitrab compile --help
echo "compile version..."
bitrab --version
echo "done"