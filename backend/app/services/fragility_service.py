"""Trend-vs-Fragility risk overlay for the momentum named strategies (Momentum Swing + Qullamäggie).

WHY: on LITE (Oct 2026) both engines scored a parabolic blow-off A100 / B–A with live breakout entries all the way
into the top; it then fell 26% in 9 sessions. Trend indicators are collinear (they all say "price went up"), so they
max out exactly when risk is highest — and they say nothing about how FRAGILE the trend is.

EVIDENCE (point-in-time study: 498 S&P 500 stocks, 2019-12 → 2026-09, 85 non-overlapping 20-day samples of
"strong-momentum" names = price > 50-SMA > 200-SMA with the 200 rising, ≤10% off the 52-week high, 6-month return
≥ +25%, RSI14 ≥ 55; current index members, so survivorship-biased):
  • the momentum state itself had ~no 20-day edge (beat SPY 46.8% of the time vs 48.2% for everything else);
  • four point-in-time measures robustly predicted CRASH risk (a ≥15% drawdown within 20 days) with the same sign in
    both halves of the sample — 20-day realized vol (t=5.4), distance above the 200-SMA (t=3.5), the biggest one-day
    gain of the last month (t=3.9) and the 10-day run-up (t=3.4). They do NOT reliably predict direction.
  • FRAGILITY = how many of the four sit in their top 20%. Cutoffs were fit on 2019-12 → 2023-05 ONLY; out of sample
    (2023-06 → 2026-09) the crash rate was 5.7% / 9.8% / 9.9% / 19.8% / 24.8% for 0–4 flags (base 8.6%).
  • EXITS: on top of a normal stop, an exit on a close below the 20-day EMA cut fragile trades losing >10% from 5.2% to
    3.5% and improved the median (−4.9% → −4.0%) for ~0.3% of average return per trade; on calm names it barely moved
    the tail (worst-5% −7.1% → −6.3%), so it is only imposed on fragile longs.
  • GAPS: when stopped, realized loss ÷ planned loss depends on how TIGHT the stop is in ATRs (0.3 ATR → 1.24× on
    average, 1 ATR → 1.08×, 2+ ATR → 1.04×) and NOT on the stock's volatility level (low/mid/high-vol terciles agree to
    within ~0.03×). An extra volatility multiplier on top of 1%-risk sizing would therefore double-count; the gap
    overshoot is what actually makes a trade cost more than planned.

So this module adds a SECOND AXIS next to the trend grade, plus three deterministic rules:
  (1) Fragility 0–4 with its measured crash odds, every input described;
  (2) fragile LONGS (3–4 flags): size capped at ½ / ⅓ (≈ base ÷ state crash odds, so crash-weighted exposure returns to
      about the base rate), fresh breakouts ranked below pullbacks, and a 20-EMA close-exit from day 1. Calm names keep
      the method's standard exits. Shorts are not fragility-capped (fragility is the parabolic-short thesis);
  (3) gap-adjusted sizing for every setup: shares sized so the EXPECTED loss when stopped — typical gap slippage
      included — equals the risk budget.
Pure numpy/pandas on the daily bars each engine already fetched: no extra network calls.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .microstructure_service import _r
from .trade_setup_service import _RISK_BUDGET

# top-20% cutoffs, fit on the in-sample half (2019-12 → 2023-05) only
CUTOFFS = {"vol": 0.3917, "above_200": 0.4045, "max_day": 0.0607, "run_10": 0.0993}
# out-of-sample (2023-06 → 2026-09) share of strong-momentum setups with a ≥15% drawdown within 20 days, by flag count
CRASH_ODDS = {0: 5.7, 1: 9.8, 2: 9.9, 3: 19.8, 4: 24.8}
BASE_CRASH = 8.6
# size cap ≈ base ÷ state crash odds (8.6/19.8 ≈ 0.43 → ½, 8.6/24.8 ≈ 0.35 → ⅓), rounded to trader-friendly fractions
SIZE_CAP = {0: 1.0, 1: 1.0, 2: 1.0, 3: 0.5, 4: 1.0 / 3.0}
# mean realized-loss ÷ planned-loss when stopped, by stop distance in ATRs (gap fills at the open; pooled; stable in both halves)
OVERSHOOT = [(0.15, 1.620), (0.20, 1.419), (0.30, 1.240), (0.50, 1.135), (0.75, 1.097),
             (1.00, 1.075), (1.50, 1.056), (2.00, 1.040), (2.50, 1.039)]
FRESH_ENTRY_TYPES = {"momentum_breakout", "momentum_episodic_pivot", "qm_breakout", "qm_episodic_pivot"}

BASIS = ("Point-in-time test on 498 S&P 500 stocks (2019-12 → 2026-09): cutoffs fit on 2019-12 → 2023-05, crash odds "
         "measured out of sample on 2023-06 → 2026-09 among strong-momentum setups (price > 50 > 200-SMA rising, ≤10% off "
         "the 52w high, 6m ≥ +25%, RSI ≥ 55). Crash = a ≥15% drawdown within 20 trading days. Current index members "
         "(survivorship-biased), no trading costs. Fragility predicts how violent the next month is, not its direction.")

_LEVEL = {0: "calm", 1: "normal", 2: "normal", 3: "fragile", 4: "extreme"}


def cap_label(cap: float) -> str:
    return "full" if cap >= 0.999 else "½" if abs(cap - 0.5) < 1e-3 else "⅓" if abs(cap - 1 / 3) < 1e-3 else f"{cap:.2f}×"


def _qty(x: float):
    """Whole shares once the count is big enough that rounding barely matters; below that, fractional (2 dp, floored) so a
    ½ / ⅓ cap is actually honored on high-priced stocks instead of being rounded back up to 1 share."""
    if x >= 20:
        return int(x)
    return max(0.01, float(np.floor(x * 100) / 100))


def gap_overshoot(k_atr: float | None) -> float:
    """Expected realized-loss ÷ planned-loss for a stop k ATRs from entry (measured; see module doc)."""
    ks = [k for k, _ in OVERSHOOT]
    vs = [v for _, v in OVERSHOOT]
    if k_atr is None or not np.isfinite(k_atr) or k_atr <= 0:
        return vs[0]                                     # unknown distance → the conservative measured end
    if k_atr <= ks[0]:
        c = (vs[0] - 1.0) * ks[0]                        # the curve is ~1 + c/k; extrapolate tighter stops, capped
        return float(min(3.0, 1.0 + c / k_atr))
    if k_atr >= ks[-1]:
        return vs[-1]
    return float(np.interp(k_atr, ks, vs))


def _rsi14(c: np.ndarray) -> float | None:
    d = pd.Series(c).diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    v = float((100 - 100 / (1 + up / dn)).iloc[-1])
    return v if np.isfinite(v) else None


def _comp(key, label, value, cutoff, kind, detail):
    def f(x):
        if x is None:
            return "—"
        return f"{x * 100:.0f}%" if kind == "pct0" else f"{x * 100:+.1f}%"
    hit = bool(value is not None and value >= cutoff)
    return {"key": key, "label": label, "value": _r(value, 4), "value_fmt": f(value),
            "cutoff": cutoff, "cutoff_fmt": f(cutoff), "hit": hit, "detail": detail}


def compute_fragility(daily) -> dict | None:
    """The fragility axis for a daily OHLC frame (same definitions as the calibration study)."""
    try:
        c = daily["Close"].to_numpy(float)
        h = daily["High"].to_numpy(float)
        l = daily["Low"].to_numpy(float)
        n = len(c)
        if n < 30:
            return None
        ret = np.diff(c) / c[:-1]
        vol = float(np.std(ret[-20:], ddof=1) * np.sqrt(252)) if len(ret) >= 20 else None
        sma200 = float(np.mean(c[-200:])) if n >= 200 else None
        above_200 = float(c[-1] / sma200 - 1.0) if sma200 else None
        max_day = float(np.max(ret[-21:])) if len(ret) >= 21 else None
        run_10 = float(c[-1] / c[-11] - 1.0) if n > 10 else None

        pc = np.concatenate([[c[0]], c[:-1]])
        tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
        atr14 = float(np.mean(tr[-14:]))
        ema20 = float(pd.Series(c).ewm(span=20, adjust=False).mean().iloc[-1])

        # is the name in the exact state the odds were calibrated on?
        sma50 = float(np.mean(c[-50:])) if n >= 50 else None
        sma200_prev = float(np.mean(c[-221:-21])) if n >= 221 else None
        r126 = float(c[-1] / c[-127] - 1.0) if n > 126 else None
        rsi = _rsi14(c)
        calibrated = bool(sma50 and sma200 and sma200_prev and r126 is not None and rsi is not None
                          and c[-1] > sma50 > sma200 and sma200 > sma200_prev
                          and c[-1] / float(np.max(h[-252:])) - 1.0 >= -0.10 and r126 >= 0.25 and rsi >= 55)

        comps = [
            _comp("vol", "Realized volatility (20d, annualized)", vol, CUTOFFS["vol"], "pct0",
                  "Std-dev of the last 20 daily returns × √252. The strongest single crash predictor in the test (t=5.4): "
                  "the most volatile fifth of momentum names crashed 18.7% of the time vs 2.4% for the calmest fifth."),
            _comp("above_200", "Distance above the 200-day SMA", above_200, CUTOFFS["above_200"], "pct1",
                  "How stretched price is above its long-term trend (t=3.5): the most stretched fifth crashed 19.6% of the "
                  "time vs 4.1% for the least."),
            _comp("max_day", "Biggest one-day gain, last 21 sessions", max_day, CUTOFFS["max_day"], "pct1",
                  "A recent lottery-like jump (t=3.9): names with the biggest recent single-day pops crashed 16.3% of the "
                  "time vs 3.2%."),
            _comp("run_10", "10-day run-up", run_10, CUTOFFS["run_10"], "pct1",
                  "How hot the last two weeks were (t=3.4): the steepest fifth of recent legs crashed 14.3% of the time vs "
                  "5.1%."),
        ]
        flags = sum(1 for x in comps if x["hit"])
        odds, cap = CRASH_ODDS[flags], SIZE_CAP[flags]
        level = _LEVEL[flags]

        if flags >= 3:
            exit_policy = {
                "mode": "fast", "level": _r(ema20),
                "rule": f"Exit on the first daily CLOSE below the 20-day EMA (${ema20:,.2f}) — from day 1, in addition to the stop.",
                "evidence": ("On top of a normal stop, this exit cut fragile trades losing >10% from 5.2% to 3.5% and improved "
                             "the median from −4.9% to −4.0%, costing ~0.3% of average return per trade (S&P 500, 2019–2026)."),
            }
        else:
            exit_policy = {
                "mode": "standard", "level": None,
                "rule": "Standard exit — the structure stop, then the method's trail once the trade works.",
                "evidence": ("On calm/normal names an extra 20-EMA exit barely changed the tail (worst-5% −7.1% → −6.3%) and "
                             "cost return, so it is not forced."),
            }

        ratio = odds / BASE_CRASH
        headline = (f"Fragility {flags}/4 · {level.upper()} — {odds:.1f}% of similar strong-momentum setups fell ≥15% "
                    f"within 20 days (vs {BASE_CRASH}% on average" + (f", {ratio:.1f}× the base" if flags >= 3 else "") + ").")
        if not calibrated:
            headline += " Reference odds: they were calibrated on strong-momentum setups and this name isn't in that exact state now."
        action = (f"Size capped at {cap_label(cap)} and a 20-EMA close-exit from day 1 — fragility predicts how violent the "
                  f"next month is, not its direction." if flags >= 3 else
                  "No size cap; standard exits. Fragility here is at or near the base crash rate.")
        return {
            "flags": flags, "max_flags": 4, "level": level,
            "crash_odds_pct": odds, "base_crash_pct": BASE_CRASH,
            "size_cap": round(cap, 4), "size_cap_label": cap_label(cap),
            "components": comps, "calibrated_state": calibrated,
            "atr14": _r(atr14, 4), "ema20": _r(ema20),
            "exit_policy": exit_policy, "headline": headline, "action": action, "basis": BASIS,
        }
    except Exception:  # noqa: BLE001 — the overlay must never break a strategy read
        return None


def apply_risk_overlay(setup: dict, frag: dict | None, budget: float = _RISK_BUDGET) -> dict:
    """Gap-adjusted sizing + (for longs) the fragility cap, fast exit and fresh-entry de-rank, applied in place."""
    if not setup:
        return setup
    try:
        entry = (setup.get("entry") or {}).get("level")
        stop = (setup.get("stop") or {}).get("level")
        if not (entry and stop) or entry == stop:
            return setup
        is_long = setup.get("direction") == "long"
        risk = abs(float(entry) - float(stop))
        atr = (frag or {}).get("atr14")
        k = risk / atr if atr else None
        over = gap_overshoot(k)
        flags = (frag or {}).get("flags", 0)
        cap = (frag or {}).get("size_cap", 1.0) if is_long else 1.0
        clabel = cap_label(cap)

        eq = setup.get("equity_plan")
        if eq:
            base_shares = _qty(budget / risk)
            gap_shares = _qty(budget / (risk * over))
            final = _qty(budget / (risk * over) * cap)          # cap applied to the exact count, then rounded once
            fractional = isinstance(final, float)
            reward = eq.get("reward_per_share_t1")
            reward = float(reward) if reward is not None else 0.0
            binding = "fragility" if cap < 0.999 else "gap" if over >= 1.08 else "none"
            explain = [
                f"Base: ${budget:,.0f} risk (1% of a nominal $25k book) ÷ ${risk:,.2f}/share to the stop = {base_shares} shares.",
                (f"Gap-adjusted: the stop is {k:.2f} ATR from entry; stops that tight historically lost {over:.2f}× the planned "
                 f"amount on average when hit (gaps through the stop), so {gap_shares} shares keeps the expected loss when "
                 f"stopped ≈ ${budget:,.0f}.") if k is not None else
                f"Gap-adjusted: ATR unavailable, so the conservative {over:.2f}× overshoot is assumed → {gap_shares} shares.",
                (f"Fragility {flags}/4 cap {clabel}: {frag['crash_odds_pct']:.1f}% of similar setups fell ≥15% within 20 days vs "
                 f"{frag['base_crash_pct']}% on average → {final} shares.") if cap < 0.999 else
                ("No fragility cap (shorts are not capped — fragility is the parabolic-short thesis)." if not is_long
                 else f"No fragility cap ({flags}/4 flags)."),
            ]
            eq["suggested_shares"] = final
            eq["dollar_risk"] = _r(final * risk, 0)
            eq["dollar_reward_t1"] = _r(final * reward, 0)
            eq["expected_loss_if_stopped"] = _r(final * risk * over, 0)
            eq["sizing_basis"] = ("1% risk, gap-adjusted" + (f", fragility cap {clabel}" if cap < 0.999 else "")
                                  + (", fractional shares (whole-share rounding would distort the size at this price)"
                                     if fractional else ""))
            eq["size_adjustment"] = {
                "base_shares": base_shares, "stop_atr_multiple": _r(k, 2), "gap_overshoot": _r(over, 3),
                "gap_adjusted_shares": gap_shares, "fragility_cap": round(cap, 4), "fragility_cap_label": clabel,
                "final_shares": final, "binding": binding, "explain": explain,
            }
            eq["note"] = (f"{final} shares ({eq['sizing_basis']}). Max loss at the stop ≈ ${round(final * risk):,}; expected loss "
                          f"if stopped incl. typical gap slippage ≈ ${round(final * risk * over):,}; T1 gain ≈ ${round(final * reward):,}.")

        if is_long:
            exit_policy = (frag or {}).get("exit_policy") or {"mode": "standard", "rule": "Standard exit.", "evidence": ""}
        else:
            exit_policy = {"mode": "standard", "level": None,
                           "rule": "Short: the stop above the climax high; cover into the 10/20-day MA.",
                           "evidence": "Fragility supports a counter-trend short, so no fragility cap or fast exit is imposed."}
        fresh = bool(is_long and flags >= 3 and setup.get("type") in FRESH_ENTRY_TYPES)
        setup["risk_overlay"] = {
            "fragility_flags": flags, "fragility_level": (frag or {}).get("level"),
            "crash_odds_pct": (frag or {}).get("crash_odds_pct"), "base_crash_pct": BASE_CRASH,
            "size_cap": round(cap, 4), "size_cap_label": clabel,
            "stop_atr_multiple": _r(k, 2), "gap_overshoot": _r(over, 3),
            "exit_policy": exit_policy, "reduced_fresh_entry": fresh,
        }
        if is_long and flags >= 3:
            watch = list(setup.get("what_to_watch") or [])
            watch.insert(0, (f"FRAGILE {flags}/4 ({frag['crash_odds_pct']:.1f}% crash odds vs {BASE_CRASH}% base): size capped at "
                             f"{clabel}; EXIT on the first daily close below the 20-EMA ${frag['ema20']:,.2f} — from day 1, in "
                             f"addition to the stop."))
            if fresh:
                watch.insert(1, "Fresh breakout into a fragile tape: the better entry is a pullback to the rising 20-EMA or a "
                                "new base. If you take this one, take it at the reduced size.")
                setup["score"] = round(float(setup.get("score") or 0) - (2.0 if flags == 3 else 3.0), 2)
                setup["confidence"] = {"high": "medium", "medium": "low"}.get(setup.get("confidence"), setup.get("confidence"))
            setup["what_to_watch"] = watch
    except Exception:  # noqa: BLE001
        pass
    return setup


def two_axis_line(grade, score, frag: dict | None) -> str:
    """The headline both engines lead their summary with: never a lone trend grade."""
    if not frag:
        return f"Trend {grade} ({score}/100)."
    tail = (f" → size {frag['size_cap_label']}, exit on a close below the 20-EMA." if frag["flags"] >= 3
            else " → standard size & exits.")
    return (f"Trend {grade} ({score}/100) · Fragility {frag['flags']}/4 {frag['level']} "
            f"({frag['crash_odds_pct']:.1f}% crash odds vs {frag['base_crash_pct']}%){tail}")
