import math
import time as _time
import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats
from scipy.optimize import minimize
from scipy.spatial.distance import euclidean
from datetime import datetime, timedelta
import asyncio
from functools import lru_cache
from .stock_service import safe_float

# ---------------------------------------------------------------------------
# Module-level TTL caches — avoid hammering yfinance on every request
# ---------------------------------------------------------------------------

# { ticker: (expires_at_monotonic, pd.Series) }
_PRICE_SERIES_CACHE: dict[str, tuple[float, pd.Series]] = {}
_PRICE_TTL_SECONDS = 6 * 3600  # 6 hours — price history is slow-moving

# { ticker: (expires_at_monotonic, float) }
_CURRENT_PRICE_CACHE: dict[str, tuple[float, float]] = {}
_CURRENT_PRICE_TTL = 15 * 60  # 15 minutes

# { cache_key: (expires_at_monotonic, result_dict) }
_LLM_CACHE: dict[str, tuple[float, dict]] = {}
_LLM_CACHE_TTL = 4 * 3600  # 4 hours — LLM picks change slowly

# Optional imports for V2
try:
    import statsmodels.tsa.stattools as ts
    from fastdtw import fastdtw
except ImportError:
    pass

# Category keywords to detect bond/fixed-income ETFs via yfinance info
_BOND_CATEGORIES = {"bond", "fixed income", "treasury", "municipal", "corporate bond",
                    "aggregate bond", "government bond", "inflation-protected"}

# ---------------------------------------------------------------------------
# Minimal fallback ETF set — used only when no LLM API key is available.
# LLM dynamically suggests sector-relevant ETFs; this is the safety net.
# ---------------------------------------------------------------------------
_FALLBACK_ETFS = [
    "SPY", "QQQ", "VTI",                          # Broad market
    "XLK", "XLF", "XLV", "XLE", "XLI", "XLY",    # GICS sectors
    "XLP", "XLRE", "XLC", "XLU",                   # Remaining sectors
]

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

async def _fetch_historical_data(ticker: str, period: str = "1y"):
    try:
        data = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if isinstance(data.columns, pd.MultiIndex):
            if "Close" in data.columns:
                close_data = data["Close"]
            else:
                return pd.Series(dtype=float)
        else:
            close_data = data["Close"]
        if isinstance(close_data, pd.DataFrame):
            return close_data[ticker].dropna()
        return close_data.dropna()
    except Exception as e:
        print(f"Error fetching data for {ticker}: {e}")
        return pd.Series(dtype=float)

def _parse_yf_download(data, tickers: list[str]) -> dict[str, pd.Series]:
    """Parse yf.download result into {ticker: close_series} defensively."""
    result: dict[str, pd.Series] = {}
    if data is None or data.empty:
        return result
    if len(tickers) == 1:
        t = tickers[0]
        if isinstance(data.columns, pd.MultiIndex):
            if "Close" in data.columns:
                cd = data["Close"]
                result[t] = (cd[t] if isinstance(cd, pd.DataFrame) and t in cd.columns else cd).dropna()
        else:
            cd = data.get("Close", pd.Series(dtype=float))
            result[t] = (cd[t] if isinstance(cd, pd.DataFrame) and t in cd.columns else cd).dropna()
        return result
    if isinstance(data.columns, pd.MultiIndex):
        if "Close" in data.columns:
            cd = data["Close"]
            for t in tickers:
                if t in cd.columns:
                    result[t] = cd[t].dropna()
    else:
        if "Close" in data:
            cd = data["Close"]
            for t in tickers:
                if t in cd.columns:
                    result[t] = cd[t].dropna()
    return result


async def _fetch_historical_data_batch(tickers: list[str], period: str = "1y") -> dict[str, pd.Series]:
    """Fetch historical daily close prices with module-level TTL cache (6 h).

    Only tickers not already cached hit yfinance — batched in a single download call.
    """
    if not tickers:
        return {}

    now = _time.monotonic()
    result: dict[str, pd.Series] = {}
    need_fetch: list[str] = []

    for t in tickers:
        entry = _PRICE_SERIES_CACHE.get(t)
        if entry and entry[0] > now:
            result[t] = entry[1]
        else:
            need_fetch.append(t)

    if not need_fetch:
        return result

    try:
        data = yf.download(" ".join(need_fetch), period=period, progress=False, auto_adjust=True)
        fetched = _parse_yf_download(data, need_fetch)
        expires = now + _PRICE_TTL_SECONDS
        for t, s in fetched.items():
            _PRICE_SERIES_CACHE[t] = (expires, s)
            result[t] = s
    except Exception as e:
        print(f"Error fetching batch data for {len(need_fetch)} tickers: {e}")

    return result


async def _get_etf_holdings_overlap(
    etf_ticker: str,
    portfolio_tickers: list[str],
) -> dict:
    """Try to fetch ETF holdings and compute overlap with portfolio.
    Returns {etfName, totalHoldings, overlap: [{ticker, etfWeight}], overlapPct}.
    """
    try:
        etf = yf.Ticker(etf_ticker)
        info = etf.info or {}
        etf_name = info.get("longName") or info.get("shortName") or etf_ticker

        # Try to get holdings from yfinance (available for many ETFs)
        try:
            # yfinance exposes top holdings via fund_top_holdings or similar
            holdings_df = etf.get_holdings()
            if holdings_df is not None and not holdings_df.empty:
                # holdings_df typically has Symbol and % Weight columns
                overlap = []
                total_overlap_weight = 0.0
                for pt in portfolio_tickers:
                    match = holdings_df[holdings_df["Symbol"].str.upper() == pt.upper()]
                    if not match.empty:
                        weight = float(match.iloc[0].get("% Weight", 0) or match.iloc[0].get("Holding Percent", 0) or 0)
                        overlap.append({"ticker": pt, "etfWeight": round(weight, 4)})
                        total_overlap_weight += weight

                return {
                    "etfName": etf_name,
                    "totalHoldings": len(holdings_df),
                    "overlap": overlap,
                    "overlapPct": round(total_overlap_weight, 2),
                    "available": True,
                }
        except Exception:
            pass

        # Fallback: use fund_holding_info or basic info
        try:
            # Some ETFs expose holdings via different attributes
            top_holdings = info.get("holdings", [])
            if top_holdings:
                overlap = []
                total_overlap_weight = 0.0
                for holding in top_holdings:
                    symbol = holding.get("symbol", "").upper()
                    weight = holding.get("holdingPercent", 0) * 100 if holding.get("holdingPercent") else 0
                    if symbol in [t.upper() for t in portfolio_tickers]:
                        overlap.append({"ticker": symbol, "etfWeight": round(weight, 2)})
                        total_overlap_weight += weight

                return {
                    "etfName": etf_name,
                    "totalHoldings": len(top_holdings),
                    "overlap": overlap,
                    "overlapPct": round(total_overlap_weight, 2),
                    "available": True,
                }
        except Exception:
            pass

        return {
            "etfName": etf_name,
            "totalHoldings": None,
            "overlap": [],
            "overlapPct": None,
            "available": False,
        }

    except Exception:
        return {
            "etfName": etf_ticker,
            "totalHoldings": None,
            "overlap": [],
            "overlapPct": None,
            "available": False,
        }


async def _build_portfolio_replacement_strategies(
    etf_ticker: str,
    harvestable_holdings: list[dict],
    total_harvestable_value: float,
    total_losses: float,
    tax_rate_pct: float,
) -> list[dict]:
    """Build execution strategies for replacing ALL harvestable holdings
    with a single ETF purchase."""

    info = _get_ticker_info(etf_ticker)
    etf_price = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0.0)
    if not etf_price:
        try:
            hist = yf.Ticker(etf_ticker).history(period="1d")
            if not hist.empty:
                etf_price = float(hist["Close"].iloc[-1])
        except Exception:
            pass

    if not etf_price:
        return []

    etf_shares_to_buy = math.floor(total_harvestable_value / etf_price)
    etf_total_cost = round(etf_price * etf_shares_to_buy, 2)

    # Strategy 1: Direct Swap — sell all losers, buy ETF
    sell_trades = []
    for h in harvestable_holdings:
        if h.get("currentPrice"):
            sell_trades.append({
                "action": "SELL",
                "ticker": h["ticker"],
                "type": "Shares",
                "qty": h["shares"],
                "price": h["currentPrice"],
                "total": h["currentValue"],
            })

    buy_trade = {
        "action": "BUY",
        "ticker": etf_ticker,
        "type": "ETF Shares",
        "qty": etf_shares_to_buy,
        "price": round(etf_price, 2),
        "total": etf_total_cost,
    }

    strategy_1 = {
        "id": "unified_direct_swap",
        "name": "Unified ETF Swap",
        "type": "Equity/ETF",
        "badge": "Simplest",
        "description": f"Sell all {len(harvestable_holdings)} loss-making holdings. Buy {etf_shares_to_buy} shares of {etf_ticker} (${etf_total_cost:,.0f}) to maintain diversified exposure in a single trade.",
        "pros": [
            "Simplest execution — one buy replaces multiple sells",
            f"Realizes ${total_losses:,.0f} in losses for ${round(total_losses * tax_rate_pct / 100):,.0f} tax savings",
            "Built-in diversification via ETF",
            "Lower ongoing portfolio complexity",
        ],
        "cons": [
            "Tracking error vs individual holdings",
            "ETF may have holdings you don't want exposure to",
            "Transaction costs on multiple sells",
        ],
        "trades": sell_trades + [buy_trade],
        "estimated_cost": etf_total_cost,
    }

    # Strategy 2: Synthetic Long on ETF
    total_shares_equiv = etf_shares_to_buy
    synthetic_result = _build_synthetic_long_legs(etf_ticker, total_shares_equiv, target_days=50)
    synthetic_cost = abs(synthetic_result.get("net_debit", 0)) if "error" not in synthetic_result else 0

    strategy_2 = {
        "id": "unified_synthetic",
        "name": "Unified Synthetic Long (ETF Options)",
        "type": "Options",
        "badge": "Capital Efficient",
        "description": f"Sell all losers. Replicate {etf_ticker} exposure via deep ITM call + short OTM put. Much less capital than buying ETF shares outright.",
        "pros": [
            "Capital efficient — fraction of full share cost",
            f"Realizes ${total_losses:,.0f} in losses",
            "~100 delta exposure to ETF",
            "Wash-sale safe (different underlying)",
        ],
        "cons": [
            "Requires options approval (Level 3+)",
            "Assignment risk on short put",
            "Needs rolling at expiration",
        ],
        "trades": sell_trades,
        **({} if "error" in synthetic_result else {
            "legs": synthetic_result["legs"],
            "contracts": synthetic_result["contracts"],
            "net_debit": synthetic_result["net_debit"],
            "delta_exposure": synthetic_result["delta_exposure"],
            "expiration": synthetic_result["expiration"],
            "dte": synthetic_result["dte"],
        }),
        "estimated_cost": round(synthetic_cost, 2),
        **({"optionsError": synthetic_result["error"]} if "error" in synthetic_result else {}),
    }

    # Strategy 3: Protective Collar on ETF
    collar_result = _build_protective_collar_legs(etf_ticker, etf_shares_to_buy, target_days=50)
    collar_cost = collar_result.get("total_cost", 0) if "error" not in collar_result else 0

    strategy_3 = {
        "id": "unified_collar",
        "name": "Unified Protective Collar (ETF)",
        "type": "Options + Equity",
        "badge": "Best Protection",
        "description": f"Buy {etf_shares_to_buy} shares of {etf_ticker} + protective put + covered call. Full downside protection with capped upside.",
        "pros": [
            "Full downside protection via put",
            "Collar often near zero net cost",
            f"Realizes ${total_losses:,.0f} in losses",
            "Single ETF simplifies management",
        ],
        "cons": [
            "Upside capped by short call",
            "Requires buying shares + options",
            "Options approval required",
        ],
        "trades": sell_trades,
        **({} if "error" in collar_result else {
            "legs": collar_result["legs"],
            "contracts": collar_result["contracts"],
            "shares_cost": collar_result["shares_cost"],
            "net_collar_cost": collar_result["net_collar_cost"],
            "protection_range": collar_result["protection_range"],
            "expiration": collar_result["expiration"],
            "dte": collar_result["dte"],
        }),
        "estimated_cost": round(collar_cost, 2),
        **({"optionsError": collar_result["error"]} if "error" in collar_result else {}),
    }

    return [strategy_1, strategy_2, strategy_3]


# ---------------------------------------------------------------------------
# Quant-level portfolio optimisation engine
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Sector / industry peer lookup (cached to avoid yfinance rate limits)
# Used as fallback when no LLM API key is available.
# ---------------------------------------------------------------------------

@lru_cache(maxsize=512)
def _get_ticker_info(ticker: str) -> dict:
    """Single cached yfinance .info call per ticker — avoids repeat HTTP calls."""
    try:
        return yf.Ticker(ticker).info or {}
    except Exception:
        return {}


