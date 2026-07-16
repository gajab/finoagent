"""Long/Short Strategy Service.

Three variants:
1. 130/30 Enhanced Equity — Multi-stock long/short portfolio with scenario analysis & tax projections
2. Single-Stock Long/Short — Long the stock, hedge with options or inverse ETFs
3. Pair Trade — Long one stock, short a correlated stock/sector ETF to isolate alpha
"""

import asyncio
import math
import logging
from datetime import datetime, date
from typing import Optional

import yfinance as yf
import numpy as np
from scipy.stats import pearsonr, skew, kurtosis

logger = logging.getLogger(__name__)


def _safe_float(v, default=None):
    """Convert to float, replacing NaN/Inf with default."""
    if v is None:
        return default
    f = float(v)
    if math.isnan(f) or math.isinf(f):
        return default
    return f


def _sanitize_list(lst):
    """Replace NaN/Inf values in a list with None (JSON-safe)."""
    return [None if (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) else v for v in lst]


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else default
    except (TypeError, ValueError):
        return default


# Sector → ETF mapping for hedging
SECTOR_ETFS = {
    "Technology": ["XLK", "VGT", "QQQ"],
    "Communication Services": ["XLC", "VOX"],
    "Healthcare": ["XLV", "VHT", "IBB"],
    "Financial Services": ["XLF", "VFH", "KBE"],
    "Financials": ["XLF", "VFH", "KBE"],
    "Consumer Cyclical": ["XLY", "VCR"],
    "Consumer Defensive": ["XLP", "VDC"],
    "Energy": ["XLE", "VDE", "OIH"],
    "Industrials": ["XLI", "VIS"],
    "Basic Materials": ["XLB", "VAW"],
    "Materials": ["XLB", "VAW"],
    "Real Estate": ["XLRE", "VNQ", "IYR"],
    "Utilities": ["XLU", "VPU"],
}


def _get_stock_info(ticker: str) -> dict:
    """Fetch basic info for a ticker."""
    stock = yf.Ticker(ticker)
    info = stock.info or {}
    price = info.get("currentPrice") or info.get("regularMarketPrice") or 0
    return {
        "ticker": ticker.upper(),
        "name": info.get("shortName") or info.get("longName") or ticker.upper(),
        "price": round(float(price), 2) if price else None,
        "sector": info.get("sector", ""),
        "industry": info.get("industry", ""),
        "market_cap": info.get("marketCap"),
        "beta": _safe_float(info.get("beta"), None),
        "pe_ratio": _safe_float(info.get("trailingPE"), None),
        "forward_pe": _safe_float(info.get("forwardPE"), None),
        "dividend_yield": _safe_float(info.get("dividendYield"), 0) * 100,
        "52w_high": _safe_float(info.get("fiftyTwoWeekHigh"), None),
        "52w_low": _safe_float(info.get("fiftyTwoWeekLow"), None),
    }


def _compute_correlation(ticker1: str, ticker2: str, period: str = "1y") -> dict:
    """Compute correlation and beta between two tickers."""
    try:
        t1 = yf.Ticker(ticker1)
        t2 = yf.Ticker(ticker2)

        h1 = t1.history(period=period, interval="1d")
        h2 = t2.history(period=period, interval="1d")

        if h1.empty or h2.empty or len(h1) < 30 or len(h2) < 30:
            return {"correlation": None, "hedge_ratio": None, "error": "Insufficient data"}

        # Align dates
        r1 = h1["Close"].pct_change().dropna()
        r2 = h2["Close"].pct_change().dropna()

        common_idx = r1.index.intersection(r2.index)
        if len(common_idx) < 30:
            return {"correlation": None, "hedge_ratio": None, "error": "Insufficient overlapping data"}

        r1 = r1.loc[common_idx]
        r2 = r2.loc[common_idx]

        corr, p_value = pearsonr(r1.values, r2.values)

        # Hedge ratio (beta of stock vs hedge)
        cov = np.cov(r1.values, r2.values)
        hedge_ratio = cov[0, 1] / cov[1, 1] if cov[1, 1] != 0 else 1.0

        # Price history for chart
        prices1 = h1["Close"].values.tolist()
        prices2 = h2["Close"].values.tolist()
        timestamps = [d.strftime("%Y-%m-%d") for d in h1.index]

        # Normalized performance (base 100)
        base1 = h1["Close"].iloc[0] if h1["Close"].iloc[0] != 0 else 1
        base2 = h2["Close"].iloc[0] if h2["Close"].iloc[0] != 0 else 1
        norm1 = _sanitize_list((h1["Close"] / base1 * 100).round(2).tolist())
        norm2 = _sanitize_list((h2["Close"] / base2 * 100).round(2).tolist())
        timestamps2 = [d.strftime("%Y-%m-%d") for d in h2.index]

        # Spread (normalized difference)
        common_dates = h1.index.intersection(h2.index)
        h1_common = h1.loc[common_dates, "Close"]
        h2_common = h2.loc[common_dates, "Close"]
        base1c = h1_common.iloc[0] if len(h1_common) > 0 and h1_common.iloc[0] != 0 else 1
        base2c = h2_common.iloc[0] if len(h2_common) > 0 and h2_common.iloc[0] != 0 else 1
        n1 = h1_common / base1c * 100
        n2 = h2_common / base2c * 100
        spread = _sanitize_list((n1 - n2).round(2).tolist())
        spread_timestamps = [d.strftime("%Y-%m-%d") for d in common_dates]

        spread_mean = _safe_float(np.mean([v for v in spread if v is not None]), 0)
        spread_std = _safe_float(np.std([v for v in spread if v is not None]), 0)
        spread_current = _safe_float(spread[-1], 0) if spread else 0
        z_score = round((spread_current - spread_mean) / spread_std, 2) if spread_std and spread_std > 0 else 0

        return {
            "correlation": _safe_float(corr, 0),
            "p_value": _safe_float(p_value, 1.0),
            "hedge_ratio": _safe_float(hedge_ratio, 1.0),
            "data_points": len(common_idx),
            "price_history_1": {
                "timestamps": timestamps,
                "prices": _sanitize_list([round(float(p), 2) for p in prices1]),
                "normalized": norm1,
            },
            "price_history_2": {
                "timestamps": timestamps2,
                "prices": _sanitize_list([round(float(p), 2) for p in prices2]),
                "normalized": norm2,
            },
            "spread": {
                "timestamps": spread_timestamps,
                "values": spread,
                "mean": round(spread_mean, 2),
                "std": round(spread_std, 2),
                "current": round(spread_current, 2),
                "z_score": z_score,
            },
        }
    except Exception as e:
        logger.warning(f"Correlation computation failed: {e}")
        return {"correlation": None, "hedge_ratio": None, "error": str(e)}


