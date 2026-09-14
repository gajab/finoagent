import asyncio
import math
from datetime import datetime
import yfinance as yf
from .stock_service import safe_float

def _run_dual_direction_buffer_sync(
    ticker: str,
    amount: float,
    duration_days: int,
    downside_buffer_pct: float,
    upside_cap_pct: float,
    target_expiration: str | None = None,
    entry_cost_mode: str = "standard",
) -> dict:
    """
    Simulates a Dual Direction Buffer strategy using the 4-Layer construction.
    Layer 1: Reference Asset Exposure (Buy Deep ITM Call)
    Layer 2: Absolute Return (Buy 2 ATM Puts, Sell 2 Buffer Puts)
    Layer 3: Transition to Buffer (Bull Put Spread to match 15% drop transition)
    Layer 4: Upside Cap (Sell OTM Call)
    """
    ticker = ticker.upper()
    stock = yf.Ticker(ticker)

    # 1. Get current price of underlying
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

    # 2. Find the best option matching the duration
    try:
        expirations = list(stock.options)
    except Exception:
        return {"error": f"No options chain available for {ticker}"}

    if not expirations:
        return {"error": f"No options expirations found for {ticker}"}

    today = datetime.now().date()

    # 3. Target strikes (depend only on price + requested buffer/cap)
    downside_pct = downside_buffer_pct / 100.0
    upside_pct = upside_cap_pct / 100.0

    deep_itm_target = current_price * 0.01       # deepest available ITM call (delta ~1)
    atm_strike_target = current_price
    buf_strike_target = current_price * (1 - downside_pct)
    cap_strike_target = current_price * (1 + upside_pct)

    def _dte(exp_str: str) -> int:
        return (datetime.strptime(exp_str, "%Y-%m-%d").date() - today).days

    def _is_monthly(exp_str: str) -> bool:
        # Standard monthly options expire on the 3rd Friday of the month
        # (always day 15-21). These carry the full, liquid strike ladder the
        # 4-layer structure needs; weeklies are often too sparse.
        d = datetime.strptime(exp_str, "%Y-%m-%d").date()
        return d.weekday() == 4 and 15 <= d.day <= 21

    valid_exps = []
    for exp_str in expirations:
        try:
            d = _dte(exp_str)
            if d >= 1:
                valid_exps.append((exp_str, d))
        except Exception:
            pass
    if not valid_exps:
        return {"error": "Could not find a valid expiration date."}

    def _chain_supports_structure(calls_df, puts_df) -> tuple[bool, int]:
        """A usable expiration needs a real strike ladder: at least one ITM call
        strike (for the deep-ITM exposure leg), a put near spot, and two or more
        put strikes at/below the buffer (the buffer put plus a lower strike for
        the Layer-3 transition spread). Sparse weeklies fail this check — using
        them would otherwise yield nonsensical, self-cancelling legs (e.g. the
        exposure and cap calls landing on the same strike)."""
        if calls_df.empty or puts_df.empty:
            return False, 0
        cs = calls_df["strike"]
        ps = puts_df["strike"]
        has_itm_call = bool((cs < current_price).any())
        near_spot = bool((ps - current_price).abs().min() <= current_price * 0.06)
        puts_below_buffer = int((ps <= buf_strike_target * 1.02).sum())
        ok = has_itm_call and near_spot and puts_below_buffer >= 2
        richness = int((cs < current_price).sum()) + len(ps)
        return ok, richness

    # 2. Choose the expiration. Honour an explicit request; otherwise the Dual
    # Direction Buffer always trades standard monthly expirations — pick the
    # monthly nearest the requested duration (whose ladder can support the
    # structure), falling back to the richest chain if none can.
    selection_warning = None
    closest_exp = actual_dte = calls = puts = None

    if target_expiration and any(e == target_expiration for e, _ in valid_exps):
        closest_exp = target_expiration
        actual_dte = _dte(target_expiration)
        chain = stock.option_chain(closest_exp)
        calls, puts = chain.calls, chain.puts
        ok, _ = _chain_supports_structure(calls, puts)
        if not ok:
            selection_warning = (
                f"Expiration {closest_exp} has a sparse strike ladder — the recommended legs "
                f"may not match the requested buffer/cap. Consider a monthly expiration."
            )
    else:
        # Restrict to monthly expirations; fall back to all only if a ticker
        # lists no monthlies at all.
        monthly_exps = [t for t in valid_exps if _is_monthly(t[0])]
        pool = monthly_exps if monthly_exps else valid_exps
        by_closeness = sorted(pool, key=lambda t: abs(t[1] - duration_days))
        richest = None  # (exp, dte, calls, puts, richness)
        for exp_str, d in by_closeness[:6]:      # cap network fetches
            try:
                chain = stock.option_chain(exp_str)
            except Exception:
                continue
            ok, richness = _chain_supports_structure(chain.calls, chain.puts)
            if richest is None or richness > richest[4]:
                richest = (exp_str, d, chain.calls, chain.puts, richness)
            if ok:
                closest_exp, actual_dte, calls, puts = exp_str, d, chain.calls, chain.puts
                break
        if closest_exp is None:
            if richest is None:
                return {"error": "No expiration with a usable options chain was found."}
            closest_exp, actual_dte, calls, puts = richest[0], richest[1], richest[2], richest[3]
            selection_warning = (
                "No nearby expiration has a full strike ladder for the requested buffer/cap; "
                "using the richest available chain, so the legs are approximate."
            )

    if not closest_exp or actual_dte < 1:
        return {"error": "Could not find a valid expiration date."}

    if calls.empty or puts.empty:
        return {"error": f"Incomplete options chain for expiration {closest_exp}"}

    def find_closest_option(df, target_strike):
        if df.empty:
            return None
        df_copy = df.copy()
        df_copy['diff'] = abs(df_copy['strike'] - target_strike)
        closest = df_copy.sort_values('diff').iloc[0]
        ask = safe_float(closest.get("ask", 0.0))
        bid = safe_float(closest.get("bid", 0.0))
        mid = round((bid + ask) / 2, 2) if bid > 0 and ask > 0 else safe_float(closest.get("lastPrice", 0.0))
        res = closest.to_dict()
        res["mid"] = mid
        res["bid"] = round(bid, 2)
        res["ask"] = round(ask, 2)
        return res

    # Layer 1: Exposure (Deep ITM Call)
    l1_call = find_closest_option(calls, deep_itm_target)
    
    # Layer 2: Absolute Return
    atm_put = find_closest_option(puts, atm_strike_target)
    buf_put = find_closest_option(puts, buf_strike_target)
    
    if not buf_put:
        return {"error": "Could not find buffer put"}

    # Find the next strike below buf_put for Layer 3 (Transition)
    lower_puts = puts[puts['strike'] < buf_put['strike']]
    buf_low_put = None
    if not lower_puts.empty:
        buf_low_put = lower_puts.sort_values('strike', ascending=False).iloc[0]
        ask = safe_float(buf_low_put.get("ask", 0.0))
        bid = safe_float(buf_low_put.get("bid", 0.0))
        mid = round((bid + ask) / 2, 2) if bid > 0 and ask > 0 else safe_float(buf_low_put.get("lastPrice", 0.0))
        buf_low_put = buf_low_put.to_dict()
        buf_low_put["mid"] = mid
    else:
        # Fallback if no lower strike exists
        buf_low_put = buf_put.copy()

    # Layer 4: Upside Cap
    cap_call = find_closest_option(calls, cap_strike_target)

    all_options = [l1_call, atm_put, buf_put, buf_low_put, cap_call]
    if any(leg is None for leg in all_options):
        return {"error": "Could not find all required option legs to construct the strategy."}

    # Guard: the Layer-1 exposure call must be genuinely in-the-money (delta ~1)
    # and struck below the Layer-4 cap call. If the chain is too sparse to offer
    # a deep-ITM strike, these two legs collapse onto the same strike and cancel
    # out, so refuse to emit a broken structure rather than mislead the user.
    if l1_call["strike"] >= current_price or l1_call["strike"] == cap_call["strike"]:
        return {"error": (
            f"{ticker}'s {closest_exp} chain has no in-the-money call strike below the current "
            f"price (${round(current_price, 2)}), so a clean 1:1 exposure leg can't be built. "
            f"Try a monthly expiration with a fuller strike ladder."
        )}

    # Calculate Transition Multiplier (X)
    # The ETF seeks a 1:1 downside profile below the buffer threshold.
    # At spot = 0, Layer 1 gives -$1.
    # Layer 2 (Buy 2 ATM, Sell 2 Buffer) maxes out and pays 2 * (atm - buffer), giving delta 0 below buffer.
    # We want Layer 3 to offset the cash gain of Layer 2 when dropping below the buffer, so that
    # the total portfolio return matches the 1:1 spot drop exactly.
    # To force the return line to exactly `downside_pct` at `buffer_strike`, we scale Layer 3.
    # If delta below buffer is already +1 (from L1), we just need to burn the cash credit.
    # Target L3 Max Loss = 2 * (atm_strike - buf_strike) 
    l2_max_gain = 2 * (atm_put['strike'] - buf_put['strike'])
    
    # Actually, Innovator says: "1x Inverse Return: between 0% and 15%".
    # At -15%, Spot is -15%. Layer 2 pays +30%. Total = +15%.
    # Below -15%, Spot loses 1:1. Layer 2 is capped (+30%). 
    # Without Layer 3, at -25% spot, the ETF would be -25% + 30% = +5%.
    # We want the ETF to be -10% at -25%. (Since 15% buffer absorbs the first 15%).
    # So we need to lose an extra 15% immediately below the buffer to zero out the L2 gain.
    # The spread width is `strike_diff`. We need a max loss of `l2_max_gain` - `buffer_loss`.
    strike_diff = buf_put['strike'] - buf_low_put['strike']
    if strike_diff > 0:
        # Buffer absorption is precisely the nominal buffer percentage. Round the
        # transition ratio to a whole number so every derived leg quantity below
        # stays an executable integer (fractional contracts can't be traded).
        buffer_value = current_price * downside_pct
        target_l3_max_loss = l2_max_gain - buffer_value
        transition_contracts = float(max(0, round(target_l3_max_loss / strike_diff)))
    else:
        transition_contracts = 0.0

    # ── Entry-cost optimization (user-selectable) ──────────────────────────
    # Adjust the recommended legs to the user's cost preference before pricing.
    #   self_financing : tune the cap so the OPTIONS overlay (puts + short call,
    #                    excl. the exposure call) nets to a credit/zero — the
    #                    protection pays for itself; you still fund the exposure.
    #   non_negative   : tune the cap so the structure isn't underwater at 0%
    #                    (flat at expiry) — intrinsic value at spot ≥ net cost.
    #   cheapest       : swap the deep-ITM exposure call for a near-ATM call so
    #                    the total debit is far smaller (gives up true 1:1 delta).
    #   standard       : the canonical 4-layer build (default, unchanged).
    entry_cost_warning = None

    def _overlay(cap):
        return (2 * atm_put["mid"] - 2 * buf_put["mid"]
                - transition_contracts * buf_put["mid"] + transition_contracts * buf_low_put["mid"]
                - cap["mid"])

    def _intrinsic_at_spot(l1, cap):
        v1 = max(0, current_price - l1["strike"])
        v2 = 2 * max(0, atm_put["strike"] - current_price) - 2 * max(0, buf_put["strike"] - current_price)
        v3 = (-transition_contracts * max(0, buf_put["strike"] - current_price)
              + transition_contracts * max(0, buf_low_put["strike"] - current_price))
        v4 = -max(0, current_price - cap["strike"])
        return v1 + v2 + v3 + v4

    def _cap_candidates():
        """Available call strikes above spot, richest (lowest) → widest (highest)."""
        above = calls[calls["strike"] > current_price].sort_values("strike")
        out = []
        for strike in above["strike"].tolist():
            c = find_closest_option(calls, strike)
            if c:
                out.append(c)
        return out

    if entry_cost_mode == "cheapest":
        # Cheapest exposure: a near-ATM long call instead of the deep-ITM one.
        atm_call = find_closest_option(calls, current_price)
        if atm_call and atm_call["strike"] < cap_call["strike"]:
            l1_call = atm_call
            entry_cost_warning = ("Cheapest mode: exposure leg is a near-ATM call — much lower entry cost, "
                                  "but delta < 1 so upside participation is not 1:1.")
    elif entry_cost_mode in ("self_financing", "non_negative"):
        cands = _cap_candidates()
        # Highest cap strike (most retained upside) that meets the cost target.
        chosen = None
        for cap in sorted(cands, key=lambda c: c["strike"], reverse=True):
            ok = (_overlay(cap) <= 0) if entry_cost_mode == "self_financing" \
                else (_intrinsic_at_spot(l1_call, cap) >= (l1_call["mid"] + _overlay(cap)))
            if ok:
                chosen = cap
                break
        if chosen is not None:
            cap_call = chosen
        elif cands:
            cap_call = min(cands, key=lambda c: c["strike"])   # richest available, best effort
            entry_cost_warning = ("Could not reach the requested entry-cost target even at the richest cap — "
                                  "used the lowest available cap strike; some debit remains.")

    # Determine base contracts for the investment amount
    # Total cash outlay per share = Layer 1 call + net options premium
    cost_per_unit = (
        l1_call["mid"]
        + 2 * atm_put["mid"]
        - 2 * buf_put["mid"]
        - transition_contracts * buf_put["mid"]
        + transition_contracts * buf_low_put["mid"]
        - cap_call["mid"]
    )

    # Simulate always returns whole, executable contracts (fractions can't be
    # traded) — round to the nearest whole base contract, minimum 1.
    affordable_shares = amount / cost_per_unit if cost_per_unit > 0 else 0
    base_contracts = float(max(1, round(affordable_shares / 100.0)))

    # Calculate actual options cost metrics for the user to execute the synthetic trade
    net_options_premium = (
        2 * atm_put["mid"] 
        - 2 * buf_put["mid"] 
        - transition_contracts * buf_put["mid"]
        + transition_contracts * buf_low_put["mid"] 
        - cap_call["mid"]
    )
    actual_trade_debit_per_share = l1_call["mid"] + net_options_premium
    actual_structure_cost = base_contracts * actual_trade_debit_per_share * 100

    # Legs for UI display (Innovator 4-Layer Spec)
    legs = [
        {
            "layer": "Layer 1: Reference Asset Exposure",
            "action": "Buy", "qty": round(1 * base_contracts, 2), "type": "Call",
            "strike": round(l1_call['strike'], 2), "midPrice": round(l1_call['mid'], 2),
            "bid": l1_call.get('bid', 0.0), "ask": l1_call.get('ask', 0.0),
            "purpose": "Provides synthetic 1:1 long exposure to the underlying asset."
        },
        {
            "layer": "Layer 2: Absolute Return",
            "action": "Buy", "qty": round(2 * base_contracts, 2), "type": "Put",
            "strike": round(atm_put['strike'], 2), "midPrice": round(atm_put['mid'], 2),
            "bid": atm_put.get('bid', 0.0), "ask": atm_put.get('ask', 0.0),
            "purpose": "Seeks to produce positive returns equal to the absolute value of decline."
        },
        {
            "layer": "Layer 2: Absolute Return",
            "action": "Sell", "qty": round(2 * base_contracts, 2), "type": "Put",
            "strike": round(buf_put['strike'], 2), "midPrice": round(buf_put['mid'], 2),
            "bid": buf_put.get('bid', 0.0), "ask": buf_put.get('ask', 0.0),
            "purpose": "Caps the absolute return at the buffer boundary."
        },
        {
            "layer": "Layer 3: Transition to Buffer",
            "action": "Sell", "qty": round(transition_contracts * base_contracts, 2), "type": "Put",
            "strike": round(buf_put['strike'], 2), "midPrice": round(buf_put['mid'], 2),
            "bid": buf_put.get('bid', 0.0), "ask": buf_put.get('ask', 0.0),
            "purpose": "Funds the downside protection & offsets absolute return when loss exceeds buffer."
        },
        {
            "layer": "Layer 3: Transition to Buffer",
            "action": "Buy", "qty": round(transition_contracts * base_contracts, 2), "type": "Put",
            "strike": round(buf_low_put['strike'], 2), "midPrice": round(buf_low_put['mid'], 2),
            "bid": buf_low_put.get('bid', 0.0), "ask": buf_low_put.get('ask', 0.0),
            "purpose": "Re-establishes a 1:1 downside profile below the transition zone."
        },
        {
            "layer": "Layer 4: Upside Cap",
            "action": "Sell", "qty": round(1 * base_contracts, 2), "type": "Call",
            "strike": round(cap_call['strike'], 2), "midPrice": round(cap_call['mid'], 2),
            "bid": cap_call.get('bid', 0.0), "ask": cap_call.get('ask', 0.0),
            "purpose": "Premium covers downside buffer, limiting max potential gain."
        }
    ]

    total_structure_cost = base_contracts * cost_per_unit * 100

    scenarios = []
    # Fine-grained 1% increments for smooth visualization
    price_changes = [x / 100 for x in range(-30, 31)]

    actual_upside_cap_pct = (cap_call['strike'] - current_price) / current_price
    actual_downside_buffer_pct = (current_price - buf_put['strike']) / current_price

    # Pre-compute the at-par basis (intrinsic value at 0% move) once
    v0_l1 = max(0, current_price - l1_call['strike'])
    v0_l2 = 2 * max(0, atm_put['strike'] - current_price) - 2 * max(0, buf_put['strike'] - current_price)
    v0_l3 = -transition_contracts * max(0, buf_put['strike'] - current_price) + transition_contracts * max(0, buf_low_put['strike'] - current_price)
    v0_l4 = -max(0, current_price - cap_call['strike'])
    gross_value_at_zero = v0_l1 + v0_l2 + v0_l3 + v0_l4
    adjusted_basis_per_share = gross_value_at_zero
    adjusted_structure_cost = base_contracts * adjusted_basis_per_share * 100

    def _calc_roi(change: float) -> dict:
        sim_price = current_price * (1 + change)
        v_l1 = max(0, sim_price - l1_call['strike'])
        v_l2 = 2 * max(0, atm_put['strike'] - sim_price) - 2 * max(0, buf_put['strike'] - sim_price)
        v_l3 = -transition_contracts * max(0, buf_put['strike'] - sim_price) + transition_contracts * max(0, buf_low_put['strike'] - sim_price)
        v_l4 = -max(0, sim_price - cap_call['strike'])
        gross_value_per_share = v_l1 + v_l2 + v_l3 + v_l4
        total_payout = base_contracts * gross_value_per_share * 100
        roi = ((total_payout - adjusted_structure_cost) / adjusted_structure_cost) * 100 if adjusted_structure_cost > 0 else 0
        return {
            "underlyingChangePct": round(change * 100, 2),
            "simulatedPrice": round(sim_price, 2),
            "totalPayout": round(total_payout, 2),
            "roi": round(roi, 2)
        }

    for change in price_changes:
        scenarios.append(_calc_roi(change))

    # --- Crossover detection: find exact % where ROI flips sign ---
    crossovers = []
    for i in range(1, len(scenarios)):
        prev_roi = scenarios[i - 1]["roi"]
        curr_roi = scenarios[i]["roi"]
        if (prev_roi > 0 and curr_roi < 0) or (prev_roi < 0 and curr_roi > 0):
            # Linear interpolation to find exact crossover %
            prev_pct = scenarios[i - 1]["underlyingChangePct"]
            curr_pct = scenarios[i]["underlyingChangePct"]
            if curr_roi != prev_roi:
                exact_pct = prev_pct + (curr_pct - prev_pct) * (-prev_roi) / (curr_roi - prev_roi)
            else:
                exact_pct = (prev_pct + curr_pct) / 2
            crossovers.append({
                "breakeven_pct": round(exact_pct, 2),
                "from_roi": round(prev_roi, 2),
                "to_roi": round(curr_roi, 2),
                "direction": "profit_to_loss" if prev_roi > 0 else "loss_to_profit"
            })

    # --- Max profit / max loss from fine scan ---
    max_profit_scenario = max(scenarios, key=lambda s: s["roi"])
    max_loss_scenario = min(scenarios, key=lambda s: s["roi"])

    # --- Buffer/Cap verification warnings ---
    buffer_cap_warnings = []
    if selection_warning:
        buffer_cap_warnings.append(selection_warning)
    if entry_cost_warning:
        buffer_cap_warnings.append(entry_cost_warning)
    # Whole-contract rounding can push the real outlay past the requested amount.
    if actual_structure_cost > amount * 1.1:
        buffer_cap_warnings.append(
            f"Smallest executable size is {int(base_contracts)} contract(s) ≈ "
            f"${round(actual_structure_cost):,}, above your ${round(amount):,} budget — "
            f"options trade in whole contracts, so this is the minimum for this underlying/expiration."
        )
    buffer_diff = abs(actual_downside_buffer_pct * 100 - downside_buffer_pct)
    cap_diff = abs(actual_upside_cap_pct * 100 - upside_cap_pct)
    if buffer_diff > 1.0:
        buffer_cap_warnings.append(
            f"Requested {downside_buffer_pct}% downside buffer but actual is {round(actual_downside_buffer_pct * 100, 1)}% "
            f"(diff: {round(buffer_diff, 1)}%) due to available strike prices."
        )
    if cap_diff > 1.0:
        buffer_cap_warnings.append(
            f"Requested {upside_cap_pct}% upside cap but actual is {round(actual_upside_cap_pct * 100, 1)}% "
            f"(diff: {round(cap_diff, 1)}%) due to available strike prices."
        )

    return {
        "success": True,
        "ticker": ticker,
        "currentPrice": round(current_price, 2),
        "investmentAmount": amount,
        "durationDays": duration_days,
        "actualDte": actual_dte,
        "expirationDate": closest_exp,
        "parameters": {
            "requestedDownsideBuffer": downside_buffer_pct,
            "requestedUpsideCap": upside_cap_pct,
            "actualDownsideBuffer": round(actual_downside_buffer_pct * 100, 2),
            "actualUpsideCap": round(actual_upside_cap_pct * 100, 2),
        },
        "structureCost": round(total_structure_cost, 2), # Legacy spot simulation cost
        "actualStructureCost": round(actual_structure_cost, 2),
        "netOptionsPremium": round(net_options_premium, 2),
        "legs": legs,
        "scenarios": scenarios,
        "crossovers": crossovers,
        "maxProfit": max_profit_scenario,
        "maxLoss": max_loss_scenario,
        "bufferCapWarnings": buffer_cap_warnings,
        "available_expirations": expirations[:15],
    }

