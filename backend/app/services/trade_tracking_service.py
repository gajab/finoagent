"""Trade tracking & management — the *secondary-confirmation* engine.

A setup from :mod:`trade_setup_service` says *where* to trade. But price reaching the
entry level is necessary, not sufficient — market dynamics shift bar to bar. This module
answers the live question a disciplined trader asks at the level:

  • **Watching** (pre-entry): *should I pull the trigger now?* Runs a battery of secondary
    confirmations — lower-timeframe CHOCH, liquidity-sweep + rejection wick, intraday-VWAP
    σ-extension, 1-hour RSI filter, CVD (order-flow proxy) divergence, plus higher-timeframe
    alignment, level integrity, volume climax, dealer-gamma context and momentum divergence —
    and returns **EXECUTE / WAIT / INVALID**.
  • **In progress** (post-entry): *is it still a good time to hold, or time to leave?* Checks
    target/stop proximity, a CHOCH *against* the position, a VWAP reclaim, gamma-flip cross and
    the R-multiple trail rule → **HOLD / SCALE-OUT / TIGHTEN / EXIT**.

Everything is best-effort and pure where it can be (so the verdict logic is unit-testable
without yfinance). Each evaluation also emits a self-contained ``payload`` JSON the user can
copy into any external LLM — or hand to our own ``/ask-llm`` endpoint.
"""

from __future__ import annotations

import math

import numpy as np

from .microstructure_service import _r, _now_str, _safe_history, _safe_history_days, _to_date
from .market_structure_service import _pivots, _structure
from .institutional_ta_service import _f
from .dealer_positioning_service import compute_dealer_positioning


# ===========================================================================
# verdict vocabulary
# ===========================================================================

VERDICT_LABEL = {
    "execute":   "Execute — conditions met",
    "wait":      "Wait — not confirmed yet",
    "invalid":   "Invalid — conditions changed",
    "hold":      "Hold — thesis intact",
    "scale_out": "Scale out — take partial profit",
    "tighten":   "Tighten — protect the position",
    "exit":      "Exit — close the trade",
}


def _mk(key, label, status, detail, value=None, weight=1.0, role="confirm"):
    """One confirmation line. ``status`` ∈ pass|fail|warn|na; a *pass* always means
    'favorable to the trade'. ``role`` ∈ gate|critical|confirm (entry) or
    trigger|caution|favorable (exit)."""
    return {"key": key, "label": label, "status": status, "detail": detail,
            "value": value, "weight": weight, "role": role}


# ===========================================================================
# pure primitives (testable without a network)
# ===========================================================================

def _atr(df, period: int = 14):
    if df is None or getattr(df, "empty", True) or len(df) < 2:
        return None
    h, l, c = _f(df["High"].values), _f(df["Low"].values), _f(df["Close"].values)
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    n = min(period, len(tr))
    return float(np.mean(tr[-n:])) if n else None


def _rsi_last(closes, period: int = 14):
    """Wilder RSI, last value."""
    c = _f(closes)
    if len(c) < period + 1:
        return None
    d = np.diff(c)
    gain = np.where(d > 0, d, 0.0)
    loss = np.where(d < 0, -d, 0.0)
    ag, al = float(np.mean(gain[:period])), float(np.mean(loss[:period]))
    for i in range(period, len(d)):
        ag = (ag * (period - 1) + gain[i]) / period
        al = (al * (period - 1) + loss[i]) / period
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    rs = ag / al
    return _r(100.0 - 100.0 / (1.0 + rs), 1)


def _session_vwap_bands(df, spot: float):
    """Anchored intraday VWAP for the current session + volume-weighted σ bands
    (±1/2/3σ) and the live z-score of spot vs VWAP."""
    if df is None or getattr(df, "empty", True) or not spot:
        return None
    idx = df.index
    try:
        last_day = _to_date(idx[-1])
        mask = np.array([_to_date(t) == last_day for t in idx])
    except Exception:
        mask = np.array([True] * len(df))
    sess = df[mask] if mask.any() else df
    if len(sess) < 6:
        sess = df.tail(78)                                   # ~1 RTH day of 5-min bars
    h, l, c, v = (_f(sess["High"].values), _f(sess["Low"].values),
                  _f(sess["Close"].values), _f(sess["Volume"].values))
    tp = (h + l + c) / 3.0
    cumv = np.cumsum(v)
    if cumv[-1] <= 0:
        return None
    vwap = float(np.cumsum(tp * v)[-1] / cumv[-1])
    var = float(np.sum(v * (tp - vwap) ** 2) / cumv[-1])
    sigma = math.sqrt(max(var, 0.0))
    z = (spot - vwap) / sigma if sigma > 0 else 0.0
    return {
        "vwap": _r(vwap), "sigma": _r(sigma, 3), "z": _r(z, 2), "session_bars": int(len(sess)),
        "upper_1": _r(vwap + sigma), "upper_2": _r(vwap + 2 * sigma), "upper_3": _r(vwap + 3 * sigma),
        "lower_1": _r(vwap - sigma), "lower_2": _r(vwap - 2 * sigma), "lower_3": _r(vwap - 3 * sigma),
    }


