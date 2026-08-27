"""Institutional REPAIR / ADJUSTMENT menu for a tested short-premium trade.

Leg-based core: each leg = {strike, right('P'/'C'), sign(+1 long / −1 short), qty, dte_days, iv,
entry} where `entry` is the price RECEIVED (short) / PAID (long) when opened. The position is valued
at the NEAREST-expiry HORIZON, so near legs settle at intrinsic while longer-dated legs
(calendars/diagonals/rolls) are Black-Scholes marked on their remaining life.

Pricing is LIVE-CHAIN-first: opening prices are the real chain mid (snapped to listed strikes) and
each leg's IV is the chain's per-strike vol (skew-aware); only the hypothetical-future HORIZON marks
fall back to BS on that live-calibrated skew. When no chain is supplied it degrades to a pure BS
model read (labelled indicative).

The engine takes the WHOLE position: a lone short (CSP / short call) gets the rich single-leg menu
(roll, jade-lizard, iron-condor cap, delta-hedge, wheel); a multi-leg structure (vertical, condor,
strangle) gets STRUCTURAL repairs (roll the whole thing out, roll the tested wing away-&-out, close
the tested wing and keep the rest, close all). Every alternative reports net credit/debit, the
repaired NET GREEKS, max loss / max gain, breakevens, lognormal PoP, defined-risk & upside-risk-free
flags, the exact legs, and the rationale.
"""
from __future__ import annotations

import math
from typing import Optional, Callable

from .stock_service import bs_price, bs_delta, bs_gamma, bs_theta, bs_vega

_MULT = 100.0
_LADDER = tuple(round(-0.30 + 0.05 * i, 2) for i in range(13))          # −30%…+30% display ladder
_WIDE = tuple(round(-0.60 + 0.04 * i, 2) for i in range(31))            # −60%…+60% for max/min


# ── live-chain-aware pricer ──────────────────────────────────────────────────────
class _Pricer:
    """Prices legs from the LIVE chain first (mid + per-strike IV, snapped to listed strikes),
    BS-fallback otherwise. `chains` = {tenor_days: {strike: {'P': {'mid','iv'}, 'C': {...}}}}."""

    def __init__(self, r: float, atm_iv: float, chains: dict, tenors: list[int]):
        self.r = r
        self.atm_iv = max(atm_iv or 0.05, 0.05)
        self.chains = chains or {}
        self.tenors = tenors

    def _chain(self, tenor: int) -> dict:
        if not self.chains:
            return {}
        key = min(self.chains.keys(), key=lambda t: abs(t - tenor))
        return self.chains[key]

    def snap(self, tenor: int, target: float) -> float:
        ch = self._chain(tenor)
        return float(min(ch.keys(), key=lambda k: abs(k - target))) if ch else float(round(target))

    def iv(self, tenor: int, strike: float, right: str) -> float:
        q = (self._chain(tenor).get(strike) or {}).get(right)
        v = q.get("iv") if q else None
        return float(v) if (v and v > 0) else self.atm_iv

    def entry(self, tenor: int, strike: float, right: str, spot: float) -> float:
        """Opening price TODAY (spot ≈ current): live mid if listed, else BS on the skew IV."""
        q = (self._chain(tenor).get(strike) or {}).get(right)
        mid = q.get("mid") if q else None
        if mid and mid > 0:
            return float(mid)
        return bs_price(spot, strike, max(tenor, 1) / 365.0, self.r, self.iv(tenor, strike, right),
                        "put" if right == "P" else "call")

    def strikes(self, tenor: int) -> list[float]:
        return sorted(self._chain(tenor).keys())

    def sigma(self, spot: float, tenor: int, iv: float) -> float:
        """A 1σ move to the horizon — the professional way to place an OTM wing (vs a flat %)."""
        return spot * max(iv, 0.05) * math.sqrt(max(tenor, 1) / 365.0)

    def wing(self, tenor: int, ref: float, direction: str, min_gap: float) -> float:
        """A LISTED strike at least `min_gap` from ref in `direction` ('above'/'below'), guaranteed
        DISTINCT from ref — so a spread can never collapse to zero width. BS-mode: ref ± min_gap."""
        min_gap = max(0.5, min_gap)
        ks = self.strikes(tenor)
        if not ks:
            return round(ref - min_gap, 2) if direction == "below" else round(ref + min_gap, 2)
        if direction == "below":
            cands = [k for k in ks if k <= ref - min_gap]
            return max(cands) if cands else (min(ks) if min(ks) < ref - 1e-9 else round(ref - min_gap, 2))
        cands = [k for k in ks if k >= ref + min_gap]
        return min(cands) if cands else (max(ks) if max(ks) > ref + 1e-9 else round(ref + min_gap, 2))