def _consolidate_legs(legs: list[dict]) -> list[dict]:
    """Collapse legs that share action+type+strike into a single executable line
    (e.g. the Layer-2 and Layer-3 buffer short puts sit on the same strike) and
    drop anything that rounds to zero — the fewest orders the user actually trades."""
    merged: dict = {}
    order: list = []
    for leg in legs:
        if (leg.get("qty") or 0) <= 0:
            continue
        key = (leg.get("action"), leg.get("type"), round(float(leg.get("strike") or 0), 2))
        if key in merged:
            merged[key]["qty"] = round(merged[key]["qty"] + leg["qty"], 2)
            # Keep it readable when two layers fold into one line.
            if leg.get("layer") and leg["layer"] not in merged[key].get("layer", ""):
                merged[key]["layer"] = "Buffer short (Layers 2 & 3)"
        else:
            merged[key] = dict(leg)
            order.append(key)
    return [merged[k] for k in order]


async def run_dual_direction_buffer(
    ticker: str,
    amount: float,
    duration_days: int,
    downside_buffer_pct: float,
    upside_cap_pct: float,
    target_expiration: str | None = None,
    entry_cost_mode: str = "standard",
) -> dict:
    """Async wrapper for run_dual_direction_buffer"""
    result = await asyncio.to_thread(
        _run_dual_direction_buffer_sync,
        ticker,
        amount,
        duration_days,
        downside_buffer_pct,
        upside_cap_pct,
        target_expiration,
        entry_cost_mode,
    )
    # Merge same-strike legs + drop zero-qty so the recommendation is the fewest
    # executable lines with no self-cancelling phantom legs.
    if result.get("success") and result.get("legs"):
        result["legs"] = _consolidate_legs(result["legs"])
    return result


