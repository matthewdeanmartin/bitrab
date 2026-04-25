#!/bin/bash
set -euo pipefail

if [[ "${CI:-}" == "" ]]; then
  . ./global_variables.sh
fi

uv --active run ruff format "$PACKAGE_DIR"
uv --active run ruff check --fix .
uv --active run pylint "$PACKAGE_DIR" --fail-under 9.8