def _cvd(df, lookback: int = 90):
    """Cumulative Volume Delta *proxy*: without true bid/ask tape we estimate per-bar
    buy/sell pressure by where the bar closed within its range ((C−O)/(H−L))·V and
    accumulate it. Divergence (price higher-high while CVD lower-high, or vice-versa)
    flags passive absorption — the classic order-flow tell."""
    if df is None or getattr(df, "empty", True) or len(df) < 12:
        return None
    s = df.tail(lookback)
    o, h, l, c, v = (_f(s["Open"].values), _f(s["High"].values), _f(s["Low"].values),
                     _f(s["Close"].values), _f(s["Volume"].values))
    rng = np.maximum(h - l, 1e-9)
    delta = np.clip((c - o) / rng, -1.0, 1.0) * v
    cvd = np.cumsum(delta)
    sh, sl = _pivots(h, l, order=2)
    div = None
    if len(sh) >= 2:
        (i1, p1), (i2, p2) = sh[-2], sh[-1]
        if p2 > p1 and cvd[i2] < cvd[i1]:
            div = "bearish"                                   # higher price high, weaker CVD → sellers absorbing
    if len(sl) >= 2 and div is None:
        (i1, p1), (i2, p2) = sl[-2], sl[-1]
        if p2 < p1 and cvd[i2] > cvd[i1]:
            div = "bullish"                                   # lower price low, stronger CVD → buyers absorbing
    slope = float(cvd[-1] - cvd[max(0, len(cvd) - 10)])
    return {"last": _r(cvd[-1], 0), "slope_10": _r(slope, 0), "divergence": div}


def _choch_tf(df, order: int = 3):
    """BOS/CHOCH read on one timeframe via the shared pivot+structure walker."""
    if df is None or getattr(df, "empty", True) or len(df) < 2 * order + 2:
        return None
    sh, sl = _pivots(df["High"].values, df["Low"].values, order)
    return _structure(df["Close"].values, sh, sl, order)


def _rejection_candle(df, level: float, side: str, lookback: int = 6):
    """A sweep-and-reject bar: price pierced ``level`` but closed back on the origin side
    leaving a dominant wick (>50% of range). ``side='short'`` → upper-wick rejection of a
    pierce ABOVE the level; ``side='long'`` → lower-wick rejection of a pierce BELOW."""
    if df is None or getattr(df, "empty", True) or not level:
        return None
    s = df.tail(lookback)
    best = None
    for t, row in s.iterrows():
        o, h, l, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
        rng = max(h - l, 1e-9)
        if side == "short":
            if h > level and c < level and (h - max(o, c)) / rng > 0.5:
                best = {"time": str(t)[:16], "wick_pct": _r((h - max(o, c)) / rng * 100, 0),
                        "high": _r(h), "close": _r(c)}
        else:
            if l < level and c > level and (min(o, c) - l) / rng > 0.5:
                best = {"time": str(t)[:16], "wick_pct": _r((min(o, c) - l) / rng * 100, 0),
                        "low": _r(l), "close": _r(c)}
    return best


def _vol_climax(df, lookback: int = 20):
    """Latest bar volume relative to the recent average — a spike marks climactic /
    exhaustion activity that lends a rejection real conviction."""
    if df is None or getattr(df, "empty", True) or len(df) < 5:
        return None
    v = _f(df["Volume"].values)
    avg = float(np.mean(v[-lookback:-1])) if len(v) > lookback else float(np.mean(v[:-1]) or 0)
    if avg <= 0:
        return None
    return {"ratio": _r(v[-1] / avg, 2), "last": _r(v[-1], 0), "avg": _r(avg, 0)}


# ===========================================================================
# market snapshot (fetch once, share across all checks)
# ===========================================================================

def _fetch_frames(stock) -> dict:
    return {
        "d5":    _safe_history(stock, "5d", "5m"),
        "d15":   _safe_history_days(stock, 20, "15m"),
        "d1h":   _safe_history_days(stock, 45, "60m"),
        "daily": _safe_history(stock, "6mo", "1d"),
    }


