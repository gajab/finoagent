"""Trade Tracking & Management router — /api/tracked-trades.

Lets a user promote a setup into a *tracked trade* and manage it through its lifecycle:

    watching ──(Execute)──▶ in_progress ──(Close)──▶ closed
        │                                              ▲
        └──────────────(Invalidate)──▶ invalidated ────┘

Every ``Refresh`` re-runs :func:`trade_tracking_service.evaluate_trade` against live
market data and caches the verdict. ``Ask AI`` ships the same JSON payload the user can
copy, to the LLM for a second opinion.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import yfinance as yf
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, get_user_api_key
from ..database import get_db
from ..models import TrackedTrade, User
from ..services.llm_service import call_llm
from ..services.trade_tracking_service import evaluate_trade

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tracked-trades", tags=["trade-tracking"])


# ---------------------------------------------------------------------------
# Pydantic I/O
# ---------------------------------------------------------------------------

class TrackTradeIn(BaseModel):
    ticker: str
    direction: str                                   # long | short | neutral
    instrument: str = "equity"                        # equity | options | futures
    setup_type: str | None = None
    title: str | None = None
    entry_low: float | None = None
    entry_high: float | None = None
    entry_level: float | None = None
    stop_level: float | None = None
    target_levels: list[float] = Field(default_factory=list)
    setup_snapshot: dict = Field(default_factory=dict)
    context_snapshot: dict | None = None
    evaluate_now: bool = True


class ExecuteIn(BaseModel):
    price: float | None = None
    qty: float | None = None
    note: str | None = None


class CloseIn(BaseModel):
    price: float | None = None
    note: str | None = None


class NotesIn(BaseModel):
    user_notes: str | None = None


class AskLLMIn(BaseModel):
    question: str | None = None
    refresh: bool = False


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _loads(s, default):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


def _row_to_dict(row: TrackedTrade) -> dict:
    return {
        "id": row.id,
        "ticker": row.ticker,
        "direction": row.direction,
        "instrument": row.instrument,
        "setup_type": row.setup_type,
        "status": row.status,
        "title": row.title,
        "entry_low": row.entry_low,
        "entry_high": row.entry_high,
        "entry_level": row.entry_level,
        "stop_level": row.stop_level,
        "target_levels": _loads(row.target_levels, []),
        "setup_snapshot": _loads(row.setup_snapshot, {}),
        "context_snapshot": _loads(row.context_snapshot, None),
        "executed_at": row.executed_at.isoformat() if row.executed_at else None,
        "executed_price": row.executed_price,
        "executed_qty": row.executed_qty,
        "execution_note": row.execution_note,
        "closed_at": row.closed_at.isoformat() if row.closed_at else None,
        "exit_price": row.exit_price,
        "exit_note": row.exit_note,
        "realized_pnl": row.realized_pnl,
        "last_eval": _loads(row.last_eval, None),
        "last_verdict": row.last_verdict,
        "last_eval_at": row.last_eval_at.isoformat() if row.last_eval_at else None,
        "user_notes": row.user_notes,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _trade_for_eval(row: TrackedTrade) -> dict:
    return {
        "ticker": row.ticker,
        "direction": row.direction,
        "instrument": row.instrument,
        "setup_type": row.setup_type,
        "status": row.status,
        "entry_low": row.entry_low,
        "entry_high": row.entry_high,
        "entry_level": row.entry_level,
        "stop_level": row.stop_level,
        "target_levels": _loads(row.target_levels, []),
        "executed_price": row.executed_price,
        "executed_qty": row.executed_qty,
        "setup_snapshot": _loads(row.setup_snapshot, {}),
    }


async def _get_row(db: AsyncSession, user: User, trade_id: int) -> TrackedTrade:
    row = (await db.execute(
        select(TrackedTrade).where(TrackedTrade.id == trade_id, TrackedTrade.user_id == user.id)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Tracked trade not found")
    return row


async def _evaluate_and_store(db: AsyncSession, row: TrackedTrade) -> dict:
    trade = _trade_for_eval(row)
    ev = await asyncio.to_thread(evaluate_trade, yf.Ticker(row.ticker), trade)
    if ev.get("ok"):
        row.last_eval = json.dumps(ev, default=str)
        row.last_verdict = ev.get("verdict")
        row.last_eval_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(row)
    return ev


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

@router.post("", status_code=201)
async def track_trade(
    body: TrackTradeIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start tracking a setup. Snapshots the plan and (optionally) runs the first evaluation."""
    if body.direction not in ("long", "short", "neutral"):
        raise HTTPException(400, "direction must be long, short, or neutral")
    row = TrackedTrade(
        user_id=user.id,
        ticker=body.ticker.upper(),
        direction=body.direction,
        instrument=body.instrument or "equity",
        setup_type=body.setup_type,
        status="watching",
        title=body.title,
        entry_low=body.entry_low,
        entry_high=body.entry_high,
        entry_level=body.entry_level,
        stop_level=body.stop_level,
        target_levels=json.dumps(body.target_levels or []),
        setup_snapshot=json.dumps(body.setup_snapshot or {}, default=str),
        context_snapshot=json.dumps(body.context_snapshot, default=str) if body.context_snapshot else None,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    if body.evaluate_now:
        try:
            await _evaluate_and_store(db, row)
        except Exception as exc:  # noqa: BLE001
            logger.info("initial eval failed for %s: %s", row.ticker, exc)
    return _row_to_dict(row)


@router.get("")
async def list_tracked_trades(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """All tracked trades grouped by lifecycle status (newest first)."""
    rows = (await db.execute(
        select(TrackedTrade).where(TrackedTrade.user_id == user.id)
        .order_by(TrackedTrade.updated_at.desc())
    )).scalars().all()
    groups: dict[str, list] = {"watching": [], "in_progress": [], "closed": [], "invalidated": []}
    for r in rows:
        groups.setdefault(r.status, []).append(_row_to_dict(r))
    return {"groups": groups,
            "counts": {k: len(v) for k, v in groups.items()},
            "total": len(rows)}


@router.get("/{trade_id}")
async def get_tracked_trade(
    trade_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await _get_row(db, user, trade_id)
    return _row_to_dict(row)


@router.post("/{trade_id}/refresh")
async def refresh_tracked_trade(
    trade_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-evaluate against live data → EXECUTE/WAIT/INVALID (watching) or
    HOLD/SCALE/TIGHTEN/EXIT (in_progress). Caches the verdict on the row."""
    row = await _get_row(db, user, trade_id)
    if row.status in ("closed", "invalidated"):
        raise HTTPException(400, f"Trade is {row.status}; nothing to evaluate.")
    ev = await _evaluate_and_store(db, row)
    return {"trade": _row_to_dict(row), "evaluation": ev}


@router.post("/{trade_id}/execute")
async def execute_tracked_trade(
    trade_id: int,
    body: ExecuteIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark the trade as executed → moves it to *in_progress* (managing toward exit)."""
    row = await _get_row(db, user, trade_id)
    if row.status not in ("watching",):
        raise HTTPException(400, f"Can only execute a watching trade (this one is {row.status}).")
    price = body.price
    if price is None:
        ev = _loads(row.last_eval, {}) or {}
        price = ev.get("spot") or row.entry_level
    row.status = "in_progress"
    row.executed_at = datetime.now(timezone.utc)
    row.executed_price = price
    row.executed_qty = body.qty
    row.execution_note = body.note
    await db.commit()
    await db.refresh(row)
    # immediately evaluate exit conditions so the card lands with a fresh read
    try:
        await _evaluate_and_store(db, row)
    except Exception:  # noqa: BLE001
        pass
    return _row_to_dict(row)


@router.post("/{trade_id}/close")
async def close_tracked_trade(
    trade_id: int,
    body: CloseIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Close the position, book realized P&L (equity), archive to *closed*."""
    row = await _get_row(db, user, trade_id)
    if row.status not in ("in_progress",):
        raise HTTPException(400, f"Can only close an in-progress trade (this one is {row.status}).")
    exit_price = body.price
    if exit_price is None:
        ev = _loads(row.last_eval, {}) or {}
        exit_price = ev.get("spot")
    realized = None
    ref = row.executed_price if row.executed_price is not None else row.entry_level
    if row.direction in ("long", "short") and row.executed_qty and ref and exit_price:
        per = (exit_price - ref) if row.direction == "long" else (ref - exit_price)
        realized = round(per * row.executed_qty, 2)
    row.status = "closed"
    row.closed_at = datetime.now(timezone.utc)
    row.exit_price = exit_price
    row.exit_note = body.note
    row.realized_pnl = realized
    await db.commit()
    await db.refresh(row)
    return _row_to_dict(row)


@router.post("/{trade_id}/invalidate")
async def invalidate_tracked_trade(
    trade_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retire a setup that never triggered / whose conditions changed."""
    row = await _get_row(db, user, trade_id)
    row.status = "invalidated"
    await db.commit()
    await db.refresh(row)
    return _row_to_dict(row)


@router.patch("/{trade_id}/notes")
async def update_notes(
    trade_id: int,
    body: NotesIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await _get_row(db, user, trade_id)
    row.user_notes = body.user_notes
    await db.commit()
    await db.refresh(row)
    return _row_to_dict(row)


@router.delete("/{trade_id}", status_code=204)
async def delete_tracked_trade(
    trade_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await _get_row(db, user, trade_id)
    await db.delete(row)
    await db.commit()


_ASK_SYSTEM = """You are a senior trading-desk risk manager reviewing a LIVE trade-tracking \
evaluation produced by a quant system. You are given the trade plan, a fresh market snapshot \
(intraday VWAP σ-bands, 1H RSI, 5m/15m CHOCH, CVD order-flow proxy, dealer gamma), the \
secondary-confirmation checklist, the system's verdict, and the open position math.

Judge it like a professional would before risking capital:
- Do you AGREE with the verdict (execute/wait/invalid or hold/scale/tighten/exit)? Say so plainly.
- Which confirmations actually matter here, and which are noise?
- What is the single biggest risk the checklist under-weights?
- Give ONE clear recommended action the trader should take right now.

Return STRICT JSON only:
{
  "agree_with_verdict": true|false,
  "assessment": "2-3 sentence professional read",
  "recommended_action": "one concrete instruction",
  "key_risks": ["..."],
  "what_to_watch": ["..."],
  "confidence": "high|medium|low"
}"""


@router.post("/{trade_id}/ask-llm")
async def ask_llm(
    trade_id: int,
    body: AskLLMIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send the live evaluation payload to the LLM for a second opinion (structured JSON)."""
    row = await _get_row(db, user, trade_id)
    openai_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not openai_key:
        raise HTTPException(400, "OpenAI API key not configured. Please add it in Settings.")
    model = (await get_user_api_key(db, user.id, "openai_model")) or "gpt-4o-mini"

    ev = None
    if body.refresh or not row.last_eval:
        try:
            ev = await _evaluate_and_store(db, row)
        except Exception:  # noqa: BLE001
            ev = None
    if ev is None or not ev.get("ok"):
        ev = _loads(row.last_eval, None)
    if not ev:
        raise HTTPException(400, "No evaluation available yet — press Refresh first.")

    payload = ev.get("payload") or ev
    user_msg = (((body.question.strip() + "\n\n") if body.question else "")
                + "Review this live trade-tracking evaluation and return the JSON verdict.\n```json\n"
                + json.dumps(payload, default=str)[:60000] + "\n```")
    try:
        answer = await call_llm(api_key=openai_key, model=model,
                                messages=[{"role": "system", "content": _ASK_SYSTEM},
                                          {"role": "user", "content": user_msg}],
                                max_tokens=1200, expect_json=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"AI review failed: {exc}")
    try:
        return {"advice": json.loads(answer), "verdict_reviewed": ev.get("verdict")}
    except Exception:
        return {"advice": {"raw": answer}, "verdict_reviewed": ev.get("verdict")}
