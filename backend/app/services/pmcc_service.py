import asyncio
import math
from datetime import datetime, timedelta
import yfinance as yf
from .stock_service import safe_float

def _run_pmcc_pmcp_sync(
    ticker: str,
    amount: float,
    is_call: bool,
    leaps_duration_days: int,
    short_duration_days: int
) -> dict:
    """
    Simulates a Poor Man's Covered Call or Put.
    For PMCC: Buy deep ITM LEAPS Call, Sell OTM Near-term Call.
    For PMCP: Buy deep ITM LEAPS Put, Sell OTM Near-term Put.
    """
    ticker = ticker.upper()
    stock = yf.Ticker(ticker)

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

    try:
        expirations = list(stock.options)
    except Exception:
        return {"error": f"No options chain available for {ticker}"}

    if not expirations:
        return {"error": f"No options expirations found for {ticker}"}

    today = datetime.now().date()
    target_leaps_date = today + timedelta(days=leaps_duration_days)
    target_short_date = today + timedelta(days=short_duration_days)

    closest_leaps_exp = None
    min_leaps_diff = 99999
    
    closest_short_exp = None
    min_short_diff = 99999

    for exp_str in expirations:
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            diff_leaps = abs((exp_date - target_leaps_date).days)
            diff_short = abs((exp_date - target_short_date).days)

            if diff_leaps < min_leaps_diff:
                min_leaps_diff = diff_leaps
                closest_leaps_exp = exp_str

            if diff_short < min_short_diff:
                min_short_diff = diff_short
                closest_short_exp = exp_str
        except Exception:
            pass

    if not closest_leaps_exp or not closest_short_exp:
        return {"error": "Could not find valid expiration dates."}
        
    if closest_leaps_exp == closest_short_exp:
         return {"error": "Short duration and LEAPS duration cannot resolve to the same options chain."}

    leaps_chain = stock.option_chain(closest_leaps_exp)
    short_chain = stock.option_chain(closest_short_exp)

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
        return res

    legs = []
    
    # Target simple delta surrogate:
    # 0.80 Delta for Deep ITM equates roughly to +/- 10-15% ITM.
    # 0.30 Delta for OTM equates roughly to +/- 5-10% OTM.
    if is_call:
        target_leaps_strike = current_price * 0.85 # Deep ITM
        target_short_strike = current_price * 1.05 # OTM
        
        long_leaps = find_closest_option(leaps_chain.calls, target_leaps_strike)
        short_leg = find_closest_option(short_chain.calls, target_short_strike)
        
        if not long_leaps or not short_leg:
             return {"error": "Could not find sufficient option strikes."}
             
        legs.append({
            "action": "Buy", "qty": 1, "type": "Call",
            "strike": round(long_leaps["strike"], 2), "midPrice": long_leaps["mid"],
            "purpose": "LEAPS (-0.80 Δ) acts as synthetic replacement for 100 long shares.",
            "expiration": closest_leaps_exp
        })
        legs.append({
            "action": "Sell", "qty": 1, "type": "Call",
            "strike": round(short_leg["strike"], 2), "midPrice": short_leg["mid"],
            "purpose": "Near-term OTM Call to generate income, rolled forward at expiry.",
            "expiration": closest_short_exp
        })
        
        cost_per_unit = long_leaps["mid"] - short_leg["mid"]
        leaps_cost = long_leaps["mid"] * 100
        short_premium = short_leg["mid"] * 100
        
    else:
        target_leaps_strike = current_price * 1.15 # Deep ITM Put
        target_short_strike = current_price * 0.95 # OTM Put
        
        long_leaps = find_closest_option(leaps_chain.puts, target_leaps_strike)
        short_leg = find_closest_option(short_chain.puts, target_short_strike)
        
        if not long_leaps or not short_leg:
             return {"error": "Could not find sufficient option strikes."}
             
        legs.append({
            "action": "Buy", "qty": 1, "type": "Put",
            "strike": round(long_leaps["strike"], 2), "midPrice": long_leaps["mid"],
            "purpose": "LEAPS (-0.80 Δ) acts as synthetic replacement for 100 short shares.",
            "expiration": closest_leaps_exp
        })
        legs.append({
            "action": "Sell", "qty": 1, "type": "Put",
            "strike": round(short_leg["strike"], 2), "midPrice": short_leg["mid"],
            "purpose": "Near-term OTM Put to generate income, rolled forward at expiry.",
            "expiration": closest_short_exp
        })
        
        cost_per_unit = long_leaps["mid"] - short_leg["mid"]
        leaps_cost = long_leaps["mid"] * 100
        short_premium = short_leg["mid"] * 100
        
    if cost_per_unit <= 0:
        return {"error": "Invalid structure: Options cost resulted in a net credit initially."}
        
    # How much of this setup can we afford?
    affordable_contracts = amount / (cost_per_unit * 100)
    qty = math.floor(affordable_contracts)
    if qty < 1:
        return {"error": f"Investment amount is too low to afford a single contract. Need at least ${round(cost_per_unit * 100, 2)}."}

    # Update qty in ui
    for leg in legs:
        leg["qty"] = qty
        
    total_debit = qty * cost_per_unit * 100
    
    # Capital efficiency mapping
    full_stock_cost = qty * 100 * current_price
    capital_efficiency_saved = full_stock_cost - total_debit
    
    # Scenario Modeling (at the expiration of the short leg)
    # Note: We must estimate the retained value of the LEAPS at the short expiry.
    # To keep it computationally lightweight, we use intrinsic + remaining time value estimate.
    short_actual_dte = abs((datetime.strptime(closest_short_exp, "%Y-%m-%d").date() - datetime.now().date()).days)
    leaps_actual_dte = abs((datetime.strptime(closest_leaps_exp, "%Y-%m-%d").date() - datetime.now().date()).days)
    remaining_days = max(1, leaps_actual_dte - short_actual_dte)
    time_decay_factor = remaining_days / max(1, leaps_actual_dte) # linear decay estimate for extrinsic
    
    scenarios = []
    
    if is_call:
        price_changes = [-0.15, -0.10, -0.05, 0.0, 0.05, 0.10, 0.15]
    else:
        price_changes = [0.15, 0.10, 0.05, 0.0, -0.05, -0.10, -0.15]
        
    extrinsic_leaps = long_leaps["mid"] - max(0, current_price - long_leaps["strike"] if is_call else long_leaps["strike"] - current_price)
    
    for change in price_changes:
        sim_price = current_price * (1 + change)
        
        if is_call:
             short_payout = -max(0, sim_price - short_leg["strike"])
             intrinsic_leaps = max(0, sim_price - long_leaps["strike"])
             est_leaps_value = intrinsic_leaps + (extrinsic_leaps * time_decay_factor)
             
             net_value_per_unit = est_leaps_value + short_payout
             net_strategy_profit = (net_value_per_unit - cost_per_unit) * 100 * qty
             
             # Comparative profit if holding 100 stock
             stock_profit = (sim_price - current_price) * 100 * qty
             status = "Assigned (Loss offset)" if sim_price >= short_leg["strike"] else "Retained LEAPS"
        else:
             short_payout = -max(0, short_leg["strike"] - sim_price)
             intrinsic_leaps = max(0, long_leaps["strike"] - sim_price)
             est_leaps_value = intrinsic_leaps + (extrinsic_leaps * time_decay_factor)
             
             net_value_per_unit = est_leaps_value + short_payout
             net_strategy_profit = (net_value_per_unit - cost_per_unit) * 100 * qty
             
             # Comparative profit if shorting 100 stock
             stock_profit = (current_price - sim_price) * 100 * qty
             status = "Assigned (Loss offset)" if sim_price <= short_leg["strike"] else "Retained LEAPS"
             
        scenarios.append({
            "underlyingChangePct": round(change * 100, 1),
            "simulatedPrice": round(sim_price, 2),
            "status": status,
            "netProfit": round(net_strategy_profit, 2),
            "stockEquivalentProfit": round(stock_profit, 2)
        })

    return {
        "success": True,
        "ticker": ticker,
        "currentPrice": round(current_price, 2),
        "investmentAmount": amount,
        "strategy": "Poor Man's Covered Call" if is_call else "Poor Man's Covered Put",
        "leapsExpiration": closest_leaps_exp,
        "shortExpiration": closest_short_exp,
        "qtyContracts": qty,
        "totalDebit": round(total_debit, 2),
        "capitalEfficiencySaved": round(capital_efficiency_saved, 2),
        "leapsCost": round(leaps_cost * qty, 2),
        "shortPremiumCollected": round(short_premium * qty, 2),
        "legs": legs,
        "scenarios": scenarios
    }

async def run_pmcc_pmcp(
    ticker: str,
    amount: float,
    is_call: bool,
    leaps_duration_days: int,
    short_duration_days: int
) -> dict:
    return await asyncio.to_thread(
        _run_pmcc_pmcp_sync,
        ticker,
        amount,
        is_call,
        leaps_duration_days,
        short_duration_days
    )
