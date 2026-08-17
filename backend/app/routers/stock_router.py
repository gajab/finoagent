"""Stock data router — ``/api/stock``."""

import datetime
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, get_premium_user, get_user_api_key
from ..database import get_db
from ..models import GuruAnalysis, StockAnalysis, SavedStrategy, User
from ..services.stock_service import fetch_stock_data, fetch_fund_details
from ..services.cache_service import get_cached, set_cached
from ..services.analysis_service import run_price_prediction
from ..services.dcf_service import run_dcf_analysis, compute_dcf_scenario, dcf_from_override
from ..services.financial_health_service import run_financial_health
from ..services.earnings_insight_service import generate_earnings_insight, get_cached_earnings_insight
from ..services.llm_service import call_llm
from ..services.debate_service import run_debate, continue_debate
from ..services.box_strategy_service import (
    run_box_strategy,
    compute_smart_prices,
    scan_box_opportunities,
    box_market_timing,
)
from ..services.hedging_service import (
    run_hedging_strategy, run_market_conditions_check, get_price_history_with_technicals,
)
from ..services.long_short_service import run_single_stock_long_short, run_pair_trade, run_pair_suggestions, run_130_30_portfolio
from ..services.structured_trades_service import run_structured_trade
from ..services.autocallable_service import run_autocallable
from ..services.dual_direction_service import run_dual_direction_buffer
from ..services.concentration_service import run_concentration_management
from ..services.pmcc_service import run_pmcc_pmcp
from ..services.zebra_service import run_zebra
from ..services.derivative_income_service import run_derivative_income, run_portfolio_derivative_income
from ..services.desk_review_service import rank_desk, run_desk_agents, evaluate_desk_trade, monitor_trade, monitor_analyze, blind_read
from ..services.cppi_service import run_cppi_simulation
from ..services.tax_loss_harvesting_service import run_portfolio_tax_loss_harvesting
from ..services.market_impact_service import analyze_market_impact
from ..services.thematic_impact_service import analyze_thematic_impact
from ..services.exit_analysis_service import run_exit_analysis
from ..services.rupee_service import get_rupee_metrics, format_rupee_context
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stock", tags=["stock"])


# =========================================================================
# Non-parametric routes MUST come before /{ticker} to avoid FastAPI conflicts
# =========================================================================

class SmartPriceIn(BaseModel):
    legs: list[dict] = Field(..., description="Option legs with bid/ask/last/oi/vol")
    dte: int = Field(default=30, description="Days to expiration")
    box_width: float = Field(default=0, description="Box width (K2 - K1)")
    intent: str = Field(default="lend", description="'lend' or 'borrow'")


@router.post("/strategies/smart-price")
async def get_smart_price(
    body: SmartPriceIn,
    user: User = Depends(get_current_user),
):
    """Compute AI-recommended fill prices for a multi-leg strategy.

    Analyzes bid-ask spreads, volume, OI, and last trade position to
    suggest prices most likely to execute.
    """
    try:
        result = compute_smart_prices(body.legs)
        # Add annualized return calculation if box_width provided
        if body.box_width > 0 and body.dte > 0:
            net = result["total_net_per_contract"]
            if body.intent == "lend" and net > 0:
                profit = body.box_width - net
                ann_return = (profit / net) * (365 / body.dte) * 100
                result["annualized_return_pct"] = round(ann_return, 2)
            elif body.intent == "borrow" and net < 0:
                proceeds = abs(net)
                interest = body.box_width - proceeds
                ann_rate = (interest / proceeds) * (365 / body.dte) * 100
                result["annualized_rate_pct"] = round(ann_rate, 2)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Smart price failed: {exc}")


# =========================================================================
# Box-spread opportunity scanner (non-parametric, must be before /{ticker})
# =========================================================================

class BoxScanIn(BaseModel):
    intent: str = Field(..., description="'lend' to earn interest, 'borrow' to raise cash")
    duration_days: int = Field(..., ge=7, le=730, description="Target duration in days")
    target_annual_return: float = Field(..., description="Min annual return (lend) / max annual rate (borrow), %")
    amount: float = Field(default=10000, gt=0, description="Target capital to deploy in USD")
    tickers: list[str] | None = Field(default=None, description="Override the default scan universe")
    max_contracts: int = Field(default=1, ge=1, le=10, description="Cap on contracts per position (1 is cheapest)")
    per_ticker: int = Field(default=1, ge=1, le=2, description="Best opportunities to return per ticker")
    quote_source: str = Field(default="yfinance", description="Quote source: 'yfinance' or 'ibkr'")