def _intr(P: float, K: float, right: str) -> float:
    return max(0.0, K - P) if right == "P" else max(0.0, P - K)


def _leg_val(P: float, lg: dict, horizon_days: float, r: float) -> float:
    """Value at horizon spot P: intrinsic if expired by the horizon, else BS on remaining life
    at the leg's own (skew-calibrated) IV — this is what makes calendars/diagonals correct."""
    rem = lg["dte_days"] - horizon_days
    if rem <= 0.5:
        return _intr(P, lg["strike"], lg["right"])
    return bs_price(P, lg["strike"], max(rem, 0.5) / 365.0, r, max(lg["iv"], 0.02),
                    "put" if lg["right"] == "P" else "call")


def _pnl_at(P: float, legs: list[dict], realized: float, stock: Optional[dict], r: float, horizon: float) -> float:
    v = realized
    for lg in legs:
        v += lg["sign"] * lg["qty"] * _MULT * (_leg_val(P, lg, horizon, r) - lg["entry"])
    if stock:
        v += stock["shares"] * (P - stock["basis"])
    return v


def _greeks(S: float, legs: list[dict], r: float, stock: Optional[dict]) -> dict:
    d = g = t = ve = 0.0
    for lg in legs:
        T = max(lg["dte_days"], 1) / 365.0
        K, iv = lg["strike"], max(lg["iv"], 0.02)
        ot = "put" if lg["right"] == "P" else "call"
        m = lg["sign"] * lg["qty"] * _MULT
        d += m * bs_delta(S, K, T, r, iv, ot)
        g += m * bs_gamma(S, K, T, r, iv)
        ve += m * bs_vega(S, K, T, r, iv)
        t += m * bs_theta(S, K, T, r, iv, ot)
    if stock:
        d += stock["shares"]
    return {"delta": round(d, 1), "gamma": round(g, 4), "theta": round(t, 2), "vega": round(ve, 2)}


def _breakevens(spot, legs, realized, stock, r, horizon) -> list[float]:
    pts = [(spot * (1 + m), _pnl_at(spot * (1 + m), legs, realized, stock, r, horizon)) for m in _WIDE]
    out = []
    for (x1, v1), (x2, v2) in zip(pts, pts[1:]):
        if ((v1 < 0 <= v2) or (v1 > 0 >= v2)) and v2 != v1:
            out.append(float(round(x1 + (0 - v1) * (x2 - x1) / (v2 - v1), 2)))
    return out


def _pop(spot, iv, horizon_days, legs, realized, stock, r) -> Optional[int]:
    if horizon_days <= 0 or iv <= 0:
        return None
    T = horizon_days / 365.0
    sig = iv * math.sqrt(T)
    if sig <= 0:
        return None
    lo, hi, n = spot * 0.4, spot * 1.8, 160
    step = (hi - lo) / n
    num = den = 0.0
    for i in range(n):
        P = lo + (i + 0.5) * step
        d2 = (math.log(P / spot) - (r - 0.5 * iv * iv) * T) / sig
        w = math.exp(-0.5 * d2 * d2) / (P * sig)
        den += w * step
        if _pnl_at(P, legs, realized, stock, r, horizon_days) >= 0:
            num += w * step
    return round(num / den * 100) if den > 0 else None