def _snapshot(frames: dict, stock) -> dict | None:
    d5, d15, d1h, daily = frames["d5"], frames["d15"], frames["d1h"], frames["daily"]
    spot = None
    for df in (d5, d15, d1h, daily):
        if df is not None and not df.empty:
            spot = float(df["Close"].values[-1]); break
    if spot is None:
        return None

    gamma = None
    try:
        dp = compute_dealer_positioning(stock)
        if dp:
            gamma = {"net_gex": dp.get("net_gex"), "gamma_flip": dp.get("gamma_flip"),
                     "walls": dp.get("walls"), "gamma_levels": dp.get("gamma_levels")}
    except Exception:  # noqa: BLE001
        pass

    return {
        "as_of": _now_str(),
        "spot": _r(spot),
        "vwap": _session_vwap_bands(d5 if d5 is not None else d15, spot),
        "rsi_1h": _rsi_last(d1h["Close"].values) if (d1h is not None and not d1h.empty) else None,
        "choch_5m": _choch_tf(d5, order=3),
        "choch_15m": _choch_tf(d15, order=2),
        "choch_1h": _choch_tf(d1h, order=2),
        "trend_daily": _choch_tf(daily, order=3),
        "cvd": _cvd(d5 if d5 is not None else d15),
        "volume": _vol_climax(d15 if d15 is not None else d5),
        "atr_15m": _r(_atr(d15) or _atr(d5)),
        "atr_daily": _r(_atr(daily)),
        "gamma": gamma,
    }


# ===========================================================================
# position math (distances, open P&L, R-multiple)
# ===========================================================================

def _position_math(trade: dict, spot: float) -> dict:
    d = trade.get("direction")
    entry = trade.get("entry_level")
    stop = trade.get("stop_level")
    tgts = [t for t in (trade.get("target_levels") or []) if t is not None]
    executed = trade.get("executed_price")
    ref = executed if executed else entry
    out = {"spot": _r(spot), "entry": _r(entry), "stop": _r(stop),
           "targets": [_r(t) for t in tgts], "ref": _r(ref)}
    if spot and entry:
        out["dist_to_entry_pct"] = _r((entry - spot) / spot * 100, 2)
    if ref and spot and d in ("long", "short"):
        per = (spot - ref) if d == "long" else (ref - spot)
        out["open_pnl_pct"] = _r(per / ref * 100, 2)
        qty = trade.get("executed_qty")
        if qty and executed:
            out["open_pnl"] = _r(per * qty, 2)
        if stop and abs(ref - stop) > 0:
            out["r_multiple"] = _r(per / abs(ref - stop), 2)
    if tgts:
        out["dist_to_t1_pct"] = _r((tgts[0] - spot) / spot * 100, 2) if spot else None
    return out


# ===========================================================================
# ENTRY confirmations (directional)
# ===========================================================================

def _near(a: float, b: float, spot: float, frac: float = 0.01) -> bool:
    return a is not None and b is not None and abs(a - b) <= frac * (spot or a)


