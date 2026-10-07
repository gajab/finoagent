#!/usr/bin/env bash
# Unit tests for the Beta experience's pure logic (action states, score build-up, price ladder, picks, split-pane maths, roll maths, leg-action payloads,
# Evidence views (technical reads, fundamental pillars, news/earnings placement, desk hand-off),
# Technical: first-touch odds vs independent PDE/Monte-Carlo references, verdict/lenses/plan/levels, chart-label layout, strategies board model).
# The frontend has no test runner, so each module is bundled with the repo's esbuild and checked with plain node.
#   usage:  bash frontend/scripts/beta-tests/run.sh
set -euo pipefail
cd "$(dirname "$0")"
SRC="$(cd ../../src/beta && pwd)"
BUILD_DIR="$(mktemp -d)"
export BUILD_DIR

build() { ENTRY="$SRC/$1" OUT="$BUILD_DIR/${1%.*}.cjs" HELPERS="${2:-expiryFrom}" node build.cjs >/dev/null; }
build betaState.ts expiryFrom,dteFrom,tradeHasStock
build betaLegEdit.ts expiryFrom,dteFrom,tradeHasStock,inferStrategyType
ENTRY="$SRC/../lib/rollMath.ts" OUT="$BUILD_DIR/rollMath.cjs" HELPERS=expiryFrom node build.cjs >/dev/null
build scoreModel.ts
build betaLadder.ts
build betaPicks.ts
build betaBookFixes.ts
build betaEvidenceModel.ts
build betaDesk.ts
ENTRY="$PWD/rowsEntry.ts" OUT="$BUILD_DIR/betaRows.cjs" HELPERS=tradeStructure,deployedCapitalWithBasis,deployedCapital node build.cjs >/dev/null
build SplitPane.tsx
ENTRY="$SRC/ta/firstPassage.ts" OUT="$BUILD_DIR/firstPassage.cjs" HELPERS=expiryFrom node build.cjs >/dev/null
ENTRY="$SRC/ta/betaTaModel.ts" OUT="$BUILD_DIR/betaTaModel.cjs" HELPERS=expiryFrom node build.cjs >/dev/null
ENTRY="$SRC/ta/betaTaChartModel.ts" OUT="$BUILD_DIR/betaTaChartModel.cjs" HELPERS=expiryFrom node build.cjs >/dev/null
ENTRY="$SRC/ta/betaStrategyModel.ts" OUT="$BUILD_DIR/betaStrategyModel.cjs" HELPERS=expiryFrom node build.cjs >/dev/null
ENTRY="$SRC/../components/taLayers.ts" OUT="$BUILD_DIR/taLayers.cjs" HELPERS=expiryFrom node build.cjs >/dev/null
ENTRY="$PWD/datesEntry.ts" OUT="$BUILD_DIR/datesEntry.cjs" HELPERS=dteFrom node build.cjs >/dev/null

fail=0
for t in state score ladder picks split roll legedit bookfixes rows evidence taodds tamodel tachart strategies; do node "test_$t.cjs" || fail=1; done
# dates are timezone-sensitive: run them in a zone behind UTC (the bug), UTC, and one ahead of UTC
for tz in America/Los_Angeles America/New_York UTC Asia/Tokyo; do TZ=$tz node test_dates.cjs || fail=1; done
rm -rf "$BUILD_DIR"
exit $fail
