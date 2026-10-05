"""Trade-Manager backtest, step 1 — build a POINT-IN-TIME panel.

For every ticker in the universe and every ``STEP`` bars, compute the full indicator suite + the 15 trader
lenses using ONLY data up to that bar (window of the last ``WIN`` bars — no look-ahead), then record the
forward outcomes (returns, max adverse / favourable excursion in ATR units). Saved as a pickle that the
analysis script (tm_backtest_analyze.py) reads.

    PYTHONPATH=. venv/bin/python scripts/tm_backtest_panel.py [out.pkl]
"""
from __future__ import annotations

import pickle
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

UNIVERSE = [
    # mega / large cap growth + value across sectors
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "ORCL", "ADBE", "CRM", "AMD", "INTC", "QCOM", "CSCO",
    "JPM", "BAC", "GS", "MS", "WFC", "V", "MA", "BRK-B",
    "XOM", "CVX", "COP", "SLB",
    "JNJ", "PFE", "MRK", "UNH", "LLY", "ABBV", "TMO",
    "PG", "KO", "PEP", "WMT", "COST", "MCD", "NKE", "SBUX", "HD", "LOW", "DIS", "NFLX",
    "CAT", "DE", "BA", "GE", "HON", "UPS", "LMT",
    "T", "VZ", "NEE", "DUK", "AMT", "PLD", "LIN", "FCX", "NEM",
    # ETFs (broad / sector / bond / commodity / international)
    # fallen / lagging names (reduce survivorship bias — all still listed, but drawn from losers & laggards)
    "PYPL", "WBA", "KHC", "MMM", "CVS", "F", "GM", "C", "SCHW", "EL", "WBD", "DG", "TGT", "LUV", "AAL", "CCL", "MRNA", "ZM", "SNAP",
    "SPY", "QQQ", "IWM", "DIA", "XLE", "XLF", "XLK", "XLV", "XLI", "XLP", "XLU", "XLY", "GLD", "SLV", "TLT", "HYG", "EEM", "EFA", "USO",
]
BENCH = "SPY"
YEARS = 12
WIN = 330
STEP = 5
FWD = (5, 10, 21, 63)
MIN_BARS = 270


def load_prices(tickers: list[str], years: int = YEARS) -> dict[str, pd.DataFrame]:
    import yfinance as yf
    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(tickers), 20):
        batch = tickers[i:i + 20]
        df = yf.download(batch, period=f"{years}y", interval="1d", auto_adjust=True, progress=False,
                         group_by="ticker", threads=True)
        for t in batch:
            try:
                d = df[t].dropna(subset=["Close"]) if isinstance(df.columns, pd.MultiIndex) else df.dropna(subset=["Close"])
                if len(d) > MIN_BARS + 30:
                    out[t] = d[["Open", "High", "Low", "Close", "Volume"]].copy()
            except Exception:  # noqa: BLE001
                continue
    return out


