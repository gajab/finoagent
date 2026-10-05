"""Trade-Manager backtest, step 4 — does MANAGING a short-put position beat holding it?

SYNTHETIC options (no historical chains available): every sample date we sell a 24-trading-day (~35 DTE)
~20Δ put — naked (cash-secured) and as a put-CREDIT-SPREAD (long put 5% lower) — priced with Black-Scholes at an
IV proxy of  1.15 × blended realized vol (typical IV/RV premium incl. skew), marked daily with a vol-spike on
down moves (IV × (1 + 1.5·drop)) so exits aren't unrealistically cheap, plus a per-leg transaction cost.
The mark path is computed ONCE per entry and every management rule is evaluated on the same path.

    hold            hold to expiry
    tp50            close at 50 % of max credit
    stop2x/stop1x   tp50 + close at −2× / −1× credit
    tech(thr)       tp50 + WEEKLY check: exit if the point-in-time technical consensus < thr
    tech+struct     tp50 + exit if consensus < 0 AND close < 50d SMA
    sma200          tp50 + exit on a close below the 200d SMA (as of that bar)
    wide            tp50 + exit if close < entry − 2.5·ATR (a wide, entry-anchored invalidation)
    dte21           tp50 + close at 21 DTE remaining (the 21-DTE rule)
    breach          tp50 + exit when the SHORT strike is breached (close < K)
Entry filters are compared as well: none / above-200d / consensus ≥ 0.

Relative comparisons are informative; absolute P&L is not (IV proxy, no real chains).

    PYTHONPATH=. venv/bin/python scripts/tm_backtest_shortput.py panel.pkl panel_prices.pkl [every_nth_row]
"""
from __future__ import annotations

import math
import pickle
import sys

import numpy as np
import pandas as pd

DTE = 24
R = 0.03
IV_MULT = 1.15
SPIKE = 1.5
COST_BPS = 2.0
NINV = 0.8416212335729143       # N^-1(0.80)  → |Δ| = 0.20


def ncdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def bs_put(S, K, tau, iv):
    if tau <= 0:
        return max(K - S, 0.0)
    sd = iv * math.sqrt(tau)
    d1 = (math.log(S / K) + (R + 0.5 * iv * iv) * tau) / sd
    return K * math.exp(-R * tau) * ncdf(-(d1 - sd)) - S * ncdf(-d1)


def strike_for_delta(S, tau, iv):
    return S * math.exp((R + 0.5 * iv * iv) * tau - NINV * iv * math.sqrt(tau))


RULES = [("hold", None), ("tp50", None), ("stop1x", None), ("stop2x", None), ("tech", -0.3), ("tech", -0.1), ("tech", 0.0), ("tech", 0.15),
         ("tech+struct", None), ("sma200", None), ("wide", None), ("dte21", None), ("breach", None)]


def path(c, i, iv0, structure):
    S0 = c[i]
    tau0 = DTE / 252
    K1 = strike_for_delta(S0, tau0, iv0)
    K2 = K1 - 0.05 * S0 if structure == "spread" else None
    p1 = bs_put(S0, K1, tau0, iv0)
    p2 = bs_put(S0, K2, tau0, iv0 * 1.05) if K2 else 0.0
    credit = p1 - p2
    if credit <= 0:
        return None
    pnl = np.empty(DTE)
    for k in range(1, DTE + 1):
        S = c[i + k]
        tau = (DTE - k) / 252
        iv = iv0 * (1 + SPIKE * max(0.0, 1 - S / S0))
        v = bs_put(S, K1, tau, iv) - (bs_put(S, K2, tau, iv * 1.05) if K2 else 0.0)
        pnl[k - 1] = credit - v
    coll = (K1 - K2) if K2 else K1 - credit
    return credit, pnl, coll, K1, 2 * COST_BPS / 1e4 * S0 * (2 if K2 else 1)


def apply(rule, thr, credit, pnl, closes, cons_at, sma50_at, sma200_at, i, atr0, K1):
    """first-exit index (0-based day k-1) for a rule, or None → hold to expiry."""
    tp = np.flatnonzero(pnl >= 0.5 * credit)
    first = {"tp": tp[0] if tp.size else DTE + 1}
    ex = DTE            # expiry sentinel
    cand = []
    if rule != "hold":
        cand.append(first["tp"])
    for k in range(1, DTE):
        j = i + k
        S = closes[k - 1]
        if rule == "stop2x" and pnl[k - 1] <= -2 * credit:
            cand.append(k - 1); break
        if rule == "stop1x" and pnl[k - 1] <= -1 * credit:
            cand.append(k - 1); break
        if rule == "tech" and k % 5 == 0 and cons_at.get(j) is not None and cons_at[j] < thr:
            cand.append(k - 1); break
        if rule == "tech+struct" and k % 5 == 0 and cons_at.get(j) is not None and cons_at[j] < 0 and S < sma50_at.get(j, -1e18):
            cand.append(k - 1); break
        if rule == "sma200" and S < sma200_at.get(j, -1e18) and j in sma200_at:
            cand.append(k - 1); break
        if rule == "wide" and S < closes[-1 - 0] * 0 + (cons_at.get("px0") - 2.5 * atr0):
            cand.append(k - 1); break
        if rule == "dte21" and DTE - k <= 21 and False:
            pass
        if rule == "breach" and S < K1:
            cand.append(k - 1); break
    if rule == "dte21":                   # 21 DTE remaining ≈ close at 21 calendar days before expiry → trading day DTE-15
        cand.append(max(0, DTE - 15 - 1))
    k_exit = min(cand) if cand else DTE
    return k_exit


