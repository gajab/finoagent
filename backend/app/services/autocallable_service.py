import asyncio
import math
from datetime import datetime, timedelta
import numpy as np
import yfinance as yf

# Autocallable (a.k.a. "phoenix" / "snowball") structured note.
#
# Mechanics modelled here:
#   - The note observes the underlying on a fixed schedule (e.g. quarterly).
#   - On any observation date, if spot >= the AUTOCALL barrier, the note redeems
#     early: investor gets principal back plus all coupons accrued so far.
#   - A coupon is paid each observation where spot >= the COUPON barrier
#     (set equal to the downside barrier here — the "phoenix" variant).
#   - At maturity, if never called:
#       * spot >= DOWNSIDE barrier -> principal returned (+ final coupon)
#       * spot <  DOWNSIDE barrier -> capital loss 1:1 with the underlying
#         (the investor is effectively short a down-and-in put — that sold-put
#         premium is exactly what funds the rich coupon).
#
# The coupon is SOLVED so it is roughly self-financing: risk-free carry on the
# protected principal plus the amortised premium of the crash put being sold.

_DEFAULT_VOL = 0.25
_MIN_COUPON = 0.01
_MAX_COUPON = 0.30
_N_PATHS = 20000


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_put(spot: float, strike: float, t: float, rate: float, vol: float) -> float:
    """Black-Scholes price of a European put (per share)."""
    if t <= 0 or vol <= 0 or spot <= 0 or strike <= 0:
        return max(0.0, strike - spot)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    return strike * math.exp(-rate * t) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def _risk_free_rate(user_interest_rate: float | None) -> tuple[float, str]:
    if user_interest_rate is not None:
        return user_interest_rate / 100.0, f"User Input ({user_interest_rate}%)"
    try:
        irx_info = yf.Ticker("^IRX").info
        price = irx_info.get("regularMarketPrice") or irx_info.get("previousClose")
        if price and price > 0:
            return price / 100.0, f"^IRX ({price}%)"
    except Exception:
        pass
    return 0.04, "Default (4.0%)"


def _annualized_vol(stock: yf.Ticker) -> float:
    try:
        hist = stock.history(period="1y")
        if hist is None or hist.empty or len(hist) < 20:
            return _DEFAULT_VOL
        closes = hist["Close"].dropna()
        log_ret = np.log(closes / closes.shift(1)).dropna()
        vol = float(log_ret.std() * math.sqrt(252))
        if not math.isfinite(vol) or vol <= 0:
            return _DEFAULT_VOL
        return min(vol, 1.5)
    except Exception:
        return _DEFAULT_VOL


