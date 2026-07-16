import asyncio
import math
from datetime import datetime, timedelta
import yfinance as yf
from .stock_service import safe_float

# Supported structure presets. Each is just a different set of option legs
# layered on top of the same zero-coupon-bond floor.
STRUCTURE_PPN = "ppn"                    # long call only (classic principal-protected note)
STRUCTURE_CAPPED = "capped"             # long call + short higher call (call spread → leveraged, capped)
STRUCTURE_YIELD = "yield_enhanced"      # long call + short OTM put (premium funds a fatter payoff, soft floor)

# Leverage guard: never let the financing trick push notional past this multiple
# of principal, no matter how cheap the net premium gets.
_MAX_PARTICIPATION = 3.0


def _mid_of(row: dict) -> float:
    """Mid price of an option row, falling back to last trade when no two-sided quote."""
    ask = safe_float(row.get("ask", 0.0))
    bid = safe_float(row.get("bid", 0.0))
    if bid > 0 and ask > 0:
        return round((bid + ask) / 2, 2)
    return safe_float(row.get("lastPrice", 0.0))


def _pick_option(df, target_strike: float, direction: str) -> dict | None:
    """
    Pick the nearest tradeable option to `target_strike`.
      direction='ge' -> nearest strike >= target (used for long ATM call, short cap call)
      direction='le' -> nearest strike <= target (used for short downside put)
    Returns the row as a dict with an added 'mid', or None if nothing has pricing.
    """
    if direction == "ge":
        cand = df[df["strike"] >= target_strike].sort_values("strike")
    else:
        cand = df[df["strike"] <= target_strike].sort_values("strike", ascending=False)

    for _, row in cand.iterrows():
        mid = _mid_of(row.to_dict())
        if mid > 0:
            d = row.to_dict()
            d["mid"] = mid
            return d
    return None


def _leg(side: str, opt_type: str, row: dict, qty: float) -> dict:
    """Serialise one option leg for the API response."""
    return {
        "side": side,                       # "Long" | "Short"
        "type": opt_type,                   # "Call" | "Put"
        "strike": round(safe_float(row["strike"]), 2),
        "midPrice": round(row["mid"], 2),
        "impliedVolatility": round(safe_float(row.get("impliedVolatility", 0)) * 100, 2),
        "contracts": round(qty, 4),
    }