def _one(args):
    t, d, bench = args
    from app.services import trader_lenses as TL
    o, h, l, c, v = (d[k].values.astype(float) for k in ("Open", "High", "Low", "Close", "Volume"))
    idx = d.index
    bser = bench.reindex(idx, method="ffill").values.astype(float)
    n = len(c)
    rows = []
    # true range for ATR-unit excursions
    for i in range(MIN_BARS, n - 1, STEP):
        lo = i - WIN + 1
        try:
            s = TL.indicator_suite(o[lo:i + 1], h[lo:i + 1], l[lo:i + 1], c[lo:i + 1], v[lo:i + 1], idx[lo:i + 1], bser[lo:i + 1])
            if not s:
                continue
            lens = TL.trader_lenses(s)
        except Exception:  # noqa: BLE001
            continue
        atr = s.get("atr14") or 0.0
        px = c[i]
        r = {"ticker": t, "date": idx[i], "px": px, "atr": atr}
        for L in lens:
            r[f"d_{L['key']}"] = L["d"]
            r[f"met_{L['key']}"] = (L["met"] / L["total"]) if L["total"] else np.nan
            for rl in L["rules"]:
                r[f"r_{L['key']}_{rl['id']}"] = np.nan if rl["met"] is None else (1.0 if rl["met"] else 0.0)
        r["cons"] = float(np.mean([L["d"] for L in lens])) if lens else np.nan
        # flat indicator features
        r.update({
            "rsi14": s.get("rsi14"), "rsi2": s.get("rsi2"), "adx": (s.get("adx") or {}).get("adx"),
            "pdi": (s.get("adx") or {}).get("plus_di"), "mdi": (s.get("adx") or {}).get("minus_di"),
            "from_hi": (s.get("range") or {}).get("pct_from_52w_high"), "above_lo": (s.get("range") or {}).get("pct_above_52w_low"),
            "vs50": (s.get("pct_vs_sma") or {}).get("50"), "vs200": (s.get("pct_vs_sma") or {}).get("200"),
            "roc21": (s.get("roc") or {}).get("21"), "roc63": (s.get("roc") or {}).get("63"),
            "roc126": (s.get("roc") or {}).get("126"), "roc252": (s.get("roc") or {}).get("252"),
            "atr_pct": s.get("atr_pct"), "atr_pctile": s.get("atr_percentile_1y"), "bw_pctile": (s.get("bollinger") or {}).get("bandwidth_percentile_1y"),
            "pctb": (s.get("bollinger") or {}).get("pct_b"), "rvol": s.get("rvol"), "cmf": s.get("cmf20"), "mfi": s.get("mfi14"),
            "ud_vol": s.get("up_down_volume_ratio_20d"), "dist_days": s.get("distribution_days_25d"), "acc_days": s.get("accumulation_days_25d"),
            "macd_hist": (s.get("macd") or {}).get("hist"), "macd_slope": (s.get("macd") or {}).get("hist_slope_3d"),
            "squeeze": 1.0 if s.get("squeeze_on") else 0.0, "st_dir": 1.0 if (s.get("supertrend") or {}).get("direction") == "up" else -1.0,
            "weekly_stage": (s.get("weekly") or {}).get("stage"), "rsi_div": {"bullish": 1.0, "bearish": -1.0}.get(s.get("rsi_divergence"), 0.0),
            "obv_div": {"bullish": 1.0, "bearish": -1.0}.get(s.get("obv_divergence"), 0.0),
            "macd_div": {"bullish": 1.0, "bearish": -1.0}.get(s.get("macd_divergence"), 0.0),
            "rs63": ((s.get("relative_strength") or {}).get("excess_return_pct") or {}).get("63"),
            "rs126": ((s.get("relative_strength") or {}).get("excess_return_pct") or {}).get("126"),
            "rs252": ((s.get("relative_strength") or {}).get("excess_return_pct") or {}).get("252"),
            "bench_above200": 1.0 if (s.get("relative_strength") or {}).get("benchmark_above_200d") else 0.0,
            "rv21": (s.get("realized_vol") or {}).get("21"), "rv63": (s.get("realized_vol") or {}).get("63"),
            "chand": (s.get("chandelier") or {}).get("long_stop"), "stl": (s.get("supertrend") or {}).get("line"),
            "ema10": (s.get("ema") or {}).get("10"), "ema21": (s.get("ema") or {}).get("21"),
            "sma50": (s.get("sma") or {}).get("50"), "sma150": (s.get("sma") or {}).get("150"), "sma200": (s.get("sma") or {}).get("200"),
            "hi252": (s.get("range") or {}).get("hi_252"), "lo252": (s.get("range") or {}).get("lo_252"),
            "sma50_gt_150": 1.0 if ((s.get("sma") or {}).get("50") or 0) > ((s.get("sma") or {}).get("150") or 0) else 0.0,
            "adr20": s.get("adr20_pct"), "stoch_k": (s.get("stoch") or {}).get("percent_k"), "aroon_up": (s.get("aroon") or {}).get("up"),
            "lo10": (s.get("prior_range") or {}).get("lo_10"), "lo20": (s.get("prior_range") or {}).get("lo_20"),
        })
        # forward outcomes
        for k in FWD:
            r[f"fwd{k}"] = (c[i + k] / px - 1.0) * 100.0 if i + k < n else np.nan
        for k in (10, 21, 63):
            if i + k < n and atr > 0:
                lows, highs = l[i + 1:i + 1 + k], h[i + 1:i + 1 + k]
                r[f"mae{k}"] = (np.min(lows) - px) / atr          # ATR units, ≤ 0 mostly
                r[f"mfe{k}"] = (np.max(highs) - px) / atr
                r[f"mae{k}_pct"] = (np.min(lows) / px - 1.0) * 100.0
            else:
                r[f"mae{k}"] = r[f"mfe{k}"] = r[f"mae{k}_pct"] = np.nan
        rows.append(r)
    return t, rows


def build_panel(prices: dict[str, pd.DataFrame], workers: int = 6) -> pd.DataFrame:
    bench = prices[BENCH]["Close"]
    jobs = [(t, d, bench) for t, d in prices.items()]
    all_rows: list[dict] = []
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for t, rows in ex.map(_one, jobs):
            all_rows += rows
            print(f"  {t}: {len(rows)} rows  ({time.time() - t0:.0f}s)", flush=True)
    return pd.DataFrame(all_rows)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "panel.pkl"
    t0 = time.time()
    px = load_prices(UNIVERSE)
    print(f"loaded {len(px)} tickers in {time.time() - t0:.0f}s", flush=True)
    pickle.dump(px, open(out.replace(".pkl", "_prices.pkl"), "wb"))
    panel = build_panel(px)
    panel.to_pickle(out)
    print(f"panel {panel.shape} -> {out} in {time.time() - t0:.0f}s", flush=True)
