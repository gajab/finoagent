"""Concentration Ladder — a multi-year, tax-aware collar LADDER for concentrated long-term stock.

The brief: the user enters, per ticker, the lots they hold (shares + cost basis; ALL lots are
assumed long-term), plus their tax rates. Against LIVE option chains the engine answers
  1. *Is now a good time to put a ladder on?*  (timing verdict from live vol / skew / trend / event / liquidity /
     collar-economics data — plus hard gates for "no gain worth deferring" and "all lots underwater").
  2. *If yes, which ladder?*  Each rung = one expiry: long put floor + short call cap on a tranche of shares, struck
     zero-cost where the chain allows, with the specific lots assigned to each rung chosen TAX-FIRST
     (highest-basis lots are called away first → smallest gain per share), and rungs spread across tax years
     so the gain that lands in each year fits the user's annual budget (or is simply pushed as far out as possible).

Tax model (US, stated up-front so the numbers can be audited — NOT tax advice):
  * Called-away tranche  → long-term gain = (Kc − basis + call premium − put premium) × shares. The call premium is added to
    the sale proceeds; the put premium (paid) is a straddle loss that is *deferred until the stock is disposed* (§1092) so
    it is released against the same sale.
  * Call expires unassigned → the premium is a SHORT-term gain (taxed at the ordinary rate) and the put premium is NOT
    deductible now — it is capitalised into the stock's basis (straddle loss deferral). This is the real cost of a collar
    on an appreciated stock and is why a "zero-cost" collar is not tax-free.
  * Put finishes in-the-money by more than its premium → the net put gain is recognised (long-term if the put is held >1y).
  * Taxes are LINEAR in gains (a loss offsets other gains at the same rate), so expected tax = rate × expected gain and
    the per-year figures add up exactly. The scenario table and the expected values come from ONE function
    (``_rung_after_tax``) so they always reconcile.
  * Probabilities / expectations use the market's risk-neutral density (RND) from the live chain — "what the market
    prices", not a forecast. Falls back to lognormal at the chain's ATM IV when a smile can't be fit.

Constructive-sale (§1259) guard: a collar whose cap-to-floor band is too narrow can be treated as a SALE for tax. We keep
the band ≥ ``MIN_BAND_PCT`` of spot (deepening the floor if necessary) and flag anything tighter. The threshold is a
practitioner rule of thumb, editable below — confirm with a tax adviser.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import numpy as np

from .quote_providers import get_provider, OptionQuote
from .hedging_service import _split_chain, _build_rnd, _atm_iv, _compute_skew, _bs_greeks
from .stock_service import bs_price
from .derivative_income_service import _context_sync, _is_monthly_expiry, _get_sofr, DEFAULT_RISK_FREE

logger = logging.getLogger(__name__)

CONTRACT = 100
MAX_TICKERS = 5
MAX_LOTS = 25
MIN_RUNG_DTE = 150                  # shortest rung; collars shorter than ~5 months are not a "ladder"
LEAP_DAYS = 365                     # a put held longer than this is a long-term position
DEFAULT_FLOOR_PCT = 15.0
MIN_BAND_PCT = 15.0                 # cap-to-floor band (% of spot) below which constructive-sale risk is flagged
MIN_CAP_PCT = 5.0                   # never sell the call closer than +5% (a cap that tight is a sale in all but name)
FLOOR_STEPS = (0.0, 5.0, 10.0)      # how much deeper than the requested floor we may go to restore the band
MAX_SPREAD_PCT = 12.0               # bid/ask spread (% of mid) above which a leg is flagged illiquid
FLEX_SPREAD_VOL_PTS = 1.5           # dealer markup per leg (vol pts) assumed for an INDICATIVE FLEX/OTC quote
FLEX_MAX_YEARS = 5
MAX_CAP_PCT = 60.0                  # FLEX/OTC: never sell the call further out than +60% — beyond it the cap is a fiction
SPREAD_WIDTH_PCT = 20.0             # put-spread: the short (financing) put sits this far below the long put
DEFAULT_CALL_PROB = 0.20            # income structures sell the call at ~this risk-neutral probability of finishing above it
STRESS_PCT = 40.0                   # tail stress used to compare structures (pre-tax loss if the stock falls this much)
DEFAULT_MAX_LOSS_PCT = 30.0         # a structure that loses more than this at the stress is not eligible
# Structure-score weights by stance: income funding of the tax bill / tail protection / upside kept / tax-structure risk.
STANCES = {
    "protect":  {"inc": 0.15, "prot": 0.50, "up": 0.20, "risk": 0.15},
    "balanced": {"inc": 0.30, "prot": 0.30, "up": 0.25, "risk": 0.15},
    "income":   {"inc": 0.50, "prot": 0.15, "up": 0.25, "risk": 0.10},
}
SCENARIO_MOVES = (-40, -30, -20, -10, 0, 10, 20, 30, 40)

# Timing-score weights (sum 100) — one editable place.
W_COLLAR, W_SKEW, W_VRP, W_TREND, W_EVENT, W_LIQ = 35, 15, 10, 20, 10, 10
VERDICT_NOW, VERDICT_STAGGER = 68, 50

DISCLAIMER = ("Planning model, not tax or investment advice. Option taxation (straddle §1092, constructive sale §1259, "
              "wash-sale, state rules) is fact-specific — confirm with a tax adviser before trading.")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _f(x, d: float = 0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else d
    except (TypeError, ValueError):
        return d


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _lin(x: Optional[float], bad: float, good: float) -> float:
    """0 at `bad`, 1 at `good`, linear and clamped (works for either orientation)."""
    if x is None or good == bad:
        return 0.5
    return _clamp01((x - bad) / (good - bad))


@dataclass
class Density:
    """Terminal-price density on a grid: K, pdf, cdf. Wraps a quant_service RND or a lognormal fallback."""
    K: np.ndarray
    pdf: np.ndarray
    cdf: np.ndarray
    source: str                     # "RND" | "lognormal"

    def prob_below(self, level: float) -> float:
        return float(np.interp(level, self.K, self.cdf))

    def prob_above(self, level: float) -> float:
        return 1.0 - self.prob_below(level)

    def expect(self, vals: np.ndarray) -> float:
        """E[g(S_T)] for g evaluated on self.K."""
        return float(np.trapezoid(vals * self.pdf, self.K))


def lognormal_density(spot: float, sigma: float, T: float, r: float, n: int = 801) -> Density:
    sigma = max(sigma, 0.05)
    T = max(T, 1e-3)
    F = spot * math.exp(r * T)
    K = F * np.exp(np.linspace(-0.9, 0.9, n))
    m = math.log(F) - 0.5 * sigma * sigma * T
    s = sigma * math.sqrt(T)
    z = (np.log(K) - m) / s
    pdf = np.exp(-0.5 * z * z) / (K * s * math.sqrt(2 * math.pi))
    pdf = pdf / np.trapezoid(pdf, K)
    cdf = np.concatenate([[0.0], np.cumsum(0.5 * (pdf[1:] + pdf[:-1]) * np.diff(K))])
    return Density(K=K, pdf=pdf, cdf=np.clip(cdf, 0.0, 1.0), source="lognormal")


def density_from_rnd(rnd) -> Density:
    return Density(K=np.asarray(rnd.K, float), pdf=np.asarray(rnd.pdf, float),
                   cdf=np.asarray(rnd.cdf, float), source="RND")


# ---------------------------------------------------------------------------
# lots
# ---------------------------------------------------------------------------

def split_lots(lots: list[dict], spot: float) -> dict:
    """Clean the user's lots, order gain lots HIGHEST-basis first (smallest gain/share — called away first), and set
    underwater lots (basis ≥ spot) aside: there is no gain to defer there, so they are harvest candidates, not ladder fuel.

    Each gain lot is {basis, shares, id}; shares are whole shares as entered."""
    gain, under = [], []
    for i, l in enumerate(lots or []):
        sh = int(_f(l.get("shares")))
        b = _f(l.get("cost_basis"))
        if sh <= 0 or b <= 0:
            continue
        rec = {"id": i, "basis": b, "shares": sh}
        (gain if b < spot else under).append(rec)
    gain.sort(key=lambda x: -x["basis"])
    tot_sh = sum(x["shares"] for x in gain)
    avg = sum(x["basis"] * x["shares"] for x in gain) / tot_sh if tot_sh else 0.0
    return {
        "gain_lots": gain, "underwater_lots": under, "gain_shares": tot_sh, "avg_basis": avg,
        "unrealized_gain": sum((spot - x["basis"]) * x["shares"] for x in gain),
        "underwater_loss": sum((spot - x["basis"]) * x["shares"] for x in under),
        "all_shares": tot_sh + sum(x["shares"] for x in under),
    }


def allocate_lots(rungs: list[dict], gain_lots: list[dict], *, covered_shares: int, mode: str,
                  budget_by_year: Optional[dict] = None, used_by_year: Optional[dict] = None) -> list[dict]:
    """Assign specific lots to rungs, soonest-expiring rung first, highest-basis lots first.

    `rungs` carry kc, net (net credit/share = call bid − put ask), year. A rung's worst-case gain per share on a lot with
    basis b is (kc + net − b). In "budget" mode a rung only takes shares while the book's running worst-case gain for its
    tax year stays ≤ the budget (`used_by_year` is the SHARED running total so several tickers draw on one budget);
    shares that don't fit anywhere are returned as the leftover. Whole contracts (100 sh) only.
    Mutates each rung with `alloc` = [{id, basis, shares}] and returns the leftover lot pieces."""
    pool = [dict(l) for l in gain_lots]                      # highest basis first already
    left_total = covered_shares
    used = used_by_year if used_by_year is not None else {}
    n_rungs = len(rungs)
    per_rung = (covered_shares // CONTRACT // n_rungs) * CONTRACT if n_rungs else 0
    extra = (covered_shares // CONTRACT) - (per_rung // CONTRACT) * n_rungs      # leftover contracts → earliest rungs

    def take(rung: dict, want: int, cap: Optional[float]) -> int:
        taken = 0
        for l in pool:
            if want - taken < CONTRACT:
                break
            if l["shares"] <= 0:
                continue
            gps = rung["kc"] + rung["net"] - l["basis"]
            n = min(l["shares"], want - taken)
            if cap is not None and gps > 0:
                n = min(n, int(cap // gps))
            n = (n // CONTRACT) * CONTRACT
            if n <= 0:
                continue
            l["shares"] -= n
            taken += n
            rung["alloc"].append({"id": l["id"], "basis": l["basis"], "shares": n})
            if cap is not None:
                cap -= gps * n
            used[rung["year"]] = used.get(rung["year"], 0.0) + gps * n
        return taken

    for r in rungs:
        r["alloc"] = []
    for k, r in enumerate(rungs):
        want = per_rung + (CONTRACT if k < extra else 0)
        cap = None
        if mode == "budget" and budget_by_year is not None:
            cap = max(0.0, budget_by_year.get(r["year"], budget_by_year.get("*", math.inf)) - used.get(r["year"], 0.0))
            cap = None if math.isinf(cap) else cap
        left_total -= take(r, want, cap)
    if mode == "budget" and budget_by_year is not None:        # second pass: spare capacity in any rung
        for r in rungs:
            cap = budget_by_year.get(r["year"], budget_by_year.get("*", math.inf)) - used.get(r["year"], 0.0)
            if math.isinf(cap) or cap <= 0:
                continue
            left_total -= take(r, (left_total // CONTRACT) * CONTRACT, cap)
    leftover = [l for l in pool if l["shares"] > 0]
    return leftover


# ---------------------------------------------------------------------------
# one rung: structure selection (live chain)
# ---------------------------------------------------------------------------

def _leg_view(q: OptionQuote, spot: float) -> dict:
    mid = q.mid if q.mid else (q.bid + q.ask) / 2 if (q.bid and q.ask) else 0.0
    spread = ((q.ask - q.bid) / mid * 100.0) if (mid and q.ask and q.bid) else None
    return {"strike": round(q.strike, 2), "bid": round(q.bid, 2), "ask": round(q.ask, 2), "mid": round(mid, 2),
            "iv": round(q.iv * 100, 1) if q.iv else None, "oi": int(q.oi or 0), "volume": int(q.volume or 0),
            "spread_pct": round(spread, 1) if spread is not None else None,
            "pct_of_spot": round((q.strike / spot - 1) * 100, 1)}


def _tradable(q: Optional[OptionQuote]) -> bool:
    return bool(q and q.bid and q.bid > 0 and q.ask and q.ask > 0)


def pick_structure(calls: dict, puts: dict, spot: float, floor_pct: float) -> Optional[dict]:
    """Full-floor collar: the put floor and the highest ZERO-COST call cap on the chain (executable prices: sell at the call BID,
    buy at the put ASK). If nothing zero-cost sits above +MIN_CAP_PCT, take the closest-to-zero cap (a small debit) and say so.
    Deepens the floor in FLOOR_STEPS until the band clears MIN_BAND_PCT (constructive-sale guard). If the zero-cost cap would sit
    beyond +MAX_CAP_PCT it is pulled in to that limit and the surplus premium buys a TIGHTER floor instead."""
    put_ks = sorted(k for k, q in puts.items() if _tradable(q))
    call_ks = sorted(k for k, q in calls.items() if _tradable(q) and k >= spot * (1 + MIN_CAP_PCT / 100))
    if not put_ks or not call_ks:
        return None
    cap_hi = spot * (1 + MAX_CAP_PCT / 100)
    best = None
    for step in FLOOR_STEPS:
        fp = floor_pct + step
        target = spot * (1 - fp / 100)
        below = [k for k in put_ks if k <= target]
        kp = below[-1] if below else put_ks[0]
        p = puts[kp]
        tightened = False
        zero = [k for k in call_ks if calls[k].bid >= p.ask]
        if zero:
            kc, is_zero = zero[-1], True
            if kc > cap_hi:
                inside = [k for k in call_ks if k <= cap_hi]
                if inside:
                    kc = inside[-1]
                    cbid = calls[kc].bid
                    aff = [k for k in put_ks if puts[k].ask <= cbid and k <= kc - MIN_BAND_PCT / 100 * spot
                           and k <= spot * 0.97]
                    if aff and aff[-1] > kp:
                        kp, p, tightened = aff[-1], puts[aff[-1]], True
        else:
            kc = max(call_ks, key=lambda k: calls[k].bid)                 # closest to funding the put
            is_zero = False
        band = (kc - kp) / spot * 100
        best = {"kp": kp, "kc": kc, "put": p, "call": calls[kc], "zero_cost": is_zero, "band_pct": band,
                "floor_requested": floor_pct, "floor_actual": (1 - kp / spot) * 100, "floor_deepened": step > 0,
                "floor_tightened": tightened}
        if band >= MIN_BAND_PCT:
            break
    return best


def _annotate(s: dict, sid: str, name: str) -> dict:
    """Uniform fields every structure carries: put (long), put2 (short, for a spread), call (short), per-share costs."""
    put, put2, call = s.get("put"), s.get("put2"), s["call"]
    s.update({"id": s.get("id", sid), "name": s.get("name", name), "put": put, "put2": put2})
    s["kp"] = put.strike if put else 0.0
    s["kp2"] = put2.strike if put2 else 0.0
    s["kc"] = call.strike
    s["c0"] = call.bid
    s["p0"] = (put.ask if put else 0.0) - (put2.bid if put2 else 0.0)        # NET cost of the protection
    s["net"] = s["c0"] - s["p0"]
    s.setdefault("floor_requested", None)
    s.setdefault("floor_deepened", False)
    s.setdefault("floor_tightened", False)
    s.setdefault("zero_cost", abs(s["net"]) < 0.005 or s["net"] > 0)
    s["floor_actual"] = (1 - s["kp"] / s["_spot"]) * 100 if (s["kp"] and s.get("_spot")) else None
    return s


def build_structures(calls: dict, puts: dict, spot: float, floor_pct: float, dens: "Density", call_prob: float,
                     *, model: bool = False) -> list[dict]:
    """The structure MENU for one expiry — what a desk would actually weigh, not just 'buy a put':
      collar        full put floor funded by the call (zero-cost) — most protection, least upside, §1259-sensitive
      spread_collar put SPREAD (long −F%, short −(F+20)%) funded by the call — tail-only protection, a much higher cap
      income_spread same put spread, call sold at ~`call_prob` of finishing above it → NET CREDIT that pays tax
      overwrite     covered call only (no put) — pure income, a qualified-covered-call profile, downside fully retained
    Executable prices throughout (sell at bid, buy at ask)."""
    out: list[dict] = []
    seen: set = set()

    def add(st: Optional[dict], sid: str, name: str):
        if not st:
            return
        st["_spot"] = spot
        st["model"] = model
        _annotate(st, sid, name)
        key = (st["kp"], st["kp2"], st["kc"])
        if key in seen:
            return
        seen.add(key)
        out.append(st)

    add(pick_structure(calls, puts, spot, floor_pct), "collar", "Zero-cost collar")

    put_ks = sorted(k for k, q in puts.items() if _tradable(q))
    cap_lo, cap_hi = spot * (1 + MIN_CAP_PCT / 100), spot * (1 + MAX_CAP_PCT / 100)
    call_ks = sorted(k for k, q in calls.items() if _tradable(q) and cap_lo <= k <= cap_hi)
    if not call_ks:
        return out
    kc_prob = min(call_ks, key=lambda k: abs(dens.prob_above(k) - call_prob))

    b1 = [k for k in put_ks if k <= spot * (1 - floor_pct / 100)]
    b2 = [k for k in put_ks if k <= spot * (1 - (floor_pct + SPREAD_WIDTH_PCT) / 100)]
    if b1 and b2 and b2[-1] < b1[-1]:
        kp1, kp2 = b1[-1], b2[-1]
        cost = puts[kp1].ask - puts[kp2].bid
        zero = [k for k in call_ks if calls[k].bid >= cost]
        if zero:
            kc, zc = zero[-1], True
        else:
            kc, zc = max(call_ks, key=lambda k: calls[k].bid), False
        add({"kp": kp1, "put": puts[kp1], "put2": puts[kp2], "call": calls[kc], "zero_cost": zc,
             "band_pct": (kc - kp1) / spot * 100, "floor_requested": floor_pct}, "spread_collar", "Put-spread collar")
        if calls[kc_prob].bid >= cost:
            add({"kp": kp1, "put": puts[kp1], "put2": puts[kp2], "call": calls[kc_prob], "zero_cost": True,
                 "band_pct": (kc_prob - kp1) / spot * 100, "floor_requested": floor_pct}, "income_spread",
                "Income put-spread collar")
    add({"put": None, "put2": None, "call": calls[kc_prob], "zero_cost": True, "band_pct": None}, "overwrite",
        "Covered-call overwrite")
    return out


def structure_constructive(s: dict, spot: float) -> dict:
    """Constructive-sale (§1259) read for a structure."""
    if not s["kp"]:
        return {"risk": "low", "note": "No put: the shares keep their full downside, so there is no constructive-sale exposure. "
                "A call written >30 days out and not deep in-the-money against stock you already hold is a 'qualified covered call' "
                "(no straddle treatment — confirm with your adviser)."}
    band = (s["kc"] - s["kp"]) / spot * 100
    f = constructive_sale_flag(band)
    if s["kp2"]:
        f = dict(f)
        f["note"] += f" The short put at ${s['kp2']:.0f} leaves the tail below it open, which lowers (not removes) the risk."
    return f




# ---------------------------------------------------------------------------
# FLEX / OTC (custom-terms) pricing — INDICATIVE, model-based
# ---------------------------------------------------------------------------

def flex_dates(today: date, n: int, horizon_days: int) -> list[tuple[str, int]]:
    """Custom expiries for a FLEX/OTC ladder: n dates evenly spread over [MIN_RUNG_DTE, horizon], each snapped to the first
    business-ish day after a year-end (Jan 15) when that is within ~4 months — the gain then lands in the NEXT tax year, which
    listed LEAPS only offer once a year. Otherwise rounded to a Friday."""
    horizon_days = min(horizon_days, FLEX_MAX_YEARS * 365)
    if n <= 1 or horizon_days <= MIN_RUNG_DTE:
        targets = [max(MIN_RUNG_DTE, horizon_days)]
    else:
        targets = [MIN_RUNG_DTE + (horizon_days - MIN_RUNG_DTE) * i / (n - 1) for i in range(n)]
    out, seen = [], set()
    for t in targets:
        tgt = date.fromordinal(today.toordinal() + int(round(t)))
        jans = [date(y, 1, 15) for y in (tgt.year, tgt.year + 1)]
        best = min(jans, key=lambda d: abs((d - tgt).days))
        d = best if abs((best - tgt).days) <= 120 and (best - today).days >= MIN_RUNG_DTE else tgt
        while d.weekday() >= 5:                      # roll weekends forward to Monday
            d = date.fromordinal(d.toordinal() + 1)
        if d.isoformat() in seen:
            continue
        seen.add(d.isoformat())
        out.append((d.isoformat(), (d - today).days))
    return sorted(out, key=lambda x: x[1])


def synthetic_chain(spot: float, T: float, r: float, iv_at, spread_vol: float, expiry: str) -> tuple[dict, dict]:
    """A dealer-style quote ladder at (nearly) every strike, priced off a smile with the dealer's markup: we BUY at IV+spread and
    SELL at IV−spread. Feeds the SAME structure builders the listed path uses, so FLEX/OTC gets the full structure menu too."""
    sp = spread_vol / 100.0
    step = max(0.5, round(spot * 0.005 * 2) / 2)
    k = math.ceil(spot * 0.45 / step) * step
    hi = spot * (1 + MAX_CAP_PCT / 100 + 0.05)
    calls, puts = {}, {}
    while k <= hi:
        iv = iv_at(k)
        lo_v, hi_v = max(iv - sp, 0.05), iv + sp
        cb, ca = bs_price(spot, k, T, r, lo_v, "call"), bs_price(spot, k, T, r, hi_v, "call")
        pb, pa = bs_price(spot, k, T, r, lo_v, "put"), bs_price(spot, k, T, r, hi_v, "put")
        calls[float(k)] = OptionQuote(strike=float(k), right="C", expiration=expiry, bid=round(float(cb), 2), ask=round(float(ca), 2),
                                      last=round(float((cb + ca) / 2), 2), mid=round(float((cb + ca) / 2), 2), iv=iv, oi=0, volume=0)
        puts[float(k)] = OptionQuote(strike=float(k), right="P", expiration=expiry, bid=round(float(pb), 2), ask=round(float(pa), 2),
                                     last=round(float((pb + pa) / 2), 2), mid=round(float((pb + pa) / 2), 2), iv=iv, oi=0, volume=0)
        k = round(k + step, 4)
    return calls, puts


def pick_structure_model(spot: float, T: float, r: float, iv_at, floor_pct: float, spread_vol: float, expiry: str) -> Optional[dict]:
    """Custom-strike zero-cost collar off a vol smile (FLEX/OTC, European) — the collar entry of the synthetic menu."""
    calls, puts = synthetic_chain(spot, T, r, iv_at, spread_vol, expiry)
    st = pick_structure(calls, puts, spot, floor_pct)
    if st:
        st["model"] = True
    return st


def constructive_sale_flag(band_pct: float) -> dict:
    if band_pct >= MIN_BAND_PCT + 10:
        return {"risk": "low", "note": f"Band {band_pct:.0f}% of spot leaves ample two-way risk on the shares."}
    if band_pct >= MIN_BAND_PCT:
        return {"risk": "elevated", "note": f"Band {band_pct:.0f}% is near the {MIN_BAND_PCT:.0f}% rule-of-thumb minimum — "
                                            "confirm §1259 (constructive sale) treatment with your adviser."}
    return {"risk": "high", "note": f"Band {band_pct:.0f}% < {MIN_BAND_PCT:.0f}%: a collar this tight can be treated as a SALE "
                                    "for tax (IRC §1259). Widen it or skip this rung."}


# ---------------------------------------------------------------------------
# one rung: tax + outcome (ONE function drives scenarios AND expectations)
# ---------------------------------------------------------------------------

def _rung_after_tax(S: np.ndarray, *, kp: float, kc: float, c0: float, p0: float, b: float,
                    st: float, lt: float, put_rate: float, defer_put_loss: bool, kp2: float = 0.0) -> dict:
    """Per-share outcome at expiry for terminal prices S (array).

    Protection is a long put at `kp` (0 = none), optionally financed by a short put at `kp2` (a put SPREAD); `p0` is its NET cost
    (0 for a pure overwrite). The spread is one straddle unit: its net result is recognised if a gain, deferred into basis if a loss.

    Returns arrays: tax (this year), gain (recognised this year, for budget/reporting), pretax (cash+stock value incl.
    option cash), liq_after_tax (value if everything were liquidated right after expiry — the apples-to-apples number against
    holding unhedged), and `called` mask."""
    S = np.asarray(S, float)
    called = S >= kc
    n0 = c0 - p0
    if kp > 0:
        put_pay = np.maximum(kp - S, 0.0)
        if kp2 > 0:
            put_pay = put_pay - np.maximum(kp2 - S, 0.0)
    else:
        put_pay = np.zeros_like(S)
    put_net = put_pay - p0
    if defer_put_loss:
        put_recog = np.maximum(put_net, 0.0)                        # gain recognised; a loss is deferred into basis
        deferred = np.where(put_net < 0, -put_net, 0.0)
    else:
        put_recog = put_net
        deferred = np.zeros_like(S)

    gain_called = kc - b + n0                                       # premium in proceeds; put cost released with the stock
    tax_called = lt * gain_called
    tax_not = st * c0 + put_rate * put_recog                       # call premium is ST income when it lapses
    tax = np.where(called, tax_called, tax_not)
    gain = np.where(called, gain_called, c0 + put_recog)

    stock_val = np.where(called, kc, S)
    pretax = stock_val + np.where(called, n0, put_pay + n0)
    b_eff = b + deferred                                            # deferred put loss capitalised into basis
    liq_tax = np.where(called, 0.0, lt * (S - b_eff))
    liq = pretax - tax - liq_tax
    return {"tax": tax, "gain": gain, "pretax": pretax, "liq": liq, "called": called}


def rung_economics(dens: Density, *, kp: float, kc: float, c0: float, p0: float, b: float, n: int,
                   st: float, lt: float, tenor_days: int, defer_put_loss: bool, spot: float, r: float,
                   kp2: float = 0.0) -> dict:
    """Expected & worst-case money for a rung of `n` shares with average basis `b`, plus the risk metrics the structure menu
    ranks on (tail expected shortfall, upside kept, loss at the stress)."""
    put_rate = lt if tenor_days > LEAP_DAYS else st
    kw = dict(kp=kp, kp2=kp2, kc=kc, c0=c0, p0=p0, b=b, st=st, lt=lt, put_rate=put_rate, defer_put_loss=defer_put_loss)
    p_c = dens.prob_above(kc)
    # expectation: the called branch is a point mass (exact via p_c); the not-called branch integrates the smooth part.
    K = dens.K
    below = K < kc
    o = _rung_after_tax(K, **kw)
    # E[x] = p_c * x_called + ∫_{S<kc} x(S) f(S) dS   (renormalised by the mass actually below kc)
    mass_below = float(np.trapezoid(np.where(below, dens.pdf, 0.0), K)) or 1.0
    scale = (1.0 - p_c) / mass_below

    def E(arr_called: float, arr: np.ndarray) -> float:
        return p_c * arr_called + scale * float(np.trapezoid(np.where(below, arr * dens.pdf, 0.0), K))

    called_pt = _rung_after_tax(np.array([kc]), **kw)
    e_tax = E(float(called_pt["tax"][0]), o["tax"])
    e_gain = E(float(called_pt["gain"][0]), o["gain"])
    e_liq = E(float(called_pt["liq"][0]), o["liq"])
    worst_gain = (kc - b + c0 - p0)                                  # if every rung is called away
    held = _rung_after_tax(np.array([spot]), **kw)

    # ---- risk metrics under the same density
    v0 = float(unhedged_liq(np.array([spot]), b, lt)[0])
    q5 = float(np.interp(0.05, dens.cdf, K))                         # 5th-percentile terminal price
    w = np.where(K <= q5, dens.pdf, 0.0)
    mass = float(np.trapezoid(w, K)) or 1.0
    es_h = v0 - float(np.trapezoid(o["liq"] * w, K)) / mass          # expected shortfall of after-tax value vs today
    es_u = v0 - float(np.trapezoid(unhedged_liq(K, b, lt) * w, K)) / mass
    up_u = float(np.trapezoid(np.maximum(K - spot, 0.0) * dens.pdf, K))
    up_h = float(np.trapezoid(np.maximum(np.minimum(K, kc) - spot, 0.0) * dens.pdf, K))
    stress_S = spot * (1 - STRESS_PCT / 100.0)
    pre = float(_rung_after_tax(np.array([stress_S]), **kw)["pretax"][0])
    return {
        "p_called": p_c, "p_floor": dens.prob_below(kp) if kp > 0 else 0.0,
        "expected_tax": e_tax * n, "expected_gain": e_gain * n, "expected_liq_after_tax": e_liq * n,
        "worst_gain": worst_gain * n, "worst_tax": lt * worst_gain * n,
        "put_rate": put_rate, "kw": kw,
        "unchanged_tax_if_flat": float(held["tax"][0]) * n,
        "es_hedged": es_h, "es_unhedged": es_u,
        "upside_capture": (up_h / up_u) if up_u > 0 else 1.0,
        "stress_loss_pct": (spot - pre) / spot * 100.0,
    }


def structure_metrics(s: dict, dens: Density, spot: float, b: float, st: float, lt: float, tenor: int,
                      defer: bool, r: float) -> dict:
    """Per-share metrics that drive the menu (n=1)."""
    e = rung_economics(dens, kp=s["kp"], kp2=s["kp2"], kc=s["kc"], c0=s["c0"], p0=s["p0"], b=b, n=1, st=st, lt=lt,
                       tenor_days=tenor, defer_put_loss=defer, spot=spot, r=r)
    tax_called_ps = lt * (s["kc"] - b + s["net"])
    funding = 1.0 if tax_called_ps <= 0 else _clamp01(max(s["net"], 0.0) / tax_called_ps)
    protection = 0.5 if e["es_unhedged"] <= 1e-9 else _clamp01(1.0 - e["es_hedged"] / e["es_unhedged"])
    cs = structure_constructive(s, spot)
    risk = {"low": 1.0, "elevated": 0.6, "high": 0.0}[cs["risk"]]
    return {"funding": funding, "protection": protection, "upside": _clamp01(e["upside_capture"]), "risk": risk,
            "stress_loss_pct": e["stress_loss_pct"], "p_called": e["p_called"], "cs": cs, "econ": e}


def choose_structure(cands: list[dict], dens: Density, spot: float, b: float, st: float, lt: float, tenor: int,
                     defer: bool, r: float, stance: str, max_loss_pct: float) -> tuple[dict, list[dict]]:
    """Score every structure on the user's stance and return (winner, menu rows). Structures that lose more than `max_loss_pct`
    at the −STRESS_PCT stress, or that carry HIGH constructive-sale risk, are ineligible unless nothing else is."""
    w = STANCES.get(stance, STANCES["balanced"])
    rows = []
    for s in cands:
        m = structure_metrics(s, dens, spot, b, st, lt, tenor, defer, r)
        score = w["inc"] * m["funding"] + w["prot"] * m["protection"] + w["up"] * m["upside"] + w["risk"] * m["risk"]
        eligible = m["stress_loss_pct"] <= max_loss_pct and m["cs"]["risk"] != "high"
        rows.append({"s": s, "m": m, "score": score, "eligible": eligible})
    pool = [x for x in rows if x["eligible"]] or [min(rows, key=lambda x: x["m"]["stress_loss_pct"])]
    best = max(pool, key=lambda x: x["score"])
    menu = []
    for x in sorted(rows, key=lambda x: -x["score"]):
        s, m = x["s"], x["m"]
        menu.append({
            "id": s["id"], "name": s["name"], "kp": s["kp"] or None, "kp2": s["kp2"] or None, "kc": s["kc"],
            "net_credit_per_share": round(s["net"], 2),
            "tax_funding_pct": round(m["funding"] * 100, 0), "protection_pct": round(m["protection"] * 100, 0),
            "upside_capture_pct": round(m["upside"] * 100, 0), "p_called_pct": round(m["p_called"] * 100, 1),
            "stress_loss_pct": round(m["stress_loss_pct"], 1), "constructive_risk": m["cs"]["risk"],
            "score": round(x["score"] * 100, 0), "eligible": x["eligible"], "recommended": x is best,
        })
    return best["s"], menu


def unhedged_liq(S: np.ndarray, b: float, lt: float) -> np.ndarray:
    S = np.asarray(S, float)
    return S - lt * (S - b)


# ---------------------------------------------------------------------------
# timing
# ---------------------------------------------------------------------------

def timing_assessment(*, spot: float, gain_pct: float, embedded_tax: float, headline: Optional[dict],
                      vol: dict, trend: dict, earnings_days: Optional[int], liq_spread: Optional[float],
                      has_gain_lots: bool, floor_pct: float) -> dict:
    """Score 'is now a good time to put a ladder on?' from LIVE data. Hard gates first, then six weighted factors."""
    if not has_gain_lots:
        return {"verdict": "NOT_APPLICABLE", "score": None, "headline": "Every lot is at/below spot — nothing to defer. "
                "Harvest the losses instead (sell the underwater lots) rather than laddering.", "factors": [], "triggers": []}
    if gain_pct < 8.0 or embedded_tax < 1000:
        return {"verdict": "JUST_SELL", "score": None, "factors": [], "triggers": [],
                "headline": f"Embedded gain is only {gain_pct:.1f}% (≈${embedded_tax:,.0f} of tax) — too little to be worth "
                            "deferring with options; simply trimming is cheaper and cleaner."}
    if not headline:
        return {"verdict": "WAIT", "score": 0, "factors": [], "triggers": ["Liquid long-dated options needed"],
                "headline": "No liquid long-dated put/call pair on the chain — can't build a ladder yet."}

    cap = headline["cap_pct"]
    skew = vol.get("skew_pts")
    ivhv = vol.get("iv_hv_ratio")
    ext = trend.get("ext_200")           # price / 200dma − 1  (fraction)
    rsi = trend.get("rsi14")
    off_hi = trend.get("off_high_pct")   # % below 52w high (positive)
    factors = []

    def add(name, weight, s, note):
        factors.append({"name": name, "weight": weight, "score": round(s, 2), "points": round(weight * s, 1), "note": note})

    add("Collar economics", W_COLLAR, _lin(cap, 8.0, 25.0),
        f"Zero-cost cap at a −{floor_pct:.0f}% floor sits at +{cap:.1f}% (≥ +25% is great, ≤ +8% means you'd give up almost all upside)")
    add("Skew (put cost)", W_SKEW, _lin(skew, 12.0, 2.0),
        "n/a" if skew is None else f"IV(90%)−IV(110%) = {skew:.1f} vol pts — flatter skew = cheaper floor per $ of cap")
    add("Vol premium (IV/HV)", W_VRP, _lin(ivhv, 0.9, 1.15),
        "n/a" if ivhv is None else f"Implied/realised = {ivhv:.2f}× — richer calls fund more floor")
    ts = 0.5
    parts = []
    if ext is not None:
        ts_e = _lin(ext, -0.05, 0.25)
        parts.append(ts_e)
    if rsi is not None:
        parts.append(_lin(rsi, 40.0, 68.0))
    if off_hi is not None:
        parts.append(_lin(-off_hi, -25.0, -3.0))                    # near highs good, deep drawdown bad
    if parts:
        ts = sum(parts) / len(parts)
    add("Trend / extension", W_TREND, ts,
        "Selling the cap into strength is better than after a drawdown: "
        + ", ".join(x for x in [
            f"{ext * 100:+.0f}% vs 200d" if ext is not None else None,
            f"RSI {rsi:.0f}" if rsi is not None else None,
            f"{off_hi:.0f}% off 52w high" if off_hi is not None else None] if x))
    if earnings_days is None:
        es, en = 1.0, "no earnings date in the data"
    elif earnings_days <= 7:
        es, en = 0.2, f"earnings in {earnings_days}d — IV and gap risk are inflated; place the ladder after the print"
    elif earnings_days <= 21:
        es, en = 0.6, f"earnings in {earnings_days}d — consider staggering around the print"
    else:
        es, en = 1.0, f"earnings in {earnings_days}d — clear"
    add("Event risk", W_EVENT, es, en)
    add("Liquidity", W_LIQ, _lin(liq_spread, 12.0, 3.0),
        "n/a" if liq_spread is None else f"Worst leg bid/ask spread {liq_spread:.1f}% of mid on the chosen strikes")

    score = round(sum(f["points"] for f in factors))
    if score >= VERDICT_NOW:
        verdict, head = "PUT_ON_NOW", f"Good window — conditions favour putting the ladder on now ({score}/100)."
    elif score >= VERDICT_STAGGER:
        verdict, head = "STAGGER", (f"Acceptable but not ideal ({score}/100): leg in — put on about half now and the rest "
                                    "over the next 4–8 weeks (or after the next earnings print).")
    else:
        verdict, head = "WAIT", f"Wait ({score}/100): the market is not paying you well to build this ladder today."
    weak = sorted([f for f in factors if f["score"] < 0.5], key=lambda f: f["score"])
    triggers = []
    for f in weak:
        if f["name"] == "Collar economics":
            triggers.append("Zero-cost cap ≥ +15–20% at the same floor (IV/skew falls, or the stock rallies)")
        elif f["name"] == "Skew (put cost)":
            triggers.append("Put skew flattens toward ≤ 5 vol pts")
        elif f["name"] == "Trend / extension":
            triggers.append("Stock stabilises/recovers (RSI > 50, back above the 200-day) — don't sell the cap into a washout")
        elif f["name"] == "Event risk":
            triggers.append("After the earnings print")
        elif f["name"] == "Liquidity":
            triggers.append("Tighter markets on the long-dated strikes (use limit orders at mid in the meantime)")
        elif f["name"] == "Vol premium (IV/HV)":
            triggers.append("Implied vol ≥ ~1.1× realised")
    return {"verdict": verdict, "score": score, "headline": head, "factors": factors, "triggers": triggers}


# ---------------------------------------------------------------------------
# market data (live)
# ---------------------------------------------------------------------------

def _trend_sync(ticker: str) -> dict:
    out = {"ext_200": None, "ext_50": None, "rsi14": None, "off_high_pct": None, "ret_3m_pct": None}
    try:
        import yfinance as yf
        h = yf.Ticker(ticker).history(period="1y")
        if h is None or h.empty:
            return out
        c = h["Close"].dropna()
        if len(c) < 60:
            return out
        last = float(c.iloc[-1])
        if len(c) >= 200:
            out["ext_200"] = last / float(c.tail(200).mean()) - 1
        out["ext_50"] = last / float(c.tail(50).mean()) - 1
        d = c.diff().dropna()
        up, dn = d.clip(lower=0).tail(14).mean(), (-d.clip(upper=0)).tail(14).mean()
        out["rsi14"] = float(100 - 100 / (1 + up / dn)) if dn and dn > 0 else 100.0
        out["off_high_pct"] = (1 - last / float(c.max())) * 100
        if len(c) >= 63:
            out["ret_3m_pct"] = (last / float(c.iloc[-63]) - 1) * 100
    except Exception as exc:  # noqa: BLE001
        logger.debug("trend fetch failed for %s: %s", ticker, exc)
    return out


def _pick_expiries(exps: list[str], today: date, n: int, horizon_days: int) -> list[tuple[str, int]]:
    """Choose up to n expiries ≥ MIN_RUNG_DTE, evenly spread to min(horizon, longest listed). Prefer standard monthlies,
    and January expiries (the LEAPS cycle) — a January expiry pushes the gain into the next tax year vs December."""
    cand = []
    for s in exps:
        try:
            d = datetime.strptime(s, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        dte = (d - today).days
        if dte >= MIN_RUNG_DTE and _is_monthly_expiry(d):
            cand.append((s, dte, d))
    if not cand:
        return []
    cand.sort(key=lambda x: x[1])
    longest = min(horizon_days, cand[-1][1])
    if n <= 1 or longest <= MIN_RUNG_DTE:
        targets = [longest]
    else:
        targets = [MIN_RUNG_DTE + (longest - MIN_RUNG_DTE) * i / (n - 1) for i in range(n)]
    chosen, seen = [], set()
    for t in targets:
        pool = [c for c in cand if c[0] not in seen]
        if not pool:
            break
        # nearest to target; within 45d prefer a January expiry (LEAPS cycle / next tax year)
        near = sorted(pool, key=lambda c: abs(c[1] - t))
        best = near[0]
        jan = [c for c in near if c[2].month == 1 and abs(c[1] - t) <= 45]
        if jan:
            best = jan[0]
        chosen.append((best[0], best[1]))
        seen.add(best[0])
    return sorted(chosen, key=lambda x: x[1])


def overlay_options(calls: dict, spot: float, dte: int, r: float) -> list[dict]:
    """Rolling short-dated call program (the institutional 'overwrite'): per-cycle premium at ~0.10Δ and ~0.20Δ. Re-sold every
    cycle it harvests the vol premium far faster than one long-dated call (theta is front-loaded) — but each assignment realises
    gain early, so it belongs on shares OUTSIDE the ladder's call legs (or instead of them)."""
    best: dict = {}
    for k, q in calls.items():
        if k <= spot or not _tradable(q) or not q.iv:
            continue
        d = _bs_greeks(spot, k, dte, q.iv, "C", r)["delta"]
        for tgt in (0.10, 0.20):
            if tgt not in best or abs(d - tgt) < best[tgt][0]:
                best[tgt] = (abs(d - tgt), k, q, d)
    out = []
    for tgt, (diff, k, q, d) in sorted(best.items()):
        if diff > 0.07:
            continue
        dens = lognormal_density(spot, q.iv, dte / 365.0, r)
        pc = dens.prob_above(k)
        cycles = 365.0 / dte
        out.append({"target_delta": tgt, "delta": round(d, 2), "strike": float(k), "cap_pct": round((k / spot - 1) * 100, 1),
                    "bid": round(q.bid, 2), "expiry": q.expiration, "dte": dte, "iv_pct": round(q.iv * 100, 1),
                    "p_assign_cycle_pct": round(pc * 100, 1), "p_assign_12m_pct": round((1 - (1 - pc) ** cycles) * 100, 1),
                    "annual_yield_pct_if_never_assigned": round(q.bid / spot * cycles * 100, 1)})
    return out