def _entry_checks(trade: dict, snap: dict, frames: dict) -> list[dict]:
    d = trade["direction"]
    is_long = d == "long"
    spot = snap["spot"]
    entry = trade.get("entry_level")
    lo = trade.get("entry_low") or entry
    hi = trade.get("entry_high") or entry
    stop = trade.get("stop_level")
    setup_type = trade.get("setup_type") or ""
    atr = snap.get("atr_15m") or (spot * 0.003 if spot else 0)
    tol = max(0.0025 * spot, 0.3 * (atr or 0)) if spot else 0
    checks: list[dict] = []

    # 0) GATE — has price actually reached the entry zone?
    if lo is not None and hi is not None and spot is not None:
        reached = (lo - tol) <= spot <= (hi + tol)
        dist = min(abs(spot - lo), abs(spot - hi)) / spot * 100
        checks.append(_mk("at_entry", "Price at entry zone",
                          "pass" if reached else "warn",
                          (f"Spot ${_r(spot)} is inside the entry band ${_r(lo)}–${_r(hi)}."
                           if reached else f"Spot ${_r(spot)} is ~{_r(dist,2)}% from the entry band ${_r(lo)}–${_r(hi)}."),
                          value=_r(dist, 2), role="gate"))

    # 1) LEVEL INTEGRITY (critical) — has price already blown through the stop?
    if stop is not None and spot is not None:
        broken = (spot > stop) if is_long is False else (spot < stop)
        # long: invalidated if spot already below the stop; short: if already above the stop
        broken = (spot < stop) if is_long else (spot > stop)
        checks.append(_mk("level_integrity", "Level still valid",
                          "fail" if broken else "pass",
                          (f"Spot ${_r(spot)} is already beyond the stop ${_r(stop)} — the setup has been "
                           f"invalidated; the level didn't hold." if broken
                           else f"The ${_r(stop)} stop hasn't been breached — the level is intact."),
                          role="critical"))

    # 2) HIGHER-TIMEFRAME ALIGNMENT (critical only for trend-continuation)
    dtrend = (snap.get("trend_daily") or {}).get("trend")
    h1trend = (snap.get("choch_1h") or {}).get("trend")
    want = "up" if is_long else "down"
    against = "down" if is_long else "up"
    if dtrend:
        aligned = dtrend == want
        conflict = dtrend == against
        is_cont = setup_type == "trend_continuation"
        status = "pass" if aligned else ("fail" if (conflict and is_cont) else "warn" if conflict else "warn")
        detail = (f"Daily trend is {dtrend} and 1H is {h1trend or 'n/a'} — "
                  + ("aligned with the trade." if aligned else
                     ("the higher timeframe has turned against a trend-continuation trade; thesis broken." if (conflict and is_cont)
                      else "counter to the trade (expected for a fade — keep size modest).")))
        checks.append(_mk("htf_alignment", "Higher-timeframe alignment", status, detail,
                          value=f"D:{dtrend}/1H:{h1trend}", weight=1.6,
                          role="critical" if (conflict and is_cont) else "confirm"))

    # 3) LOWER-TIMEFRAME CHOCH (5m/15m) — momentum turning in the trade's direction
    ev15 = (snap.get("choch_15m") or {}).get("last_event") or {}
    ev5 = (snap.get("choch_5m") or {}).get("last_event") or {}
    want_dir = "bullish" if is_long else "bearish"
    e15d, e5d = ev15.get("direction"), ev5.get("direction")
    if e15d == want_dir or e5d == want_dir:
        which = "15m" if e15d == want_dir else "5m"
        ev = ev15 if e15d == want_dir else ev5
        st = "pass"
        detail = (f"{which} {ev.get('type','break')} {want_dir} at ${ev.get('level')} — lower-timeframe momentum "
                  f"has flipped in the trade's favor (break of the recent swing {'low' if not is_long else 'high'}).")
    elif e15d and e15d != want_dir:
        st = "fail"
        detail = (f"15m last break is {e15d} (against the trade) — the bounce hasn't rolled over yet; "
                  f"entering here fights lower-timeframe momentum.")
    else:
        st = "warn"
        detail = "No clean 5m/15m change-of-character yet — momentum hasn't confirmed the turn."
    checks.append(_mk("ltf_choch", "Lower-timeframe CHOCH (5m/15m)", st, detail,
                      value=f"15m:{e15d}/5m:{e5d}", weight=2.0))

    # 4) LIQUIDITY SWEEP + REJECTION WICK (15m preferred, 5m fallback)
    side = "long" if is_long else "short"
    lvl = (lo if is_long else hi) or entry
    rej = _rejection_candle(frames.get("d15"), lvl, side) or _rejection_candle(frames.get("d5"), lvl, side)
    checks.append(_mk("rejection_wick", "Liquidity sweep + rejection wick",
                      "pass" if rej else "warn",
                      (f"A {rej['wick_pct']}%-wick candle swept ${_r(lvl)} and closed back — stops taken, level "
                       f"defended ({rej['time']})." if rej
                       else f"No sweep-and-reject candle at ${_r(lvl)} in the last few bars yet."),
                      value=rej, weight=1.5))

    # 5) INTRADAY VWAP σ-EXTENSION
    vwap = snap.get("vwap") or {}
    z = vwap.get("z")
    if z is not None:
        favorable_z = (z <= -2) if is_long else (z >= 2)
        near_z = (z <= -1) if is_long else (z >= 1)
        st = "pass" if favorable_z else ("warn" if near_z else "na")
        detail = (f"Spot is {abs(z):.1f}σ {'below' if is_long else 'above'} the session VWAP ${vwap.get('vwap')} "
                  + ("— a stretched extreme, prime for the reversion." if favorable_z
                     else "— extended but not yet at the ±2σ band." if near_z
                     else f"— not stretched {'below' if is_long else 'above'} VWAP; the σ-extension edge is absent."))
        checks.append(_mk("vwap_extension", "Intraday VWAP σ-extension", st, detail, value=z, weight=1.0))

    # 6) 1-HOUR RSI FILTER
    rsi = snap.get("rsi_1h")
    if rsi is not None:
        # short into a bounce wants an overbought 1H (>60); long into a flush wants oversold (<40)
        favorable = (rsi < 40) if is_long else (rsi > 60)
        near = (rsi < 48) if is_long else (rsi > 52)
        st = "pass" if favorable else ("warn" if near else "na")
        detail = (f"1H RSI {rsi} — " + ("oversold bounce-zone, supports the long." if (is_long and favorable)
                  else "overbought bounce inside the downtrend, supports the short." if (not is_long and favorable)
                  else "leaning your way but not yet at the filter threshold." if near
                  else "not at the RSI extreme the fade wants."))
        checks.append(_mk("rsi_1h", "1-hour RSI filter", st, detail, value=rsi, weight=1.0))

    # 7) CVD (order-flow proxy) DIVERGENCE
    cvd = snap.get("cvd") or {}
    div = cvd.get("divergence")
    if div is not None or cvd:
        favorable = div == ("bullish" if is_long else "bearish")
        against = div == ("bearish" if is_long else "bullish")
        st = "pass" if favorable else ("fail" if against else "warn")
        detail = (f"CVD shows a {div} divergence — passive {'buyers' if is_long else 'sellers'} absorbing at the level."
                  if favorable else
                  f"CVD confirms {'sellers' if is_long else 'buyers'} still in control (no divergence) — order flow "
                  f"isn't backing the trade yet." if against else
                  "No clean CVD divergence; order flow is neutral here.")
        checks.append(_mk("cvd_divergence", "CVD divergence (order-flow proxy)", st, detail,
                          value=div, weight=1.0))

    # 8) VOLUME CLIMAX — is the rejection on conviction volume?
    vol = snap.get("volume") or {}
    ratio = vol.get("ratio")
    if ratio is not None:
        st = "pass" if ratio >= 1.5 else ("warn" if ratio >= 1.0 else "na")
        detail = (f"Latest 15m volume is {ratio}× the recent average — climactic activity backing the turn."
                  if ratio >= 1.5 else
                  f"Volume is {ratio}× average — participation is ordinary, not climactic.")
        checks.append(_mk("volume_climax", "Volume climax at the level", st, detail, value=ratio, weight=0.7))

    # 9) DEALER-GAMMA CONTEXT
    checks.append(_gamma_context_check(snap, entry, spot, is_long))

    # 10) ROOM TO TARGET vs intraday noise
    tgts = [t for t in (trade.get("target_levels") or []) if t is not None]
    if tgts and entry and atr:
        room = abs(tgts[0] - entry)
        st = "pass" if room >= 1.5 * atr else "warn"
        detail = (f"T1 is ${_r(room)} away vs a 15m ATR of ${_r(atr)} ({_r(room/atr,1)}× noise) — "
                  + ("enough room to clear the chop." if room >= 1.5 * atr
                     else "a tight target relative to intraday noise; scalp it or widen T1."))
        checks.append(_mk("room_to_target", "Room to first target", st, detail,
                          value=_r(room / atr, 1), weight=0.5))
    return checks


