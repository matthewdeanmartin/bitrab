#! /bin/bash
# Smoke test: exercises the CLI arg parser and non-interactive commands.
# Counts successes and failures; exits non-zero if any check failed.
# Uses --no-tui -j 1 for run checks to avoid spawning workers in CI/scripts.

set -ou pipefail

PASS=0
FAIL=0

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

echo "--- global flags ---"
check "bitrab --help"                        uv run bitrab --help
check "bitrab --version"                     uv run bitrab --version
check "bitrab --license"                     uv run bitrab --license

echo ""
echo "--- run ---"
check "bitrab run --help"                    uv run bitrab run --help
check "bitrab run --dry-run --no-tui -j 1"  uv run bitrab run --dry-run --no-tui -j 1
check "bitrab run --dry-run --no-tui -j 2"  uv run bitrab run --dry-run --no-tui -j 2
check "bitrab run --dry-run --jobs build"    uv run bitrab run --dry-run --no-tui -j 1 --jobs build
check "bitrab run --dry-run --stage test"    uv run bitrab run --dry-run --no-tui -j 1 --stage test

echo ""
echo "--- list ---"
check "bitrab list --help"                   uv run bitrab list --help
check "bitrab list"                          uv run bitrab list

echo ""
echo "--- validate ---"
check "bitrab validate --help"               uv run bitrab validate --help
check "bitrab validate"                      uv run bitrab validate
check "bitrab validate --json"               uv run bitrab validate --json

echo ""
echo "--- debug ---"
check "bitrab debug --help"                  uv run bitrab debug --help
check "bitrab debug"                         uv run bitrab debug

echo ""
echo "--- clean (dry-run) ---"
check "bitrab clean --help"                  uv run bitrab clean --help
check "bitrab clean --dry-run"               uv run bitrab clean --dry-run

echo ""
echo "--- graph (dry-run) ---"
check "bitrab graph --help"                  uv run bitrab graph --help
check "bitrab graph --dry-run"               uv run bitrab graph --dry-run

echo ""
echo "--- expected failures ---"
check_fails "bitrab lint exits non-zero"          uv run bitrab lint
check_fails "bitrab run --config missing.yml"     uv run bitrab run --config __nonexistent__.yml

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
