#!/usr/bin/env bash
# Launch the FinoAgent Derivative Income MCP server over stdio, using the backend venv
# (which already has yfinance / QuantLib / the `app` package installed).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
PY="${FINOAGENT_PYTHON:-$REPO_ROOT/backend/venv/bin/python}"

if [[ ! -x "$PY" ]]; then
  echo "Python interpreter not found at $PY. Set FINOAGENT_PYTHON to your backend venv python." >&2
  exit 1
fi

export FINOAGENT_BACKEND_DIR="${FINOAGENT_BACKEND_DIR:-$REPO_ROOT/backend}"
exec "$PY" "$HERE/server.py"