# Curated GICS industry → peer stocks (wash-sale compliant alternatives)
_INDUSTRY_PEERS: dict[str, list[str]] = {
    "Semiconductors": ["NVDA", "AMD", "INTC", "QCOM", "AVGO", "MU", "AMAT", "LRCX", "KLAC", "TXN", "MRVL", "ON", "MCHP"],
    "Semiconductor Equipment": ["AMAT", "LRCX", "KLAC", "ASML", "ENTG", "ONTO"],
    "Software - Application": ["MSFT", "CRM", "ORCL", "NOW", "ADBE", "INTU", "WDAY", "CDNS", "TEAM", "HUBS"],
    "Software - Infrastructure": ["MSFT", "ORCL", "IBM", "PANW", "FTNT", "ZS", "CRWD", "OKTA", "NET"],
    "Internet Content & Information": ["GOOGL", "META", "SNAP", "PINS", "IAC"],
    "Consumer Electronics": ["AAPL", "SONO", "GPRO", "ROKU"],
    "Electronic Components": ["GLW", "TDY", "KEYS", "FFIV", "TRMB"],  # removed APLE (REIT, wrong co)
    "IT Services": ["ACN", "IBM", "CTSH", "IT", "EPAM"],  # removed WIT (ADR liquidity issues)
    "Biotechnology": ["AMGN", "GILD", "BIIB", "VRTX", "REGN", "MRNA", "BNTX", "INCY", "ALNY"],
    "Drug Manufacturers - General": ["JNJ", "PFE", "MRK", "ABBV", "LLY", "BMY", "NVO", "AZN"],
    "Drug Manufacturers - Specialty & Generic": ["TEVA", "VTRS", "JAZZ", "PRGO", "ENDP"],  # MYL→VTRS, removed AGN (delisted 2020) & HZN (delisted 2023)
    "Health Care Plans": ["UNH", "CVS", "CI", "HUM", "ELV", "MOH", "CNC"],
    "Medical Devices": ["MDT", "SYK", "BSX", "EW", "ISRG", "ZBH", "RMD"],
    "Banks - Diversified": ["JPM", "BAC", "WFC", "C", "USB", "TFC", "PNC"],
    "Banks - Regional": ["FITB", "HBAN", "RF", "KEY", "CFG", "ZION", "WAL"],
    "Capital Markets": ["GS", "MS", "BLK", "SCHW", "ICE", "CME", "CBOE"],
    "Insurance": ["BRK-B", "MET", "PRU", "AFL", "AIG", "TRV", "CB"],
    "Credit Services": ["V", "MA", "AXP", "DFS", "SYF", "COF"],
    "Asset Management": ["BLK", "APO", "KKR", "BAM", "IVZ", "BEN", "AMG"],
    "Oil & Gas E&P": ["XOM", "CVX", "COP", "EOG", "DVN", "MRO", "OXY", "FANG"],  # removed PXD (acquired by XOM May 2024)
    "Oil & Gas Integrated": ["XOM", "CVX", "COP", "BP", "SHEL", "TTE"],  # TOT→TTE (TotalEnergies NYSE ticker)
    "Oil & Gas Midstream": ["EPD", "MPC", "VLO", "PSX", "KMI", "WMB", "OKE"],
    "Specialty Retail": ["AMZN", "HD", "LOW", "TJX", "ROST", "ORLY", "AZO", "BBY"],
    "Discount Stores": ["WMT", "COST", "TGT", "DLTR", "DG", "BJ"],
    "Auto Manufacturers": ["TSLA", "F", "GM", "TM", "HMC", "STLA", "RIVN", "LCID"],
    "Auto Parts": ["BWA", "LEA", "AXL", "DAN", "APTV", "LKQ"],  # removed MO (Altria=tobacco, wrong co)
    "Aerospace & Defense": ["BA", "LMT", "RTX", "NOC", "GD", "HII", "LHX"],  # L3H→LHX
    "Industrial Conglomerates": ["GE", "HON", "MMM", "EMR", "ETN", "PH"],
    "Specialty Industrial Machinery": ["CAT", "DE", "PCAR", "CMI", "ITW", "ROK", "DOV"],
    "Trucking": ["UPS", "FDX", "ODFL", "SAIA", "XPO", "JBHT"],
    "Integrated Freight & Logistics": ["UPS", "FDX", "AMZN", "GXO", "CHRW"],
    "Telecom Services": ["T", "VZ", "TMUS", "CMCSA", "CHTR"],  # removed DISH (bankruptcy)
    "Entertainment": ["DIS", "NFLX", "WBD", "FOX", "AMZN"],  # removed PARA (merger uncertainty)
    "REIT - Diversified": ["O", "VTR", "WPC", "NNN", "ADC"],  # removed SRC (acquired by O 2024), STORE (acquired 2023)
    "REIT - Industrial": ["PLD", "REXR", "FR", "EGP", "STAG"],  # removed DRE (acquired by PLD 2022)
    "REIT - Retail": ["SPG", "O", "KIM", "REG", "BRX"],
    "REIT - Office": ["BXP", "ARE", "SLG", "HIW", "CUZ"],
    "REIT - Residential": ["AMT", "EQIX", "PSA", "EQR", "AVB", "ESS", "MAA"],
    "Utilities - Diversified": ["NEE", "DUK", "SO", "D", "EXC", "SRE", "PCG"],
    "Utilities - Regulated Electric": ["NEE", "DUK", "SO", "AEP", "EXC", "XEL", "PPL"],
    "Basic Materials - Chemicals": ["DOW", "LYB", "EMN", "CE", "OLN", "RPM"],
    "Gold": ["NEM", "GOLD", "AEM", "KGC", "WPM", "RGLD"],
    # Crypto & blockchain — not a standard GICS category, but needed for niche tickers
    "Crypto & Blockchain": ["MSTR", "COIN", "RIOT", "MARA", "CLSK", "HUT", "BITF", "CIFR", "BTDR", "CORZ"],
    "Bitcoin Mining": ["MARA", "RIOT", "CLSK", "HUT", "BITF", "CIFR", "BTDR", "CORZ", "IREN"],
    "Financial Data & Stock Exchanges": ["CME", "ICE", "CBOE", "NDAQ", "MSCI", "SPGI", "MCO"],
    "Cannabis": ["TLRY", "CGC", "ACB", "OGI", "SNDL", "VFF", "CRON"],
    "Space & Satellite": ["RKLB", "LUNR", "ASTS", "BKSY", "RDW", "SPIR", "PL"],
}

# Sector ETF map — used as fallback when no LLM key is available
_SECTOR_PEER_ETFS: dict[str, list[str]] = {
    "Technology": ["XLK", "QQQ", "VGT", "IGV", "SOXX", "SMH"],
    "Financial Services": ["XLF", "VFH", "KRE", "KBE", "IAI"],
    "Healthcare": ["XLV", "VHT", "IBB", "XBI", "IHI"],
    "Consumer Cyclical": ["XLY", "VCR", "XRT", "ONLN"],
    "Consumer Defensive": ["XLP", "VDC", "IYK"],
    "Energy": ["XLE", "VDE", "XOP", "OIH", "IEO"],
    "Utilities": ["XLU", "VPU", "IDU"],
    "Industrials": ["XLI", "VIS", "ITA", "XAR"],
    "Basic Materials": ["XLB", "VAW", "PICK", "GDX"],
    "Real Estate": ["XLRE", "VNQ", "IYR"],
    "Communication Services": ["XLC", "VOX", "IYZ"],
    "Crypto & Blockchain": ["BITQ", "BKCH", "DAPP", "IBLC", "BLOK"],
}


def _get_sector_peers(ticker: str, max_peers: int = 40) -> list[str]:
    """Return sector/industry-matched peer tickers for `ticker`.

    Uses cached yfinance info — no extra HTTP calls if _get_ticker_info
    was already called for this ticker. Returns empty list if sector info
    is unavailable (caller should fall back to _FALLBACK_ETFS).
    """
    info = _get_ticker_info(ticker)
    sector = info.get("sector", "") or ""
    industry = info.get("industry", "") or ""

    peers: list[str] = []

    # Industry-level peers (most specific — same GICS industry group)
    if industry:
        for key, peer_list in _INDUSTRY_PEERS.items():
            if key.lower() in industry.lower() or industry.lower() in key.lower():
                peers.extend(p for p in peer_list if p != ticker)
                break

    # Sector ETFs (always add — very wash-sale safe, low TE)
    sector_etfs = _SECTOR_PEER_ETFS.get(sector, [])
    combined = list(dict.fromkeys(peers + sector_etfs))  # dedup, preserve order
    return combined[:max_peers]


def _compute_quant_metrics_base(
    original_returns: pd.Series,
    replacement_returns: pd.Series,
) -> dict:
    """Core correlation / tracking-error metrics from aligned daily returns."""
    common = pd.DataFrame({"orig": original_returns, "repl": replacement_returns}).dropna()
    if len(common) < 20:
        return {}

    orig = common["orig"]
    repl = common["repl"]

    correlation = float(orig.corr(repl))
    
    # Active return mathematically expects (Portfolio - Benchmark)
    # Using repl - orig properly aligns all downstream metrics mathematically
    active_return_daily = repl - orig
    
    tracking_error = float(active_return_daily.std() * math.sqrt(252))  # annualised
    mean_active_return = float(active_return_daily.mean() * 252)

    # Beta: cov(repl, orig) / var(orig)
    beta = float(orig.cov(repl) / orig.var()) if orig.var() != 0 else 1.0

    # R² — proportion of variance explained
    r_squared = correlation ** 2

    # Z-score of the cumulative return-difference drift (statistical significance)
    # We test the hypothesis that mean active return == 0
    # Z = mean_diff / StandardError(mean_diff)
    diff_mean = active_return_daily.mean()
    diff_std = active_return_daily.std()
    
    if diff_std > 0:
        se_mean = diff_std / math.sqrt(len(active_return_daily))
        z_score = float(diff_mean / se_mean)
    else:
        z_score = 0.0

    # Information ratio: Annualized Outperformance / Annualized Tracking Error
    info_ratio = float(mean_active_return / tracking_error) if tracking_error != 0 else 0.0

    # Max drawdown of the tracking difference
    # Because active_return = repl - orig, positive means repl is outperforming.
    # Therefore, a cumulative tracking DRAWDOWN correctly represents the worst 
    # continuous period of UNDERPERFORMANCE (bleeding against the benchmark).
    cum_active = active_return_daily.cumsum()
    peak = cum_active.cummax()
    dd = cum_active - peak
    max_dd = float(dd.min())

    return {
        "correlation": round(correlation, 6),
        "correlationPct": round(correlation * 100, 2),
        "trackingError": round(tracking_error * 100, 2),     # as %
        "trackingErrorAnn": round(tracking_error, 6),
        "beta": round(beta, 4),
        "rSquared": round(r_squared, 4),
        "rSquaredPct": round(r_squared * 100, 2),
        "zScore": round(z_score, 4),
        "informationRatio": round(info_ratio, 4),
        "annualizedDrift": round(mean_active_return * 100, 2),         # as %
        "maxTrackingDrawdown": round(max_dd * 100, 2),        # as %
    }


def _build_weighted_return_series(
    price_data: dict[str, pd.Series],
    weights: dict[str, float],
) -> pd.Series:
    """Build a value-weighted daily return series from price data + weights."""
    frames = {}
    for ticker, w in weights.items():
        if ticker in price_data and not price_data[ticker].empty and w > 0:
            frames[ticker] = price_data[ticker]
    if not frames:
        return pd.Series(dtype=float)
    combined = pd.DataFrame(frames).dropna()
    if combined.empty:
        return pd.Series(dtype=float)
    returns = combined.pct_change().dropna()
    weighted = pd.Series(0.0, index=returns.index)
    total_w = sum(weights.get(t, 0) for t in returns.columns)
    if total_w == 0:
        return pd.Series(dtype=float)
    for t in returns.columns:
        weighted += returns[t] * (weights.get(t, 0) / total_w)
    return weighted


# ---------------------------------------------------------------------------
# Ledoit-Wolf shrinkage covariance + TE decomposition helpers
# ---------------------------------------------------------------------------

def _shrinkage_cov(return_matrix: np.ndarray) -> np.ndarray:
    """Ledoit-Wolf analytical shrinkage estimator.

    More stable than sample covariance when n_assets is non-trivial vs T.
    Falls back to sample covariance if sklearn is unavailable.
    """
    try:
        from sklearn.covariance import LedoitWolf
        lw = LedoitWolf()
        lw.fit(return_matrix.T)  # (T x n)
        return lw.covariance_
    except Exception:
        return np.cov(return_matrix)


def _decompose_te(
    replacement_returns: np.ndarray,
    original_returns: np.ndarray,
    factor_return_matrix: np.ndarray | None = None,
) -> dict:
    """Decompose annualised TE into systematic and idiosyncratic components.

    If factor_return_matrix is None, returns only te_total with zeros for
    systematic/idiosyncratic (graceful degradation).
    """
    diff = replacement_returns - original_returns
    te_total = float(np.std(diff) * np.sqrt(252)) * 100  # as %

    if factor_return_matrix is None or factor_return_matrix.shape[0] < 20:
        return {
            "teTotal": round(te_total, 2),
            "teSystematic": 0.0,
            "teIdiosyncratic": round(te_total, 2),
            "pctSystematic": 0.0,
        }

    try:
        F = factor_return_matrix
        beta, _, _, _ = np.linalg.lstsq(F, diff, rcond=None)
        systematic = F @ beta
        idiosyncratic = diff - systematic

        te_sys = float(np.std(systematic) * np.sqrt(252)) * 100
        te_idio = float(np.std(idiosyncratic) * np.sqrt(252)) * 100
        pct_sys = round((te_sys / te_total) * 100, 1) if te_total > 0 else 0.0

        return {
            "teTotal": round(te_total, 2),
            "teSystematic": round(te_sys, 2),
            "teIdiosyncratic": round(te_idio, 2),
            "pctSystematic": pct_sys,
        }
    except Exception:
        return {
            "teTotal": round(te_total, 2),
            "teSystematic": 0.0,
            "teIdiosyncratic": round(te_total, 2),
            "pctSystematic": 0.0,
        }


# ---------------------------------------------------------------------------
# Institutional-grade Factor Analysis Engine
# Barra-style multi-factor risk model using price-derived factor proxies
# ---------------------------------------------------------------------------

# Factor proxy ETFs — the *legs* we download to build factor returns.
# (These are only the raw price series; the actual factors are the
# orthogonalized long/short spreads defined in FACTOR_SPREAD_DEFS below.)
FACTOR_PROXY_ETFS = {
    "market":    "SPY",    # Market factor (CAPM beta)
    "size":      "IWM",    # Small-cap
    "value":     "IWD",    # Value
    "growth":    "IWF",    # Growth (used as the short leg of the value spread)
    "momentum":  "MTUM",   # Momentum
    "quality":   "QUAL",   # Quality
    "volatility":"SPLV",   # Low-vol
}

# Orthogonalized factor-mimicking return spreads.
#
# Long-only style/sector ETFs are nearly collinear with the broad market, so
# regressing a stock's returns on them yields unstable, economically
# meaningless "loadings" (everything loads ~1 because everything ~= the market).
# Institutional risk models (Barra/Axioma, AQR factor work) instead use
# long/short factor-mimicking portfolios. We approximate those with tradable
# ETF spreads so each loading is an interpretable *style tilt* and the
# factor-neutralization constraints in the optimizer constrain style rather
# than noise.  Format: factor -> (long_leg, short_leg | None).  A None short
# leg means the raw series (used for the market factor).
FACTOR_SPREAD_DEFS = {
    "market":     ("SPY", None),    # market return (CAPM proxy)
    "size":       ("IWM", "SPY"),   # small minus broad        (SMB)
    "value":      ("IWD", "IWF"),   # value minus growth       (HML)
    "momentum":   ("MTUM", "SPY"),  # momentum minus market    (WML)
    "quality":    ("QUAL", "SPY"),  # quality minus market     (QMJ)
    "volatility": ("SPLV", "SPY"),  # low-vol minus market     (BAB-ish)
}


def _build_factor_spreads(price_data: dict[str, pd.Series]) -> dict[str, pd.Series]:
    """Build orthogonalized factor-mimicking daily-return series from price data.

    Each style factor is a long/short spread (e.g. value = IWD - IWF) so the
    resulting loadings are interpretable style tilts rather than collinear,
    near-market betas. ``market`` is the raw SPY return. Returns
    ``{factor_name: return_series}`` for every factor whose legs are present.
    """
    spreads: dict[str, pd.Series] = {}
    for fname, (long_leg, short_leg) in FACTOR_SPREAD_DEFS.items():
        long_s = price_data.get(long_leg)
        if long_s is None or long_s.empty:
            continue
        long_ret = long_s.pct_change()
        if short_leg is None:
            s = long_ret.dropna()
        else:
            short_s = price_data.get(short_leg)
            if short_s is None or short_s.empty:
                continue
            s = (long_ret - short_s.pct_change()).dropna()
        if not s.empty:
            spreads[fname] = s
    return spreads

SECTOR_PROXY_ETFS = {
    "Technology":              "XLK",
    "Financial Services":      "XLF",
    "Healthcare":              "XLV",
    "Consumer Cyclical":       "XLY",
    "Consumer Defensive":      "XLP",
    "Energy":                  "XLE",
    "Utilities":               "XLU",
    "Industrials":             "XLI",
    "Basic Materials":         "XLB",
    "Real Estate":             "XLRE",
    "Communication Services":  "XLC",
}


