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
repaired NET GREEKS, max loss / max gain, breakevens (exact — bisected on the true payoff), lognormal
PoP, defined-risk & upside-risk-free flags, the exact legs, and the rationale.

ROLLS. The defense principle is NO net new money: a roll must be a CREDIT, so debit rolls are dropped
from a lone short's menu. For a lone short that isn't healthy, the live roll search
(`roll_optimizer_service`) is the source of rolls — `build_roll_alternatives` turns its candidates into
full alternatives through the same `_build`, and `drop_plain_rolls` removes the fixed-horizon
(+roll_days) fallback rolls this module builds (tagged `plain_roll`). `rank_defenses` then scores EVERY
alternative — rolls, caps, covers, Hold and Close — on one composite, so there is a single recommendation
rather than a desk pick and a separate roll optimizer that can disagree.
"""
from __future__ import annotations

import datetime as dt
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
        """The strike's vol. For an IN-THE-MONEY strike, the OUT-OF-THE-MONEY side's IV at the same strike: an ITM option is
        mostly intrinsic, so its own quoted IV is noise (AAOI's 80 put at $68 read 61% — a few cents of time value against a
        dollar-wide market and a lagging spot — beside 103% on the 80 call), and that one number drove the hold row's whole
        distribution (E[P&L] −$788 against a −$691 mark). By put-call parity both sides carry the same vol."""
        row = self._chain(tenor).get(strike) or {}
        if self.spot and self.spot > 0 and ((strike > self.spot) if right == "P" else (strike < self.spot)):
            alt = (row.get("C" if right == "P" else "P") or {}).get("iv")
            if alt and alt > 0:
                return float(alt)
        q = row.get(right)
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


def _fwd_iv(iv_far: float, iv_near: float, far_days: float, near_days: float) -> float:
    """FORWARD implied vol for the stretch between the near expiry and a longer leg's expiry:
    σ_fwd² = (σ_far²·T_far − σ_near²·T_near) / (T_far − T_near).

    A far leg's IV is a WHOLE-LIFE number. The engine diffuses the stock at the near IV up to the horizon, so
    what is left of the far leg by then must be valued at the vol implied for the REMAINING life — otherwise
    the shape of the term structure is booked as profit: with a rich front (66%) over a cheaper back (50%) the
    far call was marked at 50% for a stretch the market prices at ~37%, manufacturing a large phantom edge in
    every calendar/diagonal (and the reverse in contango). Floored/capped so an extreme surface can't imply an
    absurd (or negative) forward variance."""
    tf, tn = far_days / 365.0, near_days / 365.0
    if tf <= tn or iv_far <= 0 or iv_near <= 0:
        return iv_far
    var = (iv_far ** 2 * tf - iv_near ** 2 * tn) / (tf - tn)
    return math.sqrt(min(max(var, (0.5 * iv_far) ** 2), (2.0 * iv_far) ** 2))


def _leg_val(P: float, lg: dict, horizon_days: float, r: float) -> float:
    """Value at horizon spot P: intrinsic if expired by the horizon, else BS on the REMAINING life at the
    vol implied for that stretch (`iv_fwd` when the leg was built beyond the near expiry, else its own
    skew-calibrated IV) — this is what makes calendars/diagonals correct."""
    rem = lg["dte_days"] - horizon_days
    if rem <= 0.5:
        return _intr(P, lg["strike"], lg["right"])
    return bs_price(P, lg["strike"], max(rem, 0.5) / 365.0, r, max(lg.get("iv_fwd") or lg["iv"], 0.02),
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
    """EXACT zero-crossings of the horizon P&L curve. Scan the display grid PLUS every strike (the
    payoff's kinks), then bisect each sign change on the true `_pnl_at`. The old grid-only scan with
    linear interpolation across a kink was off by real dollars — a $700 call sold for $0.90 read as a
    $697.68 breakeven instead of $700.90 — and a breakeven is the one number a trader anchors on."""
    def f(x: float) -> float:
        return _pnl_at(x, legs, realized, stock, r, horizon)

    xs = sorted({spot * (1 + m) for m in _WIDE} | {float(lg["strike"]) for lg in legs})
    xs = [x for x in xs if x > 0]
    vals = [f(x) for x in xs]
    out: list[float] = []
    for i in range(len(xs) - 1):
        lo, hi, flo, fhi = xs[i], xs[i + 1], vals[i], vals[i + 1]
        if not ((flo < 0 <= fhi) or (flo > 0 >= fhi)):
            continue
        for _ in range(50):                                   # bisect the bracket on the TRUE curve
            mid = 0.5 * (lo + hi)
            fm = f(mid)
            if fm == 0.0:
                lo = hi = mid
                break
            if (fm < 0) == (flo < 0):
                lo, flo = mid, fm
            else:
                hi = mid
        out.append(float(round(0.5 * (lo + hi), 2)))
    return out


def _norm_cdf(x: float) -> float:
    """Standard-normal CDF via erf — for the closed-form risk-neutral P(finish ITM)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _dist_metrics(spot, iv, horizon_days, legs, realized, stock, r, collect: Optional[list] = None) -> tuple[Optional[int], Optional[float]]:
    """Lognormal-weighted PoP (%) AND E[P&L] ($) to the horizon — the recovery-odds engine.
    Returns (pop_pct, expected_pnl). The expected P&L is what a 'do-nothing / hold' read needs:
    not just 'can it come back' but 'what is this worth on average from here'. `collect` (a list) receives every
    (pnl, weight) sample, so a caller can read other probabilities off the SAME integration (see `set_beat_close`)."""
    if horizon_days <= 0 or iv <= 0:
        return None, None
    T = horizon_days / 365.0
    sig = iv * math.sqrt(T)
    if sig <= 0:
        return None, None
    # Integrate in LOG space over ±5σ of the horizon distribution (midpoint rule, 0.05σ steps, standard-normal
    # weights — the constant factors cancel in the ratios below; the ±5σ tail carries ~6e-7 of the mass). The old
    # fixed window (0.4×–1.8× spot, 160 points) did not scale with vol·√T: at 58% vol over 56 days it cut off the
    # right tail, understating what a short call can lose and handing every far-dated / high-vol short a spurious
    # +$30…$80 of "edge".
    n, k = 200, 5.0
    dz = 2.0 * k / n
    mu = (r - 0.5 * iv * iv) * T                     # E[ln(S_T / S_0)]
    num = den = ev = 0.0
    for i in range(n):
        z = -k + (i + 0.5) * dz
        P = spot * math.exp(mu + sig * z)
        w = math.exp(-0.5 * z * z)
        pnl = _pnl_at(P, legs, realized, stock, r, horizon_days)
        if collect is not None:
            collect.append((pnl, w))
        den += w
        ev += pnl * w
        if pnl >= 0:
            num += w
    if den <= 0:
        return None, None
    return round(num / den * 100), round(ev / den, 0)


def _pop(spot, iv, horizon_days, legs, realized, stock, r) -> Optional[int]:
    return _dist_metrics(spot, iv, horizon_days, legs, realized, stock, r)[0]


def _build(*, name, category, mechanics, legs, realized, stock, establish_cash, spot, r, rationale, iv_ref,
           group="adjust", horizon_days: Optional[int] = None) -> dict:
    horizon = (int(horizon_days) if horizon_days is not None
               else (min((lg["dte_days"] for lg in legs), default=0) if legs else 0))
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
    # The stock must diffuse at the market-implied vol for THIS structure's horizon. The legs that settle at the
    # horizon carry exactly that (their own chain IVs — the SHORT ones first, as they define the exposure); `iv_ref`
    # (the tested leg's NEAR IV) is only the fallback. Feeding the near IV to a far-dated structure priced its legs
    # at the back-month IV but diffused the stock at the front IV for the whole horizon — booking the term-structure
    # gap as free edge on every far-dated short (a +$375 "edge" on a fair roll in contango).
    _sh = [lg["iv"] for lg in legs if lg["dte_days"] == horizon and lg["sign"] < 0 and lg.get("iv")]
    _al = [lg["iv"] for lg in legs if lg["dte_days"] == horizon and lg.get("iv")]
    iv_h = (sum(_sh) / len(_sh)) if _sh else ((sum(_al) / len(_al)) if _al else iv_ref)
    samples: list = []
    pop, ev = _dist_metrics(spot, iv_h, horizon, legs, realized, stock, r, collect=samples)
    # E[P&L] is NET of the risk-free return the committed cash would otherwise have earned. The payoff model lets
    # bought stock drift at r but never charged for the cash that buys it, so every stock structure carried a phantom
    # +r·T·outlay (≈ +$83 on a $31k collar over three weeks — enough to flatter it far above simply closing).
    if ev is not None and establish_cash < 0 and horizon > 0:
        ev = round(ev - (-establish_cash) * (math.exp(r * horizon / 365.0) - 1.0), 0)
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
        "_samples": samples,                     # popped by `set_beat_close` once the benchmark is known — never serialized
        "turns_profitable": bool(max_gain > 0),
        # `price` = the leg's per-share entry price (for a NEW leg: today's chain mid — what you'd actually trade at).
        "legs": [{"action": "SELL" if lg["sign"] < 0 else "BUY", "right": lg["right"],
                  "strike": round(lg["strike"], 2), "qty": lg["qty"], "dte_days": lg["dte_days"],
                  "price": round(float(lg["entry"]), 2)} for lg in legs]
                + ([{"action": "BUY" if stock_shares > 0 else "SELL", "right": "STK",
                     "strike": round(stock["basis"], 2), "qty": abs(int(stock_shares)), "dte_days": None,
                     "price": round(stock["basis"], 2)}] if stock else []),
    }


def set_beat_close(alts: list[dict], benchmark: Optional[float]) -> None:
    """`p_beat_close` on every alternative = the odds its ALL-IN P&L at its own horizon ends BETTER than closing everything
    today (`benchmark` = the close P&L). On a trapped trade that is the question being asked — "does this leave me better off
    than taking the loss now?" — and unlike P(profit) it doesn't read 0% for a roll that can only claw back part of the loss.
    Read off the same lognormal samples `_build` integrated; the private samples are dropped either way."""
    for a in alts:
        smp = a.pop("_samples", None)
        if benchmark is None or not smp or a.get("category") == "exit":
            continue
        den = sum(w for _, w in smp)
        if den > 0:
            a["p_beat_close"] = round(100.0 * sum(w for p, w in smp if p > float(benchmark) + 0.5) / den)


def attach_steps(alt: dict, held: dict, spot: float) -> None:
    """Spell out, for ONE alternative, exactly WHAT TO DO and what it all adds up to — INCLUDING the position you
    already hold. The card used to list only the NEW legs ("−1 P$75 Dec18 +1 P$65 Dec18"), so you could not tell what
    to close, nor what the move did to the trade you are already in.

    Diffs the alternative's legs against `held` (the position as it stands today):
      close  — held legs the alternative drops, at TODAY's mark   (BUY to close a short, SELL to close a long)
      open   — legs the alternative adds, at today's chain mid
      keep   — held legs it leaves untouched
    and the ALL-IN picture (`all_in`): what the original position collected, what closing locks in, the net cash
    today, and the best / worst case and break-evens of the WHOLE thing (old trade + this move) — the numbers the
    card's Max gain / Max loss already are, now named and bridged.

    `held` = {"legs": [{right, strike, sign, qty, dte_days, expiry, entry, mark}], "stock": …, "collected": $}.
    Sets `alt["steps"]` and `alt["all_in"]` in place. Pure — no pricing."""
    cat = alt.get("category")
    alt_opts = [l for l in (alt.get("legs") or []) if l.get("right") in ("P", "C")]

    def key(right, strike, sign, dte):
        return (right, round(float(strike), 4), int(sign), int(dte) if dte is not None else -1)

    avail: dict = {}
    meta: dict = {}
    for lg in held.get("legs") or []:
        k = key(lg["right"], lg["strike"], lg["sign"], lg.get("dte_days"))
        avail[k] = avail.get(k, 0) + int(lg["qty"])
        meta[k] = lg

    keep, opens = [], []
    for l in alt_opts:
        sign = -1 if l.get("action") == "SELL" else 1
        k = key(l["right"], l["strike"], sign, l.get("dte_days"))
        q = int(l["qty"])
        have = avail.get(k, 0)
        kq = min(have, q)
        if kq:
            keep.append({"action": l["action"], "right": l["right"], "strike": l["strike"], "qty": kq,
                         "expiry": (meta[k].get("expiry") or l.get("expiry")), "price": round(float(meta[k]["entry"]), 2)})
            avail[k] = have - kq
        if q - kq:
            opens.append({"action": l["action"], "right": l["right"], "strike": l["strike"], "qty": q - kq,
                          "expiry": l.get("expiry"), "price": round(float(l.get("price") or 0.0), 2)})

    closes, closed_pnl = [], 0.0
    for k, left in avail.items():
        if left <= 0:
            continue
        lg = meta[k]
        mark = float(lg.get("mark") or 0.0)
        closes.append({"action": "BUY" if lg["sign"] < 0 else "SELL", "right": lg["right"], "strike": lg["strike"],
                       "qty": left, "expiry": lg.get("expiry"), "price": round(mark, 2)})
        closed_pnl += lg["sign"] * left * _MULT * (mark - float(lg["entry"]))      # short: (entry − mark); long: (mark − entry)

    def cash(step):                                         # SELL brings cash in, BUY takes it out
        return (1 if step["action"] == "SELL" else -1) * step["price"] * step["qty"] * _MULT

    for s in closes + opens:
        s["cash"] = round(cash(s), 0)

    # shares: only when the alternative itself carries a stock leg (an option-only alternative leaves them untouched)
    stk = next((l for l in (alt.get("legs") or []) if l.get("right") == "STK"), None)
    stock_step, stock_cash = None, None
    if stk:
        alt_sh = int(stk["qty"]) * (1 if stk["action"] == "BUY" else -1)
        held_sh = int((held.get("stock") or {}).get("shares") or 0)
        delta = alt_sh - held_sh
        if delta:
            stock_step = {"action": "BUY" if delta > 0 else "SELL", "right": "STK", "qty": abs(delta), "price": round(spot, 2),
                          "strike": None, "expiry": None}
            stock_cash = -delta * spot

    opt_cash = sum(s["cash"] for s in closes + opens)
    net = float(alt.get("net_cash") or 0.0)
    # Prices are shown to the cent, so each contract-leg can drift ≤ $0.50 from the unrounded engine cash — scale the
    # reconciliation tolerance with the number of contracts instead of widening it for everything.
    tol = 1.5 + 0.5 * sum(int(s["qty"]) for s in closes + opens)
    if stock_step is not None:
        if abs(opt_cash + stock_cash - net) <= tol:
            stock_step["cash"] = round(stock_cash, 0)                       # shares paid for in cash
        else:
            stock_step["cash"] = None                                       # futures / margin hedge — no cash outlay modeled
    reconciled = abs(opt_cash + (stock_step["cash"] if (stock_step and stock_step.get("cash") is not None) else 0.0) - net) <= tol

    notes = []
    if cat == "assignment":
        # the wheel is "let the put be assigned", not "buy it back": the engine's cash is just the new call's credit
        closes = []
        opens = [o for o in opens if o["right"] == "C"]
        if stk:
            notes.append(f"Let the short put be ASSIGNED — you buy {int(stk['qty'])} sh at ${float(stk['strike']):.2f} effective, then sell the call.")
        stock_step = None
        reconciled = True
        closed_pnl = 0.0

    scn0 = next((s["pnl"] for s in (alt.get("scenarios") or []) if s.get("move_pct") == 0), None)
    alt["steps"] = {"close": closes, "open": opens, "keep": keep, "stock": stock_step, "notes": notes,
                    "close_cash": round(sum(s["cash"] for s in closes), 0), "open_cash": round(sum(s["cash"] for s in opens), 0),
                    "net_cash": round(net, 0), "reconciled": bool(reconciled)}
    alt["all_in"] = {"collected_at_open": round(float(held.get("collected") or 0.0), 0), "locked_in": round(closed_pnl, 0),
                     "net_cash_today": round(net, 0), "best_case": alt.get("max_gain"), "worst_case": alt.get("max_loss"),
                     "at_spot": scn0, "breakevens": alt.get("breakevens") or []}


