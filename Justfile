set dotenv-load := true

LOGS_DIR := ".justlogs"
STAMP_DIR := ".build_history"
NO_COLOR_ENV := "NO_COLOR=1 CLICOLOR=0 FORCE_COLOR=0 PY_COLORS=0"
BITRAB_CONFIG := ".bitrab-ci.yml"
QUALITY_GATE_PARALLEL := "4"
QUALITY_GATE_BACKEND := "thread"

venv := `if [ -z "${VIRTUAL_ENV-}" ]; then echo "uv run"; else echo ""; fi`

_default: check

# List available build commands and their purpose.
help:
    @echo "Build commands:"
    @echo "  help             List build commands and descriptions"
    @echo "  fix              Run source-mutating fixers in canonical order"
    @echo "  fix-ci           Read-only formatter drift checks"
    @echo "  verify           Run read-only verification targets"
    @echo "  fast-verify      Run read-only verification in parallel with log collation"
    @echo "  triage           Alias for fast-verify"
    @echo "  repro            Run serial verification for easier debugging"
    @echo "  bugs             Run bug-finding focused checks"
    @echo "  check-human      Run fix, then verify with human-friendly sequencing"
    @echo "  check            Alias for check-human"
    @echo "  check-ci         Run non-mutating CI-safe verification and docs checks"
    @echo "  check-llm        Run compact token-efficient verification"
    @echo "  full-verify      Run verify plus docs checks"
    @echo "  ruff             Run read-only ruff checks"
    @echo "  mypy             Run mypy"
    @echo "  pylint           Run pylint"
    @echo "  bandit           Run bandit"
    @echo "  pytest           Run the Python test suite"
    @echo "  smoke            Run CLI smoke tests"
    @echo "  test             Run pytest plus smoke tests"
    @echo "  benchmark        Run performance benchmarks"
    @echo "  pre-commit       Run pre-commit hooks"
    @echo "  check-docs       Run documentation checks"
    @echo "  check-md         Run markdown checks in read-only mode"
    @echo "  check-spelling   Run spelling checks"
    @echo "  check-changelog  Validate changelog format"
    @echo "  check-all-docs   Run all documentation checks"
    @echo "  quality-gate     Validate and run the shared bitrab quality gate"
    @echo "  quality-gate-serial Run the shared bitrab quality gate in serial mode"
    @echo "  refresh-schema   Refresh vendored GitLab schema files"
    @echo "  publish          Build the distribution"

# Install all project dependencies with uv.
uv-lock:
    @echo "Installing dependencies"
    {{venv}} uv sync --all-extras --no-progress

# Remove compiled Python artifacts.
clean-pyc:
    @echo "Removing compiled files"

# Remove test and coverage outputs.
clean-test:
    @echo "Removing coverage data"
    rm -f .coverage || true
    rm -f .coverage.* || true

clean: clean-pyc clean-test

# Placeholder for plugin installation hooks.
install-plugins:
    @echo "N/A"

# Create the build stamp directory.
init-build-history:
    mkdir -p {{STAMP_DIR}}

