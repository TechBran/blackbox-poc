#!/usr/bin/env bash
# Capture current working venv state for reproducibility
set -euo pipefail

VENV_DIR="${1:-Orchestrator/venv}"
OUT="${2:-requirements.lock.txt}"

if [[ ! -d "$VENV_DIR" ]]; then
    echo "ERROR: venv not found at $VENV_DIR" >&2
    exit 1
fi

"$VENV_DIR/bin/pip" freeze --all > "$OUT"
echo "Wrote $(wc -l < "$OUT") packages to $OUT"
