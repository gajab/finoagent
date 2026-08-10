"""Trade math — the single source of truth for position analytics.

Every number shown to the user for annualized return, P&L, or cost basis
flows through this module. Two goals:

1. **Consistency** — the same trade, rendered in BoxStrategy, TradePortfolio,
   or My Trades, produces *byte-identical* numbers. No more "simple in one
   screen, geometric in another".
2. **Transparency** — every computed number is accompanied by the inputs
   and conventions used. Surfacing in UI includes warnings when inputs
   are weak (stale quotes, wide spreads, cross markets).

Convention
----------
- Annualization: **geometric**, 365-day calendar year.
      ann = (1 + profit/cost) ^ (365/days) − 1
  We deliberately avoid simple-interest annualization. For a 2% return over
  30 days, simple gives 24.33%, geometric gives 29.0%. Simple systematically
  understates; every professional platform reports geometric.
- Days-to-expiry: **calendar days** to 4pm ET on expiry. No business-day math.
- Option prices: **per share** (×100 is applied only at display / cost_total).
- Mid: (bid + ask) / 2 when both > 0; last otherwise; None if no quote.
- Sign conventions: cost is always positive (capital at risk); profit is
  always positive for a winning trade; loss is represented separately.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Literal, Optional


# ── Primitives ──────────────────────────────────────────────────────────

def mid_price(bid: Optional[float], ask: Optional[float], last: Optional[float] = None) -> Optional[float]:
    """Normalize a quote to a single price.

    - Prefer mid = (bid + ask) / 2 when both > 0 and bid <= ask.
    - Fall back to last if quote is crossed or one-sided.
    - Return None if no usable price exists.
    """
    b = bid if (bid is not None and bid > 0) else None
    a = ask if (ask is not None and ask > 0) else None
    if b is not None and a is not None and b <= a:
        return (b + a) / 2.0
    if last is not None and last > 0:
        return float(last)
    # One-sided but not crossed
    if a is not None and b is None:
        return float(a)
    if b is not None and a is None:
        return float(b)
    return None


def is_wide_spread(bid: Optional[float], ask: Optional[float], threshold_pct: float = 5.0) -> bool:
    """True if bid/ask spread exceeds threshold_pct of mid. Threshold default 5%."""
    m = mid_price(bid, ask)
    if m is None or m <= 0 or bid is None or ask is None:
        return False
    spread = abs(ask - bid)
    return (spread / m) * 100.0 > threshold_pct


def is_crossed(bid: Optional[float], ask: Optional[float]) -> bool:
    """True if bid > ask (crossed market — bad quote)."""
    if bid is None or ask is None:
        return False
    return bid > ask and bid > 0 and ask > 0


def days_between(d1, d2) -> int:
    """Calendar days between two dates (or datetimes). Truncates to date part.
    Returns 0 if d2 < d1 (never negative — caller's responsibility to check order)."""
    if isinstance(d1, datetime):
        d1 = d1.date()
    if isinstance(d2, datetime):
        d2 = d2.date()
    delta = (d2 - d1).days
    return max(0, delta)


def dte_from_expiry(expiry: str | date, today: Optional[date] = None) -> int:
    """Days-to-expiry from an ISO 'YYYY-MM-DD' string or date, using calendar days."""
    if isinstance(expiry, str):
        try:
            expiry = datetime.strptime(expiry, "%Y-%m-%d").date()
        except ValueError:
            return 0
    base = today or date.today()
    return max(0, (expiry - base).days)


# ── Annualization — the single canonical formula ───────────────────────

def annualized_return(profit: float, cost: float, days: int) -> float:
    """Geometric annualized return as a decimal (0.30 = 30%).

    Returns 0.0 for degenerate inputs (cost<=0, days<=0) rather than raising,
    so callers can render something benign instead of crashing.

    >>> round(annualized_return(2, 100, 30), 4)   # 2% over 30 days
    0.2898
    >>> round(annualized_return(5, 100, 365), 4)  # 5% over 365 days
    0.05
    """
    if cost <= 0 or days <= 0:
        return 0.0
    roi = profit / cost
    # Guard against absurd compounding blowup from tiny days
    if days < 1:
        days = 1
    return (1.0 + roi) ** (365.0 / days) - 1.0


def annualized_return_pct(profit: float, cost: float, days: int) -> float:
    """Same as annualized_return, returned as a percent (30.0 = 30%)."""
    return annualized_return(profit, cost, days) * 100.0


def simple_annualized_pct(profit: float, cost: float, days: int) -> float:
    """Simple-interest annualization. Kept available for nostalgia + reference,
    but should NEVER be used in new UI. Present so tests can prove the
    geometric formula diverges from the old math as expected."""
    if cost <= 0 or days <= 0:
        return 0.0
    return (profit / cost) * (365.0 / days) * 100.0


def realized_close_pnl(action: str, entry_price: float, exit_price: float,
                       qty: float, *, is_option: bool = True) -> float:
    """Realized P&L from closing a leg (or the stock) at ``exit_price``.

    Single source of the close sign convention, mirroring the trade P&L math:
    a SHORT leg earns (entry − exit) — you sold to open, buy back cheaper to keep
    the difference; a LONG leg earns (exit − entry). Options scale ×100×contracts;
    stock scales ×shares. ``qty`` is always the magnitude (contracts / shares).
    """
    is_short = any(k in str(action).upper() for k in ("SELL", "SHORT"))
    per = (entry_price - exit_price) if is_short else (exit_price - entry_price)
    return per * (100.0 if is_option else 1.0) * float(qty)


# ── Quote staleness ─────────────────────────────────────────────────────

def is_stale(ts: Optional[datetime], now: Optional[datetime] = None, max_age_seconds: int = 300) -> bool:
    """True if quote timestamp is older than max_age_seconds. Default 5 minutes."""
    if ts is None:
        return False  # no ts to check — caller decides
    n = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (n - ts).total_seconds()
    return age > max_age_seconds


# ── Position P&L from the transaction ledger ────────────────────────────

@dataclass
class PositionSnapshot:
    """Current state of a position derived from walking the transaction ledger.

    Uses FIFO lot matching for realized P&L. Open lots are averaged for
    the 'avg_cost' figure; individual lots are preserved for tax purposes.
    """
    net_quantity: float                         # signed; positive = long, negative = short
    avg_cost: float                             # weighted avg of open lots; 0 if no position
    total_cost_basis: float                     # sum of open lot (price * qty)
    realized_pnl: float                         # closed lots only
    fees_paid: float                            # total fees across all transactions
    open_lots: list[dict] = field(default_factory=list)   # [{qty, price, date}]

    @property
    def is_open(self) -> bool:
        return abs(self.net_quantity) > 1e-9


def walk_ledger(transactions: list[dict]) -> PositionSnapshot:
    """Walk a list of transactions (oldest first) and return current position state.

    Each transaction dict must have keys: action, quantity (signed),
    price, fees, executed_at. Ledger is treated as append-only truth.
    """
    open_lots: list[dict] = []
    realized = 0.0
    fees = 0.0

    # Sort defensively — callers should pass ordered list but we don't trust them.
    txs = sorted(transactions, key=lambda t: (t.get("executed_at") or "", t.get("id") or 0))

    for t in txs:
        qty = float(t.get("quantity") or 0.0)
        price = float(t.get("price") or 0.0)
        fees += float(t.get("fees") or 0.0)
        action = (t.get("action") or "").lower()

        if qty == 0:
            # adjust/roll with no quantity change — nothing to match
            continue

        if action in {"open", "add"} or (action == "adjust" and qty > 0):
            # Add to position — new lot
            open_lots.append({"qty": qty, "price": price, "date": t.get("executed_at")})
        elif action in {"reduce", "close"} or (action == "adjust" and qty < 0):
            # Match against open lots FIFO. Sign: if position is long, reducing
            # means selling (qty should be negative); we compare abs values.
            remaining = abs(qty)
            while remaining > 1e-9 and open_lots:
                lot = open_lots[0]
                lot_qty = abs(lot["qty"])
                if lot_qty <= remaining:
                    # Close out entire lot
                    direction = 1.0 if lot["qty"] > 0 else -1.0
                    realized += (price - lot["price"]) * lot_qty * direction
                    remaining -= lot_qty
                    open_lots.pop(0)
                else:
                    # Partial close of the lot
                    direction = 1.0 if lot["qty"] > 0 else -1.0
                    realized += (price - lot["price"]) * remaining * direction
                    # Shrink lot
                    if lot["qty"] > 0:
                        lot["qty"] -= remaining
                    else:
                        lot["qty"] += remaining
                    remaining = 0.0
            # If we had more qty than open lots, we opened a new position in
            # the opposite direction — but for now treat the excess as noise.
            # (A real short-after-close flow should record a separate 'open'.)

    net_qty = sum(lot["qty"] for lot in open_lots)
    total_cost = sum(lot["qty"] * lot["price"] for lot in open_lots)
    avg = (total_cost / net_qty) if abs(net_qty) > 1e-9 else 0.0
    return PositionSnapshot(
        net_quantity=net_qty,
        avg_cost=avg,
        total_cost_basis=total_cost,
        realized_pnl=realized,
        fees_paid=fees,
        open_lots=open_lots,
    )


# ── Per-leg quant advisor — deterministic close / hold / roll ───────────
#
# The single source of truth for the action we recommend on each option leg
# and the trade as a whole. Pure functions: they take already-computed
# numbers (probability ITM, entry vs current premium, DTE) and return a verdict
# with a plain-English reason. No market data, no numpy — so they are trivially
# unit-testable and produce byte-identical output everywhere they're rendered.
#
# The probability-of-ITM itself comes from the market-implied risk-neutral
# density (quant_service SVI/RND) at the caller; when that isn't available the
# caller falls back to prob_itm_lognormal() below.

# Action vocabulary. LET_EXPIRE is a distinct, gentler cousin of CLOSE:
# "it's basically worthless, no need to pay commission to buy it back".
LegAction = Literal["CLOSE", "HOLD", "ROLL", "LET_EXPIRE"]


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via the stdlib error function (no scipy dependency)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def prob_itm_lognormal(
    spot: float,
    strike: float,
    iv: float,
    dte_days: int,
    right: str,
    r: float = 0.05,
) -> Optional[float]:
    """Risk-neutral P(option finishes in the money) under Black-Scholes/GBM.

    Fallback for when a fitted RND smile isn't available. Uses the N(d2) term:
        call: P(S_T > K) = N(d2)
        put:  P(S_T < K) = N(-d2)
      d2 = [ln(S/K) + (r − ½σ²)T] / (σ√T)

    `iv` accepts either a percent (e.g. 32.0) or a decimal (0.32). Returns a
    probability in [0, 1], or None for degenerate inputs.
    """
    if spot <= 0 or strike <= 0 or dte_days <= 0 or not iv or iv <= 0:
        return None
    sigma = iv / 100.0 if iv > 3.0 else float(iv)   # accept % or decimal
    if sigma <= 0:
        return None
    T = dte_days / 365.0
    d2 = (math.log(spot / strike) + (r - 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    is_call = str(right).upper().startswith("C")
    return _norm_cdf(d2) if is_call else _norm_cdf(-d2)


def _captured_pct(sign: int, entry_prem: Optional[float], current_mid: Optional[float]) -> Optional[float]:
    """Fraction of the leg's edge realized so far, from the holder's view.

    short (sign<0): (entry_credit − current_cost_to_buy_back) / entry_credit
                    → 1.0 = fully decayed (max profit), negative = moved against us.
    long  (sign>0): (current_value − entry_cost) / entry_cost
                    → +1.0 = doubled, −1.0 = total loss.
    Returns None when we can't compute (missing/zero entry premium).
    """
    if entry_prem is None or current_mid is None or abs(entry_prem) < 1e-9:
        return None
    if sign < 0:
        return (abs(entry_prem) - current_mid) / abs(entry_prem)
    return (current_mid - abs(entry_prem)) / abs(entry_prem)


def classify_leg_action(
    *,
    sign: int,
    right: str,
    p_itm: Optional[float],
    entry_prem: Optional[float],
    current_mid: Optional[float],
    dte: int,
) -> dict:
    """Deterministic close / hold / roll recommendation for ONE option leg.

    Inputs:
      sign        +1 for a long (bought) leg, −1 for a short (sold) leg
      right       "C"/"P" (or "CALL"/"PUT")
      p_itm       risk-neutral probability the leg finishes ITM, 0..1 (or None)
      entry_prem  per-share premium at entry (sign-agnostic; abs is used)
      current_mid per-share current mid (or None if no quote)
      dte         calendar days to expiry

    Returns {action, reason, p_itm_pct, captured_pct}.
    """
    is_put = str(right).upper().startswith("P")
    rt = "put" if is_put else "call"
    captured = _captured_pct(sign, entry_prem, current_mid)
    p = p_itm if p_itm is not None else None
    p_txt = f"{p * 100:.0f}%" if p is not None else "n/a"
    mid_txt = f"${current_mid:.2f}" if current_mid is not None else "n/a"

    action: LegAction
    if sign < 0:
        # ── SHORT leg: we sold it and want it to expire worthless ──
        if p is not None and p <= 0.15 and (captured is None or captured >= 0.70):
            if dte <= 5 and current_mid is not None and current_mid <= 0.10:
                action = "LET_EXPIRE"
                reason = (f"Short {rt} worth just {mid_txt} with {p_txt} chance ITM and {dte}d left — "
                          f"let it expire (or buy back for pennies to free margin).")
            else:
                cap_txt = f", {captured * 100:.0f}% of credit captured" if captured is not None else ""
                action = "CLOSE"
                reason = (f"Short {rt} only {p_txt} likely to finish ITM{cap_txt} — "
                          f"buy to close, bank the gain and free the margin.")
        elif p is not None and p >= 0.50:
            defend = "down" if is_put else "up"
            action = "ROLL"
            reason = (f"Short {rt} now {p_txt} likely to finish ITM — roll {defend}/out to defend "
                      f"before assignment risk builds.")
        elif dte <= 3:
            action = "HOLD"
            reason = f"Short {rt} {p_txt} ITM with {dte}d left — hold and let theta finish it."
        else:
            action = "HOLD"
            reason = f"Short {rt} {p_txt} ITM — theta is working for you, hold."
    else:
        # ── LONG leg: we bought it and want it in the money ──
        winning = captured is None or captured >= 0.0
        if p is not None and p >= 0.60 and winning:
            action = "HOLD"
            reason = f"Long {rt} {p_txt} likely ITM and in profit vs entry — hold, it's working."
        elif p is not None and p <= 0.15:
            if current_mid is not None and current_mid <= 0.15:
                action = "CLOSE"
                reason = (f"Long {rt} down to {mid_txt} with only {p_txt} chance ITM — "
                          f"salvage what's left rather than ride it to zero.")
            else:
                action = "ROLL"
                reason = (f"Long {rt} only {p_txt} likely ITM with {dte}d left — roll closer to spot "
                          f"to cut theta bleed if the thesis holds.")
        elif dte <= 3:
            action = "CLOSE"
            reason = (f"Long {rt} near expiry ({dte}d) — realize the {mid_txt} of value now rather than "
                      f"carry gamma/theta risk overnight.")
        else:
            action = "HOLD"
            reason = f"Long {rt} {p_txt} ITM — thesis still live, hold."

    return {
        "action": action,
        "reason": reason,
        "p_itm_pct": round(p * 100, 1) if p is not None else None,
        "captured_pct": round(captured * 100, 1) if captured is not None else None,
    }


# ── Structural payoff extremes — max profit/loss of the WHOLE trade ─────
#
# An option structure's expiry P&L is piecewise-linear with kinks only at the
# strikes. So the global max and min over all prices [0, ∞) occur either at a
# strike, at price 0 (the lowest price can go), or off at ±∞ if a tail slopes.
# Evaluating those breakpoints gives the exact max profit / max loss and the
# price where each happens — not a windowed approximation.

def _stock_pnl(stock: Optional[dict], price: float) -> float:
    """Linear P&L of a stock or futures leg at `price`.
    stock = {shares (signed: + long / − short), avg_cost, mult (1 for equity,
    contract multiplier for futures)}."""
    if not stock:
        return 0.0
    return float(stock["shares"]) * float(stock.get("mult") or 1.0) * (price - float(stock["avg_cost"]))


def expiry_payoff(legs: list[dict], price: float, entry_cost: float,
                  stock: Optional[dict] = None) -> float:
    """Total P&L of the structure at expiry for a given underlying `price`.

    Each option leg dict needs: strike, right ("C"/"P"), sign (+1 long / −1 short),
    qty. `entry_cost` is in the storage convention (BUY negative / SELL positive),
    i.e. it is *added* (a debit is negative and pulls P&L down). An optional linear
    `stock` leg (see _stock_pnl) is added on top — covers covered calls, collars,
    stock+option combos, and pure equity/futures.
    """
    total = float(entry_cost) + _stock_pnl(stock, price)
    for lg in legs:
        K = float(lg["strike"])
        if str(lg["right"]).upper().startswith("C"):
            intrinsic = max(0.0, price - K)
        else:
            intrinsic = max(0.0, K - price)
        total += lg["sign"] * float(lg["qty"] or 1) * 100.0 * intrinsic
    return total


def structure_payoff_extremes(legs: list[dict], entry_cost: float,
                              stock: Optional[dict] = None) -> Optional[dict]:
    """Exact max profit / max loss of the whole position at expiry, with the price
    at which each occurs and whether either tail is unbounded. Handles options,
    an optional linear stock/futures leg, or both.

    Returns None only if there is nothing to evaluate. Keys: max_profit,
    max_profit_price, max_loss, max_loss_price (None if that side is unbounded),
    unbounded_profit, unbounded_loss (bools).
    """
    strikes = sorted({float(lg["strike"]) for lg in legs if lg.get("strike")})
    if not strikes and not stock:
        return None

    # Slope as price → ∞: long calls + the linear stock/futures leg carry it up,
    # short calls carry it down. Price can't fall below 0, so the low side is
    # always bounded (evaluated at 0).
    stock_slope = (float(stock["shares"]) * float(stock.get("mult") or 1.0)) if stock else 0.0
    slope_high = stock_slope + sum(
        lg["sign"] * float(lg["qty"] or 1) * 100.0
        for lg in legs if str(lg["right"]).upper().startswith("C")
    )
    unbounded_profit = slope_high > 1e-9
    unbounded_loss = slope_high < -1e-9

    if strikes:
        hi_probe = strikes[-1] * 1.5 + 10.0
        probes = [0.0] + strikes + [hi_probe]
    else:
        # Pure linear (stock/futures): extremes are at 0 or the far tail only.
        anchor = float(stock["avg_cost"]) if stock and stock.get("avg_cost") else 100.0
        hi_probe = anchor * 2.0 + 10.0
        probes = [0.0, hi_probe]
    pts = [(p, expiry_payoff(legs, p, entry_cost, stock)) for p in probes]

    def _representative_price(target_val: float) -> float:
        """The most meaningful single price for a (possibly flat) extreme: a
        plateau's boundary strike rather than 0 or the far tail — but only when
        that boundary genuinely shares the extreme value (a flat plateau)."""
        ach = [p for p, v in pts if abs(v - target_val) < 0.01]
        if strikes and hi_probe in ach and any(abs(p - strikes[-1]) < 1e-9 for p in ach):
            return strikes[-1]
        if strikes and 0.0 in ach and any(abs(p - strikes[0]) < 1e-9 for p in ach):
            return strikes[0]
        strike_hits = [p for p in ach if p in strikes]
        if strike_hits:
            return strike_hits[0]
        non_tail = [p for p in ach if p != hi_probe]
        return non_tail[0] if non_tail else ach[0]

    hi_val = max(v for _, v in pts)
    lo_val = min(v for _, v in pts)
    return {
        "max_profit": None if unbounded_profit else round(hi_val, 2),
        "max_profit_price": None if unbounded_profit else round(_representative_price(hi_val), 2),
        "max_loss": None if unbounded_loss else round(lo_val, 2),
        "max_loss_price": None if unbounded_loss else round(_representative_price(lo_val), 2),
        "unbounded_profit": unbounded_profit,
        "unbounded_loss": unbounded_loss,
    }


def structure_breakevens(legs: list[dict], entry_cost: float,
                         stock: Optional[dict] = None) -> list:
    """EXACT breakevens (underlying prices where the expiry P&L crosses 0).

    The expiry payoff is piecewise-linear with kinks ONLY at the strikes, so we evaluate
    at the breakpoints (0, each strike, a far-high probe) and solve each linear SEGMENT
    for its zero analytically. This is exact — unlike scanning a coarse price grid, whose
    linear interpolation runs ACROSS a strike kink and mislocates a breakeven by up to
    half a grid step (the source of the asymmetric ~$0.26 breakeven error). Same leg
    format as `structure_payoff_extremes`.
    """
    strikes = sorted({float(lg["strike"]) for lg in legs if lg.get("strike")})
    if not strikes and not stock:
        return []
    hi = (strikes[-1] * 2.0 + 10.0) if strikes else (float((stock or {}).get("avg_cost") or 100.0) * 2.0 + 10.0)
    xs = [0.0] + strikes + [hi]
    pts = [(x, expiry_payoff(legs, x, entry_cost, stock)) for x in xs]
    bes: list[float] = []
    for (x1, v1), (x2, v2) in zip(pts, pts[1:]):
        if v1 == 0.0:                                   # breakeven sits exactly on a breakpoint
            bes.append(x1)
        elif (v1 < 0.0 < v2) or (v1 > 0.0 > v2):        # sign change → exact root of a LINEAR segment
            bes.append(x1 + (0.0 - v1) * (x2 - x1) / (v2 - v1))
    if pts[-1][1] == 0.0:
        bes.append(pts[-1][0])
    out: list = []
    for b in sorted(bes):
        if b > 0 and (not out or abs(b - out[-1]) > 1e-6):   # dedupe shared endpoints, keep positive
            out.append(round(b, 2))
    return out


# Rank of urgency so the overall verdict can pick the most pressing leg action.
_ACTION_URGENCY = {"ROLL": 3, "CLOSE": 2, "LET_EXPIRE": 1, "HOLD": 0}


def _structure_standing(
    underlying_price: Optional[float],
    breakevens: Optional[list],
    max_profit: Optional[float],
    max_loss: Optional[float],
) -> Optional[str]:
    """Where the underlying sits relative to the structure's profit zone at expiry.

    For a defined-risk structure the breakevens bound the profitable band; this
    says whether spot is inside it (on track) or outside (working against you).
    """
    if underlying_price is None or underlying_price <= 0 or not breakevens:
        return None
    bes = sorted(b for b in breakevens if b and b > 0)
    if not bes:
        return None
    if len(bes) >= 2:
        lo, hi = bes[0], bes[-1]
        if lo <= underlying_price <= hi:
            return f"spot ${underlying_price:,.2f} sits inside the profit band ${lo:,.2f}–${hi:,.2f}"
        side = "below" if underlying_price < lo else "above"
        edge = lo if underlying_price < lo else hi
        return f"spot ${underlying_price:,.2f} is {side} the ${edge:,.2f} breakeven — in the loss zone"
    be = bes[0]
    return f"spot ${underlying_price:,.2f} vs breakeven ${be:,.2f}"


def summarize_trade_actions(
    *,
    leg_actions: list[dict],
    hold_signal: str,
    pop: Optional[float],
    expected_value: Optional[float],
    unrealized_pnl: float,
    max_profit: Optional[float],
    max_loss: Optional[float],
    dte: int,
    breakevens: Optional[list] = None,
    underlying_price: Optional[float] = None,
    has_stock: bool = False,
    stock_pnl: Optional[float] = None,
) -> dict:
    """Judge the *whole structure's* outcome, then translate into one headline.

    `hold_signal` (STRONG_HOLD / HOLD / CLOSE / STRONG_CLOSE) is computed upstream
    from the structure's PoP / expected value / distance to max profit & loss, and
    remains the single source of truth for the *signal*. Here we frame it in terms
    of the total-trade payoff — risk vs reward, where spot sits in the profit band,
    how much of the outcome is already realized — and attach the concrete per-leg
    moves so the user knows what to actually do.

    Returns {action, headline, outcome, leg_notes: [...], reasons: [...]}.
    """
    label = {
        "STRONG_HOLD": "Hold",
        "HOLD": "Hold",
        "CLOSE": "Close",
        "STRONG_CLOSE": "Close now",
    }.get(hold_signal, "Review")

    # Concrete per-leg moves, most urgent first.
    ordered = sorted(
        leg_actions,
        key=lambda la: _ACTION_URGENCY.get(la.get("action", "HOLD"), 0),
        reverse=True,
    )
    leg_notes = [la["reason"] for la in ordered]

    rolls = [la for la in leg_actions if la.get("action") == "ROLL"]
    closes = [la for la in leg_actions if la.get("action") in ("CLOSE", "LET_EXPIRE")]

    # ── Whole-structure outcome sentence ──────────────────────────────────
    outcome_bits: list[str] = []
    if max_profit is not None and max_loss is not None and max_loss < 0:
        rr = abs(max_profit / max_loss) if max_loss else None
        outcome_bits.append(
            f"defined risk: makes up to ${max_profit:,.0f}, risks ${abs(max_loss):,.0f}"
            + (f" ({rr:.2f}:1)" if rr else "")
        )
    elif max_loss is not None and max_loss < 0:
        outcome_bits.append(f"risks up to ${abs(max_loss):,.0f}")
    standing = _structure_standing(underlying_price, breakevens, max_profit, max_loss)
    if standing:
        outcome_bits.append(standing)
    pnl_side = "up" if unrealized_pnl >= 0 else "down"
    outcome_bits.append(f"currently {pnl_side} ${abs(unrealized_pnl):,.0f}")
    if pop is not None:
        outcome_bits.append(f"{pop:.0f}% chance of profit")
    outcome = "Trade outcome — " + "; ".join(outcome_bits) + "."

    reasons: list[str] = []
    if pop is not None:
        reasons.append(f"Probability of profit {pop:.0f}%")
    if expected_value is not None:
        reasons.append(f"Expected value ${expected_value:,.0f}")
    if max_profit is not None and max_profit > 0 and unrealized_pnl >= max_profit * 0.8:
        reasons.append(f"{unrealized_pnl / max_profit * 100:.0f}% of max profit captured")
    if max_loss is not None and max_loss < 0 and unrealized_pnl <= max_loss * 0.8:
        reasons.append("Near max loss")

    # Build the one-line headline: signal, framed by the total-trade outcome.
    if hold_signal in ("CLOSE", "STRONG_CLOSE"):
        why = (standing if standing else
               (reasons[0].lower() if reasons else "risk/reward no longer favorable"))
        move = "unwind all legs together" if len(leg_actions) > 1 else "exit the position"
        headline = f"{label} the trade — {why}; {move}."
    elif rolls:
        headline = f"{label} the structure, but adjust: {rolls[0]['reason']}"
    elif closes and hold_signal.endswith("HOLD"):
        n = len(closes)
        headline = f"{label} the structure — act on {n} leg{'s' if n > 1 else ''}: {closes[0]['reason']}"
    elif standing:
        headline = f"{label} the structure — {standing}."
    else:
        headline = f"{label} — {reasons[0] if reasons else 'position on track'}."

    if has_stock and stock_pnl is not None:
        side = "up" if stock_pnl >= 0 else "down"
        reasons.append(f"Underlying stock leg {side} ${abs(stock_pnl):,.0f} — treat as the core, options are the overlay")

    return {
        "action": hold_signal,
        "headline": headline,
        "outcome": outcome,
        "leg_notes": leg_notes,
        "reasons": reasons,
    }


# ── Whole-trade EXIT timing — Strong Hold / Hold / Close / Strong Close ──

ExitSignal = Literal["STRONG_HOLD", "HOLD", "CLOSE", "STRONG_CLOSE"]


def exit_recommendation(
    *,
    hold_signal: str,
    pop: Optional[float],
    unrealized_pnl: float,
    max_profit: Optional[float],
    max_loss: Optional[float],
    dte: Optional[int],
    theta_per_day: float = 0.0,
) -> dict:
    """When is the best time to exit the WHOLE trade? Maps the hold/close signal
    into the 4-level exit vocabulary and layers on lifecycle *exit-timing* rules
    a screener misses — the take-half-early rule, gamma/pin risk into expiry, and
    profit already banked vs the theta still left to collect.

    Returns {signal, reasons, captured_pct}. signal ∈
      STRONG_HOLD | HOLD | CLOSE | STRONG_CLOSE.
    """
    signal: ExitSignal = {
        "STRONG_HOLD": "STRONG_HOLD", "HOLD": "HOLD",
        "CLOSE": "CLOSE", "STRONG_CLOSE": "STRONG_CLOSE",
    }.get(hold_signal, "HOLD")  # type: ignore[assignment]
    reasons: list[str] = []

    captured = (unrealized_pnl / max_profit) if (max_profit and max_profit > 0) else None

    # Take-profit management: the closer you are to max profit, the less theta is
    # left to collect and the more you're just holding gamma/pin risk for pennies.
    if captured is not None:
        if captured >= 0.85:
            signal = "STRONG_CLOSE"
            reasons.append(f"Captured {captured * 100:.0f}% of max profit — almost nothing left to earn; close to free capital and shed gamma/pin risk")
        elif captured >= 0.5 and dte and dte > 7:
            if signal in ("STRONG_HOLD", "HOLD"):
                signal = "CLOSE"
            reasons.append(f"Captured {captured * 100:.0f}% of max profit with {dte}d left — the take-half-early exit that lifts realized returns")

    # Cut losses when the structure is deep in the red.
    if max_loss is not None and max_loss < 0 and unrealized_pnl <= max_loss * 0.8:
        signal = "STRONG_CLOSE"
        reasons.append("Near max loss — cut it rather than hope for a reversal")

    # Gamma / pin / assignment risk explodes in the last days.
    if dte is not None and dte <= 2:
        if unrealized_pnl > 0 and signal in ("STRONG_HOLD", "HOLD"):
            signal = "CLOSE"
        reasons.append(f"{dte}d to expiry — gamma/pin/assignment risk elevated; realize rather than carry overnight")

    # Edge gone.
    if pop is not None and pop < 30 and signal in ("STRONG_HOLD", "HOLD"):
        signal = "CLOSE"
        reasons.append(f"Probability of profit down to {pop:.0f}% — the edge has eroded")

    # Still collecting: positive theta + plenty of runway + healthy PoP = let it work.
    if not reasons:
        if theta_per_day > 0 and (pop is None or pop >= 60):
            reasons.append("Positive theta still accruing with the edge intact — let it work")
        else:
            reasons.append({
                "STRONG_HOLD": "Thesis intact and edge working — hold",
                "HOLD": "On track — hold and monitor",
                "CLOSE": "Risk/reward no longer compelling — consider trimming or closing",
                "STRONG_CLOSE": "Exit the position",
            }.get(signal, "Monitor"))

    return {
        "signal": signal,
        "reasons": reasons,
        "captured_pct": round(captured * 100, 1) if captured is not None else None,
    }