async def _compute_factor_loadings(
    ticker: str,
    price_data: dict[str, pd.Series] | None = None,
) -> dict:
    """Compute Barra-style factor loadings for a single ticker.

    Returns dict with factor names -> beta loadings, plus idiosyncratic vol.
    Uses 1Y daily returns regressed against orthogonalized factor-mimicking
    spreads (see ``_build_factor_spreads``) rather than collinear long-only
    proxies, so the loadings are interpretable style tilts.
    """
    # Fetch ticker price if not in price_data
    if price_data and ticker in price_data:
        ticker_prices = price_data[ticker]
    else:
        ticker_prices = await _fetch_historical_data(ticker, "1y")
    if ticker_prices.empty:
        return {}

    ticker_returns = ticker_prices.pct_change().dropna()

    # Assemble the raw factor legs (from price_data or fetched on demand),
    # then build orthogonalized factor-mimicking return spreads from them.
    legs_price: dict[str, pd.Series] = {}
    for leg in set(FACTOR_PROXY_ETFS.values()):
        if price_data and leg in price_data and not price_data[leg].empty:
            legs_price[leg] = price_data[leg]
        else:
            fd = await _fetch_historical_data(leg, "1y")
            if not fd.empty:
                legs_price[leg] = fd

    factor_data = _build_factor_spreads(legs_price)
    if not factor_data:
        return {}

    # Build aligned DataFrame
    all_data = {"ticker": ticker_returns}
    all_data.update(factor_data)
    df = pd.DataFrame(all_data).dropna()

    if len(df) < 60:
        return {}

    y = df["ticker"].values
    X_cols = [c for c in df.columns if c != "ticker"]
    X = df[X_cols].values
    X = np.column_stack([np.ones(len(X)), X])  # Add intercept

    try:
        # OLS regression: y = α + Σ(βi * fi) + ε
        betas, residuals, _, _ = np.linalg.lstsq(X, y, rcond=None)
        alpha = betas[0]
        factor_betas = {X_cols[i]: round(float(betas[i + 1]), 4) for i in range(len(X_cols))}

        # Compute R², residual vol
        y_pred = X @ betas
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0

        idio_vol = float(np.std(y - y_pred) * np.sqrt(252))  # annualized

        # Also compute basic stats
        ann_vol = float(np.std(y) * np.sqrt(252))
        ann_return = float(np.mean(y) * 252)

        # Simple CAPM beta (cov(ticker, market) / var(market))
        # Multi-factor betas can be distorted by multicollinearity;
        # CAPM beta is more intuitive for display purposes.
        capm_beta = 1.0
        if "market" in factor_data:
            mkt_common = pd.DataFrame({"t": ticker_returns, "m": factor_data["market"]}).dropna()
            if len(mkt_common) >= 20:
                capm_beta = float(mkt_common["t"].cov(mkt_common["m"]) / mkt_common["m"].var())

        return {
            "ticker": ticker,
            "factorLoadings": factor_betas,
            "capmBeta": round(capm_beta, 4),
            "alpha": round(float(alpha) * 252, 6),  # annualized alpha
            "rSquared": round(float(r_squared), 4),
            "idiosyncraticVol": round(idio_vol, 4),
            "annualizedVol": round(ann_vol, 4),
            "annualizedReturn": round(ann_return, 4),
        }
    except Exception as e:
        print(f"Factor regression failed for {ticker}: {e}")
        return {}


def _compute_factor_drift(
    original_loadings: dict,
    replacement_loadings: dict,
) -> dict:
    """Compute the factor drift between original and replacement portfolios.

    Returns per-factor drift, aggregate drift (L2 norm), and style drift flags.
    """
    if not original_loadings or not replacement_loadings:
        return {}

    orig_factors = original_loadings.get("factorLoadings", {})
    repl_factors = replacement_loadings.get("factorLoadings", {})

    all_factors = set(list(orig_factors.keys()) + list(repl_factors.keys()))
    per_factor = {}
    drifts_sq = []

    for f in sorted(all_factors):
        o = orig_factors.get(f, 0)
        r = repl_factors.get(f, 0)
        diff = r - o
        per_factor[f] = {
            "original": round(o, 4),
            "replacement": round(r, 4),
            "drift": round(diff, 4),
            "absDrift": round(abs(diff), 4),
        }
        drifts_sq.append(diff ** 2)

    aggregate_drift = math.sqrt(sum(drifts_sq)) if drifts_sq else 0

    # Style drift: flag if any single factor drifts > 0.15 or aggregate > 0.3
    style_drift_flags = [
        f for f, d in per_factor.items() if d["absDrift"] > 0.15
    ]

    # CAPM beta (simple, intuitive) — separate from multi-factor tilts
    orig_capm = original_loadings.get("capmBeta", 1.0)
    repl_capm = replacement_loadings.get("capmBeta", 1.0)
    capm_drift = repl_capm - orig_capm

    return {
        "perFactor": per_factor,
        "capmBeta": {
            "original": round(orig_capm, 4),
            "replacement": round(repl_capm, 4),
            "drift": round(capm_drift, 4),
            "absDrift": round(abs(capm_drift), 4),
        },
        "aggregateDrift": round(aggregate_drift, 4),
        "styleDriftFlags": style_drift_flags,
        "hasStyleDrift": len(style_drift_flags) > 0 or aggregate_drift > 0.3,
        "driftRating": (
            "Excellent" if aggregate_drift < 0.1 else
            "Good" if aggregate_drift < 0.2 else
            "Moderate" if aggregate_drift < 0.35 else
            "High" if aggregate_drift < 0.5 else
            "Critical"
        ),
    }


def _compute_trade_trigger(
    tax_benefit: float,
    total_harvestable_value: float,
    tracking_error_ann: float,
    transaction_cost_bps: float = 10.0,
    holding_period_days: int = 30,
) -> dict:
    """Institutional trigger-based harvesting check.

    Trade only if: Tax Benefit > Transaction Cost + Expected TE Cost
    """
    # Transaction cost
    txn_cost = total_harvestable_value * (transaction_cost_bps / 10000) * 2  # round-trip

    # Expected TE cost = TE * √(holding_period / 252) * capital
    te_cost = (
        tracking_error_ann
        * math.sqrt(holding_period_days / 252)
        * total_harvestable_value
    )

    total_cost = txn_cost + te_cost
    net_benefit = tax_benefit - total_cost
    benefit_ratio = tax_benefit / total_cost if total_cost > 0 else float('inf')

    return {
        "taxBenefit": round(tax_benefit, 2),
        "transactionCost": round(txn_cost, 2),
        "trackingErrorCost": round(te_cost, 2),
        "totalCost": round(total_cost, 2),
        "netBenefit": round(net_benefit, 2),
        "benefitRatio": round(benefit_ratio, 2),
        "triggered": net_benefit > 0,
        "verdict": (
            "Strong Execute" if benefit_ratio > 3 else
            "Execute" if benefit_ratio > 1.5 else
            "Marginal" if benefit_ratio > 1 else
            "Do Not Execute"
        ),
        "verdictDetail": (
            f"Tax benefit (${tax_benefit:,.0f}) is {benefit_ratio:.1f}x the total cost "
            f"(${total_cost:,.0f} = ${txn_cost:,.0f} txn + ${te_cost:,.0f} TE). "
            + ("Trade is clearly beneficial." if benefit_ratio > 1.5
               else "Trade is marginal — consider waiting for a larger loss." if benefit_ratio > 1
               else "Cost exceeds benefit — not worth executing now.")
        ),
    }


async def _compute_portfolio_factor_profile(
    tickers: list[str],
    weights: dict[str, float],
    price_data: dict[str, pd.Series] | None = None,
) -> dict:
    """Compute weighted factor loadings for a portfolio of tickers.

    Returns the portfolio-level factor profile (weighted sum of individual loadings).
    """
    individual_loadings = {}
    tasks = [_compute_factor_loadings(t, price_data) for t in tickers]
    results = await asyncio.gather(*tasks)

    for t, result in zip(tickers, results):
        if result:
            individual_loadings[t] = result

    if not individual_loadings:
        return {}

    # Compute weighted average factor loadings
    all_factors = set()
    for il in individual_loadings.values():
        all_factors.update(il.get("factorLoadings", {}).keys())

    total_weight = sum(weights.get(t, 0) for t in individual_loadings)
    if total_weight == 0:
        return {}

    portfolio_loadings = {}
    for f in sorted(all_factors):
        weighted_sum = sum(
            individual_loadings[t]["factorLoadings"].get(f, 0) * (weights.get(t, 0) / total_weight)
            for t in individual_loadings
        )
        portfolio_loadings[f] = round(weighted_sum, 4)

    # Portfolio-level stats
    weighted_vol = sum(
        individual_loadings[t].get("annualizedVol", 0) * (weights.get(t, 0) / total_weight)
        for t in individual_loadings
    )
    weighted_r2 = sum(
        individual_loadings[t].get("rSquared", 0) * (weights.get(t, 0) / total_weight)
        for t in individual_loadings
    )
    # Weighted CAPM beta
    weighted_capm_beta = sum(
        individual_loadings[t].get("capmBeta", 1.0) * (weights.get(t, 0) / total_weight)
        for t in individual_loadings
    )

    return {
        "factorLoadings": portfolio_loadings,
        "capmBeta": round(weighted_capm_beta, 4),
        "individualLoadings": {
            t: il["factorLoadings"] for t, il in individual_loadings.items()
        },
        "portfolioVol": round(weighted_vol, 4),
        "avgRSquared": round(weighted_r2, 4),
    }


async def _run_institutional_analysis(
    portfolio_series: pd.Series,
    holding_details: dict,
    harvestable_list: list[dict],
    all_tickers: list[str],
    best_etf_ticker: str,
    best_etf_correlation: float,
    total_losses: float,
    total_harvestable_value: float,
    tax_rate_pct: float,
    tracking_error_ann: float = 0.0,
) -> dict:
    """Run full institutional-grade TLH analysis:
    1. Factor profile of original portfolio
    2. Factor profile of replacement (ETF)
    3. Factor drift analysis
    4. Trade trigger check
    5. Sector exposure comparison
    """
    try:
        # Compute portfolio weights
        total_value = sum(
            holding_details.get(t, {}).get("currentValue", 0) for t in all_tickers
        )
        portfolio_weights = {
            t: holding_details.get(t, {}).get("currentValue", 0) / total_value
            for t in all_tickers
            if total_value > 0
        }

        # Pre-fetch factor proxy data
        all_fetch = list(set(all_tickers + [best_etf_ticker] + list(FACTOR_PROXY_ETFS.values())))
        
        fetch_results = await _fetch_historical_data_batch(all_fetch, "1y")
        price_data = {
            t: data for t, data in fetch_results.items() if not data.empty
        }

        # 1. Original portfolio factor profile
        original_profile = await _compute_portfolio_factor_profile(
            all_tickers, portfolio_weights, price_data
        )

        # 2. Replacement (ETF) factor profile
        etf_loadings = await _compute_factor_loadings(best_etf_ticker, price_data)

        # 3. Factor drift
        factor_drift = {}
        if original_profile and etf_loadings:
            factor_drift = _compute_factor_drift(
                {"factorLoadings": original_profile.get("factorLoadings", {}),
                 "capmBeta": original_profile.get("capmBeta", 1.0)},
                etf_loadings,
            )

        # 4. Trade trigger
        tax_benefit = total_losses * tax_rate_pct / 100
        te_ann = tracking_error_ann if tracking_error_ann > 0 else 0.05
        trade_trigger = _compute_trade_trigger(
            tax_benefit=tax_benefit,
            total_harvestable_value=total_harvestable_value,
            tracking_error_ann=te_ann,
        )

        # 5. Sector exposure
        sector_exposure_original = {}
        sector_exposure_etf = {}

        for t in all_tickers:
            try:
                info = _get_ticker_info(t)
                sector = info.get("sector", "Other")
                w = portfolio_weights.get(t, 0)
                sector_exposure_original[sector] = round(
                    sector_exposure_original.get(sector, 0) + w * 100, 2
                )
            except Exception:
                pass

        # ETF sector exposure — multi-source with fallback
        # Normalize yfinance sector names to match our standard names
        _SECTOR_NAME_MAP = {
            "technology": "Technology",
            "healthcare": "Healthcare",
            "financial_services": "Financial Services",
            "financialservices": "Financial Services",
            "consumer_cyclical": "Consumer Cyclical",
            "consumercyclical": "Consumer Cyclical",
            "consumer_defensive": "Consumer Defensive",
            "consumerdefensive": "Consumer Defensive",
            "communication_services": "Communication Services",
            "communicationservices": "Communication Services",
            "energy": "Energy",
            "industrials": "Industrials",
            "basic_materials": "Basic Materials",
            "basicmaterials": "Basic Materials",
            "real_estate": "Real Estate",
            "realestate": "Real Estate",
            "utilities": "Utilities",
        }

        def _normalize_sector(name: str) -> str:
            key = name.lower().replace(" ", "").replace("_", "")
            for pattern, standard in _SECTOR_NAME_MAP.items():
                if key == pattern.replace("_", ""):
                    return standard
            return name.replace("_", " ").title()

        try:
            info = _get_ticker_info(best_etf_ticker)
            sector_weights = info.get("sectorWeightings", [])
            for sw in sector_weights:
                for sector, weight in sw.items():
                    sector_name = _normalize_sector(sector)
                    sector_exposure_etf[sector_name] = round(float(weight) * 100, 2)
        except Exception:
            pass

        # Fallback: hardcoded sector allocations for common ETFs
        if not sector_exposure_etf:
            _ETF_SECTOR_FALLBACK = {
                "IGV": {"Technology": 100.0},
                "VGT": {"Technology": 100.0},
                "XLK": {"Technology": 100.0},
                "FTEC": {"Technology": 100.0},
                "IYW": {"Technology": 100.0},
                "SMH": {"Technology": 100.0},
                "SOXX": {"Technology": 100.0},
                "HACK": {"Technology": 100.0},
                "CIBR": {"Technology": 100.0},
                "XLF": {"Financial Services": 100.0},
                "VFH": {"Financial Services": 100.0},
                "KRE": {"Financial Services": 100.0},
                "XLV": {"Healthcare": 100.0},
                "VHT": {"Healthcare": 100.0},
                "IBB": {"Healthcare": 100.0},
                "XBI": {"Healthcare": 100.0},
                "XLE": {"Energy": 100.0},
                "VDE": {"Energy": 100.0},
                "XOP": {"Energy": 100.0},
                "XLI": {"Industrials": 100.0},
                "VIS": {"Industrials": 100.0},
                "XLB": {"Basic Materials": 100.0},
                "VAW": {"Basic Materials": 100.0},
                "XLU": {"Utilities": 100.0},
                "VPU": {"Utilities": 100.0},
                "XLC": {"Communication Services": 100.0},
                "VOX": {"Communication Services": 100.0},
                "XLY": {"Consumer Cyclical": 100.0},
                "VCR": {"Consumer Cyclical": 100.0},
                "XLP": {"Consumer Defensive": 100.0},
                "VDC": {"Consumer Defensive": 100.0},
                "XLRE": {"Real Estate": 100.0},
                "VNQ": {"Real Estate": 100.0},
                "QQQ": {"Technology": 57.0, "Communication Services": 16.0,
                         "Consumer Cyclical": 15.0, "Healthcare": 6.0,
                         "Industrials": 3.0, "Other": 3.0},
                "QQQM": {"Technology": 57.0, "Communication Services": 16.0,
                          "Consumer Cyclical": 15.0, "Healthcare": 6.0,
                          "Industrials": 3.0, "Other": 3.0},
                "SPY": {"Technology": 31.0, "Healthcare": 13.0,
                         "Financial Services": 13.0, "Consumer Cyclical": 10.0,
                         "Communication Services": 9.0, "Industrials": 8.0,
                         "Consumer Defensive": 6.0, "Energy": 4.0,
                         "Utilities": 2.5, "Real Estate": 2.0,
                         "Basic Materials": 1.5},
                "VOO": {"Technology": 31.0, "Healthcare": 13.0,
                         "Financial Services": 13.0, "Consumer Cyclical": 10.0,
                         "Communication Services": 9.0, "Industrials": 8.0,
                         "Consumer Defensive": 6.0, "Energy": 4.0,
                         "Utilities": 2.5, "Real Estate": 2.0,
                         "Basic Materials": 1.5},
                "VTI": {"Technology": 30.0, "Healthcare": 13.0,
                         "Financial Services": 13.0, "Consumer Cyclical": 10.0,
                         "Communication Services": 9.0, "Industrials": 9.0,
                         "Consumer Defensive": 6.0, "Energy": 4.0,
                         "Utilities": 3.0, "Real Estate": 2.0,
                         "Basic Materials": 1.0},
                "IWM": {"Healthcare": 17.0, "Industrials": 16.0,
                         "Financial Services": 15.0, "Technology": 13.0,
                         "Consumer Cyclical": 11.0, "Energy": 7.0,
                         "Real Estate": 6.0, "Consumer Defensive": 4.0,
                         "Basic Materials": 4.0, "Utilities": 4.0,
                         "Communication Services": 3.0},
                # Bond ETFs
                "AGG": {"Fixed Income": 100.0},
                "BND": {"Fixed Income": 100.0},
                "MUB": {"Fixed Income": 100.0},
                "TLT": {"Fixed Income": 100.0},
                "LQD": {"Fixed Income": 100.0},
                "HYG": {"Fixed Income": 100.0},
                "IEF": {"Fixed Income": 100.0},
                "SHY": {"Fixed Income": 100.0},
            }
            if best_etf_ticker in _ETF_SECTOR_FALLBACK:
                sector_exposure_etf = _ETF_SECTOR_FALLBACK[best_etf_ticker].copy()

        # Tracking error constraint check (institutional: < 50 bps = 0.50%)
        te_pct = te_ann * 100
        te_constraint = {
            "trackingErrorBps": round(te_pct * 100, 1),  # in basis points
            "trackingErrorPct": round(te_pct, 2),
            "withinInstitutionalLimit": te_pct < 0.50,
            "limit": "50 bps",
            "rating": (
                "Institutional Grade" if te_pct < 0.50 else
                "Acceptable" if te_pct < 1.0 else
                "Above Threshold" if te_pct < 2.0 else
                "High Risk"
            ),
        }

        return {
            "available": True,
            "originalFactorProfile": original_profile,
            "replacementFactorProfile": {
                "ticker": best_etf_ticker,
                **(etf_loadings or {}),
            },
            "factorDrift": factor_drift,
            "tradeTrigger": trade_trigger,
            "sectorExposure": {
                "original": sector_exposure_original,
                "replacement": sector_exposure_etf,
            },
            "trackingErrorConstraint": te_constraint,
        }

    except Exception as e:
        print(f"Institutional analysis failed (non-fatal): {e}")
        return {"available": False, "error": str(e)}


