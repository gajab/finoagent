"""Trade-Manager backtest, step 7 — HOLD-vs-CLOSE value of a technical / risk STATE for an OPEN short put.

For every synthetic short put (same model as tm_backtest_shortput.py) we stand at the weekly check-points k = 5, 10, 15, 20
(skipping trades already closed by the 50%-profit rule) and compute  Δ = (final P&L if held to expiry) − (P&L if closed now),
in % of collateral. Δ < 0 ⇒ closing now would have been better. We then slice Δ by the state observed AT that bar:
strike distance in σ (→ P(touch)), consensus, vol ratio, drawdown, below-200d, supertrend, stage …

    PYTHONPATH=. venv/bin/python scripts/tm_backtest_hold.py panel.pkl panel_prices.pkl
"""
from __future__ import annotations

import math
import pickle
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import tm_backtest_shortput as S

CHECKS = (5, 10, 15, 20)


def run(panel: pd.DataFrame, prices: dict, every: int = 2) -> pd.DataFrame:
    panel = panel.copy()
    panel["date"] = pd.to_datetime(panel["date"])
    rows = []
    for t, g in panel.groupby("ticker"):
        c = prices[t]["Close"].values.astype(float)
        idx = prices[t].index
        g = g.assign(pos=idx.get_indexer(g["date"])).dropna(subset=["rv21", "rv63", "cons"])
        feat = g.set_index("pos")
        for _, r in g.iloc[::every].iterrows():
            i = int(r["pos"])
            if i < 0 or i + S.DTE >= len(c):
                continue
            iv0 = max(0.12, 0.5 * r["rv21"] / 100 + 0.5 * r["rv63"] / 100) * S.IV_MULT
            pth = S.path(c, i, iv0, "naked")
            if pth is None:
                continue
            credit, pnl, coll, K1, cost = pth
            fin = pnl[S.DTE - 1] - cost / 2
            for k in CHECKS:
                j = i + k
                if j not in feat.index:
                    continue
                cur = pnl[k - 1]
                if cur >= 0.5 * credit:
                    continue                                     # would already have been banked
                f = feat.loc[j]
                S_j = c[j]
                rv = 0.4 * f["rv21"] + 0.6 * f["rv63"]
                tau = (S.DTE - k) / 252
                sd = rv / 100 * math.sqrt(max(tau, 1e-6))
                z = math.log(K1 / S_j) / sd if sd > 0 else np.nan          # >0 means strike BELOW spot (OTM)
                p_touch = 1.0 if S_j <= K1 else min(1.0, 2 * (1 - S.ncdf(abs(z))))
                rows.append({"ticker": t, "date": idx[j], "k": k, "delta": (fin - cur) / coll * 100, "cur": cur / coll * 100,
                             "fin": fin / coll * 100, "cur_pct_credit": cur / credit * 100, "moneyness": S_j / K1 - 1,
                             "z": -z if S_j > K1 else -abs(z), "p_touch": p_touch, "cons": f["cons"], "from_hi": f["from_hi"], "vs200": f["vs200"],
                             "vs50": f["vs50"], "st_dir": f["st_dir"], "stage": f["weekly_stage"], "rsi14": f["rsi14"],
                             "ratio": f["rv21"] / f["rv63"], "bench200": f["bench_above200"], "adx": f["adx"]})
    return pd.DataFrame(rows)


def rep(g: pd.DataFrame) -> pd.Series:
    d = g["delta"]
    return pd.Series({"n": len(d), "hold−close Δ%": d.mean(), "median": d.median(), "P(Δ<0)": (d < 0).mean() * 100,
                      "worst5%": np.percentile(d, 5), "P(finish<−2%)": (g["fin"] < -2).mean() * 100, "avg_cur%": g["cur"].mean()})


if __name__ == "__main__":
    panel = pd.read_pickle(sys.argv[1])
    prices = pickle.load(open(sys.argv[2], "rb"))
    df = run(panel, prices, 2)
    pd.set_option("display.width", 230); pd.set_option("display.max_columns", 30); pd.set_option("display.max_rows", 100)
    print(f"open-position check-points: {len(df)}")
    print("\n=== all ===\n", rep(df).round(3).to_frame().T)
    print("\n=== by P(touch) bucket (σ-based, at the check-point) ===")
    print(df.groupby(pd.cut(df.p_touch, [0, .1, .2, .35, .5, .75, 1.0], include_lowest=True)).apply(rep).round(3))
    print("\n=== by moneyness (spot/strike−1) ===")
    print(df.groupby(pd.cut(df.moneyness, [-1, 0, .02, .05, .10, .20, 5])).apply(rep).round(3))
    print("\n=== by P&L so far (% of credit) ===")
    print(df.groupby(pd.cut(df.cur_pct_credit, [-1e4, -200, -100, -50, 0, 25, 50])).apply(rep).round(3))
    for name, col, bins in (("consensus", "cons", [-1, -.4, -.1, .1, .4, 1]), ("21/63 vol ratio", "ratio", [0, .75, 1, 1.4, 10]), ("drawdown from high", "from_hi", [-100, -20, -10, -5, 1])):
        print(f"\n=== by {name} ===")
        print(df.groupby(pd.cut(df[col], bins)).apply(rep).round(3))
    for name, mask in (("below 200d", df.vs200 < 0), ("below 50d", df.vs50 < 0), ("supertrend down", df.st_dir < 0), ("stage 4", df.stage == 4), ("bench below 200d", df.bench200 == 0)):
        print(f"\n=== {name}: ON vs OFF ===")
        print(pd.concat({"ON": rep(df[mask]), "OFF": rep(df[~mask])}, axis=1).T.round(3))
    df.to_pickle(__import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(sys.argv[1])), "hold_out.pkl"))
