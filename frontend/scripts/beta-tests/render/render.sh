#!/usr/bin/env bash
# jsdom render tests for the Beta Manage panels. jsdom is NOT a repo dependency: it is installed into a temp dir.
#   usage:  bash frontend/scripts/beta-tests/render/render.sh
set -euo pipefail
cd "$(dirname "$0")"
FRONTEND="$(cd ../../.. && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
(cd "$TMP" && npm init -y >/dev/null 2>&1 && npm install jsdom --silent >/dev/null 2>&1)
# ECharts needs a canvas, which jsdom lacks: the Technical harness swaps echarts-for-react for a stub that exposes the option it was given.
node -e "
const esb=require('$FRONTEND/node_modules/esbuild');
const build=(entry,out,alias)=>esb.build({entryPoints:[entry],bundle:true,platform:'node',format:'cjs',outfile:out,external:['jsdom'],define:{'import.meta.env':'{}'},jsx:'automatic',
  nodePaths:['$FRONTEND/node_modules'],logLevel:'error',alias:alias||{}});
Promise.all([
  build('harness.tsx','$TMP/harness.bundle.cjs'),
  build('harness_ta.tsx','$TMP/harness_ta.bundle.cjs',{'echarts-for-react':'$PWD/echartsStub.tsx'}),
]).catch(e=>{console.error(e.message);process.exit(1)})"
cp run.cjs fixtures.cjs "$TMP/"
cd "$TMP"
rc=0
for h in harness harness_ta; do HARNESS=$h node run.cjs 2>&1 | grep -v "Warning\|Future Flag"; [ "${PIPESTATUS[0]}" -eq 0 ] || rc=1; done
exit $rc