def _build(*, name, category, mechanics, legs, realized, stock, establish_cash, spot, r, rationale, iv_ref) -> dict:
    horizon = min((lg["dte_days"] for lg in legs), default=0) if legs else 0
    scn = [{"move_pct": round(m * 100), "spot": round(spot * (1 + m), 2),
            "pnl": round(_pnl_at(spot * (1 + m), legs, realized, stock, r, horizon), 0)} for m in _LADDER]
    ext_P = [spot * (1 + m) for m in _WIDE] + [0.01, spot * 3.0]
    ext = [_pnl_at(P, legs, realized, stock, r, horizon) for P in ext_P]
    max_loss, max_gain = round(min(ext), 0), round(max(ext), 0)
    short_calls = sum(lg["qty"] for lg in legs if lg["right"] == "C" and lg["sign"] < 0)
    long_calls = sum(lg["qty"] for lg in legs if lg["right"] == "C" and lg["sign"] > 0)
    naked_calls = max(0.0, short_calls - long_calls)
    stock_shares = stock["shares"] if stock else 0
    covered = max(0.0, stock_shares) >= 100 * naked_calls
    upside_undefined = bool((naked_calls > 0 and not covered) or stock_shares < 0)
    gk = _greeks(spot, legs, r, stock)
    return {
        "name": name, "category": category, "mechanics": mechanics, "rationale": rationale, "risk_note": rationale,
        "net_cash": round(establish_cash, 0),
        "scenarios": scn,
        "max_loss": None if upside_undefined else max_loss, "max_gain": max_gain,
        "breakevens": _breakevens(spot, legs, realized, stock, r, horizon),
        "greeks": gk, "theta_day": gk["theta"],
        "defined_risk": not upside_undefined,
        "upside_risk_free": bool(_pnl_at(spot * 1.60, legs, realized, stock, r, horizon) >= -1),
        "pop_pct": _pop(spot, iv_ref, horizon, legs, realized, stock, r),
        "turns_profitable": bool(max_gain > 0),
        "legs": [{"action": "SELL" if lg["sign"] < 0 else "BUY", "right": lg["right"],
                  "strike": round(lg["strike"], 2), "qty": lg["qty"], "dte_days": lg["dte_days"]} for lg in legs]
                + ([{"action": "BUY" if stock_shares > 0 else "SELL", "right": "STK",
                     "strike": round(stock["basis"], 2), "qty": abs(int(stock_shares)), "dte_days": None}] if stock else []),
    }


def _close_realized(legs: list[dict], now_val: Callable[[dict], float]) -> float:
    """Realized P&L from closing a set of held legs at their current value."""
    return sum(lg["sign"] * lg["qty"] * _MULT * (now_val(lg) - lg["entry"]) for lg in legs)


def _close_cash(legs: list[dict], now_val: Callable[[dict], float]) -> float:
    """Net cash NOW from unwinding held legs (sell longs +, buy back shorts −)."""
    return sum(lg["sign"] * lg["qty"] * _MULT * now_val(lg) for lg in legs)


def _open_cash(legs: list[dict]) -> float:
    """Net cash NOW from opening new legs (sell shorts +, buy longs −)."""
    return sum(-lg["sign"] * lg["qty"] * _MULT * lg["entry"] for lg in legs)


def _credit_spread(pr: "_Pricer", tenor: int, K_short: float, side: str, base_credit: float, spot: float):
    """Size a credit spread off LISTED strikes: pick the WIDEST wing where
    base_credit + spread_credit ≥ width (⇒ that tail is risk-free); if none qualifies, take the
    NARROWEST valid spread (least residual risk). Returns
    (K_short, K_far, short_px, long_px, width, spread_credit, risk_free) or None if no valid spread."""
    right = "C" if side == "above" else "P"
    short_px = pr.entry(tenor, K_short, right, spot)
    ks = [k for k in pr.strikes(tenor) if (k > K_short if side == "above" else k < K_short)]
    if not ks:                                              # BS-mode: choose width ≤ credit (risk-free)
        w = max(1.0, round(base_credit)) if base_credit >= 1 else max(1.0, round(spot * 0.03))
        K_far = round(K_short + w if side == "above" else K_short - w, 2)
        long_px = pr.entry(tenor, K_far, right, spot)
        cr = short_px - long_px
        return (K_short, K_far, short_px, long_px, abs(K_far - K_short), cr,
                (base_credit + cr) >= abs(K_far - K_short)) if cr > 0 else None
    widest = sorted(ks, reverse=True) if side == "above" else sorted(ks)     # widest gap first
    for K_far in widest:
        w = abs(K_far - K_short)
        cr = short_px - pr.entry(tenor, K_far, right, spot)
        if cr > 0 and (base_credit + cr) >= w:              # risk-free on this tail
            return (K_short, K_far, short_px, pr.entry(tenor, K_far, right, spot), w, cr, True)
    for K_far in (sorted(ks) if side == "above" else sorted(ks, reverse=True)):   # narrowest valid
        w = abs(K_far - K_short)
        cr = short_px - pr.entry(tenor, K_far, right, spot)
        if cr > 0 and w > 0:
            return (K_short, K_far, short_px, pr.entry(tenor, K_far, right, spot), w, cr, (base_credit + cr) >= w)
    return None


