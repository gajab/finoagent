"""Stock data fetching service — ported from fetch_stock.py.

All heavy computation is synchronous (yfinance, numpy, pandas) and is
wrapped in ``asyncio.to_thread`` so the FastAPI event-loop stays responsive.
"""

import asyncio
import json
import math
from datetime import datetime, timedelta, date

import numpy as np
import pandas as pd
import yfinance as yf
from scipy.stats import norm


# ---------------------------------------------------------------------------
# JSON-safe float sanitiser
# ---------------------------------------------------------------------------

def _coerce_date(value):
    """Best-effort convert a yfinance calendar value to a ``datetime.date``."""
    if value is None:
        return None
    # pandas Timestamp subclasses datetime, so this also handles Timestamps.
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (ValueError, TypeError):
        return None


def _san(obj):
    """Recursively replace nan/inf floats with None so JSON encoding never fails."""
    if isinstance(obj, dict):
        return {k: _san(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_san(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    # numpy scalar types
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, np.ndarray):
        return [_san(x) for x in obj.tolist()]
    return obj


# ---------------------------------------------------------------------------
# Helper formatters
# ---------------------------------------------------------------------------

def fmt_market_cap(val):
    if val is None:
        return "N/A"
    if val >= 1e12:
        return f"${val/1e12:.2f}T"
    if val >= 1e9:
        return f"${val/1e9:.2f}B"
    if val >= 1e6:
        return f"${val/1e6:.2f}M"
    return f"${val:,.0f}"


def fmt_large_num(val):
    if val is None:
        return "N/A"
    if abs(val) >= 1e12:
        return f"${val/1e12:.2f}T"
    if abs(val) >= 1e9:
        return f"${val/1e9:.2f}B"
    if abs(val) >= 1e6:
        return f"${val/1e6:.1f}M"
    return f"${val:,.0f}"


def safe_float(val, default=0.0):
    """Safely convert to float, handling NaN."""
    try:
        v = float(val)
        return default if math.isnan(v) or math.isinf(v) else v
    except Exception:
        return default


def safe_int(val, default=0):
    """Safely convert to int, handling NaN."""
    try:
        v = float(val)
        return default if math.isnan(v) or math.isinf(v) else int(v)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Technical analysis helpers
# ---------------------------------------------------------------------------

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return []
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0).astype(float)
    losses = np.where(deltas < 0, -deltas, 0).astype(float)

    # Wilder's smoothing
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    rsi_values = []
    for i in range(period, len(deltas)):
        if i == period:
            rs = avg_gain / (avg_loss + 1e-10)
        else:
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            rs = avg_gain / (avg_loss + 1e-10)
        rsi_values.append(round(100 - (100 / (1 + rs)), 2))
    return rsi_values


def find_support_resistance(highs, lows, closes, n=5):
    """Find support and resistance using swing highs/lows."""
    supports = []
    resistances = []

    for i in range(n, len(closes) - n):
        if lows[i] == min(lows[i - n : i + n + 1]):
            supports.append(lows[i])
        if highs[i] == max(highs[i - n : i + n + 1]):
            resistances.append(highs[i])

    support = (
        round(np.mean(supports[-3:]), 2)
        if supports
        else round(min(lows[-20:]) if len(lows) >= 20 else min(lows), 2)
    )
    resistance = (
        round(np.mean(resistances[-3:]), 2)
        if resistances
        else round(max(highs[-20:]) if len(highs) >= 20 else max(highs), 2)
    )
    return support, resistance


def analyze_volume_price(prices, volumes):
    """Analyze volume-price relationship for accumulation/distribution."""
    if len(prices) < 10 or len(volumes) < 10:
        return {"phase": "insufficient_data", "analysis": "Not enough data for volume analysis."}

    recent_prices = prices[-10:]
    recent_volumes = volumes[-10:]
    older_prices = prices[-20:-10] if len(prices) >= 20 else prices[: len(prices) // 2]
    older_volumes = volumes[-20:-10] if len(volumes) >= 20 else volumes[: len(volumes) // 2]

    avg_recent_vol = np.mean(recent_volumes)
    avg_older_vol = np.mean(older_volumes) if len(older_volumes) > 0 else avg_recent_vol
    vol_change = ((avg_recent_vol - avg_older_vol) / (avg_older_vol + 1e-10)) * 100

    price_trend = "rising" if recent_prices[-1] > recent_prices[0] else "falling"
    price_change_pct = ((recent_prices[-1] - recent_prices[0]) / (recent_prices[0] + 1e-10)) * 100
    vol_trend = "increasing" if vol_change > 10 else ("decreasing" if vol_change < -10 else "stable")

    if price_trend == "rising" and vol_trend == "increasing":
        phase = "accumulation"
        big_money = "Big money appears to be moving INTO the stock. Rising price with increasing volume signals institutional buying."
    elif price_trend == "rising" and vol_trend in ("decreasing", "stable"):
        phase = "weak_rally"
        big_money = "Caution: Price is rising but volume is not confirming. This could be a weak rally without strong institutional support."
    elif price_trend == "falling" and vol_trend == "increasing":
        phase = "distribution"
        big_money = "Big money appears to be EXITING the stock. Falling price with increasing volume signals institutional selling/distribution."
    elif price_trend == "falling" and vol_trend in ("decreasing", "stable"):
        phase = "weak_decline"
        big_money = "Selling pressure is declining. Low volume on price drops can indicate selling exhaustion — potential accumulation zone."
    else:
        phase = "neutral"
        big_money = "No clear institutional activity pattern detected."

    return {
        "phase": phase,
        "priceTrend": price_trend,
        "priceChangePct": round(price_change_pct, 2),
        "volumeTrend": vol_trend,
        "volumeChangePct": round(vol_change, 2),
        "avgRecentVolume": int(avg_recent_vol),
        "avgOlderVolume": int(avg_older_vol),
        "bigMoneyAnalysis": big_money,
    }


# ---------------------------------------------------------------------------
# News summary
# ---------------------------------------------------------------------------

def generate_news_summary(news_items):
    """Generate a structured summary from news items."""
    if not news_items:
        return "No recent news available for analysis."

    titles = [n["title"] for n in news_items]
    summaries = [n["summary"] for n in news_items if n.get("summary")]
    publishers = [n["publisher"] for n in news_items]

    pub_counts: dict[str, int] = {}
    for p in publishers:
        pub_counts[p] = pub_counts.get(p, 0) + 1

    all_text = " ".join(titles + summaries).lower()
    themes: list[str] = []
    theme_keywords = {
        "Earnings & Financials": ["earnings", "revenue", "profit", "eps", "quarterly", "results", "beat", "miss"],
        "AI & Technology": ["ai", "artificial intelligence", "machine learning", "tech", "innovation", "chatgpt"],
        "Market Movement": ["stock", "shares", "rally", "decline", "surge", "plunge", "drop", "gain", "bull", "bear"],
        "Analyst Coverage": ["analyst", "upgrade", "downgrade", "price target", "rating", "buy", "sell", "overweight"],
        "Dividends & Buybacks": ["dividend", "buyback", "repurchase", "shareholder", "payout"],
        "Regulatory & Legal": ["lawsuit", "regulation", "sec", "antitrust", "legal", "compliance", "court"],
        "M&A Activity": ["acquisition", "merger", "deal", "takeover", "partnership"],
        "Product & Strategy": ["launch", "product", "strategy", "expansion", "growth", "new"],
    }

    for theme, keywords in theme_keywords.items():
        if any(kw in all_text for kw in keywords):
            themes.append(theme)

    positive_words = ["beat", "surge", "gain", "rally", "upgrade", "growth", "strong", "record", "outperform", "bullish", "buy", "positive"]
    negative_words = ["miss", "decline", "drop", "plunge", "downgrade", "weak", "loss", "risk", "concern", "bearish", "sell", "negative", "fall"]

    pos_count = sum(1 for w in positive_words if w in all_text)
    neg_count = sum(1 for w in negative_words if w in all_text)

    if pos_count > neg_count + 2:
        sentiment = "Predominantly Positive"
    elif neg_count > pos_count + 2:
        sentiment = "Predominantly Negative"
    elif pos_count > neg_count:
        sentiment = "Slightly Positive"
    elif neg_count > pos_count:
        sentiment = "Slightly Negative"
    else:
        sentiment = "Mixed/Neutral"

    return json.dumps({
        "totalArticles": len(news_items),
        "themes": themes[:5],
        "sentiment": sentiment,
        "topHeadlines": titles[:3],
        "sourceBreakdown": pub_counts,
    })


# ---------------------------------------------------------------------------
# Earnings
# ---------------------------------------------------------------------------

def get_earnings_data(stock, info):
    """Get last reported quarterly earnings data."""
    earnings: dict = {
        "available": False,
        "lastQuarter": "",
        "reportedEPS": None,
        "estimatedEPS": None,
        "epsSurprise": None,
        "epsSurprisePct": None,
        "revenue": None,
        "revenueFormatted": "N/A",
        "netIncome": None,
        "netIncomeFormatted": "N/A",
        "quarterlyHistory": [],
    }

    try:
        eh = stock.earnings_history
        if eh is not None and not eh.empty:
            latest = eh.iloc[-1]
            earnings["available"] = True
            earnings["lastQuarter"] = str(eh.index[-1])
            earnings["reportedEPS"] = round(float(latest["epsActual"]), 2) if latest["epsActual"] is not None else None
            earnings["estimatedEPS"] = round(float(latest["epsEstimate"]), 2) if latest["epsEstimate"] is not None else None
            earnings["epsSurprise"] = round(float(latest["epsDifference"]), 2) if latest["epsDifference"] is not None else None
            earnings["epsSurprisePct"] = round(float(latest["surprisePercent"]) * 100, 2) if latest["surprisePercent"] is not None else None

            for idx, row in eh.iterrows():
                earnings["quarterlyHistory"].append({
                    "quarter": str(idx),
                    "epsActual": round(float(row["epsActual"]), 2) if row["epsActual"] is not None else None,
                    "epsEstimate": round(float(row["epsEstimate"]), 2) if row["epsEstimate"] is not None else None,
                    "surprisePct": round(float(row["surprisePercent"]) * 100, 2) if row["surprisePercent"] is not None else None,
                })
    except Exception:
        pass

    try:
        qi = stock.quarterly_income_stmt
        if qi is not None and not qi.empty:
            col = qi.columns[0]
            if not earnings["lastQuarter"]:
                earnings["lastQuarter"] = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)
                earnings["available"] = True
            for key in ["Total Revenue", "TotalRevenue"]:
                if key in qi.index:
                    val = qi.loc[key, col]
                    if val is not None and str(val) != "nan":
                        earnings["revenue"] = float(val)
                        earnings["revenueFormatted"] = fmt_large_num(float(val))
                    break
            for key in ["Net Income", "NetIncome"]:
                if key in qi.index:
                    val = qi.loc[key, col]
                    if val is not None and str(val) != "nan":
                        earnings["netIncome"] = float(val)
                        earnings["netIncomeFormatted"] = fmt_large_num(float(val))
                    break
            if earnings["reportedEPS"] is None:
                for key in ["Diluted EPS", "DilutedEPS", "Basic EPS", "BasicEPS"]:
                    if key in qi.index:
                        val = qi.loc[key, col]
                        if val is not None and str(val) != "nan":
                            earnings["reportedEPS"] = round(float(val), 2)
                        break
    except Exception:
        pass

    earnings["trailingPE"] = round(info.get("trailingPE", 0), 2) if info.get("trailingPE") else None
    earnings["forwardPE"] = round(info.get("forwardPE", 0), 2) if info.get("forwardPE") else None
    earnings["pegRatio"] = round(info.get("pegRatio", 0), 2) if info.get("pegRatio") else None
    earnings["sector"] = info.get("sector", "")
    earnings["industry"] = info.get("industry", "")

    # ---- Forward-Looking Guidance ----
    guidance: dict = {}

    # Forward EPS estimates
    fwd_eps = info.get("forwardEps")
    guidance["forwardEps"] = round(float(fwd_eps), 2) if fwd_eps is not None else None
    trailing_eps = info.get("trailingEps")
    guidance["trailingEps"] = round(float(trailing_eps), 2) if trailing_eps is not None else None

    # EPS growth: forward vs trailing
    if fwd_eps is not None and trailing_eps is not None and trailing_eps != 0:
        guidance["epsGrowthPct"] = round(((fwd_eps - trailing_eps) / abs(trailing_eps)) * 100, 2)
    else:
        guidance["epsGrowthPct"] = None

    # Current-year EPS estimate
    eps_cy = info.get("epsCurrentYear")
    guidance["epsCurrentYear"] = round(float(eps_cy), 2) if eps_cy is not None else None

    # Decompose the headline EPS growth into the two analyst steps it actually
    # spans.  The simple "forward vs trailing" number compares last-12-month
    # GAAP EPS against an *adjusted* analyst estimate for the next fiscal year,
    # so it conflates two things (a basis change + ~2 years of growth) and can
    # look far larger than the company's real per-year growth.  Exposing the
    # per-year steps lets the UI explain the jump instead of just showing it.
    if eps_cy is not None and trailing_eps not in (None, 0):
        guidance["epsCurrentYearGrowthPct"] = round(
            ((float(eps_cy) - float(trailing_eps)) / abs(float(trailing_eps))) * 100, 2
        )
    else:
        guidance["epsCurrentYearGrowthPct"] = None
    if fwd_eps is not None and eps_cy not in (None, 0):
        guidance["epsForwardGrowthPct"] = round(
            ((float(fwd_eps) - float(eps_cy)) / abs(float(eps_cy))) * 100, 2
        )
    else:
        guidance["epsForwardGrowthPct"] = None

    # Revenue growth estimate
    rev_growth = info.get("revenueGrowth")
    guidance["revenueGrowthPct"] = round(float(rev_growth) * 100, 2) if rev_growth is not None else None

    # Earnings growth estimate
    earn_growth = info.get("earningsGrowth")
    guidance["earningsGrowthPct"] = round(float(earn_growth) * 100, 2) if earn_growth is not None else None

    # Analyst price targets
    guidance["targetMeanPrice"] = round(float(info["targetMeanPrice"]), 2) if info.get("targetMeanPrice") else None
    guidance["targetHighPrice"] = round(float(info["targetHighPrice"]), 2) if info.get("targetHighPrice") else None
    guidance["targetLowPrice"] = round(float(info["targetLowPrice"]), 2) if info.get("targetLowPrice") else None
    guidance["targetMedianPrice"] = round(float(info["targetMedianPrice"]), 2) if info.get("targetMedianPrice") else None

    # Analyst consensus
    guidance["numberOfAnalysts"] = int(info["numberOfAnalystOpinions"]) if info.get("numberOfAnalystOpinions") else None
    rec_key = info.get("recommendationKey")
    guidance["recommendation"] = rec_key if rec_key else None  # "buy", "hold", etc.
    rec_mean = info.get("recommendationMean")
    guidance["recommendationScore"] = round(float(rec_mean), 2) if rec_mean is not None else None  # 1=Strong Buy, 5=Sell

    # Next earnings date.  yfinance's calendar is often stale — it keeps the
    # last *reported* date until the next one is scheduled, and can return a
    # range of estimated dates.  Pick the earliest date still in the future; if
    # every candidate is in the past, roll the most recent one forward by the
    # typical quarterly cadence and flag it as an estimate, so we never present a
    # past date labelled "Next Earnings".
    guidance["nextEarningsDate"] = None
    guidance["nextEarningsDateIsEstimated"] = False
    guidance["lastEarningsDate"] = None
    try:
        cal = stock.calendar
        raw_dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if raw_dates is not None and not isinstance(raw_dates, (list, tuple)):
            raw_dates = [raw_dates]
        candidates = [d for d in (_coerce_date(x) for x in (raw_dates or [])) if d is not None]
        if candidates:
            today = datetime.now().date()
            future = sorted(d for d in candidates if d >= today)
            past = sorted(d for d in candidates if d < today)
            if past:
                guidance["lastEarningsDate"] = past[-1].isoformat()
            if future:
                guidance["nextEarningsDate"] = future[0].isoformat()
                # A multi-date calendar entry is an estimated window, not a
                # confirmed date.
                guidance["nextEarningsDateIsEstimated"] = len(candidates) > 1
            else:
                # All known dates are stale → estimate the next report ~1 quarter
                # after the most recent one.
                est = past[-1]
                while est < today:
                    est += timedelta(days=91)
                guidance["nextEarningsDate"] = est.isoformat()
                guidance["nextEarningsDateIsEstimated"] = True
    except Exception:
        pass

    # Forward revenue estimate (per share)
    rev_per_share = info.get("revenuePerShare")
    guidance["revenuePerShare"] = round(float(rev_per_share), 2) if rev_per_share is not None else None

    # Profit margins for context
    guidance["profitMargin"] = round(float(info["profitMargins"]) * 100, 2) if info.get("profitMargins") else None
    guidance["operatingMargin"] = round(float(info["operatingMargins"]) * 100, 2) if info.get("operatingMargins") else None

    # Current price for target comparison
    cur_price = info.get("currentPrice") or info.get("regularMarketPrice")
    guidance["currentPrice"] = round(float(cur_price), 2) if cur_price is not None else None

    # Upside/downside to mean target
    if guidance["targetMeanPrice"] and guidance["currentPrice"] and guidance["currentPrice"] > 0:
        guidance["targetUpsidePct"] = round(
            ((guidance["targetMeanPrice"] - guidance["currentPrice"]) / guidance["currentPrice"]) * 100, 1
        )
    else:
        guidance["targetUpsidePct"] = None

    earnings["guidance"] = guidance

    return earnings


# ---------------------------------------------------------------------------
# Options helpers (Black-Scholes)
# ---------------------------------------------------------------------------

def bs_delta(S, K, T, r, sigma, option_type="call"):
    """Calculate Black-Scholes delta."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    if option_type == "call":
        return norm.cdf(d1)
    else:
        return norm.cdf(d1) - 1


def bs_prob_otm(S, K, T, r, sigma, option_type="call"):
    """Probability that option expires OTM (worthless)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d2 = (math.log(S / K) + (r - 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    if option_type == "call":
        return norm.cdf(-d2)
    else:
        return norm.cdf(d2)


def _bs_d1_d2(S, K, T, r, sigma):
    """Shared d1/d2 computation for Black-Scholes functions."""
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return d1, d2


def bs_price(S, K, T, r, sigma, option_type="call"):
    """Black-Scholes option price. Returns intrinsic value when T <= 0."""
    if S <= 0 or K <= 0:
        return 0.0
    if T <= 0:
        if option_type == "call":
            return max(0.0, S - K)
        return max(0.0, K - S)
    if sigma <= 0:
        sigma = 0.001
    d1, d2 = _bs_d1_d2(S, K, T, r, sigma)
    if option_type == "call":
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_gamma(S, K, T, r, sigma):
    """Black-Scholes gamma (same for calls and puts)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1, _ = _bs_d1_d2(S, K, T, r, sigma)
    return norm.pdf(d1) / (S * sigma * math.sqrt(T))


def bs_theta(S, K, T, r, sigma, option_type="call"):
    """Black-Scholes theta (per calendar day)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1, d2 = _bs_d1_d2(S, K, T, r, sigma)
    common = -(S * norm.pdf(d1) * sigma) / (2 * math.sqrt(T))
    if option_type == "call":
        annual = common - r * K * math.exp(-r * T) * norm.cdf(d2)
    else:
        annual = common + r * K * math.exp(-r * T) * norm.cdf(-d2)
    return annual / 365.0


def bs_vega(S, K, T, r, sigma):
    """Black-Scholes vega per 1% IV change (same for calls and puts)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1, _ = _bs_d1_d2(S, K, T, r, sigma)
    return S * norm.pdf(d1) * math.sqrt(T) / 100.0


def get_options_data(stock, info):
    """Fetch options data: IV, HV, put/call ratio, and trading opportunities."""
    price = info.get("currentPrice") or info.get("regularMarketPrice") or 0
    if not price:
        return {"available": False, "error": "No current price available"}

    risk_free_rate = 0.045

    try:
        expirations = list(stock.options)
    except Exception:
        return {"available": False, "error": "No options data available"}

    if not expirations:
        return {"available": False, "error": "No options expiration dates found"}

    # Historical Volatility (30-day)
    hv_30 = None
    hv_60 = None
    try:
        hist = stock.history(period="6mo", interval="1d")
        if hist is not None and len(hist) > 30:
            log_returns = np.log(hist["Close"] / hist["Close"].shift(1)).dropna()
            hv_30 = round(float(log_returns[-30:].std() * np.sqrt(252) * 100), 2)
            if len(log_returns) >= 60:
                hv_60 = round(float(log_returns[-60:].std() * np.sqrt(252) * 100), 2)
    except Exception:
        pass

    # Scan options chains
    total_put_oi = 0
    total_call_oi = 0
    total_put_vol = 0
    total_call_vol = 0
    all_ivs: list[float] = []

    csp_opportunities: list[dict] = []
    cc_opportunities: list[dict] = []

    today = datetime.now().date()
    min_dte = 7
    max_dte = 90
    min_annualized = 4.0
    min_prob_otm = 0.88

    for exp_str in expirations:
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        except Exception:
            continue
        dte = (exp_date - today).days
        if dte < 1:
            continue

        try:
            chain = stock.option_chain(exp_str)
        except Exception:
            continue

        puts = chain.puts
        calls = chain.calls

        # Aggregate OI and volume
        total_put_oi += puts["openInterest"].fillna(0).sum()
        total_call_oi += calls["openInterest"].fillna(0).sum()
        total_put_vol += puts["volume"].fillna(0).sum()
        total_call_vol += calls["volume"].fillna(0).sum()

        # Collect IVs for ATM options
        atm_puts = puts[(puts["strike"] >= price * 0.95) & (puts["strike"] <= price * 1.05)]
        atm_calls = calls[(calls["strike"] >= price * 0.95) & (calls["strike"] <= price * 1.05)]
        for _, row in atm_puts.iterrows():
            iv = row.get("impliedVolatility")
            if iv and iv > 0:
                all_ivs.append(iv)
        for _, row in atm_calls.iterrows():
            iv = row.get("impliedVolatility")
            if iv and iv > 0:
                all_ivs.append(iv)

        if dte < min_dte or dte > max_dte:
            continue

        T = dte / 365.0

        # Cash Secured Puts (OTM puts)
        otm_puts = puts[puts["strike"] < price]
        for _, row in otm_puts.iterrows():
            strike = safe_float(row["strike"])
            bid = safe_float(row.get("bid", 0))
            ask = safe_float(row.get("ask", 0))
            iv = safe_float(row.get("impliedVolatility", 0))
            oi = safe_int(row.get("openInterest", 0))
            vol = safe_int(row.get("volume", 0))
            mid = round((bid + ask) / 2, 2) if bid > 0 and ask > 0 else safe_float(row.get("lastPrice", 0))

            if mid <= 0 or iv <= 0:
                continue

            prob = bs_prob_otm(price, strike, T, risk_free_rate, iv, "put")
            delta = bs_delta(price, strike, T, risk_free_rate, iv, "put")

            annualized_return = (mid / strike) * (365 / dte) * 100
            otm_pct = round((price - strike) / price * 100, 2)

            if prob >= min_prob_otm and annualized_return >= min_annualized:
                csp_opportunities.append({
                    "expiration": exp_str,
                    "dte": dte,
                    "strike": strike,
                    "bid": bid,
                    "ask": ask,
                    "mid": mid,
                    "iv": round(iv * 100, 1),
                    "delta": round(delta, 3),
                    "probOTM": round(prob * 100, 1),
                    "annualizedReturn": round(annualized_return, 2),
                    "otmPct": otm_pct,
                    "openInterest": oi,
                    "volume": vol,
                    "capitalRequired": round(strike * 100, 2),
                    "premiumPer100": round(mid * 100, 2),
                })

        # Covered Calls (OTM calls)
        otm_calls = calls[calls["strike"] > price]
        for _, row in otm_calls.iterrows():
            strike = safe_float(row["strike"])
            bid = safe_float(row.get("bid", 0))
            ask = safe_float(row.get("ask", 0))
            iv = safe_float(row.get("impliedVolatility", 0))
            oi = safe_int(row.get("openInterest", 0))
            vol = safe_int(row.get("volume", 0))
            mid = round((bid + ask) / 2, 2) if bid > 0 and ask > 0 else safe_float(row.get("lastPrice", 0))

            if mid <= 0 or iv <= 0:
                continue

            prob = bs_prob_otm(price, strike, T, risk_free_rate, iv, "call")
            delta = bs_delta(price, strike, T, risk_free_rate, iv, "call")

            annualized_return = (mid / price) * (365 / dte) * 100
            otm_pct = round((strike - price) / price * 100, 2)
            upside = strike - price
            total_return_if_called = ((mid + upside) / price) * (365 / dte) * 100

            if prob >= min_prob_otm and annualized_return >= min_annualized:
                cc_opportunities.append({
                    "expiration": exp_str,
                    "dte": dte,
                    "strike": strike,
                    "bid": bid,
                    "ask": ask,
                    "mid": mid,
                    "iv": round(iv * 100, 1),
                    "delta": round(delta, 3),
                    "probOTM": round(prob * 100, 1),
                    "annualizedReturn": round(annualized_return, 2),
                    "totalReturnIfCalled": round(total_return_if_called, 2),
                    "otmPct": otm_pct,
                    "openInterest": oi,
                    "volume": vol,
                    "premiumPer100": round(mid * 100, 2),
                })

    # IV stats
    current_iv = round(np.mean(all_ivs) * 100, 2) if all_ivs else None
    iv_high = round(max(all_ivs) * 100, 2) if all_ivs else None
    iv_low = round(min(all_ivs) * 100, 2) if all_ivs else None

    # Put/Call ratio
    pc_ratio_oi = round(total_put_oi / max(total_call_oi, 1), 2)
    pc_ratio_vol = round(total_put_vol / max(total_call_vol, 1), 2)

    # Sort opportunities: best annualized return first
    csp_opportunities.sort(key=lambda x: x["annualizedReturn"], reverse=True)
    cc_opportunities.sort(key=lambda x: x["annualizedReturn"], reverse=True)

    # Limit to top 15 each
    csp_opportunities = csp_opportunities[:15]
    cc_opportunities = cc_opportunities[:15]

    return {
        "available": True,
        "currentPrice": round(price, 2),
        "expirationCount": len(expirations),
        "nearestExpiration": expirations[0] if expirations else "",
        "farthestExpiration": expirations[-1] if expirations else "",
        "iv": {
            "current": current_iv,
            "high": iv_high,
            "low": iv_low,
        },
        "hv30": hv_30,
        "hv60": hv_60,
        "putCallRatio": {
            "openInterest": pc_ratio_oi,
            "volume": pc_ratio_vol,
            "totalPutOI": int(total_put_oi),
            "totalCallOI": int(total_call_oi),
            "totalPutVol": int(total_put_vol),
            "totalCallVol": int(total_call_vol),
        },
        "cashSecuredPuts": csp_opportunities,
        "coveredCalls": cc_opportunities,
        "criteria": {
            "minAnnualizedReturn": min_annualized,
            "minProbOTM": min_prob_otm * 100,
            "minDTE": min_dte,
            "maxDTE": max_dte,
        },
    }


# ---------------------------------------------------------------------------
# Technical-analysis timeframe presets
# ---------------------------------------------------------------------------

TECHNICAL_TIMEFRAMES: dict[str, dict] = {
    # Day trading — ultra-short intraday
    "day_1m":       {"label": "Day Trading (1d / 1m)",         "period": "1d",   "interval": "1m"},
    "day_5m":       {"label": "Day Trading (1d / 5m)",         "period": "1d",   "interval": "5m"},
    "day_15m":      {"label": "Day Trading (1d / 15m)",        "period": "1d",   "interval": "15m"},
    "day_5d":       {"label": "Day Trading (5d / 5m)",         "period": "5d",   "interval": "5m"},
    # Default — short-term swing
    "short_term":   {"label": "Short Term (15-Day / 30-min)",  "period": "1mo",  "interval": "30m"},
    # Multi-day / weekly swing
    "swing":        {"label": "Swing (3mo / 1h)",              "period": "3mo",  "interval": "1h"},
    # Position trading — weeks to months
    "medium_term":  {"label": "Medium Term (6mo / 1d)",        "period": "6mo",  "interval": "1d"},
    # Long-term investor view
    "long_term":    {"label": "Long Term (5y / 1wk)",          "period": "5y",   "interval": "1wk"},
}
_DEFAULT_TIMEFRAME = "short_term"


def compute_technical_block(stock, timeframe: str = _DEFAULT_TIMEFRAME) -> dict:
    """Compute price/volume/RSI/support-resistance technical block for *timeframe*.

    Does NOT include MACD/Bollinger/SMA/EMA — those are always daily-based and
    are computed separately by the caller. Returns a dict matching the
    `TechnicalData` shape used by the frontend.
    """
    cfg = TECHNICAL_TIMEFRAMES.get(timeframe) or TECHNICAL_TIMEFRAMES[_DEFAULT_TIMEFRAME]
    period, interval = cfg["period"], cfg["interval"]

    technical: dict = {"timeframe": timeframe, "timeframeLabel": cfg["label"],
                       "period": period, "interval": interval}
    try:
        hist = stock.history(period=period, interval=interval)
        if hist is None or hist.empty:
            return {**technical, "error": "No price history available for this timeframe."}

        # Use date-only labels for daily/weekly intervals; include time for intraday
        is_intraday = interval.endswith("m") or interval.endswith("h")
        fmt = "%Y-%m-%d %H:%M" if is_intraday else "%Y-%m-%d"
        timestamps = [t.strftime(fmt) for t in hist.index]
        closes = hist["Close"].values.tolist()
        highs = hist["High"].values.tolist()
        lows = hist["Low"].values.tolist()
        volumes = hist["Volume"].values.tolist()

        rsi_values = calculate_rsi(np.array(closes))
        current_rsi = rsi_values[-1] if rsi_values else None

        if current_rsi is not None:
            if current_rsi > 70:
                rsi_signal = "OVERBOUGHT — RSI above 70 suggests the stock may be overextended. Watch for potential pullback."
            elif current_rsi > 60:
                rsi_signal = "BULLISH — RSI in 60-70 range shows strong momentum without being overbought."
            elif current_rsi > 40:
                rsi_signal = "NEUTRAL — RSI in 40-60 range indicates balanced momentum."
            elif current_rsi > 30:
                rsi_signal = "BEARISH — RSI in 30-40 range shows weakening momentum."
            else:
                rsi_signal = "OVERSOLD — RSI below 30 suggests the stock may be oversold. Watch for potential bounce."
        else:
            rsi_signal = "Insufficient data for RSI calculation."

        support, resistance = find_support_resistance(
            np.array(highs), np.array(lows), np.array(closes), n=3
        )
        vol_analysis = analyze_volume_price(closes, volumes)

        analysis_lines = []
        analysis_lines.append(f"RSI ({current_rsi:.1f}): {rsi_signal}" if current_rsi else "RSI: N/A")
        analysis_lines.append(f"Support Level: ${support:.2f} | Resistance Level: ${resistance:.2f}")
        current_price = closes[-1] if closes else 0
        if current_price and support and resistance:
            dist_to_support = ((current_price - support) / current_price) * 100
            dist_to_resist = ((resistance - current_price) / current_price) * 100
            analysis_lines.append(f"Price is {dist_to_support:.1f}% above support and {dist_to_resist:.1f}% below resistance.")
        analysis_lines.append(f"\nVolume Trend: {vol_analysis['volumeTrend'].title()} ({vol_analysis['volumeChangePct']:+.1f}%)")
        analysis_lines.append(f"Price Trend: {vol_analysis['priceTrend'].title()} ({vol_analysis['priceChangePct']:+.1f}%)")
        analysis_lines.append(f"Phase: {vol_analysis['phase'].replace('_', ' ').title()}")
        analysis_lines.append(f"\n{vol_analysis['bigMoneyAnalysis']}")

        technical.update({
            "timestamps": timestamps,
            "prices": [round(p, 2) for p in closes],
            "volumes": [int(v) for v in volumes],
            "highs": [round(h, 2) for h in highs],
            "lows": [round(l, 2) for l in lows],
            "rsiValues": rsi_values,
            "rsiTimestamps": timestamps[15:] if len(timestamps) > 15 else timestamps,
            "currentRSI": round(current_rsi, 2) if current_rsi else None,
            "rsiSignal": rsi_signal,
            "supportLevel": support,
            "resistanceLevel": resistance,
            "volumeAnalysis": vol_analysis,
            "analysisSummary": "\n".join(analysis_lines),
        })
        return _san(technical)
    except Exception as e:
        return {**technical, "error": str(e)}


def compute_momentum_indicators(stock) -> dict:
    """Compute MACD/Bollinger/SMA/EMA — always daily-based regardless of UI timeframe."""
    out: dict = {}
    try:
        daily_hist = stock.history(period="1y", interval="1d")
        if daily_hist is None or daily_hist.empty or len(daily_hist) < 26:
            return out
        daily_close = daily_hist["Close"].values

        ema12 = pd.Series(daily_close).ewm(span=12, adjust=False).mean().values
        ema26 = pd.Series(daily_close).ewm(span=26, adjust=False).mean().values
        macd_line = ema12 - ema26
        signal_line = pd.Series(macd_line).ewm(span=9, adjust=False).mean().values
        histogram = macd_line - signal_line

        macd_crossover = "none"
        if len(macd_line) >= 2 and len(signal_line) >= 2:
            if macd_line[-2] <= signal_line[-2] and macd_line[-1] > signal_line[-1]:
                macd_crossover = "bullish_crossover"
            elif macd_line[-2] >= signal_line[-2] and macd_line[-1] < signal_line[-1]:
                macd_crossover = "bearish_crossover"

        out["macd"] = {
            "macdLine": round(float(macd_line[-1]), 4),
            "signalLine": round(float(signal_line[-1]), 4),
            "histogram": round(float(histogram[-1]), 4),
            "signal": "bullish" if macd_line[-1] > signal_line[-1] else "bearish",
            "crossover": macd_crossover,
            "macdValues": [round(float(v), 4) for v in macd_line[-60:]],
            "signalValues": [round(float(v), 4) for v in signal_line[-60:]],
            "histogramValues": [round(float(v), 4) for v in histogram[-60:]],
            "timestamps": [t.strftime("%Y-%m-%d") for t in daily_hist.index[-60:]],
        }

        if len(daily_close) >= 20:
            sma20 = pd.Series(daily_close).rolling(window=20).mean().values
            std20 = pd.Series(daily_close).rolling(window=20).std().values
            upper_band = float(sma20[-1] + 2 * std20[-1])
            lower_band = float(sma20[-1] - 2 * std20[-1])
            band_width = upper_band - lower_band
            pct_b = (float(daily_close[-1]) - lower_band) / band_width if band_width > 0 else 0.5
            out["bollingerBands"] = {
                "upper": round(upper_band, 2),
                "middle": round(float(sma20[-1]), 2),
                "lower": round(lower_band, 2),
                "bandwidthPct": round(band_width / float(sma20[-1]) * 100, 2) if sma20[-1] > 0 else 0,
                "percentB": round(float(pct_b), 4),
                "position": "overbought" if pct_b > 1.0 else "oversold" if pct_b < 0.0 else "within_bands",
            }

        sma_data: dict = {}
        if len(daily_close) >= 50:
            sma50 = float(pd.Series(daily_close).rolling(window=50).mean().values[-1])
            sma_data["sma50"] = round(sma50, 2)
            sma_data["priceVsSma50"] = "above" if daily_close[-1] > sma50 else "below"
        if len(daily_close) >= 200:
            sma200 = float(pd.Series(daily_close).rolling(window=200).mean().values[-1])
            sma_data["sma200"] = round(sma200, 2)
            sma_data["priceVsSma200"] = "above" if daily_close[-1] > sma200 else "below"
            if "sma50" in sma_data:
                sma_data["goldenDeathCross"] = "golden_cross" if sma_data["sma50"] > sma200 else "death_cross"
        if sma_data:
            out["movingAverages"] = sma_data

        out["emaCrossover"] = {
            "ema12": round(float(ema12[-1]), 2),
            "ema26": round(float(ema26[-1]), 2),
            "signal": "bullish" if ema12[-1] > ema26[-1] else "bearish",
        }
    except Exception:
        pass
    return _san(out)


# ---------------------------------------------------------------------------
# Main synchronous fetch (mirrors original main())
# ---------------------------------------------------------------------------

def _fetch_stock_data_sync(ticker: str) -> dict:
    """Synchronous function that fetches all stock data for a given ticker."""
    ticker = ticker.upper()
    stock = yf.Ticker(ticker)

    # Basic info
    info = stock.info or {}
    company_name = info.get("longName") or info.get("shortName") or ticker
    price = info.get("currentPrice") or info.get("regularMarketPrice") or 0
    prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose") or 0
    change = price - prev_close if price and prev_close else 0
    change_pct = (change / prev_close * 100) if prev_close else 0
    market_cap = fmt_market_cap(info.get("marketCap"))
    fifty_two_week_high = info.get("fiftyTwoWeekHigh")
    fifty_two_week_low = info.get("fiftyTwoWeekLow")
    
    # Dividend Yield based on current price
    dividend_rate = info.get("dividendRate")
    if dividend_rate is not None and price > 0:
        dividend_yield = (dividend_rate / price) * 100
    else:
        dividend_yield = (info.get("trailingAnnualDividendYield", 0) or 0) * 100

    # ======= NEWS =======
    news_items: list[dict] = []
    try:
        news_raw = stock.news or []
        for item in news_raw[:15]:
            content = item.get("content", {})
            title = content.get("title", "")
            provider = content.get("provider", {})
            publisher = provider.get("displayName", "Unknown")
            pub_date_str = content.get("pubDate", "")
            canonical_url = content.get("canonicalUrl", {})
            link = canonical_url.get("url", "")
            summary = content.get("summary", "")
            if not title:
                continue
            pub_display = ""
            if pub_date_str:
                try:
                    dt = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
                    pub_display = dt.strftime("%b %d, %Y %I:%M %p")
                except Exception:
                    pub_display = pub_date_str
            news_items.append({
                "title": title,
                "publisher": publisher,
                "link": link,
                "published": pub_display,
                "summary": summary[:250] if summary else "",
            })
    except Exception:
        pass

    news_summary_raw = generate_news_summary(news_items[:10])

    # ======= ANALYST RATINGS =======
    analyst_ratings: list[dict] = []
    try:
        rec = stock.recommendations
        if rec is not None and not rec.empty:
            for _, row in rec.iterrows():
                analyst_ratings.append({
                    "period": str(row.get("period", "")),
                    "strongBuy": int(row.get("strongBuy", 0)),
                    "buy": int(row.get("buy", 0)),
                    "hold": int(row.get("hold", 0)),
                    "sell": int(row.get("sell", 0)),
                    "strongSell": int(row.get("strongSell", 0)),
                })
    except Exception:
        pass

    # ======= ANNUAL FINANCIALS =======
    years: list[str] = []
    eps_data: list = []
    revenue_data: list = []
    fcf_data: list = []
    try:
        income_stmt = stock.income_stmt
        cashflow = stock.cashflow
        if income_stmt is not None and not income_stmt.empty:
            cols = list(income_stmt.columns)[:4]
            cols.reverse()
            for col in cols:
                year_str = col.strftime("%Y") if hasattr(col, "strftime") else str(col)[:4]
                years.append(year_str)
                rev = None
                for key in ["Total Revenue", "TotalRevenue"]:
                    if key in income_stmt.index:
                        val = income_stmt.loc[key, col]
                        if val is not None and str(val) != "nan":
                            rev = float(val)
                        break
                revenue_data.append(rev)
                ep = None
                for key in ["Basic EPS", "BasicEPS", "Diluted EPS", "DilutedEPS"]:
                    if key in income_stmt.index:
                        val = income_stmt.loc[key, col]
                        if val is not None and str(val) != "nan":
                            ep = float(val)
                        break
                eps_data.append(ep)
                fc = None
                if cashflow is not None and not cashflow.empty and col in cashflow.columns:
                    for key in ["Free Cash Flow", "FreeCashFlow"]:
                        if key in cashflow.index:
                            val = cashflow.loc[key, col]
                            if val is not None and str(val) != "nan":
                                fc = float(val)
                            break
                fcf_data.append(fc)

            # ── Trailing-Twelve-Months point (sum of the last 4 quarters) ──
            # Appended as a final "TTM" bar so the charts show the most current
            # read alongside the fiscal-year history.
            try:
                qi = stock.quarterly_income_stmt
                qc = stock.quarterly_cashflow
                q_cols = list(qi.columns)[:4] if (qi is not None and not qi.empty) else []
                if len(q_cols) == 4:
                    def _ttm_sum(df, keys, cols):
                        for k in keys:
                            if df is not None and not df.empty and k in df.index:
                                vals = [float(df.loc[k, c]) for c in cols
                                        if c in df.columns and str(df.loc[k, c]) != "nan"]
                                return sum(vals) if len(vals) == len(cols) else None
                        return None
                    ttm_rev = _ttm_sum(qi, ["Total Revenue", "TotalRevenue"], q_cols)
                    ttm_eps = _ttm_sum(qi, ["Diluted EPS", "DilutedEPS", "Basic EPS", "BasicEPS"], q_cols)
                    qc_cols = list(qc.columns)[:4] if (qc is not None and not qc.empty) else []
                    ttm_fcf = _ttm_sum(qc, ["Free Cash Flow", "FreeCashFlow"], qc_cols) if len(qc_cols) == 4 else None
                    if ttm_rev is not None or ttm_eps is not None or ttm_fcf is not None:
                        years.append("TTM")
                        revenue_data.append(ttm_rev)
                        eps_data.append(round(ttm_eps, 2) if ttm_eps is not None else None)
                        fcf_data.append(ttm_fcf)
            except Exception:
                pass
    except Exception:
        pass

    # ======= TECHNICAL DATA (default: 15 days / 30-min) =======
    technical = compute_technical_block(stock, _DEFAULT_TIMEFRAME)

    # ======= MOMENTUM INDICATORS (always daily) =======
    technical.update(compute_momentum_indicators(stock))

    # ======= EARNINGS =======
    earnings = get_earnings_data(stock, info)

    # ======= OPTIONS & VOLATILITY =======
    options_data = get_options_data(stock, info)

    # ── Fund / ETF metadata (null for pure equities) ─────────────────────────
    def _safe_pct(v, threshold=1.5):
        if v is None:
            return None
        try:
            fv = float(v)
            return round(fv * 100, 2) if abs(fv) < threshold else round(fv, 2)
        except (TypeError, ValueError):
            return None

    tr = info.get("turnoverRatio")
    fund_info = {
        "quoteType": info.get("quoteType", "EQUITY"),
        "aumB": _san(info.get("totalAssets") or info.get("netAssets")),
        "fundYieldPct": _safe_pct(info.get("yield") or info.get("trailingAnnualDividendYield"), threshold=1.0),
        "ytdReturnPct": _safe_pct(info.get("ytdReturn"), threshold=1.5),
        "threeYrReturnPct": _safe_pct(info.get("threeYearAverageReturn"), threshold=1.5),
        "fiveYrReturnPct": _safe_pct(info.get("fiveYearAverageReturn"), threshold=1.5),
        "fundTurnoverPct": round(float(tr) * 100, 1) if tr is not None else None,
        "fundCategory": info.get("category") or "",
        "fundFamily": info.get("fundFamily") or "",
        "netExpenseRatioPct": _san(info.get("annualReportExpenseRatio") or info.get("totalExpenseRatio")),
        "morningstarRiskRating": info.get("morningStarRiskRating"),
        "morningstarOverallRating": info.get("morningStarOverallRating"),
        "fundInceptionDate": None,  # epoch conversion not critical for dashboard display
    }
    # Convert expense ratio to percent if given as a decimal (e.g. 0.0068 → 0.68)
    if fund_info["netExpenseRatioPct"] is not None:
        er = float(fund_info["netExpenseRatioPct"])
        fund_info["netExpenseRatioPct"] = round(er * 100, 3) if abs(er) < 1.0 else round(er, 3)

    result = {
        "ticker": ticker,
        "companyName": company_name,
        "price": round(price, 2) if price else 0,
        "change": round(change, 2),
        "changePercent": round(change_pct, 2),
        "marketCap": market_cap,
        "fiftyTwoWeekHigh": round(fifty_two_week_high, 2) if fifty_two_week_high else None,
        "fiftyTwoWeekLow": round(fifty_two_week_low, 2) if fifty_two_week_low else None,
        "dividendYield": round(dividend_yield, 2) if dividend_yield else 0,
        "sector": info.get("sector", ""),
        "industry": info.get("industry", ""),
        "description": info.get("longBusinessSummary", ""),
        "news": news_items,
        "newsSummary": news_summary_raw,
        "analystRatings": analyst_ratings,
        "financials": {
            "years": years,
            "eps": eps_data,
            "revenue": revenue_data,
            "freeCashFlow": fcf_data,
        },
        "technical": technical,
        "earnings": earnings,
        "options": options_data,
        **fund_info,
    }

    return _san(result)


# ---------------------------------------------------------------------------
# Public async entry-point
# ---------------------------------------------------------------------------

async def fetch_stock_data(ticker: str) -> dict:
    """Async wrapper — runs the heavy sync work in a thread pool."""
    return await asyncio.to_thread(_fetch_stock_data_sync, ticker)


# ---------------------------------------------------------------------------
# Fund / ETF details — holdings, returns, benchmark comparison
# ---------------------------------------------------------------------------

# Category → comparable ETF tickers (used for peer comparison)
_CATEGORY_PEERS: dict[str, list[str]] = {
    "large blend":          ["SPY", "IVV", "VOO"],
    "large growth":         ["QQQ", "VUG", "IWF"],
    "large value":          ["VTV", "IVE", "SCHV"],
    "mid blend":            ["IJH", "VO", "MDY"],
    "mid growth":           ["IWP", "VOT"],
    "mid value":            ["IJJ", "VOE"],
    "small blend":          ["IWM", "VB", "IJR"],
    "small growth":         ["IWO", "VBK"],
    "small value":          ["IWN", "VBR"],
    "technology":           ["QQQ", "VGT", "XLK"],
    "health":               ["XLV", "VHT", "IYH"],
    "financials":           ["XLF", "VFH", "IYF"],
    "energy":               ["XLE", "VDE", "IYE"],
    "consumer cyclical":    ["XLY", "VCR"],
    "consumer defensive":   ["XLP", "VDC"],
    "industrials":          ["XLI", "VIS"],
    "real estate":          ["VNQ", "IYR"],
    "utilities":            ["XLU", "VPU"],
    "communication services": ["XLC", "VOX"],
    "materials":            ["XLB", "VAW"],
    "bond":                 ["AGG", "BND", "TLT"],
    "intermediate bond":    ["AGG", "BND", "VCIT"],
    "short bond":           ["SHY", "BSV", "NEAR"],
    "long bond":            ["TLT", "EDV", "VGLT"],
    "international":        ["VEA", "IEFA", "EFA"],
    "emerging markets":     ["VWO", "EEM", "IEMG"],
    "world":                ["VT", "ACWI"],
    "allocation":           ["AOM", "AOR", "AOA"],
    "innovation":           ["ARKK", "ARKW", "ARKF"],
    "dividend":             ["VYM", "SCHD", "DVY"],
}

_ALWAYS_COMPARE = ["SPY", "QQQ"]  # always include as baselines


def _compute_period_return(hist: "pd.DataFrame", days: int) -> float | None:
    """Return % price change over the last `days` calendar days."""
    if hist is None or hist.empty:
        return None
    try:
        cutoff = hist.index[-1] - pd.Timedelta(days=days)
        subset = hist[hist.index >= cutoff]
        if len(subset) < 2:
            return None
        ret = (subset["Close"].iloc[-1] / subset["Close"].iloc[0] - 1) * 100
        return round(float(ret), 2)
    except Exception:
        return None


def _annualized_return(hist: "pd.DataFrame", years: int) -> float | None:
    """Return annualized % return over the last `years` years."""
    if hist is None or hist.empty:
        return None
    try:
        cutoff = hist.index[-1] - pd.Timedelta(days=365 * years)
        subset = hist[hist.index >= cutoff]
        if len(subset) < 2:
            return None
        total = float(subset["Close"].iloc[-1]) / float(subset["Close"].iloc[0])
        actual_years = (subset.index[-1] - subset.index[0]).days / 365.25
        if actual_years < 0.5:
            return None
        ann = (total ** (1 / actual_years) - 1) * 100
        return round(ann, 2)
    except Exception:
        return None


def _fetch_fund_details_sync(ticker: str) -> dict:
    ticker = ticker.upper()
    stock = yf.Ticker(ticker)
    info = stock.info or {}

    # ── Top holdings ─────────────────────────────────────────────────────────
    top_holdings: list[dict] = []
    try:
        th = stock.funds_data.top_holdings
        if th is not None and not th.empty:
            for sym, row in th.iterrows():
                top_holdings.append({
                    "ticker": str(sym),
                    "name": str(row.get("Name", sym)),
                    "weight_pct": round(float(row.get("Holding Percent", 0)) * 100, 2),
                })
    except Exception:
        pass

    # ── Sector weightings ────────────────────────────────────────────────────
    sector_weightings: dict = {}
    try:
        sw = stock.funds_data.sector_weightings
        if sw:
            sector_weightings = {k: round(float(v) * 100, 2) for k, v in sw.items() if v}
    except Exception:
        pass

    # ── Asset class breakdown ────────────────────────────────────────────────
    asset_classes: dict = {}
    try:
        ac = stock.funds_data.asset_classes
        if ac:
            asset_classes = {k: round(float(v) * 100, 2) for k, v in ac.items() if v}
    except Exception:
        pass

    # ── Fund operations (with category averages) ─────────────────────────────
    fund_ops: dict = {}
    try:
        fo = stock.funds_data.fund_operations
        if fo is not None and not fo.empty and ticker in fo.columns:
            col = fo[ticker]
            cat_col = fo.get("Category Average") if "Category Average" in fo.columns else None
            er = col.get("Annual Report Expense Ratio")
            tur = col.get("Annual Holdings Turnover")
            aum_raw = col.get("Total Net Assets")
            cat_er = cat_col.get("Annual Report Expense Ratio") if cat_col is not None else None
            cat_tur = cat_col.get("Annual Holdings Turnover") if cat_col is not None else None
            def _pct(v): return round(float(v) * 100, 3) if v is not None else None
            fund_ops = {
                "expense_ratio_pct": _pct(er),
                "category_avg_expense_ratio_pct": _pct(cat_er),
                "turnover_pct": _pct(tur),
                "category_avg_turnover_pct": _pct(cat_tur),
                "aum_m": round(float(aum_raw), 1) if aum_raw else None,
            }
    except Exception:
        pass

    # ── Historical returns ────────────────────────────────────────────────────
    hist_1y = hist_max = None
    try:
        hist_1y = stock.history(period="1y")
    except Exception:
        pass
    try:
        hist_max = stock.history(period="max")
    except Exception:
        pass

    returns = {
        "7d_pct":    _compute_period_return(hist_1y, 7),
        "1m_pct":    _compute_period_return(hist_1y, 30),
        "3m_pct":    _compute_period_return(hist_1y, 91),
        "1yr_pct":   _compute_period_return(hist_1y, 365),
        "3yr_ann":   _annualized_return(hist_max, 3),
        "5yr_ann":   _annualized_return(hist_max, 5),
        "10yr_ann":  _annualized_return(hist_max, 10),
        "ytd_pct":   _safe_pct_fund(info.get("ytdReturn"), 1.5),
    }

    # ── Benchmark / peer comparison ───────────────────────────────────────────
    category_raw = (info.get("category") or "").lower()
    peers = list({*_ALWAYS_COMPARE, *_CATEGORY_PEERS.get(category_raw, [])})
    peers = [p for p in peers if p != ticker][:6]

    benchmark_comparison: list[dict] = []
    for peer in peers:
        try:
            pt = yf.Ticker(peer)
            pinfo = pt.info or {}
            phist = pt.history(period="1y")
            phist_max = pt.history(period="max")
            benchmark_comparison.append({
                "ticker": peer,
                "name": pinfo.get("longName") or pinfo.get("shortName") or peer,
                "7d_pct":   _compute_period_return(phist, 7),
                "1m_pct":   _compute_period_return(phist, 30),
                "3m_pct":   _compute_period_return(phist, 91),
                "1yr_pct":  _compute_period_return(phist, 365),
                "3yr_ann":  _annualized_return(phist_max, 3),
                "5yr_ann":  _annualized_return(phist_max, 5),
                "10yr_ann": _annualized_return(phist_max, 10),
                "ytd_pct":  _safe_pct_fund(pinfo.get("ytdReturn"), 1.5),
                "expense_ratio_pct": _safe_pct_er(pinfo.get("annualReportExpenseRatio") or pinfo.get("totalExpenseRatio")),
                "aum_b": _fmt_aum(pinfo.get("totalAssets") or pinfo.get("netAssets")),
            })
        except Exception:
            continue

    # ── Fund overview / manager context (for LLM) ────────────────────────────
    fund_overview: dict = {}
    try:
        fo_dict = stock.funds_data.fund_overview
        if fo_dict:
            fund_overview = fo_dict
    except Exception:
        pass

    return _san({
        "ticker": ticker,
        "company_name": info.get("longName") or info.get("shortName") or ticker,
        "fund_family": info.get("fundFamily") or fund_overview.get("family") or "",
        "legal_type": info.get("legalType") or fund_overview.get("legalType") or "",
        "category": info.get("category") or fund_overview.get("categoryName") or "",
        "top_holdings": top_holdings,
        "sector_weightings": sector_weightings,
        "asset_classes": asset_classes,
        "fund_ops": fund_ops,
        "returns": returns,
        "benchmark_comparison": benchmark_comparison,
    })


def _safe_pct_fund(v, threshold=1.5):
    if v is None:
        return None
    try:
        fv = float(v)
        return round(fv * 100, 2) if abs(fv) < threshold else round(fv, 2)
    except (TypeError, ValueError):
        return None


def _safe_pct_er(v):
    """Expense ratio: convert decimal to % if < 1."""
    if v is None:
        return None
    try:
        fv = float(v)
        return round(fv * 100, 3) if abs(fv) < 1.0 else round(fv, 3)
    except (TypeError, ValueError):
        return None


def _fmt_aum(v) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v) / 1e9, 2)
    except (TypeError, ValueError):
        return None


async def fetch_fund_details(ticker: str) -> dict:
    """Async wrapper for fund details fetch."""
    return await asyncio.to_thread(_fetch_fund_details_sync, ticker)