@router.post("/strategies/box-spread/scan")
async def scan_box_spreads(
    body: BoxScanIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Sweep a universe of liquid option markets for the box spreads most likely
    to fill, ranked by fill probability at the executable (natural) price."""
    try:
        return await scan_box_opportunities(
            intent=body.intent,
            duration_days=body.duration_days,
            target_annual_return=body.target_annual_return,
            amount=body.amount,
            tickers=body.tickers,
            max_contracts=body.max_contracts,
            per_ticker=body.per_ticker,
            quote_source=body.quote_source,
            user=user,
            db=db,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Box scan failed: {exc}")


@router.get("/strategies/box-spread/market-timing")
async def get_box_market_timing(user: User = Depends(get_current_user)):
    """Is now a good time to send a box order? Mid-session fills cleanest."""
    return box_market_timing()


# =========================================================================
# Derivative Income — portfolio sweep (non-parametric, must be before /{ticker})
# =========================================================================

_DI_DEFAULT_STRUCTURES = ["covered_call", "cash_secured_put", "collar", "credit_spread",
                          "iron_condor", "jade_lizard", "calendar"]


class DerivativeIncomePortfolioIn(BaseModel):
    offset: int = Field(default=0, ge=0, description="Pagination offset into top holdings by value")
    limit: int = Field(default=10, ge=1, le=10, description="Holdings analyzed per page (≤10 to spare the quote API)")
    target_dte: int | None = Field(default=None, ge=1, le=365, description="Target days-to-expiry; blank = monthlies ≤45d")
    min_prob: float = Field(default=0.85, ge=0.5, le=0.99, description="Min probability of NOT being assigned")
    min_income: float = Field(default=20.0, ge=0, description="Min premium ($/contract) to surface")
    structures: list[str] = Field(default_factory=lambda: ["covered_call", "collar", "cash_secured_put"])
    quote_source: str = Field(default="yfinance", description="'yfinance' or 'ibkr'")


@router.post("/strategies/derivative-income/portfolio")
async def compute_derivative_income_portfolio(
    body: DerivativeIncomePortfolioIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Covered-call/collar income across the user's top-N holdings by market value,
    paginated to protect the quote provider (UI bumps offset for 'Next 10')."""
    try:
        result = await run_portfolio_derivative_income(
            offset=body.offset,
            limit=body.limit,
            target_dte=body.target_dte,
            min_prob=body.min_prob,
            min_income=body.min_income,
            structures=body.structures,
            quote_source=body.quote_source,
            user=user,
            db=db,
        )
        if result.get("error") and not result.get("results"):
            raise HTTPException(400, result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Derivative income (portfolio) failed: {exc}")


# =========================================================================
# Option expirations lookup (non-parametric, must be before /{ticker})
# =========================================================================

@router.get("/options-expirations/{symbol}")
async def get_option_expirations(
    symbol: str,
    user: User = Depends(get_current_user),
):
    """Return available option expiration dates for a ticker (yfinance)."""
    import yfinance as yf
    clean = symbol.strip().upper()
    if clean.startswith("."):
        clean = "^" + clean[1:]
    try:
        stock = yf.Ticker(clean)
        expirations = list(stock.options)
    except Exception:
        return {"expirations": []}
    return {"expirations": expirations}


# =========================================================================
# Parametric routes — /{ticker}/...
# =========================================================================

@router.get("/{ticker}")
async def get_stock_data(
    ticker: str,
    user: User = Depends(get_current_user),
):
    """Fetch comprehensive stock data for *ticker*."""
    # Handle index cases where user might type .XSP but yfinance expects ^XSP
    if ticker.startswith("."):
        ticker = "^" + ticker[1:]
        
    try:
        data = await fetch_stock_data(ticker)
        return data
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error fetching stock data: {exc}")


@router.get("/{ticker}/technical")
async def get_technical_for_timeframe(
    ticker: str,
    timeframe: str = Query(default="short_term"),
    user: User = Depends(get_current_user),
):
    """Recompute the price/volume/RSI/support-resistance block for the requested
    timeframe preset (day_trading, short_term, swing, medium_term, long_term).

    Momentum indicators (MACD, BB, SMA, EMA) are always daily-based and included
    so the panel renders fully on every switch.
    """
    import asyncio
    import yfinance as yf
    from ..services.stock_service import (
        compute_technical_block, compute_momentum_indicators, TECHNICAL_TIMEFRAMES,
    )

    if ticker.startswith("."):
        ticker = "^" + ticker[1:]
    if timeframe not in TECHNICAL_TIMEFRAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown timeframe '{timeframe}'. Valid: {list(TECHNICAL_TIMEFRAMES.keys())}",
        )

    def _build():
        stock = yf.Ticker(ticker.upper())
        block = compute_technical_block(stock, timeframe)
        block.update(compute_momentum_indicators(stock))
        return block

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _build)
        return {
            "ticker": ticker.upper(),
            "presets": [
                {"key": k, "label": v["label"]} for k, v in TECHNICAL_TIMEFRAMES.items()
            ],
            "technical": result,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Technical analysis failed: {exc}")


@router.get("/{ticker}/microstructure")
async def get_microstructure(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Multi-timeframe Volume-Profile hierarchy (Macro/Swing/Micro POC·VAH·VAL·LVN),
    naked/virgin POCs, and the Anchored-VWAP matrix (YTD / earnings / 52-wk high·low).

    Heavy (5 history fetches) so it is cached and computed off the event loop. The
    frontend loads it lazily when the user opens the Microstructure panel.
    """
    import asyncio
    import yfinance as yf
    from ..services.microstructure_service import compute_microstructure

    if ticker.startswith("."):
        ticker = "^" + ticker[1:]
    ticker = ticker.upper()

    cache_key = f"micro:{ticker}:v1"
    cached = await get_cached(db, cache_key)
    if cached is not None:
        return {"ticker": ticker, "microstructure": cached, "cached": True}

    def _build():
        return compute_microstructure(yf.Ticker(ticker))

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _build)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Microstructure analysis failed: {exc}")

    if not result:
        raise HTTPException(status_code=404, detail="No microstructure data available for this ticker.")

    await set_cached(db, cache_key, result, ttl_seconds=900)
    return {"ticker": ticker, "microstructure": result, "cached": False}


@router.get("/{ticker}/market-structure")
async def get_market_structure(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Multi-timeframe market structure (BOS/CHOCH across Daily/4H/1H via scipy pivots),
    liquidity pools (unswept BSL/SSL), unmitigated Order Blocks / FVGs, and cross-timeframe
    mitigation confluence. Cached + computed off the event loop; loaded lazily by the UI."""
    import asyncio
    import yfinance as yf
    from ..services.market_structure_service import compute_market_structure

    if ticker.startswith("."):
        ticker = "^" + ticker[1:]
    ticker = ticker.upper()

    cache_key = f"mstruct:{ticker}:v1"
    cached = await get_cached(db, cache_key)
    if cached is not None:
        return {"ticker": ticker, "market_structure": cached, "cached": True}

    def _build():
        return compute_market_structure(yf.Ticker(ticker))

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _build)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Market-structure analysis failed: {exc}")

    if not result:
        raise HTTPException(status_code=404, detail="No market-structure data available for this ticker.")

    await set_cached(db, cache_key, result, ttl_seconds=900)
    return {"ticker": ticker, "market_structure": result, "cached": False}


@router.get("/{ticker}/regime")
async def get_regime(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Market-regime classifier: Hurst exponent + Kaufman Efficiency Ratio on Daily/4H,
    and the 50-day VWAP z-score (statistical over-expansion). Cached + executor-run."""
    import asyncio
    import yfinance as yf
    from ..services.regime_service import compute_regime

    if ticker.startswith("."):
        ticker = "^" + ticker[1:]
    ticker = ticker.upper()

    cache_key = f"regime:{ticker}:v1"
    cached = await get_cached(db, cache_key)
    if cached is not None:
        return {"ticker": ticker, "regime": cached, "cached": True}

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: compute_regime(yf.Ticker(ticker)))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Regime analysis failed: {exc}")
    if not result:
        raise HTTPException(status_code=404, detail="No regime data available for this ticker.")

    await set_cached(db, cache_key, result, ttl_seconds=900)
    return {"ticker": ticker, "regime": result, "cached": False}


@router.get("/{ticker}/dealer-positioning")
async def get_dealer_positioning(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Dealer positioning overlay: Net GEX, gamma flip level, gamma walls, and the
    options-implied expected move (±1σ) for ~30d & ~45d. Cached + executor-run (slow:
    it pulls several option-chain expiries)."""
    import asyncio
    import yfinance as yf
    from ..services.dealer_positioning_service import compute_dealer_positioning

    if ticker.startswith("."):
        ticker = "^" + ticker[1:]
    ticker = ticker.upper()

    cache_key = f"dealergex:{ticker}:v1"
    cached = await get_cached(db, cache_key)
    if cached is not None:
        return {"ticker": ticker, "dealer_positioning": cached, "cached": True}

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: compute_dealer_positioning(yf.Ticker(ticker)))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Dealer-positioning analysis failed: {exc}")
    if not result:
        raise HTTPException(status_code=404, detail="No option-chain data available for this ticker.")

    await set_cached(db, cache_key, result, ttl_seconds=900)
    return {"ticker": ticker, "dealer_positioning": result, "cached": False}


@router.get("/{ticker}/trade-setups")
async def get_trade_setups(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The Trade-Setup engine: fuses microstructure + market structure + regime + dealer
    positioning into ranked, concrete setups (entry / stop / target / R:R, sized to the
    expected move, filtered by regime). Cached + executor-run (fans out to all four)."""
    import asyncio
    import yfinance as yf
    from ..services.trade_setup_service import compute_trade_setups

    if ticker.startswith("."):
        ticker = "^" + ticker[1:]
    ticker = ticker.upper()

    cache_key = f"setups:{ticker}:v1"
    cached = await get_cached(db, cache_key)
    if cached is not None:
        return {"ticker": ticker, "trade_setups": cached, "cached": True}

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: compute_trade_setups(yf.Ticker(ticker)))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Trade-setup analysis failed: {exc}")
    if not result:
        raise HTTPException(status_code=404, detail="No trade-setup data available for this ticker.")

    await set_cached(db, cache_key, result, ttl_seconds=900)
    return {"ticker": ticker, "trade_setups": result, "cached": False}


@router.get("/{ticker}/chart-patterns")
async def get_chart_patterns(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Classical chart-pattern recognition (double top/bottom, H&S, triangles, flags, cup &
    handle, VCP, wedges + Fibonacci) with drawable geometry, measured-move targets and an
    education block per pattern. Cached + executor-run."""
    import asyncio
    import yfinance as yf
    from ..services.chart_pattern_service import compute_chart_patterns

    if ticker.startswith("."):
        ticker = "^" + ticker[1:]
    ticker = ticker.upper()

    cache_key = f"patterns:{ticker}:v1"
    cached = await get_cached(db, cache_key)
    if cached is not None:
        return {"ticker": ticker, "chart_patterns": cached, "cached": True}

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: compute_chart_patterns(yf.Ticker(ticker)))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Chart-pattern analysis failed: {exc}")
    if not result:
        raise HTTPException(status_code=404, detail="No chart-pattern data available for this ticker.")

    await set_cached(db, cache_key, result, ttl_seconds=900)
    return {"ticker": ticker, "chart_patterns": result, "cached": False}


@router.get("/{ticker}/chart-patterns/image")
async def get_chart_pattern_image(
    ticker: str,
    pattern: str = Query(..., description="Pattern type slug (e.g. 'cup_handle', 'double_top')"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Render one detected pattern as an annotated candlestick PNG (mplfinance) for download/share."""
    import asyncio
    import yfinance as yf
    from fastapi.responses import Response
    from ..services.chart_pattern_service import compute_chart_patterns, render_pattern_png

    if ticker.startswith("."):
        ticker = "^" + ticker[1:]
    ticker = ticker.upper()

    cache_key = f"patterns:{ticker}:v1"
    data = await get_cached(db, cache_key)
    if data is None:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: compute_chart_patterns(yf.Ticker(ticker)))
        if data:
            await set_cached(db, cache_key, data, ttl_seconds=900)
    if not data:
        raise HTTPException(status_code=404, detail="No chart-pattern data available.")

    match = next((p for p in (data.get("patterns") or []) if p.get("type") == pattern), None)
    if not match:
        raise HTTPException(status_code=404, detail=f"Pattern '{pattern}' not found for {ticker}.")

    loop = asyncio.get_event_loop()
    png = await loop.run_in_executor(None, lambda: render_pattern_png(data.get("series") or {}, match, ticker))
    if not png:
        raise HTTPException(status_code=500, detail="Could not render the pattern image.")
    return Response(content=png, media_type="image/png",
                    headers={"Content-Disposition": f'inline; filename="{ticker}_{pattern}.png"'})


class MicroChatMessage(BaseModel):
    role: str
    content: str


class TaAnalyzeIn(BaseModel):
    selection: dict = Field(default_factory=dict, description="JSON of the user-selected TA indicators")
    messages: list[MicroChatMessage] = Field(default_factory=list, description="Prior chat turns (client-held)")


_TA_ANALYZE_SYSTEM = """You are an elite technical strategist and derivatives desk head. You read \
markets like an institution — not trendlines, but WHERE volume traded, the AUCTION levels price is \
drawn back to, the footprints of large orders, and market STRUCTURE across timeframes. You are given \
ONLY the indicators the user selected, as JSON. They may come from the Volume-Profile / Microstructure \
map, the Market-Structure map, or both — work with whatever is present.

Concept key (only use what appears in the JSON):
- POC / VAH / VAL — most-traded price and the 70% Value Area. Above VAH reads "expensive" (rich to \
sell calls); below VAL reads "cheap" (rich to sell puts); inside = balanced/range.
- LVN — a thin price between volume shelves; price travels through it fast — a breakout accelerant and \
a spot for TIGHT stops. Naked/Virgin POC — a prior POC not retested; a magnet / target.
- AVWAP — volume-weighted price since a catalyst (YTD/earnings/52-wk high-low); above = buyers in \
control since that event, a trend filter.
- BOS (Break of Structure) = trend CONTINUATION; CHOCH (Change of Character) = the first counter-trend \
break, an EARLY-REVERSAL warning. Read them per timeframe and note multi-timeframe alignment.
- Liquidity Pools — BSL (buy-side, above swing highs) and SSL (sell-side, below swing lows) are resting \
stops; price is often drawn to SWEEP them before reversing. "Equal highs/lows" = engineered liquidity \
(stronger draw). Order Blocks / Fair-Value Gaps that are UNMITIGATED are unfinished business price tends \
to revisit; a confluence of unmitigated zones across timeframes is a high-probability reaction area.
- Regime — Hurst>0.5 / high Efficiency-Ratio = TRENDING (favor momentum breakouts, trend-following, \
directional vertical/debit spreads); Hurst<0.5 / low ER = MEAN-REVERTING (favor fading extremes, range \
trades, iron condors/strangles). The 50-day VWAP z-score flags statistical over-expansion (|z|≥2 = stretched). \
Pick structures that FIT the regime — don't sell condors in a trend or chase breakouts in a range.
- Dealer positioning — Net GEX>0 (dealers LONG gamma) = volatility suppressed, price pins/mean-reverts; \
Net GEX<0 (SHORT gamma) = volatility expansion, moves amplified. The GAMMA FLIP is the spot where this \
regime changes. Call/Put WALLS (largest +/− GEX strikes) act as resistance/support magnets. The EXPECTED \
MOVE (±1σ) is the options market's own range forecast — size targets/stops and condor wings around it.

Output plain English, grounded in the ACTUAL NUMBERS in the JSON (quote the price levels). Be specific \
and concise — short sections with bold headers, no filler, no hedging boilerplate. Cover:
1. **Read** — where spot sits vs the selected levels/zones; the structural bias (trend vs the BOS/CHOCH \
and timeframe alignment) and any confluence. State bullish / bearish / balanced and why.
2. **Swing trade** — a concrete idea: entry zone (a named level/zone), invalidation/STOP (an LVN, a swing, \
or below a demand zone), and target(s) (a liquidity pool / naked POC / opposite value-area edge). Give ~R:R.
3. **Directional option trade** — align to the bias (e.g. debit spread), strikes anchored to the levels, \
expiry suited to the timeframe of the structure.
4. **Premium-income trade** — sell puts near demand / SSL / VAL, calls near supply / BSL / VAH, or an iron \
condor bracketing the balance area; name the strikes from the levels.
5. **Entry timing & risk** — how to time around these levels (e.g. wait for a liquidity sweep + CHOCH), \
where stops go, which pool/POC is the magnet, and a note on sizing.
End with one line: "Educational analysis, not financial advice." If a section isn't supported by the \
selected indicators, say what to add rather than inventing levels."""


@router.post("/{ticker}/ta/analyze")
async def analyze_ta(
    ticker: str,
    body: TaAnalyzeIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send the user's SELECTED technical indicators (from any TA panel, as JSON) to the
    LLM for a plain-English read + concrete trade ideas, with follow-up questions. Stateless:
    the client holds the transcript and re-posts ``messages`` each turn."""
    ticker = ticker.upper()

    openai_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not openai_key:
        raise HTTPException(400, "OpenAI API key not configured. Please add it in Settings.")
    model = (await get_user_api_key(db, user.id, "openai_model")) or "gpt-4o-mini"

    if not body.selection:
        raise HTTPException(400, "Select at least one indicator to analyze.")

    selection_json = json.dumps(body.selection, indent=2, default=str)
    system_prompt = (
        f"{_TA_ANALYZE_SYSTEM}\n\nTicker: {ticker}\n"
        f"Selected indicators (JSON):\n```json\n{selection_json}\n```"
    )

    convo = [
        {"role": m.role, "content": m.content}
        for m in body.messages[-20:]
        if m.role in ("user", "assistant") and m.content.strip()
    ]
    if not convo:
        convo = [{"role": "user",
                  "content": "Analyze the selected indicators and tell me what trades I could confidently make."}]

    messages = [{"role": "system", "content": system_prompt}, *convo]
    try:
        answer = await call_llm(api_key=openai_key, model=model, messages=messages, max_tokens=1600)
    except Exception as exc:
        raise HTTPException(502, f"TA analysis failed: {exc}")

    return {"role": "assistant", "content": answer}


class VerifySetupIn(BaseModel):
    setup: dict = Field(..., description="The quant-generated setup (entry/stop/targets/equity_plan/options_plan)")
    dossier: dict = Field(default_factory=dict, description="The full TA dossier (all indicators + advanced metrics)")
    enrichments: dict = Field(default_factory=dict, description="Toggle flags {sentiment, fundamental, analyst}: bool")


def _gather_verify_enrichments(ticker: str, want: set) -> dict:
    """Cheap raw enrichment data (headlines / key fundamentals / analyst ratings) for the
    dimensions the user toggled — the verify-LLM reasons over these, no sub-LLM calls."""
    import yfinance as yf
    out: dict = {}
    try:
        stock = yf.Ticker(ticker)
    except Exception:  # noqa: BLE001
        return out
    if "sentiment" in want:
        try:
            news = stock.news or []
            heads = []
            for n in news[:10]:
                c = n.get("content") if isinstance(n.get("content"), dict) else None
                title = n.get("title") or (c or {}).get("title")
                pub = n.get("publisher") or ((c or {}).get("provider") or {}).get("displayName")
                if title:
                    heads.append({"title": title, "publisher": pub})
            if heads:
                out["sentiment"] = {"recent_headlines": heads}
        except Exception:  # noqa: BLE001
            pass
    info = {}
    if want & {"fundamental", "analyst"}:
        try:
            info = stock.info or {}
        except Exception:  # noqa: BLE001
            info = {}
    if "fundamental" in want and info:
        keys = ["trailingPE", "forwardPE", "priceToBook", "profitMargins", "grossMargins", "revenueGrowth",
                "earningsGrowth", "debtToEquity", "returnOnEquity", "freeCashflow", "marketCap", "beta"]
        fund = {k: info.get(k) for k in keys if info.get(k) is not None}
        if fund:
            out["fundamental"] = fund
    if "analyst" in want and info:
        an = {k: info.get(k) for k in ("recommendationKey", "recommendationMean", "targetMeanPrice",
                                       "targetHighPrice", "targetLowPrice", "numberOfAnalystOpinions") if info.get(k) is not None}
        try:
            rec = stock.recommendations
            if rec is not None and not rec.empty:
                an["recent"] = [{"firm": r.get("Firm"), "grade": r.get("To Grade"), "action": r.get("Action")}
                                for _, r in rec.tail(6).iterrows()]
        except Exception:  # noqa: BLE001
            pass
        if an:
            out["analyst"] = an
    return out


_VERIFY_SYSTEM = """You are the CHIEF RISK OFFICER and head of trade execution on an elite desk. A quant \
engine has produced a candidate trade from a full technical dossier (multi-timeframe volume profile, market \
structure/liquidity, regime, dealer gamma, and the classic indicators). Your job is a rigorous, sceptical \
PRE-TRADE REVIEW — protect capital first, then improve the trade.

Work ONLY from the JSON provided (quote the actual numbers). Be specific and decisive; no filler. Do:
1. VERIFY — does the thesis hold up across ALL the evidence, or do signals conflict? Decide take / adjust / pass.
2. P&L SANITY — check the stated risk:reward, the options payoff (net cost, max profit/loss, breakevens) and the \
share sizing for internal consistency and reasonableness; flag anything wrong or unattractive (e.g. a debit \
spread risking more than it can make).
3. ALTERNATE — propose ONE better or complementary trade (equity OR options) with concrete entry/stop/target or \
strikes+expiry, and why it's better.
4. RISKS — the top 3 risks / what invalidates the trade.
5. ENRICHMENTS — if a sentiment / fundamental / analyst block is present, factor it explicitly; if absent, ignore \
that dimension (do NOT invent it).

Return ONLY valid JSON, no prose outside it:
{"verdict":"take|adjust|pass","confidence":"high|medium|low","summary":"one or two sentences",
 "verification":["evidence-grounded points, cite numbers"],
 "pnl_check":"assessment of the risk:reward / payoff / sizing",
 "adjustments":["concrete changes if verdict=adjust, else []"],
 "alternate":{"name":"","kind":"equity|options","direction":"long|short|neutral","entry":"","stop":"","target":"","structure":"","why":""},
 "risks":["..."],
 "enrichment_read":{"sentiment":"","fundamental":"","analyst":""}}"""


@router.post("/{ticker}/verify-setup")
async def verify_setup(
    ticker: str,
    body: VerifySetupIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a quant setup + the full TA dossier (+ optional sentiment/fundamental/analyst
    blocks the user toggled) to the LLM for a rigorous pre-trade review: verify the trade,
    sanity-check the P&L, propose an alternate, and list the risks. Returns structured JSON."""
    ticker = ticker.upper()
    openai_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not openai_key:
        raise HTTPException(400, "OpenAI API key not configured. Please add it in Settings.")
    model = (await get_user_api_key(db, user.id, "openai_model")) or "gpt-4o-mini"

    # gather only the enrichment dimensions the user toggled on (cheap yfinance fetches, off-loop)
    want = {k for k in ("sentiment", "fundamental", "analyst") if (body.enrichments or {}).get(k)}
    enrich = {}
    if want:
        import asyncio
        loop = asyncio.get_event_loop()
        enrich = await loop.run_in_executor(None, lambda: _gather_verify_enrichments(ticker, want))
    payload = {"ticker": ticker, "setup": body.setup, "dossier": body.dossier,
               "enrichments": enrich, "enrichments_selected": sorted(want)}
    user_msg = ("Review this quant trade and return the JSON verdict.\n```json\n"
                + json.dumps(payload, default=str)[:60000] + "\n```")

    try:
        answer = await call_llm(api_key=openai_key, model=model,
                                messages=[{"role": "system", "content": _VERIFY_SYSTEM},
                                          {"role": "user", "content": user_msg}],
                                max_tokens=2000, expect_json=True)
    except Exception as exc:
        raise HTTPException(502, f"Trade verification failed: {exc}")

    try:
        return {"verification": json.loads(answer), "enrichments_used": list(enrich.keys())}
    except Exception:
        return {"verification": {"raw": answer, "verdict": None}, "enrichments_used": list(enrich.keys())}


@router.get("/{ticker}/prediction")
async def get_price_prediction(
    ticker: str,
    horizon: int = Query(default=30, ge=1, le=365),
    user: User = Depends(get_current_user),
):
    """Run statistical price prediction for *ticker*."""
    try:
        result = await run_price_prediction(ticker, horizon_days=horizon)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Prediction failed: {exc}")


# =========================================================================
# DCF Analysis
# =========================================================================

class DCFScenarioIn(BaseModel):
    """Two-stage DCF scenario inputs (percentages as whole numbers, e.g. 10 = 10%)."""
    base_fcf: float = Field(..., description="Normalized starting free cash flow, in dollars")
    stage1_growth: float = Field(..., description="Constant FCF growth % during the CAP years")
    cap_years: int = Field(4, ge=1, le=15, description="Competitive-advantage period — years of stage-1 growth before the fade")
    discount_rate: float = Field(..., description="WACC / discount rate %")
    terminal_growth: float = Field(2.5, description="Perpetuity growth % after the fade")
    exit_multiple: float = Field(16.0, description="Terminal EV/FCF multiple (cross-checks Gordon growth)")
    shares_outstanding: int = Field(..., gt=0)
    net_debt: float = Field(0, description="Net debt (total debt minus cash). Positive = net debtor.")
    current_price: float = Field(0, description="Current price — used for the reverse-DCF implied growth")


@router.get("/{ticker}/dcf")
async def get_dcf_analysis(
    ticker: str,
    user: User = Depends(get_current_user),
):
    """Run DCF analysis with smart defaults based on company financials."""
    try:
        result = await run_dcf_analysis(ticker)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DCF analysis failed: {exc}")


@router.post("/{ticker}/dcf/scenario")
async def compute_dcf(
    ticker: str,
    body: DCFScenarioIn,
    user: User = Depends(get_current_user),
):
    """Compute a user-customized TWO-STAGE DCF scenario (same engine as the headline)."""
    try:
        return dcf_from_override(body.model_dump())["valuation"]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DCF scenario failed: {exc}")


# =========================================================================
# Guru Analysis — LLM-powered investing philosophy takes
# =========================================================================

GURUS = [
    {
        "id": "warren_buffett",
        "name": "Warren Buffett",
        "title": "Chairman & CEO, Berkshire Hathaway",
        "philosophy": "Value investing, wide economic moats, long-term compounding, margin of safety. Focuses on businesses with durable competitive advantages, predictable earnings, and excellent management. Avoids speculation and prefers companies he understands deeply.",
        "avatar_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/51/Warren_Buffett_KU_Visit.jpg/220px-Warren_Buffett_KU_Visit.jpg",
    },
    {
        "id": "peter_lynch",
        "name": "Peter Lynch",
        "title": "Former Manager, Fidelity Magellan Fund",
        "philosophy": "Growth at a reasonable price (GARP). Look for 'tenbaggers' — stocks that can multiply 10x. Invest in what you know, analyze PEG ratios, and find companies with strong earnings growth that the market hasn't fully recognized yet.",
        "avatar_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Peter_Lynch_-_1%2C_bAnswer.png/220px-Peter_Lynch_-_1%2C_bAnswer.png",
    },
    {
        "id": "george_soros",
        "name": "George Soros",
        "title": "Founder, Soros Fund Management",
        "philosophy": "Reflexivity theory and macro investing. Markets are inherently unstable and driven by participant biases. Looks for situations where market perceptions diverge from reality. Willing to make large, concentrated bets on macro trends and currency/geopolitical shifts.",
        "avatar_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/5/50/George_Soros_-_World_Economic_Forum_Annual_Meeting_2011.jpg/220px-George_Soros_-_World_Economic_Forum_Annual_Meeting_2011.jpg",
    },
    {
        "id": "jack_bogle",
        "name": "John 'Jack' Bogle",
        "title": "Founder, Vanguard Group",
        "philosophy": "Passive indexing and low-cost investing. The market is very hard to beat consistently. Minimize fees, diversify broadly, think long-term, and avoid market timing. Questions whether any individual stock justifies a concentrated position.",
        "avatar_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a1/JohnCBowordle.jpg/220px-JohnCBowordle.jpg",
    },
    {
        "id": "ray_dalio",
        "name": "Ray Dalio",
        "title": "Founder, Bridgewater Associates",
        "philosophy": "Principles-based macro investing and risk parity. Analyzes economic machine cycles (credit, debt, productivity). Diversifies across uncorrelated assets and focuses on understanding where we are in the long-term and short-term debt cycles.",
        "avatar_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c4/Ray_Dalio_2017.jpg/220px-Ray_Dalio_2017.jpg",
    },
    {
        "id": "charlie_munger",
        "name": "Charlie Munger",
        "title": "Vice Chairman, Berkshire Hathaway",
        "philosophy": "Mental models and multi-disciplinary thinking. Invert problems — think about what could go wrong. Demands an extremely high quality bar: wonderful company at a fair price beats a fair company at a wonderful price. Emphasizes moats, management integrity, and avoiding stupidity over seeking brilliance.",
        "avatar_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1b/Charlie_Munger_%28crop%29.jpg/220px-Charlie_Munger_%28crop%29.jpg",
    },
]


class GuruOut(BaseModel):
    id: str
    guru_id: str
    guru_name: str
    analysis: str
    created_at: datetime.datetime


@router.get("/{ticker}/gurus")
async def get_guru_analyses(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get stored guru analyses for a ticker, plus guru metadata."""
    ticker = ticker.upper()

    # Fetch existing analyses
    result = await db.execute(
        select(GuruAnalysis).where(
            GuruAnalysis.user_id == user.id,
            GuruAnalysis.ticker == ticker,
        ).order_by(GuruAnalysis.created_at.desc())
    )
    analyses = result.scalars().all()

    # Build a map of guru_id -> analysis
    analysis_map = {}
    for a in analyses:
        if a.guru_id not in analysis_map:  # Take the latest for each guru
            analysis_map[a.guru_id] = {
                "id": a.id,
                "guru_id": a.guru_id,
                "analysis": a.analysis,
                "created_at": a.created_at.isoformat(),
            }

    return {
        "ticker": ticker,
        "gurus": GURUS,
        "analyses": analysis_map,
    }


@router.post("/{ticker}/gurus/{guru_id}/refresh")
async def refresh_guru_analysis(
    ticker: str,
    guru_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate (or refresh) a guru's analysis for a ticker via LLM."""
    ticker = ticker.upper()

    # Validate guru
    guru = next((g for g in GURUS if g["id"] == guru_id), None)
    if not guru:
        raise HTTPException(404, "Unknown guru")

    # Get OpenAI key
    openai_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not openai_key:
        raise HTTPException(400, "OpenAI API key not configured. Please add it in Settings.")
    model_pref = await get_user_api_key(db, user.id, "openai_model")
    model = model_pref or "gpt-4o-mini"

    # Fetch stock data for context
    try:
        stock_data = await fetch_stock_data(ticker)
    except Exception:
        stock_data = {}

    # Build rich context
    context_parts = []
    if stock_data:
        context_parts.append(f"Current Price: ${stock_data.get('price', 'N/A')}")
        context_parts.append(f"Market Cap: {stock_data.get('marketCap', 'N/A')}")
        context_parts.append(f"Sector: {stock_data.get('sector', 'N/A')}")
        context_parts.append(f"Industry: {stock_data.get('industry', 'N/A')}")
        context_parts.append(f"Change: {stock_data.get('changePercent', 'N/A')}%")

        earn = stock_data.get("earnings", {})
        if earn.get("available"):
            context_parts.append(f"Trailing P/E: {earn.get('trailingPE', 'N/A')}")
            context_parts.append(f"Forward P/E: {earn.get('forwardPE', 'N/A')}")
            context_parts.append(f"PEG Ratio: {earn.get('pegRatio', 'N/A')}")
            context_parts.append(f"Latest EPS: {earn.get('reportedEPS', 'N/A')}")
            context_parts.append(f"EPS Surprise: {earn.get('epsSurprisePct', 'N/A')}%")
            context_parts.append(f"Revenue: {earn.get('revenueFormatted', 'N/A')}")
            context_parts.append(f"Net Income: {earn.get('netIncomeFormatted', 'N/A')}")

        fin = stock_data.get("financials", {})
        if fin:
            context_parts.append(f"EPS History: {fin.get('eps', [])}")
            context_parts.append(f"Revenue History: {fin.get('revenue', [])}")
            context_parts.append(f"FCF History: {fin.get('freeCashFlow', [])}")

        opts = stock_data.get("options", {})
        if opts.get("available"):
            iv = opts.get("iv", {})
            context_parts.append(f"IV: {iv.get('current', 'N/A')}%")
            context_parts.append(f"Put/Call Ratio: {opts.get('putCallRatio', {}).get('openInterest', 'N/A')}")

        tech = stock_data.get("technical", {})
        if tech:
            context_parts.append(f"RSI: {tech.get('currentRSI', 'N/A')} ({tech.get('rsiSignal', '')})")
            context_parts.append(f"Analysis: {tech.get('analysisSummary', '')}")

        ns = stock_data.get("newsSummary")
        if isinstance(ns, dict):
            context_parts.append(f"News Sentiment: {ns.get('sentiment', 'N/A')}")
        elif isinstance(ns, str):
            context_parts.append(f"News Summary: {ns[:300]}")

    stock_context = "\n".join(context_parts)

    # Build prompt
    system_prompt = f"""You are {guru['name']}, {guru['title']}.

IMPORTANT: You must respond ENTIRELY in character as {guru['name']}. Write in first person.
Use {guru['name']}'s known speaking style, favorite phrases, and analytical frameworks.

Your investing philosophy: {guru['philosophy']}

You are analyzing {ticker} ({stock_data.get('companyName', ticker) if stock_data else ticker}).

Here is the current data for this company:
{stock_context}

INSTRUCTIONS:
- Provide YOUR personal take on this stock, staying completely in character.
- Start with your overall verdict (Buy, Hold, or Avoid) and a confidence level.
- Explain WHY using your specific investing framework and philosophy.
- Reference specific numbers from the data above.
- Discuss what you like and what concerns you.
- Compare to your ideal investment criteria.
- Keep it concise but insightful — around 200-300 words.
- Use "I" and speak as if you're directly advising the reader.
- Do NOT use markdown headers — write in flowing paragraphs with emphasis where needed.
- End with a memorable one-liner or piece of wisdom that's characteristic of you."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"What's your take on {ticker} as an investment right now?"},
    ]

    try:
        answer = await call_llm(api_key=openai_key, model=model, messages=messages, max_tokens=800)
    except Exception as exc:
        raise HTTPException(502, f"LLM request failed: {exc}")

    # Delete old analysis for this guru+ticker+user and save new one
    old_result = await db.execute(
        select(GuruAnalysis).where(
            GuruAnalysis.user_id == user.id,
            GuruAnalysis.ticker == ticker,
            GuruAnalysis.guru_id == guru_id,
        )
    )
    for old in old_result.scalars().all():
        await db.delete(old)

    new_analysis = GuruAnalysis(
        user_id=user.id,
        ticker=ticker,
        guru_id=guru_id,
        guru_name=guru["name"],
        analysis=answer,
        created_at=datetime.datetime.now(datetime.timezone.utc),
    )
    db.add(new_analysis)
    await db.commit()
    await db.refresh(new_analysis)

    return {
        "id": new_analysis.id,
        "guru_id": guru_id,
        "guru_name": guru["name"],
        "analysis": answer,
        "created_at": new_analysis.created_at.isoformat(),
    }


@router.post("/{ticker}/gurus/refresh-all")
async def refresh_all_gurus(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Refresh all guru analyses at once."""
    ticker = ticker.upper()

    openai_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not openai_key:
        raise HTTPException(400, "OpenAI API key not configured. Please add it in Settings.")

    results = {}
    for guru in GURUS:
        try:
            # Call the individual refresh (reuses the same function logic)
            # To avoid code duplication, we make the call directly
            r = await refresh_guru_analysis(ticker, guru["id"], user, db)
            results[guru["id"]] = r
        except Exception as e:
            logger.warning(f"Failed to refresh guru {guru['id']} for {ticker}: {e}")
            results[guru["id"]] = {"error": str(e)}

    return {"ticker": ticker, "results": results}


# =========================================================================
# Financial Health
# =========================================================================

@router.get("/{ticker}/financial-health")
async def get_financial_health(
    ticker: str,
    user: User = Depends(get_current_user),
):
    """Comprehensive financial health metrics from yfinance data."""
    try:
        result = await run_financial_health(ticker)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Financial health analysis failed: {exc}")


# =========================================================================
# Earnings-report insight — EDGAR filing + LLM summary of the latest quarter
# =========================================================================

@router.get("/{ticker}/earnings-insights")
async def get_earnings_insights(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a previously generated earnings recap (cached), or nulls."""
    cached = await get_cached_earnings_insight(db, ticker.upper())
    if cached:
        return cached
    return {"ticker": ticker.upper(), "quarter": None, "summary": None, "sources": [], "generated_at": None}


@router.post("/{ticker}/earnings-insights/generate")
async def generate_earnings_insights(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Summarize the latest quarterly SEC filing (10-Q / earnings release) with the LLM."""
    openai_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not openai_key:
        raise HTTPException(400, "OpenAI API key not configured. Please add it in Settings.")
    model = (await get_user_api_key(db, user.id, "openai_model")) or "gpt-4o-mini"
    try:
        return await generate_earnings_insight(db, ticker.upper(), openai_key, model)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Earnings insight generation failed: {exc}")


# =========================================================================
# Qualitative Analysis — LLM-powered, on-demand
# =========================================================================

@router.get("/{ticker}/qualitative")
async def get_qualitative_analysis(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return stored qualitative analysis for the ticker (if any)."""
    ticker = ticker.upper()
    result = await db.execute(
        select(StockAnalysis).where(
            StockAnalysis.user_id == user.id,
            StockAnalysis.ticker == ticker,
            StockAnalysis.analysis_type == "qualitative",
        ).order_by(StockAnalysis.created_at.desc()).limit(1)
    )
    row = result.scalar_one_or_none()
    if row:
        return {
            "ticker": ticker,
            "analysis": row.analysis,
            "created_at": row.created_at.isoformat(),
        }
    return {"ticker": ticker, "analysis": None, "created_at": None}


@router.post("/{ticker}/qualitative/generate")
async def generate_qualitative_analysis(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate qualitative analysis using LLM with stock context."""
    ticker = ticker.upper()

    openai_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not openai_key:
        raise HTTPException(400, "OpenAI API key not configured. Please add it in Settings.")
    model_pref = await get_user_api_key(db, user.id, "openai_model")
    model = model_pref or "gpt-4o-mini"

    # Fetch stock data for context
    try:
        stock_data = await fetch_stock_data(ticker)
    except Exception:
        stock_data = {}

    # Build context
    context = _build_stock_context(ticker, stock_data)

    system_prompt = f"""You are a senior equity research analyst conducting a thorough qualitative analysis of {ticker} ({stock_data.get('companyName', ticker) if stock_data else ticker}).

Here is the current data for this company:
{context}

Provide a comprehensive qualitative analysis covering these key areas:

1. **Management Quality**: Assess leadership track record, capital allocation decisions, insider ownership patterns, and strategic vision. Note any recent leadership changes.

2. **Business Model Resilience**: Evaluate the durability of the business model. How well does it perform in different economic conditions? Is revenue recurring or one-time? What is the customer concentration risk?

3. **Competitive Advantages (Moat)**: Identify and assess competitive advantages — technology/IP, patents, brand strength, network effects, switching costs, cost advantages, distribution/scale advantages. Rate the moat's durability.

4. **Product Pipeline & Innovation**: Assess R&D effectiveness, upcoming product launches, technology roadmap, and ability to adapt to market changes. How well does the company innovate vs. competitors?

5. **Reputation & Stakeholder Perception**: Consider brand reputation, customer satisfaction, employee reviews (Glassdoor sentiment), ESG considerations, and any recent controversies or goodwill events.

FORMATTING REQUIREMENTS:
- Use markdown with clear headers for each section
- Be specific — reference actual data from the context above
- Provide a 1-5 star rating for each category
- End with an **Overall Qualitative Score** (1-5 stars) and a brief synthesis
- Keep the total analysis between 400-600 words
- Be balanced — highlight both strengths and risks"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Conduct a qualitative analysis of {ticker}."},
    ]

    try:
        answer = await call_llm(api_key=openai_key, model=model, messages=messages, max_tokens=1500)
    except Exception as exc:
        raise HTTPException(502, f"LLM request failed: {exc}")

    # Delete old and save new
    old_result = await db.execute(
        select(StockAnalysis).where(
            StockAnalysis.user_id == user.id,
            StockAnalysis.ticker == ticker,
            StockAnalysis.analysis_type == "qualitative",
        )
    )
    for old in old_result.scalars().all():
        await db.delete(old)

    new_row = StockAnalysis(
        user_id=user.id,
        ticker=ticker,
        analysis_type="qualitative",
        analysis=answer,
        created_at=datetime.datetime.now(datetime.timezone.utc),
    )
    db.add(new_row)
    await db.commit()
    await db.refresh(new_row)

    return {
        "ticker": ticker,
        "analysis": answer,
        "created_at": new_row.created_at.isoformat(),
    }


# =========================================================================
# Multi-Agent Debate — Bull vs. Bear vs. Judge → Black-Litterman view
# =========================================================================

@router.get("/{ticker}/debate")
async def get_agent_debate(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the most recent stored Bull/Bear/Judge debate for the ticker."""
    ticker = ticker.upper()
    result = await db.execute(
        select(StockAnalysis).where(
            StockAnalysis.user_id == user.id,
            StockAnalysis.ticker == ticker,
            StockAnalysis.analysis_type == "debate",
        ).order_by(StockAnalysis.created_at.desc()).limit(1)
    )
    row = result.scalar_one_or_none()
    if not row:
        return {"available": False, "ticker": ticker}
    try:
        payload = json.loads(row.analysis)
        payload["available"] = True
        return payload
    except Exception:
        return {"available": False, "ticker": ticker}


@router.post("/{ticker}/debate/generate")
async def generate_agent_debate(
    ticker: str,
    max_rounds: int = 4,
    confidence_target: float = 0.8,
    dcf: DCFScenarioIn | None = Body(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Run the iterative Bull↔Bear↔Judge debate live and store it.

    The debate loops for up to ``max_rounds`` (capped server-side) and stops early
    once the Judge hits ``confidence_target`` or the arguments stop adding signal.
    Each round is 3 sequential LLM calls, so this can take a while.
    """
    ticker = ticker.upper()

    openai_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not openai_key:
        raise HTTPException(400, "OpenAI API key not configured. Please add it in Settings.")
    model_pref = await get_user_api_key(db, user.id, "openai_model")
    model = model_pref or "gpt-4o-mini"

    try:
        payload = await run_debate(
            db, ticker, openai_key, model,
            max_rounds=max_rounds, confidence_target=confidence_target,
            dcf_override=dcf.model_dump() if dcf else None,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Debate generation failed: {exc}")

    # Persist as a NEW history entry — prior debates are kept (see /debate/history),
    # so "Re-run" archives the old debate instead of overwriting it.
    db.add(StockAnalysis(
        user_id=user.id, ticker=ticker, analysis_type="debate",
        analysis=json.dumps(payload),
        created_at=datetime.datetime.now(datetime.timezone.utc),
    ))
    await db.commit()

    payload["available"] = True
    return payload


class DebateContinueIn(BaseModel):
    user_input: str = Field(..., min_length=1, description="Analyst's answers to the open questions / extra data")


@router.post("/{ticker}/debate/continue")
async def continue_agent_debate(
    ticker: str,
    body: DebateContinueIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Run ONE more debate round on the stored debate, incorporating the user's
    answers to the open questions. Re-stores and returns the updated debate."""
    ticker = ticker.upper()

    openai_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not openai_key:
        raise HTTPException(400, "OpenAI API key not configured. Please add it in Settings.")
    model = (await get_user_api_key(db, user.id, "openai_model")) or "gpt-4o-mini"

    row = (await db.execute(
        select(StockAnalysis).where(
            StockAnalysis.user_id == user.id,
            StockAnalysis.ticker == ticker,
            StockAnalysis.analysis_type == "debate",
        ).order_by(StockAnalysis.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "No debate to continue — run one first.")
    try:
        prior = json.loads(row.analysis)
    except Exception:
        raise HTTPException(400, "Stored debate is unreadable.")

    try:
        payload = await continue_debate(db, ticker, openai_key, model, prior, body.user_input)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Debate continuation failed: {exc}")

    # Update the CURRENT debate in place (append the round) — history untouched.
    row.analysis = json.dumps(payload)
    row.created_at = datetime.datetime.now(datetime.timezone.utc)
    await db.commit()

    payload["available"] = True
    return payload


async def _latest_debate_row(db: AsyncSession, user_id: int, ticker: str):
    return (await db.execute(
        select(StockAnalysis).where(
            StockAnalysis.user_id == user_id,
            StockAnalysis.ticker == ticker,
            StockAnalysis.analysis_type == "debate",
        ).order_by(StockAnalysis.created_at.desc()).limit(1)
    )).scalar_one_or_none()


@router.post("/{ticker}/debate/save")
async def save_agent_debate(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark the current debate as saved (kept permanently)."""
    ticker = ticker.upper()
    row = await _latest_debate_row(db, user.id, ticker)
    if not row:
        raise HTTPException(404, "No debate to save.")
    try:
        payload = json.loads(row.analysis)
    except Exception:
        raise HTTPException(400, "Stored debate is unreadable.")
    payload["saved"] = True
    row.analysis = json.dumps(payload)
    await db.commit()
    payload["available"] = True
    return payload


@router.delete("/{ticker}/debate")
async def clear_agent_debate(
    ticker: str,
    all_history: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete the current debate — or the whole history with ?all_history=true."""
    ticker = ticker.upper()
    if all_history:
        rows = (await db.execute(
            select(StockAnalysis).where(
                StockAnalysis.user_id == user.id,
                StockAnalysis.ticker == ticker,
                StockAnalysis.analysis_type == "debate",
            )
        )).scalars().all()
        for r in rows:
            await db.delete(r)
    else:
        row = await _latest_debate_row(db, user.id, ticker)
        if row:
            await db.delete(row)
    await db.commit()
    return {"cleared": True}


@router.get("/{ticker}/debate/history")
async def debate_history(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Summaries of PAST debates (all but the current), newest first, for lazy display."""
    ticker = ticker.upper()
    rows = (await db.execute(
        select(StockAnalysis).where(
            StockAnalysis.user_id == user.id,
            StockAnalysis.ticker == ticker,
            StockAnalysis.analysis_type == "debate",
        ).order_by(StockAnalysis.created_at.desc())
    )).scalars().all()
    items = []
    for r in rows[1:]:   # skip the current (latest) — it's shown fully
        try:
            p = json.loads(r.analysis)
        except Exception:
            continue
        c = p.get("conclusion") or {}
        items.append({
            "id": r.id,
            "created_at": r.created_at.isoformat(),
            "saved": bool(p.get("saved")),
            "view_return": c.get("view_return"),
            "base_confidence": c.get("base_confidence"),
            "rounds": c.get("rounds"),
        })
    return {"items": items}


@router.get("/{ticker}/debate/item/{item_id}")
async def debate_item(
    ticker: str,
    item_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Full JSON of a specific past debate (lazy-loaded when its card is expanded)."""
    row = (await db.execute(
        select(StockAnalysis).where(
            StockAnalysis.id == item_id,
            StockAnalysis.user_id == user.id,
            StockAnalysis.analysis_type == "debate",
        )
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Debate not found.")
    try:
        payload = json.loads(row.analysis)
    except Exception:
        raise HTTPException(400, "Stored debate is unreadable.")
    payload["available"] = True
    return payload


# =========================================================================
# Industry & Macro Analysis — LLM-powered, on-demand
# =========================================================================

@router.get("/{ticker}/macro")
async def get_macro_analysis(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return stored industry/macro analysis for the ticker (if any)."""
    ticker = ticker.upper()
    result = await db.execute(
        select(StockAnalysis).where(
            StockAnalysis.user_id == user.id,
            StockAnalysis.ticker == ticker,
            StockAnalysis.analysis_type == "macro",
        ).order_by(StockAnalysis.created_at.desc()).limit(1)
    )
    row = result.scalar_one_or_none()
    if row:
        return {
            "ticker": ticker,
            "analysis": row.analysis,
            "created_at": row.created_at.isoformat(),
        }
    return {"ticker": ticker, "analysis": None, "created_at": None}


@router.post("/{ticker}/macro/generate")
async def generate_macro_analysis(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate industry & macro analysis using LLM with stock context."""
    ticker = ticker.upper()

    openai_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not openai_key:
        raise HTTPException(400, "OpenAI API key not configured. Please add it in Settings.")
    model_pref = await get_user_api_key(db, user.id, "openai_model")
    model = model_pref or "gpt-4o-mini"

    # Fetch stock data for context
    try:
        stock_data = await fetch_stock_data(ticker)
    except Exception:
        stock_data = {}

    # Build context
    context = _build_stock_context(ticker, stock_data)

    system_prompt = f"""You are a macro-economic and industry research analyst assessing external factors affecting {ticker} ({stock_data.get('companyName', ticker) if stock_data else ticker}).

Here is the current data for this company:
{context}

Provide a comprehensive industry and macro analysis covering these key areas:

1. **Sector Growth & Long-Term Demand Trends**: Analyze the overall sector trajectory. What are the key secular growth drivers? What is the total addressable market (TAM) outlook? Are there structural tailwinds or headwinds?

2. **Competitive Dynamics**: Assess market share trends, threat of new entrants, substitution risks, and pricing power. Who are the main competitors and how does this company position against them? Is the industry consolidating or fragmenting?

3. **Regulatory & Policy Changes**: Identify current and upcoming regulatory changes that could impact profitability — antitrust, environmental regulations, tax policy changes, trade policies, data privacy laws, or sector-specific regulations.

4. **Economic Cycles & Macro Factors**: Evaluate sensitivity to interest rates, inflation, consumer spending, currency fluctuations, and credit cycles. How does the company perform in different macro environments (recession, expansion, stagflation)?

5. **Geopolitical & Supply Chain Risks**: Assess exposure to geopolitical tensions, supply chain disruptions, geographic revenue concentration, and commodity price dependencies.

FORMATTING REQUIREMENTS:
- Use markdown with clear headers for each section
- Be specific — reference actual data from the context above
- For each section, rate the impact as: 🟢 Favorable, 🟡 Neutral, or 🔴 Unfavorable
- End with an **Overall External Environment Assessment** summary
- Keep the total analysis between 400-600 words
- Be forward-looking — focus on next 1-3 years outlook"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Assess the industry and macro influences on {ticker}."},
    ]

    try:
        answer = await call_llm(api_key=openai_key, model=model, messages=messages, max_tokens=1500)
    except Exception as exc:
        raise HTTPException(502, f"LLM request failed: {exc}")

    # Delete old and save new
    old_result = await db.execute(
        select(StockAnalysis).where(
            StockAnalysis.user_id == user.id,
            StockAnalysis.ticker == ticker,
            StockAnalysis.analysis_type == "macro",
        )
    )
    for old in old_result.scalars().all():
        await db.delete(old)

    new_row = StockAnalysis(
        user_id=user.id,
        ticker=ticker,
        analysis_type="macro",
        analysis=answer,
        created_at=datetime.datetime.now(datetime.timezone.utc),
    )
    db.add(new_row)
    await db.commit()
    await db.refresh(new_row)

    return {
        "ticker": ticker,
        "analysis": answer,
        "created_at": new_row.created_at.isoformat(),
    }


# =========================================================================
# AI Impact, Fortress & Stress Test Analysis — LLM-powered, on-demand
# =========================================================================

async def _get_stored_analysis(ticker: str, analysis_type: str, user_id: str, db: AsyncSession):
    ticker = ticker.upper()
    result = await db.execute(
        select(StockAnalysis).where(
            StockAnalysis.user_id == user_id,
            StockAnalysis.ticker == ticker,
            StockAnalysis.analysis_type == analysis_type,
        ).order_by(StockAnalysis.created_at.desc()).limit(1)
    )
    row = result.scalar_one_or_none()
    if row:
        analysis_data = row.analysis
        if isinstance(analysis_data, str) and analysis_data.strip().startswith('{'):
            try:
                analysis_data = json.loads(analysis_data)
            except:
                pass
        return {
            "ticker": ticker,
            "analysis": analysis_data,
            "created_at": row.created_at.isoformat(),
        }
    return {"ticker": ticker, "analysis": None, "created_at": None}

async def _generate_ai_analysis(ticker: str, analysis_type: str, system_prompt: str, user, db: AsyncSession, extra_context: str = ""):
    ticker = ticker.upper()

    openai_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not openai_key:
        raise HTTPException(400, "OpenAI API key not configured. Please add it in Settings.")
    model_pref = await get_user_api_key(db, user.id, "openai_model")
    model = model_pref or "gpt-4o-mini"

    # Fetch stock data for context
    try:
        stock_data = await fetch_stock_data(ticker)
    except Exception:
        stock_data = {}

    context = _build_stock_context(ticker, stock_data)
    if extra_context:
        context = f"{context}\n\n{extra_context}"

    full_prompt = f"{system_prompt}\n\nHere is the current data for this company:\n{context}"

    messages = [
        {"role": "system", "content": full_prompt},
        {"role": "user", "content": f"Generate the {analysis_type} analysis for {ticker}. Return ONLY valid JSON."},
    ]

    try:
        answer = await call_llm(api_key=openai_key, model=model, messages=messages, max_tokens=4000, expect_json=True)
    except Exception as exc:
        raise HTTPException(502, f"LLM request failed: {exc}")

    # Clean markdown formatting from JSON output
    cleaned_answer = answer.strip()
    if cleaned_answer.startswith("```json"):
        cleaned_answer = cleaned_answer[7:]
    elif cleaned_answer.startswith("```"):
        cleaned_answer = cleaned_answer[3:]
    if cleaned_answer.endswith("```"):
        cleaned_answer = cleaned_answer[:-3]
    cleaned_answer = cleaned_answer.strip()

    try:
        parsed_data = json.loads(cleaned_answer)
        final_answer = json.dumps(parsed_data)
    except json.JSONDecodeError:
        logger.error(f"Failed to parse LLM JSON output for {analysis_type}: {answer[:200]}...")
        final_answer = answer

    # Save to db
    old_result = await db.execute(
        select(StockAnalysis).where(
            StockAnalysis.user_id == user.id,
            StockAnalysis.ticker == ticker,
            StockAnalysis.analysis_type == analysis_type,
        )
    )
    for old in old_result.scalars().all():
        await db.delete(old)

    new_row = StockAnalysis(
        user_id=user.id,
        ticker=ticker,
        analysis_type=analysis_type,
        analysis=final_answer,
        created_at=datetime.datetime.now(datetime.timezone.utc),
    )
    db.add(new_row)
    await db.commit()
    await db.refresh(new_row)

    res_data = final_answer
    if isinstance(final_answer, str) and final_answer.startswith('{'):
        try:
            res_data = json.loads(final_answer)
        except:
            pass

    return {
        "ticker": ticker,
        "analysis": res_data,
        "created_at": new_row.created_at.isoformat(),
    }

# --- AI Impact ---
@router.get("/{ticker}/ai-impact")
async def get_ai_impact(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _get_stored_analysis(ticker, "ai_impact", user.id, db)

@router.post("/{ticker}/ai-impact/generate")
async def generate_ai_impact(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    prompt = """You are an expert AI impact analyst evaluating a stock's vulnerability and opportunity regarding Artificial Intelligence.
Analyze the company across these 8 specific dimensions:
1. Labor Automation Vulnerability
2. Revenue Model Disruption
3. AI Adoption & Implementation
4. Competitive Moat Durability
5. Operational AI Leverage
6. Regulatory & Ethical Risk
7. Industry Transformation Velocity
8. Data & Ecosystem Strength

For each dimension, provide:
- name: The dimension name
- weight: A percentage weight (sum of all 8 must equal 100%)
- score: A score from 1 to 5 (1 = highly vulnerable/negative impact, 5 = highly resilient/positive impact)
- explanation: A concise 1-3 sentence explanation of why.

Also calculate a weighted composite score (1-5, matching the scale).

YOU MUST RETURN ONLY A VALID JSON OBJECT WITH THE FOLLOWING EXACT STRUCTURE:
{
  "compositeScore": 4.2,
  "dimensions": [
    {
      "name": "Labor Automation Vulnerability",
      "weight": 8,
      "score": 3,
      "explanation": "..."
    }
  ]
}"""
    return await _generate_ai_analysis(ticker, "ai_impact", prompt, user, db)

# --- AI Fortress Analysis ---
@router.get("/{ticker}/ai-fortress")
async def get_ai_fortress(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _get_stored_analysis(ticker, "ai_fortress", user.id, db)

@router.post("/{ticker}/ai-fortress/generate")
async def generate_ai_fortress(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    prompt = """You are an expert technology analyst evaluating if a software company's business model survives the shift from "Human-centric" to "Agent-centric" workflows.
Analyze the company against the "Seven Fortress Architectures":
1. The "Liability Shield" (The Regulatory Moat): Does the software provide regulatory assurance where users face liability?
2. The "Dark Data" Monopoly (The Data Moat): Does the company own massive, proprietary, historical datasets not on the public internet?
3. The "Messy Reality" Integrator (The Complexity Moat): Is the value in connecting fragmented, dirty legacy systems?
4. The "Physics & Atoms" Barrier (The Reality Moat): Does the software control physical objects or hard-science simulations?
5. The "Source-of-Truth" Lock (Authority Moat): Is the software the legally/operationally authoritative record?
6. The "Human Intuition" Core (The Judgment Moat): Does value rely on nuanced human expertise, creativity, or ethics?
7. The "Ecosystem Lock-in" Fortress (The Network Moat): Does utility exponentially increase with network effects and integrations?

For each architecture, state whether the company exhibits this fortress moat (true/false), provide a rating out of 5, and a brief explanation.
Provide an overall summary verdict.

YOU MUST RETURN ONLY A VALID JSON OBJECT WITH THE FOLLOWING EXACT STRUCTURE:
{
  "summary": "Overall verdict on fortress strength...",
  "fortresses": [
    {
      "name": "The 'Liability Shield' (Regulatory)",
      "present": true,
      "score": 4,
      "explanation": "..."
    }
  ]
}"""
    return await _generate_ai_analysis(ticker, "ai_fortress", prompt, user, db)

# --- AI Business Stress Test ---
@router.get("/{ticker}/ai-stress-test")
async def get_ai_stress_test(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _get_stored_analysis(ticker, "ai_stress_test", user.id, db)

@router.post("/{ticker}/ai-stress-test/generate")
async def generate_ai_stress_test(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    prompt = """You are an expert technology analyst evaluating software companies against the "Business Model Stress Test" for the AI era.
Evaluate the company across these 7 angles:
1. The "Seat-Death" Vulnerability: If AI makes customers 10x efficient, does revenue collapse? (High Risk if Seat Pricing).
2. The "Code Replicability": Is it a workflow wrapper (score 1) or deep proprietary math/science (score 10)?
3. The "DIY" Temptation: Would a CTO build this in-house with an AI Agent?
4. The "Blame Externalization" Requirement: Does the customer need a third party to blame when things go wrong?
5. The "Switching Cost" Barrier: How embedded is the software in ops/data/compliance?
6. The "Scalability & Adaptation" Forecast: Can they incorporate AI without cannibalization?
7. The "AI Budget Cannibalization" Defense: Is it Discretionary (nice to have) or Mandatory (lights turn off without it)?

Provide an overall risk rating.

YOU MUST RETURN ONLY A VALID JSON OBJECT WITH THE FOLLOWING EXACT STRUCTURE:
{
  "overallRisk": "Low Risk", 
  "angles": [
    {
      "name": "The 'Seat-Death' Vulnerability",
      "rating": "High Risk",
      "defense": "..."
    }
  ]
}"""
    return await _generate_ai_analysis(ticker, "ai_stress_test", prompt, user, db)

# --- RUPEE Framework (Marwari/Baniya "Sethji" value analysis) ---
@router.get("/{ticker}/rupee")
async def get_rupee(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _get_stored_analysis(ticker, "rupee", user.id, db)

@router.post("/{ticker}/rupee/generate")
async def generate_rupee(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    metrics = await get_rupee_metrics(ticker)
    company = metrics.get("name", ticker)
    extra_context = format_rupee_context(metrics)

    prompt = f"""Act as a shrewd, traditional Indian business owner (a Marwari/Baniya "Sethji") who evaluates publicly traded companies strictly as if buying the entire physical business ("dukaan") with your own hard-earned money.

Ignore Wall Street fluff like "Adjusted EBITDA" or "TAM projections." You only care about capital protection, cold hard cash, low debt, and buying at a massive bargain (the classic Dhandho framework: "Heads I win, tails I don't lose much").

Evaluate the company: {company}.

Open the Bahi-Khata (ledger) and analyze this business using the RUPEE Framework. For EACH letter provide "The Reality" (hard financial numbers/metrics from the ledger) and "The Marwari Verdict" (your interpretation using traditional business sense and relevant Hindi/Marwari jargon).

The five sections, in order:
- R — Rokda (Cash Generation): Focus on Free Cash Flow and FCF margin. Is the galla (cash box) filling up, or is the business bleeding cash?
- U — Udhaari (Debt & Leverage): Focus on Total Debt-to-Equity and interest coverage. Are we slaves to the bank paying byaj (interest), or is the balance sheet clean?
- P — Price (Valuation): Focus on P/E, Price-to-Book, and Margin of Safety. Are we buying at kabaad ke bhav (scrap value), or paying the euphoria premium?
- E — Excellence (Moat, Quality & Margins): Focus on ROE/ROIC, Net & Gross Margins, and Economic Moat. Is there an ek-chhatra raj (monopoly/moat), or is it low-margin mehnat ki roti that barely beats a fixed deposit?
- E — Earnings (Growth & Trajectory): Focus on EPS Growth (trailing AND forward) and Revenue Growth. Is the dhandha actually growing its bottom line, or shrinking? Be skeptical of forward EPS jumps that come from a depressed GAAP base vs adjusted analyst estimates.

Then give the final ruling — "Dhandho Kharido Ya Nahi?" — choosing exactly ONE stance:
- "Saaf Inkaar" (Absolutely Not / Bleeding Cash / High Debt)
- "Taareef Karo, Par Kharido Mat" (Great business, but price too high / Wait for a drop)
- "Ghar le aao" (Great Business, Great Price / Buy it now)

Keep the tone witty, pragmatic, ruthlessly focused on cash, and highly skeptical of management promises.

YOU MUST RETURN ONLY A VALID JSON OBJECT WITH THIS EXACT STRUCTURE:
{{
  "company": "{company}",
  "sections": [
    {{"key": "R", "title": "Rokda (Cash Generation)", "reality": "hard numbers...", "verdict": "the Marwari verdict with jargon..."}},
    {{"key": "U", "title": "Udhaari (Debt & Leverage)", "reality": "...", "verdict": "..."}},
    {{"key": "P", "title": "Price (Valuation)", "reality": "...", "verdict": "..."}},
    {{"key": "E1", "title": "Excellence (Moat, Quality & Margins)", "reality": "...", "verdict": "..."}},
    {{"key": "E2", "title": "Earnings (Growth & Trajectory)", "reality": "...", "verdict": "..."}}
  ],
  "stance": "Ghar le aao",
  "verdict": "final definitive ruling explaining why, in the Sethji voice..."
}}"""
    return await _generate_ai_analysis(ticker, "rupee", prompt, user, db, extra_context=extra_context)

# =========================================================================
# Helper — shared stock context builder for LLM endpoints
# =========================================================================

# =========================================================================
# Strategies — Structured Trades
# =========================================================================

class StructuredTradeIn(BaseModel):
    ticker: str = Field(..., description="Reference asset ticker (e.g. SPY, QQQ)")
    amount: float = Field(..., gt=0, description="Target investment amount in USD")
    duration_days: int = Field(..., gt=0, le=1825, description="Duration of the note in days")
    interest_rate: float | None = Field(None, description="Optional custom risk-free interest rate (in %)")
    structure: str = Field("ppn", description="Note flavor: ppn | capped | yield_enhanced")
    cap_pct: float = Field(15.0, gt=0, le=100, description="Capped note: short-call strike, % above spot")
    put_buffer_pct: float = Field(20.0, gt=0, le=90, description="Yield-enhanced note: short-put strike, % below spot")

@router.post("/{ticker}/strategies/structured-trade")
async def compute_structured_trade(
    ticker: str,
    body: StructuredTradeIn,
    user: User = Depends(get_current_user),
):
    """Calculate an interactive structured trade (principal protected / capped / yield-enhanced note)."""
    active_ticker = body.ticker or ticker
    if active_ticker.startswith("."):
        active_ticker = "^" + active_ticker[1:]

    try:
        result = await run_structured_trade(
            ticker=active_ticker,
            amount=body.amount,
            duration_days=body.duration_days,
            user_interest_rate=body.interest_rate,
            structure=body.structure,
            cap_pct=body.cap_pct,
            put_buffer_pct=body.put_buffer_pct,
        )
        if "error" in result:
            raise HTTPException(400, result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Structured Trade failed: {exc}")

# =========================================================================
# Strategies — Autocallable (Phoenix) Note
# =========================================================================

class AutocallableIn(BaseModel):
    ticker: str = Field(..., description="Reference asset ticker (e.g. SPY, QQQ)")
    amount: float = Field(..., gt=0, description="Target investment amount in USD")
    duration_days: int = Field(..., gt=0, le=1825, description="Maximum tenor in days")
    autocall_barrier_pct: float = Field(100.0, gt=0, le=200, description="Auto-redeem if spot >= this % of initial")
    downside_barrier_pct: float = Field(70.0, gt=0, lt=100, description="Capital protected above this % of initial")
    frequency: int = Field(4, ge=1, le=12, description="Observation dates per year (4 = quarterly)")
    interest_rate: float | None = Field(None, description="Optional custom risk-free rate (in %)")
    coupon_pct: float | None = Field(None, gt=0, le=50, description="Optional fixed annual coupon override (in %)")


@router.post("/{ticker}/strategies/autocallable")
async def compute_autocallable(
    ticker: str,
    body: AutocallableIn,
    user: User = Depends(get_current_user),
):
    """Simulate an autocallable (phoenix) note via Monte-Carlo, with a self-financing coupon."""
    active_ticker = body.ticker or ticker
    if active_ticker.startswith("."):
        active_ticker = "^" + active_ticker[1:]

    if body.downside_barrier_pct >= body.autocall_barrier_pct:
        raise HTTPException(400, "Downside barrier must be below the autocall barrier.")

    try:
        result = await run_autocallable(
            ticker=active_ticker,
            amount=body.amount,
            duration_days=body.duration_days,
            autocall_barrier_pct=body.autocall_barrier_pct,
            downside_barrier_pct=body.downside_barrier_pct,
            frequency=body.frequency,
            user_interest_rate=body.interest_rate,
            user_coupon_pct=body.coupon_pct,
        )
        if "error" in result:
            raise HTTPException(400, result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Autocallable simulation failed: {exc}")


# =========================================================================
# Strategies — Dual Direction Buffer
# =========================================================================

class DualDirectionBufferIn(BaseModel):
    ticker: str = Field(..., description="Reference asset ticker (e.g. SPY, QQQ)")
    amount: float = Field(..., gt=0, description="Target investment amount in USD")
    duration_days: int = Field(..., gt=0, le=1825, description="Duration in days")
    downside_buffer_pct: float = Field(..., ge=0, le=50, description="Downside protection buffer % (0-50)")
    upside_cap_pct: float = Field(..., ge=0, le=100, description="Upside gain cap % (0-100)")
    target_expiration: str | None = Field(default=None, description="Specific expiration date (YYYY-MM-DD)")

@router.post("/{ticker}/strategies/dual-direction-buffer")
async def compute_dual_direction_buffer(
    ticker: str,
    body: DualDirectionBufferIn,
    user: User = Depends(get_current_user),
):
    """Calculate a 4-leg options payload for Dual Direction Buffer constraint."""
    active_ticker = body.ticker or ticker
    if active_ticker.startswith("."):
        active_ticker = "^" + active_ticker[1:]
        
    try:
        result = await run_dual_direction_buffer(
            ticker=active_ticker,
            amount=body.amount,
            duration_days=body.duration_days,
            downside_buffer_pct=body.downside_buffer_pct,
            upside_cap_pct=body.upside_cap_pct,
            target_expiration=body.target_expiration,
        )
        if "error" in result:
            raise HTTPException(400, result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Dual Direction Buffer failed: {exc}")

# =========================================================================
# Strategies — Concentration Management
# =========================================================================

class ConcentrationIn(BaseModel):
    shares: int = Field(..., gt=0, description="Amount of shares held")
    cost_basis: float = Field(..., gt=0, description="Average buy price of the shares")
    position_type: str = Field(..., description="'long' or 'short'")
    tax_rate_pct: float = Field(..., ge=0, description="Capital gains tax bracket %")
    duration_days: int = Field(..., gt=0, description="Days to expiry (30, 60, 90...)")
    upside_cap_pct: float = Field(10.0, ge=0, description="Target upside capture %")
    downside_protection_pct: float = Field(10.0, ge=0, description="Target downside protection %")

@router.post("/{ticker}/strategies/concentration")
async def compute_concentration(
    ticker: str,
    body: ConcentrationIn,
    user: User = Depends(get_current_user),
):
    """Calculate an Equity Collar (or reverse collar) to manage concentration risk efficiently."""
    if ticker.startswith("."):
        ticker = "^" + ticker[1:]
        
    try:
        result = await run_concentration_management(
            ticker=ticker,
            shares=body.shares,
            cost_basis=body.cost_basis,
            position_type=body.position_type,
            tax_rate_pct=body.tax_rate_pct,
            duration_days=body.duration_days,
            upside_cap_pct=body.upside_cap_pct,
            downside_protection_pct=body.downside_protection_pct,
        )
        if "error" in result:
            raise HTTPException(400, result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Concentration Management failed: {exc}")

# =========================================================================
# Strategies — Poor Man's Covered Call / Put
# =========================================================================

class PmccIn(BaseModel):
    amount: float = Field(..., gt=0, description="Target investment amount in USD")
    is_call: bool = Field(..., description="True for PMCC (Bullish), False for PMCP (Bearish)")
    leaps_duration_days: int = Field(..., ge=120, description="Target duration for long leg")
    short_duration_days: int = Field(..., gt=0, description="Target duration for short leg")

@router.post("/{ticker}/strategies/pmcc")
async def compute_pmcc(
    ticker: str,
    body: PmccIn,
    user: User = Depends(get_current_user),
):
    """Calculate a Poor Man's Covered Call or Put."""
    if ticker.startswith("."):
        ticker = "^" + ticker[1:]
        
    try:
        result = await run_pmcc_pmcp(
            ticker=ticker,
            amount=body.amount,
            is_call=body.is_call,
            leaps_duration_days=body.leaps_duration_days,
            short_duration_days=body.short_duration_days,
        )
        if "error" in result:
            raise HTTPException(400, result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PMCC strategy failed: {exc}")

# =========================================================================
# Strategies — ZEBRA (Zero Extrinsic BackRatio)
# =========================================================================

class ZebraIn(BaseModel):
    amount: float = Field(..., gt=0, description="Target investment amount in USD")
    is_call: bool = Field(..., description="True for Bullish (Calls), False for Bearish (Puts)")
    duration_days: int = Field(..., gt=0, description="Target duration to expiration in days")
    strategy_variant: str = Field("zero_extrinsic", description="Variant: zero_extrinsic, low_debit, theta_positive")
    hedge_mode: str = Field("protective", description="Loss hedge: none, protective, collar")

@router.post("/{ticker}/strategies/zebra")
async def compute_zebra(
    ticker: str,
    body: ZebraIn,
    user: User = Depends(get_current_user),
):
    """Calculate a ZEBRA Options Strategy structure."""
    if ticker.startswith("."):
        ticker = "^" + ticker[1:]
        
    try:
        result = await run_zebra(
            ticker=ticker,
            amount=body.amount,
            is_call=body.is_call,
            duration_days=body.duration_days,
            strategy_variant=body.strategy_variant,
            hedge_mode=body.hedge_mode,
        )
        if "error" in result:
            raise HTTPException(400, result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"ZEBRA strategy failed: {exc}")

# =========================================================================
# Strategies — CPPI (Constant Proportion Portfolio Insurance)
# =========================================================================

class CppiIn(BaseModel):
    amount: float = Field(..., gt=0, description="Target initial investment amount in USD")
    floor_pct: float = Field(default=90.0, ge=50.0, le=100.0, description="Capital protection floor percentage (50-100)")
    multiplier: float = Field(default=3.0, ge=1.0, le=10.0, description="Strategy risk multiplier (1-10)")
    duration_years: int = Field(default=3, ge=1, le=10, description="Backtest duration in years")
    rebalance_freq: str = Field(default="weekly", description="Rebalancing frequency: 'daily', 'weekly', 'monthly', or 'threshold'")
    rebalance_threshold_pct: float = Field(default=2.0, ge=0.1, le=20.0, description="Deviation threshold % for threshold rebalancing")
    risk_free_rate: float = Field(default=4.5, ge=0.0, le=20.0, description="Annualized risk-free interest rate in %")
    transaction_fee_pct: float = Field(default=0.1, ge=0.0, le=2.0, description="Per-trade transaction fee %")
    allow_leverage: bool = Field(default=False, description="Allow stock exposure > 100% of portfolio value")
    dynamic_multiplier: bool = Field(default=False, description="Adjust risk multiplier dynamically based on stock volatility")

@router.post("/{ticker}/strategies/cppi")
async def compute_cppi(
    ticker: str,
    body: CppiIn,
    user: User = Depends(get_current_user),
):
    """Run CPPI (Constant Proportion Portfolio Insurance) simulation on yfinance history."""
    if ticker.startswith("."):
        ticker = "^" + ticker[1:]
        
    try:
        result = await run_cppi_simulation(
            ticker=ticker,
            amount=body.amount,
            floor_pct=body.floor_pct,
            multiplier=body.multiplier,
            duration_years=body.duration_years,
            rebalance_freq=body.rebalance_freq,
            rebalance_threshold_pct=body.rebalance_threshold_pct,
            risk_free_rate=body.risk_free_rate,
            transaction_fee_pct=body.transaction_fee_pct,
            allow_leverage=body.allow_leverage,
            dynamic_multiplier=body.dynamic_multiplier
        )
        if "error" in result:
            raise HTTPException(400, result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"CPPI strategy failed: {exc}")

# =========================================================================
# Strategies — Tax Loss Harvesting
# =========================================================================

class PortfolioTLHHoldingIn(BaseModel):
    """Holding row for portfolio TLH — no purchase_date required (analysis uses cost vs market)."""

    ticker: str = Field(..., min_length=1, max_length=20)
    shares: float = Field(..., gt=0)
    cost_basis: float = Field(..., gt=0)


class PortfolioTLHIn(BaseModel):
    holdings: list[PortfolioTLHHoldingIn] = Field(..., min_length=1, description="List of holdings to analyze")
    tax_rate_pct: float = Field(15.0, ge=0, le=100, description="Marginal tax slab percentage")
    preferred_etf: str | None = Field(None, description="Force a specific ETF/stock as replacement instead of auto-discovery")
    custom_stocks: list[str] = Field(default_factory=list, description="List of custom manual stock replacements (Max 3)")

@router.post("/strategies/portfolio-tax-loss-harvesting")
async def compute_portfolio_tax_loss_harvesting(
    body: PortfolioTLHIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Analyze multiple holdings for tax loss harvesting using Advanced Institutional algorithms."""
    try:
        openai_key = await get_user_api_key(db, user.id, "openai_api_key")
        holdings_dicts = [h.model_dump() for h in body.holdings]
        result = await run_portfolio_tax_loss_harvesting(
            holdings=holdings_dicts,
            tax_rate_pct=body.tax_rate_pct,
            preferred_etf=body.preferred_etf,
            custom_stocks=body.custom_stocks,
            openai_key=openai_key,
        )
        if result.get("error"):
            raise HTTPException(400, result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Portfolio TLH failed: {exc}")


# =========================================================================
# Strategies — Box Spread
# =========================================================================

class BoxSpreadIn(BaseModel):
    ticker: str = Field(..., description="Ticker symbol (e.g. XSP, SPY, SPX)")
    amount: float = Field(..., gt=0, description="Target investment amount in USD")
    duration_days: int = Field(..., ge=7, le=730, description="Target duration in days")
    target_annual_return: float = Field(..., description="Target annualized return in percentage")
    intent: str = Field(..., description="'lend' for earning interest, 'borrow' for getting cash")
    quote_source: str = Field(default="yfinance", description="Quote source: 'yfinance' or 'ibkr'")
    target_expiration: str | None = Field(default=None, description="Specific expiration date (YYYY-MM-DD)")
    max_contracts: int = Field(default=1, ge=1, le=10, description="Cap on contracts per position (1 is cheapest)")


@router.post("/{ticker}/strategies/box-spread")
async def compute_box_spread(
    ticker: str,
    body: BoxSpreadIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Find box spread combinations matching user criteria."""
    active_ticker = body.ticker or ticker
    try:
        result = await run_box_strategy(
            ticker=active_ticker,
            amount=body.amount,
            duration_days=body.duration_days,
            target_annual_return=body.target_annual_return,
            intent=body.intent,
            quote_source=body.quote_source,
            user=user,
            db=db,
            target_expiration=body.target_expiration,
            max_contracts=body.max_contracts,
        )
        if "error" in result and not result.get("spreads"):
            raise HTTPException(400, result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Box strategy failed: {exc}")


# =========================================================================
# Strategies — Derivative Income (covered calls / CSPs / collars / credit spreads)
# =========================================================================

class DerivativeIncomeIn(BaseModel):
    target_dte: int | None = Field(default=None, ge=1, le=365, description="Target days-to-expiry; blank = monthlies ≤45d")
    target_expiration: str | None = Field(default=None, description="Exact expiry (YYYY-MM-DD); overrides target_dte")
    min_prob: float = Field(default=0.90, ge=0.5, le=0.99, description="Min probability of NOT being assigned")
    min_income: float = Field(default=20.0, ge=0, description="Min premium ($/contract) to surface")
    structures: list[str] = Field(default_factory=lambda: list(_DI_DEFAULT_STRUCTURES))
    quote_source: str = Field(default="yfinance", description="'yfinance' or 'ibkr'")


@router.post("/{ticker}/strategies/derivative-income")
async def compute_derivative_income(
    ticker: str,
    body: DerivativeIncomeIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deep-scan one underlying (ETF / stock / European index) for option-premium
    income with ≥min_prob probability of not being exercised, ranked vs SOFR."""
    if ticker.startswith("."):
        ticker = "^" + ticker[1:]
    try:
        result = await run_derivative_income(
            ticker=ticker,
            target_dte=body.target_dte,
            target_expiration=body.target_expiration,
            min_prob=body.min_prob,
            min_income=body.min_income,
            structures=body.structures,
            quote_source=body.quote_source,
            user=user,
            db=db,
        )
        if result.get("error"):
            raise HTTPException(400, result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Derivative income failed: {exc}")


# =========================================================================
# Desk Review — ticker-level, ranks every candidate trade (+ Quant→Risk→PM cascade)
# =========================================================================

class DeskReviewIn(BaseModel):
    target_dte: int | None = Field(default=None, ge=1, le=365)
    target_expiration: str | None = Field(default=None, description="Exact expiry (YYYY-MM-DD); overrides target_dte")
    min_prob: float = Field(default=0.90, ge=0.5, le=0.99)
    min_income: float = Field(default=20.0, ge=0)
    structures: list[str] = Field(default_factory=lambda: list(_DI_DEFAULT_STRUCTURES))
    quote_source: str = Field(default="yfinance")
    owns_underlying: bool = Field(default=False, description="User already holds the shares → score covered calls as an income overlay, not a fresh buy-write")


class FocusTrade(BaseModel):
    """Desk Review v2 selector — the single trade to review (structure + expiry + primary short strike)."""
    structure: str
    expiration: str | None = None
    short_strike: float | None = None


class EvaluateLegIn(BaseModel):
    """One leg of a user-supplied ('bring-your-own') trade to evaluate."""
    action: str = Field(..., description="BUY or SELL")
    type: str = Field(..., description="CALL or PUT")
    strike: float = Field(..., gt=0)
    expiration: str = Field(..., description="Leg expiry (YYYY-MM-DD); legs may differ (calendars)")


class DeskEvaluateIn(BaseModel):
    """Evaluate a user-entered multi-leg options trade on the desk pipeline."""
    legs: list[EvaluateLegIn] = Field(default_factory=list)
    quote_source: str = Field(default="yfinance")
    owns_underlying: bool = Field(default=False, description="Hold the underlying → a short call is a COVERED call (income overlay), not naked; enables the collar")


class DeskReviewAgentsIn(DeskReviewIn):
    model: str = Field(default="gpt-4o", description="LLM for the Quant/Risk/PM cascade")
    focus: FocusTrade | None = Field(default=None, description="v2: review ONE scanned trade instead of ranking all")
    evaluate: DeskEvaluateIn | None = Field(default=None, description="Debate the user's own bring-your-own trade")


@router.post("/{ticker}/desk-review")
async def compute_desk_review(
    ticker: str,
    body: DeskReviewIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deterministic ticker-level desk review: rank ALL candidate income trades by a
    blended desk score (algorithmic quant + technical alignment) with full desk metrics.
    No LLM — instant."""
    if ticker.startswith("."):
        ticker = "^" + ticker[1:]
    try:
        result = await rank_desk(
            ticker, target_dte=body.target_dte, min_prob=body.min_prob,
            min_income=body.min_income, structures=body.structures,
            quote_source=body.quote_source, user=user, db=db,
            target_expiration=body.target_expiration, owns_underlying=body.owns_underlying,
        )
        if result.get("error"):
            raise HTTPException(400, result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Desk review failed: {exc}")


@router.post("/{ticker}/desk-review/evaluate")
async def compute_desk_evaluate(
    ticker: str,
    body: DeskEvaluateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Evaluate a USER-ENTERED multi-leg trade (bring-your-own) on the full desk pipeline —
    same chrome (price / volatility / events / TA) + Desk Review (metrics + grade) as the
    single-ticker scan, but for the exact trade the user provides. No LLM — instant."""
    if ticker.startswith("."):
        ticker = "^" + ticker[1:]
    try:
        result = await evaluate_desk_trade(
            ticker, legs=[l.model_dump() for l in body.legs],
            quote_source=body.quote_source, owns_underlying=body.owns_underlying,
            user=user, db=db,
        )
        if result.get("error"):
            raise HTTPException(400, result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Trade evaluation failed: {exc}")


@router.post("/{ticker}/desk-review/agents")
async def compute_desk_review_agents(
    ticker: str,
    body: DeskReviewAgentsIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """On-demand Quant → Risk → PM LLM cascade over ALL ranked trades — recommends the
    single best trade to enter. Each agent feeds the next; needs the user's OpenAI key."""
    if ticker.startswith("."):
        ticker = "^" + ticker[1:]
    api_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="OpenAI API key not configured. Please add it in Settings.")
    try:
        result = await run_desk_agents(
            ticker, api_key=api_key, target_dte=body.target_dte, min_prob=body.min_prob,
            min_income=body.min_income, structures=body.structures,
            quote_source=body.quote_source, model=body.model,
            focus=body.focus.model_dump() if body.focus else None, user=user, db=db,
            target_expiration=body.target_expiration,
            evaluate=body.evaluate.model_dump() if body.evaluate else None,
        )
        if result.get("error"):
            raise HTTPException(400, result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Desk review agents failed: {exc}")


class MonitorTradeIn(BaseModel):
    """One recommended trade to build a live monitoring plan for."""
    structure: str
    put_short: float | None = Field(default=None, description="Short put strike (down-side danger)")
    call_short: float | None = Field(default=None, description="Short call strike (up-side danger)")
    credit: float = Field(default=0.0, description="Net credit per share")
    spot: float | None = Field(default=None, description="Spot at scan time; falls back to the live read")
    dte: int = Field(default=30, ge=1, le=400)


@router.post("/{ticker}/desk-review/monitor")
async def compute_desk_monitor(
    ticker: str,
    body: MonitorTradeIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """On-demand deterministic monitoring plan for ONE recommended trade: reads the LIVE advanced TA
    (microstructure · market structure · regime · dealer gamma) and maps the real structures onto the
    trade's short strikes → WATCH / DEFEND / EXIT levels + corrective actions + the strike rationale. No
    LLM (the deep read is a separate action)."""
    if ticker.startswith("."):
        ticker = "^" + ticker[1:]
    try:
        return await monitor_trade(ticker, body.model_dump())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Monitor plan failed: {exc}")


class MonitorAnalyzeIn(MonitorTradeIn):
    model: str = Field(default="gpt-4o", description="LLM for the deep technical read")


@router.post("/{ticker}/desk-review/monitor/analyze")
async def compute_desk_monitor_analyze(
    ticker: str,
    body: MonitorAnalyzeIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """LLM 'deep read' over the SAME live advanced-TA levels — a qualitative monitoring narrative grounded
    in the real structures + dealer gamma. Needs the user's OpenAI key."""
    if ticker.startswith("."):
        ticker = "^" + ticker[1:]
    api_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="OpenAI API key not configured. Please add it in Settings.")
    try:
        result = await monitor_analyze(ticker, body.model_dump(exclude={"model"}), api_key=api_key, model=body.model)
        if result.get("error"):
            raise HTTPException(400, result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Monitor deep read failed: {exc}")


@router.post("/{ticker}/desk-review/blind")
async def compute_desk_blind(
    ticker: str,
    body: DeskReviewAgentsIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """INDEPENDENT LLM second opinion on ONE trade, BLIND to our desk score/grade (it never sees them) —
    an un-anchored read: a DECISION + cited factor calls (no fuzzy rating), plus the divergence vs the rule
    grade. Needs the user's OpenAI key."""
    if ticker.startswith("."):
        ticker = "^" + ticker[1:]
    api_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="OpenAI API key not configured. Please add it in Settings.")
    try:
        result = await blind_read(
            ticker, api_key=api_key, target_dte=body.target_dte, min_prob=body.min_prob,
            min_income=body.min_income, structures=body.structures, quote_source=body.quote_source,
            model=body.model, focus=body.focus.model_dump() if body.focus else None,
            user=user, db=db, target_expiration=body.target_expiration,
        )
        if result.get("error"):
            raise HTTPException(400, result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Blind read failed: {exc}")


# =========================================================================
# Strategies — Hedging
# =========================================================================

class HedgingIn(BaseModel):
    ticker: str = Field(..., description="Ticker symbol of the holding to hedge (e.g. SMH)")
    shares: float = Field(..., gt=0, description="Number of shares held")
    horizon_days: int = Field(default=45, ge=5, le=730, description="Hedging horizon in days")
    protection_pct: float = Field(default=10.0, ge=0.0, le=40.0, description="Downside protection depth (% OTM for the floor; 0 = floor at spot)")
    upside_pct: float = Field(default=15.0, ge=0.0, le=60.0, description="Upside cap distance (% OTM) for collar-type structures; 0 = no cap / unlimited upside")
    downside_buffer: float = Field(default=8.0, ge=0.0, le=40.0, description="No-loss buffer: fully absorb the first X% of a decline (0 = skip the buffer/put-spread structures)")
    upside_giveup: float = Field(default=8.0, ge=0.0, le=40.0, description="Forgo the first X% of gains (funds the buffer; 0 = give up none)")
    downside_cap: float = Field(default=0.0, ge=0.0, le=60.0, description="Stop protecting below this % drop: sell a financing put here (0 = full protection, no cap)")
    max_cost_pct: float = Field(default=2.0, ge=0.0, le=20.0, description="Budget: max net cost as % of position notional")
    hedge_ratio: float = Field(default=1.0, gt=0, le=3.0, description="Fraction of shares to hedge (1.0 = full)")
    benchmark: str = Field(default="SPY", description="Benchmark for beta computation")
    quote_source: str = Field(default="yfinance", description="Quote source: 'yfinance' or 'ibkr'")
    target_expiration: str | None = Field(default=None, description="Specific expiration date (YYYY-MM-DD)")


@router.get("/{ticker}/strategies/hedging/price-history")
async def hedging_price_history(
    ticker: str,
    days: int = Query(default=90, ge=10, le=365),
    user: User = Depends(get_current_user),
):
    """Daily close/volume + SMAs + fractal support/resistance for the hedge-context chart."""
    return await get_price_history_with_technicals(ticker.upper(), days)


@router.get("/{ticker}/strategies/hedging/market-check")
async def check_hedging_market(
    ticker: str,
    benchmark: str = Query(default="SPY"),
    horizon_days: int = Query(default=45, ge=5, le=365),
    user: User = Depends(get_current_user),
):
    """Lightweight market-conditions timing check — no hedge construction."""
    return await run_market_conditions_check(ticker.upper(), benchmark, horizon_days)


@router.post("/{ticker}/strategies/hedging")
async def compute_hedging(
    ticker: str,
    body: HedgingIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Build a menu of institutional option hedges for a long holding."""
    active_ticker = body.ticker or ticker
    try:
        result = await run_hedging_strategy(
            ticker=active_ticker,
            shares=body.shares,
            horizon_days=body.horizon_days,
            protection_pct=body.protection_pct,
            upside_pct=body.upside_pct,
            downside_buffer=body.downside_buffer,
            upside_giveup=body.upside_giveup,
            downside_cap=body.downside_cap,
            max_cost_pct=body.max_cost_pct,
            hedge_ratio=body.hedge_ratio,
            benchmark=body.benchmark,
            quote_source=body.quote_source,
            target_expiration=body.target_expiration,
            user=user,
            db=db,
        )
        if "error" in result and not result.get("hedges"):
            raise HTTPException(400, result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Hedging strategy failed: {exc}")


# =========================================================================
# Strategies — Long/Short (Single Stock)
# =========================================================================

class SingleStockLongShortIn(BaseModel):
    investment_amount: float = Field(..., gt=0, description="Total investment amount in USD")
    hedge_type: str = Field(default="sector_etf", description="Hedge type: sector_etf")


@router.post("/{ticker}/strategies/long-short")
async def compute_single_stock_long_short(
    ticker: str,
    body: SingleStockLongShortIn,
    user: User = Depends(get_current_user),
):
    """Build a single-stock long/short strategy with sector ETF hedge."""
    try:
        result = await run_single_stock_long_short(
            ticker=ticker,
            investment_amount=body.investment_amount,
            hedge_type=body.hedge_type,
        )
        if "error" in result:
            raise HTTPException(400, result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Long/Short strategy failed: {exc}")


# =========================================================================
# Strategies — Pair Trade Long/Short
# =========================================================================

class PairTradeIn(BaseModel):
    long_ticker: str = Field(..., description="Ticker to go long on")
    short_ticker: str = Field(..., description="Ticker to short")
    investment_amount: float = Field(..., gt=0, description="Investment amount for long side")


@router.post("/strategies/pair-trade")
async def compute_pair_trade(
    body: PairTradeIn,
    user: User = Depends(get_current_user),
):
    """Build a pair trade long/short strategy."""
    try:
        result = await run_pair_trade(
            long_ticker=body.long_ticker,
            short_ticker=body.short_ticker,
            investment_amount=body.investment_amount,
        )
        if "error" in result:
            raise HTTPException(400, result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Pair trade failed: {exc}")


# =========================================================================
# Strategies — Pair Trade Suggestions (System Recommendations)
# =========================================================================

@router.get("/{ticker}/strategies/pair-suggestions")
async def get_pair_suggestions(
    ticker: str,
    user: User = Depends(get_current_user),
):
    """Suggest short-side candidates for a pair trade given a long ticker."""
    try:
        result = await run_pair_suggestions(ticker=ticker, max_candidates=6)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Pair suggestion failed: {exc}")


# =========================================================================
# 130/30 Enhanced Equity Portfolio
# =========================================================================

class PositionInput(BaseModel):
    ticker: str
    shares: float | None = None  # None = auto-optimize

class Build130_30In(BaseModel):
    long_positions: list[PositionInput]
    short_positions: list[PositionInput]
    investment_amount: float = Field(gt=0)
    leverage_ratio: str = "130/30"
    tax_rate_st: float = 0.37
    tax_rate_lt: float = 0.20

@router.post("/strategies/130-30")
async def build_130_30(
    body: Build130_30In,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Build a 130/30 enhanced equity portfolio with scenario analysis and tax projections."""
    try:
        # Get OpenAI key for LLM insights (optional)
        openai_key = await get_user_api_key(db, user.id, "openai_api_key")
        openai_model = (await get_user_api_key(db, user.id, "openai_model")) or "gpt-4o"

        result = await run_130_30_portfolio(
            long_positions=[p.model_dump() for p in body.long_positions],
            short_positions=[p.model_dump() for p in body.short_positions],
            investment_amount=body.investment_amount,
            leverage_ratio=body.leverage_ratio,
            tax_rate_st=body.tax_rate_st,
            tax_rate_lt=body.tax_rate_lt,
            openai_key=openai_key,
            openai_model=openai_model,
        )
        if result.get("error"):
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("130/30 portfolio build failed")
        raise HTTPException(status_code=500, detail=f"Portfolio build failed: {exc}")


# =========================================================================
# Exit Analysis (Quantitative — No LLM)
# =========================================================================

@router.post("/{ticker}/exit-analysis")
async def get_exit_analysis(
    ticker: str,
):
    """Compute comprehensive quantitative exit analysis with 6-pillar scoring."""
    if ticker.startswith("."):
        ticker = "^" + ticker[1:]

    try:
        result = await run_exit_analysis(ticker=ticker)
        if result.get("error"):
            raise HTTPException(400, result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Exit analysis failed: {exc}")


# =========================================================================
# Market Impact Analysis
# =========================================================================

class MarketImpactIn(BaseModel):
    event_text: str = Field(..., description="The news excerpt, tweet, or economic event.")
    duration_str: str = Field(..., description="Relative duration like '1day' or '1month'.")
    exact_datetime: str = Field("", description="Optional explicit datetime ISO string.")

@router.post("/market/impact", response_model=None)
async def compute_market_impact(
    body: MarketImpactIn,
    user: User = Depends(get_premium_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Analyze the impact of a given text event on sectors, stocks, and macro identifiers."""
    api_key = await get_user_api_key(db, user.id, "openai_api_key")
    
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key is required. Please set it in Settings."
        )

    try:
        result = await analyze_market_impact(
            event_text=body.event_text,
            duration_str=body.duration_str,
            exact_datetime=body.exact_datetime,
            openai_key=api_key
        )
        if "error" in result:
             raise HTTPException(400, result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Market Impact Analysis failed: {exc}")

class ThematicImpactIn(BaseModel):
    scenario_text: str = Field(..., description="The hypothetical scenario or theme to analyze.")
    time_horizon: str = Field(
        default="1-3 months",
        description="Time window over which the scenario is expected to play out.",
    )
    include_portfolio_impact: bool = Field(
        default=False,
        description="If true, pulls the user's active SavedStrategy positions and asks the LLM to score each one.",
    )


def _summarize_trade_for_llm(strategy: SavedStrategy) -> dict:
    """Compact, prompt-friendly summary of an active position."""
    stype = strategy.strategy_type or "stock"
    # Try to extract a human-legible quantity (shares or contracts) without pulling heavy calculations.
    qty = None
    try:
        params = json.loads(strategy.parameters) if isinstance(strategy.parameters, str) else (strategy.parameters or {})
        qty = params.get("shares") or params.get("contracts")
    except Exception:
        pass
    return {
        "ticker": strategy.ticker,
        "strategy_type": stype,
        "quantity": qty,
        "notes": (strategy.notes or "")[:200],
    }


@router.post("/market/thematic-impact", response_model=None)
async def compute_thematic_impact(
    body: ThematicImpactIn,
    user: User = Depends(get_premium_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Institutional-style What-If scenario analysis.

    Produces: executive thesis, probability/horizon, historical analogue, granular
    sector/stock/ETF impacts (with magnitude + estimated % move), second-order
    effects, cross-asset reactions, suggested hedges, invalidation signals, and —
    when ``include_portfolio_impact=True`` — per-position scoring for the caller's
    active SavedStrategy rows.
    """
    api_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="OpenAI API key is required. Please set it in Settings.",
        )

    # Optionally pull the user's active positions so the LLM can score them directly.
    portfolio: list[dict] | None = None
    if body.include_portfolio_impact:
        try:
            stmt = select(SavedStrategy).where(
                SavedStrategy.user_id == user.id,
                SavedStrategy.trade_status == "active",
            )
            rows = (await db.execute(stmt)).scalars().all()
            portfolio = [_summarize_trade_for_llm(s) for s in rows if s.ticker]
        except Exception as exc:
            logger.warning(f"Failed to fetch portfolio for What-If: {exc}")
            portfolio = None

    try:
        result = await analyze_thematic_impact(
            scenario_text=body.scenario_text,
            openai_key=api_key,
            time_horizon=body.time_horizon,
            portfolio=portfolio,
        )
        if "error" in result:
            raise HTTPException(400, result["error"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Thematic Impact Analysis failed: {exc}")


# =========================================================================
# Fund / ETF details — holdings, returns, benchmark comparison
# =========================================================================

@router.get("/{ticker}/fund-details")
async def get_fund_details(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Holdings, sector weights, multi-period returns and benchmark comparison for an ETF or MF."""
    ticker = ticker.upper()
    cache_key = f"fund_details:{ticker}"
    cached = await get_cached(db, cache_key)
    if cached:
        return cached
    try:
        result = await fetch_fund_details(ticker)
    except Exception as exc:
        raise HTTPException(500, f"Fund details fetch failed: {exc}")
    await set_cached(db, cache_key, result, ttl_seconds=3600)
    return result


@router.get("/{ticker}/fund-manager-brief")
async def get_fund_manager_brief(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return stored AI fund-manager brief for the ticker (if any)."""
    ticker = ticker.upper()
    result = await db.execute(
        select(StockAnalysis).where(
            StockAnalysis.user_id == user.id,
            StockAnalysis.ticker == ticker,
            StockAnalysis.analysis_type == "fund_manager_brief",
        ).order_by(StockAnalysis.created_at.desc()).limit(1)
    )
    row = result.scalar_one_or_none()
    if row:
        return {"ticker": ticker, "analysis": row.analysis, "created_at": row.created_at.isoformat()}
    return {"ticker": ticker, "analysis": None, "created_at": None}


@router.post("/{ticker}/fund-manager-brief/generate")
async def generate_fund_manager_brief(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate AI brief on fund manager(s) using LLM."""
    ticker = ticker.upper()
    openai_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not openai_key:
        raise HTTPException(400, "OpenAI API key not configured. Please add it in Settings.")
    model_pref = await get_user_api_key(db, user.id, "openai_model")
    model = model_pref or "gpt-4o-mini"

    try:
        stock_data = await fetch_stock_data(ticker)
    except Exception:
        stock_data = {}

    company_name = stock_data.get("companyName", ticker)
    fund_family = stock_data.get("fundFamily", "")
    fund_category = stock_data.get("fundCategory", "")
    quote_type = stock_data.get("quoteType", "ETF")

    system_prompt = f"""You are a senior investment research analyst. Research and profile the management team of {company_name} ({ticker}), a {quote_type} managed by {fund_family} in the {fund_category} category.

Provide a structured research brief covering:

1. **Fund Manager(s) & Tenure**: Name(s), years at the fund, professional background. Note if the fund is passively managed (index tracking) or actively managed.

2. **Investment Philosophy & Style**: Core investment beliefs, portfolio construction approach, risk management style, concentration vs diversification approach.

3. **Public Profile & Media Presence**: Notable interviews, conference talks, published letters/commentaries, CNBC/Bloomberg appearances, Twitter/X or LinkedIn presence. Quote specific public statements if known.

4. **Track Record**: Major investment successes and failures, key calls made, performance vs benchmark over career.

5. **Controversy or Concerns**: Any regulatory issues, significant drawdowns, style drift, or public criticism.

6. **Assessment**: Overall rating of management quality for an investor's decision-making.

IMPORTANT: Be factual. If the fund is passively managed (index fund), state this clearly and focus the profile on the fund family's overall management philosophy and index methodology. If you are unsure about specific details, say so rather than inventing facts. Use the disclaimer "Based on publicly available information as of knowledge cutoff."

Format with clear markdown headers. Keep to 400–600 words."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Research the management team of {ticker} ({company_name})."},
    ]
    try:
        answer = await call_llm(api_key=openai_key, model=model, messages=messages, max_tokens=1500)
    except Exception as exc:
        raise HTTPException(502, f"LLM request failed: {exc}")

    old = await db.execute(
        select(StockAnalysis).where(
            StockAnalysis.user_id == user.id,
            StockAnalysis.ticker == ticker,
            StockAnalysis.analysis_type == "fund_manager_brief",
        )
    )
    for row in old.scalars().all():
        await db.delete(row)

    new_row = StockAnalysis(
        user_id=user.id,
        ticker=ticker,
        analysis_type="fund_manager_brief",
        analysis=answer,
        created_at=datetime.datetime.now(datetime.timezone.utc),
    )
    db.add(new_row)
    await db.commit()
    await db.refresh(new_row)

    return {"ticker": ticker, "analysis": answer, "created_at": new_row.created_at.isoformat()}


# =========================================================================
# Helper — shared stock context builder for LLM endpoints
# =========================================================================

def _build_stock_context(ticker: str, stock_data: dict) -> str:
    """Build a rich text context from stock_data for LLM prompts."""
    parts: list[str] = []
    if not stock_data:
        return f"Ticker: {ticker}"

    parts.append(f"Current Price: ${stock_data.get('price', 'N/A')}")
    parts.append(f"Market Cap: {stock_data.get('marketCap', 'N/A')}")
    parts.append(f"Sector: {stock_data.get('sector', 'N/A')}")
    parts.append(f"Industry: {stock_data.get('industry', 'N/A')}")
    parts.append(f"Change: {stock_data.get('changePercent', 'N/A')}%")

    earn = stock_data.get("earnings", {})
    if earn.get("available"):
        parts.append(f"Trailing P/E: {earn.get('trailingPE', 'N/A')}")
        parts.append(f"Forward P/E: {earn.get('forwardPE', 'N/A')}")
        parts.append(f"PEG Ratio: {earn.get('pegRatio', 'N/A')}")
        parts.append(f"Latest EPS: {earn.get('reportedEPS', 'N/A')}")
        parts.append(f"EPS Surprise: {earn.get('epsSurprisePct', 'N/A')}%")
        parts.append(f"Revenue: {earn.get('revenueFormatted', 'N/A')}")
        parts.append(f"Net Income: {earn.get('netIncomeFormatted', 'N/A')}")

    fin = stock_data.get("financials", {})
    if fin:
        parts.append(f"EPS History: {fin.get('eps', [])}")
        parts.append(f"Revenue History: {fin.get('revenue', [])}")
        parts.append(f"FCF History: {fin.get('freeCashFlow', [])}")

    opts = stock_data.get("options", {})
    if opts.get("available"):
        iv = opts.get("iv", {})
        parts.append(f"IV: {iv.get('current', 'N/A')}%")
        parts.append(f"Put/Call Ratio: {opts.get('putCallRatio', {}).get('openInterest', 'N/A')}")

    tech = stock_data.get("technical", {})
    if tech:
        parts.append(f"RSI: {tech.get('currentRSI', 'N/A')} ({tech.get('rsiSignal', '')})")
        parts.append(f"Analysis: {tech.get('analysisSummary', '')}")

    ns = stock_data.get("newsSummary")
    if isinstance(ns, dict):
        parts.append(f"News Sentiment: {ns.get('sentiment', 'N/A')}")
    elif isinstance(ns, str):
        parts.append(f"News Summary: {ns[:300]}")

    return "\n".join(parts)
