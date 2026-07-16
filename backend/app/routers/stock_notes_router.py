"""Stock notes router — per-ticker LLM Q&A with persistent conversation history."""

import datetime
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import get_current_user, get_user_api_key
from ..database import get_db
from ..models import Portfolio, PortfolioHolding, StockNote, User
from ..services.llm_service import call_llm
from ..services.stock_service import fetch_stock_data
from ..services.analysis_service import run_technical_analysis, run_price_prediction

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stock", tags=["stock-notes"])


# --------------------------------------------------------------------------
# Pydantic schemas
# --------------------------------------------------------------------------

class AskIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)


class NoteOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime.datetime


class AskResponse(BaseModel):
    answer: str
    notes: list[NoteOut]


# --------------------------------------------------------------------------
# Helper: build rich stock context for LLM
# --------------------------------------------------------------------------

async def _build_stock_context(ticker: str, db: AsyncSession, user_id: int) -> str:
    """Gather all relevant stock data and user holdings to include in the LLM system prompt."""
    sections: list[str] = []

    # 1. Stock data (price, news, sentiment, technicals, etc.)
    try:
        stock_data = await fetch_stock_data(ticker)
        if stock_data:
            basic = {
                "price": stock_data.get("price"),
                "change": stock_data.get("change"),
                "changePercent": stock_data.get("changePercent"),
                "marketCap": stock_data.get("marketCap"),
                "sector": stock_data.get("sector"),
                "industry": stock_data.get("industry"),
            }
            sections.append(f"CURRENT STOCK DATA:\n{json.dumps(basic, default=str)}")

            # News summary
            ns = stock_data.get("newsSummary")
            if ns:
                sections.append(f"NEWS SUMMARY:\nSentiment: {ns.get('sentiment', 'N/A')}\nThemes: {', '.join(ns.get('themes', []))}\nTop Headlines: {'; '.join(ns.get('topHeadlines', [])[:5])}")

            # Technical snapshot from stock data
            tech = stock_data.get("technical")
            if tech:
                tech_summary = tech.get("analysisSummary", "")
                vol_analysis = tech.get("volumeAnalysis", {})
                sections.append(f"TECHNICAL SNAPSHOT:\nRSI: {tech.get('currentRSI', 'N/A')} ({tech.get('rsiSignal', '')})\nSupport: {tech.get('supportLevel', 'N/A')}, Resistance: {tech.get('resistanceLevel', 'N/A')}\nVolume Phase: {vol_analysis.get('phase', 'N/A')} — {vol_analysis.get('bigMoneyAnalysis', '')}\nSummary: {tech_summary}")

            # Earnings
            earn = stock_data.get("earnings")
            if earn and earn.get("available"):
                sections.append(f"EARNINGS: Reported EPS: {earn.get('reportedEPS')}, Estimated: {earn.get('estimatedEPS')}, Surprise: {earn.get('epsSurprisePct')}%, Trailing PE: {earn.get('trailingPE')}, Forward PE: {earn.get('forwardPE')}")

            # Options volatility
            opts = stock_data.get("options")
            if opts and opts.get("available"):
                iv = opts.get("iv", {})
                sections.append(f"OPTIONS: IV Current: {iv.get('current', 'N/A')}%, HV30: {opts.get('hv30', 'N/A')}%, Put/Call Ratio (OI): {opts.get('putCallRatio', {}).get('openInterest', 'N/A')}")
    except Exception as e:
        logger.warning(f"Failed to fetch stock data for context: {e}")

    # 2. Technical analysis
    try:
        ta = await run_technical_analysis(ticker)
        if ta and "error" not in ta:
            macd = ta.get("macd", {})
            bb = ta.get("bollinger_bands", {})
            sections.append(f"DETAILED TECHNICALS:\nMACD: {macd.get('signal', 'N/A')} (crossover: {macd.get('crossover', 'none')})\nBollinger: {bb.get('position', 'N/A')} (%B: {bb.get('percent_b', 'N/A')})\nRSI: {ta.get('rsi', 'N/A')} ({ta.get('rsi_signal', '')})\nSMA50: {ta.get('sma50', 'N/A')} ({ta.get('price_vs_sma50', '')})\nSMA200: {ta.get('sma200', 'N/A')} ({ta.get('price_vs_sma200', '')})\n{ta.get('signal_summary', '')}")
    except Exception as e:
        logger.warning(f"Failed to run technical analysis for context: {e}")

    # 3. Price prediction
    try:
        pred = await run_price_prediction(ticker)
        if pred and "error" not in pred:
            lt = pred.get("linear_trend", {})
            ov = pred.get("overall_signal", {})
            vol = pred.get("volatility", {})
            sections.append(f"PRICE PREDICTION (30-day):\nSignal: {ov.get('signal', 'N/A')} (confidence: {ov.get('confidence_pct', 'N/A')}%)\nProjected Price: ${lt.get('projected_price', 'N/A')} ({lt.get('projected_change_pct', 'N/A')}%)\n95% Range: ${vol.get('projected_95_range', {}).get('low', 'N/A')} - ${vol.get('projected_95_range', {}).get('high', 'N/A')}")
    except Exception as e:
        logger.warning(f"Failed to run price prediction for context: {e}")

    # 4. User's holdings for this ticker
    try:
        result = await db.execute(
            select(Portfolio).options(selectinload(Portfolio.holdings))
            .where(Portfolio.user_id == user_id)
        )
        portfolio = result.scalar_one_or_none()
        if portfolio:
            ticker_holdings = [h for h in portfolio.holdings if h.ticker.upper() == ticker.upper()]
            if ticker_holdings:
                holdings_info = []
                for h in ticker_holdings:
                    today = datetime.date.today()
                    days_held = (today - h.purchase_date).days
                    holding_type = "long-term" if days_held > 365 else "short-term"
                    holdings_info.append(
                        f"  {h.shares} shares @ ${h.cost_basis} (purchased {h.purchase_date}, {days_held} days, {holding_type})"
                    )
                sections.append(f"USER'S HOLDINGS OF {ticker.upper()}:\n" + "\n".join(holdings_info))
            else:
                sections.append(f"USER'S HOLDINGS: User does not currently hold {ticker.upper()}")
    except Exception as e:
        logger.warning(f"Failed to fetch portfolio context: {e}")

    return "\n\n".join(sections)


