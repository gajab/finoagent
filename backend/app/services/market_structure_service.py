"""Multi-timeframe market structure, liquidity pools & mitigation confluence (SMC).

Extends the single-timeframe institutional read into the cross-timeframe picture a
smart-money trader actually plans from:

  • Pivot Mechanics (BOS / CHOCH) — swing highs/lows via ``scipy.signal.argrelextrema``,
    then a sequential break read across DAILY / 4-HOUR / 1-HOUR. A Break of Structure
    (BOS) = trend continuation; a Change of Character (CHOCH) = the first counter-trend
    break, an early-reversal tell.
  • Liquidity Pools (BSL / SSL) — resting stop clusters ABOVE swing highs (Buy-Side
    Liquidity) and BELOW swing lows (Sell-Side Liquidity). We surface the UNSWEPT pools
    (pending draws-on-liquidity) and flag equal-highs/lows as engineered liquidity.
  • Mitigation Confluence — unmitigated Order Blocks / Fair-Value Gaps stacked across
    timeframes (e.g. an unmitigated 4H bullish FVG sitting inside a Daily demand OB =
    a high-probability zone).

Reuses the proven zone maths from ``institutional_ta_service`` (``_order_blocks``,
``_fair_value_gaps``, ``_atr``) and the fetch/format helpers from
``microstructure_service`` rather than reimplementing them. Pure numpy/scipy, best-effort:
degenerate input yields ``None``/empty, never raises.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import argrelextrema

from .institutional_ta_service import _f, _atr, _order_blocks, _fair_value_gaps
from .microstructure_service import (
    _safe_history, _safe_history_days, _price_series, _now_str, _r,
)

# Timeframe display + structural weight (higher timeframe dominates the bias).
_TF_WEIGHT = {"daily": 3, "h4": 2, "h1": 1}


# ---------------------------------------------------------------------------
# Pivots (scipy local extrema)
# ---------------------------------------------------------------------------

def _pivots(highs, lows, order: int):
    """Swing highs/lows as local extrema with ``order`` bars on each side.
    Returns ([(idx, price)], [(idx, price)]) sorted by index."""
    h, l = _f(highs), _f(lows)
    n = len(h)
    if n < 2 * order + 1:
        return [], []
    hi = argrelextrema(h, np.greater, order=order)[0]
    lo = argrelextrema(l, np.less, order=order)[0]
    sh = [(int(i), float(h[i])) for i in hi]
    sl = [(int(i), float(l[i])) for i in lo]
    return sh, sl


# ---------------------------------------------------------------------------
# BOS / CHOCH sequence
# ---------------------------------------------------------------------------

def _structure(closes, sh, sl, order: int, keep: int = 6) -> dict:
    """Walk closes; a close beyond the last *confirmed* swing high/low is a break.
    Continuation (same side as trend) = BOS; the first counter-trend break = CHOCH.
    A pivot only becomes a reference ``order`` bars after it prints (no look-ahead)."""
    c = _f(closes)
    n = len(c)
    highs, lows = sorted(sh), sorted(sl)
    events: list[dict] = []
    trend = "range"
    hp = lp = 0
    active_high = active_low = None
    ah_idx = al_idx = -1

    for i in range(n):
        while hp < len(highs) and highs[hp][0] + order <= i:      # confirmed pivot high
            active_high, ah_idx = highs[hp][1], highs[hp][0]; hp += 1
        while lp < len(lows) and lows[lp][0] + order <= i:
            active_low, al_idx = lows[lp][1], lows[lp][0]; lp += 1
        price = float(c[i])
        if active_high is not None and i > ah_idx and price > active_high:
            events.append({"type": "BOS" if trend == "up" else "CHOCH",
                           "direction": "bullish", "level": round(active_high, 2), "index": i})
            trend = "up"; active_high = None
        elif active_low is not None and i > al_idx and price < active_low:
            events.append({"type": "BOS" if trend == "down" else "CHOCH",
                           "direction": "bearish", "level": round(active_low, 2), "index": i})
            trend = "down"; active_low = None

    rsh = highs[-1][1] if highs else None
    rsl = lows[-1][1] if lows else None
    return {
        "trend": trend,
        "last_event": events[-1] if events else None,
        "events": events[-keep:],
        "recent_swing_high": round(rsh, 2) if rsh is not None else None,
        "recent_swing_low": round(rsl, 2) if rsl is not None else None,
    }


# ---------------------------------------------------------------------------
# Liquidity pools (BSL / SSL) — unswept resting liquidity
# ---------------------------------------------------------------------------

def _liquidity_pools(sh, sl, closes, atr: float, price: float, keep: int = 6) -> list[dict]:
    c = _f(closes)
    n = len(c)
    if not price:
        return []
    tol = max(0.0012 * price, 0.25 * atr)      # "equal" tolerance for engineered liquidity

    def build(pivots, kind: str, swept_if) -> list[dict]:
        raw = []
        for idx, lvl in pivots:
            swept = bool(np.any(swept_if(c[idx + 1:], lvl))) if idx + 1 < n else False
            raw.append({"type": kind, "price": round(float(lvl), 2), "swept": swept, "index": int(idx)})
        for p in raw:                          # equal highs/lows → stronger pool
            p["equal_count"] = sum(1 for q in raw if abs(q["price"] - p["price"]) <= tol)
        return raw

    pools = (build(sh, "BSL", lambda arr, lvl: arr > lvl)      # BSL swept once a close exceeds the high
             + build(sl, "SSL", lambda arr, lvl: arr < lvl))
    unswept = [p for p in pools if not p["swept"]]
    for p in unswept:
        p["side"] = "above" if p["price"] >= price else "below"
        p["distance_pct"] = round((p["price"] - price) / price * 100, 2)
        p["strength"] = "strong" if p["equal_count"] >= 2 else "normal"

    unswept.sort(key=lambda p: abs(p["distance_pct"]))
    out: list[dict] = []
    for p in unswept:
        if any(abs(p["price"] - o["price"]) <= tol for o in out):
            continue
        out.append(p)
        if len(out) >= keep:
            break
    return out


# ---------------------------------------------------------------------------
# Cross-timeframe mitigation confluence
# ---------------------------------------------------------------------------

_KIND_LABEL = {"OB": "Order Block", "FVG": "Fair-Value Gap"}
_TF_LABEL = {"daily": "Daily", "h4": "4H", "h1": "1H"}


def _conf_text(stack: list[dict], bias: str, lo: float, hi: float) -> str:
    parts = [f"{_TF_LABEL.get(z['tf'], z['tf'])} {'demand' if bias == 'bullish' else 'supply'} "
             f"{_KIND_LABEL.get(z['kind'], z['kind'])}" for z in stack]
    return (f"{bias.capitalize()} confluence ${lo}–${hi}: " + " + ".join(parts) +
            f" — unmitigated across timeframes, a high-probability {'long' if bias == 'bullish' else 'short'} zone.")


def _confluence(zones: list[dict], keep: int = 4) -> list[dict]:
    """Stack unmitigated same-bias zones whose price ranges overlap across DIFFERENT
    timeframes. ``zones`` carry {tf, tf_w, kind, type, top, bottom}."""
    def overlap(a, b) -> bool:
        return a["bottom"] <= b["top"] and b["bottom"] <= a["top"]

    out: list[dict] = []
    for bias in ("bullish", "bearish"):
        zs = [z for z in zones if z["type"] == bias]
        used: set[int] = set()
        for i, za in enumerate(zs):
            if i in used:
                continue
            stack = [za]
            tfs_in = {za["tf"]}
            for j in range(i + 1, len(zs)):
                if j in used or zs[j]["tf"] in tfs_in:   # at most one zone per timeframe
                    continue
                if any(overlap(zs[j], s) for s in stack):
                    stack.append(zs[j]); tfs_in.add(zs[j]["tf"]); used.add(j)
            if len(tfs_in) >= 2:                         # must span ≥2 timeframes
                lo = round(max(z["bottom"] for z in stack), 2)
                hi = round(min(z["top"] for z in stack), 2)
                if lo > hi:
                    lo, hi = hi, lo
                out.append({
                    "bias": bias,
                    "timeframes": sorted({z["tf"] for z in stack}, key=lambda t: -_TF_WEIGHT.get(t, 0)),
                    "zone": [lo, hi],
                    "score": sum(z["tf_w"] for z in stack),
                    "components": [{"tf": z["tf"], "kind": z["kind"]} for z in stack],
                    "summary": _conf_text(stack, bias, lo, hi),
                })
    out.sort(key=lambda x: -x["score"])
    return out[:keep]


# ---------------------------------------------------------------------------
# Per-timeframe block
# ---------------------------------------------------------------------------

def _tf_structure(df, key: str, label: str, order: int, price: float) -> tuple[dict | None, list[dict]]:
    """Returns (block, unmitigated_zones) for one timeframe."""
    if df is None or getattr(df, "empty", True) or len(df) < max(2 * order + 5, 20):
        return None, []
    o, h, l, c = df["Open"].values, df["High"].values, df["Low"].values, df["Close"].values
    atr = _atr(h, l, c)
    sh, sl = _pivots(h, l, order)
    struct = _structure(c, sh, sl, order)
    obs = _order_blocks(o, h, l, c, atr)
    fvgs = _fair_value_gaps(h, l)
    pools = _liquidity_pools(sh, sl, c, atr, price)

    zones: list[dict] = []
    w = _TF_WEIGHT.get(key, 1)
    for ob in obs:
        if not ob["mitigated"]:
            zones.append({"tf": key, "tf_w": w, "kind": "OB", "type": ob["type"],
                          "top": ob["top"], "bottom": ob["bottom"]})
    for fv in fvgs:
        if not fv["filled"]:
            zones.append({"tf": key, "tf_w": w, "kind": "FVG", "type": fv["type"],
                          "top": fv["top"], "bottom": fv["bottom"]})

    block = {
        "label": label, "trend": struct["trend"], "atr": _r(atr),
        "structure": struct, "order_blocks": obs, "fair_value_gaps": fvgs,
        "liquidity_pools": pools,
    }
    return block, zones


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def _resample(df, rule: str = "4h"):
    """Aggregate an intraday frame to a coarser bar (yfinance has no native 4H)."""
    if df is None or getattr(df, "empty", True):
        return None
    try:
        agg = df.resample(rule).agg({"Open": "first", "High": "max", "Low": "min",
                                     "Close": "last", "Volume": "sum"}).dropna(subset=["Close"])
        return agg if not agg.empty else None
    except Exception:
        return None


def compute_market_structure(stock) -> dict | None:
    """Full multi-timeframe structure/liquidity/confluence read. ``None`` only if no
    usable data exists; every section is otherwise best-effort."""
    try:
        daily = _safe_history(stock, "1y", "1d")
        h1 = _safe_history_days(stock, 58, "1h")
        h4 = _resample(h1, "4h")

        price = None
        for df in (h1, h4, daily):
            if df is not None and not df.empty:
                price = float(df["Close"].values[-1])
                break
        if price is None:
            return None

        timeframes: dict = {}
        all_zones: list[dict] = []
        for key, label, df, order in (("daily", "Daily", daily, 5),
                                      ("h4", "4-Hour", h4, 4),
                                      ("h1", "1-Hour", h1, 6)):
            block, zones = _tf_structure(df, key, label, order, price)
            timeframes[key] = block
            all_zones.extend(zones)

        return {
            "price": _r(price),
            "as_of": _now_str(),
            "bias": _mtf_bias(timeframes),
            "timeframes": timeframes,
            "confluence": _confluence(all_zones),
            "price_series": _price_series(daily if daily is not None else h1),
        }
    except Exception:  # noqa: BLE001 — never break the technical response
        return None


def _mtf_bias(timeframes: dict) -> dict:
    """Weighted alignment of the per-timeframe trends (Daily dominates)."""
    score = 0
    votes = []
    for key, block in timeframes.items():
        if not block:
            continue
        t = block["trend"]
        votes.append((key, t))
        score += _TF_WEIGHT.get(key, 1) * (1 if t == "up" else -1 if t == "down" else 0)
    trends = {t for _, t in votes if t in ("up", "down")}
    aligned = len(votes) >= 2 and len(trends) == 1 and "range" not in {t for _, t in votes}
    overall = "bullish" if score > 0 else "bearish" if score < 0 else "mixed"
    note = ("All timeframes aligned — trade with the trend." if aligned
            else "Timeframes disagree — expect chop / wait for alignment." if overall == "mixed"
            else f"Net {overall}, but not fully aligned across timeframes.")
    return {"overall": overall, "aligned": bool(aligned), "score": int(score), "note": note}