def _build_single_stock_strategy(
    ticker: str,
    investment_amount: float,
    hedge_type: str,  # "sector_etf" or "protective_put" or "collar"
) -> dict:
    """Build a single-stock long/short strategy."""
    stock_info = _get_stock_info(ticker)
    if not stock_info["price"]:
        return {"error": f"Cannot fetch price for {ticker}"}

    sector = stock_info["sector"]
    sector_etfs = SECTOR_ETFS.get(sector, [])

    # Get sector ETF info
    hedge_candidates = []
    for etf_ticker in sector_etfs[:3]:
        try:
            etf_info = _get_stock_info(etf_ticker)
            corr_data = _compute_correlation(ticker, etf_ticker)
            hedge_candidates.append({
                **etf_info,
                **corr_data,
            })
        except Exception as e:
            logger.warning(f"Failed to get ETF info for {etf_ticker}: {e}")

    # Calculate position sizing
    shares_long = int(investment_amount / stock_info["price"])
    long_value = shares_long * stock_info["price"]

    # For each hedge candidate, calculate short position
    strategies = []
    for candidate in hedge_candidates:
        if not candidate.get("price") or not candidate.get("hedge_ratio"):
            continue

        hedge_ratio = candidate["hedge_ratio"]
        # Shares to short = (long_value * hedge_ratio) / hedge_price
        short_value = long_value * abs(hedge_ratio)
        shares_short = int(short_value / candidate["price"])
        actual_short_value = shares_short * candidate["price"]

        strategies.append({
            "hedge_ticker": candidate["ticker"],
            "hedge_name": candidate["name"],
            "hedge_price": candidate["price"],
            "correlation": candidate.get("correlation"),
            "hedge_ratio": hedge_ratio,
            "shares_long": shares_long,
            "long_value": round(long_value, 2),
            "shares_short": shares_short,
            "short_value": round(actual_short_value, 2),
            "net_exposure": round(long_value - actual_short_value, 2),
            "gross_exposure": round(long_value + actual_short_value, 2),
            "price_history_1": candidate.get("price_history_1"),
            "price_history_2": candidate.get("price_history_2"),
            "spread": candidate.get("spread"),
        })

    # Risk metrics for the base stock
    stock = yf.Ticker(ticker)
    raw_info = stock.info or {}
    quant_analytics = None
    try:
        hist = stock.history(period="1y", interval="1d")
        returns = hist["Close"].pct_change().dropna()
        volatility = _safe_float(returns.std() * np.sqrt(252) * 100)
        max_drawdown = _safe_float(((hist["Close"] / hist["Close"].cummax()) - 1).min() * 100)
        sharpe = _safe_float((returns.mean() * 252) / (returns.std() * np.sqrt(252))) if returns.std() > 0 else 0

        # Quant analytics (use raw yfinance info for factor scoring)
        try:
            spy = yf.Ticker("SPY")
            spy_hist = spy.history(period="1y", interval="1d")
            market_returns = spy_hist["Close"].pct_change().dropna()
            # Align dates
            common = returns.index.intersection(market_returns.index)
            aligned_returns = returns.loc[common]
            aligned_market = market_returns.loc[common]

            # Get spread z-score from first strategy if available
            spread_z = None
            if strategies:
                spread_data = strategies[0].get("spread")
                if isinstance(spread_data, dict):
                    spread_z = spread_data.get("z_score")
                elif isinstance(spread_data, list) and len(spread_data) > 1:
                    spread_arr = np.array(spread_data)
                    if spread_arr.std() > 0:
                        spread_z = float((spread_arr[-1] - spread_arr.mean()) / spread_arr.std())

            quant_analytics = _build_quant_analytics(
                ticker, hist, raw_info, aligned_returns, aligned_market,
                investment_amount, spread_z
            )
        except Exception as e:
            logger.warning(f"Failed to compute quant analytics for {ticker}: {e}")
    except Exception:
        volatility = None
        max_drawdown = None
        sharpe = None

    return {
        "type": "single_stock",
        "ticker": ticker.upper(),
        "stock_info": stock_info,
        "investment_amount": investment_amount,
        "strategies": strategies,
        "stock_metrics": {
            "annualized_volatility": round(volatility, 2) if volatility else None,
            "max_drawdown_1y": round(max_drawdown, 2) if max_drawdown else None,
            "sharpe_ratio_1y": round(sharpe, 2) if sharpe else None,
        },
        "sector_etfs_available": sector_etfs,
        "quant_analytics": quant_analytics,
    }


def _build_pair_trade(
    long_ticker: str,
    short_ticker: str,
    investment_amount: float,
) -> dict:
    """Build a pair trade strategy."""
    long_info = _get_stock_info(long_ticker)
    short_info = _get_stock_info(short_ticker)

    if not long_info["price"]:
        return {"error": f"Cannot fetch price for {long_ticker}"}
    if not short_info["price"]:
        return {"error": f"Cannot fetch price for {short_ticker}"}

    # Compute correlation and hedge ratio
    corr_data = _compute_correlation(long_ticker, short_ticker)

    hedge_ratio = abs(corr_data.get("hedge_ratio", 1.0)) if corr_data.get("hedge_ratio") else 1.0

    # Position sizing
    shares_long = int(investment_amount / long_info["price"])
    long_value = shares_long * long_info["price"]

    short_value = long_value * hedge_ratio
    shares_short = int(short_value / short_info["price"])
    actual_short_value = shares_short * short_info["price"]

    # Risk metrics + quant analytics for both
    metrics = {}
    quant_long = None
    quant_short = None

    # Get spread z-score for signals (spread is a dict with 'z_score' pre-computed)
    spread_z = None
    spread_data = corr_data.get("spread")
    if isinstance(spread_data, dict):
        spread_z = spread_data.get("z_score")
    elif isinstance(spread_data, list) and len(spread_data) > 1:
        spread_arr = np.array(spread_data)
        if spread_arr.std() > 0:
            spread_z = float((spread_arr[-1] - spread_arr.mean()) / spread_arr.std())

    # Fetch market returns once for both legs
    try:
        spy = yf.Ticker("SPY")
        spy_hist = spy.history(period="1y", interval="1d")
        market_returns = spy_hist["Close"].pct_change().dropna()
    except Exception:
        market_returns = None

    for t, label, _stock_info in [(long_ticker, "long", long_info), (short_ticker, "short", short_info)]:
        try:
            stock = yf.Ticker(t)
            raw_info = stock.info or {}
            hist = stock.history(period="1y", interval="1d")
            returns = hist["Close"].pct_change().dropna()
            vol = float(returns.std() * np.sqrt(252) * 100)
            max_dd = float(((hist["Close"] / hist["Close"].cummax()) - 1).min() * 100)
            metrics[label] = {
                "annualized_volatility": round(vol, 2),
                "max_drawdown_1y": round(max_dd, 2),
            }

            # Quant analytics per leg (use raw yfinance info for factor scoring)
            if market_returns is not None:
                try:
                    common = returns.index.intersection(market_returns.index)
                    aligned_returns = returns.loc[common]
                    aligned_market = market_returns.loc[common]
                    qa = _build_quant_analytics(
                        t, hist, raw_info, aligned_returns, aligned_market,
                        investment_amount, spread_z
                    )
                    if label == "long":
                        quant_long = qa
                    else:
                        quant_short = qa
                except Exception as e:
                    logger.warning(f"Failed to compute quant analytics for {t}: {e}")
        except Exception:
            metrics[label] = {"annualized_volatility": None, "max_drawdown_1y": None}

    # Pair-level analytics: relative factor edge
    pair_analytics = None
    if quant_long and quant_short:
        try:
            long_fs = quant_long["factor_scores"]
            short_fs = quant_short["factor_scores"]
            pair_analytics = {
                "relative_value": round(long_fs.get("value", 50) - short_fs.get("value", 50), 1),
                "relative_momentum": round(long_fs.get("momentum", 50) - short_fs.get("momentum", 50), 1),
                "relative_quality": round(long_fs.get("quality", 50) - short_fs.get("quality", 50), 1),
                "relative_composite": round(long_fs.get("composite", 50) - short_fs.get("composite", 50), 1),
                "spread_z_score": round(spread_z, 2) if spread_z is not None else None,
                "long_entry_signal": quant_long.get("signals", {}).get("entry_signal"),
                "short_entry_signal": quant_short.get("signals", {}).get("entry_signal"),
            }
        except Exception:
            pass

    return {
        "type": "pair_trade",
        "long_ticker": long_ticker.upper(),
        "short_ticker": short_ticker.upper(),
        "long_info": long_info,
        "short_info": short_info,
        "correlation": corr_data.get("correlation"),
        "hedge_ratio": round(hedge_ratio, 4),
        "investment_amount": investment_amount,
        "shares_long": shares_long,
        "long_value": round(long_value, 2),
        "shares_short": shares_short,
        "short_value": round(actual_short_value, 2),
        "net_exposure": round(long_value - actual_short_value, 2),
        "gross_exposure": round(long_value + actual_short_value, 2),
        "price_history": corr_data.get("price_history_1"),
        "price_history_2": corr_data.get("price_history_2"),
        "spread": corr_data.get("spread"),
        "long_metrics": metrics.get("long", {}),
        "short_metrics": metrics.get("short", {}),
        "quant_analytics_long": quant_long,
        "quant_analytics_short": quant_short,
        "pair_analytics": pair_analytics,
    }


