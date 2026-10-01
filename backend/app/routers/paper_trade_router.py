"""Paper Trader router — /api/paper-trades.

A one-click paper-trading loop over the Income Desk. The user "places" any scanned/evaluated
opportunity; we snapshot the full opportunity (incl. its Quant Analysis) at placement and let
them compare that placed read against the SAME engine's read now, across many trades.

    open ──(Close)──▶ closed        (Delete removes it entirely)

Laziness contract (explicit product requirement): the list + detail routes read stored/cached
columns ONLY — no yfinance, no scan. All repricing + quant recompute happens on ``/refresh``,
per trade, on user action; the page-level "Refresh all" is the client looping ``/refresh`` with
bounded concurrency. So a book of many paper trades never triggers one heavy request.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..database import get_db
from ..models import PaperTrade, User
from ..services import paper_trade_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/paper-trades", tags=["paper-trades"])


# ---------------------------------------------------------------------------
# Pydantic I/O
# ---------------------------------------------------------------------------

class PaperTradeIn(BaseModel):
    ticker: str
    opp: dict = Field(...)                 # the full DeskRankedTrade shown when the user placed it
    spot: float | None = None             # underlying spot at placement (backfilled on refresh if absent)
    quote_source: str = "yfinance"
    note: str | None = None


class CloseIn(BaseModel):
    quote_source: str = "yfinance"
    note: str | None = None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _loads(s, default):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


def _list_dict(pt: PaperTrade) -> dict:
    """LIGHTWEIGHT row — denormalized/cached columns only (no snapshot/eval blobs). Zero compute:
    the whole list renders from this without touching the network."""
    contracts = pt.contracts or 1
    # For an OPEN trade last_value_per_share is the current structure mid; for an EXPIRED one it's
    # the net intrinsic liability at settlement — in both cases × 100 × contracts is the $ value.
    last_value = (pt.last_value_per_share * paper_trade_service.CONTRACT_MULTIPLIER * contracts
                  if pt.last_value_per_share is not None else None)
    # ITM/OTM outcome for an expired trade (cheap parse of the cached settlement, expired rows only).
    expiry_outcome = None
    if pt.status == "expired" and pt.last_eval:
        try:
            expiry_outcome = "itm" if json.loads(pt.last_eval).get("itm") else "otm"
        except Exception:
            pass
    return {
        "id": pt.id,
        "ticker": pt.ticker,
        "structure": pt.structure,
        "label": pt.label,
        "expiration": pt.expiration,
        "dte": paper_trade_service.dte_remaining(pt.expiration),
        "contracts": contracts,
        "status": pt.status,                # open | closed | expired
        "expiry_outcome": expiry_outcome,   # itm | otm | null (expired only)
        "notes": pt.notes,
        "created_at": pt.created_at.isoformat(),
        "updated_at": pt.updated_at.isoformat(),
        # entry basis (cost basis = net credit received)
        "cost_basis": pt.entry_credit,
        "entry_premium_per_share": pt.entry_premium_per_share,
        "entry_spot": pt.entry_spot,
        "placed_desk_score": pt.placed_desk_score,
        "placed_algo_grade": pt.placed_algo_grade,
        # cached CURRENT read (from the last /refresh)
        "last_eval_at": pt.last_eval_at.isoformat() if pt.last_eval_at else None,
        "last_spot": pt.last_spot,
        "current_value": last_value,
        "last_pnl": pt.last_pnl,
        "last_desk_score": pt.last_desk_score,
        "last_algo_grade": pt.last_algo_grade,
        # close
        "closed_at": pt.closed_at.isoformat() if pt.closed_at else None,
        "close_pnl": pt.close_pnl,
        "close_note": pt.close_note,
    }


def _detail_dict(pt: PaperTrade) -> dict:
    """List row + the heavy JSON blobs (parsed) needed by the expanded view: the opportunity as
    placed, and the cached CURRENT desk read. Still no compute — reads stored columns."""
    return {
        **_list_dict(pt),
        "legs": _loads(pt.legs, []),
        "placed_snapshot": _loads(pt.placed_snapshot, {}),   # DeskRankedTrade → placed Quant Analysis
        "last_eval": _loads(pt.last_eval, None),             # cached DeskScoreResult + pnl (current)
    }


async def _get_row(db: AsyncSession, user: User, trade_id: int) -> PaperTrade:
    row = (await db.execute(
        select(PaperTrade).where(PaperTrade.id == trade_id, PaperTrade.user_id == user.id)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Paper trade not found")
    return row


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

@router.post("", status_code=201)
async def create_paper_trade(
    body: PaperTradeIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Place a paper trade from an Income-Desk opportunity — snapshots the opportunity (incl. its
    Quant Analysis) at placement. Always 1 contract. NO compute (the opportunity is already
    priced and passed in); the first live read happens on expand / refresh."""
    opp = body.opp or {}
    if not opp.get("structure"):
        raise HTTPException(400, "opp must include a structure")
    fields = paper_trade_service.build_paper_trade_fields(opp, contracts=1, spot=body.spot)
    row = PaperTrade(user_id=user.id, ticker=body.ticker.upper(), status="open",
                     notes=body.note, **fields)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _list_dict(row)


def _is_terminal(s: str) -> bool:
    return s in ("closed", "expired")


