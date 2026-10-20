"""Regenerates momentumFragile.json — the REAL backend Momentum Swing engine (momentum_service.compute_momentum_setup) run on
a synthetic, seeded LITE-like series (a long orderly uptrend, then a violent parabolic final leg) scaled to end at the
preview's mock spot (238.90), with SPY mocked flat and deep=False (no options / regime-edge fetches). It must come out
4/4 FRAGILE with live long setups, so the render test exercises the whole second axis. Synthetic data only; run from the
repo root:
    backend/venv/bin/python frontend/scripts/beta-tests/preview/gen_momentum_fragile_fixture.py [seed]
"""
import json, os, sys
import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
sys.path.insert(0, os.path.join(ROOT, "backend"))
import yfinance as yf                                   # noqa: E402
from app.services.momentum_service import compute_momentum_setup   # noqa: E402

SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 2
SPOT = 238.90
n = 420
rng = np.random.default_rng(SEED)
r = list(0.0025 + rng.normal(0, 0.015, n - 25)) + list(0.012 + rng.normal(0, 0.045, 25))
r[-15] = 0.09                                          # a lottery-like one-day pop inside the last 21 sessions
r[-9:] = [0.03, 0.025, 0.02, 0.035, 0.015, 0.03, 0.02, 0.025, 0.03]   # a hot 10-day run into "now"
c = 50.0 * np.cumprod(1 + np.array(r))
c = c * (SPOT / c[-1])
o = np.concatenate([[c[0]], c[:-1]])
w = np.abs(rng.normal(0, 0.01, n))
idx = pd.date_range(end="2026-10-05", periods=n, freq="B", tz="America/New_York")
daily = pd.DataFrame({"Open": o, "High": np.maximum(o, c) * (1 + w), "Low": np.minimum(o, c) * (1 - w), "Close": c,
                      "Volume": rng.integers(20_000_000, 40_000_000, n).astype(float)}, index=idx)
spy = daily.copy()
spy[["Open", "High", "Low", "Close"]] = np.full((n, 4), 100.0) * (1 + np.linspace(0, 0.02, n))[:, None]


class _Fake:
    def __init__(self, df):
        self.df, self.calendar = df, {}

    def history(self, period=None, interval=None, **_):
        return pd.DataFrame() if (interval and interval[-1] in "mh") else self.df.copy()


yf.Ticker = lambda *a, **k: _Fake(spy)
out = compute_momentum_setup(_Fake(daily), deep=False)
frag = out["qualification"]["fragility"]
assert frag["flags"] == 4, f"seed {SEED}: expected a 4/4 fragile read, got {frag['flags']}"
assert any(s["direction"] == "long" for s in out["setups"]), f"seed {SEED}: no live long setup"
path = os.path.join(os.path.dirname(__file__), "momentumFragile.json")
with open(path, "w") as f:
    json.dump({"ticker": "NVDA", "momentum_setup": out}, f, indent=1, default=str)
print("wrote", path, "| flags", frag["flags"], "| setups", [s["type"] for s in out["setups"]])