def _vs_hold(a: dict, hold_pop, hold_ev, hold_ml) -> None:
    """Δ-vs-hold on an alternative (recovery odds · max loss · E[P&L]) — ONE helper so every alternative,
    including the roll-search candidates built later, is measured against the baseline identically."""
    a["d_pop"] = round(a["pop_pct"] - hold_pop) if (a.get("pop_pct") is not None and hold_pop is not None) else None
    a["d_max_loss"] = round(a["max_loss"] - hold_ml) if (a.get("max_loss") is not None and hold_ml is not None) else None
    a["d_ev"] = round(a["ev"] - hold_ev) if (a.get("ev") is not None and hold_ev is not None) else None


def identify_wing(hold: list[dict], spot: float) -> Optional[dict]:
    """The TESTED WING of a short-premium position, when it is a clean vertical: the tested short (the short furthest
    into / nearest through the money) plus exactly ONE same-right long of equal size on the protective side (a put
    credit spread, a call credit spread, or one side of an iron condor). That is the unit a trader ROLLS — close the
    old spread, open a new one at a later expiry — so the live roll search can price it as one package. Anything else
    (a lone short, a strangle, a debit/ratio structure) returns None.

    `hold` = option legs [{right, sign, strike, qty, entry, …}]. `kept` = the legs OUTSIDE the wing (the other side
    of a condor), which a wing roll leaves untouched."""
    shorts = [lg for lg in hold if lg["sign"] < 0]
    if not shorts:
        return None
    tested = max(shorts, key=lambda lg: (lg["strike"] - spot) if lg["right"] == "P" else (spot - lg["strike"]))
    longs = [lg for lg in hold if lg["sign"] > 0 and lg["right"] == tested["right"]]
    if len(longs) != 1:
        return None
    lng = longs[0]
    if int(lng["qty"]) != int(tested["qty"]):
        return None
    protective = lng["strike"] < tested["strike"] if tested["right"] == "P" else lng["strike"] > tested["strike"]
    if not protective:
        return None
    return {"right": tested["right"], "qty": int(tested["qty"]),
            "short_strike": float(tested["strike"]), "long_strike": float(lng["strike"]),
            "width": abs(float(tested["strike"]) - float(lng["strike"])),
            "short_entry": float(tested["entry"]), "long_entry": float(lng["entry"]),
            "net_entry": float(tested["entry"]) - float(lng["entry"]),
            "kept": [dict(lg) for lg in hold if lg is not tested and lg is not lng]}


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