@router.get("")
async def list_paper_trades(
    status: str = "all",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """All paper trades (newest first). LIGHTWEIGHT — cached columns only.

    Before serializing, auto-settle any OPEN trades that have expired: this is the only place a
    list touches the network, and it's self-limiting (a trade expires once, then is cached
    'expired' forever; only past-expiry open trades trigger a single close-price fetch). ``status``
    = open | closed (incl. expired) | all."""
    rows = (await db.execute(
        select(PaperTrade).where(PaperTrade.user_id == user.id).order_by(PaperTrade.created_at.desc())
    )).scalars().all()

    # Settle expired-open trades (sequential DB writes on this one session; close price memoized
    # per ticker+expiry so many same-underlying trades share one fetch). Best-effort per trade.
    close_memo: dict = {}
    settled_any = False
    for r in rows:
        if r.status == "open" and paper_trade_service.is_expired(r):
            try:
                await paper_trade_service.settle_expiry(r, db, close_memo=close_memo, commit=False)
                settled_any = True
            except Exception as exc:  # noqa: BLE001 — leave open, retry next load
                logger.info("expiry settle skipped for paper trade %s (%s): %s", r.id, r.ticker, exc)
    if settled_any:
        await db.commit()
        # Re-load: the settlement UPDATE expires server-onupdate columns (updated_at), and reading
        # them during serialization would fire a SYNC lazy-load inside the async request → error.
        rows = (await db.execute(
            select(PaperTrade).where(PaperTrade.user_id == user.id).order_by(PaperTrade.created_at.desc())
        )).scalars().all()

    if status == "open":
        shown = [r for r in rows if r.status == "open"]
    elif status == "closed":
        shown = [r for r in rows if _is_terminal(r.status)]
    else:
        shown = rows
    return {
        "items": [_list_dict(r) for r in shown],
        "counts": {
            "open": sum(1 for r in rows if r.status == "open"),
            "closed": sum(1 for r in rows if _is_terminal(r.status)),
            "total": len(rows),
        },
    }


@router.get("/{trade_id}")
async def get_paper_trade(
    trade_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """One paper trade with the placed snapshot + the cached current read. No compute."""
    row = await _get_row(db, user, trade_id)
    return _detail_dict(row)


@router.post("/{trade_id}/refresh")
async def refresh_paper_trade(
    trade_id: int,
    quote_source: str = "yfinance",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """THE heavy path: re-price the exact legs off a fresh chain, recompute the current quant +
    management read, compute live P&L, and cache it. Returns the fresh DeskScoreResult + ``pnl``
    (``matched: False`` with an ``error`` when the structure/legs can't be priced now)."""
    row = await _get_row(db, user, trade_id)
    # Terminal trades (closed/expired) have no live chain to re-price — return the cached read.
    if _is_terminal(row.status):
        cached = _loads(row.last_eval, None) or {"matched": False, "error": f"Trade is {row.status}."}
        return {"id": row.id, **cached}
    # An open trade past expiry settles to intrinsic instead of re-pricing.
    if paper_trade_service.is_expired(row):
        try:
            result = await paper_trade_service.settle_expiry(row, db, commit=True)
        except Exception as exc:  # noqa: BLE001
            logger.info("expiry settle failed for %s (%s): %s", row.id, row.ticker, exc)
            raise HTTPException(status_code=502, detail=f"Expiry settlement failed: {exc}")
        return {"id": row.id, **result}
    try:
        result = await paper_trade_service.recompute(row, user, db, quote_source)
    except Exception as exc:  # noqa: BLE001
        logger.exception("paper-trade refresh failed for %s (%s)", row.id, row.ticker)
        raise HTTPException(status_code=502, detail=f"Refresh failed: {exc}")
    return {"id": row.id, **result}


@router.post("/{trade_id}/close")
async def close_paper_trade(
    trade_id: int,
    body: CloseIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Bank the current P&L and archive to Closed (kept as a placed-vs-final record). Best-effort
    fresh recompute first so the banked number is current; falls back to the last cached P&L."""
    import datetime as dt
    row = await _get_row(db, user, trade_id)
    if _is_terminal(row.status):
        raise HTTPException(400, f"Paper trade already {row.status}")
    # If it has already expired, settle to intrinsic rather than a live re-price.
    if paper_trade_service.is_expired(row):
        try:
            await paper_trade_service.settle_expiry(row, db, commit=True)
        except Exception as exc:  # noqa: BLE001
            logger.info("close: expiry settle failed for %s: %s", row.id, exc)
        return _detail_dict(row)
    try:
        await paper_trade_service.recompute(row, user, db, body.quote_source)
    except Exception as exc:  # noqa: BLE001
        logger.info("close: fresh recompute failed for %s, banking last cached P&L: %s", row.id, exc)
    row.status = "closed"
    row.closed_at = dt.datetime.now(dt.timezone.utc)
    row.close_pnl = row.last_pnl
    row.close_note = body.note
    await db.commit()
    await db.refresh(row)
    return _detail_dict(row)


@router.delete("/{trade_id}", status_code=204)
async def delete_paper_trade(
    trade_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await _get_row(db, user, trade_id)
    await db.delete(row)
    await db.commit()
    return None
