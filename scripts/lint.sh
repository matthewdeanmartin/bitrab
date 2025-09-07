#!/bin/bash
set -euo pipefail

if [[ "${CI:-}" == "" ]]; then
  . ./global_variables.sh
fi

uv run ruff format .
uv run ruff check --fix .
uv run pylint "$PACKAGE_DIR" --fail-under 9.8
