"""Saved strategy routes: save, load, update, delete named strategy configurations."""

import datetime as _dt
import json
import logging
import math
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func as sql_func
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, get_user_api_key
from ..database import get_db
from ..models import SavedStrategy, TradeTransaction, User
from ..services.llm_service import call_llm

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/saved-strategies", tags=["saved-strategies"])

MAX_PER_USER_PER_TYPE = 50


# --------------------------------------------------------------------------
# Pydantic schemas
# --------------------------------------------------------------------------

class SavedStrategyCreateIn(BaseModel):
    strategy_type: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=200)
    ticker: str = Field(..., min_length=1, max_length=20)
    parameters: dict = Field(...)        # input params (amount, duration, etc.)
    legs_data: list = Field(...)         # legs with bid/ask/mid/price at save time
    result_snapshot: dict = Field(...)   # computed result summary
    notes: Optional[str] = None


class SavedStrategyUpdateIn(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    ticker: Optional[str] = Field(None, min_length=1, max_length=20)
    strategy_type: Optional[str] = None
    parameters: Optional[dict] = None
    legs_data: Optional[list] = None
    result_snapshot: Optional[dict] = None
    notes: Optional[str] = None
    entry_prices: Optional[list] = None
    entry_net_debit: Optional[float] = None
    entry_date: Optional[str] = None  # ISO datetime string


class SavedStrategyOut(BaseModel):
    id: int
    strategy_type: str
    name: str
    ticker: str
    parameters: dict
    legs_data: list
    result_snapshot: dict
    notes: Optional[str]
    trade_status: Optional[str] = None
    entry_prices: Optional[list] = None
    entry_date: Optional[str] = None
    entry_net_debit: Optional[float] = None
    order_source: Optional[str] = None
    exit_date: Optional[str] = None
    exit_prices: Optional[list] = None
    exit_net: Optional[float] = None
    bpr: Optional[float] = None          # buying-power reduction (Reg-T margin); annualization base
    roll: Optional[dict] = None          # roll campaign overlay (cost-basis adj + effective breakevens); None if never rolled
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class MarkTradedIn(BaseModel):
    entry_prices: list = Field(...)        # per-leg entry prices [{strike, type, price}, ...]
    entry_net_debit: float = Field(...)    # total net debit/credit
    order_source: str = Field(default="manual")  # "ibkr" | "manual"
    broker_order_id: Optional[int] = None
    entry_date: Optional[str] = None       # ISO datetime; defaults to now if not provided


class CloseTradeIn(BaseModel):
    exit_prices: list = Field(...)         # per-leg exit prices
    exit_net: float = Field(...)           # total exit net


class ClosePositionLegIn(BaseModel):
    """One option leg to close, at the price the user actually got out at."""
    leg_index: int = Field(..., ge=0)
    exit_price: float = Field(..., ge=0)   # per-share option premium paid/received at close


class ClosePositionIn(BaseModel):
    """Close SOME or ALL of a placed trade. Records realized P&L per leg so the
    long-term ledger is correct; a partial close keeps the trade active with the
    remaining legs, a full close (all legs + stock) flips it to 'closed'."""
    legs: list[ClosePositionLegIn] = Field(default_factory=list)
    close_stock: bool = False
    stock_exit_price: Optional[float] = Field(None, ge=0)
    executed_at: Optional[str] = None
    note: Optional[str] = None


class ManualTradeIn(BaseModel):
    strategy_type: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=200)
    ticker: str = Field(..., min_length=1, max_length=20)
    parameters: dict = Field(default_factory=dict)
    legs_data: list = Field(...)
    result_snapshot: dict = Field(default_factory=dict)
    entry_prices: list = Field(...)
    entry_net_debit: float = Field(...)
    order_source: str = Field(default="manual")
    notes: Optional[str] = None
    entry_date: Optional[str] = None  # ISO datetime; defaults to now if not provided


# ── Transaction ledger schemas ────────────────────────────────────────

class TransactionIn(BaseModel):
    """Append a buy/sell/adjust action to a position's ledger."""
    action: str = Field(..., description="'open' | 'add' | 'reduce' | 'close' | 'adjust'")
    quantity: float = Field(..., description="Signed: +long / -short. Shares or contracts.")
    price: float = Field(..., ge=0, description="Per-unit price paid (premium for options).")
    fees: float = Field(default=0.0, ge=0)
    executed_at: Optional[str] = Field(None, description="ISO datetime; defaults to now")
    leg_index: Optional[int] = Field(None, description="Which leg (multi-leg options); None = whole position")
    source: str = Field(default="manual")
    note: Optional[str] = None


class TransactionOut(BaseModel):
    id: int
    strategy_id: int
    action: str
    quantity: float
    price: float
    fees: float
    executed_at: str
    leg_index: Optional[int]
    source: str
    note: Optional[str]
    created_at: str

    class Config:
        from_attributes = True


class TransactionUpdateIn(BaseModel):
    """Partial update for an existing transaction — all fields optional."""
    action: Optional[str] = None
    quantity: Optional[float] = None
    price: Optional[float] = Field(None, ge=0)
    fees: Optional[float] = Field(None, ge=0)
    executed_at: Optional[str] = None
    note: Optional[str] = None


class NotesUpdateIn(BaseModel):
    notes: Optional[str] = Field(None, max_length=5000)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _roll_realized(params: dict) -> float:
    """Cumulative realized P&L ($) banked from ROLLS specifically (the cost-basis
    adjustment), as opposed to genuine closes. Authoritative source is the sum of
    closed_legs tagged ``roll``; falls back to the stored ``roll_realized_pnl`` scalar
    when the legs aren't tagged (older data). See [[trade-pnl-math]] convention."""
    legs = params.get("closed_legs") or []
    tagged = [l for l in legs if l.get("roll")]
    if tagged:
        return round(sum(float(l.get("realized") or 0) for l in tagged), 2)
    try:
        return round(float(params.get("roll_realized_pnl") or 0.0), 2)
    except (ValueError, TypeError):
        return 0.0


def _roll_summary(s: SavedStrategy) -> Optional[dict]:
    """Roll-campaign overlay for a trade that has been rolled ≥1× — the numbers that
    let the user treat a rolled position as ONE continuing trade with an adjusted cost
    basis, rather than a string of disconnected close+open events.

    Pure (no market data): everything derives from persisted legs + the banked roll
    realized. The effective breakeven reuses the exact payoff engine by folding the
    roll-realized into the entry cost — so it reconciles byte-for-byte with the live
    breakeven math. Returns None for a trade that was never rolled (zero overhead on
    the common path)."""
    try:
        params = json.loads(s.parameters) if s.parameters else {}
    except (ValueError, TypeError):
        return None
    roll_realized = _roll_realized(params)
    rolls = params.get("rolls") or []
    count = len(rolls)
    if count == 0 and abs(roll_realized) < 0.005:
        return None

    try:
        legs = json.loads(s.legs_data) if s.legs_data else []
        entry_prices = json.loads(s.entry_prices) if s.entry_prices else []
    except (ValueError, TypeError):
        legs, entry_prices = [], []

    opt_legs = [l for l in legs if str(l.get("type", "")).upper() in ("CALL", "PUT")]
    leg_metas = [{"i": i, "_leg": l, "strike": l.get("strike"), "type": l.get("type"),
                  "qty": l.get("qty") or l.get("contracts") or 1, "action": l.get("action")}
                 for i, l in enumerate(opt_legs)]
    raw_net = option_legs_net_debit(leg_metas, entry_prices) if leg_metas else None

    # Optional linear stock/futures leg for a covered/combo payoff.
    shares = float(params.get("shares") or 0)
    stock = None
    if shares > 0:
        avg = params.get("avg_cost")
        if avg in (None, "") and entry_prices:
            avg = (entry_prices[0] or {}).get("price")
        try:
            avg_f = float(avg)
        except (TypeError, ValueError):
            avg_f = 0.0
        if avg_f > 0:
            signed = -shares if "short" in (s.strategy_type or "").lower() else shares
            stock = {"shares": signed, "avg_cost": avg_f, "mult": float(params.get("multiplier") or 1.0)}

    payoff_legs = [{
        "strike": float(m["strike"]),
        "right": _leg_right(m["type"]),
        "sign": -1 if _is_short_action(m["action"]) else 1,
        "qty": float(m["qty"] or 1),
    } for m in leg_metas if m.get("strike") is not None]

    raw_be = eff_be = None
    if raw_net is not None and (payoff_legs or stock):
        from ..services.trade_math import structure_breakevens
        raw_be = structure_breakevens(payoff_legs, raw_net, stock)
        eff_be = structure_breakevens(payoff_legs, raw_net + roll_realized, stock)

    return {
        "count": count,
        "roll_realized_pnl": round(roll_realized, 2),
        "raw_net_credit": raw_net,
        "effective_net_credit": round(raw_net + roll_realized, 2) if raw_net is not None else None,
        "raw_breakevens": raw_be,
        "effective_breakevens": eff_be,
        "history": rolls,
    }


def _to_out(s: SavedStrategy) -> SavedStrategyOut:
    return SavedStrategyOut(
        id=s.id,
        strategy_type=s.strategy_type,
        name=s.name,
        ticker=s.ticker,
        parameters=json.loads(s.parameters),
        legs_data=json.loads(s.legs_data),
        result_snapshot=json.loads(s.result_snapshot),
        notes=s.notes,
        trade_status=s.trade_status,
        entry_prices=json.loads(s.entry_prices) if s.entry_prices else None,
        entry_date=s.entry_date.isoformat() if s.entry_date else None,
        entry_net_debit=s.entry_net_debit,
        order_source=s.order_source,
        exit_date=s.exit_date.isoformat() if s.exit_date else None,
        exit_prices=json.loads(s.exit_prices) if s.exit_prices else None,
        exit_net=s.exit_net,
        created_at=s.created_at.isoformat(),
        updated_at=s.updated_at.isoformat(),
        bpr=_closed_trade_bpr(s),
        roll=_roll_summary(s),
    )


def calc_reg_t_margin(leg_list, U: float = 0.0, mode: str = "reg_t") -> float:
    """Margin requirement for a set of option legs — the SINGLE source of the app's
    Reg-T / portfolio margin math (get_live_pnl and the closed-trade BPR both call this).

    Defined-risk spreads = width (both regimes). Naked shorts depend on the regime:
      • Reg T (default): max(20%·underlying − out-of-the-money, 10%·strike[put]/
        underlying[call]) — the standard broker rule.
      • Portfolio Margin: risk-based ≈ a 15% adverse move on the underlying.
    With no spot (U=0), naked falls back to 20%-of-strike (a spot-free Reg-T proxy).
    """
    def _naked(strike: float, qty: float, right: str) -> float:
        if mode == "portfolio" and U > 0:
            return 0.15 * U * qty * 100
        if U > 0:
            otm = max(0.0, U - strike) if right == "P" else max(0.0, strike - U)
            floor_base = strike if right == "P" else U
            return max(0.20 * U - otm, 0.10 * floor_base) * qty * 100
        return 0.20 * strike * qty * 100   # no spot → legacy 20%-of-strike fallback

    puts = [l for l in leg_list if l.get("right") == "P" or "PUT" in (l.get("type") or "").upper()]
    calls = [l for l in leg_list if l.get("right") == "C" or "CALL" in (l.get("type") or "").upper()]
    margin = 0.0

    # Process put spreads
    sell_puts = sorted(
        [{"strike": float(l["strike"]), "qty": l["qty"], "rem": l["qty"]}
         for l in puts if "SELL" in (l.get("action") or "").upper()],
        key=lambda x: -x["strike"],
    )
    buy_puts = [{"strike": float(l["strike"]), "qty": l["qty"], "rem": l["qty"]}
                for l in puts if "BUY" in (l.get("action") or "").upper()]
    for sp in sell_puts:
        # Pair with higher-strike buys first (protective)
        for bp in buy_puts:
            if sp["rem"] <= 0:
                break
            if bp["rem"] <= 0 or bp["strike"] <= sp["strike"]:
                continue
            paired = min(sp["rem"], bp["rem"])
            sp["rem"] -= paired
            bp["rem"] -= paired
        # Pair with lower-strike buys (spread margin)
        for bp in buy_puts:
            if sp["rem"] <= 0:
                break
            if bp["rem"] <= 0 or bp["strike"] >= sp["strike"]:
                continue
            paired = min(sp["rem"], bp["rem"])
            margin += (sp["strike"] - bp["strike"]) * paired * 100
            sp["rem"] -= paired
            bp["rem"] -= paired
        if sp["rem"] > 0:
            margin += _naked(sp["strike"], sp["rem"], "P")

    # Process call spreads
    sell_calls = sorted(
        [{"strike": float(l["strike"]), "qty": l["qty"], "rem": l["qty"]}
         for l in calls if "SELL" in (l.get("action") or "").upper()],
        key=lambda x: x["strike"],
    )
    buy_calls = [{"strike": float(l["strike"]), "qty": l["qty"], "rem": l["qty"]}
                 for l in calls if "BUY" in (l.get("action") or "").upper()]
    for sc in sell_calls:
        for bc in buy_calls:
            if sc["rem"] <= 0:
                break
            if bc["rem"] <= 0 or bc["strike"] > sc["strike"]:
                continue
            paired = min(sc["rem"], bc["rem"])
            sc["rem"] -= paired
            bc["rem"] -= paired
        for bc in buy_calls:
            if sc["rem"] <= 0:
                break
            if bc["rem"] <= 0 or bc["strike"] <= sc["strike"]:
                continue
            paired = min(sc["rem"], bc["rem"])
            margin += (bc["strike"] - sc["strike"]) * paired * 100
            sc["rem"] -= paired
            bc["rem"] -= paired
        if sc["rem"] > 0:
            margin += _naked(sc["strike"], sc["rem"], "C")

    return round(margin, 2)


def _leg_for_margin(rec: dict) -> dict:
    """Map a closed_legs record (or a live legs_data leg) to calc_reg_t_margin's shape."""
    return {
        "strike": rec.get("strike"),
        "qty": abs(float(rec.get("qty") or rec.get("contracts") or 1)),
        "action": rec.get("action"),
        "type": rec.get("type"),
        "right": rec.get("right"),
    }


def _closed_trade_bpr(s: SavedStrategy) -> Optional[float]:
    """Backend BPR (buying-power reduction) for a trade — the annualization base for the
    Closed ledger, computed HERE (not the frontend) via the single Reg-T source so a CSP
    reads its ~20% Reg-T margin, not the full strike collateral. Priority:
      1. margin_required captured on an active refresh (exact, computed with the live spot).
      2. Stock notional for stock / covered call / collar (the stock IS the capital on hold).
      3. calc_reg_t_margin on the option legs, using the last cached spot when we have one,
         else the no-spot 20%-of-strike Reg-T proxy (a closed trade has no live underlying).
    Returns None for a pure long-premium trade (no short margin, no stock) — the frontend
    then falls back to the debit paid. Never raises: a bad row just yields None."""
    try:
        params = json.loads(s.parameters or "{}")
    except (ValueError, TypeError):
        return None

    last = params.get("last_pnl") or {}
    mr = last.get("margin_required")
    try:
        if mr not in (None, "") and float(mr) > 0:
            return round(float(mr), 2)
    except (ValueError, TypeError):
        pass

    mode = params.get("margin_mode") or "reg_t"
    closed = params.get("closed_legs") or []
    try:
        live_legs = json.loads(s.legs_data or "[]")
    except (ValueError, TypeError):
        live_legs = []

    # Stock notional (covered call / collar / stock): the stock is the buying power used.
    stock_notional = 0.0
    for r in closed:
        if r.get("type") == "stock":
            try:
                stock_notional += abs(float(r.get("entry_price") or 0)) * abs(float(r.get("qty") or 0))
            except (ValueError, TypeError):
                pass
    if stock_notional <= 0:
        try:
            shares = float(params.get("shares") or 0)
            avg = float(params.get("avg_cost") or 0)
            if shares > 0 and avg > 0:
                stock_notional = shares * avg
        except (ValueError, TypeError):
            pass
    if stock_notional > 0:
        return round(stock_notional, 2)

    # Option legs → the single Reg-T margin source. Prefer live legs; a fully-closed trade
    # has emptied legs_data, so reconstruct from the closed_legs records.
    opt_source = live_legs if live_legs else closed
    opt_legs = [_leg_for_margin(l) for l in opt_source
                if (l.get("type") or "").lower() in ("call", "put") or l.get("right") in ("C", "P")]
    if not opt_legs:
        return None
    try:
        U = float(last.get("underlying_price") or 0)
    except (ValueError, TypeError):
        U = 0.0
    m = calc_reg_t_margin(opt_legs, U, mode)
    return round(m, 2) if m and m > 0 else None


async def _sync_stock_ledger(db: AsyncSession, strategy: SavedStrategy) -> None:
    """Re-walk the full transaction ledger for a stock strategy and sync
    parameters.shares, parameters.avg_cost, entry_net_debit, and trade_status.

    Called after any mutation (add / edit / delete) so the strategy row always
    reflects the current position without requiring a separate P&L refresh.
    """
    stype = (strategy.strategy_type or "").lower()
    if not stype.startswith("stock") and stype != "futures":
        return

    from ..services.trade_math import walk_ledger as _walk_ledger

    txns_result = await db.execute(
        select(TradeTransaction)
        .where(TradeTransaction.strategy_id == strategy.id)
        .order_by(TradeTransaction.executed_at.asc(), TradeTransaction.id.asc())
    )
    all_txns_objs = txns_result.scalars().all()
    all_txns = [
        {
            "action": t.action,
            "quantity": t.quantity,
            "price": t.price,
            "fees": t.fees or 0.0,
            "executed_at": t.executed_at.isoformat() if t.executed_at else "",
            "id": t.id,
        }
        for t in all_txns_objs
    ]

    if not all_txns:
        params = json.loads(strategy.parameters or "{}")
        params["shares"] = 0
        if stype == "futures":
            params["contracts"] = 0
        params.pop("avg_cost", None)
        strategy.parameters = json.dumps(params)
        return

    snap = _walk_ledger(all_txns)
    params = json.loads(strategy.parameters or "{}")
    params["shares"] = round(snap.net_quantity, 8)
    if stype == "futures":
        params["contracts"] = round(snap.net_quantity, 8)
    params["avg_cost"] = round(snap.avg_cost, 6) if snap.avg_cost else params.get("avg_cost", 0)
    strategy.parameters = json.dumps(params)
    multiplier = float(params.get("multiplier") or 1.0)
    if snap.total_cost_basis > 0:
        strategy.entry_net_debit = round(snap.total_cost_basis * (multiplier if stype == "futures" else 1.0), 2)

    # Reconcile trade_status: closed only when there is an explicit "close"
    # action AND the net position is flat.  If the user deleted the close row
    # or edited qty back up, reopen the trade automatically.
    has_close_action = any(t["action"] == "close" for t in all_txns)
    if snap.net_quantity < 1e-9 and has_close_action:
        strategy.trade_status = "closed"
        # Keep exit_date as the latest close transaction's date
        close_objs = [t for t in all_txns_objs if t.action == "close"]
        if close_objs:
            strategy.exit_date = max(close_objs, key=lambda t: t.executed_at or t.created_at).executed_at
    else:
        strategy.trade_status = "active"
        strategy.exit_date = None


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@router.get("", response_model=list[SavedStrategyOut])
async def list_saved_strategies(
    strategy_type: Optional[str] = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List saved strategies, optionally filtered by type."""
    q = select(SavedStrategy).where(SavedStrategy.user_id == user.id)
    if strategy_type:
        q = q.where(SavedStrategy.strategy_type == strategy_type)
    q = q.order_by(SavedStrategy.updated_at.desc())

    result = await db.execute(q)
    strategies = result.scalars().all()
    return [_to_out(s) for s in strategies]


@router.post("", response_model=SavedStrategyOut, status_code=201)
async def create_saved_strategy(
    body: SavedStrategyCreateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save a new strategy configuration."""
    # Enforce limit per type
    count_result = await db.execute(
        select(sql_func.count(SavedStrategy.id)).where(
            SavedStrategy.user_id == user.id,
            SavedStrategy.strategy_type == body.strategy_type,
        )
    )
    count = count_result.scalar() or 0
    if count >= MAX_PER_USER_PER_TYPE:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {MAX_PER_USER_PER_TYPE} saved strategies per type. Delete one first.",
        )

    strategy = SavedStrategy(
        user_id=user.id,
        strategy_type=body.strategy_type.strip(),
        name=body.name.strip(),
        ticker=body.ticker.upper().strip(),
        parameters=json.dumps(body.parameters),
        legs_data=json.dumps(body.legs_data),
        result_snapshot=json.dumps(body.result_snapshot),
        notes=body.notes,
    )
    db.add(strategy)
    await db.commit()
    await db.refresh(strategy)
    return _to_out(strategy)