async def run_single_stock_long_short(
    ticker: str,
    investment_amount: float,
    hedge_type: str = "sector_etf",
) -> dict:
    """Async wrapper for single-stock long/short."""
    return await asyncio.to_thread(
        _build_single_stock_strategy,
        ticker,
        investment_amount,
        hedge_type,
    )


async def run_pair_trade(
    long_ticker: str,
    short_ticker: str,
    investment_amount: float,
) -> dict:
    """Async wrapper for pair trade strategy."""
    return await asyncio.to_thread(
        _build_pair_trade,
        long_ticker,
        short_ticker,
        investment_amount,
    )


# =========================================================================
# Pair-trade candidate suggestions
# =========================================================================

# Well-known competitor groups for popular sectors / industries.
# Each entry is a group of stocks that are commonly traded against each other.
COMPETITOR_GROUPS: list[list[str]] = [
    # Big Tech
    ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA"],
    # Semiconductors
    ["NVDA", "AMD", "INTC", "AVGO", "QCOM", "MU", "TXN"],
    # Cloud / SaaS
    ["CRM", "NOW", "SNOW", "DDOG", "NET", "PLTR", "ZS"],
    # E-Commerce / Retail
    ["AMZN", "WMT", "COST", "TGT", "EBAY", "ETSY"],
    # Social Media / Advertising
    ["META", "SNAP", "PINS", "GOOG", "TTD"],
    # Streaming / Media
    ["NFLX", "DIS", "WBD", "PARA", "CMCSA"],
    # Banks
    ["JPM", "BAC", "WFC", "C", "GS", "MS", "USB"],
    # Payments / Fintech
    ["V", "MA", "PYPL", "SQ", "FIS", "FISV"],
    # Pharma / Biotech
    ["JNJ", "PFE", "MRK", "ABBV", "LLY", "BMY", "AMGN"],
    # Oil / Energy
    ["XOM", "CVX", "COP", "SLB", "EOG", "OXY", "DVN"],
    # Airlines
    ["DAL", "UAL", "LUV", "AAL", "ALK", "JBLU"],
    # Auto
    ["TSLA", "F", "GM", "RIVN", "LCID", "TM", "HMC"],
    # Telecom
    ["T", "VZ", "TMUS"],
    # Consumer Staples
    ["PG", "KO", "PEP", "CL", "MDLZ", "GIS", "KHC"],
    # Restaurants / Food
    ["MCD", "SBUX", "CMG", "YUM", "QSR", "DPZ"],
    # Real Estate / REITs
    ["AMT", "PLD", "EQIX", "SPG", "O", "DLR"],
    # Industrials
    ["CAT", "DE", "HON", "GE", "MMM", "BA", "RTX"],
    # Cybersecurity
    ["CRWD", "PANW", "FTNT", "ZS", "S", "OKTA"],
]


def _suggest_pair_candidates(ticker: str, max_candidates: int = 6) -> dict:
    """Suggest pair-trade short candidates for a given long ticker.

    Strategy:
    1. Look for the ticker in well-known competitor groups.
    2. Fetch the ticker's sector and find same-sector stocks via yfinance.
    3. Compute correlations and rank candidates.
    """
    ticker = ticker.upper()
    candidates_set: set[str] = set()

    # Step 1 — known competitor groups
    for group in COMPETITOR_GROUPS:
        upper_group = [t.upper() for t in group]
        if ticker in upper_group:
            for t in upper_group:
                if t != ticker:
                    candidates_set.add(t)

    # Step 2 — sector-based ETF peers
    try:
        stock = yf.Ticker(ticker)
        info = stock.info or {}
        sector = info.get("sector", "")
        industry = info.get("industry", "")
    except Exception:
        sector = ""
        industry = ""

    # Add sector ETFs as potential candidates
    sector_etfs = SECTOR_ETFS.get(sector, [])
    for etf in sector_etfs:
        candidates_set.add(etf.upper())

    # Discard the ticker itself
    candidates_set.discard(ticker)

    if not candidates_set:
        return {
            "ticker": ticker,
            "sector": sector,
            "industry": industry,
            "candidates": [],
            "message": "No peer candidates found for this ticker.",
        }

    # Step 3 — Compute quick correlations for each candidate
    scored: list[dict] = []
    for candidate_ticker in candidates_set:
        try:
            corr_data = _compute_correlation(ticker, candidate_ticker, period="1y")
            corr_val = corr_data.get("correlation")
            if corr_val is None:
                continue
            cand_info = _get_stock_info(candidate_ticker)
            scored.append({
                "ticker": candidate_ticker,
                "name": cand_info.get("name", candidate_ticker),
                "price": cand_info.get("price"),
                "sector": cand_info.get("sector", ""),
                "industry": cand_info.get("industry", ""),
                "market_cap": cand_info.get("market_cap"),
                "correlation": round(float(corr_val), 4),
                "hedge_ratio": round(float(abs(corr_data.get("hedge_ratio", 1.0))), 4),
            })
        except Exception as e:
            logger.debug(f"Skipping candidate {candidate_ticker}: {e}")
            continue

    # Sort by absolute correlation descending (higher = better pair candidate)
    scored.sort(key=lambda x: abs(x["correlation"]), reverse=True)
    top = scored[:max_candidates]

    return {
        "ticker": ticker,
        "sector": sector,
        "industry": industry,
        "candidates": top,
    }


async def run_pair_suggestions(ticker: str, max_candidates: int = 6) -> dict:
    """Async wrapper for pair-trade candidate suggestions."""
    return await asyncio.to_thread(_suggest_pair_candidates, ticker, max_candidates)


# ---------------------------------------------------------------------------
# AQR Flex-style Quant Analytics
# ---------------------------------------------------------------------------

def _compute_factor_scores(ticker: str, hist, info: dict) -> dict:
    """Compute AQR-style factor scores (0-100) using yfinance data."""
    scores = {}

    # --- Value Score ---
    pe = info.get("trailingPE") or info.get("forwardPE")
    pb = info.get("priceToBook")
    div_yield = info.get("dividendYield") or 0
    ev_ebitda = info.get("enterpriseToEbitda")

    value_components = []
    if pe and pe > 0:
        # Lower P/E = better value. Score: 100 * (1 - min(pe, 60)/60)
        value_components.append(max(0, 100 * (1 - min(pe, 60) / 60)))
    if pb and pb > 0:
        value_components.append(max(0, 100 * (1 - min(pb, 10) / 10)))
    if div_yield > 0:
        value_components.append(min(100, div_yield * 100 / 0.05))  # 5% yield = 100
    if ev_ebitda and ev_ebitda > 0:
        value_components.append(max(0, 100 * (1 - min(ev_ebitda, 30) / 30)))
    scores["value"] = round(sum(value_components) / len(value_components), 1) if value_components else 50.0

    # --- Momentum Score (AQR: 12-1 month, skip last month) ---
    closes = hist["Close"]
    mom_components = []
    if len(closes) >= 252:
        # 12-1 month momentum: return from 252 days ago to 21 days ago
        mom_12_1 = (closes.iloc[-21] / closes.iloc[-252] - 1) * 100
        mom_components.append(min(100, max(0, 50 + mom_12_1 * 2)))  # Center at 50
    if len(closes) >= 63:
        mom_3m = (closes.iloc[-1] / closes.iloc[-63] - 1) * 100
        mom_components.append(min(100, max(0, 50 + mom_3m * 3)))
    if len(closes) >= 126:
        mom_6m = (closes.iloc[-1] / closes.iloc[-126] - 1) * 100
        mom_components.append(min(100, max(0, 50 + mom_6m * 2)))
    scores["momentum"] = round(sum(mom_components) / len(mom_components), 1) if mom_components else 50.0

    # --- Quality Score (AQR QMJ: profitability, stability, leverage) ---
    roe = info.get("returnOnEquity")
    gross_margin = info.get("grossMargins")
    debt_equity = info.get("debtToEquity")
    profit_margin = info.get("profitMargins")

    quality_components = []
    if roe is not None:
        quality_components.append(min(100, max(0, roe * 100 / 0.30 * 100 / 100)))  # 30% ROE = 100
    if gross_margin is not None:
        quality_components.append(min(100, gross_margin * 100 / 0.60 * 100 / 100))  # 60% margin = 100
    if debt_equity is not None and debt_equity >= 0:
        quality_components.append(max(0, 100 * (1 - min(debt_equity, 300) / 300)))
    if profit_margin is not None and profit_margin > 0:
        quality_components.append(min(100, profit_margin * 100 / 0.25 * 100 / 100))  # 25% = 100
    scores["quality"] = round(sum(quality_components) / len(quality_components), 1) if quality_components else 50.0

    # --- Defensive Score (low beta, low vol = high score) ---
    beta = info.get("beta") or 1.0
    returns = closes.pct_change().dropna()
    annual_vol = float(returns.std() * np.sqrt(252)) if len(returns) > 1 else 0.20

    def_components = [
        max(0, 100 * (1 - min(beta, 2.0) / 2.0)),  # Beta 0 = 100, Beta 2 = 0
        max(0, 100 * (1 - min(annual_vol, 0.60) / 0.60)),  # Vol 0 = 100, Vol 60% = 0
    ]
    scores["defensive"] = round(sum(def_components) / len(def_components), 1)

    # --- Composite (equal-weighted) ---
    all_scores = [scores["value"], scores["momentum"], scores["quality"], scores["defensive"]]
    scores["composite"] = round(sum(all_scores) / len(all_scores), 1)

    return scores


