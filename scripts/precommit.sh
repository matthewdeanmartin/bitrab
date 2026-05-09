#!/bin/bash
set -euo pipefail
if [[ "${CI:-}" == "" ]]; then
  . ./global_variables.sh
fi

export UV_NO_SYNC=true
uv  run --active pre-commit run --all-files