async def _get_price_safe(ticker: str) -> float:
    """Get current price for a ticker with 15-min TTL cache, returning 0 on failure."""
    now = _time.monotonic()
    entry = _CURRENT_PRICE_CACHE.get(ticker)
    if entry and entry[0] > now:
        return entry[1]
    try:
        info = _get_ticker_info(ticker)
        price = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0.0)
        if not price:
            hist = yf.Ticker(ticker).history(period="1d")
            if not hist.empty:
                price = float(hist["Close"].iloc[-1])
        if price:
            _CURRENT_PRICE_CACHE[ticker] = (now + _CURRENT_PRICE_TTL, price)
        return price
    except Exception:
        return 0.0


def _metrics_for_portfolio_vs_etf(
    portfolio_series: pd.Series, etf_series: pd.Series
) -> dict | None:
    """Align portfolio vs ETF levels; return chart + correlation fields, or None if too thin."""
    df = pd.DataFrame({"portfolio": portfolio_series, "etf": etf_series}).dropna()
    if len(df) < 20:
        return None
    norm_p = (df["portfolio"] / df["portfolio"].iloc[0]) * 100
    norm_e = (df["etf"] / df["etf"].iloc[0]) * 100
    spread = norm_e - norm_p
    mean_spread = float(spread.mean())
    std_spread = float(spread.std())
    ret_p = df["portfolio"].pct_change().dropna()
    ret_e = df["etf"].pct_change().dropna()
    aligned = pd.DataFrame({"p": ret_p, "e": ret_e}).dropna()
    if len(aligned) < 10:
        return None
    corr = float(aligned["p"].corr(aligned["e"]))

    # Active (tracking) return of the ETF vs the portfolio.
    active = aligned["e"] - aligned["p"]
    tracking_error_ann = float(active.std() * math.sqrt(252))   # annualized

    # Proper standardized drift statistic: t-stat of mean active return
    # (mean / standard-error). Unified with _compute_quant_metrics_base so the
    # same "z-score" means the same thing everywhere in the engine.
    active_std = float(active.std())
    if active_std > 0:
        z_score = float(active.mean() / (active_std / math.sqrt(len(active))))
    else:
        z_score = 0.0

    # Beta of the ETF on the portfolio (≈1 ⇒ matched market exposure).
    port_var = float(aligned["p"].var())
    beta = float(aligned["e"].cov(aligned["p"]) / port_var) if port_var > 0 else 1.0

    return {
        "correlation": corr,
        "z_score": z_score,
        "trackingError": round(tracking_error_ann * 100, 2),    # annualized %
        "trackingErrorAnn": tracking_error_ann,
        "beta": round(beta, 4),
        "spreadMean": mean_spread,
        "spreadStd": std_spread,
        "dates": df.index.strftime("%Y-%m-%d").tolist(),
        "portfolioNorm": norm_p.tolist(),
        "etfNorm": norm_e.tolist(),
        "spreadHistory": spread.tolist(),
    }


async def _build_portfolio_series(holdings: list[dict]) -> tuple[pd.Series, dict]:
    """Value-weighted portfolio price path (fixed share counts) and per-ticker P&L detail.

    Each holding uses ``cost_basis`` as average cost **per share** (matches TLH UI).
    ``holding_details`` keys are upper-case tickers.
    """
    merged: dict[str, dict] = {}
    for h in holdings:
        t = str(h.get("ticker", "")).upper().strip()
        if not t:
            continue
        sh = float(h.get("shares", 0))
        cb = float(h.get("cost_basis", h.get("costBasis", 0)))
        if sh <= 0 or cb <= 0:
            continue
        if t in merged:
            prev = merged[t]
            tot_sh = prev["shares"] + sh
            cb = (
                (prev["shares"] * prev["cost_basis"] + sh * cb) / tot_sh
                if tot_sh > 0
                else cb
            )
            merged[t] = {"shares": tot_sh, "cost_basis": cb}
        else:
            merged[t] = {"shares": sh, "cost_basis": cb}

    if not merged:
        return pd.Series(dtype=float), {}

    tickers = list(merged.keys())
    price_map = await _fetch_historical_data_batch(tickers, "1y")
    frames: dict[str, pd.Series] = {}
    for t in tickers:
        s = price_map.get(t)
        if s is not None and not s.empty:
            frames[t] = s

    holding_details: dict[str, dict] = {}
    portfolio_series = pd.Series(dtype=float)

    if frames:
        df = pd.DataFrame(frames).dropna(how="any")
        if not df.empty:
            port = pd.Series(0.0, index=df.index)
            for t in df.columns:
                if t in merged:
                    port = port + merged[t]["shares"] * df[t]
            portfolio_series = port

    for t in tickers:
        m = merged[t]
        sh, cb = m["shares"], m["cost_basis"]
        cur_px: float | None = None
        if t in frames and frames[t] is not None and not frames[t].empty:
            cur_px = float(frames[t].iloc[-1])
        if cur_px is None or cur_px <= 0:
            cur_px = await _get_price_safe(t)
        cost_val = round(sh * cb, 2)
        cur_val = round(sh * cur_px, 2) if cur_px else 0.0
        unrealized = round(cur_val - cost_val, 2)

        # Detect delisted / no-data tickers
        is_delisted = (cur_px is None or cur_px <= 0) and t not in frames

        holding_details[t] = {
            "ticker": t,
            "shares": sh,
            "costBasis": round(cb, 4),
            "currentPrice": round(cur_px, 4) if cur_px else None,
            "currentValue": cur_val,
            "costValue": cost_val,
            "unrealizedPnl": unrealized,
            "pnlPct": round((unrealized / cost_val) * 100, 2) if cost_val else 0.0,
            "harvestable": unrealized < 0,
            "delisted": is_delisted,
            "warning": f"{t} appears to be delisted or has no market data. Treated as 100% loss for harvesting purposes." if is_delisted else None,
        }

    if portfolio_series.empty:
        return pd.Series(dtype=float), holding_details
    return portfolio_series.dropna(), holding_details


async def _call_llm_for_replacements(
    harvest_tickers: list[str],
    keep_tickers: list[str],
    openai_key: str,
    extra_context: str = "",
) -> dict:
    """Call LLM to suggest replacement stocks + ETFs for harvested positions.

    Returns ``{"stocks": [...], "etfs": [...], "rationale": "..."}`` or ``{}`` on failure.
    LLM responses are cached for 4 hours keyed by sorted tickers.
    """
    import json
    from .llm_service import call_llm

    cache_key = f"replacements:{','.join(sorted(harvest_tickers))}:{extra_context}"
    now = _time.monotonic()
    entry = _LLM_CACHE.get(cache_key)
    if entry and entry[0] > now:
        return entry[1]

    harvest_info_lines = []
    for t in harvest_tickers:
        info = _get_ticker_info(t)
        sector_str = info.get("sector", "Unknown")
        industry_str = info.get("industry", "Unknown")
        biz_summary = info.get("longBusinessSummary", "")
        # Take first 200 chars of business summary for context
        biz_short = (biz_summary[:200] + "...") if len(biz_summary) > 200 else biz_summary
        harvest_info_lines.append(
            f"  {t} (sector: {sector_str}, industry: {industry_str})"
            + (f"\n    Business: {biz_short}" if biz_short else "")
        )
    harvest_info_block = "\n".join(harvest_info_lines) if harvest_info_lines else "  (unknown)"

    prompt = f"""You are an institutional quantitative analyst specializing in Direct Indexing and Tax Loss Harvesting.

HARVESTED POSITIONS (sector, industry, and business context):
{harvest_info_block}

RETAINED POSITIONS (avoid overlap): {', '.join(keep_tickers) if keep_tickers else 'None'}

YOUR TASK:
Suggest 8 replacement STOCKS and 5 replacement ETFs that minimize structural tracking error against the harvested positions.

REQUIREMENTS:
1. WASH SALE: Do NOT suggest any of: {', '.join(harvest_tickers)}
2. BUSINESS MODEL MATCH: Replacements must operate in the SAME core business area as the harvested tickers. Pay close attention to the actual business description — for example, a crypto mining company should be replaced with other crypto/blockchain companies (e.g., MSTR, COIN, RIOT, MARA, CLSK, HUT), NOT generic tech or financial stocks. A biotech should be replaced with other biotechs in the same therapeutic area.
3. SECTOR & INDUSTRY MATCH: Stocks must be in the SAME GICS sector and preferably same sub-industry. If the company operates in an emerging or niche area (crypto, cannabis, space, etc.), prioritize companies in that niche over broad sector matches.
4. FACTOR MATCH: Market cap, beta, and volatility profile should be similar to the harvested assets.
5. LIQUIDITY: US-listed only. No leveraged or inverse ETFs.
6. ETF FOCUS: ETFs should capture the same thematic/industry exposure (e.g., BITQ/BKCH for crypto, ARKG for genomics, XBI for biotech, SOXX for semis, etc.)

Return ONLY valid JSON with no markdown:
{{"stocks": ["T1","T2","T3","T4","T5","T6","T7","T8"], "etfs": ["E1","E2","E3","E4","E5"], "rationale": "One sentence on factor/sector alignment."}}"""

    try:
        resp = await call_llm(openai_key, "gpt-4o", [{"role": "user", "content": prompt}], max_tokens=500, temperature=0, expect_json=True)
        if "```json" in resp:
            resp = resp.split("```json")[-1].split("```")[0].strip()
        elif "```" in resp:
            resp = resp.split("```")[-1].split("```")[0].strip()
        resp = resp.strip()

        data = None
        try:
            data = json.loads(resp)
        except Exception:
            for i in range(len(resp), 0, -1):
                if resp[i - 1] == '}':
                    try:
                        data = json.loads(resp[:i])
                        break
                    except Exception:
                        pass
        if not data:
            raise ValueError("JSON parse failed")

        result = {
            "stocks": [t.upper() for t in data.get("stocks", [])[:8]],
            "etfs": [t.upper() for t in data.get("etfs", [])[:5]],
            "rationale": data.get("rationale", ""),
        }
        _LLM_CACHE[cache_key] = (now + _LLM_CACHE_TTL, result)
        return result
    except Exception as e:
        print(f"LLM replacement suggestions failed: {e}")
        return {}


async def _call_llm_for_etfs(
    portfolio_tickers: list[str],
    openai_key: str,
) -> list[str]:
    """Ask LLM for ETF candidates suited to replace a given equity portfolio.

    Returns a list of ETF tickers (may be empty on failure).
    Cached 4 hours per portfolio composition.
    """
    import json
    from .llm_service import call_llm

    cache_key = f"etfs:{','.join(sorted(portfolio_tickers))}"
    now = _time.monotonic()
    entry = _LLM_CACHE.get(cache_key)
    if entry and entry[0] > now:
        return entry[1]

    # Summarise sectors so the prompt is concise
    sector_counts: dict[str, int] = {}
    for t in portfolio_tickers:
        s = _get_ticker_info(t).get("sector", "Unknown")
        sector_counts[s] = sector_counts.get(s, 0) + 1
    top_sectors = sorted(sector_counts, key=sector_counts.get, reverse=True)[:5]

    prompt = f"""You are a quantitative portfolio manager. A US equity portfolio is concentrated in these sectors: {', '.join(top_sectors)}.

Suggest exactly 12 US-listed ETFs that best replicate this portfolio's factor/sector exposure for tax-loss harvesting.
Rules:
- Equity ETFs only (no bonds, no leveraged, no inverse, no commodities)
- Minimum $100M AUM, traded on NYSE/NASDAQ
- Mix of broad-market AND sector-specific ETFs suited to the sectors above

Return ONLY valid JSON with no markdown: {{"etfs": ["E1","E2",...,"E12"]}}"""

    try:
        resp = await call_llm(openai_key, "gpt-4o", [{"role": "user", "content": prompt}], max_tokens=300, temperature=0, expect_json=True)
        if "```json" in resp:
            resp = resp.split("```json")[-1].split("```")[0].strip()
        elif "```" in resp:
            resp = resp.split("```")[-1].split("```")[0].strip()
        data = json.loads(resp.strip())
        result = [t.upper() for t in data.get("etfs", [])[:12]]
        _LLM_CACHE[cache_key] = (now + _LLM_CACHE_TTL, result)
        return result
    except Exception as e:
        print(f"LLM ETF suggestions failed: {e}")
        return []


