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

    def __init__(self, r: float, atm_iv: float, chains: dict, tenors: list[int], spot: Optional[float] = None):
        self.r = r
        self.atm_iv = max(atm_iv or 0.05, 0.05)
        self.chains = chains or {}
        self.tenors = tenors
        self.spot = spot
        self._atm_cache: dict = {}

    def _chain(self, tenor: int) -> dict:
        if not self.chains:
            return {}
        key = min(self.chains.keys(), key=lambda t: abs(t - tenor))
        return self.chains[key]

    def _tenor_atm_iv(self, tenor: int) -> float:
        """ATM IV of THIS tenor's own chain (nearest-to-spot strike, P/C blended) — so an off-strike
        far leg falls back to the FAR vol, not the near ATM. This is what preserves the term-structure
        edge in a calendar / diagonal instead of flattening it to a single vol."""
        key = min(self.chains.keys(), key=lambda t: abs(t - tenor)) if self.chains else None
        if key is None:
            return self.atm_iv
        if key in self._atm_cache:
            return self._atm_cache[key]
        ch = self.chains[key]
        s = self.spot if (self.spot and self.spot > 0) else sorted(ch.keys())[len(ch) // 2]
        k = min(ch.keys(), key=lambda x: abs(x - s))
        ivs = [float(v) for v in ((ch[k].get(rt) or {}).get("iv") for rt in ("P", "C")) if v and v > 0]
        out = (sum(ivs) / len(ivs)) if ivs else self.atm_iv
        self._atm_cache[key] = out
        return out

    def snap(self, tenor: int, target: float) -> float:
        ch = self._chain(tenor)
        return float(min(ch.keys(), key=lambda k: abs(k - target))) if ch else float(round(target))

    def iv(self, tenor: int, strike: float, right: str) -> float:
        q = (self._chain(tenor).get(strike) or {}).get(right)
        v = q.get("iv") if q else None
        return float(v) if (v and v > 0) else self._tenor_atm_iv(tenor)

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


def _norm_cdf(x: float) -> float:
    """Standard-normal CDF via erf — for the closed-form risk-neutral P(finish ITM)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _dist_metrics(spot, iv, horizon_days, legs, realized, stock, r) -> tuple[Optional[int], Optional[float]]:
    """Lognormal-weighted PoP (%) AND E[P&L] ($) to the horizon — the recovery-odds engine.
    Returns (pop_pct, expected_pnl). The expected P&L is what a 'do-nothing / hold' read needs:
    not just 'can it come back' but 'what is this worth on average from here'."""
    if horizon_days <= 0 or iv <= 0:
        return None, None
    T = horizon_days / 365.0
    sig = iv * math.sqrt(T)
    if sig <= 0:
        return None, None
    lo, hi, n = spot * 0.4, spot * 1.8, 160
    step = (hi - lo) / n
    num = den = ev = 0.0
    for i in range(n):
        P = lo + (i + 0.5) * step
        d2 = (math.log(P / spot) - (r - 0.5 * iv * iv) * T) / sig
        w = math.exp(-0.5 * d2 * d2) / (P * sig)
        pnl = _pnl_at(P, legs, realized, stock, r, horizon_days)
        den += w * step
        ev += pnl * w * step
        if pnl >= 0:
            num += w * step
    if den <= 0:
        return None, None
    return round(num / den * 100), round(ev / den, 0)


def _pop(spot, iv, horizon_days, legs, realized, stock, r) -> Optional[int]:
    return _dist_metrics(spot, iv, horizon_days, legs, realized, stock, r)[0]


def _build(*, name, category, mechanics, legs, realized, stock, establish_cash, spot, r, rationale, iv_ref,
           group="adjust") -> dict:
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
    pop, ev = _dist_metrics(spot, iv_ref, horizon, legs, realized, stock, r)
    return {
        "name": name, "category": category, "group": group,
        "mechanics": mechanics, "rationale": rationale, "risk_note": rationale,
        "net_cash": round(establish_cash, 0),
        "scenarios": scn,
        "max_loss": None if upside_undefined else max_loss, "max_gain": max_gain,
        "breakevens": _breakevens(spot, legs, realized, stock, r, horizon),
        "greeks": gk, "theta_day": gk["theta"],
        "defined_risk": not upside_undefined,
        "upside_risk_free": bool(_pnl_at(spot * 1.60, legs, realized, stock, r, horizon) >= -1),
        "pop_pct": pop, "ev": ev,
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


def _recoverability(*, spot, K, right, C, iv0, D, r, hold_legs, hold_pop, stock=None) -> dict:
    """Lens 1 — RECOVERABILITY. Given you're already here: how likely is a return to breakeven
    (recovery_score = the do-nothing PoP), how far that breakeven is in σ, and how deep in trouble
    the tested short is — a fresh breach still full of time value vs one effectively already assigned."""
    T = max(D, 1) / 365.0
    otype = "put" if right == "P" else "call"
    tdelta = bs_delta(spot, K, T, r, max(iv0, 0.02), otype)
    bes = _breakevens(spot, hold_legs, 0.0, stock, r, D)
    # the breakeven you must reclaim — on the tested side of spot (a put needs spot UP, a call DOWN).
    if right == "P":
        cands = [b for b in bes if b >= spot] or bes
        be = min(cands) if cands else None
    else:
        cands = [b for b in bes if b <= spot] or bes
        be = max(cands) if cands else None
    sig = max(iv0, 0.02) * math.sqrt(T)
    dist_sigma = round(abs(math.log(be / spot)) / sig, 2) if (be and sig > 0 and be > 0) else None
    absd = abs(tdelta)
    severity = ("assigned" if absd >= 0.85 else "deep" if absd >= 0.65
                else "fresh" if absd >= 0.45 else "healthy")
    # POSTURE — is this trade actually in trouble? A far-OTM short (low Δ) is HEALTHY / winning; the
    # "recovery" framing only makes sense once it's TESTED. Get this right or the whole lens contradicts
    # itself (96% "recoverable" yet "needs a +36% bounce, outlook adverse").
    posture = "tested" if severity != "healthy" else "healthy"
    exp_move = spot * max(iv0, 0.02) * math.sqrt(T)
    # signed move from spot to breakeven: for a TESTED short it's the RECOVERY move needed; for a
    # HEALTHY short it's the ADVERSE move the position can ABSORB before breakeven (the cushion).
    move_to_be = round((be / spot - 1.0) * 100.0, 1) if be else None
    needed_move_pct = move_to_be if posture == "tested" else None
    cushion_pct = (abs(move_to_be) if move_to_be is not None else None) if posture == "healthy" else None

    factors: list[dict] = []
    if posture == "healthy":
        factors.append({"label": "Cushion to breakeven", "kind": "prob", "favorable": True,
                        "detail": (f"the stock can move {cushion_pct:.1f}% ({dist_sigma}σ) against you before "
                                   f"${round(be,2)} — that's the cushion to breakeven at expiry"
                                   if cushion_pct is not None else "well clear of the strike")})
        factors.append({"label": "Time / theta", "kind": "prob", "favorable": True,
                        "detail": f"{D} DTE of decay working FOR you (more time is also more chance to get tested)"})
    else:
        if needed_move_pct is not None:
            past = needed_move_pct <= 0
            factors.append({"label": "Distance to breakeven", "kind": "prob",
                            "favorable": bool(past or (dist_sigma is not None and dist_sigma <= 0.75)),
                            "detail": (f"already {abs(needed_move_pct):.1f}% past breakeven — cushion, not a deficit" if past
                                       else f"needs a {needed_move_pct:.1f}% move ({dist_sigma}σ) to reclaim ${round(be,2)} by expiry")})
        factors.append({"label": "Time remaining", "kind": "prob", "favorable": bool(D and D >= 10),
                        "detail": (f"{D} DTE — room for a bounce to develop" if (D and D >= 10)
                                   else f"{D} DTE — little runway left to recover")})
    mny = ("far OTM, well clear of the strike" if absd < 0.20 else "moderately OTM" if absd < 0.38
           else "approaching the strike" if absd < 0.60 else "right at the strike" if absd < 0.85 else "through the strike")
    factors.append({"label": "Moneyness", "kind": "prob", "favorable": bool(absd < 0.30),
                    "detail": f"short Δ {round(tdelta,2)} — {mny}"})
    factors.append({"label": "Implied vol", "kind": "prob", "favorable": None,
                    "detail": f"σ ≈ {iv0*100:.0f}% → ±${exp_move:.2f} expected move by expiry"})

    return {
        "recovery_score": hold_pop,                # HEALTHY: P(keep the premium / expire OTM). TESTED: P(recover to breakeven).
        "posture": posture,                        # healthy = safe/winning; tested = in trouble
        "breakeven": round(be, 2) if be else None,
        "needed_move_pct": needed_move_pct,        # % move to reclaim breakeven (TESTED only)
        "cushion_pct": cushion_pct,                # adverse % move the position can absorb (HEALTHY only)
        "dist_to_be_sigma": dist_sigma,            # that move in std-devs to the horizon
        "expected_move": round(exp_move, 2),       # ±1σ $ move to expiry (the yardstick for the above)
        "tested_delta": round(tdelta, 2),
        "severity": severity,                      # fresh | deep | assigned | healthy
        "factors": factors,                        # auditable build-up (prob drivers + TA appended by router)
        "outlook": None,                           # {tilt, note} — set by the router once TA factors are known
    }


def _assignment(*, now_val, spot, K, right, C, iv0, D, r, n, ex_div=None, covered=False, stock_basis=None) -> dict:
    """Lens 2 — ASSIGNMENT (the real risk on a trapped short). Risk-neutral P(finish ITM), the
    extrinsic value left (→ early-exercise pressure), pin risk into expiry, and the DOLLAR
    consequence of being assigned (effective basis / capital tied up)."""
    T = max(D, 1) / 365.0
    s = max(iv0, 0.02)
    sig = s * math.sqrt(T)
    p_itm = None
    if sig > 0:
        d2 = (math.log(spot / K) + (r - 0.5 * s * s) * T) / sig
        p_itm = round((_norm_cdf(-d2) if right == "P" else _norm_cdf(d2)) * 100)
    mark = now_val({"strike": K, "right": right})
    intrinsic = _intr(spot, K, right)
    extrinsic = round(max(0.0, mark - intrinsic), 2)
    # early exercise: a short call is called away just before ex-div when time value < the dividend;
    # a short put only when it's so deep ITM that ~no time value remains.
    early, early_reason = False, None
    if right == "C" and ex_div and ex_div.get("amount", 0) > 0 and (ex_div.get("days") is None or ex_div["days"] <= D):
        if extrinsic < float(ex_div["amount"]):
            early, early_reason = True, (f"${float(ex_div['amount']):.2f} dividend vs ${extrinsic:.2f} time "
                                         "value → likely called just before ex-div")
    if not early and intrinsic > 0 and extrinsic <= 0.10:
        early, early_reason = True, f"deep ITM, only ~${extrinsic:.2f} time value left → assignment can come any day"
    em = spot * s * math.sqrt(T)
    pin = round(abs(spot - K) / em, 2) if em > 0 else None
    basis = round(K - C, 2)
    cap = round(100.0 * K * n, 0)
    if right == "P":
        consequence = (f"Assignment ⇒ you BUY {int(100 * n)} sh at ${K:.0f} (effective ${basis:.2f} after the "
                       f"${C:.2f} credit) — ${cap:,.0f} of cash committed.")
    elif covered:
        basis = round(stock_basis if stock_basis else (K - C), 2)
        gain = "a gain" if K >= basis else "a small loss"
        consequence = (f"Called away ⇒ your {int(100 * n)} shares are SOLD at ${K:.0f}. This is a CAPPED outcome, not an "
                       f"unbounded loss: you keep the ${C:.2f} premium plus {gain} from ${basis:.2f} to ${K:.0f}. Roll the "
                       "call up/out ONLY to keep more upside — the downside is the stock's, not the option's.")
    else:
        consequence = (f"Assignment ⇒ {int(100 * n)} sh CALLED AWAY at ${K:.0f} — NAKED: you're left short {int(100 * n)} "
                       "shares (unbounded upside risk). Cap it (buy a long call) or roll up/out.")
    return {
        "p_itm": p_itm, "extrinsic": extrinsic, "intrinsic": round(intrinsic, 2),
        "early_assignment_risk": early, "early_reason": early_reason,
        "pin_ratio": pin,                           # |spot−strike| / expected move (low + short DTE ⇒ pin)
        "effective_basis": basis, "assignment_capital": cap, "consequence": consequence,
    }


def _cost_of_waiting(*, spot, iv0, D, r, hold_legs) -> list[dict]:
    """The decision half-life: how the recovery PoP / E[P&L] erode as the clock runs if you do
    NOTHING — the held position re-priced with less remaining life at the same spot."""
    out = []
    for td in (5, 10, 20):
        rem = D - max(1, round(td * 7 / 5))          # trading days → calendar days of decay
        if rem < 1:
            continue
        legs = [{**lg, "dte_days": rem} for lg in hold_legs]
        pop, ev = _dist_metrics(spot, iv0, rem, legs, 0.0, None, r)
        out.append({"in_trading_days": td, "dte_left": rem, "recovery_pop": pop, "expected_pnl": ev})
    return out


def repair_alternatives(*, legs: list[dict], spot: float, dte_days: int, r: float = 0.045,
                        roll_days: int = 45, atm_iv: float = 0.30,
                        chains: Optional[dict] = None, ex_div: Optional[dict] = None,
                        near_expiry: Optional[str] = None, far_expiry: Optional[str] = None,
                        base_date: Optional[str] = None, stock: Optional[dict] = None) -> dict:
    """Build the repair menu for the WHOLE position.

    legs: the trade's option legs [{strike, right('P'/'C'), sign(+1/−1), qty, entry}] (entry = the
          per-share credit received / debit paid at open). All at the trade's expiry `dte_days`.
    chains: live {tenor_days: {strike: {'P': {'mid','iv'}, 'C': {...}}}} for the near + far expiries.
    ex_div: optional {'amount': float, 'days': int|None} — the next dividend before expiry, for the
            short-call early-assignment check.
    near_expiry / far_expiry: ISO dates for the near (D) and far (D+roll_days) tenors, so every
            suggested leg carries a real expiration; base_date anchors the fallback (today).
    """
    import datetime as _dt
    D = int(dte_days)
    Dg = D + roll_days
    pr = _Pricer(r, atm_iv, chains or {}, [D, Dg], spot=spot)

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
        "name": "Close the trade", "category": "exit", "group": "benchmark",
        "mechanics": "Unwind every leg at current prices and walk.",
        "rationale": "Realizes the current P&L with certainty — the bar every repair must clear.",
        "risk_note": "Certain outcome — the benchmark.",
        "net_cash": round(_close_cash(hold, now_val), 0),
        "scenarios": [{"move_pct": round(m * 100), "spot": round(spot * (1 + m), 2), "pnl": round(unreal, 0)} for m in _LADDER],
        "max_loss": round(unreal, 0), "max_gain": round(unreal, 0), "breakevens": [],
        "greeks": {"delta": 0, "gamma": 0, "theta": 0, "vega": 0}, "theta_day": 0,
        "defined_risk": True, "upside_risk_free": True, "pop_pct": None, "ev": round(unreal, 0),
        "turns_profitable": bool(unreal > 0), "legs": [],
    })

    lone_short = len(shorts) == 1 and len(longs) == 0

    def _rf_txt(base, cs, width, rf):
        return (f"total credit ${base+cs:.2f} ≥ ${width:.0f} width → that tail is RISK-FREE" if rf
                else f"total credit ${base+cs:.2f} vs ${width:.0f} width → ${max(0.0, width-(base+cs)):.2f}/sh residual risk on that tail")

    def _add_calendar(rt, K_short, K_long, *, keep, name, mechanics, rationale):
        """Build a calendar/diagonal (short near ``K_short`` @D · long far ``K_long`` @Dg), priced at the
        NEAR horizon, and append it ONLY if it clears the honest bar — can turn a profit (keep=True) or
        beats simply closing (keep=False, the re-center). `keep` reuses the held short at its credit;
        otherwise the tested short is closed and a fresh calendar opened."""
        same = keep and abs(K_short - K) < 1e-6
        near_px = C if same else pr.entry(D, K_short, rt, spot)
        far_px = pr.entry(Dg, K_long, rt, spot)
        cal = [L(K_short, rt, -1, D, near_px), L(K_long, rt, 1, Dg, far_px)]
        if keep:
            realized = 0.0
            est = _open_cash([L(K_long, rt, 1, Dg, far_px)])            # near already held → only buy the far
        else:
            realized = _close_realized([tested], now_val)
            est = _close_cash([tested], now_val) + _open_cash(cal)      # close the lost strike, open both legs
        row = _build(name=name, category="calendar", group=("adjust" if keep else "replace"),
                     mechanics=mechanics, legs=cal, realized=realized, stock=None, establish_cash=est,
                     spot=spot, r=r, iv_ref=iv0, rationale=rationale)
        # A KEEP calendar/diagonal is a fix worth SHOWING even when marginal (its numbers speak for it);
        # a re-center (replace) must beat closing flat with a real shot before it earns a slot.
        ok = (row.get("pop_pct") is not None) if keep else (row["max_gain"] > unreal and (row.get("pop_pct") or 0) >= 5)
        if ok:
            alts.append(row)

    def _add_ratio(rc, mode):
        """Directional RECOVERY structures that REPLACE the tested short (close it, redeploy) in the
        recovery direction (rc='C' recovers a tested PUT on a bounce, rc='P' recovers a tested CALL on a
        drop). Replacing — not overlaying — banks the loss, removes the assignment risk, and avoids
        DOUBLING the delta a deep-ITM short already carries. 'zebra' = buy 2 ITM + sell 1 ATM (a
        Zero-Extrinsic Back-Ratio ≈ ±100Δ stock replacement, ~no extrinsic to decay, DEFINED risk);
        'backratio' = sell 1 near + buy 2 farther OTM (cheap convexity, big payoff on a strong move,
        DEFINED risk). Gated to beat simply closing flat."""
        up = (rc == "C")
        if mode == "zebra":
            K_atm = pr.snap(D, spot)
            K_itm = pr.snap(D, spot * (0.85 if up else 1.15))
            if (up and K_itm >= K_atm) or ((not up) and K_itm <= K_atm):
                return
            new = [L(K_itm, rc, 1, D, pr.entry(D, K_itm, rc, spot), qty=2 * n),
                   L(K_atm, rc, -1, D, pr.entry(D, K_atm, rc, spot), qty=n)]
            name = f"Replace with a {'call' if up else 'put'} ZEBRA (2×${K_itm:.0f} / −1×${K_atm:.0f})"
            mech = (f"CLOSE the ${K:.0f} {'put' if right == 'P' else 'call'} and BUY 2× the ${K_itm:.0f} {'call' if up else 'put'} "
                    f"& SELL 1× the ${K_atm:.0f} — a Zero-Extrinsic Back-Ratio ≈ {'+' if up else '−'}100Δ stock replacement "
                    f"(almost no time premium to decay), so a {'bounce' if up else 'pullback'} claws the loss back; risk DEFINED at the debit.")
            rat = (f"Bank the loss + remove assignment risk, then get a clean, capital-efficient ~{'+' if up else '−'}100 delta with a "
                   f"DEFINED max loss (the debit) and no naked tail — the pro's stock-replacement recovery, without doubling a deep short's delta.")
        else:  # backratio (1×2)
            K_s = pr.snap(D, spot * (1.02 if up else 0.98))
            K_l = pr.wing(D, K_s, "above" if up else "below", max(spot * 0.06, 0.7 * pr.sigma(spot, D, iv0)))
            if (up and K_l <= K_s) or ((not up) and K_l >= K_s):
                return
            new = [L(K_s, rc, -1, D, pr.entry(D, K_s, rc, spot), qty=n),
                   L(K_l, rc, 1, D, pr.entry(D, K_l, rc, spot), qty=2 * n)]
            name = f"Replace with a {'call' if up else 'put'} back-ratio (−1×${K_s:.0f} / +2×${K_l:.0f})"
            mech = (f"CLOSE the ${K:.0f} {'put' if right == 'P' else 'call'}; SELL 1× ${K_s:.0f} & BUY 2× ${K_l:.0f} {'calls' if up else 'puts'} "
                    f"— cheap/credit convexity that pays off big on a strong {'rally' if up else 'drop'}; DEFINED risk (buy 2 > sell 1).")
            rat = (f"Bank the loss, then finance {'upside' if up else 'downside'} convexity for near-zero cost — a sharp {'bounce' if up else 'pullback'} "
                   f"recovers with leverage; worst case a KNOWN max loss near ${K_l:.0f}. Advanced when you expect a fast move back.")
        row = _build(name=name, category="ratio", group="replace", mechanics=mech, legs=new,
                     realized=_close_realized([tested], now_val), stock=None,
                     establish_cash=_close_cash([tested], now_val) + _open_cash(new),
                     spot=spot, r=r, iv_ref=iv0, rationale=rat)
        if (row.get("pop_pct") or 0) >= 8 and row["max_gain"] > unreal:   # a replacement must beat closing flat
            alts.append(row)

    def _add_butterfly(rc, mode):
        """Defined-risk recovery TENT kept ON TOP of the tested short (a FIX — a fly barely moves delta,
        so it doesn't over-expose the way a ZEBRA would). rc='C' recovers a tested put on a bounce,
        rc='P' a tested call on a drop. 'tent' = a near-dated fly centered a modest move toward recovery
        (cheap bet the stock lands at the target); 'diagonal' = a calendarised fly (short body near D,
        long wings far Dg) that ALSO harvests near theta with longer-dated protection."""
        up = (rc == "C")
        wg = max(spot * 0.04, 0.6 * pr.sigma(spot, D, iv0))
        Kc = pr.snap(D, spot + (0.5 if up else -0.5) * pr.sigma(spot, D, iv0))     # recovery-target center
        Klo = pr.wing(D, Kc, "below", wg)
        Khi = pr.wing(D, Kc, "above", wg)
        if not (Klo < Kc < Khi):
            return
        if mode == "tent":
            fly = [L(Klo, rc, 1, D, pr.entry(D, Klo, rc, spot)),
                   L(Kc, rc, -1, D, pr.entry(D, Kc, rc, spot), qty=2 * n),
                   L(Khi, rc, 1, D, pr.entry(D, Khi, rc, spot))]
            name = f"Add a {'call' if up else 'put'} butterfly tent (${Klo:.0f}/{Kc:.0f}/{Khi:.0f})"
            mech = (f"Keep the tested short; add a {'call' if up else 'put'} fly +1 ${Klo:.0f} / −2 ${Kc:.0f} / +1 ${Khi:.0f} "
                    f"~{D}d — a cheap DEFINED-risk tent that pays if the stock {'bounces' if up else 'pulls back'} to ~${Kc:.0f}.")
            rat = (f"A low-cost, defined-risk bet on a {'bounce' if up else 'pullback'} to ~${Kc:.0f} — the surgeon's fix: keep the "
                   f"trade, add a targeted recovery tent for a small known cost. Best when you have a specific target in mind.")
        else:  # ASYMMETRIC BROKEN-WING FLY — widen the outer wing on the risky side so it leans to a
               # CREDIT and reshapes the payoff into a defined-risk tent (transform the risk array).
            Klo = pr.wing(D, Kc, "below", (wg if up else 2.2 * wg))
            Khi = pr.wing(D, Kc, "above", (2.2 * wg if up else wg))
            if not (Klo < Kc < Khi):
                return
            fly = [L(Klo, rc, 1, D, pr.entry(D, Klo, rc, spot)),
                   L(Kc, rc, -1, D, pr.entry(D, Kc, rc, spot), qty=2 * n),
                   L(Khi, rc, 1, D, pr.entry(D, Khi, rc, spot))]
            name = f"Asymmetric broken-wing fly (${Klo:.0f}/{Kc:.0f}/${Khi:.0f})"
            mech = (f"Keep the tested short; add a broken-wing {'call' if up else 'put'} fly +1 ${Klo:.0f} / −2 ${Kc:.0f} / "
                    f"+1 ${Khi:.0f} ~{D}d — the {'upper' if up else 'lower'} wing is WIDENED so the fly leans to a CREDIT and "
                    f"reshapes the P&L into a defined-risk tent centered ~${Kc:.0f}.")
            rat = ("Transform the risk array — the broken (wider) wing tilts the fly to a credit and reshapes the payoff into a "
                   "defined-risk profit tent for little/no cost. The quant's 'reshape the array, don't just roll' move.")
        row = _build(name=name, category="butterfly", group="adjust", mechanics=mech, legs=[dict(tested)] + fly,
                     realized=0.0, stock=None, establish_cash=_open_cash(fly), spot=spot, r=r, iv_ref=iv0, rationale=rat)
        if row["max_gain"] > 0 and row.get("pop_pct") is not None:
            alts.append(row)

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

        # 1b) ROLL DOWN & OUT TO A PUT SPREAD — the DEFINED-RISK roll: drop the strike AND cap the tail.
        rs = _credit_spread(pr, Dg, K2, "below", C, spot)
        if rs:
            _, Kp_far, ps_short, ps_long, wsp, _csp, _rf = rs
            sp_legs = [L(K2, "P", -1, Dg, ps_short), L(Kp_far, "P", 1, Dg, ps_long)]
            alts.append(_build(
                name=f"Roll down to a put spread (${K2:.0f}/{Kp_far:.0f}, +{roll_days}d)", category="defined_risk",
                mechanics=f"Buy back the ${K:.0f} put; sell the ${K2:.0f} put & BUY the ${Kp_far:.0f} put ~{Dg}d — a DEFINED-risk credit spread {abs(round((K2-K)/K*100))}% lower; the long wing caps the tail at the ${wsp:.0f} width.",
                legs=sp_legs, realized=_close_realized([tested], now_val), stock=None,
                establish_cash=_close_cash([tested], now_val) + _open_cash(sp_legs),
                spot=spot, r=r, iv_ref=iv0,
                rationale="Same downside relief as the naked roll, but the long wing turns the open put tail into a KNOWN max loss — roll AND cap in one move."))

        # 1b2) ROLL UP + PROTECTION — raise the short TOWARD spot for a fatter credit and BUY a long put
        #      below to CAP the (now larger) tail: a defined-risk, higher-income rangebound bet.
        Kup = pr.snap(D, spot - 0.30 * pr.sigma(spot, D, iv0))                 # closer short = real premium
        Kup_far = pr.wing(D, Kup, "below", max(spot * 0.05, 1.0 * pr.sigma(spot, D, iv0)))   # ~1σ protection wing
        if Kup > K and Kup_far < Kup:                         # only if it genuinely rolls UP (closer to spot)
            up_legs = [L(Kup, "P", -1, D, pr.entry(D, Kup, "P", spot)), L(Kup_far, "P", 1, D, pr.entry(D, Kup_far, "P", spot))]
            _rowup = _build(
                name=f"Roll up + protection (${Kup:.0f}/{Kup_far:.0f} spread)", category="defined_risk",
                mechanics=f"Buy back the ${K:.0f} put; SELL the ${Kup:.0f} put (closer to spot = MORE premium) & BUY the ${Kup_far:.0f} put as protection ~{D}d — a DEFINED-risk credit spread that pulls in extra income and caps the tail at the ${abs(Kup-Kup_far):.0f} width.",
                legs=up_legs, realized=_close_realized([tested], now_val), stock=None,
                establish_cash=_close_cash([tested], now_val) + _open_cash(up_legs),
                spot=spot, r=r, iv_ref=iv0,
                rationale=f"Get PAID more AND cap the risk — raise the short to ${Kup:.0f} for a fatter credit, buy the ${Kup_far:.0f} wing so max loss is KNOWN. A rangebound income bet: profits if the stock holds over ${Kup:.0f}; the closer short raises breach odds, but the wing bounds it. The aggressive 'roll up + protection' play.")
            if _rowup["max_gain"] > 0:
                alts.append(_rowup)

        # 1c) WIDEN TO A STRANGLE — sell an untested-side call to pull in fresh credit that offsets the
        #     put loss. Adds an UPSIDE tail (undefined), so it's a rangebound-view play only.
        Ksc = pr.snap(D, spot + 0.7 * pr.sigma(spot, D, iv0))
        csc = pr.entry(D, Ksc, "C", spot)
        if csc >= 0.15 and Ksc > spot:
            alts.append(_build(
                name=f"Widen to a strangle (sell the ${Ksc:.0f} call)", category="overlay",
                mechanics=f"Keep the ${K:.0f} put and SELL the ${Ksc:.0f} call (~${csc:.2f}) — pulls in ${csc*_MULT*n:.0f} of new credit that lowers your net breakeven and recovers part of the loss if the stock stays rangebound.",
                legs=[L(K, "P", -1, D, C), L(Ksc, "C", -1, D, csc)],
                realized=0.0, stock=None, establish_cash=csc * _MULT * n, spot=spot, r=r, iv_ref=iv0,
                rationale="Premium on the untested side to offset the loss — but the naked call adds an UPSIDE tail; only if you expect the stock to stall, not rip. Cap it with a call wing (that's the jade) if the upside worries you."))

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
                name=f"Re-center into a broken-wing fly (${Kl:.0f}/{Kb:.0f}/{Kh:.0f})", category="butterfly", group="replace",
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

        # ADVANCED — TERM-STRUCTURE DEFENSES (calendars / diagonals), each gated so only a calendar
        # worth doing is shown. (a) same-strike calendar keeps the short + long far put (best on a
        # FRESH breach, K≈spot); (b) diagonal buys a cheaper LOWER far put; (c) re-center closes the
        # lost strike and opens an at-the-money calendar (the play when the stock has run far away).
        far_kp = pr.entry(Dg, K, "P", spot)
        _add_calendar("P", K, K, keep=True,
            name=f"Calendarised gamma hedge (buy the ${K:.0f} put ~{Dg}d)",
            mechanics=f"Keep the near ${K:.0f} put; BUY the ${K:.0f} put ~{Dg}d (~${far_kp:.2f}) — the long far put's gamma OFFSETS the near short's gamma at the strike, FLATTENING the curve so a breach whipsaws you far less, DEFINES the downside, and harvests the rich near vol; roll the near short down each cycle.",
            rationale=f"Calendarised gamma hedge — flattens the short's gamma at ${K:.0f}, defines the downside, sells richer near vol vs cheaper far. Profits most if the stock stabilizes near ${K:.0f}; best on a fresh breach.")
        Kdl = pr.wing(Dg, K, "below", max(spot * 0.05, 0.6 * pr.sigma(spot, Dg, iv0)))
        if Kdl < K:
            _add_calendar("P", K, Kdl, keep=True,
                name=f"Convert to a put diagonal (long far ${Kdl:.0f} put)",
                mechanics=f"Keep the near ${K:.0f} put; BUY the ${Kdl:.0f} put ~{Dg}d (~${pr.entry(Dg, Kdl, 'P', spot):.2f}) — a cheaper far floor than the same-strike calendar; harvests near theta and caps the DEEP tail below ${Kdl:.0f}.",
                rationale="A cheaper calendar: the lower long far put costs less and leaves more room to recover on a bounce, while still bounding a crash.")
        Kspot = pr.snap(D, spot)
        _add_calendar("P", Kspot, Kspot, keep=False,
            name=f"Re-center into a put calendar (${Kspot:.0f}, at spot)",
            mechanics=f"CLOSE the ${K:.0f} put (bank the loss) and open a fresh ${Kspot:.0f} put calendar at the money (short ~{D}d / long ~{Dg}d) — stop defending the strike the stock left behind.",
            rationale=f"The re-center calendar: bank the loss on the abandoned strike and redeploy into an at-the-money calendar that makes money on stabilization near ${Kspot:.0f} + term-structure decay. The desk's 'trade where the stock IS', as a vol structure — the advanced move for a deep loss.")

        # BUTTERFLY recovery tents (FIX — keep the short; a fly barely moves delta) + RATIO / BACK-RATIO
        # REPLACEMENTS (tested put recovers on an UP move → CALL structures).
        _add_butterfly("C", "tent")
        _add_butterfly("C", "diagonal")
        _add_ratio("C", "zebra")
        _add_ratio("C", "backratio")

    elif lone_short and right == "C":  # ── naked/covered short call: mirror ───────────────────────
        K2 = pr.wing(Dg, K, "above", max(spot * 0.05, 0.5 * pr.sigma(spot, Dg, iv0)))
        far = pr.entry(Dg, K2, "C", spot)
        alts.append(_build(
            name=f"Roll up & out (→ ${K2:.0f}, +{roll_days}d)", category="roll",
            mechanics=f"Buy back ${K:.0f} (~${now_val(tested):.2f}), sell ${K2:.0f} ~{Dg}d (~${far:.2f}) — raises the strike, buys time.",
            legs=[L(K2, "C", -1, Dg, far)], realized=_close_realized([tested], now_val), stock=None,
            establish_cash=_close_cash([tested], now_val) + _open_cash([L(K2, "C", -1, Dg, far)]),
            spot=spot, r=r, iv_ref=iv0, rationale="Relieves the tested call by raising the strike; caps recovery at the new credit."))

        # 1b) ROLL UP & OUT TO A CALL SPREAD — the DEFINED-RISK roll: raise the strike AND cap the tail.
        rc = _credit_spread(pr, Dg, K2, "above", C, spot)
        if rc:
            _, Kc_far, cs_short, cs_long, wcs, _cc, _rf = rc
            cs_legs = [L(K2, "C", -1, Dg, cs_short), L(Kc_far, "C", 1, Dg, cs_long)]
            alts.append(_build(
                name=f"Roll up to a call spread (${K2:.0f}/{Kc_far:.0f}, +{roll_days}d)", category="defined_risk",
                mechanics=f"Buy back the ${K:.0f} call; sell the ${K2:.0f} call & BUY the ${Kc_far:.0f} call ~{Dg}d — a DEFINED-risk credit spread higher; the long wing caps the unbounded upside at the ${wcs:.0f} width.",
                legs=cs_legs, realized=_close_realized([tested], now_val), stock=None,
                establish_cash=_close_cash([tested], now_val) + _open_cash(cs_legs),
                spot=spot, r=r, iv_ref=iv0,
                rationale="The roll every naked-call defense should prefer: raises the strike AND turns the unbounded upside tail into a known max loss."))

        # 1b2) ROLL DOWN + PROTECTION — lower the short TOWARD spot for a fatter credit and BUY a long
        #      call above to CAP the (now larger) tail: a defined-risk, higher-income rangebound bet.
        Kdn = pr.snap(D, spot + 0.30 * pr.sigma(spot, D, iv0))                 # closer short = real premium
        Kdn_far = pr.wing(D, Kdn, "above", max(spot * 0.05, 1.0 * pr.sigma(spot, D, iv0)))   # ~1σ protection wing
        if Kdn < K and Kdn_far > Kdn:                          # only if it genuinely rolls DOWN (closer to spot)
            dn_legs = [L(Kdn, "C", -1, D, pr.entry(D, Kdn, "C", spot)), L(Kdn_far, "C", 1, D, pr.entry(D, Kdn_far, "C", spot))]
            _rowdn = _build(
                name=f"Roll down + protection (${Kdn:.0f}/{Kdn_far:.0f} spread)", category="defined_risk",
                mechanics=f"Buy back the ${K:.0f} call; SELL the ${Kdn:.0f} call (closer to spot = MORE premium) & BUY the ${Kdn_far:.0f} call as protection ~{D}d — a DEFINED-risk credit spread that pulls in extra income and caps the tail at the ${abs(Kdn_far-Kdn):.0f} width.",
                legs=dn_legs, realized=_close_realized([tested], now_val), stock=None,
                establish_cash=_close_cash([tested], now_val) + _open_cash(dn_legs),
                spot=spot, r=r, iv_ref=iv0,
                rationale=f"Get PAID more AND cap the risk — lower the short to ${Kdn:.0f} for a fatter credit, buy the ${Kdn_far:.0f} wing so max loss is KNOWN. A rangebound income bet: profits if the stock holds under ${Kdn:.0f}; the closer short raises breach odds, but the wing bounds it. The aggressive 'roll down + protection' play.")
            if _rowdn["max_gain"] > 0:                         # only when it can actually profit (else it's a bad roll)
                alts.append(_rowdn)

        # 1c) WIDEN TO A STRANGLE — sell an untested-side put to pull in fresh credit that offsets the
        #     call loss. Rangebound-view play; the short put adds a (bounded) downside exposure.
        Ksp = pr.snap(D, spot - 0.7 * pr.sigma(spot, D, iv0))
        csp3 = pr.entry(D, Ksp, "P", spot)
        if csp3 >= 0.15 and 0 < Ksp < spot and not stock:   # NEVER bolt a naked put onto a COVERED call
            alts.append(_build(
                name=f"Widen to a strangle (sell the ${Ksp:.0f} put)", category="overlay",
                mechanics=f"Keep the ${K:.0f} call and SELL the ${Ksp:.0f} put (~${csp3:.2f}) — pulls in ${csp3*_MULT*n:.0f} of new credit that lowers your net breakeven and recovers part of the loss if the stock stays rangebound.",
                legs=[L(K, "C", -1, D, C), L(Ksp, "P", -1, D, csp3)],
                realized=0.0, stock=None, establish_cash=csp3 * _MULT * n, spot=spot, r=r, iv_ref=iv0,
                rationale="Premium on the untested (put) side to offset the loss — the short put adds a downside exposure (bounded at the strike). Only if you expect the stock to stall, not keep running."))
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

        # ADVANCED — TERM-STRUCTURE DEFENSES (calendars), gated. (a) same-strike calendar caps the
        # naked call's UNBOUNDED upside with a long far call + harvests near theta (best on a fresh
        # breach); (b) re-center closes the run-over strike and opens an at-the-money call calendar.
        far_kc = pr.entry(Dg, K, "C", spot)
        _add_calendar("C", K, K, keep=True,
            name=f"Calendarised gamma hedge (buy the ${K:.0f} call ~{Dg}d)",
            mechanics=f"Keep the near ${K:.0f} call; BUY the ${K:.0f} call ~{Dg}d (~${far_kc:.2f}) — the long far call's gamma OFFSETS the near short's gamma at the strike, FLATTENING the curve so a breach hurts far less, and turns the unbounded upside into DEFINED risk while harvesting the rich near vol; roll the near short up each cycle.",
            rationale=f"Calendarised gamma hedge — long far call flattens the short's gamma at ${K:.0f} (a breach no longer whipsaws you), defines the upside, and you own cheaper far vol vs richer near. The 'cap the tail AND keep selling' defense; best if the stock stalls near ${K:.0f}.")
        Kspot = pr.snap(D, spot)
        _add_calendar("C", Kspot, Kspot, keep=False,
            name=f"Re-center into a call calendar (${Kspot:.0f}, at spot)",
            mechanics=f"CLOSE the ${K:.0f} call (bank the loss) and open a fresh ${Kspot:.0f} call calendar at the money (short ~{D}d / long ~{Dg}d) — stop defending the strike the stock ran through.",
            rationale=f"The re-center calendar: bank the loss on the run-over strike and redeploy into an at-the-money calendar that profits on stabilization near ${Kspot:.0f} + term-structure decay. The advanced move for a call the stock has left far below.")

        # BUTTERFLY recovery tents (FIX — keep the short) + RATIO / BACK-RATIO REPLACEMENTS
        # (tested call recovers on a DOWN move → PUT structures).
        _add_butterfly("P", "tent")
        _add_butterfly("P", "diagonal")
        _add_ratio("P", "zebra")
        _add_ratio("P", "backratio")
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

    # ── HOLD-AS-IS baseline — the "do nothing" row every repair is judged against.
    hold_row = _build(
        name="Hold as-is (do nothing)", category="hold", group="benchmark",
        mechanics="Keep every leg unchanged and carry the position to expiry.",
        legs=[dict(lg) for lg in hold], realized=0.0, stock=stock, establish_cash=0.0,
        spot=spot, r=r, iv_ref=iv0,
        rationale="The no-action baseline — the recovery odds and expected P&L if you simply sit. Every repair is measured against this.")
    hold_pop, hold_ev, hold_ml = hold_row.get("pop_pct"), hold_row.get("ev"), hold_row.get("max_loss")

    # Δ-vs-hold on every actionable alt: does the repair lift the recovery odds / cap the loss, and
    # at what change in expected P&L? (PoP is to each structure's own horizon.)
    for a in alts:
        if a["category"] in ("exit", "hold"):
            continue
        a["d_pop"] = round(a["pop_pct"] - hold_pop) if (a.get("pop_pct") is not None and hold_pop is not None) else None
        a["d_max_loss"] = round(a["max_loss"] - hold_ml) if (a.get("max_loss") is not None and hold_ml is not None) else None
        a["d_ev"] = round(a["ev"] - hold_ev) if (a.get("ev") is not None and hold_ev is not None) else None
    alts.append(hold_row)                            # pinned alongside Close as the two benchmarks

    # Stamp every suggested leg with a REAL expiration date so the user knows exactly what to trade.
    def _expiry_for(dd):
        if dd is None:
            return None
        if near_expiry and far_expiry:                # snap to the near (D) or far (Dg) real expiry
            return near_expiry if abs(dd - D) <= abs(dd - Dg) else far_expiry
        try:
            base = _dt.date.fromisoformat(base_date) if base_date else _dt.date.today()
            return (base + _dt.timedelta(days=int(dd))).isoformat()
        except (ValueError, TypeError):
            return None
    for a in alts:
        for lg in a.get("legs", []):
            if lg.get("right") in ("P", "C"):
                lg["expiry"] = _expiry_for(lg.get("dte_days"))

    _covered = bool(stock and (stock.get("shares") or 0) >= 100 * n and right == "C")
    return {
        "tested": bool(cushion_pct < 5.0), "cushion_pct": round(cushion_pct, 1),
        "short_right": right, "short_strike": K, "spot": round(spot, 2),
        "dte_days": D, "contracts": n, "unrealized_pnl": round(unreal, 0),
        "structure": ("covered_call" if _covered else _classify(hold)), "covered": _covered,
        "pricing": ("live chain mid + skew IV (horizon marks BS-calibrated)" if chains else "model (Black-Scholes) — indicative"),
        # Lens 1/2 + the decision clock — all off the one chain fetch, no extra I/O.
        "recoverability": _recoverability(spot=spot, K=K, right=right, C=C, iv0=iv0, D=D, r=r,
                                          hold_legs=hold, hold_pop=hold_pop, stock=stock),
        "assignment": _assignment(now_val=now_val, spot=spot, K=K, right=right, C=C, iv0=iv0, D=D, r=r,
                                  n=n, ex_div=ex_div, covered=_covered, stock_basis=(stock or {}).get("basis")),
        "cost_of_waiting": _cost_of_waiting(spot=spot, iv0=iv0, D=D, r=r, hold_legs=hold),
        "hold": {"pop_pct": hold_pop, "expected_pnl": hold_ev, "max_loss": hold_ml,
                 "greeks": hold_row.get("greeks"), "breakevens": hold_row.get("breakevens")},
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
