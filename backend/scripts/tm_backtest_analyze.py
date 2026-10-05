"""Trade-Manager backtest, step 2 — does each lens / feature actually predict forward outcomes?

Reads the point-in-time panel from tm_backtest_panel.py and reports, per signal:
  * cross-sectional IC (Spearman, per date → mean + t-stat, overlap-adjusted) vs forward 21d / 63d return
  * pooled IC and a first-half / second-half split (stability, "out-of-sample")
  * bucket table (quintiles of the composite): mean forward return, hit-rate, avg adverse excursion

    PYTHONPATH=. venv/bin/python scripts/tm_backtest_analyze.py panel.pkl
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd
from scipy import stats


def ic_by_date(df: pd.DataFrame, sig: str, tgt: str, min_n: int = 20) -> pd.Series:
    out = {}
    for d, g in df.groupby("date"):
        g = g[[sig, tgt]].dropna()
        if len(g) >= min_n and g[sig].nunique() > 2:
            out[d] = stats.spearmanr(g[sig], g[tgt])[0]
    return pd.Series(out)


def summarize(df: pd.DataFrame, sigs: list[str], tgt: str, overlap: float) -> pd.DataFrame:
    rows = []
    mid = df["date"].sort_values().iloc[len(df) // 2]
    for s in sigs:
        ic = ic_by_date(df, s, tgt)
        if len(ic) < 30:
            continue
        t = ic.mean() / (ic.std(ddof=1) / np.sqrt(len(ic) / overlap)) if ic.std(ddof=1) > 0 else np.nan
        ic1 = ic[ic.index < mid].mean()
        ic2 = ic[ic.index >= mid].mean()
        pooled = df[[s, tgt]].dropna()
        pic = stats.spearmanr(pooled[s], pooled[tgt])[0] if len(pooled) > 50 else np.nan
        rows.append({"signal": s, "IC": ic.mean(), "t_adj": t, "IC_1st_half": ic1, "IC_2nd_half": ic2, "pooled_IC": pic, "n_dates": len(ic)})
    return pd.DataFrame(rows).set_index("signal").sort_values("t_adj", ascending=False)


def buckets(df: pd.DataFrame, sig: str, q: int = 5) -> pd.DataFrame:
    g = df.dropna(subset=[sig, "fwd21"]).copy()
    g["bucket"] = pd.qcut(g[sig].rank(method="first"), q, labels=False) + 1
    return g.groupby("bucket").agg(
        n=("fwd21", "size"), sig_mean=(sig, "mean"), fwd21=("fwd21", "mean"), fwd21_med=("fwd21", "median"),
        win=("fwd21", lambda x: (x > 0).mean() * 100), p5=("fwd21", lambda x: np.percentile(x, 5)),
        mae21_pct=("mae21_pct", "mean"), mae21_atr=("mae21", "mean"), fwd63=("fwd63", "mean"),
    ).round(2)


if __name__ == "__main__":
    df = pd.read_pickle(sys.argv[1])
    df["date"] = pd.to_datetime(df["date"])
    print(f"panel: {df.shape}, {df.ticker.nunique()} tickers, {df.date.min().date()} → {df.date.max().date()}")
    lens = [c for c in df.columns if c.startswith("d_")]
    feats = ["cons", "rsi14", "adx", "from_hi", "above_lo", "vs50", "vs200", "roc21", "roc63", "roc126", "roc252", "rs63", "rs126", "rs252",
             "cmf", "mfi", "ud_vol", "dist_days", "acc_days", "macd_hist", "macd_slope", "st_dir", "weekly_stage", "rsi_div", "obv_div",
             "macd_div", "squeeze", "bw_pctile", "atr_pctile", "pctb", "rvol"]
    pd.set_option("display.width", 200)
    pd.set_option("display.max_rows", 200)
    for tgt, ov in (("fwd21", 4.2), ("fwd63", 12.6)):
        print(f"\n=== lens IC vs {tgt} (cross-sectional Spearman by date; t adj for overlapping windows) ===")
        print(summarize(df, lens + ["cons"], tgt, ov).round(3))
    print("\n=== features IC vs fwd21 ===")
    print(summarize(df, feats, "fwd21", 4.2).round(3))
    print("\n=== composite 'cons' quintiles (1 = most bearish) ===")
    print(buckets(df, "cons"))
    print("\n=== composite by regime: benchmark above/below 200d ===")
    for v, g in df.groupby("bench_above200"):
        print(f"bench_above200={v}:"); print(buckets(g, "cons"))