def _gamma_context_check(snap: dict, level, spot, is_long: bool) -> dict:
    g = snap.get("gamma") or {}
    sign = (g.get("net_gex") or {}).get("sign")
    gl = g.get("gamma_levels") or {}
    walls = g.get("walls") or {}
    if is_long:
        wall = (gl.get("put_support") or {}).get("strike") or (walls.get("put_wall") or {}).get("strike")
        wall_lbl = "put support"
    else:
        wall = (gl.get("call_resistance") or {}).get("strike") or (walls.get("call_wall") or {}).get("strike")
        wall_lbl = "call resistance"
    at_wall = _near(level, wall, spot, 0.012) if (level and wall) else False
    if sign == "long":
        st = "pass" if at_wall else "warn"
        detail = (f"Dealers are long gamma (vol-suppressed, mean-reverting) and the entry sits at {wall_lbl} ${_r(wall)} — "
                  f"a pin that favors the fade." if at_wall
                  else "Dealers long gamma (vol-suppressed) — supportive of mean-reversion, though the entry isn't right at a wall.")
    elif sign == "short":
        st = "warn"
        detail = ("Dealers are short gamma (moves get amplified) — fading here is riskier; the tape can run. "
                  "Keep size down and honor the stop.")
    else:
        st = "na"
        detail = "Dealer gamma context unavailable."
    return _mk("gamma_context", "Dealer-gamma context", st, detail,
               value={"sign": sign, "wall": _r(wall)}, weight=0.8)


# ===========================================================================
# EXIT confirmations (directional, in-progress)
# ===========================================================================

