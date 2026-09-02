"""Paper Trader — place an Income-Desk opportunity as a paper trade and re-price it on demand.

The user "places" any scanned/evaluated opportunity in one click; we snapshot the full
opportunity (a ``DeskRankedTrade``, incl. its Quant Analysis) at placement, then on each Refresh
re-price the EXACT legs through the same desk engine and compare placed-vs-now.

Laziness contract: the ONLY heavy path is :func:`recompute` (yfinance + ``rank_desk``). The
list/detail routes read stored/cached columns only — no network, no scan.

P&L is short-premium mark-to-market: you SELL the structure for the entry net credit and would
buy it back at the current net value, so ``unrealized = entry_credit − current_value`` (positive
when the premium has decayed in your favour). Cost basis = the entry net credit received.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Optional

from .desk_review_service import reprice_desk_focus, score_desk_management

logger = logging.getLogger(__name__)

CONTRACT_MULTIPLIER = 100   # shares per option contract


def dte_remaining(expiration: Optional[str]) -> Optional[int]:
    """Calendar days from today to the expiration (min 1). None if unparseable."""
    if not expiration:
        return None
    try:
        return max(1, (dt.date.fromisoformat(str(expiration)[:10]) - dt.date.today()).days)
    except (ValueError, TypeError):
        return None


def _per_share_credit(opp_or_row: dict) -> Optional[float]:
    """Net credit PER SHARE for the structure. Prefers ``premium_per_share``; else backs it out
    of the per-contract ``premium`` (÷100)."""
    pps = opp_or_row.get("premium_per_share")
    if pps is None and opp_or_row.get("premium") is not None:
        try:
            pps = float(opp_or_row["premium"]) / CONTRACT_MULTIPLIER
        except (TypeError, ValueError):
            pps = None
    return None if pps is None else float(pps)


def build_paper_trade_fields(opp: dict, contracts: int = 1, spot: Optional[float] = None) -> dict:
    """Derive the stored columns for a NEW paper trade from the placed opportunity
    (a ``DeskRankedTrade``). Pure — no network. Cost basis = net credit received
    (``entry_premium_per_share × 100 × contracts``)."""
    pps = _per_share_credit(opp)
    entry_credit = (pps * CONTRACT_MULTIPLIER * contracts) if pps is not None else None
    return {
        "structure": opp.get("structure"),
        "expiration": opp.get("expiration"),
        "label": opp.get("label"),
        "short_strike": opp.get("short_strike") or opp.get("put_short") or opp.get("call_short"),
        "contracts": contracts,
        "legs": json.dumps(opp.get("legs") or []),
        "entry_spot": spot,
        "entry_premium_per_share": pps,
        "entry_credit": entry_credit,
        "placed_snapshot": json.dumps(opp),
        "placed_desk_score": opp.get("desk_score"),
        "placed_algo_grade": opp.get("algo_grade"),
    }


def _pnl_block(pt, row: dict, spot: Optional[float]) -> dict:
    """Compute the displayed P&L + the ``pnl_snapshot`` the management overlay consumes, from the
    repriced ``row`` and the trade's stored entry basis. Returns ``(display, snapshot)`` merged
    into one dict; ``snapshot`` keys are consumed by :func:`score_desk_management`."""
    contracts = pt.contracts or 1
    cur_pps = _per_share_credit(row)
    entry_pps = pt.entry_premium_per_share
    entry_credit = pt.entry_credit
    cur_value = (cur_pps * CONTRACT_MULTIPLIER * contracts) if cur_pps is not None else None
    unrealized = (entry_credit - cur_value) if (entry_credit is not None and cur_value is not None) else None
    # captured_pct — PERCENT of max profit banked (0-100; NEGATIVE if the premium moved against
    # you). max_profit == entry_credit for these short-premium structures, so this equals
    # (1 − current/entry) × 100 whether measured in per-share or dollar terms.
    captured = None
    if entry_pps and cur_pps is not None and abs(entry_pps) > 1e-9:
        captured = round((1.0 - (cur_pps / entry_pps)) * 100.0, 1)
    # max_loss must be NEGATIVE for the near-max-loss override (lifecycle_overlay). The opp
    # reports a positive magnitude → negate and scale by contracts.
    ml = row.get("max_loss")
    max_loss = (-abs(float(ml)) * contracts) if ml is not None else None
    unrealized_pct = (round(unrealized / abs(entry_credit) * 100.0, 2)
                      if (unrealized is not None and entry_credit) else None)
    spot_change_pct = (round((spot - pt.entry_spot) / pt.entry_spot * 100.0, 2)
                       if (spot and pt.entry_spot) else None)
    dte = dte_remaining(pt.expiration)
    return {
        # displayed
        "cost_basis": entry_credit,
        "current_value": cur_value,
        "unrealized_pnl": unrealized,
        "unrealized_pct": unrealized_pct,
        "captured_pct": captured,
        "current_spot": spot,
        "entry_spot": pt.entry_spot,
        "spot_change_pct": spot_change_pct,
        "dte_remaining": dte,
        "contracts": contracts,
        "entry_premium_per_share": entry_pps,
        "current_premium_per_share": cur_pps,
        # for the management overlay (score_desk_management → management_desk_score)
        "max_profit": entry_credit,
        "max_loss": max_loss,
    }


async def recompute(pt, user, db, quote_source: str = "yfinance") -> dict:
    """The one heavy path: re-price the exact legs off a fresh chain, compute current P&L, run the
    holder management overlay, and CACHE the result (``last_eval`` + denormalized ``last_*``) so
    the list view stays compute-free. Returns the full result dict + a ``pnl`` block, or
    ``{matched: False, error, pnl: None}`` when the trade can't be priced.

    Commits ``pt`` (cache columns + first-refresh ``entry_spot`` backfill)."""
    legs = json.loads(pt.legs) if pt.legs else []
    dte = dte_remaining(pt.expiration)
    row, desk = await reprice_desk_focus(
        ticker=pt.ticker, structure=pt.structure, expiration=pt.expiration,
        short_strike=pt.short_strike, legs=legs, target_dte=dte,
        user=user, db=db, quote_source=quote_source,
    )
    if row is None:
        # desk is the error dict; leave the cache untouched so the last good read still shows.
        return {**desk, "pnl": None}

    spot = desk.get("spot") or (desk.get("context") or {}).get("spot")
    pnl = _pnl_block(pt, row, spot)
    pnl_snapshot = {
        "analysis": {"dte_remaining": pnl["dte_remaining"], "captured_pct": pnl["captured_pct"]},
        "unrealized_pnl": pnl["unrealized_pnl"],
        "max_profit": pnl["max_profit"],
        "max_loss": pnl["max_loss"],
    }
    result = score_desk_management(row, desk, pnl_snapshot=pnl_snapshot, structure=pt.structure)
    result["pnl"] = pnl

    # Cache — the list route renders these with zero compute.
    pt.last_eval = json.dumps(result, default=str)
    pt.last_eval_at = dt.datetime.now(dt.timezone.utc)
    pt.last_spot = spot
    pt.last_value_per_share = pnl["current_premium_per_share"]
    pt.last_pnl = pnl["unrealized_pnl"]
    pt.last_desk_score = result.get("desk_score")
    pt.last_algo_grade = result.get("algo_grade")
    if pt.entry_spot is None and spot is not None:
        pt.entry_spot = spot   # backfill so the spot-move column works from the next refresh on
    await db.commit()
    await db.refresh(pt)
    return result
