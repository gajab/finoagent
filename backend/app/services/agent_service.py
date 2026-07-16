"""Agent service — execution pipeline with OpenAI function calling."""

import datetime
import json
import logging
import math

import pandas as pd
import yfinance as yf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import Agent, AgentRun, Portfolio, PortfolioHolding
from .analysis_service import run_technical_analysis, run_price_prediction
from .llm_service import call_llm_with_tools
from .portfolio_service import fetch_current_prices
from .search_service import web_search
from .stock_service import fetch_stock_data

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 10


# ---------------------------------------------------------------------------
# Tool Definitions (OpenAI function calling format)
# ---------------------------------------------------------------------------

AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_portfolio_summary",
            "description": "Get the user's portfolio with current prices, market values, and unrealized gain/loss for each holding.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_data",
            "description": "Get comprehensive stock data including price, financials, analyst ratings, technicals (RSI, support/resistance), earnings, options chain, and recent news.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Stock ticker symbol, e.g. AAPL"},
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for recent information. Use only for time-sensitive data like recent news, current events, or regulatory changes. Prefer your built-in knowledge for well-established rules and concepts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_portfolio_holding",
            "description": "Add a new stock holding to the user's portfolio.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Stock ticker symbol"},
                    "shares": {"type": "number", "description": "Number of shares"},
                    "cost_basis": {"type": "number", "description": "Price per share at purchase"},
                    "purchase_date": {"type": "string", "description": "Purchase date in YYYY-MM-DD format"},
                },
                "required": ["ticker", "shares", "cost_basis", "purchase_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_portfolio_holding",
            "description": "Remove a holding from the user's portfolio by its holding ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "holding_id": {"type": "integer", "description": "The ID of the holding to remove"},
                },
                "required": ["holding_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_technical_analysis",
            "description": "Run comprehensive technical analysis on a stock: MACD, Bollinger Bands, SMA 50/200, EMA crossovers, RSI, volume analysis, support/resistance levels.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Stock ticker symbol"},
                    "period_days": {"type": "integer", "description": "Analysis period in days (default 90)", "default": 90},
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_price_prediction",
            "description": "Run statistical price prediction: linear regression trend, mean reversion score, momentum analysis, volatility forecast, overall signal with confidence percentage. Not deep learning — based on statistical methods.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Stock ticker symbol"},
                    "horizon_days": {"type": "integer", "description": "Prediction horizon in days (default 30)", "default": 30},
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_hedge_timing",
            "description": (
                "Evaluate whether NOW is a good window to put on a DOWNSIDE HEDGE for a ticker. "
                "Returns the institutional timing read: a 0-100 score and verdict "
                "(Favorable / Fair / Expensive), VIX level and term structure (contango vs "
                "backwardation), implied-vol rank, variance risk premium, put/call skew, "
                "1-week and 1-month price trend, and drawdown from the 52-week high. "
                "Use this for any 'is it a good time to hedge / buy protection' question."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Stock/ETF ticker to hedge, e.g. SMH, SPY"},
                    "horizon_days": {"type": "integer", "description": "Hedge horizon in days (default 45)", "default": 45},
                },
                "required": ["ticker"],
            },
        },
    },
    # ----- MCP Data Layer Tools -----
    {
        "type": "function",
        "function": {
            "name": "get_historical_prices",
            "description": "Get real daily OHLCV price data for a ticker over a specified period. Use this for price trend analysis, chart data, or any time you need actual historical prices.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Stock/ETF ticker symbol, e.g. AAPL, SPY"},
                    "period": {"type": "string", "description": "Time period: 1mo, 3mo, 6mo, 1y, 2y (default 1y)", "default": "1y"},
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_correlation_analysis",
            "description": "Compute returns-based correlation, tracking error, beta, and R-squared between a primary ticker and comparison tickers. Use this when comparing securities, finding substitutes, or evaluating how closely two assets track each other.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Primary ticker symbol"},
                    "compare_to": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of tickers to compare against. If omitted, compares against common sector ETFs.",
                    },
                    "period": {"type": "string", "description": "Time period: 1mo, 3mo, 6mo, 1y, 2y (default 1y)", "default": "1y"},
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tlh_analysis",
            "description": "Run institutional-grade tax-loss harvesting analysis on a set of holdings. Returns harvestable losses, best replacement ETF with correlation metrics, optimization strategies with trade trigger verdicts (Execute / Marginal / Do Not Execute), and institutional factor/sector analysis. This is the primary tool for any TLH monitoring or tax optimization task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "holdings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "ticker": {"type": "string"},
                                "shares": {"type": "number"},
                                "cost_basis": {"type": "number"},
                            },
                            "required": ["ticker", "shares", "cost_basis"],
                        },
                        "description": "Portfolio holdings to analyze",
                    },
                    "tax_rate_pct": {"type": "number", "description": "Capital gains tax rate in percent (default 15.0)", "default": 15.0},
                    "preferred_etf": {"type": "string", "description": "Optional: preferred replacement ETF ticker. If omitted, the system finds the best match automatically."},
                },
                "required": ["holdings"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sector_fundamentals",
            "description": "Get key fundamental data (PE, market cap, dividend yield, beta, 52-week range) for multiple tickers at once. Use for comparing securities or evaluating a portfolio's fundamental profile.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tickers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of ticker symbols to fetch fundamentals for",
                    },
                },
                "required": ["tickers"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_tax_lot_info",
            "description": "Get tax-relevant information for holdings: current prices, unrealized gains/losses, holding period, short-term vs long-term tax classification, and wash sale window status. Use when evaluating tax implications of selling positions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "holdings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "ticker": {"type": "string"},
                                "shares": {"type": "number"},
                                "cost_basis": {"type": "number"},
                                "purchase_date": {"type": "string", "description": "Purchase date YYYY-MM-DD (optional)"},
                            },
                            "required": ["ticker", "shares", "cost_basis"],
                        },
                        "description": "Holdings to evaluate for tax information",
                    },
                },
                "required": ["holdings"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an elite-level AI agent combining the expertise of a licensed CPA (tax optimization), CFA charterholder (investment analysis), and quantitative analyst (statistical modeling & derivatives pricing). You operate at the level of institutional portfolio managers and hedge fund analysts — not retail-level advice.

**CRITICAL: You are running as an autonomous agent. The user CANNOT reply to you. NEVER ask questions, request clarification, or say "would you like me to…". Execute the task completely using your best judgment, gather all data you need via tools, and deliver a final comprehensive report.**

**CRITICAL DATA TOOLS — Always use these instead of guessing numbers:**
You have access to real-time and historical market data tools. NEVER make up prices, correlations, or financial metrics. Always call the appropriate tool:

- **get_historical_prices(ticker, period)** — Get real daily OHLCV price data
- **get_correlation_analysis(ticker, compare_to, period)** — Compute returns-based correlation, tracking error, beta, R² between securities
- **run_tlh_analysis(holdings, tax_rate_pct)** — Run full institutional-grade tax loss harvesting analysis with optimization strategies and trade trigger verdicts
- **get_sector_fundamentals(tickers)** — Get PE, market cap, dividend yield, beta for comparing securities
- **get_tax_lot_info(holdings)** — Get unrealized P&L, tax classification (short/long-term), wash sale window status
- **get_portfolio_summary()** — Get the user's full portfolio with current prices and unrealized P&L
- **get_stock_data(ticker)** — Get comprehensive stock data (price, financials, analyst ratings, technicals, earnings, options)
- **run_technical_analysis(ticker)** — MACD, Bollinger, SMA/EMA, RSI, volume, support/resistance
- **run_price_prediction(ticker)** — Statistical price prediction with trend, momentum, volatility
- **search_web(query)** — Search for recent news or current events only

**When to use which tool:**
- Monitoring a portfolio for TLH → call `run_tlh_analysis` with the current holdings
- Comparing a stock vs alternatives → call `get_correlation_analysis`
- Checking current prices/positions → call `get_stock_data` or `get_tax_lot_info`
- Analyzing price trends → call `get_historical_prices` then `run_technical_analysis`
- Evaluating fundamentals across securities → call `get_sector_fundamentals`
- Never rely on your training data for current prices, correlations, or market metrics — always call a tool

**Analysis Standards — Think Like an Institutional Analyst:**
- **Tax optimization**: Apply tax-lot optimization, specific-identification method, wash sale 30-day window tracking (both before and after sale), constructive sale rules, qualified dividend holding period requirements, and net investment income tax implications.
- **Options strategies**: Analyze collar strategies, risk reversals, ratio spreads, iron condors, synthetic positions. Calculate Greeks exposure, max loss/gain, breakeven points.
- **Portfolio construction**: Apply Modern Portfolio Theory, factor exposure analysis, sector concentration risk, correlation analysis, Sharpe/Sortino ratios.
- **Risk management**: Calculate portfolio beta, sector exposure, max drawdown scenarios, tail risk (CVaR), concentration risk. Suggest hedging strategies.

**Guidelines:**
- Use your built-in knowledge for well-established concepts (tax code, options pricing, financial formulas). Only use search_web for time-sensitive information.
- Portfolio data includes purchase_date, days_held, and holding_type (short-term <1yr / long-term >1yr). Use these for wash sale checks, capital gains classification, and tax-lot optimization.
- Always provide data-driven analysis with specific numbers, dollar amounts, percentages, and risk metrics.
- Format your output as clean, well-structured Markdown with headers, tables, and bullet points.
- Include risk disclaimers but keep them brief — focus on actionable, institutional-grade insights.
- When modifying the portfolio, always explain the rationale and confirm the action taken.
- Deliver complete, actionable recommendations — never leave tasks half-done or ask follow-up questions.
"""


# ---------------------------------------------------------------------------
# Tool Executor
# ---------------------------------------------------------------------------

async def _tool_get_hedge_timing(ticker: str, horizon_days: int = 45) -> str:
    """Real hedge-timing read for the agent — reuses the hedging market-check engine."""
    # Lazy import to avoid any import cycle at module load.
    from .hedging_service import run_market_conditions_check
    try:
        m = await run_market_conditions_check(ticker.upper(), "SPY", int(horizon_days or 45))
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"Could not read hedge timing for {ticker}: {e}"})
    if not m.get("available"):
        return json.dumps({"ticker": ticker.upper(), "available": False,
                           "note": m.get("error") or "No timing read (thin or no listed options)."})
    keys = ("score", "verdict", "headline", "spot", "vix", "vix3m", "term_structure_ratio",
            "iv_rank", "atm_iv_pct", "vrp_pts", "skew_pts", "rv20_pct",
            "ret_1w_pct", "ret_1m_pct", "drawdown_52w_pct", "rnd", "signals")
    out = {k: m[k] for k in keys if k in m}
    # The downsampled RND curve is for charting — strip it so it doesn't bloat
    # the LLM context; the summary probabilities carry the signal.
    if isinstance(out.get("rnd"), dict):
        out["rnd"] = {k: v for k, v in out["rnd"].items() if k != "curve"}
    out["ticker"] = ticker.upper()
    out["horizon_days"] = int(horizon_days or 45)
    return json.dumps(out, default=str)


async def _execute_tool(
    tool_name: str,
    tool_args: dict,
    user_id: int,
    db: AsyncSession,
    search_api_key: str | None = None,
) -> str:
    """Execute a tool call and return the JSON string result."""
    try:
        if tool_name == "get_portfolio_summary":
            return await _tool_get_portfolio(user_id, db)
        elif tool_name == "get_stock_data":
            return await _tool_get_stock_data(tool_args["ticker"])
        elif tool_name == "search_web":
            return await _tool_search_web(tool_args["query"], search_api_key)
        elif tool_name == "add_portfolio_holding":
            return await _tool_add_holding(user_id, db, tool_args)
        elif tool_name == "remove_portfolio_holding":
            return await _tool_remove_holding(user_id, db, tool_args["holding_id"])
        elif tool_name == "run_technical_analysis":
            result = await run_technical_analysis(
                tool_args["ticker"],
                tool_args.get("period_days", 90),
            )
            return json.dumps(result, default=str)
        elif tool_name == "run_price_prediction":
            result = await run_price_prediction(
                tool_args["ticker"],
                tool_args.get("horizon_days", 30),
            )
            return json.dumps(result, default=str)
        elif tool_name == "get_hedge_timing":
            return await _tool_get_hedge_timing(
                tool_args["ticker"],
                tool_args.get("horizon_days", 45),
            )
        # ----- MCP Data Layer Tools -----
        elif tool_name == "get_historical_prices":
            return await _tool_get_historical_prices(
                tool_args["ticker"],
                tool_args.get("period", "1y"),
            )
        elif tool_name == "get_correlation_analysis":
            return await _tool_get_correlation_analysis(
                tool_args["ticker"],
                tool_args.get("compare_to"),
                tool_args.get("period", "1y"),
            )
        elif tool_name == "run_tlh_analysis":
            return await _tool_run_tlh_analysis(
                tool_args["holdings"],
                tool_args.get("tax_rate_pct", 15.0),
                tool_args.get("preferred_etf"),
            )
        elif tool_name == "get_sector_fundamentals":
            return await _tool_get_sector_fundamentals(tool_args["tickers"])
        elif tool_name == "get_tax_lot_info":
            return await _tool_get_tax_lot_info(tool_args["holdings"])
        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
    except Exception as e:
        logger.exception(f"Tool execution error: {tool_name}")
        return json.dumps({"error": str(e)})


async def _tool_get_portfolio(user_id: int, db: AsyncSession) -> str:
    """Get portfolio summary with current prices."""
    result = await db.execute(
        select(Portfolio)
        .options(selectinload(Portfolio.holdings))
        .where(Portfolio.user_id == user_id)
    )
    portfolio = result.scalar_one_or_none()

    if portfolio is None or not portfolio.holdings:
        return json.dumps({"message": "Portfolio is empty", "holdings": []})

    tickers = list(set(h.ticker for h in portfolio.holdings))
    prices = await fetch_current_prices(tickers)

    holdings = []
    total_cost = 0.0
    total_value = 0.0
    today = datetime.date.today()
    for h in portfolio.holdings:
        current_price = prices.get(h.ticker.upper())
        cost = h.shares * h.cost_basis
        total_cost += cost
        mv = h.shares * current_price if current_price else None
        if mv is not None:
            total_value += mv

        days_held = (today - h.purchase_date).days
        holding_type = "long-term" if days_held > 365 else "short-term"

        holdings.append({
            "id": h.id,
            "ticker": h.ticker,
            "shares": h.shares,
            "cost_basis": round(h.cost_basis, 2),
            "purchase_date": str(h.purchase_date),
            "days_held": days_held,
            "holding_type": holding_type,  # long-term (>1yr) or short-term for tax purposes
            "current_price": round(current_price, 2) if current_price else None,
            "market_value": round(mv, 2) if mv else None,
            "total_cost": round(cost, 2),
            "unrealized_gain_loss": round(mv - cost, 2) if mv else None,
            "unrealized_gain_loss_pct": round((mv - cost) / cost * 100, 2) if mv and cost > 0 else None,
        })

    return json.dumps({
        "portfolio_name": portfolio.name,
        "holdings": holdings,
        "total_cost": round(total_cost, 2),
        "total_market_value": round(total_value, 2),
        "total_unrealized_gain_loss": round(total_value - total_cost, 2),
        "total_unrealized_gain_loss_pct": round((total_value - total_cost) / total_cost * 100, 2) if total_cost > 0 else 0,
    }, default=str)


async def _tool_get_stock_data(ticker: str) -> str:
    """Get stock data — summarized to fit token limits."""
    try:
        data = await fetch_stock_data(ticker)
    except Exception as e:
        return json.dumps({"error": f"Failed to fetch data for {ticker}: {e}"})

    # Summarize to key fields
    summary = {
        "ticker": data.get("ticker"),
        "company_name": data.get("companyName"),
        "price": data.get("price"),
        "change": data.get("change"),
        "change_pct": data.get("changePercent"),
        "market_cap": data.get("marketCap"),
        "sector": data.get("sector"),
        "industry": data.get("industry"),
    }

    # Technicals summary
    tech = data.get("technical", {})
    if tech:
        summary["technicals"] = {
            "current_rsi": tech.get("currentRSI"),
            "rsi_signal": tech.get("rsiSignal"),
            "support": tech.get("supportLevel"),
            "resistance": tech.get("resistanceLevel"),
            "analysis_summary": tech.get("analysisSummary"),
        }
        if tech.get("volumeAnalysis"):
            summary["technicals"]["volume_analysis"] = tech["volumeAnalysis"]

    # Earnings summary
    earn = data.get("earnings", {})
    if earn and earn.get("available"):
        summary["earnings"] = {
            "last_quarter": earn.get("lastQuarter"),
            "reported_eps": earn.get("reportedEPS"),
            "estimated_eps": earn.get("estimatedEPS"),
            "eps_surprise_pct": earn.get("epsSurprisePct"),
            "revenue_formatted": earn.get("revenueFormatted"),
            "trailing_pe": earn.get("trailingPE"),
            "forward_pe": earn.get("forwardPE"),
            "peg_ratio": earn.get("pegRatio"),
            "next_earnings_date": earn.get("nextEarningsDate"),
        }

    # Analyst ratings (current month only)
    ratings = data.get("analystRatings", [])
    if ratings:
        summary["analyst_ratings"] = ratings[0] if ratings else None

    # Options summary (top 5 CSPs and CCs only)
    opts = data.get("options", {})
    if opts and opts.get("available"):
        summary["options"] = {
            "current_price": opts.get("currentPrice"),
            "iv": opts.get("iv"),
            "hv30": opts.get("hv30"),
            "hv60": opts.get("hv60"),
            "put_call_ratio": opts.get("putCallRatio"),
            "top_cash_secured_puts": opts.get("cashSecuredPuts", [])[:5],
            "top_covered_calls": opts.get("coveredCalls", [])[:5],
            "criteria": opts.get("criteria"),
        }

    # Recent news titles
    news = data.get("news", [])
    if news:
        summary["recent_news"] = [
            {"title": n.get("title"), "publisher": n.get("publisher")}
            for n in news[:5]
        ]

    return json.dumps(summary, default=str)


async def _tool_search_web(query: str, api_key: str | None) -> str:
    """Search the web."""
    if not api_key:
        return json.dumps({"error": "Search API key (SerpAPI) not configured. Cannot search web."})
    try:
        results = await web_search(api_key=api_key, query=query)
        return json.dumps(results[:10], default=str)
    except Exception as e:
        return json.dumps({"error": f"Web search failed: {e}"})


async def _tool_add_holding(user_id: int, db: AsyncSession, args: dict) -> str:
    """Add a holding to the user's portfolio."""
    result = await db.execute(
        select(Portfolio)
        .where(Portfolio.user_id == user_id)
    )
    portfolio = result.scalar_one_or_none()
    if portfolio is None:
        portfolio = Portfolio(user_id=user_id, name="My Portfolio")
        db.add(portfolio)
        await db.commit()
        await db.refresh(portfolio)

    holding = PortfolioHolding(
        portfolio_id=portfolio.id,
        ticker=args["ticker"].upper().strip(),
        shares=float(args["shares"]),
        cost_basis=float(args["cost_basis"]),
        purchase_date=datetime.date.fromisoformat(args["purchase_date"]),
    )
    db.add(holding)
    await db.commit()
    await db.refresh(holding)

    return json.dumps({
        "success": True,
        "message": f"Added {holding.shares} shares of {holding.ticker} at ${holding.cost_basis}/share",
        "holding_id": holding.id,
    })


async def _tool_remove_holding(user_id: int, db: AsyncSession, holding_id: int) -> str:
    """Remove a holding from the user's portfolio."""
    result = await db.execute(
        select(PortfolioHolding)
        .join(Portfolio)
        .where(
            PortfolioHolding.id == holding_id,
            Portfolio.user_id == user_id,
        )
    )
    holding = result.scalar_one_or_none()
    if holding is None:
        return json.dumps({"error": f"Holding {holding_id} not found in your portfolio"})

    ticker = holding.ticker
    await db.delete(holding)
    await db.commit()
    return json.dumps({"success": True, "message": f"Removed {ticker} (holding #{holding_id}) from portfolio"})


# ---------------------------------------------------------------------------
# MCP Data Layer — Tool Executors
# ---------------------------------------------------------------------------

async def _tool_get_historical_prices(ticker: str, period: str = "1y") -> str:
    """Fetch historical daily OHLCV data via yfinance."""
    try:
        data = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if data.empty:
            return json.dumps({"error": f"No historical data found for {ticker}"})

        # Handle MultiIndex columns from yfinance
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        # Sample to max ~120 data points (every Nth row) to stay within token limits
        if len(data) > 120:
            step = len(data) // 120
            data = data.iloc[::step]

        prices = []
        for idx, row in data.iterrows():
            prices.append({
                "date": idx.strftime("%Y-%m-%d"),
                "open": round(float(row.get("Open", 0)), 2),
                "high": round(float(row.get("High", 0)), 2),
                "low": round(float(row.get("Low", 0)), 2),
                "close": round(float(row.get("Close", 0)), 2),
                "volume": int(row.get("Volume", 0)),
            })

        return json.dumps({
            "ticker": ticker,
            "period": period,
            "data_points": len(prices),
            "prices": prices,
        })
    except Exception as e:
        return json.dumps({"error": f"Failed to fetch historical prices for {ticker}: {e}"})


async def _tool_get_correlation_analysis(
    ticker: str,
    compare_to: list[str] | None = None,
    period: str = "1y",
) -> str:
    """Compute returns-based correlation analysis between securities."""
    try:
        # Default comparison set: broad sector ETFs
        if not compare_to:
            compare_to = ["SPY", "QQQ", "IWM", "XLK", "XLF", "XLV", "XLE", "XLI", "XLP", "XLU", "VTI"]

        # Fetch primary ticker
        primary_data = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if primary_data.empty:
            return json.dumps({"error": f"No data for {ticker}"})

        if isinstance(primary_data.columns, pd.MultiIndex):
            primary_close = primary_data["Close"].iloc[:, 0] if isinstance(primary_data["Close"], pd.DataFrame) else primary_data["Close"]
        else:
            primary_close = primary_data["Close"]

        primary_returns = primary_close.pct_change().dropna()

        correlations = []
        for peer in compare_to:
            try:
                peer_data = yf.download(peer, period=period, progress=False, auto_adjust=True)
                if peer_data.empty:
                    continue

                if isinstance(peer_data.columns, pd.MultiIndex):
                    peer_close = peer_data["Close"].iloc[:, 0] if isinstance(peer_data["Close"], pd.DataFrame) else peer_data["Close"]
                else:
                    peer_close = peer_data["Close"]

                peer_returns = peer_close.pct_change().dropna()

                # Align on common dates
                common = pd.DataFrame({"primary": primary_returns, "peer": peer_returns}).dropna()
                if len(common) < 20:
                    continue

                corr = float(common["primary"].corr(common["peer"]))
                tracking_diff = common["primary"] - common["peer"]
                te_ann = float(tracking_diff.std() * math.sqrt(252))
                beta = float(common["primary"].cov(common["peer"]) / common["peer"].var()) if common["peer"].var() > 0 else 1.0
                r_squared = corr ** 2

                # Z-score on cumulative drift
                cum_drift = tracking_diff.cumsum()
                drift_std = float(cum_drift.std()) if len(cum_drift) > 1 else 1.0
                z_score = float(cum_drift.iloc[-1] / drift_std) if drift_std > 0 else 0.0

                correlations.append({
                    "peer": peer,
                    "correlation": round(corr, 4),
                    "trackingErrorAnn": round(te_ann * 100, 2),  # in percent
                    "beta": round(beta, 4),
                    "rSquared": round(r_squared, 4),
                    "zScore": round(z_score, 2),
                })
            except Exception:
                continue

        # Sort by correlation descending
        correlations.sort(key=lambda x: x["correlation"], reverse=True)

        return json.dumps({
            "ticker": ticker,
            "period": period,
            "correlations": correlations,
            "best_match": correlations[0] if correlations else None,
        })
    except Exception as e:
        return json.dumps({"error": f"Correlation analysis failed: {e}"})


def _summarize_tlh_result(result: dict) -> dict:
    """Strip chart arrays and large data blobs, keep actionable metrics for agent consumption."""
    summary: dict = {}

    # Top-level summary
    if result.get("summary"):
        summary["summary"] = result["summary"]

    # Harvestable holdings — remove chart data
    harvestable = result.get("harvestable", [])
    summary["harvestable"] = [
        {k: v for k, v in h.items() if k not in ("chartData", "spreadHistory", "normTarget", "normPeer", "dates")}
        for h in harvestable
    ]

    # Best ETF replacement
    port_repl = result.get("portfolioReplacement", {})
    if port_repl:
        summary["bestETF"] = port_repl.get("bestETF")
        alt_etfs = port_repl.get("alternateETFs", [])
        summary["alternateETFs"] = alt_etfs[:5]

    # Optimization strategies — keep metrics + trade trigger, drop full allocation arrays
    opt = result.get("optimizationSuggestions", [])
    summary["strategies"] = [
        {
            "type": s.get("type"),
            "title": s.get("title"),
            "score": s.get("score"),
            "metrics": s.get("metrics"),
            "tradeTrigger": s.get("tradeTrigger"),
        }
        for s in opt
    ]

    # Institutional analysis summary
    ia = result.get("institutionalAnalysis", {})
    if ia.get("available"):
        ia_summary: dict = {
            "tradeTrigger": ia.get("tradeTrigger"),
            "trackingErrorConstraint": ia.get("trackingErrorConstraint"),
        }
        fd = ia.get("factorDrift", {})
        if fd:
            ia_summary["factorDrift"] = {
                "aggregateDrift": fd.get("aggregateDrift"),
                "driftRating": fd.get("driftRating"),
                "capmBeta": fd.get("capmBeta"),
            }
        se = ia.get("sectorExposure")
        if se:
            ia_summary["sectorExposure"] = se
        summary["institutionalAnalysis"] = ia_summary

    return summary


async def _tool_run_tlh_analysis(
    holdings: list[dict],
    tax_rate_pct: float = 15.0,
    preferred_etf: str | None = None,
    custom_stocks: list[str] = None,
) -> str:
    """Run full TLH analysis — delegates to the TLH service."""
    try:
        from .tax_loss_harvesting_service import run_portfolio_tax_loss_harvesting

        result = await run_portfolio_tax_loss_harvesting(
            holdings=holdings,
            tax_rate_pct=tax_rate_pct,
            preferred_etf=preferred_etf,
            custom_stocks=custom_stocks,
        )
        # Summarize for token efficiency
        summarized = _summarize_tlh_result(result)
        return json.dumps(summarized, default=str)
    except Exception as e:
        return json.dumps({"error": f"TLH analysis failed: {e}"})


async def _tool_get_sector_fundamentals(tickers: list[str]) -> str:
    """Fetch key fundamentals for multiple tickers."""
    try:
        results = []
        for t in tickers[:20]:  # cap at 20 to avoid timeout
            try:
                info = yf.Ticker(t).info
                results.append({
                    "ticker": t,
                    "sector": info.get("sector"),
                    "industry": info.get("industry"),
                    "marketCap": info.get("marketCap"),
                    "pe": info.get("trailingPE"),
                    "forwardPe": info.get("forwardPE"),
                    "pegRatio": info.get("pegRatio"),
                    "dividendYield": round(info.get("dividendYield", 0) * 100, 2) if info.get("dividendYield") else None,
                    "beta": info.get("beta"),
                    "52wHigh": info.get("fiftyTwoWeekHigh"),
                    "52wLow": info.get("fiftyTwoWeekLow"),
                    "currentPrice": info.get("currentPrice") or info.get("regularMarketPrice"),
                })
            except Exception:
                results.append({"ticker": t, "error": "Failed to fetch data"})

        return json.dumps(results, default=str)
    except Exception as e:
        return json.dumps({"error": f"Sector fundamentals failed: {e}"})


async def _tool_get_tax_lot_info(holdings: list[dict]) -> str:
    """Compute tax-relevant info for holdings: unrealized P&L, holding period, wash sale dates."""
    try:
        tickers = list(set(h["ticker"].upper() for h in holdings))
        prices = await fetch_current_prices(tickers)
        today = datetime.date.today()

        results = []
        for h in holdings:
            ticker = h["ticker"].upper()
            shares = float(h["shares"])
            cost_basis = float(h["cost_basis"])
            purchase_date_str = h.get("purchase_date")

            current_price = prices.get(ticker)
            total_cost = shares * cost_basis
            market_value = shares * current_price if current_price else None
            unrealized_pnl = round(market_value - total_cost, 2) if market_value else None
            pnl_pct = round((market_value - total_cost) / total_cost * 100, 2) if market_value and total_cost > 0 else None

            # Holding period and tax classification
            if purchase_date_str:
                purchase_date = datetime.date.fromisoformat(purchase_date_str)
                days_held = (today - purchase_date).days
                tax_class = "long-term" if days_held > 365 else "short-term"
                # Wash sale window: 30 days before and after sale
                wash_sale_window_end = str(purchase_date + datetime.timedelta(days=30))
            else:
                days_held = None
                tax_class = "unknown"
                wash_sale_window_end = None

            results.append({
                "ticker": ticker,
                "shares": shares,
                "costBasis": round(cost_basis, 2),
                "currentPrice": round(current_price, 2) if current_price else None,
                "marketValue": round(market_value, 2) if market_value else None,
                "unrealizedPnl": unrealized_pnl,
                "pnlPct": pnl_pct,
                "daysHeld": days_held,
                "taxClassification": tax_class,
                "washSaleWindowEnd": wash_sale_window_end,
            })

        return json.dumps(results, default=str)
    except Exception as e:
        return json.dumps({"error": f"Tax lot info failed: {e}"})


# ---------------------------------------------------------------------------
# Email notification helper
# ---------------------------------------------------------------------------

async def _maybe_send_email(db: AsyncSession, agent: Agent, run: AgentRun):
    """Send email notification if agent has it enabled and run has output."""
    try:
        from .email_service import send_agent_report_email

        if not agent.send_email_on_run or not run.output:
            return
            
        target_email = agent.email_report_to
        if not target_email:
            logger.warning(f"Agent {agent.id} has send_email_on_run=True but no email_report_to configured.")
            return

        await send_agent_report_email(
            to_email=target_email,
            agent_name=agent.name,
            run_output_markdown=run.output,
            run_id=run.id,
        )
    except Exception as email_err:
        logger.warning(f"Email notification failed for agent {agent.id}: {email_err}")

# ---------------------------------------------------------------------------
# Main Execution Pipeline
# ---------------------------------------------------------------------------

async def execute_agent(
    agent_id: int,
    db: AsyncSession,
    openai_api_key: str,
    search_api_key: str | None = None,
    run_id: int | None = None,
    model: str = "gpt-4o-mini",
) -> AgentRun:
    """Execute an agent and return the AgentRun record.

    If *run_id* is provided the existing AgentRun row is reused (created by
    the router); otherwise a new row is created.
    """
    # Load agent
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise ValueError(f"Agent {agent_id} not found")

    now = datetime.datetime.now(datetime.timezone.utc)

    if run_id is not None:
        # Reuse existing run record
        run_result = await db.execute(select(AgentRun).where(AgentRun.id == run_id))
        run = run_result.scalar_one_or_none()
        if run is None:
            raise ValueError(f"AgentRun {run_id} not found")
        run.status = "running"
        run.started_at = now
    else:
        # Create new run record
        run = AgentRun(agent_id=agent_id, status="running", started_at=now)
        db.add(run)

    agent.last_run_at = now
    await db.commit()
    await db.refresh(run)

    try:
        # Build initial context
        portfolio_context = await _tool_get_portfolio(agent.user_id, db)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"**Agent Instruction:** {agent.instruction}\n\n"
                    f"**Current Portfolio Data:**\n```json\n{portfolio_context}\n```\n\n"
                    f"Please execute this task. Use the available tools to gather any additional data you need, "
                    f"then provide a comprehensive, well-formatted Markdown report."
                ),
            },
        ]

        # Multi-turn tool calling loop
        for round_num in range(MAX_TOOL_ROUNDS):
            response = await call_llm_with_tools(
                api_key=openai_api_key,
                model=model,
                messages=messages,
                tools=AGENT_TOOLS,
                max_tokens=4096,
            )

            # Check if LLM wants to call tools
            tool_calls = response.get("tool_calls")
            if not tool_calls:
                # Final response — LLM is done
                final_content = response.get("content", "")
                run.output = final_content
                run.status = "completed"
                run.completed_at = datetime.datetime.now(datetime.timezone.utc)
                await db.commit()
                await _maybe_send_email(db, agent, run)
                return run

            # Append assistant message with tool calls
            messages.append(response)

            # Execute each tool call
            for tc in tool_calls:
                func_name = tc["function"]["name"]
                func_args = json.loads(tc["function"]["arguments"])
                logger.info(f"Agent {agent_id} calling tool: {func_name}({func_args})")

                tool_result = await _execute_tool(
                    tool_name=func_name,
                    tool_args=func_args,
                    user_id=agent.user_id,
                    db=db,
                    search_api_key=search_api_key,
                )

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result,
                })

        # If we exhausted all rounds, ask LLM for final output without tools
        messages.append({
            "role": "user",
            "content": "Please provide your final analysis now based on all the data gathered.",
        })
        from .llm_service import call_llm
        final_content = await call_llm(
            api_key=openai_api_key,
            model=model,
            messages=messages,
            max_tokens=4096,
        )
        run.output = final_content
        run.status = "completed"
        run.completed_at = datetime.datetime.now(datetime.timezone.utc)
        await db.commit()
        await _maybe_send_email(db, agent, run)
        return run

    except Exception as e:
        logger.exception(f"Agent {agent_id} execution failed")
        run.status = "failed"
        run.error = str(e)
        run.completed_at = datetime.datetime.now(datetime.timezone.utc)
        await db.commit()
        return run