def _rank_score_for_etf(row: dict) -> float:
    """Lightweight ranking score for a candidate ETF (higher = better).

    Tracking-error-first, the way an institution screens a tracking basket.
    Raw correlation is only a tie-breaker because, across equities, it is
    largely spurious (everything co-moves with the market).
    """
    te = row.get("trackingError", 100.0)        # annualized %, lower better
    beta = row.get("beta", 1.0)
    corr = row.get("correlation", 0.0)
    te_score = max(0.0, 1 - te / 30.0)
    beta_score = max(0.0, 1 - abs(beta - 1))
    return 0.60 * te_score + 0.20 * beta_score + 0.20 * max(0.0, corr)


async def _find_best_portfolio_etf(
    portfolio_series: pd.Series,
    portfolio_tickers: list[str],
    top_n: int = 10,
    openai_key: str | None = None,
    extra_candidates: list[str] | None = None,
) -> list[dict]:
    """Rank ETF candidates against the portfolio by *tracking error* (primary),
    then dollar-beta match, then correlation — the institutional screen for a
    tracking basket, not the old correlation-only sort.

    With an API key: asks LLM for contextually relevant ETFs (12 tickers).
    Without: falls back to _FALLBACK_ETFS (13 broad/sector ETFs).
    ``extra_candidates`` (e.g. top-weight-holder ETFs from Part C) are folded in
    so the headline pick also benefits from the tight, high-overlap proxies.
    """
    if portfolio_series.empty:
        return []

    held = {x.upper().strip() for x in portfolio_tickers}

    # Get candidates: LLM-suggested first, fallback as safety net
    llm_etfs: list[str] = []
    if openai_key:
        llm_etfs = await _call_llm_for_etfs(portfolio_tickers, openai_key)

    extra = [e.upper().strip() for e in (extra_candidates or []) if e and e.strip()]

    # Merge top-holder proxies + LLM picks + fallback safety net, exclude held
    candidates = [
        e for e in list(dict.fromkeys(extra + llm_etfs + _FALLBACK_ETFS))
        if e not in held
    ]
    if not candidates:
        candidates = _FALLBACK_ETFS

    # Single batch download (all cached after first request)
    batch = await _fetch_historical_data_batch(candidates, "1y")

    scored: list[tuple[float, dict]] = []
    for etf, etf_s in batch.items():
        if etf_s is None or etf_s.empty:
            continue
        row = _metrics_for_portfolio_vs_etf(portfolio_series, etf_s)
        if row is None:
            continue
        scored.append((_rank_score_for_etf(row), {"ticker": etf, **row}))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:top_n]]


def _build_methodology_block() -> dict:
    """Static description of the engine's methodology + research basis, surfaced
    in the UI so the analysis is auditable and reads as actionable."""
    return {
        "objective": (
            "Minimize forward tracking-error variance of the replacement basket "
            "vs the harvested exposure, subject to factor (style) neutrality, "
            "wash-sale safety, and a benefit-vs-cost trade trigger."
        ),
        "riskModel": (
            "1Y daily returns; Ledoit-Wolf shrinkage covariance; orthogonalized "
            "factor-mimicking spreads — market, size (IWM−SPY), value (IWD−IWF), "
            "momentum (MTUM−SPY), quality (QUAL−SPY), low-vol (SPLV−SPY)."
        ),
        "selection": (
            "Candidates ranked primarily by annualized tracking error and "
            "dollar-beta match — not raw correlation, which is largely spurious "
            "across equities. An SLSQP optimizer then builds an ETF blend and a "
            "pure-stock blend under ±0.15 per-factor deviation constraints."
        ),
        "proxyDiscovery": (
            "Tightest proxies are the ETFs that hold each harvested name at the "
            "highest weight (LLM-nominated as of today's date, then verified and "
            "scored on measured price history)."
        ),
        "tradeTrigger": (
            "Execute only when after-tax benefit > round-trip transaction cost + "
            "expected tracking-error drag over the 31-day wash-sale window."
        ),
        "references": [
            "Constantinides (1983) — optimal tax timing / option value of loss realization",
            "Stein & Narasimhan (1999); Berkin & Ye (2003, FAJ) — after-tax benchmarking",
            "Sosner et al. (AQR) — tax-aware factor investing & loss harvesting",
            "Chaudhuri, Burnham & Lo (2020, J. Financial Data Science) — empirical TLH alpha",
            "Ledoit & Wolf (2004) — well-conditioned shrinkage covariance",
        ],
    }


async def _call_llm_for_top_holder_etfs(ticker: str, openai_key: str) -> list[dict]:
    """Nominate US-listed ETFs that hold ``ticker`` at the highest portfolio weight.

    Mirrors the user's manual method: ask (with today's date) which ETFs carry
    the name at the largest weight. The LLM is used only for *discovery* — fit is
    always measured from real price history downstream. Cached 4h per ticker/day.
    Returns ``[{"etf": str, "statedWeight": float|None}]``.
    """
    import json
    from .llm_service import call_llm

    today = datetime.now().strftime("%Y-%m-%d")
    cache_key = f"topholders:{ticker.upper()}:{today}"
    now = _time.monotonic()
    entry = _LLM_CACHE.get(cache_key)
    if entry and entry[0] > now:
        return entry[1]

    info = _get_ticker_info(ticker)
    sector = info.get("sector", "Unknown")
    industry = info.get("industry", "Unknown")

    prompt = f"""Today's date is {today}. You are an ETF analyst with up-to-date holdings knowledge.

List the US-listed ETFs that currently hold {ticker} ({sector} / {industry}) at the HIGHEST portfolio weight.
Prioritize thematic / industry / single-country or niche ETFs where {ticker} is a TOP-10 holding (often 5-12% weight), NOT broad-market funds where it is a tiny sliver.

Rules:
- US-listed, tradable, > $50M AUM. No leveraged or inverse ETFs.
- 6 ETFs max, ranked by {ticker}'s weight (largest first).
- Give each ETF's approximate current weight in {ticker} as a percentage number.

Return ONLY valid JSON, no markdown:
{{"etfs": [{{"ticker": "XXX", "weight": 9.1}}, ...]}}"""

    try:
        resp = await call_llm(openai_key, "gpt-4o", [{"role": "user", "content": prompt}], max_tokens=300, temperature=0, expect_json=True)
        if "```json" in resp:
            resp = resp.split("```json")[-1].split("```")[0].strip()
        elif "```" in resp:
            resp = resp.split("```")[-1].split("```")[0].strip()
        data = json.loads(resp.strip())
        out: list[dict] = []
        for e in data.get("etfs", [])[:6]:
            et = str(e.get("ticker", "")).upper().strip()
            if et:
                w = e.get("weight")
                try:
                    w = float(w) if w is not None else None
                except (TypeError, ValueError):
                    w = None
                out.append({"etf": et, "statedWeight": w})
        _LLM_CACHE[cache_key] = (now + _LLM_CACHE_TTL, out)
        return out
    except Exception as e:
        print(f"Top-holder ETF nomination failed for {ticker}: {e}")
        return []


async def _discover_one_ticker_proxies(ticker: str, openai_key: str | None) -> list[dict]:
    """Find the ETFs that hold ``ticker`` at the highest weight (the user's manual
    method) and attach measured fit where price history exists.

    The LLM's nominated ETFs + stated weights are ALWAYS kept — that list is the
    core deliverable. Correlation / annualized tracking error / beta are *added*
    when the proxy has usable price data (many niche/thematic ETFs are thin or
    missing in yfinance, e.g. TRVL — those still show with their stated weight and
    blank metrics rather than being dropped). Without an API key, falls back to the
    ticker's GICS sector-proxy ETF.
    """
    t = ticker.upper()
    try:
        noms = await _call_llm_for_top_holder_etfs(t, openai_key) if openai_key else []
        if not noms:
            sector = _get_ticker_info(t).get("sector")
            proxy = SECTOR_PROXY_ETFS.get(sector)
            noms = [{"etf": proxy, "statedWeight": None}] if proxy else []
        # De-dupe preserving LLM rank order.
        seen: set[str] = set()
        ordered = []
        for n in noms:
            et = n.get("etf")
            if et and et not in seen:
                seen.add(et)
                ordered.append(n)
        if not ordered:
            return []

        cand_etfs = [n["etf"] for n in ordered]
        price_map = await _fetch_historical_data_batch([t] + cand_etfs, "1y")
        t_series = price_map.get(t)

        proxies: list[dict] = []
        for n in ordered:
            et = n["etf"]
            row = None
            et_series = price_map.get(et)
            if (t_series is not None and not t_series.empty
                    and et_series is not None and not et_series.empty):
                row = _metrics_for_portfolio_vs_etf(t_series, et_series)
            proxies.append({
                "etf": et,
                "statedWeight": n.get("statedWeight"),
                "correlation": round(row["correlation"] * 100, 2) if row else None,
                "trackingError": row.get("trackingError") if row else None,
                "beta": row.get("beta") if row else None,
                "measured": bool(row),
            })

        # Measured proxies first (tightest TE), then unmeasured by stated weight.
        proxies.sort(key=lambda p: (
            0 if p["measured"] else 1,
            p["trackingError"] if p["trackingError"] is not None else (
                -(p["statedWeight"] or 0)),
        ))
        return proxies[:6]
    except Exception as e:
        print(f"Top-holder proxy discovery failed for {t}: {e}")
        return []


async def _discover_top_holder_proxies(
    harvestable_list: list[dict],
    openai_key: str | None,
) -> dict:
    """Run :func:`_discover_one_ticker_proxies` for the largest losers concurrently.

    Returns ``{ticker: [{etf, statedWeight, correlation, trackingError, beta,
    measured}]}``. Concurrency keeps total latency ≈ one ticker's, not N×.
    """
    targets = sorted(harvestable_list, key=lambda h: h.get("losses", 0), reverse=True)[:5]
    if not targets:
        return {}
    tickers = [h["ticker"].upper() for h in targets]
    lists = await asyncio.gather(
        *[_discover_one_ticker_proxies(t, openai_key) for t in tickers]
    )
    return {t: lst for t, lst in zip(tickers, lists) if lst}


