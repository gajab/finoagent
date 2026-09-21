"""Deep-quant ROLL OPTIMIZER for a tested short-premium trade.

The defense principle the user asked for: **no net new money**. A roll must be a CREDIT (the new
short's premium ≥ the buy-back of the tested short). Among the credit rolls, pick the (strike, expiry)
that best balances the three things that actually matter when you're trapped:

  1. loss vs. cash        — bank a credit now, don't pay to defend;
  2. capital to add/receive — prefer receiving premium; a lower strike ties up less (a CSP);
  3. lowest chance the NEXT trade loses — the new short should have a high probability of finishing
     OTM, judged by the market-implied **RND** (not just Black-Scholes), and its strike should sit
     BEYOND the levels the stock struggles to cross: **support/resistance** (TA), a **gamma wall** or
     the **gamma flip** (dealer positioning / GEX), the **volume-profile POC**, and a low-density
     tail of the RND.

Everything is measured off the live chain across several roll-out expiries — no invented numbers. The
result is the top-ranked credit rolls, each with its full factor breakdown and the exact legs to trade.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import math
from typing import Optional


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_p_otm(spot: float, K: float, dte: int, iv: float, right: str, r: float = 0.045) -> Optional[float]:
    """Black-Scholes lognormal P(the new short finishes OTM). put OTM = S_T > K; call OTM = S_T < K."""
    T = max(dte, 1) / 365.0
    s = max(iv, 0.02)
    sig = s * math.sqrt(T)
    if sig <= 0:
        return None
    d2 = (math.log(spot / K) + (r - 0.5 * s * s) * T) / sig
    return _norm_cdf(d2) if right == "P" else _norm_cdf(-d2)


def _poc(closes, volumes, bins: int = 30) -> Optional[float]:
    """Volume point-of-control: the price bin that traded the most volume over the window."""
    import numpy as np
    c = np.asarray(closes, dtype=float); v = np.asarray(volumes, dtype=float)
    if len(c) < 5 or v.sum() <= 0:
        return None
    lo, hi = float(c.min()), float(c.max())
    if hi <= lo:
        return None
    edges = np.linspace(lo, hi, bins + 1)
    idx = np.clip(np.digitize(c, edges) - 1, 0, bins - 1)
    vol = np.zeros(bins)
    for i, vi in zip(idx, v):
        vol[int(i)] += float(vi)
    b = int(vol.argmax())
    return round(float((edges[b] + edges[b + 1]) / 2.0), 2)


async def optimize_roll(*, ticker: str, provider, spot: float, tested_right: str, tested_strike: float,
                        tested_qty: int, tested_entry: float, current_dte: int, covered: bool = False,
                        r: float = 0.045, max_expiries: int = 5, max_horizon: int = 130) -> dict:
    """Search the (strike, expiry) grid of CREDIT rolls and rank them on the confluence of RND
    probability, market structure (support/resistance · gamma flip/wall · volume POC), credit and
    cushion. Returns {tested, structure, weights, candidates:[…]}. Best-effort; guarded throughout."""
    from .hedging_service import _split_chain, _build_rnd
    from .stock_service import find_support_resistance
    from . import dealer_positioning_service as dps

    is_put = tested_right == "P"
    today = dt.date.today()

    try:
        exps_raw = list(await provider.get_option_expirations(ticker))
    except Exception:  # noqa: BLE001
        return {"error": "Couldn't fetch option expirations to optimize the roll."}

    def _dte(e) -> Optional[int]:
        try:
            return (dt.date.fromisoformat(str(e)[:10]) - today).days
        except (ValueError, TypeError):
            return None

    # ── candidate roll-OUT expiries. Prefer the liquid MONTHLIES (3rd Friday) traders actually roll to —
    #    include EVERY monthly in the window plus the nearest weekly (a fast/cheap roll). Never drop a
    #    monthly for an arbitrary weekly the way blind index-subsampling did (that skipped monthlies). ──
    def _is_monthly(iso: str) -> bool:
        try:
            d = dt.date.fromisoformat(iso)
            return d.weekday() == 4 and 15 <= d.day <= 21          # the standard 3rd-Friday monthly
        except (ValueError, TypeError):
            return False

    inwin = sorted(((str(e)[:10], _dte(e)) for e in exps_raw if _dte(e) is not None
                    and current_dte + 3 <= _dte(e) <= max_horizon), key=lambda x: x[1])
    if not inwin:
        return {"error": "No later expirations available within the roll horizon — nothing to roll into."}
    monthlies = [x for x in inwin if _is_monthly(x[0])]
    nearest_weekly = next((x for x in inwin if not _is_monthly(x[0])), None)
    chosen = list(monthlies)
    if nearest_weekly and nearest_weekly not in chosen:
        chosen.insert(0, nearest_weekly)                           # keep a quick near-dated roll on the menu
    if not chosen:                                                 # (rare) no monthly in window → take what's there
        chosen = inwin
    cand_exps = sorted(set(chosen), key=lambda x: x[1])[:max_expiries]

    # ── structure levels (once). A short-premium trade gets into TROUBLE on the last ~5–15 days' move
    #    (a spike/crash), so read structure THERE — recent intraday pivots, not a stale 6-month daily
    #    chart whose levels the stock has long since left behind. ──
    def _structure() -> dict:
        import yfinance as yf
        tk = yf.Ticker(ticker)

        def _levels(period, interval):
            try:
                h = tk.history(period=period, interval=interval)
                if h is None or h.empty or len(h) < 12:
                    return None
                sup, res = find_support_resistance(h["High"].tolist(), h["Low"].tolist(), h["Close"].tolist())
                return {"support": (float(sup) if sup is not None else None),
                        "resistance": (float(res) if res is not None else None),
                        "poc": _poc(h["Close"].tolist(), h["Volume"].tolist()),
                        "bars": int(len(h)), "window": period}
            except Exception:  # noqa: BLE001
                return None

        out: dict = {}
        recent = _levels("5d", "15m")                                    # the last week — immediate pivots
        swing = _levels("15d", "30m") or _levels("1mo", "1d")            # the last ~2–3 weeks
        prim = swing or recent or {}
        out["support"], out["resistance"], out["poc"] = prim.get("support"), prim.get("resistance"), prim.get("poc")
        out["recent"], out["swing"] = recent, swing
        try:
            dp = dps.compute_dealer_positioning(tk)                      # gamma is off the LIVE chain → already 'now'
            if dp:
                out["flip"] = (dp.get("gamma_flip") or {}).get("level")
                walls = dp.get("walls") or {}
                w = (walls.get("put_wall") if is_put else walls.get("call_wall")) or {}
                out["wall"] = w.get("strike")
                out["gamma_regime"] = (dp.get("net_gex") or {}).get("sign")
        except Exception:  # noqa: BLE001
            pass
        try:
            from . import correlated_assets_service as cas
            out["next_earnings"] = cas._next_earnings_sync(ticker)          # rolling PAST earnings = binary risk
        except Exception:  # noqa: BLE001
            out["next_earnings"] = None
        return out

    struct = await asyncio.to_thread(_structure)
    support, resistance = struct.get("support"), struct.get("resistance")   # ~15-day swing (primary)
    poc, flip, wall = struct.get("poc"), struct.get("flip"), struct.get("wall")
    _r5 = struct.get("recent") or {}
    sup5, res5, poc5 = _r5.get("support"), _r5.get("resistance"), _r5.get("poc")   # last ~5 days (immediate)
    try:
        _ne = struct.get("next_earnings")
        next_earn = dt.date.fromisoformat(str(_ne)[:10]) if _ne else None
    except (ValueError, TypeError):
        next_earn = None

    # ── buy-back mark of the tested short (current expiry) ──
    cur_exp = min(exps_raw, key=lambda e: abs((_dte(e) or 0) - current_dte)) if exps_raw else None
    tested_mark = None
    if cur_exp is not None:
        try:
            c0, p0 = _split_chain(await provider.get_option_chain(ticker, cur_exp))
            q0 = (p0 if is_put else c0).get(float(tested_strike))
            m0 = getattr(q0, "mid", None) if q0 else None
            tested_mark = float(m0) if m0 else None
        except Exception:  # noqa: BLE001
            tested_mark = None
    if not tested_mark or tested_mark <= 0:
        intr = max(0.0, tested_strike - spot) if is_put else max(0.0, spot - tested_strike)
        tested_mark = intr + 0.05                                   # intrinsic + a token time value

    base_capital = 100.0 * float(tested_strike) * tested_qty
    # probability (RND, time-aware) leads; structure is time-DECAYED and cushion is measured in σ over
    # the horizon, so a longer expiry (more days to drift across a level) scores lower — and credit is
    # de-weighted so a far-dated roll can't win on premium it only earns by taking on more time/risk.
    weights = {"probability": 0.40, "structure": 0.30, "credit": 0.15, "cushion": 0.15}
    results: list[dict] = []

    for exp_iso, dte in cand_exps:
        try:
            calls, puts = _split_chain(await provider.get_option_chain(ticker, exp_iso))
        except Exception:  # noqa: BLE001
            continue
        book = puts if is_put else calls
        if not book:
            continue
        strikes_all = sorted(set(list(calls.keys()) + list(puts.keys())))
        rnd = None
        try:
            rnd = await asyncio.to_thread(_build_rnd, calls, puts, strikes_all, spot, dte)
        except Exception:  # noqa: BLE001
            rnd = None
        atm_k = min(book.keys(), key=lambda k: abs(k - spot))
        atm_iv = getattr(book.get(atm_k), "iv", None)

        # Candidate strikes on the SAFE side of the CURRENT strike — a roll must never move the strike
        # to a RISKIER spot than it already is (a short call rolls UP / same, a short put rolls DOWN /
        # same). Anchoring only to spot wrongly offered rolling a far-OTM $770 call down to ~$650.
        if is_put:
            hi = min(float(tested_strike), spot * 1.005)       # never roll the put UP (raises risk)
            lo = min(float(tested_strike), spot) * 0.80
        else:
            lo = max(float(tested_strike), spot * 0.995)       # never roll the call DOWN (raises risk)
            hi = max(float(tested_strike), spot) * 1.20
        for K in sorted((k for k in book.keys() if lo <= k <= hi), reverse=is_put):
            q = book.get(K)
            credit_new = float(getattr(q, "mid", None) or 0.0)
            if credit_new <= 0:
                continue
            roll_net = round((credit_new - tested_mark) * 100.0 * tested_qty, 0)
            if roll_net < 0:                                        # ── CREDIT ONLY: no net new money ──
                continue
            p_otm, src = None, "BS"
            if rnd is not None:
                try:
                    p_otm = float(rnd.prob_above(K) if is_put else rnd.prob_below(K)); src = "RND"
                except Exception:  # noqa: BLE001
                    p_otm = None
            if p_otm is None and atm_iv:
                p_otm = _bs_p_otm(spot, K, dte, float(atm_iv), tested_right, r)
            if p_otm is None or p_otm < 0.55:                      # quality floor — no coin-flip rolls
                continue

            total_credit = tested_entry + roll_net / (100.0 * tested_qty)
            new_be = round(K - total_credit if is_put else K + total_credit, 2)
            new_cap = round(100.0 * K * tested_qty, 0)

            # confluence against the RECENT structure — the strike must sit BEYOND the levels the stock
            # struggles to cross (safe direction: below for a put, above for a call).
            if is_put:
                lv = [("15d support", support), ("5d pivot", sup5), ("5d POC", poc5),
                      ("volume POC", poc), ("gamma wall", wall), ("gamma flip", flip)]
                checks = [(nm, K <= level) for nm, level in lv if level]
            else:
                lv = [("15d resistance", resistance), ("5d pivot", res5), ("5d POC", poc5),
                      ("volume POC", poc), ("gamma wall", wall), ("gamma flip", flip)]
                checks = [(nm, K >= level) for nm, level in lv if level]
            flags = {f"clears {nm}": ok for nm, ok in checks}
            cleared = [nm for nm, ok in checks if ok]
            n_levels = len(checks)
            structure_raw = (len(cleared) / n_levels * 100.0) if n_levels else 50.0
            # Structure protection ERODES over time — a support/resistance level is a short-horizon
            # phenomenon; the more days to expiry, the more the stock can drift across it. Discount it.
            time_factor = max(0.45, min(1.0, 1.0 - (dte - 30) / 140.0))
            structure_score = structure_raw * time_factor

            pop_score = p_otm * 100.0
            credit_score = max(0.0, min(100.0, roll_net / (0.01 * base_capital) * 100.0)) if base_capital > 0 else 0.0
            # Cushion in σ OVER THIS EXPIRY'S HORIZON — the SAME $ distance is FEWER σ (weaker) the more
            # days to expiry (σ grows with √T), so a far-dated roll must sit further out to score. This is
            # exactly the "more days → more chance to drift across the strike/level" the trader feels.
            iv_ref = float(atm_iv) if atm_iv else 0.30
            sig_move = iv_ref * math.sqrt(max(dte, 1) / 365.0)
            cushion_sigma = (abs(math.log(K / spot)) / sig_move) if (sig_move > 0 and spot > 0 and K > 0) else 0.0
            cushion_score = max(0.0, min(100.0, cushion_sigma / 1.5 * 100.0))   # ≥1.5σ OTM ⇒ full marks
            composite = round(weights["probability"] * pop_score + weights["structure"] * structure_score
                              + weights["credit"] * credit_score + weights["cushion"] * cushion_score, 1)
            try:
                spans_earn = bool(next_earn and today < next_earn <= dt.date.fromisoformat(exp_iso))
            except (ValueError, TypeError):
                spans_earn = False
            if spans_earn:
                composite = round(composite - 6.0, 1)              # rolling PAST earnings adds binary risk

            why = (f"Roll to the ${K:.0f} {'put' if is_put else 'call'} exp {exp_iso} for a "
                   f"{'+' if roll_net >= 0 else '−'}${abs(roll_net):,.0f} credit — "
                   f"{round(p_otm * 100)}% {src}-OTM ({cushion_sigma:.1f}σ over {dte}d)"
                   + (f", clears {', '.join(cleared)}" if cleared else ", but clears no structural level")
                   + (" — but that's far out, so structure counts less" if time_factor < 0.7 else "")
                   + f"; new breakeven ${new_be}"
                   + (f", frees capital to ${new_cap:,.0f}" if is_put and new_cap < base_capital else "")
                   + (f"; ⚠ spans earnings {next_earn.isoformat()} (binary risk)" if spans_earn
                      else " — stays before earnings" if next_earn else "")
                   + ".")

            results.append({
                "expiry": exp_iso, "dte": dte, "strike": round(K, 2), "right": tested_right,
                "roll_net_cash": roll_net, "credit_per_share": round(credit_new - tested_mark, 2),
                "new_credit_total": round(total_credit, 2), "new_breakeven": new_be, "new_capital": new_cap,
                "p_otm": round(p_otm * 100, 1), "p_otm_source": src, "spans_earnings": spans_earn,
                "structure": {**flags, "cleared_count": len(cleared), "levels_available": n_levels},
                "scores": {"composite": composite, "probability": round(pop_score, 1),
                           "structure": round(structure_score, 1), "credit": round(credit_score, 1),
                           "cushion": round(cushion_score, 1),
                           "cushion_sigma": round(cushion_sigma, 2), "time_factor": round(time_factor, 2)},
                "legs": [
                    {"action": "BUY", "right": tested_right, "strike": round(float(tested_strike), 2),
                     "qty": tested_qty, "dte_days": current_dte, "expiry": (str(cur_exp)[:10] if cur_exp else None)},
                    {"action": "SELL", "right": tested_right, "strike": round(K, 2),
                     "qty": tested_qty, "dte_days": dte, "expiry": exp_iso},
                ],
                "why": why,
            })

    results.sort(key=lambda x: -x["scores"]["composite"])
    return {
        "tested": {"right": tested_right, "strike": round(float(tested_strike), 2), "qty": tested_qty,
                   "entry": round(tested_entry, 2), "mark": round(tested_mark, 2), "dte": current_dte,
                   "covered": bool(covered)},
        "spot": round(spot, 2),
        "structure": {"support": support, "resistance": resistance, "poc": poc,
                      "gamma_flip": flip, "gamma_wall": wall, "gamma_regime": struct.get("gamma_regime"),
                      "recent_5d": ({"support": sup5, "resistance": res5, "poc": poc5} if _r5 else None),
                      "next_earnings": (next_earn.isoformat() if next_earn else None),
                      "window": "structure read from the last ~5–15 days (recent intraday pivots) — where the trouble happened"},
        "weights": weights,
        "considered": len(results),
        "candidates": results[:6],
        "note": ("Covered call — the roll keeps your shares; 'capital' below is the called-away notional, not new cash."
                 if covered else "Cash-secured short — a lower strike frees capital; every roll shown is a NET CREDIT."),
    }