def _exit_checks(trade: dict, snap: dict, frames: dict) -> list[dict]:
    d = trade["direction"]
    is_long = d == "long"
    spot = snap["spot"]
    stop = trade.get("stop_level")
    tgts = [t for t in (trade.get("target_levels") or []) if t is not None]
    checks: list[dict] = []

    # STOP breach (critical trigger)
    if stop is not None and spot is not None:
        hit = (spot <= stop) if is_long else (spot >= stop)
        checks.append(_mk("stop_hit", "Stop level", "fail" if hit else "pass",
                          (f"Spot ${_r(spot)} has breached the ${_r(stop)} stop — exit now to cap the loss." if hit
                           else f"Stop ${_r(stop)} is intact."),
                          role="trigger"))

    # TARGET reached
    if tgts and spot is not None:
        t1 = tgts[0]
        t1_hit = (spot >= t1) if is_long else (spot <= t1)
        checks.append(_mk("target_t1", "First target", "pass" if t1_hit else "warn",
                          (f"T1 ${_r(t1)} reached — bank partial profit and trail the rest." if t1_hit
                           else f"Spot ${_r(spot)} is {_r(abs(t1-spot)/spot*100,2)}% from T1 ${_r(t1)}."),
                          role="trigger"))
        if len(tgts) > 1:
            t2 = tgts[-1]
            t2_hit = (spot >= t2) if is_long else (spot <= t2)
            checks.append(_mk("target_t2", "Final target", "pass" if t2_hit else "warn",
                              (f"Final target ${_r(t2)} reached — close the trade." if t2_hit
                               else f"Runner target ${_r(t2)} not yet reached."),
                              role="trigger"))

    # CHOCH AGAINST the position (5m/15m)
    ev15 = (snap.get("choch_15m") or {}).get("last_event") or {}
    against_dir = "bearish" if is_long else "bullish"
    if ev15.get("direction") == against_dir:
        checks.append(_mk("choch_against", "Momentum turned against you",
                          "fail", f"A 15m {ev15.get('type','break')} {against_dir} at ${ev15.get('level')} — "
                          f"lower-timeframe momentum has flipped against the position; tighten or exit.",
                          value=ev15, role="caution"))
    else:
        checks.append(_mk("choch_against", "Momentum still with you", "pass",
                          "No 15m change-of-character against the position.", role="caution"))

    # VWAP RECLAIM/LOSS against the position
    vwap = snap.get("vwap") or {}
    vw = vwap.get("vwap")
    if vw and spot is not None:
        adverse = (spot < vw) if is_long else (spot > vw)
        checks.append(_mk("vwap_side", "Intraday VWAP", "fail" if adverse else "pass",
                          (f"Price is on the wrong side of session VWAP ${vw} — the intraday advantage has flipped."
                           if adverse else f"Price holds the favorable side of session VWAP ${vw}."),
                          value=vwap.get("z"), role="caution"))

    # GAMMA-FLIP cross
    g = snap.get("gamma") or {}
    flip = (g.get("gamma_flip") or {}).get("level")
    if flip and spot is not None:
        # long profits above; caution when back under the flip (short-gamma air pocket).
        adverse = (spot < flip) if is_long else (spot > flip)
        if adverse:
            checks.append(_mk("gamma_flip", "Gamma flip", "warn",
                              (f"Back {'below' if is_long else 'above'} the gamma flip ${_r(flip)} — "
                               f"dealer hedging now {'accelerates downside' if is_long else 'builds a supportive floor'}; "
                               f"{'let winners run but watch for air pockets' if is_long else 'harder for a short to fall further'}."),
                              value=_r(flip), role="caution"))

    # R-MULTIPLE trail rule (favorable management)
    pos = _position_math(trade, spot)
    rmult = pos.get("r_multiple")
    if rmult is not None and rmult >= 1.0:
        checks.append(_mk("trail_rule", "In profit — trail the stop", "pass",
                          f"Trade is +{rmult}R. Trail the stop to at least breakeven and let the runner work toward the next target.",
                          value=rmult, role="favorable"))

    # MOMENTUM EXHAUSTION near target (favorable to book)
    rsi = snap.get("rsi_1h")
    if rsi is not None and tgts and spot is not None:
        near_t1 = abs(spot - tgts[0]) / spot < 0.01
        exhausted = (rsi > 68) if is_long else (rsi < 32)
        if near_t1 and exhausted:
            checks.append(_mk("exhaustion", "Momentum exhaustion at target", "pass",
                              f"1H RSI {rsi} at the target — momentum is stretched; a good place to take profit.",
                              value=rsi, role="favorable"))
    return checks


# ===========================================================================
# NEUTRAL (range / premium) confirmations
# ===========================================================================

def _neutral_checks(trade: dict, snap: dict, frames: dict, mode: str) -> list[dict]:
    spot = snap["spot"]
    lo = trade.get("entry_low")
    hi = trade.get("entry_high")
    checks: list[dict] = []
    inside = (lo is not None and hi is not None and lo <= spot <= hi)
    if lo is not None and hi is not None:
        checks.append(_mk("in_range", "Price inside the range",
                          "pass" if inside else "fail",
                          (f"Spot ${_r(spot)} sits between the short strikes ${_r(lo)}–${_r(hi)}." if inside
                           else f"Spot ${_r(spot)} has left the ${_r(lo)}–${_r(hi)} range — the premium structure is under threat."),
                          role="critical" if mode == "exit" else "gate"))
    # breakout CHOCH beyond the range = the enemy of a short-premium trade
    ev15 = (snap.get("choch_15m") or {}).get("last_event") or {}
    breakout = ev15 and ((ev15.get("level") or 0) >= (hi or 1e9) or (ev15.get("level") or 1e9) <= (lo or -1e9))
    checks.append(_mk("range_breakout", "No breakout", "fail" if breakout else "pass",
                      ("A 15m structural break has occurred at the range edge — the range is breaking; manage the tested side."
                       if breakout else "No structural break of the range edges — the range is holding."),
                      role="caution", weight=1.5))
    # dealers long gamma = pinning = friend of premium selling
    sign = ((snap.get("gamma") or {}).get("net_gex") or {}).get("sign")
    checks.append(_mk("gamma_pin", "Dealer gamma", "pass" if sign == "long" else ("warn" if sign == "short" else "na"),
                      ("Dealers long gamma — volatility is suppressed and price tends to pin; ideal for a range/premium trade."
                       if sign == "long" else
                       "Dealers short gamma — moves get amplified; a range trade is more exposed to a breakout."
                       if sign == "short" else "Dealer gamma unavailable."),
                      value=sign, weight=1.0))
    # VWAP not stretched (calm) supports staying in the range
    z = (snap.get("vwap") or {}).get("z")
    if z is not None:
        calm = abs(z) <= 1.2
        checks.append(_mk("vwap_calm", "Away from the σ extremes", "pass" if calm else "warn",
                          (f"Spot is only {abs(z):.1f}σ from VWAP — calm, range-friendly." if calm
                           else f"Spot is {abs(z):.1f}σ from VWAP — stretched toward a range edge; watch the tested wall."),
                          value=z, weight=0.6))
    return checks


