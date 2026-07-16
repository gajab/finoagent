"""TLH saved portfolio routes: save, load, delete named TLH portfolios."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import get_current_user
from ..database import get_db
from ..models import TLHSavedPortfolio, TLHSavedHolding, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tlh-portfolios", tags=["tlh-portfolios"])


# --------------------------------------------------------------------------
# Pydantic schemas
# --------------------------------------------------------------------------

class TLHHoldingIn(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=20)
    shares: int = Field(..., gt=0)
    cost_basis: float = Field(..., gt=0)


class TLHPortfolioCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    tax_rate_pct: float = Field(default=15.0, ge=0, le=100)
    holdings: list[TLHHoldingIn] = Field(..., min_length=1)


class TLHHoldingOut(BaseModel):
    ticker: str
    shares: int
    cost_basis: float


class TLHPortfolioOut(BaseModel):
    id: int
    name: str
    tax_rate_pct: float
    holdings: list[TLHHoldingOut]
    holding_count: int
    tickers: list[str]
    created_at: str

    class Config:
        from_attributes = True


class TLHPortfolioListItem(BaseModel):
    id: int
    name: str
    tax_rate_pct: float
    holding_count: int
    tickers: list[str]
    holdings: list[TLHHoldingOut]
    created_at: str

    class Config:
        from_attributes = True


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@router.get("", response_model=list[TLHPortfolioListItem])
async def list_tlh_portfolios(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all saved TLH portfolios for the current user."""
    result = await db.execute(
        select(TLHSavedPortfolio)
        .where(TLHSavedPortfolio.user_id == user.id)
        .options(selectinload(TLHSavedPortfolio.holdings))
        .order_by(TLHSavedPortfolio.updated_at.desc())
    )
    portfolios = result.scalars().all()

    return [
        TLHPortfolioListItem(
            id=p.id,
            name=p.name,
            tax_rate_pct=p.tax_rate_pct,
            holding_count=len(p.holdings),
            tickers=[h.ticker for h in p.holdings],
            holdings=[TLHHoldingOut(ticker=h.ticker, shares=h.shares, cost_basis=h.cost_basis) for h in p.holdings],
            created_at=p.created_at.isoformat(),
        )
        for p in portfolios
    ]


@router.post("", response_model=TLHPortfolioOut, status_code=201)
async def create_tlh_portfolio(
    body: TLHPortfolioCreateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save a new TLH portfolio with holdings."""
    # Limit to 20 saved portfolios per user
    count_result = await db.execute(
        select(TLHSavedPortfolio).where(TLHSavedPortfolio.user_id == user.id)
    )
    existing = count_result.scalars().all()
    if len(existing) >= 20:
        raise HTTPException(status_code=400, detail="Maximum 20 saved TLH portfolios. Delete one first.")

    portfolio = TLHSavedPortfolio(
        user_id=user.id,
        name=body.name.strip(),
        tax_rate_pct=body.tax_rate_pct,
    )
    db.add(portfolio)
    await db.flush()  # get portfolio.id

    for h in body.holdings:
        holding = TLHSavedHolding(
            portfolio_id=portfolio.id,
            ticker=h.ticker.upper().strip(),
            shares=h.shares,
            cost_basis=h.cost_basis,
        )
        db.add(holding)

    await db.commit()
    await db.refresh(portfolio, attribute_names=["holdings"])

    return TLHPortfolioOut(
        id=portfolio.id,
        name=portfolio.name,
        tax_rate_pct=portfolio.tax_rate_pct,
        holdings=[
            TLHHoldingOut(ticker=h.ticker, shares=h.shares, cost_basis=h.cost_basis)
            for h in portfolio.holdings
        ],
        holding_count=len(portfolio.holdings),
        tickers=[h.ticker for h in portfolio.holdings],
        created_at=portfolio.created_at.isoformat(),
    )


@router.delete("/{portfolio_id}", status_code=204)
async def delete_tlh_portfolio(
    portfolio_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a saved TLH portfolio (verify ownership)."""
    result = await db.execute(
        select(TLHSavedPortfolio).where(
            TLHSavedPortfolio.id == portfolio_id,
            TLHSavedPortfolio.user_id == user.id,
        )
    )
    portfolio = result.scalar_one_or_none()
    if not portfolio:
        raise HTTPException(status_code=404, detail="TLH portfolio not found")

    await db.delete(portfolio)
    await db.commit()