def _compute_risk_decomposition(stock_returns, market_returns, rf_rate: float = 0.05) -> dict:
    """CAPM-based risk decomposition: alpha, beta, R², tracking error, information ratio."""
    rf_daily = rf_rate / 252

    # Align series
    if hasattr(stock_returns, 'index') and hasattr(market_returns, 'index'):
        common = stock_returns.index.intersection(market_returns.index)
        sr = stock_returns.loc[common].values
        mr = market_returns.loc[common].values
    else:
        sr = np.array(stock_returns)
        mr = np.array(market_returns)

    if len(sr) < 30:
        return {
            "beta": None, "alpha_annual": None, "alpha_t_stat": None,
            "r_squared": None, "idiosyncratic_vol": None,
            "downside_beta": None, "tracking_error": None, "information_ratio": None,
        }

    # Excess returns
    sr_ex = sr - rf_daily
    mr_ex = mr - rf_daily

    # OLS: stock_excess = alpha + beta * market_excess
    A = np.vstack([mr_ex, np.ones(len(mr_ex))]).T
    result = np.linalg.lstsq(A, sr_ex, rcond=None)
    beta_val, alpha_daily = result[0]

    # Residuals and R²
    predicted = beta_val * mr_ex + alpha_daily
    residuals = sr_ex - predicted
    ss_res = np.sum(residuals ** 2)
    ss_tot = np.sum((sr_ex - np.mean(sr_ex)) ** 2)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0

    # Alpha annualized and t-stat
    alpha_annual = alpha_daily * 252
    residual_std = np.std(residuals)
    alpha_t_stat = (alpha_daily / residual_std * np.sqrt(len(sr))) if residual_std > 0 else 0

    # Idiosyncratic volatility
    stock_vol = float(np.std(sr) * np.sqrt(252))
    idio_vol = stock_vol * np.sqrt(max(0, 1 - r_squared))

    # Downside beta (negative market days only)
    down_mask = mr_ex < 0
    if np.sum(down_mask) > 10:
        A_down = np.vstack([mr_ex[down_mask], np.ones(np.sum(down_mask))]).T
        res_down = np.linalg.lstsq(A_down, sr_ex[down_mask], rcond=None)
        downside_beta = float(res_down[0][0])
    else:
        downside_beta = float(beta_val)

    # Tracking error and information ratio
    active_returns = sr - beta_val * mr
    tracking_error = float(np.std(active_returns) * np.sqrt(252))
    info_ratio = float(alpha_annual / tracking_error) if tracking_error > 0 else 0

    return {
        "beta": round(float(beta_val), 3),
        "alpha_annual": round(float(alpha_annual) * 100, 2),  # As percentage
        "alpha_t_stat": round(float(alpha_t_stat), 2),
        "r_squared": round(float(r_squared), 3),
        "idiosyncratic_vol": round(float(idio_vol) * 100, 1),  # As percentage
        "downside_beta": round(float(downside_beta), 3),
        "tracking_error": round(float(tracking_error) * 100, 1),  # As percentage
        "information_ratio": round(float(info_ratio), 3),
    }


def _compute_tail_risk(returns) -> dict:
    """VaR, CVaR, Sortino, Calmar, skewness, kurtosis, tail ratio."""
    r = np.array(returns)
    if len(r) < 30:
        return {
            "var_95": None, "cvar_95": None, "sortino": None, "calmar": None,
            "skewness": None, "kurtosis": None, "tail_ratio": None,
        }

    # VaR 95% (daily, then show as percentage)
    var_95 = float(np.percentile(r, 5))

    # CVaR / Expected Shortfall
    cvar_95 = float(np.mean(r[r <= var_95]))

    # Sortino ratio
    mean_return = float(np.mean(r))
    downside = r[r < 0]
    downside_std = float(np.std(downside)) if len(downside) > 1 else float(np.std(r))
    sortino = (mean_return * 252) / (downside_std * np.sqrt(252)) if downside_std > 0 else 0

    # Calmar ratio: annualized return / |max drawdown|
    cumulative = np.cumprod(1 + r)
    running_max = np.maximum.accumulate(cumulative)
    drawdowns = cumulative / running_max - 1
    max_dd = float(np.min(drawdowns))
    ann_return = float((cumulative[-1]) ** (252 / len(r)) - 1) if len(r) > 0 else 0
    calmar = ann_return / abs(max_dd) if max_dd != 0 else 0

    # Skewness and kurtosis
    sk = float(skew(r))
    ku = float(kurtosis(r))

    # Tail ratio: average gain in top 5% / |average loss in bottom 5%|
    top_5 = r[r >= np.percentile(r, 95)]
    bot_5 = r[r <= np.percentile(r, 5)]
    tail_ratio = float(np.mean(top_5) / abs(np.mean(bot_5))) if len(bot_5) > 0 and np.mean(bot_5) != 0 else 1.0

    return {
        "var_95": round(var_95 * 100, 2),
        "cvar_95": round(cvar_95 * 100, 2),
        "sortino": round(float(sortino), 2),
        "calmar": round(float(calmar), 2),
        "skewness": round(sk, 3),
        "kurtosis": round(ku, 3),
        "tail_ratio": round(tail_ratio, 3),
    }


