"""Portfolio routes: holdings, transactions, enhanced P&L summary."""

import datetime
import json
import logging
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import get_current_user, get_user_api_key
from ..database import get_db
from ..models import Portfolio, PortfolioHolding, PortfolioTransaction, User, TrackedCompany, PortfolioHighlightDismissal
from ..services.stock_service import fetch_stock_data
from ..services.portfolio_service import (
    fetch_current_prices,
    compute_position_from_transactions,
    compute_annualized_return,
)
from ..services.llm_service import call_llm
from ..services.portfolio_enrichment_service import (
    fetch_enriched_view,
    fetch_prices_cached,
    invalidate_prices,
    invalidate_ticker,
    invalidate_all,
)
from ..services.cache_service import get_cached, set_cached, invalidate
from ..services.exit_strategy_service import analyze_exit_strategy
from ..services.portfolio_optimization_service import optimize_portfolio

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

ASSET_TYPES = {"STOCK", "ETF", "BOND", "MUTUAL_FUND", "CASH", "OPTION", "CRYPTO", "OTHER"}
TRANSACTION_TYPES = {"BUY", "SELL", "OPTION_BUY", "OPTION_SELL", "TRANSFER_IN", "TRANSFER_OUT"}


# --------------------------------------------------------------------------
# Pydantic schemas
# --------------------------------------------------------------------------

class HoldingIn(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=20)
    shares: float = Field(..., gt=0)
    cost_basis: float = Field(..., gt=0)
    purchase_date: datetime.date
    asset_type: str = "STOCK"


class HoldingOut(BaseModel):
    id: int
    ticker: str
    asset_type: str
    shares: float
    cost_basis: float
    purchase_date: datetime.date
    created_at: datetime.datetime


class HoldingWithPrice(BaseModel):
    id: int
    ticker: str
    shares: float
    cost_basis: float
    purchase_date: datetime.date
    current_price: float | None = None
    market_value: float | None = None
    total_cost: float
    unrealized_gain_loss: float | None = None
    unrealized_gain_loss_pct: float | None = None


class PortfolioOut(BaseModel):
    id: int
    name: str
    holdings: list[HoldingOut]
    created_at: datetime.datetime


class PortfolioSummary(BaseModel):
    id: int
    name: str
    holdings: list[HoldingWithPrice]
    total_market_value: float | None
    total_cost: float
    total_unrealized_gain_loss: float | None
    total_unrealized_gain_loss_pct: float | None


class TransactionIn(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=20)
    asset_type: str = Field(default="STOCK")
    transaction_type: str = Field(...)
    shares: float = Field(..., gt=0)
    price_per_share: float = Field(..., ge=0)
    fees: float = Field(default=0.0, ge=0)
    date: datetime.date
    notes: str | None = None


class TransactionOut(BaseModel):
    id: int
    holding_id: int
    ticker: str
    transaction_type: str
    shares: float
    price_per_share: float
    fees: float
    date: datetime.date
    notes: str | None
    created_at: datetime.datetime


class EnhancedHolding(BaseModel):
    id: int
    ticker: str
    company_name: str | None = None
    sector: str | None = None
    industry: str | None = None
    asset_type: str
    shares: float
    cost_basis: float           # avg cost per share
    total_cost: float           # shares * cost_basis
    purchase_date: datetime.date
    first_buy_date: datetime.date | None
    # live data
    current_price: float | None
    market_value: float | None
    day_change: float | None        # $ change today per share
    day_change_pct: float | None    # % change today
    day_pnl: float | None           # shares * day_change
    # P&L
    unrealized_gain_loss: float | None
    unrealized_gain_loss_pct: float | None
    realized_gain_loss: float
    total_gain_loss: float | None
    total_return_pct: float | None
    annualized_return_pct: float | None
    # weight in portfolio
    weight_pct: float | None
    # transaction count
    transaction_count: int


class EnhancedPortfolioSummary(BaseModel):
    id: int
    name: str
    holdings: list[EnhancedHolding]
    total_market_value: float | None
    total_cost: float
    total_unrealized_gain_loss: float | None
    total_unrealized_gain_loss_pct: float | None
    total_realized_gain_loss: float
    total_gain_loss: float | None
    total_day_pnl: float | None
    total_day_pnl_pct: float | None
    portfolio_annualized_return: float | None


class HighlightHistoricalPoint(BaseModel):
    date: str
    price: float


class HighlightNewsItem(BaseModel):
    title: str
    publisher: str
    link: str
    published: str
    summary: str


class HighlightTechnicalSignal(BaseModel):
    rsi: float | None = None
    rsi_signal: str | None = None
    support: float | None = None
    resistance: float | None = None
    macd_signal: str | None = None
    sma_signal: str | None = None


class HighlightFundamentalSignal(BaseModel):
    market_cap: str | None = None
    sector: str | None = None
    industry: str | None = None
    trailing_pe: float | None = None
    forward_pe: float | None = None
    peg_ratio: float | None = None
    dividend_yield: float | None = None


class HighlightHolding(BaseModel):
    ticker: str
    company_name: str | None = None
    asset_type: str
    shares: float
    cost_basis: float
    current_price: float | None = None
    day_change: float | None = None
    day_change_pct: float | None = None
    unrealized_gain_loss: float | None = None
    unrealized_gain_loss_pct: float | None = None
    weight_pct: float | None = None
    last_5d_history: list[HighlightHistoricalPoint] = []
    technicals: HighlightTechnicalSignal
    fundamentals: HighlightFundamentalSignal
    news: list[HighlightNewsItem] = []
    hypothesis: str


class HighlightResponse(BaseModel):
    highlights: list[HighlightHolding]
    total_portfolio_holdings: int


class DismissHighlightIn(BaseModel):
    ticker: str



# --------------------------------------------------------------------------
# Helper: get or create user's portfolio
# --------------------------------------------------------------------------

async def _get_or_create_portfolio(db: AsyncSession, user_id: int) -> Portfolio:
    """Return the user's portfolio, creating one if it doesn't exist."""
    result = await db.execute(
        select(Portfolio)
        .options(
            selectinload(Portfolio.holdings).selectinload(PortfolioHolding.transactions)
        )
        .where(Portfolio.user_id == user_id)
    )
    portfolio = result.scalar_one_or_none()

    if portfolio is None:
        portfolio = Portfolio(user_id=user_id, name="My Portfolio")
        db.add(portfolio)
        await db.commit()
        await db.refresh(portfolio)
        result = await db.execute(
            select(Portfolio)
            .options(
                selectinload(Portfolio.holdings).selectinload(PortfolioHolding.transactions)
            )
            .where(Portfolio.id == portfolio.id)
        )
        portfolio = result.scalar_one()

    return portfolio


# --------------------------------------------------------------------------
# GET /api/portfolio/optimize — min-vol + HRP allocation & rebalance trades
# --------------------------------------------------------------------------

