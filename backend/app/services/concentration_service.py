import asyncio
import math
from datetime import datetime, timedelta
import yfinance as yf
from .stock_service import safe_float

def _run_concentration_management_sync(
    ticker: str,
    shares: int,
    cost_basis: float,
    position_type: str,
    tax_rate_pct: float,
    duration_days: int,
    upside_cap_pct: float = 10.0,
    downside_protection_pct: float = 10.0
) -> dict:
    """
    Simulates a Concentration Management strategy using an Equity Collar.
    For a Long position: Sell OTM Call (to generate income/set exit), Buy OTM Put (floor protection)
    For a Short position: Sell OTM Put (buy to cover), Buy OTM Call (protect upside risk)
    Calculates tax impact.
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

    chain = stock.option_chain(closest_exp)
    calls = chain.calls
    puts = chain.puts

    if calls.empty or puts.empty:
        return {"error": f"Incomplete options chain for expiration {closest_exp}"}

    # Helper function to find closest strike
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

    is_long = position_type.lower() == 'long'
    contracts_needed = math.ceil(shares / 100)
    
    legs = []
    
    if is_long:
        # Long Position Collar: target selling call at +upside%, buying put at -downside%
        target_call_strike = current_price * (1 + upside_cap_pct / 100.0)
        target_put_strike = current_price * (1 - downside_protection_pct / 100.0)
        
        short_call = find_closest_option(calls, target_call_strike)
        long_put = find_closest_option(puts, target_put_strike)
        
        if not short_call or not long_put:
             return {"error": "Could not find suitable option strikes required."}
             
        legs.append({
            "action": "Sell", "qty": contracts_needed, "type": "Call",
            "strike": round(short_call["strike"], 2), "midPrice": short_call["mid"],
            "purpose": "Generate income and set target exit price"
        })
        legs.append({
            "action": "Buy", "qty": contracts_needed, "type": "Put",
            "strike": round(long_put["strike"], 2), "midPrice": long_put["mid"],
            "purpose": "Protect against downside crash"
        })
        
        net_premium_per_share = short_call["mid"] - long_put["mid"]
        
    else:
        # Short Position Reverse Collar: target selling put at -downside%, buying call at +upside%
        target_put_strike = current_price * (1 - downside_protection_pct / 100.0)
        target_call_strike = current_price * (1 + upside_cap_pct / 100.0)
        
        short_put = find_closest_option(puts, target_put_strike)
        long_call = find_closest_option(calls, target_call_strike)
        
        if not short_put or not long_call:
             return {"error": "Could not find suitable option strikes required."}
             
        legs.append({
            "action": "Sell", "qty": contracts_needed, "type": "Put",
            "strike": round(short_put["strike"], 2), "midPrice": short_put["mid"],
            "purpose": "Generate income and set target buy-to-cover price"
        })
        legs.append({
            "action": "Buy", "qty": contracts_needed, "type": "Call",
            "strike": round(long_call["strike"], 2), "midPrice": long_call["mid"],
            "purpose": "Protect against short squeeze/upside risk"
        })
        
        net_premium_per_share = short_put["mid"] - long_call["mid"]

    net_premium_total = net_premium_per_share * 100 * contracts_needed
    tax_rate = tax_rate_pct / 100.0
    
    scenarios = []
    
    # Calculate a nice spread of points for the chart around the configured bounds
    u_pct = upside_cap_pct / 100.0
    d_pct = -downside_protection_pct / 100.0
    price_changes = sorted(list(set([
        round(d_pct - 0.15, 3), round(d_pct - 0.05, 3), 
        round(d_pct, 3), 0.0, round(u_pct, 3), 
        round(u_pct + 0.05, 3), round(u_pct + 0.15, 3)
    ])))
    
    for change in price_changes:
        sim_price = current_price * (1 + change)
        unrealized_gain = 0
        realized_gain = 0
        capital_gains_tax = 0
        final_stock_value = 0
        status = ""
        
        if is_long:
            call_strike = legs[0]["strike"]
            put_strike = legs[1]["strike"]
            
            if sim_price >= call_strike:
                # Stock assigned at call_strike
                final_stock_value = call_strike * shares
                realized_gain = (call_strike - cost_basis) * shares
                capital_gains_tax = realized_gain * tax_rate if realized_gain > 0 else 0
                status = "Called Away (Exited Position)"
            elif sim_price <= put_strike:
                # Put active, provides floor
                final_stock_value = put_strike * shares # Effectively can sell at put strike
                unrealized_gain = (put_strike - cost_basis) * shares
                status = "Protected Floor Restored"
            else:
                # Stays in between
                final_stock_value = sim_price * shares
                unrealized_gain = (sim_price - cost_basis) * shares
                status = "Retained Position"
        else:
            put_strike = legs[0]["strike"]
            call_strike = legs[1]["strike"]
            
            if sim_price <= put_strike:
                # Put assigned, forced to buy-to-cover at put_strike
                # Short profit = (Cost Basis - Exit Price)
                realized_gain = (cost_basis - put_strike) * shares
                capital_gains_tax = realized_gain * tax_rate if realized_gain > 0 else 0
                final_stock_value = (cost_basis - put_strike) * shares # Representing the closed out short cash position
                status = "Assigned to Cover (Exited Short)"
            elif sim_price >= call_strike:
                # Call active, limits short losses
                unrealized_gain = (cost_basis - call_strike) * shares
                final_stock_value = unrealized_gain
                status = "Upside Stop-Loss Triggered"
            else:
                unrealized_gain = (cost_basis - sim_price) * shares
                final_stock_value = unrealized_gain
                status = "Retained Short Position"

        # The net outcome factors in the initial stock holding value + the net premium - the taxes paid
        net_after_tax_value = final_stock_value + net_premium_total - capital_gains_tax

        scenarios.append({
            "underlyingChangePct": round(change * 100, 1),
            "simulatedPrice": round(sim_price, 2),
            "realizedGain": round(realized_gain, 2),
            "unrealizedGain": round(unrealized_gain, 2),
            "taxOwed": round(capital_gains_tax, 2),
            "status": status,
            "netAfterTaxValue": round(net_after_tax_value, 2)
        })

    current_value = current_price * shares if is_long else (cost_basis - current_price) * shares

    return {
        "success": True,
        "ticker": ticker,
        "currentPrice": round(current_price, 2),
        "shares": shares,
        "positionType": position_type,
        "currentValue": round(current_value, 2),
        "costBasis": cost_basis,
        "durationDays": duration_days,
        "expirationDate": closest_exp,
        "actualDte": actual_dte,
        "taxRatePct": tax_rate_pct,
        "netPremiumTotal": round(net_premium_total, 2),
        "syntheticTaxSavings": round(net_premium_total * tax_rate, 2), # If the premium offsets tax burdens
        "legs": legs,
        "scenarios": scenarios
    }

async def run_concentration_management(
    ticker: str,
    shares: int,
    cost_basis: float,
    position_type: str,
    tax_rate_pct: float,
    duration_days: int,
    upside_cap_pct: float = 10.0,
    downside_protection_pct: float = 10.0
) -> dict:
    return await asyncio.to_thread(
        _run_concentration_management_sync,
        ticker,
        shares,
        cost_basis,
        position_type,
        tax_rate_pct,
        duration_days,
        upside_cap_pct,
        downside_protection_pct
    )