def _compute_signals(hist, spread_z: float | None = None) -> dict:
    """Momentum + mean-reversion signals: RSI, Bollinger, MAs, entry recommendation."""
    closes = hist["Close"]
    n = len(closes)
    result = {}

    # RSI (14-day)
    if n >= 15:
        delta = closes.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean().iloc[-1]
        avg_loss = loss.rolling(14).mean().iloc[-1]
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        result["rsi_14"] = round(100 - 100 / (1 + rs), 1)
    else:
        result["rsi_14"] = 50

    # Bollinger Band position (20-day, 2 std)
    if n >= 20:
        sma20 = closes.rolling(20).mean().iloc[-1]
        std20 = closes.rolling(20).std().iloc[-1]
        upper = sma20 + 2 * std20
        lower = sma20 - 2 * std20
        result["bollinger_pct"] = round((closes.iloc[-1] - lower) / (upper - lower), 3) if upper != lower else 0.5
    else:
        result["bollinger_pct"] = 0.5

    # Moving average trends
    result["above_sma_50"] = bool(closes.iloc[-1] > closes.rolling(50).mean().iloc[-1]) if n >= 50 else None
    result["above_sma_200"] = bool(closes.iloc[-1] > closes.rolling(200).mean().iloc[-1]) if n >= 200 else None

    # 12-1 month momentum (AQR-style: skip most recent month)
    if n >= 252:
        result["momentum_12_1"] = round((closes.iloc[-21] / closes.iloc[-252] - 1) * 100, 2)
    elif n >= 63:
        result["momentum_12_1"] = round((closes.iloc[-1] / closes.iloc[-63] - 1) * 100, 2)
    else:
        result["momentum_12_1"] = 0

    # Spread z-score regime
    if spread_z is not None:
        if spread_z > 2:
            result["spread_regime"] = "Extended"
        elif spread_z < -2:
            result["spread_regime"] = "Compressed"
        elif abs(spread_z) <= 1:
            result["spread_regime"] = "Neutral"
        else:
            result["spread_regime"] = "Mild"

    # Entry signal composite
    bullish_points = 0
    bearish_points = 0
    reasons = []

    rsi = result["rsi_14"]
    if rsi < 30:
        bullish_points += 2
        reasons.append("RSI oversold (<30)")
    elif rsi < 45:
        bullish_points += 1
        reasons.append("RSI below neutral")
    elif rsi > 70:
        bearish_points += 2
        reasons.append("RSI overbought (>70)")
    elif rsi > 55:
        bearish_points += 1

    mom = result["momentum_12_1"]
    if mom > 10:
        bullish_points += 2
        reasons.append(f"Strong momentum (+{mom:.1f}%)")
    elif mom > 0:
        bullish_points += 1
        reasons.append("Positive momentum")
    elif mom < -10:
        bearish_points += 2
        reasons.append(f"Negative momentum ({mom:.1f}%)")
    elif mom < 0:
        bearish_points += 1

    if result.get("above_sma_50"):
        bullish_points += 1
        reasons.append("Above 50-day MA")
    elif result.get("above_sma_50") is False:
        bearish_points += 1

    if result.get("above_sma_200"):
        bullish_points += 1
        reasons.append("Above 200-day MA")
    elif result.get("above_sma_200") is False:
        bearish_points += 1
        reasons.append("Below 200-day MA")

    bb = result["bollinger_pct"]
    if bb < 0.1:
        bullish_points += 1
        reasons.append("Near Bollinger lower band")
    elif bb > 0.9:
        bearish_points += 1
        reasons.append("Near Bollinger upper band")

    net = bullish_points - bearish_points
    if net >= 4:
        result["entry_signal"] = "STRONG_ENTRY"
    elif net >= 2:
        result["entry_signal"] = "ENTRY"
    elif net <= -4:
        result["entry_signal"] = "AVOID"
    elif net <= -2:
        result["entry_signal"] = "CAUTION"
    else:
        result["entry_signal"] = "NEUTRAL"

    result["entry_reasons"] = reasons
    return result


def _compute_position_sizing(alpha_pct: float | None, volatility: float, investment: float,
                              max_position_pct: float = 0.25) -> dict:
    """Kelly criterion, risk parity, and recommended position sizing."""
    alpha = (alpha_pct or 0) / 100  # Convert from percentage
    vol = max(volatility, 0.01)

    # Full Kelly: f* = mu / sigma^2
    kelly_full = alpha / (vol ** 2) if vol > 0 else 0
    kelly_full = max(-1, min(2, kelly_full))  # Cap at 200%, floor at -100%
    kelly_half = kelly_full / 2

    # Risk parity: size so position contributes 10% annualized vol
    target_vol = 0.10
    risk_parity_pct = target_vol / vol if vol > 0 else 0.10

    # Inverse volatility weight (normalized as fraction)
    inv_vol = 1.0 / vol if vol > 0 else 1.0

    # Recommended: min of half-kelly (if positive), risk parity, and cap
    candidates = [risk_parity_pct, max_position_pct]
    if kelly_half > 0:
        candidates.append(kelly_half)
    recommended_pct = min(candidates)
    recommended_pct = max(0.02, min(recommended_pct, max_position_pct))  # Floor 2%, cap at max

    return {
        "kelly_full": round(kelly_full * 100, 1),
        "kelly_half": round(kelly_half * 100, 1),
        "risk_parity_pct": round(risk_parity_pct * 100, 1),
        "inverse_vol_pct": round(inv_vol, 3),
        "recommended_pct": round(recommended_pct * 100, 1),
        "recommended_dollars": round(investment * recommended_pct, 0),
    }


def _build_quant_analytics(ticker: str, hist, info: dict, returns, market_returns,
                            investment: float, spread_z: float | None = None) -> dict:
    """Unified quant analytics builder for a single stock."""
    factor_scores = _compute_factor_scores(ticker, hist, info)
    risk_decomp = _compute_risk_decomposition(returns, market_returns)
    tail_risk = _compute_tail_risk(returns)
    signals = _compute_signals(hist, spread_z)
    vol = float(returns.std() * np.sqrt(252)) if len(returns) > 1 else 0.20
    position_sizing = _compute_position_sizing(
        risk_decomp.get("alpha_annual"), vol, investment
    )
    return {
        "factor_scores": factor_scores,
        "risk_decomposition": risk_decomp,
        "tail_risk": tail_risk,
        "signals": signals,
        "position_sizing": position_sizing,
    }


# ====================================================================
# 130/30 Enhanced Equity Portfolio
# ====================================================================

_LEVERAGE_MAP = {
    "120/20": (1.20, 0.20),
    "130/30": (1.30, 0.30),
    "150/50": (1.50, 0.50),
}


def _batch_stock_data(tickers: list[str]) -> dict[str, dict]:
    """Fetch price, info, history for multiple tickers efficiently."""
    results = {}
    batch = yf.Tickers(" ".join(tickers))
    for t in tickers:
        try:
            yticker = batch.tickers.get(t.upper()) or yf.Ticker(t)
            info = yticker.info or {}
            price = info.get("currentPrice") or info.get("regularMarketPrice") or 0
            hist = yticker.history(period="1y", interval="1d")
            results[t.upper()] = {
                "info": info,
                "price": float(price) if price else None,
                "name": info.get("shortName") or info.get("longName") or t.upper(),
                "sector": info.get("sector", "Unknown"),
                "industry": info.get("industry", ""),
                "beta": _safe_float(info.get("beta"), 1.0),
                "hist": hist,
                "returns": hist["Close"].pct_change().dropna() if not hist.empty else None,
            }
        except Exception as e:
            logger.warning(f"Failed to fetch {t}: {e}")
            results[t.upper()] = {
                "info": {}, "price": None, "name": t.upper(),
                "sector": "Unknown", "industry": "", "beta": 1.0,
                "hist": None, "returns": None,
            }
    return results


def _allocate_unspecified_positions(
    positions: list[dict],
    stock_data: dict,
    target_value: float,
    locked_value: float,
    side: str,
) -> list[dict]:
    """Auto-allocate shares for positions without specified quantities.

    Uses factor-score-weighted allocation for remaining capital.
    """
    remaining = target_value - locked_value
    if remaining <= 0:
        return positions

    unspec = [p for p in positions if p.get("shares") is None]
    if not unspec:
        return positions

    # Compute factor scores for unspecified positions to weight allocation
    weights = {}
    for p in unspec:
        t = p["ticker"].upper()
        sd = stock_data.get(t, {})
        info = sd.get("info", {})
        hist = sd.get("hist")
        if hist is not None and not hist.empty:
            try:
                fs = _compute_factor_scores(t, hist, info)
                # For long: higher composite = more weight; for short: lower composite = more weight
                score = fs.get("composite", 50)
                weights[t] = max(score, 5) if side == "long" else max(100 - score, 5)
            except Exception:
                weights[t] = 50
        else:
            weights[t] = 50

    total_w = sum(weights.values())
    for p in positions:
        if p.get("shares") is not None:
            continue
        t = p["ticker"].upper()
        sd = stock_data.get(t, {})
        price = sd.get("price")
        if not price or price <= 0:
            p["shares"] = 0
            continue
        alloc = remaining * (weights[t] / total_w)
        p["shares"] = max(1, int(alloc / price))
    return positions


