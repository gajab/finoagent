"""Constant Proportion Portfolio Insurance (CPPI) Strategy Service.

Implements the dynamic CPPI and Volatility-Targeted Dynamic CPPI (D-CPPI) 
mathematical asset allocation algorithms on actual historical stock data.
"""

import asyncio
import math
import logging
from datetime import datetime
import numpy as np
import yfinance as yf
from .stock_service import safe_float

logger = logging.getLogger(__name__)

# Annualized spread charged over the risk-free rate on margin (negative cash)
# balances when leverage is enabled — an institutional broker-loan assumption.
_BORROW_SPREAD = 0.015  # 150 bps


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else default
    except (TypeError, ValueError):
        return default


def _to_jsonable(obj):
    """Recursively coerce a result tree into JSON-native Python types.

    numpy scalars (e.g. ``numpy.bool_``, ``numpy.float64``) and non-finite
    floats are not serializable by Starlette's ``JSONResponse`` and would
    surface to the client as an opaque 500. Convert numpy -> Python and map
    NaN/Inf -> 0.0 so the payload is always well-formed.
    """
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [_to_jsonable(v) for v in obj.tolist()]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else 0.0
    return obj

def _run_cppi_simulation_sync(
    ticker: str,
    amount: float,
    floor_pct: float,
    multiplier: float,
    duration_years: int,
    rebalance_freq: str,
    rebalance_threshold_pct: float,
    risk_free_rate: float,
    transaction_fee_pct: float,
    allow_leverage: bool,
    dynamic_multiplier: bool
) -> dict:
    """
    Synchronous core simulation of the CPPI / D-CPPI trading strategy.
    Runs on actual historical daily prices of ticker fetched via yfinance.
    """
    ticker = ticker.upper()
    stock = yf.Ticker(ticker)
    
    # Map duration years to yfinance period
    period_map = {1: "1y", 2: "2y", 3: "3y", 5: "5y", 10: "10y"}
    period = period_map.get(duration_years, "3y")
    
    try:
        hist = stock.history(period=period, interval="1d")
    except Exception as exc:
        return {"error": f"Failed to fetch historical data for {ticker}: {exc}"}
        
    if hist.empty or len(hist) < 30:
        return {"error": f"Insufficient historical price data returned for {ticker}."}

    # Extract price history
    dates = [d.strftime("%Y-%m-%d") for d in hist.index]
    prices = [float(p) for p in hist["Close"]]
    highs = [float(p) for p in hist["High"]]
    lows = [float(p) for p in hist["Low"]]
    
    # Trailing 20-day annualized volatility driving the Dynamic (D-CPPI) multiplier.
    # Computed with NO look-ahead: the multiplier applied on day t may only use
    # returns realised strictly before day t (window ending at t-1). This avoids
    # the subtle bias of letting today's move size today's own exposure.
    close_series = hist["Close"]
    rets_arr = close_series.pct_change().to_numpy()  # rets_arr[0] is NaN
    ann_factor = math.sqrt(252.0)
    default_vol = 0.25  # 25% fallback before enough history exists

    vol_values = []
    for t in range(len(prices)):
        window = rets_arr[max(1, t - 20):t]  # up to 20 returns ending at t-1
        window = window[~np.isnan(window)]
        if window.size >= 2:
            v = float(np.std(window, ddof=1) * ann_factor)
            vol_values.append(v if v > 1e-6 else default_vol)
        else:
            vol_values.append(default_vol)

    # CPPI Variables Initialization
    v_0 = amount
    f_0 = (floor_pct / 100.0) * v_0
    
    v_t = v_0
    f_t = f_0
    
    # Setup safe rate daily factor
    r_f_daily = (risk_free_rate / 100.0) / 252.0
    borrow_daily = (risk_free_rate / 100.0 + _BORROW_SPREAD) / 252.0
    fee_factor = transaction_fee_pct / 100.0

    def accrue_cash(cash: float) -> float:
        # Positive cash earns the risk-free rate; negative (margin) balances are
        # charged risk-free + an institutional borrow spread.
        return cash * (1.0 + (r_f_daily if cash >= 0.0 else borrow_daily))
    
    # Track historical metrics for plotting
    cppi_history = []
    bh_history = []
    floor_history = []
    risky_exposure_history = []
    cash_exposure_history = []
    multiplier_history = []
    
    trade_logs = []
    cash_locked = False
    cash_lock_date = None
    total_trades = 0
    total_fees = 0.0
    max_leverage_reached = 1.0
    
    # Day 0 initialization
    # Calculate starting multiplier
    if dynamic_multiplier:
        # Scale multiplier inversely with volatility: m = max(1.0, min(8.0, 0.60 / rolling_vol))
        current_m = max(1.0, min(8.0, 0.60 / vol_values[0]))
    else:
        current_m = multiplier
        
    c_0 = v_0 - f_0
    target_e = current_m * c_0
    
    # Position sizing limits
    if not allow_leverage:
        target_e = min(v_0, target_e)
    else:
        target_e = min(2.0 * v_0, target_e) # Limit maximum leverage to 2.0x
        
    target_e = max(0.0, target_e)
    
    risky_exposure = target_e
    safe_exposure = v_0 - risky_exposure
    
    # Apply initial transaction fee
    initial_fee = risky_exposure * fee_factor
    safe_exposure -= initial_fee
    total_fees += initial_fee
    v_t = risky_exposure + safe_exposure
    
    qty = risky_exposure / prices[0]
    
    # Record starting step
    cppi_history.append(v_t)
    bh_history.append(v_0)
    floor_history.append(f_0)
    risky_exposure_history.append(risky_exposure)
    cash_exposure_history.append(safe_exposure)
    multiplier_history.append(current_m)
    
    if initial_fee > 0:
        trade_logs.append({
            "date": dates[0],
            "action": "BUY (Initial Allocation)",
            "price": round(prices[0], 2),
            "shares": round(qty, 4),
            "value": round(risky_exposure, 2),
            "fee": round(initial_fee, 2),
            "portfolio_value": round(v_t, 2),
            "cash_balance": round(safe_exposure, 2)
        })
        total_trades += 1

    # Daily simulation loop
    for t in range(1, len(prices)):
        current_price = prices[t]
        prev_price = prices[t-1]
        
        # 1. Update risky asset exposure (stock changes by return)
        e_new = qty * current_price
        
        # 2. Update safe asset exposure (cash earns risk-free; margin pays borrow)
        s_new = accrue_cash(safe_exposure)

        # 3. Total portfolio value before rebalancing
        v_t = e_new + s_new

        # 4. Floor compounds at the risk-free rate on its own deterministic
        #    schedule. It is intentionally NOT capped to the portfolio value:
        #    in a gap-down the portfolio can fall through the floor, and that
        #    breach is precisely the gap risk this engine is built to surface.
        f_t = f_t * (1.0 + r_f_daily)

        c_t = v_t - f_t
        
        # 5. Volatility / Multiplier update
        if dynamic_multiplier:
            current_m = max(1.0, min(8.0, 0.60 / vol_values[t]))
        else:
            current_m = multiplier
            
        # 6. Cash-Lock check
        if c_t <= 0.0 and not cash_locked:
            cash_locked = True
            cash_lock_date = dates[t]
            
            # Liquidate position
            liquidation_value = qty * current_price
            liquidation_fee = liquidation_value * fee_factor
            
            safe_exposure = s_new + liquidation_value - liquidation_fee
            total_fees += liquidation_fee
            qty = 0.0
            risky_exposure = 0.0
            v_t = safe_exposure
            
            trade_logs.append({
                "date": dates[t],
                "action": "LIQUIDATE (Cash-Lock Triggered)",
                "price": round(current_price, 2),
                "shares": round(liquidation_value / current_price, 4),
                "value": round(liquidation_value, 2),
                "fee": round(liquidation_fee, 2),
                "portfolio_value": round(v_t, 2),
                "cash_balance": round(safe_exposure, 2)
            })
            total_trades += 1
            
        elif cash_locked:
            # Entirely in cash
            qty = 0.0
            risky_exposure = 0.0
            safe_exposure = s_new
            v_t = safe_exposure
            
        else:
            # 7. Evaluate Rebalancing Condition
            target_e = current_m * c_t
            
            if not allow_leverage:
                target_e = min(v_t, target_e)
            else:
                target_e = min(2.0 * v_t, target_e)
                
            target_e = max(0.0, target_e)
            
            # Record leverage metrics
            current_leverage = target_e / v_t if v_t > 0 else 1.0
            if current_leverage > max_leverage_reached:
                max_leverage_reached = current_leverage
                
            # Check rebalance triggers
            trigger_rebalance = False
            
            if rebalance_freq == "daily":
                trigger_rebalance = True
            elif rebalance_freq == "weekly" and t % 5 == 0:
                trigger_rebalance = True
            elif rebalance_freq == "monthly" and t % 21 == 0:
                trigger_rebalance = True
            elif rebalance_freq == "threshold":
                deviation_pct = abs(e_new - target_e) / v_t * 100.0
                if deviation_pct >= rebalance_threshold_pct:
                    trigger_rebalance = True
                    
            if trigger_rebalance:
                trade_amount = target_e - e_new
                trade_fee = abs(trade_amount) * fee_factor
                
                # Check if we can afford the trade fee
                if s_new - trade_amount - trade_fee >= -0.5 * v_t: # margin borrowing limit
                    # Execute trade
                    safe_exposure = s_new - trade_amount - trade_fee
                    qty = target_e / current_price
                    risky_exposure = target_e
                    v_t = risky_exposure + safe_exposure
                    total_fees += trade_fee
                    
                    if abs(trade_amount) > 1.0: # Filter microscopic dust trades
                        trade_logs.append({
                            "date": dates[t],
                            "action": "BUY" if trade_amount > 0 else "SELL",
                            "price": round(current_price, 2),
                            "shares": round(abs(trade_amount) / current_price, 4),
                            "value": round(abs(trade_amount), 2),
                            "fee": round(trade_fee, 2),
                            "portfolio_value": round(v_t, 2),
                            "cash_balance": round(safe_exposure, 2)
                        })
                        total_trades += 1
                else:
                    # Keep same holdings if fee is completely unaffordable
                    qty = qty
                    risky_exposure = e_new
                    safe_exposure = s_new
            else:
                # No trade today
                qty = qty
                risky_exposure = e_new
                safe_exposure = s_new

        # 7b. Solvency guard: under leverage a severe single-day gap can wipe out
        # equity. Model a margin call — floor the account at a residual value and
        # lock it permanently rather than letting equity go negative.
        if v_t <= 0.0:
            v_t = max(v_t, 1.0)
            risky_exposure = 0.0
            qty = 0.0
            safe_exposure = v_t
            if not cash_locked:
                cash_locked = True
                cash_lock_date = dates[t]

        # 8. Record Daily Metrics
        cppi_history.append(v_t)
        bh_history.append(v_0 * (current_price / prices[0]))
        floor_history.append(f_t)
        risky_exposure_history.append(risky_exposure)
        cash_exposure_history.append(safe_exposure)
        multiplier_history.append(current_m)

    # 9. Compute Portfolio Statistics
    cppi_arr = np.array(cppi_history)
    bh_arr = np.array(bh_history)
    
    total_days = len(prices)
    years = total_days / 252.0
    
    # Cumulative returns
    cum_cppi = (cppi_arr[-1] / cppi_arr[0] - 1.0) * 100.0
    cum_bh = (bh_arr[-1] / bh_arr[0] - 1.0) * 100.0
    
    # CAGR
    cagr_cppi = (pow(cppi_arr[-1] / cppi_arr[0], 1.0 / years) - 1.0) * 100.0 if cppi_arr[-1] > 0 else -100.0
    cagr_bh = (pow(bh_arr[-1] / bh_arr[0], 1.0 / years) - 1.0) * 100.0
    
    # Daily simple returns — the institutional basis for risk-adjusted ratios.
    daily_rf = (risk_free_rate / 100.0) / 252.0
    cppi_ret = cppi_arr[1:] / cppi_arr[:-1] - 1.0
    bh_ret = bh_arr[1:] / bh_arr[:-1] - 1.0

    def ann_vol(rets):
        return float(np.std(rets, ddof=1) * math.sqrt(252) * 100.0) if rets.size > 1 else 0.0

    def ann_sharpe(rets):
        if rets.size < 2:
            return 0.0
        ex = rets - daily_rf
        sd = np.std(ex, ddof=1)
        return float(np.mean(ex) / sd * math.sqrt(252)) if sd > 1e-12 else 0.0

    def ann_sortino(rets):
        if rets.size < 2:
            return 0.0
        ex = rets - daily_rf
        downside = ex[ex < 0.0]
        dd = math.sqrt(float(np.mean(downside ** 2))) if downside.size > 0 else 0.0
        return float(np.mean(ex) / dd * math.sqrt(252)) if dd > 1e-12 else 0.0

    vol_cppi = ann_vol(cppi_ret)
    vol_bh = ann_vol(bh_ret)
    sharpe_cppi = ann_sharpe(cppi_ret)
    sharpe_bh = ann_sharpe(bh_ret)
    sortino_cppi = ann_sortino(cppi_ret)
    sortino_bh = ann_sortino(bh_ret)

    # Max Drawdowns
    def get_max_drawdown(arr):
        peaks = np.maximum.accumulate(arr)
        drawdowns = (arr - peaks) / peaks * 100.0
        return float(np.min(drawdowns))

    max_dd_cppi = get_max_drawdown(cppi_arr)
    max_dd_bh = get_max_drawdown(bh_arr)

    # Calmar ratio = CAGR / |Max Drawdown| (return earned per unit of worst loss).
    calmar_cppi = float(cagr_cppi / abs(max_dd_cppi)) if max_dd_cppi < -1e-9 else 0.0
    calmar_bh = float(cagr_bh / abs(max_dd_bh)) if max_dd_bh < -1e-9 else 0.0
    
    # Shortfall violations (did it fall below the floor?)
    # Since floor is growing, check if CPPI ever drops below the floor by more than 0.5% (to account for tiny decimal deviations)
    floor_diffs = cppi_arr - np.array(floor_history)
    breached_days = int(np.sum(floor_diffs < -0.005 * cppi_arr))
    floor_breached = breached_days > 0
    worst_breach_pct = float(np.min(floor_diffs / cppi_arr * 100.0)) if floor_breached else 0.0
    
    # Gap Risk Stress Test Diagnostics:
    # A single-day drop of 1/multiplier in the stock will breach the floor.
    # We display what this threshold drop is today.
    current_vol = vol_values[-1]
    avg_vol = float(np.mean(vol_values))
    latest_m = current_m
    
    gap_breach_threshold = (1.0 / latest_m) * 100.0
    
    # Estimate gap breach probability (using normal distribution approximation)
    # P(Daily Return <= -1/m)
    daily_vol = current_vol / math.sqrt(252)
    daily_rf = risk_free_rate / 100.0 / 252.0
    
    # Z-score of the breach threshold: Z = (-1/m - daily_rf) / daily_vol.
    # current_vol / daily_vol are fractions (e.g. 0.45), so divide by daily_vol
    # directly — no extra /100 scaling.
    z_score = (-1.0 / latest_m - daily_rf) / daily_vol if daily_vol > 0 else -9.9
    
    # Standard normal cumulative distribution approximation
    def norm_cdf(z):
        return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
        
    gap_breach_prob = norm_cdf(z_score) * 100.0 if z_score > -9.0 else 0.0
    
    # Format histories to JSON safe lists
    limit_points = 500 # Downsample chart points if data is huge for UI performance
    step = max(1, len(dates) // limit_points)
    
    chart_dates = dates[::step]
    chart_cppi = [round(v, 2) for v in cppi_history[::step]]
    chart_bh = [round(v, 2) for v in bh_history[::step]]
    chart_floor = [round(v, 2) for v in floor_history[::step]]
    chart_risky = [round(v, 2) for v in risky_exposure_history[::step]]
    chart_cash = [round(v, 2) for v in cash_exposure_history[::step]]
    chart_multiplier = [round(m, 2) for m in multiplier_history[::step]]
    
    # Append the last item if it got skipped in downsampling
    if chart_dates[-1] != dates[-1]:
        chart_dates.append(dates[-1])
        chart_cppi.append(round(cppi_history[-1], 2))
        chart_bh.append(round(bh_history[-1], 2))
        chart_floor.append(round(floor_history[-1], 2))
        chart_risky.append(round(risky_exposure_history[-1], 2))
        chart_cash.append(round(cash_exposure_history[-1], 2))
        chart_multiplier.append(round(multiplier_history[-1], 2))

    return _to_jsonable({
        "success": True,
        "ticker": ticker,
        "currentPrice": round(prices[-1], 2),
        "investmentAmount": amount,
        "floorPct": floor_pct,
        "riskMultiplier": multiplier,
        "durationYears": duration_years,
        "rebalanceFreq": rebalance_freq,
        "rebalanceThresholdPct": rebalance_threshold_pct,
        "riskFreeRate": risk_free_rate,
        "transactionFeePct": transaction_fee_pct,
        "allowLeverage": allow_leverage,
        "dynamicMultiplier": dynamic_multiplier,
        
        # Performance comparison
        "performance": {
            "cppi": {
                "cumulativeReturn": round(cum_cppi, 2),
                "cagr": round(cagr_cppi, 2),
                "volatility": round(vol_cppi, 2),
                "sharpe": round(sharpe_cppi, 2),
                "sortino": round(sortino_cppi, 2),
                "calmar": round(calmar_cppi, 2),
                "maxDrawdown": round(max_dd_cppi, 2),
                "finalValue": round(cppi_arr[-1], 2)
            },
            "buyAndHold": {
                "cumulativeReturn": round(cum_bh, 2),
                "cagr": round(cagr_bh, 2),
                "volatility": round(vol_bh, 2),
                "sharpe": round(sharpe_bh, 2),
                "sortino": round(sortino_bh, 2),
                "calmar": round(calmar_bh, 2),
                "maxDrawdown": round(max_dd_bh, 2),
                "finalValue": round(bh_arr[-1], 2)
            }
        },
        
        # Risk summaries
        "riskAnalysis": {
            "cashLocked": cash_locked,
            "cashLockDate": cash_lock_date,
            "totalTrades": total_trades,
            "totalFeesPaid": round(total_fees, 2),
            "maxLeverageReached": round(max_leverage_reached, 2),
            "floorBreached": floor_breached,
            "worstFloorBreachPct": round(abs(worst_breach_pct), 2) if floor_breached else 0.0,
            
            # Gap risk diagnostic
            "gapBreachThresholdPct": round(gap_breach_threshold, 2),
            "gapBreachProbabilityPct": round(gap_breach_prob, 4),
            "currentAssetVol": round(current_vol * 100.0, 2),
            "averageAssetVol": round(avg_vol * 100.0, 2),
            "latestMultiplier": round(latest_m, 2)
        },
        
        # Charts
        "charts": {
            "dates": chart_dates,
            "cppiValue": chart_cppi,
            "buyAndHoldValue": chart_bh,
            "floorValue": chart_floor,
            "riskyExposure": chart_risky,
            "cashExposure": chart_cash,
            "multiplier": chart_multiplier
        },
        
        # Trades
        "trades": trade_logs[:100] # Return latest 100 logs to prevent payload blowup
    })

async def run_cppi_simulation(
    ticker: str,
    amount: float,
    floor_pct: float,
    multiplier: float,
    duration_years: int,
    rebalance_freq: str,
    rebalance_threshold_pct: float,
    risk_free_rate: float,
    transaction_fee_pct: float,
    allow_leverage: bool,
    dynamic_multiplier: bool
) -> dict:
    """
    Async wrapper executing the CPPI dynamic simulator in a background thread pool.
    """
    return await asyncio.to_thread(
        _run_cppi_simulation_sync,
        ticker,
        amount,
        floor_pct,
        multiplier,
        duration_years,
        rebalance_freq,
        rebalance_threshold_pct,
        risk_free_rate,
        transaction_fee_pct,
        allow_leverage,
        dynamic_multiplier
    )
