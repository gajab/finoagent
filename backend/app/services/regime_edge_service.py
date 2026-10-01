"""Regime-conditional edge stats — does a signal's historical edge depend on the market regime?

The desk question this answers: *"A 20-day breakout — does it actually work on THIS name, and does
that depend on whether we're trending or chopping?"* Retail tools fire a signal and stop there; an
institution first asks for the historical edge, the sample size, and whether it survives the regime.

For each canonical, deterministic TA signal (momentum breakout, breakdown, mean-reversion dip, trend
pullback) we:
  1. find every historical occurrence on the name (point-in-time, no look-ahead),
  2. label the REGIME in force at that bar — Trending / Transitional / Choppy — from the same
     Kaufman Efficiency Ratio that ``regime_service`` uses (high ER = clean trend, low ER = chop),
  3. evaluate the trade with a TRIPLE BARRIER (target/stop in ATR units within a horizon), and
  4. bucket the R-multiples by regime → win rate, expectancy (R), profit factor, sample size and a
     t-stat, so "breakout wins in trending regimes, loses in chop" is a measured claim, not a vibe.

**Why ER terciles, not the fixed Hurst thresholds:** R/S Hurst on daily equity returns almost never
prints below ~0.48, so ``regime_service``'s absolute "mean-reverting" bucket is empty for nearly
every name and the trend-vs-chop contrast can't form. We instead split each name's occurrences by
ITS OWN ER terciles — guaranteeing balanced, comparable-power buckets — and report the average Hurst
& ER of each bucket so "Trending" and "Choppy" are verifiable, not just labels. The absolute
regime_service classification is still surfaced for continuity with the Regime panel.

Everything is deterministic (no LLM) and reconciles (per-regime counts sum to the overall count).
Lazy + cached; pure numpy/pandas. yfinance daily OHLCV is enough — no paid feed required.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .microstructure_service import _safe_history, _now_str, _r, append_live_daily_bar
from .regime_service import _hurst, _efficiency_ratio, _classify

_REGIMES = ("trending", "transitional", "choppy")
_REGIME_LABEL = {"trending": "Trending", "transitional": "Transitional", "choppy": "Choppy"}
_HURST_WINDOW = 252           # trailing bars for the (display-only) Hurst at each bar; matches regime_service daily
_MIN_HURST_BARS = 150         # Hurst needs ~120 returns; below this Hurst is left blank (bucketing still works)
_ER_WINDOW = 20               # Kaufman Efficiency-Ratio window — the regime conditioner (matches regime_service)
_ATR_PERIOD = 14
_MIN_BUCKET_N = 8             # below this a regime bucket's stats are shown but flagged low-confidence


# ---------------------------------------------------------------------------
# indicators (local, deterministic — no external indicator coupling)
# ---------------------------------------------------------------------------

def _sma(a: np.ndarray, w: int) -> np.ndarray:
    return pd.Series(a).rolling(w).mean().to_numpy()


def _ema(a: np.ndarray, span: int) -> np.ndarray:
    return pd.Series(a).ewm(span=span, adjust=False).mean().to_numpy()


def _rsi(closes: np.ndarray, period: int) -> np.ndarray:
    """Wilder RSI (Connors-compatible). Flat series → 50 (no information), not a spurious extreme."""
    d = np.diff(closes, prepend=closes[0])
    up = np.where(d > 0, d, 0.0)
    dn = np.where(d < 0, -d, 0.0)
    ru = pd.Series(up).ewm(alpha=1.0 / period, adjust=False).mean().to_numpy()
    rd = pd.Series(dn).ewm(alpha=1.0 / period, adjust=False).mean().to_numpy()
    rsi = np.full(len(closes), 50.0)
    both = (ru + rd) > 0
    rsi[both] = 100.0 - 100.0 / (1.0 + np.divide(ru, rd, out=np.full_like(ru, np.inf), where=rd > 0))[both]
    rsi[(rd <= 0) & (ru > 0)] = 100.0
    return rsi


def _atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, period: int = _ATR_PERIOD) -> np.ndarray:
    n = len(c)
    tr = np.empty(n)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    return pd.Series(tr).rolling(period).mean().to_numpy()


def _er_series(c: np.ndarray, window: int = _ER_WINDOW) -> np.ndarray:
    """Rolling Kaufman Efficiency Ratio = |net change| / Σ|bar-to-bar change| over the window (0..1).
    Vectorised over the whole series — this is the cheap, responsive regime conditioner."""
    absdiff = np.abs(np.diff(c, prepend=c[0]))
    path = pd.Series(absdiff).rolling(window).sum().to_numpy()
    change = np.abs(c - pd.Series(c).shift(window).to_numpy())
    with np.errstate(invalid="ignore", divide="ignore"):
        er = np.where(path > 0, np.minimum(1.0, change / path), np.nan)
    return er


# ---------------------------------------------------------------------------
# signal library — each returns a boolean "condition true here" array (point-in-time)
# ---------------------------------------------------------------------------

def _signal_conditions(o, h, l, c, v) -> dict:
    """Canonical, deterministic entry conditions. Direction is fixed per signal so the regime
    dependence is interpretable (a breakout SHOULD favour trending; a dip fade SHOULD favour chop)."""
    n = len(c)
    sma50, sma200 = _sma(c, 50), _sma(c, 200)
    ema20 = _ema(c, 20)
    rsi2 = _rsi(c, 2)
    roll_hi = pd.Series(h).rolling(20).max().shift(1).to_numpy()   # prior-20 high, excludes current bar
    roll_lo = pd.Series(l).rolling(20).min().shift(1).to_numpy()

    breakout = c > roll_hi                                          # close above the 20-day high
    breakdown = c < roll_lo                                         # close below the 20-day low
    mean_rev = (rsi2 < 10.0) & (c > sma200)                         # oversold dip inside an uptrend (Connors-style)
    uptrend = (c > sma50) & (sma50 > sma200)
    pullback = uptrend & (l <= ema20) & (c > ema20)                # tagged & reclaimed the 20-EMA in an uptrend

    return {
        "breakout": {"cond": breakout, "dir": "long", "label": "20-Day Breakout",
                     "thesis": "Momentum — close breaks the prior 20-day high. Expected to reward trend, bleed in chop.",
                     "warmup": 21},
        "breakdown": {"cond": breakdown, "dir": "short", "label": "20-Day Breakdown",
                      "thesis": "Momentum (short) — close breaks the prior 20-day low. Expected to reward down-trends.",
                      "warmup": 21},
        "mean_rev_dip": {"cond": mean_rev, "dir": "long", "label": "Mean-Reversion Dip",
                         "thesis": "Buy an RSI(2) oversold dip above the 200-DMA. Expected to reward mean-reverting/range tape, fail in a slide.",
                         "warmup": 205},
        "trend_pullback": {"cond": pullback, "dir": "long", "label": "Trend Pullback (20-EMA)",
                           "thesis": "Buy a reclaim of the 20-EMA inside an uptrend. Expected to reward trending regimes.",
                           "warmup": 205},
    }


def _occurrences(cond: np.ndarray, warmup: int, last_ok: int, cooldown: int) -> list[int]:
    """First-trigger indices (rising edge) with a cooldown, so a signal that stays true for days
    counts once, not every bar (which would over-weight persistent conditions)."""
    idx: list[int] = []
    last = -10 ** 9
    for i in range(max(warmup, 1), last_ok + 1):
        if bool(cond[i]) and not bool(cond[i - 1]) and (i - last) >= cooldown:
            idx.append(i)
            last = i
    return idx


# ---------------------------------------------------------------------------
# outcome (triple barrier) + statistics
# ---------------------------------------------------------------------------

def _outcome(h, l, c, i: int, atr_i: float, direction: str, kt: float, ks: float, horizon: int, n: int):
    """Triple-barrier R-multiple + forward return %. Stop is checked before target within a bar
    (pessimistic, the standard convention). Returns (R, fwd_pct) or None if risk is degenerate."""
    entry = c[i]
    risk = ks * atr_i
    if not np.isfinite(risk) or risk <= 0:
        return None
    if direction == "long":
        tgt, stp = entry + kt * atr_i, entry - ks * atr_i
    else:
        tgt, stp = entry - kt * atr_i, entry + ks * atr_i
    end = min(i + horizon, n - 1)
    r = None
    for j in range(i + 1, end + 1):
        if direction == "long":
            if l[j] <= stp:
                r = -1.0; break
            if h[j] >= tgt:
                r = kt / ks; break
        else:
            if h[j] >= stp:
                r = -1.0; break
            if l[j] <= tgt:
                r = kt / ks; break
    if r is None:                                    # timed out — mark to the exit close in R units
        exitp = c[end]
        r = ((exitp - entry) if direction == "long" else (entry - exitp)) / risk
    fwd = ((c[end] - entry) if direction == "long" else (entry - c[end])) / entry * 100.0
    return float(r), float(fwd)


def _stats(rs: list[float], fwds: list[float],
           hursts: list[float] | None = None, ers: list[float] | None = None) -> dict | None:
    if not rs:
        return None
    arr = np.asarray(rs, dtype=float)
    n = len(arr)
    wins, losses = arr[arr > 0], arr[arr <= 0]
    gp, gl = float(wins.sum()), float(-losses.sum())
    exp = float(arr.mean())
    sd = float(arr.std(ddof=1)) if n > 1 else 0.0
    t = exp / (sd / np.sqrt(n)) if sd > 0 else None
    hv = [x for x in (hursts or []) if x is not None and np.isfinite(x)]
    ev = [x for x in (ers or []) if x is not None and np.isfinite(x)]
    return {
        "n": n,
        "win_rate": _r(len(wins) / n * 100, 0),
        "expectancy": _r(exp, 2),                          # in R (risk units)
        "avg_win": _r(float(wins.mean()) if len(wins) else 0.0, 2),
        "avg_loss": _r(float(losses.mean()) if len(losses) else 0.0, 2),
        "profit_factor": _r(gp / gl, 2) if gl > 0 else None,
        "t_stat": _r(t, 2),
        "avg_fwd_return": _r(float(np.mean(fwds)), 2),     # % over the horizon
        "avg_hurst": _r(float(np.mean(hv)), 3) if hv else None,   # verifies the bucket's label
        "avg_er": _r(float(np.mean(ev)), 3) if ev else None,
        "low_confidence": n < _MIN_BUCKET_N,
    }


def _edge(stats: dict | None) -> str:
    """Verdict for a signal within a given regime."""
    if not stats or stats["n"] < _MIN_BUCKET_N:
        return "insufficient"
    e = stats["expectancy"] or 0.0
    t = stats["t_stat"]
    if e >= 0.15 and (t is None or t >= 1.3):
        return "confirmed"
    if e <= -0.10:
        return "negative"
    return "weak"


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def compute_regime_edge(stock, history: str = "5y", horizon: int = 10,
                        k_target: float = 2.0, k_stop: float = 1.0) -> dict | None:
    """Regime-conditional edge for the canonical signal library on one name. ``None`` when there
    isn't enough history to label regimes and evaluate trades."""
    try:
        horizon = int(horizon) if 3 <= int(horizon) <= 40 else 10
    except (TypeError, ValueError):
        horizon = 10
    df = _safe_history(stock, history, "1d")
    df = append_live_daily_bar(stock, df)
    if df is None or getattr(df, "empty", True) or len(df) < 260:
        return None

    o = df["Open"].to_numpy(float); h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float); c = df["Close"].to_numpy(float)
    v = df["Volume"].to_numpy(float)
    n = len(c)
    atr = _atr(h, l, c)
    er = _er_series(c)                    # the regime conditioner (cheap, whole-series)
    sigs = _signal_conditions(o, h, l, c, v)
    last_ok = n - 1 - horizon            # need the full horizon of forward bars to score a trade
    if last_ok <= 210:
        return None

    # Regime = tercile of THIS name's ER. Cut points from the evaluable window so buckets are
    # balanced and comparable-power (fixed Hurst thresholds leave "chop" empty for equities).
    er_valid = er[210:last_ok + 1]
    er_valid = er_valid[np.isfinite(er_valid)]
    if len(er_valid) < 30:
        return None
    q_lo, q_hi = (float(x) for x in np.percentile(er_valid, [33.334, 66.667]))

    def regime_at(i: int) -> str | None:
        e = er[i]
        if not np.isfinite(e):
            return None
        return "trending" if e >= q_hi else "choppy" if e < q_lo else "transitional"

    # Hurst is display-only (verifies the ER buckets), computed at occurrence bars, memoised.
    hurst_cache: dict[int, float | None] = {}

    def hurst_at(i: int) -> float | None:
        if i in hurst_cache:
            return hurst_cache[i]
        lo = max(0, i - _HURST_WINDOW + 1)
        seg = c[lo:i + 1]
        hv = _hurst(seg) if len(seg) >= _MIN_HURST_BARS else None
        hurst_cache[i] = hv
        return hv

    signals_out: list[dict] = []
    for key, spec in sigs.items():
        warmup = max(spec["warmup"], _ATR_PERIOD + 1, _ER_WINDOW + 1)
        occ = _occurrences(spec["cond"], warmup, last_ok, cooldown=horizon)
        by_regime: dict[str, dict] = {r: {"rs": [], "fwds": [], "hursts": [], "ers": []} for r in _REGIMES}
        all_rs: list[float] = []
        all_fwds: list[float] = []
        for i in occ:
            reg = regime_at(i)
            if reg is None or not np.isfinite(atr[i]):
                continue
            res = _outcome(h, l, c, i, float(atr[i]), spec["dir"], k_target, k_stop, horizon, n)
            if res is None:
                continue
            r, fwd = res
            all_rs.append(r); all_fwds.append(fwd)
            b = by_regime[reg]
            b["rs"].append(r); b["fwds"].append(fwd)
            b["hursts"].append(hurst_at(i)); b["ers"].append(float(er[i]))

        regime_stats = {r: _stats(b["rs"], b["fwds"], b["hursts"], b["ers"]) for r, b in by_regime.items()}
        overall = _stats(all_rs, all_fwds)
        firing_now = bool(spec["cond"][n - 1]) and not bool(spec["cond"][n - 2])
        signals_out.append({
            "key": key, "label": spec["label"], "direction": spec["dir"], "thesis": spec["thesis"],
            "overall": overall,
            "by_regime": regime_stats,
            "edge_by_regime": {r: _edge(regime_stats[r]) for r in _REGIMES},
            "firing_now": firing_now,
        })

    # current regime = which ER tercile the latest bar sits in; Hurst + absolute regime_service label
    # surfaced alongside for continuity with the Regime panel.
    cur = regime_at(n - 1) or "transitional"
    ch = hurst_at(n - 1)
    ce = float(er[n - 1]) if np.isfinite(er[n - 1]) else None
    absolute = _classify(ch, _efficiency_ratio(c[max(0, n - _HURST_WINDOW):]))
    current = {
        "regime": cur, "label": _REGIME_LABEL[cur],
        "hurst": _r(ch, 3) if ch is not None else None,
        "efficiency_ratio": _r(ce, 3) if ce is not None else None,
        "er_terciles": {"low": _r(q_lo, 3), "high": _r(q_hi, 3)},
        "absolute_regime": absolute["regime"], "confidence": absolute["confidence"],
    }

    # attach the "live" bucket + verdict to each signal, and rank for the read
    for s in signals_out:
        s["current"] = s["by_regime"].get(cur)
        s["current_edge"] = s["edge_by_regime"].get(cur, "insufficient")

    read = _build_read(signals_out, current, horizon, k_target, k_stop)

    return {
        "as_of": _now_str(), "price": _r(c[-1]),
        "history": history, "horizon": horizon,
        "barrier": {"target_atr": k_target, "stop_atr": k_stop,
                    "reward_risk": _r(k_target / k_stop, 2)},
        "current_regime": current,
        "regimes": list(_REGIMES),
        "regime_labels": _REGIME_LABEL,
        "signals": signals_out,
        "read": read,
        "meta": {
            "bars": n, "evaluated_through": last_ok,
            "note": ("Regime-conditional edge from deterministic triple-barrier backtests on this "
                     "name's own history. R = multiple of the ATR-based risk. Educational, not advice; "
                     "in-sample and no costs/slippage — judge on sample size (n) and t-stat, not a single number."),
        },
    }