# Create and clear the parallel verification log directory.
init-logs:
    mkdir -p {{LOGS_DIR}}
    rm -f {{LOGS_DIR}}/*.log {{LOGS_DIR}}/*.ok || true

# Sort imports in-place.
isort: uv-lock install-plugins init-build-history
    @echo "Formatting imports"
    {{venv}} isort .
    touch {{STAMP_DIR}}/isort

black: uv-lock install-plugins init-build-history
    @echo "Formatting code"
    {{venv}} black bitrab
    {{venv}} black test
    touch {{STAMP_DIR}}/black

ruff-fix: uv-lock install-plugins init-build-history
    @echo "Auto-fixing with ruff"
    {{venv}} ruff check --fix .
    touch {{STAMP_DIR}}/ruff-fix

sync-metadata: uv-lock install-plugins init-build-history
    @echo "Syncing generated metadata"
    {{venv}} metametameta pep621
    {{venv}} git2md bitrab --ignore __init__.py __pycache__ --output SOURCE.md
    touch {{STAMP_DIR}}/sync-metadata

fix: ruff-fix isort black sync-metadata

# Check formatter and linter drift without mutating files.
format-check: uv-lock install-plugins
    @echo "Checking formatter drift"
    {{NO_COLOR_ENV}} {{venv}} isort --check-only .
    {{NO_COLOR_ENV}} {{venv}} black --check bitrab test
    {{NO_COLOR_ENV}} {{venv}} ruff check .

fix-ci: format-check

# Run read-only Ruff checks.
ruff-only:
    @echo "Running ruff"
    {{venv}} ruff check .

ruff: uv-lock install-plugins ruff-only

# Run mypy type checking.
mypy-only:
    @echo "Running mypy"
    {{venv}} mypy bitrab --ignore-missing-imports --check-untyped-defs

mypy: uv-lock install-plugins mypy-only

# Run pylint checks.
pylint-only:
    @echo "Running pylint"
    {{venv}} pylint bitrab --fail-under 9.8 --rcfile=.pylintrc

pylint: uv-lock install-plugins pylint-only

# Run Bandit security checks.
bandit-only:
    @echo "Running bandit"
    {{venv}} bandit bitrab -r --quiet

bandit: uv-lock install-plugins bandit-only

# Run the pytest suite with coverage output.
pytest-only:
    @echo "Running unit tests"
    {{venv}} pytest test -vv \
      --cov=bitrab --cov-report=html --cov-fail-under 35 --cov-branch \
      --cov-report=xml --junitxml=junit.xml -o junit_family=legacy \
      --timeout=15 --session-timeout=600

pytest: clean uv-lock install-plugins pytest-only

# Run CLI smoke tests.
smoke-only:
    @echo "Running CLI smoke checks"
    {{venv}} bash scripts/basic_checks.sh

smoke: uv-lock install-plugins smoke-only

# Run pytest and smoke checks together.
test: pytest smoke

# Run the full read-only verification suite.
verify: ruff mypy pylint bandit test

# Write Ruff output to a parallel log file.
ruff-log:
    : > {{LOGS_DIR}}/ruff.log
    {{NO_COLOR_ENV}} {{venv}} ruff check . > {{LOGS_DIR}}/ruff.log 2>&1
    touch {{LOGS_DIR}}/ruff.ok

mypy-log:
    : > {{LOGS_DIR}}/mypy.log
    {{NO_COLOR_ENV}} {{venv}} mypy bitrab --ignore-missing-imports --check-untyped-defs > {{LOGS_DIR}}/mypy.log 2>&1
    touch {{LOGS_DIR}}/mypy.ok

pylint-log:
    : > {{LOGS_DIR}}/pylint.log
    {{NO_COLOR_ENV}} {{venv}} pylint bitrab --fail-under 9.8 --rcfile=.pylintrc > {{LOGS_DIR}}/pylint.log 2>&1
    touch {{LOGS_DIR}}/pylint.ok

bandit-log:
    : > {{LOGS_DIR}}/bandit.log
    {{NO_COLOR_ENV}} {{venv}} bandit bitrab -r --quiet > {{LOGS_DIR}}/bandit.log 2>&1
    touch {{LOGS_DIR}}/bandit.ok

smoke-log:
    : > {{LOGS_DIR}}/smoke.log
    {{NO_COLOR_ENV}} {{venv}} bash scripts/basic_checks.sh > {{LOGS_DIR}}/smoke.log 2>&1
    touch {{LOGS_DIR}}/smoke.ok

pytest-log:
    : > {{LOGS_DIR}}/pytest.log
    {{NO_COLOR_ENV}} {{venv}} pytest test -vv \
      --cov=bitrab --cov-report=html --cov-fail-under 35 --cov-branch \
      --cov-report=xml --junitxml=junit.xml -o junit_family=legacy \
      --timeout=15 --session-timeout=600 --color=no > {{LOGS_DIR}}/pytest.log 2>&1
    touch {{LOGS_DIR}}/pytest.ok

[parallel]
fast-verify-phase: ruff-log mypy-log pylint-log bandit-log smoke-log pytest-log

# Run read-only verification in parallel and print collated logs.
fast-verify: clean uv-lock install-plugins init-logs
    just fast-verify-phase || true
    missing=0; \
    for f in ruff mypy pylint bandit smoke pytest; do \
      echo ""; \
      echo "===== $f ====="; \
      if test -f {{LOGS_DIR}}/$f.log; then tail -n 80 {{LOGS_DIR}}/$f.log; fi; \
      if ! test -f {{LOGS_DIR}}/$f.ok; then missing=1; fi; \
    done; \
    exit $missing

triage: fast-verify

# Run serial verification to simplify local debugging.
repro: clean uv-lock install-plugins
    @echo "Running serial reproduction-friendly verification"
    {{venv}} pytest test -n 0 -vv --maxfail=1 \
      --cov=bitrab --cov-report=xml --cov-branch --junitxml=junit.xml \
      -o junit_family=legacy --timeout=15 --session-timeout=600
    {{venv}} bash scripts/basic_checks.sh

bugs: fix-ci ruff mypy pylint bandit repro smoke

# Run benchmark-focused tests.
benchmark: uv-lock install-plugins
    @echo "Running performance benchmarks"
    {{venv}} python scripts/run_benchmarks.py test/test_perf.py test/test_perf_fast.py -o "addopts=" --benchmark-min-rounds=5 --benchmark-min-time=0.1

pre-commit: uv-lock install-plugins
    @echo "Running pre-commit hooks"
    {{venv}} pre-commit run --all-files

# Run the human-friendly default workflow: fix, then verify.
check-human:
    @echo "=== fix ==="
    just fix
    @echo "=== verify ==="
    just verify

check: check-human

# Run the CI-safe non-mutating build workflow.
check-ci: fix-ci fast-verify check-all-docs

# Run the widest non-mutating verification flow.
full-verify: verify check-all-docs

# Run documentation checks.
check-docs:
    {{NO_COLOR_ENV}} {{venv}} interrogate bitrab --verbose --fail-under 70
    {{NO_COLOR_ENV}} {{venv}} pydoctest --config .pydoctest.json | grep -v "__init__" | grep -v "__main__" | grep -v "Unable to parse"

# Build API docs locally.
make-docs:
    {{venv}} pdoc bitrab --html -o docs --force

# Run markdown checks without mutating files.
check-md:
    {{NO_COLOR_ENV}} {{venv}} linkcheckMarkdown README.md
    {{NO_COLOR_ENV}} {{venv}} markdownlint README.md --config .markdownlintrc
    {{NO_COLOR_ENV}} {{venv}} mdformat --check README.md docs/*.md

# Run spelling and dictionary checks.
check-spelling:
    {{NO_COLOR_ENV}} {{venv}} pylint bitrab --enable C0402 --rcfile=.pylintrc_spell
    {{NO_COLOR_ENV}} {{venv}} pylint docs --enable C0402 --rcfile=.pylintrc_spell
    {{NO_COLOR_ENV}} {{venv}} codespell README.md --ignore-words=private_dictionary.txt
    {{NO_COLOR_ENV}} {{venv}} codespell bitrab --ignore-words=private_dictionary.txt
    {{NO_COLOR_ENV}} {{venv}} codespell docs --ignore-words=private_dictionary.txt

# Validate the changelog format.
check-changelog:
    {{NO_COLOR_ENV}} {{venv}} changelogmanager validate

# Run the full documentation verification suite.
check-all-docs: check-docs check-md check-spelling check-changelog

# Dogfood the project against itself.
check-own-ver:
    {{NO_COLOR_ENV}} {{venv}} ./scripts/dog_food.sh

# Build a distributable package after tests pass.
publish: test
    rm -rf dist && hatch build

# Placeholder issue target.
issues:
    @echo "N/A"

# Run compact LLM-oriented tests output.
test-llm: clean uv-lock install-plugins
    @echo "=== pytest (errors only) ==="
    {{NO_COLOR_ENV}} {{venv}} pytest test -q --tb=short --no-header \
      --cov=bitrab --cov-fail-under 35 --cov-branch \
      --timeout=15 --session-timeout=600 --color=no 2>&1 | tail -40

# Run compact LLM-oriented lint output.
lint-llm: uv-lock install-plugins
    @echo "=== ruff ==="
    {{NO_COLOR_ENV}} {{venv}} ruff check . 2>&1 | head -50
    @echo "=== pylint ==="
    {{NO_COLOR_ENV}} {{venv}} pylint bitrab --fail-under 9.8 --output-format=text --rcfile=.pylintrc 2>&1 \
      | grep -E "^bitrab|^E|^W|^C|Your code|[Ee]rror" | head -60

# Run compact LLM-oriented type-check output.
mypy-llm: uv-lock install-plugins
    @echo "=== mypy ==="
    {{NO_COLOR_ENV}} {{venv}} mypy bitrab --ignore-missing-imports --check-untyped-defs --no-error-summary 2>&1 \
      | grep -v "^Success" | head -60

# Run compact LLM-oriented security output.
bandit-llm: uv-lock install-plugins
    @echo "=== bandit ==="
    {{NO_COLOR_ENV}} {{venv}} bandit bitrab -r --severity-level medium 2>&1 | grep -E "Issue|Severity|>>|^$" | head -40

# Run compact LLM-oriented smoke output.
smoke-llm: uv-lock install-plugins
    @echo "=== smoke ==="
    {{NO_COLOR_ENV}} {{venv}} bash scripts/basic_checks.sh 2>&1 | tail -30

# Run the compact LLM-oriented verification suite.
check-llm: mypy-llm lint-llm bandit-llm test-llm smoke-llm
    @echo "=== check-llm done ==="

# Compatibility alias for fast-verify.
check-fast: fast-verify

# Validate and run the shared bitrab quality gate in parallel.
quality-gate: uv-lock install-plugins
    @echo "Validating shared bitrab quality gate"
    {{NO_COLOR_ENV}} {{venv}} bitrab -c {{BITRAB_CONFIG}} validate
    @echo "Running shared bitrab quality gate"
    {{NO_COLOR_ENV}} {{venv}} bitrab -c {{BITRAB_CONFIG}} run --no-tui --parallel {{QUALITY_GATE_PARALLEL}} --parallel-backend {{QUALITY_GATE_BACKEND}} --no-worktrees

# Validate and run the shared bitrab quality gate in serial mode.
quality-gate-serial: uv-lock install-plugins
    @echo "Validating shared bitrab quality gate"
    {{NO_COLOR_ENV}} {{venv}} bitrab -c {{BITRAB_CONFIG}} validate
    @echo "Running shared bitrab quality gate in serial mode"
    {{NO_COLOR_ENV}} {{venv}} bitrab -c {{BITRAB_CONFIG}} run --no-tui --serial

# Refresh vendored GitLab schema assets.
refresh-schema:
    just update-schema

# Download vendored GitLab schema assets.
update-schema:
    mkdir -p bitrab/schemas
    @echo "Downloading GitLab CI schema..."
    @if curl -fsSL "https://gitlab.com/gitlab-org/gitlab/-/raw/master/app/assets/javascripts/editor/schema/ci.json" -o bitrab/schemas/gitlab_ci_schema.json ; then \
      echo "✅ Schema saved"; \
    else \
      echo "⚠️  Warning: Failed to download schema"; \
    fi
    @echo "Downloading NOTICE..."
    @if curl -fsSL "https://gitlab.com/gitlab-org/gitlab/-/raw/master/app/assets/javascripts/editor/schema/NOTICE?ref_type=heads" -o bitrab/schemas/NOTICE.txt ; then \
      echo "✅ NOTICE saved"; \
    else \
      echo "⚠️  Warning: Failed to download NOTICE"; \
    fi
