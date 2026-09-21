"""Qullamäggie momentum-breakout setups — the fourth trade style.

Kristjan Kullamägi ("Qullamaggie") trades the *strongest* stocks with a strict, repeatable
momentum playbook. This module encodes that method deterministically (no LLM guessing — the
user-trust principle) and emits setups that render through the exact same SetupCard UI as the
swing / position / day-trade styles.

The read has two parts:

  1. **Qualification** — a checklist computed from real daily bars: a big prior move, a high
     ADR% (average daily range), a stacked & rising 10/20-EMA + 50-SMA, price near its
     52-week high, and a tight consolidation/base. This is the most useful part even when a
     name does NOT qualify — it says exactly *why* this isn't a Qullamäggie candidate.

  2. **Setups** (only when the tape supports them):
       • **Breakout** — the classic continuation: buy the break of a tight consolidation after
         a big move, entry refined by the *opening-range high* of the breakout day, tight stop
         at the day's low / base low, then sell part into the 3–5 day thrust and trail the
         10/20-day MA.
       • **Episodic Pivot (EP)** — a big gap-up on a catalyst (earnings/news) out of a base, on
         a volume surge; enter the break of the gap-day high, stop the gap-day low.
       • **Parabolic Short** — a climactic, over-extended run showing its first break; a
         counter-trend mean-reversion short back toward the 10/20-day MA. High risk, small size.

Kept in its OWN lazy endpoint (`/qullamaggie-setup`) so the main Setups load never pays for the
extra ~15-month daily pull + intraday opening-range fetch (production is one 512 MiB Cloud Run
instance — keep per-request peak low).
"""
from __future__ import annotations

import datetime as _dt

import numpy as np

from .microstructure_service import _r, _now_str, _safe_history
from .trade_setup_service import _entry_style, _equity_plan, _sizing, _rr, _next_earnings
from .day_trade_service import _opening_range, _to_date


# ---------------------------------------------------------------------------
# thresholds — the Qullamäggie screen (documented defaults; tuned to be selective)
# ---------------------------------------------------------------------------

_MOVE_STRONG = 0.30          # a "big mover": best trailing move ≥ 30%
_MOVE_WARN = 0.15            # borderline momentum 15–30%
_ADR_STRONG = 5.0            # high average daily range (%) — enough range to trade
_ADR_WARN = 3.0
_NEAR_HIGH = 0.15            # within 15% of the 52-week high = "at highs"
_NEAR_HIGH_WARN = 0.30       # 15–30% below = acceptable; >30% = not a leader
_EP_GAP = 0.08               # an Episodic Pivot gaps ≥ 8% on the open
_EP_VOL_MULT = 2.5           # …on ≥ 2.5× the 20-day average volume
_EP_LOOKBACK = 12            # a still-actionable EP happened within the last ~12 sessions


# ---------------------------------------------------------------------------
# deterministic metrics (pure — take numpy arrays, no network)
# ---------------------------------------------------------------------------

def _ema(series: np.ndarray, span: int) -> np.ndarray:
    """Exponential moving average (adjust=False, like a trading platform)."""
    a = 2.0 / (span + 1.0)
    out = np.empty_like(series, dtype=float)
    out[0] = series[0]
    for i in range(1, len(series)):
        out[i] = a * series[i] + (1 - a) * out[i - 1]
    return out


def _adr_pct(highs: np.ndarray, lows: np.ndarray, n: int = 20) -> float | None:
    """Kullamägi's ADR% = 100·(mean(High/Low over n) − 1) — the average daily range as a %."""
    if len(highs) < n:
        n = len(highs)
    if n < 2:
        return None
    hl = highs[-n:] / np.where(lows[-n:] > 0, lows[-n:], np.nan)
    hl = hl[np.isfinite(hl)]
    if hl.size == 0:
        return None
    return float((np.mean(hl) - 1.0) * 100.0)