# ===========================================================================
# verdict logic
# ===========================================================================

def _decide_entry(checks: list[dict], pos: dict, direction: str) -> dict:
    crit = [c for c in checks if c["role"] == "critical" and c["status"] == "fail"]
    gate = next((c for c in checks if c["role"] == "gate"), None)
    reached = gate is not None and gate["status"] == "pass"
    confirms = [c for c in checks if c["role"] == "confirm" and c["status"] != "na"]
    got = sum(c["weight"] * (1.0 if c["status"] == "pass" else 0.4 if c["status"] == "warn" else 0.0) for c in confirms)
    tot = sum(c["weight"] for c in confirms) or 1e-9
    pct = int(round(got / tot * 100))
    passes = [c["label"] for c in confirms if c["status"] == "pass"]
    missing = [c["label"] for c in confirms if c["status"] in ("fail", "warn")][:5]

    if crit:
        return {"verdict": "invalid", "confidence_pct": pct,
                "reasons": [c["detail"] for c in crit], "need": []}
    if not reached:
        return {"verdict": "wait", "confidence_pct": pct,
                "reasons": [gate["detail"] if gate else "Price hasn't reached the entry zone."],
                "need": ["Wait for price to tag the entry zone."] + missing}

    ltf = next((c for c in checks if c["key"] == "ltf_choch"), None)
    momentum_against = ltf is not None and ltf["status"] == "fail"
    if pct >= 65 and not momentum_against:
        return {"verdict": "execute", "confidence_pct": pct,
                "reasons": (["Confirmed: " + "; ".join(passes[:4])] if passes else
                            ["Enough confirmations have lined up at the level."]),
                "need": missing[:2]}
    if pct < 35 and momentum_against:
        return {"verdict": "invalid", "confidence_pct": pct,
                "reasons": ["At the level but lower-timeframe momentum is firmly against the trade and confirmations "
                            "are absent — the edge has decayed."], "need": []}
    return {"verdict": "wait", "confidence_pct": pct,
            "reasons": ["Price is at the level but confirmations are still forming."],
            "need": missing}


def _decide_exit(checks: list[dict], pos: dict) -> dict:
    by = {c["key"]: c for c in checks}
    stop_hit = by.get("stop_hit", {}).get("status") == "fail"
    t2_hit = by.get("target_t2", {}).get("status") == "pass"
    t1_hit = by.get("target_t1", {}).get("status") == "pass"
    caution_fail = [c for c in checks if c["role"] == "caution" and c["status"] == "fail"]

    if stop_hit:
        return {"verdict": "exit", "reasons": [by["stop_hit"]["detail"]], "need": []}
    if t2_hit:
        return {"verdict": "exit", "reasons": [by["target_t2"]["detail"]], "need": []}
    if t1_hit:
        return {"verdict": "scale_out", "reasons": [by["target_t1"]["detail"]], "need": []}
    if len(caution_fail) >= 2:
        return {"verdict": "tighten", "reasons": [c["detail"] for c in caution_fail[:3]], "need": []}
    if len(caution_fail) == 1:
        return {"verdict": "tighten", "reasons": [caution_fail[0]["detail"],
                                                  "One trigger alone — tighten the stop rather than exiting outright unless a second confirms."],
                "need": []}
    r = pos.get("r_multiple")
    hold_reason = "Thesis intact — no exit trigger. " + (f"Currently {r:+}R." if r is not None else "")
    return {"verdict": "hold", "reasons": [hold_reason.strip()], "need": []}


