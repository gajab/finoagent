"""Intraday day-trade setups — the third trade style (alongside swing and position).

These are SAME-SESSION trades built off 5m/15m structure, session VWAP and the opening range —
NOT the daily-structure swing setups. Entry is usually "now" on a VWAP hold/reclaim or an
opening-range break, the stop is tight (just through VWAP / the range), and the target is the next
intraday level (opening-range extension, prior-day high/low, or ~1.5× the intraday ATR). Thin in $
by nature — that's day trading — and clearly labelled so.

Kept in its OWN lazy endpoint (`/day-trade-setups`) so the main Setups load stays free of the extra
intraday fetches (production is one 512 MiB Cloud Run instance).

Reuses the Trade-Tracker's intraday primitives and the swing engine's card helpers so a day-trade
card renders through the exact same UI.
"""
from __future__ import annotations

import datetime as _dt

import numpy as np

from .microstructure_service import _r, _now_str
from .trade_tracking_service import _fetch_frames, _session_vwap_bands, _atr
from .trade_setup_service import _entry_style, _trade_horizon, _equity_plan, _sizing, _rr


def _to_date(x):
    try:
        import pandas as pd
        return pd.Timestamp(x).date()
    except Exception:  # noqa: BLE001
        return None


def _opening_range(d5):
    """High/low of the current session's first ~30 minutes (6×5-min bars)."""
    if d5 is None or getattr(d5, "empty", True):
        return None
    idx = d5.index
    last_day = _to_date(idx[-1])
    mask = [(_to_date(t) == last_day) for t in idx]
    sess = d5[np.array(mask)] if any(mask) else d5.tail(6)
    orb = sess.head(6)
    if len(orb) < 3:
        return None
    return {"high": float(orb["High"].max()), "low": float(orb["Low"].min()),
            "bars": int(len(orb))}


def _day_setup(kind, direction, entry, stop, target, spot, atr15, thesis, evidence) -> dict | None:
    if not (entry and stop and target and spot):
        return None
    rr = _rr(entry, stop, target)
    if not rr or rr < 1.0:                       # never surface a sub-1:1 day trade
        return None
    return {
        "type": kind, "direction": direction, "regime_fit": "neutral", "style": "day",
        "confidence": "high" if rr >= 2 else "medium", "score": round(rr, 2),
        "entry": {"low": _r(entry), "high": _r(entry), "level": _r(entry), "label": kind.replace("_", " ")},
        "stop": {"level": _r(stop), "label": "tight intraday stop"},
        "targets": [{"level": _r(target), "label": "intraday target", "rr": rr}],
        "risk_reward": rr,
        "sizing": _sizing(entry, stop, target, spot, None),
        "options": {"structure": "—", "detail": "Intraday — trade the shares/futures.", "bias": direction},
        "options_plan": {"available": False, "structure": "—",
                         "note": "Intraday day-trade — trade the shares/futures; too little time to hold a defined-risk options structure."},
        "thesis": thesis, "evidence": [e for e in evidence if e],
        "entry_style": _entry_style(direction, entry, spot, atr15),
        "horizon": _trade_horizon(entry, target, atr15, "day"),
        "from_current": {"to_entry_pct": round((entry - spot) / spot * 100, 2) if spot else None,
                         "to_t1_pct": round((target - spot) / spot * 100, 2) if spot else None},
        "equity_plan": _equity_plan(direction, entry, stop, [{"level": target}], spot),
        "what_to_watch": [
            "Same-session trade — manage it intraday and don't carry a losing day-trade overnight.",
            "Session VWAP is the line in the sand: losing it flips the intraday bias, so honor the stop.",
        ],
        "edge": {}, "event_risk": None,
    }