def run(panel: pd.DataFrame, prices: dict, every: int = 2) -> pd.DataFrame:
    panel = panel.copy()
    panel["date"] = pd.to_datetime(panel["date"])
    rows = []
    for t, g in panel.groupby("ticker"):
        d = prices[t]
        c = d["Close"].values.astype(float)
        idx = d.index
        g = g.assign(pos=idx.get_indexer(g["date"])).dropna(subset=["rv21", "rv63", "cons"])
        cons_at = dict(zip(g["pos"], g["cons"]))
        sma50_at = dict(zip(g["pos"], g["sma50"]))
        sma200_at = {p: v for p, v in zip(g["pos"], g["sma200"]) if pd.notna(v)}
        recs = g.iloc[::every]
        for _, r in recs.iterrows():
            i = int(r["pos"])
            if i < 0 or i + DTE >= len(c):
                continue
            iv0 = max(0.12, 0.5 * r["rv21"] / 100 + 0.5 * r["rv63"] / 100) * IV_MULT
            closes = c[i + 1:i + 1 + DTE]
            atr0 = r["atr"]
            for st in ("naked", "spread"):
                pth = path(c, i, iv0, st)
                if pth is None:
                    continue
                credit, pnl, coll, K1, cost = pth
                for rule, thr in RULES:
                    # rule evaluation
                    cand = [] if rule == "hold" else [int(np.flatnonzero(pnl >= 0.5 * credit)[0]) if (pnl >= 0.5 * credit).any() else DTE]
                    for k in range(1, DTE):
                        j, S, m = i + k, closes[k - 1], None
                        if rule == "stop2x" and pnl[k - 1] <= -2 * credit: cand.append(k - 1); break
                        if rule == "stop1x" and pnl[k - 1] <= -1 * credit: cand.append(k - 1); break
                        if rule == "tech" and k % 5 == 0 and j in cons_at and cons_at[j] < thr: cand.append(k - 1); break
                        if rule == "tech+struct" and k % 5 == 0 and j in cons_at and cons_at[j] < 0 and S < sma50_at.get(j, -1e18): cand.append(k - 1); break
                        if rule == "sma200" and k % 1 == 0 and (r["sma200"] == r["sma200"]) and S < r["sma200"]: cand.append(k - 1); break
                        if rule == "wide" and S < c[i] - 2.5 * atr0: cand.append(k - 1); break
                        if rule == "breach" and S < K1: cand.append(k - 1); break
                    if rule == "dte21":
                        cand.append(DTE - 15 - 1)
                    ke = min(cand) if cand else DTE
                    if ke >= DTE:                                   # held to expiry: no closing cost
                        p = pnl[DTE - 1] - cost / 2
                        early = False
                    else:
                        p = pnl[ke] - cost
                        early = True
                    label = rule if rule != "tech" else f"tech({thr})"
                    rows.append((t, idx[i], st, label, p / coll * 100, (ke + 1) if ke < DTE else DTE, early,
                                 r["vs200"], r["cons"], r["from_hi"], r["bench_above200"]))
    return pd.DataFrame(rows, columns=["ticker", "date", "struct", "rule", "ret_coll", "held", "early", "vs200", "cons", "from_hi", "bench200"])


def report(df: pd.DataFrame) -> pd.DataFrame:
    def agg(g):
        r = g["ret_coll"]
        return pd.Series({"n": len(r), "mean%": r.mean(), "median%": r.median(), "win%": (r > 0).mean() * 100, "avg_loss": r[r <= 0].mean(),
                          "p5%": np.percentile(r, 5), "CVaR5%": r[r <= np.percentile(r, 5)].mean(), "worst%": r.min(), "sd": r.std(),
                          "mean/sd": r.mean() / r.std(), "early%": g["early"].mean() * 100, "held": g["held"].mean()})
    return df.groupby(["struct", "rule"]).apply(agg).round(3)


if __name__ == "__main__":
    panel = pd.read_pickle(sys.argv[1])
    prices = pickle.load(open(sys.argv[2], "rb"))
    every = int(sys.argv[3]) if len(sys.argv) > 3 else 2
    res = run(panel, prices, every)
    pd.set_option("display.width", 220); pd.set_option("display.max_rows", 200)
    print(f"trades per cell: {len(res) // max(1, res.groupby(['struct', 'rule']).ngroups)}")
    print("\n=== ALL ENTRIES ===")
    print(report(res))
    for name, mask in (("above 200d", res["vs200"] > 0), ("below 200d", res["vs200"] <= 0), ("cons>=0", res["cons"] >= 0), ("cons<0", res["cons"] < 0)):
        print(f"\n=== entries: {name} ===")
        print(report(res[mask])[["n", "mean%", "win%", "p5%", "CVaR5%", "mean/sd"]])
    res["date"] = pd.to_datetime(res["date"])
    mid = res["date"].sort_values().iloc[len(res) // 2]
    print(f"\n=== 2nd half only (≥ {mid.date()}) ===")
    print(report(res[res.date >= mid])[["n", "mean%", "win%", "p5%", "CVaR5%", "mean/sd"]])
    import os
    res.to_pickle(os.path.join(os.path.dirname(os.path.abspath(sys.argv[1])), "shortput_out.pkl"))