def _decide_neutral(checks: list[dict], mode: str) -> dict:
    by = {c["key"]: c for c in checks}
    breakout = by.get("range_breakout", {}).get("status") == "fail"
    outside = by.get("in_range", {}).get("status") == "fail"
    if mode == "exit":
        if outside or breakout:
            return {"verdict": "exit", "reasons": [(by.get("in_range") or by["range_breakout"])["detail"],
                                                   "Manage the tested side of the structure (roll or close it)."], "need": []}
        return {"verdict": "hold", "reasons": ["Price is pinned inside the range — let theta work."], "need": []}
    # entry
    if outside or breakout:
        return {"verdict": "invalid", "confidence_pct": 0,
                "reasons": [(by.get("in_range") or by.get("range_breakout"))["detail"]], "need": []}
    scored = [c for c in checks if c["key"] in ("gamma_pin", "vwap_calm")]
    ok = sum(1 for c in scored if c["status"] == "pass")
    if ok >= 1 and by.get("in_range", {}).get("status") == "pass":
        return {"verdict": "execute", "confidence_pct": 70,
                "reasons": ["Price is inside the range with supportive conditions (pinning / calm) — sell the premium."],
                "need": [c["label"] for c in scored if c["status"] != "pass"]}
    return {"verdict": "wait", "confidence_pct": 45,
            "reasons": ["Inside the range but conditions aren't ideal yet."],
            "need": [c["label"] for c in scored if c["status"] != "pass"]}


# ===========================================================================
# public entry point
# ===========================================================================

def _headline(ticker: str, verdict: str, direction: str) -> str:
    v = {
        "execute": f"{ticker}: conditions are met — execute the {direction} now.",
        "wait": f"{ticker}: not yet — wait for confirmation.",
        "invalid": f"{ticker}: conditions changed — this setup is no longer valid.",
        "hold": f"{ticker}: hold — the thesis is intact.",
        "scale_out": f"{ticker}: take partial profit and trail the rest.",
        "tighten": f"{ticker}: protect the position — tighten your stop.",
        "exit": f"{ticker}: time to close the trade.",
    }
    return v.get(verdict, f"{ticker}: {verdict}")


def evaluate_trade(stock, trade: dict) -> dict:
    """Evaluate a tracked trade against live market data.

    ``trade`` is the persisted row as a dict (direction, instrument, setup_type, status,
    entry_low/high/level, stop_level, target_levels[], executed_price/qty, setup_snapshot).
    Returns a full evaluation dict incl. a copyable ``payload``. Never raises.
    """
    try:
        frames = _fetch_frames(stock)
        snap = _snapshot(frames, stock)
        if snap is None:
            return {"ok": False, "error": "no_market_data", "as_of": _now_str(),
                    "headline": "Couldn't fetch live market data — try again shortly."}
        spot = snap["spot"]
        status = trade.get("status") or "watching"
        direction = trade.get("direction") or "neutral"
        mode = "exit" if status == "in_progress" else "entry"
        pos = _position_math(trade, spot)

        if direction == "neutral":
            checks = _neutral_checks(trade, snap, frames, mode)
            decision = _decide_neutral(checks, mode)
        elif mode == "exit":
            checks = _exit_checks(trade, snap, frames)
            decision = _decide_exit(checks, pos)
        else:
            checks = _entry_checks(trade, snap, frames)
            decision = _decide_entry(checks, pos, direction)

        verdict = decision["verdict"]
        ticker = trade.get("ticker", "")
        eval_out = {
            "ok": True,
            "as_of": snap["as_of"],
            "mode": mode,
            "spot": spot,
            "verdict": verdict,
            "verdict_label": VERDICT_LABEL.get(verdict, verdict),
            "headline": _headline(ticker, verdict, direction),
            "confidence_pct": decision.get("confidence_pct"),
            "reasons": decision.get("reasons", []),
            "need": decision.get("need", []),
            "checks": checks,
            "position": pos,
            "market": snap,
        }
        # self-contained copyable payload (also what /ask-llm ships to the model)
        eval_out["payload"] = {
            "ticker": ticker,
            "instrument": trade.get("instrument"),
            "direction": direction,
            "setup_type": trade.get("setup_type"),
            "status": status,
            "plan": {"entry_zone": [trade.get("entry_low"), trade.get("entry_high")],
                     "entry": trade.get("entry_level"), "stop": trade.get("stop_level"),
                     "targets": trade.get("target_levels"), "executed_price": trade.get("executed_price"),
                     "executed_qty": trade.get("executed_qty")},
            "as_of": snap["as_of"],
            "market": snap,
            "position": pos,
            "checks": [{k: c[k] for k in ("key", "label", "status", "detail", "value", "role")} for c in checks],
            "verdict": verdict,
            "verdict_label": VERDICT_LABEL.get(verdict, verdict),
            "reasons": decision.get("reasons", []),
            "still_needed": decision.get("need", []),
            "options_snapshot": (trade.get("setup_snapshot") or {}).get("options_plan")
            if isinstance(trade.get("setup_snapshot"), dict) else None,
        }
        return eval_out
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "eval_failed", "detail": str(exc), "as_of": _now_str(),
                "headline": "Evaluation failed — the market data may be temporarily unavailable."}