def _run_autocallable_sync(
    ticker: str,
    amount: float,
    duration_days: int,
    autocall_barrier_pct: float = 100.0,
    downside_barrier_pct: float = 70.0,
    frequency: int = 4,
    user_interest_rate: float | None = None,
    user_coupon_pct: float | None = None,
) -> dict:
    ticker = ticker.upper()
    stock = yf.Ticker(ticker)

    rate, rate_source = _risk_free_rate(user_interest_rate)

    info = stock.info or {}
    spot = info.get("currentPrice") or info.get("regularMarketPrice") or 0.0
    if not spot:
        try:
            hist = stock.history(period="1d")
            if not hist.empty:
                spot = float(hist["Close"].iloc[-1])
        except Exception:
            pass
    if not spot:
        return {"error": f"Could not fetch current price for {ticker}"}

    vol = _annualized_vol(stock)

    frequency = max(1, min(12, int(frequency)))
    t_years = max(duration_days / 365.0, 1.0 / frequency)
    n_obs = max(1, round(frequency * t_years))
    dt = t_years / n_obs

    autocall_price = spot * autocall_barrier_pct / 100.0
    downside_price = spot * downside_barrier_pct / 100.0  # also the coupon barrier (phoenix)

    # 1. Solve a self-financing annual coupon (or honour a user override)
    if user_coupon_pct is not None:
        annual_coupon = user_coupon_pct / 100.0
        coupon_source = f"User ({user_coupon_pct}%)"
    else:
        put_premium = _bs_put(spot, downside_price, t_years, rate, vol)
        put_pct = put_premium / spot
        annual_coupon = rate + put_pct / t_years
        annual_coupon = max(_MIN_COUPON, min(_MAX_COUPON, annual_coupon))
        coupon_source = "Solved (risk-free + crash-put premium)"
    per_period_coupon = annual_coupon / frequency

    # 2. Monte-Carlo the path-dependent payoff (risk-neutral GBM, seeded for stability)
    rng = np.random.default_rng(42)
    z = rng.standard_normal((_N_PATHS, n_obs))
    drift = (rate - 0.5 * vol * vol) * dt
    diffusion = vol * math.sqrt(dt)
    log_path = np.cumsum(drift + diffusion * z, axis=1)
    prices = spot * np.exp(log_path)  # price at each observation [paths, n_obs]

    hit = prices >= autocall_price
    has_call = hit.any(axis=1)
    first_call = np.where(has_call, hit.argmax(axis=1), n_obs - 1)

    j = np.arange(n_obs)[None, :]
    within = j <= first_call[:, None]
    coupon_count = ((prices >= downside_price) & within).sum(axis=1)
    coupon_return = coupon_count * per_period_coupon

    final_price = prices[:, -1]
    not_called = ~has_call
    loss = not_called & (final_price < downside_price)
    principal_return = np.where(loss, final_price / spot - 1.0, 0.0)

    total_return = coupon_return + principal_return
    holding_years = np.where(has_call, (first_call + 1) / frequency, t_years)
    annualized = np.where(
        holding_years > 0,
        np.power(np.clip(1.0 + total_return, 1e-9, None), 1.0 / holding_years) - 1.0,
        total_return,
    )

    p_called = float(has_call.mean())
    p_loss = float(loss.mean())
    p_protected = float((not_called & ~loss).mean())

    # 3. Per-observation autocall schedule (for the probability chart)
    today = datetime.now().date()
    schedule = []
    for k in range(n_obs):
        called_this = float((has_call & (first_call == k)).mean())
        cum_called = float((has_call & (first_call <= k)).mean())
        obs_date = today + timedelta(days=round((k + 1) * dt * 365))
        schedule.append({
            "period": k + 1,
            "date": obs_date.isoformat(),
            "pCalledThisPeriod": round(called_this * 100, 1),
            "cumPCalled": round(cum_called * 100, 1),
            "couponIfCalledPct": round((k + 1) * per_period_coupon * 100, 2),
        })

    # 4. Deterministic terminal scenarios (illustrate the coupon ceiling + crash cliff)
    total_coupons_full = annual_coupon * t_years
    scenarios = []
    for change in [-0.50, -0.40, -0.30, -0.20, -0.10, 0.0, 0.10, 0.20]:
        fp = spot * (1 + change)
        if fp >= downside_price:
            status = "called" if fp >= autocall_price else "protected"
            roi = total_coupons_full * 100
            payout = amount * (1 + total_coupons_full)
        else:
            status = "loss"
            roi = change * 100
            payout = amount * (1 + change)
        scenarios.append({
            "underlyingChangePct": round(change * 100, 1),
            "simulatedPrice": round(fp, 2),
            "status": status,
            "totalPayout": round(payout, 2),
            "roi": round(roi, 2),
        })

    return {
        "success": True,
        "ticker": ticker,
        "structure": "autocallable",
        "currentPrice": round(spot, 2),
        "investmentAmount": amount,
        "durationDays": duration_days,
        "tenorYears": round(t_years, 2),
        "observations": n_obs,
        "frequency": frequency,
        "interestRate": round(rate * 100, 2),
        "rateSource": rate_source,
        "impliedVol": round(vol * 100, 1),
        "couponSource": coupon_source,
        "couponPerAnnumPct": round(annual_coupon * 100, 2),
        "couponPerPeriodPct": round(per_period_coupon * 100, 2),
        "maxCouponPct": round(total_coupons_full * 100, 2),
        "barriers": {
            "autocallPct": round(autocall_barrier_pct, 1),
            "autocallPrice": round(autocall_price, 2),
            "downsidePct": round(downside_barrier_pct, 1),
            "downsidePrice": round(downside_price, 2),
        },
        "probabilities": {
            "called": round(p_called * 100, 1),
            "heldProtected": round(p_protected * 100, 1),
            "capitalLoss": round(p_loss * 100, 1),
        },
        "expected": {
            "returnPct": round(float(total_return.mean()) * 100, 2),
            "annualizedPct": round(float(annualized.mean()) * 100, 2),
            "holdingYears": round(float(holding_years.mean()), 2),
            "p5ReturnPct": round(float(np.percentile(total_return, 5)) * 100, 2),
            "medianReturnPct": round(float(np.percentile(total_return, 50)) * 100, 2),
            "p95ReturnPct": round(float(np.percentile(total_return, 95)) * 100, 2),
        },
        "schedule": schedule,
        "scenarios": scenarios,
    }


async def run_autocallable(
    ticker: str,
    amount: float,
    duration_days: int,
    autocall_barrier_pct: float = 100.0,
    downside_barrier_pct: float = 70.0,
    frequency: int = 4,
    user_interest_rate: float | None = None,
    user_coupon_pct: float | None = None,
) -> dict:
    """Async wrapper for _run_autocallable_sync."""
    return await asyncio.to_thread(
        _run_autocallable_sync,
        ticker,
        amount,
        duration_days,
        autocall_barrier_pct,
        downside_barrier_pct,
        frequency,
        user_interest_rate,
        user_coupon_pct,
    )