def _assignment(*, now_val, spot, K, right, C, iv0, D, r, n, ex_div=None, covered=False, stock_basis=None,
                wing: Optional[dict] = None) -> dict:
    """Lens 2 — ASSIGNMENT (the real risk on a trapped short). Risk-neutral P(finish ITM), the
    extrinsic value left (→ early-exercise pressure), pin risk into expiry, and the DOLLAR
    consequence of being assigned (effective basis / capital tied up).

    `wing` (from `identify_wing`) = the short is half of a vertical: the consequence then depends on WHERE it finishes —
    past the long both legs exercise and cancel (no shares, the full width), between the strikes the short is assigned
    and the long expires (unhedged shares). The effective basis uses the spread's NET credit, not the short leg's own
    premium (an 80/70 opened for $0.34 read "effective $77.40 after the $2.60 credit")."""
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
    if wing and wing.get("long_strike") is not None:
        Kl = float(wing["long_strike"])
        net = float(wing.get("net_entry") or 0.0)
        basis = round(K - net, 2) if right == "P" else round(K + net, 2)
        width_loss = (abs(K - Kl) - net) * _MULT * n
        if right == "P":
            consequence = (f"Below ${Kl:.0f} at expiry both legs exercise and cancel — no shares, the full ${width_loss:,.0f} loss. "
                           f"Between ${Kl:.0f} and ${K:.0f} the ${K:.0f} put is assigned and the ${Kl:.0f} expires: you BUY {int(100 * n)} sh "
                           f"at ${K:.0f} (effective ${basis:.2f} after the ${net:.2f} net credit) with no protection left — ${cap:,.0f} "
                           "of cash or margin. Above it, nothing happens.")
        else:
            consequence = (f"Above ${Kl:.0f} at expiry both legs exercise and cancel — no shares, the full ${width_loss:,.0f} loss. "
                           f"Between ${K:.0f} and ${Kl:.0f} the ${K:.0f} call is assigned and the ${Kl:.0f} expires: you are SHORT "
                           f"{int(100 * n)} sh from ${K:.0f} (effective ${basis:.2f}) with nothing capping the upside. Below it, nothing happens.")
    elif right == "P":
        consequence = (f"Assignment ⇒ you BUY {int(100 * n)} sh at ${K:.0f} (effective ${basis:.2f} after the "
                       f"${C:.2f} credit) — ${cap:,.0f} of cash committed.")
    elif covered:
        basis = round(stock_basis if stock_basis else (K - C), 2)
        gain = "a gain" if K >= basis else "a small loss"
        consequence = (f"Called away ⇒ your {int(100 * n)} shares are SOLD at ${K:.0f}. This is a CAPPED outcome, not an "
                       f"unbounded loss: you keep the ${C:.2f} premium plus {gain} from ${basis:.2f} to ${K:.0f}. Roll the "
                       "call up/out ONLY to keep more upside — the downside is the stock's, not the option's.")
    else:
        # A called-away NAKED short call sells (shorts) shares at K and keeps the premium → the effective
        # short entry is K + C. (`K − C` is the PUT formula — it read $699.10 on a $700 call sold for $0.90.)
        basis = round(K + C, 2)
        consequence = (f"Assignment ⇒ {int(100 * n)} sh CALLED AWAY at ${K:.0f} (effective ${basis:.2f} after the "
                       f"${C:.2f} credit) — NAKED: you're left short {int(100 * n)} shares (unbounded upside risk). "
                       "Cap it (buy a long call) or roll up/out.")
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
                        base_date: Optional[str] = None, stock: Optional[dict] = None,
                        far_dte: Optional[int] = None) -> dict:
    """Build the repair menu for the WHOLE position.

    legs: the trade's option legs [{strike, right('P'/'C'), sign(+1/−1), qty, entry}] (entry = the
          per-share credit received / debit paid at open). All at the trade's expiry `dte_days`.
    chains: live {tenor_days: {strike: {'P': {'mid','iv'}, 'C': {...}}}} for the near + far expiries.
    ex_div: optional {'amount': float, 'days': int|None} — the next dividend before expiry, for the
            short-call early-assignment check.
    near_expiry / far_expiry: ISO dates for the near (D) and far tenors, so every suggested leg
            carries a real expiration; base_date anchors the fallback (today).
    far_dte: the ACTUAL days-to-expiry of the far chain's listed expiry. The far chain is the listed
            expiry NEAREST D+roll_days, so it rarely lands on exactly +45d (DDOG: target 66d → Nov 20 = 56d).
            The far leg must be built, priced AND marked at that real tenor — using the nominal one priced it
            off the Nov 20 chain but valued it as if 10 more days of time value remained at the horizon.
    """
    import datetime as _dt
    D = int(dte_days)
    Dg = int(far_dte) if (far_dte and int(far_dte) > D) else D + roll_days
    roll_days = Dg - D                     # labels ("+45d") and the far tenor now describe the SAME expiry
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
    wing_info = identify_wing(hold, spot)             # a clean vertical wing → the live roll search can price it as ONE package

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
        leg = {"strike": float(strike), "right": rt, "sign": sign, "qty": qty or n,
               "dte_days": tenor, "iv": pr.iv(tenor, float(strike), rt), "entry": float(entry)}
        if tenor > D:
            # Beyond the near expiry (calendars / diagonals): once the stock has diffused at iv0 to the horizon,
            # what remains of this leg is worth the FORWARD vol for the leftover stretch, not its whole-life IV.
            leg["iv_fwd"] = _fwd_iv(leg["iv"], iv0, tenor, D)
        return leg

    alts: list[dict] = []
    # 0) CLOSE ALL — the benchmark.
    if stock:
        # SHARES HELD behind the options: closing means buying the option leg(s) back — the shares STAY, so the outcome is
        # NOT a flat line. It is priced exactly like Hold (same shares, same distribution, same horizon), so the two
        # benchmarks are comparable; a flat "walk away" row set against a Hold that carried the stock made every close look
        # like a +$unreal-vs-+$stock-drift mismatch and (before the shares were seen at all) a +$5,964 "gain".
        close_row = _build(
            name="Close the trade", category="exit", group="benchmark",
            mechanics="Buy the option leg(s) back at current prices — the shares stay in your account.",
            legs=[], realized=unreal, stock=stock, establish_cash=_close_cash(hold, now_val), spot=spot, r=r, iv_ref=iv0,
            horizon_days=D,
            rationale="Realizes the option P&L with certainty and releases the shares from the cap — their own risk is unchanged.")
        close_row["legs"] = []                                      # nothing to trade except the buy-back (the STK row is the HELD shares)
        close_row["keeps_shares"] = True
        close_row["risk_note"] = "Certain on the option leg; the share position keeps its own risk."
        alts.append(close_row)
    else:
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
                     mechanics=mechanics, legs=cal, realized=realized, stock=stock, establish_cash=est,
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
                     realized=_close_realized([tested], now_val), stock=stock,
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
                     realized=0.0, stock=stock, establish_cash=_open_cash(fly), spot=spot, r=r, iv_ref=iv0, rationale=rat)
        if row["max_gain"] > 0 and row.get("pop_pct") is not None:
            alts.append(row)

    if lone_short and right == "P":   # ── CSP: rich single-leg menu ─────────────────────────────
        # 1) ROLL OUT AT THE SAME STRIKE — pure duration, zero directional change. Often the BIGGEST
        #    credit of any roll when the far tenor's extra time value beats the near buyback (exactly
        #    what a term-structure-aware search is built to find), and the only "fix" that adds no new
        #    directional exposure at all — was missing from this menu even though it's the plain-vanilla
        #    first move a real desk checks before reaching for anything fancier.
        same_far_p = pr.entry(Dg, K, "P", spot)
        alts.append(_build(
            name=f"Roll out at the same ${K:.0f} strike (+{roll_days}d)", category="roll",
            mechanics=f"Buy back the ${K:.0f} put (~${now_val(tested):.2f}) and sell the SAME ${K:.0f} strike ~{Dg}d (~${same_far_p:.2f}) — no strike change, purely more time.",
            legs=[L(K, "P", -1, Dg, same_far_p)], realized=_close_realized([tested], now_val), stock=stock,
            establish_cash=_close_cash([tested], now_val) + _open_cash([L(K, "P", -1, Dg, same_far_p)]),
            spot=spot, r=r, iv_ref=iv0,
            rationale="The plain-vanilla roll: same strike, more time, zero new directional exposure. A bet that the extra weeks of decay/vol are worth more than the buyback — often the single biggest credit on offer when the term structure even modestly favors it."))

        # CAP THE TAIL — buy a lower put at the SAME expiry: turns the cash-secured put into a put credit
        # spread (defined downside) for a small debit, and frees most of the capital the naked put ties up.
        Kw_p = pr.wing(D, K, "below", max(spot * 0.05, 0.75 * pr.sigma(spot, D, iv0)))
        if Kw_p < K:
            wpx_p = pr.entry(D, Kw_p, "P", spot)
            alts.append(_build(
                name=f"Cap the tail — buy the ${Kw_p:.0f} put (same expiry)", category="defined_risk",
                mechanics=(f"Keep the ${K:.0f} put and BUY the ${Kw_p:.0f} put ({D}d, ~${wpx_p:.2f}) — converts the cash-secured put "
                           f"into a ${K - Kw_p:.0f}-wide put credit spread: the downside becomes a KNOWN max loss for a small debit."),
                legs=[dict(tested), L(Kw_p, "P", 1, D, wpx_p)], realized=0.0, stock=stock,
                establish_cash=-wpx_p * _MULT * n, spot=spot, r=r, iv_ref=iv0,
                rationale=(f"The plain-vanilla tail cap: a ${wpx_p * _MULT * n:,.0f} wing bounds the loss at the ${Kw_p:.0f} strike however far "
                           "it falls. Keeps the trade and its duration intact."
                           + (f" NOTE: PoP reads low because the wing costs more than the ${C * _MULT * n:,.0f} credit you originally "
                              "collected — this is insurance, priced at fair value (≈ no change in E[P&L]), not a profit play."
                              if wpx_p > C else ""))))

        # 1a) ROLL DOWN & OUT — a real listed strike below K (never the same strike).
        K2 = pr.wing(Dg, K, "below", max(spot * 0.05, 0.5 * pr.sigma(spot, Dg, iv0)))
        far = pr.entry(Dg, K2, "P", spot)
        alts.append(_build(
            name=f"Roll down & out (→ ${K2:.0f}, +{roll_days}d)", category="roll",
            mechanics=f"Buy back the ${K:.0f} put (~${now_val(tested):.2f}), sell the ${K2:.0f} put ~{Dg} DTE (~${far:.2f}) — moves the strike {abs(round((K2-K)/K*100))}% lower, buys time.",
            legs=[L(K2, "P", -1, Dg, far)], realized=_close_realized([tested], now_val), stock=stock,
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
                legs=sp_legs, realized=_close_realized([tested], now_val), stock=stock,
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
                legs=up_legs, realized=_close_realized([tested], now_val), stock=stock,
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
                realized=0.0, stock=stock, establish_cash=csc * _MULT * n, spot=spot, r=r, iv_ref=iv0,
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
                realized=0.0, stock=stock, establish_cash=cs * _MULT * n, spot=spot, r=r, iv_ref=iv0,
                rationale="Harvests theta on the FAR (low-gamma) call side and lowers your put breakeven with (near) no upside tail. Doesn't fix the downside — the put is still the risk."))
            # 4) IRON-CONDOR CAP — add a protective put wing below K to bound the downside too.
            Kpw = pr.wing(D, K, "below", max(spot * 0.05, pr.sigma(spot, D, iv0)))
            pw = pr.entry(D, Kpw, "P", spot)
            alts.append(_build(
                name=f"Cap into an iron condor (buy ${Kpw:.0f} put)", category="defined_risk",
                mechanics=f"Keep the ${K:.0f} put, BUY a ${Kpw:.0f} put (~${pw:.2f}) to cap the downside, add the ${Kc:.0f}/{Kc2:.0f} call spread — fully DEFINED risk both ways.",
                legs=[L(K, "P", -1, D, C), L(Kpw, "P", 1, D, pw), L(Kc, "C", -1, D, c_short), L(Kc2, "C", 1, D, c_long)],
                realized=0.0, stock=stock, establish_cash=(cs - pw) * _MULT * n, spot=spot, r=r, iv_ref=iv0,
                rationale="Both open tails become KNOWN max losses for the cost of the put wing — the move when you want the trade fully bounded before an uncertain stretch."))

        # 3) ROLL-DOWN + JADE — far-dated spread, financed roll.
        far_sp = _credit_spread(pr, Dg, pr.snap(Dg, spot + 0.6 * pr.sigma(spot, Dg, iv0)), "above", C, spot)
        if far_sp and (far_sp[6] or far_sp[5] >= 0.35 * far_sp[4]):
            Kcf, Kc2f, cf_s, cf_l, wf, csf, rff = far_sp
            new_legs = [L(K2, "P", -1, Dg, far), L(Kcf, "C", -1, Dg, cf_s), L(Kc2f, "C", 1, Dg, cf_l)]
            alts.append(_build(
                name=f"Roll-down + jade (${K2:.0f} put + ${Kcf:.0f}/{Kc2f:.0f})", category="overlay",
                mechanics=f"Buy back ${K:.0f}, sell the ${K2:.0f} put ~{Dg}d AND add the ${Kcf:.0f}/{Kc2f:.0f} call spread (~${csf:+.2f}/sh) — the call credit funds the roll-down; {_rf_txt(0, csf, wf, rff)}.",
                legs=new_legs, realized=_close_realized([tested], now_val), stock=stock,
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
                legs=bwb, realized=_close_realized([tested], now_val), stock=stock,
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
        # ROLL OUT AT THE SAME STRIKE — pure duration, zero directional change (see the CSP mirror
        # above for why this belongs FIRST, before strike-moving rolls: it's the plain-vanilla move
        # and often the biggest credit when the term structure even modestly favors it).
        same_far_c = pr.entry(Dg, K, "C", spot)
        alts.append(_build(
            name=f"Roll out at the same ${K:.0f} strike (+{roll_days}d)", category="roll",
            mechanics=f"Buy back the ${K:.0f} call (~${now_val(tested):.2f}) and sell the SAME ${K:.0f} strike ~{Dg}d (~${same_far_c:.2f}) — no strike change, purely more time.",
            legs=[L(K, "C", -1, Dg, same_far_c)], realized=_close_realized([tested], now_val), stock=stock,
            establish_cash=_close_cash([tested], now_val) + _open_cash([L(K, "C", -1, Dg, same_far_c)]),
            spot=spot, r=r, iv_ref=iv0,
            rationale="The plain-vanilla roll: same strike, more time, zero new directional exposure. A bet that the extra weeks of decay/vol are worth more than the buyback — often the single biggest credit on offer when the term structure even modestly favors it."))

        # CAP THE TAIL — buy a further-OTM call at the SAME expiry: converts a NAKED short call into a defined-risk
        # call credit spread for a small, known debit. The cheapest, most standard way to bound an unbounded
        # short call while keeping the trade (and its premium / odds) intact — no roll, no new duration. Skipped
        # for a covered call (the shares already define it).
        Kw_c = pr.wing(D, K, "above", max(spot * 0.05, 0.75 * pr.sigma(spot, D, iv0)))
        if Kw_c > K and not stock:
            wpx_c = pr.entry(D, Kw_c, "C", spot)
            alts.append(_build(
                name=f"Cap the tail — buy the ${Kw_c:.0f} call (same expiry)", category="defined_risk",
                mechanics=(f"Keep the ${K:.0f} call and BUY the ${Kw_c:.0f} call ({D}d, ~${wpx_c:.2f}) — converts the naked call into a "
                           f"${Kw_c - K:.0f}-wide call credit spread: the loss becomes a KNOWN max instead of unlimited, for a small debit."),
                legs=[dict(tested), L(Kw_c, "C", 1, D, wpx_c)], realized=0.0, stock=stock,
                establish_cash=-wpx_c * _MULT * n, spot=spot, r=r, iv_ref=iv0,
                rationale=(f"The plain-vanilla tail cap: a ${wpx_c * _MULT * n:,.0f} wing bounds the loss at the ${Kw_c:.0f} strike however far "
                           "it runs. Keeps the trade and its duration intact."
                           + (f" NOTE: PoP reads low because the wing costs more than the ${C * _MULT * n:,.0f} credit you originally "
                              "collected — this is insurance, priced at fair value (≈ no change in E[P&L]), not a profit play."
                              if wpx_c > C else ""))))

        K2 = pr.wing(Dg, K, "above", max(spot * 0.05, 0.5 * pr.sigma(spot, Dg, iv0)))
        far = pr.entry(Dg, K2, "C", spot)
        alts.append(_build(
            name=f"Roll up & out (→ ${K2:.0f}, +{roll_days}d)", category="roll",
            mechanics=f"Buy back ${K:.0f} (~${now_val(tested):.2f}), sell ${K2:.0f} ~{Dg}d (~${far:.2f}) — raises the strike, buys time.",
            legs=[L(K2, "C", -1, Dg, far)], realized=_close_realized([tested], now_val), stock=stock,
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
                legs=cs_legs, realized=_close_realized([tested], now_val), stock=stock,
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
                legs=dn_legs, realized=_close_realized([tested], now_val), stock=stock,
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
                realized=0.0, stock=stock, establish_cash=csp3 * _MULT * n, spot=spot, r=r, iv_ref=iv0,
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
                realized=0.0, stock=stock, establish_cash=ps * _MULT * n, spot=spot, r=r, iv_ref=iv0,
                rationale="Mirror jade for a tested call — extra theta on the far (low-gamma) put side, lowers the call breakeven, with (near) no added downside tail. Doesn't fix the tested call itself."))

        # ADVANCED — TERM-STRUCTURE DEFENSES (calendars), gated. (a) same-strike calendar caps the
        # naked call's UNBOUNDED upside with a long far call + harvests near theta (best on a fresh
        # breach); (b) re-center closes the run-over strike and opens an at-the-money call calendar.
        far_kc = pr.entry(Dg, K, "C", spot)
        # …all of this, and the recovery tents/ratios below, are fixes for a NAKED call the stock ran over. With the shares
        # held there is no unbounded tail to calendarise away and no "loss to recover on a pullback" (the stock's own gain
        # is the offset), so they are skipped for a covered call — the same gate as the tail-cap / strangle above.
        if not stock:
            _add_calendar("C", K, K, keep=True,
                name=f"Calendarised gamma hedge (buy the ${K:.0f} call ~{Dg}d)",
                mechanics=f"Keep the near ${K:.0f} call; BUY the ${K:.0f} call ~{Dg}d (~${far_kc:.2f}) — the long far call's gamma OFFSETS the near short's gamma at the strike, FLATTENING the curve so a breach hurts far less, and turns the unbounded upside into DEFINED risk while harvesting the rich near vol; roll the near short up each cycle.",
                rationale=f"Calendarised gamma hedge — long far call flattens the short's gamma at ${K:.0f} (a breach no longer whipsaws you), defines the upside, and you own cheaper far vol vs richer near. The 'cap the tail AND keep selling' defense; best if the stock stalls near ${K:.0f}.")
        Kspot = pr.snap(D, spot)
        if not stock:
            _add_calendar("C", Kspot, Kspot, keep=False,
                name=f"Re-center into a call calendar (${Kspot:.0f}, at spot)",
                mechanics=f"CLOSE the ${K:.0f} call (bank the loss) and open a fresh ${Kspot:.0f} call calendar at the money (short ~{D}d / long ~{Dg}d) — stop defending the strike the stock ran through.",
                rationale=f"The re-center calendar: bank the loss on the run-over strike and redeploy into an at-the-money calendar that profits on stabilization near ${Kspot:.0f} + term-structure decay. The advanced move for a call the stock has left far below.")

        # BUTTERFLY recovery tents (FIX — keep the short) + RATIO / BACK-RATIO REPLACEMENTS
        # (tested call recovers on a DOWN move → PUT structures).
        if not stock:
            _add_butterfly("P", "tent")
            _add_butterfly("P", "diagonal")
            _add_ratio("P", "zebra")
            _add_ratio("P", "backratio")

        # ── STOCK-OWNERSHIP DEFENSES — the move a naked short call being run over by a RALLY really wants:
        #    own the underlying so the trend that THREATENS the call now PAYS you. (Needs capital — shown.) ──
        cover_cost = spot * _MULT * n
        max_up = (K - spot + C)                                    # per-share gain if called away at K
        if not stock:                                              # already own the shares? then there is nothing to "cover"
            alts.append(_build(
                name=f"Cover it — buy {int(100 * n)} sh (→ covered call)", category="cover",
                mechanics=f"BUY {int(100 * n)} shares at ${spot:.2f} (${cover_cost:,.0f} capital); keep the ${K:.0f} call. Above ${K:.0f} the shares gain 1:1 with the call → called away at ${K:.0f} for +${K-spot:.2f}/sh plus the ${C:.2f} premium (${max_up*_MULT*n:,.0f} max). Below, you simply own the stock (the premium cushions).",
                legs=[L(K, "C", -1, D, C)], realized=0.0, stock={"shares": 100 * n, "basis": spot},
                establish_cash=-cover_cost, spot=spot, r=r, iv_ref=iv0,
                rationale=f"EARN WITH the rising stock instead of fighting it — the uptrend that threatens the naked call now pays you up to ${K:.0f}, where you're called away at a PROFIT. Caps the unbounded upside (now defined), rides a continued rally, keeps the premium. Cost: ${cover_cost:,.0f} capital + new downside stock risk — collar it to floor that."))

        Kpp = pr.snap(D, spot * 0.93)                             # ~7% OTM protective put floor
        pp = pr.entry(D, Kpp, "P", spot)
        if stock:
            # a COVERED call already owns the shares → the collar is just the protective put on them (no stock purchase)
            alts.append(_build(
                name=f"Collar the shares — buy the ${Kpp:.0f} put", category="cover",
                mechanics=f"Keep your shares and the ${K:.0f} call; BUY the ${Kpp:.0f} put ({D}d, ~${pp:.2f}, ${pp*_MULT*n:,.0f}) — the put FLOORS the downside on the shares, the call caps the upside: DEFINED both ways.",
                legs=[L(K, "C", -1, D, C), L(Kpp, "P", 1, D, pp)], realized=0.0, stock=stock,
                establish_cash=-pp * _MULT * n, spot=spot, r=r, iv_ref=iv0,
                rationale=f"Floor the shares you already hold: the ${Kpp:.0f} long put caps the downside if the stock reverses, for ${pp*_MULT*n:,.0f}. No new stock capital."))
        else:
            alts.append(_build(
                name=f"Collar it — buy stock + ${Kpp:.0f} put", category="cover",
                mechanics=f"BUY {int(100 * n)} shares at ${spot:.2f} + BUY the ${Kpp:.0f} put (~${pp:.2f}); keep the ${K:.0f} call. Called away at ${K:.0f} = max gain; the ${Kpp:.0f} put FLOORS the downside — DEFINED both ways. Cost ${(spot+pp)*_MULT*n:,.0f}.",
                legs=[L(K, "C", -1, D, C), L(Kpp, "P", 1, D, pp)], realized=0.0, stock={"shares": 100 * n, "basis": spot},
                establish_cash=-(spot + pp) * _MULT * n, spot=spot, r=r, iv_ref=iv0,
                rationale=f"The PROTECTED cover — earn with the stock up to ${K:.0f}, but the ${Kpp:.0f} long put caps the downside if it reverses. Defined-risk BOTH ways (a collar). Best when you'll own OKTA-type names but want a floor; costs the stock capital + the put premium."))

        if not stock:                                             # a covered call is already delta-hedged by its shares
            _delta_hedge(alts, _build, tested, spot, D, r, iv0, C, n, "call")

    else:   # ── MULTI-LEG structure (vertical / condor / strangle): STRUCTURAL repairs ──────────
        # 1) Roll the WHOLE structure out — same strikes, later expiry.
        new_whole = [L(lg["strike"], lg["right"], lg["sign"], Dg, pr.entry(Dg, lg["strike"], lg["right"], spot), lg["qty"]) for lg in hold]
        alts.append(_build(
            name=f"Roll the whole structure out (+{roll_days}d)", category="roll",
            mechanics=f"Close all {len(hold)} legs and reopen the SAME strikes ~{Dg} DTE — resets the clock for the structure to work out, usually near credit-neutral.",
            legs=new_whole, realized=_close_realized(hold, now_val), stock=stock,
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
            legs=target, realized=_close_realized(wing, now_val), stock=stock,
            establish_cash=_close_cash(wing, now_val) + _open_cash(wing_new), spot=spot, r=r, iv_ref=iv0,
            rationale="Relieves ONLY the threatened side — the standard condor/strangle defense: move the tested wing out of the way, keep collecting on the safe wing."))

        # 3) Close the tested WING, keep the rest — bank the untested credit.
        if other:
            alts.append(_build(
                name=f"Close the tested wing, keep the {'call' if right=='P' else 'put'} side", category="defined_risk",
                mechanics=f"Buy back the tested {'put' if right=='P' else 'call'} wing (stop that bleed) and hold the untested wing to expiry to keep its credit.",
                legs=[dict(lg) for lg in other], realized=_close_realized(wing, now_val), stock=stock,
                establish_cash=_close_cash(wing, now_val), spot=spot, r=r, iv_ref=iv0,
                rationale="Takes the risk off the tested side for a defined cost while the safe wing keeps decaying — a clean partial exit."))

    # The defense principle: NO net new money — a roll must be a CREDIT (or at worst even). The live roll search only
    # ever proposes those, so every eager roll ("Roll up to a call spread", "Roll the tested wing away & out", …) obeys
    # the same rule; a debit roll offered beside a search that says "credit-only" is a contradiction. (Applies to
    # spreads and condors too — it used to cover only a lone short, so a spread could be "defended" by paying.)
    alts = [a for a in alts if not (a["name"].startswith("Roll") and a["net_cash"] < 0)]

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
        _vs_hold(a, hold_pop, hold_ev, hold_ml)
        # The fixed-horizon (+roll_days) rolls are only a FALLBACK: for a lone short, the live roll search
        # (roll_optimizer_service → build_roll_alternatives) finds the real strike/expiry and REPLACES them,
        # so they carry a marker the merge uses to drop them (one source of truth for rolls).
        if a["category"] == "roll" and (lone_short or (wing_info and (
                a["name"].startswith("Roll the tested") or (a["name"].startswith("Roll the whole") and not wing_info["kept"])))):
            a["plain_roll"] = True
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

    # The position AS IT STANDS — every alternative is spelled out against it (what to close / open / keep, and the
    # all-in result including what you already hold). `mark` = today's value of each held leg (the close price).
    held = {"legs": [{"right": lg["right"], "strike": lg["strike"], "sign": lg["sign"], "qty": lg["qty"], "dte_days": D,
                      "expiry": _expiry_for(D), "entry": round(lg["entry"], 4), "mark": round(now_val(lg), 4)} for lg in hold],
            "stock": ({"shares": stock["shares"], "basis": stock["basis"]} if stock else None),
            "collected": round(sum(-lg["sign"] * lg["qty"] * _MULT * lg["entry"] for lg in hold), 0)}
    for a in alts:
        attach_steps(a, held, spot)
    set_beat_close(alts, (None if stock else unreal))

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
                                  n=n, ex_div=ex_div, covered=_covered, stock_basis=(stock or {}).get("basis"),
                                  wing=(wing_info if (wing_info and not wing_info["kept"]) else None)),
        "cost_of_waiting": _cost_of_waiting(spot=spot, iv0=iv0, D=D, r=r, hold_legs=hold),
        "hold": {"pop_pct": hold_pop, "expected_pnl": hold_ev, "max_loss": hold_ml,
                 "greeks": hold_row.get("greeks"), "breakevens": hold_row.get("breakevens")},
        "alternatives": alts,
        "held": held,
        "wing": ({k: v for k, v in wing_info.items() if k != "kept"} | {"kept_legs": len(wing_info["kept"])}
                 if wing_info else None),
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


# ── shared risk math — the router's risk read and everything here use the SAME functions ──────────
INCOME_VIABLE_SLACK = 12    # an income-viable fix is preferred unless it scores more than this many points below the top benchmark
NEAREST_SUFFICIENT = 6      # among income-viable fixes within this many points of the best, take the SHORTEST extension
NONVIABLE_MARGIN = 6        # a fix that costs money / worsens the odds must beat CLOSING by this many points (it removes the tail outright)
_TOUCH_R_ANCHOR = 0.045     # risk-neutral rate the raw momentum is shrunk back toward
_TOUCH_SHRINK = 0.35        # share of the (capped) momentum deviation that survives into the drift
_TOUCH_CAP = 1.0            # ±100%/yr ceiling on the raw extrapolated momentum


def touch_drift(mu_raw: Optional[float]) -> float:
    """The drift a BARRIER (P(touch)) calculation may use, from the raw annualized momentum.

    The raw figure is the last ~week's EMA move extrapolated to a year — an honest DESCRIPTION of a hot
    week ("+723%/yr" on AMD) but not a forecast. A barrier probability takes its drift literally, so fed
    raw it re-applies that week's pace to the whole remaining horizon and saturates near 100% for any
    name with a recent run, whatever the strike distance (and contradicts the risk-neutral P(ITM) and the
    market-implied RND right beside it). Cap at ±100%/yr and shrink 65% back toward the risk-neutral rate:
    a real move still TILTS the odds; it just isn't taken as a prophecy."""
    if mu_raw is None:
        return 0.0
    capped = max(-_TOUCH_CAP, min(_TOUCH_CAP, float(mu_raw)))
    return _TOUCH_R_ANCHOR + _TOUCH_SHRINK * (capped - _TOUCH_R_ANCHOR)


def barrier_touch_prob(S, K, iv, dte, mu, right) -> Optional[float]:
    """Drift-aware first-passage probability the short strike is BREACHED (touched) at ANY point before
    expiry — the real 'will it get tested' risk, ~2× the terminal-ITM odds (GBM reflection principle).
    Returns 0..1 or None."""
    T = max(dte, 1) / 365.0
    sig = max(iv or 0.0, 0.02)
    if not S or not K or S <= 0 or K <= 0 or T <= 0:
        return None
    nu = mu - 0.5 * sig * sig
    sq = sig * math.sqrt(T)
    if sq <= 0:
        return None
    a = math.log(K / S)
    try:
        if right == "C":                         # upper barrier K
            if a <= 0:
                return 0.99
            p = _norm_cdf((-a + nu * T) / sq) + math.exp(2 * nu * a / (sig * sig)) * _norm_cdf((-a - nu * T) / sq)
        else:                                    # lower barrier K
            if a >= 0:
                return 0.99
            p = _norm_cdf((a - nu * T) / sq) + math.exp(2 * nu * a / (sig * sig)) * _norm_cdf((a + nu * T) / sq)
    except (OverflowError, ValueError):
        return None
    return max(0.0, min(0.999, float(p)))


def implied_event_move(near_iv: Optional[float], far_iv: Optional[float], near_dte: Optional[float],
                       far_dte: Optional[float], earn_days: Optional[float]) -> Optional[float]:
    """The market-implied ONE-σ earnings move, in percent, read off the term structure.

    When the print falls after the near expiry but on/before the far one, the far month's total variance holds the
    event on top of ordinary diffusion. Taking the near (pre-event) IV as the base rate for the whole stretch,
    event variance = σ_far²·T_far − σ_near²·T_far = (σ_far² − σ_near²)·T_far, and the implied move is its square root.
    Indicative, not a quote — it needs the print to sit between the two expiries and a back month dearer than the
    front (otherwise the event cannot be isolated and this returns None). Capped at 60% so a bad chain can't print
    nonsense."""
    if None in (near_iv, far_iv, near_dte, far_dte, earn_days):
        return None
    if not (float(near_dte) < float(earn_days) <= float(far_dte)):
        return None
    var = (float(far_iv) ** 2 - float(near_iv) ** 2) * float(far_dte) / 365.0
    if var <= 0:
        return None
    return round(min(60.0, math.sqrt(var) * 100.0), 1)


def _pct_gap(px: float, level: float) -> str:
    d = (level - px) / px * 100.0
    return f"{abs(d):.1f}% {'above' if d >= 0 else 'below'}"


def structure_factor(px: float, sup: float, res: float, need_up: bool) -> dict:
    """The 'Structure' TA factor — SIGN-AWARE. `need_up` = a PUT (threatened by a fall, so it wants a floor
    beneath price); a call wants a CEILING above. When price has already broken THROUGH the level, that level
    is no longer a floor/ceiling — say so (the old hard-coded text printed '-0.9% above — a ceiling that can
    cap the rise' for a stock already trading 0.9% ABOVE the 'resistance')."""
    if need_up:
        if sup < px:
            return {"favorable": True,
                    "detail": f"support ${sup:.2f} sits {_pct_gap(px, sup)} — a floor that can halt the slide"}
        return {"favorable": False,
                "detail": f"price is already {_pct_gap(px, sup).split(' ')[0]} BELOW support ${sup:.2f} — the floor has "
                          "broken, nothing beneath it to halt the slide"}
    if res > px:
        return {"favorable": True,
                "detail": f"resistance ${res:.2f} sits {_pct_gap(px, res)} — a ceiling that can cap the rise"}
    return {"favorable": False,
            "detail": f"price is already {_pct_gap(px, res).split(' ')[0]} ABOVE resistance ${res:.2f} — a breakout: the "
                      "ceiling has broken, nothing overhead to cap the rise"}


def level_note(px: float, sup: float, res: float) -> str:
    """One-line support/resistance read with the direction of each gap stated correctly."""
    note = f"Spot ${px:.2f}: support ${sup:.2f} ({_pct_gap(px, sup)}), resistance ${res:.2f} ({_pct_gap(px, res)})."
    if px > res:
        note += " Price is trading THROUGH the range top (a breakout)."
    elif px < sup:
        note += " Price is trading THROUGH the range floor (a breakdown)."
    return note


def build_roll_alternatives(*, candidates: list[dict], tested: dict, tested_mark: float, hold: Optional[dict],
                            spot: float, r: float, stock: Optional[dict], iv_default: float,
                            held: Optional[dict] = None, kept: Optional[list] = None,
                            benchmark: Optional[float] = None) -> list[dict]:
    """Turn the roll optimizer's CREDIT-ONLY candidates into FULL alternatives — payoff, greeks, PoP,
    E[P&L], max loss, Δ-vs-hold — built by the SAME `_build` engine as every other repair, so a roll is
    scored on exactly the same footing as a cap, a collar or a close. This is what makes the desk's
    recommendation and the roll search ONE ranked menu instead of two opinions.

    `tested` = {right, strike, qty, entry[, long_strike, dte_days, expiry]}; `tested_mark` = the buy-back price the
    search used (for a spread: the WHOLE old spread's debit); `hold` = the baseline {pop_pct, expected_pnl, max_loss}.

    A SPREAD roll (`tested["long_strike"]` present — candidates then carry `long_strike`/`long_price`/`long_iv`)
    closes the old spread and opens a new one of the same width; `tested["entry"]` is then the spread's NET credit per
    share, so the realized loss of closing it is carried into every scenario. `kept` = the legs OUTSIDE the wing (the
    untouched side of an iron condor) as held-leg dicts {right, sign, strike, qty, dte_days, iv, entry}.
    `held` (from `repair_alternatives`) lets each row explain itself: what to close / open and the all-in result."""
    right = tested["right"]
    n = int(tested.get("qty") or 1)
    K0 = float(tested["strike"])
    Kl0 = float(tested["long_strike"]) if tested.get("long_strike") is not None else None
    wing = Kl0 is not None
    entry0 = abs(float(tested.get("entry") or 0.0))
    cur_dte, cur_exp = tested.get("dte_days"), tested.get("expiry")
    word = "put" if right == "P" else "call"
    realized = n * _MULT * (entry0 - float(tested_mark))             # buying the old short (spread) back, vs its original credit
    kept = [dict(k) for k in (kept or [])]
    out: list[dict] = []
    for c in candidates:
        K, dte = float(c["strike"]), int(c["dte"])
        new_px = float(c.get("new_price") or (float(tested_mark) + float(c.get("credit_per_share") or 0.0)))
        iv = float(c.get("iv") or 0.0) or float(iv_default)
        short_leg = {"strike": K, "right": right, "sign": -1, "qty": n, "dte_days": dte, "iv": iv, "entry": new_px}
        new_legs = [short_leg]
        Kl = None
        if wing and c.get("long_strike") is not None:
            Kl = float(c["long_strike"])
            new_legs.append({"strike": Kl, "right": right, "sign": 1, "qty": n, "dte_days": dte,
                             "iv": float(c.get("long_iv") or 0.0) or iv, "entry": float(c.get("long_price") or 0.0)})
        if kept and cur_dte is not None and dte > int(cur_dte):
            # the kept side expires first, so the horizon is THEIR expiry — what's left of the new wing is worth the
            # forward vol for that stretch (same rule as every calendar / diagonal in this engine)
            for lg in new_legs:
                lg["iv_fwd"] = _fwd_iv(lg["iv"], float(iv_default), dte, int(cur_dte))
        roll_net = float(c["roll_net_cash"])
        try:
            d = dt.date.fromisoformat(str(c["expiry"])[:10])
            exp_lbl = f"{d.strftime('%b')} {d.day}"
        except (ValueError, TypeError):
            exp_lbl = str(c.get("expiry"))
        same = abs(K - K0) < 1e-6
        credit_lbl = "even cost" if abs(roll_net) < 0.5 else f"{'+' if roll_net >= 0 else '−'}${abs(roll_net):,.0f} credit"
        if wing and Kl is not None:
            name = f"Roll the ${K0:.0f}/{Kl0:.0f} {word} spread → ${K:.0f}/{Kl:.0f} · {exp_lbl} ({dte}d) — {credit_lbl}"
            mechanics = (f"Buy back the ${K0:.0f}/{Kl0:.0f} {word} spread (~${float(tested_mark):.2f} debit) and sell the "
                         f"${K:.0f}/{Kl:.0f} {word} spread expiring {c.get('expiry')} (~${float(c.get('credit_per_share') or 0.0) + float(tested_mark):.2f} credit) — "
                         f"{'EVEN cost' if abs(roll_net) < 0.5 else 'a NET CREDIT of $' + format(roll_net, ',.0f')}, the same ${abs(K - Kl):.0f} width, "
                         "defined risk; no new money.")
        else:
            name = f"Roll to the ${K:.0f} {word} · {exp_lbl} ({dte}d) — {credit_lbl}"
            mechanics = (f"Buy back the ${K0:.0f} {word} (~${float(tested_mark):.2f}) and sell the "
                         f"{'SAME ' if same else ''}${K:.0f} {word} expiring {c.get('expiry')} (~${new_px:.2f}) — a NET CREDIT of "
                         f"${roll_net:,.0f}; no new money.")
        row = _build(
            name=name, category="roll", group="adjust", mechanics=mechanics,
            legs=new_legs + kept, realized=realized, stock=stock, establish_cash=roll_net, spot=spot, r=r, iv_ref=iv,
            rationale=c.get("why") or "")
        for lg in row["legs"]:
            if lg.get("right") in ("P", "C"):
                lg["expiry"] = cur_exp if (cur_dte is not None and lg.get("dte_days") == int(cur_dte)) else c.get("expiry")
        row["roll_meta"] = {
            "expiry": c.get("expiry"), "dte": dte, "strike": K, "credit_total": roll_net,
            "p_otm": c.get("p_otm"), "p_otm_source": c.get("p_otm_source"),
            "spans_earnings": bool(c.get("spans_earnings")), "structure": c.get("structure") or {},
            "scores": c.get("scores") or {}, "new_breakeven": c.get("new_breakeven"),
            "new_capital": c.get("new_capital"),
            **({"wing": {"short": K, "long": Kl, "width": abs(K - Kl), "old_short": K0, "old_long": Kl0}} if (wing and Kl is not None) else {}),
        }
        if hold:
            _vs_hold(row, hold.get("pop_pct"), hold.get("expected_pnl"), hold.get("max_loss"))
        if held:
            attach_steps(row, held, spot)
        out.append(row)
    set_beat_close(out, benchmark)
    return out


def build_ladder_alternatives(*, ladder: dict, right: str, short_strike: float, long_strike: Optional[float], qty: int,
                              credit_ps: float, unwind_ps: float, hold: Optional[dict], spot: float, r: float,
                              held: Optional[dict] = None, horizon_cap: int = 180, wait_ctx: Optional[dict] = None,
                              benchmark: Optional[float] = None, same_max_debit_frac: float = 0.15) -> list[dict]:
    """Turn the ASSIGNMENT LADDER (defend_playbook_service.assignment_ladder) into full alternatives — payoff, PoP,
    E[P&L], all-in steps — built by the same `_build` as every repair, so the moves a spread's own roll search can never
    find (it insists on the same width, which is a DEBIT once the spread is in the money) are ranked on the same footing:

      · CASH-SECURED — close what you hold and sell ONE put (call) further away, the premium paying for the close. Two
        rungs: the nearest one that puts the assignment price at or below today's stock price, and the lowest assignment
        price on the whole ladder. Its tail is the stock's (`defined_risk` False), so the income policy never picks it
        on its own — it is the answer for the "lowest cost if assigned" goal.
      · WIDER DEFINED RISK — the spread with the short furthest away that still pays for the close: the nearest rung,
        and the rung with the best P(profit) within `horizon_cap` days.
      · SAME STRIKES, MORE TIME — the spread rolled as-is to the first MONTHLY on the ladder, allowed a small debit (≤
        `same_max_debit_frac` of the width): the trade a premium seller reaches for first ("assume assignment at $80, sell the
        Nov 20 $80 call, roll the $70 put" — stock + short call + long put pays exactly like the short put spread). It costs a
        little, buys time and the print, and its best case stays near break-even — the row says all three. For a put spread it
        carries `alt_route`: the same payoff placed as a collar after assignment.

    Each row carries `roll_meta` (with `route`, `basis`, …) so the timing read, the goals and the cards treat it as a roll.
    `wait_ctx` = {held_legs, dte_now, event_var, earn_days, near_vol} → each row also gets `wait`: the same roll done on
    the last day before the current expiry at the same price (`defend_playbook_service.wait_cost`)."""
    rungs = (ladder or {}).get("rungs") or []
    put = right == "P"
    n = int(qty or 1)
    word = "put" if put else "call"
    realized = n * _MULT * (float(credit_ps) - float(unwind_ps))          # closing what you hold, vs what it collected
    picks: list[tuple[str, dict]] = []
    csp = [rg for rg in rungs if rg.get("csp")]
    near_ok = next((rg for rg in csp if (rg["csp"]["basis"] <= spot if put else rg["csp"]["basis"] >= spot)), None)
    if near_ok:
        picks.append(("csp", near_ok))
    if csp:
        lowest = min(csp, key=lambda rg: rg["csp"]["basis"] if put else -rg["csp"]["basis"])
        if lowest is not near_ok:
            picks.append(("csp", lowest))
    if long_strike is not None:
        sp = [rg for rg in rungs if rg.get("spread")]
        if sp:
            picks.append(("spread", sp[0]))
            within = [rg for rg in sp if rg["dte"] <= horizon_cap]
            if within:
                best = max(within, key=lambda rg: (rg["spread"]["p_profit_pct"], -rg["dte"]))
                if best is not sp[0]:
                    picks.append(("spread", best))

    if long_strike is not None:
        same = [rg for rg in rungs if rg.get("same_width")]
        width0 = abs(short_strike - long_strike)
        from .defend_playbook_service import _monthly_like
        first = next((rg for rg in same if _monthly_like(rg["expiry"])), same[0] if same else None)
        if first and first["same_width"]["net_cash"] >= -same_max_debit_frac * width0 * _MULT * n:
            picks.append(("same", first))

    out: list[dict] = []
    for route, rg in picks:
        exp, dte = rg["expiry"], int(rg["dte"])
        try:
            d = dt.date.fromisoformat(str(exp)[:10])
            exp_lbl = f"{d.strftime('%b')} {d.day}" + (f" '{d.strftime('%y')}" if d.year != dt.date.today().year else "")
        except (ValueError, TypeError):
            exp_lbl = str(exp)
        old_txt = (f"${short_strike:.0f}/{long_strike:.0f} {word} spread" if long_strike is not None else f"${short_strike:.0f} {word}")
        if route == "csp":
            c = rg["csp"]
            K = float(c["strike"])
            legs = [{"strike": K, "right": right, "sign": -1, "qty": n, "dte_days": dte, "iv": float(c["iv"]), "entry": float(c["premium"])}]
            net = float(c["net_cash"])
            credit_lbl = "even cost" if abs(net) < 0.5 else f"{'+' if net >= 0 else '−'}${abs(net):,.0f}"
            name = (f"Convert to a cash-secured ${K:.0f} {word} · {exp_lbl} ({dte}d) — {credit_lbl} · "
                    f"{'assigned' if put else 'called'} at ${c['basis']:.2f}")
            mech = (f"Close the {old_txt} (~${unwind_ps:.2f} debit) and sell the ${K:.0f} {word} expiring {exp} (~${c['premium']:.2f}) — "
                    f"{'EVEN cost' if abs(net) < 0.5 else 'a NET CREDIT of $' + format(net, ',.0f')}, no new money. "
                    + (f"Cash-secured: if assigned you buy {100 * n} sh at ${K:.0f}, effective ${c['basis']:.2f} after every credit — "
                       f"{c['basis_vs_spot_pct']:+.1f}% vs today, ${c['capital']:,.0f} of cash." if put else
                       f"If assigned you are short {100 * n} sh from ${K:.0f}, effective ${c['basis']:.2f}."))
            rat = (f"Uses time{' and the earnings premium' if rg.get('spans_earnings') else ''} to move the assignment price from "
                   f"${short_strike:.0f} to an effective ${c['basis']:.2f} for no new money — the move for a stock you are willing to own. "
                   "The long protection goes: below the strike the loss is the stock's, not capped.")
            rm_extra = {"route": "csp", "basis": c["basis"], "basis_vs_spot_pct": c["basis_vs_spot_pct"],
                        "p_assigned_pct": c["p_assigned_pct"], "capital": c["capital"], "net_cash_natural": c.get("net_cash_natural")}
            p_otm, new_be, cap = 100 - c["p_assigned_pct"], c["basis"], c["capital"]
        elif route == "same":
            sw = rg["same_width"]
            Ks, Kl = float(sw["short"]), float(sw["long"])
            legs = [{"strike": Ks, "right": right, "sign": -1, "qty": n, "dte_days": dte, "iv": float(sw["short_iv"]), "entry": float(sw["short_mid"])},
                    {"strike": Kl, "right": right, "sign": 1, "qty": n, "dte_days": dte, "iv": float(sw["long_iv"]), "entry": float(sw["long_mid"])}]
            net = float(sw["net_cash"])
            credit_lbl = "even cost" if abs(net) < 0.5 else f"{'+' if net >= 0 else '−'}${abs(net):,.0f}"
            name = f"Roll the ${Ks:.0f}/{Kl:.0f} {word} spread to {exp_lbl} ({dte}d) — same strikes, {credit_lbl}"
            mech = (f"Close the {old_txt} (~${unwind_ps:.2f}) and sell the same ${Ks:.0f}/{Kl:.0f} {word} spread expiring {exp} "
                    f"(~${sw['short_mid'] - sw['long_mid']:.2f}) — {'a debit of $' + format(-net, ',.0f') if net < -0.5 else 'about even'}. "
                    "Same strikes, same width: more time and the earnings print to recover; the worst case moves by the debit.")
            rat = ("Keeps the trade exactly as it is, just later: time for the stock to recover. It cannot add profit — the best case is "
                   "the old credit minus what the roll costs.")
            rm_extra = {"route": "same", "wing": {"short": Ks, "long": Kl, "width": width0, "old_short": short_strike, "old_long": long_strike},
                        "net_cash_natural": sw.get("net_cash_natural")}
            p_otm, new_be, cap = None, None, round(width0 * _MULT * n)
        else:
            spd = rg["spread"]
            Ks, Kl = float(spd["short"]), float(spd["long"])
            legs = [{"strike": Ks, "right": right, "sign": -1, "qty": n, "dte_days": dte, "iv": float(spd["short_iv"]), "entry": float(spd["short_mid"])},
                    {"strike": Kl, "right": right, "sign": 1, "qty": n, "dte_days": dte, "iv": float(spd["long_iv"]), "entry": float(spd["long_mid"])}]
            net = float(spd["net_cash"])
            credit_lbl = "even cost" if abs(net) < 0.5 else f"{'+' if net >= 0 else '−'}${abs(net):,.0f} credit"
            name = (f"Roll to a {'wider ' if spd.get('wider') else ''}${Ks:.0f}/{Kl:.0f} {word} spread · {exp_lbl} ({dte}d) — {credit_lbl}")
            mech = (f"Close the {old_txt} (~${unwind_ps:.2f} debit) and open the ${Ks:.0f}/{Kl:.0f} {word} spread expiring {exp} "
                    f"(~${spd['credit']:.2f} credit) — {'EVEN cost' if abs(net) < 0.5 else 'a NET CREDIT of $' + format(net, ',.0f')}, defined risk"
                    + (f"; the wing is ${spd['width']:.0f} wide (was ${abs(short_strike - long_strike):.0f}) — that extra width is what pays for "
                       "the better strike, and it raises the worst case." if spd.get("wider") else "."))
            rat = (f"Keeps the risk DEFINED while moving the short from ${short_strike:.0f} to ${Ks:.0f} and adding {dte} days"
                   + (" across earnings" if rg.get("spans_earnings") else "")
                   + f": break-even ${spd['breakeven']:.2f}, ≈{spd['p_profit_pct']}% odds of finishing in profit, worst case "
                   f"${abs(spd['max_loss']):,.0f}.")
            rm_extra = {"route": "spread", "wing": {"short": Ks, "long": Kl, "width": spd["width"], "old_short": short_strike, "old_long": long_strike},
                        "net_cash_natural": spd.get("net_cash_natural")}
            p_otm, new_be, cap = None, spd["breakeven"], round(spd["width"] * _MULT * n)
        row = _build(name=name, category="roll", group="adjust", mechanics=mech, legs=legs, realized=realized, stock=None,
                     establish_cash=net, spot=spot, r=r, iv_ref=legs[0]["iv"], rationale=rat)
        for lg in row["legs"]:
            if lg.get("right") in ("P", "C"):
                lg["expiry"] = exp
        row["roll_meta"] = {"expiry": exp, "dte": dte, "strike": legs[0]["strike"], "credit_total": net, "p_otm": p_otm,
                            "p_otm_source": ("BS" if p_otm is not None else None), "spans_earnings": bool(rg.get("spans_earnings")),
                            "structure": {}, "scores": {}, "new_breakeven": new_be, "new_capital": cap, **rm_extra}
        if hold:
            _vs_hold(row, hold.get("pop_pct"), hold.get("expected_pnl"), hold.get("max_loss"))
        if held:
            attach_steps(row, held, spot)
        if route == "same" and put:
            # the user's framing of the very same trade: take the shares at the short strike, sell a call there, keep a put below
            sw = rg["same_width"]
            old_long = next((l for l in (held or {}).get("legs") or [] if l.get("sign", 0) > 0 and l.get("right") == right), None)
            put_roll = (float(sw["long_mid"]) - float(old_long["mark"])) * _MULT * n if old_long else None
            call_px = sw.get("other_mid")
            row["alt_route"] = {
                "title": "Same payoff as a collar: take the shares, sell a covered call",
                "steps": [s_ for s_ in [
                    (f"Now: sell the {_short_lbl(held)} ${long_strike:.0f} put and buy the {exp_lbl} ${long_strike:.0f} put"
                     + (f" — about {'−' if put_roll > 0 else '+'}${abs(put_roll):,.0f}" if put_roll is not None else "")),
                    f"At expiry: let the ${short_strike:.0f} put be assigned — you buy {100 * n} shares at ${short_strike:.0f} (${short_strike * _MULT * n:,.0f})",
                    (f"Then sell the {exp_lbl} ${short_strike:.0f} call" + (f" — about +${float(call_px) * _MULT * n:,.0f} at today's price" if call_px else "")),
                ] if s_],
                "capital": round(short_strike * _MULT * n),
                "note": ("Shares + short $%.0f call + long $%.0f put pay exactly like the $%.0f/%.0f put spread. The spread roll needs no cash "
                         "for shares and is one order; the collar ties up $%s and the call is priced only when you sell it. Never sell the "
                         "call before the shares arrive — until then it is naked.") % (short_strike, long_strike, short_strike, long_strike,
                                                                                      format(short_strike * _MULT * n, ",.0f")),
            }
        if wait_ctx:
            try:
                from .defend_playbook_service import wait_cost
                row["wait"] = wait_cost(
                    held_legs=wait_ctx["held_legs"], spot=spot, r=r, dte_now=wait_ctx.get("dte_now"),
                    new_legs=[{"right": lg["right"], "strike": lg["strike"], "sign": lg["sign"], "qty": lg["qty"], "dte": dte,
                               "iv": lg["iv"], "mid": lg["entry"]} for lg in legs],
                    event_var=wait_ctx.get("event_var"), earn_days=wait_ctx.get("earn_days"), near_vol=wait_ctx.get("near_vol"))
            except Exception:  # noqa: BLE001
                row["wait"] = None
        out.append(row)
    set_beat_close(out, benchmark)
    return out


def _short_lbl(held: Optional[dict]) -> str:
    """'Oct 30' — the expiry of the position you hold, for the plain-English steps."""
    exp = next((l.get("expiry") for l in (held or {}).get("legs") or [] if l.get("expiry")), None)
    try:
        d = dt.date.fromisoformat(str(exp)[:10])
        return f"{d.strftime('%b')} {d.day}"
    except (ValueError, TypeError):
        return "current"



def drop_plain_rolls(menu: dict) -> dict:
    """Remove the fixed-horizon fallback rolls (tagged `plain_roll` by `repair_alternatives`). Called once
    the live roll search has spoken — or been deliberately skipped for a healthy trade — so there is exactly
    ONE source of truth for rolls (a +45d roll offered beside the search's 'no credit roll clears the bar'
    was precisely the kind of contradiction this exists to prevent)."""
    menu["alternatives"] = [a for a in (menu.get("alternatives") or []) if not a.get("plain_roll")]
    return menu


def rank_defenses(menu: dict) -> dict:
    """Institutional RANKING pass — collapses every lens into the ONE number a real desk actually
    decides on. Without this, a rich menu of correctly-priced structures is still just a pile of
    cards with no verdict. Folds FOUR auditable axes into a composite `desk_score` (0-100) for
    every alternative, PLUS Hold and Close (so the honest answer can legitimately be 'do nothing'
    or 'close it' — a desk doesn't force a fancy structure onto a healthy trade, or onto one where
    nothing clears the bar):

      edge        — E[P&L] improvement vs holding PER DOLLAR of capital the fix ties up (a
                    risk-adjusted return ON CAPITAL: a $50 edge on $500 beats a $500 edge on $50k
                    — raw EV alone rewards size, not efficiency).
      risk_shape  — defined risk AND how SMALL that defined max loss is vs full assignment (a
                    $17k 'defined' loss against a $21k assignment is barely a fix; a $1.2k one is
                    a real one) — undefined tails score low but not zero.
      recovery    — the change in P(back to breakeven) vs holding, in percentage points.
      market_fit  — does the NEW position's stance agree with what's actually happening —
                    P(touch), trend, VRP, the chart pattern, IV term/skew (all already computed
                    upstream for the recoverability read, but until now never touched WHICH repair
                    wins)? Rewards capping/converting risk when the tape says de-risk; rewards
                    riding the move only when a structure is explicitly built to (cover/collar/
                    wheel); penalizes structures that do neither while the read is stressed.

    Composite = 0.35 edge + 0.30 risk_shape + 0.20 recovery + 0.15 market_fit → sets `desk_score` +
    `score_breakdown` on every alt (mutates in place) and derives `menu['desk_recommendation']`:
    ONE named pick with computed reasons, the runner-up, and anything clearly dominated worth
    avoiding. Best-effort / self-contained — callers should wrap in try/except so a bug here never
    costs the underlying (already-correct) alternatives.
    """
    alts = menu.get("alternatives") or []
    rec = menu.get("recoverability") or {}
    assign = menu.get("assignment") or {}
    hold_row = next((a for a in alts if a.get("category") == "hold"), None)
    close_row = next((a for a in alts if a.get("category") == "exit"), None)
    if hold_row is None:
        return menu
    hold_ev = hold_row.get("ev") or 0.0
    hold_ml = hold_row.get("max_loss")
    cap_ref = max(float(assign.get("assignment_capital") or 0.0), 500.0)

    posture = rec.get("posture")
    p_touch = rec.get("p_touch")
    trend = rec.get("trend_pct")
    vrp = rec.get("vrp_pct")
    right = menu.get("short_right")
    ivx = menu.get("iv_structure") or {}
    pattern = (menu.get("context") or {}).get("pattern") or {}
    # Earnings: days to the NEXT print (None if unknown / already reported). Only a leg that OUTLIVES it is exposed.
    earn = (menu.get("context") or {}).get("earnings") or {}
    earn_days = earn.get("days") if isinstance(earn.get("days"), (int, float)) and earn.get("days") >= 0 else None
    earn_date = earn.get("date")
    earn_when = ((f"{earn_date} ({int(earn_days)}d out)" if earn_date else f"in {int(earn_days)}d")
                 if earn_days is not None else None)
    cur_dte = menu.get("dte_days")
    spot_px = float(menu.get("spot") or 0.0)
    em = (menu.get("event") or {}).get("implied_move_pct")       # market-implied ±1σ earnings move, % (None if not isolable)
    adverse_up = (right == "C")                          # the move that HURTS this tested short
    trending_adverse = bool(trend is not None and ((trend > 8 and adverse_up) or (trend < -8 and not adverse_up)))
    stressed = bool((p_touch or 0) >= 55 or trending_adverse or (vrp is not None and vrp <= -20) or posture == "tested")
    acute = bool((p_touch or 0) >= 85)                    # not just "worth watching" — actively likely to breach THIS cycle

    def _clip(x, lo=0.0, hi=100.0):
        return max(lo, min(hi, x))

    def _undefined_risk():
        """An OPEN tail isn't a fixed penalty — it's exactly as risky as the odds it gets tested.
        A naked far-OTM short with P(touch) 12% is what the whole premium-selling thesis IS; the
        SAME open tail at P(touch) 67% is a live problem. Scale off P(touch) (falls back to 50,
        i.e. no opinion, when it isn't available) — this is what stops a healthy/quiet trade from
        scoring an unnecessary 'fix' as better than Hold just because Hold is technically open-ended."""
        return _clip(100.0 - float(p_touch if p_touch is not None else 50.0))

    def _core(d_ev, d_pop, max_loss, defined):
        capital = max(abs(max_loss) if (defined and max_loss is not None) else cap_ref, 500.0)
        edge = _clip(50.0 + (float(d_ev or 0.0) / capital) * 400.0)
        risk = _clip(100.0 - 100.0 * min(abs(max_loss) / cap_ref, 1.0)) if (defined and max_loss is not None) else _undefined_risk()
        recov = _clip(50.0 + float(d_pop) * 2.0) if d_pop is not None else 50.0
        return edge, risk, recov

    def _event(a):
        """EARNINGS risk carried by an alternative's legs → (points to subtract from its composite, why).

        Earnings is a binary JUMP that the diffusion-vol EV/PoP model does not price, so any leg that outlives the
        print carries risk the numbers understate — and it is a DIRECT deduction (not folded into the 15%-weight
        market-fit axis, where 20 fit-points is worth 3 composite points and changes nothing):
          · a NAKED short leg spanning the print   −15 (call: unbounded)  / −10 (put, covered call)
          · a short leg capped by a same-right wing −6   (a gap can still hit the max loss)
          · a CALENDAR whose long far leg spans it  −10  (you pay event vol, and once the near leg expires you are
                                                          holding a directional call through the print — an earnings
                                                          bet, not a defense)"""
        if earn_days is None:
            return 0.0, None
        opt = [l for l in (a.get("legs") or []) if l.get("right") in ("P", "C") and l.get("dte_days") is not None]
        shorts = [l for l in opt if l.get("action") == "SELL" and l["dte_days"] >= earn_days]
        if shorts:
            def _capped(s):
                return any(x.get("action") == "BUY" and x.get("right") == s.get("right") and x["dte_days"] >= s["dte_days"] for x in opt)
            if all(_capped(s) for s in shorts):
                # How exposed is the wing? That depends on where the short sits relative to what the PRINT alone can do:
                # P(event move takes it out) = 1 − Φ(distance ÷ implied move). A short at the money is a coin-flip on the
                # gap; one well outside the implied move barely cares. 6 pts is the floor (a gap can still hit the max).
                s0 = min(shorts, key=lambda x: abs(spot_px - float(x["strike"]))) if spot_px > 0 else shorts[0]
                if em and spot_px > 0:
                    dist = abs(spot_px - float(s0["strike"])) / spot_px * 100.0
                    p_ev = 1.0 - _norm_cdf(dist / float(em))
                    side = "below" if s0.get("right") == "P" else "above"
                    return 6.0 + min(10.0, 20.0 * p_ev), (
                        f"a short leg spans earnings {earn_when} — the ${float(s0['strike']):.0f} short sits {dist:.1f}% {side} spot "
                        f"vs a ±{float(em):.0f}% market-implied earnings move (≈{p_ev * 100:.0f}% odds the print alone takes it out); "
                        "the wing caps the loss, but a gap can still hit the max")
                return 6.0, f"a short leg spans earnings {earn_when} — its wing caps the loss, but a gap can still hit the max"
            has_stock = any(l.get("right") == "STK" for l in (a.get("legs") or []))
            naked_call = (not has_stock) and any(s.get("right") == "C" and not _capped(s) for s in shorts)
            return (15.0 if naked_call else 10.0), (
                f"a NAKED short {'call' if naked_call else 'leg'} spans earnings {earn_when} — a binary gap can leap the strike, "
                "and the model's diffusion vol understates it")
        if a.get("category") == "calendar" and any(l.get("action") == "BUY" and l["dte_days"] >= earn_days for l in opt):
            return 10.0, (f"the long far leg spans earnings {earn_when} — you pay event vol and, once the near leg expires, hold a "
                          "directional call through the print: an earnings bet, not a defense")
        return 0.0, None

    def _market_fit(a):
        cat, grp, legs = a.get("category"), a.get("group"), (a.get("legs") or [])
        fit, notes = 50.0, []
        rm = a.get("roll_meta")
        if rm:
            # A roll's market fit IS the roll search's structure read: does the new strike sit BEYOND the
            # levels the stock struggles to cross (support/resistance · POC · gamma flip/wall), and how many σ
            # of cushion does it have over ITS OWN horizon? Both are already time-decayed for far expiries
            # (more days → more chance to drift across a level), so a long-dated roll can't win on premium alone.
            sc = rm.get("scores") or {}
            st = rm.get("structure") or {}
            fit = (0.6 * float(sc["structure"] if sc.get("structure") is not None else 50.0)
                   + 0.4 * float(sc["cushion"] if sc.get("cushion") is not None else 50.0))
            if st.get("levels_available"):
                notes.append(f"strike clears {st.get('cleared_count', 0)} of {st['levels_available']} structural levels "
                             "(support/resistance · POC · gamma)")
            # (earnings spans are charged once, for EVERY alternative, by `_event` — not per-source here)
        has_put_hedge = any(l.get("right") == "P" and l.get("action") == "BUY" for l in legs)
        collars = cat == "cover" and has_put_hedge
        rides = (cat == "cover" and not has_put_hedge) or cat == "assignment"
        caps = cat in ("defined_risk", "calendar", "butterfly") or collars    # payoff-SHAPE sense (pattern-fit below)
        near_term_cap = cat == "defined_risk" or collars     # actually changes/caps THIS expiry's exposure now
        duration_play = cat == "calendar"                    # caps the EVENTUAL tail; the near leg's breach risk
                                                               # THIS cycle is untouched — not an urgent-de-risk move
        replaces = grp == "replace"
        naked_add = cat in ("overlay", "roll") and not a.get("defined_risk")
        if stressed:
            if near_term_cap or replaces:
                fit += 18; notes.append("caps or removes the open tail while the read is stressed")
            if duration_play:
                # A calendar bounds the EVENTUAL tail but leaves THIS cycle's breach risk untouched, and its only
                # edge is the term-structure read scored below — so it earns no blanket "measured response" credit.
                if acute:
                    fit -= 12; notes.append("flattens the EVENTUAL tail, but doesn't reduce THIS cycle's breach risk — not the first move when P(touch) is this extreme")
            if rides:
                fit += 8; notes.append("converts the adverse move into upside instead of just absorbing it")
            if naked_add:
                fit -= 18; notes.append("adds/keeps an undefined tail while P(touch)/trend/VRP say de-risk")
            if cat == "overlay" and vrp is not None and vrp <= -20:
                fit -= 8; notes.append("sells more premium while implied vol is already under-priced")
            if acute and near_term_cap:
                fit += 8; notes.append("P(touch) is acute — moving/capping the CURRENT strike now is the priority")
        elif cat == "hedge":
            fit -= 8; notes.append("a benign read doesn't need a delta-hedge yet")
        if posture == "healthy" and not stressed and (caps or replaces or rides):
            fit -= 20; notes.append("posture is healthy — this isn't needed yet; it would just add cost/complexity to a winning trade")
        if cat == "calendar" and not ivx.get("far_spans_earnings"):
            # (skipped when the back month spans earnings: its IV then carries event variance, so front-vs-back
            #  is not a clean term-structure read in either direction)
            tl = ivx.get("term_label")
            if tl == "front rich (backwardated)":
                fit += 10; notes.append("term structure favors selling the front (backwardated)")
            elif tl == "front cheap (contango)":
                fit -= 10; notes.append("term structure argues against a calendar (contango)")
        if collars:
            sk = ivx.get("skew_label") or ""
            if "put skew" in sk:
                fit -= 5; notes.append("the protective put is dear (put skew rich)")
            elif "call skew" in sk:
                fit += 5; notes.append("the protective put is cheap (call skew)")
        pdir, pstat = pattern.get("direction"), pattern.get("status")
        if pdir and pstat in ("forming", "confirmed"):
            adverse_pattern = (pdir == "bullish") if adverse_up else (pdir == "bearish")
            if adverse_pattern and (caps or replaces):
                fit += 6; notes.append(f"a {pdir} {pattern.get('type', 'pattern')} argues for capping/replacing now")
            elif (not adverse_pattern) and (rides or replaces):
                fit += 6; notes.append(f"a {pdir} {pattern.get('type', 'pattern')} argues for riding / redeploying this way")
        return _clip(fit), notes

    def _score(a):
        d_ev, max_loss, defined = a.get("d_ev"), a.get("max_loss"), bool(a.get("defined_risk"))
        edge, risk, recov = _core(d_ev, a.get("d_pop"), max_loss, defined)
        fit, notes = _market_fit(a)
        ev_pts, ev_note = _event(a)
        composite = max(0, round(0.35 * edge + 0.30 * risk + 0.20 * recov + 0.15 * fit - ev_pts))
        a["desk_score"] = composite
        a["score_breakdown"] = {"edge": round(edge), "risk": round(risk), "recovery": round(recov), "market_fit": round(fit)}
        a["event_risk"] = {"points": ev_pts, "note": ev_note} if ev_pts else None      # shown as a ⚠ chip on the card
        a["_fit_notes"] = ([ev_note] if ev_note else []) + notes                       # the print leads the reasons

    for a in alts:
        if a.get("category") not in ("hold", "exit"):
            _score(a)

    # HOLD & CLOSE sit in the SAME scoring system — the honest pick can legitimately be either.
    risk_h = _clip(100.0 - 100.0 * min(abs(hold_ml) / cap_ref, 1.0)) if hold_ml is not None else _undefined_risk()
    fit_h = (72.0 if posture == "healthy" else
             22.0 if (posture == "tested" and (menu.get("dte_days") or 99) <= 7) else
             38.0 if stressed else 50.0)
    ev_h, ev_h_note = _event(hold_row)                     # does the CURRENT expiry itself run through the print?
    hold_row["desk_score"] = max(0, round(0.35 * 50 + 0.30 * risk_h + 0.20 * 50 + 0.15 * fit_h - ev_h))
    hold_row["score_breakdown"] = {"edge": 50, "risk": round(risk_h), "recovery": 50, "market_fit": round(fit_h)}
    hold_row["event_risk"] = {"points": ev_h, "note": ev_h_note} if ev_h else None

    if close_row is not None:
        unreal = close_row.get("ev")
        d_ev_close = (float(unreal) - hold_ev) if unreal is not None else 0.0
        edge_c = _clip(50.0 + (d_ev_close / cap_ref) * 400.0)
        # Closing is judged by the SAME rule as every other defined-risk row: its "max loss" is the loss it LOCKS IN.
        # It used to get a free 100 on the risk axis (zero forward risk) while a rolled spread — whose worst case is
        # bounded and also priced — was charged for it, so Close structurally beat every fix by ~10 points.
        # A close that KEEPS the shares (a covered call bought back) still carries the shares' own tail — its worst case
        # is the share position's max loss, not just the option loss it locks in.
        locked = (abs(float(close_row["max_loss"]))
                  if (close_row.get("keeps_shares") and close_row.get("max_loss") is not None)
                  else abs(min(float(unreal or 0.0), 0.0)))
        risk_c = _clip(100.0 - 100.0 * min(locked / cap_ref, 1.0))
        # Stress argues for closing only when there is a tail worth escaping. A defined-risk position's downside is
        # already bounded (and priced in); closing it forfeits the remaining premium and the recovery.
        fit_c = (65.0 if hold_ml is None else 45.0) if stressed else 25.0
        # …and its RECOVERY axis the same way: every other row is scored on P(all-in P&L ≥ 0) vs holding, and closing a
        # loss means that probability is 0 — you can never get back to profit. It was handed a flat 55 (above the 50
        # baseline!) for foreclosing the recovery that Hold (54%) and every roll (58–70%) were being credited with.
        # A plain close is CERTAIN (100 if it locks in a gain, 0 if a loss). A close that KEEPS the shares is not — the
        # share position still moves after the buy-back — so it is scored on its OWN P(all-in P&L ≥ 0), like every row.
        own_pop = close_row.get("pop_pct") if close_row.get("keeps_shares") else None
        close_pop = float(own_pop) if own_pop is not None else (100.0 if float(unreal or 0.0) >= 0 else 0.0)
        hold_pop_v = hold_row.get("pop_pct")
        recov_c = (_clip(50.0 + 2.0 * (close_pop - float(hold_pop_v))) if hold_pop_v is not None
                   else (_clip(close_pop) if own_pop is not None else (100.0 if close_pop else 35.0)))
        close_row["desk_score"] = round(0.35 * edge_c + 0.30 * risk_c + 0.20 * recov_c + 0.15 * fit_c)
        close_row["score_breakdown"] = {"edge": round(edge_c), "risk": round(risk_c), "recovery": round(recov_c), "market_fit": round(fit_c)}

    ranked = sorted([a for a in alts if a.get("desk_score") is not None], key=lambda a: a["desk_score"], reverse=True)
    if not ranked:
        return menu
    winner = ranked[0]
    # INCOME POLICY — this is a premium-selling desk, and "close it and book the loss" is the lazy answer on a defined-risk
    # income trade. A fix is INCOME-VIABLE when it (1) needs no new money (even or credit), (2) keeps the risk defined (or
    # covered), (3) costs no material expected value, and (4) does NOT make the payoff worse than holding — worst case,
    # probability of profit. Such a fix is a PARETO improvement on what you already own (a lower break-even, a better best
    # case, the same bounded worst case) for free, so it is preferred over Hold/Close whenever the position needs defending
    # — unless it scores more than INCOME_VIABLE_SLACK points below the top benchmark (i.e. it is genuinely a worse
    # idea, which is what the composite is for). With no such fix — a naked call whose only options are a debit cap or an
    # open-tail roll — there is nothing to prefer and the composite decides (Close stands).
    income_note, income_override = None, None
    hold_pop = hold_row.get("pop_pct")
    material = max(100.0, 0.005 * cap_ref)

    def _income_viable(a):
        if a.get("category") in ("hold", "exit") or a.get("group") == "replace":
            return False
        if float(a.get("net_cash") or 0.0) < -1.0:
            return False
        if not (a.get("defined_risk") or menu.get("covered")):
            return False
        if a.get("d_ev") is not None and float(a["d_ev"]) < -material:
            return False
        if a.get("d_pop") is not None and float(a["d_pop"]) < -2:
            return False
        if hold_ml is not None and a.get("max_loss") is not None and float(a["max_loss"]) < float(hold_ml) - 0.05 * abs(float(hold_ml)):
            return False                                                   # a materially WORSE worst case isn't "defending"
        return True

    def _horizon_days(a):
        rm = a.get("roll_meta") or {}
        if rm.get("dte") is not None:
            return int(rm["dte"])
        dtes = [int(l["dte_days"]) for l in (a.get("legs") or []) if l.get("dte_days") is not None]
        return max(dtes) if dtes else 0

    if stressed and winner.get("category") in ("hold", "exit"):
        pool = [a for a in ranked if _income_viable(a)]
        # NEAREST SUFFICIENT: among the viable fixes that are about equally good, the one that extends the trade LEAST wins.
        # Every extra week is more time for the stock to cross the strike and more event exposure, and a far-dated roll
        # for a sliver of credit isn't income — it is "bank the loss and hide" (a $53 best case over 92 days at risk).
        viable = None
        if pool:
            top = pool[0]["desk_score"]
            viable = min((a for a in pool if a["desk_score"] >= top - NEAREST_SUFFICIENT), key=lambda a: (_horizon_days(a), -a["desk_score"]))
        if viable is not None and (winner["desk_score"] - viable["desk_score"]) <= INCOME_VIABLE_SLACK:
            gap = winner["desk_score"] - viable["desk_score"]
            was = "closing" if winner.get("category") == "exit" else "doing nothing"
            fallback = "Close" if winner.get("category") == "exit" else "Hold"
            income_note = (f"Income policy: {was} scores {winner['desk_score']} ({max(gap, 0)} point{'s' if gap != 1 else ''} ahead of this fix), but that score "
                           "has no term for the premium you keep collecting. This fix costs no new money, keeps the risk defined and "
                           f"improves the payoff you already hold — on an income trade that beats {was}. {fallback} stays the fallback.")
            income_override = {"over": winner.get("name"), "over_score": winner["desk_score"], "gap": max(gap, 0)}
            winner = viable

    # The mirror image: a fix that is NOT income-viable (it costs new money, or leaves your odds worse than holding) is a
    # gamble against the cleanest de-risk there is — closing removes the tail outright. It has to beat Close clearly,
    # not by a coin-flip (a naked call's "roll down + protection" edged Close by 2 points on a worse P(profit)).
    close_note = None
    if (close_row is not None and winner is not close_row and not income_override
            and winner.get("category") not in ("hold", "exit") and not _income_viable(winner)
            and (winner["desk_score"] - close_row["desk_score"]) < NONVIABLE_MARGIN):
        # …and the fallback is the BETTER of the two benchmarks: when holding outscores closing, rejecting the fix must not
        # hand the pick to Close (AAOI: a wider roll 62, Hold 60, Close 57 → "Close" — on an income desk, backwards).
        bench = hold_row if (hold_row.get("desk_score") or 0) > (close_row.get("desk_score") or 0) else close_row
        why_not = ('costs new money' if float(winner.get('net_cash') or 0.0) < -1.0 else
                   ('lowers your odds of profit (' + format(int(winner['d_pop']), '+d') + 'pt)' if (winner.get('d_pop') or 0) < 0 else
                    ('raises the worst case ' + format(float(hold_ml), '+,.0f') + ' → ' + format(float(winner['max_loss']), '+,.0f')
                     if (hold_ml is not None and winner.get('max_loss') is not None and float(winner['max_loss']) < float(hold_ml) - 0.05 * abs(float(hold_ml)))
                     else 'does not improve the payoff you hold')))
        close_note = (f"{'Holding' if bench is hold_row else 'Closing'} scores {bench['desk_score']} — within {NONVIABLE_MARGIN} of "
                      f"{winner.get('name')} ({winner['desk_score']}), which {why_not}; a fix like that has to beat "
                      f"{'holding' if bench is hold_row else 'closing'} clearly, and it didn't.")
        winner = bench

    # When the pick is a BENCHMARK (hold/close), the useful runner-up is the best way to KEEP the trade alive —
    # the desk's stated focus is fixing the current trade, not just exiting it — so skip past the other benchmark.
    if winner.get("category") in ("hold", "exit"):
        # …an actual FIX (group 'adjust'), not a close-and-redeploy; and none at all for a plain Hold on a
        # trade that isn't stressed (nothing to keep alive — the panel is monitoring only).
        runner = (None if (winner.get("category") == "hold" and not stressed) else
                  next((a for a in ranked[1:] if a.get("category") not in ("hold", "exit") and a.get("group") != "replace"), None))
        runner_role = "fix"
    else:
        runner = next((a for a in ranked if a is not winner), None)      # (the income override can put winner at ranked[k>0])
        runner_role = "next"
    # "Avoid" means a MATERIAL deficit — a re-timed fair-value roll that is $1 worse than holding is noise, not a
    # trap. Flag only an open-tail alternative that costs real E[P&L] (≥ $100 or 0.5% of notional) or gives up
    # real recovery odds (≥ 8 points) versus simply holding.
    material_ev = max(100.0, 0.005 * cap_ref)
    dominated = [a for a in ranked if a is not winner and a.get("category") not in ("hold", "exit", "hedge")
                 and not a.get("defined_risk")
                 and ((a.get("d_ev") or 0) <= -material_ev or (a.get("d_pop") or 0) <= -8)]

    iv_pct, hv_pct = rec.get("iv_pct"), rec.get("hv_pct")
    tail_word = "call" if right == "C" else "put"
    rolls = [a for a in ranked if a.get("roll_meta")]          # already best-first

    def _roll_line():
        """Where the live roll search's best candidate landed, and WHY — so the pick can never contradict the
        search the way two separate sections did: a credit roll re-times the exposure, it doesn't shrink it."""
        if not rolls:
            return None
        b = rolls[0]
        rm = b["roll_meta"]
        d_ev = b.get("d_ev")
        fair = d_ev is not None and abs(d_ev) <= 0.02 * cap_ref
        tail = "defined risk" if b.get("defined_risk") else "tail stays open"
        p_txt = f"{float(rm['p_otm']):.0f}% P(OTM)" if rm.get("p_otm") is not None else "credit-only"
        return (f"Best credit roll — {b['name']} ({b['desk_score']}/100, #{ranked.index(b) + 1} of {len(ranked)}): "
                f"{p_txt}, "
                + (f"but ΔE[P&L] is only {d_ev:+,.0f} vs holding — a fair-value credit that re-times the risk rather than "
                   f"reducing it ({tail})." if fair else
                   f"ΔE[P&L] {d_ev:+,.0f} vs holding ({tail})." if d_ev is not None else f"{tail}."))

    def _vs_hold_line(a):
        """The all-in story against what you hold today, in numbers: worst case, best case, break-even, P(profit)."""
        bits = []
        if a.get("max_loss") is not None and hold_ml is not None:
            bits.append(f"worst case {float(hold_ml):+,.0f} → {float(a['max_loss']):+,.0f}")
        if a.get("max_gain") is not None and hold_row.get("max_gain") is not None:
            bits.append(f"best case {float(hold_row['max_gain']):+,.0f} → {float(a['max_gain']):+,.0f}")
        hb, ab = hold_row.get("breakevens") or [], a.get("breakevens") or []
        if hb and ab:
            k = -1 if right == "P" else 0                                     # the break-even on the tested side
            bits.append(f"break-even ${float(hb[k]):,.2f} → ${float(ab[k]):,.2f}")
        if a.get("pop_pct") is not None and hold_pop is not None:
            bits.append(f"P(profit) {hold_pop}% → {a['pop_pct']}%")
        return ("Versus holding — " + " · ".join(bits)) if bits else None

    def _why(a, *, full=False):
        out = []
        cat, rm = a.get("category"), a.get("roll_meta")
        if cat == "hold":
            if a is not winner:                                  # Hold as the FALLBACK to a fix: say what it keeps and what it forgoes
                bits = [f"keeps the position exactly as it is (worst case {float(hold_ml):+,.0f})" if hold_ml is not None else
                        "keeps the position exactly as it is (open tail)"]
                hb = hold_row.get("breakevens") or []
                if hb:
                    bits.append(f"break-even stays ${float(hb[-1 if right == 'P' else 0]):,.2f}")
                if hold_pop is not None:
                    bits.append(f"P(profit) stays {hold_pop}%")
                out.append("Do nothing: " + ", ".join(bits) + " — no trade, no cost, but none of the improvement.")
            elif stressed:
                free = [x for x in rolls if float(x.get("net_cash") or 0.0) >= -1.0]
                if free:
                    out.append("No fix clears the bar right now — the rolls that cost no new money all raise the worst case (a wider "
                               "wing) or hand the tail to the stock (a cash-secured put), and closing "
                               f"({close_row['desk_score'] if close_row is not None else '—'}/100) isn't clearly better — so holding keeps "
                               "the defined-risk position in place while you watch it. The goals below show what each path costs.")
                else:
                    out.append("No fix clears the bar right now — no roll at even cost or better that keeps the risk defined, and closing "
                               f"({close_row['desk_score'] if close_row is not None else '—'}/100) isn't clearly better — so holding keeps the "
                               "defined-risk position in place while you watch it.")
                an = menu.get("anatomy") or {}
                th = (an.get("net") or {}).get("theta_day")
                if th is not None and th < -0.5:
                    out.append(f"But this is a hold for a BOUNCE, not for decay: time costs ≈${abs(th):,.0f}/day at a flat price "
                               "(the long leg's time value is bleeding faster than the short's).")
                pin = an.get("pin") or {}
                if (pin.get("p_pct") or 0) >= 10:
                    out.append(f"Expiry plan: if it is between ${pin['low']:,.0f} and ${pin['high']:,.0f} into the final close "
                               f"(≈{pin['p_pct']}% odds), close or roll it — don't carry assigned shares with no protection.")
            else:
                out.append("Nothing on the menu beats simply holding — its own recovery odds / E[P&L] are already best-in-class.")
            if ev_h_note:
                out.append(f"Note: this expiry itself runs through earnings {earn_when} — holding takes the print.")
        elif cat == "exit":
            unreal = float(a.get("ev") or 0.0)
            if a.get("keeps_shares"):
                opt_pnl = float(menu.get("unrealized_pnl") or 0.0)      # the OPTION's P&L — `ev` here also carries the shares' drift
                out.append(f"Locks in {'a gain of' if opt_pnl >= 0 else 'the loss of'} {abs(opt_pnl):,.0f} on the option with certainty "
                           "and releases the shares from the cap — they keep their own downside.")
            else:
                out.append(f"Locks in {'a gain of' if unreal >= 0 else 'the loss of'} {abs(unreal):,.0f} with certainty and removes the tail entirely.")
            if ev_h_note:
                out.append(f"Also removes the exposure to earnings {earn_when}, which this expiry runs through.")
            if hold_ml is None:
                out.append(f"Holding leaves an UNBOUNDED tail (naked short {tail_word}) on ${cap_ref:,.0f} of notional.")
            if vrp is not None and vrp <= -10:
                out.append(f"Premium is {abs(vrp):.0f}% under-priced (IV {iv_pct}% vs HV {hv_pct}%) — you aren't paid to carry it, "
                           "and any roll re-sells more time at that same sub-realized vol.")
            K_c = float(menu.get("short_strike") or 0.0)
            if K_c and spot_px and ((spot_px < K_c) if right == "P" else (spot_px > K_c)):
                out.append(f"The ${K_c:.0f} short is already ${abs(spot_px - K_c):.2f} in the money "
                           f"({(assign or {}).get('p_itm')}% odds it finishes there).")
            elif p_touch is not None and p_touch >= 40:
                out.append(f"{p_touch:.0f}% chance the strike is breached before expiry.")
        else:
            vh = _vs_hold_line(a) if (full and a is winner) else None
            if rm and not vh:
                out.append(f"net credit {rm['credit_total']:+,.0f} (no new money) · new breakeven ${float(rm['new_breakeven']):,.2f}"
                           if rm.get("new_breakeven") is not None else f"net credit {rm['credit_total']:+,.0f} (no new money)")
            if rm and rm.get("p_otm") is not None:
                out.append(f"{float(rm['p_otm']):.0f}% {rm.get('p_otm_source') or ''} P(OTM) over {rm['dte']}d".replace("  ", " "))
            d_ev, d_pop, d_ml = a.get("d_ev"), a.get("d_pop"), a.get("d_max_loss")
            if vh:
                # the pick's all-in story in ONE line (worst/best case, break-even, P(profit)) — the steps block above already
                # shows net cash and the legs, so the net credit / break-even / max-loss bullets would only repeat it
                out.append(vh)
                if d_ev is not None:
                    out.append(f"Expected P&L {d_ev:+,.0f} vs holding — a fair-value re-timing; the gain is in the shape of the payoff, not in expected value.")
                if not a.get("defined_risk"):
                    out.append("tail stays open — weighed against the edge above")
            else:
                if d_ev is not None:
                    out.append(f"E[P&L] {d_ev:+,.0f} vs holding")
                if a.get("defined_risk") and d_ml is not None:
                    out.append(f"max loss {a.get('max_loss'):,.0f} ({d_ml:+,.0f} vs holding)")
                elif not a.get("defined_risk"):
                    out.append("tail stays open — weighed against the edge above")
                if d_pop is not None:
                    out.append(f"recovery odds {d_pop:+}pt vs holding")
        if cat in ("hold", "exit") and full and a is winner and close_row is not None and stressed:
            other = close_row if cat == "hold" else hold_row
            if abs(float(a["desk_score"]) - float(other["desk_score"])) <= 3:
                c_ev = float(close_row.get("ev") or 0.0)
                same = abs(hold_ev - c_ev) <= max(25.0, 0.03 * abs(c_ev))
                out.append(f"Hold and Close are a coin-flip on the score ({hold_row['desk_score']} vs {close_row['desk_score']})"
                           + (f" and have about the same expected P&L ({hold_ev:+,.0f} vs {c_ev:+,.0f})" if same else
                              f"; expected P&L {hold_ev:+,.0f} holding vs {c_ev:+,.0f} closing")
                           + f". It comes down to appetite: keep the {hold_pop}% chance of getting back to profit (and carry whatever is "
                           "ahead), or lock the loss now and take the tail off.")
        out.extend((a.get("_fit_notes") or [])[:2])
        if full and not rm:
            rl = _roll_line()                                   # a non-roll pick must still answer "but the roll search said…"
            if rl:
                out.append(rl)
        if full and a is winner and (income_note or close_note):
            out.insert(0, income_note or close_note)            # why the pick is (or isn't) simply "Close"
        return out

    def _avoid_why(a):
        d_ev, d_pop = a.get("d_ev"), a.get("d_pop") or 0
        return f"E[P&L] {d_ev:+,.0f} and recovery odds {d_pop:+}pt vs holding, with the tail still open"

    def _short_exp(iso):
        try:
            d = dt.date.fromisoformat(str(iso)[:10])
            return f"{d.strftime('%b')} {d.day}"
        except (ValueError, TypeError):
            return str(iso)

    def _timing():
        """WHEN to act — the part a roll search can't say. Read off what the market expects: the earnings print (and the
        move the term structure prices for it), IV vs realized vol, the term structure, and what waiting costs.
        Deterministic; every sentence cites a number that is on screen."""
        pts: list[str] = []
        verdict, title, act_by = "neutral", "No timing edge — act when the trade needs it", None
        early = bool((assign or {}).get("early_assignment_risk"))
        urgent = bool(early or acute or (posture == "tested" and cur_dte is not None and cur_dte <= 3))
        cow = menu.get("cost_of_waiting") or []
        c5 = next((c for c in cow if c.get("in_trading_days") == 5), None)

        if posture == "healthy" and not stressed:
            return {"verdict": "hold", "title": "Nothing to time — leave it",
                    "points": ["The trade is healthy; there is no defense to schedule. Re-check if P(touch), the cushion or an earnings date changes."],
                    "act_by": None, "vol": None}

        an = menu.get("anatomy") or {}
        th_day = (an.get("net") or {}).get("theta_day")
        K_t = float(menu.get("short_strike") or 0.0)
        itm = bool(K_t and spot_px and ((spot_px < K_t) if right == "P" else (spot_px > K_t)))
        if urgent:
            verdict, title = "now", "Act now — the clock is the risk"
            if itm and th_day is not None and th_day < -0.5:
                title = f"Decide now — every day costs ≈${abs(th_day):,.0f} at a flat price"
            if early:
                pts.append(f"Early-assignment risk: {(assign or {}).get('early_reason')}.")
            if acute and itm:
                pts.append(f"The ${K_t:.0f} short is already ${abs(spot_px - K_t):.2f} in the money (P(finish ITM) "
                           f"{(assign or {}).get('p_itm')}%) — this is no longer about whether it gets tested, but what to do with it.")
            elif acute:
                pts.append(f"P(touch) {float(p_touch):.0f}% — the strike is very likely to be breached this cycle.")
            if itm and th_day is not None and th_day < -0.5:
                pts.append(f"Time is against this position (≈${abs(th_day):,.0f}/day at a flat price): waiting only pays if the stock moves your way.")
            if cur_dte is not None and cur_dte <= 3:
                pts.append(f"{cur_dte}d left — gamma and pin risk accelerate into expiry; there is no time to be patient.")
        if c5 and rec.get("recovery_score") is not None and c5.get("recovery_pop") is not None:
            drop = float(rec["recovery_score"]) - float(c5["recovery_pop"])
            if drop >= 5:
                pts.append(f"Waiting 5 trading days cuts recovery odds {rec['recovery_score']}% → {c5['recovery_pop']}%"
                           + (f" and E[P&L] to {c5['expected_pnl']:+,.0f}" if c5.get("expected_pnl") is not None else "") + " — the odds erode with the clock.")

        move_txt = f" — the term structure prices a ±{float(em):.0f}% move" if em else ""
        if earn_days is not None and cur_dte is not None:
            try:
                act_by = (dt.date.today() + dt.timedelta(days=max(int(earn_days) - 1, 0))).isoformat()
            except Exception:  # noqa: BLE001
                act_by = None
            if earn_days > cur_dte:                                           # the print lands AFTER this expiry
                if verdict != "now":
                    verdict, title = "before_print", f"Window: now → {_short_exp(act_by)} — earnings {_short_exp(earn_date)} ({int(earn_days)}d)"
                pts.append(f"Earnings {earn_when} land AFTER this expiry{move_txt}. Holding isn't exposed — but anything you roll beyond "
                           "it is a bet on the print, and back-month IV carries the event, which is exactly why an even-cost roll looks so easy.")
                # the nearest roll through the print shows it best: the event is the biggest share of its premium
                waits = sorted((a for a in rolls if a.get("wait") and (a.get("roll_meta") or {}).get("spans_earnings")),
                               key=lambda a: int(a["roll_meta"].get("dte") or 0))
                if waits:
                    wa = waits[0]["wait"]
                    ns = wa.get("new_short") or {}
                    if ns and wa.get("cost_flat") is not None:
                        leg = f"${float(ns['strike']):.0f} {'put' if right == 'P' else 'call'} in {_short_exp(waits[0]['roll_meta']['expiry'])}"
                        rises = float(ns["iv_later_pct"]) - float(ns["iv_now_pct"]) >= 1.0
                        tail = (f"Rolling then instead of now: {wa['cost_flat']:+,.0f} if the stock goes nowhere"
                                + (" — buying back what you hold costs more by then, too." if wa["cost_flat"] < 0 else "."))
                        if wa["cost_flat"] < 0 and rises:
                            pts.append(f"Waiting for IV to 'rise into earnings' doesn't pay: on the last day before expiry the {leg} would quote "
                                       f"{ns['iv_later_pct']:.0f}% IV ({ns['iv_now_pct']:.0f}% today) yet pay ${ns['premium_later']:.2f} instead of "
                                       f"${ns['premium_now']:.2f} — the event premium stays, the ordinary time value decays. {tail}")
                        elif wa["cost_flat"] < 0:
                            pts.append(f"Waiting doesn't pay: on the last day before expiry the {leg} would pay ${ns['premium_later']:.2f} instead of "
                                       f"${ns['premium_now']:.2f}. {tail}")
                        else:
                            pts.append(f"Waiting pays here: the position you hold is still decaying in your favour, so the same roll on the last "
                                       f"day before expiry nets more ({tail[0].lower() + tail[1:]}) — roll late, before the closing bell.")
                pre = [a for a in rolls if int((a.get('roll_meta') or {}).get('dte') or 0) < earn_days]
                post = [a for a in rolls if int((a.get('roll_meta') or {}).get('dte') or 0) >= earn_days]
                if pre:
                    b = pre[0]
                    pts.append(f"Cleanest: {_short_exp(b['roll_meta']['expiry'])} ({b['roll_meta']['dte']}d) — it ends BEFORE the print "
                               f"(#{ranked.index(b) + 1} of {len(ranked)}, {b['desk_score']}/100).")
                if post:
                    b = winner if (winner in post) else post[0]                # the PICK if it is the one that runs through the print
                    rm_b = b['roll_meta']
                    k_short = float(rm_b['strike'])
                    lead = "Through the print" if b is winner else "Best roll through the print"
                    b_itm = (k_short > spot_px) if right == "P" else (k_short < spot_px)
                    if spot_px > 0 and b_itm:
                        need = abs(k_short - spot_px) / spot_px * 100.0
                        line = (f"{lead}: {_short_exp(rm_b['expiry'])} ({rm_b['dte']}d) — its ${k_short:.0f} short is still {need:.1f}% IN the money: "
                                f"it needs a {'rally' if right == 'P' else 'drop'} of that size by then to finish clear"
                                + (f" (the print alone is priced at ±{float(em):.0f}%)" if em else "") + ".")
                    elif spot_px > 0:
                        dist = abs(spot_px - k_short) / spot_px * 100.0
                        if em:
                            p_ev = (1.0 - _norm_cdf(dist / float(em))) * 100.0
                            inside = dist < float(em)
                            where = (f"INSIDE the move (≈{p_ev:.0f}% odds the print alone takes it out) — that is the price of the even-cost roll"
                                     if inside else f"outside the move (≈{p_ev:.0f}% odds the print alone takes it out)")
                            line = (f"{lead}: {_short_exp(rm_b['expiry'])} ({rm_b['dte']}d{'' if b is winner else ', ' + format(b['desk_score'], 'd') + '/100'}) — its "
                                    f"${k_short:.0f} short sits {dist:.1f}% from spot vs ±{float(em):.0f}% priced: {where}.")
                            if inside:
                                clear = [a for a in post if a is not b and bool(a.get('defined_risk')) == bool(b.get('defined_risk'))
                                         and (a.get('roll_meta') or {}).get('route') == rm_b.get('route')
                                         and abs(spot_px - float(a['roll_meta']['strike'])) / spot_px * 100.0 >= float(em)
                                         and ((float(a['roll_meta']['strike']) < spot_px) if right == 'P' else (float(a['roll_meta']['strike']) > spot_px))]
                                if clear:
                                    c = clear[0]
                                    line += (f" A roll that clears the move exists — {_short_exp(c['roll_meta']['expiry'])} ${float(c['roll_meta']['strike']):.0f} "
                                             f"({c['roll_meta']['dte']}d) for {float(c['roll_meta']['credit_total']):+,.0f} — but it pays less and ties the position up longer.")
                                else:
                                    line += " No roll at even cost or better clears the move."
                        else:
                            line = (f"{lead}: {_short_exp(rm_b['expiry'])} ({rm_b['dte']}d) — its ${k_short:.0f} short sits {dist:.1f}% from spot "
                                    "(the implied earnings move can't be isolated from this chain).")
                    else:
                        line = f"{lead}: {_short_exp(rm_b['expiry'])} ({rm_b['dte']}d)."
                    pts.append(line + (" The risk is defined, so the worst case is capped; size it as a bet on the print."
                                       if b.get('defined_risk') else ""))
                if rolls and not pre:
                    pts.append("No roll that ends before the print clears the bar — so either sell the print from outside its range, or let this "
                               "expiry run its course and re-establish AFTER the print, when IV has crushed and the gap is already in the price.")
            elif earn_days <= cur_dte:                                        # the print falls INSIDE this trade's life
                if verdict != "now":
                    verdict, title = "before_print", f"Defend before the print — earnings {_short_exp(earn_date)} ({int(earn_days)}d)"
                pts.append(f"Earnings {earn_when} land INSIDE this trade's life. IV is bid into the print and crushes after it, so a roll or close "
                           "done BEFORE it gets the inflated premium; holding through it takes the gap on a model that can't price a jump.")

        iv_p, hv_p = rec.get("iv_pct"), rec.get("hv_pct")
        if iv_p is not None and hv_p is not None and vrp is not None:
            if vrp >= 10:
                pts.append(f"Premium is rich vs realized (IV {iv_p}% vs HV {hv_p}%) — selling more time now is well paid.")
                if verdict == "neutral":
                    verdict, title = "vol_rich", "Good time to roll — premium is rich"
            elif vrp <= -10:
                pts.append(f"Premium is thin vs realized (IV {iv_p}% vs HV {hv_p}%) — re-selling time now is poorly paid; if nothing forces "
                           "you, a vol bump is the better moment to roll.")
                if verdict == "neutral":
                    verdict, title = "vol_cheap", "Poor time to re-sell premium — it's cheap vs realized"
        tl = ivx.get("term_label")
        if tl == "front rich (backwardated)" and ivx.get("near_iv_pct") and ivx.get("far_iv_pct"):
            pts.append(f"Front-month IV {ivx['near_iv_pct']}% is above the back month {ivx['far_iv_pct']}% (backwardation) — rolling OUT sells "
                       "cheaper vol than the front's premium suggests, so credits are thinner than they look.")
        if not pts:
            pts.append("No event, no vol extreme and no clock pressure: nothing argues for acting sooner or later — act when the position needs it.")
        return {"verdict": verdict, "title": title, "points": pts, "act_by": act_by,
                "vol": {"iv_pct": iv_p, "hv_pct": hv_p, "vrp_pct": vrp, "implied_move_pct": em,
                        "earnings_days": (int(earn_days) if earn_days is not None else None)}}

    menu["timing"] = _timing()
    menu["desk_recommendation"] = {
        "name": winner.get("name"), "category": winner.get("category"), "group": winner.get("group"),
        "desk_score": winner.get("desk_score"), "score_breakdown": winner.get("score_breakdown"),
        "policy": ("income" if income_override else None), "policy_over": income_override,
        "reasons": _why(winner, full=True),
        "runner_up": ({"name": runner.get("name"), "desk_score": runner.get("desk_score"), "reasons": _why(runner)[:2],
                       "role": runner_role} if runner else None),
        "avoid": [{"name": a.get("name"), "why": _avoid_why(a)} for a in dominated[:2]],
    }
    for a in alts:
        a.pop("_fit_notes", None)
        a["is_desk_pick"] = (a is winner)
    try:
        from .defend_playbook_service import action_plan, objectives, timing_read
        menu["playbook"] = {"objectives": objectives(menu)}
        menu["action_plan"] = action_plan(menu)
        menu["timing_read"] = timing_read(menu)
    except Exception:  # noqa: BLE001 — the goals are a re-reading of the ranked menu; never cost the menu itself
        menu["playbook"] = menu.get("playbook") or None
    return menu
