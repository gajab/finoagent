"""Larry Connors' 2-Period RSI — the second NAMED technical strategy (after Qullamäggie).

Connors' RSI-2 (from *Short Term Trading Strategies That Work*) is a high-win-rate MEAN-REVERSION
system — the opposite temperament to Qullamäggie's momentum breakout. Everything here is computed
deterministically from real daily bars (the user-trust principle) and rendered through the shared
SetupCard, so a Connors card looks and tracks like any other setup.

The rules encoded (long; the short side is the mirror below the 200-SMA):
  1. **Trend filter** — price above the 200-day SMA (only fade pullbacks *with* the long-term trend).
  2. **Oversold trigger** — **RSI(2) < 10** (classic), **< 5** = higher conviction. RSI(5)/RSI(10) are
     shown as context (less extreme). A 2-day **cumulative RSI(2) < 35** is a secondary confirmation.
  3. **Pullback** — price below the 5-day SMA (a genuine short-term dip).
  4. **Exit** — the CLOSE back **above the 5-day SMA** (mean reversion complete), or RSI(2) > ~65–70.
     This is a *rule*, not a fixed target — the 5-SMA is shown as the objective for R:R reference.
  5. **Stops** — Connors' own research found fixed stops REDUCE this system's returns, so the stop
     here is a *catastrophe* stop (a wide ATR/swing stop) plus a ~10-day time stop; the real risk
     control is small size + the 200-SMA filter.

**VIX ("buy the panic"):** when the VIX is stretched well above its 10-day average (a fear spike /
black-swan tape), an RSI-2 oversold long is historically the highest-probability window — the fear is
usually overdone short-term. We compute the VIX's %-above-its-10-SMA and its own RSI(2); a fear spike
boosts conviction and can arm a (slightly relaxed) "panic" long. Honest caveat surfaced on the card:
in a *true* regime-change crash, mean-reversion can keep failing, so the size/time-stop limits matter.

**Execution** (the hard part, surfaced explicitly): the signal is close-based, so the faithful entry
is market-on-close / a limit at ≤ the signal close — NOT a market order into the volatile open, which
usually gaps away and misses the bounce. The overnight hold is where the edge lives; size for gap risk.

A quick **in-sample backtest** of the exact rules on this name's own history is included so the
(often sub-1) R:R is put in context by the real win rate — Connors' whole edge is frequency, not R:R.

Kept in its OWN lazy endpoint (`/connors-rsi-setup`) — one extra ~18-month daily pull + one small VIX
fetch — so the main Setups load never pays for it (Cloud Run one-instance budget).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .microstructure_service import _r, _now_str, _safe_history
from .trade_tracking_service import _atr
from .trade_setup_service import _equity_plan, _sizing, _rr


# ---------------------------------------------------------------------------
# thresholds (Connors' documented defaults)
# ---------------------------------------------------------------------------

_RSI_BUY = 10.0            # RSI(2) < 10 = classic oversold long trigger
_RSI_BUY_STRONG = 5.0      # < 5 = higher conviction
_RSI_BUY_PANIC = 25.0      # relaxed trigger when the VIX confirms a fear spike
_RSI_SELL = 90.0           # RSI(2) > 90 = overbought short trigger (below the 200-SMA)
_RSI_SELL_STRONG = 95.0
_RSI_EXIT = 65.0           # exit when RSI(2) recovers above ~65 (Connors cumulative-RSI exit)
_CUM_RSI_BUY = 35.0        # 2-day sum of RSI(2) < 35 = cumulative-RSI confirmation
_VIX_STRETCH = 0.08        # VIX ≥ 8% above its 10-day SMA = a fear spike ("buy the panic")
_MAXHOLD = 10              # time stop (sessions) — Connors exits stale trades


# ---------------------------------------------------------------------------
# indicators (pure — Wilder RSI as a Series, matching cycle_sector_ta._rsi)
# ---------------------------------------------------------------------------

def _rsi_series(closes: pd.Series, period: int) -> pd.Series:
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_g = gain.ewm(com=period - 1, adjust=False).mean()     # com=period-1 ⇒ Wilder smoothing
    avg_l = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_g / avg_l.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.where(~((avg_l == 0) & (avg_g > 0)), 100.0)      # all gains, no losses ⇒ RSI 100
    rsi = rsi.where(~((avg_l == 0) & (avg_g == 0)), 50.0)      # perfectly flat ⇒ neutral 50 (NOT 100)
    return rsi.fillna(50.0)                                    # warmup bars ⇒ neutral


def _backtest(closes: np.ndarray, sma5: np.ndarray, sma200: np.ndarray, rsi2: np.ndarray,
              side: str = "long", buy: float = _RSI_BUY, maxhold: int = _MAXHOLD) -> dict | None:
    """In-sample backtest of the EXACT rules (no stop, exit on the 5-SMA cross or the time stop) so
    the win rate / expectancy contextualise the small R:R. Non-overlapping, close-to-close."""
    n = len(closes)
    trades: list[dict] = []
    in_pos, entry_i = False, None
    for i in range(1, n):
        s200, s5, r = sma200[i], sma5[i], rsi2[i]
        if np.isnan(s200) or np.isnan(s5) or np.isnan(r):
            continue
        if not in_pos:
            armed = (closes[i] > s200 and r < buy) if side == "long" else (closes[i] < s200 and r > (100 - buy))
            if armed:
                in_pos, entry_i = True, i
        else:
            held = i - entry_i
            done = (closes[i] > s5 if side == "long" else closes[i] < s5) or held >= maxhold
            if done:
                ret = (closes[i] / closes[entry_i] - 1.0) * (1 if side == "long" else -1)
                trades.append({"ret": ret, "hold": held})
                in_pos, entry_i = False, None
    if not trades:
        return {"trades": 0, "note": "No historical signals in the sample window."}
    rets = np.array([t["ret"] for t in trades])
    wins = rets[rets > 0]; losses = rets[rets <= 0]
    win_rate = len(wins) / len(rets) * 100.0
    avg_win = float(np.mean(wins)) * 100 if wins.size else 0.0
    avg_loss = float(np.mean(losses)) * 100 if losses.size else 0.0
    expectancy = float(np.mean(rets)) * 100
    return {
        "trades": len(trades), "win_rate_pct": _r(win_rate, 1),
        "avg_return_pct": _r(expectancy, 2), "avg_win_pct": _r(avg_win, 2), "avg_loss_pct": _r(avg_loss, 2),
        "avg_hold_days": _r(float(np.mean([t["hold"] for t in trades])), 1),
        "payoff": _r(abs(avg_win / avg_loss), 2) if avg_loss else None,
        "note": "In-sample, no stop, exit on the 5-SMA cross / 10-day time stop — illustrative of the edge, not a promise.",
    }


# ---------------------------------------------------------------------------
# VIX ("buy the panic")
# ---------------------------------------------------------------------------

def _vix_read(vix_df) -> dict | None:
    """VIX level + %-above its 10-day SMA + its own RSI(2). A stretched VIX = a fear spike."""
    if vix_df is None or getattr(vix_df, "empty", True) or "Close" not in vix_df or len(vix_df) < 12:
        return None
    vc = vix_df["Close"].astype(float)
    last = float(vc.iloc[-1])
    sma10 = float(vc.tail(10).mean())
    pct_above = (last / sma10 - 1.0) if sma10 > 0 else 0.0
    vix_rsi2 = float(_rsi_series(vc, 2).iloc[-1])
    # fear = the VIX genuinely STRETCHED above its 10-day MA (Connors' rule). RSI(2) alone is too
    # trigger-happy (any 2-day VIX uptick spikes it), so it's reported as context, not a trigger.
    fear = pct_above >= _VIX_STRETCH
    pctile = float((vc.rank(pct=True).iloc[-1]) * 100.0)
    return {"level": _r(last), "sma10": _r(sma10), "pct_above_sma10": _r(pct_above * 100, 1),
            "rsi2": _r(vix_rsi2, 1), "percentile_1y": _r(pctile, 0), "fear_spike": bool(fear)}


def _fetch_vix():
    try:
        import yfinance as yf
        return _safe_history(yf.Ticker("^VIX"), "6mo", "1d")
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# checklist + setup builder
# ---------------------------------------------------------------------------

def _check(key, label, status, value, ideal, detail):
    return {"key": key, "label": label, "status": status, "value": value, "ideal": ideal, "detail": detail}


def _connors_setup(kind, direction, entry, stop, targets, spot, thesis, evidence, horizon, watch,
                   entry_style, base_score=5.0) -> dict | None:
    tgt = [t for t in targets if t and t.get("level") is not None]
    if not (entry and stop and spot and tgt):
        return None
    t1 = tgt[0]["level"]
    rr = _rr(entry, stop, t1)
    for t in tgt:
        t["rr"] = _rr(entry, stop, t["level"])
    return {
        "type": kind, "direction": direction, "regime_fit": "counter_regime", "style": "connors_rsi2",
        "confidence": "high" if base_score >= 6 else "medium",
        "score": round(base_score + min(rr or 0, 2), 2),
        "entry": {"low": _r(entry), "high": _r(entry), "level": _r(entry), "label": kind.replace("_", " ")},
        "stop": {"level": _r(stop), "label": "catastrophe stop (Connors: fixed stops hurt this system)"},
        "targets": tgt,
        "risk_reward": rr,
        "sizing": _sizing(entry, stop, t1, spot, None),
        "options": {"structure": "—", "detail": "Connors RSI-2 is a shares/ETF mean-reversion trade — trade the shares, small size.", "bias": direction},
        "options_plan": {"available": False, "structure": "—",
                         "note": "Mean-reversion swing — trade the shares/ETF. Too short-dated and small-target to fit a defined-risk options structure."},
        "equity_plan": _equity_plan(direction, entry, stop, tgt, spot),
        "edge": {}, "event_risk": None,
        "entry_style": entry_style,
        "horizon": horizon,
        "from_current": {"to_entry_pct": round((entry - spot) / spot * 100, 2) if spot else None,
                         "to_t1_pct": round((t1 - spot) / spot * 100, 2) if spot else None},
        "thesis": thesis,
        "evidence": [e for e in evidence if e],
        "what_to_watch": watch,
    }


def _long_setup(m, spot, atr, panic=False):
    sma5 = m["sma5"]
    target = sma5 if sma5 and sma5 > spot * 1.001 else round(spot + 1.2 * atr, 2)
    t2 = m["sma10"] if (m["sma10"] and m["sma10"] > target) else round(spot + 2.0 * atr, 2)
    recent_low = m["recent_low"]
    stop = round(min(recent_low, spot - 2.5 * atr), 2)
    if stop >= spot:
        stop = round(spot - 2.5 * atr, 2)
    entry_style = {"type": "market_on_close",
                   "label": f"Enter on the close · MOC or limit ≤ ${_r(spot)}",
                   "note": ("The RSI-2 signal is confirmed only on the CLOSE — enter market-on-close (or a limit at ≤ "
                            f"${_r(spot)} in the last 15–30 min). Do NOT market-buy the next open; if it gaps up, the "
                            "bounce likely already happened — skip it."),
                   "distance_pct": 0.0}
    horizon = {"label": "Mean-reversion", "style": "connors_rsi2",
               "note": "Connors RSI-2 — a short, high-win-rate mean-reversion trade (typically 1–5 sessions). Exit on the close back above the 5-day SMA, not at a fixed target."}
    vix = m.get("vix") or {}
    watch = [
        f"EXIT rule (not a fixed target): close ABOVE the 5-day SMA ${_r(sma5)}, or RSI(2) back above ~{int(_RSI_EXIT)} — exit on the close (MOC / limit at the 5-SMA).",
        f"Time stop: if neither exit triggers within ~{_MAXHOLD} sessions, close it — the mean-reversion edge decays after a few days.",
        f"Connors found fixed stops REDUCE this system's returns; ${_r(m['sma200'])} (200-SMA) is the regime line and the ${_r(_stop_pct(spot, m))}% stop is a catastrophe stop only — the real control is SMALL size.",
        "Connors scales IN: you may add a second unit if RSI(2) drops even lower (more oversold); manage the adds as one position and keep total risk small.",
    ]
    if vix.get("fear_spike"):
        watch.insert(0, (f"VIX is {vix['pct_above_sma10']}% above its 10-day average (fear spike, {int(vix['percentile_1y'])}th pctile) — "
                         "historically the highest-probability RSI-2 long window. BUT in a true regime-change crash mean-reversion keeps failing, so honor the size/time-stop limits."))
    bt = m.get("backtest") or {}
    edge_txt = (f" On this name the rule has hit ~{bt.get('win_rate_pct')}% winners over {bt.get('trades')} past signals "
                f"(avg +{bt.get('avg_return_pct')}%/trade, ~{bt.get('avg_hold_days')}d)." if bt.get("trades") else "")
    lead = "Panic mean-reversion long" if panic else "Connors RSI-2 oversold long"
    thesis = (f"{lead} — price is above the 200-day SMA (uptrend) and RSI(2) is {m['rsi2']:.0f} "
              f"({'deeply ' if m['rsi2'] < _RSI_BUY_STRONG else ''}oversold), {('with a VIX fear spike' if panic else 'a short-term dip below the 5-day SMA')}. "
              f"Buy the close, exit on the close back above the 5-day SMA ${_r(sma5)}; catastrophe stop ${_r(stop)}.{edge_txt}")
    ev = [f"RSI(2) {m['rsi2']:.0f}", f"RSI(5) {m['rsi5']:.0f}", f"RSI(10) {m['rsi10']:.0f}",
          f"2-day cum RSI(2) {m['cum_rsi2']:.0f}", f"above 200-SMA ${_r(m['sma200'])}",
          (vix.get("fear_spike") and f"VIX +{vix['pct_above_sma10']}% vs 10-SMA (fear)") or "",
          (bt.get("trades") and f"backtest {bt['win_rate_pct']}% win / {bt['trades']} trades") or ""]
    base = 6.5 if (m["rsi2"] < _RSI_BUY_STRONG or panic) else 5.5
    return _connors_setup("connors_rsi2_long", "long", spot, stop,
                          [{"level": _r(target), "label": "5-day SMA (mean-reversion exit)"},
                           {"level": _r(t2), "label": "10-day SMA / further mean"}],
                          spot, thesis, ev, horizon, watch, entry_style, base_score=base)


def _short_setup(m, spot, atr):
    sma5 = m["sma5"]
    target = sma5 if sma5 and sma5 < spot * 0.999 else round(spot - 1.2 * atr, 2)
    t2 = m["sma10"] if (m["sma10"] and m["sma10"] < target) else round(spot - 2.0 * atr, 2)
    stop = round(max(m["recent_high"], spot + 2.5 * atr), 2)
    entry_style = {"type": "market_on_close",
                   "label": f"Short on the close · MOC or limit ≥ ${_r(spot)}",
                   "note": "RSI-2 short signal — confirmed on the close. Short market-on-close / a limit at ≥ current price; don't chase the open.",
                   "distance_pct": 0.0}
    horizon = {"label": "Mean-reversion", "style": "connors_rsi2",
               "note": "Connors RSI-2 short — fade an overbought bounce in a downtrend; exit on the close back below the 5-day SMA."}
    watch = [
        f"EXIT rule: close BELOW the 5-day SMA ${_r(sma5)}, or RSI(2) back below ~{100 - int(_RSI_EXIT)} — exit on the close.",
        f"Time stop: close it within ~{_MAXHOLD} sessions if it doesn't work.",
        "Counter-trend short below the 200-SMA — high risk, small size; fixed stops hurt the system, so the stop is a catastrophe stop only.",
    ]
    thesis = (f"Connors RSI-2 overbought short — price is below the 200-day SMA (downtrend) and RSI(2) is {m['rsi2']:.0f} "
              f"(overbought). Short the close, cover on the close back below the 5-day SMA ${_r(sma5)}; catastrophe stop ${_r(stop)}.")
    ev = [f"RSI(2) {m['rsi2']:.0f}", f"below 200-SMA ${_r(m['sma200'])}", f"RSI(5) {m['rsi5']:.0f}"]
    return _connors_setup("connors_rsi2_short", "short", spot, stop,
                          [{"level": _r(target), "label": "5-day SMA (mean-reversion cover)"},
                           {"level": _r(t2), "label": "10-day SMA / further mean"}],
                          spot, thesis, ev, horizon, watch, entry_style, base_score=5.0)


def _stop_pct(spot, m):
    atr = m["atr"]
    stop = min(m["recent_low"], spot - 2.5 * atr)
    return _r((spot - stop) / spot * 100, 1)


# ---------------------------------------------------------------------------
# checklist
# ---------------------------------------------------------------------------

def _signal(m) -> dict:
    checks, score = [], 0.0
    c, sma200, sma5 = m["close"], m["sma200"], m["sma5"]
    rsi2 = m["rsi2"]
    above200 = sma200 is not None and c > sma200
    below200 = sma200 is not None and c < sma200

    # 1) 200-SMA trend filter
    if above200:
        st, val, pts = "pass", f"above (${_r(sma200)})", 30
    elif below200:
        st, val, pts = "warn", f"below (${_r(sma200)})", 0     # not a fail — it arms the SHORT side
    else:
        st, val, pts = "warn", "n/a", 0
    score += pts
    checks.append(_check("trend_200", "200-day SMA trend", st, val, "price above the 200-SMA (for longs)",
                         "Connors only fades pullbacks WITH the long-term trend — longs above the 200-SMA, the mirror (shorts) below it."))

    # 2) RSI(2) oversold trigger
    if above200 and rsi2 < _RSI_BUY_STRONG:
        st, pts = "pass", 40
    elif above200 and rsi2 < _RSI_BUY:
        st, pts = "pass", 32
    elif above200 and rsi2 < _RSI_BUY_PANIC:
        st, pts = "warn", 15
    elif below200 and rsi2 > _RSI_SELL:
        st, pts = "pass", 32
    else:
        st, pts = "warn" if rsi2 < 30 or rsi2 > 70 else "fail", 0
    score += pts
    checks.append(_check("rsi2", "RSI(2) extreme", st, f"{rsi2:.0f}", "< 10 (long) / > 90 (short)",
                         f"The core trigger. RSI(2) {rsi2:.0f}; the lower (for longs) the higher the historical edge. Context: RSI(5) {m['rsi5']:.0f}, RSI(10) {m['rsi10']:.0f}."))

    # 3) pullback below the 5-day SMA
    pull = sma5 is not None and ((c < sma5) if above200 else (c > sma5))
    st = "pass" if pull else "warn"
    score += 15 if pull else 0
    checks.append(_check("pullback_5sma", "Pullback vs 5-SMA", st, f"{'below' if (sma5 and c < sma5) else 'above'} (${_r(sma5)})",
                         "below the 5-SMA (a real dip)", "Confirms a genuine short-term pullback into the trend, not a mid-range read. The 5-SMA is also the exit."))

    # 4) cumulative RSI(2) confirmation
    cum = m["cum_rsi2"]
    st = "pass" if cum < _CUM_RSI_BUY else "warn"
    score += 10 if cum < _CUM_RSI_BUY else 0
    checks.append(_check("cum_rsi", "2-day cumulative RSI(2)", st, f"{cum:.0f}", "< 35",
                         "Connors' cumulative-RSI variant — a 2-day sum of RSI(2) under 35 confirms persistent (not one-bar) oversold."))

    # 5) VIX fear (soft boost)
    vix = m.get("vix")
    if not vix:
        st, val, detail = "warn", "n/a", "VIX read unavailable."
    elif vix["fear_spike"]:
        st, val = "pass", f"+{vix['pct_above_sma10']}% vs 10-SMA"
        detail = f"VIX {vix['level']} is stretched above its 10-day SMA ({int(vix['percentile_1y'])}th pctile, RSI2 {vix['rsi2']:.0f}) — a fear spike; the highest-probability mean-reversion window. Regime-crash caveat applies."
        score += 5
    else:
        st, val, detail = "warn", f"+{vix['pct_above_sma10']}% vs 10-SMA", f"VIX {vix['level']} is not stretched — a normal-vol tape (no extra fear edge, the base RSI-2 setup still stands)."
    checks.append(_check("vix_fear", "VIX fear spike", st, val, "VIX stretched > 8% above its 10-SMA", detail))

    score = min(100.0, score)
    if above200 and rsi2 < _RSI_BUY:
        state, tone = ("BUY ZONE (strong)" if rsi2 < _RSI_BUY_STRONG else "BUY ZONE"), "buy"
    elif above200 and rsi2 < _RSI_BUY_PANIC and (vix or {}).get("fear_spike"):
        state, tone = "PANIC BUY (VIX)", "buy"
    elif above200 and rsi2 < _RSI_BUY_PANIC:
        state, tone = "WATCH (pulling back)", "watch"
    elif below200 and rsi2 > _RSI_SELL:
        state, tone = "SHORT ZONE", "short"
    else:
        state, tone = "FLAT (no signal)", "flat"
    armed = state not in ("FLAT (no signal)", "WATCH (pulling back)")
    summary = {
        "buy": f"RSI-2 mean-reversion LONG armed — RSI(2) {rsi2:.0f} and above the 200-SMA. Enter on the close, exit above the 5-SMA.",
        "short": f"RSI-2 mean-reversion SHORT armed — RSI(2) {rsi2:.0f} and below the 200-SMA. Short the close, cover below the 5-SMA.",
        "watch": f"Pulling back (RSI(2) {rsi2:.0f}) but not yet at the < {int(_RSI_BUY)} trigger — on watch; wait for the close-based signal.",
        "flat": f"No Connors signal — RSI(2) is {rsi2:.0f} (needs < {int(_RSI_BUY)} above the 200-SMA, or > {int(_RSI_SELL)} below it).",
    }[tone]
    return {"state": state, "tone": tone, "armed": armed, "score": int(round(score)),
            "checks": checks, "summary": summary}


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def _build_metrics(daily, vix_df) -> dict:
    close_s = daily["Close"].astype(float)
    c = close_s.values
    spot = float(c[-1])
    rsi2_s = _rsi_series(close_s, 2)
    rsi5_s = _rsi_series(close_s, 5)
    rsi10_s = _rsi_series(close_s, 10)
    sma5 = float(close_s.tail(5).mean())
    sma10 = float(close_s.tail(10).mean())
    sma200 = float(close_s.tail(200).mean()) if len(c) >= 200 else None
    atr = _atr(daily) or (spot * 0.02)
    lows = daily["Low"].astype(float).values
    highs = daily["High"].astype(float).values
    sma5_arr = close_s.rolling(5).mean().values
    sma200_arr = close_s.rolling(200).mean().values
    return {
        "close": spot, "atr": atr,
        "rsi2": float(rsi2_s.iloc[-1]), "rsi5": float(rsi5_s.iloc[-1]), "rsi10": float(rsi10_s.iloc[-1]),
        "cum_rsi2": float(rsi2_s.iloc[-1] + rsi2_s.iloc[-2]) if len(rsi2_s) >= 2 else float(rsi2_s.iloc[-1]),
        "sma5": sma5, "sma10": sma10, "sma200": sma200,
        "recent_low": float(np.min(lows[-10:])), "recent_high": float(np.max(highs[-10:])),
        "vix": _vix_read(vix_df),
        "backtest": _backtest(c, sma5_arr, sma200_arr, rsi2_s.values,
                              side="short" if (sma200 and spot < sma200) else "long"),
        "_rsi2_last": float(rsi2_s.iloc[-1]),
    }


def _execution(m, spot, side: str = "long") -> dict:
    long = side == "long"
    cmp_e = "≤" if long else "≥"                              # long fills at/below; short at/above
    exit_dir = "above" if long else "below"
    return {
        "signal_basis": "close",
        "recommended_order": (f"Market-on-Close (MOC), or a limit at {cmp_e} ${_r(spot)} in the last 15–30 minutes — the RSI-2 "
                              "signal is only valid on the close."),
        "overnight_risk": ("You hold overnight. The mean-reversion edge is concentrated in the next 1–3 sessions (the "
                           "overnight gap included), so overnight exposure is where the edge lives — but a hard gap against "
                           "you is the main risk, so keep size small."),
        "open_alternative": (f"Prefer next morning? Use a LIMIT at {cmp_e} the prior close — never a market order into the open. "
                             f"If it gaps {'up' if long else 'down'} past your limit, the move has likely already happened → skip "
                             "the trade (don't chase the volatile open and miss the point)."),
        "exit_basis": (f"Exit on the CLOSE when price closes {exit_dir} the 5-day SMA (${_r(m['sma5'])}) or RSI(2) "
                       f"{'>' if long else '<'} ~{int(_RSI_EXIT) if long else 100 - int(_RSI_EXIT)} — MOC or a limit at the 5-SMA. "
                       f"Time stop: exit after ~{_MAXHOLD} sessions if neither triggers."),
        "stops_note": ("Connors' research found fixed stops REDUCE this system's returns; use a wide catastrophe stop + small "
                       "size + the 200-SMA filter rather than a tight stop."),
    }


def compute_connors_setups(stock, vix_df=None) -> dict | None:
    """Connors RSI-2 signal checklist + mean-reversion setup(s) + VIX read + execution plan + an
    in-sample backtest. Best-effort; returns None only if there's no usable daily history."""
    try:
        daily = _safe_history(stock, "18mo", "1d")
        if daily is None or getattr(daily, "empty", True) or len(daily) < 60:
            return None
        if vix_df is None:
            vix_df = _fetch_vix()

        m = _build_metrics(daily, vix_df)
        spot, atr = m["close"], m["atr"]
        sig = _signal(m)

        setups: list[dict] = []
        above200 = m["sma200"] is not None and spot > m["sma200"]
        below200 = m["sma200"] is not None and spot < m["sma200"]
        if above200 and m["rsi2"] < _RSI_BUY:
            s = _long_setup(m, spot, atr, panic=False)
            if s:
                setups.append(s)
        elif above200 and m["rsi2"] < _RSI_BUY_PANIC and (m.get("vix") or {}).get("fear_spike"):
            s = _long_setup(m, spot, atr, panic=True)          # VIX-armed "buy the panic" long
            if s:
                setups.append(s)
        if below200 and m["rsi2"] > _RSI_SELL:
            s = _short_setup(m, spot, atr)
            if s:
                setups.append(s)

        for i, s in enumerate(setups):
            s["rank"] = i + 1

        return {
            "price": _r(spot), "as_of": _now_str(), "atr": _r(atr),
            "signal": sig,
            "indicators": {"rsi2": _r(m["rsi2"], 1), "rsi5": _r(m["rsi5"], 1), "rsi10": _r(m["rsi10"], 1),
                           "cum_rsi2": _r(m["cum_rsi2"], 1), "sma5": _r(m["sma5"]), "sma10": _r(m["sma10"]),
                           "sma200": _r(m["sma200"])},
            "vix": m.get("vix"),
            "backtest": m.get("backtest"),
            "execution": _execution(m, spot, side="short" if sig["tone"] == "short" else "long"),
            "setups": setups,
            "meta": {"has_vix": bool(m.get("vix")), "has_200sma": m["sma200"] is not None,
                     "note": "Larry Connors' 2-Period RSI mean-reversion — deterministic signal + setup + VIX read + backtest."},
        }
    except Exception:  # noqa: BLE001
        return None
