"""Institutional market-structure & order-flow analytics.

Institutional quants distrust hand-drawn trendlines and chart patterns; they model
*where volume actually traded* (Volume / Market Profile) and hunt the footprints of
large orders (Smart-Money Concepts). This module turns raw OHLCV into those hard
numbers and then **distills them into a plain-English market STATE plus a matching
options-income playbook** so a retail user can act on them:

  • Volume Profile   — Point of Control (POC) and the 70% Value Area (VAH/VAL).
  • Order Blocks     — the origin candle of a large institutional move (demand/supply).
  • Fair Value Gaps  — 3-candle price imbalances the market tends to revisit ("fill").
  • Liquidity Sweeps — stop-hunts: a wick past a prior swing that snaps back.
  • Displacement     — outsized, high-conviction candles (institutional intent).
  • Market Structure — swings, trend, Break of Structure (BOS) / Change of Character.
  • Regime           — the distillation: "Mean-Reversion / Trend-Following / Range",
                       a bias, a rationale, and which income structures are favored.

Pure numpy, best-effort: any degenerate input yields ``None``/empty fields, never raises.
The ``favored_income`` ids match the Derivative Income structures so the read is actionable.
"""

from __future__ import annotations

import numpy as np

# income structure ids (shared with derivative_income_service)
_CC = "covered_call"
_CSP = "cash_secured_put"
_PCS = "put_credit_spread"
_CCS = "call_credit_spread"
_COLLAR = "collar"
_IC = "iron_condor"
_JL = "jade_lizard"

_INCOME_LABEL = {
    _CC: "Covered Call", _CSP: "Cash-Secured Put", _PCS: "Put Credit Spread",
    _CCS: "Call Credit Spread", _COLLAR: "Collar", _IC: "Iron Condor", _JL: "Jade Lizard",
}


def _f(a) -> np.ndarray:
    return np.asarray(a, dtype=float)


def _atr(highs, lows, closes, period: int = 14) -> float:
    h, l, c = _f(highs), _f(lows), _f(closes)
    if len(c) < 2:
        return 0.0
    prev = c[:-1]
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - prev), np.abs(l[1:] - prev)))
    n = min(period, len(tr))
    return float(np.mean(tr[-n:])) if n else 0.0


def _swings(highs, lows, left: int = 2, right: int = 2):
    """Fractal swing highs/lows: a local extreme with `left`/`right` lower/higher neighbors.
    Returns ([(idx, price)], [(idx, price)]) for highs and lows."""
    h, l = _f(highs), _f(lows)
    n = len(h)
    sh, sl = [], []
    for i in range(left, n - right):
        if h[i] == max(h[i - left:i + right + 1]) and h[i] > h[i - 1]:
            sh.append((i, float(h[i])))
        if l[i] == min(l[i - left:i + right + 1]) and l[i] < l[i - 1]:
            sl.append((i, float(l[i])))
    return sh, sl


# ---------------------------------------------------------------------------
# Volume Profile / Market Profile
# ---------------------------------------------------------------------------

def _value_area(vol: np.ndarray, poc_idx: int, target: float = 0.70):
    total = float(vol.sum())
    if total <= 0:
        return poc_idx, poc_idx
    acc = float(vol[poc_idx])
    lo = hi = poc_idx
    n = len(vol)
    while acc < target * total and (lo > 0 or hi < n - 1):
        up = float(vol[hi + 1]) if hi < n - 1 else -1.0
        dn = float(vol[lo - 1]) if lo > 0 else -1.0
        if up >= dn:
            hi += 1; acc += up
        else:
            lo -= 1; acc += dn
    return lo, hi