# --------------------------------------------------------------------------
# GET /api/stock/{ticker}/notes — fetch conversation history
# --------------------------------------------------------------------------

@router.get("/{ticker}/notes", response_model=list[NoteOut])
async def get_notes(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch the last 7 days of Q&A notes for this ticker."""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)
    result = await db.execute(
        select(StockNote)
        .where(
            StockNote.user_id == user.id,
            StockNote.ticker == ticker.upper(),
            StockNote.created_at >= cutoff,
        )
        .order_by(StockNote.created_at.asc())
    )
    notes = result.scalars().all()
    return [
        NoteOut(id=n.id, role=n.role, content=n.content, created_at=n.created_at)
        for n in notes
    ]


# --------------------------------------------------------------------------
# POST /api/stock/{ticker}/notes/ask — send question, get LLM answer
# --------------------------------------------------------------------------

@router.post("/{ticker}/notes/ask", response_model=AskResponse)
async def ask_question(
    ticker: str,
    body: AskIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ask LLM a question about a specific stock, with full context."""
    ticker = ticker.upper()

    # Get user's OpenAI key
    openai_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not openai_key:
        raise HTTPException(400, "OpenAI API key not configured. Please add it in Settings.")

    model_pref = await get_user_api_key(db, user.id, "openai_model")
    model = model_pref or "gpt-4o-mini"

    # Build rich context
    stock_context = await _build_stock_context(ticker, db, user.id)

    # Load last 7 days of conversation history
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)
    hist_result = await db.execute(
        select(StockNote)
        .where(
            StockNote.user_id == user.id,
            StockNote.ticker == ticker,
            StockNote.created_at >= cutoff,
        )
        .order_by(StockNote.created_at.asc())
    )
    history = hist_result.scalars().all()

    # Build messages
    system_prompt = f"""You are an elite stock analyst with CPA, CFA, and quantitative finance expertise.
You are analyzing {ticker} for the user. Below is real-time data about this stock:

{stock_context}

INSTRUCTIONS:
- Answer the user's question thoroughly using the data provided above.
- Provide actionable, data-driven insights at an institutional level.
- Include specific numbers, levels, and percentages from the data.
- NEVER ask follow-up questions — the user cannot reply in this interface. Provide your best complete analysis.
- If the question involves tax implications, apply specific tax rules (wash sale, short-term vs long-term capital gains, etc.).
- If the question involves options strategies, include Greeks and probability analysis.
- Format your response in clear markdown with headers and bullet points."""

    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    # Add conversation history
    for note in history:
        messages.append({"role": note.role, "content": note.content})

    # Add current question
    messages.append({"role": "user", "content": body.question})

    # Call LLM
    try:
        answer = await call_llm(
            api_key=openai_key,
            model=model,
            messages=messages,
            max_tokens=2000,
        )
    except Exception as exc:
        raise HTTPException(502, f"LLM request failed: {exc}")

    # Save both user question and assistant answer
    now = datetime.datetime.now(datetime.timezone.utc)
    user_note = StockNote(
        user_id=user.id, ticker=ticker, role="user", content=body.question, created_at=now
    )
    assistant_note = StockNote(
        user_id=user.id, ticker=ticker, role="assistant", content=answer, created_at=now
    )
    db.add(user_note)
    db.add(assistant_note)
    await db.commit()
    await db.refresh(user_note)
    await db.refresh(assistant_note)

    # Return updated history
    hist_result2 = await db.execute(
        select(StockNote)
        .where(
            StockNote.user_id == user.id,
            StockNote.ticker == ticker,
            StockNote.created_at >= cutoff,
        )
        .order_by(StockNote.created_at.asc())
    )
    all_notes = hist_result2.scalars().all()

    return AskResponse(
        answer=answer,
        notes=[NoteOut(id=n.id, role=n.role, content=n.content, created_at=n.created_at) for n in all_notes],
    )


# --------------------------------------------------------------------------
# DELETE /api/stock/{ticker}/notes — clear notes
# --------------------------------------------------------------------------

@router.delete("/{ticker}/notes")
async def clear_notes(
    ticker: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Clear all notes for a ticker."""
    result = await db.execute(
        select(StockNote).where(StockNote.user_id == user.id, StockNote.ticker == ticker.upper())
    )
    notes = result.scalars().all()
    for n in notes:
        await db.delete(n)
    await db.commit()
    return {"ok": True, "cleared": len(notes)}