async def run_dual_direction_buffer_ibkr(
    ticker: str,
    amount: float,
    duration_days: int,
    downside_buffer_pct: float,
    upside_cap_pct: float,
    target_expiration: str | None = None,
    entry_cost_mode: str = "standard",
) -> dict:
    """IBKR-optimized version: uses yfinance for strike/expiry identification,
    rounds all contract quantities to integers (math.ceil) for IBKR compatibility,
    and recalculates strategy costs with the rounded quantities.
    """
    # Step 1: Run the yfinance calculation to identify optimal strikes and pricing
    result = await asyncio.to_thread(
        _run_dual_direction_buffer_sync,
        ticker,
        amount,
        duration_days,
        downside_buffer_pct,
        upside_cap_pct,
        target_expiration,
        entry_cost_mode,
    )

    if not result.get("success"):
        return result

    legs = result["legs"]

    # Step 2: Round quantities to integers using ceil.
    # L1 and L4 are 1x base; L2 pairs are 2x base; L3 pairs are transition x base.
    # Matching leg pairs (L2 buy/sell, L3 sell/buy) must share the same integer qty
    # to maintain structural balance.
    l1_qty = max(1, math.ceil(legs[0]["qty"]))   # Layer 1 Buy Call
    l2_qty = max(1, math.ceil(legs[1]["qty"]))   # Layer 2 Buy Put (2x)
    # L2 sell (legs[2]) must match L2 buy quantity
    # Layer 3 is optional: if the transition spread rounded to nothing, keep it
    # at zero rather than forcing a phantom 1-lot that just cancels itself.
    l3_qty = math.ceil(legs[3]["qty"]) if legs[3]["qty"] > 1e-9 else 0
    # L3 buy (legs[4]) must match L3 sell quantity
    l4_qty = max(1, math.ceil(legs[5]["qty"]))   # Layer 4 Sell Call

    legs[0]["qty"] = l1_qty
    legs[1]["qty"] = l2_qty
    legs[2]["qty"] = l2_qty   # must match L2 buy
    legs[3]["qty"] = l3_qty
    legs[4]["qty"] = l3_qty   # must match L3 sell
    legs[5]["qty"] = l4_qty

    # Merge same-strike legs + drop zero-qty → fewest executable lines.
    legs = _consolidate_legs(legs)

    # Step 3: Recalculate actual structure cost with integer quantities
    # Net cost = sum of (signed qty * midPrice * 100)
    # Buy legs add cost, Sell legs reduce cost
    _sign = lambda action: 1 if action == "Buy" else -1
    actual_structure_cost_ibkr = sum(
        _sign(leg["action"]) * leg["qty"] * leg["midPrice"] * 100
        for leg in legs
    )

    # Net options premium per share (excludes L1 deep ITM call)
    net_options_premium_ibkr = sum(
        _sign(leg["action"]) * leg["midPrice"]
        for leg in legs
        if leg.get("layer") != "Layer 1: Reference Asset Exposure"
    )

    result["legs"] = legs
    result["actualStructureCost"] = round(actual_structure_cost_ibkr, 2)
    result["netOptionsPremium"] = round(net_options_premium_ibkr, 2)
    result["ibkr_mode"] = True
    result["quantities_rounded"] = True

    return result