def compute_day_trade_setups(stock) -> dict | None:
    """Intraday day-trade setups from 5m/15m structure + session VWAP + the opening range.
    Best-effort; returns None only if no intraday data at all."""
    try:
        frames = _fetch_frames(stock)
        d5, d15, daily = frames.get("d5"), frames.get("d15"), frames.get("daily")
        src = d5 if (d5 is not None and not d5.empty) else d15
        if src is None or src.empty:
            return None
        spot = float(src["Close"].values[-1])
        atr15 = _atr(d15) or _atr(d5) or (spot * 0.004)
        vwap = _session_vwap_bands(src, spot) or {}
        vw = vwap.get("vwap")
        orange = _opening_range(d5)
        pdh = pdl = None
        if daily is not None and len(daily) >= 2:
            pdh = float(daily["High"].values[-2]); pdl = float(daily["Low"].values[-2])

        # TODAY's session tape — the day-trade bias comes from the session VWAP + today's momentum,
        # NOT the multi-day 15m trend (that flagged GLD 'down' from a prior-week selloff while it was
        # cleanly rallying above a rising VWAP intraday → the intraday long was wrongly blocked).
        try:
            sday = _to_date(src.index[-1])
            sess = src[np.array([_to_date(t) == sday for t in src.index])]
        except Exception:  # noqa: BLE001
            sess = src.tail(20)
        if sess is None or len(sess) < 3:
            sess = src.tail(20)
        sess_hi, sess_lo = float(sess["High"].max()), float(sess["Low"].min())
        closes = sess["Close"].values.astype(float)
        mom = float(closes[-1] - closes[max(0, len(closes) - 12)])   # ~last hour of 5m closes

        setups: list[dict] = []

        # 1) VWAP intraday trade — bias = price side of session VWAP + today's momentum. When price is
        # NEAR VWAP, enter now (VWAP hold/reject); when EXTENDED, buy the pullback / short the bounce
        # back toward VWAP (don't chase an extended move).
        if vw and atr15:
            dist = spot - vw
            near = abs(dist) <= 0.8 * atr15
            up = spot >= vw and mom >= -0.25 * atr15
            dn = spot < vw and mom <= 0.25 * atr15
            if up:
                if near:
                    entry, stop = spot, round(vw - 0.5 * atr15, 2)
                    typ, lead = "vwap_hold_long", f"Holding at the rising session VWAP ${_r(vw)} — long now near ${_r(spot)}"
                else:
                    entry, stop = round(vw + 0.3 * atr15, 2), round(vw - 0.6 * atr15, 2)
                    typ, lead = "vwap_pullback_long", (f"Uptrend but ${_r(dist)} extended above VWAP — don't chase; buy the "
                                                       f"PULLBACK toward the rising VWAP at ${round(vw + 0.3 * atr15, 2)}")
                cands = [x for x in (sess_hi + 0.3 * atr15, pdh) if x and x > entry + 0.6 * atr15]
                target = round(min(cands), 2) if cands else round(entry + 2.0 * atr15, 2)
                s = _day_setup(typ, "long", entry, stop, target, spot, atr15,
                               f"{lead}, stop ${stop} below VWAP, target ${target} (session-high retest / measured move).",
                               [f"session VWAP ${_r(vw)}", f"above VWAP by ${_r(dist)}", f"session ${_r(sess_lo)}–${_r(sess_hi)}", f"intraday ATR ${_r(atr15)}"])
                if s:
                    setups.append(s)
            elif dn:
                if near:
                    entry, stop = spot, round(vw + 0.5 * atr15, 2)
                    typ, lead = "vwap_reject_short", f"Rejecting the falling session VWAP ${_r(vw)} — short now near ${_r(spot)}"
                else:
                    entry, stop = round(vw - 0.3 * atr15, 2), round(vw + 0.6 * atr15, 2)
                    typ, lead = "vwap_pullback_short", (f"Downtrend but ${_r(abs(dist))} extended below VWAP — short the "
                                                        f"BOUNCE back toward VWAP at ${round(vw - 0.3 * atr15, 2)}")
                cands = [x for x in (sess_lo - 0.3 * atr15, pdl) if x and x < entry - 0.6 * atr15]
                target = round(max(cands), 2) if cands else round(entry - 2.0 * atr15, 2)
                s = _day_setup(typ, "short", entry, stop, target, spot, atr15,
                               f"{lead}, stop ${stop} above VWAP, target ${target} (session-low retest / measured move).",
                               [f"session VWAP ${_r(vw)}", f"below VWAP by ${_r(abs(dist))}", f"session ${_r(sess_lo)}–${_r(sess_hi)}", f"intraday ATR ${_r(atr15)}"])
                if s:
                    setups.append(s)

        # 2) Opening-range break — price pressing the OR edge → trade the break, measured-move target.
        if orange:
            orh, orl = orange["high"], orange["low"]
            width = orh - orl
            if width > 0:
                if spot >= orh - 0.25 * width and spot <= orh + 0.5 * width:      # near/above the OR high
                    entry = round(orh + 0.05 * atr15, 2); stop = round(orl + 0.5 * width, 2)
                    target = round(orh + width, 2)
                    s = _day_setup("opening_range_break_long", "long", entry, stop, target, spot, atr15,
                                   f"Opening-range breakout — buy the break of ${_r(orh)} (30-min range ${_r(orl)}–${_r(orh)}), "
                                   f"stop at the range mid ${stop}, target ${target} (one range width).",
                                   [f"opening range ${_r(orl)}–${_r(orh)}", f"range width ${_r(width)}"])
                    if s:
                        setups.append(s)
                elif spot <= orl + 0.25 * width and spot >= orl - 0.5 * width:     # near/below the OR low
                    entry = round(orl - 0.05 * atr15, 2); stop = round(orh - 0.5 * width, 2)
                    target = round(orl - width, 2)
                    s = _day_setup("opening_range_break_short", "short", entry, stop, target, spot, atr15,
                                   f"Opening-range breakdown — sell the break of ${_r(orl)} (30-min range ${_r(orl)}–${_r(orh)}), "
                                   f"stop the range mid ${stop}, target ${target}.",
                                   [f"opening range ${_r(orl)}–${_r(orh)}", f"range width ${_r(width)}"])
                    if s:
                        setups.append(s)

        setups.sort(key=lambda s: -(s.get("risk_reward") or 0))
        for i, s in enumerate(setups):
            s["rank"] = i + 1
        return {
            "price": _r(spot), "as_of": _now_str(), "atr": _r(atr15),
            "vwap": vwap, "opening_range": orange,
            "setups": setups[:3],
            "meta": {"has_vwap": bool(vw), "has_opening_range": bool(orange)},
        }
    except Exception:  # noqa: BLE001
        return None
