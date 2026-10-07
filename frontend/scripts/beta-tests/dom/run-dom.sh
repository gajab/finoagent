#!/usr/bin/env bash
# DOM tests for the Beta's write actions (mocked network — never touches real data).
#   usage:  bash frontend/scripts/beta-tests/dom/run-dom.sh
set -euo pipefail
cd "$(dirname "$0")"
export JSDOM_DIR="${JSDOM_DIR:-${TMPDIR:-/tmp}/finoagent-jsdom}"
if [ ! -d "$JSDOM_DIR/node_modules/jsdom" ]; then
  mkdir -p "$JSDOM_DIR"
  (cd "$JSDOM_DIR" && npm init -y >/dev/null && npm install jsdom --silent --no-audit --no-fund)
fi
node run.cjs 2>&1 | grep -v "React Router Future Flag Warning"
