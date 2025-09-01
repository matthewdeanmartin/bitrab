#!/bin/bash

if [[ "${CI:-}" == "" ]]; then
  . ./global_variables.sh
fi

uv run bandit "$PACKAGE_DIR" -r --quiet