@router.get("/optimize")
async def optimize_portfolio_endpoint(
    risk_free_rate: float = 0.045,
    lookback: str = "2y",
    max_weight: float | None = None,
    sector_max: float | None = None,
    transaction_cost_bps: float = 0.0,
    l2_gamma: float = 0.0,
    force_refresh: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Optimal weights (minimum-volatility + HRP), a DiscreteAllocation trade
    list vs. the current book, and efficient-frontier points for the chart.

    The constraint knobs apply to the minimum-volatility target only (HRP is
    unconstrained by construction):

    * ``max_weight`` / ``sector_max`` — per-name / per-sector caps as fractions
      (``0.1`` = 10%). ``sector_max`` uses the cached fundamentals sector map.
    * ``transaction_cost_bps`` — turnover penalty vs. the current book.
    * ``l2_gamma`` — L2 weight-dispersion penalty.

    Result is price-derived, so it's cached for 15 min (``_TTL_PRICE``); pass
    ``force_refresh=true`` to recompute. Only successful runs are cached — the
    "not enough data" responses stay live so adding holdings takes effect at once.
    """
    portfolio = await _get_or_create_portfolio(db, user.id)

    # Sector caps need a ticker→sector map. Read it best-effort from the
    # fundamentals cache (no network) — names without a cached sector simply
    # aren't sector-constrained.
    sector_map: dict[str, str] | None = None
    if sector_max is not None:
        sector_map = {}
        for h in portfolio.holdings:
            cached_meta = await get_cached(db, f"portfolio:fundamentals:{h.ticker}")
            if cached_meta and cached_meta.get("sector"):
                sector_map[h.ticker.upper()] = cached_meta["sector"]

    cache_key = (
        f"portfolio:optim:{portfolio.id}:{lookback}:rf{risk_free_rate}"
        f":mw{max_weight}:sm{sector_max}:tc{transaction_cost_bps}:l2{l2_gamma}:v2"
    )

    if not force_refresh:
        cached = await get_cached(db, cache_key)
        if cached is not None:
            return cached

    result = await optimize_portfolio(
        portfolio.holdings,
        risk_free_rate=risk_free_rate,
        lookback=lookback,
        max_weight=max_weight,
        sector_max=sector_max,
        sector_map=sector_map,
        transaction_cost_bps=transaction_cost_bps,
        l2_gamma=l2_gamma,
    )

    if result.get("available"):
        await set_cached(db, cache_key, result, ttl_seconds=900)
    return result


# --------------------------------------------------------------------------
# GET /api/portfolio — get user's portfolio with holdings
# --------------------------------------------------------------------------

@router.get("", response_model=PortfolioOut)
async def get_portfolio(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await _get_or_create_portfolio(db, user.id)
    return PortfolioOut(
        id=portfolio.id,
        name=portfolio.name,
        holdings=[
            HoldingOut(
                id=h.id,
                ticker=h.ticker,
                asset_type=h.asset_type or "STOCK",
                shares=h.shares,
                cost_basis=h.cost_basis,
                purchase_date=h.purchase_date,
                created_at=h.created_at,
            )
            for h in portfolio.holdings
        ],
        created_at=portfolio.created_at,
    )


# --------------------------------------------------------------------------
# GET /api/portfolio/summary — legacy summary with current prices
# --------------------------------------------------------------------------

@router.get("/summary", response_model=PortfolioSummary)
async def get_portfolio_summary(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await _get_or_create_portfolio(db, user.id)

    tickers = list(set(h.ticker for h in portfolio.holdings))
    prices = await fetch_current_prices(tickers) if tickers else {}

    holdings_with_prices: list[HoldingWithPrice] = []
    total_market_value = 0.0
    total_cost = 0.0
    all_prices_available = True

    for h in portfolio.holdings:
        current_price = prices.get(h.ticker.upper())
        cost = h.shares * h.cost_basis
        total_cost += cost

        if current_price is not None:
            market_value = h.shares * current_price
            total_market_value += market_value
            gain_loss = market_value - cost
            gain_loss_pct = (gain_loss / cost * 100) if cost > 0 else 0.0
        else:
            market_value = gain_loss = gain_loss_pct = None
            all_prices_available = False

        holdings_with_prices.append(
            HoldingWithPrice(
                id=h.id,
                ticker=h.ticker,
                shares=h.shares,
                cost_basis=h.cost_basis,
                purchase_date=h.purchase_date,
                current_price=round(current_price, 2) if current_price else None,
                market_value=round(market_value, 2) if market_value is not None else None,
                total_cost=round(cost, 2),
                unrealized_gain_loss=round(gain_loss, 2) if gain_loss is not None else None,
                unrealized_gain_loss_pct=round(gain_loss_pct, 2) if gain_loss_pct is not None else None,
            )
        )

    total_gl = total_market_value - total_cost if all_prices_available else None
    total_gl_pct = (total_gl / total_cost * 100) if (total_gl is not None and total_cost > 0) else None

    return PortfolioSummary(
        id=portfolio.id,
        name=portfolio.name,
        holdings=holdings_with_prices,
        total_market_value=round(total_market_value, 2) if all_prices_available else None,
        total_cost=round(total_cost, 2),
        total_unrealized_gain_loss=round(total_gl, 2) if total_gl is not None else None,
        total_unrealized_gain_loss_pct=round(total_gl_pct, 2) if total_gl_pct is not None else None,
    )


# --------------------------------------------------------------------------
# GET /api/portfolio/enhanced — full enriched summary (Step 1 backbone)
# --------------------------------------------------------------------------

@router.get("/enhanced", response_model=EnhancedPortfolioSummary)
async def get_enhanced_portfolio_summary(
    force_refresh: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns all holdings with day change, annualised returns, realized P&L,
    and per-holding weight. Prices come from the 15-min DB cache; pass
    force_refresh=true (the user's Refresh action) to pull fresh quotes.
    """
    portfolio = await _get_or_create_portfolio(db, user.id)

    tickers = list(set(h.ticker for h in portfolio.holdings))
    price_data = await fetch_prices_cached(db, tickers, force_refresh=force_refresh) if tickers else {}

    # Pull sector/company_name from fundamentals cache (best-effort; None if not yet cached)
    meta: dict[str, dict] = {}
    for ticker in tickers:
        cached = await get_cached(db, f"portfolio:fundamentals:{ticker}")
        if cached:
            meta[ticker] = cached

    enhanced_holdings: list[EnhancedHolding] = []
    total_market_value = 0.0
    total_cost_basis = 0.0
    total_realized = 0.0
    total_day_pnl = 0.0
    prices_available = 0
    day_pnl_available = 0

    for h in portfolio.holdings:
        pd = price_data.get(h.ticker.upper(), {})
        current_price = pd.get("price")
        day_change = pd.get("day_change")
        day_change_pct = pd.get("day_change_pct")

        cost = round(h.shares * h.cost_basis, 4)
        realized = h.realized_gain_loss or 0.0
        total_invested = h.total_invested
        first_buy_date = h.first_buy_date or h.purchase_date

        if current_price is not None:
            market_value = round(h.shares * current_price, 4)
            unrealized = round(market_value - cost, 4)
            unrealized_pct = round(unrealized / cost * 100, 2) if cost > 0 else 0.0
            total_gl = round(unrealized + realized, 4)
            total_return_pct = round((total_gl / (total_invested or cost)) * 100, 2) if (total_invested or cost) > 0 else None
            ann_return = compute_annualized_return(
                total_invested=total_invested or cost,
                current_value=market_value,
                realized_gain_loss=realized,
                first_buy_date=first_buy_date,
            )
            total_market_value += market_value
            prices_available += 1
        else:
            market_value = unrealized = unrealized_pct = total_gl = total_return_pct = ann_return = None

        day_pnl = round(h.shares * day_change, 4) if (day_change is not None and h.shares) else None
        if day_pnl is not None:
            total_day_pnl += day_pnl
            day_pnl_available += 1

        total_cost_basis += cost
        total_realized += realized

        m = meta.get(h.ticker, {})
        enhanced_holdings.append(
            EnhancedHolding(
                id=h.id,
                ticker=h.ticker,
                company_name=m.get("company_name"),
                sector=m.get("sector") or None,
                industry=m.get("industry") or None,
                asset_type=h.asset_type or "STOCK",
                shares=h.shares,
                cost_basis=round(h.cost_basis, 4),
                total_cost=round(cost, 2),
                purchase_date=h.purchase_date,
                first_buy_date=first_buy_date,
                current_price=round(current_price, 4) if current_price else None,
                market_value=round(market_value, 2) if market_value is not None else None,
                day_change=round(day_change, 4) if day_change is not None else None,
                day_change_pct=round(day_change_pct, 2) if day_change_pct is not None else None,
                day_pnl=round(day_pnl, 2) if day_pnl is not None else None,
                unrealized_gain_loss=round(unrealized, 2) if unrealized is not None else None,
                unrealized_gain_loss_pct=round(unrealized_pct, 2) if unrealized_pct is not None else None,
                realized_gain_loss=round(realized, 2),
                total_gain_loss=round(total_gl, 2) if total_gl is not None else None,
                total_return_pct=total_return_pct,
                annualized_return_pct=ann_return,
                weight_pct=None,  # filled in below once we have total
                transaction_count=len(h.transactions),
            )
        )

    # Back-fill weight_pct
    for eh in enhanced_holdings:
        if total_market_value and eh.market_value is not None:
            eh.weight_pct = round(eh.market_value / total_market_value * 100, 2)

    # Portfolio-level annualised return (market-value weighted)
    port_ann_return: float | None = None
    weighted_ann = [
        (eh.annualized_return_pct or 0) * (eh.weight_pct or 0)
        for eh in enhanced_holdings
        if eh.annualized_return_pct is not None and eh.weight_pct is not None
    ]
    if weighted_ann:
        port_ann_return = round(sum(weighted_ann) / 100, 2)

    total_unrealized = round(total_market_value - total_cost_basis, 2) if prices_available == len(portfolio.holdings) else None
    total_unrealized_pct = round(total_unrealized / total_cost_basis * 100, 2) if (total_unrealized is not None and total_cost_basis > 0) else None
    total_gl_port = round((total_unrealized or 0) + total_realized, 2) if total_unrealized is not None else None
    day_pnl_out = round(total_day_pnl, 2) if day_pnl_available > 0 else None
    day_pnl_pct = round(total_day_pnl / (total_market_value - total_day_pnl) * 100, 2) if (day_pnl_out and total_market_value) else None

    return EnhancedPortfolioSummary(
        id=portfolio.id,
        name=portfolio.name,
        holdings=enhanced_holdings,
        total_market_value=round(total_market_value, 2) if prices_available > 0 else None,
        total_cost=round(total_cost_basis, 2),
        total_unrealized_gain_loss=total_unrealized,
        total_unrealized_gain_loss_pct=total_unrealized_pct,
        total_realized_gain_loss=round(total_realized, 2),
        total_gain_loss=total_gl_port,
        total_day_pnl=day_pnl_out,
        total_day_pnl_pct=day_pnl_pct,
        portfolio_annualized_return=port_ann_return,
    )


# --------------------------------------------------------------------------
# POST /api/portfolio/holdings — add a holding (direct, no transaction)
# --------------------------------------------------------------------------

@router.post("/holdings", response_model=HoldingOut, status_code=201)
async def add_holding(
    body: HoldingIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await _get_or_create_portfolio(db, user.id)
    asset_type = body.asset_type.upper() if body.asset_type.upper() in ASSET_TYPES else "STOCK"

    holding = PortfolioHolding(
        portfolio_id=portfolio.id,
        ticker=body.ticker.upper().strip(),
        asset_type=asset_type,
        shares=body.shares,
        cost_basis=body.cost_basis,
        purchase_date=body.purchase_date,
    )
    db.add(holding)
    await db.commit()
    await db.refresh(holding)

    return HoldingOut(
        id=holding.id,
        ticker=holding.ticker,
        asset_type=holding.asset_type or "STOCK",
        shares=holding.shares,
        cost_basis=holding.cost_basis,
        purchase_date=holding.purchase_date,
        created_at=holding.created_at,
    )


# --------------------------------------------------------------------------
# DELETE /api/portfolio/holdings/{holding_id}
# --------------------------------------------------------------------------

@router.delete("/holdings/{holding_id}")
async def delete_holding(
    holding_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await _get_or_create_portfolio(db, user.id)

    result = await db.execute(
        select(PortfolioHolding).where(
            PortfolioHolding.id == holding_id,
            PortfolioHolding.portfolio_id == portfolio.id,
        )
    )
    holding = result.scalar_one_or_none()
    if holding is None:
        raise HTTPException(status_code=404, detail="Holding not found")

    await db.delete(holding)
    await db.commit()
    return {"ok": True, "deleted": holding_id}


# --------------------------------------------------------------------------
# PUT /api/portfolio/holdings/{holding_id}
# --------------------------------------------------------------------------

class HoldingUpdate(BaseModel):
    ticker: str | None = None
    asset_type: str | None = None
    shares: float | None = Field(default=None, gt=0)
    cost_basis: float | None = Field(default=None, gt=0)
    purchase_date: datetime.date | None = None


@router.put("/holdings/{holding_id}", response_model=HoldingOut)
async def update_holding(
    holding_id: int,
    body: HoldingUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await _get_or_create_portfolio(db, user.id)

    result = await db.execute(
        select(PortfolioHolding).where(
            PortfolioHolding.id == holding_id,
            PortfolioHolding.portfolio_id == portfolio.id,
        )
    )
    holding = result.scalar_one_or_none()
    if holding is None:
        raise HTTPException(status_code=404, detail="Holding not found")

    if body.ticker is not None:
        holding.ticker = body.ticker.upper().strip()
    if body.asset_type is not None:
        at = body.asset_type.upper()
        holding.asset_type = at if at in ASSET_TYPES else "STOCK"
    if body.shares is not None:
        holding.shares = body.shares
    if body.cost_basis is not None:
        holding.cost_basis = body.cost_basis
    if body.purchase_date is not None:
        holding.purchase_date = body.purchase_date

    await db.commit()
    await db.refresh(holding)

    return HoldingOut(
        id=holding.id,
        ticker=holding.ticker,
        asset_type=holding.asset_type or "STOCK",
        shares=holding.shares,
        cost_basis=holding.cost_basis,
        purchase_date=holding.purchase_date,
        created_at=holding.created_at,
    )


# --------------------------------------------------------------------------
# Bulk delete
# --------------------------------------------------------------------------

class BulkDeleteIn(BaseModel):
    holding_ids: list[int] = Field(..., min_length=1)


@router.post("/holdings/bulk-delete")
async def bulk_delete(
    body: BulkDeleteIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    portfolio = await _get_or_create_portfolio(db, user.id)

    result = await db.execute(
        select(PortfolioHolding).where(
            PortfolioHolding.id.in_(body.holding_ids),
            PortfolioHolding.portfolio_id == portfolio.id,
        )
    )
    holdings = result.scalars().all()
    if not holdings:
        raise HTTPException(status_code=404, detail="No matching holdings found")

    for h in holdings:
        await db.delete(h)

    await db.commit()
    return {"ok": True, "deleted": len(holdings)}


# --------------------------------------------------------------------------
# Transaction endpoints
# --------------------------------------------------------------------------

@router.post("/transactions", response_model=TransactionOut, status_code=201)
async def add_transaction(
    body: TransactionIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Add a transaction (BUY/SELL/OPTION_BUY/OPTION_SELL).
    Automatically creates or updates the corresponding holding using
    weighted-average cost basis.
    """
    if body.transaction_type.upper() not in TRANSACTION_TYPES:
        raise HTTPException(400, f"Invalid transaction_type. Valid: {TRANSACTION_TYPES}")

    portfolio = await _get_or_create_portfolio(db, user.id)
    ticker = body.ticker.upper().strip()
    asset_type = body.asset_type.upper() if body.asset_type.upper() in ASSET_TYPES else "STOCK"
    txn_type = body.transaction_type.upper()

    # Find or create holding for this ticker
    result = await db.execute(
        select(PortfolioHolding)
        .options(selectinload(PortfolioHolding.transactions))
        .where(
            PortfolioHolding.portfolio_id == portfolio.id,
            PortfolioHolding.ticker == ticker,
        )
    )
    holding = result.scalar_one_or_none()

    if holding is None:
        # Create a placeholder holding
        holding = PortfolioHolding(
            portfolio_id=portfolio.id,
            ticker=ticker,
            asset_type=asset_type,
            shares=0,
            cost_basis=0,
            purchase_date=body.date,
            realized_gain_loss=0,
            total_invested=0,
            first_buy_date=body.date,
        )
        db.add(holding)
        await db.flush()  # get holding.id

    # Create the transaction
    txn = PortfolioTransaction(
        holding_id=holding.id,
        portfolio_id=portfolio.id,
        ticker=ticker,
        transaction_type=txn_type,
        shares=body.shares,
        price_per_share=body.price_per_share,
        fees=body.fees or 0.0,
        date=body.date,
        notes=body.notes,
    )
    db.add(txn)
    await db.flush()

    # Reload transactions for this holding and recompute position
    result2 = await db.execute(
        select(PortfolioTransaction).where(
            PortfolioTransaction.holding_id == holding.id
        )
    )
    all_txns = result2.scalars().all()
    position = compute_position_from_transactions(all_txns)

    holding.shares = position["shares"]
    holding.cost_basis = position["cost_basis"]
    holding.realized_gain_loss = position["realized_gain_loss"]
    holding.total_invested = position["total_invested"]
    if position["first_buy_date"]:
        holding.first_buy_date = position["first_buy_date"]
    if holding.purchase_date is None:
        holding.purchase_date = position["first_buy_date"] or body.date
    holding.asset_type = asset_type

    await db.commit()
    await db.refresh(txn)

    return TransactionOut(
        id=txn.id,
        holding_id=txn.holding_id,
        ticker=txn.ticker,
        transaction_type=txn.transaction_type,
        shares=txn.shares,
        price_per_share=txn.price_per_share,
        fees=txn.fees,
        date=txn.date,
        notes=txn.notes,
        created_at=txn.created_at,
    )


@router.get("/transactions", response_model=list[TransactionOut])
async def get_transactions(
    ticker: str | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all transactions, optionally filtered by ticker."""
    portfolio = await _get_or_create_portfolio(db, user.id)

    query = select(PortfolioTransaction).where(
        PortfolioTransaction.portfolio_id == portfolio.id
    )
    if ticker:
        query = query.where(PortfolioTransaction.ticker == ticker.upper().strip())

    result = await db.execute(query.order_by(PortfolioTransaction.date.desc(), PortfolioTransaction.id.desc()))
    txns = result.scalars().all()

    return [
        TransactionOut(
            id=t.id,
            holding_id=t.holding_id,
            ticker=t.ticker,
            transaction_type=t.transaction_type,
            shares=t.shares,
            price_per_share=t.price_per_share,
            fees=t.fees,
            date=t.date,
            notes=t.notes,
            created_at=t.created_at,
        )
        for t in txns
    ]


@router.delete("/transactions/{txn_id}")
async def delete_transaction(
    txn_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a transaction and recompute the holding's position."""
    portfolio = await _get_or_create_portfolio(db, user.id)

    result = await db.execute(
        select(PortfolioTransaction).where(
            PortfolioTransaction.id == txn_id,
            PortfolioTransaction.portfolio_id == portfolio.id,
        )
    )
    txn = result.scalar_one_or_none()
    if txn is None:
        raise HTTPException(404, "Transaction not found")

    holding_id = txn.holding_id
    await db.delete(txn)
    await db.flush()

    # Recompute position from remaining transactions
    result2 = await db.execute(
        select(PortfolioTransaction).where(PortfolioTransaction.holding_id == holding_id)
    )
    remaining = result2.scalars().all()

    result3 = await db.execute(select(PortfolioHolding).where(PortfolioHolding.id == holding_id))
    holding = result3.scalar_one_or_none()

    if holding:
        if remaining:
            position = compute_position_from_transactions(remaining)
            holding.shares = position["shares"]
            holding.cost_basis = position["cost_basis"]
            holding.realized_gain_loss = position["realized_gain_loss"]
            holding.total_invested = position["total_invested"]
            if position["first_buy_date"]:
                holding.first_buy_date = position["first_buy_date"]
        else:
            # No transactions left — delete the holding if it was transaction-managed
            # (if shares is 0 and total_invested > 0, it was fully managed by txns)
            await db.delete(holding)

    await db.commit()
    return {"ok": True, "deleted": txn_id}


# --------------------------------------------------------------------------
# Bulk Transaction Import: Parse free-form text via LLM
# --------------------------------------------------------------------------

class BulkTransactionTextIn(BaseModel):
    text: str = Field(..., min_length=5, max_length=8000)
    ticker: str | None = None   # optional hint (e.g. when called from a single-ticker panel)


class ParsedTransactionItem(BaseModel):
    ticker: str
    asset_type: str = "STOCK"
    transaction_type: str
    shares: float
    price_per_share: float
    fees: float = 0.0
    date: str
    notes: str = ""


class BulkTransactionParseResponse(BaseModel):
    transactions: list[ParsedTransactionItem]


class BulkTransactionSaveIn(BaseModel):
    transactions: list[TransactionIn]


@router.post("/transactions/bulk-parse", response_model=BulkTransactionParseResponse)
async def bulk_parse_transactions(
    body: BulkTransactionTextIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Parse free-form transaction text (brokerage statements, CSV snippets, prose) via LLM."""
    openai_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not openai_key:
        raise HTTPException(400, "OpenAI API key not configured. Please add it in Settings.")

    model_pref = await get_user_api_key(db, user.id, "openai_model")
    model = model_pref or "gpt-4o-mini"

    ticker_hint = f"\nNote: if a ticker is not clearly labeled, these transactions are likely for ticker {body.ticker.upper()}." if body.ticker else ""

    system_prompt = f"""You are a data extraction assistant that specialises in parsing brokerage order logs, trade confirmations, and account statements from any broker (Schwab, Fidelity, TD Ameritrade, Robinhood, IBKR, Vanguard, etc.).{ticker_hint}

For every buy/sell/transfer/option trade found, extract these fields:

- ticker       : Ticker symbol UPPERCASE. Look for symbols in parentheses like "(AAPL)", in a "Symbol:" field, or in a column labelled Symbol. Ignore account numbers.
- asset_type   : STOCK | ETF | MUTUAL_FUND | OPTION. Infer from the description:
                 If name contains ETF, FUND, TR (Trust), INDEX, SPDR, iShares, Vanguard fund, WisdomTree → ETF
                 If name contains "MUTUAL FUND" or ends in class letters (A/B/C/INST) → MUTUAL_FUND
                 If description contains "CALL", "PUT", contract notation → OPTION
                 Otherwise → STOCK
- transaction_type : BUY | SELL | TRANSFER_IN | TRANSFER_OUT | OPTION_BUY | OPTION_SELL
                 "YOU BOUGHT" / "PURCHASE" / "BUY" / "BOUGHT" → BUY
                 "YOU SOLD" / "SOLD" / "SELL" / "SALE" → SELL
                 "TRANSFERRED IN" / "RECEIVED" → TRANSFER_IN
                 "TRANSFERRED OUT" / "DELIVERED" → TRANSFER_OUT
                 Option buy → OPTION_BUY, option sell → OPTION_SELL
                 Dividend reinvestment → BUY
- shares          : Number of shares as a positive float. Strip leading "+".
- price_per_share : Price per share in USD, positive float. If only a total amount is given and shares are known, compute price = |total_amount| / shares.
- fees            : Commission, fee, or expense in USD. Default 0.0 if not shown.
- date            : Trade/order date (NOT settlement date) in YYYY-MM-DD. Parse formats like "Jun-09-2026", "06/09/2026", "June 9 2026", "2026-06-09". If month is ambiguous use month-first (US convention).
- notes           : Account name, settlement date, basket name, order ID, or other context. Keep brief. Empty string if nothing useful.

STRICT RULES:
- Return ONLY a valid JSON array — no markdown fences, no prose, no comments.
- All tickers uppercase, max 10 chars.
- If the same ticker appears multiple times (e.g. two partial fills on the same day), emit each as a separate object.
- Skip non-trade lines: dividends (unless reinvested), interest, fees-only rows, balance/cash rows, header rows.
- If price_per_share cannot be determined (no price and no total), omit that transaction.
- Return [] if nothing tradeable is found.

Example output (do not include this in your response):
[
  {{"ticker":"GLDM","asset_type":"ETF","transaction_type":"BUY","shares":2.0,"price_per_share":84.10,"fees":0,"date":"2026-06-09","notes":"Golden Years ***2627, settled 2026-06-10"}},
  {{"ticker":"AAPL","asset_type":"STOCK","transaction_type":"SELL","shares":10,"price_per_share":200.00,"fees":1.99,"date":"2024-04-01","notes":""}}
]"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": body.text},
    ]

    try:
        raw = await call_llm(api_key=openai_key, model=model, messages=messages, max_tokens=4000, expect_json=True)
    except Exception as exc:
        raise HTTPException(502, f"LLM request failed: {exc}")

    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        parsed = json.loads(cleaned.strip())
        if not isinstance(parsed, list):
            raise ValueError("Expected a JSON array")
        valid_asset_types = {"STOCK", "ETF", "MUTUAL_FUND", "OPTION", "BOND", "CRYPTO", "CASH", "OTHER"}
        transactions = [
            ParsedTransactionItem(
                ticker=(item.get("ticker") or body.ticker or "").upper().strip(),
                asset_type=(item.get("asset_type") or "STOCK").upper() if (item.get("asset_type") or "STOCK").upper() in valid_asset_types else "STOCK",
                transaction_type=(item.get("transaction_type") or "BUY").upper(),
                shares=float(item.get("shares") or 0),
                price_per_share=float(item.get("price_per_share") or 0),
                fees=float(item.get("fees") or 0),
                date=str(item.get("date") or datetime.date.today().isoformat()),
                notes=str(item.get("notes") or ""),
            )
            for item in parsed
            if (item.get("shares") or 0) > 0 and (item.get("price_per_share") or 0) >= 0
        ]
        return BulkTransactionParseResponse(transactions=transactions)
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning(f"Failed to parse LLM transaction response: {e}\nRaw: {raw}")
        raise HTTPException(422, f"Could not parse the text. Error: {e}")


@router.post("/transactions/bulk-save")
async def bulk_save_transactions(
    body: BulkTransactionSaveIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save multiple transactions, each updating the holding position (same logic as single add)."""
    if not body.transactions:
        raise HTTPException(400, "No transactions provided")

    portfolio = await _get_or_create_portfolio(db, user.id)
    saved_count = 0

    # Process chronologically so position recomputation is correct
    ordered = sorted(body.transactions, key=lambda t: t.date)

    for item in ordered:
        if item.transaction_type.upper() not in TRANSACTION_TYPES:
            raise HTTPException(400, f"Invalid transaction_type '{item.transaction_type}'")

        ticker = item.ticker.upper().strip()
        asset_type = item.asset_type.upper() if item.asset_type.upper() in ASSET_TYPES else "STOCK"
        txn_type = item.transaction_type.upper()

        result = await db.execute(
            select(PortfolioHolding)
            .options(selectinload(PortfolioHolding.transactions))
            .where(PortfolioHolding.portfolio_id == portfolio.id, PortfolioHolding.ticker == ticker)
        )
        holding = result.scalar_one_or_none()

        if holding is None:
            holding = PortfolioHolding(
                portfolio_id=portfolio.id,
                ticker=ticker,
                asset_type=asset_type,
                shares=0,
                cost_basis=0,
                purchase_date=item.date,
                realized_gain_loss=0,
                total_invested=0,
                first_buy_date=item.date,
            )
            db.add(holding)
            await db.flush()

        txn = PortfolioTransaction(
            holding_id=holding.id,
            portfolio_id=portfolio.id,
            ticker=ticker,
            transaction_type=txn_type,
            shares=item.shares,
            price_per_share=item.price_per_share,
            fees=item.fees or 0.0,
            date=item.date,
            notes=item.notes or None,
        )
        db.add(txn)
        await db.flush()

        result2 = await db.execute(
            select(PortfolioTransaction).where(PortfolioTransaction.holding_id == holding.id)
        )
        all_txns = result2.scalars().all()
        position = compute_position_from_transactions(all_txns)

        holding.shares = position["shares"]
        holding.cost_basis = position["cost_basis"]
        holding.realized_gain_loss = position["realized_gain_loss"]
        holding.total_invested = position["total_invested"]
        if position["first_buy_date"]:
            holding.first_buy_date = position["first_buy_date"]
        if holding.purchase_date is None:
            holding.purchase_date = position["first_buy_date"] or item.date
        holding.asset_type = asset_type

        saved_count += 1

    await db.commit()
    return {"ok": True, "count": saved_count}


# --------------------------------------------------------------------------
# Bulk Import: Parse free-form text via LLM
# --------------------------------------------------------------------------

class BulkImportTextIn(BaseModel):
    text: str = Field(..., min_length=10, max_length=5000)


class ParsedHolding(BaseModel):
    ticker: str
    shares: float
    cost_basis: float
    purchase_date: str


class BulkParseResponse(BaseModel):
    holdings: list[ParsedHolding]


@router.post("/holdings/bulk-parse", response_model=BulkParseResponse)
async def bulk_parse(
    body: BulkImportTextIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    openai_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not openai_key:
        raise HTTPException(400, "OpenAI API key not configured. Please add it in Settings.")

    model_pref = await get_user_api_key(db, user.id, "openai_model")
    model = model_pref or "gpt-4o-mini"

    system_prompt = """You are a data extraction assistant. Extract each portfolio holding into a structured JSON array.

For each holding extract:
- ticker: The stock ticker symbol (uppercase)
- shares: Number of shares (float)
- cost_basis: Price per share at purchase (float, dollars)
- purchase_date: Date in YYYY-MM-DD format

RULES:
- Return ONLY a valid JSON array, no markdown code fences.
- If date is vague like "January 2024", use "2024-01-01"
- If cost basis is total amount (e.g. "$15,000 of AAPL at $150"), compute shares = 15000/150
- If no date, use "2024-01-01"
- Ticker symbols uppercase
- Return [] if nothing parseable

Example: [{"ticker": "AAPL", "shares": 100, "cost_basis": 150.00, "purchase_date": "2024-01-05"}]"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": body.text},
    ]

    try:
        raw = await call_llm(api_key=openai_key, model=model, messages=messages, max_tokens=2000, expect_json=True)
    except Exception as exc:
        raise HTTPException(502, f"LLM request failed: {exc}")

    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()

        parsed = json.loads(cleaned)
        if not isinstance(parsed, list):
            raise ValueError("Expected a JSON array")

        holdings = [
            ParsedHolding(
                ticker=str(item.get("ticker", "")).upper().strip(),
                shares=float(item.get("shares", 0)),
                cost_basis=float(item.get("cost_basis", 0)),
                purchase_date=str(item.get("purchase_date", "2024-01-01")),
            )
            for item in parsed
        ]
        return BulkParseResponse(holdings=holdings)
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning(f"Failed to parse LLM bulk import response: {e}\nRaw: {raw}")
        raise HTTPException(422, f"Could not parse the text. Error: {e}")


class BulkSaveIn(BaseModel):
    holdings: list[HoldingIn]


@router.post("/holdings/bulk-save")
async def bulk_save(
    body: BulkSaveIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not body.holdings:
        raise HTTPException(400, "No holdings provided")

    portfolio = await _get_or_create_portfolio(db, user.id)
    count = 0
    for h in body.holdings:
        asset_type = h.asset_type.upper() if hasattr(h, "asset_type") and h.asset_type else "STOCK"
        holding = PortfolioHolding(
            portfolio_id=portfolio.id,
            ticker=h.ticker.upper().strip(),
            asset_type=asset_type if asset_type in ASSET_TYPES else "STOCK",
            shares=h.shares,
            cost_basis=h.cost_basis,
            purchase_date=h.purchase_date,
        )
        db.add(holding)
        count += 1

    await db.commit()
    return {"ok": True, "count": count}


# --------------------------------------------------------------------------
# POST /api/portfolio/analyze-exit
# --------------------------------------------------------------------------

class AnalyzeExitIn(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=20)
    persona: str = Field(...)


@router.post("/analyze-exit")
async def analyze_exit(
    body: AnalyzeExitIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    openai_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not openai_key:
        raise HTTPException(400, "OpenAI API key not configured. Please add it in Settings.")

    model_pref = await get_user_api_key(db, user.id, "openai_model")
    model = model_pref or "gpt-4o-mini"

    result = await analyze_exit_strategy(
        ticker=body.ticker.upper(),
        persona=body.persona,
        openai_key=openai_key,
        model=model,
    )

    if "error" in result:
        raise HTTPException(502, result["error"])

    return result


# --------------------------------------------------------------------------
# Enriched views: dividends / fundamentals / technical
# --------------------------------------------------------------------------

@router.get("/dividends")
async def get_dividend_view(
    force_refresh: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Dividend data for all portfolio holdings. Cached 24 h per ticker."""
    portfolio = await _get_or_create_portfolio(db, user.id)
    tickers = list(set(h.ticker for h in portfolio.holdings))
    if not tickers:
        return []
    data = await fetch_enriched_view(db, tickers, "dividends", force_refresh=force_refresh)
    # Cached prices (15-min TTL) to compute yield and market value from current market price
    price_data = await fetch_prices_cached(db, tickers, force_refresh=force_refresh)
    shares_map = {h.ticker: h.shares for h in portfolio.holdings}
    result = []
    for ticker in tickers:
        row = dict(data.get(ticker, {"ticker": ticker}))
        shares = shares_map.get(ticker, 0)
        row["shares"] = shares
        rate = row.get("annual_dividend_rate")
        row["annual_income"] = round(shares * rate, 2) if rate and rate > 0 else 0.0
        # Live price: override yield and compute market value
        current_price = price_data.get(ticker, {}).get("price")
        if current_price and current_price > 0:
            row["market_value"] = round(shares * current_price, 2)
            if rate and rate > 0:
                row["dividend_yield_pct"] = round(rate / current_price * 100, 2)
        else:
            row["market_value"] = None
        result.append(row)
    return result


@router.get("/fundamentals")
async def get_fundamental_view(
    force_refresh: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fundamental data for all portfolio holdings. Cached 24 h per ticker."""
    portfolio = await _get_or_create_portfolio(db, user.id)
    tickers = list(set(h.ticker for h in portfolio.holdings))
    if not tickers:
        return []
    data = await fetch_enriched_view(db, tickers, "fundamentals", force_refresh=force_refresh)
    return list(data.values())


@router.get("/technical")
async def get_technical_view(
    force_refresh: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Technical + analyst data for all portfolio holdings. Cached 1 h per ticker."""
    portfolio = await _get_or_create_portfolio(db, user.id)
    tickers = list(set(h.ticker for h in portfolio.holdings))
    if not tickers:
        return []
    data = await fetch_enriched_view(db, tickers, "technical", force_refresh=force_refresh)
    return list(data.values())


@router.get("/sectors")
async def get_sectors(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return sector/industry/company/asset_type metadata for all holdings.
    Reads fundamentals cache first; refreshes stale entries (those missing
    the sector key from an old cache record); falls back to asset_type for
    non-equity securities that yfinance never assigns a sector to.
    TTL 24 h — same as fundamentals cache."""
    portfolio = await _get_or_create_portfolio(db, user.id)
    holdings = portfolio.holdings
    tickers = list(set(h.ticker for h in holdings))
    if not tickers:
        return []

    # Map ticker → asset_type from DB (authoritative)
    asset_type_map: dict[str, str] = {h.ticker: (h.asset_type or "STOCK") for h in holdings}

    data = await fetch_enriched_view(db, tickers, "fundamentals", force_refresh=False)

    # Stale detection — two cases:
    #   1. "sector" key entirely absent (old cache before we added the field)
    #   2. "sector" key present but empty for what should be an equity —
    #      yfinance always fills sector for STOCK/EQUITY; empty means a failed
    #      or partial fetch (e.g. FISV, BRKB returned "" previously).
    _equity_quote_types = {"EQUITY", ""}  # quote_types where sector should exist
    stale = [
        t for t, d in data.items()
        if "error" not in d and (
            "sector" not in d                                               # missing key
            or (
                not d.get("sector")                                         # empty sector
                and asset_type_map.get(t, "STOCK") == "STOCK"              # stored as stock
                and (d.get("quote_type") or "").upper()
                    not in {"ETF", "MUTUALFUND", "CRYPTOCURRENCY", "FUTURE", "INDEX"}
            )
        )
    ]
    if stale:
        fresh = await fetch_enriched_view(db, stale, "fundamentals", force_refresh=True)
        data.update(fresh)

    # yfinance quote_type → canonical asset_type.
    # This auto-corrects holdings that were stored with the wrong type
    # (e.g. an ETF accidentally added as STOCK).
    _QT_TO_AT: dict[str, str] = {
        "ETF":            "ETF",
        "MUTUALFUND":     "MUTUAL_FUND",
        "CRYPTOCURRENCY": "CRYPTO",
        "FUTURE":         "OTHER",
        "INDEX":          "OTHER",
    }

    # Sector display label for non-equity asset types.
    _AT_SECTOR: dict[str, str] = {
        "ETF":         "ETF",
        "MUTUAL_FUND": "Mutual Fund",
        "CRYPTO":      "Crypto",
        "BOND":        "Bond",
        "CASH":        "Cash",
        "OPTION":      "Option",
        "OTHER":       "Other",
    }

    result = []
    for t, d in data.items():
        db_at = asset_type_map.get(t, "STOCK")
        qt    = (d.get("quote_type") or "").upper()
        # yfinance-derived type takes precedence for known non-equity quote types
        effective_at = _QT_TO_AT.get(qt, db_at)

        sector = d.get("sector") or ""
        if not sector:
            # Funds have no GICS sector — group them by their real Morningstar
            # category (e.g. "Foreign Large Blend") rather than lumping every
            # ETF / mutual fund under a single "ETF" bucket.
            if effective_at in ("ETF", "MUTUAL_FUND"):
                sector = d.get("fund_category") or _AT_SECTOR.get(effective_at) or ""
            else:
                sector = _AT_SECTOR.get(effective_at) or ""

        result.append({
            "ticker":       t,
            "sector":       sector,
            "industry":     d.get("industry") or "",
            "company_name": d.get("company_name") or t,
            "asset_type":   effective_at,   # corrected by yfinance when possible
            "fund_category": d.get("fund_category") or "",
        })

    return result


# --------------------------------------------------------------------------
# POST /api/portfolio/holdings/{ticker}/analyze  — AI deep analysis + chat
# --------------------------------------------------------------------------

class HoldingAnalyzeIn(BaseModel):
    question: str | None = None          # None / empty → generate initial analysis
    history: list[dict] = []             # [{role, content}] conversation so far
    holding_context: dict = {}           # P&L snapshot passed from frontend


def _fmt_val(v, suffix="") -> str:
    if v is None:
        return "N/A"
    return f"{v}{suffix}"


@router.post("/holdings/{ticker}/analyze")
async def analyze_holding(
    ticker: str,
    body: HoldingAnalyzeIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Stream a full LLM analysis of a holding, including all cached enrichment data."""
    openai_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not openai_key:
        raise HTTPException(400, "OpenAI API key not configured. Please add it in Settings.")

    model_pref = await get_user_api_key(db, user.id, "openai_model")
    model = model_pref or "gpt-4o-mini"

    t = ticker.upper().strip()

    # Pull all three enrichment caches in parallel
    div_data  = await get_cached(db, f"portfolio:dividends:{t}") or {}
    fund_data = await get_cached(db, f"portfolio:fundamentals:{t}") or {}
    tech_data = await get_cached(db, f"portfolio:technical:{t}") or {}

    hc = body.holding_context

    # ── Position block ──────────────────────────────────────────────────
    position_block = f"""POSITION  ({t} — {fund_data.get('company_name') or t})
  Sector/Industry : {fund_data.get('sector') or 'N/A'} / {fund_data.get('industry') or 'N/A'}
  Shares          : {_fmt_val(hc.get('shares'))}
  Avg Cost        : ${_fmt_val(hc.get('cost_basis'))}
  Current Price   : ${_fmt_val(hc.get('current_price'))}
  Market Value    : ${_fmt_val(hc.get('market_value'))}
  Unrealized P&L  : ${_fmt_val(hc.get('unrealized_gain_loss'))} ({_fmt_val(hc.get('unrealized_gain_loss_pct'))}%)
  Realized P&L    : ${_fmt_val(hc.get('realized_gain_loss'))}
  Total Return    : {_fmt_val(hc.get('total_return_pct'))}%
  Ann. Return     : {_fmt_val(hc.get('annualized_return_pct'))}%
  Today           : {_fmt_val(hc.get('day_change_pct'))}%
  Portfolio Wt.   : {_fmt_val(hc.get('weight_pct'))}%"""

    # ── Dividend block ──────────────────────────────────────────────────
    dividend_block = f"""DIVIDENDS
  Yield           : {_fmt_val(div_data.get('dividend_yield_pct'))}%
  Annual Rate     : ${_fmt_val(div_data.get('annual_dividend_rate'))}
  Payout Ratio    : {_fmt_val(div_data.get('payout_ratio_pct'))}%
  5yr Avg Yield   : {_fmt_val(div_data.get('five_yr_avg_yield_pct'))}%
  Ex-Div Date     : {_fmt_val(div_data.get('ex_dividend_date'))}""" if div_data else "DIVIDENDS: No data"

    # ── Fundamentals block ──────────────────────────────────────────────
    is_fund = (fund_data.get("quote_type") or "").upper() in {"ETF", "MUTUALFUND"}
    if is_fund:
        fundamental_block = f"""FUND FUNDAMENTALS
  AUM             : ${_fmt_val(fund_data.get('aum_b'))}B
  Expense Ratio   : {_fmt_val(fund_data.get('net_expense_ratio_pct'))}%
  Fund Yield      : {_fmt_val(fund_data.get('fund_yield_pct'))}%
  YTD Return      : {_fmt_val(fund_data.get('ytd_return_pct'))}%
  3yr Return      : {_fmt_val(fund_data.get('three_yr_return_pct'))}%
  5yr Return      : {_fmt_val(fund_data.get('five_yr_return_pct'))}%
  Turnover        : {_fmt_val(fund_data.get('fund_turnover_pct'))}%
  Category        : {fund_data.get('fund_category') or 'N/A'}
  Fund Family     : {fund_data.get('fund_family') or 'N/A'}
  M* Rating       : {_fmt_val(fund_data.get('morningstar_overall_rating'))}/5"""
    else:
        fundamental_block = f"""FUNDAMENTALS
  Market Cap      : ${_fmt_val(fund_data.get('market_cap_b'))}B
  Trailing P/E    : {_fmt_val(fund_data.get('trailing_pe'))}x
  Forward P/E     : {_fmt_val(fund_data.get('forward_pe'))}x
  PEG Ratio       : {_fmt_val(fund_data.get('peg_ratio'))}
  EPS (TTM)       : ${_fmt_val(fund_data.get('eps_ttm'))}
  Fwd EPS         : ${_fmt_val(fund_data.get('eps_forward'))}
  Revenue         : ${_fmt_val(fund_data.get('revenue_b'))}B
  Rev Growth YoY  : {_fmt_val(fund_data.get('revenue_growth_yoy_pct'))}%
  EPS Growth YoY  : {_fmt_val(fund_data.get('earnings_growth_yoy_pct'))}%
  Operating Margin: {_fmt_val(fund_data.get('operating_margin_pct'))}%
  Net Margin      : {_fmt_val(fund_data.get('net_margin_pct'))}%
  ROE             : {_fmt_val(fund_data.get('roe_pct'))}%
  Debt/Equity     : {_fmt_val(fund_data.get('debt_to_equity'))}
  Price/Book      : {_fmt_val(fund_data.get('price_to_book'))}x
  Free Cash Flow  : ${_fmt_val(fund_data.get('free_cashflow_b'))}B"""

    # ── Technical block ─────────────────────────────────────────────────
    technical_block = f"""TECHNICALS
  Beta            : {_fmt_val(tech_data.get('beta'))}
  50d MA          : ${_fmt_val(tech_data.get('ma_50d'))} (vs price: {_fmt_val(tech_data.get('vs_50d_pct'))}%)
  200d MA         : ${_fmt_val(tech_data.get('ma_200d'))} (vs price: {_fmt_val(tech_data.get('vs_200d_pct'))}%)
  52w High/Low    : ${_fmt_val(tech_data.get('week52_high'))} / ${_fmt_val(tech_data.get('week52_low'))}
  52w Range Pos.  : {_fmt_val(tech_data.get('range_position_pct'))}%
  52w Change      : {_fmt_val(tech_data.get('week52_change_pct'))}%
  Analyst Target  : ${_fmt_val(tech_data.get('analyst_target_mean'))} (upside: {_fmt_val(tech_data.get('analyst_upside_pct'))}%)
  Target High/Low : ${_fmt_val(tech_data.get('analyst_target_high'))} / ${_fmt_val(tech_data.get('analyst_target_low'))}
  Recommendation  : {tech_data.get('recommendation') or 'N/A'} ({_fmt_val(tech_data.get('analyst_count'))} analysts)
  Rec. Score      : {_fmt_val(tech_data.get('recommendation_mean'))} (1=strong buy, 5=strong sell)""" if tech_data else "TECHNICALS: No data"

    system_prompt = f"""You are a sharp, opinionated financial analyst reviewing a portfolio holding.
You have full access to the investor's position data, dividends, fundamentals, and technicals.

{position_block}

{dividend_block}

{fundamental_block}

{technical_block}

INSTRUCTIONS:
- Be concise but insightful. Use bullet points liberally.
- Highlight what's working, what's a risk, and what to watch.
- Reference specific numbers from the data above.
- For follow-up questions, answer directly without re-summarising the whole position.
- Use markdown formatting (bold, bullets, headers) for readability.
- Do not disclaim that you are an AI or that this is not financial advice — the investor understands this."""

    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    if body.history:
        messages.extend(body.history)

    if not body.history:
        # First load: generate a comprehensive initial analysis
        messages.append({
            "role": "user",
            "content": (
                "Give me a comprehensive analysis of this position covering:\n"
                "1. **Position performance** — how is this holding doing for me?\n"
                "2. **Fundamental health** — is the business/fund performing well?\n"
                "3. **Technical outlook** — what do price levels and analyst targets say?\n"
                "4. **Key risks** — what could go wrong?\n"
                "5. **What to watch** — top 2-3 catalysts or data points to track."
            )
        })
    elif body.question:
        messages.append({"role": "user", "content": body.question})

    try:
        raw = await call_llm(
            api_key=openai_key, model=model, messages=messages, max_tokens=1800
        )
    except Exception as exc:
        raise HTTPException(502, f"LLM request failed: {exc}")

    return {"content": raw, "role": "assistant"}


# --------------------------------------------------------------------------
# GET /api/portfolio/events — upcoming earnings & ex-dividend calendar
# --------------------------------------------------------------------------

@router.get("/events")
async def get_portfolio_events(
    days_ahead: int = 45,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return upcoming earnings and ex-dividend dates for all holdings (cache-backed)."""
    portfolio = await _get_or_create_portfolio(db, user.id)
    holdings = portfolio.holdings
    if not holdings:
        return []

    shares_map = {h.ticker: h.shares for h in holdings}
    today = datetime.date.today()
    cutoff = today + datetime.timedelta(days=days_ahead)
    events: list[dict] = []

    for holding in holdings:
        t = holding.ticker

        # Earnings from technical cache
        tech = await get_cached(db, f"portfolio:technical:{t}")
        if tech and tech.get("earnings_date"):
            try:
                ed = datetime.date.fromisoformat(tech["earnings_date"])
                if today <= ed <= cutoff:
                    events.append({
                        "ticker": t,
                        "type": "earnings",
                        "date": tech["earnings_date"],
                        "days_until": (ed - today).days,
                        "label": "Earnings",
                        "company_name": tech.get("company_name") or t,
                    })
            except (ValueError, TypeError):
                pass

        # Ex-dividend from dividend cache
        div = await get_cached(db, f"portfolio:dividends:{t}")
        if div and div.get("ex_dividend_date"):
            try:
                xd = datetime.date.fromisoformat(div["ex_dividend_date"])
                if today <= xd <= cutoff:
                    rate = div.get("annual_dividend_rate")
                    shares = shares_map.get(t, 0)
                    # Estimate per-quarter income
                    est_income = round(shares * rate / 4, 2) if rate and shares else None
                    events.append({
                        "ticker": t,
                        "type": "ex_div",
                        "date": div["ex_dividend_date"],
                        "days_until": (xd - today).days,
                        "label": "Ex-Div",
                        "estimated_income": est_income,
                        "company_name": div.get("company_name") or t,
                    })
            except (ValueError, TypeError):
                pass

    return sorted(events, key=lambda e: e["date"])


# --------------------------------------------------------------------------
# POST /api/portfolio/copilot — whole-portfolio AI advisor
# --------------------------------------------------------------------------

class PortfolioCopilotIn(BaseModel):
    question: str | None = None
    history: list[dict] = []


@router.post("/copilot")
async def portfolio_copilot(
    body: PortfolioCopilotIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Full-portfolio AI copilot with access to all holdings, enrichment, and risk data."""
    openai_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not openai_key:
        raise HTTPException(400, "OpenAI API key not configured. Please add it in Settings.")

    model_pref = await get_user_api_key(db, user.id, "openai_model")
    model = model_pref or "gpt-4o-mini"

    portfolio = await _get_or_create_portfolio(db, user.id)
    if not portfolio.holdings:
        raise HTTPException(400, "Portfolio is empty.")

    tickers = list(set(h.ticker for h in portfolio.holdings))
    price_data = await fetch_prices_cached(db, tickers)

    # Pull all enrichment caches (best-effort; empty dict if not yet cached)
    div_map: dict[str, dict] = {}
    fund_map: dict[str, dict] = {}
    tech_map: dict[str, dict] = {}
    for t in tickers:
        d = await get_cached(db, f"portfolio:dividends:{t}")
        if d: div_map[t] = d
        f = await get_cached(db, f"portfolio:fundamentals:{t}")
        if f: fund_map[t] = f
        tc = await get_cached(db, f"portfolio:technical:{t}")
        if tc: tech_map[t] = tc

    holdings_map = {h.ticker: h for h in portfolio.holdings}

    # ── Build per-holding data ──────────────────────────────────────────────
    rows: list[dict] = []
    total_mv = 0.0
    total_cost = 0.0
    total_income = 0.0
    sector_bucket: dict[str, float] = {}

    for t in tickers:
        h = holdings_map[t]
        pd = price_data.get(t, {})
        price = pd.get("price")
        mv = h.shares * price if price else None
        cost = round(h.shares * h.cost_basis, 2)
        unreal = round(mv - cost, 2) if mv is not None else None
        unreal_pct = round(unreal / cost * 100, 1) if (unreal is not None and cost > 0) else None
        day_chg = pd.get("day_change_pct")

        fund = fund_map.get(t, {})
        div = div_map.get(t, {})
        tech = tech_map.get(t, {})

        sector = fund.get("sector") or ""
        qt = (fund.get("quote_type") or "").upper()
        if not sector:
            sector = "ETF" if qt == "ETF" else ("Fund" if qt == "MUTUALFUND" else "—")

        rate = div.get("annual_dividend_rate") or 0
        income = round(h.shares * rate, 2)

        if mv: total_mv += mv
        total_cost += cost
        total_income += income
        if sector and mv:
            sector_bucket[sector] = sector_bucket.get(sector, 0) + mv

        rows.append({
            "t": t, "company": fund.get("company_name") or t,
            "sector": sector, "asset_type": (h.asset_type or "STOCK"),
            "shares": h.shares, "cb": h.cost_basis,
            "mv": mv, "cost": cost, "unreal": unreal, "unreal_pct": unreal_pct,
            "day_chg": day_chg, "income": income,
            "beta": tech.get("beta"), "rec": tech.get("recommendation"),
            "upside": tech.get("analyst_upside_pct"),
            "vs50": tech.get("vs_50d_pct"), "vs200": tech.get("vs_200d_pct"),
            "earnings_date": tech.get("earnings_date"),
            "ex_div_date": div.get("ex_dividend_date"),
            "pe": fund.get("trailing_pe"), "fwd_pe": fund.get("forward_pe"),
            "rev_grw": fund.get("revenue_growth_yoy_pct"),
            "eps_grw": fund.get("earnings_growth_yoy_pct"),
            "net_margin": fund.get("net_margin_pct"),
            "fund_cat": fund.get("fund_category"),
            "ytd_ret": fund.get("ytd_return_pct"),
            "fund_yield": fund.get("fund_yield_pct"),
        })

    rows.sort(key=lambda r: r["mv"] or 0, reverse=True)

    def _v(v, suffix="", prefix="", d=1):
        return "N/A" if v is None else f"{prefix}{v:.{d}f}{suffix}"

    # ── Holdings table ──────────────────────────────────────────────────────
    holding_lines = []
    for r in rows:
        wt = round(r["mv"] / total_mv * 100, 1) if r["mv"] and total_mv else None
        holding_lines.append(
            f"  {r['t']:<7} {r['company'][:22]:<22} {r['sector'][:14]:<14} "
            f"wt={_v(wt,'%')} mv={_v(r['mv'],'',prefix='$',d=0)} "
            f"unreal={_v(r['unreal_pct'],'%')} day={_v(r['day_chg'],'%')}"
        )

    # ── Sector breakdown ────────────────────────────────────────────────────
    sector_lines = sorted(sector_bucket.items(), key=lambda x: -x[1])
    sector_text = " | ".join(
        f"{s}: {v/total_mv*100:.0f}%" for s, v in sector_lines[:7]
    ) if total_mv > 0 else "N/A"

    # ── Dividend lines ──────────────────────────────────────────────────────
    div_lines = [
        f"  {r['t']}: ${r['income']:,.0f}/yr  yield {_v(div_map.get(r['t'],{}).get('dividend_yield_pct'),'%')}  ex-div {r['ex_div_date'] or 'N/A'}"
        for r in rows if r["income"] > 0
    ]

    # ── Technical signals ───────────────────────────────────────────────────
    tech_lines = [
        f"  {r['t']}: beta={_v(r['beta'])} vs50={_v(r['vs50'],'%')} vs200={_v(r['vs200'],'%')} "
        f"rec={r['rec'] or 'N/A'} upside={_v(r['upside'],'%')} earnings={r['earnings_date'] or 'N/A'}"
        for r in rows[:12]
        if any(v is not None for v in [r["beta"], r["rec"], r["upside"]])
    ]

    # ── Fund / ETF lines ────────────────────────────────────────────────────
    fund_lines = [
        f"  {r['t']}: cat={r['fund_cat'] or 'N/A'} YTD={_v(r['ytd_ret'],'%')} yield={_v(r['fund_yield'],'%')}"
        for r in rows
        if r["asset_type"] in ("ETF", "MUTUAL_FUND") or (fund_map.get(r["t"],{}).get("quote_type","").upper() in ("ETF","MUTUALFUND"))
    ]

    # ── Equity fundamentals ──────────────────────────────────────────────────
    eq_lines = [
        f"  {r['t']}: P/E={_v(r['pe'],'x')} FwdP/E={_v(r['fwd_pe'],'x')} RevGrw={_v(r['rev_grw'],'%')} EPSGrw={_v(r['eps_grw'],'%')} NetMgn={_v(r['net_margin'],'%')}"
        for r in rows[:10]
        if r["asset_type"] not in ("ETF","MUTUAL_FUND") and any(v is not None for v in [r["pe"], r["rev_grw"]])
    ]

    unreal_total = total_mv - total_cost if total_mv else 0
    portfolio_yield = round(total_income / total_mv * 100, 2) if total_mv > 0 else 0

    context = f"""PORTFOLIO OVERVIEW
==================
Positions: {len(tickers)}  |  Market Value: ${total_mv:,.0f}  |  Cost Basis: ${total_cost:,.0f}
Unrealized P&L: ${unreal_total:,.0f} ({_v(unreal_total/total_cost*100,'%') if total_cost else 'N/A'})
Annual Dividend Income: ${total_income:,.0f}  |  Portfolio Yield: {portfolio_yield:.2f}%
Sector Allocation: {sector_text}

HOLDINGS (largest to smallest)
================================
  {'TICKER':<7} {'COMPANY':<22} {'SECTOR':<14} {'WT%':<9} {'MKT_VAL':<12} {'UNREAL%':<10} DAY%
{chr(10).join(holding_lines)}

DIVIDENDS & INCOME (payers only)
==================================
{chr(10).join(div_lines) if div_lines else '  No significant dividend payers in portfolio'}

EQUITY FUNDAMENTALS (top positions)
======================================
{chr(10).join(eq_lines) if eq_lines else '  Fundamental data not yet cached — run Refresh All'}

FUND / ETF PERFORMANCE
========================
{chr(10).join(fund_lines) if fund_lines else '  No funds or ETFs in portfolio'}

TECHNICALS & ANALYST SIGNALS
================================
{chr(10).join(tech_lines) if tech_lines else '  Technical data not yet cached — run Refresh All'}"""

    system_prompt = f"""You are an institutional-grade portfolio advisor embedded in a personal investment platform. You have complete, real-time visibility into this investor's portfolio.

{context}

ADVISOR GUIDELINES:
- Be direct, specific, and actionable — reference tickers and numbers explicitly
- Think like a portfolio manager: concentration risk, factor tilts, sector exposure, liquidity, correlation
- Surface non-obvious connections (e.g. two holdings with same supplier chain)
- Flag technical/fundamental divergences worth investigating
- Prioritise upcoming catalysts (earnings, ex-div, macro events)
- For follow-ups, answer the specific question — don't re-summarise the whole portfolio
- Use markdown (bullets, **bold**, headers) for readability
- Skip disclaimers — the user understands this is AI-assisted analysis"""

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    if body.history:
        messages.extend(body.history)

    if not body.history and not body.question:
        messages.append({"role": "user", "content": (
            "Give me a concise portfolio briefing covering:\n"
            "1. **Top concentration or risk concerns** — specific tickers\n"
            "2. **Standout performers & laggards** — what's driving them\n"
            "3. **Upcoming catalysts** — earnings, ex-div, macro exposures\n"
            "4. **2-3 specific action items** worth considering this week\n"
            "Be direct and portfolio-specific. 4-6 bullets per section."
        )})
    elif body.question:
        messages.append({"role": "user", "content": body.question})

    try:
        raw = await call_llm(api_key=openai_key, model=model, messages=messages, max_tokens=1500)
    except Exception as exc:
        raise HTTPException(502, f"LLM request failed: {exc}")

    return {"content": raw, "role": "assistant"}


# --------------------------------------------------------------------------
# GET /api/portfolio/brief — the "Today" daily briefing (rebuilt front door)
# --------------------------------------------------------------------------

_TTL_BRIEF_NARRATIVE = 3 * 3600  # 3h; busted on Refresh All / force_refresh
_TTL_BRIEF_NEWS = 2 * 3600  # 2h; one headline per top mover
_TTL_BRIEF_SNAPSHOT = 90 * 86400  # 90d; the since-last-visit baseline


def _brief_mover(r: dict, news: dict | None = None) -> dict:
    return {
        "ticker": r["t"],
        "company": r["company"],
        "day_change_pct": round(r["day_chg_pct"], 2) if r["day_chg_pct"] is not None else None,
        "day_pnl": round(r["day_pnl"], 2) if r["day_pnl"] is not None else None,
        "news": news,
    }


def _humanize_since(base_date_iso: str, today: datetime.date) -> str:
    """Turn a baseline ISO date into a friendly 'since ...' phrase."""
    try:
        bd = datetime.date.fromisoformat(base_date_iso)
    except (ValueError, TypeError):
        return base_date_iso
    days = (today - bd).days
    if days <= 0:
        return "earlier today"
    if days == 1:
        return "yesterday"
    if days <= 7:
        return f"{days} days ago"
    return bd.strftime("%b ") + str(bd.day)


def _fetch_ticker_news_sync(ticker: str, company_name: str = "") -> list[dict]:
    """Light, relevance-filtered news pull for a single ticker (top 2 headlines).

    Deliberately avoids yf .info (heavy) — builds the relevance filter from the
    ticker symbol plus the company name we already have cached.
    """
    import yfinance as yf
    try:
        raw = yf.Ticker(ticker).news or []
    except Exception as exc:
        logger.warning("Brief news fetch failed for %s: %s", ticker, exc)
        return []

    filter_words = {ticker.lower()}
    for word in (company_name or "").split():
        w = word.strip(",.()'-").lower()
        if len(w) >= 3 and w not in {"inc", "corp", "ltd", "the", "and", "llc",
                                     "plc", "co.", "group", "fund", "etf", "trust"}:
            filter_words.add(w)

    out: list[dict] = []
    for item in raw[:20]:
        content = item.get("content", item) if isinstance(item, dict) else item
        if not isinstance(content, dict):
            continue
        title = content.get("title", "") or ""
        summary = content.get("summary", "") or ""
        if not title:
            continue
        combined = (title + " " + summary).lower()
        if filter_words and not any(fw in combined for fw in filter_words):
            continue
        provider = content.get("provider") or {}
        publisher = provider.get("displayName", "") if isinstance(provider, dict) else ""
        click = content.get("clickThroughUrl") or {}
        url = click.get("url", "") if isinstance(click, dict) else ""
        pub_date = content.get("pubDate", "") or ""
        out.append({
            "title": title[:160],
            "publisher": publisher[:60],
            "url": url,
            "published": pub_date[:25],
        })
        if len(out) >= 2:
            break
    return out


async def _fetch_ticker_news(db: AsyncSession, ticker: str, company_name: str = "",
                             force_refresh: bool = False) -> dict | None:
    """Return the single most relevant cached headline for a ticker (or None)."""
    import asyncio
    key = f"portfolio:news:{ticker}"
    if not force_refresh:
        cached = await get_cached(db, key)
        if isinstance(cached, dict) and "items" in cached:
            items = cached["items"]
            return items[0] if items else None
    items = await asyncio.to_thread(_fetch_ticker_news_sync, ticker, company_name)
    await set_cached(db, key, {"items": items}, ttl_seconds=_TTL_BRIEF_NEWS)
    return items[0] if items else None


def _compute_brief_delta(
    baseline: dict | None,
    rows_by_t: dict,
    total_mv: float,
    sector_bucket: dict,
    today: datetime.date,
) -> dict | None:
    """Diff the live book against a prior-day baseline snapshot.

    `baseline` is a slot dict: {"date": iso, "snapshot": {total_mv, sectors, holdings}}.
    Returns a delta block, or None if there's nothing to compare against.
    """
    if not isinstance(baseline, dict):
        return None
    base_date = baseline.get("date")
    snap = baseline.get("snapshot") or {}
    base_mv = snap.get("total_mv")
    base_holdings = snap.get("holdings") or {}
    base_sectors = snap.get("sectors") or {}
    if not base_date or base_mv is None:
        return None

    # Per-ticker price contribution (price-driven, common holdings only) so the
    # headline number reflects the market moving the book — not added/removed cash.
    contributors: list[dict] = []
    crossings: list[dict] = []
    market_change = 0.0
    for t, r in rows_by_t.items():
        bh = base_holdings.get(t)
        if not bh:
            continue
        base_price = bh.get("price")
        price_now = r.get("price")
        shares_now = r.get("shares")
        cost_basis = r.get("cost_basis")
        if base_price is not None and price_now is not None and shares_now:
            contrib = shares_now * (price_now - base_price)
            market_change += contrib
            pct_move = ((price_now - base_price) / base_price * 100) if base_price else None
            contributors.append({
                "ticker": t,
                "company": r.get("company") or t,
                "amount": round(contrib, 2),
                "pct_move": round(pct_move, 2) if pct_move is not None else None,
            })
        if base_price is not None and price_now is not None and cost_basis:
            was_gain = base_price >= cost_basis
            now_gain = price_now >= cost_basis
            if was_gain != now_gain:
                crossings.append({
                    "ticker": t,
                    "company": r.get("company") or t,
                    "direction": "into_gain" if now_gain else "into_loss",
                    "price": round(price_now, 2),
                    "cost_basis": round(cost_basis, 2),
                })

    contributors.sort(key=lambda c: -abs(c["amount"]))
    market_change_pct = (market_change / base_mv * 100) if base_mv > 0 else None

    # Sector weight drift (percentage points of book weight).
    drifts: list[dict] = []
    for s in set(sector_bucket) | set(base_sectors):
        now_w = (sector_bucket.get(s, 0) / total_mv * 100) if total_mv > 0 else 0
        base_w = (base_sectors.get(s, 0) / base_mv * 100) if base_mv > 0 else 0
        drifts.append({"sector": s, "drift_pp": round(now_w - base_w, 2), "now_weight": round(now_w, 1)})
    drifts.sort(key=lambda d: -abs(d["drift_pp"]))
    top_drift = drifts[0] if drifts and abs(drifts[0]["drift_pp"]) >= 0.5 else None

    added = [t for t in rows_by_t if t not in base_holdings]
    removed = [t for t in base_holdings if t not in rows_by_t]

    return {
        "baseline_date": base_date,
        "since_label": _humanize_since(base_date, today),
        "market_change": round(market_change, 2),
        "market_change_pct": round(market_change_pct, 2) if market_change_pct is not None else None,
        "top_contributors": contributors[:3],
        "crossings": crossings[:4],
        "sector_drift": top_drift,
        "positions_added": added[:5],
        "positions_removed": removed[:5],
    }


async def _generate_brief_narrative(
    db: AsyncSession,
    user: User,
    facts: str,
    force_refresh: bool,
) -> str | None:
    """Return a cached 2-3 sentence plain-English brief, or None if no LLM key.

    Only the prose is cached (the numbers around it are recomputed live), so this
    costs at most one small LLM call every few hours rather than one per page load.
    """
    key = f"portfolio:brief_narrative:{user.id}"
    if not force_refresh:
        cached = await get_cached(db, key)
        if isinstance(cached, dict) and cached.get("text"):
            return cached["text"]

    openai_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not openai_key:
        return None
    model = (await get_user_api_key(db, user.id, "openai_model")) or "gpt-4o-mini"

    messages = [
        {"role": "system", "content": (
            "You are a sharp personal portfolio analyst writing the one-paragraph lead of a daily briefing. "
            "The investor can ALREADY SEE tiles for total value, today's $/%, all-time P&L and dividend yield — "
            "anything tagged '[Context — already on screen]' must NOT be restated. "
            "Your job is to tell them what they can't see at a glance: what changed since they last looked, why, "
            "and the one thing to watch. Lead with the single most important item in the data — a gain/loss "
            "crossing, the biggest contributor to the move, a benchmark divergence, or a news catalyst on a mover. "
            "Be specific with tickers and figures from the data. If the day is genuinely quiet, say so plainly in "
            "one sentence instead of inventing drama. 2-3 sentences, conversational but precise, address the "
            "investor as 'you'. No markdown, no bullet points, no greetings, no disclaimers."
        )},
        {"role": "user", "content": f"Today's portfolio intelligence:\n\n{facts}\n\nWrite the briefing lead."},
    ]
    try:
        text = await call_llm(api_key=openai_key, model=model, messages=messages, max_tokens=220, temperature=0.7)
    except Exception as exc:
        logger.warning("Brief narrative LLM failed: %s", exc)
        return None
    text = (text or "").strip()
    if text:
        await set_cached(db, key, {"text": text}, ttl_seconds=_TTL_BRIEF_NARRATIVE)
    return text or None


@router.get("/brief")
async def get_portfolio_brief(
    force_refresh: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Daily portfolio briefing — deterministic headline + cached LLM narrative.

    Headline figures, movers and upcoming events are recomputed live from the
    already-cached price + enrichment data on every call, so numbers stay as fresh
    as the 15-min price cache. Only the prose narrative is cached (3h) to avoid an
    LLM round-trip on every page load.
    """
    portfolio = await _get_or_create_portfolio(db, user.id)
    holdings = portfolio.holdings
    if not holdings:
        return {"empty": True}

    tickers = list({h.ticker for h in holdings})
    # SPY rides along in the same batched fetch so the benchmark is essentially free.
    quote_tickers = tickers + (["SPY"] if "SPY" not in tickers else [])
    price_data = await fetch_prices_cached(db, quote_tickers)
    holdings_map = {h.ticker: h for h in holdings}
    today = datetime.date.today()

    rows: list[dict] = []
    total_mv = total_cost = total_income = day_pnl_total = 0.0
    sector_bucket: dict[str, float] = {}

    for t in tickers:
        h = holdings_map[t]
        pd = price_data.get(t, {})
        price = pd.get("price")
        prev_close = pd.get("previous_close")
        day_chg_pct = pd.get("day_change_pct")
        mv = h.shares * price if price else None
        cost = h.shares * h.cost_basis
        day_pnl = h.shares * (price - prev_close) if (price and prev_close) else None

        div = await get_cached(db, f"portfolio:dividends:{t}") or {}
        fund = await get_cached(db, f"portfolio:fundamentals:{t}") or {}
        rate = div.get("annual_dividend_rate") or 0
        income = h.shares * rate

        sector = fund.get("sector") or ""
        if not sector:
            qt = (fund.get("quote_type") or "").upper()
            sector = "ETFs" if qt == "ETF" else ("Funds" if qt == "MUTUALFUND" else "Other")

        if mv:
            total_mv += mv
            sector_bucket[sector] = sector_bucket.get(sector, 0) + mv
        total_cost += cost
        total_income += income
        if day_pnl is not None:
            day_pnl_total += day_pnl

        rows.append({
            "t": t,
            "company": div.get("company_name") or fund.get("company_name") or t,
            "mv": mv, "cost": cost,
            "price": price, "shares": h.shares, "cost_basis": h.cost_basis,
            "day_chg_pct": day_chg_pct, "day_pnl": day_pnl,
            "unreal_pct": ((mv - cost) / cost * 100) if (mv and cost > 0) else None,
            "sector": sector,
        })

    unreal_total = total_mv - total_cost
    unreal_pct = (unreal_total / total_cost * 100) if total_cost > 0 else None
    prev_mv = total_mv - day_pnl_total
    day_pct = (day_pnl_total / prev_mv * 100) if prev_mv > 0 else None
    port_yield = (total_income / total_mv * 100) if total_mv > 0 else None

    # ── v2: benchmark context (portfolio vs S&P 500 today) ──
    spy_q = price_data.get("SPY", {})
    spy_pct = spy_q.get("day_change_pct")
    benchmark = None
    if spy_pct is not None and day_pct is not None:
        benchmark = {
            "symbol": "SPY",
            "day_change_pct": round(spy_pct, 2),
            "vs_portfolio_pp": round(day_pct - spy_pct, 2),
        }

    valued = [r for r in rows if r["day_chg_pct"] is not None and r["mv"]]
    gainers = sorted([r for r in valued if (r["day_chg_pct"] or 0) > 0], key=lambda r: -r["day_chg_pct"])[:3]
    losers = sorted([r for r in valued if (r["day_chg_pct"] or 0) < 0], key=lambda r: r["day_chg_pct"])[:3]

    cutoff = today + datetime.timedelta(days=30)
    upcoming: list[dict] = []
    income_30d = 0.0
    for t in tickers:
        tech = await get_cached(db, f"portfolio:technical:{t}") or {}
        ed_raw = tech.get("earnings_date")
        if ed_raw:
            try:
                ed = datetime.date.fromisoformat(ed_raw)
                if today <= ed <= cutoff:
                    upcoming.append({"ticker": t, "type": "earnings", "date": ed_raw, "days_until": (ed - today).days})
            except (ValueError, TypeError):
                pass
        div = await get_cached(db, f"portfolio:dividends:{t}") or {}
        xd_raw = div.get("ex_dividend_date")
        if xd_raw:
            try:
                xd = datetime.date.fromisoformat(xd_raw)
                if today <= xd <= cutoff:
                    rate = div.get("annual_dividend_rate") or 0
                    sh = holdings_map[t].shares
                    est = round(sh * rate / 4, 2) if (rate and sh) else None
                    if est:
                        income_30d += est
                    upcoming.append({"ticker": t, "type": "ex_div", "date": xd_raw, "days_until": (xd - today).days, "estimated_income": est})
            except (ValueError, TypeError):
                pass
    upcoming.sort(key=lambda e: e["date"])

    # ── v2: since-last-visit delta (snapshot diff) ──
    # The snapshot doc holds two frozen daily slots: `today` (today's opening book)
    # and `prev` (the previous active day's opening book). We always diff against the
    # previous day so the "since ..." story survives same-day reloads, and we roll the
    # slots forward at most once per calendar day.
    rows_by_t = {r["t"]: r for r in rows}
    snapshot_key = f"portfolio:brief_snapshot:{user.id}"
    snap_doc = await get_cached(db, snapshot_key)
    today_iso = today.isoformat()

    already_rolled_today = (
        isinstance(snap_doc, dict)
        and isinstance(snap_doc.get("today"), dict)
        and snap_doc["today"].get("date") == today_iso
    )
    if already_rolled_today:
        baseline_slot = snap_doc.get("prev")  # diff vs the prior day
    else:
        baseline_slot = snap_doc.get("today") if isinstance(snap_doc, dict) else None

    delta = _compute_brief_delta(baseline_slot, rows_by_t, total_mv, sector_bucket, today)

    if not already_rolled_today:
        current_snapshot = {
            "total_mv": round(total_mv, 2),
            "sectors": {s: round(v, 2) for s, v in sector_bucket.items()},
            "holdings": {
                r["t"]: {
                    "price": r["price"],
                    "mv": round(r["mv"], 2) if r["mv"] else None,
                    "cost": round(r["cost"], 2),
                    "shares": r["shares"],
                }
                for r in rows if r["price"] is not None
            },
        }
        await set_cached(db, snapshot_key, {
            "prev": baseline_slot,
            "today": {"date": today_iso, "snapshot": current_snapshot},
        }, ttl_seconds=_TTL_BRIEF_SNAPSHOT)

    # ── v2: one news headline for the single biggest gainer & loser ──
    gainer_news = (
        await _fetch_ticker_news(db, gainers[0]["t"], gainers[0]["company"], force_refresh)
        if gainers else None
    )
    loser_news = (
        await _fetch_ticker_news(db, losers[0]["t"], losers[0]["company"], force_refresh)
        if losers else None
    )

    headline = {
        "total_market_value": round(total_mv, 2),
        "day_change": round(day_pnl_total, 2),
        "day_change_pct": round(day_pct, 2) if day_pct is not None else None,
        "total_unrealized": round(unreal_total, 2),
        "total_unrealized_pct": round(unreal_pct, 2) if unreal_pct is not None else None,
        "annual_income": round(total_income, 2),
        "portfolio_yield_pct": round(port_yield, 2) if port_yield is not None else None,
        "positions": len(tickers),
    }

    # ── Facts for the narrative: lead with what's NEW; tag on-screen tiles ──
    # The LLM only phrases these deterministic facts. Order = priority. The final
    # "[Context ...]" line lists figures already visible on screen so the model can
    # avoid restating them.
    facts_lines: list[str] = []

    # 1) Since-last-visit delta — the headline new information.
    if delta and (delta["market_change"] or delta["crossings"]
                  or delta["top_contributors"] or delta["positions_added"]):
        mc = delta["market_change"]
        line = f"Since {delta['since_label']}, the market moved your book {'+' if mc >= 0 else ''}${mc:,.0f}"
        if delta["market_change_pct"] is not None:
            line += f" ({delta['market_change_pct']:+.1f}%)"
        facts_lines.append(line)
        if delta["top_contributors"]:
            facts_lines.append("Drove that change: " + ", ".join(
                f"{c['ticker']} {'+' if c['amount'] >= 0 else ''}${c['amount']:,.0f}"
                + (f" ({c['pct_move']:+.1f}%)" if c["pct_move"] is not None else "")
                for c in delta["top_contributors"]))
        for cr in delta["crossings"][:3]:
            verb = "flipped into a GAIN" if cr["direction"] == "into_gain" else "slipped into a LOSS"
            facts_lines.append(
                f"{cr['ticker']} {verb} versus your cost (now ${cr['price']:.2f} vs ${cr['cost_basis']:.2f} basis)")
        if delta["sector_drift"]:
            sd = delta["sector_drift"]
            facts_lines.append(
                f"{sd['sector']} weight {'up' if sd['drift_pp'] >= 0 else 'down'} "
                f"{abs(sd['drift_pp']):.1f}pp to {sd['now_weight']:.0f}% of the book")
        if delta["positions_added"]:
            facts_lines.append("New positions since then: " + ", ".join(delta["positions_added"]))

    # 2) News catalysts on the day's biggest movers.
    if gainers and gainer_news:
        facts_lines.append(
            f"Top gainer {gainers[0]['t']} ({gainers[0]['day_chg_pct']:+.1f}% today) — \"{gainer_news['title']}\""
            + (f" ({gainer_news['publisher']})" if gainer_news.get("publisher") else ""))
    if losers and loser_news:
        facts_lines.append(
            f"Top loser {losers[0]['t']} ({losers[0]['day_chg_pct']:+.1f}% today) — \"{loser_news['title']}\""
            + (f" ({loser_news['publisher']})" if loser_news.get("publisher") else ""))

    # 3) Benchmark divergence (only when it's actually divergent).
    if benchmark and abs(benchmark["vs_portfolio_pp"]) >= 0.2:
        diff = benchmark["vs_portfolio_pp"]
        facts_lines.append(
            f"Today you're {'ahead of' if diff >= 0 else 'behind'} the S&P 500 by "
            f"{abs(diff):.2f}pp (SPY {benchmark['day_change_pct']:+.2f}%)")

    # 4) Upcoming catalysts worth a heads-up.
    if income_30d > 0:
        facts_lines.append(f"~${income_30d:,.0f} in dividends land within 30 days")
    near_earn = [e for e in upcoming if e["type"] == "earnings" and e["days_until"] <= 14]
    if near_earn:
        facts_lines.append("Earnings within 2 weeks: " + ", ".join(e["ticker"] for e in near_earn[:6]))

    # 5) Concentration note (only if meaningfully concentrated).
    top_holding = max((r for r in rows if r["mv"]), key=lambda r: r["mv"], default=None)
    if top_holding and total_mv and (top_holding["mv"] / total_mv) >= 0.25:
        facts_lines.append(
            f"Concentration: {top_holding['t']} is {top_holding['mv'] / total_mv * 100:.0f}% of the book")

    substantive = len(facts_lines)

    # Context already visible on screen — the model must NOT restate these.
    ctx = [f"value ${total_mv:,.0f}", f"{len(tickers)} positions"]
    if day_pct is not None:
        ctx.append(f"today {day_pct:+.2f}%")
    if unreal_pct is not None:
        ctx.append(f"all-time {unreal_pct:+.1f}%")
    if port_yield:
        ctx.append(f"yield {port_yield:.2f}%")
    facts_lines.append("[Context — already on screen, do not restate]: " + ", ".join(ctx))

    if substantive == 0:
        facts_lines.insert(
            0, "Quiet day — little has changed since the last check, with no notable movers, "
               "crossings or catalysts. Acknowledge the calm briefly.")

    facts = "\n".join(facts_lines)

    narrative = await _generate_brief_narrative(db, user, facts, force_refresh)

    return {
        "empty": False,
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "narrative": narrative,
        "headline": headline,
        "delta": delta,
        "benchmark": benchmark,
        "movers": {
            "gainers": [_brief_mover(r, gainer_news if i == 0 else None) for i, r in enumerate(gainers)],
            "losers": [_brief_mover(r, loser_news if i == 0 else None) for i, r in enumerate(losers)],
        },
        "upcoming": upcoming[:6],
        "income_next_30d": round(income_30d, 2),
    }


def _fetch_5d_history_sync(ticker: str) -> list[dict]:
    import yfinance as yf
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1mo", interval="1d")
        points = []
        if hist is not None and not hist.empty:
            tail = hist.tail(5)
            for dt, row in tail.iterrows():
                points.append({
                    "date": dt.strftime("%Y-%m-%d"),
                    "price": round(float(row["Close"]), 2)
                })
        return points
    except Exception as exc:
        logger.error(f"Error fetching 5d history for {ticker}: {exc}")
        return []


async def fetch_5d_history(ticker: str) -> list[dict]:
    import asyncio
    return await asyncio.to_thread(_fetch_5d_history_sync, ticker)


@router.get("/highlights", response_model=HighlightResponse)
async def get_highlights(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Identify the top 5 holdings that need review and return them with detailed technicals, news, and an LLM-generated hypothesis."""
    portfolio = await _get_or_create_portfolio(db, user.id)
    if not portfolio.holdings:
        return HighlightResponse(highlights=[], total_portfolio_holdings=0)

    # Filter out holdings that are already tracked (status='active') or dismissed
    tracked_res = await db.execute(
        select(TrackedCompany.ticker)
        .where(TrackedCompany.user_id == user.id, TrackedCompany.status == "active")
    )
    tracked_tickers = {t[0].upper().strip() for t in tracked_res.all()}

    dismissed_res = await db.execute(
        select(PortfolioHighlightDismissal.ticker)
        .where(PortfolioHighlightDismissal.user_id == user.id)
    )
    dismissed_tickers = {t[0].upper().strip() for t in dismissed_res.all()}

    candidates = [
        h for h in portfolio.holdings
        if h.ticker.upper().strip() not in tracked_tickers and h.ticker.upper().strip() not in dismissed_tickers
    ]

    if not candidates:
        return HighlightResponse(highlights=[], total_portfolio_holdings=len(portfolio.holdings))

    tickers = list(set(h.ticker.upper().strip() for h in candidates))
    price_data = await fetch_prices_cached(db, tickers, force_refresh=False)

    total_market_value = 0.0
    holding_prices = {}
    holding_day_changes = {}

    for h in candidates:
        ticker = h.ticker.upper().strip()
        pd = price_data.get(ticker, {})
        price = pd.get("price")
        day_chg = pd.get("day_change_pct")
        if price is not None:
            holding_prices[ticker] = price
            total_market_value += h.shares * price
        if day_chg is not None:
            holding_day_changes[ticker] = day_chg

    scored_candidates = []
    for h in candidates:
        ticker = h.ticker.upper().strip()
        price = holding_prices.get(ticker)
        day_change_pct = holding_day_changes.get(ticker) or 0.0
        cost = h.shares * h.cost_basis

        if price is not None:
            market_value = h.shares * price
            weight_pct = (market_value / total_market_value * 100.0) if total_market_value > 0 else 0.0
            unrealized = market_value - cost
            unrealized_pct = (unrealized / cost * 100.0) if cost > 0 else 0.0
        else:
            weight_pct = 0.0
            unrealized = 0.0
            unrealized_pct = 0.0
            price = h.cost_basis

        # Priority scoring:
        weight_score = min(weight_pct, 40.0) / 40.0 * 30.0
        change_score = min(abs(day_change_pct), 15.0) / 15.0 * 40.0
        gain_loss_score = min(abs(unrealized_pct), 50.0) / 50.0 * 30.0
        priority_score = weight_score + change_score + gain_loss_score

        scored_candidates.append({
            "holding": h,
            "ticker": ticker,
            "price": price,
            "day_change_pct": day_change_pct,
            "unrealized": unrealized,
            "unrealized_pct": unrealized_pct,
            "weight_pct": weight_pct,
            "score": priority_score
        })

    # Sort candidates by score descending and take the top 5
    top_candidates = sorted(scored_candidates, key=lambda x: x["score"], reverse=True)[:5]

    openai_key = await get_user_api_key(db, user.id, "openai_api_key")
    model_pref = await get_user_api_key(db, user.id, "openai_model")
    model = model_pref or "gpt-4o-mini"

    highlights = []
    current_time_str = datetime.datetime.now().strftime("%B %d, %Y %I:%M %p")

    async def process_candidate(c):
        ticker = c["ticker"]
        # Fetch rich stock details
        try:
            stock_details = await fetch_stock_data(ticker)
        except Exception as exc:
            logger.error(f"Error fetching stock data for {ticker}: {exc}")
            stock_details = {}

        # Fetch 5-day history for sparkline
        last_5d = await fetch_5d_history(ticker)

        # Calculate current price, unrealized gain/loss based on the fresh price
        fresh_price = stock_details.get("price") or c["price"]
        cost = c["holding"].shares * c["holding"].cost_basis
        if fresh_price is not None:
            market_value = c["holding"].shares * fresh_price
            unrealized = market_value - cost
            unrealized_pct = (unrealized / cost * 100.0) if cost > 0 else 0.0
        else:
            unrealized = c["unrealized"]
            unrealized_pct = c["unrealized_pct"]

        # Technical signals breakdown
        tech = stock_details.get("technical", {})
        technicals = HighlightTechnicalSignal(
            rsi=tech.get("currentRSI"),
            rsi_signal=tech.get("rsiSignal"),
            support=tech.get("supportLevel"),
            resistance=tech.get("resistanceLevel"),
            macd_signal=tech.get("macd", {}).get("signal"),
            sma_signal=tech.get("movingAverages", {}).get("priceVsSma50")
        )

        # Fundamentals signals breakdown
        earnings_info = stock_details.get("earnings", {})
        fundamentals = HighlightFundamentalSignal(
            market_cap=stock_details.get("marketCap"),
            sector=stock_details.get("sector"),
            industry=stock_details.get("industry"),
            trailing_pe=earnings_info.get("trailingPE"),
            forward_pe=earnings_info.get("forwardPE"),
            peg_ratio=earnings_info.get("pegRatio"),
            dividend_yield=stock_details.get("dividendYield")
        )

        # News list
        news_items = []
        for n in stock_details.get("news", [])[:3]:
            news_items.append(HighlightNewsItem(
                title=n.get("title", ""),
                publisher=n.get("publisher", ""),
                link=n.get("link", ""),
                published=n.get("published", ""),
                summary=n.get("summary", "")
            ))

        # Generate LLM hypothesis
        if not openai_key:
            hypothesis = (
                "OpenAI API key not configured. Please add your key in Settings to generate AI hypotheses. "
                "You can still view the technical, fundamental, and news data above."
            )
        else:
            news_summary = ""
            if news_items:
                for item in news_items:
                    news_summary += f"- {item.title} (Source: {item.publisher}, Date: {item.published})\n  {item.summary[:150]}...\n"
            else:
                news_summary = "No recent news found."

            user_prompt = f"""Generate a review highlight for {ticker} ({stock_details.get('companyName') or ticker}).
Current Server Time: {current_time_str}

Portfolio Details:
- Current Holding: {c['holding'].shares:.4f} shares
- Cost Basis: ${c['holding'].cost_basis:.2f} per share (Total Cost: ${c['holding'].shares * c['holding'].cost_basis:.2f})
- Current Price: ${fresh_price:.2f} (Daily Change: {c['day_change_pct']:.2f}%)
- Unrealized P&L: ${unrealized:.2f} ({unrealized_pct:.2f}%)
- Portfolio Weight: {c['weight_pct']:.2f}%

Fundamentals:
- Market Cap: {stock_details.get('marketCap') or 'N/A'}
- Sector: {stock_details.get('sector') or 'N/A'} | Industry: {stock_details.get('industry') or 'N/A'}
- Trailing P/E: {earnings_info.get('trailingPE') or 'N/A'} | Forward P/E: {earnings_info.get('forwardPE') or 'N/A'}
- PEG Ratio: {earnings_info.get('pegRatio') or 'N/A'}
- Dividend Yield: {stock_details.get('dividendYield') or 0.0}%
- Last Reported Quarter: {earnings_info.get('lastQuarter') or 'N/A'}
- Next Earnings Date: {earnings_info.get('guidance', {}).get('nextEarningsDate') or 'N/A'}

Technicals:
- RSI (14): {tech.get('currentRSI') or 'N/A'} ({tech.get('rsiSignal') or 'N/A'})
- Support Level: ${tech.get('supportLevel') or 'N/A'} | Resistance Level: ${tech.get('resistanceLevel') or 'N/A'}
- Trend: {tech.get('volumeAnalysis', {}).get('phase', '').replace('_', ' ').title()}

Recent News:
{news_summary}

Write a concise investment hypothesis and catalyst watch for this holding. Include:
1. **Developments & Drivers**: Briefly explain any major news or factors driving the stock recently. Ensure your context matches the provided news publication dates relative to today.
2. **AI Hypothesis**: What is the most plausible path forward for the stock price in the near-term?
3. **Catalysts to Watch**: What specific events or data releases should the user monitor? (Explicitly mention if earnings has already passed recently or when next earnings is expected, based on the provided dates).

Keep your response highly engaging, compact, and formatted as brief paragraphs with **bold headers**. Maximum 180 words. Do not use markdown headers (like # or ##) or bullet points (like * or -) to save vertical space. Speak directly to the investor."""

            try:
                hypothesis = await call_llm(
                    api_key=openai_key,
                    model=model,
                    messages=[
                        {"role": "system", "content": "You are a professional equity research analyst. Your task is to provide a brief, high-impact price movement hypothesis for a card swipe review interface."},
                        {"role": "user", "content": user_prompt}
                    ],
                    max_tokens=500,
                    temperature=0.7
                )
            except Exception as e:
                logger.error(f"Error generating LLM hypothesis for {ticker}: {e}")
                hypothesis = f"Failed to generate AI hypothesis: {e}"

        # Determine fallbacks for price fields if None
        current_price = fresh_price
        day_chg = price_data.get(ticker, {}).get("day_change")
        day_chg_pct = c["day_change_pct"]

        return HighlightHolding(
            ticker=ticker,
            company_name=stock_details.get("companyName") or ticker,
            asset_type=c["holding"].asset_type or "STOCK",
            shares=c["holding"].shares,
            cost_basis=c["holding"].cost_basis,
            current_price=round(current_price, 2) if current_price else None,
            day_change=round(day_chg, 2) if day_chg is not None else None,
            day_change_pct=round(day_chg_pct, 2) if day_chg_pct is not None else None,
            unrealized_gain_loss=round(unrealized, 2) if unrealized is not None else None,
            unrealized_gain_loss_pct=round(unrealized_pct, 2) if unrealized_pct is not None else None,
            weight_pct=round(c["weight_pct"], 2) if c["weight_pct"] is not None else None,
            last_5d_history=[HighlightHistoricalPoint(date=p["date"], price=p["price"]) for p in last_5d],
            technicals=technicals,
            fundamentals=fundamentals,
            news=news_items,
            hypothesis=hypothesis
        )

    tasks = [process_candidate(c) for c in top_candidates]
    highlights = await asyncio.gather(*tasks)

    return HighlightResponse(
        highlights=highlights,
        total_portfolio_holdings=len(portfolio.holdings)
    )


@router.post("/highlights/dismiss")
async def dismiss_highlight(
    body: DismissHighlightIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Record that a user dismissed a portfolio highlight suggestions card."""
    ticker = body.ticker.upper().strip()

    # Check if already dismissed
    existing = (await db.execute(
        select(PortfolioHighlightDismissal).where(
            PortfolioHighlightDismissal.user_id == user.id,
            PortfolioHighlightDismissal.ticker == ticker
        )
    )).scalar_one_or_none()

    if not existing:
        dismissal = PortfolioHighlightDismissal(
            user_id=user.id,
            ticker=ticker
        )
        db.add(dismissal)
        await db.commit()

    return {"ok": True}


@router.post("/highlights/reset")
async def reset_highlights(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete all highlights dismissals to let the user review them again."""
    from sqlalchemy import delete
    await db.execute(
        delete(PortfolioHighlightDismissal).where(PortfolioHighlightDismissal.user_id == user.id)
    )
    await db.commit()
    return {"ok": True}


@router.post("/refresh/{ticker}")
async def refresh_ticker(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Bust all enrichment caches for a single ticker and re-fetch."""
    t = ticker.upper().strip()
    portfolio = await _get_or_create_portfolio(db, user.id)
    tickers = [h.ticker for h in portfolio.holdings]
    if t not in tickers:
        raise HTTPException(404, f"{t} not in portfolio")
    await invalidate_ticker(db, t)
    # Pre-warm all three views for this ticker
    for view in ("dividends", "fundamentals", "technical"):
        await fetch_enriched_view(db, [t], view, force_refresh=False)
    return {"ok": True, "ticker": t}


@router.post("/refresh-all")
async def refresh_all(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Bust all enrichment caches for every holding in the portfolio."""
    portfolio = await _get_or_create_portfolio(db, user.id)
    tickers = list(set(h.ticker for h in portfolio.holdings))
    await invalidate_all(db, tickers)
    await invalidate_prices(db, tickers)
    await invalidate(db, f"portfolio:brief_narrative:{user.id}")
    return {"ok": True, "count": len(tickers)}