def repair_alternatives(*, legs: list[dict], spot: float, dte_days: int, r: float = 0.045,
                        roll_days: int = 45, atm_iv: float = 0.30,
                        chains: Optional[dict] = None) -> dict:
    """Build the repair menu for the WHOLE position.

    legs: the trade's option legs [{strike, right('P'/'C'), sign(+1/−1), qty, entry}] (entry = the
          per-share credit received / debit paid at open). All at the trade's expiry `dte_days`.
    chains: live {tenor_days: {strike: {'P': {'mid','iv'}, 'C': {...}}}} for the near + far expiries.
    """
    D = int(dte_days)
    Dg = D + roll_days
    pr = _Pricer(r, atm_iv, chains or {}, [D, Dg])

    # normalize the held legs, tagging each with its live skew IV.
    hold: list[dict] = []
    for l in legs:
        rt = "P" if str(l.get("right", l.get("type", ""))).upper().startswith("P") or "PUT" in str(l.get("type", "")).upper() else "C"
        K = float(l["strike"])
        hold.append({"strike": K, "right": rt, "sign": int(l.get("sign", -1)),
                     "qty": int(l.get("qty") or 1), "dte_days": D,
                     "iv": pr.iv(D, K, rt), "entry": abs(float(l.get("entry") or 0.0))})
    if not hold:
        return {"error": "No option legs to repair."}

    def now_val(lg):
        return pr.entry(D, lg["strike"], lg["right"], spot)

    unreal = _close_realized(hold, now_val)
    shorts = [lg for lg in hold if lg["sign"] < 0]
    longs = [lg for lg in hold if lg["sign"] > 0]

    # the TESTED short = the short furthest into (or nearest through) the money.
    def tested_depth(lg):
        return (lg["strike"] - spot) if lg["right"] == "P" else (spot - lg["strike"])   # + = tested/ITM
    tested = max(shorts, key=tested_depth) if shorts else hold[0]
    right = tested["right"]
    K = tested["strike"]
    n = tested["qty"]
    C = tested["entry"]
    iv0 = tested["iv"]
    cushion_pct = ((spot - K) / K * 100.0) if right == "P" else ((K - spot) / K * 100.0)

    def L(strike, rt, sign, tenor, entry, qty=None):
        return {"strike": float(strike), "right": rt, "sign": sign, "qty": qty or n,
                "dte_days": tenor, "iv": pr.iv(tenor, float(strike), rt), "entry": float(entry)}

    alts: list[dict] = []
    # 0) CLOSE ALL — the benchmark.
    alts.append({
        "name": "Close the trade", "category": "exit",
        "mechanics": "Unwind every leg at current prices and walk.",
        "rationale": "Realizes the current P&L with certainty — the bar every repair must clear.",
        "risk_note": "Certain outcome — the benchmark.",
        "net_cash": round(_close_cash(hold, now_val), 0),
        "scenarios": [{"move_pct": round(m * 100), "spot": round(spot * (1 + m), 2), "pnl": round(unreal, 0)} for m in _LADDER],
        "max_loss": round(unreal, 0), "max_gain": round(unreal, 0), "breakevens": [],
        "greeks": {"delta": 0, "gamma": 0, "theta": 0, "vega": 0}, "theta_day": 0,
        "defined_risk": True, "upside_risk_free": True, "pop_pct": None,
        "turns_profitable": bool(unreal > 0), "legs": [],
    })

    lone_short = len(shorts) == 1 and len(longs) == 0

    def _rf_txt(base, cs, width, rf):
        return (f"total credit ${base+cs:.2f} ≥ ${width:.0f} width → that tail is RISK-FREE" if rf
                else f"total credit ${base+cs:.2f} vs ${width:.0f} width → ${max(0.0, width-(base+cs)):.2f}/sh residual risk on that tail")

    if lone_short and right == "P":   # ── CSP: rich single-leg menu ─────────────────────────────
        # 1) ROLL DOWN & OUT — a real listed strike below K (never the same strike).
        K2 = pr.wing(Dg, K, "below", max(spot * 0.05, 0.5 * pr.sigma(spot, Dg, iv0)))
        far = pr.entry(Dg, K2, "P", spot)
        alts.append(_build(
            name=f"Roll down & out (→ ${K2:.0f}, +{roll_days}d)", category="roll",
            mechanics=f"Buy back the ${K:.0f} put (~${now_val(tested):.2f}), sell the ${K2:.0f} put ~{Dg} DTE (~${far:.2f}) — moves the strike {abs(round((K2-K)/K*100))}% lower, buys time.",
            legs=[L(K2, "P", -1, Dg, far)], realized=_close_realized([tested], now_val), stock=None,
            establish_cash=_close_cash([tested], now_val) + _open_cash([L(K2, "P", -1, Dg, far)]),
            spot=spot, r=r, iv_ref=iv0, rationale="Direct downside relief — drops the strike and adds time. Costs little; caps recovery at the new credit."))

        # near call credit spread — short call ~0.6σ OTM (≈30-delta, real premium), wing sized
        # risk-free from LISTED strikes. Only offer if it's worth it: risk-free, or ≥70% covered.
        near = _credit_spread(pr, D, pr.snap(D, spot + 0.6 * pr.sigma(spot, D, iv0)), "above", C, spot)
        if near and (near[6] or (C + near[5]) >= 0.7 * near[4]):
            Kc, Kc2, c_short, c_long, width, cs, rf = near
            # 2) JADE-LIZARD OVERLAY
            alts.append(_build(
                name=f"Jade-lizard overlay (${Kc:.0f}/{Kc2:.0f} calls)", category="overlay",
                mechanics=f"Keep the ${K:.0f} put; sell the ${Kc:.0f} call (~${c_short:.2f}) & buy the ${Kc2:.0f} (~${c_long:.2f}) for ~${cs:+.2f}/sh — {_rf_txt(C, cs, width, rf)}.",
                legs=[L(K, "P", -1, D, C), L(Kc, "C", -1, D, c_short), L(Kc2, "C", 1, D, c_long)],
                realized=0.0, stock=None, establish_cash=cs * _MULT * n, spot=spot, r=r, iv_ref=iv0,
                rationale="Harvests theta on the FAR (low-gamma) call side and lowers your put breakeven with (near) no upside tail. Doesn't fix the downside — the put is still the risk."))
            # 4) IRON-CONDOR CAP — add a protective put wing below K to bound the downside too.
            Kpw = pr.wing(D, K, "below", max(spot * 0.05, pr.sigma(spot, D, iv0)))
            pw = pr.entry(D, Kpw, "P", spot)
            alts.append(_build(
                name=f"Cap into an iron condor (buy ${Kpw:.0f} put)", category="defined_risk",
                mechanics=f"Keep the ${K:.0f} put, BUY a ${Kpw:.0f} put (~${pw:.2f}) to cap the downside, add the ${Kc:.0f}/{Kc2:.0f} call spread — fully DEFINED risk both ways.",
                legs=[L(K, "P", -1, D, C), L(Kpw, "P", 1, D, pw), L(Kc, "C", -1, D, c_short), L(Kc2, "C", 1, D, c_long)],
                realized=0.0, stock=None, establish_cash=(cs - pw) * _MULT * n, spot=spot, r=r, iv_ref=iv0,
                rationale="Both open tails become KNOWN max losses for the cost of the put wing — the move when you want the trade fully bounded before an uncertain stretch."))

        # 3) ROLL-DOWN + JADE — far-dated spread, financed roll.
        far_sp = _credit_spread(pr, Dg, pr.snap(Dg, spot + 0.6 * pr.sigma(spot, Dg, iv0)), "above", C, spot)
        if far_sp and (far_sp[6] or far_sp[5] >= 0.35 * far_sp[4]):
            Kcf, Kc2f, cf_s, cf_l, wf, csf, rff = far_sp
            new_legs = [L(K2, "P", -1, Dg, far), L(Kcf, "C", -1, Dg, cf_s), L(Kc2f, "C", 1, Dg, cf_l)]
            alts.append(_build(
                name=f"Roll-down + jade (${K2:.0f} put + ${Kcf:.0f}/{Kc2f:.0f})", category="overlay",
                mechanics=f"Buy back ${K:.0f}, sell the ${K2:.0f} put ~{Dg}d AND add the ${Kcf:.0f}/{Kc2f:.0f} call spread (~${csf:+.2f}/sh) — the call credit funds the roll-down; {_rf_txt(0, csf, wf, rff)}.",
                legs=new_legs, realized=_close_realized([tested], now_val), stock=None,
                establish_cash=_close_cash([tested], now_val) + _open_cash(new_legs),
                spot=spot, r=r, iv_ref=iv0, rationale="The complete premium-selling repair: lower strike + two theta streams + the call spread pays for the roll."))

        # BROKEN-WING BUTTERFLY RE-CENTER — close the tested put, open a put BWB whose profit TENT
        # sits at the CURRENT spot for a credit/even. A stabilization or small bounce now pays back
        # the loss; the wider (broken) lower wing keeps it a DEFINED-risk credit. The desk's move to
        # "move the target to where the stock actually is" rather than chase it lower.
        gap = max(spot * 0.03, 0.35 * pr.sigma(spot, D, iv0))
        Kb = pr.snap(D, spot)
        Kh = pr.wing(D, Kb, "above", gap)                # tight upper wing
        Kl = pr.wing(D, Kb, "below", 2.5 * gap)          # broken (wider) lower wing → leans to a credit
        if Kh > Kb > Kl:
            bwb = [L(Kh, "P", 1, D, pr.entry(D, Kh, "P", spot)),
                   L(Kb, "P", -1, D, pr.entry(D, Kb, "P", spot), qty=2 * n),
                   L(Kl, "P", 1, D, pr.entry(D, Kl, "P", spot))]
            fly_open = _open_cash(bwb)                    # the fly's OWN cost (excludes closing the loss)
            row = _build(
                name=f"Re-center into a broken-wing fly (${Kl:.0f}/{Kb:.0f}/{Kh:.0f})", category="defined_risk",
                mechanics=f"Close the ${K:.0f} put; open a put fly +1 ${Kh:.0f} / −2 ${Kb:.0f} / +1 ${Kl:.0f} — profit TENT re-centers to ~${Kb:.0f} (spot), defined risk.",
                legs=bwb, realized=_close_realized([tested], now_val), stock=None,
                establish_cash=_close_cash([tested], now_val) + fly_open, spot=spot, r=r, iv_ref=iv0,
                rationale="Re-centers the max-profit to where the stock IS now, so a stabilize / small bounce recovers the loss — defined risk. The desk's 'stop chasing it lower, move the target to spot' repair.")
            # institutional gate: the fly itself must be ~even/credit AND beat simply closing.
            if fly_open >= -0.25 * _MULT * n and row["max_gain"] > unreal:
                alts.append(row)

        _delta_hedge(alts, _build, tested, spot, D, r, iv0, C, n, "put")
        basis = K - C
        Kwc = pr.snap(Dg, max(spot, K) * 1.03)
        wc = pr.entry(Dg, Kwc, "C", spot)
        alts.append(_build(
            name=f"Take assignment → covered call (${Kwc:.0f})", category="assignment",
            mechanics=f"Let the put assign: own {100*n} sh at an effective ${basis:.2f}, then sell the ${Kwc:.0f} call ~{Dg}d (~${wc:.2f}).",
            legs=[L(Kwc, "C", -1, Dg, wc)], realized=0.0, stock={"shares": 100 * n, "basis": basis},
            establish_cash=wc * _MULT * n, spot=spot, r=r, iv_ref=iv0,
            rationale=f"Best when you're happy to OWN {100*n} sh at ${basis:.2f}. Turns the loser into stock+call income; profits on any bounce."))

    elif lone_short and right == "C":  # ── naked/covered short call: mirror ───────────────────────
        K2 = pr.wing(Dg, K, "above", max(spot * 0.05, 0.5 * pr.sigma(spot, Dg, iv0)))
        far = pr.entry(Dg, K2, "C", spot)
        alts.append(_build(
            name=f"Roll up & out (→ ${K2:.0f}, +{roll_days}d)", category="roll",
            mechanics=f"Buy back ${K:.0f} (~${now_val(tested):.2f}), sell ${K2:.0f} ~{Dg}d (~${far:.2f}) — raises the strike, buys time.",
            legs=[L(K2, "C", -1, Dg, far)], realized=_close_realized([tested], now_val), stock=None,
            establish_cash=_close_cash([tested], now_val) + _open_cash([L(K2, "C", -1, Dg, far)]),
            spot=spot, r=r, iv_ref=iv0, rationale="Relieves the tested call by raising the strike; caps recovery at the new credit."))
        # reverse-jade: short put ~0.6σ OTM (below spot) + a real long put wing below — sized
        # risk-free. Only offer if worth it (risk-free or ≥70% covered) — never a tiny credit for a big tail.
        rj = _credit_spread(pr, D, pr.snap(D, spot - 0.6 * pr.sigma(spot, D, iv0)), "below", C, spot)
        if rj and (rj[6] or (C + rj[5]) >= 0.7 * rj[4]):
            Kp, Kp2, p_short, p_long, width, ps, rf = rj
            alts.append(_build(
                name=f"Reverse-jade overlay (${Kp:.0f}/{Kp2:.0f} puts)", category="overlay",
                mechanics=f"Keep the ${K:.0f} call; sell the ${Kp:.0f} put (~${p_short:.2f}) & buy the ${Kp2:.0f} (~${p_long:.2f}) for ~${ps:+.2f}/sh — {_rf_txt(C, ps, width, rf)}.",
                legs=[L(K, "C", -1, D, C), L(Kp, "P", -1, D, p_short), L(Kp2, "P", 1, D, p_long)],
                realized=0.0, stock=None, establish_cash=ps * _MULT * n, spot=spot, r=r, iv_ref=iv0,
                rationale="Mirror jade for a tested call — extra theta on the far (low-gamma) put side, lowers the call breakeven, with (near) no added downside tail. Doesn't fix the tested call itself."))
        _delta_hedge(alts, _build, tested, spot, D, r, iv0, C, n, "call")

    else:   # ── MULTI-LEG structure (vertical / condor / strangle): STRUCTURAL repairs ──────────
        # 1) Roll the WHOLE structure out — same strikes, later expiry.
        new_whole = [L(lg["strike"], lg["right"], lg["sign"], Dg, pr.entry(Dg, lg["strike"], lg["right"], spot), lg["qty"]) for lg in hold]
        alts.append(_build(
            name=f"Roll the whole structure out (+{roll_days}d)", category="roll",
            mechanics=f"Close all {len(hold)} legs and reopen the SAME strikes ~{Dg} DTE — resets the clock for the structure to work out, usually near credit-neutral.",
            legs=new_whole, realized=_close_realized(hold, now_val), stock=None,
            establish_cash=_close_cash(hold, now_val) + _open_cash(new_whole), spot=spot, r=r, iv_ref=iv0,
            rationale="Buys the whole structure more time without changing its shape — best when the thesis is intact but it needs longer."))

        # the tested WING = the tested short + a same-side protective long (a vertical).
        wing = [tested] + [lg for lg in longs if lg["right"] == right]
        other = [lg for lg in hold if lg not in wing]
        # 2) Roll the tested WING away & out — keep the other wing.
        shift = 0.94 if right == "P" else 1.06
        wing_new = []
        for lg in wing:
            nk = pr.snap(Dg, lg["strike"] * shift)
            wing_new.append(L(nk, lg["right"], lg["sign"], Dg, pr.entry(Dg, nk, lg["right"], spot), lg["qty"]))
        target = other_as_legs(other, L, pr, Dg=None) + wing_new
        alts.append(_build(
            name=f"Roll the tested {'put' if right=='P' else 'call'} wing away & out", category="roll",
            mechanics=f"Buy back the tested {'put' if right=='P' else 'call'} wing and reopen it ~{abs(round((shift-1)*100))}% {'lower' if right=='P' else 'higher'} & ~{Dg}d; keep the far wing as-is.",
            legs=target, realized=_close_realized(wing, now_val), stock=None,
            establish_cash=_close_cash(wing, now_val) + _open_cash(wing_new), spot=spot, r=r, iv_ref=iv0,
            rationale="Relieves ONLY the threatened side — the standard condor/strangle defense: move the tested wing out of the way, keep collecting on the safe wing."))

        # 3) Close the tested WING, keep the rest — bank the untested credit.
        if other:
            alts.append(_build(
                name=f"Close the tested wing, keep the {'call' if right=='P' else 'put'} side", category="defined_risk",
                mechanics=f"Buy back the tested {'put' if right=='P' else 'call'} wing (stop that bleed) and hold the untested wing to expiry to keep its credit.",
                legs=[dict(lg) for lg in other], realized=_close_realized(wing, now_val), stock=None,
                establish_cash=_close_cash(wing, now_val), spot=spot, r=r, iv_ref=iv0,
                rationale="Takes the risk off the tested side for a defined cost while the safe wing keeps decaying — a clean partial exit."))

    return {
        "tested": bool(cushion_pct < 5.0), "cushion_pct": round(cushion_pct, 1),
        "short_right": right, "short_strike": K, "spot": round(spot, 2),
        "dte_days": D, "contracts": n, "unrealized_pnl": round(unreal, 0),
        "structure": _classify(hold),
        "pricing": ("live chain mid + skew IV (horizon marks BS-calibrated)" if chains else "model (Black-Scholes) — indicative"),
        "alternatives": alts,
    }


