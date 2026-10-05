"""Momentum Swing — a world-class, multi-factor momentum setup (the 3rd named strategy).

Qullamäggie encodes one trader's breakout playbook; Connors encodes mean reversion. This is the
general **momentum leader** engine the desk actually reasons with: it fuses the WHOLE stack into one
confluence score and one actionable entry, rather than firing a single indicator. Everything is
deterministic and REUSES the platform's existing engines, so every number reconciles with the
Advanced tab (institutional_ta for VP/POC/LVN, demand/supply order blocks, bull/bear FVG & swing
liquidity; regime_service for Hurst/ER; dealer for GEX/walls — best-effort).

How top momentum traders (O'Neil / Minervini / Kullamägi / Kell / Stockbee) actually frame a setup —
encoded here as 8 weighted pillars, behind 2 hard gates:

  GATES (fail → "not a momentum setup, here's why"):
    • Stage-2 uptrend     — price > 50SMA > 200SMA, 200 rising (never long momentum in a downtrend)
    • Leadership          — a real prior move / near 52w-high / positive RS vs SPY (lead, don't lag)

  PILLARS (confluence, scored over the evidence actually available):
    1. Trend & MA structure  — stacked rising 10/20/50/200 (EMA+SMA), golden cross, EMA crossovers, Hurst
    2. Relative strength      — RS line vs SPY (slope, new high), distance from 52w high, prior-move size
    3. Volatility compression — Bollinger squeeze + ATR contraction + a tight/contracting VCP base (the coil)
    4. Momentum oscillators   — MACD (zero/signal/hist), RSI (continuation-aware: >70 in trend ≠ sell), ROC
    5. Volume                 — dry-up in the base → expansion on the thrust, OBV rising, no distribution climax
    6. Structure & liquidity  — demand/supply (order blocks), POC/value area/LVN vacuum, bull/bear FVG, BSL/SSL
    7. Regime                 — trending (Hurst/ER) + best-effort regime-edge verdict that momentum WINS here
    8. Dealer / options       — above gamma flip / call wall as target & put wall as support, put/call (best-effort)

Entry is auto-detected: a **breakout** of the coil (refined by the opening-range high), a **pullback**
into a rising MA / demand zone in an intact uptrend, or an **episodic pivot** gap — whichever is live.
Stops snap to real support (base low / rising MA / demand OB / bull FVG / POC / SSL); targets to real
supply (LVN vacuum / value-area high / prior swing / 52w high / call wall). Renders through the shared
SetupCard, same as the other styles. Lazy endpoint — the heavy fusion is off the main Setups load.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .microstructure_service import _r, _now_str, _safe_history
from .trade_setup_service import _entry_style, _equity_plan, _sizing, _rr, _next_earnings
from .qullamaggie_service import _ema, _adr_pct, _pct_move, _find_base, _detect_ep, _check
from .regime_edge_service import _rsi, _atr
from .regime_service import _hurst, _efficiency_ratio, _classify
from .institutional_ta_service import compute_institutional_ta
from .day_trade_service import _opening_range, _to_date


# ---------------------------------------------------------------------------
# pure indicator helpers (numpy arrays in — no network)
# ---------------------------------------------------------------------------

def _sma(c: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(c).rolling(n).mean().to_numpy()


def _slope_pct(a: np.ndarray, n: int) -> float | None:
    """% change of a series over the last n bars (MA 'rising'/'falling')."""
    if a is None or len(a) <= n or not np.isfinite(a[-1]) or not np.isfinite(a[-1 - n]) or a[-1 - n] == 0:
        return None
    return float(a[-1] / a[-1 - n] - 1.0) * 100.0


def _macd(c: np.ndarray):
    line = _ema(c, 12) - _ema(c, 26)
    signal = _ema(line, 9)
    return line, signal, line - signal


def _bollinger(c: np.ndarray, n: int = 20, k: float = 2.0):
    s = pd.Series(c)
    mid = s.rolling(n).mean()
    sd = s.rolling(n).std(ddof=0)
    up, lo = mid + k * sd, mid - k * sd
    width = (up - lo) / mid * 100.0
    pctb = (s - lo) / (up - lo)
    return mid.to_numpy(), up.to_numpy(), lo.to_numpy(), width.to_numpy(), pctb.to_numpy()


def _last_cross(a: np.ndarray, b: np.ndarray) -> dict:
    """Most recent crossover of series a over/under b → direction + bars since + current side."""
    d = np.sign(np.asarray(a, float) - np.asarray(b, float))
    idx = None
    dirn = None
    for i in range(len(d) - 1, 0, -1):
        if np.isfinite(d[i]) and np.isfinite(d[i - 1]) and d[i] != d[i - 1] and d[i] != 0:
            idx, dirn = i, ("up" if d[i] > 0 else "down")
            break
    return {"dir": dirn, "bars_since": (len(d) - 1 - idx) if idx is not None else None,
            "above": bool(np.isfinite(a[-1]) and np.isfinite(b[-1]) and a[-1] > b[-1])}


def _obv(c: np.ndarray, v: np.ndarray) -> np.ndarray:
    o = np.zeros(len(c))
    for i in range(1, len(c)):
        o[i] = o[i - 1] + (v[i] if c[i] > c[i - 1] else -v[i] if c[i] < c[i - 1] else 0.0)
    return o


def _rsi_divergence(c: np.ndarray, rsi: np.ndarray, look: int = 40) -> bool:
    """Bearish divergence: price makes a higher high over the window but RSI makes a lower high."""
    if len(c) < look + 2:
        return False
    half = look // 2
    p_recent, p_prior = float(np.max(c[-half:])), float(np.max(c[-look:-half]))
    r_recent = float(np.max(rsi[-half:])) if np.isfinite(rsi[-half:]).any() else np.nan
    r_prior = float(np.max(rsi[-look:-half])) if np.isfinite(rsi[-look:-half]).any() else np.nan
    if not (np.isfinite(r_recent) and np.isfinite(r_prior)):
        return False
    return bool(p_recent > p_prior and r_recent < r_prior - 2.0)


# ---------------------------------------------------------------------------
# factor collection
# ---------------------------------------------------------------------------

def _collect_factors(daily, inst: dict | None, spy_df) -> dict:
    o = daily["Open"].to_numpy(float); h = daily["High"].to_numpy(float)
    l = daily["Low"].to_numpy(float); c = daily["Close"].to_numpy(float)
    v = daily["Volume"].to_numpy(float) if "Volume" in daily else np.ones_like(c)
    n = len(c)
    close = float(c[-1])

    ema10, ema20, ema50 = _ema(c, 10), _ema(c, 20), _ema(c, 50)
    sma50 = _sma(c, 50); sma150 = _sma(c, 150); sma200 = _sma(c, 200)
    s50 = float(sma50[-1]) if np.isfinite(sma50[-1]) else None
    s150 = float(sma150[-1]) if np.isfinite(sma150[-1]) else None
    s200 = float(sma200[-1]) if np.isfinite(sma200[-1]) else None

    macd_line, macd_sig, macd_hist = _macd(c)
    rsi = _rsi(c, 14)
    bb_mid, bb_up, bb_lo, bb_w, bb_pctb = _bollinger(c)
    atr = _atr(h, l, c)
    atr_now = float(atr[-1]) if np.isfinite(atr[-1]) else max(close * 0.02, 0.01)
    atr_1mo = float(atr[-21]) if n > 21 and np.isfinite(atr[-21]) else None
    adr = _adr_pct(h, l)
    obv = _obv(c, v)

    # Bollinger squeeze: current band-width in the low end of its own 6-month range
    bw_hist = bb_w[np.isfinite(bb_w)][-126:]
    bb_pctile = float((bw_hist < bb_w[-1]).mean() * 100) if bw_hist.size else None
    squeeze = bool(bb_pctile is not None and bb_pctile <= 20)

    hurst = _hurst(c[-252:]) if n >= 160 else None
    er = _efficiency_ratio(c[-252:]) if n >= 30 else None

    # relative strength vs SPY
    rs = {"slope_1m": None, "slope_3m": None, "new_high": None, "available": False}
    if spy_df is not None and not getattr(spy_df, "empty", True):
        sc = spy_df["Close"].to_numpy(float)
        m = min(len(c), len(sc))
        if m > 70:
            line = c[-m:] / sc[-m:]
            rs = {
                "slope_1m": _r(_slope_pct(line, 21), 1),
                "slope_3m": _r(_slope_pct(line, 63), 1),
                "new_high": bool(line[-1] >= np.max(line[-126:]) * 0.995) if m >= 126 else None,
                "available": True,
            }

    win52 = h[-252:] if n >= 60 else h
    low52 = l[-252:] if n >= 60 else l
    high_52w, low_52w = float(np.max(win52)), float(np.min(low52))
    moves = _pct_move(c, l)
    base = _find_base(h, l, c, adr)

    # volume: dry-up in the base vs the prior run + today's expansion; OBV slope; distribution climax
    vol = {"dry_up": False, "expansion": False, "dryup_ratio": None, "today_ratio": None,
           "obv_rising": None, "distribution_climax": False}
    if n >= 25 and float(np.sum(v[-25:])) > 0:
        avg20 = float(np.mean(v[-21:-1]))
        vol["expansion"] = bool(avg20 > 0 and v[-1] >= 1.3 * avg20)
        vol["today_ratio"] = _r(v[-1] / avg20, 2) if avg20 else None
        vol["obv_rising"] = bool(_slope_pct(obv - obv.min() + 1.0, 20) and _slope_pct(obv - obv.min() + 1.0, 20) > 0)
        if base and n >= 2 * base["days"]:
            K = base["days"]
            vp, vprior = float(np.mean(v[-K:])), float(np.mean(v[-2 * K:-K]))
            if vprior > 0:
                vol["dryup_ratio"] = _r(vp / vprior, 2)
                vol["dry_up"] = bool(vp / vprior <= 0.85)
        # distribution climax: a big down bar on heavy volume in the last 5 sessions
        for i in range(max(1, n - 5), n):
            if avg20 > 0 and v[i] >= 2.0 * avg20 and c[i] < o[i] and (o[i] - c[i]) >= 0.6 * max(h[i] - l[i], 1e-9):
                vol["distribution_climax"] = True
                break

    return {
        "close": close, "n": n, "atr": atr_now, "adr_pct": adr,
        "atr_contracting": bool(atr_1mo and atr_now < atr_1mo), "atr_1mo": atr_1mo,
        "ema10": float(ema10[-1]), "ema20": float(ema20[-1]), "ema50": float(ema50[-1]),
        "sma50": s50, "sma150": s150, "sma200": s200,
        "ema20_rising": _slope_pct(ema20, 10),
        # better of the ~3-week and ~2-week slope so a V-recovery (50-SMA just turned up after a dip
        # that rolled into its window) reads as rising, consistent with the Qullamäggie stack check.
        "sma50_rising": max([s for s in (_slope_pct(sma50, 15), _slope_pct(sma50, 10)) if s is not None], default=None),
        "sma200_rising": _slope_pct(sma200, 21),
        "stacked": bool(s50 and s200 and close > float(ema10[-1]) > float(ema20[-1]) > s50 > s200),
        "stacked_soft": bool(s50 and close > float(ema20[-1]) > s50),
        "golden_cross": _last_cross(sma50, sma200), "ema_cross_10_20": _last_cross(ema10, ema20),
        "ema_cross_20_50": _last_cross(ema20, ema50),
        "macd_line": float(macd_line[-1]), "macd_signal": float(macd_sig[-1]), "macd_hist": float(macd_hist[-1]),
        "macd_hist_rising": bool(n > 2 and macd_hist[-1] > macd_hist[-2]),
        "macd_above_zero": bool(macd_line[-1] > 0), "macd_above_signal": bool(macd_line[-1] > macd_sig[-1]),
        "rsi": _r(float(rsi[-1]), 1), "rsi_bear_div": _rsi_divergence(c, rsi),
        "roc_20": _r(_slope_pct(c, 20), 1), "roc_63": _r(_slope_pct(c, 63), 1),
        "bb_width": _r(float(bb_w[-1]), 2), "bb_pctb": _r(float(bb_pctb[-1]), 2),
        "bb_squeeze": squeeze, "bb_width_pctile": _r(bb_pctile, 0), "bb_upper": _r(float(bb_up[-1])),
        "hurst": _r(hurst, 3) if hurst is not None else None, "efficiency_ratio": er,
        "rs": rs, "high_52w": high_52w, "low_52w": low_52w,
        "pct_from_52w_high": _r((close / high_52w - 1.0) * 100, 1) if high_52w else None,
        "moves": moves, "base": base, "volume": vol,
        "_o": o, "_h": h, "_l": l, "_c": c, "_v": v, "_ema10": ema10, "_ema20": ema20,
    }


# ---------------------------------------------------------------------------
# pillar scoring
# ---------------------------------------------------------------------------

def _pillar(points: float, mx: float, checks: list) -> dict:
    return {"points": round(points, 1), "max": mx, "checks": checks}


def _st(ok, warn=False):
    return "pass" if ok else "warn" if warn else "fail"


def _score(f: dict, ctx: dict) -> dict:
    checks: list = []
    pillars: dict = {}
    close = f["close"]

    # ── Pillar 1 · Trend & MA structure (18) ──
    p, mx = 0.0, 18.0
    if f["stacked"] and (f["sma50_rising"] or 0) > 0:
        p += 9; stk = "stacked & rising"; st = "pass"
    elif f["stacked"]:
        p += 6; stk = "stacked, flat"; st = "warn"
    elif f["stacked_soft"]:
        p += 4; stk = "price>20EMA>50SMA"; st = "warn"
    else:
        stk = "not stacked"; st = "fail"
    s50s, e20s = f["sma50_rising"], f["ema20_rising"]
    rise_txt = (f"Rising test: 50-SMA slope {_r(s50s, 1) if s50s is not None else '—'}% "
                f"(best of ~3-wk/~2-wk, so a V-recovery after a dip still counts) = {'up' if (s50s or 0) > 0 else 'flat/down'}; "
                f"20-EMA slope {_r(e20s, 1) if e20s is not None else '—'}%.")
    checks.append(_check("ma_stack", "Stacked rising MAs", st, stk, "price>10>20EMA>50>200SMA, all sloping up",
                         f"10EMA ${_r(f['ema10'])} · 20EMA ${_r(f['ema20'])} · 50SMA ${_r(f['sma50'])} · 200SMA ${_r(f['sma200'])}. {rise_txt}"))
    gc = f["golden_cross"]
    gc_ok = bool(gc["above"])
    if gc_ok:
        p += 5
    checks.append(_check("golden_cross", "Golden cross (50>200)", _st(gc_ok, gc["bars_since"] is None),
                         ("50>200SMA" if gc_ok else "50<200SMA") + (f" · {gc['bars_since']}d ago" if gc["bars_since"] is not None else ""),
                         "50-SMA above 200-SMA",
                         "Golden cross = primary uptrend; a recent cross is an early momentum turn, a long-standing one confirms the trend."))
    xo = f["ema_cross_10_20"]
    if xo["above"]:
        p += 2
    hurst_ok = bool(f["hurst"] and f["hurst"] >= 0.5)
    if hurst_ok:
        p += 2
    checks.append(_check("ema_xo", "EMA crossovers", _st(xo["above"]),
                         ("10>20EMA" if xo["above"] else "10<20EMA") + (f" ({xo['bars_since']}d)" if xo["bars_since"] is not None else ""),
                         "fast EMAs above slow", f"Hurst {f['hurst']} ({'persistent/trending' if hurst_ok else 'weak/choppy'})."))
    pillars["trend_structure"] = _pillar(p, mx, checks[-3:])

    # ── Pillar 2 · Relative strength & leadership (14) ──
    p, c2 = 0.0, []
    rs = f["rs"]
    rs_ok = bool(rs["available"] and ((rs["slope_1m"] or 0) > 0 or rs["new_high"]))
    if rs["available"]:
        if rs["new_high"] and (rs["slope_1m"] or 0) > 0:
            p += 7
        elif rs_ok:
            p += 4
    rsv = (f"1m {rs['slope_1m']}% · 3m {rs['slope_3m']}%" + (" · RS new high" if rs["new_high"] else "")) if rs["available"] else "n/a"
    c2.append(_check("rel_strength", "Relative strength vs SPY", _st(rs_ok, rs["available"] and not rs_ok),
                     rsv, "RS line rising / at new highs", "Momentum is RELATIVE — leaders outperform the index; laggards that can't beat SPY are not momentum trades."))
    dfh = f["pct_from_52w_high"]
    if dfh is not None and dfh >= -15:
        p += 4; sth = "pass"
    elif dfh is not None and dfh >= -25:
        p += 2; sth = "warn"
    else:
        sth = "fail"
    c2.append(_check("near_high", "Near 52-week high", sth, f"{dfh}%" if dfh is not None else "—", "within 15% of highs",
                     f"52w high ${_r(f['high_52w'])}. Leaders make new highs first."))
    best = f["moves"]["best_pct"]
    if best is not None and best >= 30:
        p += 3; stm = "pass"
    elif best is not None and best >= 15:
        p += 1.5; stm = "warn"
    else:
        stm = "fail"
    c2.append(_check("prior_move", "Prior momentum move", stm, f"+{best:.0f}%" if best is not None else "—", "≥ +30% (1–6mo)",
                     f"1mo {f['moves']['move_1m']}% · 3mo {f['moves']['move_3m']}% · 6mo {f['moves']['move_6m']}%."))
    pillars["relative_strength"] = _pillar(p, 14.0, c2)
    checks += c2

    # ── Pillar 3 · Volatility compression → expansion (16) ──
    p, c3 = 0.0, []
    base = f["base"]
    if base:
        p += 6 if base.get("contracting") else 4
        bst, bval = ("pass", f"{base['depth_pct']}%/{base['days']}d" + (" contracting" if base.get("contracting") else ""))
        bdet = f"Tight base under ${base['high']} — the launchpad; a contracting range (VCP) is the highest-quality coil."
    else:
        bst, bval, bdet = "warn", "none", "No tight base — either extended (chase risk) or still basing wide. Best entries come from a coil."
    c3.append(_check("base", "Base / VCP contraction", bst, bval, "tight, contracting flag", bdet))
    if f["bb_squeeze"]:
        p += 5; sqst = "pass"
    elif f["bb_width_pctile"] is not None and f["bb_width_pctile"] <= 40:
        p += 2.5; sqst = "warn"
    else:
        sqst = "warn"
    c3.append(_check("bb_squeeze", "Bollinger squeeze", sqst,
                     f"width {f['bb_width']}% (pctile {f['bb_width_pctile']})", "band-width in its low 20%",
                     "A Bollinger squeeze = volatility coiled at a multi-month low; expansion out of it fuels the momentum move."))
    if f["atr_contracting"]:
        p += 5; ast = "pass"
    else:
        ast = "warn"
    c3.append(_check("atr_contract", "ATR contraction", ast,
                     f"ATR ${_r(f['atr'])}" + (f" < 1mo ${_r(f['atr_1mo'])}" if f['atr_1mo'] else ""),
                     "volatility drying up into the base", "Falling ATR into a base = energy stored for the breakout; rising ATR = already moving/extended."))
    pillars["volatility_compression"] = _pillar(p, 16.0, c3)
    checks += c3

    # ── Pillar 4 · Momentum oscillators (12) — RSI is continuation-aware ──
    p, c4 = 0.0, []
    macd_ok = f["macd_above_signal"] and f["macd_above_zero"]
    if macd_ok and f["macd_hist_rising"]:
        p += 5
    elif macd_ok:
        p += 3.5
    elif f["macd_above_signal"]:
        p += 2
    c4.append(_check("macd", "MACD", _st(macd_ok, f["macd_above_signal"]),
                     f"{'>' if f['macd_above_zero'] else '<'}0, {'>' if f['macd_above_signal'] else '<'}signal, hist {'↑' if f['macd_hist_rising'] else '↓'}",
                     "above zero & signal, histogram rising", "MACD above zero AND its signal with a rising histogram = momentum accelerating."))
    rsiv = f["rsi"]
    # continuation-aware: 55–80 is the momentum sweet spot; >70 is NOT a sell in an uptrend unless divergence
    if rsiv is not None and f["rsi_bear_div"]:
        rst, rpts = "warn", 1.0
        rdet = "RSI bearish divergence — price made a higher high but RSI didn't. Momentum fading; demand confirmation."
    elif rsiv is not None and 55 <= rsiv <= 82:
        rst, rpts = "pass", 4.0
        rdet = f"RSI {rsiv} in the momentum sweet spot. In a confirmed uptrend RSI>70 is CONTINUATION, not a sell."
    elif rsiv is not None and rsiv > 82:
        rst, rpts = "warn", 2.5
        rdet = f"RSI {rsiv} very hot — fine in a power trend, but late to initiate; prefer a pullback entry."
    elif rsiv is not None and 45 <= rsiv < 55:
        rst, rpts = "warn", 2.0
        rdet = f"RSI {rsiv} neutral — momentum not yet asserting."
    else:
        rst, rpts = "fail", 0.0
        rdet = f"RSI {rsiv} weak — not a momentum profile."
    p += rpts
    c4.append(_check("rsi", "RSI (momentum)", rst, f"{rsiv}", "55–80 (continuation)", rdet))
    roc = f["roc_20"]
    if roc is not None and roc > 0:
        p += 3 if roc >= 5 else 1.5
    c4.append(_check("roc", "Rate of change", _st(roc is not None and roc > 0),
                     f"20d {roc}% · 63d {f['roc_63']}%", "positive & accelerating", "Price rate-of-change confirms the thrust."))
    pillars["momentum_oscillators"] = _pillar(p, 12.0, c4)
    checks += c4

    # ── Pillar 5 · Volume (10) ──
    p, c5 = 0.0, []
    vol = f["volume"]
    if vol["dry_up"] and vol["expansion"]:
        p += 6; vst, vval = "pass", "dry base + surge"
    elif vol["dry_up"]:
        p += 4.5; vst, vval = "pass", "base drying up"
    elif vol["expansion"]:
        p += 3; vst, vval = "warn", "surge, base not quiet"
    else:
        vst, vval = "warn", "flat"
    vdet = (f"Base {vol['dryup_ratio']}× the prior run" if vol["dryup_ratio"] else "Volume") + \
           (f"; today {vol['today_ratio']}× the 20-day avg." if vol["today_ratio"] else ".")
    c5.append(_check("volume", "Volume (dry-up → expansion)", vst, vval, "quiet base, surge on the break", vdet))
    if vol["obv_rising"]:
        p += 2
    c5.append(_check("obv", "OBV accumulation", _st(bool(vol["obv_rising"])),
                     "rising" if vol["obv_rising"] else "flat/falling", "OBV trending up", "On-balance volume rising = accumulation under the move."))
    if vol["distribution_climax"]:
        p -= 2
        c5.append(_check("climax", "Distribution check", "fail", "heavy down-bar", "no distribution",
                         "A high-volume down bar in the last week = institutional distribution; a red flag under a breakout."))
    else:
        p += 2
        c5.append(_check("climax", "Distribution check", "pass", "clean", "no distribution", "No high-volume selling climax recently."))
    pillars["volume"] = _pillar(max(0.0, p), 10.0, c5)
    checks += c5

    # ── Pillar 6 · Structure, demand/supply & liquidity (14) ──
    p, c6 = 0.0, []
    inst = ctx.get("inst") or {}
    vp = inst.get("volume_profile") or {}
    obs = inst.get("order_blocks") or []
    fvgs = inst.get("fair_value_gaps") or []
    ms = inst.get("market_structure") or {}
    demand = [b for b in obs if b.get("type") == "bullish" and not b.get("mitigated") and b.get("top", 1e9) < close]
    bull_fvg = [g for g in fvgs if g.get("type") == "bullish" and not g.get("filled") and (g.get("mid") or 1e9) < close]
    supp = max([b["top"] for b in demand] + [g["mid"] for g in bull_fvg] + ([vp["val"]] if vp.get("val") and vp["val"] < close else []) + ([f["ema20"]] if f["ema20"] < close else []), default=None)
    near_support = bool(supp and (close - supp) / close <= 0.08)
    if demand or bull_fvg:
        p += 4
    c6.append(_check("demand", "Demand below (stop base)", _st(bool(demand or bull_fvg), near_support),
                     f"{len(demand)} OB · {len(bull_fvg)} FVG", "a demand zone to stop under",
                     "Unmitigated bullish order blocks / fair-value gaps below = where buyers step in; the stop goes under them, not at a round number."))
    # path to supply: room above to VAH / swing high / 52w high
    res_above = [x for x in [vp.get("vah"), ms.get("recent_swing_high"), f["high_52w"], ctx.get("call_wall")] if x and x > close]
    room = ((min(res_above) / close - 1.0) * 100.0) if res_above else None
    if room is not None and room >= 4:
        p += 4; rmst = "pass"
    elif room is not None and room >= 2:
        p += 2; rmst = "warn"
    else:
        rmst = "warn"
    c6.append(_check("room", "Room to supply", rmst, f"+{room:.1f}% to next" if room is not None else "clear",
                     "≥4% clear path to resistance", "Clear air (LVN vacuum / distance to the next supply) lets a breakout travel before hitting sellers."))
    bsl, ssl = ms.get("recent_swing_high"), ms.get("recent_swing_low")
    if ms.get("trend") == "up":
        p += 3
    if ms.get("change_of_character"):
        p -= 1.5
    c6.append(_check("liquidity", "Structure & liquidity (BSL/SSL)", _st(ms.get("trend") == "up", not ms.get("change_of_character")),
                     f"trend {ms.get('trend','?')}" + (" · CHoCH" if ms.get("change_of_character") else ""),
                     "uptrend, BSL above as target",
                     f"Buy-side liquidity ${_r(bsl)} above (target), sell-side ${_r(ssl)} below (invalidation). POC ${_r(vp.get('poc'))}."))
    if vp.get("poc") and abs(close / vp["poc"] - 1.0) <= 0.03:
        p += 3  # coiling at the POC (fair value) is a high-energy launch spot
    pillars["structure_liquidity"] = _pillar(max(0.0, p), 14.0, c6)
    checks += c6

    # ── Pillar 7 · Regime (10) ──
    p, c7 = 0.0, []
    reg = ctx.get("regime") or {}
    trending = reg.get("regime") == "trending"
    if trending:
        p += 5
    elif reg.get("regime") == "transitional":
        p += 2.5
    c7.append(_check("regime", "Market regime", _st(trending, reg.get("regime") == "transitional"),
                     f"{reg.get('label','?')} (ER {f['efficiency_ratio']})", "trending favors momentum",
                     reg.get("playbook", "Trending regimes reward momentum continuation; choppy regimes chop breakouts up.")))
    edge = ctx.get("regime_edge")
    if edge:
        v = edge.get("verdict")
        if v == "confirmed":
            p += 5
        elif v == "weak":
            p += 2
        c7.append(_check("regime_edge", "Regime-edge (backtested)", _st(v == "confirmed", v == "weak"),
                         edge.get("summary", v or "—"), "breakout edge confirmed in this regime",
                         "The regime-edge engine's measured verdict: does a breakout/pullback actually WIN on this name in the current regime?"))
    else:
        c7.append(_check("regime_edge", "Regime-edge (backtested)", "warn", "n/a", "—",
                         "Regime-edge not evaluated for this name (best-effort) — see the Regime-edge panel."))
    pillars["regime"] = _pillar(p, 10.0 if edge else 5.0, c7)
    checks += c7

    # ── Pillar 8 · Dealer / options (6, best-effort) ──
    dealer = ctx.get("dealer")
    if dealer:
        p, c8 = 0.0, []
        above_flip = dealer.get("above_flip")
        if above_flip:
            p += 2.5
        c8.append(_check("gamma", "Dealer gamma", _st(bool(above_flip), above_flip is None),
                         f"net GEX {dealer.get('net_gex_label','?')}" + (f", flip ${_r(dealer.get('gamma_flip'))}" if dealer.get("gamma_flip") else ""),
                         "above the gamma flip",
                         "Above the gamma flip / supportive positive GEX = dealers cushion dips; below the flip, moves get amplified."))
        cw = dealer.get("call_wall")
        if cw and cw > close:
            p += 2
        c8.append(_check("walls", "Call/put walls", _st(bool(cw)),
                         (f"call wall ${_r(cw)}" if cw else "—") + (f" · put wall ${_r(dealer.get('put_wall'))}" if dealer.get("put_wall") else ""),
                         "call wall above as magnet/target", "The call wall above acts as a target/magnet; the put wall below is dealer-supported support."))
        sign = dealer.get("net_gex_sign")
        if sign == "long":
            p += 1.5
        c8.append(_check("netgex", "Net GEX regime", _st(sign == "long", sign is not None),
                         dealer.get("net_gex_label", "—"), "long gamma (dealers cushion dips)",
                         "Net dealer gamma: long = dips bought (supports a swing hold); short = moves amplified (sharper, two-way)."
                         + (f" Expected 30d move ±${_r(dealer.get('expected_move'))}." if dealer.get("expected_move") else "")))
        pillars["dealer_options"] = _pillar(p, 6.0, c8)
        checks += c8

    total = sum(pl["points"] for pl in pillars.values())
    mx = sum(pl["max"] for pl in pillars.values())
    score = round(total / mx * 100) if mx > 0 else 0
    return {"pillars": pillars, "checks": checks, "score": int(score)}


# ---------------------------------------------------------------------------
# setup builders (SetupCard shape — mirrors qullamaggie's _qm_setup, style="momentum")
# ---------------------------------------------------------------------------

def _mom_setup(kind, direction, entry, stop, targets, spot, atr, thesis, evidence, horizon, watch,
               regime_fit="with_regime", base_score=6.0) -> dict | None:
    tgt = [t for t in targets if t and t.get("level") is not None and t["level"] != entry]
    if not (entry and stop and spot and tgt and entry != stop):
        return None
    t1 = tgt[0]["level"]
    rr = _rr(entry, stop, t1)
    if not rr or rr <= 0:
        return None
    for t in tgt:
        t["rr"] = _rr(entry, stop, t["level"])
    return {
        "type": kind, "direction": direction, "regime_fit": regime_fit, "style": "momentum",
        "confidence": "high" if rr >= 2.5 else "medium" if rr >= 1.5 else "low",
        "score": round(base_score + min(rr, 4), 2),
        "entry": {"low": _r(entry), "high": _r(entry), "level": _r(entry), "label": kind.replace("_", " ")},
        "stop": {"level": _r(stop), "label": "structure stop"},
        "targets": tgt, "risk_reward": rr,
        "sizing": _sizing(entry, stop, t1, spot, None),
        "options": {"structure": "—", "detail": "Momentum swing is a shares trade — express it with position size on a tight structure stop.", "bias": direction},
        "options_plan": {"available": False, "structure": "—", "note": "Shares strategy; leverage comes from size on a tight stop, not options."},
        "equity_plan": _equity_plan(direction, entry, stop, tgt, spot),
        "edge": {}, "event_risk": None,
        "entry_style": _entry_style(direction, entry, spot, atr),
        "horizon": horizon,
        "from_current": {"to_entry_pct": round((entry - spot) / spot * 100, 2) if spot else None,
                         "to_t1_pct": round((t1 - spot) / spot * 100, 2) if spot else None},
        "thesis": thesis, "evidence": [e for e in evidence if e], "what_to_watch": watch,
    }


def _supports_below(f, inst, entry):
    """Real support candidates under `entry`, highest-first (tightest valid stop base)."""
    vp = (inst or {}).get("volume_profile") or {}
    obs = (inst or {}).get("order_blocks") or []
    fvgs = (inst or {}).get("fair_value_gaps") or []
    ms = (inst or {}).get("market_structure") or {}
    cands = []
    for b in obs:
        if b.get("type") == "bullish" and not b.get("mitigated") and b.get("bottom", 1e9) < entry:
            cands.append(("demand OB", b["bottom"]))
    for g in fvgs:
        if g.get("type") == "bullish" and not g.get("filled") and (g.get("bottom") or 1e9) < entry:
            cands.append(("bull FVG", g["bottom"]))
    for lbl, lv in [("20-EMA", f["ema20"]), ("50-SMA", f["sma50"]), ("value-area low", vp.get("val")),
                    ("POC", vp.get("poc")), ("swing low (SSL)", ms.get("recent_swing_low")),
                    ("base low", (f["base"] or {}).get("low"))]:
        if lv and lv < entry:
            cands.append((lbl, lv))
    return sorted(cands, key=lambda x: -x[1])


def _targets_above(f, inst, entry, atr, call_wall=None):
    vp = (inst or {}).get("volume_profile") or {}
    ms = (inst or {}).get("market_structure") or {}
    out = []
    for lbl, lv in [("value-area high", vp.get("vah")), ("swing high (BSL)", ms.get("recent_swing_high")),
                    ("52-week high", f["high_52w"]), ("call wall", call_wall)]:
        if lv and lv > entry:
            out.append((lbl, float(lv)))
    out.sort(key=lambda x: x[1])
    return out


def _pick_stop(f, inst, entry, atr):
    for lbl, lv in _supports_below(f, inst, entry):
        if (entry - lv) <= 2.2 * atr and (entry - lv) >= 0.3 * atr:
            return round(lv, 2), lbl
    return round(entry - 1.5 * atr, 2), "1.5× ATR"


def _two_targets(f, inst, entry, atr, call_wall, depth=None, thrust=(3.0, 5.0)):
    """Momentum targets are primarily an ATR THRUST (a breakout to new highs has no overhead supply, so
    the nearby 52w-high / VAH is NOT the target — the move is). Real supply above (call wall / VAH /
    swing high) is woven in only when it sits ≥1 ATR above entry, as a nearer scale-out / context."""
    out = [{"level": round(entry + k * atr, 2), "label": f"{k:g}× ATR thrust"} for k in thrust]
    for lbl, lv in _targets_above(f, inst, entry, atr, call_wall):
        if lv >= entry + 1.0 * atr:                      # ignore levels hugging the entry (not real targets)
            out.append({"level": round(lv, 2), "label": lbl})
    out.sort(key=lambda t: t["level"])
    dedup: list = []
    for t in out:
        if not any(abs(t["level"] - x["level"]) < 0.6 * atr for x in dedup):
            dedup.append(t)
    return dedup[:3]


def _breakout(f, inst, ctx, spot, atr, orange):
    base = f["base"]
    if not base:
        return None
    pivot = base["high"]
    if spot < pivot * 0.90:                  # too far below the pivot — not a breakout yet
        return None
    trig = max(0.03 * atr, 0.001 * spot)
    orh = (orange or {}).get("high")
    if orh and orh >= pivot:
        entry = round(orh + trig, 2); en = f"opening-range high ${_r(orh)} (breakout live above the ${pivot} pivot)"
    else:
        entry = round(pivot + trig, 2); en = f"break of the ${pivot} base pivot (refine with the 5-min opening-range high)"
    stop, slbl = _pick_stop(f, inst, entry, atr)
    depth = (base["high"] - base["low"]) if base else None
    tt = _two_targets(f, inst, entry, atr, ctx.get("call_wall"), depth=depth)
    horizon = {"label": "Momentum swing", "style": "momentum",
               "note": "Breakout of the coil — hold the thrust, scale into strength, then trail the 10/20-day EMA while the trend holds."}
    vol = f["volume"]
    watch = [
        f"Confirm the break on a volume EXPANSION ({'base already dried up' if vol['dry_up'] else 'base not especially quiet'}) — a break on light volume fails more often.",
        f"Trail: 10-EMA ${_r(f['ema10'])} (aggressive) / 20-EMA ${_r(f['ema20'])} (looser); exit on a daily close below the trail.",
        f"Invalidation: a close back inside the base (below ${slbl} ${_r(stop)}) = failed breakout — take the stop, no averaging down.",
    ]
    thesis = (f"Momentum breakout — a leader (RS {f['rs'].get('slope_1m')}% vs SPY, +{(f['moves']['best_pct'] or 0):.0f}% prior move) "
              f"coiled in a {base['days']}-day base ({base['depth_pct']}% deep{', VCP-contracting' if base.get('contracting') else ''}"
              f"{', Bollinger squeeze' if f['bb_squeeze'] else ''}) under ${pivot}. Buy the {en}; stop under {slbl} ${_r(stop)}; "
              f"targets at real supply (then trail).")
    ev = [f"{base['depth_pct']}%/{base['days']}d base", f"from 52w high {f['pct_from_52w_high']}%",
          f["bb_squeeze"] and "BB squeeze" or "", f["golden_cross"]["above"] and "golden cross" or "",
          vol["dry_up"] and f"vol dry-up {vol['dryup_ratio']}×" or "", orh and f"ORH ${_r(orh)}" or ""]
    return _mom_setup("momentum_breakout", "long", entry, stop, tt, spot, atr, thesis, ev, horizon, watch, base_score=7.0)


def _pullback(f, inst, ctx, spot, atr):
    # buy a pullback into a rising MA / demand zone inside an intact uptrend (not extended)
    if not f["stacked_soft"]:
        return None
    ema20, ema50 = f["ema20"], f["ema50"]
    extended = (spot / f["ema10"] - 1.0) >= 0.12 or (spot - f["ema10"]) >= 3.0 * atr
    if extended:
        return None
    lows = f["_l"]; closes = f["_c"]
    tagged_ma = None
    for lbl, ma in [("20-EMA", ema20), ("50-SMA", f["sma50"])]:
        if ma and float(np.min(lows[-5:])) <= ma * 1.02 and spot > ma:   # dipped to the MA in the last week & back above
            tagged_ma = (lbl, ma); break
    if not tagged_ma:
        return None
    mlbl, ma = tagged_ma
    trig = max(0.05 * atr, 0.001 * spot)
    entry = round(max(spot, float(closes[-1])) + trig, 2)                # trigger just above the reclaim bar
    stop, slbl = _pick_stop(f, inst, entry, atr)
    if stop >= ma:                                                       # keep the stop under the MA being bought
        stop = round(min(stop, ma - 0.5 * atr), 2); slbl = f"under {mlbl}"
    tt = _two_targets(f, inst, entry, atr, ctx.get("call_wall"))
    horizon = {"label": "Momentum pullback", "style": "momentum",
               "note": "Buy strength on a pullback to a rising MA / demand zone in an uptrend — lower-risk entry than chasing the breakout."}
    watch = [
        f"Entry confirms on a reclaim bar closing back above the {mlbl} ${_r(ma)} on improving volume.",
        f"Trail: 10/20-EMA; exit on a daily close below {slbl} ${_r(stop)}.",
        "Pullback invalidation: a decisive close below the rising MA breaks the uptrend structure — stand aside.",
    ]
    thesis = (f"Momentum pullback — an intact uptrend (stacked rising MAs, RS {f['rs'].get('slope_1m')}%) just pulled back "
              f"to its {mlbl} ${_r(ma)} and is turning back up. Buy the reclaim ${_r(entry)}; stop {slbl} ${_r(stop)}; "
              f"ride the resumption toward prior highs.")
    ev = [f"pullback to {mlbl}", f"from 52w high {f['pct_from_52w_high']}%",
          f["golden_cross"]["above"] and "golden cross" or "", f"RSI {f['rsi']}",
          (f['volume'].get('obv_rising')) and "OBV rising" or ""]
    return _mom_setup("momentum_pullback", "long", entry, stop, tt, spot, atr, thesis, ev, horizon, watch, base_score=6.5)


def _ep(f, inst, ctx, spot, atr, orange):
    ep = _detect_ep(f["_o"], f["_h"], f["_l"], f["_c"], f["_v"])
    if not ep or ep.get("extended"):
        return None
    pivot = ep["gap_high"]
    trig = max(0.03 * atr, 0.001 * spot)
    orh = (orange or {}).get("high")
    if ep["bars_since"] == 0 and orh and orh >= pivot:
        entry = round(orh + trig, 2); en = f"opening-range high ${_r(orh)} on the gap day"
    else:
        entry = round(pivot + trig, 2); en = f"break of the gap-day high ${pivot}"
    stop = round(ep["gap_low"], 2)
    if (entry - stop) > 2.5 * atr:
        stop = round(entry - 1.8 * atr, 2)
    tt = _two_targets(f, inst, entry, atr, ctx.get("call_wall"))
    horizon = {"label": "Episodic pivot", "style": "momentum",
               "note": "A gap-up on a catalyst out of a base — momentum ignition; hold the thrust, trail the 10/20-day EMA."}
    watch = [f"Hold above the gap-day low ${_r(stop)} — a fill of the gap kills the thesis.",
             f"Trail: 10/20-EMA; scale into the thrust.",
             "EP works when the catalyst re-rates the name — size with the gap's follow-through, not against it."]
    thesis = (f"Episodic pivot — a {ep['gap_pct']:.0f}% gap-up on {ep['vol_mult']:.0f}× volume out of a base "
              f"{ep['bars_since']} session(s) ago. Buy the {en}; stop the gap-day low ${_r(stop)}.")
    ev = [f"gap +{ep['gap_pct']:.0f}%", f"vol {ep['vol_mult']:.0f}×", f"from 52w high {f['pct_from_52w_high']}%"]
    return _mom_setup("momentum_episodic_pivot", "long", entry, stop, tt, spot, atr, thesis, ev, horizon, watch, base_score=6.0)


# ---------------------------------------------------------------------------
# best-effort context enrichment (dealer, regime-edge) — never blocks
# ---------------------------------------------------------------------------

def _dealer_context(stock) -> dict | None:
    try:
        from .dealer_positioning_service import compute_dealer_positioning
        d = compute_dealer_positioning(stock)
        if not d:
            return None
        spot = d.get("price")
        flip = (d.get("gamma_flip") or {}).get("level")
        net = d.get("net_gex") or {}
        walls = d.get("walls") or {}
        cw = (walls.get("call_wall") or {}).get("strike")
        pw = (walls.get("put_wall") or {}).get("strike")
        return {
            "above_flip": bool(spot and flip and spot > flip) if (spot and flip) else None,
            "gamma_flip": flip,
            "net_gex_sign": net.get("sign"),
            "net_gex_label": "long γ (vol suppressed, dips bought)" if net.get("sign") == "long"
                             else "short γ (vol amplified)" if net.get("sign") == "short" else "?",
            "call_wall": cw, "put_wall": pw,
            "expected_move": (d.get("expected_move") or {}).get("em_30d"),
        }
    except Exception:  # noqa: BLE001
        return None


def _regime_edge_context(stock) -> dict | None:
    try:
        from .regime_edge_service import compute_regime_edge
        r = compute_regime_edge(stock)
        if not r:
            return None
        bo = next((s for s in r["signals"] if s["key"] == "breakout"), None)
        if not bo:
            return None
        cur = bo.get("current") or {}
        return {"verdict": bo.get("current_edge"),
                "summary": f"breakout {int(cur.get('win_rate') or 0)}% win, {cur.get('expectancy')}R (n={cur.get('n', 0)}) in {r['current_regime']['label']}",
                "regime": r["current_regime"]["label"]}
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def compute_momentum_setup(stock, deep: bool = True) -> dict | None:
    """Multi-factor momentum-swing confluence + the live entry (breakout / pullback / EP).
    Best-effort; returns None only when there's no usable daily history."""
    try:
        daily = _safe_history(stock, "2y", "1d")
        if daily is None or getattr(daily, "empty", True) or len(daily) < 60:
            return None
        spot = float(daily["Close"].to_numpy(float)[-1])

        o = daily["Open"].to_numpy(float); h = daily["High"].to_numpy(float)
        l = daily["Low"].to_numpy(float); c = daily["Close"].to_numpy(float)
        v = daily["Volume"].to_numpy(float) if "Volume" in daily else np.ones_like(c)
        inst = compute_institutional_ta(o.tolist(), h.tolist(), l.tolist(), c.tolist(), v.tolist(),
                                        float(_rsi(c, 14)[-1]))

        spy_df = None
        try:
            import yfinance as yf
            spy_df = _safe_history(yf.Ticker("SPY"), "1y", "1d")
        except Exception:  # noqa: BLE001
            spy_df = None

        d5 = _safe_history(stock, "5d", "5m")
        orange = _opening_range(d5) if d5 is not None else None

        f = _collect_factors(daily, inst, spy_df)
        atr = f["atr"]

        # regime (local, consistent with the regime engine)
        reg_cls = _classify(_hurst(c[-252:]) if len(c) >= 160 else None, f["efficiency_ratio"])
        regime = {"regime": reg_cls["regime"], "label": {"trending": "Trending", "mean_reverting": "Mean-Reverting",
                  "transitional": "Transitional"}.get(reg_cls["regime"], "Transitional"),
                  "playbook": reg_cls.get("playbook")}

        ctx = {"inst": inst, "regime": regime}

        # hard gates
        gate_trend = bool(f["sma50"] and f["sma200"] and spot > f["sma50"] and f["sma50"] > f["sma200"]
                          and (f["sma200_rising"] or 0) >= 0)
        lead_move = (f["moves"]["best_pct"] or 0) >= 10
        lead_high = f["pct_from_52w_high"] is not None and f["pct_from_52w_high"] >= -25
        lead_rs = bool(f["rs"]["available"] and (f["rs"]["slope_1m"] or 0) > 0)
        gate_lead = bool(lead_move or lead_high or lead_rs)

        # best-effort heavy context: only when the cheap gates pass (don't pay for non-candidates)
        if deep and gate_trend and gate_lead:
            dealer = _dealer_context(stock)
            if dealer:
                ctx["dealer"] = dealer
                ctx["call_wall"] = dealer.get("call_wall")
            edge = _regime_edge_context(stock)
            if edge:
                ctx["regime_edge"] = edge

        sc = _score(f, ctx)
        score = sc["score"]

        # build setups (auto-detected mode)
        setups = []
        if gate_trend and gate_lead:
            for builder in (_breakout, _pullback):
                s = builder(f, inst, ctx, spot, atr, orange) if builder is _breakout else builder(f, inst, ctx, spot, atr)
                if s:
                    setups.append(s)
        ep_s = _ep(f, inst, ctx, spot, atr, orange)
        if ep_s:
            setups.append(ep_s)
        setups.sort(key=lambda s: -(s.get("score") or 0))
        for i, s in enumerate(setups):
            s["rank"] = i + 1

        gates_ok = gate_trend and gate_lead
        is_candidate = bool(gates_ok and score >= 60)
        grade = "A" if score >= 80 else "B" if score >= 65 else "C" if score >= 50 else "—"
        entry_mode = setups[0]["type"].replace("momentum_", "").replace("_", " ") if setups else None

        if not gate_trend:
            summary = "Not a momentum setup — the stock isn't in a Stage-2 uptrend (needs price > 50-SMA > 200-SMA, 200 rising). Momentum longs require an established uptrend."
        elif not gate_lead:
            summary = "In an uptrend but NOT a leader — no real prior move, well off its 52-week high, and not outperforming SPY. Momentum trades the strongest names, not laggards."
        elif is_candidate and setups:
            summary = (f"A momentum candidate (grade {grade}, {score}/100) with a live {entry_mode} entry — "
                       f"a {regime['label'].lower()}-regime leader with confluence across trend, volatility, volume & structure.")
        elif is_candidate:
            summary = (f"A momentum leader (grade {grade}, {score}/100) but no trigger yet — it's either extended (wait for a pullback) "
                       f"or still basing. Watch for a tight coil to break or a pullback into a rising MA.")
        else:
            misses = [c["label"] for c in sc["checks"] if c["status"] == "fail"]
            summary = f"Partial momentum profile ({score}/100) — weak on: {', '.join(misses[:4])}." if misses else f"Partial momentum profile ({score}/100) — not enough confluence to qualify."

        qualification = {"is_candidate": is_candidate, "grade": grade, "score": score,
                         "checks": sc["checks"], "summary": summary,
                         "passes": sum(1 for c in sc["checks"] if c["status"] == "pass"),
                         "entry_mode": entry_mode, "gates": {"uptrend": gate_trend, "leadership": gate_lead}}

        return {
            "price": _r(spot), "as_of": _now_str(), "atr": _r(atr), "adr_pct": _r(f["adr_pct"], 1),
            "qualification": qualification,
            "pillars": {k: {"points": p["points"], "max": p["max"]} for k, p in sc["pillars"].items()},
            "moving_averages": {"ema10": _r(f["ema10"]), "ema20": _r(f["ema20"]), "ema50": _r(f["ema50"]),
                                "sma50": _r(f["sma50"]), "sma150": _r(f["sma150"]), "sma200": _r(f["sma200"])},
            "metrics": {
                "rsi": f["rsi"], "macd_hist": _r(f["macd_hist"], 3), "adr_pct": _r(f["adr_pct"], 1),
                "bb_squeeze": f["bb_squeeze"], "bb_width_pctile": f["bb_width_pctile"],
                "atr_contracting": f["atr_contracting"], "hurst": f["hurst"], "efficiency_ratio": f["efficiency_ratio"],
                "golden_cross": f["golden_cross"], "rs": f["rs"], "moves": f["moves"],
                "pct_from_52w_high": f["pct_from_52w_high"], "high_52w": _r(f["high_52w"]), "low_52w": _r(f["low_52w"]),
                "base": f["base"], "volume": f["volume"], "regime": regime,
                "regime_edge": ctx.get("regime_edge"), "dealer": ctx.get("dealer"),
            },
            "setups": setups[:3],
            "meta": {"has_intraday": bool(orange), "deep": bool(ctx.get("dealer") or ctx.get("regime_edge")),
                     "note": "Momentum-swing confluence: 8 weighted pillars behind 2 hard gates (Stage-2 uptrend + leadership), "
                             "fusing trend/MA structure, relative strength, volatility compression, oscillators, volume, "
                             "demand-supply & liquidity, regime and dealer positioning. Deterministic; stops/targets snap to real structure."},
        }
    except Exception:  # noqa: BLE001
        return None
