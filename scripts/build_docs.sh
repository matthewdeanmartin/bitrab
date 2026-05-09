#!/bin/bash

if [[ "${CI:-}" == "" ]]; then
  . ./global_variables.sh
fi

export UV_NO_SYNC=true
uv  run --active pdoc "$PACKAGE_DIR" --html -o docs --force