@router.put("/{strategy_id}", response_model=SavedStrategyOut)
async def update_saved_strategy(
    strategy_id: int,
    body: SavedStrategyUpdateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing saved strategy (ownership verified)."""
    result = await db.execute(
        select(SavedStrategy).where(
            SavedStrategy.id == strategy_id,
            SavedStrategy.user_id == user.id,
        )
    )
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Saved strategy not found")

    if body.name is not None:
        strategy.name = body.name.strip()
    if body.ticker is not None:
        strategy.ticker = body.ticker.upper().strip()
    if body.strategy_type is not None:
        strategy.strategy_type = body.strategy_type.strip()
    if body.parameters is not None:
        strategy.parameters = json.dumps(body.parameters)
    if body.legs_data is not None:
        strategy.legs_data = json.dumps(body.legs_data)
    if body.result_snapshot is not None:
        strategy.result_snapshot = json.dumps(body.result_snapshot)
    if body.notes is not None:
        strategy.notes = body.notes
    if body.entry_prices is not None:
        strategy.entry_prices = json.dumps(body.entry_prices)
    if body.entry_net_debit is not None:
        strategy.entry_net_debit = body.entry_net_debit
    if body.entry_date is not None:
        import datetime as dt
        try:
            strategy.entry_date = dt.datetime.fromisoformat(body.entry_date)
        except (ValueError, TypeError):
            pass

    await db.commit()
    await db.refresh(strategy)
    return _to_out(strategy)


@router.delete("/{strategy_id}", status_code=204)
async def delete_saved_strategy(
    strategy_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a saved strategy (ownership verified)."""
    result = await db.execute(
        select(SavedStrategy).where(
            SavedStrategy.id == strategy_id,
            SavedStrategy.user_id == user.id,
        )
    )
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Saved strategy not found")

    await db.delete(strategy)
    await db.commit()


# --------------------------------------------------------------------------
# Trade Tracking Routes
# --------------------------------------------------------------------------

def _has_banked_realized(s: SavedStrategy) -> bool:
    """True if the trade has realized P&L banked from a (partial or full) close —
    i.e. it carries closed_legs or a non-zero realized_pnl. Used so the Closed tab can
    surface the realized chunk of a still-active PARTIAL close."""
    try:
        p = json.loads(s.parameters or "{}")
    except (ValueError, TypeError):
        return False
    if p.get("closed_legs"):
        return True
    try:
        return abs(float(p.get("realized_pnl") or 0)) > 0.005
    except (ValueError, TypeError):
        return False


def _has_shown_realized(s: SavedStrategy, include_rolls: bool = False) -> bool:
    """Whether a trade should surface in the Closed tab. A CLOSED trade always does.
    A still-active trade shows only when it banked realized from a genuine (partial)
    close; realized banked *purely* from rolls is hidden by default — the campaign is
    still in play — and revealed only when the user opts in with ``include_rolls``."""
    if s.trade_status == "closed":
        return True
    if not _has_banked_realized(s):
        return False
    if include_rolls:
        return True
    p = _safe_params(s)
    if any(not l.get("roll") for l in (p.get("closed_legs") or [])):
        return True
    try:
        total = float(p.get("realized_pnl") or 0)
    except (ValueError, TypeError):
        total = 0.0
    return abs(total - _roll_realized(p)) > 0.005


# ── Closed-tab monthly rollups ────────────────────────────────────────────────
# A closed trade never re-prices, so a PRIOR month's realized numbers are immutable.
# We compute each prior month ONCE, store the small rollup in the DB cache, and stop
# shipping those trades' full records to the browser. The client then downloads only the
# CURRENT month's trades (expandable) plus a handful of tiny summaries, instead of the
# entire closed history on every landing.
#
# "Frozen" (rollup-only) month  ⇔  strictly before the current month AND untouched by any
# still-open PARTIAL close (a partial can bank more realized later, so its month stays live).
# Everything else — the current month, undated closes, any prior month a partial touched —
# ships as full rows ("loose") so the user can still expand it.

_CLOSED_LEDGER_TTL = 180 * 86400   # rollups are immutable; the signature below guards staleness


def _closed_ledger_key(user_id: int) -> str:
    return f"closed_ledger:{user_id}:v1"


def _now_month() -> str:
    n = _dt.datetime.now(_dt.timezone.utc)
    return f"{n.year:04d}-{n.month:02d}"


def _month_label(month: str) -> str:
    if month == "undated":
        return "Undated"
    try:
        y, m = month.split("-")
        return _dt.date(int(y), int(m), 1).strftime("%B %Y")
    except (ValueError, TypeError):
        return month


def _is_short_action(action: Optional[str]) -> bool:
    a = (action or "").lower()
    return "sell" in a or "short" in a


def _safe_params(s: SavedStrategy) -> dict:
    try:
        return json.loads(s.parameters or "{}")
    except (ValueError, TypeError):
        return {}


def _close_month_of(s: SavedStrategy, params: dict) -> Optional[str]:
    """The 'YYYY-MM' bucket a trade's realized P&L belongs to. Full close → exit_date;
    partial (still-active, banked) → the latest closed-leg date; else updated_at. Sliced from
    the ISO string so the frontend can group the returned rows by this exact tag (no local-vs-
    UTC month drift between the frozen summaries and the live current-month rows)."""
    iso = None
    if s.trade_status == "closed" and s.exit_date:
        iso = s.exit_date.isoformat()
    else:
        for l in (params.get("closed_legs") or []):
            ca = l.get("closed_at")
            if ca and (iso is None or ca > iso):
                iso = ca
    if not iso and s.updated_at:
        iso = s.updated_at.isoformat()
    return iso[:7] if iso and len(iso) >= 7 else None


def _closed_realized(s: SavedStrategy, params: dict, include_rolls: bool = True) -> Optional[float]:
    """Authoritative banked realized P&L — mirrors the frontend's closedRealized():
    parameters.realized_pnl, else the legacy exit_net.

    ``include_rolls`` only affects a still-ACTIVE trade: a roll banks realized into
    realized_pnl as a cost-basis adjustment, but the campaign is still live, so the
    Closed tab excludes that roll portion by default (include_rolls=False). A fully
    CLOSED trade always reports its whole realized (the campaign is over — rolls
    included), regardless of the flag."""
    rp = params.get("realized_pnl")
    base: Optional[float] = None
    if rp is not None:
        try:
            base = float(rp)
        except (ValueError, TypeError):
            base = None
    if base is None and s.exit_net is not None:
        try:
            base = float(s.exit_net)
        except (ValueError, TypeError):
            base = None
    if base is None:
        return None
    if s.trade_status != "closed" and not include_rolls:
        base -= _roll_realized(params)
    return round(base, 2)


def _closed_cost_proceeds(params: dict, include_roll_legs: bool = True) -> tuple[Optional[float], Optional[float]]:
    """Σ buys (cost) and Σ sells (proceeds) from closed_legs — mirrors buildClosedLedger:
    a short leg sells-to-open / buys-to-close; a long leg the reverse. (None, None) when the
    legs carry no prices (then realized falls back to the stored cumulative value).

    ``include_roll_legs=False`` skips legs banked by a roll, so a still-active rolled trade's
    Closed-tab row reflects only its genuine (non-roll) closes by default."""
    cost = proceeds = 0.0
    saw = False
    for l in (params.get("closed_legs") or []):
        if not include_roll_legs and l.get("roll"):
            continue
        try:
            qty = abs(float(l.get("qty") or 0))
        except (ValueError, TypeError):
            qty = 0.0
        if qty <= 0:
            continue
        mult = 1 if l.get("type") == "stock" else 100
        try:
            entry = float(l.get("entry_price") or 0)
            exit_ = float(l.get("exit_price") or 0)
        except (ValueError, TypeError):
            continue
        saw = True
        if _is_short_action(l.get("action")):
            proceeds += entry * mult * qty
            cost += exit_ * mult * qty
        else:
            cost += entry * mult * qty
            proceeds += exit_ * mult * qty
    if not saw:
        return None, None
    return round(cost, 2), round(proceeds, 2)


def _closed_row_out(s: SavedStrategy) -> dict:
    """Full trade record for the Closed tab, tagged with its canonical close-month so the
    frontend groups it consistently with the frozen summaries."""
    d = _to_out(s).model_dump()
    d["close_month"] = _close_month_of(s, _safe_params(s)) or "undated"
    return d


def _compute_closed_ledger(rows: list[SavedStrategy], now_m: str, include_rolls: bool = False) -> dict:
    """Partition closed/partial trades into frozen prior-month rollups, loose ids, and the
    current-month + loose full rows. Pure arithmetic over persisted fields — no pricing.

    A fully-closed trade always counts its whole realized (rolls included — the campaign is
    done); a still-active partial excludes its roll-banked portion unless ``include_rolls``."""
    contribs = []
    partial_months: set[str] = set()
    for s in rows:
        params = _safe_params(s)
        m = _close_month_of(s, params) or "undated"
        is_partial = s.trade_status != "closed"
        count_rolls = include_rolls or not is_partial
        cost, proceeds = _closed_cost_proceeds(params, include_roll_legs=count_rolls)
        contribs.append({
            "s": s, "month": m, "is_partial": is_partial,
            "realized": _closed_realized(s, params, include_rolls=count_rolls),
            "cost": cost, "proceeds": proceeds, "bpr": _closed_trade_bpr(s),
        })
        if is_partial and m != "undated":
            partial_months.add(m)

    impure = partial_months | {now_m}
    frozen: dict[str, dict] = {}
    loose_ids: list[int] = []
    current_rows: list[SavedStrategy] = []

    for c in contribs:
        m = c["month"]
        if m != "undated" and m < now_m and m not in impure:
            g = frozen.get(m)
            if g is None:
                g = frozen[m] = {"month": m, "label": _month_label(m), "count": 0,
                                 "wins": 0, "scored": 0, "cost": 0.0, "proceeds": 0.0,
                                 "realized": 0.0, "bpr": 0.0}
            g["count"] += 1
            r = c["realized"]
            if r is not None:
                g["scored"] += 1
                g["realized"] += r
                if r > 0:
                    g["wins"] += 1
            if c["cost"] is not None:
                g["cost"] += c["cost"]
            if c["proceeds"] is not None:
                g["proceeds"] += c["proceeds"]
            if c["bpr"]:
                g["bpr"] += c["bpr"]
        else:
            current_rows.append(c["s"])
            if m == "undated" or m < now_m:   # a prior/undated loose row (not caught by the month query)
                loose_ids.append(c["s"].id)

    for g in frozen.values():
        for k in ("cost", "proceeds", "realized", "bpr"):
            g[k] = round(g[k], 2)

    frozen_list = sorted(frozen.values(), key=lambda g: g["month"], reverse=True)
    return {"frozen": frozen_list, "loose_ids": loose_ids, "current_rows": current_rows}


def _closed_ledger_response(now_m: str, frozen: list[dict], current_rows: list[SavedStrategy],
                            stored: bool, include_rolls: bool = False) -> dict:
    total = sum((g.get("realized") or 0) for g in frozen)
    total += sum((_closed_realized(s, _safe_params(s), include_rolls=include_rolls) or 0)
                 for s in current_rows)
    return {
        "current_month": now_m,
        "current_month_label": _month_label(now_m),
        "frozen_months": frozen,
        "trades": [_closed_row_out(s) for s in current_rows],
        "total_realized": round(total, 2),
        "stored": stored,
        "include_rolls": include_rolls,
    }


async def _closed_ledger_sig(db: AsyncSession, user_id: int) -> str:
    """Cheap staleness signature over ALL of the user's trades (count + latest touch) plus the
    current month. Any close / partial / edit / delete changes count or max(updated_at), and a
    month rollover changes the suffix — so a matching signature means the stored rollups are
    still valid, with no manual cache invalidation to get wrong."""
    row = (await db.execute(
        select(sql_func.count(SavedStrategy.id), sql_func.max(SavedStrategy.updated_at))
        .where(SavedStrategy.user_id == user_id, SavedStrategy.trade_status.isnot(None))
    )).one()
    cnt, mx = row[0], row[1]
    return f"{cnt}:{mx.isoformat() if mx else '-'}:{_now_month()}"


@router.get("/trades/closed-ledger")
async def closed_ledger(
    include_rolls: bool = Query(
        False,
        description="Also surface still-active ROLLED trades' partial realized (the cost-basis "
                    "adjustment banked on each roll). Off by default — a rolled campaign is still "
                    "in play, so its roll P&L stays out of the Closed ledger until opted in."),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Closed-tab data with prior months pre-summarized.

    Returns the CURRENT month's closed trades (plus partial-close realized and any loose prior
    rows) as FULL records so they stay expandable, and every strictly-prior immutable month as a
    compact stored rollup {month, count, wins, scored, cost, proceeds, realized, bpr}. The heavy
    full closed history is no longer shipped to the browser on every landing.

    Storage: the prior-month rollups live in the DB cache, guarded by a self-validating
    signature (see _closed_ledger_sig). On a signature hit we reuse the stored summaries and
    fetch only the current slice (current-month closes + active partials + loose ids), so the
    older closed rows are never even loaded.

    Rolls: the cached (default) path never counts a still-active trade's roll-banked realized —
    those trades are the campaign that's still open. ``include_rolls=true`` is an opt-in that
    recomputes cold with the roll partials folded in (frozen fully-closed months are identical
    either way, so nothing about the cache needs to change)."""
    from ..services.cache_service import get_cached, set_cached
    from sqlalchemy import or_

    now_m = _now_month()

    if include_rolls:
        # Opt-in view: recompute cold with roll partials included. Skip the (roll-excluding)
        # cache entirely rather than store a second variant — the toggle is a deliberate,
        # infrequent user action.
        all_rows = list((await db.execute(select(SavedStrategy).where(
            SavedStrategy.user_id == user.id, SavedStrategy.trade_status.isnot(None),
        ))).scalars().all())
        shown = [s for s in all_rows if _has_shown_realized(s, include_rolls=True)]
        comp = _compute_closed_ledger(shown, now_m, include_rolls=True)
        return _closed_ledger_response(now_m, comp["frozen"], comp["current_rows"],
                                       stored=False, include_rolls=True)

    sig = await _closed_ledger_sig(db, user.id)
    cached = await get_cached(db, _closed_ledger_key(user.id))

    if cached and cached.get("sig") == sig:
        frozen = cached.get("frozen") or []
        loose_ids = cached.get("loose_ids") or []
        month_start = _dt.datetime(int(now_m[:4]), int(now_m[5:7]), 1, tzinfo=_dt.timezone.utc)
        conds = [
            (SavedStrategy.trade_status == "closed") & (SavedStrategy.exit_date >= month_start),
            SavedStrategy.trade_status == "active",   # partials filtered below
        ]
        if loose_ids:
            conds.append(SavedStrategy.id.in_(loose_ids))
        rows = list((await db.execute(select(SavedStrategy).where(
            SavedStrategy.user_id == user.id,
            SavedStrategy.trade_status.isnot(None),
            or_(*conds),
        ))).scalars().all())
        current = [s for s in rows if _has_shown_realized(s, include_rolls=False)]
        return _closed_ledger_response(now_m, frozen, current, stored=True)

    # Cold or stale → recompute everything and refreeze the prior months.
    all_rows = list((await db.execute(select(SavedStrategy).where(
        SavedStrategy.user_id == user.id, SavedStrategy.trade_status.isnot(None),
    ))).scalars().all())
    closed_or_banked = [s for s in all_rows if _has_shown_realized(s, include_rolls=False)]
    comp = _compute_closed_ledger(closed_or_banked, now_m)
    try:
        await set_cached(db, _closed_ledger_key(user.id), {
            "sig": sig, "now_month": now_m,
            "frozen": comp["frozen"], "loose_ids": comp["loose_ids"],
        }, ttl_seconds=_CLOSED_LEDGER_TTL)
    except Exception:  # noqa: BLE001 — caching must never break the response
        pass
    return _closed_ledger_response(now_m, comp["frozen"], comp["current_rows"], stored=False)


@router.get("/trades/closed-ledger/{month}")
async def closed_ledger_month(
    month: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Full closed-trade records for ONE prior 'YYYY-MM' month — lazily loaded when the user
    expands a stored (frozen) month band on the Closed tab. Frozen months hold only fully-closed
    trades (partials are never frozen), so match on exit_date within the month, then confirm each
    row's canonical close-month to guard the tz edge."""
    import re
    if not re.fullmatch(r"\d{4}-\d{2}", month or ""):
        raise HTTPException(status_code=400, detail="month must be 'YYYY-MM'")
    y, mo = int(month[:4]), int(month[5:7])
    start = _dt.datetime(y, mo, 1, tzinfo=_dt.timezone.utc)
    end = _dt.datetime(y + (1 if mo == 12 else 0), (1 if mo == 12 else mo + 1), 1, tzinfo=_dt.timezone.utc)
    rows = list((await db.execute(select(SavedStrategy).where(
        SavedStrategy.user_id == user.id,
        SavedStrategy.trade_status == "closed",
        SavedStrategy.exit_date >= start,
        SavedStrategy.exit_date < end,
    ).order_by(SavedStrategy.exit_date.desc()))).scalars().all())
    return [_closed_row_out(s) for s in rows if _close_month_of(s, _safe_params(s)) == month]


class DeleteClosedPartIn(BaseModel):
    part: str = Field(..., description="'options' | 'stock'")


@router.post("/{strategy_id}/delete-closed-part", response_model=Optional[SavedStrategyOut])
async def delete_closed_part(
    strategy_id: int,
    body: DeleteClosedPartIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete just the OPTION legs or the STOCK leg from a closed trade's realized ledger, so a
    combo (e.g. a covered call's stock vs its call) can be pruned one part at a time. Recomputes
    realized_pnl from the kept closed_legs. If nothing is left AND the trade is fully closed, the
    whole trade is deleted (returns null); a still-active partial just loses its banked chunk and
    stays open. Bumps updated_at, which invalidates the frozen closed-ledger cache signature."""
    part = (body.part or "").lower()
    if part not in ("options", "stock"):
        raise HTTPException(status_code=400, detail="part must be 'options' or 'stock'")
    result = await db.execute(select(SavedStrategy).where(
        SavedStrategy.id == strategy_id, SavedStrategy.user_id == user.id))
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Trade not found")

    params = json.loads(strategy.parameters) if strategy.parameters else {}
    closed = list(params.get("closed_legs") or [])

    def _is_stock(l: dict) -> bool:
        return (l.get("type") or "").lower() == "stock"

    keep = [l for l in closed if (_is_stock(l) if part == "options" else not _is_stock(l))]
    if len(keep) == len(closed):
        raise HTTPException(status_code=400, detail=f"No {part} legs to delete on this trade")

    # Nothing left AND fully closed → drop the whole trade.
    if not keep and strategy.trade_status == "closed":
        await db.delete(strategy)
        await db.commit()
        return None

    params["closed_legs"] = keep
    new_real = round(sum(float(l.get("realized") or 0) for l in keep), 2)
    params["realized_pnl"] = new_real
    strategy.parameters = json.dumps(params)
    if strategy.trade_status == "closed":
        strategy.exit_net = new_real
        strategy.exit_prices = json.dumps(
            [{"leg_index": r.get("leg_index"), "exit_price": r.get("exit_price")} for r in keep])
    await db.commit()
    await db.refresh(strategy)
    return _to_out(strategy)


@router.get("/trades", response_model=list[SavedStrategyOut])
async def list_trades(
    status: Optional[str] = Query(None, description="Filter: 'active' or 'closed'"),
    include_partial: bool = Query(
        False,
        description="With status='closed', also return still-ACTIVE trades that banked realized "
                    "P&L from a partial close, so their realized portion shows in the Closed ledger."),
    include_rolls: bool = Query(
        False,
        description="With status='closed'+include_partial, also include trades whose only banked "
                    "realized came from ROLLS (hidden by default — the campaign is still open)."),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List strategies marked as trades (active or closed)."""
    q = select(SavedStrategy).where(
        SavedStrategy.user_id == user.id,
        SavedStrategy.trade_status.isnot(None),
    )
    if status == "closed" and include_partial:
        # Pull closed + active, then keep active ones only when they carry realized P&L.
        q = q.where(SavedStrategy.trade_status.in_(["closed", "active"]))
    elif status:
        q = q.where(SavedStrategy.trade_status == status)
    q = q.order_by(SavedStrategy.updated_at.desc())
    result = await db.execute(q)
    rows = result.scalars().all()
    if status == "closed" and include_partial:
        rows = [s for s in rows if _has_shown_realized(s, include_rolls=include_rolls)]
    return [_to_out(s) for s in rows]


@router.post("/{strategy_id}/mark-traded", response_model=SavedStrategyOut)
async def mark_strategy_as_traded(
    strategy_id: int,
    body: MarkTradedIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark an existing saved strategy as an active trade with entry details."""
    result = await db.execute(
        select(SavedStrategy).where(
            SavedStrategy.id == strategy_id,
            SavedStrategy.user_id == user.id,
        )
    )
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Saved strategy not found")

    import datetime as dt
    strategy.trade_status = "active"
    strategy.entry_prices = json.dumps(body.entry_prices)
    strategy.entry_net_debit = body.entry_net_debit
    if body.entry_date:
        try:
            strategy.entry_date = dt.datetime.fromisoformat(body.entry_date)
        except (ValueError, TypeError):
            strategy.entry_date = dt.datetime.now(dt.timezone.utc)
    else:
        strategy.entry_date = dt.datetime.now(dt.timezone.utc)
    strategy.order_source = body.order_source
    if body.broker_order_id:
        strategy.broker_order_id = body.broker_order_id

    # Seed the opening ledger row (mirrors create_manual_trade behaviour) so
    # transaction history is complete from the moment a strategy becomes a trade.
    stype = (strategy.strategy_type or "").lower()
    if stype.startswith("stock"):
        params_dict = json.loads(strategy.parameters or "{}")
        ep_list = body.entry_prices if isinstance(body.entry_prices, list) else []
        init_shares = float(params_dict.get("shares") or params_dict.get("quantity") or 0)
        init_price = float(ep_list[0].get("price") if ep_list and isinstance(ep_list[0], dict) else 0)
        if init_shares > 0 and init_price > 0:
            signed_qty = -init_shares if "short" in stype else init_shares
            db.add(TradeTransaction(
                strategy_id=strategy.id,
                action="open",
                leg_index=None,
                quantity=signed_qty,
                price=init_price,
                fees=0.0,
                executed_at=strategy.entry_date,
                source=body.order_source,
                note="Initial position",
            ))

    await db.commit()
    await db.refresh(strategy)
    return _to_out(strategy)


@router.post("/{strategy_id}/close-trade", response_model=SavedStrategyOut)
async def close_trade(
    strategy_id: int,
    body: CloseTradeIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Close an active trade with exit details."""
    result = await db.execute(
        select(SavedStrategy).where(
            SavedStrategy.id == strategy_id,
            SavedStrategy.user_id == user.id,
            SavedStrategy.trade_status == "active",
        )
    )
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Active trade not found")

    import datetime as dt
    strategy.trade_status = "closed"
    strategy.exit_prices = json.dumps(body.exit_prices)
    strategy.exit_net = body.exit_net
    strategy.exit_date = dt.datetime.now(dt.timezone.utc)

    await db.commit()
    await db.refresh(strategy)
    return _to_out(strategy)


@router.post("/{strategy_id}/close-position", response_model=SavedStrategyOut)
async def close_position(
    strategy_id: int,
    body: ClosePositionIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Close SOME or ALL of a placed trade at the prices the user actually got.

    Computes realized P&L per leg (authoritative, banked into
    parameters.realized_pnl + parameters.closed_legs), writes a 'close' ledger row
    per leg for the audit trail, drops the closed legs from legs_data, and — only
    when nothing is left (no option legs AND no stock) — flips trade_status to
    'closed' so the trade moves to the Closed tab. A partial close leaves the trade
    active with its remaining legs.

    Sign convention (mirrors trade P&L math): a SHORT option earns (entry − exit),
    a LONG option earns (exit − entry); stock earns (exit − entry) long / (entry −
    exit) short. All option amounts are ×100×contracts.
    """
    import datetime as dt
    from ..services.trade_math import realized_close_pnl
    result = await db.execute(
        select(SavedStrategy).where(
            SavedStrategy.id == strategy_id,
            SavedStrategy.user_id == user.id,
            SavedStrategy.trade_status == "active",
        )
    )
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Active trade not found")

    legs = json.loads(strategy.legs_data) if strategy.legs_data else []
    params = json.loads(strategy.parameters) if strategy.parameters else {}
    entry_prices = json.loads(strategy.entry_prices) if strategy.entry_prices else []
    shares = float(params.get("shares") or 0)
    is_short_stock = "short" in (strategy.strategy_type or "").lower()

    # entry-price index convention: on a combo (stock + options), the stock's entry
    # sits at entry_prices[0] and option leg i is at entry_prices[i+1].
    offset = 1 if (shares > 0 and len(entry_prices) == len(legs) + 1) else 0

    def _leg_entry(i: int, leg: dict) -> float:
        # Robust entry premium — the SAME resolver get_live_pnl uses (leg's own premium →
        # entry_prices matched by strike/right → positional). This keeps realized-at-close
        # consistent with live P&L AND stops a combo's option leg from inheriting the stock's
        # entry_prices[0] when the array length doesn't line up (the "$278 CSP" bug).
        p = _leg_entry_premium(leg, i + offset, entry_prices)
        if p is not None:
            return p
        j = i + offset
        ep = (entry_prices[j] or {}).get("price") if 0 <= j < len(entry_prices) else None
        if ep in (None, ""):
            ep = leg.get("mid") or leg.get("price")
        try:
            return float(ep or 0)
        except (TypeError, ValueError):
            return 0.0

    executed = dt.datetime.now(dt.timezone.utc)
    if body.executed_at:
        try:
            executed = dt.datetime.fromisoformat(body.executed_at)
        except (ValueError, TypeError):
            pass

    close_idx = {l.leg_index for l in body.legs if 0 <= l.leg_index < len(legs)}
    exit_of = {l.leg_index: float(l.exit_price) for l in body.legs}
    closed_records = list(params.get("closed_legs") or [])
    realized_total = 0.0

    # ---- option legs ----
    for i in sorted(close_idx):
        leg = legs[i]
        qty = float(leg.get("qty") or leg.get("contracts") or 1)
        entry = _leg_entry(i, leg)
        exitp = exit_of[i]
        is_short = any(k in str(leg.get("action", "")).upper() for k in ("SELL", "SHORT"))
        realized = realized_close_pnl(leg.get("action", ""), entry, exitp, qty, is_option=True)
        realized_total += realized
        closed_records.append({
            "leg_index": i, "action": leg.get("action"), "type": leg.get("type"),
            "strike": leg.get("strike"), "qty": qty, "entry_price": round(entry, 4),
            "exit_price": round(exitp, 4), "realized": round(realized, 2),
            "closed_at": executed.isoformat(),
            "leg": leg,   # full original leg (expiry etc.) so a mistaken close can be re-opened faithfully
        })
        db.add(TradeTransaction(
            strategy_id=strategy.id, action="close", leg_index=i,
            quantity=(qty if is_short else -qty),      # flatten: buy-to-close short / sell-to-close long
            price=exitp, fees=0.0, executed_at=executed, source="manual",
            note=body.note or f"Close {leg.get('action')} {leg.get('type')} ${leg.get('strike')} @ ${exitp}",
        ))

    remaining_legs = [l for i, l in enumerate(legs) if i not in close_idx]

    # ---- stock ----
    if body.close_stock and shares > 0:
        entry_stock = (entry_prices[0] or {}).get("price") if (offset == 1 and entry_prices) else None
        if entry_stock in (None, ""):
            entry_stock = params.get("avg_cost")
        try:
            entry_stock = float(entry_stock or 0)
        except (TypeError, ValueError):
            entry_stock = 0.0
        exitp = float(body.stock_exit_price or 0)
        realized = realized_close_pnl("SELL" if is_short_stock else "BUY", entry_stock, exitp, shares, is_option=False)
        realized_total += realized
        closed_records.append({
            "leg_index": None, "type": "stock", "qty": shares,
            "entry_price": round(entry_stock, 4), "exit_price": round(exitp, 4),
            "realized": round(realized, 2), "closed_at": executed.isoformat(),
        })
        db.add(TradeTransaction(
            strategy_id=strategy.id, action="close", leg_index=None,
            quantity=(shares if is_short_stock else -shares), price=exitp, fees=0.0,
            executed_at=executed, source="manual",
            note=body.note or f"Close {int(shares)} shares @ ${exitp}",
        ))
        params["shares"] = 0

    if not close_idx and not (body.close_stock and shares > 0):
        raise HTTPException(status_code=400, detail="Nothing selected to close")

    params["realized_pnl"] = round(float(params.get("realized_pnl") or 0.0) + realized_total, 2)
    params["closed_legs"] = closed_records
    strategy.legs_data = json.dumps(remaining_legs)
    strategy.parameters = json.dumps(params)

    fully_closed = len(remaining_legs) == 0 and float(params.get("shares") or 0) == 0
    if fully_closed:
        strategy.trade_status = "closed"
        strategy.exit_date = executed
        strategy.exit_prices = json.dumps(
            [{"leg_index": r.get("leg_index"), "exit_price": r.get("exit_price")} for r in closed_records]
        )
        strategy.exit_net = params["realized_pnl"]   # realized P&L (legacy field kept populated)

    await db.commit()
    await db.refresh(strategy)
    return _to_out(strategy)


class RollLegIn(BaseModel):
    """One replacement option leg to roll INTO (open)."""
    action: str = Field(..., description="'buy' | 'sell'")
    type: str = Field(..., description="'call' | 'put'")
    strike: float = Field(..., gt=0)
    expiration: str = Field(..., min_length=4)   # ISO 'YYYY-MM-DD'
    qty: float = Field(..., gt=0)
    premium: float = Field(..., ge=0)            # per-share credit(sell)/debit(buy) at the roll fill


class RollPositionIn(BaseModel):
    """Roll one or more legs in ONE action — buy back the tested leg(s) and open the
    replacement leg(s) — keeping the position as a single continuing campaign. Realized
    P&L on the bought-back legs is banked as a cost-basis ADJUSTMENT (tagged as a roll),
    not as a standalone closed trade, so the Closed tab isn't polluted while the trade is
    still live and the new cost basis carries the running P&L forward."""
    close_legs: list[ClosePositionLegIn] = Field(default_factory=list)   # legs to roll out of
    open_legs: list[RollLegIn] = Field(default_factory=list)             # legs to roll into
    executed_at: Optional[str] = None
    note: Optional[str] = None


@router.post("/{strategy_id}/roll-position", response_model=SavedStrategyOut)
async def roll_position(
    strategy_id: int,
    body: RollPositionIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Roll a placed trade: close the tested leg(s) and open the replacement leg(s) as ONE
    continuing campaign, so the realized P&L on the buy-back adjusts the cost basis instead of
    being reported as a separate closed trade.

    Every other platform books a roll as *close old + open new*, which scatters one economic
    trade across two P&L lines and makes it impossible to see whether the campaign is still
    ahead. Here the trade stays ACTIVE, the buy-back realized is banked into
    parameters.realized_pnl AND parameters.roll_realized_pnl (and each closed_legs record is
    tagged ``roll``), and the effective breakeven / net credit fold that running total in — see
    _roll_summary + trade_math.roll_target_credit. A user can roll the same trade any number of
    times; the roll realized accumulates. Reuses the exact realized-at-close resolver
    (_leg_entry → _leg_entry_premium) that live P&L uses, so the numbers reconcile."""
    import datetime as dt
    import uuid
    from ..services.trade_math import realized_close_pnl

    result = await db.execute(
        select(SavedStrategy).where(
            SavedStrategy.id == strategy_id,
            SavedStrategy.user_id == user.id,
            SavedStrategy.trade_status == "active",
        )
    )
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Active trade not found")
    if not body.close_legs and not body.open_legs:
        raise HTTPException(status_code=400, detail="A roll needs at least one leg to close or open")

    legs = json.loads(strategy.legs_data) if strategy.legs_data else []
    params = json.loads(strategy.parameters) if strategy.parameters else {}
    entry_prices = json.loads(strategy.entry_prices) if strategy.entry_prices else []
    shares = float(params.get("shares") or 0)
    offset = 1 if (shares > 0 and len(entry_prices) == len(legs) + 1) else 0

    def _leg_entry(i: int, leg: dict) -> float:
        # SAME robust resolver as close_position / live P&L (leg premium → strike/right → positional).
        p = _leg_entry_premium(leg, i + offset, entry_prices)
        if p is not None:
            return p
        j = i + offset
        ep = (entry_prices[j] or {}).get("price") if 0 <= j < len(entry_prices) else None
        if ep in (None, ""):
            ep = leg.get("mid") or leg.get("price")
        try:
            return float(ep or 0)
        except (TypeError, ValueError):
            return 0.0

    executed = dt.datetime.now(dt.timezone.utc)
    if body.executed_at:
        try:
            executed = dt.datetime.fromisoformat(body.executed_at)
        except (ValueError, TypeError):
            pass

    roll_id = uuid.uuid4().hex[:12]
    prior_rolls = list(params.get("rolls") or [])
    seq = len(prior_rolls) + 1

    close_idx = {l.leg_index for l in body.close_legs if 0 <= l.leg_index < len(legs)}
    exit_of = {l.leg_index: float(l.exit_price) for l in body.close_legs}
    closed_records = list(params.get("closed_legs") or [])
    realized_total = 0.0
    buyback_cost = 0.0            # $ paid to buy back short legs (informational, for the roll record)
    from_descr: list[str] = []

    # ---- roll OUT of the tested legs (bank realized, tagged as a roll) ----
    for i in sorted(close_idx):
        leg = legs[i]
        qty = float(leg.get("qty") or leg.get("contracts") or 1)
        entry = _leg_entry(i, leg)
        exitp = exit_of[i]
        is_short = any(k in str(leg.get("action", "")).upper() for k in ("SELL", "SHORT"))
        realized = realized_close_pnl(leg.get("action", ""), entry, exitp, qty, is_option=True)
        realized_total += realized
        if is_short:
            buyback_cost += exitp * 100.0 * qty
        closed_records.append({
            "leg_index": i, "action": leg.get("action"), "type": leg.get("type"),
            "strike": leg.get("strike"), "qty": qty, "entry_price": round(entry, 4),
            "exit_price": round(exitp, 4), "realized": round(realized, 2),
            "closed_at": executed.isoformat(), "leg": leg,
            "roll": True, "roll_id": roll_id, "roll_seq": seq,
        })
        db.add(TradeTransaction(
            strategy_id=strategy.id, action="close", leg_index=i,
            quantity=(qty if is_short else -qty),
            price=exitp, fees=0.0, executed_at=executed, source="manual",
            note=f"roll:{roll_id} · out {leg.get('action')} {leg.get('type')} ${leg.get('strike')} @ ${exitp}",
        ))
        from_descr.append(
            f"{leg.get('action')} {leg.get('type')} ${leg.get('strike')}"
            f"{' exp ' + str(leg.get('expiration') or leg.get('expiry')) if (leg.get('expiration') or leg.get('expiry')) else ''}")

    remaining_legs = [l for i, l in enumerate(legs) if i not in close_idx]

    # ---- roll INTO the replacement legs (open, appended to the same position) ----
    new_credit = 0.0
    to_descr: list[str] = []
    added_legs: list[dict] = []
    for nl in body.open_legs:
        is_sell = nl.action.lower() == "sell"
        leg_dict = {
            "action": nl.action.lower(), "type": nl.type.lower(),
            "strike": float(nl.strike), "qty": float(nl.qty),
            "expiration": nl.expiration, "premium": float(nl.premium),
            "label": f"{'Short' if is_sell else 'Long'} {nl.type.title()} ${nl.strike:g}",
        }
        added_legs.append(leg_dict)
        new_credit += (1 if is_sell else -1) * float(nl.premium) * float(nl.qty) * 100.0
        db.add(TradeTransaction(
            strategy_id=strategy.id, action="add", leg_index=None,
            quantity=(-nl.qty if is_sell else nl.qty),   # short opens negative, long positive
            price=float(nl.premium), fees=0.0, executed_at=executed, source="manual",
            note=f"roll:{roll_id} · into {leg_dict['action']} {leg_dict['type']} ${nl.strike:g} exp {nl.expiration}",
        ))
        to_descr.append(f"{leg_dict['action']} {leg_dict['type']} ${nl.strike:g} exp {nl.expiration}")

    new_legs_data = remaining_legs + added_legs

    # Rebuild entry_prices to mirror the new legs_data (stock at [0] on a combo, then one
    # strike/type-tagged row per leg using each leg's own entry premium) so the robust
    # strike/right resolver always lines up — avoids the positional "$278 CSP" drift.
    new_entry_prices: list[dict] = []
    if shares > 0:
        if offset == 1 and entry_prices:
            new_entry_prices.append(entry_prices[0])
        else:
            new_entry_prices.append({"price": params.get("avg_cost")})
    for l in new_legs_data:
        new_entry_prices.append({
            "price": l.get("premium"), "strike": l.get("strike"), "type": l.get("type"),
        })

    params["realized_pnl"] = round(float(params.get("realized_pnl") or 0.0) + realized_total, 2)
    params["roll_realized_pnl"] = round(float(params.get("roll_realized_pnl") or 0.0) + realized_total, 2)
    params["closed_legs"] = closed_records
    params["rolls"] = prior_rolls + [{
        "roll_id": roll_id, "seq": seq, "date": executed.isoformat(),
        "realized": round(realized_total, 2), "buyback_cost": round(buyback_cost, 2),
        "new_credit": round(new_credit, 2), "from": from_descr, "to": to_descr,
        "note": body.note or None,
    }]
    params.pop("last_pnl", None)          # stale — the legs just changed; force a fresh refresh
    params.pop("last_pnl_at", None)

    new_leg_metas = [{"i": i, "_leg": l, "strike": l.get("strike"), "type": l.get("type"),
                      "qty": l.get("qty") or 1, "action": l.get("action")}
                     for i, l in enumerate(new_legs_data)
                     if str(l.get("type", "")).upper() in ("CALL", "PUT")]
    nd = option_legs_net_debit(new_leg_metas, new_entry_prices) if new_leg_metas else None
    if nd is not None:
        strategy.entry_net_debit = nd

    strategy.legs_data = json.dumps(new_legs_data)
    strategy.entry_prices = json.dumps(new_entry_prices)
    strategy.parameters = json.dumps(params)

    # A roll keeps the trade ACTIVE. Only a roll into literally nothing (and no stock) collapses
    # to a close — a degenerate case, but handled so the trade doesn't get stranded.
    if not new_legs_data and float(params.get("shares") or 0) == 0:
        strategy.trade_status = "closed"
        strategy.exit_date = executed
        strategy.exit_net = params["realized_pnl"]

    await db.commit()
    await db.refresh(strategy)
    return _to_out(strategy)


@router.post("/{strategy_id}/reopen", response_model=SavedStrategyOut)
async def reopen_trade(
    strategy_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Undo a close — restore a closed (or partially-closed) trade to ACTIVE with all its legs,
    for when the user closed one by mistake.

    Reverses close_position / close_trade: rebuilds legs_data from the GENUINE closed_legs (each
    record keeps the full original leg going forward; legacy records reconstruct from their flat
    fields, so a pre-this-change close may come back without its expiry — the user can edit it),
    restores shares, clears the genuine-close realized / exit_* fields, and deletes the 'close'
    ledger rows so a mistaken close leaves no trace (and, for stock, so the ledger walk doesn't
    immediately re-close it). ROLL-banked legs are preserved (they're cost-basis adjustments to a
    still-open campaign, and their replacement legs are already live — restoring them would double
    the position); their roll realized is kept. Idempotent-safe: a trade with nothing genuine to
    undo is a 400."""
    result = await db.execute(select(SavedStrategy).where(
        SavedStrategy.id == strategy_id, SavedStrategy.user_id == user.id))
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Trade not found")

    params = json.loads(strategy.parameters) if strategy.parameters else {}
    closed = list(params.get("closed_legs") or [])
    # A ROLL's closed legs are cost-basis adjustments to a still-open campaign, not closes to
    # undo — and their replacement legs are already live. Reopen must leave them alone, or it
    # would restore a rolled-out leg on top of the one it was rolled into (doubling the position).
    restorable = [r for r in closed if not r.get("roll")]
    roll_legs = [r for r in closed if r.get("roll")]
    if strategy.trade_status != "closed" and not restorable:
        raise HTTPException(status_code=400, detail="Nothing to re-open — this trade has no closed legs to undo")

    legs = json.loads(strategy.legs_data) if strategy.legs_data else []

    def _restore_leg(rec: dict) -> dict:
        leg = rec.get("leg")
        if isinstance(leg, dict) and leg:
            return dict(leg)
        return {   # legacy fallback — expiry may be missing (record didn't keep the full leg)
            "action": rec.get("action"), "type": rec.get("type"),
            "strike": rec.get("strike"), "qty": rec.get("qty"),
            "premium": rec.get("entry_price"),
            "expiration": rec.get("expiration") or rec.get("expiry"),
        }

    restored_shares = 0.0
    for rec in restorable:
        if (rec.get("type") or "").lower() == "stock":
            try:
                restored_shares += abs(float(rec.get("qty") or 0))
            except (ValueError, TypeError):
                pass
            if rec.get("entry_price") not in (None, ""):
                params["avg_cost"] = rec.get("entry_price")
        else:
            legs.append(_restore_leg(rec))

    if restored_shares > 0:
        params["shares"] = float(params.get("shares") or 0) + restored_shares

    # Keep the roll history (and its realized) intact; only the genuine-close realized is undone.
    if roll_legs:
        params["closed_legs"] = roll_legs
        roll_real = round(sum(float(r.get("realized") or 0) for r in roll_legs), 2)
        params["realized_pnl"] = roll_real
        params["roll_realized_pnl"] = roll_real
    else:
        params.pop("closed_legs", None)
        params.pop("realized_pnl", None)
        params.pop("roll_realized_pnl", None)
    params.pop("last_pnl", None)          # stale snapshot from the closed state
    params.pop("last_pnl_at", None)

    strategy.legs_data = json.dumps(legs)
    strategy.parameters = json.dumps(params)
    strategy.trade_status = "active"
    strategy.exit_date = None
    strategy.exit_prices = None
    strategy.exit_net = None

    close_txns = (await db.execute(select(TradeTransaction).where(
        TradeTransaction.strategy_id == strategy.id, TradeTransaction.action == "close"))).scalars().all()
    for t in close_txns:
        await db.delete(t)

    await db.commit()
    await db.refresh(strategy)
    return _to_out(strategy)


class ImportOrderIn(BaseModel):
    text: str = Field(..., min_length=3)
    purpose: Optional[str] = None


def _import_suggest_name(legs: list, underlying: str) -> str:
    opt = [l for l in legs if l.get("kind") == "option"]
    if not opt:
        return f"{underlying} stock"
    if len(opt) == 1:
        l = opt[0]
        return f"{underlying} {'Short' if l['action'] == 'SELL' else 'Long'} {l['type'].title()} ${l['strike']:g}"
    return f"{underlying} {len(opt)}-leg options"


def _import_build_payload(legs: list, underlying: str, purpose: Optional[str]) -> dict:
    """Parsed legs → a ManualTradeIn-shaped payload the frontend can POST as-is."""
    opt = [l for l in legs if l.get("kind") == "option"]
    stock = [l for l in legs if l.get("kind") == "stock"]
    legs_data = [{"action": l["action"].lower(), "type": l["type"].lower(),
                  "strike": l["strike"], "expiration": l["expiration"],
                  "qty": l["qty"], "premium": l["price"]} for l in opt]
    # combo entry_prices put the stock at index 0, then the option legs (the app's convention).
    entry_prices = ([{"price": stock[0].get("price")}] if stock else []) + [{"price": l["price"]} for l in opt]
    # BUY-negative / SELL-positive, matching option_legs_net_debit.
    net_debit = sum((-1 if l["action"] == "BUY" else 1) * (l["price"] or 0) * l["qty"] * 100 for l in opt)
    params: dict = {"purpose": purpose or "income"}
    if opt:
        params["contracts"] = max(l["qty"] for l in opt)
    if stock:
        params["shares"] = stock[0]["qty"]
        params["avg_cost"] = stock[0].get("price")
    stype = ("options_spread" if len(opt) > 1
             else "covered_call" if (stock and opt and opt[0]["type"] == "CALL" and opt[0]["action"] == "SELL")
             else "cash_secured_put" if (opt and opt[0]["type"] == "PUT" and opt[0]["action"] == "SELL")
             else "stock" if (stock and not opt) else "options")
    return {
        "strategy_type": stype, "name": _import_suggest_name(legs, underlying),
        "ticker": underlying, "parameters": params, "legs_data": legs_data,
        "result_snapshot": {}, "entry_prices": entry_prices,
        "entry_net_debit": round(net_debit, 2), "order_source": "import",
        "notes": "Imported from pasted broker order",
    }


@router.post("/import-order")
async def import_order(
    body: ImportOrderIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Parse a pasted broker order (Fidelity label/value rows, single or multi-leg)
    and decide what it means against the user's OPEN trades:
      • opposite side of an existing leg (same symbol/strike/expiry) → it CLOSES it,
      • SAME side of an existing leg → a warning (adding to a position; log manually),
      • otherwise → a brand-new trade (payload returned ready to POST).
    Read-only — the frontend applies via the existing create / close-position endpoints."""
    from ..services.order_parser import parse_pasted_orders
    legs = parse_pasted_orders(body.text)
    if not legs:
        raise HTTPException(status_code=400,
                            detail="Couldn't recognize an order in that text. Paste the broker rows "
                                   "(Symbol, Contracts, Price …).")
    underlyings = sorted({l["underlying"] for l in legs})
    if len(underlyings) > 1:
        raise HTTPException(status_code=400,
                            detail=f"Found multiple underlyings ({', '.join(underlyings)}). Paste one trade at a time.")
    underlying = underlyings[0]

    result = await db.execute(select(SavedStrategy).where(
        SavedStrategy.user_id == user.id, SavedStrategy.ticker == underlying,
        SavedStrategy.trade_status == "active"))
    active = result.scalars().all()

    def _nexp(e) -> str:
        return str(e or "")[:10]

    close_matches: list[dict] = []
    warnings: list[str] = []
    n_opt = 0
    for pi, pl in enumerate(legs):
        if pl["kind"] != "option":
            continue
        n_opt += 1
        for t in active:
            for li, el in enumerate(json.loads(t.legs_data or "[]")):
                er = "C" if ("CALL" in str(el.get("type", "")).upper()
                             or str(el.get("type", "")).upper() == "C") else "P"
                if not (er == pl["right"]
                        and abs(float(el.get("strike", 0) or 0) - pl["strike"]) < 0.01
                        and _nexp(el.get("expiration") or el.get("expiry")) == pl["expiration"]):
                    continue
                ea = "SELL" if "SELL" in str(el.get("action", "")).upper() else "BUY"
                if ea != pl["action"]:
                    close_matches.append({
                        "parsed_idx": pi, "trade_id": t.id, "trade_name": t.name,
                        "leg_index": li, "exit_price": pl["price"],
                        "label": f"{pl['type']} ${pl['strike']:g} exp {pl['expiration']}",
                    })
                else:
                    warnings.append(
                        f'You already hold {ea} {el.get("type")} ${el.get("strike")} exp {pl["expiration"]} '
                        f'in "{t.name}" — this order is the SAME side, so it was NOT auto-added. '
                        f'Use Log Trade to add to the position manually if that\'s intended.')

    if warnings:
        intent = "duplicate"
    elif close_matches and n_opt > 0:
        intent = "close"
    else:
        intent = "new"

    return {
        "legs": legs, "underlying": underlying, "intent": intent,
        "close_matches": close_matches, "warnings": warnings,
        "manual_trade": _import_build_payload(legs, underlying, body.purpose),
    }


_BOOK_TR_TTL = 90 * 86400   # persist the last "Manage Book" result ~90 days (until next refresh)


def _book_tr_key(user_id: int) -> str:
    return f"book_tail_risk:v1:{user_id}"


@router.get("/book-tail-risk")
async def book_tail_risk(
    quote_source: str = "yfinance",
    refresh: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Book-level short-vol / tail-risk desk ("Manage Book"): aggregate the OPTION-LAYER
    greeks of every open trade, flag laddered/concentrated shorts as one bet, replay crash
    scenarios on the aggregate, and size INDEX / VIX tail hedges with the Spitznagel
    cost-vs-drag test.

    Persistence: the last computed result is stored per-user in the DB cache. On page load
    (``refresh=false``) we return that STORED snapshot (fast, no quote fetch) so the panel
    shows last-known numbers immediately. ``refresh=true`` (user clicks Refresh) recomputes
    from live quotes and OVERWRITES the stored snapshot."""
    from ..services.book_tail_risk import compute_book_tail_risk
    from ..services.cache_service import get_cached, set_cached
    import datetime as dt
    key = _book_tr_key(user.id)

    if not refresh:                                   # page load → serve the stored snapshot
        stored = await get_cached(db, key)
        if stored:
            return {**stored, "stored": True}
        return {"stored": False, "positions": 0}      # nothing stored yet → UI shows "Analyze book"

    result = await db.execute(select(SavedStrategy).where(
        SavedStrategy.user_id == user.id, SavedStrategy.trade_status == "active"))
    strategies = list(result.scalars().all())
    if not strategies:
        return {"positions": 0, "error": "No active trades to analyze.", "stored": False}
    computed = await compute_book_tail_risk(strategies, quote_source, user, db)
    if not computed.get("error"):
        computed["computed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            await set_cached(db, key, computed, ttl_seconds=_BOOK_TR_TTL)
        except Exception:  # noqa: BLE001 — persistence must never break the response
            pass
    return {**computed, "stored": not computed.get("error")}


@router.get("/book-hedge-advice")
async def book_hedge_advice(
    quote_source: str = "yfinance",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """LLM hedging strategy for the WHOLE book. The model is fed ONLY the pre-computed
    numbers (greeks, β, CVaR, full-reprice stress P&Ls, the ranked hedge menu with each
    candidate's cost / CVaR-cut / CAGR, concentration, assignment capital) — NO opinions,
    NO verdict text — and is explicitly told to do NO arithmetic, only reason over the data."""
    from ..services.book_tail_risk import compute_book_tail_risk
    from ..services.cache_service import get_cached
    api_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not api_key:
        return {"error": "OpenAI API key not configured. Add it in Settings."}

    # Advise on exactly what the user SEES — reuse the stored "Manage Book" snapshot if present
    # (fast, consistent); only recompute if nothing's been stored yet.
    d = await get_cached(db, _book_tr_key(user.id))
    if not d:
        result = await db.execute(select(SavedStrategy).where(
            SavedStrategy.user_id == user.id, SavedStrategy.trade_status == "active"))
        strategies = list(result.scalars().all())
        if not strategies:
            return {"error": "No active trades to analyze."}
        d = await compute_book_tail_risk(strategies, quote_source, user, db)
    if d.get("error"):
        return {"error": d["error"]}

    def _pick(obj, keys):
        return {k: obj.get(k) for k in keys if obj.get(k) is not None}

    # DATA-ONLY payload — strip every opinion (verdict prose, assumptions text, hedge_note).
    book_data = {
        "positions": d.get("positions"),
        "net_greeks": _pick(d, ("net_delta", "net_gamma", "net_vega", "net_theta")),
        "beta_weighted_delta_spy_shares": d.get("beta_delta_spy"),
        "beta_delta_notional_usd": d.get("beta_delta_notional"),
        "avg_beta": d.get("avg_beta"),
        "short_vol": d.get("short_vol"),
        "book_capital_usd": d.get("book_capital"),
        "annual_income_usd": d.get("annual_income"),
        "theta_pct_of_capital_per_day": d.get("theta_net_liq_pct"),
        "carry_yield_pct_annual": d.get("carry_yield_pct"),
        "risk_horizon": d.get("horizon"),
        "var_95_usd": d.get("var_95"), "cvar_95_usd": d.get("cvar_95"),
        "var_99_usd": d.get("var_99"), "cvar_99_usd": d.get("cvar_99"),
        "cvar_95_pct_of_capital": d.get("cvar_capital_pct"),
        "market_vol_pct": (d.get("assumptions") or {}).get("mkt_vol_pct"),
        "stress_scenarios_full_reprice": [
            _pick(s, ("label", "move_pct", "pnl", "pct_of_capital")) for s in (d.get("crash_scenarios") or [])],
        "concentration_by_underlying": [
            _pick(c, ("ticker", "beta", "trades", "short_legs", "gamma_share_pct", "laddered")) for c in (d.get("concentration") or [])],
        "stock_notional_ref_usd": d.get("stock_notional"),   # shares held (reference only — NOT in the P&L)
        # WHO actually loses in a −20% month (OPTION OVERLAY only — stock is a separate holding) — target here.
        "loss_by_name_at_minus20pct_option_overlay": [
            _pick(x, ("ticker", "pnl", "beta")) for x in (d.get("loss_by_name") or [])][:10],
        # DETERMINISTIC factor read (measured ρ / GICS sectors — NOT your opinion, do not override it):
        #   sectors = where capital & net direction sit; clusters = names that move together (hidden
        #   concentration); macro = which factors (rates/oil/gold/USD/…) the whole book loads on.
        "factor_exposure": (lambda fe: {
            "sectors": [_pick(s, ("sector", "tickers", "capital", "net_directional", "n")) for s in (fe.get("sectors") or [])][:8],
            "correlated_clusters": [_pick(c, ("tickers", "capital", "net_directional", "avg_rho")) for c in (fe.get("clusters") or [])],
            "macro_loadings": fe.get("macro") or [],
        } if fe else None)(d.get("factor_exposure") or {}),
        # STANDARDIZED institutional guardrails — each breach already carries the cheapest computed fix.
        "risk_scorecard": (lambda s: {
            "grade": s.get("grade"), "n_breach": s.get("n_breach"), "n_warn": s.get("n_warn"),
            "flagged_checks": [_pick(c, ("key", "label", "status", "value_str", "limit_str", "fix"))
                               for c in (s.get("checks") or []) if c.get("status") != "pass"],
        } if s else None)(d.get("risk_scorecard") or {}),
        "naked_assignment_capital": d.get("naked_assignment"),
        "hedge_menu": [
            _pick(h, ("label", "instrument", "kind", "long_strike", "short_strike", "contracts",
                      "annual_bleed", "offsets_pct", "cvar_reduction", "efficiency", "cagr_lift_pct",
                      "cost_effective", "sleeve_capital", "dte_days"))
            for h in (d.get("hedge_menu") or [])],
    }

    system = (
        "You are an institutional derivatives risk manager advising a retail client on a premium-selling "
        "(short-vol) option book. You are given a JSON where EVERY number is ALREADY COMPUTED — greeks, "
        "stress P&Ls, a per-name loss waterfall, a DETERMINISTIC factor read (GICS sectors, MEASURED-ρ "
        "correlated clusters, macro loadings), and a ranked hedge_menu. Do NOT perform ANY arithmetic, do "
        "NOT recompute, and — critically — do NOT invent or assert any correlation, sector, theme, or "
        "macro link that is not present in factor_exposure (those ρ/sector values are measured from real "
        "data; treat them as ground truth and never override them with your own market intuition).\n\n"
        "START from risk_scorecard: the book is graded and each BREACHED guardrail already carries the "
        "cheapest computed fix (a specific trade + its cost + before→after). Your job is to PRIORITIZE and "
        "SEQUENCE these — find the one or two trades that clear the MOST breaches per dollar (e.g. a single "
        "SPX put spread that fixes both extreme-tail AND CVaR; buying SPY that fixes both net-direction AND "
        "up/down symmetry), and note when de-risking a concentrated cluster is cheaper than a blanket hedge. "
        "Keep every fix's own numbers; do not invent new ones.\n\n"
        "Then add the deeper read, NOT a restatement of the table. In order:\n"
        "1) Name the book's REAL concentration: the correlated_clusters and the top sectors — these are "
        "the names that move together, so a hit to one hits the whole cluster (cite the tickers + avg_rho).\n"
        "2) Name the macro_loadings that matter (e.g. rates/oil/gold/USD) and what event would light them up.\n"
        "3) Point to loss_by_name_at_minus20pct_option_overlay — which specific NAMES drive the option-overlay "
        "crash loss (this desk manages the option income only; the shares behind covered calls are a separate "
        "core holding and are NOT in the P&L — stock_notional_ref is reference-only for assignment sizing).\n"
        "3b) Check stress_scenarios BOTH signs: if a melt-up / squeeze (positive move_pct) loses MORE than "
        "the −20% downside, the UPSIDE is the under-hedged tail — say so and recommend call spreads / "
        "trimming the biggest short calls / a small long-call hedge, NOT a downside index put (which does "
        "nothing for a squeeze).\n"
        "4) THEN recommend what to manage: which single-name shorts to trim/roll/spread to cut the cluster "
        "or sector concentration, AND the single best book-level tail hedge from hedge_menu (cite its "
        "cvar_reduction, annual_bleed, cagr_lift_pct). Prefer de-risking a concentrated cluster over a "
        "blanket index hedge when the concentration is the dominant risk.\n"
        "Be concrete and concise: 2-3 sentence rationale, then up to 5 bullet actions naming specific "
        "tickers/clusters and hedge_menu rows. No preamble."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": "BOOK RISK DATA (all figures pre-computed):\n" + json.dumps(book_data, default=str)},
    ]
    try:
        advice = await call_llm(api_key=api_key, model="gpt-4o", messages=messages,
                                max_tokens=600, temperature=0.3)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"LLM call failed: {exc}"}
    return {"advice": advice, "data_sent": book_data}


@router.post("/manual-trade", response_model=SavedStrategyOut, status_code=201)
async def create_manual_trade(
    body: ManualTradeIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new strategy and immediately mark it as an active trade.

    Also writes the initial ``TradeTransaction`` ledger entry ("open") so the
    new position has a complete history from the start.
    """
    import datetime as dt
    if body.entry_date:
        try:
            parsed_entry_date = dt.datetime.fromisoformat(body.entry_date)
        except (ValueError, TypeError):
            parsed_entry_date = dt.datetime.now(dt.timezone.utc)
    else:
        parsed_entry_date = dt.datetime.now(dt.timezone.utc)

    strategy = SavedStrategy(
        user_id=user.id,
        strategy_type=body.strategy_type.strip(),
        name=body.name.strip(),
        ticker=body.ticker.upper().strip(),
        parameters=json.dumps(body.parameters),
        legs_data=json.dumps(body.legs_data),
        result_snapshot=json.dumps(body.result_snapshot),
        notes=body.notes,
        trade_status="active",
        entry_prices=json.dumps(body.entry_prices),
        entry_net_debit=body.entry_net_debit,
        entry_date=parsed_entry_date,
        order_source=body.order_source,
    )
    db.add(strategy)
    await db.flush()   # populate strategy.id for the ledger FK

    # Seed the ledger. For stock: one row with (quantity, price) derived from
    # parameters + entry_net_debit. For options: summary row at leg_index=None
    # with quantity=num_contracts, price=|net_debit/100|.
    _seed_open_transaction(db, strategy, body, parsed_entry_date)

    await db.commit()
    await db.refresh(strategy)
    return _to_out(strategy)


def _seed_open_transaction(
    db: AsyncSession,
    strategy: SavedStrategy,
    body: ManualTradeIn,
    executed_at,
) -> None:
    """Create the initial 'open' ledger row for a new trade.

    Uses pragmatic defaults: for stock strategies we pull shares from
    parameters; for options we record contracts (default 1) and per-share
    premium. The frontend can always POST more granular per-leg transactions
    via /transactions if needed.
    """
    stype = (strategy.strategy_type or "").lower()
    params = body.parameters or {}

    if stype.startswith("stock"):
        # Stock long/short: quantity=shares, price=per-share entry
        shares = float(params.get("shares") or params.get("quantity") or 0)
        price_per_share = (
            body.entry_prices[0].get("price")
            if body.entry_prices and isinstance(body.entry_prices[0], dict)
            else None
        )
        if shares > 0 and price_per_share:
            # Short positions get negative quantity to mark direction.
            signed_qty = -shares if "short" in stype else shares
            db.add(TradeTransaction(
                strategy_id=strategy.id,
                action="open",
                leg_index=None,
                quantity=signed_qty,
                price=float(price_per_share),
                fees=0.0,
                executed_at=executed_at,
                source=body.order_source,
                note="Initial position",
            ))
            return

    # Options / multi-leg fallback: one summary row at the position level.
    contracts = float(params.get("contracts") or params.get("num_contracts") or 1)
    # Net cost per contract expressed as a positive number; sign carried by action + entry_net_debit.
    price_per_contract = abs(body.entry_net_debit) / max(contracts, 1) if body.entry_net_debit else 0.0
    db.add(TradeTransaction(
        strategy_id=strategy.id,
        action="open",
        leg_index=None,
        quantity=contracts,
        price=price_per_contract / 100.0,   # store as per-share premium
        fees=0.0,
        executed_at=executed_at,
        source=body.order_source,
        note="Initial position",
    ))


# ── Transaction ledger endpoints ──────────────────────────────────────

@router.get("/{strategy_id}/transactions", response_model=list[TransactionOut])
async def list_transactions(
    strategy_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the full transaction ledger for a position, oldest first.

    If no ledger rows exist yet (e.g. trade created via mark-traded before
    ledger seeding was added) a synthetic opening row is synthesised from the
    strategy's recorded entry data so the history is never empty for stock positions.
    """
    # Fetch the strategy (verifies ownership + gives us metadata for synthesis)
    strat_result = await db.execute(
        select(SavedStrategy).where(
            SavedStrategy.id == strategy_id,
            SavedStrategy.user_id == user.id,
        )
    )
    strategy = strat_result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Position not found")

    result = await db.execute(
        select(TradeTransaction)
        .where(TradeTransaction.strategy_id == strategy_id)
        .order_by(TradeTransaction.executed_at.asc(), TradeTransaction.id.asc())
    )
    txs = result.scalars().all()

    if txs:
        return [
            TransactionOut(
                id=t.id,
                strategy_id=t.strategy_id,
                action=t.action,
                quantity=t.quantity,
                price=t.price,
                fees=t.fees,
                executed_at=t.executed_at.isoformat() if t.executed_at else "",
                leg_index=t.leg_index,
                source=t.source,
                note=t.note,
                created_at=t.created_at.isoformat() if t.created_at else "",
            )
            for t in txs
        ]

    # ── No ledger rows: synthesise an opening entry from strategy metadata ──
    # Covers trades created via mark-traded before ledger seeding was introduced.
    stype = (strategy.strategy_type or "").lower()
    if stype.startswith("stock"):
        ep_list = json.loads(strategy.entry_prices or "[]")
        params_dict = json.loads(strategy.parameters or "{}")
        init_shares = float(params_dict.get("shares") or 0)
        init_price = float(
            (ep_list[0].get("price") if ep_list else None)
            or params_dict.get("avg_cost")
            or 0
        )
        if init_shares > 0 and init_price > 0:
            signed_qty = -init_shares if "short" in stype else init_shares
            entry_ts = strategy.entry_date.isoformat() if strategy.entry_date else ""
            return [TransactionOut(
                id=0,
                strategy_id=strategy_id,
                action="open",
                quantity=signed_qty,
                price=init_price,
                fees=0.0,
                executed_at=entry_ts,
                leg_index=None,
                source=strategy.order_source or "manual",
                note="Initial position (reconstructed from trade record)",
                created_at=entry_ts,
            )]

    return []


@router.post("/{strategy_id}/transactions", response_model=TransactionOut, status_code=201)
async def add_transaction(
    strategy_id: int,
    body: TransactionIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Append a transaction to a position's ledger (add/reduce/close/adjust).

    For 'close' transactions, also flips the position's trade_status to
    'closed' and sets exit_date. Legacy exit_net is left untouched — new code
    should compute P&L by walking the ledger.
    """
    import datetime as dt
    result = await db.execute(
        select(SavedStrategy).where(
            SavedStrategy.id == strategy_id,
            SavedStrategy.user_id == user.id,
        )
    )
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Position not found")

    action = body.action.strip().lower()
    if action not in {"open", "add", "reduce", "close", "adjust"}:
        raise HTTPException(status_code=400, detail=f"Unknown action '{body.action}'")

    executed = dt.datetime.now(dt.timezone.utc)
    if body.executed_at:
        try:
            executed = dt.datetime.fromisoformat(body.executed_at)
        except (ValueError, TypeError):
            pass

    tx = TradeTransaction(
        strategy_id=strategy.id,
        action=action,
        leg_index=body.leg_index,
        quantity=body.quantity,
        price=body.price,
        fees=body.fees,
        executed_at=executed,
        source=body.source,
        note=body.note,
    )
    db.add(tx)

    # Flush now so tx has a real PK before we SELECT the full ledger below.
    await db.flush()

    # Auto-seed missing initial ledger entry for trades created via /mark-traded
    # (they never got an opening row). Only when this is the very first row.
    stype = (strategy.strategy_type or "").lower()
    if stype.startswith("stock") and action != "open":
        count_result = await db.execute(
            select(sql_func.count(TradeTransaction.id)).where(
                TradeTransaction.strategy_id == strategy_id
            )
        )
        if int(count_result.scalar() or 0) == 1:
            ep_list = json.loads(strategy.entry_prices or "[]")
            params_dict = json.loads(strategy.parameters or "{}")
            init_shares = float(params_dict.get("shares") or 0)
            init_price = float(
                (ep_list[0].get("price") if ep_list else None)
                or params_dict.get("avg_cost") or 0
            )
            if init_shares > 0 and init_price > 0:
                signed_init = -init_shares if "short" in stype else init_shares
                db.add(TradeTransaction(
                    strategy_id=strategy_id,
                    action="open",
                    leg_index=None,
                    quantity=signed_init,
                    price=init_price,
                    fees=0.0,
                    executed_at=strategy.entry_date or executed,
                    source="manual",
                    note="Initial position (auto-seeded)",
                ))
                await db.flush()

    # Sync ledger → parameters (handles trade_status reconciliation too)
    await _sync_stock_ledger(db, strategy)

    await db.commit()
    await db.refresh(tx)
    return TransactionOut(
        id=tx.id,
        strategy_id=tx.strategy_id,
        action=tx.action,
        quantity=tx.quantity,
        price=tx.price,
        fees=tx.fees,
        executed_at=tx.executed_at.isoformat() if tx.executed_at else "",
        leg_index=tx.leg_index,
        source=tx.source,
        note=tx.note,
        created_at=tx.created_at.isoformat() if tx.created_at else "",
    )


@router.put("/{strategy_id}/transactions/{tx_id}", response_model=TransactionOut)
async def edit_transaction(
    strategy_id: int,
    tx_id: int,
    body: TransactionUpdateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Edit an existing ledger transaction and re-sync the position state."""
    import datetime as dt

    strat_result = await db.execute(
        select(SavedStrategy).where(
            SavedStrategy.id == strategy_id,
            SavedStrategy.user_id == user.id,
        )
    )
    strategy = strat_result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Position not found")

    tx_result = await db.execute(
        select(TradeTransaction).where(
            TradeTransaction.id == tx_id,
            TradeTransaction.strategy_id == strategy_id,
        )
    )
    tx = tx_result.scalar_one_or_none()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    if body.action is not None:
        new_action = body.action.strip().lower()
        if new_action not in {"open", "add", "reduce", "close", "adjust"}:
            raise HTTPException(status_code=400, detail=f"Unknown action '{body.action}'")
        tx.action = new_action
    if body.quantity is not None:
        tx.quantity = body.quantity
    if body.price is not None:
        tx.price = body.price
    if body.fees is not None:
        tx.fees = body.fees
    if body.note is not None:
        tx.note = body.note.strip() or None
    if body.executed_at is not None:
        try:
            tx.executed_at = dt.datetime.fromisoformat(body.executed_at)
        except (ValueError, TypeError):
            pass

    await db.flush()
    await _sync_stock_ledger(db, strategy)
    await db.commit()
    await db.refresh(tx)
    return TransactionOut(
        id=tx.id,
        strategy_id=tx.strategy_id,
        action=tx.action,
        quantity=tx.quantity,
        price=tx.price,
        fees=tx.fees,
        executed_at=tx.executed_at.isoformat() if tx.executed_at else "",
        leg_index=tx.leg_index,
        source=tx.source,
        note=tx.note,
        created_at=tx.created_at.isoformat() if tx.created_at else "",
    )


@router.delete("/{strategy_id}/transactions/{tx_id}", status_code=204)
async def delete_transaction(
    strategy_id: int,
    tx_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a ledger transaction and re-sync the position state."""
    strat_result = await db.execute(
        select(SavedStrategy).where(
            SavedStrategy.id == strategy_id,
            SavedStrategy.user_id == user.id,
        )
    )
    strategy = strat_result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Position not found")

    tx_result = await db.execute(
        select(TradeTransaction).where(
            TradeTransaction.id == tx_id,
            TradeTransaction.strategy_id == strategy_id,
        )
    )
    tx = tx_result.scalar_one_or_none()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    await db.delete(tx)
    await db.flush()
    await _sync_stock_ledger(db, strategy)
    await db.commit()


@router.patch("/{strategy_id}/notes", response_model=SavedStrategyOut)
async def update_notes(
    strategy_id: int,
    body: NotesUpdateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the free-form notes on a position. Intended for inline autosave."""
    result = await db.execute(
        select(SavedStrategy).where(
            SavedStrategy.id == strategy_id,
            SavedStrategy.user_id == user.id,
        )
    )
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Position not found")

    strategy.notes = (body.notes or "").strip() or None
    await db.commit()
    await db.refresh(strategy)
    return _to_out(strategy)


# The user-selected purpose a trade serves; drives grouping + which lifecycle
# view to show. Stored in parameters.purpose (no migration); absent ⇒ "income".
_VALID_PURPOSES = {"income", "hedge", "trade", "managed_floor",
                   "managed_buffer", "dual_directional", "other"}

# hold_vs_close signal → the 4-level whole-trade EXIT vocabulary (for the paths
# that don't warrant the full exit-timing engine — pure stock / futures).
_EXIT_MAP = {"STRONG_HOLD": "STRONG_HOLD", "HOLD": "HOLD",
             "CLOSE": "CLOSE", "STRONG_CLOSE": "STRONG_CLOSE"}


class PurposeUpdateIn(BaseModel):
    purpose: str = Field(...)


@router.patch("/{strategy_id}/purpose", response_model=SavedStrategyOut)
async def update_purpose(
    strategy_id: int,
    body: PurposeUpdateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set the strategy purpose (income / hedge / trade / managed_floor /
    managed_buffer / dual_directional / other) on a position."""
    purpose = (body.purpose or "income").lower().strip()
    if purpose not in _VALID_PURPOSES:
        raise HTTPException(status_code=400, detail=f"purpose must be one of {sorted(_VALID_PURPOSES)}")
    result = await db.execute(
        select(SavedStrategy).where(
            SavedStrategy.id == strategy_id, SavedStrategy.user_id == user.id,
        )
    )
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Position not found")
    params = json.loads(strategy.parameters or "{}")
    params["purpose"] = purpose
    strategy.parameters = json.dumps(params)
    await db.commit()
    await db.refresh(strategy)
    return _to_out(strategy)


def _leg_right(type_str: str) -> str:
    """Normalize an option type to 'C' or 'P'."""
    t = str(type_str or "").upper()
    return "C" if ("CALL" in t or t == "C") else "P"


def _leg_entry_premium(leg: dict, idx: int, entry_prices_list: list) -> float | None:
    """Per-share entry premium (always positive) for one option leg.

    Tried in order: the leg's own fields → the matching entry_prices row by
    (strike, right) → the positional entry_prices row. Returns None if nothing
    usable is found, so the caller can fall back to the stored net debit rather
    than compute a partial (and therefore wrong) sum.
    """
    for key in ("premium", "entry_price", "price"):
        v = leg.get(key)
        if v is not None:
            try:
                return abs(float(v))
            except (TypeError, ValueError):
                pass

    strike = leg.get("strike")
    right = _leg_right(leg.get("type"))
    if strike is not None and entry_prices_list:
        try:
            ks = float(strike)
            for ep in entry_prices_list:
                if not isinstance(ep, dict) or ep.get("strike") is None:
                    continue
                if abs(float(ep["strike"]) - ks) < 1e-6 and _leg_right(ep.get("type")) == right:
                    pv = ep.get("price", ep.get("premium"))
                    if pv is not None:
                        return abs(float(pv))
        except (TypeError, ValueError):
            pass

    if 0 <= idx < len(entry_prices_list) and isinstance(entry_prices_list[idx], dict):
        pv = entry_prices_list[idx].get("price", entry_prices_list[idx].get("premium"))
        if pv is not None:
            try:
                return abs(float(pv))
            except (TypeError, ValueError):
                pass
    return None


def option_legs_net_debit(leg_metas: list[dict], entry_prices_list: list) -> float | None:
    """Net entry debit for a set of option legs, from per-leg entry premiums.

    Sign convention matches the stored ``entry_net_debit``: **BUY negative**
    (cash paid), **SELL positive** (credit received); ×100 per contract. Returns
    None if ANY leg lacks a usable entry premium — a partial sum would be wrong,
    so the caller keeps the stored value instead.

    Each leg_meta must expose: i, strike, type, qty, action.
    """
    total = 0.0
    for lm in leg_metas:
        ep = _leg_entry_premium(
            lm.get("_leg", {}), lm["i"], entry_prices_list
        )
        if ep is None:
            return None
        sign = -1.0 if "BUY" in str(lm.get("action", "")).upper() else 1.0
        total += sign * ep * float(lm.get("qty") or 1) * 100.0
    return round(total, 2)


def build_payoff(
    underlying_price: float,
    opt_legs: list[dict],
    stock: Optional[dict],
    entry_cost: float,
    r: float = 0.05,
    avg_iv: Optional[float] = None,
    dte_days: Optional[int] = None,
) -> dict:
    """Payoff scenarios + exact structural extremes for ANY position type — pure
    equity, futures, options, or a stock+option combo — so every trade gets the
    same graph and max-gain/max-loss numbers.

    opt_legs: [{strike, right('C'/'P'), sign(+1/−1), qty, iv(decimal), dte_years}]
    stock:    {shares(signed), avg_cost, mult} or None
    entry_cost: options net debit (BUY negative / SELL positive); 0 when no options.

    Returns {scenarios, max_profit, max_loss, max_profit_price, max_loss_price,
    unbounded_profit, unbounded_loss, breakevens}. The price grid spans both the
    ±30% window and every strike so the flat max-profit/max-loss plateaus show.
    """
    from ..services.stock_service import bs_price
    from ..services.trade_math import structure_payoff_extremes

    if underlying_price <= 0:
        return {"scenarios": [], "max_profit": None, "max_loss": None,
                "max_profit_price": None, "max_loss_price": None,
                "unbounded_profit": False, "unbounded_loss": False, "breakevens": []}

    strikes = [float(l["strike"]) for l in opt_legs if l.get("strike")]
    anchor = float(stock["avg_cost"]) if (stock and stock.get("avg_cost")) else underlying_price
    lo = max(0.01, min([underlying_price * 0.70, anchor * 0.70] + [s * 0.85 for s in strikes]))
    hi = max([underlying_price * 1.30, anchor * 1.30] + [s * 1.15 for s in strikes])
    mult = float(stock.get("mult") or 1.0) if stock else 1.0
    shares = float(stock["shares"]) if stock else 0.0

    def _stock(S):
        return shares * mult * (S - float(stock["avg_cost"])) if stock else 0.0

    n = 49
    scenarios = []
    for k in range(n):
        S = lo + (hi - lo) * k / (n - 1)
        now = _stock(S) + entry_cost
        exp = _stock(S) + entry_cost
        for l in opt_legs:
            K = float(l["strike"])
            intrinsic = max(0.0, S - K) if l["right"] == "C" else max(0.0, K - S)
            exp += l["sign"] * l["qty"] * 100.0 * intrinsic
            iv, dte_y = l.get("iv"), l.get("dte_years") or 0.0
            if iv and iv > 0 and dte_y > 0:
                val = bs_price(S, K, dte_y, r, iv, "call" if l["right"] == "C" else "put")
            else:
                val = intrinsic  # no vol/time info → mark at intrinsic
            now += l["sign"] * l["qty"] * 100.0 * val
        scenarios.append({
            "price_change_pct": round((S / underlying_price - 1) * 100, 1),
            "price": round(S, 2),
            "pnl_now": round(now, 2),
            "pnl_at_expiry": round(exp, 2),
            "roi_now": 0.0, "roi_at_expiry": 0.0, "margin_at_risk": 0.0,
        })

    ext = structure_payoff_extremes(
        [{"strike": l["strike"], "right": l["right"], "sign": l["sign"], "qty": l["qty"]}
         for l in opt_legs if l.get("strike")],
        entry_cost,
        stock=stock,
    ) or {}

    breakevens = []
    for i in range(len(scenarios) - 1):
        a, b = scenarios[i]["pnl_at_expiry"], scenarios[i + 1]["pnl_at_expiry"]
        if (a <= 0 < b) or (a >= 0 > b):
            p1, p2 = scenarios[i]["price"], scenarios[i + 1]["price"]
            if b != a:
                breakevens.append(round(p1 + (0 - a) * (p2 - p1) / (b - a), 2))

    # Probability of profit + expected value (+ Kelly) from a lognormal terminal
    # distribution when we have an implied vol and DTE — the same quant the
    # options-only path shows, so combos/covered calls get it too.
    pop = expected_value = kelly_fraction = None
    max_profit, max_loss = ext.get("max_profit"), ext.get("max_loss")
    if avg_iv and avg_iv > 0 and dte_days and dte_days > 0:
        T = dte_days / 365.0
        sig_sqrt_t = avg_iv * math.sqrt(T)
        tot = prof = ev_sum = 0.0
        for i in range(len(scenarios) - 1):
            p_mid = (scenarios[i]["price"] + scenarios[i + 1]["price"]) / 2.0
            pnl_mid = (scenarios[i]["pnl_at_expiry"] + scenarios[i + 1]["pnl_at_expiry"]) / 2.0
            dp = scenarios[i + 1]["price"] - scenarios[i]["price"]
            if p_mid <= 0 or sig_sqrt_t <= 0:
                continue
            z = (math.log(p_mid / underlying_price) - (r - 0.5 * avg_iv ** 2) * T) / sig_sqrt_t
            dens = math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi) / (p_mid * sig_sqrt_t)
            w = dens * dp
            tot += w
            ev_sum += pnl_mid * w
            if pnl_mid > 0:
                prof += w
        if tot > 0:
            pop = round(prof / tot * 100, 1)
            expected_value = round(ev_sum / tot, 2)
            if max_profit and max_loss and max_loss != 0:
                p_win = pop / 100.0
                R = abs(max_profit / max_loss)
                if R > 0:
                    kelly_fraction = round(max(0.0, min(1.0, (p_win * R - (1 - p_win)) / R)), 3)

    return {
        "scenarios": scenarios,
        "max_profit": max_profit,
        "max_loss": max_loss,
        "max_profit_price": ext.get("max_profit_price"),
        "max_loss_price": ext.get("max_loss_price"),
        "unbounded_profit": ext.get("unbounded_profit", False),
        "unbounded_loss": ext.get("unbounded_loss", False),
        "breakevens": breakevens,
        "pop": pop,
        "expected_value": expected_value,
        "kelly_fraction": kelly_fraction,
    }


class PnlSnapshotIn(BaseModel):
    snapshot: dict


@router.put("/{strategy_id}/pnl-snapshot")
async def save_pnl_snapshot(
    strategy_id: int,
    body: PnlSnapshotIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Persist the last-refreshed P&L snapshot (trimmed, from the frontend) so My Trades can
    show the last-known numbers on landing — before any refresh. Stored in the JSON
    `parameters` column (`last_pnl` + `last_pnl_at`); no schema migration needed."""
    import datetime as dt
    result = await db.execute(
        select(SavedStrategy).where(SavedStrategy.id == strategy_id, SavedStrategy.user_id == user.id)
    )
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Trade not found")
    try:
        params = json.loads(strategy.parameters) if strategy.parameters else {}
    except (json.JSONDecodeError, TypeError):
        params = {}
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    params["last_pnl"] = body.snapshot
    params["last_pnl_at"] = now_iso
    strategy.parameters = json.dumps(params, default=str)
    await db.commit()
    return {"saved": True, "last_pnl_at": now_iso}


@router.get("/{strategy_id}/repair-menu")
async def get_repair_menu(
    strategy_id: int,
    quote_source: str = "yfinance",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Institutional REPAIR MENU for a tested short-premium trade (CSP short put / short call
    run over by an adverse move): roll out, roll away & out, cap into a spread, delta-hedge with
    stock/futures, take assignment → wheel, or close — each model-priced and laddered across spot
    scenarios so the user can weigh turning it around vs. banking the loss."""
    import datetime as dt
    from ..services.quote_providers import get_provider
    from ..services.hedging_service import _split_chain
    from ..services.trade_repair_service import repair_alternatives

    result = await db.execute(select(SavedStrategy).where(
        SavedStrategy.id == strategy_id, SavedStrategy.user_id == user.id))
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Trade not found")

    raw = json.loads(strategy.legs_data) if strategy.legs_data else []
    entry_prices = json.loads(strategy.entry_prices) if strategy.entry_prices else []
    roll_days = 45

    # Build the FULL position: every option leg with its sign + per-share entry credit/debit. LONG
    # stock legs are kept too, so a COVERED CALL is modeled covered (defined) — not as a naked short call.
    legs, near_exp = [], None
    covered_shares, stock_basis = 0.0, None
    for i, l in enumerate(raw):
        typ = str(l.get("type", "")).lower()
        act = str(l.get("action", "")).upper()
        if l.get("strike") and ("call" in typ or "put" in typ):
            right = "P" if "put" in typ else "C"
            sign = -1 if "SELL" in act else 1
            ep = entry_prices[i].get("price") if i < len(entry_prices) and isinstance(entry_prices[i], dict) else None
            entry = abs(float(ep)) if ep else abs(float(l.get("premium") or l.get("mid") or 0.0))
            legs.append({"strike": float(l["strike"]), "right": right, "sign": sign,
                         "qty": int(l.get("qty") or l.get("contracts") or 1), "entry": entry})
            near_exp = near_exp or str(l.get("expiration") or l.get("expiry") or "")[:10]
        elif ("stock" in typ or "share" in typ or "equity" in typ) and ("SELL" not in act):
            covered_shares += abs(float(l.get("qty") or l.get("shares") or 0.0))
            bp = entry_prices[i].get("price") if i < len(entry_prices) and isinstance(entry_prices[i], dict) else None
            if bp:
                stock_basis = float(bp)
            elif l.get("premium") or l.get("mid") or l.get("price"):
                stock_basis = float(l.get("premium") or l.get("mid") or l.get("price"))
    if not legs:
        return {"error": "No option legs to repair — the repair menu is for short-premium trades (CSPs / short calls / spreads / condors)."}

    provider = get_provider(quote_source, user=user, db=db)
    try:
        uq = await provider.get_underlying_price(strategy.ticker)
        spot = float(getattr(uq, "price", None) or getattr(uq, "last", None) or 0.0)
    except Exception:  # noqa: BLE001
        spot = 0.0
    if spot <= 0:
        return {"error": "Couldn't fetch the underlying price to build the repair menu."}

    try:
        dte_days = max(1, (dt.date.fromisoformat(near_exp) - dt.date.today()).days)
    except (ValueError, TypeError):
        dte_days = 30

    # LIVE chains for the near (trade) expiry AND the far (+roll_days) expiry → {tenor: {strike:{P/C:{mid,iv}}}}.
    async def _chain_for(target_dte: int) -> tuple:
        try:
            exps = await provider.get_option_expirations(strategy.ticker)
            exp = min(exps, key=lambda e: abs((dt.date.fromisoformat(str(e)[:10]) - dt.date.today()).days - target_dte))
            adte = max(1, (dt.date.fromisoformat(str(exp)[:10]) - dt.date.today()).days)
            calls, puts = _split_chain(await provider.get_option_chain(strategy.ticker, exp))
            book: dict = {}
            for side, rt in ((puts, "P"), (calls, "C")):
                for k, q in (side or {}).items():
                    mid = getattr(q, "mid", None)
                    iv = getattr(q, "iv", None)
                    book.setdefault(float(k), {})[rt] = {"mid": float(mid) if mid else None,
                                                         "iv": float(iv) if iv else None}
            return adte, book, str(exp)[:10]
        except Exception:  # noqa: BLE001 — no chain → pure BS fallback in the engine
            return target_dte, {}, None

    (n_dte, near_book, n_exp), (f_dte, far_book, f_exp) = await _chain_for(dte_days), await _chain_for(dte_days + roll_days)
    chains = {k: v for k, v in ((n_dte, near_book), (f_dte, far_book)) if v}
    atm_iv = 0.30
    near_atm = min(near_book.keys(), key=lambda k: abs(k - spot)) if near_book else None
    if near_atm is not None:
        for rt in ("P", "C"):
            v = (near_book[near_atm].get(rt) or {}).get("iv")
            if v:
                atm_iv = v
                break

    # Ex-dividend before expiry only bears on an ITM short call (early-assignment risk), so fetch it
    # best-effort ONLY in that case — CSPs / OTM trades (the common tested trade) pay nothing extra.
    ex_div = None
    if any(l["right"] == "C" and l["sign"] < 0 and spot > l["strike"] for l in legs):
        try:
            import asyncio
            import yfinance as yf

            def _next_div():
                t = yf.Ticker(strategy.ticker)
                cal = getattr(t, "calendar", None)
                when = (cal or {}).get("Ex-Dividend Date") if isinstance(cal, dict) else None
                divs = getattr(t, "dividends", None)
                amt = float(divs.iloc[-1]) if (divs is not None and len(divs)) else None   # last paid ≈ next
                return amt, when

            amt, when = await asyncio.to_thread(_next_div)
            if amt and amt > 0 and when is not None:
                try:
                    days = (dt.date.fromisoformat(str(when)[:10]) - dt.date.today()).days
                except (ValueError, TypeError):
                    days = None
                if days is not None and 0 <= days <= dte_days:
                    ex_div = {"amount": amt, "days": days}
        except Exception:  # noqa: BLE001 — best-effort; deep-ITM extrinsic heuristic still applies
            ex_div = None

    stock = {"shares": covered_shares, "basis": stock_basis if stock_basis else spot} if covered_shares > 0 else None
    menu = repair_alternatives(legs=legs, spot=spot, dte_days=dte_days, roll_days=roll_days,
                               atm_iv=atm_iv, chains=chains or None, ex_div=ex_div,
                               near_expiry=(n_exp or near_exp), far_expiry=f_exp,
                               base_date=dt.date.today().isoformat(), stock=stock)
    menu["ticker"] = strategy.ticker

    # TA + context enrichment: fold the technicals INTO the recoverability read (structure / momentum /
    # volume-profile / gamma regime tilt the model odds) and attach the "what broke & setup" context —
    # so one click on Defend shows everything (no second fetch). Best-effort; failures leave the core intact.
    try:
        import asyncio
        rec = menu.get("recoverability") or {}
        ta_factors, outlook, context = await asyncio.to_thread(
            _defend_ta_context, strategy.ticker, spot, dte_days, menu.get("short_right"),
            rec.get("breakeven"), (menu.get("assignment") or {}).get("extrinsic"), menu.get("contracts") or 1,
            rec.get("posture"), rec.get("cushion_pct"), menu.get("short_strike"), rec.get("expected_move"))
        if rec:
            rec.setdefault("factors", []).extend(ta_factors)
            rec["outlook"] = outlook
            risk = (context or {}).get("risk") or {}
            if risk:                                  # reconcile: P(touch)/VRP/trend can downgrade a naive 'healthy' → 'marginal'
                rec["posture"] = risk.get("posture") or rec.get("posture")
                for k in ("p_touch", "vrp_pct", "trend_pct", "iv_pct", "hv_pct"):
                    rec[k] = risk.get(k)
                rec["risk_read"] = risk.get("read")
        menu["context"] = context
    except Exception:  # noqa: BLE001 — TA is an overlay; the core recoverability/actions still stand
        pass
    return _to_native(menu)


class DefendPayloadIn(BaseModel):
    """The already-computed Defend payload the panel POSTs to the context / committee lenses, so the
    server reasons over the SAME numbers without re-pricing the chain (one heavy fetch per trade)."""
    defend: dict = Field(default_factory=dict)


def _to_native(obj):
    """Recursively coerce numpy scalars/arrays → native Python so FastAPI/pydantic can serialize the
    response (find_support_resistance etc. return numpy.float64, and `x <= numpy_float` is numpy.bool_,
    which the JSON encoder chokes on with 'numpy.bool object is not iterable')."""
    import numpy as _np
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_native(v) for v in obj]
    if isinstance(obj, _np.generic):
        return obj.item()
    if isinstance(obj, _np.ndarray):
        return [_to_native(v) for v in obj.tolist()]
    return obj


# ── Defend TA / context helpers (deterministic; fold the technicals INTO the recoverability read) ──
def _ema(arr, span: int):
    import numpy as np
    a = np.asarray(arr, dtype=float)
    if len(a) == 0:
        return a
    k = 2.0 / (span + 1.0)
    out = np.empty_like(a)
    out[0] = a[0]
    for i in range(1, len(a)):
        out[i] = a[i] * k + out[i - 1] * (1.0 - k)
    return out


def _poc(closes, volumes, bins: int = 30):
    """Volume point-of-control: the price bin that traded the most volume over the window."""
    import numpy as np
    c = np.asarray(closes, dtype=float); v = np.asarray(volumes, dtype=float)
    if len(c) < 5 or v.sum() <= 0:
        return None
    lo, hi = float(c.min()), float(c.max())
    if hi <= lo:
        return None
    edges = np.linspace(lo, hi, bins + 1)
    idx = np.clip(np.digitize(c, edges) - 1, 0, bins - 1)
    vol = np.zeros(bins)
    for i, vi in zip(idx, v):
        vol[int(i)] += float(vi)
    b = int(vol.argmax())
    return round(float((edges[b] + edges[b + 1]) / 2.0), 2)


def _p_touch(S, K, iv, dte, mu, right):
    """Drift-aware first-passage probability the short strike is BREACHED (touched) at ANY point before
    expiry — the real 'will it get tested' risk, ~2× the terminal-ITM odds and higher with an adverse
    trend (GBM reflection principle). Returns 0..1 or None. This is what makes a 'healthy' 82%-OTM short
    honest: a 75% touch means it is NOT safe."""
    import math
    T = max(dte, 1) / 365.0
    sig = max(iv or 0.0, 0.02)
    if not S or not K or S <= 0 or K <= 0 or T <= 0:
        return None
    nu = mu - 0.5 * sig * sig
    sq = sig * math.sqrt(T)
    if sq <= 0:
        return None
    _N = lambda x: 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
    a = math.log(K / S)
    try:
        if right == "C":                         # upper barrier K
            if a <= 0:
                return 0.99
            p = _N((-a + nu * T) / sq) + math.exp(2 * nu * a / (sig * sig)) * _N((-a - nu * T) / sq)
        else:                                    # lower barrier K
            if a >= 0:
                return 0.99
            p = _N((a - nu * T) / sq) + math.exp(2 * nu * a / (sig * sig)) * _N((a + nu * T) / sq)
    except (OverflowError, ValueError):
        return None
    return max(0.0, min(0.999, float(p)))


def _defend_ta_context(ticker, spot, dte, right, be, extrinsic, contracts_n, posture=None, cushion_pct=None,
                       short_strike=None, exp_move=None):
    """Best-effort DETERMINISTIC technicals that TILT the recovery odds (structure · breakeven-vs-level ·
    momentum · RSI · volume-profile · gamma regime) + the 'what broke & setup' context (recoverable time
    value · realized-vol state · sector cohort · earnings). Returns (ta_factors, outlook, context).
    A tested PUT recovers on an UP move, a tested CALL on a DOWN move — every factor is signed to that."""
    import datetime as dt
    import numpy as np
    import yfinance as yf
    from ..services.stock_service import find_support_resistance, calculate_rsi
    from ..services.dealer_positioning_service import _collect_contracts, _net_gex_at, _gamma_flip
    from ..services import correlated_assets_service as cas

    need_up = (right == "P")
    ta: list[dict] = []
    context: dict = {}
    _closes_cap, _hv_cap = None, None          # captured for the quant risk read (trend / VRP)
    stock = yf.Ticker(ticker)
    px = float(spot) if spot else None
    # DTE-ADAPTIVE timeframe. Recoverability is a MANAGEMENT read — it must match the trade's REMAINING
    # horizon, the SAME ladder as the desk: a 3-DTE defended trade reads intraday structure, not 6-month
    # daily. `dte` here is the trade's remaining DTE, so this auto-tightens as expiry nears.
    from ..services.desk_review_service import _tf_for_dte
    from ..services.stock_service import TECHNICAL_TIMEFRAMES
    _stf, _ = _tf_for_dte(dte)
    _cfg = TECHNICAL_TIMEFRAMES.get(_stf) or {"period": "6mo", "interval": "1d", "label": "Medium Term (6mo / 1d)"}
    context["ta_timeframe"] = _cfg.get("label")
    _bpy = {"1m": 252 * 390, "5m": 252 * 78, "15m": 252 * 26, "30m": 252 * 13,
            "1h": int(252 * 6.5), "1d": 252, "1wk": 52}.get(_cfg["interval"], 252)   # bars/yr → correct HV annualization

    # history-derived: structure · breakeven-vs-level · momentum · RSI · POC · realized-vol state
    try:
        hist = stock.history(period=_cfg["period"], interval=_cfg["interval"])
        if hist is not None and not hist.empty:
            highs = hist["High"].tolist(); lows = hist["Low"].tolist()
            closes = hist["Close"].tolist(); vols = hist["Volume"].tolist()
            sup, res = find_support_resistance(highs, lows, closes)
            sup = float(sup) if sup is not None else None
            res = float(res) if res is not None else None
            px = px or (closes[-1] if closes else None)
            if px and sup and res:
                context["technical"] = {"support": sup, "resistance": res,
                    "note": (f"Spot ${px:.2f}: support ${sup:.2f} ({((px-sup)/px*100):.1f}% below), "
                             f"resistance ${res:.2f} ({((res-px)/px*100):.1f}% above).")}
                if need_up:
                    ta.append({"label": "Structure", "kind": "ta", "favorable": bool(sup < px),
                               "detail": f"support ${sup:.2f} sits {((px-sup)/px*100):.1f}% below — a floor that can halt the slide"})
                else:
                    ta.append({"label": "Structure", "kind": "ta", "favorable": bool(res > px),
                               "detail": f"resistance ${res:.2f} sits {((res-px)/px*100):.1f}% above — a ceiling that can cap the rise"})
                if be:
                    if need_up and be > res:
                        ta.append({"label": "Breakeven vs resistance", "kind": "ta", "favorable": False,
                                   "detail": f"breakeven ${be:.2f} is ABOVE resistance ${res:.2f} — you must break a ceiling to recover"})
                    elif (not need_up) and be < sup:
                        ta.append({"label": "Breakeven vs support", "kind": "ta", "favorable": False,
                                   "detail": f"breakeven ${be:.2f} is BELOW support ${sup:.2f} — a floor must break for you to recover"})
            c = np.asarray(closes, dtype=float)
            _closes_cap = c
            if len(c) >= 26:
                macd = _ema(c, 12) - _ema(c, 26); sigl = _ema(macd, 9)
                bull = bool(macd[-1] > sigl[-1])
                fav = bull if need_up else (not bull)
                ta.append({"label": "Momentum (MACD)", "kind": "ta", "favorable": bool(fav),
                           "detail": (f"MACD {'above' if bull else 'below'} signal — momentum {'up' if bull else 'down'}, "
                                      f"{'with' if fav else 'against'} the {'bounce' if need_up else 'pullback'} you need")})
            rsi = calculate_rsi(c)
            if rsi is not None and len(rsi):
                rv = float(rsi[-1])
                fav = (rv < 35) if need_up else (rv > 65)
                ta.append({"label": "RSI", "kind": "ta", "favorable": bool(fav),
                           "detail": f"RSI {rv:.0f} — {'oversold, bounce-prone' if rv < 35 else 'overbought, pullback-prone' if rv > 65 else 'neutral'}"})
            poc = _poc(closes, vols)
            if poc and px:
                nearpoc = abs(px - poc) / px < 0.02
                ta.append({"label": "Volume profile", "kind": "ta", "favorable": bool(nearpoc),
                           "detail": (f"spot is on the POC ${poc:.2f} — a high-volume magnet that tends to stall moves" if nearpoc
                                      else f"POC ${poc:.2f} ({((poc-px)/px*100):+.1f}%) — the high-volume shelf is away from spot")})
            rets = np.diff(np.log(c)) if len(c) > 1 else np.array([])
            if len(rets) > 60:
                hv20 = float(np.std(rets[-20:]) * (_bpy ** 0.5) * 100); hv60 = float(np.std(rets[-60:]) * (_bpy ** 0.5) * 100)
                _hv_cap = hv60
                state = "spiking" if hv20 > hv60 * 1.3 else "cooling" if hv20 < hv60 * 0.8 else "steady"
                context["vol_note"] = f"Realized vol {state} (HV20 {hv20:.0f}% vs HV60 {hv60:.0f}%)."
    except Exception:  # noqa: BLE001
        pass

    # gamma regime — dealers LONG γ (moves suppressed / mean-reverting) helps a bounce; SHORT γ (amplified) hurts.
    try:
        s = px or (float(spot) if spot else None)
        if s:
            contracts, _exps = _collect_contracts(stock, s)
            if len(contracts) >= 10:
                net = _net_gex_at(contracts, s); flip = _gamma_flip(contracts, s)
                long_g = net >= 0
                flip_txt = f", flip ${flip:.2f}" if flip else ""
                ta.append({"label": "Gamma regime", "kind": "ta", "favorable": bool(long_g),
                           "detail": (f"dealers LONG gamma (net GEX +{net/1e6:.0f}M{flip_txt}) — moves suppressed / mean-reverting, helps a bounce back" if long_g
                                      else f"dealers SHORT gamma (net GEX {net/1e6:.0f}M{flip_txt}) — moves amplified, works against a recovery")})
    except Exception:  # noqa: BLE001
        pass

    # ── CHART PATTERN + RANGE — is a classical pattern setting up a move TOWARD the strike, and where
    #    does spot sit in its range? A bullish break targeting a short call (or bearish a short put) is a
    #    real, chartable threat the greeks alone miss. ──
    try:
        from ..services.chart_pattern_service import compute_chart_patterns
        # Match the pattern window to the trade's horizon — a 60–90 DTE trade cares about the last ~quarter,
        # not a year-old double top; and when it's gone wrong, it's the RECENT action that matters.
        pat_period = "3mo" if (dte or 60) <= 90 else "6mo" if (dte or 60) <= 180 else "1y"
        cp = compute_chart_patterns(stock, period=pat_period, interval="1d")
        pats = (cp or {}).get("patterns") or []
        if pats:
            p = pats[0]
            pdir = p.get("direction"); ptype = str(p.get("type", "")).replace("_", " ")
            tgt = (p.get("target") or {}).get("price"); status = str(p.get("status", "")).replace("_", " ")
            fav = bool((pdir == "bullish") == need_up)      # bullish helps a put-recovery / threatens a call
            thr = ""
            if short_strike and tgt:
                ks = float(short_strike)
                if right == "C" and pdir == "bullish":
                    thr = f" — target {'PAST' if tgt >= ks else 'toward'} your ${ks:.0f} strike"
                elif right == "P" and pdir == "bearish":
                    thr = f" — target {'PAST' if tgt <= ks else 'toward'} your ${ks:.0f} strike"
            ta.append({"label": "Chart pattern", "kind": "ta", "favorable": fav,
                       "detail": f"{status} {pdir} {ptype} (conf {round((p.get('confidence') or 0)*100)}%), target ${tgt:.0f}{thr}"})
            context["pattern"] = {"type": ptype, "direction": pdir, "status": status, "target": tgt,
                                  "breakout": (p.get("breakout") or {}).get("level"),
                                  "confidence": round((p.get("confidence") or 0) * 100), "window": pat_period}
        tch = context.get("technical") or {}
        sup_, res_ = tch.get("support"), tch.get("resistance")
        if sup_ and res_ and px and res_ > sup_:
            width = (res_ - sup_) / px * 100.0
            where = ("ABOVE the range" if px > res_ else "BELOW the range" if px < sup_
                     else f"mid-range ({(px - sup_) / (res_ - sup_) * 100:.0f}% up)")
            ks_txt = ""
            if short_strike:
                ks = float(short_strike)
                ks_txt = (f" Your ${ks:.0f} strike is {'ABOVE the range top — the stock must break out to threaten it' if ks > res_ else 'inside the range' if ks >= sup_ else 'below the range'}."
                          if right == "C" else
                          f" Your ${ks:.0f} strike is {'BELOW the range floor — a breakdown is needed to threaten it' if ks < sup_ else 'inside the range' if ks <= res_ else 'above the range'}.")
            context["range"] = {"low": round(sup_, 2), "high": round(res_, 2), "width_pct": round(width, 1),
                                "where": where, "note": f"Range ${sup_:.2f}–${res_:.2f} ({width:.0f}% wide); spot is {where}.{ks_txt}"}
    except Exception:  # noqa: BLE001
        pass

    # context: recoverable time value + sector cohort + earnings-before-expiry
    context["loss_read"] = {"time_value_recoverable": round(float(extrinsic or 0.0) * 100.0 * (contracts_n or 1), 0)}
    try:
        themes = cas.themes_for(ticker) or []
        peers = [p for p in (cas.theme_peers(ticker) or []) if p.upper() != str(ticker).upper()][:8]
        context["sector"] = {"themes": themes, "peers": peers,
            "note": (f"{ticker} sits in {', '.join(themes)} — if the cohort is red together it's systemic; a sector hedge may beat a single-name repair."
                     if themes else "No mapped sector theme for this name.")}
    except Exception:  # noqa: BLE001
        pass
    try:
        ed = cas._next_earnings_sync(ticker)
        if ed:
            edd = dt.date.fromisoformat(str(ed)[:10]); days = (edd - dt.date.today()).days
            before = bool(dte is not None and 0 <= days <= dte)
            context["earnings"] = {"before_expiry": before, "date": edd.isoformat(), "days": days,
                "note": (f"Earnings {edd.isoformat()} in {days}d — BEFORE expiry: a binary gap that can leap the strike; defending/closing ahead of it is its own call."
                         if before else f"Next earnings {edd.isoformat()} ({days}d) — outside this expiry.")}
    except Exception:  # noqa: BLE001
        pass

    # ── QUANT RISK READ — reconcile with the management desk. Terminal-OTM alone is NAIVE: a Δ0.34 short
    #    can be 82% "safe" at expiry yet have a 75% chance of being BREACHED along the way, in an uptrend,
    #    with under-priced premium. Compute the drift-aware P(touch) + VRP + trend and DOWNGRADE a naive
    #    'healthy' to MARGINAL when the position is OTM but genuinely at risk. This is what makes Defend
    #    agree with the "STRONG CLOSE" the Manage panel already shows. ──
    try:
        T = max(dte, 1) / 365.0
        iv = (float(exp_move) / (float(spot) * (T ** 0.5))) if (exp_move and spot and T > 0) else None
        mu = None
        if _closes_cap is not None and len(_closes_cap) >= 20:
            e = _ema(_closes_cap, 20)
            look = min(len(e) - 1, max(5, int(_bpy / 52)))                 # ~1 week of bars
            if look > 0 and e[-1] > 0 and e[-1 - look] > 0:
                mu = float(np.log(e[-1] / e[-1 - look]) * (_bpy / look))   # annualized EMA slope (trend velocity)
        p_touch = _p_touch(float(spot), float(short_strike), iv, dte, mu if mu is not None else 0.0, right) if (short_strike and iv) else None
        vrp = (iv / (_hv_cap / 100.0) - 1.0) if (iv and _hv_cap and _hv_cap > 0) else None
        adverse_trend = bool(mu is not None and ((right == "C" and mu > 0.15) or (right == "P" and mu < -0.15)))
        _pat = context.get("pattern") or {}
        adverse_pattern = bool(_pat and _pat.get("status") != "broken out"
                               and ((right == "C" and _pat.get("direction") == "bullish")
                                    or (right == "P" and _pat.get("direction") == "bearish")))
        base = posture or "healthy"
        if base == "tested":
            final = "tested"
        else:
            final = ("marginal" if ((p_touch is not None and p_touch >= 0.50) or (vrp is not None and vrp <= -0.20)
                                     or adverse_trend or adverse_pattern) else "healthy")
        read = None
        if final == "marginal":
            bits = []
            if p_touch is not None:
                bits.append(f"a {round(p_touch*100)}% chance the ${float(short_strike):.0f} strike is BREACHED before expiry (drift-aware, ~2× the expiry-ITM odds)")
            if vrp is not None and vrp <= -0.10:
                bits.append(f"premium {abs(vrp)*100:.0f}% UNDER-priced (IV {iv*100:.0f}% < HV {_hv_cap:.0f}%) — you're not paid for the risk")
            if adverse_trend:
                bits.append(f"trend {mu*100:+.0f}%/yr pushing {'toward' if right=='C' else 'against'} the strike")
            if adverse_pattern:
                _tgt = _pat.get("target")
                bits.append(f"a {_pat.get('status')} {_pat.get('direction')} {_pat.get('type')}" + (f" targeting ${_tgt:.0f}" if _tgt else "") + " on the chart")
            read = ("Not tested yet — but a MARGINAL short, not a safe one: " + "; ".join(bits) +
                    ". A quant de-risks NOW: cap the tail (roll to a defined-risk spread / add a wing), transform the payoff "
                    "(asymmetric broken-wing fly), flatten gamma (calendarised hedge), or bank the credit and close.")
        elif final == "healthy":
            read = (f"Genuinely healthy — only a {round((p_touch or 0)*100)}% chance the strike is even touched, cushion wide"
                    + (f", premium fairly priced (VRP {vrp*100:+.0f}%)" if vrp is not None else "") + "; let theta work.")
        context["risk"] = {"posture": final,
                           "p_touch": (round(p_touch * 100, 1) if p_touch is not None else None),
                           "iv_pct": (round(iv * 100, 1) if iv else None), "hv_pct": (round(_hv_cap, 1) if _hv_cap else None),
                           "vrp_pct": (round(vrp * 100, 1) if vrp is not None else None),
                           "trend_pct": (round(mu * 100, 1) if mu is not None else None), "read": read}
    except Exception:  # noqa: BLE001
        pass

    # outlook — net tilt from the TA factors that carry a direction.
    _final_posture = ((context.get("risk") or {}).get("posture")) or posture
    dirn = [f for f in ta if f.get("favorable") is not None]
    favs = sum(1 for f in dirn if f["favorable"]); adv = sum(1 for f in dirn if not f["favorable"])
    if not dirn:
        tilt, note = "balanced", "Not enough technical data to read the setup."
    elif _final_posture == "healthy":
        # A far-OTM winner: adverse momentum is a THREAT to the cushion, not a recovery odds — and with a
        # wide cushion it's context, not a call to action. Never tell a healthy trade its odds are optimistic.
        tilt = "favorable" if favs > adv else "watch" if adv > favs else "balanced"
        cush = f" You have a {cushion_pct:.0f}% cushion, so this is context, not a threat yet." if cushion_pct else ""
        note = (f"This trade is HEALTHY — not tested. Technicals ({favs} favorable / {adv} pressuring the cushion) "
                + ("are pushing toward your strike — worth watching." if adv > favs else "are benign.")
                + cush)
    else:
        tilt = "favorable" if favs > adv else "adverse" if adv > favs else "balanced"
        note = (f"Technicals lean {tilt.upper()} for a recovery ({favs} favorable / {adv} adverse) — "
                + ("they back the model odds." if tilt == "favorable"
                   else "treat the raw recovery odds as OPTIMISTIC." if tilt == "adverse"
                   else "they neither help nor hurt the model odds."))
    return ta, {"tilt": tilt, "note": note}, context


@router.post("/{strategy_id}/defend/committee")
async def run_defend_committee(
    strategy_id: int,
    body: DefendPayloadIn,
    quote_source: str = "yfinance",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lens 7 — the WAR ROOM. A Quant → Risk → PM DEFENSE cascade fed ONLY the computed Defend numbers
    (recoverability, assignment, the HOLD baseline, the CLOSE benchmark, cost-of-waiting, and the
    priced repairs). One structured LLM call: the model synthesizes and picks a path but NEVER invents
    data. Returns {quant, risk, pm, verdict}."""
    import json as _json
    from ..services.llm_service import clean_json_text

    result = await db.execute(select(SavedStrategy).where(
        SavedStrategy.id == strategy_id, SavedStrategy.user_id == user.id))
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Trade not found")
    api_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="OpenAI API key not configured. Please add it in Settings.")

    d = body.defend or {}
    alts = d.get("alternatives") or []
    if not alts:
        return {"error": "Run the defend analysis first — the committee reasons over its numbers."}

    rec = d.get("recoverability") or {}
    asg = d.get("assignment") or {}
    hold = next((a for a in alts if a.get("category") == "hold"), {})
    close = next((a for a in alts if a.get("category") == "exit"), {})
    repairs = sorted([a for a in alts if a.get("category") not in ("exit", "hold")],
                     key=lambda a: (a.get("d_pop") if a.get("d_pop") is not None else -999), reverse=True)[:4]
    cw = d.get("cost_of_waiting") or []

    def _legs(a):
        return " ".join(f"{'−' if l.get('action') == 'SELL' else '+'}{l.get('qty')}{l.get('right')}{l.get('strike')}"
                        for l in (a.get("legs") or []) if l.get("right") in ("P", "C"))
    repairs_txt = "\n".join(
        f"  - {a.get('name')}: [{_legs(a)}] net {a.get('net_cash')}, max loss {a.get('max_loss')}, "
        f"PoP {a.get('pop_pct')}% (Δ{a.get('d_pop')} vs hold), E[P&L] {a.get('ev')}, defined_risk {a.get('defined_risk')}"
        for a in repairs) or "  (none priced)"
    cw_txt = "; ".join(f"+{x.get('in_trading_days')}d → PoP {x.get('recovery_pop')}%, E[P&L] {x.get('expected_pnl')}"
                       for x in cw) or "n/a"
    outlook = rec.get("outlook") or {}
    ta_facts = [f for f in (rec.get("factors") or []) if f.get("kind") == "ta"]
    ta_txt = "; ".join(f"{f.get('label')} [{'FAVORABLE' if f.get('favorable') else 'ADVERSE' if f.get('favorable') is False else 'neutral'}] {f.get('detail')}"
                       for f in ta_facts) or "n/a"
    ctxb = d.get("context") or {}
    ctx_extra = " ".join(x for x in [ctxb.get("vol_note"), (ctxb.get("sector") or {}).get("note"),
                                     (ctxb.get("earnings") or {}).get("note")] if x) or "n/a"
    ctx = (
        f"TESTED TRADE — {d.get('ticker')} {d.get('structure')} · tested {d.get('short_right')} ${d.get('short_strike')} "
        f"· spot ${d.get('spot')} · {d.get('dte_days')}d · cushion {d.get('cushion_pct')}% · mark-to-close {d.get('unrealized_pnl')}\n\n"
        f"RECOVERABILITY: recovery {rec.get('recovery_score')}% (P finish ≥ breakeven ${rec.get('breakeven')}), needs "
        f"{rec.get('needed_move_pct')}% ({rec.get('dist_to_be_sigma')}σ, ±${rec.get('expected_move')} expected move), "
        f"severity {rec.get('severity')}, tested Δ {rec.get('tested_delta')}\n"
        f"TECHNICAL OUTLOOK: {outlook.get('tilt')} — {outlook.get('note')}\n"
        f"TA FACTORS: {ta_txt}\n"
        f"ASSIGNMENT: P(ITM) {asg.get('p_itm')}%, time value ${asg.get('extrinsic')}, early-exercise {asg.get('early_assignment_risk')} "
        f"({asg.get('early_reason')}), effective basis ${asg.get('effective_basis')}, capital ${asg.get('assignment_capital')}\n"
        f"HOLD (do nothing): PoP {hold.get('pop_pct')}%, E[P&L] {hold.get('ev')}, max loss {hold.get('max_loss')}\n"
        f"CLOSE now: realizes {close.get('net_cash')}\n"
        f"COST OF WAITING: {cw_txt}\n"
        f"CONTEXT: {ctx_extra}\n"
        f"PRICED REPAIRS (ranked by Δrecovery vs hold):\n{repairs_txt}\n"
    )
    sys_prompt = (
        "You are a 3-seat DEFENSE COMMITTEE for a tested short-premium options trade that has gone against the "
        "trader. The premium-income thesis is DEAD; your ONLY mandate is to minimize expected loss / maximize "
        "recovery FROM HERE. You are given every number: recoverability (with a TECHNICAL OUTLOOK + signed TA "
        "factors), assignment, a HOLD (do-nothing) baseline, a CLOSE benchmark, the cost of waiting, and a menu "
        "of PRICED repairs.\n"
        "HARD RULES: reason ONLY from these numbers — NEVER invent a correlation, level, price, or fact; the quant "
        "(PoP, Δ-vs-hold, E[P&L], greeks) and the TA factors are GIVEN and correct — never recompute them; a repair "
        "must BEAT hold (Δrecovery > 0 and/or a materially smaller or newly-defined max loss) to justify its cost, "
        "and CLOSE is always the honest fallback. Be DETAILED and METRIC-DENSE: every sentence cites specific "
        "numbers, and the roles must AGREE with the final verdict (no contradictions).\n"
        "TAIL & RISK DISCIPLINE (do not violate): an UNDEFINED / unbounded max loss is THE risk — NEVER describe it "
        "as 'no additional risk' or a comfort. When the current max loss is undefined, CAPPING it (a defined-risk "
        "repair) is a FIRST-CLASS defensive objective and a valid reason to pick a repair even at some E[P&L] cost; "
        "do NOT dismiss a defined-risk repair merely because it doesn't raise recovery odds. Always state the "
        "assignment / called-away consequence explicitly and whether the trader can take it (capital, or keeping vs "
        "losing shares) — if they can't, favour a repair that rolls or caps. 'Hold' is only valid when the tail is "
        "bounded (or covered) AND its E[P&L] genuinely beats the alternatives; if the tail is open, say so and offer "
        "the cap as the risk-managed alternate.\n"
        "THREE SEATS (each: a short stance, a 2–4 sentence rationale citing ≥3 specific numbers, and a `metrics` "
        "list of the exact figures leaned on):\n"
        "• QUANT — the odds: recovery %, needed move in σ, how the TA OUTLOOK tilts those odds, which repair moves "
        "Δrecovery most and at what E[P&L] vs hold.\n"
        "• RISK — assignment P(ITM), early-exercise, capital at stake, max loss DEFINED vs OPEN (and if open, which "
        "repair caps it and by how much), whether the trader can take assignment. Flag an open tail as a concern, "
        "never as a reassurance.\n"
        "• PM — weigh it and pick ONE path: hold, a NAMED repair, take-assignment-and-wheel, or close.\n"
        "Return STRICT JSON, nothing else:\n"
        '{"quant":{"stance":"<=6 words","rationale":"2-4 sentences, number-dense","metrics":["figure","figure","figure"]},'
        '"risk":{"stance":"...","rationale":"...","metrics":["...","..."]},'
        '"pm":{"stance":"...","rationale":"...","metrics":["...","..."]},'
        '"verdict":{"primary_action":"the ONE action — name the repair, or Hold / Close",'
        '"why":"2-3 sentences citing recovery %, Δrecovery, E[P&L], max loss and the TA tilt",'
        '"confidence":"low|medium|high",'
        '"alternates":[{"action":"second choice","why":"one line, why it is second not first"},'
        '{"action":"third choice","why":"one line"}],'
        '"do_not":"the one tempting mistake to avoid, tied to a number (or null)"}}'
    )
    messages = [{"role": "system", "content": sys_prompt},
                {"role": "user", "content": ctx + "\nDeliberate as the three seats, then return ONLY the JSON object."}]
    try:
        raw = await call_llm(api_key=api_key, model="gpt-4o", messages=messages, max_tokens=1100, temperature=0.2)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Defense committee failed: {exc}")
    try:
        parsed = _json.loads(clean_json_text(raw))
    except Exception:  # noqa: BLE001
        return {"error": "The committee returned an unparseable response — try again."}

    def _role(key, title):
        x = parsed.get(key) or {}
        mets = x.get("metrics")
        mets = [str(m) for m in mets][:6] if isinstance(mets, list) else []
        return {"role": title, "stance": (x.get("stance") or "").strip(),
                "rationale": (x.get("rationale") or "").strip(), "metrics": mets}

    def _alt(a):
        if isinstance(a, dict):
            return {"action": (a.get("action") or "").strip(), "why": (a.get("why") or "").strip()}
        return {"action": str(a).strip(), "why": ""}          # tolerate a bare-string alternate
    verdict = parsed.get("verdict")
    if isinstance(verdict, dict):
        alts = verdict.get("alternates") or []
        verdict = {"primary_action": (verdict.get("primary_action") or "").strip(),
                   "why": (verdict.get("why") or "").strip(),
                   "confidence": (verdict.get("confidence") or "").strip().lower() or None,
                   "alternates": [_alt(a) for a in alts if a][:3],
                   "do_not": (verdict.get("do_not") or None)}
    else:
        verdict = None
    return {"quant": _role("quant", "Quant"), "risk": _role("risk", "Risk"), "pm": _role("pm", "PM"),
            "verdict": verdict, "data_sent": ctx}


@router.post("/{strategy_id}/defend/optimize-roll")
async def optimize_defend_roll(
    strategy_id: int,
    quote_source: str = "yfinance",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deep-quant ROLL OPTIMIZER — the credit-only, market-structure-aware roll finder. Picks the
    (strike, expiry) to roll the tested short into that best balances credit (no net new money),
    capital, and the probability the NEXT short holds — judged by the market RND plus support/
    resistance, gamma flip/wall (GEX) and the volume POC. Heavy (multi-expiry chains + RND), so lazy."""
    import datetime as dt
    from ..services.quote_providers import get_provider
    from ..services.roll_optimizer_service import optimize_roll

    result = await db.execute(select(SavedStrategy).where(
        SavedStrategy.id == strategy_id, SavedStrategy.user_id == user.id))
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Trade not found")

    raw = json.loads(strategy.legs_data) if strategy.legs_data else []
    entry_prices = json.loads(strategy.entry_prices) if strategy.entry_prices else []
    shorts, covered_shares, near_exp = [], 0.0, None
    for i, l in enumerate(raw):
        typ = str(l.get("type", "")).lower()
        act = str(l.get("action", "")).upper()
        if l.get("strike") and ("call" in typ or "put" in typ):
            right = "P" if "put" in typ else "C"
            sign = -1 if "SELL" in act else 1
            ep = entry_prices[i].get("price") if i < len(entry_prices) and isinstance(entry_prices[i], dict) else None
            entry = abs(float(ep)) if ep else abs(float(l.get("premium") or l.get("mid") or 0.0))
            if sign < 0:
                shorts.append({"right": right, "strike": float(l["strike"]),
                               "qty": int(l.get("qty") or l.get("contracts") or 1), "entry": entry})
            near_exp = near_exp or str(l.get("expiration") or l.get("expiry") or "")[:10]
        elif ("stock" in typ or "share" in typ or "equity" in typ) and "BUY" in act:
            covered_shares += abs(float(l.get("qty") or l.get("shares") or 0.0))
    if not shorts:
        return {"error": "The roll optimizer is for a short-premium trade (CSP / covered call / short call)."}

    provider = get_provider(quote_source, user=user, db=db)
    try:
        uq = await provider.get_underlying_price(strategy.ticker)
        spot = float(getattr(uq, "price", None) or getattr(uq, "last", None) or 0.0)
    except Exception:  # noqa: BLE001
        spot = 0.0
    if spot <= 0:
        return {"error": "Couldn't fetch the underlying price to optimize the roll."}

    try:
        current_dte = max(1, (dt.date.fromisoformat(near_exp) - dt.date.today()).days)
    except (ValueError, TypeError):
        current_dte = 30

    # the tested short = the one furthest into / nearest through the money.
    tested = max(shorts, key=lambda s: (s["strike"] - spot) if s["right"] == "P" else (spot - s["strike"]))
    covered = tested["right"] == "C" and covered_shares >= 100 * tested["qty"]

    menu = await optimize_roll(
        ticker=strategy.ticker, provider=provider, spot=spot,
        tested_right=tested["right"], tested_strike=tested["strike"], tested_qty=tested["qty"],
        tested_entry=tested["entry"], current_dte=current_dte, covered=covered)
    menu["ticker"] = strategy.ticker
    return _to_native(menu)


@router.get("/{strategy_id}/live-pnl")
async def get_live_pnl(
    strategy_id: int,
    quote_source: str = "yfinance",
    margin_mode: str = "reg_t",           # 'reg_t' (default) | 'portfolio' — from Settings
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Fetch current quotes for an active trade and compute unrealized P&L with full analysis."""
    import asyncio
    import datetime as dt
    import math
    from scipy.stats import norm
    from ..services.quote_providers import get_provider
    from ..services.stock_service import bs_price, bs_delta, bs_gamma, bs_theta, bs_vega
    from ..services.trade_math import (
        classify_leg_action, summarize_trade_actions, prob_itm_lognormal, dte_from_expiry,
        structure_payoff_extremes, structure_breakevens, exit_recommendation,
    )
    from ..services.lifecycle_service import (
        higher_order_greeks, pm_ratios, payoff_distribution_metrics,
        algorithmic_quant, management_exit, _weighted_var_cvar,
    )
    import numpy as _np

    def _leg_p_itm(right, strike, exp_key, leg_dte, iv, delta, rnd_map):
        """Risk-neutral P(leg finishes ITM): market-implied RND first, then a
        Black-Scholes lognormal fallback, then |delta| as a last resort."""
        if strike and strike > 0:
            rnd = rnd_map.get(exp_key)
            if rnd is not None:
                return rnd.prob_above(strike) if right == "C" else rnd.prob_below(strike)
            p = prob_itm_lognormal(underlying_price, strike, iv, leg_dte, right)
            if p is not None:
                return p
        if delta is not None:
            return min(0.99, abs(delta))   # delta ≈ risk-neutral P(ITM)
        return None

    result = await db.execute(
        select(SavedStrategy).where(
            SavedStrategy.id == strategy_id,
            SavedStrategy.user_id == user.id,
            SavedStrategy.trade_status == "active",
        )
    )
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Active trade not found")

    legs = json.loads(strategy.legs_data)
    entry_prices_list = json.loads(strategy.entry_prices) if strategy.entry_prices else []
    entry_cost = strategy.entry_net_debit or 0.0
    params = json.loads(strategy.parameters) if strategy.parameters else {}

    # --- Provider setup ---
    try:
        if quote_source == "ibkr":
            provider = get_provider("ibkr", user=user, db=db)
        else:
            provider = get_provider("yfinance")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Detect hybrid positions: any trade that carries a stock leg (parameters.shares > 0)
    # AND option legs in legs_data should go through the combo path regardless of
    # strategy_type (covers covered_call, stock_long-with-added-legs, etc.)
    _has_stock_params = float(params.get("shares") or 0) > 0
    _has_option_legs  = any(l.get("type", "").upper() in ("CALL", "PUT") for l in legs)

    # --- Futures strategies: early return with futures-specific P&L ---
    if strategy.strategy_type == "futures":
        import datetime as dt
        underlying_price = 0.0
        try:
            uq = await provider.get_underlying_price(strategy.ticker)
            underlying_price = uq.price
        except Exception:
            pass

        contracts = float(params.get("contracts") or params.get("shares") or 1)
        avg_cost = float(
            params.get("avg_cost")
            or (entry_prices_list[0].get("price") if entry_prices_list else None)
            or 0
        )
        multiplier = float(params.get("multiplier") or 1.0)
        is_long = params.get("action") != "short"

        cost_basis = contracts * avg_cost * multiplier
        current_value = contracts * underlying_price * multiplier if underlying_price > 0 else 0.0

        if is_long:
            unrealized_pnl = (underlying_price - avg_cost) * contracts * multiplier if underlying_price > 0 else 0.0
        else:
            unrealized_pnl = (avg_cost - underlying_price) * contracts * multiplier if underlying_price > 0 else 0.0

        margin_req = float(params.get("margin_req") or 0.0)
        pnl_basis = margin_req if margin_req > 0 else cost_basis
        pnl_pct = round((unrealized_pnl / pnl_basis) * 100, 2) if pnl_basis > 0 else 0.0
        if strategy.entry_date:
            ed = strategy.entry_date if strategy.entry_date.tzinfo else strategy.entry_date.replace(tzinfo=dt.timezone.utc)
            days_held = (dt.datetime.now(dt.timezone.utc) - ed).days
        else:
            days_held = 0

        # Quantitative Futures Recommendation Algorithm
        import numpy as np
        import pandas as pd
        import yfinance as yf

        hold_signal = "HOLD"
        hold_reasons = []
        ta_signals = []
        macro_signals = []
        underlying_outlook = "NEUTRAL"
        rsi = 50.0

        dte = dte_from_expiry(params.get("expiration")) if params.get("expiration") else None
        pnl_basis = margin_req if margin_req > 0 else cost_basis
        pnl_pct_value = (unrealized_pnl / pnl_basis) * 100 if pnl_basis > 0 else 0.0

        if underlying_price > 0:
            # 1. Fetch history to compute Technical Indicators (SMA, RSI, MACD)
            try:
                symbol_norm = strategy.ticker.strip().upper()
                if symbol_norm.startswith("/"):
                    symbol_norm = symbol_norm[1:]
                    if not ("=" in symbol_norm or "." in symbol_norm):
                        symbol_norm = symbol_norm + "=F"

                ticker_obj = yf.Ticker(symbol_norm)
                history = await asyncio.to_thread(
                    ticker_obj.history, period="300d", interval="1d"
                )
                if not history.empty and len(history) >= 50:
                    closes = history["Close"]
                    price_val = float(closes.iloc[-1])
                    
                    # Moving averages
                    sma50_series = closes.rolling(min(50, len(closes))).mean()
                    sma200_series = closes.rolling(min(200, len(closes))).mean()
                    sma50 = float(sma50_series.iloc[-1]) if len(sma50_series) > 0 else price_val
                    sma200 = float(sma200_series.iloc[-1]) if len(sma200_series) > 0 else price_val
                    
                    # RSI (14-day)
                    delta = closes.diff()
                    gain = delta.clip(lower=0)
                    loss = (-delta).clip(lower=0)
                    avg_g = gain.ewm(com=13, adjust=False).mean()
                    avg_l = loss.ewm(com=13, adjust=False).mean()
                    rs = avg_g / avg_l.replace(0, np.nan)
                    rsi_series = 100 - (100 / (1 + rs))
                    rsi = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50.0
                    
                    # MACD (12, 26, 9)
                    ema12 = closes.ewm(span=12, adjust=False).mean()
                    ema26 = closes.ewm(span=26, adjust=False).mean()
                    macd = ema12 - ema26
                    signal_line = macd.ewm(span=9, adjust=False).mean()
                    macd_val = float(macd.iloc[-1])
                    sig_val = float(signal_line.iloc[-1])
                    
                    # Technical scoring
                    tech_score = 0.0
                    if price_val > sma50: tech_score += 1.0
                    if price_val > sma200: tech_score += 1.0
                    if sma50 > sma200: tech_score += 1.0
                    if macd_val > sig_val: tech_score += 1.0
                    if rsi > 50.0: tech_score += 0.5
                    if rsi < 30.0: tech_score += 1.5  # oversold reversal potential
                    if rsi > 70.0: tech_score -= 1.5  # overbought exhaustion risk
                    
                    if price_val < sma50: tech_score -= 1.0
                    if price_val < sma200: tech_score -= 1.0
                    if sma50 < sma200: tech_score -= 1.0
                    if macd_val < sig_val: tech_score -= 1.0
                    
                    if tech_score >= 1.5:
                        underlying_outlook = "BULLISH"
                        ta_signals.append(f"TA Profile: BULLISH trend (Price ${price_val:.2f} above SMA50/SMA200, MACD positive).")
                    elif tech_score <= -1.5:
                        underlying_outlook = "BEARISH"
                        ta_signals.append(f"TA Profile: BEARISH trend (Price ${price_val:.2f} below SMA50/SMA200, MACD momentum negative).")
                    else:
                        underlying_outlook = "NEUTRAL"
                        ta_signals.append("TA Profile: NEUTRAL rangebound consolidation.")
                    
                    if rsi > 70.0:
                        ta_signals.append(f"RSI Warning: Overbought exhaustion conditions ({rsi:.1f}).")
                    elif rsi < 30.0:
                        ta_signals.append(f"RSI Signal: Deeply oversold support bounce candidate ({rsi:.1f}).")
            except Exception as e:
                logger.error(f"Error computing TA for futures: {e}")

            # 2. Fetch Macro & Sentiment Context (VIX for sentiment, TNX for yields/macro)
            try:
                vix_ticker = yf.Ticker("^VIX")
                tnx_ticker = yf.Ticker("^TNX")
                vix_info = await asyncio.to_thread(vix_ticker.history, period="5d")
                tnx_info = await asyncio.to_thread(tnx_ticker.history, period="5d")
                
                if not vix_info.empty:
                    vix_close = float(vix_info["Close"].iloc[-1])
                    if vix_close > 22.0:
                        macro_signals.append(f"Sentiment: Fearful market regime (VIX at {vix_close:.1f}), hedging recommended.")
                    elif vix_close < 13.0:
                        macro_signals.append(f"Sentiment: High market complacency (VIX low at {vix_close:.1f}).")
                    else:
                        macro_signals.append(f"Sentiment: Normal volatility environment (VIX at {vix_close:.1f}).")
                        
                if not tnx_info.empty:
                    tnx_close = float(tnx_info["Close"].iloc[-1])
                    tnx_prev = float(tnx_info["Close"].iloc[0])
                    yield_trend = "rising" if tnx_close > tnx_prev else "falling"
                    macro_signals.append(f"Rates/Debt: 10Y Treasury Yield is {yield_trend} at {tnx_close:.2f}%.")
                    
                    symbol_upper = strategy.ticker.upper()
                    is_gold = "GOLD" in symbol_upper or "GC" in symbol_upper or "1OZ" in symbol_upper
                    is_equity = "SPX" in symbol_upper or "ES" in symbol_upper or "NQ" in symbol_upper or "YM" in symbol_upper
                    is_long = params.get("action") != "short"
                    
                    if is_gold:
                        if yield_trend == "falling":
                            macro_signals.append("Macro: Falling yields support gold as opportunity cost of holding non-yielding asset decreases.")
                        else:
                            macro_signals.append("Macro: Rising yields create headwinds for gold (opportunity cost increases).")
                    elif is_equity:
                        if yield_trend == "rising" and tnx_close > 4.2:
                            macro_signals.append("Debt Headwind: Rising yields raise corporate interest expenses and refinancing costs, squeezing profit margins.")
            except Exception as e:
                logger.error(f"Error fetching macro signals: {e}")

            # 3. Rollover risk check (DTE)
            if dte is not None and dte <= 5:
                hold_signal = "ROLL / CLOSE"
                hold_reasons.append(f"Rollover Alert: Contract expires in {dte} days. Roll or close to avoid delivery.")
            elif dte is not None and dte <= 10:
                hold_signal = "ROLL SOON"
                hold_reasons.append(f"Contract expiration approaching ({dte} days remaining). Plan rollover.")
            elif dte is not None and dte <= 21:
                hold_signal = "MANAGE (21-DTE)"
                hold_reasons.append(f"21-DTE management window: {dte} days left. Gamma accelerates inside 21 DTE — "
                                    f"the desk takes profit on winners and rolls tested strikes HERE rather than "
                                    f"carrying pin / assignment risk into expiration.")

            # 3b. Earnings vs expiry — the vega-event rule: don't hold SHORT premium through the print. If the
            # next print lands before expiry the fat premium is inflated by the IV ramp and a gap is the tail;
            # advise closing before it (the crush only pays if you survive the gap outside the implied move).
            try:
                _exp = params.get("expiration")
                _edf = getattr(ticker_obj, "earnings_dates", None)
                _ne = None
                if _edf is not None and not _edf.empty:
                    _td = dt.date.today()
                    _fut = [ix.date() for ix in _edf.index if hasattr(ix, "date") and ix.date() >= _td]
                    _ne = min(_fut) if _fut else None
                if _ne and _exp:
                    _expd = dt.date.fromisoformat(_exp)
                    _d2e = (_ne - dt.date.today()).days
                    _is_short = params.get("action") == "short"
                    if _ne <= _expd:
                        if _is_short:
                            hold_signal = "CLOSE BEFORE EARNINGS"
                            hold_reasons.insert(0, f"⚠ Earnings {_ne.isoformat()} ({_d2e}d) is BEFORE your {_exp} "
                                f"expiry — you're holding SHORT premium THROUGH the print. The pre-earnings IV ramp "
                                f"puffed the premium, but a gap through your strike is the tail. Unless the short "
                                f"strikes sit OUTSIDE the implied move (an intentional crush harvest), CLOSE before "
                                f"{_ne.isoformat()} to bank the decay and sidestep the binary.")
                        else:
                            hold_reasons.append(f"Earnings {_ne.isoformat()} ({_d2e}d) before your {_exp} expiry — a "
                                f"vega event: expect a gap and a post-print IV crush. Size / manage exposure into it.")
                    else:
                        hold_reasons.append(f"Clean of earnings — the next print {_ne.isoformat()} is AFTER your "
                            f"{_exp} expiry; no event / gap risk in this trade's life (you keep the elevated premium "
                            f"without the binary).")
            except Exception as _ee:
                logger.debug(f"earnings-vs-expiry check failed: {_ee}")

            # 4. Synthesis of P&L + TA + Macro
            if hold_signal == "HOLD":
                is_long = params.get("action") != "short"
                if pnl_pct_value <= -20.0:
                    # Stop loss exceeded
                    if underlying_outlook == "BULLISH" and rsi < 35.0 and is_long:
                        hold_signal = "HOLD / MONITOR"
                        hold_reasons.append(f"Risk Warning: Position down {abs(pnl_pct_value):.1f}% on margin, but underlying has structurally bullish trend and is oversold. Monitor support closely before exiting.")
                    else:
                        hold_signal = "CLOSE / STOP LOSS"
                        hold_reasons.append(f"Stop Loss Triggered: Position down {abs(pnl_pct_value):.1f}% on margin. Underlying trend is {underlying_outlook} — close to limit risk.")
                elif pnl_pct_value >= 25.0:
                    # Profit target met
                    if underlying_outlook == "BEARISH" and is_long:
                        hold_signal = "TAKE PROFIT"
                        hold_reasons.append(f"Profit Target Met: Up {pnl_pct_value:.1f}% on margin. Underlying momentum has turned bearish — lock in gains.")
                    else:
                        hold_signal = "STRONG HOLD"
                        hold_reasons.append(f"Profit Target Met: Up {pnl_pct_value:.1f}% on margin. Underlying trend is bullish/supportive — hold for further upside.")
                elif pnl_pct_value <= -10.0:
                    # Drawdown
                    if underlying_outlook == "BEARISH" and is_long:
                        hold_signal = "REDUCE SIZE"
                        hold_reasons.append(f"Drawdown Alert: Down {abs(pnl_pct_value):.1f}% on margin. Bearish underlying momentum suggests scaling down exposure.")
                    else:
                        hold_signal = "HOLD"
                        hold_reasons.append(f"Drawdown of {abs(pnl_pct_value):.1f}% on margin. Trend is constructive; hold and monitor support levels.")
                else:
                    # Normal range
                    if underlying_outlook == "BULLISH" and is_long:
                        hold_signal = "ACCUMULATE"
                        hold_reasons.append(f"Accumulate: Stable performance ({pnl_pct_value:+.1f}%) with a bullish technical framework.")
                    elif underlying_outlook == "BEARISH" and is_long:
                        hold_signal = "HOLD / CAUTION"
                        hold_reasons.append("Hold (Caution): Neutral performance, but underlying exhibits bearish momentum.")
                    else:
                        hold_signal = "HOLD"
                        hold_reasons.append("Hold: Rangebound consolidation. Trade remains in line with thesis.")

            # Append indicators details
            hold_reasons.extend(ta_signals)
            hold_reasons.extend(macro_signals)
        else:
            hold_reasons = ["Could not fetch live price — check manually"]

        # Linear futures payoff graph + extremes (multiplier-scaled).
        _fut_pay = build_payoff(
            underlying_price, [],
            {"shares": contracts if is_long else -contracts, "avg_cost": avg_cost, "mult": multiplier},
            0.0,
        )

        return {
            "strategy_id": strategy_id,
            "ticker": strategy.ticker,
            "quote_source": quote_source,
            "underlying_price": round(underlying_price, 2),
            "entry_cost": round(cost_basis, 2),
            "current_value": round(current_value, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "pnl_pct": pnl_pct,
            "days_held": days_held,
            "current_quotes": [],
            "greeks": [],
            "net_greeks": {"delta": contracts * multiplier if is_long else -contracts * multiplier, "gamma": 0.0, "theta": 0.0, "vega": 0.0},
            "margin_required": margin_req,
            "total_capital": round(margin_req if margin_req > 0 else cost_basis, 2),
            "scenarios": _fut_pay["scenarios"],
            "theta_projection": [],
            "max_profit": _fut_pay["max_profit"],
            "max_loss": _fut_pay["max_loss"],
            "max_profit_price": _fut_pay["max_profit_price"],
            "max_loss_price": _fut_pay["max_loss_price"],
            "unbounded_profit": _fut_pay["unbounded_profit"],
            "unbounded_loss": _fut_pay["unbounded_loss"],
            "breakevens": [round(avg_cost, 2)] if avg_cost > 0 else [],
            "expiration_date": params.get("expiration") or None,
            "leg_analysis": [],
            "analysis": {
                "annualized_return_to_expiry": None,
                "probability_of_profit": None,
                "pop_method": None,
                "expected_value": None,
                "risk_reward_ratio": None,
                "kelly_fraction": None,
                "theta_burn_rate_day": 0.0,
                "theta_burn_rate_pct": 0.0,
                "days_to_theta_breakeven": None,
                "hold_vs_close": hold_signal,
                "hold_vs_close_reasons": hold_reasons,
                "dte_remaining": dte_from_expiry(params.get("expiration")) if params.get("expiration") else 0,
                "recommendation": {
                    "action": hold_signal,
                    "headline": f"{hold_signal.title()} — Futures {'Long' if is_long else 'Short'} {strategy.ticker} "
                                + (f"up ${unrealized_pnl:,.0f}" if unrealized_pnl >= 0 else f"down ${abs(unrealized_pnl):,.0f}"),
                    "leg_notes": [],
                    "reasons": hold_reasons,
                },
                "exit_signal": _EXIT_MAP.get(hold_signal, "HOLD"),
                "exit_reasons": hold_reasons,
                "captured_pct": None,
            },
        }

    # --- Stock strategies: early return with equity-specific P&L ---
    if strategy.strategy_type in ("stock_long", "stock_short") and not _has_option_legs:
        import datetime as dt
        underlying_price = 0.0
        try:
            uq = await provider.get_underlying_price(strategy.ticker)
            underlying_price = uq.price
        except Exception:
            pass

        shares = float(params.get("shares") or 0)
        # Priority: params.avg_cost (ledger-computed weighted avg, updated on every
        # transaction) > entry_prices[0].price (original entry, stale after adds) >
        # fallback from entry_net_debit.
        avg_cost = float(
            params.get("avg_cost")
            or (entry_prices_list[0].get("price") if entry_prices_list else None)
            or (abs(entry_cost) / shares if shares > 0 else 0)
        )
        cost_basis = shares * avg_cost  # always positive
        current_value = shares * underlying_price if underlying_price > 0 else 0.0
        unrealized_pnl = current_value - cost_basis
        pnl_pct = round((unrealized_pnl / cost_basis) * 100, 2) if cost_basis > 0 else 0.0
        if strategy.entry_date:
            ed = strategy.entry_date if strategy.entry_date.tzinfo else strategy.entry_date.replace(tzinfo=dt.timezone.utc)
            days_held = (dt.datetime.now(dt.timezone.utc) - ed).days
        else:
            days_held = 0

        is_long = strategy.strategy_type == "stock_long"
        if underlying_price > 0:
            hold_signal = "HOLD"
            if unrealized_pnl > 0:
                hold_reasons = ["Position is in profit — monitor vs target"]
            else:
                hold_reasons = ["Position is at a loss — review thesis"]
        else:
            hold_signal = "HOLD"
            hold_reasons = ["Could not fetch live price — check manually"]

        # Linear payoff graph + extremes (long: unlimited upside, max loss = cost basis).
        _pay = build_payoff(
            underlying_price, [],
            {"shares": shares if is_long else -shares, "avg_cost": avg_cost, "mult": 1.0},
            0.0,
        )

        return {
            "strategy_id": strategy_id,
            "ticker": strategy.ticker,
            "quote_source": quote_source,
            "underlying_price": round(underlying_price, 2),
            "entry_cost": round(cost_basis, 2),          # always positive for stocks
            "current_value": round(current_value, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "pnl_pct": pnl_pct,
            "days_held": days_held,
            "current_quotes": [],
            "greeks": [],
            "net_greeks": {"delta": shares if is_long else -shares, "gamma": 0.0, "theta": 0.0, "vega": 0.0},
            "margin_required": 0.0,
            "total_capital": round(cost_basis, 2),
            "scenarios": _pay["scenarios"],
            "theta_projection": [],
            "max_profit": _pay["max_profit"],
            "max_loss": _pay["max_loss"],
            "max_profit_price": _pay["max_profit_price"],
            "max_loss_price": _pay["max_loss_price"],
            "unbounded_profit": _pay["unbounded_profit"],
            "unbounded_loss": _pay["unbounded_loss"],
            "breakevens": [round(avg_cost, 2)] if avg_cost > 0 else [],
            "expiration_date": None,
            "leg_analysis": [],
            "analysis": {
                "annualized_return_to_expiry": None,
                "probability_of_profit": None,
                "pop_method": None,
                "expected_value": None,
                "risk_reward_ratio": None,
                "kelly_fraction": None,
                "theta_burn_rate_day": 0.0,
                "theta_burn_rate_pct": 0.0,
                "days_to_theta_breakeven": None,
                "hold_vs_close": hold_signal,
                "hold_vs_close_reasons": hold_reasons,
                "dte_remaining": 0,
                "recommendation": {
                    "action": hold_signal,
                    "headline": ("Hold" if "HOLD" in hold_signal else "Close")
                                + f" — {'long' if is_long else 'short'} {strategy.ticker} "
                                + (f"up ${unrealized_pnl:,.0f}" if unrealized_pnl >= 0 else f"down ${abs(unrealized_pnl):,.0f}"),
                    "outcome": f"Trade outcome — {'long' if is_long else 'short'} equity, currently "
                               + (f"up ${unrealized_pnl:,.0f}" if unrealized_pnl >= 0 else f"down ${abs(unrealized_pnl):,.0f}")
                               + f" ({pnl_pct:+.1f}%).",
                    "leg_notes": [],
                    "reasons": hold_reasons,
                },
                "exit_signal": _EXIT_MAP.get(hold_signal, "HOLD"),
                "exit_reasons": hold_reasons,
                "captured_pct": None,
            },
        }

    # --- stock + options combo: covers stock_combo, covered_call, and any stock trade
    #     that had option legs added later ---
    if strategy.strategy_type in ("stock_combo", "covered_call") or (_has_stock_params and _has_option_legs):
        underlying_price = 0.0
        try:
            uq = await provider.get_underlying_price(strategy.ticker)
            underlying_price = uq.price
        except Exception:
            pass

        shares = float(params.get("shares") or 0)
        avg_cost = float(
            params.get("avg_cost")
            or (entry_prices_list[0].get("price") if entry_prices_list else None)
            or (abs(entry_cost) / shares if shares > 0 else 0)
        )
        stock_cost = shares * avg_cost
        stock_value = shares * underlying_price if underlying_price > 0 else 0.0
        stock_pnl = stock_value - stock_cost

        # Process option legs if any
        opt_legs = [l for l in legs if l.get("type") and str(l.get("type", "")).upper() in ("CALL", "PUT")]
        opt_entry_cost = float(params.get("options_net_debit") or 0)
        opt_current_net = 0.0
        opt_quotes = []
        opt_greeks = []
        combo_leg_meta = []   # always defined — a covered call whose option leg was closed has NO opt_legs
        if opt_legs:
            snapshot = json.loads(strategy.result_snapshot) if strategy.result_snapshot else {}
            strategy_expiration = snapshot.get("expirationDate") or params.get("expiration") or None

            # Build per-leg metadata
            combo_leg_meta = []
            for i, leg in enumerate(opt_legs):
                strike = leg.get("strike")
                opt_type = leg.get("type", "").upper()
                expiration = leg.get("expiration") or leg.get("exp") or leg.get("expiry") or strategy_expiration
                right = "C" if "CALL" in opt_type else "P"
                qty = leg.get("qty", 1)
                action = leg.get("action", "").upper()
                combo_leg_meta.append({
                    "i": i, "strike": strike, "type": opt_type, "expiration": expiration,
                    "right": right, "qty": qty, "action": action, "_leg": leg,
                })

            # Prefer a leg-derived options net debit over the stored params value,
            # for the same consistency reason as the pure-options path. entry_prices[0]
            # is the stock leg on combos, so option entry rows start at index 1.
            _combo_ep = entry_prices_list[1:] if len(entry_prices_list) > len(opt_legs) else entry_prices_list
            _combo_debit = option_legs_net_debit(combo_leg_meta, _combo_ep)
            if _combo_debit is not None:
                opt_entry_cost = _combo_debit

            def _record_combo_quote(lm: dict, q: object) -> None:
                opt_quotes.append({
                    "leg": lm["i"], "strike": lm["strike"], "type": lm["type"],
                    "bid": q.bid, "ask": q.ask, "mid": q.mid, "oi": q.oi, "volume": q.volume,
                })
                opt_greeks.append({
                    "leg": lm["i"], "strike": lm["strike"], "type": lm["type"],
                    "iv": round(q.iv * 100, 2) if q.iv else None,
                    "delta": round(q.delta, 4) if q.delta else None,
                    "gamma": round(q.gamma, 4) if q.gamma else None,
                    "theta": round(q.theta, 4) if q.theta else None,
                    "vega": round(q.vega, 4) if q.vega else None,
                })
                nonlocal opt_current_net
                if "BUY" in lm["action"]:
                    opt_current_net += q.mid * lm["qty"] * 100
                else:
                    opt_current_net -= q.mid * lm["qty"] * 100

            if quote_source == "ibkr" and hasattr(provider, "get_multiple_option_quotes"):
                # IBKR fast batch path
                req_dicts = []
                valid_combo_indices = []
                for lm in combo_leg_meta:
                    if lm["strike"] and lm["expiration"]:
                        req_dicts.append({
                            "symbol": strategy.ticker, "expiration": lm["expiration"],
                            "strike": float(lm["strike"]), "right": lm["right"],
                        })
                        valid_combo_indices.append(lm["i"])
                if req_dicts:
                    try:
                        ibkr_quotes = await provider.get_multiple_option_quotes(req_dicts)
                        # Retry warmup failures
                        failed = [j for j, q in enumerate(ibkr_quotes) if q is None]
                        if failed:
                            await asyncio.sleep(0.8)
                            retry = await provider.get_multiple_option_quotes([req_dicts[j] for j in failed])
                            for ri, j in enumerate(failed):
                                if retry[ri] is not None:
                                    ibkr_quotes[j] = retry[ri]
                        for j, q in enumerate(ibkr_quotes):
                            idx = valid_combo_indices[j]
                            lm = combo_leg_meta[idx]
                            if q is not None:
                                _record_combo_quote(lm, q)
                    except Exception:
                        pass
            else:
                # Generic / yfinance path — get_option_chain per expiration
                chain_cache_combo: dict = {}
                for lm in combo_leg_meta:
                    if not lm["strike"] or not lm["expiration"]:
                        continue
                    try:
                        exp_key = lm["expiration"]
                        if exp_key not in chain_cache_combo:
                            chain_cache_combo[exp_key] = await provider.get_option_chain(strategy.ticker, exp_key)
                        chain = chain_cache_combo[exp_key]
                        target = float(lm["strike"])
                        same_right = [q for q in chain.quotes if q.right == lm["right"]]
                        matching = sorted(same_right, key=lambda q: abs(q.strike - target))
                        if not matching or abs(matching[0].strike - target) >= 0.50:
                            continue
                        q = matching[0]
                        # Fill missing Greeks via BS when IV is available (yfinance doesn't return Greeks)
                        _giv = q.iv
                        _gd2, _gg2, _gt2, _gv2 = q.delta, q.gamma, q.theta, q.vega
                        if _giv and underlying_price > 0 and any(
                            x is None for x in (_gd2, _gg2, _gt2, _gv2)
                        ):
                            try:
                                _exp_dt2 = dt.datetime.strptime(lm["expiration"], "%Y-%m-%d").date()
                                _T2 = max((_exp_dt2 - dt.datetime.now(dt.timezone.utc).date()).days / 365.0, 0.001)
                                _K2, _S2, _r2 = float(lm["strike"]), underlying_price, 0.05
                                _otype2 = "call" if lm["right"] == "C" else "put"
                                if _gd2 is None:
                                    _gd2 = bs_delta(_S2, _K2, _T2, _r2, _giv, _otype2)
                                if _gg2 is None:
                                    _gg2 = bs_gamma(_S2, _K2, _T2, _r2, _giv)
                                if _gt2 is None:
                                    _gt2 = bs_theta(_S2, _K2, _T2, _r2, _giv, _otype2)
                                if _gv2 is None:
                                    _gv2 = bs_vega(_S2, _K2, _T2, _r2, _giv)
                            except Exception:
                                pass

                        class _QWithBS:
                            def __init__(self, orig, delta, gamma, theta, vega):
                                self.bid = orig.bid; self.ask = orig.ask; self.mid = orig.mid
                                self.iv = orig.iv; self.delta = delta; self.gamma = gamma
                                self.theta = theta; self.vega = vega

                        _record_combo_quote(lm, _QWithBS(q, _gd2, _gg2, _gt2, _gv2))
                    except Exception:
                        pass

        # Portfolio delta: 1 delta per share (long stock) + option deltas × qty × 100
        combo_net_delta = shares  # stock contribution
        combo_net_theta = 0.0
        combo_net_gamma = 0.0
        combo_net_vega  = 0.0
        for gd in opt_greeks:
            lm_action = next((l.get("action", "").upper() for j, l in enumerate(opt_legs) if j == gd["leg"]), "BUY")
            lm_qty = next((l.get("qty", 1) for j, l in enumerate(opt_legs) if j == gd["leg"]), 1)
            sign = 1 if "BUY" in lm_action else -1
            mult = sign * lm_qty * 100
            if gd.get("delta") is not None:
                combo_net_delta += gd["delta"] * mult
            if gd.get("theta") is not None:
                combo_net_theta += gd["theta"] * mult
            if gd.get("gamma") is not None:
                combo_net_gamma += gd["gamma"] * mult
            if gd.get("vega") is not None:
                combo_net_vega  += gd["vega"] * mult

        # opt_entry_cost sign convention: negative = cash paid (cost), positive = credit received
        # opt_current_net sign convention: positive = value of long opts, negative = value owed on shorts
        # total capital deployed = stock cost + abs(options cost) - credit received
        total_entry_cost = stock_cost - opt_entry_cost  # always positive for normal positions
        total_current_value = stock_value + opt_current_net
        options_pnl = opt_current_net + opt_entry_cost  # gain/loss on options alone
        unrealized_pnl = stock_pnl + options_pnl
        pnl_pct = round((unrealized_pnl / total_entry_cost) * 100, 2) if total_entry_cost > 0 else 0.0

        if strategy.entry_date:
            _ed2 = strategy.entry_date if strategy.entry_date.tzinfo else strategy.entry_date.replace(tzinfo=dt.timezone.utc)
            days_held = (dt.datetime.now(dt.timezone.utc) - _ed2).days
        else:
            days_held = 0

        # --- Per-leg close/hold/roll advice for the option overlay ---
        # No full-chain RND here (combo path fetches only the legs), so P(ITM)
        # uses the lognormal fallback from each leg's IV, then |delta|.
        combo_leg_advice = []
        combo_opt_legs = []
        combo_min_dte = 0
        for lm in combo_leg_meta:
            gd = next((g for g in opt_greeks if g["leg"] == lm["i"]), None)
            cq = next((v for v in opt_quotes if v["leg"] == lm["i"]), None)
            strike = float(lm["strike"]) if lm["strike"] else 0.0
            leg_dte = dte_from_expiry(lm["expiration"]) if lm["expiration"] else 0
            combo_min_dte = max(combo_min_dte, leg_dte)
            sign = 1 if "BUY" in lm["action"] else -1
            p_itm = _leg_p_itm(lm["right"], strike, lm["expiration"], leg_dte,
                               gd.get("iv") if gd else None, gd.get("delta") if gd else None, {})
            entry_prem = opt_legs[lm["i"]].get("premium") if lm["i"] < len(opt_legs) else None
            advice = classify_leg_action(
                sign=sign, right=lm["right"], p_itm=p_itm,
                entry_prem=float(entry_prem) if entry_prem is not None else None,
                current_mid=cq["mid"] if cq else None, dte=leg_dte,
            )
            combo_leg_advice.append({
                "leg": lm["i"], "strike": strike, "type": lm["type"], "right": lm["right"],
                "action": advice["action"], "reason": advice["reason"],
                "p_itm_pct": advice["p_itm_pct"], "captured_pct": advice["captured_pct"],
                "prob_source": ("lognormal" if (gd and gd.get("iv") and strike > 0) else "delta"),
            })
            if strike > 0:
                combo_opt_legs.append({
                    "strike": strike, "right": lm["right"], "sign": sign, "qty": lm["qty"],
                    "iv": (gd.get("iv") / 100.0) if (gd and gd.get("iv")) else None,
                    "dte_years": leg_dte / 365.0,
                })

        # Whole-position payoff: the stock leg folded in with the option overlay
        # (e.g. covered call caps upside at the short strike, collar caps both sides).
        _combo_ivs = [l["iv"] for l in combo_opt_legs if l.get("iv")]
        _combo_avg_iv = sum(_combo_ivs) / len(_combo_ivs) if _combo_ivs else None
        combo_pay = build_payoff(
            underlying_price, combo_opt_legs,
            {"shares": shares, "avg_cost": avg_cost, "mult": 1.0} if shares > 0 else None,
            opt_entry_cost,
            avg_iv=_combo_avg_iv, dte_days=combo_min_dte or None,
        )
        # Options overlay ALONE (stock stripped out) — gives the Option-leg section
        # its own cost basis / PoP / EV / max gain-loss, identical to a standalone
        # options trade, for UI consistency.
        opt_only_pay = build_payoff(
            underlying_price, combo_opt_legs, None, opt_entry_cost,
            avg_iv=_combo_avg_iv, dte_days=combo_min_dte or None,
        )

        # Lifecycle metrics: Trader greeks fold the stock leg into delta; PM ratios
        # judge the whole stock+option structure.
        _combo_life_legs = [l for l in combo_opt_legs if l.get("iv")]
        combo_trader = higher_order_greeks(_combo_life_legs, underlying_price, stock_shares=shares) if _combo_life_legs else {}
        combo_pm = pm_ratios(combo_pay["scenarios"], underlying_price, _combo_avg_iv or 0,
                             combo_min_dte, abs(total_entry_cost) or 1, combo_pay["max_loss"]) if combo_pay["scenarios"] else {}

        # Signal from the whole-structure quant (PoP / distance to max profit & loss),
        # so the Quant Advisor reasons over stock + options together.
        combo_pop = combo_pay.get("pop")
        combo_signal = "HOLD"
        if combo_pop is not None and combo_pop < 30:
            combo_signal = "CLOSE"
        elif combo_pay["max_profit"] and unrealized_pnl >= combo_pay["max_profit"] * 0.8:
            combo_signal = "CLOSE"
        elif combo_pop is not None and combo_pop >= 70 and (combo_pay.get("expected_value") or 0) > 0:
            combo_signal = "STRONG_HOLD"
        combo_rec = summarize_trade_actions(
            leg_actions=combo_leg_advice, hold_signal=combo_signal,
            pop=combo_pop, expected_value=combo_pay.get("expected_value"), unrealized_pnl=unrealized_pnl,
            max_profit=combo_pay["max_profit"], max_loss=combo_pay["max_loss"],
            dte=combo_min_dte, breakevens=combo_pay["breakevens"] or ([round(avg_cost, 2)] if avg_cost > 0 else None),
            underlying_price=underlying_price, has_stock=True, stock_pnl=stock_pnl,
        )
        # LIFECYCLE recommendation is scoped to the OPTION OVERLAY, not the stock —
        # for a covered call / collar / hedge / managed structure the user holds the
        # stock for its own reason; the desk manages the OPTIONS (roll/close the call,
        # roll the hedge). So captured %, exit signal and the quant score all read the
        # options-only payoff (opt_only_pay) + options P&L, never the equity core.
        _opt_max_profit = opt_only_pay.get("max_profit")
        _opt_captured = (round(options_pnl / _opt_max_profit * 100, 1)
                         if (_opt_max_profit and _opt_max_profit > 0) else None)
        _combo_exit = exit_recommendation(
            hold_signal=combo_signal, pop=opt_only_pay.get("pop", combo_pop), unrealized_pnl=options_pnl,
            max_profit=_opt_max_profit, max_loss=opt_only_pay.get("max_loss"),
            dte=combo_min_dte, theta_per_day=combo_net_theta,
        )
        # MANAGEMENT read on the OPTION OVERLAY — anchored on the overlay's PoP
        # (probability the short options keep their edge), then take-profit + time.
        # The whole-structure 5-lens is kept as reference only, not the anchor.
        _combo_quant_exit = None
        if combo_pm:
            _combo_aq = algorithmic_quant(
                pm={**combo_pm, "pop": combo_pop}, cvar95=None, capital=abs(total_entry_cost) or 1,
                max_loss=opt_only_pay.get("max_loss"), max_profit=_opt_max_profit,
                kelly=opt_only_pay.get("kelly_fraction"), dte_days=combo_min_dte,
            )
            _combo_quant_exit = management_exit(
                pop_pct=opt_only_pay.get("pop", combo_pop), captured_pct=_opt_captured,
                dte_days=combo_min_dte, unrealized_pnl=options_pnl, max_profit=_opt_max_profit,
                max_loss=opt_only_pay.get("max_loss"),
                iv_pct=(_combo_avg_iv * 100) if _combo_avg_iv else None, theta_per_day=combo_net_theta,
                quality_subscores=_combo_aq["subscores"], quality_score=_combo_aq["score"],
            )

        return {
            "strategy_id": strategy_id,
            "ticker": strategy.ticker,
            "quote_source": quote_source,
            "underlying_price": round(underlying_price, 2),
            "entry_cost": round(total_entry_cost, 2),
            "current_value": round(total_current_value, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "pnl_pct": pnl_pct,
            "days_held": days_held,
            "current_quotes": opt_quotes,
            "stock_pnl": round(stock_pnl, 2),
            "options_pnl": round(options_pnl, 2),
            "stock_value": round(stock_value, 2),
            "stock_cost": round(stock_cost, 2),
            "greeks": opt_greeks,
            "net_greeks": {
                "delta": round(combo_net_delta, 4),
                "gamma": round(combo_net_gamma, 4),
                "theta": round(combo_net_theta, 4),
                "vega": round(combo_net_vega, 4),
            },
            "margin_required": 0.0,
            "total_capital": round(total_entry_cost, 2),
            "scenarios": combo_pay["scenarios"],
            "theta_projection": [],
            "max_profit": combo_pay["max_profit"],
            "max_loss": combo_pay["max_loss"] if combo_pay["max_loss"] is not None else (round(-stock_cost, 2) if stock_cost > 0 else None),
            "max_profit_price": combo_pay["max_profit_price"],
            "max_loss_price": combo_pay["max_loss_price"],
            "unbounded_profit": combo_pay["unbounded_profit"],
            "unbounded_loss": combo_pay["unbounded_loss"],
            "breakevens": combo_pay["breakevens"] or ([round(avg_cost, 2)] if avg_cost > 0 else []),
            "expiration_date": None,
            "leg_analysis": combo_leg_advice,
            "lifecycle": {"trader": combo_trader, "pm": combo_pm,
                          "avg_iv_pct": round((_combo_avg_iv or 0) * 100, 1)},
            "avg_iv": round(_combo_avg_iv or 0, 4),
            # The option overlay's own metrics, so the UI's Option-leg section is
            # byte-for-byte the same grid a standalone options trade renders.
            "options_breakdown": {
                "cost_basis": round(abs(opt_entry_cost), 2),
                "current_value": round(opt_current_net, 2),
                "options_pnl": round(options_pnl, 2),
                "net_delta": round(combo_net_delta - shares, 4),   # options only (strip stock's +1/share)
                "net_theta": round(combo_net_theta, 4),
                "net_vega": round(combo_net_vega, 4),
                "net_gamma": round(combo_net_gamma, 4),
                "entry_cost": round(opt_entry_cost, 2),
                "pop": opt_only_pay.get("pop"),
                "expected_value": opt_only_pay.get("expected_value"),
                "kelly_fraction": opt_only_pay.get("kelly_fraction"),
                "max_profit": opt_only_pay.get("max_profit"),
                "max_loss": opt_only_pay.get("max_loss"),
                "max_profit_price": opt_only_pay.get("max_profit_price"),
                "max_loss_price": opt_only_pay.get("max_loss_price"),
                "unbounded_profit": opt_only_pay.get("unbounded_profit", False),
                "unbounded_loss": opt_only_pay.get("unbounded_loss", False),
                "days_held": days_held,
            },
            "analysis": {
                "annualized_return_to_expiry": None,
                "probability_of_profit": combo_pop,
                "pop_method": "lognormal" if combo_pop is not None else None,
                "expected_value": combo_pay.get("expected_value"),
                "risk_reward_ratio": (round(abs(combo_pay["max_profit"] / combo_pay["max_loss"]), 2)
                                      if combo_pay["max_profit"] and combo_pay["max_loss"] else None),
                "kelly_fraction": combo_pay.get("kelly_fraction"),
                "theta_burn_rate_day": round(combo_net_theta, 2),
                "theta_burn_rate_pct": 0.0,
                "days_to_theta_breakeven": None,
                "hold_vs_close": combo_signal,
                "hold_vs_close_reasons": combo_rec["reasons"] or ["Combo position — stock core with option overlay"],
                "dte_remaining": combo_min_dte,
                "recommendation": combo_rec,
                "exit_signal": _combo_exit["signal"],
                "exit_reasons": _combo_exit["reasons"],
                "captured_pct": _combo_exit["captured_pct"],
                "quant_exit": _combo_quant_exit,
                "exit_scope": "options_overlay",   # recommendation manages the options, stock held separately
            },
        }

    # --- Fetch underlying price (options strategies) ---
    underlying_price = 0.0
    try:
        uq = await provider.get_underlying_price(strategy.ticker)
        underlying_price = uq.price
    except Exception:
        pass

    # --- Resolve strategy-level expiration fallback ---
    # Legs often don't store expiration per-leg; it's in result_snapshot or parameters
    snapshot = json.loads(strategy.result_snapshot) if strategy.result_snapshot else {}
    # params already parsed above (before the stock early-return)
    strategy_expiration = (
        snapshot.get("expirationDate")  # DDB stores it here
        or (snapshot.get("spread", {}) or {}).get("expiration")  # Box stores it here
        or params.get("expiration")
        or None
    )

    # --- Fetch current quotes ---
    current_values = []
    greeks_data = []

    # Build leg metadata
    leg_meta = []
    for i, leg in enumerate(legs):
        strike = leg.get("strike")
        opt_type = leg.get("type", "").upper()
        expiration = leg.get("expiration") or leg.get("exp") or leg.get("expiry") or strategy_expiration
        right = "C" if "CALL" in opt_type or opt_type == "C" else "P"
        qty = leg.get("qty", 1)
        action = leg.get("action", "").upper()
        sign = 1 if "BUY" in action else -1
        leg_meta.append({
            "i": i, "strike": strike, "type": opt_type, "expiration": expiration,
            "right": right, "qty": qty, "action": action, "sign": sign, "_leg": leg,
        })

    # Recompute the entry cost from the legs themselves so cost basis and P&L are
    # internally consistent and immune to a mis-stored entry_net_debit (e.g. a
    # short leg whose credit was recorded with the wrong sign). Same convention as
    # the stored value — BUY negative (cash paid), SELL positive (credit) — so all
    # downstream formulas (unrealized_pnl, scenarios, total_capital) are unchanged.
    _derived_debit = option_legs_net_debit(leg_meta, entry_prices_list)
    if _derived_debit is not None:
        if entry_cost and abs(_derived_debit - entry_cost) > 0.01:
            logger.warning(
                f"entry_net_debit mismatch for strategy {strategy_id}: "
                f"stored={entry_cost} derived-from-legs={_derived_debit} — using derived."
            )
        entry_cost = _derived_debit

    # Cache by expiration to avoid re-fetching the same chain; hoisted so the
    # RND calibration below can reuse the full chains (IBKR path leaves it empty).
    chain_cache: dict = {}

    # IBKR batch path
    if quote_source == "ibkr" and hasattr(provider, "get_multiple_option_quotes"):
        req_dicts = []
        valid_indices = []
        for lm in leg_meta:
            if lm["strike"] and lm["expiration"]:
                req_dicts.append({
                    "symbol": strategy.ticker, "expiration": lm["expiration"],
                    "strike": float(lm["strike"]), "right": lm["right"],
                })
                valid_indices.append(lm["i"])
            else:
                current_values.append({"leg": lm["i"], "error": "missing strike/expiration"})

        if req_dicts:
            try:
                quotes = await provider.get_multiple_option_quotes(req_dicts)
                # IBKR warmup: first call subscribes but may return None.
                # Retry failed legs after a short delay.
                failed_indices = [j for j, q in enumerate(quotes) if q is None]
                if failed_indices:
                    await asyncio.sleep(0.8)
                    retry_reqs = [req_dicts[j] for j in failed_indices]
                    retry_quotes = await provider.get_multiple_option_quotes(retry_reqs)
                    for ri, j in enumerate(failed_indices):
                        if retry_quotes[ri] is not None:
                            quotes[j] = retry_quotes[ri]
                for j, q in enumerate(quotes):
                    idx = valid_indices[j]
                    lm = leg_meta[idx]
                    if q is None:
                        current_values.append({"leg": idx, "error": "no quote"})
                        continue
                    current_values.append({
                        "leg": idx, "strike": lm["strike"], "type": lm["type"],
                        "bid": q.bid, "ask": q.ask, "mid": q.mid, "oi": q.oi, "volume": q.volume,
                    })
                    greeks_data.append({
                        "leg": idx, "strike": lm["strike"], "type": lm["type"],
                        "iv": round(q.iv * 100, 2) if q.iv else None,
                        "delta": round(q.delta, 4) if q.delta else None,
                        "gamma": round(q.gamma, 4) if q.gamma else None,
                        "theta": round(q.theta, 4) if q.theta else None,
                        "vega": round(q.vega, 4) if q.vega else None,
                    })
            except Exception as e:
                for idx in valid_indices:
                    current_values.append({"leg": idx, "error": str(e)})
    else:
        # Generic provider path — uses OptionChain.quotes (list[OptionQuote])
        for lm in leg_meta:
            try:
                if not lm["strike"] or not lm["expiration"]:
                    current_values.append({"leg": lm["i"], "error": "missing strike/expiration"})
                    continue
                exp_key = lm["expiration"]
                if exp_key not in chain_cache:
                    chain_cache[exp_key] = await provider.get_option_chain(strategy.ticker, exp_key)
                chain = chain_cache[exp_key]
                # Filter quotes by right (C/P) and closest strike (tolerance 0.50 for rounding differences)
                target_strike = float(lm["strike"])
                same_right = [q for q in chain.quotes if q.right == lm["right"]]
                matching = sorted(same_right, key=lambda q: abs(q.strike - target_strike))
                if not matching or abs(matching[0].strike - target_strike) >= 0.50:
                    logger.warning(f"No quote match for leg {lm['i']}: {lm['right']} {target_strike} in {exp_key}")
                    current_values.append({"leg": lm["i"], "error": "no quote"})
                    continue
                q = matching[0]
                current_values.append({
                    "leg": lm["i"], "strike": lm["strike"], "type": lm["type"],
                    "bid": q.bid, "ask": q.ask, "mid": q.mid, "oi": q.oi, "volume": q.volume,
                })
                # yfinance doesn't return Greeks — compute from BS when IV is available
                _giv = q.iv
                _gd, _gg, _gt, _gv = q.delta, q.gamma, q.theta, q.vega
                if _giv and underlying_price > 0 and any(x is None for x in (_gd, _gg, _gt, _gv)):
                    try:
                        _exp_dt = dt.datetime.strptime(lm["expiration"], "%Y-%m-%d").date()
                        _T = max((_exp_dt - dt.datetime.now(dt.timezone.utc).date()).days / 365.0, 0.001)
                        _K, _S, _r = float(lm["strike"]), underlying_price, 0.05
                        _otype = "call" if lm["right"] == "C" else "put"
                        if _gd is None:
                            _gd = bs_delta(_S, _K, _T, _r, _giv, _otype)
                        if _gg is None:
                            _gg = bs_gamma(_S, _K, _T, _r, _giv)
                        if _gt is None:
                            _gt = bs_theta(_S, _K, _T, _r, _giv, _otype)
                        if _gv is None:
                            _gv = bs_vega(_S, _K, _T, _r, _giv)
                    except Exception:
                        pass
                greeks_data.append({
                    "leg": lm["i"], "strike": lm["strike"], "type": lm["type"],
                    "iv": round(_giv * 100, 2) if _giv else None,
                    "delta": round(_gd, 4) if _gd is not None else None,
                    "gamma": round(_gg, 4) if _gg is not None else None,
                    "theta": round(_gt, 4) if _gt is not None else None,
                    "vega": round(_gv, 4) if _gv is not None else None,
                })
            except Exception as e:
                current_values.append({"leg": lm["i"], "error": str(e)})

    # --- Calculate current portfolio value ---
    current_net = 0.0
    for lm in leg_meta:
        cv = next((v for v in current_values if v.get("leg") == lm["i"] and "mid" in v), None)
        if cv:
            if "BUY" in lm["action"]:
                current_net += cv["mid"] * lm["qty"] * 100
            else:
                current_net -= cv["mid"] * lm["qty"] * 100

    unrealized_pnl = current_net + entry_cost
    if strategy.entry_date:
        _ed = strategy.entry_date if strategy.entry_date.tzinfo else strategy.entry_date.replace(tzinfo=dt.timezone.utc)
        days_held = (dt.datetime.now(dt.timezone.utc) - _ed).days
    else:
        days_held = 0

    # --- Net portfolio Greeks ---
    net_greeks = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    for gd in greeks_data:
        lm = next((m for m in leg_meta if m["i"] == gd["leg"]), None)
        if not lm:
            continue
        mult = lm["sign"] * lm["qty"] * 100
        if gd.get("delta") is not None:
            net_greeks["delta"] += gd["delta"] * mult
        if gd.get("gamma") is not None:
            net_greeks["gamma"] += gd["gamma"] * mult
        if gd.get("theta") is not None:
            net_greeks["theta"] += gd["theta"] * mult
        if gd.get("vega") is not None:
            net_greeks["vega"] += gd["vega"] * mult
    net_greeks = {k: round(v, 4) for k, v in net_greeks.items()}

    # --- Market-implied risk-neutral density (SVI/RND) per expiration ---
    # Breeden-Litzenberger density off a calibrated SVI smile — the QuantLib-grade
    # view of where the market prices the underlying at expiry. Reused for per-leg
    # P(ITM) at each strike and for the structure's probability of profit. Best-effort:
    # needs a full chain (generic/yfinance path), so IBKR falls back to lognormal.
    rnd_by_exp: dict = {}
    if underlying_price > 0 and chain_cache:
        try:
            from ..services.quant_service import calibrate_svi, risk_neutral_density
            for exp_key, chain in chain_cache.items():
                if not chain or not getattr(chain, "quotes", None):
                    continue
                by_strike: dict = {}
                for q in chain.quotes:
                    by_strike.setdefault(q.strike, {})[q.right] = q
                strikes, ivs, cmids, pmids = [], [], [], []
                for k in sorted(by_strike):
                    cq = by_strike[k].get("C"); pq = by_strike[k].get("P")
                    # Put IV below spot, call IV above — the liquid, skew-bearing wing.
                    iv = (pq.iv if (k <= underlying_price and pq and pq.iv) else
                          cq.iv if (k > underlying_price and cq and cq.iv) else
                          (cq.iv if (cq and cq.iv) else (pq.iv if (pq and pq.iv) else None)))
                    if not iv or iv <= 0:
                        continue
                    strikes.append(float(k)); ivs.append(float(iv))
                    cmids.append(cq.mid if cq else None); pmids.append(pq.mid if pq else None)
                if len(strikes) < 6:
                    continue
                smile = calibrate_svi(strikes, ivs, underlying_price, dte_from_expiry(exp_key),
                                      call_mids=cmids, put_mids=pmids)
                if smile:
                    rnd_by_exp[exp_key] = risk_neutral_density(smile)
        except Exception as e:
            logger.warning(f"RND calibration failed for {strategy.ticker}: {e}")

    # --- Per-leg close/hold/roll advice + P(ITM) at each strike ---
    leg_advice = []
    for lm in leg_meta:
        cv = next((v for v in current_values if v.get("leg") == lm["i"] and "mid" in v), None)
        gd = next((g for g in greeks_data if g["leg"] == lm["i"]), None)
        strike = float(lm["strike"]) if lm["strike"] else 0.0
        leg_dte = dte_from_expiry(lm["expiration"]) if lm["expiration"] else 0
        leg_iv = gd.get("iv") if gd else None
        leg_delta = gd.get("delta") if gd else None
        p_itm = _leg_p_itm(lm["right"], strike, lm["expiration"], leg_dte, leg_iv, leg_delta, rnd_by_exp)
        entry_prem = legs[lm["i"]].get("premium") if lm["i"] < len(legs) else None
        current_mid = cv["mid"] if cv else None
        advice = classify_leg_action(
            sign=lm["sign"], right=lm["right"], p_itm=p_itm,
            entry_prem=float(entry_prem) if entry_prem is not None else None,
            current_mid=current_mid, dte=leg_dte,
        )
        leg_advice.append({
            "leg": lm["i"], "strike": strike, "type": lm["type"], "right": lm["right"],
            "action": advice["action"], "reason": advice["reason"],
            "p_itm_pct": advice["p_itm_pct"], "captured_pct": advice["captured_pct"],
            "prob_source": ("rnd" if rnd_by_exp.get(lm["expiration"]) is not None
                            else "lognormal" if (leg_iv and strike > 0) else "delta"),
        })

    # --- Build per-leg data for scenario analysis ---
    r = 0.05  # risk-free rate assumption
    now = dt.datetime.now(dt.timezone.utc)
    leg_analysis = []
    for lm in leg_meta:
        gd = next((g for g in greeks_data if g["leg"] == lm["i"]), None)
        iv_decimal = (gd["iv"] / 100.0) if gd and gd.get("iv") else 0.30
        exp_str = lm["expiration"] or ""
        try:
            exp_date = dt.datetime.strptime(exp_str, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
        except Exception:
            exp_date = now + dt.timedelta(days=30)
        # CALENDAR-day count (date−date), identical to the greeks_data path above — a
        # datetime−datetime diff would truncate the intraday hours and read 1 day short,
        # which is exactly what made the desk theta (_trader) disagree with the leg table.
        dte_days = max((exp_date.date() - now.date()).days, 0)
        dte_years = dte_days / 365.0
        opt_type_bs = "call" if lm["right"] == "C" else "put"
        cv = next((v for v in current_values if v.get("leg") == lm["i"] and "mid" in v), None)
        leg_analysis.append({
            "strike": float(lm["strike"]) if lm["strike"] else 0,
            "iv": iv_decimal, "dte_years": dte_years, "dte_days": dte_days,
            "opt_type": opt_type_bs, "sign": lm["sign"], "qty": lm["qty"],
            "mid": cv["mid"] if cv else 0,
        })

    # --- Margin calculation (IBKR-style spread margin) ---
    # Margin math lives in the module-level calc_reg_t_margin (single source, also used by
    # the closed-trade BPR). Kept as a local alias so the call sites below read unchanged.
    def _calc_margin(leg_list, U: float = 0.0, mode: str = "reg_t"):
        return calc_reg_t_margin(leg_list, U, mode)

    margin_required = _calc_margin(legs, underlying_price, margin_mode)
    # For debit spreads: capital = entry cost + margin
    # For credit spreads: capital at risk = margin (or abs(entry_cost) if larger)
    if entry_cost > 0:
        total_capital = entry_cost + margin_required
    else:
        total_capital = max(abs(entry_cost), margin_required)

    # --- Scenario analysis (P&L vs underlying price) with margin impact ---
    scenarios = []
    if underlying_price > 0 and leg_analysis:
        # Price grid that spans BOTH the ±25% window the user asked for AND every
        # strike (plus a margin), so the flat max-profit / max-loss plateaus of the
        # whole trade are actually visible on the chart — not clipped off-screen.
        _strikes_all = [la["strike"] for la in leg_analysis if la["strike"] > 0]
        _lo = min([underlying_price * 0.70] + [s * 0.85 for s in _strikes_all])
        _hi = max([underlying_price * 1.30] + [s * 1.15 for s in _strikes_all])
        _n_pts = 49
        sim_prices = [_lo + (_hi - _lo) * k / (_n_pts - 1) for k in range(_n_pts)]
        for sim_price in sim_prices:
            pct = sim_price / underlying_price - 1.0 if underlying_price > 0 else 0.0
            # Current value (BS-priced with remaining DTE)
            position_value = 0.0
            for la in leg_analysis:
                if la["strike"] <= 0:
                    continue
                val = bs_price(sim_price, la["strike"], la["dte_years"], r, la["iv"], la["opt_type"])
                position_value += la["sign"] * la["qty"] * val * 100
            # At-expiration value (intrinsic only)
            exp_payout = 0.0
            for la in leg_analysis:
                if la["strike"] <= 0:
                    continue
                if la["opt_type"] == "call":
                    intrinsic = max(0.0, sim_price - la["strike"])
                else:
                    intrinsic = max(0.0, la["strike"] - sim_price)
                exp_payout += la["sign"] * la["qty"] * intrinsic * 100
            pnl_now = position_value + entry_cost
            pnl_exp = exp_payout + entry_cost
            roi_now = (pnl_now / abs(total_capital) * 100) if total_capital != 0 else 0
            roi_exp = (pnl_exp / abs(total_capital) * 100) if total_capital != 0 else 0
            # Margin impact: how much of margin is at risk at this price
            margin_at_risk = min(margin_required, max(0, -pnl_exp)) if margin_required > 0 else 0
            scenarios.append({
                "price_change_pct": round(pct * 100, 1),
                "price": round(sim_price, 2),
                "pnl_now": round(pnl_now, 2),
                "pnl_at_expiry": round(pnl_exp, 2),
                "roi_now": round(roi_now, 2),
                "roi_at_expiry": round(roi_exp, 2),
                "margin_at_risk": round(margin_at_risk, 2),
            })

    # --- Theta decay projection (every day till expiry) ---
    theta_projection = []
    if underlying_price > 0 and leg_analysis:
        min_dte = min((la["dte_days"] for la in leg_analysis if la["dte_days"] > 0), default=60)
        # Build day points: daily for first 14 days, then weekly, then at expiry
        day_points = list(range(0, min(15, min_dte + 1)))
        for d in range(14, min_dte + 1, 7):
            if d not in day_points:
                day_points.append(d)
        if min_dte not in day_points:
            day_points.append(min_dte)
        day_points = sorted(set(day_points))
        for d in day_points:
            position_value = 0.0
            for la in leg_analysis:
                if la["strike"] <= 0:
                    continue
                future_dte = max(la["dte_years"] - d / 365.0, 0)
                val = bs_price(underlying_price, la["strike"], future_dte, r, la["iv"], la["opt_type"])
                position_value += la["sign"] * la["qty"] * val * 100
            pnl = position_value + entry_cost
            theta_projection.append({
                "days_from_now": d,
                "dte_remaining": max(0, min_dte - d),
                "value": round(position_value, 2),
                "pnl": round(pnl, 2),
            })

    # --- Max profit / max loss / breakevens (at expiration) ---
    max_profit = None
    max_loss = None
    max_profit_price = None
    max_loss_price = None
    unbounded_profit = False
    unbounded_loss = False
    breakevens = []
    exp_pnls = []
    _pl_legs = []   # option legs for exact extremes/breakevens (empty for box spreads)

    # BOX spreads: guaranteed payoff is structural (spread width × contracts), no live price needed.
    # Compute this up-front so it's always available regardless of quote source or IBKR warmup.
    if strategy.strategy_type == "box_spread":
        box_strikes = sorted(set(
            float(l.get("strike", 0)) for l in legs if l.get("strike")
        ))
        if len(box_strikes) >= 2:
            spread_width = box_strikes[-1] - box_strikes[0]
            box_qty = int(legs[0].get("qty", 1)) if legs else 1
            guaranteed_payoff = spread_width * box_qty * 100
            # max_profit = what you receive at expiry minus what you paid
            max_profit = round(guaranteed_payoff - abs(entry_cost), 2)
            max_loss = round(-abs(entry_cost), 2) if max_profit <= 0 else None
    else:
        # Exact structural extremes for the WHOLE trade from the payoff breakpoints
        # (strikes + price 0), including the price where each occurs and whether a
        # tail is unbounded. This is the max gain/loss regardless of the chart window.
        _pl_legs = [
            {"strike": lm["strike"], "right": lm["right"], "sign": lm["sign"], "qty": lm["qty"]}
            for lm in leg_meta if lm.get("strike")
        ]
        _ext = structure_payoff_extremes(_pl_legs, entry_cost) if _pl_legs else None
        if _ext:
            max_profit = _ext["max_profit"]
            max_loss = _ext["max_loss"]
            max_profit_price = _ext["max_profit_price"]
            max_loss_price = _ext["max_loss_price"]
            unbounded_profit = _ext["unbounded_profit"]
            unbounded_loss = _ext["unbounded_loss"]

    if underlying_price > 0 and leg_analysis:
        scan_low = underlying_price * 0.5
        scan_high = underlying_price * 1.5
        step = (scan_high - scan_low) / 200
        for j in range(201):
            p = scan_low + j * step
            payout = 0.0
            for la in leg_analysis:
                if la["strike"] <= 0:
                    continue
                if la["opt_type"] == "call":
                    intrinsic = max(0.0, p - la["strike"])
                else:
                    intrinsic = max(0.0, la["strike"] - p)
                payout += la["sign"] * la["qty"] * intrinsic * 100
            pnl = payout + entry_cost
            exp_pnls.append((p, pnl))

        if exp_pnls:
            # Structural extremes above are exact; only fall back to the scan when we
            # have neither an exact value nor a genuine unbounded (unlimited) tail.
            if max_profit is None and not unbounded_profit:
                max_profit = round(max(pnl for _, pnl in exp_pnls), 2)
            if max_loss is None and not unbounded_loss:
                max_loss = round(min(pnl for _, pnl in exp_pnls), 2)

    # Breakevens — EXACT (piecewise-linear roots at the strike breakpoints), NOT a coarse
    # grid scan whose interpolation crosses a strike kink and mislocates one breakeven by
    # up to half a grid step (the asymmetric ~$0.26 error).
    if _pl_legs:
        breakevens = structure_breakevens(_pl_legs, entry_cost)

    # --- Expiration date for display ---
    min_exp_date = None
    for la in leg_analysis:
        exp_str = ""
        for lm in leg_meta:
            if lm["i"] == leg_analysis.index(la):
                exp_str = lm.get("expiration") or ""
                break
        if exp_str:
            min_exp_date = exp_str if not min_exp_date else min(min_exp_date, exp_str)

    # --- Institutional-level analysis ---
    min_dte_days = min((la["dte_days"] for la in leg_analysis if la["dte_days"] > 0), default=0)
    avg_iv = sum(la["iv"] for la in leg_analysis) / len(leg_analysis) if leg_analysis else 0.30

    # Probability of Profit (PoP) via log-normal distribution
    # --- PoP, expected value AND the PM ratios from ONE distribution ---
    # Use the market-implied RND when we could calibrate one (it prices the skew
    # a plain lognormal ignores); otherwise a lognormal at avg_iv. Deriving pop,
    # EV and the PM ratios (Omega/Sortino/Calmar) from the SAME weights is what
    # guarantees they can never contradict — no more "EV +$108 but Exp.Return −1.5%".
    pop = None
    pop_method = None
    expected_value = None
    _pm_metrics = {}
    _cvar95 = None
    _var95 = None
    if exp_pnls and underlying_price > 0 and min_dte_days > 0:
        _prices = [p for p, _ in exp_pnls]
        _pnls = [pl for _, pl in exp_pnls]
        _dom_rnd = rnd_by_exp[min(rnd_by_exp.keys(), key=lambda e: dte_from_expiry(e))] if rnd_by_exp else None
        _weights = None
        if _dom_rnd is not None:
            _cdf = [_dom_rnd.prob_below(p) for p in _prices]
            _weights = [max(0.0, _cdf[i + 1] - _cdf[i]) for i in range(len(_cdf) - 1)]
            pop_method = "rnd"
        elif avg_iv > 0:
            _T = min_dte_days / 365.0
            _ssig = avg_iv * math.sqrt(_T)
            _weights = []
            for i in range(len(_prices) - 1):
                _pm_price = (_prices[i] + _prices[i + 1]) / 2.0
                if _pm_price <= 0:
                    _weights.append(0.0)
                    continue
                _z = (math.log(_pm_price / underlying_price) - (r - 0.5 * avg_iv ** 2) * _T) / _ssig
                _weights.append(norm.pdf(_z) / (_pm_price * _ssig) * (_prices[i + 1] - _prices[i]))
            pop_method = "lognormal"
        if _weights and sum(_weights) > 0:
            _dm = payoff_distribution_metrics(_prices, _pnls, _weights, abs(total_capital), max_loss, min_dte_days)
            pop = _dm["pop"]
            expected_value = _dm["expected_value"]
            _pm_metrics = {k: _dm[k] for k in ("omega", "sortino", "calmar", "expected_return_pct", "downside_dev_pct")}
            # CVaR95 (expected shortfall) for the algorithmic tail term.
            _wa = _np.asarray(_weights, dtype=float)
            _pnl_mid = (_np.asarray(_pnls[:-1], dtype=float) + _np.asarray(_pnls[1:], dtype=float)) / 2.0
            _wn = _wa / _wa.sum() if _wa.sum() > 0 else _wa
            _var95, _cvar95 = _weighted_var_cvar(_pnl_mid, _wn)

    # Risk/Reward Ratio
    risk_reward = None
    if max_profit is not None and max_loss is not None and max_loss != 0:
        risk_reward = round(max_profit / abs(max_loss), 2)

    # Annualized Return to Expiry
    annualized_return = None
    if min_dte_days > 0 and total_capital > 0:
        # Use expected P&L at expiry at current price
        exp_pnl_at_current = None
        if exp_pnls:
            closest = min(exp_pnls, key=lambda x: abs(x[0] - underlying_price))
            exp_pnl_at_current = closest[1]
        if exp_pnl_at_current is not None:
            roi_to_exp = exp_pnl_at_current / abs(total_capital)
            try:
                ann = ((1 + roi_to_exp) ** (365.0 / min_dte_days)) - 1
                annualized_return = round(max(-9.99, min(9.99, ann)) * 100, 1)
            except (ValueError, OverflowError):
                annualized_return = None

    # Kelly Criterion
    kelly_fraction = None
    if pop is not None and max_profit is not None and max_loss is not None and max_loss != 0:
        p_win = pop / 100.0
        R = abs(max_profit / max_loss) if max_loss != 0 else 0
        if R > 0:
            kelly = (p_win * R - (1 - p_win)) / R
            kelly_fraction = round(max(0, min(1, kelly)), 3)

    # Theta Burn Rate
    theta_per_day = net_greeks.get("theta", 0)
    theta_pct = round(theta_per_day / abs(current_net) * 100, 3) if current_net != 0 else 0

    # Days to Theta Breakeven
    days_to_theta_be = None
    if unrealized_pnl > 0 and theta_per_day < 0:
        days_to_theta_be = round(abs(unrealized_pnl / theta_per_day), 1)

    # Hold vs Close Signal
    is_box_spread = strategy.strategy_type == "box_spread"

    if is_box_spread:
        # BOX spreads expire at a guaranteed value (width − cost) regardless of underlying.
        # Mark-to-market fluctuations are noise; closing early only makes sense if you can
        # roll to a meaningfully higher annualized rate.
        if max_profit is not None and max_profit > 0:
            hold_signal = "STRONG_HOLD"
            hold_reasons = [
                f"Guaranteed +${max_profit:.2f} at expiry ({min_dte_days}d remaining)",
                "BOX spread is risk-free at expiry — mark-to-market moves are irrelevant",
                "Close only to roll into a significantly higher annualized rate",
            ]
            if annualized_return is not None:
                hold_reasons.insert(0, f"On track for {annualized_return:.1f}% annualized")
        else:
            hold_signal = "HOLD"
            hold_reasons = ["BOX spread — hold to expiry for guaranteed outcome"]
    else:
        hold_signal = "HOLD"
        hold_reasons = []
        if pop is not None:
            if pop >= 70:
                hold_reasons.append(f"High probability of profit ({pop}%)")
            elif pop < 30:
                hold_reasons.append(f"Low probability of profit ({pop}%)")
                hold_signal = "CLOSE"
        if expected_value is not None:
            if expected_value > 0:
                hold_reasons.append(f"Positive expected value (${expected_value:.2f})")
            else:
                hold_reasons.append(f"Negative expected value (${expected_value:.2f})")
        if min_dte_days <= 3:
            hold_reasons.append(f"Very near expiry ({min_dte_days} DTE) — gamma risk elevated")
            if unrealized_pnl > 0 and max_profit is not None and max_profit > 0 and unrealized_pnl >= max_profit * 0.5:
                hold_signal = "CLOSE"
            elif unrealized_pnl < 0:
                hold_signal = "STRONG_CLOSE"
        if theta_per_day < 0 and days_to_theta_be is not None and days_to_theta_be < 5:
            hold_reasons.append(f"Theta will erode profit in ~{days_to_theta_be:.0f} days")
            hold_signal = "CLOSE"
        if max_profit is not None and max_profit > 0 and unrealized_pnl >= max_profit * 0.8:
            hold_reasons.append(f"Near max profit ({round(unrealized_pnl / max_profit * 100)}% captured)")
            hold_signal = "CLOSE"
        # Only fire "near max loss" when max_loss is a real negative loss (not a guaranteed profit like BOX)
        if max_loss is not None and max_loss < 0 and unrealized_pnl <= max_loss * 0.8:
            hold_reasons.append(f"Near max loss (${max_loss:.2f}) — consider cutting losses")
            hold_signal = "STRONG_CLOSE"
        # Upgrade to STRONG variants
        if hold_signal == "HOLD" and pop is not None and pop >= 70 and expected_value is not None and expected_value > 0:
            hold_signal = "STRONG_HOLD"
        if hold_signal == "CLOSE" and pop is not None and pop < 20:
            hold_signal = "STRONG_CLOSE"
        if not hold_reasons:
            hold_reasons.append("Neutral position — monitor")

    # --- Lifecycle metrics: Trader greeks + PM ratios ---
    # PM ratios come from the SAME distribution as pop/EV above (see _pm_metrics),
    # so the PM desk and the Quant Advisor can never disagree in sign.
    _life_legs = [
        {"strike": la["strike"], "right": "C" if la["opt_type"] == "call" else "P",
         "sign": la["sign"], "qty": la["qty"], "iv": la["iv"], "dte_years": la["dte_years"]}
        for la in leg_analysis if la.get("strike", 0) > 0
    ]
    _trader = higher_order_greeks(_life_legs, underlying_price) if _life_legs else {}
    lifecycle = {
        "trader": _trader, "pm": _pm_metrics, "avg_iv_pct": round(avg_iv * 100, 1),
        # Position-level Capital Risk (VaR/CVaR are positive-loss $) — the same tiles
        # the Income desk's "Capital Risk" section shows.
        "risk": {
            "var_95": _var95, "cvar_95": _cvar95,
            "max_profit": max_profit, "max_loss": max_loss,
            "capital": abs(total_capital) if total_capital else (margin_required or None),
        },
    }

    # Fold per-leg verdicts + structure metrics into one headline recommendation.
    recommendation = summarize_trade_actions(
        leg_actions=leg_advice,
        hold_signal=hold_signal,
        pop=pop,
        expected_value=expected_value,
        unrealized_pnl=unrealized_pnl,
        max_profit=max_profit,
        max_loss=max_loss,
        dte=min_dte_days,
        breakevens=breakevens,
        underlying_price=underlying_price,
        has_stock=_has_stock_params,
        stock_pnl=None,
    )

    _exit = exit_recommendation(
        hold_signal=hold_signal, pop=pop, unrealized_pnl=unrealized_pnl,
        max_profit=max_profit, max_loss=max_loss, dte=min_dte_days,
        theta_per_day=net_greeks.get("theta", 0.0),
    )
    # Tier-2 QUANT ALGORITHMIC exit — a MANAGEMENT read for a trade you already
    # hold: anchored on the position's probability of KEEPING its edge (PoP here;
    # the on-demand desk score refines with drift-adjusted keep prob), then the
    # take-profit + time/gamma overlay. The entry-flavoured 5-lens is computed only
    # as a reference readout — it no longer drives the hold/close signal.
    _quant_exit = None
    if _pm_metrics:
        _aq = algorithmic_quant(
            pm={**_pm_metrics, "pop": pop}, cvar95=_cvar95, capital=abs(total_capital),
            max_loss=max_loss, max_profit=max_profit, kelly=kelly_fraction, dte_days=min_dte_days,
        )
        _quant_exit = management_exit(
            pop_pct=pop, captured_pct=_exit["captured_pct"], dte_days=min_dte_days,
            unrealized_pnl=unrealized_pnl, max_profit=max_profit, max_loss=max_loss,
            iv_pct=(avg_iv * 100) if avg_iv else None, theta_per_day=net_greeks.get("theta", 0.0),
            quality_subscores=_aq["subscores"], quality_score=_aq["score"],
        )

    analysis = {
        "annualized_return_to_expiry": annualized_return,
        "probability_of_profit": pop,
        "pop_method": pop_method,
        "expected_value": expected_value,
        "risk_reward_ratio": risk_reward,
        "kelly_fraction": kelly_fraction,
        "theta_burn_rate_day": round(theta_per_day, 2),
        "theta_burn_rate_pct": theta_pct,
        "days_to_theta_breakeven": days_to_theta_be,
        "hold_vs_close": hold_signal,
        "hold_vs_close_reasons": hold_reasons,
        "dte_remaining": min_dte_days,
        "recommendation": recommendation,
        "exit_signal": _exit["signal"],
        "exit_reasons": _exit["reasons"],
        "captured_pct": _exit["captured_pct"],
        "quant_exit": _quant_exit,
        "exit_scope": "whole_trade",
    }

    return {
        "strategy_id": strategy_id,
        "ticker": strategy.ticker,
        "quote_source": quote_source,
        "underlying_price": round(underlying_price, 2),
        "entry_cost": entry_cost,
        "current_value": round(current_net, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "pnl_pct": round((unrealized_pnl / abs(total_capital)) * 100, 2) if total_capital != 0 else 0,
        "days_held": days_held,
        "current_quotes": current_values,
        "greeks": greeks_data,
        "net_greeks": net_greeks,
        "margin_required": margin_required,
        "total_capital": round(total_capital, 2),
        "scenarios": scenarios,
        "theta_projection": theta_projection,
        "max_profit": max_profit,
        "max_loss": max_loss,
        "max_profit_price": max_profit_price,
        "max_loss_price": max_loss_price,
        "unbounded_profit": unbounded_profit,
        "unbounded_loss": unbounded_loss,
        "breakevens": breakevens,
        "expiration_date": min_exp_date,
        "leg_analysis": leg_advice,
        "lifecycle": lifecycle,
        "avg_iv": round(avg_iv, 4),
        "analysis": analysis,
    }


# ── Risk desk — portfolio VaR/CVaR/stress over all derivative trades ─────

class PortfolioRiskPosition(BaseModel):
    ticker: str
    spot: float
    net_delta: float = 0.0
    net_gamma: float = 0.0
    net_vega: float = 0.0
    iv: float = 0.30            # annualized, decimal


class PortfolioRiskRequest(BaseModel):
    positions: list[PortfolioRiskPosition] = Field(default_factory=list)
    horizon_days: int = Field(default=1, ge=1, le=30)


@router.post("/portfolio-risk")
async def compute_portfolio_risk(
    body: PortfolioRiskRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Book-level VaR / CVaR / stress tests over the caller's derivative trades.

    The frontend already fetches per-trade live-pnl (which carries the Trader
    greeks + implied vol); it posts a compact position list here and we run the
    delta-gamma-vega Monte Carlo. Aggregated by underlying — the real risk unit."""
    from ..services.lifecycle_service import portfolio_risk as _prisk
    positions = [p.model_dump() for p in body.positions]
    return _prisk(positions, horizon_days=body.horizon_days)


class TradeAdvisorRequest(BaseModel):
    user_question: str = Field(default="", max_length=2000)
    pnl_snapshot: dict = Field(...)  # The full LivePnlResponse from frontend


@router.post("/{strategy_id}/trade-advisor")
async def trade_advisor(
    strategy_id: int,
    body: TradeAdvisorRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """LLM-powered trade advisor: sends all trade data, greeks, scenarios, and analysis
    to an LLM acting as an experienced quant advisor."""
    import datetime as dt

    result = await db.execute(
        select(SavedStrategy).where(
            SavedStrategy.id == strategy_id,
            SavedStrategy.user_id == user.id,
        )
    )
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Trade not found")

    api_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="OpenAI API key not configured. Please add it in Settings.")

    # Build comprehensive trade context
    legs = json.loads(strategy.legs_data)
    entry_prices = json.loads(strategy.entry_prices) if strategy.entry_prices else []
    pnl = body.pnl_snapshot

    # Format legs with entry prices
    legs_detail = []
    for i, leg in enumerate(legs):
        ep = entry_prices[i] if i < len(entry_prices) else {}
        current_quote = None
        greek = None
        if pnl.get("current_quotes"):
            current_quote = next((q for q in pnl["current_quotes"] if q.get("leg") == i), None)
        if pnl.get("greeks"):
            greek = pnl["greeks"][i] if i < len(pnl["greeks"]) else None

        leg_str = (
            f"  Leg {i+1}: {leg.get('action','?')} {leg.get('qty',1)}x "
            f"{leg.get('type','?')} @ ${leg.get('strike','?')} "
            f"exp {leg.get('expiration', leg.get('exp', '?'))}\n"
            f"    Entry Price: ${ep.get('price', leg.get('mid', leg.get('midPrice', '?')))}"
        )
        if current_quote:
            leg_str += (
                f"\n    Current: Bid=${current_quote.get('bid','?')} "
                f"Ask=${current_quote.get('ask','?')} Mid=${current_quote.get('mid','?')}"
            )
        if greek:
            leg_str += (
                f"\n    Greeks: IV={greek.get('iv','?')}% "
                f"Delta={greek.get('delta','?')} Gamma={greek.get('gamma','?')} "
                f"Theta={greek.get('theta','?')} Vega={greek.get('vega','?')}"
            )
        legs_detail.append(leg_str)

    legs_text = "\n".join(legs_detail)

    # Net greeks
    ng = pnl.get("net_greeks", {})
    net_greeks_text = (
        f"Net Delta: {ng.get('delta', '?')}\n"
        f"Net Gamma: {ng.get('gamma', '?')}\n"
        f"Net Theta: {ng.get('theta', '?')}/day\n"
        f"Net Vega: {ng.get('vega', '?')}"
    )

    # Scenarios
    scenarios = pnl.get("scenarios", [])
    scenario_lines = []
    for s in scenarios:
        scenario_lines.append(
            f"  {s.get('price_change_pct',0):+.0f}%: "
            f"Price=${s.get('price','?')}, "
            f"P&L Now=${s.get('pnl_now','?')}, "
            f"P&L@Exp=${s.get('pnl_at_expiry','?')}, "
            f"ROI Now={s.get('roi_now','?')}%, "
            f"ROI@Exp={s.get('roi_at_expiry','?')}%"
        )
    scenarios_text = "\n".join(scenario_lines) if scenario_lines else "Not available"

    # Analysis
    analysis = pnl.get("analysis", {})
    analysis_text = (
        f"Signal: {analysis.get('hold_vs_close', '?')}\n"
        f"Reasons: {', '.join(analysis.get('hold_vs_close_reasons', []))}\n"
        f"Probability of Profit: {analysis.get('probability_of_profit', '?')}%\n"
        f"Expected Value: ${analysis.get('expected_value', '?')}\n"
        f"Risk/Reward Ratio: {analysis.get('risk_reward_ratio', '?')}x\n"
        f"Annualized Return to Expiry: {analysis.get('annualized_return_to_expiry', '?')}%\n"
        f"Kelly Fraction: {analysis.get('kelly_fraction', '?')}\n"
        f"Theta Burn/Day: ${analysis.get('theta_burn_rate_day', '?')} ({analysis.get('theta_burn_rate_pct', '?')}%)\n"
        f"Days to Theta Breakeven: {analysis.get('days_to_theta_breakeven', '?')}\n"
        f"DTE Remaining: {analysis.get('dte_remaining', '?')}"
    )

    # Theta projection
    theta_proj = pnl.get("theta_projection", [])
    theta_lines = []
    for t in theta_proj[:10]:
        theta_lines.append(
            f"  +{t.get('days_from_now',0)}d (DTE {t.get('dte_remaining','?')}): P&L=${t.get('pnl','?')}"
        )
    theta_text = "\n".join(theta_lines) if theta_lines else "Not available"

    days_held = pnl.get("days_held", 0)
    entry_date_str = strategy.entry_date.strftime("%Y-%m-%d") if strategy.entry_date else "Unknown"

    system_prompt = f"""You are an elite quantitative options strategist with 20+ years at top-tier firms (AQR, Citadel, Two Sigma, Renaissance Technologies). You combine deep mathematical rigor with practical market wisdom. Your analysis is institutional-grade.

## YOUR ROLE
- Analyze the full position with all Greeks, scenarios, and quantitative metrics
- Provide actionable, specific trade management advice
- Explain the reasoning behind every recommendation using quantitative evidence
- Think like a risk manager: what could go wrong, and how to protect against it
- Consider current market regime (volatility environment, trend, macro context)

## ANALYSIS FRAMEWORK
Always structure your analysis around:
1. **Position Assessment**: What is this trade? Is it bullish/bearish/neutral? What's the thesis?
2. **Greek Risk Profile**: Interpret the net Greeks — are you delta-exposed, gamma-short, theta-positive? What does this mean for the position?
3. **Scenario Analysis**: Walk through the key scenarios — what happens in each? Where are the danger zones?
4. **Probability Edge**: Does the math favor holding? What's the expected value and probability of profit?
5. **Time Decay Impact**: How is theta affecting the position? Is time working for or against you?
6. **Risk Management**: What are the max loss scenarios? Where should you set mental stops?
7. **Actionable Recommendation**: Specific, concrete next steps with reasoning

## RULES
- Never give generic advice. Every statement must reference specific numbers from the position data
- When recommending HOLD or CLOSE, quantify the edge (or lack thereof) driving the decision
- If the position has asymmetric risk, flag it explicitly
- Consider the position's P&L trajectory — is it improving or deteriorating?
- Factor in the bid-ask spread when evaluating exit costs
- Consider gamma risk escalation as DTE decreases
- Be direct and concise — no filler. Quant PMs want signal, not noise"""

    user_context = f"""## TRADE POSITION DATA

**Trade**: {strategy.name}
**Underlying**: {strategy.ticker}
**Strategy Type**: {strategy.strategy_type.replace('_', ' ').title()}
**Entry Date**: {entry_date_str} ({days_held} days held)
**Underlying Price**: ${pnl.get('underlying_price', '?')}

### LEGS (Entry → Current)
{legs_text}

### P&L SUMMARY
- Entry Cost: ${pnl.get('entry_cost', '?')}
- Current Value: ${pnl.get('current_value', '?')}
- Unrealized P&L: ${pnl.get('unrealized_pnl', '?')} ({pnl.get('pnl_pct', '?')}%)
- Max Profit (at expiry): ${pnl.get('max_profit', '?')}
- Max Loss (at expiry): ${pnl.get('max_loss', '?')}
- Breakevens: {', '.join(f'${b}' for b in pnl.get('breakevens', [])) or 'None'}
- Margin Required: ${pnl.get('margin_required', 0)}
- Total Capital Deployed: ${pnl.get('total_capital', '?')}
- Expiration: {pnl.get('expiration_date', '?')}

### NET PORTFOLIO GREEKS
{net_greeks_text}

### QUANTITATIVE ANALYSIS
{analysis_text}

### SCENARIO ANALYSIS (P&L vs Underlying Move)
{scenarios_text}

### THETA DECAY PROJECTION
{theta_text}"""

    user_question = body.user_question.strip() if body.user_question else ""
    if user_question:
        user_message = f"""{user_context}

---

## USER QUESTION
{user_question}

Provide a thorough, quantitative answer to this specific question using all the position data above. Reference specific numbers."""
    else:
        user_message = f"""{user_context}

---

Provide your full institutional-grade analysis of this position. Cover all 7 points in the analysis framework. Be specific, reference numbers, and give a clear actionable recommendation."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    try:
        response = await call_llm(
            api_key=api_key,
            model="gpt-4o",
            messages=messages,
            max_tokens=2000,
            temperature=0.3,
        )
        return {"content": response, "model": "gpt-4o"}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM advisor request failed: {exc}")


# ── Continuous Lifecycle Management — the 3 desk-role agents ─────────────

_ROLE_PERSONAS = {
    "risk": {
        "title": "Risk Desk",
        "system": (
            "You are a world-class quantitative RISK MANAGER on a derivatives desk — think a "
            "statistically rigorous head of market risk. Your mandate is VaR, CVaR (expected "
            "shortfall) and stress testing over the DERIVATIVES BOOK (all derivative trades, not "
            "the whole account). Moving markets must never quietly push the book past tolerance.\n\n"
            "Judge: 1-day VaR/CVaR vs the size of the book, tail loss under the stress scenarios, "
            "net short-gamma / short-vega danger, concentration in one underlying, and vanna/charm "
            "exposure into events. Be precise and numeric.\n\n"
            "Output EXACTLY:\n"
            "VERDICT: WITHIN LIMITS | ELEVATED | BREACH\n"
            "Then 2-4 tight bullets citing the specific numbers that drove it. Recommend a concrete "
            "de-risk action (what to hedge/trim and why) ONLY if ELEVATED or BREACH; if WITHIN LIMITS "
            "say so in one line and stop. No filler."
        ),
    },
    "pm": {
        "title": "Portfolio Manager",
        "system": (
            "You are a world-class PORTFOLIO MANAGER. Your job on a LIVE position is to decide whether "
            "the thesis is still valid and still paying for its risk. You evaluate risk-adjusted quality "
            "via Omega, Sortino and Calmar alongside P&L, probability of profit and expected value.\n\n"
            "Judge: is the edge intact (Omega > 1, positive expected value), is the risk-adjusted return "
            "acceptable (Sortino/Calmar), has price hit a target or invalidated the thesis, is capital "
            "better deployed elsewhere. Reference the thesis/notes if given.\n\n"
            "Output EXACTLY:\n"
            "VERDICT: THESIS INTACT | REVIEW | EXIT\n"
            "Then 2-4 tight bullets citing the ratios and P&L that drove it. Recommend a concrete action "
            "(hold / trim / add / roll / close and the level) ONLY if REVIEW or EXIT; if THESIS INTACT "
            "say so in one line and stop. No filler."
        ),
    },
    "trader": {
        "title": "Execution Trader",
        "system": (
            "You are a world-class OPTIONS TRADER / delta-hedger running the book intraday. You watch the "
            "dynamic Greeks — delta and its evolution through gamma, charm (delta decay) and vanna "
            "(delta's drift with vol) — plus vega and volga, and you keep the book to the PM's mandate "
            "(e.g. delta-neutral) with the fewest, cheapest adjustments.\n\n"
            "Judge: how far is delta from target, how fast will gamma/charm/vanna move it before the next "
            "session, is there a vol event that vanna/volga will amplify. Give the EXACT hedge in shares "
            "or contracts (e.g. 'sell 24 shares to flatten +24Δ').\n\n"
            "Output EXACTLY:\n"
            "VERDICT: HEDGED | ADJUST\n"
            "Then 2-4 tight bullets citing the Greeks that drove it. Give the precise hedging trade ONLY "
            "if ADJUST; if HEDGED say 'no adjustment needed' in one line and stop. No filler."
        ),
    },
}


class LifecycleAgentRequest(BaseModel):
    role: str = Field(...)                      # 'risk' | 'pm' | 'trader'
    pnl_snapshot: dict = Field(...)             # LivePnlResponse (carries lifecycle metrics)
    portfolio_risk: Optional[dict] = None       # book VaR/CVaR/stress (for the risk role)


@router.post("/{strategy_id}/lifecycle-agent")
async def run_lifecycle_agent(
    strategy_id: int,
    body: LifecycleAgentRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Run one of the three desk-role agents (Risk / PM / Trader) against the
    trade's live metrics + setup. Returns a professional assessment that only
    prescribes action when something actually needs executing."""
    role = (body.role or "").lower()
    persona = _ROLE_PERSONAS.get(role)
    if not persona:
        raise HTTPException(status_code=400, detail="role must be one of: risk, pm, trader")

    result = await db.execute(
        select(SavedStrategy).where(
            SavedStrategy.id == strategy_id, SavedStrategy.user_id == user.id,
        )
    )
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Trade not found")

    api_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="OpenAI API key not configured. Please add it in Settings.")

    pnl = body.pnl_snapshot or {}
    life = pnl.get("lifecycle", {}) or {}
    trader = life.get("trader", {}) or {}
    pm = life.get("pm", {}) or {}
    analysis = pnl.get("analysis", {}) or {}
    legs = json.loads(strategy.legs_data) if strategy.legs_data else []
    legs_txt = "\n".join(
        f"  {l.get('action','?')} {l.get('qty',1)}x {l.get('type','?')} ${l.get('strike','?')} "
        f"exp {l.get('expiration', l.get('exp','?'))}"
        for l in legs
    ) or "  (stock only)"

    ctx = f"""## TRADE
Name: {strategy.name}  |  Underlying: {strategy.ticker}  |  Type: {strategy.strategy_type}
Thesis / notes: {strategy.notes or '(none)'}
Legs:
{legs_txt}

## LIVE P&L
Cost basis ${pnl.get('entry_cost')}, current value ${pnl.get('current_value')}, unrealized ${pnl.get('unrealized_pnl')} ({pnl.get('pnl_pct')}%)
Max gain ${pnl.get('max_profit')} / max loss ${pnl.get('max_loss')} | breakevens {pnl.get('breakevens')} | DTE {analysis.get('dte_remaining')}

## PM METRICS
PoP {analysis.get('probability_of_profit')}% | Expected value ${analysis.get('expected_value')} | Kelly {analysis.get('kelly_fraction')}
Omega {pm.get('omega')} | Sortino {pm.get('sortino')} | Calmar {pm.get('calmar')} | Exp. return {pm.get('expected_return_pct')}% | Downside dev {pm.get('downside_dev_pct')}%

## TRADER GREEKS (position)
Delta {trader.get('net_delta')} | Gamma {trader.get('net_gamma')} | Vega {trader.get('net_vega')} | Theta {trader.get('net_theta')}/d
Vanna {trader.get('net_vanna')} (Δ per +1 vol-pt) | Charm {trader.get('net_charm')} (Δ per +1 day) | Volga {trader.get('net_volga')} (vega per +1 vol-pt)
"""
    if role == "risk" and body.portfolio_risk:
        pr = body.portfolio_risk
        stress = "; ".join(f"{s['name']}: ${s['pnl']:,}" for s in pr.get("stress_tests", []))
        ctx += (f"\n## DERIVATIVES BOOK RISK ({pr.get('n_positions')} trades, {pr.get('n_underlyings')} underlyings, "
                f"{pr.get('horizon_days')}-day)\n"
                f"VaR95 ${pr.get('var_95'):,} | VaR99 ${pr.get('var_99'):,} | CVaR95 ${pr.get('cvar_95'):,} | "
                f"CVaR99 ${pr.get('cvar_99'):,}\nNet vega ${pr.get('net_vega'):,} | delta notional "
                f"${pr.get('net_delta_notional'):,}\nStress: {stress}\n")

    messages = [
        {"role": "system", "content": persona["system"]},
        {"role": "user", "content": ctx + "\nAssess now. Follow the required output format exactly."},
    ]
    try:
        response = await call_llm(api_key=api_key, model="gpt-4o", messages=messages,
                                  max_tokens=900, temperature=0.2)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM {role} agent failed: {exc}")

    # Parse the leading VERDICT token → action_needed flag for the UI.
    verdict = ""
    for line in response.splitlines():
        if line.strip().upper().startswith("VERDICT"):
            verdict = line.split(":", 1)[-1].strip()
            break
    calm = {"WITHIN LIMITS", "THESIS INTACT", "HEDGED"}
    action_needed = bool(verdict) and verdict.upper() not in calm
    return {"role": role, "title": persona["title"], "verdict": verdict,
            "action_needed": action_needed, "content": response, "model": "gpt-4o"}


def _parse_verdict(response: str, calm_words: set) -> tuple[str, bool]:
    """Pull the leading VERDICT token and decide whether action is required."""
    verdict = ""
    for line in response.splitlines():
        if line.strip().upper().startswith("VERDICT"):
            verdict = line.split(":", 1)[-1].strip()
            break
    return verdict, bool(verdict) and verdict.upper() not in calm_words


# Pre-entry variants of the desk agents. Unlike the lifecycle agents (which run
# on a LIVE, already-placed position), these advise whether to ENTER a proposed
# structure the user is still evaluating. Each "green" set is the verdict that
# means "go / no concern" (so action_needed = verdict not in green).
_PRE_TRADE_PERSONAS = {
    "risk": {
        "title": "Risk Desk",
        "green": {"ACCEPTABLE"},
        "system": (
            "You are the RISK MANAGER on a derivatives desk vetting a NEW trade before it is placed. "
            "Stay STRICTLY in your lane — loss containment and sizing — and do NOT rehash the PM's edge "
            "case or the trader's fills. Assume PoP and breakevens are known; your job is what happens "
            "when the trade is WRONG.\n\n"
            "Focus only on: worst-case loss vs capital committed and vs a sane account (concentration / "
            "position sizing), the SHAPE of the tail (is loss bounded, or does it accelerate — gap and "
            "jump risk below protection), CVaR/expected-shortfall not just the headline max loss, "
            "assignment / pin / early-exercise risk, margin and short-vol/short-gamma blow-up risk, and "
            "liquidity-to-exit under stress. Call out the ONE blind spot the other desks will gloss over.\n\n"
            "Output EXACTLY:\n"
            "VERDICT: ACCEPTABLE | ELEVATED | EXCESSIVE\n"
            "Then 2-4 tight bullets — each a RISK point (tail/sizing/assignment/liquidity), citing the "
            "risk numbers (max loss, CVaR, capital, margin). End with one line: enter as sized / cut size "
            "to X / do not enter. No PoP-cheerleading, no filler."
        ),
    },
    "pm": {
        "title": "Portfolio Manager",
        "green": {"ENTER"},
        "system": (
            "You are the PORTFOLIO MANAGER deciding whether this NOT-yet-placed trade earns a slot in the "
            "book. Stay in your lane — capital allocation and risk-adjusted edge — and do NOT re-derive "
            "greeks (trader's job) or restate the max loss (risk's job).\n\n"
            "Focus only on: is there a real EDGE (Omega > 1, positive expected value), is the risk-adjusted "
            "return worth it (Sortino/Calmar), and above all OPPORTUNITY COST — does the expected return "
            "beat simply holding cash/T-bills or the underlying over this horizon (a trade that ties up "
            "capital for a return below the risk-free rate is a pass no matter how high PoP is). Consider "
            "capital efficiency, how the payoff fits a view worth owning, and edge durability. Call out the "
            "blind spot the other desks miss (e.g. great PoP but terrible carry).\n\n"
            "Output EXACTLY:\n"
            "VERDICT: ENTER | RESIZE | SKIP\n"
            "Then 2-4 tight bullets — each a PM point (edge / risk-adjusted return / opportunity cost / fit), "
            "citing Omega, Sortino, expected return vs the cash hurdle, Kelly. End with one line: enter / "
            "enter smaller / skip and what you'd want first. No filler."
        ),
    },
    "trader": {
        "title": "Execution Trader",
        "green": {"GOOD ENTRY"},
        "system": (
            "You are the EXECUTION TRADER / vol specialist checking a proposed entry before it goes to "
            "market. Stay in your lane — Greeks, vol and fills — and do NOT re-argue the PM's edge or the "
            "risk desk's sizing.\n\n"
            "Focus only on: the net GREEKS you're putting on (delta exposure vs intent, gamma/theta "
            "trade-off, vega and whether you're long or short vol), the VOL REGIME (is IV rich or cheap "
            "for what you're doing — selling into low IV or buying into high IV is a mistake), event/vanna/"
            "volga risk into earnings or data, strike & expiration selection, whether the net debit/credit "
            "is fair vs the mids, leg LIQUIDITY and realistic fill/slippage, and timing. Call out the blind "
            "spot the other desks miss (e.g. short vol into an event, or a delta that doesn't match the thesis).\n\n"
            "Output EXACTLY:\n"
            "VERDICT: GOOD ENTRY | WAIT | RESTRUCTURE\n"
            "Then 2-4 tight bullets — each an EXECUTION/GREEKS/VOL point, citing the greeks and IV. End with "
            "one line: work the order at $X / wait for Y / restructure to Z. No filler."
        ),
    },
}


class PreTradeAgentRequest(BaseModel):
    role: str = Field(...)                          # 'risk' | 'pm' | 'trader'
    ticker: str = Field(...)
    strategy_type: str = ""
    legs: list[dict] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)     # free-form key/value metrics
    scenarios: list[dict] = Field(default_factory=list)  # [{move_pct, price, roi, pnl}] payoff curve
    breakevens: list = Field(default_factory=list)  # breakeven underlying moves or prices
    desk_metrics: Optional[dict] = None  # {trader, pm, risk, quant} from pre-trade-metrics
    notes: Optional[str] = None


@router.post("/pre-trade-agent")
async def run_pre_trade_agent(
    body: PreTradeAgentRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Run a desk-role agent (Risk / PM / Trader) against a PROPOSED, not-yet-placed
    structure (Dual Direction Buffer, Hedging, …) so the user gets a should-I-enter
    assessment while still evaluating the trade. No saved trade required."""
    role = (body.role or "").lower()
    persona = _PRE_TRADE_PERSONAS.get(role)
    if not persona:
        raise HTTPException(status_code=400, detail="role must be one of: risk, pm, trader")

    api_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="OpenAI API key not configured. Please add it in Settings.")

    legs_txt = "\n".join(
        f"  {l.get('action','?')} {l.get('qty', l.get('contracts', 1))}x {l.get('type','?')} "
        f"${l.get('strike','?')}" + (f" exp {l.get('expiration')}" if l.get('expiration') else "")
        for l in body.legs
    ) or "  (no legs provided)"

    def _fmt(v):
        if isinstance(v, bool):
            return str(v)
        if isinstance(v, float):
            return f"{v:,.2f}"
        if isinstance(v, int):
            return f"{v:,}"
        return str(v)
    metrics_txt = "\n".join(
        f"  {k}: {_fmt(v)}" for k, v in body.metrics.items() if v is not None
    ) or "  (none)"

    # Full payoff curve so the agent judges the whole PROFILE (buffer, breakevens,
    # capped upside, how extreme the max-loss move is) — not a single ratio.
    scen_txt = ""
    if body.scenarios:
        rows = []
        for s in body.scenarios:
            mv, roi, pnl = s.get("move_pct"), s.get("roi"), s.get("pnl")
            price = s.get("price")
            if isinstance(mv, (int, float)):
                line = f"  {mv:+.0f}% move"
                if isinstance(price, (int, float)):
                    line += f" (≈${price:,.0f})"
                if isinstance(roi, (int, float)):
                    line += f": ROI {roi:+.1f}%"
                if isinstance(pnl, (int, float)):
                    line += f" (P&L ${pnl:,.0f})"
                rows.append(line)
        if rows:
            scen_txt = "\n## PAYOFF AT EXPIRATION (underlying move → outcome)\n" + "\n".join(rows) + "\n"
    if body.breakevens:
        scen_txt += "Breakevens: " + ", ".join(str(b) for b in body.breakevens) + "\n"

    # Role-specific desk numbers — each agent leads with ITS OWN metrics so the
    # three don't all recite the same PoP/breakeven facts.
    desk = body.desk_metrics or {}
    role_txt = ""
    if role == "trader" and desk.get("trader"):
        t = desk["trader"]
        role_txt = ("\n## YOUR DESK — POSITION GREEKS\n"
                    f"Net Δ {t.get('net_delta')} | Γ {t.get('net_gamma')} | Vega ${t.get('net_vega')}/vol-pt | "
                    f"Theta ${t.get('net_theta')}/day\n"
                    f"Vanna {t.get('net_vanna')} | Charm {t.get('net_charm')} | Volga {t.get('net_volga')} | "
                    f"avg IV {t.get('avg_iv_pct')}%\n")
    elif role == "pm" and desk.get("pm"):
        p = desk["pm"]
        role_txt = ("\n## YOUR DESK — RISK-ADJUSTED QUALITY\n"
                    f"Omega {p.get('omega')} | Sortino {p.get('sortino')} | Calmar {p.get('calmar')} | "
                    f"Exp. return {p.get('expected_return_pct')}% | PoP {p.get('pop')}% | "
                    f"EV ${p.get('expected_value')} | Kelly {p.get('kelly_fraction')}\n")
    elif role == "risk" and desk.get("risk"):
        rk = desk["risk"]
        role_txt = ("\n## YOUR DESK — TAIL & CAPITAL\n"
                    f"VaR95 ${rk.get('var_95')} | CVaR95 (expected shortfall) ${rk.get('cvar_95')} | "
                    f"Max loss ${rk.get('max_loss')} | Max profit ${rk.get('max_profit')} | "
                    f"Capital ${rk.get('capital')}\n")
    if desk.get("quant"):
        q = desk["quant"]
        role_txt += (f"\n(For context — the algorithmic Quant read: {q.get('verdict')} "
                     f"{q.get('score')}/100: {'; '.join(q.get('reasons', [])[:3])})\n")

    ctx = (
        "## PROPOSED TRADE — NOT YET PLACED\n"
        f"Underlying: {body.ticker}  |  Structure: {body.strategy_type or 'custom'}\n"
        "The user is still EVALUATING this and has not entered. Advise whether to enter.\n\n"
        f"## LEGS\n{legs_txt}\n\n"
        f"## KEY METRICS\n{metrics_txt}\n"
        f"{scen_txt}"
        f"{role_txt}"
    )
    if body.notes:
        ctx += f"\n## NOTES\n{body.notes}\n"

    guidance = (
        "\nJudge the trade on its WHOLE payoff profile, not a single number: the range of moves where "
        "it is profitable, the breakeven cushion from spot, the capped upside, and — critically — HOW "
        "EXTREME a move is required to reach the max loss. A max loss that only occurs near a total "
        "collapse (underlying → 0) is a remote tail, NOT the expected risk; do not reduce the trade to a "
        "naive max-gain-over-max-loss ratio. Weigh the plausible outcomes given the DTE.\n"
        "Assess now for ENTRY. Follow the required output format exactly."
    )
    messages = [
        {"role": "system", "content": persona["system"]},
        {"role": "user", "content": ctx + guidance},
    ]
    try:
        response = await call_llm(api_key=api_key, model="gpt-4o", messages=messages,
                                  max_tokens=800, temperature=0.2)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM {role} agent failed: {exc}")

    verdict, action_needed = _parse_verdict(response, persona["green"])
    return {"role": role, "title": persona["title"], "verdict": verdict,
            "action_needed": action_needed, "content": response, "model": "gpt-4o"}


class PreTradeMetricsRequest(BaseModel):
    ticker: str = Field(...)
    expiration: Optional[str] = None
    spot: float = Field(...)
    capital: float = 0.0
    dte: int = 0
    stock_shares: float = 0.0
    legs: list[dict] = Field(default_factory=list)       # {action, type, strike, qty|contracts, iv?}
    scenarios: list[dict] = Field(default_factory=list)  # {price, pnl}
    max_loss: Optional[float] = None
    max_profit: Optional[float] = None
    sofr_pct: float = 5.0
    strategy_type: str = ""   # anchors the Quant recommendation (buffer/hedge/income/…)


@router.post("/pre-trade-metrics")
async def run_pre_trade_metrics(
    body: PreTradeMetricsRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Full desk read (Trader Greeks + PM ratios + VaR/CVaR) and an ALGORITHMIC
    Quant recommendation for a PROPOSED, not-yet-placed structure. Same lifecycle
    engine as the live My Trades desks. Per-leg IV is used when supplied, else
    filled once from the option chain."""
    import asyncio
    from ..services.lifecycle_service import compute_pretrade_metrics, terminal_payoff_curve

    def _right(t):
        t = str(t or "").upper()
        return "C" if t.startswith("C") else ("P" if t.startswith("P") else None)

    def _sign(a):
        return 1 if str(a or "").upper().startswith("B") else -1

    dte = max(int(body.dte or 0), 0)
    dte_years = dte / 365.0

    opt_legs, stock_shares, need_iv = [], float(body.stock_shares or 0.0), False
    for l in body.legs:
        typ = str(l.get("type") or "").upper()
        qty = float(l.get("qty") or l.get("contracts") or 1)
        if typ.startswith("EQ") or typ == "STOCK":
            stock_shares += _sign(l.get("action")) * qty * 100.0
            continue
        right = _right(typ)
        if not right:
            continue
        iv = l.get("iv")
        iv_pct = (iv * 100.0) if isinstance(iv, (int, float)) and 0 < iv <= 3 else (float(iv) if iv else None)
        if not iv_pct:
            need_iv = True
        opt_legs.append({"strike": float(l.get("strike") or 0), "right": right,
                         "sign": _sign(l.get("action")), "qty": qty, "iv": iv_pct, "dte_years": dte_years,
                         "price": float(l.get("price") or l.get("mid") or 0.0)})

    # Fill any missing IVs from the option chain, once.
    if need_iv and body.expiration:
        try:
            import yfinance as yf
            ch = yf.Ticker(body.ticker.upper()).option_chain(body.expiration)

            def _iv_at(right, strike):
                df = ch.calls if right == "C" else ch.puts
                if df is None or df.empty or "impliedVolatility" not in df:
                    return None
                idx = (df["strike"] - strike).abs().idxmin()
                v = float(df.loc[idx, "impliedVolatility"])
                return v * 100.0 if v and v > 0 else None

            for lg in opt_legs:
                if not lg["iv"]:
                    lg["iv"] = _iv_at(lg["right"], lg["strike"])
        except Exception as exc:
            logger.warning(f"pre-trade IV fetch failed for {body.ticker}: {exc}")

    ivs = [lg["iv"] for lg in opt_legs if lg["iv"]]
    avg_iv = (sum(ivs) / len(ivs) / 100.0) if ivs else 0.0   # decimal

    # If the caller didn't send a payoff curve (e.g. the income scanner), build one
    # from the priced legs so PM/Quant metrics still compute for every structure.
    scenarios = body.scenarios
    if (not scenarios or len(scenarios) < 3) and opt_legs:
        scenarios = terminal_payoff_curve(opt_legs, stock_shares, float(body.spot))

    return await asyncio.to_thread(
        compute_pretrade_metrics,
        [lg for lg in opt_legs if lg["iv"]], float(body.spot), scenarios,
        float(body.capital or 0), body.max_loss, body.max_profit,
        avg_iv, dte, stock_shares, 0.05, float(body.sofr_pct or 5.0), None,
        body.strategy_type,
    )


class PortfolioRiskAgentRequest(BaseModel):
    portfolio_risk: dict = Field(...)
    trades_summary: list[dict] = Field(default_factory=list)


@router.post("/portfolio-risk-agent")
async def run_portfolio_risk_agent(
    body: PortfolioRiskAgentRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Run the Risk Desk agent over the WHOLE derivatives book (not a single trade).
    Fed the book VaR/CVaR/stress + a per-trade summary so it can flag concentration
    and net short-vol / short-gamma danger."""
    api_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="OpenAI API key not configured. Please add it in Settings.")

    pr = body.portfolio_risk or {}
    stress = "; ".join(f"{s.get('name')}: ${s.get('pnl'):,}" for s in pr.get("stress_tests", []))
    by_u = "; ".join(
        f"{u.get('ticker')} (Δ {u.get('net_delta')}, vega {u.get('net_vega')}, IV {u.get('iv')}%)"
        for u in pr.get("by_underlying", [])
    )
    trades = "\n".join(
        f"  {t.get('ticker')} · {t.get('name','')} · P&L ${t.get('unrealized_pnl')} · "
        f"Δ {t.get('net_delta')} · vega {t.get('net_vega')}"
        for t in body.trades_summary
    ) or "  (none)"

    ctx = f"""## DERIVATIVES BOOK RISK ({pr.get('n_positions')} trades, {pr.get('n_underlyings')} underlyings, {pr.get('horizon_days')}-day horizon)
VaR95 ${pr.get('var_95'):,} | VaR99 ${pr.get('var_99'):,} | CVaR95 ${pr.get('cvar_95'):,} | CVaR99 ${pr.get('cvar_99'):,}
Book net vega ${pr.get('net_vega'):,} | net delta notional ${pr.get('net_delta_notional'):,}
Stress: {stress}

## BY UNDERLYING
{by_u}

## TRADES
{trades}

Assess the BOOK now (concentration, net short-vol/gamma, tail under stress). Follow the required output format exactly."""

    messages = [
        {"role": "system", "content": _ROLE_PERSONAS["risk"]["system"]},
        {"role": "user", "content": ctx},
    ]
    try:
        response = await call_llm(api_key=api_key, model="gpt-4o", messages=messages,
                                  max_tokens=900, temperature=0.2)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM risk agent failed: {exc}")

    verdict, action_needed = _parse_verdict(response, {"WITHIN LIMITS", "THESIS INTACT", "HEDGED"})
    return {"role": "risk", "title": "Risk Desk", "verdict": verdict,
            "action_needed": action_needed, "content": response, "model": "gpt-4o"}


# ── Institutional Lifecycle Manager — the quant PM who manages a LIVE trade ──

_LIFECYCLE_MANAGER_PERSONA = """# ROLE
You are the HEAD OF A DERIVATIVES DESK — a world-class institutional quant portfolio manager
with two decades running an options book. A trade is ALREADY LIVE (real capital, real P&L).
Your ONE job right now: decide how to MANAGE it from here and issue a single exit signal.

# THE MANDATE — exit vocabulary (this is the deliverable)
VERDICT is EXACTLY one of:
  STRONG_HOLD   — edge firmly intact, hold with conviction; nothing to do
  HOLD          — on track; hold and monitor named levels
  CLOSE         — the risk/reward has tilted; begin taking it off / tighten / roll
  STRONG_CLOSE  — exit now (or defend immediately); the reason to hold is gone

# HARD RULES
- Every number below is PRE-COMPUTED and authoritative. INTERPRET it — do NOT recompute
  (you are excellent at judgment, unreliable at arithmetic). Never restate a figure you
  weren't given.
- You are managing a PLACED position, not screening an entry. Weigh what's already banked,
  what's left to earn, and the path risk to get there.
- A deterministic engine has already proposed a signal + reasons. Treat it as a respected
  colleague's opening view — agree, upgrade, or overrule it, and say why.
- Be specific and institutional. No filler, no hedging language, no generic advice.

# REASONING PROCESS (think in these 5 steps, then decide)
1. EDGE STILL THERE? — VRP / IV-vs-HV, PoP, expected value vs the risk-free hurdle, Omega.
   Is this position still being paid for its risk, or is it now dead money?
2. PROFIT BANKED vs THETA LEFT — % of max profit captured. The take-half-early discipline:
   once most of the premium is harvested, the marginal theta rarely justifies the gamma/pin
   risk you keep holding. Is there enough left to earn to justify staying?
3. PATH RISK — greeks trajectory into expiry (gamma, charm pulling delta, vanna into a vol
   move), pin/assignment risk near the short strike, tail (max loss / CVaR), DTE.
4. CATALYSTS — earnings / ex-div / macro inside the remaining window that change the math.
5. CAPITAL EFFICIENCY — is this collateral working, or better redeployed?

# OUTPUT (exactly this shape)
VERDICT: <STRONG_HOLD | HOLD | CLOSE | STRONG_CLOSE>
REASONING:
1) Edge — ...
2) Profit vs theta — ...
3) Path risk — ...
4) Catalysts — ...
5) Capital — ...
MANAGEMENT PLAN: <the concrete next action(s): hold / take profit at X / roll the short to Y /
   defend below Z / close — with the price or % levels that trigger each>
KEY LEVELS: <breakeven(s), the short strike, the profit-take level, the stop>
"""


class LifecycleManagerRequest(BaseModel):
    pnl_snapshot: dict = Field(...)   # full LivePnlResponse (carries lifecycle + analysis)


@router.post("/{strategy_id}/lifecycle-manager")
async def run_lifecycle_manager(
    strategy_id: int,
    body: LifecycleManagerRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The institutional quant PM: reasons over the WHOLE live trade and issues a single
    STRONG_HOLD / HOLD / CLOSE / STRONG_CLOSE with a concrete management plan."""
    result = await db.execute(
        select(SavedStrategy).where(
            SavedStrategy.id == strategy_id, SavedStrategy.user_id == user.id,
        )
    )
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Trade not found")
    api_key = await get_user_api_key(db, user.id, "openai_api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="OpenAI API key not configured. Please add it in Settings.")

    pnl = body.pnl_snapshot or {}
    a = pnl.get("analysis", {}) or {}
    life = pnl.get("lifecycle", {}) or {}
    trd = life.get("trader", {}) or {}
    pm = life.get("pm", {}) or {}
    params = json.loads(strategy.parameters) if strategy.parameters else {}
    legs = json.loads(strategy.legs_data) if strategy.legs_data else []
    legs_txt = "\n".join(
        f"  {l.get('action','?')} {l.get('qty',1)}x {l.get('type','?')} ${l.get('strike','?')} "
        f"exp {l.get('expiration', l.get('exp','?'))}" for l in legs
    ) or "  (stock only)"
    exit_reasons = "; ".join(a.get("exit_reasons", []) or [])

    ctx = f"""## LIVE TRADE
{strategy.name} · {strategy.ticker} · {strategy.strategy_type} · purpose={params.get('purpose','income')}
Thesis / notes: {strategy.notes or '(none)'}
Legs:
{legs_txt}

## P&L (already realized on paper)
Cost basis ${pnl.get('entry_cost')} · current value ${pnl.get('current_value')} · unrealized ${pnl.get('unrealized_pnl')} ({pnl.get('pnl_pct')}%) · {pnl.get('days_held')}d held · {a.get('dte_remaining')} DTE
Max gain ${pnl.get('max_profit')} @ ${pnl.get('max_profit_price')} · max loss ${pnl.get('max_loss')} @ ${pnl.get('max_loss_price')} · breakevens {pnl.get('breakevens')}
% of max profit captured: {a.get('captured_pct')}%

## EDGE & QUALITY
PoP {a.get('probability_of_profit')}% ({a.get('pop_method')}) · Expected value ${a.get('expected_value')} · Kelly {a.get('kelly_fraction')}
Omega {pm.get('omega')} · Sortino {pm.get('sortino')} · Calmar {pm.get('calmar')} · exp. return {pm.get('expected_return_pct')}% · downside σ {pm.get('downside_dev_pct')}% · avg IV {life.get('avg_iv_pct')}%

## GREEKS (position)
Δ {trd.get('net_delta')} · Γ {trd.get('net_gamma')} · ν {trd.get('net_vega')} · Θ {trd.get('net_theta')}/d · Vanna {trd.get('net_vanna')} · Charm {trd.get('net_charm')} · Volga {trd.get('net_volga')}

## THE DESK'S TWO ALGORITHMIC VIEWS (respected colleagues — agree, upgrade, or overrule)
Deterministic (rules): {a.get('exit_signal')} — {exit_reasons}
Quant algorithmic (scored 0-100): {(a.get('quant_exit') or {}).get('signal')} @ {(a.get('quant_exit') or {}).get('score')}/100 (base quality {(a.get('quant_exit') or {}).get('base_quality')}, adjustments {[f"{x['name']} {x['pts']:+d}" for x in (a.get('quant_exit') or {}).get('adjustments', [])]}, overrides {(a.get('quant_exit') or {}).get('overrides')})

Manage this trade now. Follow the output format exactly."""

    messages = [
        {"role": "system", "content": _LIFECYCLE_MANAGER_PERSONA},
        {"role": "user", "content": ctx},
    ]
    try:
        response = await call_llm(api_key=api_key, model="gpt-4o", messages=messages,
                                  max_tokens=1100, temperature=0.25)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM lifecycle manager failed: {exc}")

    # Parse the exit verdict (normalize spaces → the canonical 4-level token).
    raw_verdict, _ = _parse_verdict(response, set())
    v = raw_verdict.upper().replace(" ", "_")
    signal = next((s for s in ("STRONG_HOLD", "STRONG_CLOSE", "HOLD", "CLOSE") if s in v), "HOLD")
    return {
        "role": "manager", "title": "Institutional Desk", "signal": signal,
        "action_needed": signal in ("CLOSE", "STRONG_CLOSE"),
        "content": response, "model": "gpt-4o",
    }


@router.get("/{strategy_id}/underlying-desk")
async def underlying_desk(
    strategy_id: int,
    quote_source: str = "yfinance",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The underlying's stock context for a PLACED trade — the SAME chrome the Income
    desk shows: price / earnings / SOFR / HV, the IV rank·percentile·skew vol panel,
    and upcoming events. Reuses the income scan with NO opportunity builders (chrome
    only), targeted at the trade's own horizon, so it's cheap (one expiry)."""
    import datetime as dt
    result = await db.execute(
        select(SavedStrategy).where(
            SavedStrategy.id == strategy_id, SavedStrategy.user_id == user.id,
        )
    )
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Trade not found")

    legs = json.loads(strategy.legs_data) if strategy.legs_data else []
    exp = next((l.get("expiration") or l.get("expiry") for l in legs
                if l.get("expiration") or l.get("expiry")), None)
    trade_dte = None
    if exp:
        try:
            trade_dte = max(1, (dt.date.fromisoformat(str(exp)[:10]) - dt.date.today()).days)
        except (ValueError, TypeError):
            trade_dte = None
    target = min(max(trade_dte, 7), 45) if trade_dte else 30

    from ..services.derivative_income_service import run_derivative_income
    try:
        res = await run_derivative_income(
            strategy.ticker, structures=["__chrome_only__"], min_prob=0.0, min_income=0.0,
            quote_source=quote_source, user=user, db=db, target_dte=target,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Underlying context failed: {exc}")
    return {
        "ticker": res.get("ticker"), "spot": res.get("spot"),
        "context": res.get("context"), "events": res.get("events", []),
        "expiry_summaries": res.get("expiry_summaries", []),
    }


# ── Tier-2 FULL desk score for a placed trade (option-math + TA + VRP) ───────

class DeskScoreRequest(BaseModel):
    pnl_snapshot: dict = Field(...)
    structure: str = Field(...)                 # covered_call / cash_secured_put / ...
    expiration: Optional[str] = None
    short_strike: Optional[float] = None
    quote_source: str = "yfinance"


@router.post("/{strategy_id}/desk-score")
async def compute_lifecycle_desk_score(
    strategy_id: int,
    body: DeskScoreRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The FULL institutional desk score for a PLACED income trade — the same
    deterministic engine the scan uses (base quality + option-math factors
    VRP/Moneyness/Skew/Liquidity/Beta + TA factors + Q-vs-P), then the lifecycle
    overlay (profit banked, time/gamma) → the 4-level exit signal. On-demand
    (heavy: runs the scan + TA), so it never touches the live-pnl refresh path."""
    from ..services.desk_review_service import compute_placed_desk_score

    result = await db.execute(
        select(SavedStrategy).where(
            SavedStrategy.id == strategy_id, SavedStrategy.user_id == user.id,
        )
    )
    strategy = result.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=404, detail="Trade not found")

    # Delegate the fresh-chain re-score + management overlay to the shared helper so a PLACED
    # trade here and a PAPER trade refresh compute identically (one engine, one code path).
    _legs = json.loads(strategy.legs_data) if strategy.legs_data else []
    try:
        return await compute_placed_desk_score(
            ticker=strategy.ticker, structure=body.structure, expiration=body.expiration,
            short_strike=body.short_strike, legs=_legs, pnl_snapshot=body.pnl_snapshot or {},
            user=user, db=db, quote_source=body.quote_source,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Desk scan failed: {exc}")