def _build_position_detail(
    ticker: str, shares: int, side: str, stock_data: dict,
    market_returns, investment: float,
) -> dict:
    """Build detailed position info including quant analytics."""
    sd = stock_data.get(ticker.upper(), {})
    price = sd.get("price") or 0
    dollar_value = shares * price
    hist = sd.get("hist")
    info = sd.get("info", {})
    returns = sd.get("returns")

    qa = None
    factor_scores = None
    if hist is not None and not hist.empty and returns is not None and market_returns is not None:
        try:
            common = returns.index.intersection(market_returns.index)
            if len(common) >= 30:
                aligned_r = returns.loc[common]
                aligned_m = market_returns.loc[common]
                qa = _build_quant_analytics(
                    ticker, hist, info, aligned_r, aligned_m, investment
                )
                factor_scores = qa.get("factor_scores")
        except Exception as e:
            logger.warning(f"Quant analytics failed for {ticker}: {e}")

    if not factor_scores:
        factor_scores = {"value": 50, "momentum": 50, "quality": 50, "defensive": 50, "composite": 50}

    return {
        "ticker": ticker.upper(),
        "name": sd.get("name", ticker.upper()),
        "price": round(price, 2) if price else None,
        "sector": sd.get("sector", "Unknown"),
        "side": side,
        "shares": shares,
        "dollar_value": round(dollar_value, 2),
        "weight_pct": 0,  # filled later once totals known
        "beta": sd.get("beta", 1.0),
        "factor_scores": factor_scores,
        "quant_analytics": qa,
    }


def _compute_portfolio_metrics(
    long_positions: list[dict],
    short_positions: list[dict],
    stock_data: dict,
    market_returns,
    investment: float,
) -> dict:
    """Compute portfolio-level risk metrics."""
    all_pos = long_positions + short_positions
    gross_long = sum(p["dollar_value"] for p in long_positions)
    gross_short = sum(p["dollar_value"] for p in short_positions)
    gross = gross_long + gross_short
    net = gross_long - gross_short

    # Weighted beta
    net_beta = 0
    for p in long_positions:
        w = p["dollar_value"] / net if net else 0
        net_beta += w * p.get("beta", 1.0)
    for p in short_positions:
        w = p["dollar_value"] / net if net else 0
        net_beta -= w * p.get("beta", 1.0)

    # Portfolio volatility estimate (simplified: weighted avg vol, ignoring correlation for speed)
    vol_sum = 0
    total_val = gross_long + gross_short
    for p in all_pos:
        sd = stock_data.get(p["ticker"], {})
        r = sd.get("returns")
        if r is not None and len(r) > 10:
            pos_vol = float(r.std() * np.sqrt(252))
            w = p["dollar_value"] / total_val if total_val else 0
            vol_sum += (w * pos_vol) ** 2
    portfolio_vol = round(float(np.sqrt(vol_sum) * 100), 2)

    # Sharpe estimate
    avg_alpha = 0
    n_qa = 0
    for p in all_pos:
        qa = p.get("quant_analytics")
        if qa:
            a = qa.get("risk_decomposition", {}).get("alpha_annual", 0)
            if a:
                avg_alpha += a
                n_qa += 1
    avg_alpha = avg_alpha / n_qa if n_qa else 0
    sharpe = round(avg_alpha / (portfolio_vol / 100), 2) if portfolio_vol > 0 else 0

    # Sector breakdown
    sector_map = {}
    for p in long_positions:
        s = p.get("sector", "Unknown")
        sector_map.setdefault(s, {"long_weight": 0, "short_weight": 0})
        sector_map[s]["long_weight"] += p["dollar_value"]
    for p in short_positions:
        s = p.get("sector", "Unknown")
        sector_map.setdefault(s, {"long_weight": 0, "short_weight": 0})
        sector_map[s]["short_weight"] += p["dollar_value"]
    sector_breakdown = []
    for sec, vals in sorted(sector_map.items()):
        lw = round(vals["long_weight"] / gross_long * 100, 1) if gross_long else 0
        sw = round(vals["short_weight"] / gross_short * 100, 1) if gross_short else 0
        sector_breakdown.append({
            "sector": sec,
            "long_weight": lw,
            "short_weight": sw,
            "net_weight": round(lw - sw, 1),
        })

    # Concentration
    weights = sorted([p["dollar_value"] / gross * 100 for p in all_pos], reverse=True) if gross else []
    top5_weight = round(sum(weights[:5]), 1) if len(weights) >= 5 else round(sum(weights), 1)
    hhi = round(sum(w ** 2 for w in weights), 1)

    return {
        "net_beta": round(net_beta, 3),
        "gross_exposure": round(gross, 2),
        "net_exposure": round(net, 2),
        "portfolio_volatility": portfolio_vol,
        "estimated_tracking_error": round(portfolio_vol * abs(1 - net_beta), 2),
        "sharpe_ratio": sharpe,
        "long_count": len(long_positions),
        "short_count": len(short_positions),
        "long_value": round(gross_long, 2),
        "short_value": round(gross_short, 2),
        "sector_breakdown": sector_breakdown,
        "top5_concentration": top5_weight,
        "hhi": hhi,
    }


def _compute_scenario_analysis(
    long_positions: list[dict],
    short_positions: list[dict],
    investment: float,
) -> dict:
    """Compute outcome scenarios across market/stock movement permutations."""
    gross_long = sum(p["dollar_value"] for p in long_positions)
    gross_short = sum(p["dollar_value"] for p in short_positions)

    # --- 1) Market-wide scenarios ---
    market_moves = [-30, -20, -15, -10, -5, 0, 5, 10, 15, 20, 30]
    market_scenarios = []
    for mkt_pct in market_moves:
        mkt = mkt_pct / 100
        long_pnl = 0
        for p in long_positions:
            beta = p.get("beta", 1.0)
            # Position P&L = value × (market_move × beta)
            long_pnl += p["dollar_value"] * (mkt * beta)
        short_pnl = 0
        for p in short_positions:
            beta = p.get("beta", 1.0)
            # Short profits when market drops: P&L = value × (-market_move × beta)
            short_pnl += p["dollar_value"] * (-mkt * beta)

        net_pnl = long_pnl + short_pnl
        market_scenarios.append({
            "market_move": mkt_pct,
            "long_pnl": round(long_pnl, 2),
            "short_pnl": round(short_pnl, 2),
            "net_pnl": round(net_pnl, 2),
            "return_pct": round(net_pnl / investment * 100, 2) if investment else 0,
            "portfolio_value": round(investment + net_pnl, 2),
        })

    # Find breakeven (where net_pnl crosses 0)
    breakeven = None
    for i in range(1, len(market_scenarios)):
        prev = market_scenarios[i - 1]
        curr = market_scenarios[i]
        if prev["net_pnl"] != 0 and curr["net_pnl"] != 0:
            if (prev["net_pnl"] > 0 and curr["net_pnl"] < 0) or (prev["net_pnl"] < 0 and curr["net_pnl"] > 0):
                # Linear interpolation
                r = abs(prev["net_pnl"]) / (abs(prev["net_pnl"]) + abs(curr["net_pnl"]))
                breakeven = round(prev["market_move"] + r * (curr["market_move"] - prev["market_move"]), 1)
                break

    # --- 2) Stress scenarios ---
    def _stress_pnl(mkt_move: float, corr_spike: float = 0) -> dict:
        long_pnl = sum(p["dollar_value"] * (mkt_move * p.get("beta", 1.0)) for p in long_positions)
        # In stress with correlation spike, shorts don't diversify as well
        adj = 1 - corr_spike * 0.3  # reduce short hedge effectiveness
        short_pnl = sum(p["dollar_value"] * (-mkt_move * p.get("beta", 1.0) * adj) for p in short_positions)
        net = long_pnl + short_pnl
        return {"net_pnl": round(net, 2), "return_pct": round(net / investment * 100, 2) if investment else 0}

    stress_scenarios = [
        {
            "name": "2008 Financial Crisis",
            "description": "Market drops 38% with correlation spike — hedges become less effective",
            **_stress_pnl(-0.38, corr_spike=0.5),
        },
        {
            "name": "COVID Crash (Mar 2020)",
            "description": "Sharp 34% decline followed by rapid recovery — short book squeezed on recovery",
            **_stress_pnl(-0.34, corr_spike=0.3),
        },
        {
            "name": "Strong Bull Market",
            "description": "Market rallies 25% — long book gains but short positions lose",
            **_stress_pnl(0.25),
        },
        {
            "name": "Sector Rotation",
            "description": "Your sectors underperform market by 10% — idiosyncratic risk",
            **_stress_pnl(-0.10, corr_spike=0.2),
        },
    ]

    # --- 3) Leverage comparison ---
    leverage_comparison = []
    for ratio_name, (long_mult, short_mult) in _LEVERAGE_MAP.items():
        # Scale positions proportionally to different leverage
        actual_long_mult = gross_long / investment if investment else 1.3
        actual_short_mult = gross_short / investment if investment else 0.3
        scale_long = long_mult / actual_long_mult if actual_long_mult else 1
        scale_short = short_mult / actual_short_mult if actual_short_mult else 1

        # Best case: market +20%
        best_long = sum(p["dollar_value"] * scale_long * 0.20 * p.get("beta", 1.0) for p in long_positions)
        best_short = sum(p["dollar_value"] * scale_short * (-0.20) * p.get("beta", 1.0) for p in short_positions)
        best = best_long + best_short

        # Worst case: market -20%, correlation spike
        worst_long = sum(p["dollar_value"] * scale_long * (-0.20) * p.get("beta", 1.0) for p in long_positions)
        worst_short = sum(p["dollar_value"] * scale_short * 0.20 * p.get("beta", 1.0) * 0.7 for p in short_positions)
        worst = worst_long + worst_short

        # Expected: market +8% (long-run avg)
        exp_long = sum(p["dollar_value"] * scale_long * 0.08 * p.get("beta", 1.0) for p in long_positions)
        exp_short = sum(p["dollar_value"] * scale_short * (-0.08) * p.get("beta", 1.0) for p in short_positions)
        expected = exp_long + exp_short

        leverage_comparison.append({
            "ratio": ratio_name,
            "best_case": round(best, 2),
            "best_case_pct": round(best / investment * 100, 2) if investment else 0,
            "worst_case": round(worst, 2),
            "worst_case_pct": round(worst / investment * 100, 2) if investment else 0,
            "expected": round(expected, 2),
            "expected_pct": round(expected / investment * 100, 2) if investment else 0,
        })

    # --- 4) Position impact analysis ---
    position_impact = []
    all_pos = long_positions + short_positions
    for p in sorted(all_pos, key=lambda x: x["dollar_value"], reverse=True)[:10]:
        weight = round(p["dollar_value"] / investment * 100, 2) if investment else 0
        # If this position drops 20%
        if p["side"] == "long":
            impact_down = round(-p["dollar_value"] * 0.20, 2)
            impact_up = round(p["dollar_value"] * 0.20, 2)
        else:  # short: "down 20%" means stock drops → we profit
            impact_down = round(p["dollar_value"] * 0.20, 2)  # profit
            impact_up = round(-p["dollar_value"] * 0.20, 2)  # loss
        position_impact.append({
            "ticker": p["ticker"],
            "side": p["side"],
            "weight": weight,
            "dollar_value": p["dollar_value"],
            "if_down_20_pnl": impact_down,
            "if_up_20_pnl": impact_up,
            "portfolio_impact_pct": round(abs(impact_down) / investment * 100, 2) if investment else 0,
        })

    max_profit = max(s["net_pnl"] for s in market_scenarios)
    max_loss = min(s["net_pnl"] for s in market_scenarios)

    return {
        "market_scenarios": market_scenarios,
        "stress_scenarios": stress_scenarios,
        "leverage_comparison": leverage_comparison,
        "position_impact": position_impact,
        "max_profit": round(max_profit, 2),
        "max_loss": round(max_loss, 2),
        "breakeven_market_move": breakeven,
    }


