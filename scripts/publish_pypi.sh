#!/bin/bash
set -euo pipefail
if [[ "${CI:-}" == "" ]]; then
  . ./global_variables.sh
fi
echo "Pretending to publish to pypi"
exit 0
