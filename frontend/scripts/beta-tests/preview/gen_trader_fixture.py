"""Regenerates traderStrategies.json — the REAL backend service (trader_strategy_service.build_trader_strategies) run on a
synthetic, seeded daily series scaled to end at the preview's mock spot (238.90). Synthetic data only; run from the repo root:
    backend/venv/bin/python frontend/scripts/beta-tests/preview/gen_trader_fixture.py [seed]
"""
import json, os, sys
import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
sys.path.insert(0, os.path.join(ROOT, "backend"))
from app.services import trader_strategy_service as S   # noqa: E402

SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 5
SPOT = 238.90
rng = np.random.default_rng(SEED)
n = 500
# an uptrend that pulls back over the last ~25 sessions, so the rule-sets disagree the way they do on a real chart
r = np.concatenate([rng.normal(0.0016, 0.016, n - 25), rng.normal(-0.004, 0.012, 25)])
c = 100 * np.exp(np.cumsum(r))
c = c * (SPOT / c[-1])
o = np.concatenate([[c[0]], c[:-1]])
h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.006, n)))
l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.006, n)))
v = rng.integers(40_000_000, 90_000_000, n).astype(float)
idx = pd.date_range(end="2026-10-05", periods=n, freq="B")
bench = 400 * np.exp(np.cumsum(rng.normal(0.0006, 0.009, n)))
out = S.build_trader_strategies(o, h, l, c, v, idx, bench)
out["as_of"] = "2026-10-05"
dst = os.path.join(os.path.dirname(__file__), "traderStrategies.json")
json.dump(out, open(dst, "w"), indent=1)
print("seed", SEED, "consensus", out["consensus"])
for s in out["strategies"]:
    print(f"{s['key']:14s} {s['stance']:8s} {s['met']}/{s['total']}  stop={s['stop']}" if s["available"] else f"{s['key']:14s} unavailable")