def _project_tax_savings(
    short_book_value: float,
    long_book_value: float,
    leverage_ratio: str,
    tax_rate_st: float,
    tax_rate_lt: float,
    years: int = 10,
) -> dict:
    """Project tax-loss harvesting benefits over time.

    Based on AQR research: 130/30 generates ~2.7x more capital losses
    than long-only over the first 10 years, translating to ~4.41% avg
    pre-liquidation tax alpha.
    """
    total_invested = long_book_value  # net investment
    _, short_mult = _LEVERAGE_MAP.get(leverage_ratio, (1.3, 0.3))

    # Long-only direct indexing loss rates (% of portfolio, decaying)
    long_only_loss_rates = [3.5, 3.0, 2.5, 2.0, 1.5, 1.2, 1.0, 0.8, 0.7, 0.6]

    # 130/30 multiplier based on leverage (AQR: 2.7x for 130/30)
    if short_mult <= 0.20:
        multiplier = 2.0
    elif short_mult <= 0.30:
        multiplier = 2.7
    else:
        multiplier = 3.5

    yearly = []
    cumulative = 0
    cumulative_long_only = 0

    for yr in range(1, years + 1):
        idx = min(yr - 1, len(long_only_loss_rates) - 1)
        lo_rate = long_only_loss_rates[idx]

        # Long-only losses
        lo_losses = total_invested * lo_rate / 100
        # 130/30 losses (multiplied, more persistent due to short book turnover)
        ls_losses = lo_losses * multiplier

        # Blend of ST and LT rates (assume 60% ST, 40% LT for short book)
        blended_rate = 0.6 * tax_rate_st + 0.4 * tax_rate_lt

        lo_tax_savings = lo_losses * blended_rate
        ls_tax_savings = ls_losses * blended_rate

        cumulative_long_only += lo_tax_savings
        cumulative += ls_tax_savings

        yearly.append({
            "year": yr,
            "long_only_losses": round(lo_losses, 2),
            "ls_losses": round(ls_losses, 2),
            "long_only_tax_savings": round(lo_tax_savings, 2),
            "ls_tax_savings": round(ls_tax_savings, 2),
            "cumulative_long_only": round(cumulative_long_only, 2),
            "cumulative_ls": round(cumulative, 2),
        })

    avg_annual_alpha = round((cumulative / years) / total_invested * 100, 2) if total_invested else 0

    # Also compute for the other leverage ratios for comparison
    comparison = {}
    for ratio_name, (_, s_mult) in _LEVERAGE_MAP.items():
        if s_mult <= 0.20:
            m = 2.0
        elif s_mult <= 0.30:
            m = 2.7
        else:
            m = 3.5
        total_savings = 0
        for yr in range(1, years + 1):
            idx = min(yr - 1, len(long_only_loss_rates) - 1)
            losses = total_invested * long_only_loss_rates[idx] / 100 * m
            total_savings += losses * (0.6 * tax_rate_st + 0.4 * tax_rate_lt)
        comparison[ratio_name] = round(total_savings, 2)

    return {
        "yearly": yearly,
        "avg_annual_tax_alpha_pct": avg_annual_alpha,
        "total_10yr_savings": round(cumulative, 2),
        "loss_multiplier_vs_long_only": multiplier,
        "leverage_comparison": comparison,
    }


