"""Trade-Manager backtest, step 3 — which EXIT rule should the exit plan lead with?

Entry set = every point-in-time sample where the technical lens consensus was bullish (a long-delta
position the Trade Manager would be managing). For each entry we run every exit rule forward (max hold
H bars, exit on the NEXT open after a close-basis trigger — conservative) and compare expectancy,
tail, hold time and return-per-day-held.

    PYTHONPATH=. venv/bin/python scripts/tm_backtest_exits.py panel.pkl panel_prices.pkl [cons_threshold]
"""
from __future__ import annotations

import pickle
import sys

import numpy as np
import pandas as pd

from app.services import trader_lenses as TL

H = 126


def _prep(d: pd.DataFrame) -> dict:
    o, h, l, c, v = (d[k].values.astype(float) for k in ("Open", "High", "Low", "Close", "Volume"))
    a14, a22 = TL.atr(h, l, c, 14), TL.atr(h, l, c, 22)
    hh22 = pd.Series(h).rolling(22, min_periods=1).max().values
    st_dir, st_line = TL.supertrend(h, l, c, 10, 3.0)
    lo10 = pd.Series(l).shift(1).rolling(10).min().values
    lo20 = pd.Series(l).shift(1).rolling(20).min().values
    return dict(o=o, h=h, l=l, c=c, a14=a14, a22=a22, hh22=hh22, st=st_dir, lo10=lo10, lo20=lo20,
                e10=TL.ema(c, 10), e21=TL.ema(c, 21), s50=TL.sma(c, 50), s150=TL.sma(c, 150), s200=TL.sma(c, 200),
                n=len(c), idx=d.index)


def _fill_next_open(P, j):
    n = P["n"]
    return P["o"][j + 1] if j + 1 < n else P["c"][j]


def _first(cond: np.ndarray) -> int:
    w = np.flatnonzero(cond)
    return int(w[0]) if w.size else -1


def simulate(P: dict, i: int, rule: str) -> tuple[float, int]:
    """Return (% return, bars held) for one entry at close of bar i."""
    n, c, o, l = P["n"], P["c"], P["o"], P["l"]
    end = min(i + H, n - 1)
    if end <= i:
        return np.nan, 0
    px = c[i]
    js = np.arange(i + 1, end + 1)
    cj = c[js]

    def exit_at(rel: int) -> tuple[float, int]:
        if rel < 0:
            return (c[end] / px - 1) * 100, end - i
        j = js[rel]
        return (_fill_next_open(P, j) / px - 1) * 100, j + 1 - i

    if rule == "time63":
        e = min(i + 63, n - 1)
        return (c[e] / px - 1) * 100, e - i
    if rule == "hold126":
        return exit_at(-1)
    if rule.startswith("chand"):
        k = float(rule[5:])
        stop = P["hh22"][js] - k * P["a22"][js]
        stop = np.maximum.accumulate(np.nan_to_num(stop, nan=-np.inf))
        # ratchet from the entry bar's own stop so the first bars aren't unprotected
        stop0 = P["hh22"][i] - k * P["a22"][i]
        stop = np.maximum(stop, stop0)
        return exit_at(_first(cj < stop))
    if rule in ("ema10", "ema21", "sma50", "sma150", "sma200"):
        return exit_at(_first(cj < P[{"ema10": "e10", "ema21": "e21", "sma50": "s50", "sma150": "s150", "sma200": "s200"}[rule]][js]))
    if rule == "supertrend":
        return exit_at(_first(P["st"][js] < 0))
    if rule == "lo10":
        return exit_at(_first(cj < P["lo10"][js]))
    if rule == "lo20":
        return exit_at(_first(cj < P["lo20"][js]))
    if rule == "minervini3":
        rs, hs = [], []
        for key in ("s50", "s150", "s200"):
            r, hd = exit_at(_first(cj < P[key][js]))
            rs.append(r); hs.append(hd)
        return float(np.mean(rs)), int(np.mean(hs))
    if rule.startswith("fix"):
        pct = float(rule[3:]) / 100
        trig = px * (1 - pct)
        rel = _first(l[js] <= trig)
        if rel < 0:
            return exit_at(-1)
        j = js[rel]
        fill = min(o[j], trig)
        return (fill / px - 1) * 100, j - i
    if rule.startswith("combo"):                       # chandelier 3 OR close < 50d (whichever first)
        k = 3.0
        stop = np.maximum(np.maximum.accumulate(np.nan_to_num(P["hh22"][js] - k * P["a22"][js], nan=-np.inf)), P["hh22"][i] - k * P["a22"][i])
        cond = (cj < stop) | (cj < P["s50"][js])
        return exit_at(_first(cond))
    raise ValueError(rule)


RULES = ["hold126", "time63", "chand2", "chand2.5", "chand3", "chand3.5", "chand4", "chand5", "ema10", "ema21", "sma50", "sma150", "sma200",
         "supertrend", "lo10", "lo20", "minervini3", "fix8", "fix10", "fix15", "combo"]


def run(panel: pd.DataFrame, prices: dict, thr: float) -> pd.DataFrame:
    P = {t: _prep(d) for t, d in prices.items()}
    ent = panel[panel["cons"] >= thr][["ticker", "date"]].copy()
    ent["date"] = pd.to_datetime(ent["date"])
    rows = []
    for t, g in ent.groupby("ticker"):
        pp = P[t]
        pos = pp["idx"].get_indexer(g["date"])
        for i in pos:
            if i < 0 or i + 20 >= pp["n"]:
                continue
            for r in RULES:
                ret, hold = simulate(pp, i, r)
                rows.append((t, pp["idx"][i], r, ret, hold))
    return pd.DataFrame(rows, columns=["ticker", "date", "rule", "ret", "hold"])


def report(res: pd.DataFrame) -> pd.DataFrame:
    def agg(g):
        r = g["ret"].dropna()
        w, ls = r[r > 0], r[r <= 0]
        return pd.Series({
            "n": len(r), "mean%": r.mean(), "median%": r.median(), "win%": (r > 0).mean() * 100,
            "avg_win": w.mean(), "avg_loss": ls.mean(), "PF": (w.sum() / -ls.sum()) if ls.sum() < 0 else np.inf,
            "p5%": np.percentile(r, 5), "worst%": r.min(), "hold": g["hold"].mean(),
            "ret/day%": r.mean() / max(1.0, g["hold"].mean()) , "sharpe_like": r.mean() / r.std() if r.std() > 0 else np.nan,
        })
    return res.groupby("rule").apply(agg).round(3).sort_values("ret/day%", ascending=False)


if __name__ == "__main__":
    panel = pd.read_pickle(sys.argv[1])
    prices = pickle.load(open(sys.argv[2], "rb"))
    thr = float(sys.argv[3]) if len(sys.argv) > 3 else 0.25
    res = run(panel, prices, thr)
    pd.set_option("display.width", 220)
    print(f"entries cons>={thr}: {res.groupby('rule').size().iloc[0]} per rule")
    print("\n=== ALL ===")
    print(report(res))
    res["date"] = pd.to_datetime(res["date"])
    mid = res["date"].sort_values().iloc[len(res) // 2]
    print(f"\n=== 1st half (< {mid.date()}) ===")
    print(report(res[res.date < mid])[["mean%", "win%", "PF", "p5%", "hold", "ret/day%"]])
    print(f"\n=== 2nd half ===")
    print(report(res[res.date >= mid])[["mean%", "win%", "PF", "p5%", "hold", "ret/day%"]])
    import os
    res.to_pickle(os.path.join(os.path.dirname(os.path.abspath(sys.argv[1])), "exits_out.pkl"))