async def run_portfolio_tax_loss_harvesting(
    holdings: list[dict],
    tax_rate_pct: float = 15.0,
    preferred_etf: str | None = None,
    custom_stocks: list[str] = None,
    openai_key: str | None = None,
) -> dict:
    """Analyze multiple holdings for tax loss harvesting opportunities.

    Now uses a PORTFOLIO-LEVEL approach:
    1. Builds a value-weighted portfolio return series
    2. Finds the best replacement ETF across ALL holdings by tracking error
       (or anchors to preferred_etf / custom_stocks when the user overrides)
    3. Shows weightage overlap between portfolio and ETF
    4. Provides unified execution strategies to replace all losers

    Overrides are treated as an **anchor, with auto alternates still shown**:
    a user-supplied ETF / stock set is always evaluated and surfaced (even if it
    scores below the auto baseline), while auto-discovery still runs to populate
    labeled alternatives for comparison.
    """
    if not holdings:
        return {"error": "No holdings provided"}

    # Normalize user overrides once, up front.
    clean_custom = [s.upper().strip() for s in (custom_stocks or []) if s and s.strip()][:3]
    anchored_etf = preferred_etf.upper().strip() if preferred_etf and preferred_etf.strip() else None
    overrides_active = bool(anchored_etf) or bool(clean_custom)

    # 1. Build portfolio series and get holding details
    portfolio_series, holding_details = await _build_portfolio_series(holdings)

    all_tickers = [h["ticker"].upper() for h in holdings]

    # Separate harvestable vs non-harvestable
    harvestable_list = []
    non_harvestable_list = []
    for t, details in holding_details.items():
        if details.get("harvestable"):
            details["losses"] = abs(details["unrealizedPnl"])
            details["taxSavings"] = round(
                _calculate_tax_savings(details["losses"], tax_rate_pct), 2
            )
            harvestable_list.append(details)
        else:
            non_harvestable_list.append(details)

    total_losses = sum(h.get("losses", 0) for h in harvestable_list)
    total_tax_savings = sum(h.get("taxSavings", 0) for h in harvestable_list)
    total_portfolio_value = sum(
        d["currentValue"] for d in holding_details.values() if d.get("currentValue")
    )
    total_cost_value = sum(
        d["costValue"] for d in holding_details.values() if d.get("costValue")
    )
    total_unrealized_pnl = round(total_portfolio_value - total_cost_value, 2)
    total_harvestable_value = sum(h.get("currentValue", 0) for h in harvestable_list)

    # Sort harvestable by largest loss first
    harvestable_list.sort(key=lambda x: x.get("losses", 0), reverse=True)

    # 2. Find best correlated ETFs for the whole portfolio
    portfolio_replacement = None
    etf_candidates = []
    best_etf = None

    top_holder_data: dict = {}
    if not portfolio_series.empty and harvestable_list:
        # Part C: for each harvested name, discover the ETFs that hold it at the
        # highest weight (the user's manual method), then MEASURE correlation/TE
        # from real prices. These tight, high-overlap proxies also seed the
        # headline candidate pool.
        top_holder_data = await _discover_top_holder_proxies(harvestable_list, openai_key)
        extra_candidates: list[str] = []
        for proxies in top_holder_data.values():
            extra_candidates.extend(p["etf"] for p in proxies)

        # Always run auto-discovery (now TE-ranked) to populate the alternate
        # list — even when the user overrides, so they get labeled comparisons.
        etf_candidates = await _find_best_portfolio_etf(
            portfolio_series, all_tickers, top_n=10, openai_key=openai_key,
            extra_candidates=extra_candidates,
        )

        # Determine the primary (anchored) replacement.
        if anchored_etf:
            # Anchor to the user's ETF/stock. Use the auto-discovered row if we
            # already measured it; otherwise measure it directly (same unified
            # metrics, so the z-score / TE / beta are consistent everywhere).
            matched = [e for e in etf_candidates if e["ticker"] == anchored_etf]
            if matched:
                best_etf = matched[0]
            else:
                custom_data = await _fetch_historical_data(anchored_etf)
                row = (
                    _metrics_for_portfolio_vs_etf(portfolio_series, custom_data)
                    if not custom_data.empty else None
                )
                if row:
                    best_etf = {"ticker": anchored_etf, **row}
                else:
                    best_etf = etf_candidates[0] if etf_candidates else None
        else:
            best_etf = etf_candidates[0] if etf_candidates else None

        if best_etf:
            best_etf_ticker = best_etf["ticker"]

            # 3. Get ETF holdings overlap
            overlap_info = await _get_etf_holdings_overlap(
                best_etf_ticker, all_tickers
            )

            # 4. Get ETF price and compute position sizing
            etf_info = _get_ticker_info(best_etf_ticker)
            etf_price = float(
                etf_info.get("currentPrice")
                or etf_info.get("regularMarketPrice")
                or 0.0
            )
            if not etf_price:
                try:
                    hist = yf.Ticker(best_etf_ticker).history(period="1d")
                    if not hist.empty:
                        etf_price = float(hist["Close"].iloc[-1])
                except Exception:
                    pass

            etf_shares = math.floor(total_harvestable_value / etf_price) if etf_price else 0
            etf_total_cost = round(etf_price * etf_shares, 2) if etf_price else 0

            # 5. Build unified execution strategies
            strategies = await _build_portfolio_replacement_strategies(
                etf_ticker=best_etf_ticker,
                harvestable_holdings=harvestable_list,
                total_harvestable_value=total_harvestable_value,
                total_losses=total_losses,
                tax_rate_pct=tax_rate_pct,
            )

            # Build alternate list excluding the selected ETF
            alt_etfs = [e for e in etf_candidates if e["ticker"] != best_etf_ticker]

            portfolio_replacement = {
                "selectedETF": best_etf_ticker,
                "bestETF": {
                    "ticker": best_etf_ticker,
                    "name": overlap_info.get("etfName", best_etf_ticker),
                    "price": round(etf_price, 2) if etf_price else None,
                    "correlation": round(best_etf["correlation"] * 100, 2),
                    "sharesToBuy": etf_shares,
                    "totalCost": etf_total_cost,
                    "zScore": round(best_etf["z_score"], 2),
                },
                "holdingsOverlap": overlap_info,
                "chartData": {
                    "dates": best_etf["dates"],
                    "portfolioNorm": best_etf["portfolioNorm"],
                    "etfNorm": best_etf["etfNorm"],
                    "spread": best_etf["spreadHistory"],
                    "spreadMean": best_etf["spreadMean"],
                    "spreadStd": best_etf["spreadStd"],
                },
                "alternateETFs": [
                    {
                        "ticker": e["ticker"],
                        "correlation": round(e["correlation"] * 100, 2),
                        "zScore": round(e["z_score"], 2),
                    }
                    for e in alt_etfs
                ],
                "strategies": strategies,
                "costComparison": {
                    "unified_swap": {
                        "cost": etf_total_cost,
                        "complexity": "Low",
                        "protection": "None",
                        "wash_sale_safe": True,
                        "capital_required": etf_total_cost,
                    },
                    "unified_synthetic": {
                        "cost": strategies[1]["estimated_cost"] if len(strategies) > 1 else 0,
                        "complexity": "Medium",
                        "protection": "Partial",
                        "wash_sale_safe": True,
                        "capital_required": strategies[1]["estimated_cost"] if len(strategies) > 1 else 0,
                    },
                    "unified_collar": {
                        "cost": strategies[2]["estimated_cost"] if len(strategies) > 2 else 0,
                        "complexity": "Medium",
                        "protection": "Full",
                        "wash_sale_safe": True,
                        "capital_required": strategies[2]["estimated_cost"] if len(strategies) > 2 else 0,
                    },
                },
            }

    # Generate optimization suggestions
    optimization_suggestions = []
    ai_stocks, ai_etfs, ai_rationale = [], [], ""
    
    if (
        not portfolio_series.empty
        and harvestable_list
        and portfolio_replacement
        and best_etf
    ):
        try:
            opt_res = await _generate_optimization_suggestions(
                portfolio_series=portfolio_series,
                holding_details=holding_details,
                harvestable_list=harvestable_list,
                non_harvestable_list=non_harvestable_list,
                all_tickers=all_tickers,
                best_etf_ticker=best_etf["ticker"],
                best_etf_correlation=best_etf["correlation"],
                tax_rate_pct=tax_rate_pct,
                custom_stocks=clean_custom,
                openai_key=openai_key,
            )
            optimization_suggestions = opt_res.get("suggestions", [])
            ai_stocks = opt_res.get("ai_stocks", [])
            ai_etfs = opt_res.get("ai_etfs", [])
            ai_rationale = opt_res.get("ai_rationale", "")
        except Exception as e:
            print(f"Optimization suggestions failed (non-fatal): {e}")

    # Run institutional-grade factor analysis
    institutional_analysis = {}
    if (
        not portfolio_series.empty
        and harvestable_list
        and portfolio_replacement
        and best_etf
    ):
        try:
            # Get tracking error from baseline ETF
            te_ann = 0.05  # default 5%
            if optimization_suggestions and "baselineMetrics" in optimization_suggestions[0]:
                baseline = optimization_suggestions[0]["baselineMetrics"]
                te_ann = baseline.get("trackingErrorAnn", 0.05)
            elif portfolio_replacement:
                # Fallback: compute TE from portfolio vs ETF returns directly
                etf_fb = await _fetch_historical_data(best_etf["ticker"])
                if not etf_fb.empty:
                    df_fb = pd.DataFrame({"p": portfolio_series, "e": etf_fb}).dropna()
                    if len(df_fb) >= 20:
                        ret_fb_p = df_fb["p"].pct_change().dropna()
                        ret_fb_e = df_fb["e"].pct_change().dropna()
                        common_fb = pd.DataFrame({"p": ret_fb_p, "e": ret_fb_e}).dropna()
                        if len(common_fb) >= 20:
                            te_ann = float((common_fb["p"] - common_fb["e"]).std() * math.sqrt(252))

            institutional_analysis = await _run_institutional_analysis(
                portfolio_series=portfolio_series,
                holding_details=holding_details,
                harvestable_list=harvestable_list,
                all_tickers=all_tickers,
                best_etf_ticker=best_etf["ticker"],
                best_etf_correlation=best_etf["correlation"],
                total_losses=total_losses,
                total_harvestable_value=total_harvestable_value,
                tax_rate_pct=tax_rate_pct,
                tracking_error_ann=te_ann,
            )
        except Exception as e:
            print(f"Institutional analysis failed (non-fatal): {e}")
            institutional_analysis = {"available": False, "error": str(e)}

    # Build holding weights for display
    holdings_with_weights = []
    for t in all_tickers:
        if t in holding_details:
            d = holding_details[t]
            d["portfolioWeight"] = round(
                (d.get("currentValue", 0) / total_portfolio_value * 100)
                if total_portfolio_value > 0 else 0, 2
            )
            holdings_with_weights.append(d)

    return {
        "success": True,
        "taxRatePct": tax_rate_pct,
        "summary": {
            "totalHoldings": len(holding_details),
            "harvestableCount": len(harvestable_list),
            "nonHarvestableCount": len(non_harvestable_list),
            "totalPortfolioValue": round(total_portfolio_value, 2),
            "totalCostValue": round(total_cost_value, 2),
            "totalUnrealizedPnl": round(total_unrealized_pnl, 2),
            "totalHarvestableLosses": round(total_losses, 2),
            "totalHarvestableValue": round(total_harvestable_value, 2),
            "totalTaxSavings": round(total_tax_savings, 2),
        },
        "holdings": holdings_with_weights,
        "harvestable": harvestable_list,
        "nonHarvestable": non_harvestable_list,
        "portfolioReplacement": portfolio_replacement,
        "optimizationSuggestions": optimization_suggestions,
        "aiRecommendations": {
            "stocks": ai_stocks,
            "etfs": ai_etfs,
            "rationale": ai_rationale,
        },
        "institutionalAnalysis": institutional_analysis,
        "overridesActive": overrides_active,
        "anchoredETF": anchored_etf,
        "anchoredStocks": clean_custom,
        "topHolderETFs": top_holder_data,
        "methodology": _build_methodology_block(),
        "timeline": [
            {
                "day": "Day 0",
                "title": "Sell All Losers & Buy ETF",
                "description": (
                    f"Sell {len(harvestable_list)} loss-making position(s) to realize "
                    f"${round(total_losses):,} in losses. Buy the recommended ETF to "
                    f"maintain diversified exposure."
                ),
            },
            {
                "day": "Day 1-30",
                "title": "Wash Sale Window",
                "description": (
                    "Hold the ETF replacement position. Do NOT repurchase any "
                    "of the sold tickers during this 30-day window."
                ),
            },
            {
                "day": "Day 31+",
                "title": "Optionally Re-acquire",
                "description": (
                    "Wash sale window clear. You may sell the ETF and re-buy "
                    "original tickers if desired, or continue holding for "
                    "broader diversification."
                ),
            },
        ],
    }

def _calculate_tax_savings(losses: float, tax_rate_pct: float) -> float:
    return losses * (tax_rate_pct / 100.0)
# ===========================================================================
# V2: Advanced Quant Functions 
# ===========================================================================

def _compute_cointegration(series1: pd.Series, series2: pd.Series) -> float:
    try:
        import statsmodels.tsa.stattools as ts
        common = pd.DataFrame({"A": series1, "B": series2}).dropna()
        if len(common) < 30:
            return 1.0
        # Engle-Granger test
        coint_res = ts.coint(common["A"], common["B"])
        p_value = float(coint_res[1])
        return p_value
    except Exception:
        return 1.0


def _compute_dtw_distance(series1: pd.Series, series2: pd.Series) -> float:
    try:
        from fastdtw import fastdtw
        from scipy.spatial.distance import euclidean
        common = pd.DataFrame({"A": series1, "B": series2}).dropna()
        if len(common) < 30:
            return 9999.0
        # Normalize to 100 base
        norm_a = (common["A"] / common["A"].iloc[0]).values
        norm_b = (common["B"] / common["B"].iloc[0]).values
        distance, path = fastdtw(norm_a, norm_b)
        return float(distance)
    except Exception:
        return 9999.0


def _compute_tail_dependence(series1: pd.Series, series2: pd.Series, percentile: float = 0.05) -> float:
    try:
        common = pd.DataFrame({"A": series1, "B": series2}).dropna()
        if len(common) < 30:
            return 0.0
        ret_a = common["A"].pct_change().dropna()
        ret_b = common["B"].pct_change().dropna()
        
        # P(B < P5 | A < P5)
        thresh_a = ret_a.quantile(percentile)
        thresh_b = ret_b.quantile(percentile)
        
        cond_a = ret_a < thresh_a
        cond_b = ret_b < thresh_b
        
        joint_extreme = (cond_a & cond_b).sum()
        total_extreme_a = cond_a.sum()
        
        if total_extreme_a == 0:
            return 0.0
            
        return float(joint_extreme / total_extreme_a)
    except Exception:
        return 0.0


def _compute_quant_metrics(
    original_returns: pd.Series,
    replacement_returns: pd.Series,
) -> dict:
    metrics = _compute_quant_metrics_base(original_returns, replacement_returns)
    if not metrics:
        return {}

    orig_price = (1 + original_returns).cumprod() * 100
    repl_price = (1 + replacement_returns).cumprod() * 100

    metrics["cointegrationPValue"] = round(_compute_cointegration(orig_price, repl_price), 4)
    metrics["lowerTailDependence"] = round(_compute_tail_dependence(orig_price, repl_price) * 100, 2)

    return metrics


def _score_strategy(m: dict, tax_capture: float = 1.0) -> float:
    """Composite replacement-quality score (higher = better).

    Weighted toward what institutions actually optimize for a tax-aware
    tracking basket — minimum tracking error, dollar-beta neutrality, style
    (factor) match, residual stationarity inside the 31-day wash window, and
    downside/tail co-movement. Raw return correlation is kept at a small weight
    because, across equities, it is largely spurious (everything co-moves with
    the market). DTW distance was removed — it has no basis in the TLH /
    direct-indexing literature and its normalization was arbitrary.
    """
    corr = m.get("correlation", 0)
    r2 = m.get("rSquared", 0)
    te = m.get("trackingError", 100)            # annualized %, lower better
    beta = m.get("beta", 1)
    z = abs(m.get("zScore", 0))                 # drift significance (t-stat), lower better

    coint_p = m.get("cointegrationPValue", 1.0)
    tail_dep = m.get("lowerTailDependence", 0) / 100.0
    factor_dist = m.get("factorDistance")       # aggregate L2 style drift, lower better (optional)

    te_score = max(0, 1 - te / 30)              # 0% TE → 1.0, 30%+ → 0
    beta_score = max(0, 1 - abs(beta - 1))      # beta=1 → 1.0
    z_score_s = max(0, 1 - z / 3)               # |z|=0 → 1.0, 3+ → 0

    # Cointegration: p < 0.05 rejects the no-cointegration null (residual is
    # stationary → the proxy mean-reverts to the position, ideal for a 31-day swap).
    coint_score = 1.0 if coint_p < 0.05 else max(0, 1 - (coint_p - 0.05) * 2)

    # Style/factor match — neutral (1.0) when not supplied, so its absence does
    # not skew rankings (a constant offset cancels across candidates).
    factor_score = 1.0 if factor_dist is None else max(0, 1 - factor_dist / 0.5)

    return (
        0.30 * te_score       # tracking error — primary institutional metric
        + 0.14 * beta_score   # dollar-beta neutrality
        + 0.12 * coint_score  # residual stationarity (wash-window mean reversion)
        + 0.10 * factor_score # style / factor-exposure match
        + 0.10 * tail_dep     # lower-tail (crash) co-movement
        + 0.10 * tax_capture  # tax-capture efficiency
        + 0.06 * z_score_s    # drift insignificance
        + 0.05 * corr         # correlation (small — largely spurious)
        + 0.03 * r2           # R²
    )


