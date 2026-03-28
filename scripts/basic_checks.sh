#! /bin/bash
# Smoke test: exercises the CLI arg parser and non-interactive commands.
# Counts successes and failures; exits non-zero if any check failed.
# Uses the already-selected Python environment and avoids nesting `uv run`
# inside the script, which would pay startup/bootstrap cost on every check.
# Uses --no-tui -j 1 for run checks to avoid spawning workers in CI/scripts.

set -ou pipefail

PASS=0
FAIL=0
BITRAB_PYTHON="${PYTHON:-python}"

run_bitrab() {
    "$BITRAB_PYTHON" -m bitrab "$@"
}

check() {
    local desc="$1"
    shift
    if "$@" > /dev/null 2>&1; then
        echo "  PASS: $desc"
        ((PASS++))
    else
        echo "  FAIL: $desc  (cmd: $*)"
        ((FAIL++))
    fi
}

check_fails() {
    local desc="$1"
    shift
    if "$@" > /dev/null 2>&1; then
        echo "  FAIL: $desc  (expected non-zero exit, got 0)"
        ((FAIL++))
    else
        echo "  PASS: $desc"
        ((PASS++))
    fi
}

echo "=== bitrab basic_checks ==="
echo ""
echo "using: ${BITRAB_PYTHON} -m bitrab"
echo ""

echo "--- global flags ---"
check "bitrab --help"                        run_bitrab --help
check "bitrab --version"                     run_bitrab --version
check "bitrab --license"                     run_bitrab --license

echo ""
echo "--- run ---"
check "bitrab run --help"                    run_bitrab run --help
check "bitrab run --dry-run --no-tui -j 1"  run_bitrab run --dry-run --no-tui -j 1
check "bitrab run --dry-run --no-tui -j 2"  run_bitrab run --dry-run --no-tui -j 2
check "bitrab run --dry-run --jobs build"    run_bitrab run --dry-run --no-tui -j 1 --jobs build
check "bitrab run --dry-run --stage test"    run_bitrab run --dry-run --no-tui -j 1 --stage test

echo ""
echo "--- list ---"
check "bitrab list --help"                   run_bitrab list --help
check "bitrab list"                          run_bitrab list

echo ""
echo "--- validate ---"
check "bitrab validate --help"               run_bitrab validate --help
check "bitrab validate"                      run_bitrab validate
check "bitrab validate --json"               run_bitrab validate --json

echo ""
echo "--- debug ---"
check "bitrab debug --help"                  run_bitrab debug --help
check "bitrab debug"                         run_bitrab debug

echo ""
echo "--- clean (dry-run) ---"
check "bitrab clean --help"                  run_bitrab clean --help
check "bitrab clean --dry-run"               run_bitrab clean --dry-run

echo ""
echo "--- graph (dry-run) ---"
check "bitrab graph --help"                  run_bitrab graph --help
check "bitrab graph --dry-run"               run_bitrab graph --dry-run

echo ""
echo "--- expected failures ---"
check_fails "bitrab lint exits non-zero"          run_bitrab lint
check_fails "bitrab run --config missing.yml"     run_bitrab run --config __nonexistent__.yml

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
