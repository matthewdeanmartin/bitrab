#!/usr/bin/env bash
# Run bitrab against itself to exercise all major execution modes.
# Each section uses --dry-run so no real work is executed.
# Exit non-zero if any run fails.

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

PASS=0
FAIL=0

run_check() {
    local desc="$1"
    shift
    echo "  RUN: $desc"
    if uv run bitrab "$@"; then
        echo "  PASS: $desc"
        ((PASS++))
    else
        echo "  FAIL: $desc"
        ((FAIL++))
    fi
}

echo "=== bitrab dog-food: running bitrab against itself ==="
echo ""

# ── no-TUI modes ────────────────────────────────────────────────────────────
echo "--- no-TUI: thread backend ---"
run_check "no-tui --parallel-backend thread" \
    run --no-tui --parallel-backend thread --yes --dry-run

echo ""
echo "--- no-TUI: process backend ---"
run_check "no-tui --parallel-backend process" \
    run --no-tui --parallel-backend process --yes --dry-run

echo ""
echo "--- no-TUI: serial ---"
run_check "no-tui --serial" \
    run --no-tui --serial --dry-run

# ── TUI modes (exit automatically via --exit-on-completion) ─────────────────
echo ""
echo "--- TUI: thread backend ---"
run_check "tui --parallel-backend thread --exit-on-completion" \
    run --parallel-backend thread --yes --exit-on-completion --dry-run

echo ""
echo "--- TUI: process backend ---"
run_check "tui --parallel-backend process --exit-on-completion" \
    run --parallel-backend process --yes --exit-on-completion --dry-run

echo ""
echo "--- TUI: serial ---"
run_check "tui --serial --exit-on-completion" \
    run --serial --exit-on-completion --dry-run

# ── results ─────────────────────────────────────────────────────────────────
echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