async def _overlay_src(provider, ticker: str, spot: float, today: date, r: float, exps: list[str]) -> list[dict]:
    cands = []
    for e in exps:
        try:
            d = datetime.strptime(e, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        dte = (d - today).days
        if 30 <= dte <= 75 and _is_monthly_expiry(d):
            cands.append((abs(dte - 45), e, dte))
    if not cands:
        return []
    _, exp, dte = min(cands)
    try:
        chain = await provider.get_option_chain(ticker, exp)
        calls, _puts = _split_chain(chain)
        return overlay_options(calls, spot, dte, r)
    except Exception as exc:  # noqa: BLE001
        logger.debug("overlay chain %s failed: %s", ticker, exc)
        return []


async def _market_for_ticker(provider, ticker: str, today: date, n: int, horizon_days: int, floor_pct: float,
                             r: float, execution: str = "listed", flex_spread: float = FLEX_SPREAD_VOL_PTS,
                             call_prob: float = DEFAULT_CALL_PROB) -> dict:
    """Live spot + per-expiry chains → candidate rung structures + vol/trend/earnings context. Sequential on purpose
    (512 MiB instance: never fan chains out in parallel)."""
    try:
        u = await provider.get_underlying_price(ticker)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Cannot fetch price for {ticker}: {exc}"}
    spot = _f(getattr(u, "price", 0))
    if spot <= 0:
        return {"error": f"No live price for {ticker}."}
    try:
        exps = await provider.get_option_expirations(ticker)
    except Exception:  # noqa: BLE001
        return {"error": f"No options available for {ticker}."}
    if execution == "flex":
        return await _flex_market(provider, ticker, today, n, horizon_days, floor_pct, r, flex_spread, spot, exps or [],
                                  call_prob)
    picks = _pick_expiries(exps or [], today, n, horizon_days)
    if not picks:
        return {"error": f"{ticker} has no listed monthly options ≥ {MIN_RUNG_DTE} days out — a ladder needs LEAPS."}

    rungs = []
    for exp, dte in picks:
        try:
            chain = await provider.get_option_chain(ticker, exp)
        except Exception as exc:  # noqa: BLE001
            logger.debug("chain %s %s failed: %s", ticker, exp, exc)
            continue
        if not chain or not chain.quotes:
            continue
        calls, puts = _split_chain(chain)
        strikes_all = sorted(set(calls) | set(puts))
        rnd = None
        try:
            rnd = _build_rnd(calls, puts, strikes_all, spot, dte)
        except Exception as exc:  # noqa: BLE001
            logger.debug("rnd fail %s %s: %s", ticker, exp, exc)
        atm = _atm_iv(puts, calls, spot)
        if rnd is not None and getattr(rnd, "smile", None) is not None and getattr(rnd.smile, "arb_free", True):
            dens = density_from_rnd(rnd)
            # sanity: a degenerate RND collapses — fall back to lognormal if the mean is far off the forward
            mean = float(np.trapezoid(dens.K * dens.pdf, dens.K))
            if not (0.8 * rnd.forward <= mean <= 1.2 * rnd.forward):
                dens = lognormal_density(spot, atm or 0.35, dte / 365.0, r)
        else:
            dens = lognormal_density(spot, atm or 0.35, dte / 365.0, r)
        cands = build_structures(calls, puts, spot, floor_pct, dens, call_prob)
        if not cands:
            continue
        skew = _compute_skew(puts, calls, spot)
        skew = skew * 100.0 if skew is not None else None            # decimal → vol points
        rungs.append({"expiry": exp, "dte": dte, "year": int(exp[:4]), "dens": dens, "cands": cands, "atm_iv": atm,
                      "skew_pts": skew})
    if not rungs:
        return {"error": f"Could not build a liquid collar for {ticker} on any long-dated expiry."}

    # vol read taken off the rung nearest one year — the tenor the timing thresholds are calibrated to
    ref = min(rungs, key=lambda x: abs(x["dte"] - 365))
    first_iv = {"atm_iv": ref["atm_iv"], "skew_pts": ref["skew_pts"]}
    ctx = await asyncio.to_thread(_context_sync, ticker)
    trend = await asyncio.to_thread(_trend_sync, ticker)
    overlay = await _overlay_src(provider, ticker, spot, today, r, exps or [])
    return {"ticker": ticker, "spot": spot, "rungs": rungs, "ctx": ctx, "trend": trend, "iv": first_iv, "overlay": overlay}



async def _flex_market(provider, ticker, today, n, horizon_days, floor_pct, r, flex_spread, spot, exps,
                       call_prob: float = DEFAULT_CALL_PROB) -> dict:
    """FLEX/OTC: ONE reference chain (~1.5y listed expiry) supplies the vol smile; custom dates are priced off it with a FLAT
    term-structure extrapolation (real dealers will differ — hence 'indicative')."""
    refs = []
    for s_ in exps:
        try:
            d = datetime.strptime(s_, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        if (d - today).days >= MIN_RUNG_DTE and _is_monthly_expiry(d):
            refs.append((s_, (d - today).days))
    if not refs:
        return {"error": f"{ticker} has no long-dated listed options to anchor a FLEX/OTC vol smile."}
    ref_exp, ref_dte = min(refs, key=lambda x: abs(x[1] - 540))
    try:
        chain = await provider.get_option_chain(ticker, ref_exp)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Could not fetch reference chain: {exc}"}
    calls, puts = _split_chain(chain)
    atm = _atm_iv(puts, calls, spot)
    if not atm:
        return {"error": f"No usable implied vols on {ticker}'s chain."}
    rnd = None
    try:
        rnd = _build_rnd(calls, puts, sorted(set(calls) | set(puts)), spot, ref_dte)
    except Exception as exc:  # noqa: BLE001
        logger.debug("flex rnd fail %s: %s", ticker, exc)
    smile = getattr(rnd, "smile", None)

    def iv_at(k: float) -> float:
        k = min(max(k, 0.7 * spot), 1.3 * spot)           # SVI is only calibrated inside this band — flat beyond it
        if smile is not None:
            try:
                v = float(smile.iv(k))
                if math.isfinite(v) and 0.05 < v < 3.0:
                    return v
            except Exception:  # noqa: BLE001
                pass
        return atm

    skew = _compute_skew(puts, calls, spot)
    skew = skew * 100.0 if skew is not None else None
    rungs = []
    for exp, dte in flex_dates(today, n, horizon_days):
        T = dte / 365.0
        dens = lognormal_density(spot, iv_at(spot), T, r)
        fcalls, fputs = synthetic_chain(spot, T, r, iv_at, flex_spread, exp)
        cands = build_structures(fcalls, fputs, spot, floor_pct, dens, call_prob, model=True)
        if not cands:
            continue
        rungs.append({"expiry": exp, "dte": dte, "year": int(exp[:4]), "dens": dens, "cands": cands, "atm_iv": iv_at(spot),
                      "skew_pts": skew})
    if not rungs:
        return {"error": f"Could not price any FLEX/OTC structure for {ticker}."}
    ctx = await asyncio.to_thread(_context_sync, ticker)
    trend = await asyncio.to_thread(_trend_sync, ticker)
    overlay = await _overlay_src(provider, ticker, spot, today, r, exps)
    return {"ticker": ticker, "spot": spot, "rungs": rungs, "ctx": ctx, "trend": trend,
            "iv": {"atm_iv": atm, "skew_pts": skew}, "venue": "flex", "overlay": overlay}

# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def _tax_year_note(exp: str) -> Optional[str]:
    d = datetime.strptime(exp, "%Y-%m-%d").date()
    if d.month == 12:
        return f"Dec expiry → any assignment is taxed in {d.year}; the January expiry would defer it to {d.year + 1}."
    return None


def _rfq(ticker: str, spot: float, rg: dict, n: int, s: dict) -> dict:
    """The term sheet to hand a FLEX / OTC desk."""
    return {"underlying": ticker, "contracts": n // CONTRACT, "shares": n, "expiry": rg["expiry"], "style": "European",
            "settlement": "Physical (shares)", "reference_spot": round(spot, 2),
            "structure": s["name"],
            "buy_put": {"strike": s["kp"], "indicative_ask": s["put"].ask} if s.get("put") else None,
            "sell_put": {"strike": s["kp2"], "indicative_bid": s["put2"].bid} if s.get("put2") else None,
            "sell_call": {"strike": s["kc"], "indicative_bid": s["call"].bid},
            "indicative_net_per_share": round(s["net"], 2),
            "ask_for": "Quote the net premium for these strikes (or the call strike that makes it a given net credit), "
                       "European exercise, covered by the shares in your account. Ask for the quote as one package, not leg by leg."}


def _structure_tax_note(s: dict) -> str:
    if not s["kp"]:
        return ("Call premium is short-term income if the call lapses, and part of the sale price (long-term) if assigned. No put, "
                "so no straddle: the call can qualify as a covered call — a loss on closing a qualified covered call is generally "
                "long-term when the stock is.")
    base = ("Put cost is a straddle loss deferred into the stock's basis (released when the shares are sold), so call premium that "
            "lapses is taxed now at your short-term rate with no offset.")
    if s["kp2"]:
        base += " The put spread is one position: only a net gain beyond its cost is recognised."
    return base


def _overlay_summary(ov: list[dict], gain_shares: int, covered: int, avg_basis: float, st: float, lt: float) -> Optional[dict]:
    """Income-overlay card: what a rolling ~45-day call program earns on shares OUTSIDE the ladder's call legs."""
    if not ov or gain_shares < CONTRACT:
        return None
    free = (gain_shares - covered) // CONTRACT * CONTRACT
    shares = free if free >= CONTRACT else gain_shares // CONTRACT * CONTRACT
    rows = []
    for o in ov:
        cycles = 365.0 / o["dte"]
        prem_yr = o["bid"] * shares * cycles
        after_tax = prem_yr * (1 - st)
        tax_if_called = lt * max(o["strike"] - avg_basis, 0.0) * shares
        rows.append({**o, "shares": shares, "premium_per_year": round(prem_yr, 0), "after_tax_premium_per_year": round(after_tax, 0),
                     "tax_if_assigned": round(tax_if_called, 0),
                     "funds_pct_of_assignment_tax": round(after_tax / tax_if_called * 100, 0) if tax_if_called > 0 else None})
    return {"outside_ladder": free >= CONTRACT, "shares": shares, "options": rows,
            "note": ("These shares are outside the ladder's call legs." if free >= CONTRACT else
                     "Every gain share already carries a ladder call — this is an ALTERNATIVE to the ladder's long-dated call legs, "
                     "not an addition.") + " Each assignment realises gain early (keep it inside your annual budget), and premium that "
                     "lapses is short-term income taxed at your ordinary rate. Premium shown assumes it is re-sold every cycle."}


def build_plan(markets: list[dict], positions: list[dict], *, tax: dict, mode: str, budget: Optional[float],
               defer_put_loss: bool, r: float, today: date, floor_pct: float, coverage_pct: float,
               assumed_spread: float = FLEX_SPREAD_VOL_PTS, stance: str = "balanced",
               max_loss_pct: float = DEFAULT_MAX_LOSS_PCT) -> dict:
    """Pure assembly (no IO): market snapshots + lots + tax profile → timing, ladders, by-year tax, scenarios."""
    st, lt = tax["st_rate_pct"] / 100.0, tax["lt_rate_pct"] / 100.0
    budget_by_year = {"*": budget} if (mode == "budget" and budget) else None
    used: dict = {}
    out_positions, warnings = [], []
    by_ticker_pos = {p["ticker"]: p for p in positions}

    # ---- phase 1: lot split + rung templates (no allocation yet)
    prepared = []
    for m in markets:
        pos = by_ticker_pos[m["ticker"]]
        if m.get("error"):
            out_positions.append({"ticker": m["ticker"], "error": m["error"]})
            continue
        spot = m["spot"]
        sp = split_lots(pos["lots"], spot)
        rungs = []
        b_rep = sp["avg_basis"] or spot * 0.5          # representative basis for scoring structures (lots allocated after)
        for rg in m["rungs"]:
            cands = rg.get("cands") or [rg["struct"]]
            for c in cands:
                if "id" not in c:
                    c["_spot"] = spot
                    _annotate(c, "collar", "Zero-cost collar")
            s, menu = choose_structure(cands, rg["dens"], spot, b_rep, st, lt, rg["dte"], defer_put_loss, r, stance, max_loss_pct)
            rungs.append({"expiry": rg["expiry"], "dte": rg["dte"], "year": rg["year"], "dens": rg["dens"], "s": s, "menu": menu,
                          "menu_cands": cands,
                          "kc": s["kc"], "kp": s["kp"], "c0": s["c0"], "p0": s["p0"], "net": s["net"], "alloc": []})
        prepared.append((m, pos, sp, rungs))

    # ---- phase 2: allocate lots tax-first, jointly over the book (shared annual budget)
    # Tickers draw on ONE shared annual budget in the order the user listed them (their priority).
    cov_target = {}
    for m, pos, sp, rungs in prepared:
        cov = int(sp["gain_shares"] * coverage_pct / 100.0) // CONTRACT * CONTRACT
        cov_target[m["ticker"]] = cov
        if rungs and cov >= CONTRACT:
            allocate_lots(rungs, sp["gain_lots"], covered_shares=cov, mode=mode,
                          budget_by_year=budget_by_year, used_by_year=used)

    # ---- phase 3: economics, timing, scenarios
    year_rows: dict = {}
    sell_now_total = pv_defer_total = 0.0
    for m, pos, sp, rungs in prepared:
        spot, tk = m["spot"], m["ticker"]
        res_rungs, tot_cov = [], 0
        worst_spread = 0.0
        for rg in rungs:
            n = sum(a["shares"] for a in rg["alloc"])
            if n <= 0:
                continue
            b = sum(a["basis"] * a["shares"] for a in rg["alloc"]) / n
            s = rg["s"]
            econ = rung_economics(rg["dens"], kp=rg["kp"], kp2=s["kp2"], kc=rg["kc"], c0=rg["c0"], p0=rg["p0"], b=b, n=n,
                                  st=st, lt=lt, tenor_days=rg["dte"], defer_put_loss=defer_put_loss, spot=spot, r=r)
            sc = []
            Sg = np.array([spot * (1 + x / 100.0) for x in SCENARIO_MOVES])
            o = _rung_after_tax(Sg, **econ["kw"])
            u = unhedged_liq(Sg, b, lt)
            for i, x in enumerate(SCENARIO_MOVES):
                sc.append({"move_pct": x, "price": round(float(Sg[i]), 2), "called": bool(o["called"][i]),
                           "tax_this_year": round(float(o["tax"][i]) * n, 0),
                           "after_tax_value": round(float(o["liq"][i]) * n, 0),
                           "unhedged_after_tax": round(float(u[i]) * n, 0),
                           "vs_unhedged": round(float(o["liq"][i] - u[i]) * n, 0)})
            T = rg["dte"] / 365.0
            embedded = (spot - b) * n
            sell_now_total += lt * embedded
            pv_defer_total += lt * embedded * (1 - math.exp(-r * T))
            flags = []
            for leg_name, leg in (("put", s["put"]), ("short put", s["put2"]), ("call", s["call"])):
                if leg is None:
                    continue
                v = _leg_view(leg, spot)
                if v["spread_pct"] is not None and not s.get("model"):
                    worst_spread = max(worst_spread, v["spread_pct"])
                    if v["spread_pct"] > MAX_SPREAD_PCT:
                        flags.append(f"{leg_name} spread {v['spread_pct']:.0f}% of mid — work the order at mid")
                if v["oi"] < 10 and not s.get("model"):
                    flags.append(f"{leg_name} open interest only {v['oi']}")
            if rg["net"] < -0.005:
                flags.append(f"No zero-cost cap above +{MIN_CAP_PCT:.0f}% — net debit ${-(rg['net']) * n:,.0f}")
            if s.get("floor_tightened"):
                flags.append(f"A zero-cost cap would sit beyond +{MAX_CAP_PCT:.0f}% — capped there and the surplus premium buys a "
                             f"tighter floor (−{s['floor_actual']:.0f}% instead of −{s['floor_requested']:.0f}%)")
            if s["kp2"]:
                flags.append(f"Put SPREAD: protection runs from ${s['kp']:.0f} down to ${s['kp2']:.0f}; a fall below ${s['kp2']:.0f} is "
                             "unhedged — that tail is what you are NOT paying for.")
            if not s["kp"]:
                flags.append("No downside protection: the shares keep their full downside. Pair with a separate tail hedge "
                             "(e.g. a far-OTM put spread, or an index/sector put) if the loss limit matters.")
            if s.get("floor_deepened"):
                flags.append(f"Floor deepened from −{s['floor_requested']:.0f}% to −{s['floor_actual']:.0f}% to keep the "
                             "cap-to-floor band wide (constructive-sale guard)")
            yn = _tax_year_note(rg["expiry"])
            if s.get("model"):
                flags.append("INDICATIVE model price (flat-vol term extrapolation, no dividends/borrow, "
                             f"±{assumed_spread:g} vol-pt dealer markup) — the bank's real quote will differ.")
            if yn:
                flags.append(yn)
            res_rungs.append({
                "expiry": rg["expiry"], "dte": rg["dte"], "tax_year": rg["year"], "shares": n, "contracts": n // CONTRACT,
                "avg_basis": round(b, 2),
                "lots": [{"basis": a["basis"], "shares": a["shares"]} for a in rg["alloc"]],
                "structure": {"id": s["id"], "name": s["name"]}, "menu": rg["menu"],
                "put": _leg_view(s["put"], spot) if s["put"] else None,
                "put2": _leg_view(s["put2"], spot) if s["put2"] else None,
                "call": _leg_view(s["call"], spot),
                "net_credit_per_share": round(rg["net"], 2), "net_credit_total": round(rg["net"] * n, 0),
                "tax_funding_pct": round(rg["net"] * n / econ["worst_tax"] * 100, 0) if econ["worst_tax"] > 0 else None,
                "zero_cost": bool(s["zero_cost"]), "floor_pct": round(s["floor_actual"], 1) if s["kp"] else None,
                "cap_pct": round((s["kc"] / spot - 1) * 100, 1),
                "band_pct": round((s["kc"] - s["kp"]) / spot * 100, 1) if s["kp"] else None,
                "constructive_sale": structure_constructive(s, spot),
                "protection_pct": next((x["protection_pct"] for x in rg["menu"] if x["recommended"]), None),
                "stress_loss_pct": round(econ["stress_loss_pct"], 1), "stress_pct": STRESS_PCT,
                "density": rg["dens"].source,
                "p_called_pct": round(econ["p_called"] * 100, 1), "p_floor_breach_pct": round(econ["p_floor"] * 100, 1),
                "max_loss_vs_spot_pct": round((spot - s["kp"] + (-rg["net"])) / spot * 100, 1) if (s["kp"] and not s["kp2"]) else None,
                "worst_case_gain": round(econ["worst_gain"], 0), "worst_case_tax": round(econ["worst_tax"], 0),
                "expected_gain": round(econ["expected_gain"], 0), "expected_tax": round(econ["expected_tax"], 0),
                "put_tax_character": "long-term" if econ["put_rate"] == lt else "short-term",
                "scenarios": sc, "flags": flags,
                "venue": "FLEX/OTC" if s.get("model") else "Listed",
                "rfq": _rfq(tk, spot, rg, n, s) if s.get("model") else None,
                "tax_note": _structure_tax_note(s),
            })
            y = year_rows.setdefault(rg["year"], {"year": rg["year"], "worst_case_gain": 0.0, "worst_case_tax": 0.0,
                                                  "expected_gain": 0.0, "expected_tax": 0.0, "rungs": 0, "tickers": set()})
            y["worst_case_gain"] += econ["worst_gain"]
            y["worst_case_tax"] += econ["worst_tax"]
            y["expected_gain"] += econ["expected_gain"]
            y["expected_tax"] += econ["expected_tax"]
            y["rungs"] += 1
            y["tickers"].add(tk)
            tot_cov += n

        headline = None
        if rungs:
            ref = min(rungs, key=lambda x: abs(x["dte"] - 365))          # thresholds are calibrated to ~1y tenor
            # the timing read is about what the MARKET pays for a plain zero-cost collar at the requested floor, whichever
            # structure the menu ends up recommending
            ref_c = next((c for c in ref["menu_cands"] if c["id"] == "collar"), None) or ref["s"]
            headline = {"cap_pct": round((ref_c["kc"] / spot - 1) * 100, 1), "expiry": ref["expiry"]}
        sh_val = sp["gain_shares"] * spot
        gain_pct = (sp["unrealized_gain"] / sh_val * 100) if sh_val else 0.0
        embedded_tax = lt * sp["unrealized_gain"]
        vol = {"skew_pts": m["iv"]["skew_pts"] if m.get("iv") else None, "iv_hv_ratio": None}
        atm = (m["iv"] or {}).get("atm_iv")
        hv30 = m["ctx"].get("hv30")
        if atm and hv30:
            vol["iv_hv_ratio"] = atm / hv30
        e_days = None
        ne = m["ctx"].get("next_earnings")
        if ne:
            try:
                e_days = (datetime.strptime(ne, "%Y-%m-%d").date() - today).days
            except ValueError:
                pass
        timing = timing_assessment(spot=spot, gain_pct=gain_pct, embedded_tax=embedded_tax, headline=headline, vol=vol,
                                   trend=m["trend"], earnings_days=e_days, liq_spread=worst_spread or None,
                                   has_gain_lots=bool(sp["gain_lots"]), floor_pct=floor_pct)
        left_sh = max(0, cov_target[tk] - tot_cov)
        notes = []
        if sp["underwater_lots"]:
            notes.append(f"{sum(l['shares'] for l in sp['underwater_lots']):,} shares sit at a LOSS "
                         f"(≈${-sp['underwater_loss']:,.0f}) — not laddered: selling them harvests a long-term loss you can "
                         "use to absorb gains from the ladder.")
        if left_sh:
            why = "the annual budget is full" if mode == "budget" else "they don't fill a whole contract per rung"
            notes.append(f"{left_sh:,} gain shares are not placed in any rung ({why}) — carry them to the next roll/year.")
        out_positions.append({
            "ticker": tk, "spot": round(spot, 2),
            "summary": {"shares": sp["all_shares"], "gain_shares": sp["gain_shares"], "covered_shares": tot_cov,
                        "avg_basis_gain_lots": round(sp["avg_basis"], 2), "market_value": round(sp["all_shares"] * spot, 0),
                        "unrealized_gain": round(sp["unrealized_gain"], 0), "gain_pct": round(gain_pct, 1),
                        "tax_if_sold_today": round(embedded_tax, 0),
                        "underwater_shares": sum(l["shares"] for l in sp["underwater_lots"]),
                        "underwater_loss": round(sp["underwater_loss"], 0)},
            "timing": timing,
            "ladder": res_rungs,
            "context": {"atm_iv_pct": round(atm * 100, 1) if atm else None,
                        "hv30_pct": round(hv30 * 100, 1) if hv30 else None,
                        "iv_hv_ratio": round(vol["iv_hv_ratio"], 2) if vol["iv_hv_ratio"] else None,
                        "skew_pts": vol["skew_pts"], "next_earnings": ne,
                        "rsi14": round(m["trend"]["rsi14"], 0) if m["trend"].get("rsi14") is not None else None,
                        "vs_200d_pct": round(m["trend"]["ext_200"] * 100, 1) if m["trend"].get("ext_200") is not None else None,
                        "off_high_pct": round(m["trend"]["off_high_pct"], 1) if m["trend"].get("off_high_pct") is not None else None},
            "notes": notes,
            "overlay": _overlay_summary(m.get("overlay") or [], sp["gain_shares"], tot_cov, sp["avg_basis"], st, lt),
        })

    by_year = []
    for y in sorted(year_rows):
        row = year_rows[y]
        bud = budget if (mode == "budget" and budget) else None
        by_year.append({"year": y, "rungs": row["rungs"], "tickers": sorted(row["tickers"]),
                        "worst_case_gain": round(row["worst_case_gain"], 0), "worst_case_tax": round(row["worst_case_tax"], 0),
                        "expected_gain": round(row["expected_gain"], 0), "expected_tax": round(row["expected_tax"], 0),
                        "budget": bud, "headroom": round(bud - row["worst_case_gain"], 0) if bud else None,
                        "within_budget": bool(row["worst_case_gain"] <= bud + 1e-6) if bud else None})
    return {
        "positions": out_positions, "by_year": by_year,
        "deferral": {"tax_if_sold_today": round(sell_now_total, 0),
                     "pv_value_of_deferral": round(pv_defer_total, 0),
                     "basis": "Tax on the gain you carry TODAY, paid at each rung's expiry instead of now: "
                              "PV benefit = tax × (1 − e^(−r·T)), r = risk-free."},
        "income": _income_summary(out_positions, st),
        "warnings": warnings,
    }


def _income_summary(positions: list[dict], st: float) -> dict:
    net = tax_called = 0.0
    for p in positions:
        for r_ in p.get("ladder", []) or []:
            net += r_["net_credit_total"]
            tax_called += r_["worst_case_tax"]
    return {"net_premium_total": round(net, 0), "tax_if_all_called": round(tax_called, 0),
            "funding_pct": round(net / tax_called * 100, 0) if tax_called > 0 and net > 0 else 0.0,
            "after_tax_net_premium": round(net * (1 - st), 0) if net > 0 else round(net, 0),
            "note": "Net premium collected on day one across all rungs vs the tax due if every rung is called away. "
                    "After-tax figure assumes the premium is taxed as short-term income."}


async def run_concentration_ladder(*, positions: list[dict], tax: dict, mode: str = "budget",
                                   annual_gain_budget: Optional[float] = None, horizon_years: int = 3,
                                   num_rungs: int = 4, floor_pct: float = DEFAULT_FLOOR_PCT, coverage_pct: float = 100.0,
                                   defer_put_loss: bool = True, execution: str = "listed",
                                   flex_spread_vol_pts: float = FLEX_SPREAD_VOL_PTS, stance: str = "balanced",
                                   max_loss_pct: float = DEFAULT_MAX_LOSS_PCT, call_prob_pct: float = DEFAULT_CALL_PROB * 100,
                                   quote_source: str = "yfinance", user=None, db=None) -> dict:
    if not positions:
        return {"error": "Add at least one position."}
    positions = positions[:MAX_TICKERS]
    for p in positions:
        p["ticker"] = p["ticker"].strip().upper()
        p["lots"] = (p.get("lots") or [])[:MAX_LOTS]
    provider = get_provider(quote_source, user=user, db=db)
    today = date.today()
    try:
        r, r_label = await _get_sofr()
    except Exception:  # noqa: BLE001
        r, r_label = DEFAULT_RISK_FREE, "default"
    markets = []
    for p in positions:                                 # sequential: bounded memory on the 512 MiB instance
        markets.append(await _market_for_ticker(provider, p["ticker"], today, num_rungs, int(horizon_years * 365),
                                                floor_pct, r, execution, flex_spread_vol_pts, call_prob_pct / 100.0))
        markets[-1].setdefault("ticker", p["ticker"])
    plan = build_plan(markets, positions, tax=tax, mode=mode, budget=annual_gain_budget, defer_put_loss=defer_put_loss,
                      r=r, today=today, floor_pct=floor_pct, coverage_pct=coverage_pct, assumed_spread=flex_spread_vol_pts, stance=stance, max_loss_pct=max_loss_pct)
    plan.update({"success": True, "execution": execution, "execution_guide": EXECUTION_GUIDE, "as_of": today.isoformat(), "mode": mode, "horizon_years": horizon_years,
                 "assumptions": {"st_rate_pct": tax["st_rate_pct"], "lt_rate_pct": tax["lt_rate_pct"],
                                 "risk_free_pct": round(r * 100, 2), "risk_free_source": r_label,
                                 "floor_pct": floor_pct, "coverage_pct": coverage_pct,
                                 "straddle_defers_put_loss": defer_put_loss, "all_lots_long_term": True,
                                 "stance": stance, "max_loss_pct": max_loss_pct, "stress_pct": STRESS_PCT,
                                 "call_prob_pct": call_prob_pct,
                                 "min_band_pct": MIN_BAND_PCT, "probabilities": "market-implied (risk-neutral), not a forecast"},
                 "roll_plan": _roll_plan(plan),
                 "disclaimer": DISCLAIMER})
    return plan


EXECUTION_GUIDE = [
    {"venue": "Listed LEAPS", "who": "Any options-approved broker — no bank call needed.",
     "notes": "Standard strikes, expiries to ~2 years, American exercise (early-assignment risk on the short call around dividends). "
              "Fine for modest sizes; large orders move the market — work them with limit orders at mid."},
    {"venue": "FLEX options (Cboe, OCC-cleared)", "who": "A broker/dealer with FLEX access — usually a bank, private-bank or prime-broker "
     "options desk (most retail platforms can't). Ask for a quote via the Cboe FLEX Hub RFQ.",
     "notes": "Custom strike, any expiry (years out), European style available, OCC-cleared so no bilateral credit risk. "
              "FLEX has minimum opening sizes (they have changed over time) — confirm with the desk that each rung clears it."},
    {"venue": "OTC collar with a dealer bank", "who": "The bank's equity-derivatives / private-wealth desk, directly.",
     "notes": "Fully custom (size, dates, strikes, even a single multi-year structure). Needs an ISDA/credit arrangement, you carry "
              "counterparty risk, and shares may be pledged as collateral. At large size this is where pricing is usually most "
              "negotiable — get 2–3 competing quotes and compare with the indicative strikes here."},
    {"venue": "Before you call", "who": "Your tax adviser and, if you are an officer/director/10% holder or hold restricted stock, "
     "company counsel.",
     "notes": "Hedging can be restricted by company policy, Section 16 and Rule 144 — pre-clear it. Have your adviser confirm "
              "straddle (§1092) and constructive-sale (§1259) treatment of the exact structure."},
]


def _roll_plan(plan: dict) -> list[dict]:
    """Listed options only reach ~2y. For the rest of the horizon the ladder is ROLLED: when a rung nears expiry, either let it
    be called away (planned gain) or roll it out to the then-longest expiry with the same floor/zero-cost rule."""
    rows = []
    for p in plan["positions"]:
        for rg in p.get("ladder", []):
            exp = datetime.strptime(rg["expiry"], "%Y-%m-%d").date()
            rows.append({"ticker": p["ticker"], "review_on": (exp.toordinal() - 60), "expiry": rg["expiry"],
                         "action": ("If the stock is below the cap: roll the whole collar out (keeps the gain deferred; "
                                    "re-run this planner for fresh strikes). If it is near/above the cap: let it be "
                                    "called away — that gain is already budgeted in " + str(rg["tax_year"]) + ".")})
    for r_ in rows:
        r_["review_on"] = date.fromordinal(r_["review_on"]).isoformat()
    rows.sort(key=lambda x: x["review_on"])
    return rows