def _run_structured_trade_sync(
    ticker: str,
    amount: float,
    duration_days: int,
    user_interest_rate: float = None,
    structure: str = STRUCTURE_PPN,
    cap_pct: float = 15.0,
    put_buffer_pct: float = 20.0,
) -> dict:
    """
    Simulates a structured note. The principal is split into:
      1. A zero-coupon bond yielding `interest_rate` to return principal at maturity.
      2. An options budget spent on a leg package whose shape depends on `structure`:
         - ppn:            long ATM call (full upside, full floor)
         - capped:         long ATM call + short call `cap_pct` above spot (leveraged, capped)
         - yield_enhanced: long ATM call + short put `put_buffer_pct` below spot
                           (sold-put premium buys more upside; floor goes soft below the put)
    All legs share one contract count `qty`, sized so the net premium spends the budget.
    """
    ticker = ticker.upper()
    if structure not in (STRUCTURE_PPN, STRUCTURE_CAPPED, STRUCTURE_YIELD):
        structure = STRUCTURE_PPN
    stock = yf.Ticker(ticker)

    # 1. Determine Risk-Free Rate
    interest_rate = 0.04  # Default 4.0% based on feedback
    rate_source = "Default (4.0%)"
    if user_interest_rate is not None:
        interest_rate = user_interest_rate / 100.0
        rate_source = f"User Input ({user_interest_rate}%)"
    else:
        # Try to dynamically fetch ^IRX (13-week Treasury Bill expected yield)
        try:
            irx_info = yf.Ticker("^IRX").info
            price = irx_info.get("regularMarketPrice") or irx_info.get("previousClose")
            if price and price > 0:
                interest_rate = price / 100.0
                rate_source = f"^IRX ({price}%)"
        except Exception:
            pass

    # 2. Get current price of underlying
    info = stock.info or {}
    current_price = info.get("currentPrice") or info.get("regularMarketPrice") or 0.0
    if not current_price:
        try:
            hist = stock.history(period="1d")
            if not hist.empty:
                current_price = hist["Close"].iloc[-1]
        except Exception:
            pass

    if not current_price:
        return {"error": f"Could not fetch current price for {ticker}"}

    # 3. Find the best option matching the duration
    try:
        expirations = list(stock.options)
    except Exception:
        return {"error": f"No options chain available for {ticker}"}

    if not expirations:
        return {"error": f"No options expirations found for {ticker}"}

    today = datetime.now().date()
    target_date = today + timedelta(days=duration_days)

    closest_exp = None
    min_diff = 99999
    actual_dte = 0

    for exp_str in expirations:
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            diff = abs((exp_date - target_date).days)
            if diff < min_diff:
                min_diff = diff
                closest_exp = exp_str
                actual_dte = (exp_date - today).days
        except Exception:
            pass

    if not closest_exp or actual_dte < 1:
        return {"error": "Could not find a valid expiration date."}

    actual_T = actual_dte / 365.0

    # 4. Zero-coupon bond floor sized to this exact expiration → options budget is the rest
    bond_allocation = amount * math.exp(-interest_rate * actual_T)
    options_budget = amount - bond_allocation

    chain = stock.option_chain(closest_exp)
    calls = chain.calls
    puts = chain.puts

    # 5. Long ATM call — the upside engine shared by every structure
    long_call = _pick_option(calls, current_price, "ge")
    if not long_call:
        # Fallback to nearest ITM call
        long_call = _pick_option(calls.sort_values("strike", ascending=False), 0, "ge")
    if not long_call:
        return {"error": "No valid call options with pricing found."}

    # 6. Assemble the leg package and the net premium paid per contract (per share).
    #    Sign convention: long legs cost premium (+), short legs collect it (−).
    short_call = None
    short_put = None
    net_premium_share = long_call["mid"]
    cap_strike = None
    barrier_strike = None

    if structure == STRUCTURE_CAPPED:
        cap_strike = current_price * (1 + cap_pct / 100.0)
        short_call = _pick_option(calls, cap_strike, "ge")
        if short_call:
            cap_strike = safe_float(short_call["strike"])
            net_premium_share -= short_call["mid"]

    elif structure == STRUCTURE_YIELD:
        barrier_strike = current_price * (1 - put_buffer_pct / 100.0)
        short_put = _pick_option(puts, barrier_strike, "le")
        if short_put:
            barrier_strike = safe_float(short_put["strike"])
            net_premium_share -= short_put["mid"]

    # Guard the financing: keep net premium positive and cap leverage at _MAX_PARTICIPATION.
    min_net_share = long_call["mid"] / _MAX_PARTICIPATION
    if net_premium_share < min_net_share:
        net_premium_share = min_net_share

    net_premium_contract = net_premium_share * 100  # cost per contract
    qty = options_budget / net_premium_contract if net_premium_contract > 0 else 0.0

    # Participation: notional controlled by the long call vs. principal
    long_strike = safe_float(long_call["strike"])
    notional_controlled = qty * 100 * current_price
    participation_rate = (notional_controlled / amount) * 100

    # 7. Build the serialised legs
    legs = [_leg("Long", "Call", long_call, qty)]
    if short_call is not None:
        legs.append(_leg("Short", "Call", short_call, qty))
    if short_put is not None:
        legs.append(_leg("Short", "Put", short_put, qty))

    def _package_payoff(sim_price: float) -> float:
        """Intrinsic payoff of all legs at expiry (dollars), excluding the bond."""
        payoff = 0.0
        payoff += max(0.0, sim_price - long_strike) * 100 * qty
        if short_call is not None:
            payoff -= max(0.0, sim_price - safe_float(short_call["strike"])) * 100 * qty
        if short_put is not None:
            payoff -= max(0.0, safe_float(short_put["strike"]) - sim_price) * 100 * qty
        return payoff

    # 8. Scenarios — wide enough to show the soft-floor cliff and the cap shelf
    price_changes = [-0.30, -0.20, -0.10, 0.0, 0.10, 0.20, 0.30, 0.40]
    scenarios = []
    for change in price_changes:
        sim_price = current_price * (1 + change)
        total_payout = _package_payoff(sim_price) + amount  # bond matures back to principal
        roi = ((total_payout - amount) / amount) * 100
        scenarios.append({
            "underlyingChangePct": round(change * 100, 1),
            "simulatedPrice": round(sim_price, 2),
            "totalPayout": round(total_payout, 2),
            "roi": round(roi, 2),
        })

    rois = [s["roi"] for s in scenarios]

    # 9. Headline metrics that make each structure legible
    cap_roi = None
    if structure == STRUCTURE_CAPPED and short_call is not None:
        max_pkg = max(0.0, safe_float(short_call["strike"]) - long_strike) * 100 * qty
        cap_roi = round((max_pkg / amount) * 100, 2)

    barrier_price = round(barrier_strike, 2) if (structure == STRUCTURE_YIELD and short_put is not None) else None
    buffer_pct_out = round(put_buffer_pct, 1) if barrier_price is not None else None

    metrics = {
        "structure": structure,
        "floorProtected": structure != STRUCTURE_YIELD,   # ppn/capped keep a hard 0% floor
        "capPct": cap_roi,                                 # max ROI for capped, else null
        "bufferPct": buffer_pct_out,                       # how far it can fall before the floor softens
        "barrierPrice": barrier_price,                     # underlying price where protection breaks
        "maxGainPct": round(max(rois), 2),
        "maxLossPct": round(min(rois), 2),
    }

    return {
        "success": True,
        "ticker": ticker,
        "structure": structure,
        "currentPrice": round(current_price, 2),
        "investmentAmount": amount,
        "durationDays": duration_days,
        "actualDte": actual_dte,
        "expirationDate": closest_exp,
        "interestRate": round(interest_rate * 100, 2),
        "rateSource": rate_source,
        "allocations": {
            "bond": round(bond_allocation, 2),
            "options": round(options_budget, 2),
        },
        # Kept for backward compatibility — the primary long call leg
        "optionParameters": {
            "type": "Call",
            "strike": round(long_strike, 2),
            "midPrice": round(long_call["mid"], 2),
            "impliedVolatility": round(safe_float(long_call.get("impliedVolatility", 0)) * 100, 2),
            "theoreticalContracts": round(qty, 4),
        },
        "legs": legs,
        "participationRate": round(participation_rate, 2),
        "metrics": metrics,
        "scenarios": scenarios,
    }


async def run_structured_trade(
    ticker: str,
    amount: float,
    duration_days: int,
    user_interest_rate: float = None,
    structure: str = STRUCTURE_PPN,
    cap_pct: float = 15.0,
    put_buffer_pct: float = 20.0,
) -> dict:
    """Async wrapper for _run_structured_trade_sync."""
    return await asyncio.to_thread(
        _run_structured_trade_sync,
        ticker,
        amount,
        duration_days,
        user_interest_rate,
        structure,
        cap_pct,
        put_buffer_pct,
    )