def _fmt(stats: dict | None) -> str:
    if not stats:
        return "no signals"
    e = stats["expectancy"]
    sign = "+" if (e or 0) > 0 else ""
    return f"{stats['win_rate']:.0f}% win, {sign}{e}R (n={stats['n']})"


def _build_read(signals: list[dict], current: dict, horizon: int, kt: float, ks: float) -> list[str]:
    read: list[str] = []
    cur = current["regime"]
    hurst_txt = f"Hurst {current['hurst']}" if current["hurst"] is not None else "Hurst n/a"
    er_txt = f"ER {current['efficiency_ratio']}" if current["efficiency_ratio"] is not None else "ER n/a"
    read.append(f"Current regime: {current['label'].upper()} ({hurst_txt}, {er_txt}, {current['confidence']} confidence). "
                f"Below is each signal's measured edge, split by the regime in force when it fired "
                f"(triple-barrier {kt:g}R target / {ks:g}R stop, {horizon}-bar horizon).")

    confirmed = [s for s in signals if s["current_edge"] == "confirmed"]
    negative = [s for s in signals if s["current_edge"] == "negative"]
    confirmed.sort(key=lambda s: (s["current"] or {}).get("expectancy") or 0, reverse=True)
    negative.sort(key=lambda s: (s["current"] or {}).get("expectancy") or 0)

    if confirmed:
        best = confirmed[0]
        read.append(f"Edge CONFIRMED in this regime: {best['label']} — {_fmt(best['current'])} in "
                    f"{current['label'].lower()} regimes."
                    + (f" Also holding up: {', '.join(s['label'] for s in confirmed[1:])}." if len(confirmed) > 1 else ""))
    if negative:
        worst = negative[0]
        read.append(f"AVOID in this regime: {worst['label']} — {_fmt(worst['current'])}; this setup has "
                    f"historically lost money on this name while it's {current['label'].lower()}.")

    # the headline contrast — a signal that flips sign across regimes is the whole point
    for s in signals:
        stats = {r: s["by_regime"][r] for r in s["by_regime"]}
        trend, chop = stats.get("trending"), stats.get("choppy")
        if trend and chop and trend["n"] >= _MIN_BUCKET_N and chop["n"] >= _MIN_BUCKET_N:
            et, ec = trend["expectancy"] or 0, chop["expectancy"] or 0
            if (et > 0) != (ec > 0) and abs(et - ec) >= 0.3:
                better, worse = ("trending", "chop") if et > ec else ("chop", "trending")
                read.append(f"{s['label']} is regime-dependent: {_fmt(trend)} when trending vs "
                            f"{_fmt(chop)} when mean-reverting — take it in {better}, skip it in {worse}.")
                break

    firing = [s for s in signals if s["firing_now"]]
    if firing:
        for s in firing:
            verdict = {"confirmed": "and its edge is confirmed here — actionable",
                       "weak": "but its edge here is weak — size down",
                       "negative": "and it has NO edge in this regime — likely a trap",
                       "insufficient": "but there's too little history to trust it"}[s["current_edge"]]
            read.append(f"⚡ {s['label']} is FIRING right now — {verdict} ({_fmt(s['current'])}).")

    if len(read) == 1:
        read.append("No signal shows a statistically meaningful edge in the current regime on this "
                    "name (small samples or flat expectancy) — treat setups here with extra caution.")
    return read