async def _generate_optimization_suggestions(
    portfolio_series: pd.Series,
    holding_details: dict,
    harvestable_list: list[dict],
    non_harvestable_list: list[dict],
    all_tickers: list[str],
    best_etf_ticker: str,
    best_etf_correlation: float,
    tax_rate_pct: float,
    custom_stocks: list[str] = None,
    openai_key: str | None = None,
) -> dict:
    """Quant-level portfolio optimisation engine (Advanced V2).
    Generates ranked replacement strategies using V2 metrics/scoring.
    """
    from itertools import combinations

    suggestions: list[dict] = []
    total_losses = sum(h.get("losses", 0) for h in harvestable_list)
    total_harvestable_value = sum(h.get("currentValue", 0) for h in harvestable_list)
    total_portfolio_value = total_harvestable_value + sum(h.get("currentValue", 0) for h in non_harvestable_list)

    # We await internal _fetch_historical_data
    from .tax_loss_harvesting_service import _fetch_historical_data, _get_price_safe, _compute_trade_trigger, _build_portfolio_series

    kept_series, _ = await _build_portfolio_series(non_harvestable_list)

    harvest_t = [h["ticker"] for h in harvestable_list]
    keep_t = [h["ticker"] for h in non_harvestable_list]

    ai_stocks: list[str] = []
    ai_etfs: list[str] = []
    ai_rationale = ""

    # -- STEP 1: LLM-first candidate discovery (always run — this populates the
    #    auto "alternatives" surfaced for comparison even when the user overrides) --
    auto_pool: list[str] = []
    if openai_key:
        llm_result = await _call_llm_for_replacements(harvest_t, keep_t, openai_key)
        llm_stocks = llm_result.get("stocks", [])
        llm_etfs = llm_result.get("etfs", [])
        ai_stocks = llm_stocks
        ai_etfs = llm_etfs
        auto_pool = list(dict.fromkeys(llm_stocks + llm_etfs))
        ai_rationale = llm_result.get("rationale", "")

    # -- STEP 2: Sector-peer fallback (when LLM unavailable or returned too few) --
    if len(auto_pool) < 4:
        for t in harvest_t:
            auto_pool.extend(_get_sector_peers(t))
        auto_pool = list(dict.fromkeys(auto_pool))

    # -- STEP 3: Final safety net --
    if not auto_pool:
        auto_pool = list(_FALLBACK_ETFS)

    # When the user supplies custom replacement stocks, ANCHOR the blend to
    # exactly those — the actionable execution card is built only from the user's
    # universe (never crowded out by auto picks). Auto-discovered names still
    # surface as labeled alternates (alternateETFs + aiRecommendations).
    clean_custom = [s.upper().strip() for s in (custom_stocks or []) if s and s.strip()][:3]
    using_custom = bool(clean_custom)
    if using_custom:
        supplement_pool = list(dict.fromkeys(clean_custom))
        ai_rationale = (ai_rationale + " " if ai_rationale else "") + \
            "Replacement blend anchored to your specified stocks; auto picks shown as alternates."
    else:
        supplement_pool = auto_pool

    # -- Pre-fetch all price data (include factor proxy ETFs for factor constraints) --
    factor_proxy_tickers = list(FACTOR_PROXY_ETFS.values())
    all_fetch_tickers = list(dict.fromkeys(
        all_tickers
        + [best_etf_ticker]
        + factor_proxy_tickers
        + [s for s in supplement_pool if s not in all_tickers][:25]
    ))

    price_data = await _fetch_historical_data_batch(all_fetch_tickers, "1y")

    etf_series = price_data.get(best_etf_ticker, pd.Series(dtype=float))
    if etf_series.empty:
        return suggestions
        
    common_base = pd.DataFrame({"orig": portfolio_series, "etf": etf_series}).dropna()
    orig_returns = common_base["orig"].pct_change().dropna()
    etf_returns = common_base["etf"].pct_change().dropna()
    
    if len(orig_returns) < 30:
        return suggestions
    baseline_metrics = _compute_quant_metrics(orig_returns, etf_returns)

    supplement_candidates = [
        s for s in supplement_pool
        if s not in all_tickers and s in price_data and not price_data[s].empty and len(price_data[s]) >= 30
    ]

    baseline_score = _score_strategy(baseline_metrics)

    # -- Build factor return matrix for TE decomposition and factor neutralization --
    # Uses only price_data (already fetched) — no additional yfinance calls
    def _sync_factor_loadings(ret_series: np.ndarray, factor_ret_matrix: np.ndarray) -> np.ndarray | None:
        """Compute OLS betas for ret_series against factor_ret_matrix. Returns β vector or None."""
        if factor_ret_matrix is None or len(ret_series) < 30:
            return None
        try:
            X = np.column_stack([np.ones(len(factor_ret_matrix)), factor_ret_matrix])
            min_len = min(len(ret_series), len(X))
            betas, _, _, _ = np.linalg.lstsq(X[:min_len], ret_series[:min_len], rcond=None)
            return betas[1:]  # exclude intercept
        except Exception:
            return None

    # Build aligned factor return matrix from price_data — using the
    # orthogonalized factor-mimicking spreads so the neutralization constraints
    # below constrain *style* (size/value/momentum/quality/low-vol) rather than
    # collinear near-market noise.
    factor_spreads = _build_factor_spreads(price_data)
    factor_returns_dict: dict[str, np.ndarray] = {
        fname: s.values for fname, s in factor_spreads.items()
    }

    factor_ret_matrix: np.ndarray | None = None
    if len(factor_returns_dict) >= 4:
        # Align all factor series to same length (use minimum)
        min_len = min(len(v) for v in factor_returns_dict.values())
        factor_ret_matrix = np.column_stack([v[-min_len:] for v in factor_returns_dict.values()])

    # -- PHASE 3: MULTI-ASSET CONVEX OPTIMIZATION (SLSQP) --
    blend_results: list[dict] = []
    blend_assets = [best_etf_ticker] + supplement_candidates
    
    if len(blend_assets) > 1:
        # Align all series
        series_dict = {"orig": portfolio_series}
        for t in blend_assets:
            if not price_data[t].empty:
                series_dict[t] = price_data[t]
                
        common_df = pd.DataFrame(series_dict).dropna()
        # Re-filter blend_assets to only tickers actually present in common_df
        # (handles delisted/empty tickers that slip through pre-fetch)
        blend_assets = [t for t in blend_assets if t in common_df.columns]

        # Cap supplement stocks to top 3 by correlation to the original portfolio.
        # Result: optimizer sees at most 1 ETF + 3 stocks — keeps strategies actionable.
        # User-specified custom stocks are ALWAYS kept (the cap only trims auto picks),
        # so an override is never crowded out by higher-correlation auto candidates.
        MAX_SUPPLEMENT_STOCKS = 3
        etf_in_blend = [t for t in blend_assets if t == best_etf_ticker]
        stock_candidates = [t for t in blend_assets if t != best_etf_ticker]
        if not using_custom and len(stock_candidates) > MAX_SUPPLEMENT_STOCKS:
            orig_ret = common_df["orig"].pct_change().dropna()
            corr_scores = {}
            for t in stock_candidates:
                t_ret = common_df[t].pct_change().dropna()
                try:
                    corr_scores[t] = float(orig_ret.corr(t_ret))
                except Exception:
                    corr_scores[t] = 0.0
            stock_candidates = sorted(stock_candidates, key=lambda t: corr_scores.get(t, 0), reverse=True)[:MAX_SUPPLEMENT_STOCKS]
        blend_assets = etf_in_blend + stock_candidates

        if len(common_df) >= 30:
            # We must use absolute prices/values to prevent mathematically artificial "Drift Error" 
            # associated with blending static scalar percentage returns against a Buy-and-Hold portfolio.
            
            # 1. We have kept_series (dollar value of kept positions on each day). Ensure it aligns.
            if not kept_series.empty:
                aligned_kept = kept_series.reindex(common_df.index).ffill()
            else:
                aligned_kept = pd.Series(0.0, index=common_df.index)
                
            # 2. Get today's prices for exactly computing fractional shares.
            today_prices = common_df.iloc[-1]
            
            # The exact historical percentage returns for the entire original portfolio
            ret_orig = common_df["orig"].pct_change().dropna().values
            
            # Asset PRICE matrix (T x N) directly from df
            asset_prices_df = common_df[blend_assets]
            asset_prices = asset_prices_df.values
            
            # Baseline portfolio total
            port_total = total_portfolio_value if total_portfolio_value > 0 else 1.0
            
            # --- Shrinkage-covariance active-variance objective (institutional min-TE) ---
            # Estimate the covariance of [blend-asset returns, harvested-basket
            # return] with the Ledoit-Wolf shrinkage estimator, then minimize the
            # closed-form active variance  wᵀΣ_aa w − 2·wᵀσ_ah + σ_hh.  Shrinkage
            # stabilizes the weights out-of-sample versus fitting the raw realized
            # path (which overfits the 1Y sample). Falls back to the exact dollar
            # simulation if the estimate can't be formed.
            _cov_ready = False
            try:
                harvest_ret_series = _build_weighted_return_series(
                    price_data,
                    {h["ticker"]: h.get("currentValue", 0) for h in harvestable_list},
                )
                ret_parts = {t: common_df[t].pct_change() for t in blend_assets}
                ret_parts["__harvest__"] = harvest_ret_series
                ret_df = pd.DataFrame(ret_parts).dropna()
                if len(ret_df) >= 30:
                    _M = ret_df.values.T                      # (N+1 x T)
                    _Sigma = _shrinkage_cov(_M)
                    _nA = len(blend_assets)
                    _Sigma_aa = _Sigma[:_nA, :_nA]
                    _sigma_ah = _Sigma[:_nA, _nA]
                    _sigma_hh = float(_Sigma[_nA, _nA])
                    _cov_ready = True
            except Exception:
                _cov_ready = False

            # Objective: annualized active variance (shrinkage), else exact sim TE.
            def min_tracking_error(weights):
                if _cov_ready:
                    w = np.asarray(weights, dtype=float)
                    active_var = float(w @ _Sigma_aa @ w - 2.0 * (w @ _sigma_ah) + _sigma_hh)
                    # Scaled annualized variance — keeps SLSQP gradients well-resolved.
                    return max(active_var, 0.0) * 252.0 * 1.0e8

                # Fallback: minimize TE of the EXACT simulated dollar portfolio.
                allocated_capital = weights * total_harvestable_value
                current_p = np.where(today_prices[blend_assets].values > 0, today_prices[blend_assets].values, 1.0)
                sim_shares = allocated_capital / current_p
                v_blend = asset_prices @ sim_shares
                v_new_port = aligned_kept.values + v_blend
                ret_new = (v_new_port[1:] / v_new_port[:-1]) - 1.0
                diff = ret_orig - ret_new
                # Scale massively to prevent floating point gradients rounding to 0 for SLSQP
                return float(np.std(diff) * np.sqrt(252) * 10000.0)

            n_assets = len(blend_assets)

            # Bounds for full portfolio: 0 to 1 for each asset
            bounds_full = tuple((0.0, 1.0) for _ in range(n_assets))

            # Base constraint: weights sum to 1.0
            constraints_list = [{'type': 'eq', 'fun': lambda w: np.sum(w) - 1.0}]

            # Factor neutralization constraints (institutional-grade: ±0.15 per factor)
            # Compute factor loadings for original portfolio and each blend asset
            MAX_FACTOR_DEV = 0.15
            if factor_ret_matrix is not None:
                orig_ret_arr = ret_orig[-len(factor_ret_matrix):]
                orig_factor_betas = _sync_factor_loadings(orig_ret_arr, factor_ret_matrix)

                if orig_factor_betas is not None:
                    asset_factor_betas: list[np.ndarray | None] = []
                    for t in blend_assets:
                        if t in price_data and not price_data[t].empty:
                            t_ret = price_data[t].pct_change().dropna().values
                            t_ret_aligned = t_ret[-len(factor_ret_matrix):]
                            asset_factor_betas.append(_sync_factor_loadings(t_ret_aligned, factor_ret_matrix))
                        else:
                            asset_factor_betas.append(None)

                    for fi in range(len(orig_factor_betas)):
                        f_target = float(orig_factor_betas[fi])
                        f_cands = np.array([
                            float(b[fi]) if b is not None and fi < len(b) else f_target
                            for b in asset_factor_betas
                        ])
                        # ineq: MAX_FACTOR_DEV - |blend_f - target_f| >= 0
                        constraints_list.append({
                            'type': 'ineq',
                            'fun': (lambda w, fc=f_cands, ft=f_target:
                                    MAX_FACTOR_DEV - abs(float(np.dot(w, fc)) - ft))
                        })

            constraints = constraints_list
            
            # PASS 1: Initialize anchored to the ETF (Unbounded ETF)
            init_w_etf = np.zeros(n_assets)
            init_w_etf[0] = 0.70
            if n_assets > 1:
                init_w_etf[1:] = 0.30 / (n_assets - 1)
                
            simple_constraints = {'type': 'eq', 'fun': lambda w: np.sum(w) - 1.0}

            opt1 = minimize(min_tracking_error, init_w_etf, method='SLSQP', bounds=bounds_full, constraints=constraints)
            # Fallback: if factor constraints caused infeasibility, retry without them
            if not opt1.success or opt1.x is None:
                opt1 = minimize(min_tracking_error, init_w_etf, method='SLSQP', bounds=bounds_full, constraints=simple_constraints)

            # PASS 2: Mathematically force a Pure-Stock Alternative by strictly binding ETF to 0.0%
            bounds_no_etf = list(bounds_full)
            bounds_no_etf[0] = (0.0, 0.0)
            bounds_no_etf = tuple(bounds_no_etf)

            init_w_stocks = np.zeros(n_assets)
            if n_assets > 1:
                init_w_stocks[1:] = 1.0 / (n_assets - 1)
            else:
                init_w_stocks[0] = 1.0

            opt2 = minimize(min_tracking_error, init_w_stocks, method='SLSQP', bounds=bounds_no_etf, constraints=constraints)
            # Fallback: if factor constraints caused infeasibility, retry without them
            if not opt2.success or opt2.x is None:
                opt2 = minimize(min_tracking_error, init_w_stocks, method='SLSQP', bounds=bounds_no_etf, constraints=simple_constraints)

            # Process both mathematical pathways independently. Titles make the
            # anchored (user-specified) universe explicit when overriding.
            etf_blend_title = "Your Custom Replacement (ETF + your stocks)" if using_custom else "AI-Optimized ETF Blend"
            stock_blend_title = "Your Custom Replacement (your stocks only)" if using_custom else "AI-Optimized Pure Stock Blend"
            for strat_name, opt_result in [
                (etf_blend_title, opt1),
                (stock_blend_title, opt2)
            ]:
                # Always use opt_result.x — scipy populates it even on non-convergence
                if opt_result.x is None:
                    continue
                opt_weights = opt_result.x
                # Filter out granular fractional dust (< 1.0% weighting)
                threshold = 0.01
                clean_w = np.where(opt_weights >= threshold, opt_weights, 0)
                # Re-normalize
                clean_w_sum = np.sum(clean_w)
                if clean_w_sum > 0:
                    clean_w = clean_w / clean_w_sum
                    
                    # Recompute final exact metrics based on the clean weights
                    allocated_cap = clean_w * total_harvestable_value
                    curr_p = np.where(today_prices[blend_assets].values > 0, today_prices[blend_assets].values, 1.0)
                    sim_s = allocated_cap / curr_p
                    v_blend_final = asset_prices @ sim_s
                    v_new_final = aligned_kept.values + v_blend_final
                    
                    ret_new_final = (v_new_final[1:] / v_new_final[:-1]) - 1.0
                    final_port_ret = pd.Series(ret_new_final, index=common_df.index[1:])
                    orig_ret_series = common_df["orig"].pct_change().dropna()
                    
                    metrics = _compute_quant_metrics(orig_ret_series, final_port_ret)

                    # TE decomposition: systematic vs idiosyncratic
                    if factor_ret_matrix is not None:
                        aligned_len = min(len(ret_new_final), len(factor_ret_matrix))
                        te_decomp = _decompose_te(
                            ret_new_final[-aligned_len:],
                            ret_orig[-aligned_len:],
                            factor_ret_matrix[-aligned_len:],
                        )
                    else:
                        te_decomp = _decompose_te(ret_new_final, ret_orig)

                    # Also dynamically scale baseline metrics using the EXACT SAME dollar math
                    etf_w = np.zeros(n_assets)
                    etf_w[0] = 1.0
                    etf_cap = etf_w * total_harvestable_value
                    etf_sim_s = etf_cap / curr_p
                    v_etf_final = asset_prices @ etf_sim_s
                    v_base_final = aligned_kept.values + v_etf_final
                    
                    ret_base_final = (v_base_final[1:] / v_base_final[:-1]) - 1.0
                    true_baseline_ret = pd.Series(ret_base_final, index=common_df.index[1:])
                    
                    baseline_metrics = _compute_quant_metrics(orig_ret_series, true_baseline_ret)
                    baseline_score = _score_strategy(baseline_metrics)
                    
                    if metrics:
                        score = _score_strategy(metrics)
                        # When the user anchors a custom universe we ALWAYS emit
                        # the card (even if it scores below the ETF baseline) so the
                        # override is never silently dropped — flagged honestly.
                        if using_custom or score > baseline_score:
                            final_weights = {}
                            active_supplements = []
                            for i, t in enumerate(blend_assets):
                                if clean_w[i] > 0:
                                    final_weights[t] = float(clean_w[i])
                                    if t != best_etf_ticker:
                                        active_supplements.append(t)

                            blend_results.append({
                                "strategy_title": strat_name,
                                "supplements": active_supplements,
                                "weights": final_weights,
                                "metrics": metrics,
                                "teDecomposition": te_decomp,
                                "score": round(score, 4),
                                "baselineScore": round(baseline_score, 4),
                                "baselineMetrics": baseline_metrics,
                                "anchored": using_custom,
                                "belowBaseline": bool(score <= baseline_score),
                            })

    # Decouple results by generating multi-pathway strategy cards instead of just keeping index 0
    blend_results.sort(key=lambda x: x["score"], reverse=True)
    
    added_titles = set()
    for best in blend_results:
        if best["strategy_title"] in added_titles:
            continue
        added_titles.add(best["strategy_title"])
        
        # Fetch prices; drop any ticker whose price can't be obtained (can't execute)
        raw_alloc = []
        for t, w in best["weights"].items():
            p = await _get_price_safe(t)
            if p and p > 0:
                raw_alloc.append((t, w, p))

        # Renormalize weights of surviving tickers to sum to 1.0
        w_sum = sum(w for _, w, _ in raw_alloc)
        if w_sum <= 0:
            alloc_parts = []
        else:
            alloc_parts = []
            remaining = len(raw_alloc)
            running_capital = 0.0
            for idx, (t, w, p) in enumerate(raw_alloc):
                norm_w = w / w_sum
                # Last item gets remaining capital to avoid rounding gap
                if idx == remaining - 1:
                    capital = round(total_harvestable_value - running_capital, 2)
                else:
                    capital = round(total_harvestable_value * norm_w, 2)
                    running_capital += capital
                shares = math.floor(capital / p) if p > 0 else 0
                alloc_parts.append({
                    "ticker": t,
                    "weight": round(norm_w * 100, 1),
                    "capital": capital,
                    "price": round(p, 2),
                    "shares": shares,
                })
            # Final guard: ensure displayed weights sum to 100.0 (fix any rounding loss)
            w_display_sum = sum(a["weight"] for a in alloc_parts)
            if alloc_parts and abs(w_display_sum - 100.0) >= 0.2:
                alloc_parts[-1]["weight"] = round(100.0 - sum(a["weight"] for a in alloc_parts[:-1]), 1)

        supp_names = ", ".join(best["supplements"])
        m = best["metrics"]
        total_tax_savings = total_losses * tax_rate_pct / 100
        blend_trade_trigger = _compute_trade_trigger(
            tax_benefit=total_tax_savings,
            total_harvestable_value=total_harvestable_value,
            tracking_error_ann=m.get("trackingErrorAnn", 0.05),
        )
        suggestions.append({
            "type": "optimal_blend",
            "title": best["strategy_title"],
            "description": (
                f"Replace harvested positions with {best_etf_ticker + ' + ' if best['weights'].get(best_etf_ticker, 0) > 0 else ''}{supp_names}. "
                f"V2 Score: {best['score']:.3f} (TE: {m.get('trackingError')}%, coint_p: {m.get('cointegrationPValue')}, tail_dep: {m.get('lowerTailDependence')}%)"
            ),
            "aiRationale": ai_rationale,
            "allocation": alloc_parts,
            "metrics": m,
            "teDecomposition": best.get("teDecomposition"),
            "score": best["score"],
            "baselineScore": best["baselineScore"],
            "baselineMetrics": best["baselineMetrics"],
            "totalCapital": round(total_harvestable_value, 2),
            "tradeTrigger": blend_trade_trigger,
            "anchored": best.get("anchored", False),
            "belowBaseline": best.get("belowBaseline", False),
            "alternateBlends": [],
        })

    # 2. SELECTIVE HARVEST
    if 2 <= len(harvestable_list) <= 6:
        selective_results: list[dict] = []
        for size in range(1, len(harvestable_list)):
            for combo in combinations(range(len(harvestable_list)), size):
                harvest_set = [harvestable_list[i] for i in combo]
                keep_set = [harvestable_list[i] for i in range(len(harvestable_list)) if i not in combo]
                harvest_tickers = [h["ticker"] for h in harvest_set]
                keep_tickers = [h["ticker"] for h in keep_set]
                subset_losses = sum(h.get("losses", 0) for h in harvest_set)

                remaining_tickers = [h["ticker"] for h in non_harvestable_list] + keep_tickers
                harvest_value = sum(h.get("currentValue", 0) for h in harvest_set)
                remaining_value = sum(holding_details.get(t, {}).get("currentValue", 0) for t in remaining_tickers)
                total_new = remaining_value + harvest_value
                if total_new == 0:
                    continue

                weights = {}
                for t in remaining_tickers:
                    v = holding_details.get(t, {}).get("currentValue", 0)
                    if v > 0:
                        weights[t] = v / total_new
                weights[best_etf_ticker] = harvest_value / total_new

                repl_returns = _build_weighted_return_series(price_data, weights)
                if repl_returns.empty or len(repl_returns) < 30:
                    continue

                metrics = _compute_quant_metrics(orig_returns, repl_returns)
                if not metrics:
                    continue
                tax_capture = subset_losses / total_losses if total_losses else 0
                score = _score_strategy(metrics, tax_capture)

                if score > baseline_score:
                    selective_results.append({
                        "harvestTickers": harvest_tickers,
                        "keepTickers": keep_tickers,
                        "losses": round(subset_losses, 2),
                        "taxSavings": round(subset_losses * tax_rate_pct / 100, 2),
                        "taxCapturePct": round(tax_capture * 100, 1),
                        "metrics": metrics,
                        "score": round(score, 4),
                    })

        selective_results.sort(key=lambda x: x["score"], reverse=True)
        if selective_results:
            best_s = selective_results[0]
            m = best_s["metrics"]
            sel_trade_trigger = _compute_trade_trigger(
                tax_benefit=best_s["taxSavings"],
                total_harvestable_value=total_harvestable_value,
                tracking_error_ann=m.get("trackingErrorAnn", 0.05),
            )
            suggestions.append({
                "type": "selective_harvest",
                "title": "Selective Quant Harvest (V2)",
                "description": (
                    f"Harvest {', '.join(best_s['harvestTickers'])} "
                    f"(keep {', '.join(best_s['keepTickers'])}). "
                    f"Coint_p {m.get('cointegrationPValue')}, Tail Dep {m.get('lowerTailDependence')}%."
                ),
                **best_s,
                "baselineMetrics": baseline_metrics,
                "baselineScore": round(baseline_score, 4),
                "tradeTrigger": sel_trade_trigger,
                "alternates": [
                    {
                        "harvestTickers": s["harvestTickers"],
                        "keepTickers": s["keepTickers"],
                        "losses": s["losses"],
                        "taxCapturePct": s["taxCapturePct"],
                        "metrics": s["metrics"],
                        "score": s["score"],
                    }
                    for s in selective_results[1:3]
                ],
            })

    suggestions.sort(key=lambda s: s.get("score", 0), reverse=True)
    return {
        "suggestions": suggestions,
        "ai_stocks": ai_stocks,
        "ai_etfs": ai_etfs,
        "ai_rationale": ai_rationale,
    }



