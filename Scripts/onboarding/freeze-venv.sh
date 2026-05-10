#!/usr/bin/env bash
# Capture current working venv state for reproducibility
set -euo pipefail

# Default paths are relative to repo root; run from there.
VENV_DIR="${1:-Orchestrator/venv}"
LOCK_FILE="${2:-requirements.lock.txt}"

if [[ ! -d "$VENV_DIR" ]]; then
    echo "ERROR: venv not found at $VENV_DIR" >&2
    exit 1
fi

if [[ ! -x "$VENV_DIR/bin/pip" ]]; then
    echo "ERROR: pip not found at $VENV_DIR/bin/pip" >&2
    exit 1
fi

"$VENV_DIR/bin/pip" freeze --all > "$LOCK_FILE.tmp"
mv "$LOCK_FILE.tmp" "$LOCK_FILE"
echo "Wrote $(wc -l < "$LOCK_FILE") packages to $LOCK_FILE"
