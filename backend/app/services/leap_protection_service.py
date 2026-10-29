"""LEAP Protection — protect, monetise and diversify a concentrated, highly-appreciated stock WITHOUT selling it.

The brief: a long-held position with a large embedded gain that the owner will not sell (tax). They want long-dated (LEAPS)
protection that still leaves upside, and to get diversified out of single-stock risk. The engine builds every relevant
structure from LIVE chains — on the stock, on a broad index (ETF options standing in for their cash-settled index twins, index
futures) and in combination — prices each leg at an EXECUTABLE quote (buy at the ask, sell at the bid), simulates the joint
stock/index outcome to the LEAP expiry, applies a stated US tax model, and ranks the structures by expected annualised return
inside the user's loss and concentration limits.

Families
  references (hold / sell-now-and-buy-the-index) · protective puts · collars (zero-cost, put-spread, upside-kicker) ·
  put butterfly / 1×2 ratio · long straddle / strangle · covered calls, covered strangle / straddle · calendars & diagonals
  (LEAP put + rolling short options) · index hedges (puts, collar, put spread, short futures, cross-collar) · monetise &
  diversify (deep-ITM call → index, collar loan → index, collar + index-futures overlay, stock-call → index-call swap,
  deep-ITM stock call + deep-ITM index put, synthetic sale reference).

Model (stated so every number can be audited)
  * REAL-WORLD (P) measure, not risk-neutral — "best annualised return" only means something under real drift:
      index total return  μ_I = r + ERP;   stock  μ_S = r + β·ERP + α   (CAPM: idiosyncratic risk earns nothing; α = user view).
    The upside a structure gives away is therefore charged at the equity risk premium it surrenders.
    Volatility = the market's ATM implied vol at the horizon (default — options get NO vol-risk-premium edge; edges come from
    the ERP, skew, bid/ask, diversification and tax) or 2-year realised vol. ρ from 2 years of aligned daily returns and
    β = ρ·σ_S/σ_I with the SAME σs the simulation uses. Joint GBM, antithetic, fixed seed → reproducible.
  * Annualised return = geometric (CAGR) — exp(E[ln W_T/W_0]/T) − 1 — which charges volatility drag, so diversification shows
    up IN the return. Arithmetic mean and a CRRA(γ=3) certainty-equivalent are reported alongside. W_0 = market value today.
  * Entry legs trade at the live bid/ask. Index legs trade as the European cash-settled twin they are taxed as (SPX for
    SPY …): priced at the smile's Black value ± the ETF's half-spread — the same as the quote out of the money, but far below
    the ETF's American quote deep in the money (that gap is the buyer's early-exercise right). Index legs that outlive the
    horizon are marked at T off their own smile less half the spread. Rolling short legs (calendars/diagonals) are re-struck each cycle at a fixed moneyness (or fixed strike for a
    calendar) and priced off the short-dated smile (sticky-moneyness) less half the observed spread.
  * Short calls that finish in-the-money are BOUGHT BACK with cash (the premise is that the shares are kept). EARLY
    exercise of every written American option is decided along each path (`holder_exercises`: calls just before an
    ex-dividend date when the dividend beats the interest on the strike plus the put value; puts when the interest on the
    strike beats the call value plus dividends to come) — the writer then closes the leg at intrinsic, which is what it is
    worth at that point. Its probability and consequence (shares called away → gain realised) are reported per leg.
  * Dividends: the stock's projected ex-dates and amounts (latest payment, no growth) are paid along each path, taxed at
    the qualified rate — or the ordinary rate while a put / non-qualified call is on (§246(c)(4)) — and sent to the plan's
    destination. Cash earns SOFR; a negative cash balance is a margin loan at SOFR + spread.
Tax (US, LINEAR — a loss offsets other gains at the same rate; NOT tax advice)
  * Stock options in a STRADDLE with the shares (any long put; a short call that is not a qualified covered call): net gains
    short-term (the straddle stops the option's holding period from running — Temp. Reg. §1.1092(b)-2T), net losses DEFERRED
    into the stock's basis (§1092). A lone OTM covered call is a qualified covered call: its losses are recognised. Written
    options are always short-term (§1234(b)). Options that do not offset the shares (short puts, long calls) are recognised.
  * Index options on SPY/QQQ/IWM/DIA are taxed as their cash-settled twins (SPX/XSP, NDX/XND, RUT/MRUT, DJX — §1256 60/40);
    other ETFs at equity-option rates. Index futures: §1256 60/40.
  * Lens "defer" (default): the shares are never sold (held for step-up) — only tax actually triggered inside the horizon
    counts and a deferred straddle loss is worth nothing. Lens "liquidate": everything is sold at the horizon (deferred losses
    come back as basis).
  * Constructive sale (§1259): collars narrower than the band threshold, synthetic shorts and deep-ITM calls are flagged; a
    HIGH-risk structure cannot be the recommendation unless the user allows it.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import numpy as np
from scipy.special import ndtr

from .quote_providers import get_provider
from .hedging_service import _split_chain
from .derivative_income_service import _is_monthly_expiry, _get_sofr, DEFAULT_RISK_FREE
from .concentration_ladder_service import constructive_sale_flag, MIN_BAND_PCT

logger = logging.getLogger(__name__)

CONTRACT = 100
MIN_LEAP_DTE = 300                 # a "LEAP" horizon; below this we fall back to the longest listed ≥ FALLBACK_MIN_DTE
FALLBACK_MIN_DTE = 150
ROLL_TARGET_DTE = 45               # calendars/diagonals re-sell the short leg about every 45 days
N_PATHS = 20_000                   # antithetic pairs → 10k independent draws
SEED = 7
CRRA_GAMMA = 3.0
TAIL_Q = 0.05                      # CVaR level: the average of the worst 5% of horizon outcomes
WIDE_SPREAD_PCT = 20.0             # bid/ask wider than this (% of mid) gets a "work a limit" warning
CACHE_TTL = 900
DEFAULT_ERP = 5.0
DEFAULT_BORROW_SPREAD = 1.5        # securities-based line over SOFR, %
DEFAULT_LTV = 90.0                 # % of the put floor's present value a collar loan borrows
INDEX_1256 = {"SPY": "SPX / XSP", "QQQ": "NDX / XND", "IWM": "RUT / MRUT", "DIA": "DJX"}
SPX_PER_SPY = 10.0                 # 1 SPX ≈ 10 SPY (MES = $5 × SPX)

FAMILY_LABEL = {
    "reference": "Reference points",
    "puts": "Protective puts",
    "collars": "Collars",
    "spreads": "Butterflies & ratios",
    "vol": "Straddles & strangles",
    "income": "Covered calls & short premium",
    "calendar": "Calendars & diagonals",
    "index": "Index hedges",
    "diversify": "Monetise & diversify",
}

# Expiry grid: stock moves (%) from today; scenarios are named points on it — (id, label, mode, move) where mode 'market'
# moves the index with the stock at β and 'stock_only' leaves the index flat.
GRID_MOVES = list(range(-60, 85, 5))
SCENARIOS = [
    ("market_crash", "Market crash", "market", -30),
    ("stock_crash", "Stock-only crash", "stock_only", -40),
    ("bear", "Bear market", "market", -15),
    ("flat", "Flat", "market", 0),
    ("bull", "Bull market", "market", 15),
    ("rally", "Strong rally", "market", 40),
    ("stock_rally", "Stock-only melt-up", "stock_only", 60),
]

DISCLAIMER = ("Planning model, not tax or investment advice. Option taxation (straddles §1092, constructive sales §1259, "
              "qualified covered calls, §1256, wash sales, state rules) is fact-specific — confirm with a tax adviser before "
              "trading. Returns are model expectations under the stated assumptions, not forecasts or guarantees.")

ALTERNATIVES = [
    {"name": "Exchange fund", "note": "Contribute the shares to a partnership of other holders' stock; after ~7 years you "
     "redeem a diversified basket with your original basis carried over. No tax on contribution, illiquid, accredited/"
     "qualified-purchaser only, fees ~0.5–1%/yr. Diversifies the idiosyncratic risk without a sale."},
    {"name": "Variable prepaid forward (VPF)", "note": "A dealer pays ~75–90% of the value today against delivery of a "
     "variable number of shares in 2–5 years — economically the collar-loan row above in one OTC contract. Rev. Rul. "
     "2003-7 conditions apply; minimums are usually $1M+."},
    {"name": "Charitable remainder trust / donor-advised fund", "note": "Gifting appreciated shares removes the embedded gain "
     "entirely (and earns a deduction); a CRT returns an income stream. Only if a charitable goal exists."},
    {"name": "Direct indexing + loss harvesting", "note": "Build an index sleeve as individual stocks and harvest their losses "
     "to offset gains realised by trimming the concentrated position a little each year."},
]


# ---------------------------------------------------------------------------
# small numerics
# ---------------------------------------------------------------------------

def _f(x, d: float = 0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else d
    except (TypeError, ValueError):
        return d


def black(F, K, T, r, sig, right: str):
    """Black-76 price of a European option on forward F (numpy-friendly)."""
    F = np.asarray(F, float)
    K = np.asarray(K, float)
    sig = np.maximum(np.asarray(sig, float), 1e-4)
    T = max(float(T), 1e-6)
    sd = sig * math.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    df = math.exp(-r * T)
    if right == "C":
        return df * (F * ndtr(d1) - K * ndtr(d2))
    return df * (K * ndtr(-d2) - F * ndtr(-d1))


def implied_vol(price: float, F: float, K: float, T: float, r: float, right: str) -> Optional[float]:
    """Bisection IV from a Black-76 price; None when the price is at/below intrinsic or out of range."""
    df = math.exp(-r * T)
    intrinsic = df * max(F - K, 0.0) if right == "C" else df * max(K - F, 0.0)
    if not (price > intrinsic + 1e-6):
        return None
    lo, hi = 1e-3, 4.0
    if float(black(F, K, T, r, hi, right)) < price:
        return None
    for _ in range(70):
        mid = 0.5 * (lo + hi)
        if float(black(F, K, T, r, mid, right)) > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def intrinsic(U, K, right: str):
    return np.maximum(U - K, 0.0) if right == "C" else np.maximum(K - U, 0.0)


# ---------------------------------------------------------------------------
# a chain with its own forward + smile
# ---------------------------------------------------------------------------

@dataclass
class Quote:
    K: float
    bid: float
    ask: float
    oi: int = 0
    volume: int = 0

    @property
    def mid(self) -> float:
        return 0.5 * (self.bid + self.ask)

    @property
    def ok(self) -> bool:
        return self.bid > 0 and self.ask > 0 and self.ask >= self.bid


class Book:
    """One expiry: quotes, the parity-implied forward (so dividends/borrow are the market's), and an OTM-mid IV smile in
    log-moneyness (flat beyond the quoted wings). yfinance's own IVs are unusable on deep-ITM strikes — never read here."""

    def __init__(self, S: float, T: float, r: float, rows: list, expiry: str, q_hint: float = 0.0):
        self.S, self.T, self.r, self.expiry = float(S), max(float(T), 1e-4), float(r), expiry
        self.european = False       # True: legs trade as the cash-settled European twin (SPX for SPY …) — see _leg
        self.calls: dict[float, Quote] = {}
        self.puts: dict[float, Quote] = {}
        for K, right, bid, ask, oi, vol in rows:
            q = Quote(float(K), _f(bid), _f(ask), int(_f(oi)), int(_f(vol)))
            (self.calls if right == "C" else self.puts)[q.K] = q
        self.F = self._forward(q_hint)
        self.q = self.r - math.log(self.F / self.S) / self.T          # market carry (dividends + borrow)
        self._smile()

    def _forward(self, q_hint: float) -> float:
        both = [K for K in self.calls if K in self.puts and self.calls[K].ok and self.puts[K].ok]
        near = sorted(both, key=lambda K: abs(K - self.S))[:6]
        g = math.exp(self.r * self.T)
        ests = [K + g * (self.calls[K].mid - self.puts[K].mid) for K in near]
        ests = [x for x in ests if 0.7 * self.S < x < 1.5 * self.S]
        if ests:
            return float(np.median(ests))
        return self.S * math.exp((self.r - q_hint) * self.T)

    def _smile(self) -> None:
        ks, vs = [], []
        for K in sorted(set(self.calls) | set(self.puts)):
            right = "P" if K < self.F else "C"
            q = (self.puts if right == "P" else self.calls).get(K)
            if q is None or not q.ok:
                continue
            iv = implied_vol(q.mid, self.F, K, self.T, self.r, right)
            if iv is not None and 0.03 < iv < 3.0:
                ks.append(math.log(K / self.F))
                vs.append(iv)
        if len(ks) < 3:
            raise ValueError(f"not enough liquid strikes to fit a smile for {self.expiry}")
        self.k = np.array(ks)
        self.v = np.array(vs)
        self.atm_iv = float(np.interp(0.0, self.k, self.v))

    def iv(self, K) -> np.ndarray | float:
        out = np.interp(np.log(np.asarray(K, float) / self.F), self.k, self.v)
        return float(out) if np.ndim(out) == 0 else out

    def iv_at(self, K, F) -> np.ndarray:
        """Smile at strike K for a (moved) forward F — sticky-moneyness."""
        return np.interp(np.log(np.asarray(K, float) / np.asarray(F, float)), self.k, self.v)

    def delta(self, K: float, right: str) -> float:
        sd = self.iv(K) * math.sqrt(self.T)
        d1 = (math.log(self.F / K) + 0.5 * sd * sd) / sd
        e = math.exp(-self.q * self.T)
        return float(e * ndtr(d1)) if right == "C" else float(-e * ndtr(-d1))

    def book(self, right: str) -> dict:
        return self.calls if right == "C" else self.puts

    def strikes(self, right: str) -> list[float]:
        return sorted(K for K, q in self.book(right).items() if q.ok)

    def pick(self, right: str, target: float, side: str = "nearest") -> Optional[float]:
        ks = self.strikes(right)
        if not ks:
            return None
        if side == "below":
            c = [k for k in ks if k <= target + 1e-9]
            return c[-1] if c else None
        if side == "above":
            c = [k for k in ks if k >= target - 1e-9]
            return c[0] if c else None
        return min(ks, key=lambda k: abs(k - target))

    def for_delta(self, right: str, target: float) -> Optional[float]:
        """OTM strike whose |delta| is nearest `target`."""
        ks = [k for k in self.strikes(right) if (k >= self.S if right == "C" else k <= self.S)]
        if not ks:
            return None
        return min(ks, key=lambda k: abs(abs(self.delta(k, right)) - target))

    def zero_cost_call(self, cost: float, kmin: float) -> Optional[float]:
        """Highest call (≥ kmin) whose BID pays for `cost` — the most upside a zero-cost structure can keep."""
        ks = [k for k in self.strikes("C") if k >= kmin and self.calls[k].bid >= cost]
        return ks[-1] if ks else None


# ---------------------------------------------------------------------------
# structures
# ---------------------------------------------------------------------------

@dataclass
class Leg:
    und: str                    # "S" stock | "I" index
    right: str                  # "C" | "P" | "F" (futures)
    K: float                    # strike (futures: the entry futures price)
    qty: float                  # per stock share held; + long, − short (index legs in index units)
    px: float                   # executable per-unit price: ask if long, bid if short; 0 for futures
    bid: float = 0.0
    ask: float = 0.0
    mid: float = 0.0
    iv: float = 0.0
    delta: float = 0.0          # spot delta per unit
    T_leg: float = 0.0          # years to this leg's own expiry
    expiry: str = ""
    oi: int = 0
    volume: int = 0
    half_spread: float = 0.0    # fraction of mid — used to mark a leg that outlives the horizon
    carry: float = 0.0          # underlying's carry yield (for marking)
    european: bool = False      # priced/settled as the European cash-settled index twin → never assigned early

    @property
    def spread_pct(self) -> Optional[float]:
        return (self.ask - self.bid) / self.mid * 100.0 if self.mid > 0 and self.right != "F" else None


@dataclass
class Roll:
    """A short-dated option re-sold every cycle on the STOCK (calendar / diagonal leg).

    Cycle j is priced at the ATM vol the market implies for that window (today's short-dated ATM for the first cycle, the
    forward vol between the short expiry and the LEAP after that) plus TODAY's short-dated skew at the strike's moneyness —
    so under the 'implied' vol view a rolled option is priced at the same vol the stock is simulated with."""
    right: str
    qty: float                  # per share; −1 = short one per share
    tau: float                  # cycle length, years
    r: float
    q: float                    # carry of the short-dated chain
    half_spread: float
    skew_k: np.ndarray          # log-moneyness grid of today's short-dated smile
    skew_v: np.ndarray          # smile minus its ATM vol
    atm0: float                 # today's short-dated ATM vol (cycle 0)
    atm_fwd: float              # forward ATM vol for later cycles
    m: float = 0.0              # strike as a multiple of the cycle-start spot (relative roll) …
    K_abs: float = 0.0          # … or a fixed strike (calendar) — only re-sold while it is out-of-the-money
    delta: float = 0.0          # today's delta of the first short
    expiry: str = ""            # today's first short-dated expiry
    first_K: float = 0.0
    first_bid: float = 0.0
    model: Optional["JumpModel"] = None   # 'implied' view: cycles after the first are priced by the simulation's own model

    def strike(self, Sj: np.ndarray) -> np.ndarray:
        return np.full_like(Sj, self.K_abs) if self.K_abs > 0 else self.m * Sj

    def active(self, Sj: np.ndarray) -> np.ndarray:
        """1 where this cycle's option is sold. A fixed-strike calendar leg is not re-sold once it is in-the-money."""
        if self.K_abs <= 0:
            return np.ones_like(Sj)
        return (Sj > self.K_abs).astype(float) if self.right == "P" else (Sj < self.K_abs).astype(float)

    def premium(self, Sj: np.ndarray, j: int = 0) -> np.ndarray:
        """Cycle 0 trades at TODAY's short-dated smile. Later cycles: under the 'implied' view at the simulation's own
        model (the market stays fair — no edge but the spread); otherwise at the forward ATM vol + today's skew (the market
        keeps pricing like today while the stock follows history — that gap is the vol-risk-premium edge)."""
        F = Sj * math.exp((self.r - self.q) * self.tau)
        K = self.strike(Sj)
        if j > 0 and self.model is not None:
            if self.K_abs > 0:
                mid = jump_price(F, K, self.tau, self.r, self.model, self.right)
            else:                                            # homogeneous in S: price once per unit spot
                mid = Sj * float(jump_price(math.exp((self.r - self.q) * self.tau), self.m, self.tau, self.r, self.model,
                                            self.right))
        else:
            iv = np.maximum((self.atm0 if j == 0 else self.atm_fwd) + np.interp(np.log(K / F), self.skew_k, self.skew_v),
                            0.03)
            mid = black(F, K, self.tau, self.r, iv, self.right)
        return mid * (1 - self.half_spread) if self.qty < 0 else mid * (1 + self.half_spread)


@dataclass
class Strategy:
    id: str
    family: str
    name: str
    legs: list = field(default_factory=list)
    rolls: list = field(default_factory=list)
    keep_cash: bool = False     # with the INDEX destination the credit stays in T-bills (the trade already holds the index)
    loan_ps: float = 0.0        # per-share loan principal — goes to the chosen destination
    sell_now: bool = False
    blurb: str = ""
    cs: dict = field(default_factory=lambda: {"risk": "low", "note": ""})
    warnings: list = field(default_factory=list)


DESTS = ("index", "bond", "cash")


@dataclass
class Env:
    S0: float
    I0: float
    B: float                    # average cost basis per share
    T: float
    r: float
    r_b: float                  # borrow rate
    q_s: float                  # dividend yield received (physical)
    q_i: float
    lt: float
    st: float
    idx1256: bool
    lens: str                   # "defer" | "liquidate"
    dest: str = "cash"          # where day-one premium, loan and sale proceeds go: "index" | "bond" | "cash"
    bond_y: float = 0.0         # fixed coupon/yield of the bond sleeve (held to the horizon, no default)
    div_t: tuple = ()           # the stock's ex-dividend dates inside the horizon (years from today) …
    div_amt: tuple = ()         # … and the $ per share going ex on each (empty: q_s paid as cash each step)

    @property
    def b1256(self) -> float:
        return 0.6 * self.lt + 0.4 * self.st

    def cash_growth(self, cash: np.ndarray, dt: float) -> np.ndarray:
        """Positive cash earns SOFR after ordinary tax; a negative balance is a margin loan (interest not deducted)."""
        return cash * np.exp(np.where(cash >= 0, self.r * (1 - self.st), self.r_b) * dt)


def _has_long_stock_put(st: Strategy) -> bool:
    return any(l.und == "S" and l.right == "P" and l.qty > 0 for l in st.legs)


def _qcc(st: Strategy, S0: float) -> bool:
    """Every short stock call is OTM at writing and there is no put → qualified covered call (no straddle)."""
    if _has_long_stock_put(st):
        return False
    for l in st.legs:
        if l.und == "S" and l.right == "C" and l.qty < 0 and l.K < S0:
            return False
    return True


def _straddle(st: Strategy, S0: float) -> bool:
    """Do the static stock-option legs offset the shares (§1092)?"""
    if _has_long_stock_put(st):
        return True
    shorts = [l for l in st.legs if l.und == "S" and l.right == "C" and l.qty < 0]
    return bool(shorts) and not _qcc(st, S0)


def sleeve_start(st: Strategy, env: Env) -> tuple[float, float, float]:
    """(cash kept, $ sent to the chosen destination, tax paid today) per share at t=0."""
    if st.sell_now:
        tax0 = env.lt * max(env.S0 - env.B, 0.0)
        return 0.0, env.S0 - tax0, tax0
    cash0 = -sum(l.qty * l.px for l in st.legs)
    out = st.loan_ps
    if cash0 > 0 and not (st.keep_cash and env.dest == "index"):
        out, cash0 = out + cash0, 0.0
    return cash0, out, 0.0


def premium_block(st: Strategy, env: Env, o: dict, *, shares: int, index: str) -> Optional[dict]:
    """The money this plan puts to work today (premium collected, loan, or sale proceeds) and what it is worth at expiry —
    so it is visible that its return is part of the plan's numbers. Index: the simulation's expected value (dividends after
    tax); bond / T-bills: their after-tax growth."""
    cash0, out0, _ = sleeve_start(st, env)
    kept = max(cash0, 0.0)
    today = out0 + kept
    if today * shares < 1:
        return None
    T = env.T
    tb = lambda x: x * math.exp(env.r * (1 - env.st) * T)            # noqa: E731
    if env.dest == "index":
        fv_out = float(np.mean(o["idx_val"]))
    elif env.dest == "bond":
        fv_out = out0 * (1 + env.bond_y * (1 - env.st)) ** T
    else:
        fv_out = tb(out0)
    fv = fv_out + tb(kept)
    src = ("sale proceeds after tax" if st.sell_now else
           "premium + loan" if (st.loan_ps > 0 and today > st.loan_ps + 1e-9) else "loan" if st.loan_ps > 0 else "premium")
    if kept > 0 and out0 <= 0:
        where = "T-bills (collateral)"
    elif kept > 0:
        where = f"{dest_label(env, index)} + T-bills"
    else:
        where = dest_label(env, index)
    return {"source": src, "today_total": round(today * shares, 0), "where": where,
            "at_expiry_total": round(fv * shares, 0), "expected": env.dest == "index" and out0 > 0,
            "kept_as_collateral": kept > 0}


def dividend_block(st: Strategy, env: Env, o: dict, *, shares: int, index: str) -> Optional[dict]:
    """The stock's dividends over the horizon: what is paid, the tax rate this plan leaves them at (a hedge can turn them
    into ordinary income), where the after-tax cash goes and what it is worth at expiry — it is in every number."""
    gross = o["div_gross"]
    if st.sell_now or gross * shares < 1:
        return None
    dest = o["div_dest"]
    qualified = abs(o["div_rate"] - env.lt) < 1e-12
    return {"payments": o["div_count"], "per_payment": round(env.div_amt[0], 4) if env.div_amt else None,
            "gross_total": round(gross * shares, 0), "after_tax_total": round(o["div_in"] * shares, 0),
            "rate_pct": round(o["div_rate"] * 100, 1), "qualified": qualified,
            "why": None if qualified else ("the put on your shares stops the qualified-dividend holding clock (§246(c)(4))"
                                          if _has_long_stock_put(st) else
                                          "the in-the-money call is not a qualified covered call (§246(c)(4))"),
            "where": {"index": index, "bond": f"a {env.bond_y * 100:.2f}% bond", "cash": "T-bills"}[dest],
            "at_expiry_total": round(float(np.mean(o["divs"])) * shares, 0), "expected": dest == "index"}


def _assign_level(p: float) -> str:
    return "high" if p >= 0.25 else "medium" if p >= 0.05 else "low" if p > 0 else "none"


def assignment_block(st: Strategy, env: Env, o: dict, times: np.ndarray, *, shares: int, ticker: str, index: str,
                     today: date) -> list[dict]:
    """Every written option: the chance (on the simulated paths) that its holder exercises before expiry, the typical date,
    what assignment would do, and what to do instead. The plan's numbers already assume the writer closes the leg at
    intrinsic the moment early exercise pays the holder — so the cost is in the returns; this says how likely it is."""
    from datetime import timedelta

    def typical(ts: np.ndarray) -> Optional[str]:
        ts = ts[np.isfinite(ts)]
        return (today + timedelta(days=int(round(float(np.median(ts)) * 365)))).isoformat() if len(ts) else None

    out = []
    for i, l in enumerate(st.legs):
        if l.right == "F" or l.qty >= 0:
            continue
        n_ct = int(round(abs(l.qty) * shares / CONTRACT))
        kind = "call" if l.right == "C" else "put"
        row = {"leg": f"{ticker if l.und == 'S' else index} ${l.K:g} {kind}", "contracts": n_ct, "american": not l.european}
        if l.european:
            twin = INDEX_1256.get(index, "its index twin")
            now = bool(holder_exercises(l.right, np.array([env.I0]), l.K, l.T_leg, env.r, l.iv, env.q_i, 0.0)[0])
            row.update({"p_pct": 0.0, "level": "none", "when": None, "twin": twin, "if_assigned": None,
                        "action": f"Trade it as {twin} — European and cash-settled, so it cannot be assigned early.",
                        "note": (f"As a {index} option it would be American: this deep in the money its buyer would exercise "
                                 f"almost at once — that right is why {index} bids ${l.bid:,.2f} against ${l.mid:,.2f} "
                                 f"European. Priced here as {twin}." if now else
                                 f"{index} options are American and can be assigned early; {twin} cannot.")})
            out.append(row)
            continue
        hit = o["early"][i]
        p = float(hit.mean())
        row.update({"p_pct": round(p * 100, 1), "level": _assign_level(p), "twin": None,
                    "when": typical(o["early_t"][i][hit]) if p > 0 else None})
        if l.und == "S" and l.right == "C":
            gain = max(l.K - env.B, 0.0) * abs(l.qty) * shares
            row.update({"trigger": "ex-dividend",
                        "if_assigned": f"{n_ct * CONTRACT:,} shares called away at ${l.K:g} — realises ${gain:,.0f} of gain "
                                       f"(≈${env.lt * gain:,.0f} long-term tax) and loses that dividend",
                        "action": "Before each ex-dividend date: if the call is in the money and its time value is below "
                                  "the dividend, buy it back or roll it out."})
        elif l.und == "S":
            row.update({"trigger": "deep in the money",
                        "if_assigned": f"You buy {n_ct * CONTRACT:,} more shares at ${l.K:g} (${l.K * n_ct * CONTRACT:,.0f}) "
                                       f"— more {ticker}, not less",
                        "action": "When the put is deep in the money with almost no time value left, buy it back or roll it."})
        else:
            row.update({"trigger": "deep in the money",
                        "if_assigned": f"{n_ct * CONTRACT:,} {index} shares {'put to you' if l.right == 'P' else 'called away'} "
                                       f"at ${l.K:g}",
                        "action": "Close or roll it before it is deep in the money."})
        out.append(row)
    for ro in st.rolls:
        if ro.qty >= 0:
            continue
        p = float((o["roll_early"] > 0).mean())
        calls = ro.right == "C"
        out.append({"leg": f"Rolled {ticker} {'calls' if calls else 'puts'}", "contracts": int(round(abs(ro.qty) * shares / CONTRACT)),
                    "american": True, "p_pct": round(p * 100, 1), "level": _assign_level(p), "when": None, "twin": None,
                    "trigger": "ex-dividend" if calls else "deep in the money",
                    "if_assigned": ("shares called away before an ex-date — realises gain on low-basis shares" if calls else
                                    "more shares put to you"),
                    "action": ("Roll before the ex-date whenever an in-the-money call's time value is below the dividend."
                               if calls else "Roll a deep in-the-money put before its time value is gone.")})
    return out


def margin_warnings(st: Strategy, env: Env, *, shares: int, index: str) -> list[str]:
    """Short index options are not covered by your shares: estimate the Reg-T margin (broad-based index ETF rule —
    premium + 15% of the index − out-of-the-money amount, at least premium + 10%). Portfolio margin is usually lower."""
    out = []
    for l in st.legs:
        if l.und != "I" or l.right == "F" or l.qty >= 0:
            continue
        otm = max(env.I0 - l.K, 0.0) if l.right == "P" else max(l.K - env.I0, 0.0)
        per = max(l.px + 0.15 * env.I0 - otm, l.px + 0.10 * (l.K if l.right == "P" else env.I0))
        req = per * abs(l.qty) * shares
        out.append(f"Selling {int(round(abs(l.qty) * shares / CONTRACT))} {index} ${l.K:g} {'puts' if l.right == 'P' else 'calls'} "
                   f"needs margin — about ${req:,.0f} under Reg-T (less with portfolio margin). T-bills and Treasuries count "
                   f"as collateral, so the premium can earn interest while it backs the position.")
    return out


# ---------------------------------------------------------------------------
# THE evaluator — one function drives the Monte-Carlo, the scenarios and the payout ledger, so they always reconcile
# ---------------------------------------------------------------------------

def holder_exercises(right: str, U: np.ndarray, K, tau: float, r: float, iv: float, q: float, D: float) -> np.ndarray:
    """Would the HOLDER of an American option exercise it now (so the writer is assigned)? Exercising is better than
    keeping the option alive as a European when —
      call (only just before an ex-dividend date): the dividend beats the interest on the strike plus the put's insurance,
           D > K·(1 − e^{−rτ}) + P_e   — so calls on low-yield stocks are almost never called early;
      put  (any date): the interest on the strike beats the call's value plus the dividends still to come,
           K·(1 − e^{−rτ}) > C_e + U·(1 − e^{−qτ})   — deep in-the-money puts are put early when rates are high.
    The American continuation is worth at least the European, so this calls exercise a little EARLY — conservative for
    the writer. Cheap screens run first; Black is priced only where the screen passes."""
    U = np.asarray(U, float)
    hit = np.zeros(U.shape, bool)
    if tau <= 1e-6:
        return hit
    K = np.broadcast_to(np.asarray(K, float), U.shape)
    carry_k = K * (1.0 - math.exp(-r * tau))
    if right == "C":
        if D <= 0:
            return hit
        m = (U > K) & (D > carry_k)
        if m.any():
            F = (U[m] - D) * math.exp((r - q) * tau)
            hit[m] = D > carry_k[m] + black(F, K[m], tau, r, iv, "P")
        return hit
    m = (U < K) & (carry_k > U * (1.0 - math.exp(-q * tau)))
    if m.any():
        F = U[m] * math.exp((r - q) * tau)
        hit[m] = carry_k[m] > black(F, K[m], tau, r, iv, "C") + U[m] * (1.0 - math.exp(-q * tau))
    return hit


def _american_short(l: "Leg") -> bool:
    """A written option the holder can exercise early: stock options always; index legs unless they trade as the
    European cash-settled twin."""
    return l.right != "F" and l.qty < 0 and not l.european


def dividend_rate(st: Strategy, env: Env) -> float:
    """Tax rate on the stock's dividends while the plan is on. §246(c)(4) / §1(h)(11)(B)(iii): a day on which you hold a
    put on the shares, or have written a call that is NOT a qualified covered call, does not count toward the 61-day
    holding test — so a collar or protective put turns the dividends into ordinary income, however long you have owned
    the shares. A lone out-of-the-money covered call (QCC), short puts and index hedges leave them qualified."""
    return env.st if _straddle(st, env.S0) else env.lt


def _dividends_on_grid(times: np.ndarray, env: Env) -> np.ndarray:
    """$ per share going ex at each grid date. Without a schedule the yield is paid as cash at each step end."""
    out = np.zeros(len(times))
    if env.div_t:
        for t, a in zip(env.div_t, env.div_amt):
            k = int(np.argmin(np.abs(times - t)))
            if k > 0 and abs(times[k] - t) < 1e-6:
                out[k] += a
    elif env.q_s > 0:
        out[1:] = env.q_s * env.S0 * np.diff(times)
    return out


def evaluate(st: Strategy, S: np.ndarray, I: np.ndarray, times: np.ndarray, cycle: np.ndarray, env: Env) -> dict:
    """Per-share outcome at the horizon for price paths S, I of shape (n, len(times)).

    `cycle[j]` = the grid index at which a rolled short sold at t_j settles (−1: none sold); a boolean array means "settles
    at the next date" (one step per cycle).

    Along each path, at every date: rolled shorts are sold / settled; written AMERICAN options are checked for early
    exercise (`holder_exercises`) — when it pays the holder, the writer closes the leg at intrinsic (what an American option
    is worth at that point: buying it back before the ex-date keeps the shares; being assigned costs the same cash but
    hands over low-basis shares); and the stock's dividends are paid, taxed (`dividend_rate`) and sent to the plan's
    destination.

    Returns arrays: W (after-tax wealth at T under the chosen lens), tax (tax paid inside the horizon), deferred (straddle
    losses pushed into the stock's basis), and the LEDGER pieces that sum exactly to W: shares, divs (dividend sleeve),
    legs[i] (each static leg's settlement, nominal — at expiry, or earlier if closed early), rolls (net premium kept from
    rolled legs, nominal), cash (today's premium + all interest), sleeve (index or bond bought with the proceeds), debt,
    liq_tax. Plus `tax_upfront` (sell-now), `early[i]` / `early_t[i]` (leg closed early: mask and date) and `roll_early`
    (rolled shorts closed early per path)."""
    n = S.shape[0]
    T = float(times[-1])
    last = len(times) - 1
    ST, IT = S[:, -1], I[:, -1]
    tax = np.zeros(n)
    deferred = np.zeros(n)
    cash0, out0, tax_upfront = sleeve_start(st, env)
    shares = 0.0 if st.sell_now else 1.0
    units = out0 / env.I0 if env.dest == "index" else 0.0
    bond0 = out0 if env.dest == "bond" else 0.0
    cash = np.full(n, cash0 + (out0 if env.dest == "cash" else 0.0))
    cycle = np.asarray(cycle)
    ends = np.where(cycle, np.arange(len(cycle)) + 1, -1) if cycle.dtype == bool else cycle.astype(int)

    long_put = _has_long_stock_put(st)
    legs = st.legs
    amer = [_american_short(l) for l in legs]
    open_ = [np.ones(n, bool) for _ in legs]
    settle = [np.zeros(n) for _ in legs]
    early_t = [np.full(n, np.nan) for _ in legs]
    rolls_nominal = np.zeros(n)
    roll_early = np.zeros(n)

    # dividends → the plan's destination (T-bills when the trade already holds the index and keeps its cash there)
    div_dest = "cash" if (st.keep_cash and env.dest == "index") else env.dest
    div_rate = dividend_rate(st, env)
    divs = _dividends_on_grid(times, env) * shares
    div_units_eff = np.zeros(n)          # index units bought with dividends, grown by the index's own after-tax yield
    div_units_px = np.zeros(n)           # … the same units without that growth (their price-gain basis)
    div_fixed = 0.0                      # bond / T-bill sleeve value at T
    div_in = 0.0                         # after-tax dividends invested (cost basis)

    def close_roll(p: dict, mask: np.ndarray, U: np.ndarray) -> np.ndarray:
        nonlocal cash, tax, deferred, rolls_nominal
        ro = p["ro"]
        pay = intrinsic(U, p["K"], ro.right) * mask
        g = ro.qty * (pay - p["prem"] * mask)                # the cycle's P&L
        cash = cash + ro.qty * pay
        rolls_nominal = rolls_nominal + g
        if ro.right == "C":
            defer_loss = long_put or not (ro.K_abs == 0 and ro.m >= 1.0)
        else:                                                # short put: offsets only a long put (calendar)
            defer_loss = long_put
        tax = tax + env.st * np.maximum(g, 0.0)              # written options: short-term
        if defer_loss:
            deferred = deferred + np.maximum(-g, 0.0)
        else:
            tax = tax + env.st * np.minimum(g, 0.0)
        p["alive"] = p["alive"] & ~mask
        return mask

    pending: list[dict] = []
    n_cycle = 0
    for j in range(last):
        if ends[j] >= 0:
            for ro in st.rolls:
                on = ro.active(S[:, j])
                prem = ro.premium(S[:, j], n_cycle) * on
                cash = cash - ro.qty * prem                  # a short (qty<0) receives the premium
                pending.append({"ro": ro, "e": int(ends[j]), "K": ro.strike(S[:, j]), "prem": prem, "alive": on > 0})
            n_cycle += 1
        cash = env.cash_growth(cash, float(times[j + 1] - times[j]))
        k = j + 1
        tk = float(times[k])
        D = float(divs[k])
        if k < last:                                         # early exercise is decided just before the ex-date
            for i, l in enumerate(legs):
                if not amer[i] or l.T_leg <= tk + 1e-6 or not open_[i].any():
                    continue
                U = S[:, k] if l.und == "S" else I[:, k]
                hit = open_[i] & holder_exercises(l.right, U, l.K, l.T_leg - tk, env.r, l.iv,
                                                  env.q_s if l.und == "S" else env.q_i, D if l.und == "S" else 0.0)
                if hit.any():
                    amt = np.where(hit, l.qty * intrinsic(U, l.K, l.right), 0.0)
                    cash = cash + amt
                    settle[i] = settle[i] + amt
                    open_[i] = open_[i] & ~hit
                    early_t[i] = np.where(hit, tk, early_t[i])
            for p in pending:
                ro = p["ro"]
                if p["e"] > k and ro.qty < 0 and p["alive"].any():
                    hit = p["alive"] & holder_exercises(ro.right, S[:, k], p["K"], float(times[p["e"]]) - tk, env.r,
                                                        max(ro.atm0, 0.05), env.q_s, D)
                    if hit.any():
                        roll_early = roll_early + close_roll(p, hit, S[:, k])
        for p in pending:
            if p["e"] == k:
                close_roll(p, p["alive"].copy(), S[:, k])
        pending = [p for p in pending if p["e"] > k]
        if D > 0:                                            # shares held through the ex-date collect the dividend
            d = D * (1 - div_rate)
            div_in += d
            if div_dest == "index":
                div_units_px = div_units_px + d / I[:, k]
                div_units_eff = div_units_eff + d / I[:, k] * math.exp(env.q_i * (1 - env.lt) * (T - tk))
            elif div_dest == "bond":
                div_fixed += d * (1 + env.bond_y * (1 - env.st)) ** (T - tk)
            else:
                div_fixed += d * math.exp(env.r * (1 - env.st) * (T - tk))

    stock_pnl = np.zeros(n)
    stock_legs = [l for l in legs if l.und == "S"]
    early = [~o for o in open_]
    for i, l in enumerate(legs):
        U = ST if l.und == "S" else IT
        if l.right == "F":
            val = U - l.K
        elif l.T_leg > T + 1e-6:                             # outlives the horizon → mark it
            tau = l.T_leg - T
            Fw = U * math.exp((env.r - l.carry) * tau)
            mid = black(Fw, l.K, tau, env.r, l.iv, l.right)
            val = mid * (1 - l.half_spread) if l.qty > 0 else mid * (1 + l.half_spread)
        else:
            val = intrinsic(U, l.K, l.right)
        amt = np.where(open_[i], l.qty * val, 0.0)
        settle[i] = settle[i] + amt
        cash = cash + amt
        g = settle[i] - l.qty * l.px
        if l.und == "S":
            stock_pnl += g
        else:
            if l.right == "F" or env.idx1256:
                rate = env.b1256
            elif l.qty > 0 and T > 1.0:
                rate = env.lt
            else:
                rate = env.st
            tax += rate * g                                  # not offsetting the shares → recognised

    if stock_legs:
        if _straddle(st, env.S0):
            tax += env.st * np.maximum(stock_pnl, 0.0)
            deferred += np.maximum(-stock_pnl, 0.0)
        else:
            all_long = all(l.qty > 0 for l in stock_legs)
            tax += (env.lt if (all_long and T > 1.0) else env.st) * stock_pnl

    stock_val = shares * ST
    idx_val = units * IT * math.exp(env.q_i * (1 - env.lt) * T)
    bond_val = bond0 * (1 + env.bond_y * (1 - env.st)) ** T     # coupons taxed as ordinary income, reinvested
    div_val = div_units_eff * IT + div_fixed
    debt = st.loan_ps * math.exp(env.r_b * T)
    liq_tax = np.zeros(n)
    if env.lens == "liquidate":
        if shares:
            liq_tax = liq_tax + env.lt * (stock_val - (env.B + deferred))
        ix_rate = env.lt if T > 1.0 else env.st
        if units:                                            # price gain only — reinvested dividends were taxed
            liq_tax = liq_tax + ix_rate * (units * IT - out0)
        if div_dest == "index" and div_in > 0:
            liq_tax = liq_tax + ix_rate * (div_units_px * IT - div_in)
    W = stock_val + idx_val + bond_val + div_val + cash - debt - tax - liq_tax
    legs_total = np.sum(settle, axis=0) if settle else np.zeros(n)
    return {"W": W, "tax": tax, "deferred": deferred, "stock_val": stock_val, "idx_val": idx_val, "bond_val": bond_val,
            "divs": div_val, "div_gross": float(np.sum(divs)), "div_in": div_in, "div_rate": div_rate,
            "div_dest": div_dest, "div_count": int(np.count_nonzero(divs)),
            "tax_upfront": tax_upfront, "units": units, "sleeve0": out0, "legs": settle, "rolls": rolls_nominal,
            "early": early, "early_t": early_t, "roll_early": roll_early,
            "cash_other": cash - legs_total - rolls_nominal, "debt": debt, "liq_tax": liq_tax}


# ---------------------------------------------------------------------------
# simulation
# ---------------------------------------------------------------------------

def time_grid(T: float, tau_roll: float, extra=()) -> tuple[np.ndarray, np.ndarray]:
    """Simulation dates: roll-cycle boundaries, the `extra` dates inside the horizon (the stock's ex-dividend dates — where
    dividends are paid and early exercise is decided) and T. Returns (times, cycle) with cycle[j] = the grid index at which
    a short sold at t_j settles (−1: nothing sold at t_j)."""
    bounds = [0.0, T]
    if tau_roll > 0 and T >= 2 * tau_roll:
        n = int(T // tau_roll)
        bounds = [i * tau_roll for i in range(n + 1)] + ([T] if T - n * tau_roll > 1e-6 else [])
    pts = sorted(set(bounds) | {float(e) for e in extra if 1e-6 < e < T - 1e-6})
    times = np.array([p for i, p in enumerate(pts) if i == 0 or p - pts[i - 1] > 1e-6])
    cycle = np.full(len(times) - 1, -1)
    if len(bounds) > 2:
        at = lambda x: int(np.argmin(np.abs(times - x)))       # noqa: E731
        for a_, b_ in zip(bounds[:-1], bounds[1:]):
            if b_ - a_ >= tau_roll - 1e-6:                      # full cycles only — the stub to T sells nothing
                cycle[at(a_)] = at(b_)
    return times, cycle


@dataclass
class JumpModel:
    """Merton jump-diffusion, optionally with COMMON market jumps: at each index jump the stock also jumps by β_j × the
    index's log-jump (crashes arrive together), on top of its own idiosyncratic jumps (lam, m, s)."""
    sigma: float                 # diffusion vol
    lam: float = 0.0             # idiosyncratic jumps per year …
    m: float = 0.0               # … mean log-jump
    s: float = 0.0               # … sd of the log-jump
    lam_c: float = 0.0           # common (index) jumps per year
    m_c: float = 0.0
    s_c: float = 0.0
    beta_j: float = 0.0          # stock log-jump per unit index log-jump
    rmse_vol_pts: Optional[float] = None

    def comp(self) -> float:
        """Jump compensator λ·E[e^J − 1] summed over both components (keeps E[S_T] at the forward/drift)."""
        mc, sc = self.beta_j * self.m_c, abs(self.beta_j) * self.s_c
        return (self.lam_c * (math.exp(mc + 0.5 * sc * sc) - 1) if self.lam_c else 0.0) + \
               (self.lam * (math.exp(self.m + 0.5 * self.s * self.s) - 1) if self.lam else 0.0)

    def jump_var(self) -> float:
        """Annual variance contributed by jumps."""
        return self.lam_c * self.beta_j ** 2 * (self.m_c ** 2 + self.s_c ** 2) + self.lam * (self.m ** 2 + self.s ** 2)


def _pois(lam_t: float) -> list[tuple[int, float]]:
    if lam_t <= 1e-12:
        return [(0, 1.0)]
    n_max = min(30, int(lam_t + 6 * math.sqrt(lam_t) + 4))
    out, w = [], math.exp(-lam_t)
    for n in range(n_max + 1):
        out.append((n, w))
        w *= lam_t / (n + 1)
    return out


def jump_price(F: float, K, T: float, r: float, jm: JumpModel, right: str):
    """European price under the jump model (Merton series over both jump counts), Black-76 on the forward."""
    F = np.asarray(F, float)
    K = np.asarray(K, float)
    mc, sc = jm.beta_j * jm.m_c, abs(jm.beta_j) * jm.s_c
    comp = jm.comp()
    total = np.zeros(np.broadcast(F, K).shape)
    for n1, w1 in _pois(jm.lam_c * T):
        for n2, w2 in _pois(jm.lam * T):
            Fn = F * math.exp(-comp * T + n1 * (mc + 0.5 * sc * sc) + n2 * (jm.m + 0.5 * jm.s * jm.s))
            var = jm.sigma ** 2 * T + n1 * sc * sc + n2 * jm.s * jm.s
            total = total + w1 * w2 * black(Fn, K, T, r, math.sqrt(max(var, 1e-10) / T), right)
    return total


def calibrate_jumps(book: Book, *, common: Optional[JumpModel] = None, beta: float = 0.0) -> Optional[JumpModel]:
    """Fit the jump model to the book's smile (OTM prices, residuals in vol units via vega). With `common` (the index's
    fitted model) the stock inherits the market's jumps scaled by β and only its diffusion + idiosyncratic jumps are fit."""
    from scipy.optimize import least_squares
    sel = (book.k >= -0.7) & (book.k <= 0.3)
    ks, vs = book.k[sel], book.v[sel]
    if len(ks) < 4:
        return None
    idx = np.unique(np.linspace(0, len(ks) - 1, min(11, len(ks))).round().astype(int))
    ks, vs = ks[idx], vs[idx]
    K = book.F * np.exp(ks)
    T, r, F = book.T, book.r, book.F
    sd = vs * math.sqrt(T)
    d1 = (-ks + 0.5 * sd * sd) / sd
    vega = np.maximum(math.exp(-r * T) * F * np.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) * math.sqrt(T), 1e-4 * F)
    puts = ks < 0
    mkt = np.where(puts, black(F, K, T, r, vs, "P"), black(F, K, T, r, vs, "C"))
    base = {"lam_c": common.lam, "m_c": common.m, "s_c": common.s, "beta_j": beta} if common else {}

    def model(x) -> np.ndarray:
        jm = JumpModel(sigma=x[0], lam=x[1], m=x[2], s=x[3], **base)
        return np.where(puts, jump_price(F, K, T, r, jm, "P"), jump_price(F, K, T, r, jm, "C"))

    def resid(x):
        return np.concatenate([(model(x) - mkt) / vega, [0.02 * (x[1] - 0.25)]])     # mild pull toward λ≈0.25 (identifiability)

    atm = book.atm_iv
    x0 = [max(0.6 * atm, 0.04), 0.25, -0.12, 0.12]
    try:
        sol = least_squares(resid, x0, bounds=([0.02, 0.0, -0.7, 0.02], [1.5, 3.0, 0.15, 0.6]), method="trf", max_nfev=300)
    except Exception as exc:  # noqa: BLE001
        logger.debug("jump calibration failed: %s", exc)
        return None
    err = (model(sol.x) - mkt) / vega
    jm = JumpModel(sigma=float(sol.x[0]), lam=float(sol.x[1]), m=float(sol.x[2]), s=float(sol.x[3]), **base)
    jm.rmse_vol_pts = float(np.sqrt(np.mean(err * err)) * 100)
    return jm


def simulate(*, S0: float, I0: float, mu_s: float, mu_i: float, q_s: float, q_i: float, sig_s, sig_i,
             rho: float, times: np.ndarray, n_paths: int = N_PATHS, seed: int = SEED,
             jm_s: Optional[JumpModel] = None, jm_i: Optional[JumpModel] = None) -> tuple[np.ndarray, np.ndarray]:
    """Joint price paths (ex-dividend) under the real-world drift μ (total return). Diffusions correlated at ρ (antithetic);
    optional jumps — the index's jumps also hit the stock through jm_s.beta_j (common crashes) plus the stock's own idiosyncratic
    jumps. Every jump component is compensated so E[S_t] = S0·e^{(μ−q)t}. `sig_s`/`sig_i` are the DIFFUSION vols, per step or
    one number. Seeded → reproducible."""
    rng = np.random.default_rng(seed)
    half = max(1, n_paths // 2)
    dt = np.diff(times)
    steps = len(dt)
    z1 = rng.standard_normal((half, steps))
    z2 = rng.standard_normal((half, steps))
    z1 = np.vstack([z1, -z1])
    z2 = np.vstack([z2, -z2])
    n = z1.shape[0]
    zs = rho * z1 + math.sqrt(max(1 - rho * rho, 0.0)) * z2
    sig_s = np.broadcast_to(np.asarray(sig_s, float), dt.shape)          # a vol per step (term structure) or one number
    sig_i = np.broadcast_to(np.asarray(sig_i, float), dt.shape)
    comp_i = jm_i.comp() if jm_i else 0.0
    comp_s = jm_s.comp() if jm_s else 0.0
    li = (mu_i - q_i - comp_i - 0.5 * sig_i ** 2) * dt + sig_i * np.sqrt(dt) * z1
    ls = (mu_s - q_s - comp_s - 0.5 * sig_s ** 2) * dt + sig_s * np.sqrt(dt) * zs
    del z1, z2, zs
    if jm_i is not None and jm_i.lam > 0:
        Nc = rng.poisson(jm_i.lam * dt, size=(n, steps))
        jc = Nc * jm_i.m + np.sqrt(Nc) * jm_i.s * rng.standard_normal((n, steps))
        li += jc
        if jm_s is not None and jm_s.lam_c:
            ls += jm_s.beta_j * jc
        del Nc, jc
    if jm_s is not None and jm_s.lam > 0:
        Ne = rng.poisson(jm_s.lam * dt, size=(n, steps))
        ls += Ne * jm_s.m + np.sqrt(Ne) * jm_s.s * rng.standard_normal((n, steps))
        del Ne
    I = I0 * np.exp(np.hstack([np.zeros((n, 1)), np.cumsum(li, axis=1)]))
    S = S0 * np.exp(np.hstack([np.zeros((n, 1)), np.cumsum(ls, axis=1)]))
    return S, I


def diffusion_rho(rho_total: float, jm_s: JumpModel, jm_i: JumpModel) -> float:
    """Diffusion correlation that makes the TOTAL (diffusion + common-jump) correlation equal the measured ρ."""
    var_s = jm_s.sigma ** 2 + jm_s.jump_var()
    var_i = jm_i.sigma ** 2 + jm_i.lam * (jm_i.m ** 2 + jm_i.s ** 2)
    cov_j = jm_s.lam_c * jm_s.beta_j * (jm_s.m_c ** 2 + jm_s.s_c ** 2)
    r = (rho_total * math.sqrt(var_s * var_i) - cov_j) / (jm_s.sigma * jm_i.sigma)
    return max(-0.95, min(0.95, r))


def scenario_paths(S0: float, I0: float, moves: list[tuple[float, float]], times: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic geometric paths from today to (1+m_s)·S0, (1+m_i)·I0 — the scenario table goes through `evaluate` too."""
    w = times / times[-1]
    S = np.array([S0 * (1 + ms / 100.0) ** w for ms, _ in moves])
    I = np.array([I0 * (1 + mi / 100.0) ** w for _, mi in moves])
    return S, I


def outcome_metrics(W: np.ndarray, IT: np.ndarray, W0: float, T: float) -> dict:
    x = np.maximum(W / W0, 0.01)
    lx = np.log(x)
    R = W / W0 - 1.0
    srt = np.sort(R)
    k = max(1, int(TAIL_Q * len(R)))
    ce = float(np.mean(x ** (1 - CRRA_GAMMA))) ** (1 / (1 - CRRA_GAMMA))
    sd = float(lx.std())
    li = np.log(IT)
    mkt = float(np.corrcoef(lx, li)[0, 1] ** 2) if sd > 1e-9 and float(li.std()) > 1e-9 else 0.0
    return {
        "cagr": math.exp(float(lx.mean()) / T) - 1,
        "mean_ann": float(x.mean()) ** (1 / T) - 1,
        "ce_ann": ce ** (1 / T) - 1,
        "cvar": float(srt[:k].mean()),
        "p_loss": float((R < 0).mean()),
        "vol_ann": sd / math.sqrt(T),
        "pct": [float(v) for v in np.percentile(R, [5, 25, 50, 75, 95])],
        "mkt_share": mkt if math.isfinite(mkt) else 0.0,
    }


def exposure(st: Strategy, env: Env, beta: float, units: float) -> dict:
    """Day-one delta exposure as % of today's position value: single-stock, index, and β-adjusted market."""
    if st.sell_now:
        s_delta = 0.0
        idx_dollars = units * env.I0
    else:
        s_delta = 1.0 + sum(l.qty * l.delta for l in st.legs if l.und == "S") + sum(r.qty * r.delta for r in st.rolls)
        idx_dollars = units * env.I0 + sum(l.qty * l.delta * env.I0 for l in st.legs if l.und == "I")
    single = 100.0 * s_delta
    index = 100.0 * idx_dollars / env.S0
    return {"single_stock_pct": single, "index_pct": index, "market_beta_pct": beta * single + index}


# ---------------------------------------------------------------------------
# the structure catalogue
# ---------------------------------------------------------------------------

def _leg(book: Book, und: str, right: str, K: Optional[float], qty: float) -> Optional[Leg]:
    if K is None:
        return None
    q = book.book(right).get(K)
    if not q or not q.ok:
        return None
    hs = (q.ask - q.bid) / (2 * q.mid) if q.mid > 0 else 0.0
    iv = float(book.iv(K))
    if book.european:
        # The index twin (SPX for SPY …) is European: its value is the smile's Black price, NOT the ETF's American quote.
        # Out of the money they agree (the smile IS the OTM mids); deep in the money the ETF quote carries the buyer's
        # early-exercise right — a deep-ITM SPY put trades near intrinsic, far above the European value.
        mid = float(black(book.F, K, book.T, book.r, iv, right))
        return Leg(und=und, right=right, K=K, qty=qty, px=mid * (1 + hs) if qty > 0 else mid * (1 - hs), bid=q.bid,
                   ask=q.ask, mid=mid, iv=iv, delta=book.delta(K, right), T_leg=book.T, expiry=book.expiry, oi=q.oi,
                   volume=q.volume, half_spread=hs, carry=book.q, european=True)
    return Leg(und=und, right=right, K=K, qty=qty, px=q.ask if qty > 0 else q.bid, bid=q.bid, ask=q.ask, mid=q.mid,
               iv=iv, delta=book.delta(K, right), T_leg=book.T, expiry=book.expiry, oi=q.oi,
               volume=q.volume, half_spread=hs, carry=book.q)


def forward_vol(sig_short: float, t_short: float, sig_long: float, t_long: float) -> float:
    """ATM forward vol between two expiries from total variance; floored so a steeply inverted curve can't go negative."""
    if t_long <= t_short + 1e-9:
        return sig_long
    var = (sig_long ** 2 * t_long - sig_short ** 2 * t_short) / (t_long - t_short)
    return math.sqrt(max(var, (0.5 * sig_long) ** 2))


def _roll(xb: Book, right: str, qty: float, *, atm_fwd: float, delta: float = 0.0, K_abs: float = 0.0) -> Optional[Roll]:
    """A rolling short leg calibrated on today's short-dated chain."""
    K = xb.for_delta(right, delta) if not K_abs else xb.pick(right, K_abs, "nearest")
    if K is None:
        return None
    q = xb.book(right)[K]
    hs = (q.ask - q.bid) / (2 * q.mid) if q.mid > 0 else 0.0
    return Roll(right=right, qty=qty, tau=xb.T, r=xb.r, q=xb.q, half_spread=hs, skew_k=xb.k, skew_v=xb.v - xb.atm_iv,
                atm0=xb.atm_iv, atm_fwd=atm_fwd, m=0.0 if K_abs else K / xb.S, K_abs=K_abs, delta=xb.delta(K, right),
                expiry=xb.expiry, first_K=K, first_bid=q.bid)


def _pct(K: float, S: float) -> str:
    v = (K / S - 1) * 100
    return f"{'+' if v >= 0 else '−'}{abs(v):.0f}%"


def dest_label(env: Env, index: str) -> str:
    return {"index": index, "bond": f"a {env.bond_y * 100:.2f}% bond", "cash": "T-bills"}[env.dest]


def build_candidates(env: Env, sb: Book, xb: Optional[Book], ib: Optional[Book], *, beta: float, ltv: float,
                     index: str) -> list[Strategy]:
    S0 = env.S0
    dl = dest_label(env, index)
    out: list[Strategy] = []
    fwd = forward_vol(xb.atm_iv, xb.T, sb.atm_iv, sb.T) if xb is not None else sb.atm_iv
    if ib is not None:
        ib.european = env.idx1256          # index legs trade as the §1256 twin we already tax them as (SPX, NDX, RUT, DJX)

    def add(st: Strategy) -> None:
        if any(l is None for l in st.legs) or any(r is None for r in st.rolls):
            return
        out.append(st)

    def P(f, side="below"):
        return sb.pick("P", S0 * (1 - f / 100.0), side)

    out.append(Strategy("hold", "reference", "Hold unhedged", blurb="Keep the shares, no options. The yardstick every "
                        "structure is measured against."))
    out.append(Strategy("sell_now", "reference", f"Sell now, pay the tax, put the rest in {dl}", sell_now=True,
                        blurb="Realise the gain today and move the after-tax proceeds — the 'simple' way out.",
                        cs={"risk": "n/a", "note": "This is the sale."}))

    # --- protective puts
    for f in (0, 10, 20):
        p = _leg(sb, "S", "P", P(f, "nearest" if f == 0 else "below"), 1)
        if p:
            add(Strategy(f"put_{f}", "puts", f"LEAP put ${p.K:g} ({_pct(p.K, S0)})", [p],
                         blurb="Pay for a floor, keep all the upside."))

    # --- tax-adjusted hedge ratio: a put's payoff is taxed (short-term, straddle) while the shares' fall saves
    # nothing (defer) or only the long-term rate (liquidate) — so one put per share protects less than a share's worth
    # AFTER tax. Buying 1/(1−st) [defer] or (1−lt)/(1−st) [liquidate] puts per share restores an after-tax floor.
    h_tax = (1.0 if env.lens == "defer" else (1 - env.lt)) / max(1 - env.st, 0.2)
    if h_tax > 1.05:
        p = _leg(sb, "S", "P", P(15), h_tax)
        if p:
            add(Strategy("put_taxadj", "puts", f"Tax-adjusted LEAP put ${p.K:g} ({_pct(p.K, S0)}) × {h_tax:.2f}/share", [p],
                         blurb="Enough puts that the floor holds AFTER the tax on the put's gain — the extra puts cover "
                               "the tax, not more shares."))
            c = _leg(sb, "S", "C", sb.zero_cost_call(h_tax * p.px, S0 * 1.03), -1)
            if c:
                add(Strategy("collar_taxadj", "collars", f"Tax-adjusted collar {h_tax:.2f}×${p.K:g}P / ${c.K:g}C", [p, c],
                             blurb="A collar sized for an after-tax floor; the call (one per share) pays for all of it."))

    # --- collars
    for f in (0, 10, 15, 20, 25):
        p = _leg(sb, "S", "P", P(f, "nearest" if f == 0 else "below"), 1)
        if not p:
            continue
        kc = sb.zero_cost_call(p.px, S0 * 1.03)
        c = _leg(sb, "S", "C", kc, -1)
        if c:
            add(Strategy(f"collar_{f}", "collars",
                         f"Zero-cost collar {'ATM ' if f == 0 else ''}${p.K:g}P / ${c.K:g}C (strikes {_pct(p.K, S0)} / {_pct(c.K, S0)})",
                         [p, c], blurb=("At-the-money put paid for by the call: no loss below today, gains up to the call "
                                        "strike. The narrowest band — watch the sale-for-tax flag." if f == 0 else
                                        "The call you sell pays for the put — floor and cap.")))
    for f in (10, 15):
        p1 = _leg(sb, "S", "P", P(f), 1)
        p2 = _leg(sb, "S", "P", P(f + 20), -1)
        if p1 and p2 and p2.K < p1.K:
            kc = sb.zero_cost_call(p1.px - p2.px, S0 * 1.03)
            c = _leg(sb, "S", "C", kc, -1)
            if c:
                add(Strategy(f"pscollar_{f}", "collars",
                             f"Put-spread collar ${p1.K:g}/{p2.K:g}P · ${c.K:g}C", [p1, p2, c],
                             blurb="Protection between the two puts only — a much higher cap for the same zero cost."))
    for f in (10, 15):
        p = _leg(sb, "S", "P", P(f), 1)
        c1 = _leg(sb, "S", "C", sb.for_delta("C", 0.30), -1)
        if p and c1:
            c2 = _leg(sb, "S", "C", sb.pick("C", c1.K * 1.20, "above"), 1)
            if c2 and c2.K > c1.K:
                add(Strategy(f"kicker_{f}", "collars", f"Collar + upside kicker ${p.K:g}P · −${c1.K:g}C · +${c2.K:g}C",
                             [p, c1, c2], blurb="Collar whose cap re-opens above the higher call — upside comes back in a "
                                                "melt-up."))

    # --- butterflies & ratios
    k1, k2, k3 = P(5), P(20), P(35)
    if k1 and k2 and k3 and k1 > k2 > k3:
        a, b, c = _leg(sb, "S", "P", k1, 1), _leg(sb, "S", "P", k2, -2), _leg(sb, "S", "P", k3, 1)
        add(Strategy("put_fly", "spreads", f"Put butterfly ${k1:g}/{k2:g}/{k3:g}", [a, b, c],
                     blurb="Cheap protection that pays most for a −5…−35% decline; nothing beyond it."))
        a2, b2 = _leg(sb, "S", "P", k1, 1), _leg(sb, "S", "P", k2, -2)
        add(Strategy("ratio_1x2", "spreads", f"1×2 put ratio ${k1:g}/2×{k2:g}", [a2, b2],
                     blurb="Near-zero-cost hedge for a moderate drop; doubles your losses below the short strike."))

    # --- straddles / strangles (long = vol hedge)
    atm_p, atm_c = _leg(sb, "S", "P", sb.pick("P", S0), 1), _leg(sb, "S", "C", sb.pick("C", S0), 1)
    if atm_p and atm_c:
        add(Strategy("long_straddle", "vol", f"Long straddle ${atm_p.K:g}", [atm_p, atm_c],
                     blurb="Buy the ATM put and call — pays on a big move either way; expensive time decay."))
    sp, sc = _leg(sb, "S", "P", P(10), 1), _leg(sb, "S", "C", sb.pick("C", S0 * 1.10, "above"), 1)
    if sp and sc:
        add(Strategy("long_strangle", "vol", f"Long strangle ${sp.K:g}P / ${sc.K:g}C", [sp, sc],
                     blurb="Cheaper than the straddle; needs a bigger move."))

    # --- income
    for d in (0.20, 0.35):
        c = _leg(sb, "S", "C", sb.for_delta("C", d), -1)
        if c:
            add(Strategy(f"cc_{int(d * 100)}", "income", f"Covered LEAP call ${c.K:g} ({_pct(c.K, S0)}, {d:.2f}Δ)", [c],
                         blurb="Sell upside for cash today. No floor."))
    c, p = _leg(sb, "S", "C", sb.for_delta("C", 0.25), -1), _leg(sb, "S", "P", sb.for_delta("P", 0.25), -1)
    if c and p:
        add(Strategy("cov_strangle", "income", f"Covered strangle −${c.K:g}C / −${p.K:g}P", [c, p],
                     blurb="More premium — but the short put ADDS stock if it falls (more concentration, not less)."))
    c, p = _leg(sb, "S", "C", sb.pick("C", S0), -1), _leg(sb, "S", "P", sb.pick("P", S0), -1)
    if c and p:
        add(Strategy("cov_straddle", "income", f"Covered straddle ${c.K:g}", [c, p],
                     blurb="Maximum premium; roughly doubles the downside."))

    # --- calendars & diagonals (stock LEAP + rolling short-dated legs)
    if xb is not None:
        for f in (10, 20):
            p = _leg(sb, "S", "P", P(f), 1)
            ro = _roll(xb, "C", -1, atm_fwd=fwd, delta=0.20)
            if p and ro:
                add(Strategy(f"diag_{f}", "calendar", f"Diagonal collar: LEAP ${p.K:g}P + sell ~0.20Δ calls monthly",
                             [p], [ro], blurb="Buy the LEAP floor once; finance it by re-selling short-dated calls — theta is "
                                              "front-loaded, and the cap resets every cycle."))
        for d in (0.15, 0.25):
            ro = _roll(xb, "C", -1, atm_fwd=fwd, delta=d)
            if ro:
                add(Strategy(f"roll_cc_{int(d * 100)}", "calendar", f"Rolling covered calls ~{d:.2f}Δ (every ~{round(xb.T * 365)}d)",
                             [], [ro], blurb="Overwrite programme: re-sell OTM calls each cycle. No floor."))
        p = _leg(sb, "S", "P", P(10), 1)
        ro = _roll(xb, "P", -1, atm_fwd=fwd, delta=0.10)
        if p and ro:
            add(Strategy("put_diag", "calendar", f"Put diagonal: LEAP ${p.K:g}P + sell ~0.10Δ puts monthly", [p], [ro],
                         blurb="Short-dated puts below the LEAP floor pay part of its cost."))
        if p:
            ro = _roll(xb, "P", -1, atm_fwd=fwd, K_abs=p.K)
            if ro:
                add(Strategy("put_cal", "calendar", f"Put calendar: LEAP ${p.K:g}P + sell the ${p.K:g}P monthly", [p], [ro],
                             blurb="Classic time spread on the floor strike — cheap carry, protection fades in a fast crash."))

    # --- deep-ITM covered calls → index (monetise)
    for m in (0.85, 0.70, 0.50):
        c = _leg(sb, "S", "C", sb.pick("C", S0 * m, "nearest"), -1)
        if c and c.K < S0:
            add(Strategy(f"ditm_{int(m * 100)}", "diversify",
                         f"Deep-ITM call ${c.K:g} ({_pct(c.K, S0)}), premium → {dl}", [c],
                         blurb="Take most of the stock's value in cash today and move it into the chosen investment; the "
                               "short call neutralises most of the single-stock exposure."))

    if ib is not None:
        h = max(beta, 0.25) * S0 / ib.S                                  # index units per stock share (β-dollar)
        F0 = ib.S * math.exp((env.r - ib.q) * env.T)                     # fair futures price at the horizon

        def IP(f, side="below"):
            return ib.pick("P", ib.S * (1 - f / 100.0), side)

        for f in (5, 10, 15):
            ip = _leg(ib, "I", "P", IP(f), h)
            if ip:
                add(Strategy(f"idx_put_{f}", "index", f"{index} put ${ip.K:g} ({_pct(ip.K, ib.S)}) × β", [ip],
                             blurb="Hedge the MARKET part of the risk with cheaper index puts — no straddle with your "
                                   "shares; the stock-specific risk stays."))
        ip = _leg(ib, "I", "P", IP(10), h)
        if ip:
            ic = _leg(ib, "I", "C", ib.zero_cost_call(ip.px, ib.S * 1.02), -h)
            if ic:
                add(Strategy("idx_collar", "index", f"{index} collar ${ip.K:g}P / ${ic.K:g}C × β", [ip, ic],
                             blurb="Zero-cost collar on the index, sized to your β-dollar exposure."))
            ip2 = _leg(ib, "I", "P", IP(25), -h)
            if ip2 and ip2.K < ip.K:
                add(Strategy("idx_pspread", "index", f"{index} put spread ${ip.K:g}/{ip2.K:g} × β", [ip, ip2],
                             blurb="Index protection between the strikes for a fraction of the put's cost."))
            kc = sb.zero_cost_call(h * ip.px, S0 * 1.03)
            c = _leg(sb, "S", "C", kc, -1)
            if c:
                add(Strategy("cross_collar", "index", f"Cross-collar: sell ${c.K:g}C on the stock, buy {index} ${ip.K:g}P × β",
                             [ip, c], blurb="Sell the stock's (usually richer) call to buy cheaper index puts."))
        add(Strategy("idx_short_fut", "index", f"Short {index} futures × β (beta hedge)",
                     [Leg("I", "F", F0, -h, 0.0, delta=1.0, T_leg=env.T, expiry=sb.expiry, carry=ib.q)],
                     blurb="Remove the market beta with §1256 futures; you keep — and are left only with — the "
                           "stock-specific risk."))

        # --- monetise & diversify
        for f in (15, 20):
            p = _leg(sb, "S", "P", P(f), 1)
            if not p:
                continue
            c = _leg(sb, "S", "C", sb.zero_cost_call(p.px, S0 * 1.03), -1)
            if c:
                loan = ltv / 100.0 * p.K * math.exp(-env.r_b * env.T)
                add(Strategy(f"collar_loan_{f}", "diversify",
                             f"Collar ${p.K:g}P/${c.K:g}C + borrow {ltv:.0f}% of the floor → {dl}", [p, c], loan_ps=loan,
                             blurb="The put floor secures a loan; the loan buys the index. Economically a variable "
                                   "prepaid forward — diversify today without selling."))
        p = _leg(sb, "S", "P", P(15), 1)
        if p:
            c = _leg(sb, "S", "C", sb.zero_cost_call(p.px, S0 * 1.03), -1)
            if c:
                d_net = 1.0 + p.delta * p.qty + c.delta * c.qty
                qf = max(0.0, 1.0 - d_net) * S0 / ib.S
                add(Strategy("collar_fut", "diversify", f"Collar ${p.K:g}P/${c.K:g}C + long {index} futures overlay",
                             [p, c, Leg("I", "F", F0, qf, 0.0, delta=1.0, T_leg=env.T, expiry=sb.expiry, carry=ib.q)],
                             blurb="Collar the stock, then replace the stock delta the collar removed with index futures "
                                   "(margined against the shares) — swaps single-stock risk for market risk."))
        ic_atm = _leg(ib, "I", "C", ib.pick("C", ib.S), 1)
        c = _leg(sb, "S", "C", sb.for_delta("C", 0.35), -1)
        if ic_atm and c:
            ic_atm.qty = c.px / ic_atm.px
            add(Strategy("swap_calls", "diversify", f"Swap upside: sell ${c.K:g}C on the stock, buy {index} ${ic_atm.K:g}C",
                         [c, ic_atm], blurb="Trade the stock's upside above the strike for index upside — no floor."))
        p = _leg(sb, "S", "P", P(15), 1)
        c = _leg(sb, "S", "C", sb.for_delta("C", 0.40), -1)
        ic2 = _leg(ib, "I", "C", ib.pick("C", ib.S), 1)
        if p and c and ic2 and c.px > p.px:
            ic2.qty = (c.px - p.px) / ic2.px
            add(Strategy("collar_idx_calls", "diversify",
                         f"Collar ${p.K:g}P/${c.K:g}C, surplus buys {index} ${ic2.K:g}C", [p, c, ic2],
                         blurb="A tighter call than zero-cost needs; the extra premium buys index upside."))
        c = _leg(sb, "S", "C", sb.pick("C", S0 * 0.70, "nearest"), -1)
        ipd = _leg(ib, "I", "P", ib.pick("P", ib.S * 1.30, "nearest"), -1)
        if c and ipd and c.K < S0 and ipd.K > ib.S and abs(ipd.delta) > 0.3:
            # match DOLLARS of exposure: the AAPL exposure the call hands over (Δ·S) buys the same notional of the index.
            # A deep-ITM put behaves like a full index position by expiry, so sizing by its delta today over-buys.
            ipd.qty = -abs(c.delta) * S0 / ib.S
            add(Strategy("ditm_pair", "diversify", f"Sell deep-ITM ${c.K:g}C on the stock + deep-ITM {index} ${ipd.K:g}P",
                         [c, ipd], keep_cash=True, blurb="Cash in from both legs today: the short call sells stock exposure, the short "
                                         "index put buys index exposure (a synthetic stock-for-index swap)."))
        # --- protected diversification: the sleeve bought with the money is itself floored with index puts
        #     (§1256 on the index twins, no straddle with the shares → the put's gain AND loss are recognised)
        ip_f = IP(10)
        pa = _leg(sb, "S", "P", P(15), h_tax if h_tax > 1.05 else 1.0)
        if pa and ip_f and env.dest == "index":
            ca = _leg(sb, "S", "C", sb.zero_cost_call(pa.qty * pa.px, S0 * 1.03), -1)
            if ca:
                loan = ltv / 100.0 * pa.K * math.exp(-env.r_b * env.T)
                ipl = _leg(ib, "I", "P", ip_f, loan / ib.S)
                if ipl:
                    add(Strategy("collar_loan_protected", "diversify",
                                 f"{'Tax-adjusted c' if pa.qty > 1 else 'C'}ollar ${pa.K:g}P/${ca.K:g}C + loan → {index} "
                                 f"with ${ip_f:g} index puts", [pa, ca, ipl], loan_ps=loan,
                                 blurb="Floor the shares, borrow against the floor, buy the index — and floor the index "
                                       "sleeve too. Diversifies without selling, with a floor on both pieces."))
        c = _leg(sb, "S", "C", sb.pick("C", S0 * 0.70, "nearest"), -1)
        if c and c.K < S0 and ip_f and env.dest == "index":
            ipv = _leg(ib, "I", "P", ip_f, 1.0)
            if ipv:
                ipv.qty = c.px / (ib.S + ipv.px)                                     # premium buys units + their puts
                if ipv:
                    add(Strategy("ditm_protected", "diversify",
                                 f"Deep-ITM ${c.K:g}C → {index} with ${ip_f:g} index puts", [c, ipv],
                                 blurb="Monetise with a deep-ITM call, buy the index, and floor the index sleeve. The shares "
                                       "below the call strike stay unprotected."))
        cs_ = _leg(sb, "S", "C", sb.pick("C", S0), -1)
        ps_ = _leg(sb, "S", "P", cs_.K if cs_ else None, 1) if cs_ else None
        if cs_ and ps_:
            loan = ltv / 100.0 * cs_.K * math.exp(-env.r_b * env.T)
            add(Strategy("synthetic_sale", "diversify", f"Synthetic sale ${cs_.K:g} (short call + long put) + loan → {dl}",
                         [ps_, cs_], loan_ps=loan,
                         blurb="Locks the price completely and borrows against it — perfect diversification, but for "
                               "tax it is a constructive SALE (reference)."))
    for st in out:
        st.cs = cs_read(st, env)
        st.warnings = warnings_for(st, env)
    return out


def cs_read(st: Strategy, env: Env) -> dict:
    """Constructive-sale (§1259) / straddle read."""
    if st.sell_now:
        return {"risk": "n/a", "note": "This is the sale — the gain is taxed today."}
    S0 = env.S0
    lp = [l.K for l in st.legs if l.und == "S" and l.right == "P" and l.qty > 0]
    sc = [l for l in st.legs if l.und == "S" and l.right == "C" and l.qty < 0]
    if lp and sc and any(abs(c.K - k) < 1e-9 for c in sc for k in lp):
        return {"risk": "high", "note": "Short call + long put at the same strike is a synthetic short — it eliminates "
                                        "substantially all risk and opportunity: a constructive sale under §1259."}
    reads = []
    if lp and sc:
        band = (min(c.K for c in sc) - max(lp)) / S0 * 100
        reads.append(constructive_sale_flag(band))
    deep = [c for c in sc if abs(c.delta) >= 0.80]
    if deep:
        d = abs(deep[0].delta)
        if d >= 0.90:
            reads.append({"risk": "high", "note": f"Deep-ITM short call (Δ {d:.2f}) gives away essentially all the upside and "
                          "most of the downside to the strike. §1259's legislative history names deep-in-the-money options; "
                          "treat this as a probable constructive sale."})
        else:
            reads.append({"risk": "elevated", "note": f"In-the-money short call (Δ {d:.2f}): not a listed §1259 transaction "
                          "and real risk remains, but it is not a qualified covered call (straddle rules apply). Get an "
                          "opinion before relying on it."})
    if not reads:
        if any(l.und == "S" for l in st.legs) or st.rolls:
            return {"risk": "low", "note": "Leaves real two-way risk on the shares."}
        if st.legs:
            return {"risk": "low", "note": "The hedge is on a diversified index — not 'substantially identical' to your "
                                          "shares, so no constructive sale or straddle with the stock."}
        return {"risk": "low", "note": "No options on the shares."}
    order = {"low": 0, "elevated": 1, "high": 2}
    worst = max(reads, key=lambda x: order[x["risk"]])
    return {"risk": worst["risk"], "note": " ".join(x["note"] for x in reads)}


def warnings_for(st: Strategy, env: Env) -> list[str]:
    w = []
    for l in st.legs:
        sp = l.spread_pct
        if sp is not None and sp > WIDE_SPREAD_PCT:
            w.append(f"Wide market on the ${l.K:g} {'call' if l.right == 'C' else 'put'} ({sp:.0f}% of mid) — work a limit "
                     "near the mid.")
        if l.right != "F" and l.oi == 0:
            w.append(f"No open interest on the ${l.K:g} {'call' if l.right == 'C' else 'put'}.")
    if any(l.und == "S" and l.right == "P" and l.qty < 0 for l in st.legs) or any(r.right == "P" and r.qty < 0 for r in st.rolls):
        w.append("Short stock puts add shares if assigned — more concentration, not less.")
    if any(l.right == "F" for l in st.legs):
        w.append("Futures are margined daily; holding them against the shares needs portfolio margin (or cash) for the "
                 "variation margin.")
    if st.loan_ps > 0:
        w.append("Loan interest is assumed NOT deductible. The put floor secures the loan, but a margin line can still be "
                 "called if the lender's terms change.")
    if st.rolls:
        w.append("Rolled legs: the first sale is today's quote; later cycles are sold at the same delta and priced fairly "
                 "by the model (market-implied view) or off today's vol surface (historical view) — real fills and vol "
                 "regimes will vary.")
    return w


# ---------------------------------------------------------------------------
# ranking + presentation
# ---------------------------------------------------------------------------

def _legs_view(st: Strategy, env: Env, shares: int, index: str) -> list[dict]:
    rows = []
    for l in st.legs:
        und = "stock" if l.und == "S" else index
        if l.right == "F":
            notional = abs(l.qty) * shares * l.K
            fut = f"≈ {notional / (l.K * SPX_PER_SPY * 5):.1f} MES" if index == "SPY" else None
            rows.append({"und": und, "action": "BUY" if l.qty > 0 else "SELL", "type": "futures", "strike": round(l.K, 2),
                         "expiry": l.expiry, "qty_per_share": round(l.qty, 4), "notional": round(notional, 0),
                         "contracts_note": fut})
            continue
        S = env.S0 if l.und == "S" else env.I0
        rows.append({"und": und, "action": "BUY" if l.qty > 0 else "SELL", "type": "call" if l.right == "C" else "put",
                     "strike": round(l.K, 2), "pct_of_spot": round((l.K / S - 1) * 100, 1), "expiry": l.expiry,
                     "qty_per_share": round(l.qty, 4), "contracts": int(round(abs(l.qty) * shares / CONTRACT)),
                     "bid": round(l.bid, 2), "ask": round(l.ask, 2), "mid": round(l.mid, 2), "px": round(l.px, 2),
                     "iv_pct": round(l.iv * 100, 1), "delta": round(l.delta, 2), "oi": l.oi, "volume": l.volume,
                     "spread_pct": round(l.spread_pct, 1) if l.spread_pct is not None else None,
                     "twin": INDEX_1256.get(index) if l.european else None})
    return rows


def _rolls_view(st: Strategy, shares: int, S0: float) -> list[dict]:
    return [{"action": "SELL" if r.qty < 0 else "BUY", "type": "call" if r.right == "C" else "put",
             "rule": (f"same ${r.K_abs:g} strike each cycle" if r.K_abs else f"~{abs(r.delta):.2f}Δ ({_pct(r.first_K, S0)}) each cycle"),
             "cycle_days": round(r.tau * 365), "first_strike": r.first_K, "first_expiry": r.expiry,
             "first_bid": round(r.first_bid, 2), "contracts": int(round(abs(r.qty) * shares / CONTRACT)),
             "first_yield_pct": round(r.first_bid / S0 * 100, 2)} for r in st.rolls]


def _tax_note(st: Strategy, env: Env, index: str) -> str:
    if st.sell_now:
        return f"Long-term gain realised today at {env.lt * 100:.1f}%."
    parts = []
    if st.legs and any(l.und == "S" for l in st.legs):
        if _straddle(st, env.S0):
            parts.append("Stock options form a straddle with your shares: net gains short-term, net losses deferred into "
                         "the shares' basis (§1092).")
        else:
            parts.append("Covered call(s) qualify as QCC (OTM, no put): premium short-term; losses recognised.")
    if st.rolls:
        parts.append("Each rolled short is taxed when it closes — gains short-term; losses deferred while a straddle exists.")
    if any(l.und == "I" for l in st.legs):
        if env.idx1256 or any(l.right == "F" for l in st.legs):
            twin = INDEX_1256.get(index)
            parts.append(f"Index legs taxed §1256 60/40 ({env.b1256 * 100:.1f}% blended)"
                         + (f" — trade them as {twin} cash-settled options/futures to get that treatment." if twin else "."))
        else:
            parts.append("Index ETF options taxed as equity options (not §1256).")
    if env.dest == "bond":
        parts.append(f"Bond coupons taxed yearly at your ordinary rate ({env.st * 100:.1f}%).")
    elif env.dest == "cash":
        parts.append(f"T-bill interest taxed yearly at your ordinary rate.")
    return " ".join(parts) or "No tax event unless you sell."


def _exp_short(expiry: str) -> str:
    try:
        return datetime.strptime(expiry, "%Y-%m-%d").strftime("%b %Y")
    except (TypeError, ValueError):
        return expiry or ""


def how_it_works(st: Strategy, env: Env, *, shares: int, ticker: str, index: str) -> list[str]:
    """The trade in plain words, one line per step, with this position's contract counts and dollars."""
    dl = dest_label(env, index)
    N = shares
    if st.sell_now:
        tax0 = env.lt * max(env.S0 - env.B, 0.0) * N
        return [f"Sell all {N:,} {ticker} shares today and pay ${tax0:,.0f} of long-term tax.",
                f"Put the remaining ${env.S0 * N - tax0:,.0f} in {dl}."]
    if not st.legs and not st.rolls:
        return [f"Keep the {N:,} shares. No options, no tax until you sell."]
    out = []
    for l in st.legs:
        n = int(round(abs(l.qty) * N / CONTRACT))
        amt = abs(l.qty) * l.px * N
        k = f"${l.K:g}"
        when = _exp_short(l.expiry)
        if l.right == "F":
            notional = abs(l.qty) * N * l.K
            out.append(f"{'Buy' if l.qty > 0 else 'Sell'} {index} futures worth ${notional:,.0f} ({when}) — "
                       f"{'adds' if l.qty > 0 else 'removes'} market exposure with no cash up front.")
            continue
        if l.und == "S":
            if l.right == "P" and l.qty > 0:
                out.append(f"Buy {n} × {k} puts ({when}) for ${amt:,.0f} — if {ticker} ends below {k}, they pay the difference.")
            elif l.right == "P":
                out.append(f"Sell {n} × {k} puts ({when}) for ${amt:,.0f} — below {k} you pay the difference (more downside).")
            elif l.qty > 0:
                out.append(f"Buy {n} × {k} calls ({when}) for ${amt:,.0f} — gains above {k} come back to you.")
            elif l.K < env.S0:
                out.append(f"Sell {n} deep-in-the-money {k} calls ({when}) for ${amt:,.0f} — hands over {ticker}'s moves "
                           f"above {k}.")
            else:
                out.append(f"Sell {n} × {k} calls ({when}) for ${amt:,.0f} — you give up gains above {k} (you keep the shares; "
                           f"the calls are bought back if they finish in the money).")
        else:
            tw = f", as {INDEX_1256[index]}" if l.european and index in INDEX_1256 else ""
            if l.right == "P" and l.qty > 0:
                out.append(f"Buy {n} × {index} {k} puts ({when}{tw}) for ${amt:,.0f} — pays if {index} ends below {k} "
                           f"(protects against a market fall, not a {ticker}-only fall).")
            elif l.right == "P":
                out.append(f"Sell {n} deep-in-the-money {index} {k} puts ({when}{tw}) for ${amt:,.0f} — gives you {index}'s ups "
                           f"and downs.")
            elif l.qty > 0:
                out.append(f"Buy {n} × {index} {k} calls ({when}{tw}) for ${amt:,.0f} — gains if {index} rises above {k}.")
            else:
                out.append(f"Sell {n} × {index} {k} calls ({when}{tw}) for ${amt:,.0f} — gives up {index} gains above {k}.")
    for r in st.rolls:
        n = int(round(abs(r.qty) * N / CONTRACT))
        kind = "calls" if r.right == "C" else "puts"
        rule = (f"at ${r.K_abs:g} (only while it is out of the money)" if r.K_abs
                else f"about {abs(r.m - 1) * 100:.0f}% {'above' if r.right == 'C' else 'below'} the price then")
        out.append(f"Every ~{round(r.tau * 365)} days sell {n} {kind} {rule} — first one: ${r.first_K:g} for "
                   f"${r.first_bid * n * CONTRACT:,.0f}.")
    cash0, out0, _ = sleeve_start(st, env)
    net = -sum(l.qty * l.px for l in st.legs) * N
    if st.loan_ps > 0:
        out.append(f"Borrow ${st.loan_ps * N:,.0f} against the protected shares at {env.r_b * 100:.2f}% → {dl}.")
    if net > 0.5:
        if st.keep_cash and env.dest == "index":
            out.append(f"Net: you collect ${net:,.0f} today → kept in T-bills as collateral (the short {index} puts already "
                       f"give you the index — investing it in {index} too would double that). Pick 'Fixed-rate bond' to hold "
                       f"Treasuries instead.")
        else:
            out.append(f"Net: you collect ${net:,.0f} today → {dl}.")
    elif net < -0.5:
        out.append(f"Net cost today: ${-net:,.0f}.")
    else:
        out.append("Net cost today: about zero.")
    return out


def ledger(st: Strategy, o: dict, env: Env, *, shares: int, ticker: str, index: str) -> dict:
    """The expiry payout as lines that sum EXACTLY to the after-tax value (every piece comes from `evaluate`)."""
    N = shares
    lines, cols = [], []

    def add(meta: dict, arr) -> None:
        a = np.asarray(arr, float) * N * np.ones_like(o["W"])
        if meta["kind"] in ("shares", "tax") or np.abs(a).max() >= 0.5:
            lines.append(meta)
            cols.append(a)

    if not st.sell_now:
        add({"kind": "shares", "label": f"{N:,} {ticker} shares"}, o["stock_val"])
        dl = {"index": index, "bond": f"{env.bond_y * 100:.2f}% bond", "cash": "T-bills"}[o["div_dest"]]
        add({"kind": "divs", "dest": o["div_dest"], "count": o["div_count"], "rate_pct": round(o["div_rate"] * 100, 1),
             "label": f"{ticker} dividends after tax ({o['div_rate'] * 100:.1f}%) → {dl}"}, o["divs"])
    for i, l in enumerate(st.legs):
        meta = {"kind": "leg", "und": "stock" if l.und == "S" else index, "type": {"C": "call", "P": "put", "F": "futures"}[l.right],
                "action": "BUY" if l.qty > 0 else "SELL", "strike": round(l.K, 2), "units": round(l.qty * N, 2),
                "contracts": int(round(abs(l.qty) * N / CONTRACT)), "early": [int(x) for x in o["early"][i]]}
        add(meta, o["legs"][i])
    if st.rolls:
        add({"kind": "rolls", "label": "Rolled " + ("calls" if st.rolls[0].right == "C" else "puts") + " — premium kept minus buy-backs"},
            o["rolls"])
    add({"kind": "cash", "label": "Cash: today's premium and interest (after tax)"}, o["cash_other"])
    if env.dest == "index":
        add({"kind": "sleeve", "dest": "index", "label": f"{index} bought today (with dividends)"}, o["idx_val"])
    elif env.dest == "bond":
        add({"kind": "sleeve", "dest": "bond", "label": f"{env.bond_y * 100:.2f}% bond bought today (coupons after tax)"},
            o["bond_val"])
    add({"kind": "loan", "label": "Repay the loan with interest"}, -o["debt"])
    add({"kind": "tax", "label": "Tax triggered by the options / income"}, -o["tax"])
    if env.lens == "liquidate":
        add({"kind": "liq_tax", "label": "Tax on selling everything at expiry"}, -o["liq_tax"])
    mat = np.vstack(cols).T if cols else np.zeros((len(o["W"]), 0))
    return {"lines": lines, "values": [[int(round(v)) for v in row] for row in mat]}


def payoff_shape(tot_mkt: np.ndarray, tot_stk: np.ndarray, W0N: float, st: Strategy, env: Env,
                 pre_mkt: Optional[np.ndarray] = None, pre_stk: Optional[np.ndarray] = None,
                 pre_base: Optional[float] = None) -> dict:
    """Floor / cap read off the expiry curves: a floor exists when the value stops falling at the left end in BOTH the
    market-driven and the stock-only curve; a cap when it stops rising at the right end. `pre_*` are the same curves
    BEFORE tax — under 'never sell' a 1-for-1 put floors the pre-tax value but its payout is taxed, so the after-tax value
    keeps sliding (the case the tax-adjusted hedge exists for)."""
    tol = 0.015 * W0N
    floored = bool(np.ptp(tot_mkt[:3]) < tol and np.ptp(tot_stk[:3]) < tol)
    pre_floored = bool(pre_mkt is not None and np.ptp(pre_mkt[:3]) < tol and np.ptp(pre_stk[:3]) < tol)
    capped = bool(np.ptp(tot_mkt[-3:]) < tol and np.ptp(tot_stk[-3:]) < tol)
    lo = float(min(tot_mkt.min(), tot_stk.min()))
    hi = float(max(tot_mkt.max(), tot_stk.max()))
    puts = [l.K for l in st.legs if l.und == "S" and l.right == "P" and l.qty > 0]
    calls = [l.K for l in st.legs if l.und == "S" and l.right == "C" and l.qty < 0 and l.K >= env.S0]
    return {"floored": floored, "floor_value": round(float(tot_mkt[0]), 0) if floored else None,
            "worst_value": round(lo, 0), "worst_pct": round((lo / W0N - 1) * 100, 1),
            "best_value": round(hi, 0), "best_pct": round((hi / W0N - 1) * 100, 1),
            "pretax_floored": pre_floored,
            # a before-tax value is read against the before-tax position (under 'sell everything' W0N is after tax)
            "pretax_floor_pct": round((float(pre_mkt[0]) / (pre_base or W0N) - 1) * 100, 1) if pre_floored else None,
            "capped": capped, "cap_value": round(float(tot_mkt[-1]), 0) if capped else None,
            "protect_below": max(puts) if puts and (floored or pre_floored) else None,
            "cap_above": min(calls) if calls and capped else None}


def rank_and_present(cands: list[Strategy], res: dict, env: Env, *, shares: int, objective: str, max_loss_pct: float,
                     max_single_pct: float, allow_cs: bool, index: str, ticker: str) -> dict:
    key = {"cagr": "cagr", "ce": "ce_ann", "mean": "mean_ann"}.get(objective, "cagr")      # metric keys (fractions)
    hold_m = res["hold"]["m"]
    rows = []
    for st in cands:
        r = res[st.id]
        m, ex = r["m"], r["ex"]
        reasons, tags = [], []
        if m["cvar"] * 100 < -max_loss_pct:
            reasons.append(f"bad-case loss {m['cvar'] * 100:.0f}% is beyond your −{max_loss_pct:.0f}% limit")
            tags.append(f"bad case {m['cvar'] * 100:.0f}%")
        if ex["single_stock_pct"] > max_single_pct + 1e-9:
            reasons.append(f"{ticker} exposure {ex['single_stock_pct']:.0f}% is above your {max_single_pct:.0f}% target")
            tags.append(f"{ticker} {ex['single_stock_pct']:.0f}%")
        if st.cs["risk"] == "high" and not allow_cs:
            reasons.append("could be taxed as a sale (§1259)")
            tags.append("§1259")
        net_ps = -sum(l.qty * l.px for l in st.legs) if not st.sell_now else 0.0
        rows.append({
            "id": st.id, "family": st.family, "family_label": FAMILY_LABEL[st.family], "name": st.name, "blurb": st.blurb,
            "how": how_it_works(st, env, shares=shares, ticker=ticker, index=index),
            "legs": _legs_view(st, env, shares, index), "rolls": _rolls_view(st, shares, env.S0),
            "net_cash_total": round(net_ps * shares, 0), "to_dest_total": round(r["sleeve0"] * shares, 0),
            "loan_total": round(st.loan_ps * shares, 0),
            "cagr_pct": round(m["cagr"] * 100, 2), "mean_ann_pct": round(m["mean_ann"] * 100, 2),
            "ce_ann_pct": round(m["ce_ann"] * 100, 2), "cvar5_pct": round(m["cvar"] * 100, 1),
            "p_loss_pct": round(m["p_loss"] * 100, 1), "vol_ann_pct": round(m["vol_ann"] * 100, 1),
            "pctiles_pct": [round(v * 100, 1) for v in m["pct"]],
            "single_stock_pct": round(ex["single_stock_pct"], 0), "index_pct": round(ex["index_pct"], 0),
            "expected_tax_total": round(r["tax_mean"] * shares, 0),
            "vs_hold_cagr_pts": round((m["cagr"] - hold_m["cagr"]) * 100, 2),
            "constructive_sale": st.cs, "eligible": not reasons, "reasons": reasons, "reason_tags": tags,
            "warnings": st.warnings + margin_warnings(st, env, shares=shares, index=index),
            "tax_note": _tax_note(st, env, index), "money": r["money"], "dividends": r["divs"], "assignment": r["assign"],
            "shape": r["shape"], "payout": r["payout"],
            "_score": m[key],
        })
    rows.sort(key=lambda x: -x["_score"])
    elig = [x for x in rows if x["eligible"] and x["family"] != "reference"]
    if elig:
        best, note = elig[0], None
    else:
        pool = [x for x in rows if x["family"] != "reference"] or rows
        best = min(pool, key=lambda x: (len(x["reasons"]), -x["_score"]))
        note = "Nothing meets every limit — this is the closest. Loosen a limit to see more."
    for i, x in enumerate(rows):
        x["rank"] = i + 1
        x["recommended"] = x is best
        x.pop("_score", None)
    fam: dict = {}
    for x in sorted(rows, key=lambda x: (not x["eligible"], x["rank"])):     # per family: best within limits, else best
        fam.setdefault(x["family"], x["id"])
    return {"rows": rows, "best": best, "best_note": note,
            "families": [{"id": f, "label": FAMILY_LABEL[f], "best_id": fam.get(f)} for f in FAMILY_LABEL
                         if any(x["family"] == f for x in rows)]}


# ---------------------------------------------------------------------------
# market data
# ---------------------------------------------------------------------------

def _rows(chain) -> list:
    calls, puts = _split_chain(chain)
    out = []
    for book, right in ((calls, "C"), (puts, "P")):
        for K, q in book.items():
            out.append([float(K), right, _f(q.bid), _f(q.ask), int(_f(q.oi)), int(_f(q.volume))])
    return out


def choose_leap(exps: list[str], today: date, target_days: int, explicit: Optional[str] = None) -> tuple[Optional[tuple], list]:
    """(expiry, dte) nearest the target among monthly LEAPs (≥ MIN_LEAP_DTE; else the longest ≥ FALLBACK_MIN_DTE). Within
    ~45 days a January expiry wins — it lands the event in the next tax year. Also returns every eligible expiry."""
    cands = []
    for s in exps or []:
        try:
            d = datetime.strptime(s, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        dte = (d - today).days
        if dte >= FALLBACK_MIN_DTE and _is_monthly_expiry(d):
            cands.append((s, dte, d))
    if not cands:
        return None, []
    longs = [c for c in cands if c[1] >= MIN_LEAP_DTE]
    pool = longs or [max(cands, key=lambda c: c[1])]
    listing = [{"expiry": c[0], "dte": c[1]} for c in sorted(pool, key=lambda c: c[1])]
    if explicit:
        hit = [c for c in pool if c[0] == explicit]
        if hit:
            return (hit[0][0], hit[0][1]), listing
    near = sorted(pool, key=lambda c: abs(c[1] - target_days))
    best = near[0]
    jan = [c for c in near if c[2].month == 1 and abs(c[1] - target_days) <= 45]
    if jan:
        best = jan[0]
    return (best[0], best[1]), listing


def _short_expiry(exps: list[str], today: date) -> Optional[tuple]:
    c = []
    for s in exps or []:
        try:
            d = datetime.strptime(s, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        dte = (d - today).days
        if 25 <= dte <= 75 and _is_monthly_expiry(d):
            c.append((abs(dte - ROLL_TARGET_DTE), s, dte))
    if not c:
        return None
    _, s, dte = min(c)
    return s, dte


def _index_expiry(exps: list[str], today: date, dte_target: int) -> Optional[tuple]:
    c = []
    for s in exps or []:
        try:
            d = datetime.strptime(s, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        dte = (d - today).days
        if dte >= dte_target:
            c.append((dte, s))
    if not c:
        return None
    dte, s = min(c)
    return (s, dte) if dte - dte_target <= 120 else None


def _history_sync(ticker: str, index: str) -> dict:
    """2y aligned daily log returns → realised vols, ρ; trailing-12m dividend yields; the stock's latest ex-dividend date,
    amount and payments per year (to project its ex-dates). Best-effort."""
    out = {"hv_s": None, "hv_i": None, "rho": None, "n_obs": 0, "div_s": None, "div_i": None,
           "div_s_last_date": None, "div_s_last_amt": None, "div_s_per_year": 0}
    try:
        import yfinance as yf
        data = yf.download([ticker, index], period="2y", interval="1d", auto_adjust=True, progress=False)["Close"]
        df = data[[ticker, index]].dropna()
        lr = np.log(df).diff().dropna()
        if len(lr) >= 120:
            out["hv_s"] = float(lr[ticker].std() * math.sqrt(252))
            out["hv_i"] = float(lr[index].std() * math.sqrt(252))
            out["rho"] = float(lr[ticker].corr(lr[index]))
            out["n_obs"] = int(len(lr))
        for t, k in ((ticker, "div_s"), (index, "div_i")):
            try:
                tk = yf.Ticker(t)
                dv = tk.dividends
                px = float(df[t].iloc[-1])
                if dv is not None and len(dv) and px > 0:
                    import pandas as pd
                    cutoff = pd.Timestamp.now(tz=dv.index.tz) - pd.Timedelta(days=365)
                    out[k] = float(dv[dv.index > cutoff].sum()) / px
                    if k == "div_s":                                # frequency from the usual spacing of recent payments
                        gaps = np.diff(dv.index[-5:].values).astype("timedelta64[D]").astype(float)
                        per = int(round(365.0 / float(np.median(gaps)))) if len(gaps) and np.median(gaps) > 0 else 1
                        out["div_s_per_year"] = max(1, min(per, 12)) if dv.index[-1] > cutoff else 0
                        out["div_s_last_date"] = dv.index[-1].date().isoformat()
                        out["div_s_last_amt"] = float(dv.iloc[-1])
                else:
                    out[k] = 0.0
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        logger.info("leap-protection history %s/%s failed: %s", ticker, index, exc)
    return out


async def fetch_snapshot(provider, ticker: str, index: str, target_days: int, expiry: Optional[str], today: date) -> dict:
    """Live spot + LEAP chain (stock) + ~45d chain (stock, for rolls) + matching index LEAP chain + 2y history.
    Sequential on purpose (512 MiB instance)."""
    try:
        u = await provider.get_underlying_price(ticker)
        spot = _f(getattr(u, "price", 0))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Cannot fetch a price for {ticker}: {exc}"}
    if spot <= 0:
        return {"error": f"No live price for {ticker}."}
    try:
        exps = await provider.get_option_expirations(ticker)
    except Exception:  # noqa: BLE001
        exps = []
    pick, listing = choose_leap(exps, today, target_days, expiry)
    if not pick:
        return {"error": f"{ticker} has no listed monthly options ≥ {FALLBACK_MIN_DTE} days out — LEAP protection needs them."}
    leap_exp, leap_dte = pick
    try:
        chain = await provider.get_option_chain(ticker, leap_exp)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Could not fetch the {ticker} {leap_exp} chain: {exc}"}
    snap = {"ticker": ticker, "index": index, "spot": spot, "expiries": listing,
            "leap": {"expiry": leap_exp, "dte": leap_dte, "rows": _rows(chain)}}
    sx = _short_expiry(exps, today)
    if sx:
        try:
            ch = await provider.get_option_chain(ticker, sx[0])
            snap["short"] = {"expiry": sx[0], "dte": sx[1], "rows": _rows(ch)}
        except Exception as exc:  # noqa: BLE001
            logger.debug("short chain %s failed: %s", ticker, exc)
    try:
        ui = await provider.get_underlying_price(index)
        snap["index_spot"] = _f(getattr(ui, "price", 0))
        iexps = await provider.get_option_expirations(index)
        ix = _index_expiry(iexps, today, leap_dte)
        if ix and snap["index_spot"] > 0:
            ch = await provider.get_option_chain(index, ix[0])
            snap["index_leap"] = {"expiry": ix[0], "dte": ix[1], "rows": _rows(ch)}
    except Exception as exc:  # noqa: BLE001
        logger.info("index chain %s failed: %s", index, exc)
    snap["hist"] = await asyncio.to_thread(_history_sync, ticker, index)
    return snap


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def dividend_schedule(hist: dict, S0: float, today: date, T: float) -> tuple[tuple, tuple]:
    """The stock's ex-dividend dates inside the horizon (years from today) and $ per share: the latest payment carried
    forward at its usual spacing (no growth assumed). Without a usable history: the trailing yield paid quarterly."""
    from datetime import timedelta
    per, amt, last = int(hist.get("div_s_per_year") or 0), _f(hist.get("div_s_last_amt")), hist.get("div_s_last_date")
    if per >= 1 and amt > 0 and last:
        try:
            d0 = date.fromisoformat(str(last)[:10])
        except ValueError:
            d0 = None
        if d0 is not None:
            ts, k = [], 1
            while k < 200:
                t = ((d0 + timedelta(days=round(k * 365.0 / per))) - today).days / 365.0
                if t > T + 1e-9:
                    break
                if t > 1e-6:
                    ts.append(round(t, 6))
                k += 1
            return tuple(ts), tuple(amt for _ in ts)
    q = _f(hist.get("div_s"))
    if q > 0:
        ts = tuple(round(t, 6) for t in np.arange(0.125, T, 0.25))
        return ts, tuple(q * S0 / 4 for _ in ts)
    return (), ()


def _jm_view(jm_s: Optional[JumpModel], jm_i: Optional[JumpModel]) -> Optional[dict]:
    if jm_s is None:
        return None
    out = {"stock_diffusion_pct": round(jm_s.sigma * 100, 1), "stock_idio_jumps_per_yr": round(jm_s.lam, 2),
           "stock_idio_jump_mean_pct": round((math.exp(jm_s.m) - 1) * 100, 1), "stock_fit_vol_pts": round(jm_s.rmse_vol_pts or 0, 2)}
    if jm_i is not None:
        out.update({"index_diffusion_pct": round(jm_i.sigma * 100, 1), "market_jumps_per_yr": round(jm_i.lam, 2),
                    "market_jump_mean_pct": round((math.exp(jm_i.m) - 1) * 100, 1),
                    "index_fit_vol_pts": round(jm_i.rmse_vol_pts or 0, 2)})
    return out


def compute(snap: dict, *, shares: int, cost_basis: float, tax: dict, tax_lens: str, erp_pct: float, alpha_pct: float,
            vol_view: str, objective: str, max_loss_pct: float, max_single_stock_pct: float, borrow_spread_pct: float,
            loan_ltv_pct: float, allow_constructive_sale: bool, r: float, r_label: str, today: date,
            proceeds_to: str = "index", bond_rate_pct: float = 4.5, stock_return_pct: Optional[float] = None,
            n_paths: int = N_PATHS) -> dict:
    ticker, index = snap["ticker"], snap["index"]
    S0 = float(snap["spot"])
    I0 = _f(snap.get("index_spot"))
    hist = snap.get("hist") or {}
    leap = snap["leap"]
    T = leap["dte"] / 365.0
    try:
        sb = Book(S0, T, r, leap["rows"], leap["expiry"], q_hint=_f(hist.get("div_s")))
    except ValueError as exc:
        return {"error": f"{ticker} {leap['expiry']}: {exc}."}
    xb = ib = None
    notes = []
    if snap.get("short"):
        try:
            xb = Book(S0, snap["short"]["dte"] / 365.0, r, snap["short"]["rows"], snap["short"]["expiry"],
                      q_hint=_f(hist.get("div_s")))
        except ValueError:
            notes.append("Short-dated chain too thin — calendars/diagonals skipped.")
    else:
        notes.append("No ~45-day monthly expiry — calendars/diagonals skipped.")
    if snap.get("index_leap") and I0 > 0:
        try:
            ib = Book(I0, snap["index_leap"]["dte"] / 365.0, r, snap["index_leap"]["rows"], snap["index_leap"]["expiry"],
                      q_hint=_f(hist.get("div_i")))
        except ValueError:
            notes.append(f"{index} LEAP chain too thin — index structures skipped.")
    else:
        notes.append(f"No {index} expiry on/after {leap['expiry']} — index structures skipped.")
    if I0 <= 0:
        return {"error": f"No live price for the index {index}."}

    # ---- the real-world model
    rho = hist.get("rho")
    rho = max(-0.95, min(0.95, rho)) if rho is not None else 0.6
    if rho is None or hist.get("rho") is None:
        notes.append("Correlation history unavailable — ρ = 0.60 assumed.")
    iv_i = ib.atm_iv if ib else (hist.get("hv_i") or 0.18)
    if vol_view == "realized" and hist.get("hv_s") and hist.get("hv_i"):
        sig_s, sig_i = float(hist["hv_s"]), float(hist["hv_i"])
    else:
        sig_s, sig_i = sb.atm_iv, iv_i
        vol_view = "implied"
    beta = rho * sig_s / sig_i
    erp, alpha = erp_pct / 100.0, alpha_pct / 100.0
    capm_s = r + beta * erp
    if stock_return_pct is not None:                     # the user's own expected return for the stock overrides CAPM + α
        alpha = stock_return_pct / 100.0 - capm_s
    mu_i = r + erp
    mu_s = capm_s + alpha
    div_t, div_amt = dividend_schedule(hist, S0, today, T)
    per_yr = int(hist.get("div_s_per_year") or 0)
    q_s = per_yr * div_amt[0] / S0 if (div_amt and per_yr >= 1) else max(0.0, _f(hist.get("div_s"), max(sb.q, 0.0)))
    q_i = max(0.0, _f(hist.get("div_i"), max(ib.q, 0.0) if ib else 0.013))
    lt, st_ = tax["lt_rate_pct"] / 100.0, tax["st_rate_pct"] / 100.0
    env = Env(S0=S0, I0=I0, B=cost_basis, T=T, r=r, r_b=r + borrow_spread_pct / 100.0, q_s=q_s, q_i=q_i, lt=lt, st=st_,
              idx1256=index.upper() in INDEX_1256, lens=tax_lens,
              dest=proceeds_to if proceeds_to in DESTS else "index", bond_y=bond_rate_pct / 100.0,
              div_t=div_t, div_amt=div_amt)

    cands = build_candidates(env, sb, xb, ib, beta=beta, ltv=loan_ltv_pct, index=index)
    times, cycle = time_grid(T, xb.T if xb else 0.0, extra=env.div_t)
    # ---- the real-world process. 'implied': a jump-diffusion fitted to each LEAP smile (the market's tail shape — skew —
    # is taken as FAIR; only the drift is tilted by the ERP), stock and index sharing market crash-jumps at β. Diffusion
    # follows the market's ATM term structure (short-dated ATM for the first roll cycle, the forward vol after it).
    # 'realized': GBM at 2-year realised vols, no jumps.
    jm_s = jm_i = None
    rho_d, sig_i_d = rho, sig_i
    target = np.full(len(times) - 1, sb.atm_iv)
    j_fwd = 0
    if xb is not None and (cycle >= 0).any():
        target[:] = forward_vol(xb.atm_iv, xb.T, sb.atm_iv, T)
        target[times[:-1] < xb.T - 1e-9] = xb.atm_iv            # the first roll window runs at the short-dated ATM
        j_fwd = min(int(np.searchsorted(times[:-1], xb.T - 1e-9)), len(times) - 2)
    if vol_view == "implied":
        jm_i = calibrate_jumps(ib) if ib is not None else None
        if jm_i is not None and (jm_i.rmse_vol_pts or 99) > 3.0:
            notes.append(f"{index} smile fit was poor ({jm_i.rmse_vol_pts:.1f} vol pts) — index modelled without jumps.")
            jm_i = None
        jm_s = calibrate_jumps(sb, common=jm_i, beta=beta) if jm_i is not None else calibrate_jumps(sb)
        if jm_s is not None and (jm_s.rmse_vol_pts or 99) > 3.0:
            notes.append(f"{ticker} smile fit was poor ({jm_s.rmse_vol_pts:.1f} vol pts) — stock modelled without jumps.")
            jm_s = None
        if jm_s is None and jm_i is not None:
            jm_i = None                                      # keep the pair consistent: no common jumps on one side only
        if jm_s is not None:
            sig_s_d = np.sqrt(np.maximum(jm_s.sigma ** 2 + target ** 2 - sb.atm_iv ** 2, (0.25 * jm_s.sigma) ** 2))
        else:
            sig_s_d = target
        if jm_i is not None:
            sig_i_d = jm_i.sigma
            rho_d = diffusion_rho(rho, jm_s, jm_i)
    else:
        sig_s_d = np.full(len(times) - 1, sig_s)
    S, I = simulate(S0=S0, I0=I0, mu_s=mu_s, mu_i=mu_i, q_s=q_s, q_i=q_i, sig_s=sig_s_d, sig_i=sig_i_d, rho=rho_d,
                    times=times, n_paths=n_paths, jm_s=jm_s, jm_i=jm_i)
    if jm_s is not None and len(times) > 2:
        roll_model = JumpModel(**{**jm_s.__dict__, "sigma": float(np.atleast_1d(sig_s_d)[j_fwd])})
        for stg in cands:
            for ro in stg.rolls:
                ro.model = roll_model
    # Starting wealth: market value if the shares are never sold; AFTER-TAX liquidation value under the liquidate lens
    # (so every row, including 'sell now', is measured from the same after-tax base).
    W0 = S0 if tax_lens == "defer" else S0 - lt * max(S0 - cost_basis, 0.0)
    # Expiry payout grid: the stock ends at S0·(1+m); 'market' = the index moves with it at β, 'stock_only' = index flat.
    bcl = max(beta, 0.3)
    mk_moves = [(m, ((1 + m / 100.0) ** (1 / bcl) - 1) * 100.0) for m in GRID_MOVES]
    gS1, gI1 = scenario_paths(S0, I0, mk_moves, times)
    gS2, gI2 = scenario_paths(S0, I0, [(m, 0.0) for m in GRID_MOVES], times)

    res = {}
    for stg in cands:
        o = evaluate(stg, S, I, times, cycle, env)
        m = outcome_metrics(o["W"], I[:, -1], W0, T)
        o1 = evaluate(stg, gS1, gI1, times, cycle, env)
        o2 = evaluate(stg, gS2, gI2, times, cycle, env)
        shape = payoff_shape(o1["W"] * shares, o2["W"] * shares, W0 * shares, stg, env,
                             (o1["W"] + o1["tax"] + o1["liq_tax"]) * shares, (o2["W"] + o2["tax"] + o2["liq_tax"]) * shares,
                             pre_base=S0 * shares)
        ann = lambda v: round(((max(v, 1.0) / (W0 * shares)) ** (1 / T) - 1) * 100, 1)      # noqa: E731
        shape.update({
            "floor_ann_pct": ann(shape["floor_value"]) if shape["floored"] else None,
            "cap_ann_pct": ann(shape["cap_value"]) if shape["capped"] else None,
            "worst_ann_pct": ann(shape["worst_value"]), "best_ann_pct": ann(shape["best_value"]),
            # real-world odds (same simulation as the expected return) of ending on the cap / on the floor
            "p_cap_pct": round(float((S[:, -1] >= shape["cap_above"]).mean()) * 100, 0) if shape["cap_above"] else None,
            "p_floor_pct": round(float((S[:, -1] <= shape["protect_below"]).mean()) * 100, 0) if shape["protect_below"] else None,
        })
        res[stg.id] = {"m": m, "ex": exposure(stg, env, beta, o["units"]), "sleeve0": o["sleeve0"],
                       "money": premium_block(stg, env, o, shares=shares, index=index),
                       "divs": dividend_block(stg, env, o, shares=shares, index=index),
                       "assign": assignment_block(stg, env, o, times, shares=shares, ticker=ticker, index=index,
                                                  today=today),
                       "tax_mean": float(o["tax"].mean()) + o["tax_upfront"], "shape": shape,
                       "payout": {"market": ledger(stg, o1, env, shares=shares, ticker=ticker, index=index),
                                  "stock_only": ledger(stg, o2, env, shares=shares, ticker=ticker, index=index)}}
    del S, I

    out = rank_and_present(cands, res, env, shares=shares, objective=objective, max_loss_pct=max_loss_pct,
                           max_single_pct=max_single_stock_pct, allow_cs=allow_constructive_sale, index=index,
                           ticker=ticker)
    gain = (S0 - cost_basis) * shares
    return {
        "success": True, "as_of": today.isoformat(), "ticker": ticker, "index": index,
        "spot": round(S0, 2), "index_spot": round(I0, 2), "shares": shares, "cost_basis": cost_basis,
        "position_value": round(S0 * shares, 0), "embedded_gain": round(gain, 0), "horizon_label": _exp_short(leap["expiry"]),
        "starting_wealth": round(W0 * shares, 0),
        "embedded_tax": round(max(gain, 0.0) * lt, 0),
        "horizon": {"expiry": leap["expiry"], "dte": leap["dte"], "years": round(T, 2),
                    "index_expiry": ib.expiry if ib else None, "roll_expiry": xb.expiry if xb else None,
                    "roll_days": round(xb.T * 365) if xb else None, "available": snap.get("expiries", [])},
        "market": {"atm_iv_pct": round(sb.atm_iv * 100, 1), "index_atm_iv_pct": round(iv_i * 100, 1),
                   "hv_pct": round(hist["hv_s"] * 100, 1) if hist.get("hv_s") else None,
                   "index_hv_pct": round(hist["hv_i"] * 100, 1) if hist.get("hv_i") else None,
                   "rho": round(rho, 2), "beta": round(beta, 2), "sigma_used_pct": round(sig_s * 100, 1),
                   "index_sigma_used_pct": round(sig_i * 100, 1), "carry_pct": round(sb.q * 100, 2),
                   "short_atm_iv_pct": round(xb.atm_iv * 100, 1) if xb else None,
                   "jumps": _jm_view(jm_s, jm_i),
                   "forward_vol_pct": round(forward_vol(xb.atm_iv, xb.T, sb.atm_iv, T) * 100, 1) if xb else None,
                   "div_yield_pct": round(q_s * 100, 2), "index_div_yield_pct": round(q_i * 100, 2),
                   "risk_free_pct": round(r * 100, 2), "risk_free_source": r_label,
                   "stock_drift_pct": round(mu_s * 100, 2), "index_drift_pct": round(mu_i * 100, 2),
                   "capm_stock_pct": round(capm_s * 100, 2),
                   "corr_obs": hist.get("n_obs")},
        "best_id": out["best"]["id"], "best_note": out["best_note"],
        "strategies": out["rows"], "families": out["families"],
        "destination": {"kind": env.dest, "label": dest_label(env, index), "rate_pct": round(env.bond_y * 100, 2)
                        if env.dest == "bond" else round(r * 100, 2) if env.dest == "cash" else None},
        "grid": {"moves": GRID_MOVES, "prices": [round(S0 * (1 + m / 100.0), 2) for m in GRID_MOVES],
                 "index_moves_market": [round(v[1], 1) for v in mk_moves]},
        "scenarios": [{"id": sid, "label": lab, "mode": mode, "move": mv} for sid, lab, mode, mv in SCENARIOS],
        "assumptions": {"objective": objective, "tax_lens": tax_lens, "vol_view": vol_view, "erp_pct": erp_pct,
                        "alpha_pct": alpha_pct, "lt_rate_pct": tax["lt_rate_pct"], "st_rate_pct": tax["st_rate_pct"],
                        "index_1256_pct": round(env.b1256 * 100, 1) if env.idx1256 else None,
                        "max_loss_pct": max_loss_pct, "max_single_stock_pct": max_single_stock_pct,
                        "borrow_rate_pct": round(env.r_b * 100, 2), "loan_ltv_pct": loan_ltv_pct,
                        "allow_constructive_sale": allow_constructive_sale, "paths": n_paths, "crra_gamma": CRRA_GAMMA,
                        "tail_q_pct": TAIL_Q * 100, "min_band_pct": MIN_BAND_PCT, "proceeds_to": env.dest,
                        "bond_rate_pct": bond_rate_pct},
        "notes": notes + ([f"Fewer than 100 shares — listed options trade in 100-share contracts; results are per share."]
                          if shares < CONTRACT else []),
        "alternatives": ALTERNATIVES, "disclaimer": DISCLAIMER,
    }


async def run_leap_protection(*, ticker: str, shares: int, cost_basis: float, index: str = "SPY",
                              horizon_years: float = 2.0, expiry: Optional[str] = None, tax: dict,
                              tax_lens: str = "defer", erp_pct: float = DEFAULT_ERP, alpha_pct: float = 0.0,
                              vol_view: str = "implied", objective: str = "cagr", max_loss_pct: float = 25.0,
                              max_single_stock_pct: float = 50.0, borrow_spread_pct: float = DEFAULT_BORROW_SPREAD,
                              loan_ltv_pct: float = DEFAULT_LTV, allow_constructive_sale: bool = False,
                              proceeds_to: str = "index", bond_rate_pct: float = 4.5,
                              stock_return_pct: Optional[float] = None,
                              quote_source: str = "yfinance", user=None, db=None) -> dict:
    ticker = ticker.strip().upper()
    index = (index or "SPY").strip().upper()
    if ticker == index:
        return {"error": "Pick a diversifying index different from the stock itself."}
    if cost_basis <= 0 or shares <= 0:
        return {"error": "Enter the shares held and their average cost basis."}
    today = date.today()
    target_days = int(round(horizon_years * 365))
    try:
        r, r_label = await _get_sofr()
    except Exception:  # noqa: BLE001
        r, r_label = DEFAULT_RISK_FREE, "default"
    key = f"leapprot:snap:{ticker}:{index}:{expiry or target_days}:{today.isoformat()}:v2"
    snap = None
    if db is not None:
        from .cache_service import get_cached
        snap = await get_cached(db, key)
    if not snap:
        provider = get_provider(quote_source, user=user, db=db)
        snap = await fetch_snapshot(provider, ticker, index, target_days, expiry, today)
        if "error" in snap:
            return snap
        if db is not None:
            from .cache_service import set_cached
            await set_cached(db, key, snap, ttl_seconds=CACHE_TTL)
    return await asyncio.to_thread(
        compute, snap, shares=shares, cost_basis=cost_basis, tax=tax, tax_lens=tax_lens, erp_pct=erp_pct,
        alpha_pct=alpha_pct, vol_view=vol_view, objective=objective, max_loss_pct=max_loss_pct,
        max_single_stock_pct=max_single_stock_pct, borrow_spread_pct=borrow_spread_pct, loan_ltv_pct=loan_ltv_pct,
        allow_constructive_sale=allow_constructive_sale, r=r, r_label=r_label, today=today, proceeds_to=proceeds_to,
        bond_rate_pct=bond_rate_pct, stock_return_pct=stock_return_pct)