# ---------------------------------------------------------------------------
# Options helpers (reuse patterns from concentration / pmcc services)
# ---------------------------------------------------------------------------

def _find_closest_expiration(expirations: list[str], target_days: int) -> tuple[str | None, int]:
    """Return (expiration_str, actual_dte) closest to target_days from today."""
    today = datetime.now().date()
    target_date = today + timedelta(days=target_days)
    best_exp = None
    min_diff = 99999
    actual_dte = 0
    for exp_str in expirations:
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            diff = abs((exp_date - target_date).days)
            if diff < min_diff:
                min_diff = diff
                best_exp = exp_str
                actual_dte = (exp_date - today).days
        except Exception:
            pass
    return best_exp, actual_dte


def _find_closest_option(df: pd.DataFrame, target_strike: float) -> dict | None:
    """Find the option row closest to target_strike and return a clean dict."""
    if df.empty:
        return None
    df_copy = df.copy()
    df_copy["diff"] = abs(df_copy["strike"] - target_strike)
    closest = df_copy.sort_values("diff").iloc[0]
    ask = safe_float(closest.get("ask", 0.0))
    bid = safe_float(closest.get("bid", 0.0))
    mid = round((bid + ask) / 2, 2) if bid > 0 and ask > 0 else safe_float(closest.get("lastPrice", 0.0))
    return {
        "strike": safe_float(closest.get("strike", 0)),
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "iv": safe_float(closest.get("impliedVolatility", 0)),
        "oi": int(safe_float(closest.get("openInterest", 0))),
        "volume": int(safe_float(closest.get("volume", 0))),
    }


# ---------------------------------------------------------------------------
# Strategy builders
# ---------------------------------------------------------------------------

def _build_synthetic_long_legs(peer_ticker: str, shares: int, target_days: int = 50) -> dict:
    """
    Build a Synthetic Long on the peer:
      Buy deep ITM Call (~0.80 delta → strike ~10% below spot)
      Sell OTM Put  (~-0.20 delta → strike ~8% below spot)
    Uses peer options to avoid wash-sale on the original ticker.
    """
    stock = yf.Ticker(peer_ticker)
    info = stock.info or {}
    price = info.get("currentPrice") or info.get("regularMarketPrice") or 0.0
    if not price:
        try:
            hist = stock.history(period="1d")
            if not hist.empty:
                price = float(hist["Close"].iloc[-1])
        except Exception:
            pass
    if not price:
        return {"error": f"No price for {peer_ticker}"}

    try:
        expirations = list(stock.options)
    except Exception:
        return {"error": f"No options chain for {peer_ticker}"}
    if not expirations:
        return {"error": f"No expirations for {peer_ticker}"}

    exp_str, dte = _find_closest_expiration(expirations, target_days)
    if not exp_str or dte < 7:
        return {"error": "No suitable expiration found"}

    chain = stock.option_chain(exp_str)
    calls = chain.calls
    puts = chain.puts

    # Deep ITM call: strike ~10% below current price
    call_target = price * 0.90
    call_opt = _find_closest_option(calls, call_target)
    if not call_opt:
        return {"error": "Could not find deep ITM call"}

    # OTM put: strike ~8% below current price
    put_target = price * 0.92
    put_opt = _find_closest_option(puts, put_target)
    if not put_opt:
        return {"error": "Could not find OTM put"}

    contracts = math.ceil(shares / 100)

    net_debit = round((call_opt["mid"] - put_opt["mid"]) * 100 * contracts, 2)
    delta_exposure = round(contracts * 100 * 0.80, 0)  # approximate

    legs = [
        {
            "action": "BUY",
            "type": "Call",
            "ticker": peer_ticker,
            "strike": call_opt["strike"],
            "bid": call_opt["bid"],
            "ask": call_opt["ask"],
            "mid": call_opt["mid"],
            "iv": call_opt["iv"],
            "oi": call_opt["oi"],
            "volume": call_opt["volume"],
            "qty": contracts,
            "expiration": exp_str,
            "purpose": "Deep ITM Call — replicates ~80 delta long exposure",
        },
        {
            "action": "SELL",
            "type": "Put",
            "ticker": peer_ticker,
            "strike": put_opt["strike"],
            "bid": put_opt["bid"],
            "ask": put_opt["ask"],
            "mid": put_opt["mid"],
            "iv": put_opt["iv"],
            "oi": put_opt["oi"],
            "volume": put_opt["volume"],
            "qty": contracts,
            "expiration": exp_str,
            "purpose": "OTM Put — offsets call cost, adds ~20 delta",
        },
    ]

    return {
        "legs": legs,
        "contracts": contracts,
        "net_debit": net_debit,
        "delta_exposure": int(delta_exposure),
        "expiration": exp_str,
        "dte": dte,
        "peer_price": round(price, 2),
    }


def _build_protective_collar_legs(peer_ticker: str, shares: int, target_days: int = 50) -> dict:
    """
    Build a Protective Collar on the peer:
      Buy shares of peer
      Buy OTM Put  (strike ~6% below spot — downside floor)
      Sell OTM Call (strike ~6% above spot — upside cap, funds the put)
    """
    stock = yf.Ticker(peer_ticker)
    info = stock.info or {}
    price = info.get("currentPrice") or info.get("regularMarketPrice") or 0.0
    if not price:
        try:
            hist = stock.history(period="1d")
            if not hist.empty:
                price = float(hist["Close"].iloc[-1])
        except Exception:
            pass
    if not price:
        return {"error": f"No price for {peer_ticker}"}

    try:
        expirations = list(stock.options)
    except Exception:
        return {"error": f"No options chain for {peer_ticker}"}
    if not expirations:
        return {"error": f"No expirations for {peer_ticker}"}

    exp_str, dte = _find_closest_expiration(expirations, target_days)
    if not exp_str or dte < 7:
        return {"error": "No suitable expiration found"}

    chain = stock.option_chain(exp_str)
    calls = chain.calls
    puts = chain.puts

    contracts = math.ceil(shares / 100)

    # OTM Put: ~6% below
    put_target = price * 0.94
    put_opt = _find_closest_option(puts, put_target)

    # OTM Call: ~6% above
    call_target = price * 1.06
    call_opt = _find_closest_option(calls, call_target)

    if not put_opt or not call_opt:
        return {"error": "Could not find collar strikes"}

    net_collar_cost = round((put_opt["mid"] - call_opt["mid"]) * 100 * contracts, 2)
    shares_cost = round(price * shares, 2)

    legs = [
        {
            "action": "BUY",
            "type": "Shares",
            "ticker": peer_ticker,
            "strike": None,
            "bid": round(price, 2),
            "ask": round(price, 2),
            "mid": round(price, 2),
            "iv": None,
            "oi": None,
            "volume": None,
            "qty": shares,
            "expiration": None,
            "purpose": f"Buy {shares} shares of {peer_ticker} to replace sold position",
        },
        {
            "action": "BUY",
            "type": "Put",
            "ticker": peer_ticker,
            "strike": put_opt["strike"],
            "bid": put_opt["bid"],
            "ask": put_opt["ask"],
            "mid": put_opt["mid"],
            "iv": put_opt["iv"],
            "oi": put_opt["oi"],
            "volume": put_opt["volume"],
            "qty": contracts,
            "expiration": exp_str,
            "purpose": "OTM Put — downside floor protection",
        },
        {
            "action": "SELL",
            "type": "Call",
            "ticker": peer_ticker,
            "strike": call_opt["strike"],
            "bid": call_opt["bid"],
            "ask": call_opt["ask"],
            "mid": call_opt["mid"],
            "iv": call_opt["iv"],
            "oi": call_opt["oi"],
            "volume": call_opt["volume"],
            "qty": contracts,
            "expiration": exp_str,
            "purpose": "OTM Call — funds the put, caps upside",
        },
    ]

    protection_floor = round(put_opt["strike"], 2)
    protection_cap = round(call_opt["strike"], 2)

    return {
        "legs": legs,
        "contracts": contracts,
        "shares_cost": shares_cost,
        "net_collar_cost": net_collar_cost,
        "total_cost": round(shares_cost + net_collar_cost, 2),
        "protection_range": {"floor": protection_floor, "cap": protection_cap},
        "expiration": exp_str,
        "dte": dte,
        "peer_price": round(price, 2),
    }

