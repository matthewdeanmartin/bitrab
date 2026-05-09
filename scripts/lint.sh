#!/bin/bash
set -euo pipefail

if [[ "${CI:-}" == "" ]]; then
  . ./global_variables.sh
fi

export UV_NO_SYNC=true
# uv --active run ruff format "$PACKAGE_DIR"
uv run --active ruff check --fix .
uv run --active pylint "$PACKAGE_DIR" --fail-under 9.8