def _pct_move(closes: np.ndarray, lows: np.ndarray) -> dict:
    """Trailing momentum: point-to-point returns over 1/3/6mo AND the thrust up from the
    base low in each window (a stock up 120% off its low is a leader even if flat lately)."""
    def ago(n):
        return float(closes[-n - 1]) if len(closes) > n else None

    c = float(closes[-1])
    out = {}
    for label, n in (("move_1m", 21), ("move_3m", 63), ("move_6m", 126)):
        base = ago(n)
        out[label] = round((c / base - 1.0) * 100.0, 1) if base and base > 0 else None
    # thrust from the lowest low over the last ~3 months (the "big move" Kullamägi wants)
    win = lows[-63:] if len(lows) >= 10 else lows
    lo = float(np.min(win)) if win.size else None
    out["thrust_from_low_pct"] = round((c / lo - 1.0) * 100.0, 1) if lo and lo > 0 else None
    cand = [v for v in out.values() if v is not None]
    out["best_pct"] = max(cand) if cand else None
    return out


def _find_base(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, adr_pct: float) -> dict | None:
    """The tight consolidation the breakout comes out of. Scans windows longest→shortest and
    returns the LONGEST recent window that is tight relative to the stock's own ADR and sits
    near the highs. Tightness = base depth vs the sum of daily ranges it spans (a contracting
    flag realizes far less range than it 'should')."""
    atr_frac = max((adr_pct or 0) / 100.0, 1e-4)
    depth_cap = min(0.30, max(0.08, 3.0 * atr_frac))       # a flag ≤ ~3 daily ranges deep
    recent_high = float(np.max(highs[-40:])) if len(highs) >= 5 else float(np.max(highs))
    best = None
    for K in (20, 17, 15, 12, 10, 8, 6, 5):
        if len(closes) < K + 1:
            continue
        hi = float(np.max(highs[-K:]))
        lo = float(np.min(lows[-K:]))
        if hi <= 0:
            continue
        depth = (hi - lo) / hi
        expected = atr_frac * K
        tightness = depth / expected if expected > 0 else 9.9
        near_top = hi >= 0.92 * recent_high                # base is forming near the highs
        if depth <= depth_cap and tightness <= 0.6 and near_top:
            third = max(2, K // 3)
            early_rng = float(np.max(highs[-K:-K + third]) - np.min(lows[-K:-K + third]))
            late_rng = float(np.max(highs[-third:]) - np.min(lows[-third:]))
            best = {
                "high": _r(hi), "low": _r(lo), "days": K,
                "depth_pct": _r(depth * 100.0, 1), "tightness": _r(tightness, 2),
                "contracting": bool(late_rng < early_rng),
            }
            break                                          # longest qualifying window wins
    return best


def _detect_ep(opens, highs, lows, closes, vols) -> dict | None:
    """Most recent Episodic Pivot: a ≥8% opening gap on ≥2.5× average volume within the last
    ~12 sessions, still actionable (price hasn't already run far beyond the gap)."""
    n = len(closes)
    if n < 25:
        return None
    avg20 = float(np.mean(vols[-21:-1])) if n > 21 else float(np.mean(vols[:-1]) or 0)
    if avg20 <= 0:
        return None
    for i in range(n - 1, max(n - 1 - _EP_LOOKBACK, 0), -1):
        if i < 1:
            break
        gap = opens[i] / closes[i - 1] - 1.0 if closes[i - 1] > 0 else 0.0
        vol_mult = vols[i] / avg20
        if gap >= _EP_GAP and vol_mult >= _EP_VOL_MULT:
            gap_hi, gap_lo = float(highs[i]), float(lows[i])
            bars_since = n - 1 - i
            extended = closes[-1] > gap_hi * (1.0 + 3.0 * (gap_hi / gap_lo - 1.0))
            return {
                "gap_pct": _r(gap * 100.0, 1), "vol_mult": _r(vol_mult, 1),
                "gap_high": _r(gap_hi), "gap_low": _r(gap_lo),
                "bars_since": bars_since, "extended": bool(extended),
            }
    return None


# ---------------------------------------------------------------------------
# checklist
# ---------------------------------------------------------------------------

def _check(key, label, status, value, ideal, detail):
    return {"key": key, "label": label, "status": status, "value": value, "ideal": ideal, "detail": detail}


def _qualify(m: dict) -> dict:
    """Score the name against the Qullamäggie screen and build the pass/warn/fail checklist."""
    checks, score = [], 0.0
    close = m["close"]

    # 1) big prior move
    best = m["moves"]["best_pct"]
    if best is None:
        st, pts = "fail", 0
    elif best >= _MOVE_STRONG * 100:
        st, pts = "pass", 25
    elif best >= _MOVE_WARN * 100:
        st, pts = "warn", 12
    else:
        st, pts = "fail", 0
    score += pts
    checks.append(_check("prior_move", "Big prior move", st,
                         f"+{best:.0f}%" if best is not None else "—", "≥ +30% (1–6mo)",
                         f"1mo {m['moves']['move_1m']}% · 3mo {m['moves']['move_3m']}% · 6mo {m['moves']['move_6m']}% · "
                         f"from base low +{m['moves']['thrust_from_low_pct']}%. Kullamägi only trades stocks already moving big."))

    # 2) ADR% (range to trade)
    adr = m["adr_pct"]
    if adr is None:
        st, pts = "fail", 0
    elif adr >= _ADR_STRONG:
        st, pts = "pass", 20
    elif adr >= _ADR_WARN:
        st, pts = "warn", 10
    else:
        st, pts = "fail", 0
    score += pts
    checks.append(_check("adr", "High ADR%", st, f"{adr:.1f}%" if adr is not None else "—", "≥ 5%",
                         "Average daily range = 100·(mean(High/Low, 20)−1). Low ADR names don't move enough intraday to trade this way."))

    # 3) stacked & rising moving averages
    e10, e20, s50 = m["ema10"], m["ema20"], m["sma50"]
    stacked = bool(e10 and e20 and s50 and close > e10 > e20 > s50)
    trend_rising = m["ema20_rising"] and m["sma50_rising"]
    if stacked and trend_rising:
        st, pts, val = "pass", 25, "stacked & rising"
    elif stacked:
        st, pts, val = "warn", 15, "stacked, trend flat"
    elif e10 and e20 and close > e10 > e20 and m["ema20_rising"]:
        st, pts, val = "warn", 12, "10>20 only"
    else:
        st, pts, val = "fail", 0, "not stacked"
    score += pts
    checks.append(_check("ma_stack", "Stacked rising MAs", st, val,
                         "price > 10EMA > 20EMA > 50SMA, rising",
                         f"10EMA ${_r(e10)} · 20EMA ${_r(e20)} · 50SMA ${_r(s50)}. Trend must be up and orderly on the daily."))

    # 4) near the 52-week high (leadership)
    d = m["pct_from_52w_high"]
    if d is None:
        st, pts = "fail", 0
    elif d >= -_NEAR_HIGH * 100:
        st, pts = "pass", 20
    elif d >= -_NEAR_HIGH_WARN * 100:
        st, pts = "warn", 10
    else:
        st, pts = "fail", 0
    score += pts
    checks.append(_check("near_highs", "Near 52-week high", st, f"{d:.0f}%" if d is not None else "—", "within 15% of highs",
                         f"52w high ${_r(m['high_52w'])}. Leaders make new highs first; laggards well off their highs are not Qullamäggie names."))

    # 5) tight base (bonus)
    base = m.get("base")
    if base:
        st, pts = "pass", 10
        detail = (f"{base['days']}-day base, {base['depth_pct']}% deep{' & contracting' if base['contracting'] else ''} "
                  f"under ${base['high']} — the launchpad for the breakout.")
    else:
        st, pts = "warn", 0
        detail = "No tight consolidation right now — either extended (chase risk) or still basing wider. Wait for a tight flag."
    score += pts
    checks.append(_check("base", "Tight consolidation", st,
                         f"{base['depth_pct']}% / {base['days']}d" if base else "none", "tight, contracting flag", detail))

    # 6) volume — Kullamägi is price-first, so this is a SOFT confirmation (never fails, never gates):
    # the ideal is a quiet base (volume drying up) then a surge on the breakout day.
    vol = m.get("volume")
    if not vol:
        st, val, vb = "warn", "n/a", 0
        detail = "Volume history unavailable to assess."
    elif vol["dry_up"] and vol["expansion"]:
        st, val, vb = "pass", "dry base + surge", 8
        detail = (f"Base volume dried to {vol['dryup_ratio']:.2f}× the prior run AND today prints "
                  f"{vol['breakout_vol_ratio']:.1f}× the 20-day average — a textbook quiet-base-then-volume breakout.")
    elif vol["dry_up"]:
        st, val, vb = "pass", "base drying up", 6
        detail = (f"Base volume is {vol['dryup_ratio']:.2f}× the prior run — sellers exhausted; the ideal is a "
                  f"volume surge on the actual break.")
    elif vol["expansion"]:
        st, val, vb = "warn", "surge, base not quiet", 3
        detail = f"Today is {vol['breakout_vol_ratio']:.1f}× the 20-day average, but the base wasn't especially quiet."
    else:
        st, val, vb = "warn", "flat / heavy", 0
        detail = ("No volume dry-up in the base and no surge yet. Kullamägi weights volume lightly for breakouts, "
                  "so treat this as a soft negative only.")
    score += vb
    checks.append(_check("volume", "Volume (dry-up → surge)", st, val, "quiet base, volume on the break", detail))
    score = min(100.0, score)

    passes = sum(1 for c in checks if c["status"] == "pass")
    # HARD gates — a Qullamäggie name must clear all four screens (not merely score well): enough
    # range (ADR), an up-trend MA stack, a real prior move, and leadership near the highs. ADR is
    # his first filter — a low-ADR blue chip (e.g. KO at ~1.4%) never qualifies, whatever its score.
    ok = lambda key: next(c["status"] for c in checks if c["key"] == key) != "fail"
    stack_ok = ok("ma_stack")
    is_candidate = bool(score >= 55 and ok("adr") and stack_ok and ok("prior_move") and ok("near_highs"))
    grade = "A" if score >= 78 else "B" if score >= 60 else "C" if score >= 40 else "—"
    if is_candidate:
        summary = (f"A Qullamäggie candidate (grade {grade}, {int(score)}/100): a strong momentum leader"
                   + (" set up in a tight base." if base else " — but wait for a tight base to form before buying."))
    else:
        misses = [c["label"] for c in checks if c["status"] == "fail"]
        summary = ("Not a Qullamäggie setup right now — "
                   + ("misses: " + ", ".join(misses) + "." if misses else "the momentum screen is only partial — not enough passes to qualify."))
    return {"is_candidate": is_candidate, "grade": grade, "score": int(round(score)),
            "checks": checks, "summary": summary, "passes": passes}


# ---------------------------------------------------------------------------
# setup builder (renders through the shared SetupCard)
# ---------------------------------------------------------------------------

def _qm_setup(kind, direction, entry, stop, targets, spot, atr, thesis, evidence, horizon, watch,
              regime_fit="with_regime", base_score=5.0) -> dict | None:
    tgt = [t for t in targets if t and t.get("level") is not None]
    if not (entry and stop and spot and tgt):
        return None
    t1 = tgt[0]["level"]
    rr = _rr(entry, stop, t1)
    if not rr:
        return None
    for t in tgt:
        t["rr"] = _rr(entry, stop, t["level"])
    score = base_score + min(rr, 4)
    return {
        "type": kind, "direction": direction, "regime_fit": regime_fit, "style": "qullamaggie",
        "confidence": "high" if rr >= 2.5 else "medium" if rr >= 1.5 else "low",
        "score": round(score, 2),
        "entry": {"low": _r(entry), "high": _r(entry), "level": _r(entry), "label": kind.replace("_", " ")},
        "stop": {"level": _r(stop), "label": "tight momentum stop"},
        "targets": tgt,
        "risk_reward": rr,
        "sizing": _sizing(entry, stop, t1, spot, None),
        "options": {"structure": "—", "detail": "Qullamäggie is a shares strategy — express it with share size and a tight stop, not options.", "bias": direction},
        "options_plan": {"available": False, "structure": "—",
                         "note": "Qullamäggie trades the shares (leverage comes from position size on a tight stop, not options)."},
        "equity_plan": _equity_plan(direction, entry, stop, tgt, spot),
        "edge": {}, "event_risk": None,
        "entry_style": _entry_style(direction, entry, spot, atr),
        "horizon": horizon,
        "from_current": {"to_entry_pct": round((entry - spot) / spot * 100, 2) if spot else None,
                         "to_t1_pct": round((t1 - spot) / spot * 100, 2) if spot else None},
        "thesis": thesis,
        "evidence": [e for e in evidence if e],
        "what_to_watch": watch,
    }


_MGMT_TRAIL = ("Sell 1/3–1/2 into strength after the initial 3–5 day thrust (into the parabolic pop), then trail the "
               "rest under the 10-day MA (aggressive) or 20-day MA (looser). Exit on a daily CLOSE below your trailing MA.")


def _breakout_setup(m, spot, atr, orange, sess_low):
    base = m.get("base")
    if not base:
        return None
    adr_d = m["adr_dollars"]
    pivot = base["high"]
    trig = max(0.03 * atr, 0.001 * spot)
    orh = (orange or {}).get("high")
    # entry = the break of the base pivot, refined by the opening-range high of the breakout day
    if orh and orh >= pivot:
        entry = round(orh + trig, 2)
        entry_note = f"opening-range high ${_r(orh)} (already above the ${pivot} base pivot — the breakout is live)"
    elif orh:
        entry = round(pivot + trig, 2)
        entry_note = f"break of the ${pivot} base pivot; on the breakout day trigger on the 5-min opening-range high (currently ${_r(orh)})"
    else:
        entry = round(pivot + trig, 2)
        entry_note = f"break of the ${pivot} base pivot; refine the trigger with the 5-min opening-range high on the breakout day"

    # stop: tightest valid of [breakout-day low, base low, 1.5·ADR], capped at ~2.5 ADR of risk
    cands = []
    if sess_low and sess_low < entry:
        cands.append(("low of the day", sess_low))
    if base["low"] < entry:
        cands.append(("base low", base["low"]))
    cands = [(lbl, lv) for lbl, lv in cands if (entry - lv) <= 2.5 * adr_d]
    if cands:
        stop_lbl, stop = max(cands, key=lambda x: x[1])       # tightest (highest) valid stop
    else:
        stop_lbl, stop = "1.5× ADR", entry - 1.5 * adr_d
    stop = round(stop, 2)

    t1 = round(entry + 3.0 * adr_d, 2)                        # a typical 3–5 day thrust
    t2 = round(entry + 5.0 * adr_d, 2)
    horizon = {"label": "Momentum swing", "style": "qullamaggie",
               "note": "Qullamäggie breakout — hold the initial 3–5 day thrust, sell part into strength, then trail the 10/20-day MA for weeks while the trend holds."}
    vol = m.get("volume") or {}
    vol_watch = ("Volume confirms the break: base volume dried up, so want a clear volume EXPANSION on the breakout bar — "
                 "a break on light volume is more likely to fail."
                 if vol.get("dry_up") else
                 "Want the breakout bar on a clear volume EXPANSION vs the base — Kullamägi is price-first, but a break on "
                 "heavy volume is more convincing than one on light volume.")
    watch = [
        f"Trailing stops: 10-day EMA ${_r(m['ema10'])} (aggressive) / 20-day EMA ${_r(m['ema20'])} (looser) — exit on a daily close below the one you're trailing.",
        _MGMT_TRAIL,
        vol_watch,
        f"Fail-fast: a close back inside the base (below ${base['high']}) or below the day's low ${_r(stop)} means the breakout failed — take the stop, no averaging down.",
        f"This is a shares trade risking to ${_r(stop)} (~{_r((entry-stop)/entry*100,1)}%) — size so that stop is a fixed, small % of your account.",
    ]
    thesis = (f"Qullamäggie breakout — a leader up big ({m['moves']['best_pct']:.0f}%) with a {base['days']}-day tight base "
              f"({base['depth_pct']}% deep{', volume drying up' if vol.get('dry_up') else ''}) under ${base['high']}. "
              f"Buy the {entry_note}, stop the tight low at ${_r(stop)}, "
              f"then manage with the 10/20-day MA trail (no fixed target — let the winner run).")
    ev = [f"ADR {m['adr_pct']:.1f}%", f"10>20>50 EMA stack", f"{base['depth_pct']}% base / {base['days']}d",
          f"{m['pct_from_52w_high']:.0f}% from 52w high",
          (vol.get("dry_up") and f"base volume {vol['dryup_ratio']}× prior (drying up)") or "",
          (orh and f"opening-range high ${_r(orh)}") or ""]
    s = _qm_setup("qm_breakout", "long", entry, stop, [{"level": t1, "label": "3× ADR thrust (scale out)"},
                                                        {"level": t2, "label": "5× ADR (trail runner)"}],
                  spot, atr, thesis, ev, horizon, watch, base_score=6.0)
    return s


def _ep_setup(m, spot, atr, orange, ep):
    if not ep or ep.get("extended"):
        return None
    adr_d = m["adr_dollars"]
    orh = (orange or {}).get("high")
    pivot = ep["gap_high"]
    trig = max(0.03 * atr, 0.001 * spot)
    if ep["bars_since"] == 0 and orh and orh >= pivot:
        entry = round(orh + trig, 2)
        entry_note = f"opening-range high ${_r(orh)} on the gap day"
    else:
        entry = round(pivot + trig, 2)
        entry_note = f"break of the gap-day high ${pivot}"
    stop = round(ep["gap_low"], 2)
    if (entry - stop) > 2.5 * adr_d:                          # cap risk if the gap bar was huge
        stop = round(entry - 1.8 * adr_d, 2)
    t1 = round(entry + 3.0 * adr_d, 2)
    t2 = round(entry + 5.0 * adr_d, 2)
    horizon = {"label": "Episodic pivot", "style": "qullamaggie",
               "note": "Gap-up on a catalyst out of a base — hold the momentum thrust, sell into strength, trail the 10/20-day MA."}
    watch = [
        f"Trailing stops: 10-day EMA ${_r(m['ema10'])} / 20-day EMA ${_r(m['ema20'])} — exit on a daily close below the trail.",
        _MGMT_TRAIL,
        f"EP invalidation: a close back below the gap-day low ${_r(stop)} (the gap filling) kills the thesis — out.",
    ]
    cat = "earnings/news catalyst" if m.get("earnings_near_gap") else "a news catalyst"
    thesis = (f"Episodic Pivot — a {ep['gap_pct']:.0f}% gap-up on {ep['vol_mult']:.0f}× volume ({cat}) out of a base "
              f"{ep['bars_since']} session(s) ago. Buy the {entry_note}, stop the gap-day low ${_r(stop)}, manage with the MA trail.")
    ev = [f"gap +{ep['gap_pct']:.0f}%", f"volume {ep['vol_mult']:.0f}× avg", f"ADR {(m['adr_pct'] or 0):.1f}%",
          (orh and f"opening-range high ${_r(orh)}") or ""]
    return _qm_setup("qm_episodic_pivot", "long", entry, stop,
                     [{"level": t1, "label": "3× ADR thrust (scale out)"}, {"level": t2, "label": "5× ADR (trail runner)"}],
                     spot, atr, thesis, ev, horizon, watch, base_score=5.5)


def _parabolic_short_setup(m, spot, atr, orange, opens, highs, lows, closes):
    if len(closes) < 6 or not (m["ema10"] and m["ema20"]):
        return None
    adr_d = m["adr_dollars"]
    atr_frac = max((m["adr_pct"] or 0) / 100.0, 1e-4)
    ext = (closes[-1] - m["ema10"]) >= 3.0 * adr_d or (closes[-1] / m["ema10"] - 1.0) >= 0.12
    run = closes[-1] / closes[-5] - 1.0
    parabolic = run >= max(0.15, 4 * atr_frac) and ext
    up_days = sum(1 for k in range(1, 5) if closes[-k] > closes[-k - 1]) >= 3
    last_red = closes[-1] < opens[-1]
    off_high = (highs[-1] - closes[-1]) >= 0.5 * max(highs[-1] - lows[-1], 1e-9)
    lost_low = closes[-1] < lows[-2]
    breaking = last_red or off_high or lost_low
    if not (parabolic and up_days and breaking):
        return None

    orl = (orange or {}).get("low")
    trig = max(0.03 * atr, 0.001 * spot)
    ref_low = min(float(lows[-1]), orl) if orl else float(lows[-1])
    entry = round(ref_low - trig, 2)                          # short the break of the reversal-day low
    stop = round(max(float(highs[-1]), float(highs[-2])) + max(0.1 * atr, 0.002 * spot), 2)  # above the climax high
    if (stop - entry) > 2.5 * adr_d:
        stop = round(entry + 1.8 * adr_d, 2)
    # target: mean-revert toward the 10/20-day MA (must sit below entry to be a valid short target)
    t1 = m["ema10"] if m["ema10"] < entry else round(entry - 3.0 * adr_d, 2)
    t2 = m["ema20"] if m["ema20"] < min(entry, t1) else round(entry - 5.0 * adr_d, 2)
    horizon = {"label": "Parabolic short", "style": "qullamaggie",
               "note": "Counter-trend momentum short — a fast snap-back to the 10/20-day MA, typically 1–5 days. High risk; keep size small and be quick."}
    watch = [
        "Counter-trend, high-risk timing trade — size SMALL and cover into the flush; don't marry the short.",
        f"Cover targets: the 10-day EMA ${_r(m['ema10'])} then the 20-day EMA ${_r(m['ema20'])} (mean reversion).",
        f"Hard stop: a close back above the climax high ${_r(stop)} — parabolics can extend further than seems possible, so honor it.",
    ]
    thesis = (f"Parabolic short — price is {_r((closes[-1]/m['ema10']-1)*100,1)}% extended above the 10-day EMA after a "
              f"+{run*100:.0f}% run and is printing its first break. Short the loss of the ${_r(ref_low)} reversal-day low, "
              f"stop above the ${_r(stop)} climax high, cover into the 10/20-day MA snap-back.")
    ev = [f"+{run*100:.0f}% in 4d", f"{_r((closes[-1]/m['ema10']-1)*100,1)}% above 10EMA", f"ADR {(m['adr_pct'] or 0):.1f}%",
          (orl and f"opening-range low ${_r(orl)}") or ""]
    return _qm_setup("qm_parabolic_short", "short", entry, stop,
                     [{"level": round(t1, 2), "label": "10-day EMA (cover)"}, {"level": round(t2, 2), "label": "20-day EMA (cover)"}],
                     spot, atr, thesis, ev, horizon, watch, regime_fit="counter_regime", base_score=4.0)


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def _build_metrics(daily, sess_low, next_earnings, ep) -> dict:
    o = daily["Open"].values.astype(float)
    h = daily["High"].values.astype(float)
    l = daily["Low"].values.astype(float)
    c = daily["Close"].values.astype(float)
    close = float(c[-1])
    adr = _adr_pct(h, l)
    adr_d = close * (adr / 100.0) if adr else close * 0.02
    ema10 = _ema(c, 10); ema20 = _ema(c, 20)
    sma50 = float(np.mean(c[-50:])) if len(c) >= 50 else float(np.mean(c))
    sma200 = float(np.mean(c[-200:])) if len(c) >= 200 else None
    win52 = h[-252:] if len(h) >= 60 else h
    low52 = l[-252:] if len(l) >= 60 else l
    high_52w = float(np.max(win52)); low_52w = float(np.min(low52))
    base = _find_base(h, l, c, adr)

    # volume read (soft confirmation): base volume drying up vs the run before it, + today's expansion
    volume = None
    v = daily["Volume"].values.astype(float) if "Volume" in daily else None
    if v is not None and len(v) >= 25 and float(np.sum(v[-25:])) > 0:
        recent_avg = float(np.mean(v[-21:-1])) if len(v) > 21 else float(np.mean(v[:-1]))
        expansion = bool(recent_avg > 0 and v[-1] >= 1.3 * recent_avg)
        dry_up = False
        dryup_ratio = None
        if base is not None:
            K = base["days"]
            if len(v) >= 2 * K:
                v_base = float(np.mean(v[-K:])); v_prior = float(np.mean(v[-2 * K:-K]))
                if v_prior > 0:
                    dryup_ratio = v_base / v_prior
                    dry_up = bool(dryup_ratio <= 0.85)
        volume = {"dry_up": dry_up, "expansion": expansion,
                  "dryup_ratio": _r(dryup_ratio, 2), "breakout_vol_ratio": _r(v[-1] / recent_avg, 2) if recent_avg else None}

    earnings_near_gap = False
    if ep and next_earnings:
        try:
            ed = _dt.date.fromisoformat(str(next_earnings)[:10])
            gap_date = _to_date(daily.index[-1 - ep["bars_since"]])
            if gap_date:
                earnings_near_gap = abs((ed - gap_date).days) <= 4
        except Exception:  # noqa: BLE001
            pass

    return {
        "close": close, "adr_pct": adr, "adr_dollars": adr_d,
        "ema10": float(ema10[-1]), "ema20": float(ema20[-1]), "sma50": sma50, "sma200": sma200,
        # "rising" = the TREND context (20-EMA over ~2 weeks, 50-SMA over ~3 weeks) — not the
        # noisy 10-EMA, which routinely flattens inside a tight base without breaking the uptrend.
        "ema20_rising": bool(len(ema20) > 11 and ema20[-1] > ema20[-11]),
        "sma50_rising": bool(len(c) >= 65 and float(np.mean(c[-50:])) > float(np.mean(c[-65:-15]))),
        "high_52w": high_52w, "low_52w": low_52w,
        "pct_from_52w_high": (close / high_52w - 1.0) * 100.0 if high_52w > 0 else None,
        "moves": _pct_move(c, l), "base": base, "volume": volume,
        "earnings_near_gap": earnings_near_gap,
        "_o": o, "_h": h, "_l": l, "_c": c,
    }


def compute_qullamaggie_setups(stock) -> dict | None:
    """Qualification checklist + Qullamäggie setups (breakout / episodic pivot / parabolic short).
    Best-effort; returns None only if there's no usable daily history."""
    try:
        daily = _safe_history(stock, "15mo", "1d")
        if daily is None or getattr(daily, "empty", True) or len(daily) < 30:
            return None
        d5 = _safe_history(stock, "5d", "5m")

        spot = float(daily["Close"].values[-1])
        # ATR ($) for entry triggers/buffers — reuse ADR as the daily-range proxy
        h = daily["High"].values.astype(float); l = daily["Low"].values.astype(float)
        atr = float(np.mean((h[-14:] - l[-14:]))) if len(daily) >= 14 else max(spot * 0.02, 0.01)

        orange = _opening_range(d5) if d5 is not None else None
        sess_low = sess_high = None
        if d5 is not None and not d5.empty:
            try:
                sday = _to_date(d5.index[-1])
                sess = d5[np.array([_to_date(t) == sday for t in d5.index])]
                if len(sess):
                    sess_low = float(sess["Low"].min()); sess_high = float(sess["High"].max())
            except Exception:  # noqa: BLE001
                pass

        next_earn = _next_earnings(stock)
        o = daily["Open"].values.astype(float)
        c = daily["Close"].values.astype(float)
        v = daily["Volume"].values.astype(float) if "Volume" in daily else np.ones_like(c)
        ep = _detect_ep(o, h, l, c, v)

        m = _build_metrics(daily, sess_low, next_earn, ep)
        qual = _qualify(m)

        setups: list[dict] = []
        # Breakout & EP are long-momentum plays — only when the name passes the screen.
        if qual["is_candidate"]:
            s = _breakout_setup(m, spot, atr, orange, sess_low)
            if s:
                setups.append(s)
        if ep:                                              # an EP is defined by the gap itself
            s = _ep_setup(m, spot, atr, orange, ep)
            if s:
                setups.append(s)
        # Parabolic short is independent (fires on over-extension, not the long screen).
        s = _parabolic_short_setup(m, spot, atr, orange, m["_o"], m["_h"], m["_l"], m["_c"])
        if s:
            setups.append(s)

        setups.sort(key=lambda s: -(s.get("score") or 0))
        for i, s in enumerate(setups):
            s["rank"] = i + 1

        return {
            "price": _r(spot), "as_of": _now_str(), "adr_pct": _r(m["adr_pct"], 1), "atr": _r(atr),
            "qualification": qual,
            "moving_averages": {"ema10": _r(m["ema10"]), "ema20": _r(m["ema20"]),
                                "sma50": _r(m["sma50"]), "sma200": _r(m["sma200"])},
            "metrics": {"adr_pct": _r(m["adr_pct"], 1), "moves": m["moves"],
                        "pct_from_52w_high": _r(m["pct_from_52w_high"], 1),
                        "high_52w": _r(m["high_52w"]), "low_52w": _r(m["low_52w"]),
                        "base": m["base"], "episodic_pivot": ep},
            "opening_range": orange,
            "setups": setups[:3],
            "meta": {"has_intraday": bool(orange), "has_daily": True,
                     "note": "Qullamäggie (Kristjan Kullamägi) momentum-breakout method — deterministic screen + setups."},
        }
    except Exception:  # noqa: BLE001
        return None