def other_as_legs(other, L, pr, Dg=None):
    """Re-express the untouched legs as fresh leg dicts (kept at their current entry)."""
    return [dict(lg) for lg in other]


def _delta_hedge(alts, build, tested, spot, D, r, iv0, C, n, otype):
    d = bs_delta(spot, tested["strike"], max(D, 1) / 365.0, r, iv0, otype)
    if otype == "put":
        hedge = round(d * _MULT * n)          # short put +Δ → SHORT shares
        stock = {"shares": -abs(hedge), "basis": spot}
        verb = f"short {abs(hedge)}"
    else:
        hedge = round(-d * _MULT * n)          # short call −Δ → LONG shares
        stock = {"shares": abs(hedge), "basis": spot}
        verb = f"long {abs(hedge)}"
    alts.append(build(
        name=f"Delta-hedge ({verb} sh / futures)", category="hedge",
        mechanics=f"Neutralize the position delta by going {verb} shares (or the future) — freezes further P&L here, instantly reversible.",
        legs=[dict(tested)], realized=0.0, stock=stock, establish_cash=0.0, spot=spot, r=r, iv_ref=iv0,
        rationale="Stabilizer, not a fix — stops the bleed in the tested direction but caps the recovery too, and the hedge delta drifts (re-hedge). Tactical, buys decision time."))


def _classify(hold) -> str:
    sp = sum(1 for lg in hold if lg["right"] == "P" and lg["sign"] < 0)
    sc = sum(1 for lg in hold if lg["right"] == "C" and lg["sign"] < 0)
    lp = sum(1 for lg in hold if lg["right"] == "P" and lg["sign"] > 0)
    lc = sum(1 for lg in hold if lg["right"] == "C" and lg["sign"] > 0)
    if sp == 1 and sc == 1 and lp == 1 and lc == 1:
        return "iron_condor"
    if sp == 1 and lp == 1 and sc == 0 and lc == 0:
        return "put_credit_spread"
    if sc == 1 and lc == 1 and sp == 0 and lp == 0:
        return "call_credit_spread"
    if sp == 1 and sc == 1 and lp == 0 and lc == 0:
        return "short_strangle"
    if sp == 1 and len(hold) == 1:
        return "cash_secured_put"
    if sc == 1 and len(hold) == 1:
        return "short_call"
    return "custom"
