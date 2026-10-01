"""Volume analysis — make volume behavior easy to SEE and reason about.

Every pattern / setup on the platform leans on volume (breakout confirmation, VCP / Qullamäggie
dry-up, climax reversals, accumulation/distribution), but a raw histogram is hard to read. This
service turns OHLCV into the reads a technical trader actually wants — all DETERMINISTIC, so the
numbers are trustworthy and inspectable (copyable JSON / Ask-AI), never LLM-invented:

  • RVOL 10/20/50 — current bar vs its recent average at three lookbacks (spike / dry-up)
  • trend         — is volume rising or falling (volume-MA slope)
  • dry-up ⇄ expansion — the contraction that precedes a breakout vs the surge that confirms one
  • climax bars   — bars ≥ 2× the reference, tagged up (blow-off / breakout) or down (capitulation)
  • up/down split — buying vs selling pressure over the recent window
  • CVD (delta)   — cumulative volume delta proxy (accumulation vs distribution)
  • OBV + divergence — price vs on-balance-volume (a classic early-warning)

**Time-of-Day adjustment (pro method) for intraday (15m/1H):** intraday volume is U-shaped (heavy
at the open/close, thin midday), so a flat trailing average makes every close look like a "spike".
The reference for an intraday bar is instead the average of the SAME time-of-day slot over the last
N days — so RVOL / climax measure a real surge relative to what that slot usually does. Daily/weekly
use a plain trailing N-bar average. Fetches its own history (larger than the 400-bar chart window)
so the N-day time-of-day baseline is available; cached, off the hot path.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from .microstructure_service import _r, _now_str, _safe_history, _safe_history_days, append_live_daily_bar
from .candles_service import supported_intervals

_INTRADAY = {"15m", "1h"}
_LOOKBACKS = (10, 20, 50)
_PRIMARY = 20                 # the lookback that drives the chart baseline + climax + the read
_CHART_BARS = 300             # bars returned for the chart; references are computed on full history


def _fetch(stock, interval: str):
    """History sized so the N-day time-of-day baseline is available (bigger than the chart window)."""
    if interval == "15m":
        return _safe_history_days(stock, 58, "15m")      # yfinance 15m ≤ 60d → ~40 trading days
    if interval == "1h":
        return _safe_history_days(stock, 120, "60m")     # ~80 trading days of hourly
    if interval == "1wk":
        return _safe_history(stock, "5y", "1wk")
    return append_live_daily_bar(stock, _safe_history(stock, "1y", "1d"))  # include today's unsettled bar


def _fmt_ts(ts, intraday: bool) -> str:
    try:
        import pandas as pd
        t = pd.Timestamp(ts)
        return t.strftime("%m-%d %H:%M") if intraday else t.strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return str(ts)[:16]


def _ref_series(v: np.ndarray, slots: list[str] | None, lookback: int) -> np.ndarray:
    """Per-bar reference volume EXCLUDING the current bar.
    slots None → trailing `lookback`-bar average; slots given → same-time-of-day average over the
    last `lookback` prior occurrences (days) of that slot."""
    n = len(v)
    ref = np.empty(n)
    if slots is None:
        for i in range(n):
            lo = max(0, i - lookback)
            ref[i] = float(np.mean(v[lo:i])) if i > lo else float(v[i])
    else:
        hist: dict[str, list[float]] = defaultdict(list)
        for i in range(n):
            prior = hist[slots[i]]
            ref[i] = float(np.mean(prior[-lookback:])) if prior else float(v[i])
            prior.append(float(v[i]))
    return ref


def _slope_state(series: np.ndarray, recent: int, prior: int) -> str:
    if len(series) < recent + prior:
        return "flat"
    r = float(np.mean(series[-recent:]))
    p = float(np.mean(series[-(recent + prior):-recent]))
    if p == 0:
        return "flat"
    ch = r / p - 1.0
    return "rising" if ch > 0.08 else "falling" if ch < -0.08 else "flat"


def compute_volume_analysis(stock, interval: str = "1d", lookback: int = _PRIMARY) -> dict | None:
    interval = interval if interval in supported_intervals() else "1d"
    primary_lb = lookback if lookback in _LOOKBACKS else _PRIMARY   # user-selectable primary lookback
    intraday = interval in _INTRADAY
    df = _fetch(stock, interval)
    if df is None or getattr(df, "empty", True) or len(df) < 12:
        return None

    o = df["Open"].values.astype(float); h = df["High"].values.astype(float)
    l = df["Low"].values.astype(float); c = df["Close"].values.astype(float)
    v = df["Volume"].values.astype(float)
    n = len(v)
    ts = [_fmt_ts(t, intraday) for t in df.index]
    slots = None
    if intraday:
        try:
            import pandas as pd
            slots = [pd.Timestamp(t).strftime("%H:%M") for t in df.index]
        except Exception:  # noqa: BLE001
            slots = None

    # references at each lookback (time-of-day for intraday, trailing otherwise)
    refs = {lb: _ref_series(v, slots, lb) for lb in _LOOKBACKS}
    primary = refs[primary_lb]

    def rv(lb: int):
        r = refs[lb][-1]
        return _r(v[-1] / r, 2) if r > 0 else None
    rvol_multi = {str(lb): rv(lb) for lb in _LOOKBACKS}
    rvol = rvol_multi[str(primary_lb)]

    # how many days the latest bar's PRIMARY reference actually used (honest on short intraday history)
    if slots is not None:
        last_slot = slots[-1]
        avail = sum(1 for s in slots[:-1] if s == last_slot)
        used = min(primary_lb, avail)
        rvol_method = "time-of-day"
        rvol_basis = f"the same {interval} time-of-day slot over the last {used} day{'s' if used != 1 else ''}"
    else:
        rvol_method = "trailing"
        rvol_basis = f"the prior {min(primary_lb, n - 1)} bars"

    avg_ref = float(np.mean(v[-primary_lb:])) if n >= primary_lb else float(np.mean(v))

    # dry-up vs expansion: last 5 bars vs the prior 15 (bar-based; a recent-contraction measure)
    dryup_ratio = None
    if n >= 20:
        recent = float(np.mean(v[-5:])); base = float(np.mean(v[-20:-5]))
        dryup_ratio = (recent / base) if base > 0 else None
    dry_state = ("drying up" if (dryup_ratio is not None and dryup_ratio <= 0.8)
                 else "expanding" if (dryup_ratio is not None and dryup_ratio >= 1.3) else "steady")

    # up vs down volume over the last 20 bars
    w = min(20, n)
    up_v = float(np.sum(v[-w:][c[-w:] >= o[-w:]])); dn_v = float(np.sum(v[-w:][c[-w:] < o[-w:]]))
    tot = up_v + dn_v
    up_pct = _r(up_v / tot * 100, 0) if tot > 0 else None
    dn_pct = _r(dn_v / tot * 100, 0) if tot > 0 else None

    # CVD (cumulative volume-delta proxy) + OBV over full history
    rng = np.where((h - l) > 0, h - l, np.nan)
    cvd = np.cumsum(np.nan_to_num(v * (2.0 * c - h - l) / rng))
    obv = np.zeros(n)
    for i in range(1, n):
        obv[i] = obv[i - 1] + (v[i] if c[i] > c[i - 1] else -v[i] if c[i] < c[i - 1] else 0.0)
    cvd_trend = _slope_state(cvd - cvd.min() + 1.0, 5, 10)
    obv_trend = _slope_state(obv - obv.min() + 1.0, 5, 10)
    price_trend = _slope_state(c, 5, 10)
    divergence = ("bearish" if price_trend == "rising" and obv_trend == "falling"
                  else "bullish" if price_trend == "falling" and obv_trend == "rising" else "aligned")
    vol_trend = _slope_state(primary, 5, 10)

    # ── chart window: return the recent bars, references computed on the FULL history above ──
    w0 = max(0, n - _CHART_BARS)
    bars = [{"t": ts[i], "o": _r(o[i]), "h": _r(h[i]), "l": _r(l[i]), "c": _r(c[i]), "v": int(v[i])} for i in range(w0, n)]
    vma_win = [_r(primary[i], 0) for i in range(w0, n)]
    cvd_win = [_r(cvd[i], 0) for i in range(w0, n)]
    obv_win = [_r(obv[i], 0) for i in range(w0, n)]
    climax = []
    for i in range(max(w0, n - 40), n):
        if primary[i] > 0 and v[i] >= 2.0 * primary[i]:
            climax.append({"index": i - w0, "t": ts[i], "ratio": _r(v[i] / primary[i], 1),
                           "direction": "up" if c[i] >= o[i] else "down", "price": _r(c[i])})
    climax = climax[-6:]

    # ── plain-English read ──
    read: list[str] = []
    if rvol is not None:
        if rvol >= 2:
            read.append(f"Latest bar is a {rvol:.1f}× volume SPIKE vs {rvol_basis} — a real move, not noise.")
        elif rvol <= 0.6:
            read.append(f"Latest bar is quiet ({rvol:.1f}× {rvol_basis}) — little participation.")
    if rvol_multi["10"] is not None and rvol_multi["50"] is not None:
        read.append(f"RVOL 10/20/50 = {rvol_multi['10']}× / {rvol_multi['20']}× / {rvol_multi['50']}× "
                    + (f"(vs {interval} time-of-day baselines)." if intraday else "(vs trailing 10/20/50-bar averages)."))
    if dryup_ratio is not None and dry_state == "drying up":
        read.append(f"Volume is DRYING UP — the last 5 bars are {dryup_ratio:.2f}× the prior 15 bars; sellers exhausting, constructive into a tight range — watch for an expansion breakout.")
    elif dry_state == "expanding":
        read.append(f"Volume is EXPANDING — the last 5 bars are {dryup_ratio:.2f}× the prior 15 bars; participation coming in, confirms the current move.")
    if climax:
        last = climax[-1]
        base_txt = f"its {interval} time-of-day average" if intraday else f"its {primary_lb}-bar average"
        read.append(f"Climax bar {last['ratio']}× {base_txt} volume, on a {last['direction']} bar at ${last['price']} — {'possible blow-off / breakout' if last['direction'] == 'up' else 'possible capitulation / exhaustion'}.")
    if up_pct is not None:
        read.append(f"Over the last 20 bars: {int(up_pct)}% up-volume / {int(dn_pct)}% down-volume — "
                    + ("buyers in control." if up_pct >= 58 else "sellers in control." if dn_pct >= 58 else "balanced."))
    if divergence == "bearish":
        read.append("Bearish divergence: price is rising but OBV isn't confirming — distribution, momentum may be fading.")
    elif divergence == "bullish":
        read.append("Bullish divergence: price is falling but OBV is rising — quiet accumulation / absorption.")
    if not read:
        read.append("Volume is unremarkable here — near its average with no spike, dry-up or divergence.")

    return {
        "interval": interval, "price": _r(c[-1]), "as_of": _now_str(),
        "bars": bars, "volume_ma": vma_win, "cvd": cvd_win, "obv": obv_win,
        "climax_bars": climax,
        "metrics": {
            "avg_volume": _r(avg_ref, 0),
            "rvol": rvol,
            "rvol_multi": rvol_multi,
            "primary_lookback": primary_lb,
            "rvol_method": rvol_method,
            "rvol_basis": rvol_basis,
            "volume_trend": vol_trend,
            "dryup_ratio": _r(dryup_ratio, 2), "dryup_state": dry_state,
            "up_volume_pct": up_pct, "down_volume_pct": dn_pct,
            "cvd_trend": cvd_trend, "obv_trend": obv_trend, "price_trend": price_trend, "divergence": divergence,
        },
        "read": read,
        "meta": {"bars": n, "shown": len(bars), "rvol_method": rvol_method,
                 "note": "Deterministic volume read from OHLCV; intraday RVOL/climax use a time-of-day baseline. Educational, not advice."},
    }
