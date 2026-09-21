"""OHLC candles feed for the interactive TA charts.

A thin, cached yfinance wrapper that serves candlesticks at a chosen interval so the Advanced-tab
panels behave like a normal trading chart (switch 15m · 1h · 1d · 1wk and the candles re-render).
Native yfinance intervals only (no resampling) to keep it correct and cheap; the analytical reads
(volume profile, structure, regime, gamma) stay in their own services — this just draws the price.
"""
from __future__ import annotations

from .microstructure_service import _safe_history, _r, _now_str, _to_date

# interval → (yfinance interval, period). Native intervals only (yfinance has no 4h).
# Intraday intervals respect yfinance's ~60d (15m) / ~730d (60m) availability limits.
_INTERVALS: dict[str, tuple[str, str]] = {
    "15m": ("15m", "1mo"),
    "1h":  ("60m", "3mo"),
    "1d":  ("1d", "1y"),
    "1wk": ("1wk", "5y"),
}
_MAX_BARS = 400          # cap payload size (chart shows the recent window anyway, with zoom)


def supported_intervals() -> list[str]:
    return list(_INTERVALS.keys())


def _fmt_ts(idx, intraday: bool) -> str:
    try:
        import pandas as pd
        ts = pd.Timestamp(idx)
        return ts.strftime("%m-%d %H:%M") if intraday else ts.strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return str(idx)[:16]


def compute_candles(stock, interval: str = "1d") -> dict | None:
    """Return OHLC candles for one interval: {interval, candles:[{t,o,h,l,c,v}], count, as_of}.
    Best-effort; None only when there's no data at all for the interval."""
    interval = interval if interval in _INTERVALS else "1d"
    yf_interval, period = _INTERVALS[interval]
    intraday = interval in ("15m", "1h")
    df = _safe_history(stock, period, yf_interval)
    if df is None or getattr(df, "empty", True):
        return None
    df = df.tail(_MAX_BARS)
    candles = []
    for idx, row in df.iterrows():
        o, h, l, c = row.get("Open"), row.get("High"), row.get("Low"), row.get("Close")
        if c is None:
            continue
        candles.append({
            "t": _fmt_ts(idx, intraday),
            "o": _r(o), "h": _r(h), "l": _r(l), "c": _r(c),
            "v": int(row.get("Volume") or 0),
        })
    if not candles:
        return None
    last = candles[-1]
    return {
        "interval": interval, "candles": candles, "count": len(candles),
        "spot": last["c"], "as_of": _now_str(),
        "range": {"low": min(x["l"] for x in candles if x["l"] is not None),
                  "high": max(x["h"] for x in candles if x["h"] is not None)},
    }
