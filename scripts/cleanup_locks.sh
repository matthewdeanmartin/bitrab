#!/bin/bash

# Kill bitrab and uv processes
pkill -f bitrab || true
pkill -f uv || true

# Kill any python processes running from this project's virtual environment
VENV_PATH=$(pwd)/.venv
pgrep -f python | while read -r pid; do
    if [[ $(readlink -f /proc/$pid/exe 2>/dev/null) == "$VENV_PATH"* ]]; then
        kill -9 $pid
    fi
done

echo "Cleanup complete. Process locks should be released."
