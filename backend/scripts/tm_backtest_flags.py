"""Trade-Manager backtest, step 6 — which ADVERSE flags actually mark a worse forward path?

For every binary technical flag (price below a MA, supertrend down, Weinstein stage 4, …) compare the next-21-day
outcome when it is ON vs OFF: mean return, P(return ≤ −8 %), mean max-adverse-excursion in ATR, and a date-clustered
t-stat of the difference (overlap-adjusted). These decide which flags may legitimately trigger the Trade
Manager's discipline overrides.

    PYTHONPATH=. venv/bin/python scripts/tm_backtest_flags.py panel.pkl
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd


def flags(df: pd.DataFrame) -> pd.DataFrame:
    f = pd.DataFrame(index=df.index)
    px = df["px"]
    f["below_sma50"] = px < df["sma50"]
    f["below_sma150"] = px < df["sma150"]
    f["below_sma200"] = px < df["sma200"]
    f["below_ema21"] = px < df["ema21"]
    f["supertrend_down"] = df["st_dir"] < 0
    f["below_chandelier3"] = px < df["chand"]
    f["below_prior_lo20"] = px < df["lo20"]
    f["below_prior_lo10"] = px < df["lo10"]
    f["stage4"] = df["weekly_stage"] == 4
    f["stage3or4"] = df["weekly_stage"].isin([3, 4])
    f["sma50_below_150"] = df["sma50_gt_150"] == 0
    f["down_20pct_from_high"] = df["from_hi"] < -20
    f["down_10pct_from_high"] = df["from_hi"] < -10
    f["atr_pctile_gt80"] = df["atr_pctile"] > 80
    f["rv21_gt_1.5x_rv63"] = (df["rv21"] / df["rv63"]) > 1.5
    f["rsi_lt_40"] = df["rsi14"] < 40
    f["rsi_gt_70"] = df["rsi14"] > 70
    f["bear_rsi_div"] = df["rsi_div"] < 0
    f["bear_obv_div"] = df["obv_div"] < 0
    f["dist_days_ge5"] = df["dist_days"] >= 5
    f["adx_gt25_minus_di_dom"] = (df["adx"] > 25) & (df["mdi"] > df["pdi"])
    f["squeeze_on"] = df["squeeze"] > 0
    f["cons_lt_neg0.25"] = df["cons"] < -0.25
    f["cons_gt_0.25"] = df["cons"] > 0.25
    f["bench_below200"] = df["bench_above200"] == 0
    f["below_sma200_and_stage4"] = f["below_sma200"] & f["stage4"]
    f["below_sma50_and_st_down"] = f["below_sma50"] & f["supertrend_down"]
    f["below_sma150_and_bench_below200"] = f["below_sma150"] & f["bench_below200"]
    return f.fillna(False)


def compare(df: pd.DataFrame, f: pd.DataFrame) -> pd.DataFrame:
    rows = []
    d = df.assign(drop8=(df["fwd21"] <= -8).astype(float))
    for c in f.columns:
        on, off = d[f[c]], d[~f[c]]
        if len(on) < 300:
            continue
        diff = on["fwd21"].mean() - off["fwd21"].mean()
        se = np.sqrt(on["fwd21"].var() / (len(on) / 4.2) + off["fwd21"].var() / (len(off) / 4.2))
        rows.append({"flag": c, "n_on": len(on), "pct_on": 100 * len(on) / len(d), "fwd21_on": on["fwd21"].mean(), "fwd21_off": off["fwd21"].mean(),
                     "diff": diff, "t": diff / se, "P(≤-8%)_on": 100 * on["drop8"].mean(), "P(≤-8%)_off": 100 * off["drop8"].mean(),
                     "mae_atr_on": on["mae21"].mean(), "mae_atr_off": off["mae21"].mean(),
                     "vol_ratio": on["fwd21"].std() / off["fwd21"].std()})
    return pd.DataFrame(rows).set_index("flag").round(2).sort_values("diff")


if __name__ == "__main__":
    df = pd.read_pickle(sys.argv[1])
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["fwd21", "mae21"])
    f = flags(df)
    pd.set_option("display.width", 230); pd.set_option("display.max_columns", 30); pd.set_option("display.max_rows", 100)
    print("=== all samples ===")
    print(compare(df, f))
    mid = df["date"].sort_values().iloc[len(df) // 2]
    print(f"\n=== 2nd half (>= {mid.date()}) ===")
    m = df["date"] >= mid
    print(compare(df[m], f[m])[["n_on", "fwd21_on", "fwd21_off", "diff", "t", "P(≤-8%)_on", "P(≤-8%)_off", "mae_atr_on", "mae_atr_off", "vol_ratio"]])
