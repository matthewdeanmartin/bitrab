#!/bin/bash

if [[ "${CI:-}" == "" ]]; then
  . ./global_variables.sh
fi

uv run pytest test -vv \
--cov="$PACKAGE_DIR" --cov-branch \
--cov-report=xml --cov-report=html \
--cov-fail-under=48 \
--junitxml=junit.xml -o junit_family=legacy \
--timeout=5 --session-timeout=600