def _volume_profile(highs, lows, closes, volumes, n_bins: int = 24) -> dict | None:
    h, l, v = _f(highs), _f(lows), _f(volumes)
    lo, hi = float(np.min(l)), float(np.max(h))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo or v.sum() <= 0:
        return None
    edges = np.linspace(lo, hi, n_bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    vol_at = np.zeros(n_bins)
    for hh, ll, vv in zip(h, l, v):
        if vv <= 0:
            continue
        lo_i = int(np.clip(np.searchsorted(edges, ll) - 1, 0, n_bins - 1))
        hi_i = int(np.clip(np.searchsorted(edges, hh) - 1, 0, n_bins - 1))
        span = hi_i - lo_i + 1
        vol_at[lo_i:hi_i + 1] += vv / span         # spread each candle across the levels it spanned
    total = float(vol_at.sum())
    poc_idx = int(np.argmax(vol_at))
    va_lo, va_hi = _value_area(vol_at, poc_idx, 0.70)
    bins = [{"price": round(float(c), 2), "volume": float(round(x)),
             "pct": round(x / total * 100, 1)} for c, x in zip(centers, vol_at)]
    return {
        "poc": round(float(centers[poc_idx]), 2),
        "vah": round(float(centers[va_hi]), 2),
        "val": round(float(centers[va_lo]), 2),
        "value_area_pct": 70,
        "bins": bins,
    }


# ---------------------------------------------------------------------------
# Smart-Money Concepts
# ---------------------------------------------------------------------------

def _order_blocks(o, h, l, c, atr: float, n_keep: int = 4) -> list[dict]:
    """The last opposite-color candle before an outsized (displacement) move — the
    footprint of institutional accumulation/distribution (a demand/supply zone)."""
    o, h, l, c = _f(o), _f(h), _f(l), _f(c)
    n = len(c)
    if atr <= 0 or n < 5:
        return []
    obs: list[dict] = []
    for i in range(3, n - 1):
        if (h[i] - l[i]) < 1.3 * atr:                 # candle i is the displacement
            continue
        up = c[i] > o[i]
        j = i - 1                                     # walk back to the last opposite candle
        while j >= 0 and ((c[j] > o[j]) == up):
            j -= 1
        if j < 0:
            continue
        top, bot = float(max(o[j], c[j])), float(min(o[j], c[j]))
        if top <= bot:
            top, bot = float(h[j]), float(l[j])
        mitigated = bool(np.any((l[i + 1:] <= top) & (h[i + 1:] >= bot)))
        obs.append({"type": "bullish" if up else "bearish",
                    "top": round(top, 2), "bottom": round(bot, 2),
                    "price": round((top + bot) / 2, 2),
                    "strength": round(float(h[i] - l[i]) / atr, 1),
                    "mitigated": mitigated, "index": int(j)})
    obs.sort(key=lambda x: (not x["mitigated"], x["index"]), reverse=True)   # recent, unmitigated first
    out, seen = [], []
    for ob in obs:
        if any(abs(ob["price"] - s) / max(s, 1e-9) < 0.012 for s in seen):
            continue
        seen.append(ob["price"]); out.append(ob)
        if len(out) >= n_keep:
            break
    return out


def _fair_value_gaps(h, l, n_keep: int = 4) -> list[dict]:
    """3-candle imbalances (gap between candle i-1 and i+1) that price tends to revisit."""
    h, l = _f(h), _f(l)
    n = len(h)
    fvgs: list[dict] = []
    for i in range(1, n - 1):
        if l[i + 1] > h[i - 1]:                        # bullish gap
            bot, top = float(h[i - 1]), float(l[i + 1])
            filled = bool(np.any(l[i + 2:] <= bot))
            fvgs.append({"type": "bullish", "bottom": round(bot, 2), "top": round(top, 2),
                         "mid": round((bot + top) / 2, 2), "filled": filled, "index": i})
        elif h[i + 1] < l[i - 1]:                      # bearish gap
            top, bot = float(l[i - 1]), float(h[i + 1])
            filled = bool(np.any(h[i + 2:] >= top))
            fvgs.append({"type": "bearish", "bottom": round(bot, 2), "top": round(top, 2),
                         "mid": round((bot + top) / 2, 2), "filled": filled, "index": i})
    fvgs.sort(key=lambda x: x["index"], reverse=True)
    unfilled = [f for f in fvgs if not f["filled"]]
    return (unfilled or fvgs)[:n_keep]


def _liquidity_sweeps(h, l, c, sh, sl, atr: float, n_keep: int = 3, lookback: int = 8) -> list[dict]:
    """Stop-hunts: within the last few bars, a wick pokes MEANINGFULLY (≥0.15×ATR) past
    the most recent prior swing then closes back inside — a genuine reversal tell (not
    routine chop). `bars_ago` lets the regime weight only very-fresh sweeps."""
    h, l, c = _f(h), _f(l), _f(c)
    n = len(c)
    poke = max(0.15 * atr, 1e-9)
    out: list[dict] = []
    for i in range(max(0, n - lookback), n):
        prior_sh = [lvl for idx, lvl in sh if idx < i - 1]
        if prior_sh:
            lvl = prior_sh[-1]
            if h[i] > lvl + poke and c[i] < lvl:
                out.append({"type": "buyside", "level": round(lvl, 2), "swept_to": round(float(h[i]), 2),
                            "reversed": True, "index": i, "bars_ago": n - 1 - i}); continue
        prior_sl = [lvl for idx, lvl in sl if idx < i - 1]
        if prior_sl:
            lvl = prior_sl[-1]
            if l[i] < lvl - poke and c[i] > lvl:
                out.append({"type": "sellside", "level": round(lvl, 2), "swept_to": round(float(l[i]), 2),
                            "reversed": True, "index": i, "bars_ago": n - 1 - i})
    out.sort(key=lambda x: x["index"], reverse=True)
    return out[:n_keep]


def _displacement(o, h, l, c, atr: float, n_keep: int = 3) -> list[dict]:
    """Recent outsized candles (range > 1.5×ATR) — high-conviction institutional intent."""
    o, h, l, c = _f(o), _f(h), _f(l), _f(c)
    n = len(c)
    if atr <= 0:
        return []
    out = []
    for i in range(n - 1, max(0, n - 25) - 1, -1):
        if (h[i] - l[i]) > 1.5 * atr:
            out.append({"index": int(i), "direction": "up" if c[i] > o[i] else "down",
                        "magnitude": round(float(h[i] - l[i]) / atr, 1)})
            if len(out) >= n_keep:
                break
    return out


def _market_structure(c, sh, sl) -> dict:
    """Trend from the last two swings + a Break of Structure / Change of Character read."""
    c = _f(c)
    last = float(c[-1])
    trend = "range"
    if len(sh) >= 2 and len(sl) >= 2:
        hh = sh[-1][1] > sh[-2][1]
        hl = sl[-1][1] > sl[-2][1]
        lh = sh[-1][1] < sh[-2][1]
        ll = sl[-1][1] < sl[-2][1]
        if hh and hl:
            trend = "up"
        elif lh and ll:
            trend = "down"
    # Fallback when swings are choppy: price vs a rising/falling 20-period average.
    if trend == "range" and len(c) >= 25:
        sma = float(np.mean(c[-20:]))
        sma_prev = float(np.mean(c[-25:-5]))
        if last > sma and sma > sma_prev:
            trend = "up"
        elif last < sma and sma < sma_prev:
            trend = "down"
    recent_sh = sh[-1][1] if sh else None
    recent_sl = sl[-1][1] if sl else None
    bos = None
    if recent_sh is not None and last > recent_sh:
        bos = {"type": "bullish", "level": round(recent_sh, 2)}
    elif recent_sl is not None and last < recent_sl:
        bos = {"type": "bearish", "level": round(recent_sl, 2)}
    choch = bos is not None and ((bos["type"] == "bullish" and trend == "down")
                                 or (bos["type"] == "bearish" and trend == "up"))
    return {"trend": trend, "bos": bos, "change_of_character": bool(choch),
            "recent_swing_high": round(recent_sh, 2) if recent_sh is not None else None,
            "recent_swing_low": round(recent_sl, 2) if recent_sl is not None else None}


# ---------------------------------------------------------------------------
# Regime distillation — the retail-readable, actionable output
# ---------------------------------------------------------------------------

def _near(price: float, level: float | None, pct: float = 0.03) -> bool:
    return level is not None and level > 0 and abs(price - level) / level <= pct


def _regime(price, rsi, vp, structure, obs, fvgs, sweeps) -> dict:
    poc = vp["poc"] if vp else None
    vah = vp["vah"] if vp else None
    val = vp["val"] if vp else None
    inside_va = vp is not None and val <= price <= vah
    above_va = vp is not None and price > vah
    below_va = vp is not None and price < val
    near_demand = any(ob["type"] == "bullish" and not ob["mitigated"] and _near(price, ob["price"]) for ob in obs)
    near_supply = any(ob["type"] == "bearish" and not ob["mitigated"] and _near(price, ob["price"]) for ob in obs)
    oversold = rsi is not None and rsi < 35
    overbought = rsi is not None and rsi > 65
    trend = structure.get("trend")
    bos = structure.get("bos")
    # Only a VERY fresh sweep (last ≤2 bars) flips the regime to a reversal, so a
    # stale historical sweep doesn't hijack the read (that bug made everything MR).
    fresh_sweep = next((s for s in sweeps if s.get("bars_ago", 99) <= 2), None)

    poc_txt = f"POC ${poc}" if poc is not None else "the point of control"

    if fresh_sweep is not None:
        side = fresh_sweep["type"]
        state, bias, mode = "Reversal (liquidity sweep)", ("bearish" if side == "buyside" else "bullish"), "mean_reversion"
        rationale = (f"A {side} liquidity sweep just ran stops at ${fresh_sweep['level']} and snapped back — "
                     f"a classic stop-hunt reversal. Fade the sweep; sell premium on the reversal side.")
        income = [_CCS, _COLLAR] if side == "buyside" else [_CSP, _PCS]
    elif oversold and (near_demand or below_va):
        state, bias, mode = "Mean-Reversion", "bullish", "mean_reversion"
        rationale = (f"RSI {rsi:.0f} (oversold) into a demand zone / below the value area — a bounce "
                     f"back toward fair value ({poc_txt}) is statistically favored. Selling downside "
                     f"premium here collects rich vol without needing a rally.")
        income = [_CSP, _PCS, _CC]
    elif overbought and (near_supply or above_va):
        state, bias, mode = "Mean-Reversion", "bearish", "mean_reversion"
        rationale = (f"RSI {rsi:.0f} (overbought) into a supply zone / above the value area — a fade "
                     f"back toward {poc_txt} is favored. Selling upside premium is favored over chasing.")
        income = [_CCS, _CC, _COLLAR]
    elif trend == "up" or (bos and bos["type"] == "bullish"):
        state, bias, mode = "Trend-Following", "bullish", "trend"
        bos_txt = f"a bullish break of structure above ${bos['level']} and " if bos and bos["type"] == "bullish" else ""
        rationale = (f"{bos_txt}an up-trend — momentum is higher. Sell downside premium WITH the trend "
                     f"(put spreads / CSPs), or write covered calls above resistance to harvest theta.").capitalize()
        income = [_PCS, _CSP, _CC]
    elif trend == "down" or (bos and bos["type"] == "bearish"):
        state, bias, mode = "Trend-Following", "bearish", "trend"
        bos_txt = f"a bearish break of structure below ${bos['level']} and " if bos and bos["type"] == "bearish" else ""
        rationale = (f"{bos_txt}a down-trend — momentum is lower. Call credit spreads / collars are favored; "
                     f"avoid selling naked puts into weakness.").capitalize()
        income = [_CCS, _COLLAR]
    elif inside_va:
        state, bias, mode = "Range / Balanced", "neutral", "range"
        rationale = (f"Price is inside the value area (${val}–${vah}) around {poc_txt} — no directional edge, "
                     f"and premium decays best in a range. Neutral, defined-risk premium selling is favored.")
        income = [_IC, _JL]
    else:
        state, bias, mode = "Neutral / Balanced", "neutral", "range"
        rationale = ("No dominant structure signal right now — treat it as balanced and let probability + "
                     "premium do the work with a defined-risk, delta-light income trade.")
        income = [_IC, _CC, _CSP]

    return {
        "state": state,
        "mode": mode,                       # mean_reversion | trend | range
        "bias": bias,                       # bullish | bearish | neutral
        "rationale": rationale,
        "favored_income": income,
        "favored_income_labels": [_INCOME_LABEL.get(x, x) for x in income],
        "signals": {
            "rsi": round(rsi, 1) if rsi is not None else None,
            "vs_value_area": "above" if above_va else "below" if below_va else "inside" if inside_va else None,
            "at_demand": near_demand, "at_supply": near_supply,
            "trend": trend,
            "bos": bos["type"] if bos else None,
            "recent_sweep": fresh_sweep["type"] if fresh_sweep else None,
        },
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compute_institutional_ta(opens, highs, lows, closes, volumes,
                             rsi: float | None = None) -> dict | None:
    """Full institutional read from OHLCV arrays. Returns ``None`` if there's too
    little data to be meaningful (<20 candles)."""
    try:
        o, h, l, c, v = _f(opens), _f(highs), _f(lows), _f(closes), _f(volumes)
        n = len(c)
        if n < 20 or len(o) != n or len(h) != n or len(l) != n:
            return None
        price = float(c[-1])
        atr = _atr(h, l, c)
        sh, sl = _swings(h, l)
        vp = _volume_profile(h, l, c, v)
        obs = _order_blocks(o, h, l, c, atr)
        fvgs = _fair_value_gaps(h, l)
        sweeps = _liquidity_sweeps(h, l, c, sh, sl, atr)
        disp = _displacement(o, h, l, c, atr)
        structure = _market_structure(c, sh, sl)
        regime = _regime(price, rsi, vp, structure, obs, fvgs, sweeps)
        return {
            "price": round(price, 2),
            "atr": round(atr, 2),
            "volume_profile": vp,
            "order_blocks": obs,
            "fair_value_gaps": fvgs,
            "liquidity_sweeps": sweeps,
            "displacement": disp,
            "market_structure": structure,
            "regime": regime,
        }
    except Exception:  # noqa: BLE001 — never break the TA response
        return None