def _build_130_30_portfolio(
    long_inputs: list[dict],
    short_inputs: list[dict],
    investment_amount: float,
    leverage_ratio: str,
    tax_rate_st: float,
    tax_rate_lt: float,
) -> dict:
    """Build a 130/30 enhanced equity portfolio from user-provided positions."""
    long_mult, short_mult = _LEVERAGE_MAP.get(leverage_ratio, (1.3, 0.3))
    long_target = investment_amount * long_mult
    short_target = investment_amount * short_mult

    # --- 1) Fetch all stock data ---
    all_tickers = list(set(
        [p["ticker"].upper() for p in long_inputs] +
        [p["ticker"].upper() for p in short_inputs]
    ))
    stock_data = _batch_stock_data(all_tickers)

    # Validate all tickers have prices
    errors = []
    for t in all_tickers:
        if not stock_data.get(t, {}).get("price"):
            errors.append(f"Cannot fetch price for {t}")
    if errors:
        return {"error": "; ".join(errors)}

    # --- 2) Compute locked value (positions with specified shares) ---
    long_locked = 0
    for p in long_inputs:
        if p.get("shares") is not None:
            price = stock_data[p["ticker"].upper()]["price"]
            long_locked += p["shares"] * price

    short_locked = 0
    for p in short_inputs:
        if p.get("shares") is not None:
            price = stock_data[p["ticker"].upper()]["price"]
            short_locked += p["shares"] * price

    # --- 3) Auto-allocate unspecified positions ---
    _allocate_unspecified_positions(long_inputs, stock_data, long_target, long_locked, "long")
    _allocate_unspecified_positions(short_inputs, stock_data, short_target, short_locked, "short")

    # --- 4) Fetch market returns for analytics ---
    try:
        spy = yf.Ticker("SPY")
        spy_hist = spy.history(period="1y", interval="1d")
        market_returns = spy_hist["Close"].pct_change().dropna()
    except Exception:
        market_returns = None

    # --- 5) Build detailed positions ---
    long_positions = []
    for p in long_inputs:
        t = p["ticker"].upper()
        shares = p.get("shares") or 0
        if shares <= 0:
            continue
        detail = _build_position_detail(t, int(shares), "long", stock_data, market_returns, investment_amount)
        long_positions.append(detail)

    short_positions = []
    for p in short_inputs:
        t = p["ticker"].upper()
        shares = p.get("shares") or 0
        if shares <= 0:
            continue
        detail = _build_position_detail(t, int(shares), "short", stock_data, market_returns, investment_amount)
        short_positions.append(detail)

    # --- 6) Compute weight percentages ---
    gross_long = sum(p["dollar_value"] for p in long_positions)
    gross_short = sum(p["dollar_value"] for p in short_positions)
    for p in long_positions:
        p["weight_pct"] = round(p["dollar_value"] / gross_long * 100, 2) if gross_long else 0
    for p in short_positions:
        p["weight_pct"] = round(p["dollar_value"] / gross_short * 100, 2) if gross_short else 0

    # Sort by dollar value descending
    long_positions.sort(key=lambda x: x["dollar_value"], reverse=True)
    short_positions.sort(key=lambda x: x["dollar_value"], reverse=True)

    # --- 7) Portfolio metrics ---
    risk_metrics = _compute_portfolio_metrics(
        long_positions, short_positions, stock_data, market_returns, investment_amount
    )

    # --- 8) Factor exposure (portfolio averages) ---
    def _avg_factor(positions, key):
        vals = [p["factor_scores"].get(key, 50) for p in positions if p.get("factor_scores")]
        return round(sum(vals) / len(vals), 1) if vals else 50
    factor_exposure = {
        "long_value": _avg_factor(long_positions, "value"),
        "long_momentum": _avg_factor(long_positions, "momentum"),
        "long_quality": _avg_factor(long_positions, "quality"),
        "long_defensive": _avg_factor(long_positions, "defensive"),
        "long_composite": _avg_factor(long_positions, "composite"),
        "short_value": _avg_factor(short_positions, "value"),
        "short_momentum": _avg_factor(short_positions, "momentum"),
        "short_quality": _avg_factor(short_positions, "quality"),
        "short_defensive": _avg_factor(short_positions, "defensive"),
        "short_composite": _avg_factor(short_positions, "composite"),
        "portfolio_value": _avg_factor(long_positions + short_positions, "value"),
        "portfolio_momentum": _avg_factor(long_positions + short_positions, "momentum"),
        "portfolio_quality": _avg_factor(long_positions + short_positions, "quality"),
        "portfolio_defensive": _avg_factor(long_positions + short_positions, "defensive"),
        "portfolio_composite": _avg_factor(long_positions + short_positions, "composite"),
    }

    # --- 9) Scenario analysis ---
    scenarios = _compute_scenario_analysis(long_positions, short_positions, investment_amount)

    # --- 10) Tax projections ---
    tax_projections = _project_tax_savings(
        gross_short, gross_long, leverage_ratio, tax_rate_st, tax_rate_lt
    )

    return {
        "type": "130_30",
        "long_positions": long_positions,
        "short_positions": short_positions,
        "risk_metrics": risk_metrics,
        "factor_exposure": factor_exposure,
        "scenario_analysis": scenarios,
        "tax_projections": tax_projections,
        "parameters": {
            "investment_amount": investment_amount,
            "leverage_ratio": leverage_ratio,
            "long_target": round(long_target, 2),
            "short_target": round(short_target, 2),
            "actual_long": round(gross_long, 2),
            "actual_short": round(gross_short, 2),
            "tax_rate_st": tax_rate_st,
            "tax_rate_lt": tax_rate_lt,
        },
        "llm_insights": None,  # filled async
    }


async def _generate_portfolio_insights(portfolio: dict, openai_key: str, model: str = "gpt-4o") -> str:
    """Generate LLM-powered portfolio insights."""
    from .llm_service import call_llm

    risk = portfolio.get("risk_metrics", {})
    factors = portfolio.get("factor_exposure", {})
    scenarios = portfolio.get("scenario_analysis", {})
    tax = portfolio.get("tax_projections", {})
    params = portfolio.get("parameters", {})

    long_tickers = [p["ticker"] for p in portfolio.get("long_positions", [])]
    short_tickers = [p["ticker"] for p in portfolio.get("short_positions", [])]

    prompt = f"""Analyze this 130/30 Enhanced Equity Portfolio as a senior quant portfolio strategist.

PORTFOLIO COMPOSITION:
- Long positions ({params.get('leverage_ratio', '130/30')}): {', '.join(long_tickers)}
- Short positions: {', '.join(short_tickers)}
- Investment: ${params.get('investment_amount', 0):,.0f}
- Long value: ${params.get('actual_long', 0):,.0f} | Short value: ${params.get('actual_short', 0):,.0f}

RISK METRICS:
- Net Beta: {risk.get('net_beta', 'N/A')} | Volatility: {risk.get('portfolio_volatility', 'N/A')}%
- Sharpe: {risk.get('sharpe_ratio', 'N/A')} | Top-5 Concentration: {risk.get('top5_concentration', 'N/A')}%

FACTOR EXPOSURE (0-100):
- Long book: Value={factors.get('long_value')}, Momentum={factors.get('long_momentum')}, Quality={factors.get('long_quality')}
- Short book: Value={factors.get('short_value')}, Momentum={factors.get('short_momentum')}, Quality={factors.get('short_quality')}

SCENARIO ANALYSIS:
- Max profit scenario: ${scenarios.get('max_profit', 0):,.0f}
- Max loss scenario: ${scenarios.get('max_loss', 0):,.0f}
- Breakeven market move: {scenarios.get('breakeven_market_move', 'N/A')}%

TAX PROJECTIONS:
- Annual tax alpha: {tax.get('avg_annual_tax_alpha_pct', 'N/A')}%
- 10-year savings: ${tax.get('total_10yr_savings', 0):,.0f}

Provide a concise analysis covering:
1. **Portfolio Thesis**: What this portfolio is positioned for
2. **Key Strengths**: Factor advantages, diversification benefits
3. **Risk Warnings**: Concentration risks, factor tilts, stress vulnerabilities
4. **Optimization Ideas**: Specific suggestions to improve risk-adjusted returns
5. **Tax Harvesting Opportunity**: How to maximize tax alpha from the short book

Keep the analysis professional, actionable, and under 400 words. Use bullet points."""

    try:
        messages = [
            {"role": "system", "content": "You are a senior quantitative portfolio strategist at a top-tier hedge fund. Provide institutional-quality analysis."},
            {"role": "user", "content": prompt},
        ]
        return await call_llm(api_key=openai_key, model=model, messages=messages, max_tokens=1500)
    except Exception as e:
        logger.warning(f"LLM insights generation failed: {e}")
        return None


async def run_130_30_portfolio(
    long_positions: list[dict],
    short_positions: list[dict],
    investment_amount: float,
    leverage_ratio: str = "130/30",
    tax_rate_st: float = 0.37,
    tax_rate_lt: float = 0.20,
    openai_key: str | None = None,
    openai_model: str = "gpt-4o",
) -> dict:
    """Async entry point for 130/30 portfolio construction."""
    result = await asyncio.to_thread(
        _build_130_30_portfolio,
        long_positions, short_positions,
        investment_amount, leverage_ratio,
        tax_rate_st, tax_rate_lt,
    )

    if result.get("error"):
        return result

    # Generate LLM insights if API key available
    if openai_key:
        try:
            insights = await _generate_portfolio_insights(result, openai_key, openai_model)
            result["llm_insights"] = insights
        except Exception as e:
            logger.warning(f"LLM insights failed: {e}")

    return result
