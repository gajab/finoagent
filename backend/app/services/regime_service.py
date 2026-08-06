"""Market-regime classifier & statistical extremes.

Answers one question before any trade is chosen: **is this market trending or
mean-reverting right now?** — because the right structure flips with the regime.

  • Hurst Exponent (H) — persistence of the series via the structure-function method
    (std of lagged differences scales as lag^H). H>0.5 = persistent/TRENDING,
    H<0.5 = anti-persistent/MEAN-REVERTING, H≈0.5 = random walk.
  • Kaufman Efficiency Ratio (ER) — |net move| / |path length| over a window, in [0,1].
    High = clean/efficient trend; low = choppy/noise.
  • Rolling Z-Score vs the 50-day VWAP — how many σ price sits from its volume-weighted
    mean; flags statistical over-expansion (a stretched tape ripe to revert).

Regime → playbook: TRENDING favors momentum breakouts / trend-following / directional
vertical (debit) spreads; MEAN-REVERTING favors fading extremes / range trades / iron
condors & strangles. Computed on Daily and 4-Hour. Pure numpy/pandas, best-effort.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .microstructure_service import _safe_history, _safe_history_days, _price_series, _now_str, _r
from .market_structure_service import _resample


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------

def _hurst(prices, min_n: int = 8) -> float | None:
    """Hurst via classic Rescaled-Range (R/S) analysis on LOG-RETURNS. Returns the slope
    of log(R/S) vs log(window). iid returns (random-walk price) → H≈0.5; persistent /
    momentum returns → H>0.5 (trending); anti-persistent / choppy returns → H<0.5
    (mean-reverting). Returns None on too-little / degenerate data.

    R/S is applied to RETURNS (not price levels): price levels are I(1) and would
    trivially read as persistent; the regime question is about the *increments*."""
    x = np.asarray(prices, dtype=float)
    x = x[np.isfinite(x) & (x > 0)]
    if len(x) < 120:
        return None
    rets = np.diff(np.log(x))
    rets = rets[np.isfinite(rets)]
    n_ret = len(rets)
    if n_ret < 100:
        return None

    # geometric ladder of window sizes
    ns: list[int] = []
    n = min_n
    while n <= n_ret // 2:
        ns.append(n)
        n = int(n * 1.6) or n + 1
    rs: list[tuple[int, float]] = []
    for w in ns:
        k = n_ret // w
        vals = []
        for i in range(k):
            seg = rets[i * w:(i + 1) * w]
            dev = seg - seg.mean()
            z = np.cumsum(dev)
            spread = float(z.max() - z.min())
            s = float(seg.std())
            if s > 0 and spread > 0:
                vals.append(spread / s)
        if vals:
            rs.append((w, float(np.mean(vals))))
    if len(rs) < 3:
        return None
    try:
        slope = float(np.polyfit(np.log([a for a, _ in rs]), np.log([b for _, b in rs]), 1)[0])
    except Exception:
        return None
    if not np.isfinite(slope):
        return None
    return float(np.clip(slope, 0.0, 1.0))


def _efficiency_ratio(prices, window: int = 20) -> float | None:
    """Kaufman ER = |net change| / Σ|bar-to-bar change| over the trailing window (0..1)."""
    p = np.asarray(prices, dtype=float)
    p = p[np.isfinite(p)]
    if len(p) <= 3:
        return None
    w = min(window, len(p) - 1)
    change = abs(float(p[-1] - p[-1 - w]))
    path = float(np.sum(np.abs(np.diff(p[-1 - w:]))))
    if path <= 0:
        return None
    return round(min(1.0, change / path), 3)


def _zscore_vwap(highs, lows, closes, volumes, window: int = 50) -> dict | None:
    """How many σ current price sits from its rolling `window`-day VWAP."""
    h, l, c, v = (np.asarray(x, dtype=float) for x in (highs, lows, closes, volumes))
    n = len(c)
    if n < window + 5 or v[-window:].sum() <= 0:
        return None
    tp = (h + l + c) / 3.0
    pv = pd.Series(tp * v)
    vv = pd.Series(v)
    vwap = (pv.rolling(window).sum() / vv.rolling(window).sum())
    resid = (pd.Series(c) - vwap).dropna()
    if len(resid) < 5:
        return None
    sd = float(resid.std())
    if sd <= 0:
        return None
    z = round((float(resid.iloc[-1]) - float(resid.mean())) / sd, 2)
    vwap_last = float(vwap.iloc[-1])
    state = ("stretched-high" if z >= 2 else "stretched-low" if z <= -2
             else "elevated-high" if z >= 1.5 else "elevated-low" if z <= -1.5 else "normal")
    return {
        "window": window, "vwap": _r(vwap_last), "z": z, "state": state,
        "distance_pct": _r((float(c[-1]) - vwap_last) / vwap_last * 100) if vwap_last else None,
    }


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------

_TRENDING = {
    "regime": "trending",
    "playbook": "Trending regime — favor momentum breakouts, trend-following entries, and "
                "directional VERTICAL (debit) spreads with the trend. Avoid fading; avoid "
                "short-premium range structures that lose in a run.",
    "favored": ["momentum_breakout", "trend_following", "debit_vertical_spread"],
}
_MEAN_REV = {
    "regime": "mean_reverting",
    "playbook": "Mean-reverting regime — fade statistical extremes back toward the mean, trade "
                "the range, and sell premium with IRON CONDORS / STRANGLES. Avoid chasing breakouts.",
    "favored": ["fade_extremes", "range_trade", "iron_condor", "strangle"],
}
_TRANSITION = {
    "regime": "transitional",
    "playbook": "No dominant regime — persistence is near a random walk. Reduce size, wait for "
                "the Hurst/ER to resolve, or trade smaller defined-risk structures.",
    "favored": ["reduce_size", "wait_confirmation"],
}


def _classify(hurst: float | None, er: float | None) -> dict:
    """Blend Hurst (persistence) and ER (trend efficiency) into a regime label."""
    if hurst is None and er is None:
        return {**_TRANSITION, "confidence": "low"}
    h = hurst if hurst is not None else 0.5
    e = er if er is not None else 0.0
    trend_score = (1 if h >= 0.55 else 0) + (1 if e >= 0.30 else 0)
    revert_score = (1 if h <= 0.45 else 0) + (1 if e < 0.20 else 0)
    if trend_score >= 1 and h >= 0.52:
        base, conf = _TRENDING, ("high" if trend_score == 2 else "medium")
    elif revert_score >= 1 and h <= 0.48:
        base, conf = _MEAN_REV, ("high" if revert_score == 2 else "medium")
    else:
        base, conf = _TRANSITION, "low"
    return {**base, "confidence": conf, "hurst": _r(h, 3), "efficiency_ratio": e}


def _tf_regime(df, label: str) -> dict | None:
    if df is None or getattr(df, "empty", True) or len(df) < 50:
        return None
    c = df["Close"].values
    hurst = _hurst(c)
    er = _efficiency_ratio(c)
    cl = _classify(hurst, er)
    return {"label": label, "hurst": _r(hurst, 3) if hurst is not None else None,
            "efficiency_ratio": er, **{k: cl[k] for k in ("regime", "confidence", "playbook", "favored")}}


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def compute_regime(stock) -> dict | None:
    """Regime read on Daily + 4-Hour plus the 50-day VWAP z-score. ``None`` only if no
    usable data exists."""
    try:
        daily = _safe_history(stock, "1y", "1d")
        h1 = _safe_history_days(stock, 58, "1h")
        h4 = _resample(h1, "4h")

        price = None
        for df in (daily, h1):
            if df is not None and not df.empty:
                price = float(df["Close"].values[-1])
                break
        if price is None:
            return None

        timeframes = {"daily": _tf_regime(daily, "Daily"), "h4": _tf_regime(h4, "4-Hour")}

        z = None
        if daily is not None and not daily.empty:
            z = _zscore_vwap(daily["High"].values, daily["Low"].values,
                             daily["Close"].values, daily["Volume"].values, window=50)

        # overall = the daily read (primary), h4 as confirmation
        primary = timeframes["daily"] or timeframes["h4"] or {}
        overall = {
            "overall": primary.get("regime", "transitional"),
            "confidence": primary.get("confidence", "low"),
            "hurst_daily": timeframes["daily"]["hurst"] if timeframes["daily"] else None,
            "er_daily": timeframes["daily"]["efficiency_ratio"] if timeframes["daily"] else None,
            "aligned": bool(timeframes["daily"] and timeframes["h4"]
                            and timeframes["daily"]["regime"] == timeframes["h4"]["regime"]),
            "playbook": primary.get("playbook", _TRANSITION["playbook"]),
            "favored": primary.get("favored", _TRANSITION["favored"]),
        }

        return {
            "price": _r(price),
            "as_of": _now_str(),
            "regime": overall,
            "timeframes": timeframes,
            "zscore": z,
            "price_series": _price_series(daily if daily is not None else h1),
        }
    except Exception:  # noqa: BLE001
        return None
